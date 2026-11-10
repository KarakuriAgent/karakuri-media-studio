"""同梱の素材パック: リポジトリの ``asset-packs/`` にある目録を、選んで入れる。

同梱するのは**目録だけ**（入手元の情報・規約の要点と URL・確かめた日・素材ごとの
名前・分類・タグ・説明・取得元の URL）。素材のファイルは入れない（どの入手元も
素材の再配布を禁止している）。入れると、取り込みの仕組み（:mod:`app.asset_packs`。
robots.txt・間隔・同意・ZIP・SVG・動画の変換・ライセンス情報の記録）で入手元から
取りに行って棚に組み込む。

- :func:`list_bundled` … 一覧（入手元の規約の要点・確かめた日・入れ済みの件数）
- :func:`install` … 入れる（同意した入手元が 1 つも無ければ断る）。進み具合と結果は
  ``GET /api/v1/library/packs/imports/{id}``
- :func:`adopt_all_aliases` … 起動時に、改名したパックの前の id で入っている素材の
  記録を今の id に揃える
"""

from __future__ import annotations

import logging
from collections import Counter
from pathlib import Path

from . import asset_packs
from . import library as library_service
from .models import (
    AssetPackImport,
    AssetPackImportRequest,
    AssetPackManifest,
    BundledAssetPack,
    BundledAssetPackInstallRequest,
    BundledAssetPackSource,
)
from .paths import ROOT

log = logging.getLogger(__name__)

#: 同梱の目録の置き場（受け取り口の ``packs/`` とは別。こちらは git で管理する）
BUNDLED_PACKS_DIR = ROOT / "asset-packs"

#: 入れる前に見せる注意
NOTICE = (
    "素材のファイルは同梱していません。入れると、各入手元のサイトから 1 件ずつ取りに行きます。"
    "規約は変わることがあるので、入れる前に各入手元の規約のページを開いて、要点と確かめた日から"
    "変わっていないかを確かめ、同意した入手元だけを選んでください。"
)

#: 読んだ目録（パス -> (更新時刻, 目録)）
_CACHE: dict[Path, tuple[float, AssetPackManifest]] = {}


class BundledPackNotFound(asset_packs.AssetPackError):
    """その id の同梱パックが無い（404）。"""


def _files() -> list[Path]:
    folder = BUNDLED_PACKS_DIR
    if not folder.is_dir():
        return []
    return sorted(
        path for path in folder.iterdir()
        if path.is_file() and path.suffix.lower() in (".yaml", ".yml", ".json")
    )


def _load(path: Path) -> AssetPackManifest:
    mtime = path.stat().st_mtime
    cached = _CACHE.get(path)
    if cached is not None and cached[0] == mtime:
        return cached[1].model_copy(deep=True)
    manifest = asset_packs.parse_manifest(path.read_text(encoding="utf-8"))
    _CACHE[path] = (mtime, manifest)
    return manifest.model_copy(deep=True)


def manifests() -> list[tuple[Path, AssetPackManifest]]:
    """同梱の目録（読めない物はログに出して飛ばす）。"""
    found: list[tuple[Path, AssetPackManifest]] = []
    for path in _files():
        try:
            found.append((path, _load(path)))
        except (OSError, asset_packs.AssetPackError) as exc:
            log.error("同梱の素材パックを読めません: %s（%s）", path, exc)
    return found


def find(pack_id: str) -> tuple[Path, AssetPackManifest]:
    """id（前の id でもよい）で同梱の目録を引く。"""
    wanted = pack_id.strip()
    for path, manifest in manifests():
        if manifest.pack == wanted:
            return path, manifest
    for path, manifest in manifests():
        if wanted in manifest.aliases:
            return path, manifest
    raise BundledPackNotFound(f"同梱の素材パックがありません: {pack_id}")


def _relative(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


async def describe(path: Path, manifest: AssetPackManifest) -> BundledAssetPack:
    """一覧の 1 件（棚にある件数も数える）。"""
    known = await asset_packs.registered(manifest.pack, manifest.aliases)
    per_source_total: Counter[str] = Counter()
    per_source_installed: Counter[str] = Counter()
    kinds: Counter[str] = Counter()
    categories: list[str] = []
    for item in manifest.items:
        per_source_total[item.source] += 1
        if (item.source, item.file) in known:
            per_source_installed[item.source] += 1
        try:
            kind = item.kind or library_service.detect_source_kind(item.file)
        except library_service.LibraryError:
            kind = "unknown"
        kinds[kind] += 1
        category = item.category.strip()
        if category and category not in categories:
            categories.append(category)
    sources = [
        BundledAssetPackSource(
            **source.model_dump(
                include={
                    "id", "name", "url", "terms_url", "terms_summary", "checked_at",
                    "credit_required", "credit_text", "content_id", "notes",
                    "acquire", "interval_seconds",
                }
            ),
            total=per_source_total[source.id],
            installed=per_source_installed[source.id],
        )
        for source in manifest.sources
    ]
    return BundledAssetPack(
        id=manifest.pack,
        aliases=list(manifest.aliases),
        name=manifest.name,
        description=manifest.description,
        categories=categories,
        kinds=dict(kinds),
        total=len(manifest.items),
        installed=sum(per_source_installed.values()),
        sources=sources,
        file=_relative(path),
        notice=NOTICE,
    )


async def list_bundled() -> list[BundledAssetPack]:
    return [await describe(path, manifest) for path, manifest in manifests()]


async def get_bundled(pack_id: str) -> BundledAssetPack:
    path, manifest = find(pack_id)
    return await describe(path, manifest)


async def install(
    pack_id: str, request: BundledAssetPackInstallRequest
) -> AssetPackImport:
    """同梱パックを入れる（取り込みを 1 回受け付ける）。

    ``accept_sources`` にパックの入手元が 1 つも無ければ断る（同意の無い取り込みは
    しない）。パックに無い入手元の id も断る（書き間違いに気づけるように）。
    一部の入手元だけに同意したときは、同意の無い入手元の素材を ``skipped`` に返す。
    """
    _path, manifest = find(pack_id)
    known = {source.id for source in manifest.sources}
    accepted = [value.strip() for value in request.accept_sources if value.strip()]
    unknown = [value for value in accepted if value not in known]
    if unknown:
        raise asset_packs.AssetPackError(
            f"このパックに無い入手元です: {', '.join(unknown)}"
            f"（入手元: {', '.join(sorted(known))}）"
        )
    if not accepted:
        raise asset_packs.AssetPackError(
            "入手元の規約に同意していません。規約のページを確かめてから、同意した入手元の id を"
            f" accept_sources に入れてください（入手元: {', '.join(sorted(known))}）"
        )
    return await asset_packs.start_import(
        AssetPackImportRequest(
            manifest=manifest, accept_sources=accepted, dry_run=request.dry_run
        )
    )


async def adopt_all_aliases() -> int:
    """同梱パックの前の id で入っている素材の記録を、今の id に揃える（起動時）。"""
    changed = 0
    for _path, manifest in manifests():
        if manifest.aliases:
            changed += await asset_packs.adopt_aliases(manifest)
    return changed
