"""素材パック: 効果音・BGM・画像を、入手元とライセンス情報ごと棚に取り込む。

目録（:class:`app.models.AssetPackManifest`。JSON か YAML）には、入手元ごとの
情報（名前・規約の URL と要点・確かめた日・取り方）と、素材ごとの情報（ファイル名・
種類・分類・タグ・取得元の URL・素材のページ・クレジットの文・sha256）を書く。
**目録と素材のファイルはリポジトリに入れない**（利用者の手元に置く。例の目録だけが
``docs/examples/`` にある）。

取り込み（:func:`start_import`）は 1 回ごとに ``library_pack_imports`` に記録し、
裏で走らせる（``GET /api/v1/library/packs/imports/{id}`` で追う）:

- **同意**: ``accept_sources`` に書いた入手元の素材だけを取り込む（規約を人が読んで
  同意した、という明示）。書かれていない入手元は ``unaccepted_sources`` に返す
- **自動の入手元**（``acquire: auto``）: 取得元の URL から 1 件ずつ、間隔をあけて
  （既定 :data:`DEFAULT_INTERVAL_SECONDS` 秒。``Crawl-delay`` があればそれ以上）取る。
  先に robots.txt を確かめ、:data:`USER_AGENT` を名乗る。禁止されていれば取らずに
  ``blocked`` に返す
- **手で置く入手元**（``acquire: manual``）: 受け取り口（既定
  ``<データの置き場>/packs/<pack>/inbox``）に人が置いたファイルを、ファイル名か
  sha256 で目録と突き合わせる
- 取り込んだ素材は ``library/<kind>/`` にコピーし、タグ（目録のタグ・分類・パック名）・
  分類・ライセンス情報（:class:`app.models.LibraryLicense`）・測った値
  （:mod:`app.media_measure`）付きでライブラリに登録する
- **何度実行しても同じ結果**: 登録済みの素材（同じパック・入手元・ファイル名）は
  飛ばす。手で置いた素材は sha256 を比べ、変わっていれば ``changed`` に返す
  （上書きはしない）
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import shutil
import time
import urllib.parse
import urllib.robotparser
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import library as library_service
from . import media_measure
from .db import get_db
from .ids import new_id
from .models import (
    AssetPackImport,
    AssetPackImportEntry,
    AssetPackImportRequest,
    AssetPackItem,
    AssetPackManifest,
    AssetPackSource,
    LibraryItem,
    LibraryLicense,
)
from .paths import DATA_DIR

log = logging.getLogger(__name__)

#: 受け取り口の親（``<データの置き場>/packs/<pack>/inbox``）
PACKS_DIR = DATA_DIR / "packs"

#: 取得のときに名乗る User-Agent と、robots.txt で照らす名前
ROBOTS_AGENT = "KarakuriMediaStudio"
USER_AGENT = (
    f"{ROBOTS_AGENT}/0.1 (asset pack importer;"
    " +https://github.com/KarakuriAgent/karakuri-media-studio)"
)

#: 1 件ごとにあける間隔（秒）。目録の ``interval_seconds`` でも
#: :data:`MIN_INTERVAL_SECONDS` より短くはしない
DEFAULT_INTERVAL_SECONDS = 2.5
MIN_INTERVAL_SECONDS = 2.0

#: 1 ファイルの上限（バイト）と、1 回の取得に待つ最長の時間（秒）
MAX_DOWNLOAD_BYTES = 500 * 1024 * 1024
HTTP_TIMEOUT_SECONDS = 60.0

#: パックの id に使える文字（受け取り口のフォルダ名になる）
_PACK_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

#: 走っている取り込みの id（再起動で消えたものを「中断」と見分ける）
_RUNNING: set[str] = set()
#: 取り込みは 1 本ずつ（同じ入手元へ並行に取りに行かないため）
_LOCK = asyncio.Lock()


class AssetPackError(Exception):
    """目録の誤り・受け付けられない取り込み（400）。"""


class AssetPackNotFound(AssetPackError):
    """取り込みの記録が無い（404）。"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------
# 目録
# --------------------------------------------------------------------------

def parse_manifest(text: str) -> AssetPackManifest:
    """目録の文字列（JSON か YAML）を読んで確かめる。"""
    raw: Any
    try:
        raw = json.loads(text)
    except ValueError:
        try:
            import yaml  # PyYAML（uvicorn[standard] と一緒に入る）
        except ImportError as exc:  # pragma: no cover - 環境による
            raise AssetPackError(
                "目録を JSON として読めません（YAML を読むには PyYAML が要ります）"
            ) from exc
        try:
            raw = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise AssetPackError(f"目録を読めません: {exc}") from exc
    if not isinstance(raw, dict):
        raise AssetPackError("目録はオブジェクト（pack / sources / items）で書いてください")
    try:
        manifest = AssetPackManifest.model_validate(raw)
    except ValueError as exc:
        raise AssetPackError(f"目録の形が違います: {exc}") from exc
    return check_manifest(manifest)


def check_manifest(manifest: AssetPackManifest) -> AssetPackManifest:
    """目録の約束を確かめる（破れていたら :class:`AssetPackError`）。"""
    if not _PACK_ID.match(manifest.pack or ""):
        raise AssetPackError(
            "pack は英数字と . _ - で 64 文字まで（受け取り口のフォルダ名になります）"
        )
    ids = [source.id for source in manifest.sources]
    if len(ids) != len(set(ids)):
        raise AssetPackError("sources の id が重複しています")
    known = set(ids)
    seen: set[tuple[str, str]] = set()
    for index, item in enumerate(manifest.items):
        where = f"items[{index}]（{item.file}）"
        if not item.file or Path(item.file).name != item.file or item.file in (".", ".."):
            raise AssetPackError(f"{where}: file はファイル名だけを書いてください")
        if item.source not in known:
            raise AssetPackError(f"{where}: 知らない入手元です（{item.source}）")
        key = (item.source, item.file)
        if key in seen:
            raise AssetPackError(f"{where}: 同じ入手元に同じファイル名が 2 つあります")
        seen.add(key)
        try:
            kind = item.kind or library_service.detect_kind(item.file)
            library_service.check_kind(kind)
            library_service.check_extension(kind, Path(item.file).suffix)
        except library_service.LibraryError as exc:
            raise AssetPackError(f"{where}: {exc}") from exc
        if item.sha256 and not re.fullmatch(r"[0-9a-fA-F]{64}", item.sha256):
            raise AssetPackError(f"{where}: sha256 は 16 進 64 文字で書いてください")
    return manifest


def load_manifest(request: AssetPackImportRequest) -> AssetPackManifest:
    """リクエストから目録を 1 つ取り出す（``manifest`` / ``manifest_text`` / ``manifest_path``）。"""
    given = [
        bool(request.manifest),
        bool(request.manifest_text.strip()),
        bool(request.manifest_path.strip()),
    ]
    if sum(given) != 1:
        raise AssetPackError(
            "目録は manifest・manifest_text・manifest_path のどれか 1 つで渡してください"
        )
    if request.manifest is not None:
        return check_manifest(request.manifest)
    if request.manifest_text.strip():
        return parse_manifest(request.manifest_text)
    path = Path(request.manifest_path).expanduser()
    if not path.is_file():
        raise AssetPackError(
            f"目録が見つかりません: {path}（アプリから見えるパスで書いてください。"
            " Docker で動かしているなら manifest / manifest_text で渡すのが確実です）"
        )
    return parse_manifest(path.read_text(encoding="utf-8"))


def inbox_dir(manifest: AssetPackManifest, override: str = "") -> Path:
    """手で置く入手元の受け取り口。"""
    if override.strip():
        return Path(override).expanduser()
    return PACKS_DIR / manifest.pack / "inbox"


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


# --------------------------------------------------------------------------
# 取得（自動の入手元）。テストが差し替えられるよう小さな関数に分けてある
# --------------------------------------------------------------------------

async def http_get(url: str) -> tuple[int, str, bytes]:
    """``url`` を取って ``(ステータス, Content-Type, 本文)``。

    :data:`USER_AGENT` を名乗り、リダイレクトは辿る。:data:`MAX_DOWNLOAD_BYTES`
    を超えたら途中でやめて :class:`AssetPackError`。
    """
    import httpx

    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=HTTP_TIMEOUT_SECONDS,
        headers={"User-Agent": USER_AGENT},
    ) as client:
        async with client.stream("GET", url) as response:
            chunks: list[bytes] = []
            size = 0
            if response.status_code == 200:
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > MAX_DOWNLOAD_BYTES:
                        raise AssetPackError(
                            f"大きすぎます（{MAX_DOWNLOAD_BYTES // (1024 * 1024)}MB まで）"
                        )
                    chunks.append(chunk)
            return (
                response.status_code,
                response.headers.get("content-type", ""),
                b"".join(chunks),
            )


async def sleep(seconds: float) -> None:
    await asyncio.sleep(seconds)


class _Fetcher:
    """入手元のサーバーへの礼儀（robots.txt・間隔）を持つ取得係（1 回の取り込みぶん）。"""

    def __init__(self) -> None:
        self.robots: dict[str, urllib.robotparser.RobotFileParser | str] = {}
        self.last: dict[str, float] = {}

    async def _wait(self, host: str, interval: float) -> None:
        last = self.last.get(host)
        if last is not None:
            remaining = interval - (time.monotonic() - last)
            if remaining > 0:
                await sleep(remaining)
        self.last[host] = time.monotonic()

    async def _robots(
        self, origin: str, host: str, interval: float
    ) -> urllib.robotparser.RobotFileParser | str:
        """その入手元の robots.txt（確かめられなければ理由の文字列）。"""
        if origin in self.robots:
            return self.robots[origin]
        await self._wait(host, interval)
        parser = urllib.robotparser.RobotFileParser()
        try:
            status, _type, body = await http_get(f"{origin}/robots.txt")
        except Exception as exc:  # noqa: BLE001 - 取れなければ安全側に倒す
            result: urllib.robotparser.RobotFileParser | str = (
                f"robots.txt を確かめられませんでした（{exc}）"
            )
        else:
            if status in (401, 403):
                result = f"robots.txt が {status} を返しました（取得を許されていません）"
            elif 400 <= status < 500:
                parser.parse([])  # 無ければすべて許す（一般的な扱い）
                result = parser
            elif status >= 500 or status != 200:
                result = f"robots.txt を確かめられませんでした（{status}）"
            else:
                parser.parse(body.decode("utf-8", "replace").splitlines())
                result = parser
        self.robots[origin] = result
        return result

    async def fetch(
        self, url: str, interval: float
    ) -> tuple[str, bytes | None, str]:
        """``(結果, 本文, 理由)``。結果は ``ok`` / ``blocked`` / ``missing`` / ``failed``。"""
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            return "failed", None, "取得元は http(s) の URL で書いてください"
        origin = f"{parsed.scheme}://{parsed.netloc}"
        robots = await self._robots(origin, parsed.netloc, interval)
        if isinstance(robots, str):
            return "blocked", None, robots
        if not robots.can_fetch(ROBOTS_AGENT, url):
            return "blocked", None, "robots.txt で禁止されています"
        delay = robots.crawl_delay(ROBOTS_AGENT)
        await self._wait(parsed.netloc, max(interval, float(delay or 0)))
        try:
            status, content_type, body = await http_get(url)
        except AssetPackError as exc:
            return "failed", None, str(exc)
        except Exception as exc:  # noqa: BLE001 - 1 件の失敗で全体を止めない
            return "failed", None, f"取得できませんでした（{exc}）"
        if status in (404, 410):
            return "missing", None, f"取得元が見つかりません（{status}）"
        if status != 200:
            return "failed", None, f"取得できませんでした（{status}）"
        if content_type.split(";")[0].strip().lower() in ("text/html", "application/xhtml+xml"):
            return "failed", None, (
                "HTML が返りました（素材のページの URL かもしれません。"
                "ファイルそのものの URL を download_url に書いてください）"
            )
        if not body:
            return "failed", None, "中身が空でした"
        return "ok", body, ""


# --------------------------------------------------------------------------
# 登録
# --------------------------------------------------------------------------

async def registered(pack: str) -> dict[tuple[str, str], LibraryItem]:
    """このパックから取り込み済みの素材（``(入手元, ファイル名)`` -> 項目）。"""
    found: dict[tuple[str, str], LibraryItem] = {}
    async with get_db() as conn:
        async with conn.execute(
            "SELECT * FROM library WHERE license IS NOT NULL AND license LIKE ?",
            (f'%"pack":{json.dumps(pack)}%',),
        ) as cur:
            rows = await cur.fetchall()
    for row in rows:
        item = library_service.row_to_item(row)
        if item.license is not None and item.license.pack == pack:
            found[(item.license.source_id, item.license.file)] = item
    return found


def _license(
    manifest: AssetPackManifest,
    source: AssetPackSource,
    item: AssetPackItem,
    sha256: str,
) -> LibraryLicense:
    notes = "\n".join(text for text in (item.description, source.notes) if text)
    return LibraryLicense(
        provider=source.name,
        provider_url=source.url,
        page_url=item.page_url,
        download_url=item.download_url,
        terms_url=source.terms_url,
        terms_summary=source.terms_summary,
        credit_required=source.credit_required,
        credit_text=item.credit_text or source.credit_text,
        checked_at=source.checked_at,
        content_id=source.content_id,
        notes=notes,
        pack=manifest.pack,
        source_id=source.id,
        file=item.file,
        sha256=sha256,
    )


async def _register(
    manifest: AssetPackManifest,
    source: AssetPackSource,
    item: AssetPackItem,
    *,
    origin: Path | None = None,
    data: bytes | None = None,
    sha256: str,
) -> LibraryItem:
    """ファイルを ``library/<kind>/`` へ置いて、ライセンス情報と測った値付きで登録する。"""
    kind = library_service.check_kind(item.kind or library_service.detect_kind(item.file))
    ext = library_service.check_extension(kind, Path(item.file).suffix)
    dest = (
        library_service.kind_dir(kind)
        / f"{library_service.safe_stem(Path(item.file).stem)}_{new_id()}{ext}"
    )
    if origin is not None:
        shutil.copy2(origin, dest)
    else:
        dest.write_bytes(data or b"")
    media_info = await media_measure.measure(dest, kind)
    category = item.category.strip()
    tags = [*item.tags, *([category] if category else []), manifest.pack]
    return await library_service._insert(
        kind=kind,
        name=(item.name or "").strip() or item.file,
        path=dest,
        nsfw=False,
        nsfw_source="",
        source_job_id=None,
        source=None,
        tags=library_service.normalize_tags(tags),
        category=category if category in library_service.CATEGORIES else None,
        license=_license(manifest, source, item, sha256),
        media_info=media_info,
    )


# --------------------------------------------------------------------------
# 取り込み
# --------------------------------------------------------------------------

def _entry(item: AssetPackItem, reason: str, **kwargs: Any) -> AssetPackImportEntry:
    return AssetPackImportEntry(
        file=item.file, source=item.source, name=item.name or item.file,
        reason=reason, **kwargs,
    )


class _Inbox:
    """受け取り口のファイル（名前で引き、無ければ sha256 で引く）。"""

    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self._hashes: dict[str, Path] | None = None

    def find(self, item: AssetPackItem) -> Path | None:
        candidate = self.folder / item.file
        if candidate.is_file():
            return candidate
        if not item.sha256:
            return None
        if self._hashes is None:
            self._hashes = {}
            if self.folder.is_dir():
                for path in sorted(self.folder.iterdir()):
                    if path.is_file():
                        self._hashes.setdefault(sha256_of(path), path)
        return self._hashes.get(item.sha256.lower())


async def run_import(
    manifest: AssetPackManifest,
    request: AssetPackImportRequest,
    result: AssetPackImport,
    *,
    on_progress: Any = None,
) -> AssetPackImport:
    """目録を 1 件ずつ処理して ``result`` を埋める（``dry_run`` なら何も取らず登録しない）。"""
    sources = {source.id: source for source in manifest.sources}
    accepted = {value.strip() for value in request.accept_sources}
    result.unaccepted_sources = [
        source.id for source in manifest.sources if source.id not in accepted
    ]
    result.total = len(manifest.items)
    folder = inbox_dir(manifest, request.inbox_dir)
    result.inbox_dir = str(folder)
    inbox = _Inbox(folder)
    known = await registered(manifest.pack)
    fetcher = _Fetcher()

    for item in manifest.items:
        source = sources[item.source]
        existing = known.get((item.source, item.file))
        try:
            await _one(
                manifest, source, item, existing, accepted, inbox, fetcher,
                result, dry_run=request.dry_run,
            )
        except Exception as exc:  # noqa: BLE001 - 1 件の失敗で全体を止めない
            log.exception("素材パックの 1 件で失敗しました: %s", item.file)
            result.failed.append(_entry(item, f"取り込めませんでした（{exc}）"))
        result.processed += 1
        if on_progress is not None:
            await on_progress(result)
    return result


async def _one(
    manifest: AssetPackManifest,
    source: AssetPackSource,
    item: AssetPackItem,
    existing: LibraryItem | None,
    accepted: set[str],
    inbox: _Inbox,
    fetcher: _Fetcher,
    result: AssetPackImport,
    *,
    dry_run: bool,
) -> None:
    if source.id not in accepted:
        result.skipped.append(
            _entry(item, "入手元に同意していません（accept_sources に入れると取り込みます）")
        )
        return

    if source.acquire == "manual":
        found = inbox.find(item)
        if found is None:
            if existing is not None:
                result.skipped.append(_entry(item, "登録済み", library_id=existing.id))
            else:
                result.missing.append(
                    _entry(item, f"受け取り口にありません（{inbox.folder}）")
                )
            return
        digest = sha256_of(found)
        if existing is not None:
            if existing.license is not None and existing.license.sha256 == digest:
                result.skipped.append(_entry(item, "登録済み", library_id=existing.id))
            else:
                result.changed.append(
                    _entry(
                        item,
                        "登録済みの素材と中身が違います（取り込み直しはしません。"
                        "差し替えるならライブラリの項目を消してからもう一度）",
                        library_id=existing.id,
                    )
                )
            return
        if item.sha256 and item.sha256.lower() != digest:
            result.failed.append(_entry(item, "sha256 が目録と違います"))
            return
        if dry_run:
            result.imported.append(_entry(item, f"取り込む予定（{found.name}）"))
            return
        added = await _register(manifest, source, item, origin=found, sha256=digest)
        result.imported.append(_entry(item, "取り込みました", library_id=added.id))
        return

    # 自動の入手元: 登録済みなら取りに行かない（サーバーに余計な負担を掛けない）。
    if existing is not None:
        result.skipped.append(_entry(item, "登録済み", library_id=existing.id))
        return
    url = item.download_url.strip()
    if not url:
        result.missing.append(_entry(item, "取得元の URL（download_url）がありません"))
        return
    if dry_run:
        result.imported.append(
            _entry(item, "取得する予定（robots.txt は本番で確かめます）", url=url)
        )
        return
    interval = max(
        MIN_INTERVAL_SECONDS,
        float(source.interval_seconds or DEFAULT_INTERVAL_SECONDS),
    )
    outcome, body, reason = await fetcher.fetch(url, interval)
    if outcome == "blocked":
        result.blocked.append(_entry(item, reason, url=url))
        return
    if outcome == "missing":
        result.missing.append(_entry(item, reason, url=url))
        return
    if outcome != "ok" or body is None:
        result.failed.append(_entry(item, reason, url=url))
        return
    digest = hashlib.sha256(body).hexdigest()
    if item.sha256 and item.sha256.lower() != digest:
        result.failed.append(_entry(item, "sha256 が目録と違います", url=url))
        return
    added = await _register(manifest, source, item, data=body, sha256=digest)
    result.imported.append(
        _entry(item, "取り込みました", library_id=added.id, url=url)
    )


# --------------------------------------------------------------------------
# 記録（library_pack_imports）
# --------------------------------------------------------------------------

async def _save(result: AssetPackImport) -> None:
    async with get_db() as conn:
        await conn.execute(
            "UPDATE library_pack_imports SET status = ?, result = ?, error = ?,"
            " finished_at = ? WHERE id = ?",
            (
                result.status,
                result.model_dump_json(),
                result.error,
                result.finished_at,
                result.id,
            ),
        )
        await conn.commit()


def _row_to_import(row: Any) -> AssetPackImport:
    data = dict(row)
    try:
        payload = json.loads(data.get("result") or "{}")
    except ValueError:
        payload = {}
    payload.update(
        id=data["id"],
        status=data["status"],
        pack=data.get("pack") or payload.get("pack", ""),
        dry_run=bool(data.get("dry_run")),
        error=data.get("error"),
        created_at=data["created_at"],
        finished_at=data.get("finished_at"),
    )
    result = AssetPackImport.model_validate(payload)
    if result.status in ("queued", "running") and result.id not in _RUNNING:
        # 走っていた取り込みがアプリの再起動で消えた（記録だけが残っている）。
        result.status = "failed"
        result.error = result.error or "中断されました（アプリが再起動しました）。もう一度実行してください"
    return result


async def get_import(import_id: str) -> AssetPackImport:
    async with get_db() as conn:
        async with conn.execute(
            "SELECT * FROM library_pack_imports WHERE id = ?", (import_id,)
        ) as cur:
            row = await cur.fetchone()
    if row is None:
        raise AssetPackNotFound("import not found")
    return _row_to_import(row)


async def list_imports(limit: int = 20) -> list[AssetPackImport]:
    async with get_db() as conn:
        async with conn.execute(
            "SELECT * FROM library_pack_imports"
            " ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (max(1, limit),),
        ) as cur:
            rows = await cur.fetchall()
    return [_row_to_import(row) for row in rows]


async def start_import(request: AssetPackImportRequest) -> AssetPackImport:
    """取り込みを 1 回受け付ける（目録の誤りは :class:`AssetPackError`）。

    ``dry_run`` はその場で走らせて終わった記録を返す（ネットワークにも棚にも
    触らない）。本番は裏で走らせて ``queued`` の記録を返す。
    """
    manifest = load_manifest(request)
    import_id = new_id()
    created = _now()
    result = AssetPackImport(
        id=import_id,
        status="queued",
        pack=manifest.pack,
        dry_run=request.dry_run,
        total=len(manifest.items),
        inbox_dir=str(inbox_dir(manifest, request.inbox_dir)),
        created_at=created,
    )
    async with get_db() as conn:
        await conn.execute(
            "INSERT INTO library_pack_imports"
            " (id, status, pack, dry_run, result, created_at)"
            " VALUES (?, 'queued', ?, ?, ?, ?)",
            (import_id, manifest.pack, int(request.dry_run),
             result.model_dump_json(), created),
        )
        await conn.commit()

    _RUNNING.add(import_id)
    if request.dry_run:
        await _execute(manifest, request, result)
        return await get_import(import_id)
    asyncio.create_task(_execute(manifest, request, result))
    return await get_import(import_id)


async def _execute(
    manifest: AssetPackManifest,
    request: AssetPackImportRequest,
    result: AssetPackImport,
) -> None:
    """1 回の取り込みを最後まで走らせる（例外は status='failed' に落とす）。"""
    try:
        async with _LOCK:
            result.status = "running"
            await _save(result)
            await run_import(manifest, request, result, on_progress=_save)
            result.status = "done"
    except Exception as exc:  # noqa: BLE001 - 記録に残して終える
        log.exception("素材パックの取り込みが落ちました: %s", result.id)
        result.status = "failed"
        result.error = str(exc)
    finally:
        result.finished_at = _now()
        await _save(result)
        _RUNNING.discard(result.id)
