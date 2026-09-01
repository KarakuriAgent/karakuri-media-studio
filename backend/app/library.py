"""ライブラリ（SPEC §7.2）: 履歴とは別に取っておく素材の置き場と目録。

履歴（ジョブ）は生成の記録なので、消せば成果物も消える。ライブラリはそこから
「残す」と決めたものと、手元のファイルをアップロードしたものを集めた棚で、
``library/{kind}/`` にファイル実体を置き、DB の ``library`` テーブルが目録になる。
配信は ``/library`` の静的マウント（:mod:`app.main`）。

ジョブの入力（``source_image`` / ``end_image`` / ``reference_video`` /
``audio_path``）にはライブラリのパスや URL をそのまま指定できる
（:func:`app.jobs.resolve_asset_path` が ``assets/`` と同じように受け付ける）。

ルーター（:mod:`app.routers.library`）から使うので、DB とファイル操作はここに
集約する。
"""

from __future__ import annotations

import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

from . import contact_sheet as contact_sheets
from . import sheets, sprites, textimage
from .db import get_db
from .ids import new_id
from .models import (
    Job,
    LibraryCategory,
    LibraryItem,
    LibraryKind,
    LibraryOrigin,
    LibrarySource,
)
from .paths import LIBRARY_DIR, rebase_stored_path

#: 種別ごとに受け付ける拡張子（routers/assets.py と同じホワイトリスト）
ALLOWED_EXT: dict[str, set[str]] = {
    "image": {".png", ".jpg", ".jpeg", ".webp", ".bmp"},
    "video": {".mp4", ".webm", ".mkv", ".mov"},
    "audio": {".mp3", ".wav", ".flac", ".m4a", ".ogg", ".opus"},
}

KINDS: tuple[str, ...] = ("image", "video", "audio")

#: 素材の分類（棚の仕切り。1 件に 1 つだけ持つ）。これ以外は未分類（DB は NULL）
CATEGORIES: tuple[str, ...] = ("character", "background", "prop")

#: 「未分類」を表す明示値。DB には NULL で入るが、API では「指定なし」（絞り込ま
#: ない / 変更しない）と区別する必要があるため、未分類そのものはこの文字列で送る
UNCATEGORIZED = "none"

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


def check_category(value: object) -> LibraryCategory | None:
    """カテゴリの値を検証して正規化する（未分類は None）。

    空文字と :data:`UNCATEGORIZED`（``"none"``）は「未分類」なので None に寄せる。
    「指定なし」との区別は呼び出し側の責任: このカラムを触るかどうかは値が None
    かどうかで決めるので、``check_category`` を通す前に判断する。
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text or text == UNCATEGORIZED:
        return None
    if text not in CATEGORIES:
        raise LibraryError(
            f"unknown category '{text}'"
            f" (allowed: {', '.join(CATEGORIES)}, {UNCATEGORIZED})"
        )
    return text  # type: ignore[return-value]


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
        # source / tags / category は後から足したカラム。古い行では NULL のことが
        # ある（category の NULL はそのまま「未分類」の意味になる）
        source=data.get("source") or None,
        tags=_load_tags(data.get("tags")),
        category=data.get("category") or None,
    )


def matches(item: LibraryItem, query: str) -> bool:
    """``query`` が表示名かタグのどれかに部分一致するか（大文字小文字を無視）。"""
    needle = query.strip().casefold()
    if not needle:
        return True
    if needle in item.name.casefold():
        return True
    return any(needle in tag.casefold() for tag in item.tags)


async def list_items(
    kind: str | None = None, category: str | None = None
) -> list[LibraryItem]:
    """登録済みの素材すべて（新しい順）。``kind`` を渡すとその種別だけ。

    ``category`` も同じく絞り込み条件で、None なら分類で絞らない。未分類だけ見たい
    ときは :data:`UNCATEGORIZED`（``"none"``）を渡す（DB では NULL だが、None は
    「絞り込まない」に使ってしまっているため）。
    """
    query = "SELECT * FROM library"
    conditions: list[str] = []
    params: list[str] = []
    if kind is not None:
        conditions.append("kind = ?")
        params.append(check_kind(kind))
    if category is not None:
        resolved = check_category(category)
        if resolved is None:
            # 未分類。カラムを足す前の行は NULL、フォームの空送信は '' になりうる
            conditions.append("(category IS NULL OR category = '')")
        else:
            conditions.append("category = ?")
            params.append(resolved)
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY created_at DESC, id DESC"
    async with get_db() as conn:
        async with conn.execute(query, tuple(params)) as cur:
            rows = await cur.fetchall()
    return [row_to_item(row) for row in rows]


async def search_items(
    *,
    kind: str | None = None,
    category: str | None = None,
    query: str = "",
    tag: str | None = None,
    limit: int | None = DEFAULT_LIMIT,
    offset: int = 0,
) -> tuple[list[LibraryItem], int]:
    """絞り込んだ素材の 1 ページと、絞り込み後の総件数。

    ``kind`` と ``category``（どちらも 1 カラムの値）だけ SQL で絞り、``query``
    （名前・タグへの部分一致）と ``tag``（完全一致）は Python 側で判定する: タグは
    JSON 配列で 1 カラムに持っているので SQL の LIKE では誤検出しやすく、個人利用
    の規模（数千件）なら読み切ってから絞るほうが確実で読みやすい。

    ``category`` は None なら分類で絞らない。未分類だけを見たいときは
    :data:`UNCATEGORIZED` を渡す。
    """
    items = await list_items(kind, category)
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
    source: LibraryOrigin | str | None,
    tags: list[str],
    #: 分類（省略時は未分類。後から足したカラムなので既定値を持たせておく）
    category: LibraryCategory | None = None,
) -> LibraryItem:
    item_id = new_id()
    async with get_db() as conn:
        await conn.execute(
            "INSERT INTO library (id, created_at, kind, name, path, nsfw,"
            " nsfw_source, source_job_id, source, tags, category)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                category,
            ),
        )
        await conn.commit()
    item = await get_item(item_id)
    assert item is not None
    return item


async def add_upload(
    kind: str,
    filename: str,
    data: bytes,
    tags: object = None,
    category: object = None,
    *,
    name: str = "",
    nsfw: bool = False,
) -> LibraryItem:
    """アップロードされたファイルを ``library/{kind}/`` に保存して登録する。

    ``category`` を省く（または空 / ``"none"``）と未分類になる。``name`` を
    省くと元のファイル名が表示名になる。``nsfw`` は手で立てるものなので
    ``nsfw_source`` は ``manual``。
    """
    resolved = check_kind(kind)
    resolved_category = check_category(category)
    original = Path(filename or "upload")
    ext = check_extension(resolved, original.suffix)
    dest = kind_dir(resolved) / f"{safe_stem(original.stem)}_{new_id()}{ext}"
    dest.write_bytes(data)
    return await _insert(
        kind=resolved,
        name=(name or "").strip() or original.name,
        path=dest,
        nsfw=nsfw,
        nsfw_source="manual" if nsfw else "",
        source_job_id=None,
        source=None,
        tags=normalize_tags(tags),
        category=resolved_category,
    )


def job_output(job: Job, source: str) -> Path:
    """ジョブの ``source`` の出力ファイル（無ければ :class:`LibraryError`）。"""
    if source not in SOURCES:
        raise LibraryError(
            f"unknown source '{source}' (allowed: {', '.join(SOURCES)})"
        )
    attribute, _, label = SOURCES[source]
    path = getattr(job, attribute, None)
    # 記録は絶対パスなので、別のプレフィックスで作られた行でも読めるよう
    # いまの ROOT に載せ替える（:func:`app.paths.rebase_stored_path`）。
    origin = rebase_stored_path(path) if path else None
    if origin is None or not origin.is_file():
        raise LibraryError(f"job {job.id} has no {label}")
    return origin


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
    job: Job,
    source: str,
    name: str = "",
    tags: object = None,
    category: object = None,
) -> LibraryItem:
    """ジョブの出力をライブラリへコピーして登録する（NSFW は元ジョブを引き継ぐ）。

    同じジョブの同じ出力が既に棚にあるなら、コピーを増やさず
    :class:`LibraryDuplicate` で既存のアイテムを返す。``category`` を省くと未分類。
    """
    resolved_category = check_category(category)
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
        category=resolved_category,
    )


async def add_from_file(
    origin: str | Path,
    kind: str,
    name: str = "",
    tags: object = None,
    category: object = None,
) -> LibraryItem:
    """アプリが作ったファイルをライブラリへコピーして登録する。

    ジョブの出力（:func:`add_from_job`）でも人のアップロード（:func:`add_upload`）
    でもない、アプリ内で組み上げた成果物のための入り口——いまは編集タブの書き出し
    （:mod:`app.timeline`）が使う。元のファイルは動かさずコピーを取る。
    """
    resolved_kind = check_kind(kind)
    resolved_category = check_category(category)
    source = rebase_stored_path(origin)
    if not source.is_file():
        raise LibraryError(f"file not found: {source}")
    ext = check_extension(resolved_kind, source.suffix)
    display = (name or "").strip() or source.name
    dest = kind_dir(resolved_kind) / f"{safe_stem(Path(display).stem)}_{new_id()}{ext}"
    shutil.copy2(source, dest)
    return await _insert(
        kind=resolved_kind,
        name=display,
        path=dest,
        nsfw=False,
        nsfw_source="",
        source_job_id=None,
        source=None,
        tags=normalize_tags(tags),
        category=resolved_category,
    )


#: 合成したリファレンスシートに必ず付けるタグ（あとから棚で見つけやすくする）
SHEET_TAG = "reference-sheet"

#: シートの ``source``（ジョブ出力ではなくアプリ内で合成したもの）
SHEET_SOURCE: LibraryOrigin = "sheet"


def sheet_name(items: list[LibraryItem]) -> str:
    """シートの既定の表示名（先頭の素材の名前 + 残りの枚数）。"""
    head = items[0].name if items else "リファレンスシート"
    rest = f"ほか{len(items) - 1}件" if len(items) > 1 else ""
    return f"リファレンスシート（{head}{rest}）"


async def add_sheet(
    item_ids: list[str],
    name: str = "",
    width: int = sheets.DEFAULT_WIDTH,
    height: int = sheets.DEFAULT_HEIGHT,
) -> LibraryItem:
    """選んだ画像素材を 1 枚のリファレンスシートに合成して登録する（SPEC §7.2）。

    ``item_ids`` の**並び順に意味がある**（:func:`app.sheets.plan_layout` の規則で
    左上から詰める）。素材のカテゴリで大小のパネルが決まるので、ここではカテゴリを
    そのまま渡すだけ。

    出来上がったシートは ``library/image/`` に置き、ふつうの素材として登録する:
    キャラクターの見た目をまとめたものなので分類は ``character``、タグには
    :data:`SHEET_TAG` を付け、``source`` は :data:`SHEET_SOURCE`。素材のどれかが
    NSFW ならシートも NSFW（引き継ぎなので ``nsfw_source`` は 'auto'）。

    枚数・大きさ・種別が合わなければ :class:`LibraryError`（ルーターは 400）。
    """
    items: list[LibraryItem] = []
    for item_id in item_ids:
        item = await get_item(item_id)
        if item is None:
            raise LibraryError(f"library item not found: {item_id}")
        if item.kind != "image":
            raise LibraryError(f"「{item.name}」は画像ではありません（{item.kind}）")
        items.append(item)
    try:
        sheets.check_count(len(items))
        data = sheets.render_sheet(
            [(rebase_stored_path(item.path), item.category) for item in items],
            width,
            height,
        )
    except sheets.SheetError as exc:
        raise LibraryError(str(exc)) from exc
    dest = kind_dir("image") / f"sheet_{new_id()}.png"
    dest.write_bytes(data)
    nsfw = any(item.nsfw for item in items)
    return await _insert(
        kind="image",
        name=(name.strip() or sheet_name(items)),
        path=dest,
        nsfw=nsfw,
        # 素材から引き継いだ判定なので、手動指定ではなく auto 扱い（from-job と同じ）
        nsfw_source="auto" if nsfw else "",
        source_job_id=None,
        source=SHEET_SOURCE,
        tags=[SHEET_TAG],
        category="character",
    )


#: スプライト（背景を抜いた透過 PNG）に必ず付けるタグと ``source``
SPRITE_TAG = "sprite"
SPRITE_SOURCE: LibraryOrigin = "sprite"

#: フォント画像に必ず付けるタグと ``source``
TEXT_TAG = "text-image"
TEXT_SOURCE: LibraryOrigin = "text"

#: コンタクトシートに必ず付けるタグと ``source``
CONTACT_SHEET_TAG = "contact-sheet"
CONTACT_SHEET_SOURCE: LibraryOrigin = "contact-sheet"


async def _add_bytes(
    *,
    kind: LibraryKind,
    data: bytes,
    ext: str,
    stem: str,
    name: str,
    source: LibraryOrigin,
    tags: list[str],
    category: LibraryCategory | None = None,
    nsfw: bool = False,
    source_job_id: str | None = None,
) -> LibraryItem:
    """アプリが作ったバイト列を ``library/{kind}/`` に置いて登録する。

    :func:`add_sheet` と同じ流儀の内部ヘルパー（スプライト・フォント画像・
    コンタクトシートが共用する）。NSFW は元素材から引き継ぐので、立っていても
    手動指定ではなく auto 扱いにする。
    """
    dest = kind_dir(kind) / f"{safe_stem(stem)}_{new_id()}{ext}"
    dest.write_bytes(data)
    return await _insert(
        kind=kind,
        name=name,
        path=dest,
        nsfw=nsfw,
        nsfw_source="auto" if nsfw else "",
        source_job_id=source_job_id,
        source=source,
        tags=tags,
        category=category,
    )


def sprite_name(origin_name: str) -> str:
    """スプライトの既定の表示名（元の名前 +「（スプライト）」）。"""
    stem = (origin_name or "スプライト").strip()
    return f"{stem}（スプライト）"


async def add_keyed(
    origin: str | Path,
    *,
    name: str = "",
    method: str = "black",
    color: object = sprites.DEFAULT_COLOR,
    tolerance: object = sprites.DEFAULT_TOLERANCE,
    trim: bool = True,
    flatten: str | None = None,
    tags: object = None,
    category: object = None,
    nsfw: bool = False,
    source_job_id: str | None = None,
) -> LibraryItem:
    """画像の背景を抜いて、透過 PNG を**新しい素材**として登録する（SPEC §7.2）。

    元のファイルは触らない。抜き方（floodfill 方式のルミナンスキー / クロマキー /
    rembg）は :mod:`app.sprites` にあり、抜けなければ :class:`LibraryError`
    （ルーターは 400）。タグには :data:`SPRITE_TAG` を必ず足すので、あとから
    「棚のスプライトだけ」を ``GET /api/v1/library?tag=sprite`` で引ける。

    ``flatten`` に色を渡すと、抜いたあとの不透明部分をその色一色に塗る
    （白抜きロゴなど）。
    """
    resolved_category = check_category(category)
    source = rebase_stored_path(origin)
    if not source.is_file():
        raise LibraryError(f"file not found: {source}")
    try:
        data = sprites.key_image(
            source,
            method=method,
            color=color,
            tolerance=tolerance,
            trim=trim,
            flatten=flatten,
        )
    except sprites.SpriteError as exc:
        raise LibraryError(str(exc)) from exc
    display = (name or "").strip() or sprite_name(source.stem)
    marked = normalize_tags(tags)
    if not any(tag.casefold() == SPRITE_TAG for tag in marked):
        marked.append(SPRITE_TAG)
    return await _add_bytes(
        kind="image",
        data=data,
        ext=".png",
        stem=Path(display).stem,
        name=display,
        source=SPRITE_SOURCE,
        tags=marked,
        category=resolved_category,
        nsfw=nsfw,
        source_job_id=source_job_id,
    )


def text_image_name(text: str) -> str:
    """フォント画像の既定の表示名（本文の 1 行目）。"""
    head = (text or "").strip().splitlines()
    return f"{head[0][:30]}（文字）" if head else "フォント画像"


async def add_text_image(
    text: str,
    *,
    name: str = "",
    tags: object = None,
    category: object = None,
    **options: object,
) -> LibraryItem:
    """フォントで組んだ文字を画像にして登録する（SPEC §7.2）。

    ``options`` はそのまま :func:`app.textimage.render_text` に渡る
    （``font`` / ``size`` / ``color`` / ``outline_color`` / ``outline_width`` /
    ``background`` / ``rotate`` / ``padding`` / ``align``）。
    """
    resolved_category = check_category(category)
    try:
        data = textimage.render_text(text, **options)  # type: ignore[arg-type]
    except textimage.TextImageError as exc:
        raise LibraryError(str(exc)) from exc
    display = (name or "").strip() or text_image_name(text)
    marked = normalize_tags(tags)
    if not any(tag.casefold() == TEXT_TAG for tag in marked):
        marked.append(TEXT_TAG)
    return await _add_bytes(
        kind="image",
        data=data,
        ext=".png",
        stem=Path(display).stem,
        name=display,
        source=TEXT_SOURCE,
        tags=marked,
        category=resolved_category,
    )


async def add_contact_sheet(
    video: str | Path,
    *,
    name: str = "",
    tags: object = None,
    nsfw: bool = False,
    **options: object,
) -> tuple[LibraryItem, list[float]]:
    """動画のコマを 1 枚のシートにして登録し、``(素材, 抜いた秒)`` を返す。

    ``options`` はそのまま :func:`app.contact_sheet.render_contact_sheet` に渡る
    （``seconds`` / ``span`` / ``frames`` / ``columns`` / ``width`` / ``labels``）。
    """
    source = rebase_stored_path(video)
    if not source.is_file():
        raise LibraryError(f"file not found: {source}")
    try:
        data, seconds = await contact_sheets.render_contact_sheet(
            source, **options  # type: ignore[arg-type]
        )
    except contact_sheets.ContactSheetError as exc:
        raise LibraryError(str(exc)) from exc
    display = (name or "").strip() or f"{source.stem}（コンタクトシート）"
    marked = normalize_tags(tags)
    if not any(tag.casefold() == CONTACT_SHEET_TAG for tag in marked):
        marked.append(CONTACT_SHEET_TAG)
    item = await _add_bytes(
        kind="image",
        data=data,
        ext=".jpg",
        stem=Path(display).stem,
        name=display,
        source=CONTACT_SHEET_SOURCE,
        tags=marked,
        nsfw=nsfw,
    )
    return item, seconds


async def update_item(
    item_id: str,
    *,
    name: str | None = None,
    nsfw: bool | None = None,
    tags: list[str] | None = None,
    category: str | None = None,
) -> LibraryItem | None:
    """表示名 / NSFW フラグ / タグ / 分類を変える（None の項目はそのまま）。

    ``category`` だけは「未分類に戻す」も指定できる必要があるので、値なし（None）
    ではなく :data:`UNCATEGORIZED`（``"none"``）や空文字で伝える。None はほかの
    項目と同じく「変更しない」。
    """
    fields: dict[str, object] = {}
    if name is not None:
        cleaned = name.strip()
        if cleaned:
            fields["name"] = cleaned
    if tags is not None:
        # 空リストは「タグを全部外す」なので、name と違って弾かない。
        fields["tags"] = json.dumps(normalize_tags(tags), ensure_ascii=False)
    if category is not None:
        # 'none' / '' は未分類（NULL）に戻す指定。tags の空リストと同じ扱い。
        fields["category"] = check_category(category)
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
    rebase_stored_path(item.path).unlink(missing_ok=True)
    return True
