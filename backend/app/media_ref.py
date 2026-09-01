"""素材の指し方（:class:`app.models.MediaRef`）を実ファイルに解決する。

「どの動画を見るか」をエージェントに書かせるとき、ジョブの出力・ライブラリの
素材・タイムラインの書き出し・生の URL のどれで書かれても同じように受けたい。
その解決だけをここに置く（コンタクトシート API が使う）。

**リポジトリの中の 3 つの置き場（``outputs/`` / ``library/`` / ``assets/``）の
外は決して開かない**: 生パスを受け付ける以上、ここが唯一の関門になる
（:func:`app.jobs.resolve_asset_path` と同じ考え方で、こちらは成果物
``outputs/`` も読める点だけが違う）。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import jobs as job_service
from . import library as library_service
from . import timeline as timeline_service
from .models import MediaRef
from .paths import ASSETS_DIR, LIBRARY_DIR, OUTPUTS_DIR, rebase_stored_path

#: 受け付ける配信 URL の接頭辞 -> 置き場
URL_ROOTS: dict[str, Path] = {
    "/outputs/": OUTPUTS_DIR,
    "/library/": LIBRARY_DIR,
    "/assets/": ASSETS_DIR,
}


class MediaRefError(Exception):
    """指し方が不正／指した先が無い（ルーターは 400、見つからないものは 404）。"""


class MediaRefNotFound(MediaRefError):
    """指し先（ジョブ / 素材 / 書き出し）が存在しない。"""


@dataclass(frozen=True)
class ResolvedMedia:
    """解決した 1 件（ファイルの実体と、成果物に引き継ぎたい素性）。"""

    path: Path
    #: 既定の表示名のもとになる名前
    name: str
    nsfw: bool = False
    job_id: str | None = None


def resolve_path(raw: str) -> Path:
    """``/outputs/…`` の URL か絶対パスを、置き場の中のファイルとして開く。

    ``http://host/outputs/…`` のような完全な URL も、既知の接頭辞が含まれていれば
    そこから後ろだけを見る（エージェントは API の応答の URL をそのまま貼るため）。
    """
    text = (raw or "").strip()
    if not text:
        raise MediaRefError("path が空です")
    for prefix, directory in URL_ROOTS.items():
        index = text.find(prefix)
        if index >= 0:
            # クエリ文字列（?v=…）は付いていても落とす
            tail = text[index + len(prefix):].split("?", 1)[0]
            return _inside(directory / tail)
    return _inside(Path(text))


def _inside(candidate: Path) -> Path:
    """置き場の中にあり、実在するファイルであることを確かめる。"""
    allowed = [directory.resolve() for directory in URL_ROOTS.values()]
    resolved = rebase_stored_path(candidate).resolve()
    if not any(root in resolved.parents for root in allowed):
        raise MediaRefError(
            "path は "
            + " / ".join(str(root) for root in allowed)
            + " の中を指してください"
        )
    if not resolved.is_file():
        raise MediaRefNotFound(f"file not found: {resolved}")
    return resolved


async def resolve(ref: MediaRef) -> ResolvedMedia:
    """``MediaRef`` を実ファイルに解決する（指定は**どれか 1 つだけ**）。"""
    given = [
        name
        for name in ("job_id", "item_id", "export_id", "path")
        if str(getattr(ref, name, "") or "").strip()
    ]
    if not given:
        raise MediaRefError(
            "source には job_id / item_id / export_id / path のどれかを指定してください"
        )
    if len(given) > 1:
        raise MediaRefError(
            f"source に指定できるのは 1 つだけです（{', '.join(given)}）"
        )

    if ref.job_id:
        job = await job_service.get_job(ref.job_id, include_workflow=False)
        if job is None:
            raise MediaRefNotFound("job not found")
        try:
            path = library_service.job_output(job, ref.source)
        except library_service.LibraryError as exc:
            raise MediaRefError(str(exc)) from exc
        return ResolvedMedia(
            path, library_service.default_name(job, ref.source), job.nsfw, job.id
        )

    if ref.item_id:
        item = await library_service.get_item(ref.item_id)
        if item is None:
            raise MediaRefNotFound("library item not found")
        path = rebase_stored_path(item.path)
        if not path.is_file():
            raise MediaRefNotFound(f"file not found: {path}")
        return ResolvedMedia(path, item.name, item.nsfw, item.source_job_id)

    if ref.export_id:
        export = await timeline_service.get_export(ref.export_id)
        if export is None:
            raise MediaRefNotFound("export not found")
        if export.status != "done" or not export.output_path:
            raise MediaRefError("まだ書き出しが終わっていません")
        path = rebase_stored_path(export.output_path)
        if not path.is_file():
            raise MediaRefNotFound(f"file not found: {path}")
        return ResolvedMedia(path, "タイムラインの書き出し")

    path = resolve_path(ref.path)
    return ResolvedMedia(path, path.stem)
