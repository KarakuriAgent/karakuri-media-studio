"""ライブラリ（SPEC §7.2）: 履歴とは別に取っておく素材の置き場と目録。

履歴（ジョブ）は生成の記録なので、消せば成果物も消える。ライブラリはそこから
「残す」と決めたものと、手元のファイルをアップロードしたものを集めた棚で、
``library/{kind}/`` にファイル実体を置き、DB の ``library`` テーブルが目録になる。
配信は ``/library`` の静的マウント（:mod:`app.main`）。

ジョブの入力（``source_image`` / ``end_image`` / ``reference_video`` /
``audio_path``）にはライブラリのパスや URL をそのまま指定できる
（:func:`app.jobs.resolve_asset_path` が ``assets/`` と同じように受け付ける）。

ルーターとエージェント（:mod:`app.agent_runner`）の両方から使うので、DB と
ファイル操作はここに集約する。
"""

from __future__ import annotations

import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

from .db import get_db
from .ids import new_id
from .models import Job, LibraryItem, LibraryKind, LibrarySource
from .paths import LIBRARY_DIR

#: 種別ごとに受け付ける拡張子（routers/assets.py と同じホワイトリスト）
ALLOWED_EXT: dict[str, set[str]] = {
    "image": {".png", ".jpg", ".jpeg", ".webp", ".bmp"},
    "video": {".mp4", ".webm", ".mkv", ".mov"},
    "audio": {".mp3", ".wav", ".flac", ".m4a", ".ogg", ".opus"},
}

KINDS: tuple[str, ...] = ("image", "video", "audio")

#: ジョブの出力 -> (Job の属性, ライブラリの種別, 表示名の接尾辞)
SOURCES: dict[str, tuple[str, LibraryKind, str]] = {
    "image": ("image_path", "image", "生成画像"),
    "last_frame": ("last_frame_path", "image", "ラストフレーム"),
    "video": ("video_path", "video", "動画"),
    "audio": ("audio_output_path", "audio", "音声"),
}

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")

#: 1 タグの最大長（UI のチップが破綻しない程度に切り詰める）
MAX_TAG_LENGTH = 40
#: 検索 1 回で返す既定の件数
DEFAULT_LIMIT = 50


class LibraryError(Exception):
    """ライブラリ操作の失敗（ルーターが 400 / 404 に変換する）。"""


class LibraryDuplicate(LibraryError):
    """同じジョブの同じ出力が既に登録されている（ルーターは 409）。

    エラーというより「もう棚にあります」という情報なので、既存のアイテムを
    そのまま持たせて呼び出し側が案内できるようにする。
    """

    def __init__(self, item: LibraryItem) -> None:
        super().__init__(f"「{item.name}」は登録済みです")
        self.item = item


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def kind_dir(kind: str) -> Path:
    directory = LIBRARY_DIR / kind
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def item_url(kind: str, name: str) -> str:
    return f"/library/{kind}/{name}"


def safe_stem(stem: str) -> str:
    return _SAFE.sub("_", stem)[:64] or "item"


def check_kind(kind: str) -> LibraryKind:
    if kind not in KINDS:
        raise LibraryError(f"unknown kind '{kind}' (allowed: {', '.join(KINDS)})")
    return kind  # type: ignore[return-value]


def check_extension(kind: str, suffix: str) -> str:
    ext = suffix.lower()
    if ext not in ALLOWED_EXT[kind]:
        raise LibraryError(
            f"unsupported extension '{ext}'"
            f" (allowed: {sorted(ALLOWED_EXT[kind])})"
        )
    return ext


def normalize_tags(raw: object) -> list[str]:
    """タグの並びを整える: 前後の空白を落とし、空と重複を捨てる（順序は保つ）。

    大文字小文字は保存したままにするが、重複判定だけは無視する（「Sakura」と
    「sakura」を別タグとして持たない）。文字列を渡すとカンマ区切りとして読む
    （multipart のフォーム値はリストで送れないため）。
    """
    if isinstance(raw, str):
        parts: list[object] = list(raw.split(","))
    elif isinstance(raw, (list, tuple)):
        parts = list(raw)
    else:
        return []
    tags: list[str] = []
    seen: set[str] = set()
    for part in parts:
        tag = str(part).strip()[:MAX_TAG_LENGTH].strip()
        key = tag.casefold()
        if tag and key not in seen:
            seen.add(key)
            tags.append(tag)
    return tags


def _load_tags(raw: object) -> list[str]:
    try:
        return normalize_tags(json.loads(raw or "[]"))
    except (TypeError, ValueError):
        return []


def row_to_item(row) -> LibraryItem:
    data = dict(row)
    path = Path(data["path"])
    return LibraryItem(
        id=data["id"],
        created_at=data["created_at"],
        kind=data["kind"],
        name=data["name"],
        path=str(path),
        url=item_url(data["kind"], path.name),
        nsfw=bool(data["nsfw"]),
        nsfw_source=data["nsfw_source"] or "",
        source_job_id=data["source_job_id"],
        # source / tags は後から足したカラム。古い行では NULL のことがある
        source=data.get("source") or None,
        tags=_load_tags(data.get("tags")),
    )


def matches(item: LibraryItem, query: str) -> bool:
    """``query`` が表示名かタグのどれかに部分一致するか（大文字小文字を無視）。"""
    needle = query.strip().casefold()
    if not needle:
        return True
    if needle in item.name.casefold():
        return True
    return any(needle in tag.casefold() for tag in item.tags)


async def list_items(kind: str | None = None) -> list[LibraryItem]:
    """登録済みの素材すべて（新しい順）。``kind`` を渡すとその種別だけ。"""
    query = "SELECT * FROM library"
    params: tuple[str, ...] = ()
    if kind is not None:
        query += " WHERE kind = ?"
        params = (check_kind(kind),)
    query += " ORDER BY created_at DESC, id DESC"
    async with get_db() as conn:
        async with conn.execute(query, params) as cur:
            rows = await cur.fetchall()
    return [row_to_item(row) for row in rows]


async def search_items(
    *,
    kind: str | None = None,
    query: str = "",
    tag: str | None = None,
    limit: int | None = DEFAULT_LIMIT,
    offset: int = 0,
) -> tuple[list[LibraryItem], int]:
    """絞り込んだ素材の 1 ページと、絞り込み後の総件数。

    ``kind`` だけ SQL で絞り、``query``（名前・タグへの部分一致）と ``tag``
    （完全一致）は Python 側で判定する: タグは JSON 配列で 1 カラムに持っている
    ので SQL の LIKE では誤検出しやすく、個人利用の規模（数千件）なら読み切って
    から絞るほうが確実で読みやすい。
    """
    items = await list_items(kind)
    wanted = (tag or "").strip().casefold()
    if wanted:
        items = [
            item for item in items
            if any(existing.casefold() == wanted for existing in item.tags)
        ]
    if query.strip():
        items = [item for item in items if matches(item, query)]
    total = len(items)
    start = max(0, offset)
    page = items[start:] if limit is None else items[start : start + max(0, limit)]
    return page, total


async def all_tags() -> list[str]:
    """登録されている全タグ（表記ゆれは最初に見たものへ寄せ、名前順に並べる）。"""
    found: dict[str, str] = {}
    for item in await list_items():
        for tag in item.tags:
            found.setdefault(tag.casefold(), tag)
    return [found[key] for key in sorted(found)]


async def get_item(item_id: str) -> LibraryItem | None:
    async with get_db() as conn:
        async with conn.execute(
            "SELECT * FROM library WHERE id = ?", (item_id,)
        ) as cur:
            row = await cur.fetchone()
    return row_to_item(row) if row else None


async def _insert(
    *,
    kind: LibraryKind,
    name: str,
    path: Path,
    nsfw: bool,
    nsfw_source: str,
    source_job_id: str | None,
    source: str | None,
    tags: list[str],
) -> LibraryItem:
    item_id = new_id()
    async with get_db() as conn:
        await conn.execute(
            "INSERT INTO library (id, created_at, kind, name, path, nsfw,"
            " nsfw_source, source_job_id, source, tags)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                item_id,
                _now(),
                kind,
                name,
                str(path),
                1 if nsfw else 0,
                nsfw_source,
                source_job_id,
                source,
                json.dumps(tags, ensure_ascii=False),
            ),
        )
        await conn.commit()
    item = await get_item(item_id)
    assert item is not None
    return item


async def add_upload(
    kind: str, filename: str, data: bytes, tags: object = None
) -> LibraryItem:
    """アップロードされたファイルを ``library/{kind}/`` に保存して登録する。"""
    resolved = check_kind(kind)
    original = Path(filename or "upload")
    ext = check_extension(resolved, original.suffix)
    dest = kind_dir(resolved) / f"{safe_stem(original.stem)}_{new_id()}{ext}"
    dest.write_bytes(data)
    return await _insert(
        kind=resolved,
        name=original.name,
        path=dest,
        nsfw=False,
        nsfw_source="",
        source_job_id=None,
        source=None,
        tags=normalize_tags(tags),
    )


def job_output(job: Job, source: str) -> Path:
    """ジョブの ``source`` の出力ファイル（無ければ :class:`LibraryError`）。"""
    if source not in SOURCES:
        raise LibraryError(
            f"unknown source '{source}' (allowed: {', '.join(SOURCES)})"
        )
    attribute, _, label = SOURCES[source]
    path = getattr(job, attribute, None)
    if not path or not Path(path).is_file():
        raise LibraryError(f"job {job.id} has no {label}")
    return Path(path)


def default_name(job: Job, source: str) -> str:
    """生成物の既定の表示名（ジョブのプロンプト + 何の出力か）。"""
    _, _, label = SOURCES[source]
    text = (
        job.video_prompt or job.image_prompt or job.audio_prompt or job.user_input or ""
    ).strip()
    head = text.splitlines()[0][:60] if text else job.id
    return f"{head}（{label}）"


async def find_from_job(job_id: str, source: str) -> LibraryItem | None:
    """そのジョブの同じ出力が既に登録されているか（``source`` が NULL の旧行は除く）。"""
    async with get_db() as conn:
        async with conn.execute(
            "SELECT * FROM library WHERE source_job_id = ? AND source = ?"
            " ORDER BY created_at, id LIMIT 1",
            (job_id, source),
        ) as cur:
            row = await cur.fetchone()
    return row_to_item(row) if row else None


async def add_from_job(
    job: Job, source: str, name: str = "", tags: object = None
) -> LibraryItem:
    """ジョブの出力をライブラリへコピーして登録する（NSFW は元ジョブを引き継ぐ）。

    同じジョブの同じ出力が既に棚にあるなら、コピーを増やさず
    :class:`LibraryDuplicate` で既存のアイテムを返す。
    """
    origin = job_output(job, source)
    existing = await find_from_job(job.id, source)
    if existing is not None:
        raise LibraryDuplicate(existing)
    _, kind, _ = SOURCES[source]
    check_extension(kind, origin.suffix)
    dest = kind_dir(kind) / f"{safe_stem(origin.stem)}_{new_id()}{origin.suffix.lower()}"
    shutil.copy2(origin, dest)
    return await _insert(
        kind=kind,
        name=(name.strip() or default_name(job, source)),
        path=dest,
        nsfw=job.nsfw,
        # 元ジョブの手動指定もライブラリでは「引き継いだ値」なので auto 扱い。
        nsfw_source="auto" if job.nsfw else "",
        source_job_id=job.id,
        source=source,
        tags=normalize_tags(tags),
    )


async def update_item(
    item_id: str,
    *,
    name: str | None = None,
    nsfw: bool | None = None,
    tags: list[str] | None = None,
) -> LibraryItem | None:
    """表示名 / NSFW フラグ / タグを変える（None の項目はそのまま）。"""
    fields: dict[str, object] = {}
    if name is not None:
        cleaned = name.strip()
        if cleaned:
            fields["name"] = cleaned
    if tags is not None:
        # 空リストは「タグを全部外す」なので、name と違って弾かない。
        fields["tags"] = json.dumps(normalize_tags(tags), ensure_ascii=False)
    if nsfw is not None:
        fields["nsfw"] = 1 if nsfw else 0
        fields["nsfw_source"] = "manual"
    if not fields:
        return await get_item(item_id)
    assignments = ", ".join(f"{key} = :{key}" for key in fields)
    async with get_db() as conn:
        cur = await conn.execute(
            f"UPDATE library SET {assignments} WHERE id = :id",
            {**fields, "id": item_id},
        )
        await conn.commit()
        if cur.rowcount == 0:
            return None
    return await get_item(item_id)


async def delete_item(item_id: str) -> bool:
    """登録を解除してファイルも消す（見つからなければ False）。"""
    item = await get_item(item_id)
    if item is None:
        return False
    async with get_db() as conn:
        await conn.execute("DELETE FROM library WHERE id = ?", (item_id,))
        await conn.commit()
    Path(item.path).unlink(missing_ok=True)
    return True
