"""編集タブ API（タイムライン -> クリップ -> 書き出し）。

DB と ffmpeg は :mod:`app.timeline` に集約してあり、ここは HTTP の入り口だけ
（:mod:`app.routers.studio` と同じ持ち方）。プレフィックスもスタジオと同じ
``/api/studio`` にして、画面から見た住所を 1 つに揃えてある。
"""

from fastapi import APIRouter, HTTPException

from .. import timeline as service
from ..models import (
    LibraryItem,
    StudioTimeline,
    StudioTimelineCreate,
    StudioTimelineDetail,
    StudioTimelineUpdate,
    TimelineClipsUpdate,
    TimelineExport,
    TimelineExportRequest,
    TimelineExportSave,
)

router = APIRouter(prefix="/api/studio", tags=["studio"])


def _http_error(exc: service.TimelineError) -> HTTPException:
    """タイムライン操作の失敗を HTTP に移す（無い = 404 / 書き出し中 = 409）。"""
    if isinstance(exc, service.TimelineNotFound):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, service.TimelineConflict):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


# --------------------------------------------------------------------------
# タイムライン
# --------------------------------------------------------------------------

@router.post(
    "/projects/{project_id}/timelines",
    response_model=StudioTimelineDetail,
    status_code=201,
)
async def create_timeline(
    project_id: str, payload: StudioTimelineCreate | None = None
) -> StudioTimelineDetail:
    """タイムラインを 1 本作る。

    ``episode_id`` を送ると**自動配置つきの初期化**になる: その話のカットを
    場 -> カット順に走査し、採用 Take の動画が実在するものを V1 へ隙間なく並べる。
    省略すれば V1 だけの空のタイムライン。
    """
    try:
        return await service.create_timeline(
            project_id, payload or StudioTimelineCreate()
        )
    except service.TimelineError as exc:
        raise _http_error(exc) from exc


@router.get("/projects/{project_id}/timelines", response_model=list[StudioTimeline])
async def list_timelines(project_id: str) -> list[StudioTimeline]:
    """その作品のタイムライン（古い順。中身は含めない）。"""
    return await service.list_timelines(project_id)


@router.get("/timelines/{timeline_id}", response_model=StudioTimelineDetail)
async def get_timeline(timeline_id: str) -> StudioTimelineDetail:
    """トラックとクリップ込みのフル EDL。

    クリップにはソースを解決した ``video_url`` / ``source_duration_ms`` と、
    実ファイルが無いことを示す ``missing`` が付く。
    """
    detail = await service.timeline_detail(timeline_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="timeline not found")
    return detail


@router.patch("/timelines/{timeline_id}", response_model=StudioTimeline)
async def update_timeline(
    timeline_id: str, payload: StudioTimelineUpdate
) -> StudioTimeline:
    """指定した項目だけ変える（送らなければ今の値のまま）。"""
    try:
        timeline = await service.update_timeline(timeline_id, payload.model_dump())
    except service.TimelineError as exc:
        raise _http_error(exc) from exc
    if timeline is None:
        raise HTTPException(status_code=404, detail="timeline not found")
    return timeline


@router.delete("/timelines/{timeline_id}", status_code=204)
async def delete_timeline(timeline_id: str) -> None:
    """タイムラインとその中身を消す（書き出したファイルは残る）。"""
    if not await service.delete_timeline(timeline_id):
        raise HTTPException(status_code=404, detail="timeline not found")


@router.put("/timelines/{timeline_id}/clips", response_model=StudioTimelineDetail)
async def replace_clips(
    timeline_id: str, payload: TimelineClipsUpdate
) -> StudioTimelineDetail:
    """クリップを丸ごと置き換える（画面の自動保存の受け口）。

    同じトラックの中で重なっているもの、``in_ms >= out_ms`` のもの、尺と切り出しの
    長さが食い違うもの（フェーズ 1 は等速のみ）は 400 で断る。
    """
    try:
        detail = await service.replace_clips(timeline_id, payload.clips)
    except service.TimelineError as exc:
        raise _http_error(exc) from exc
    if detail is None:
        raise HTTPException(status_code=404, detail="timeline not found")
    return detail


# --------------------------------------------------------------------------
# 書き出し
# --------------------------------------------------------------------------

@router.post(
    "/timelines/{timeline_id}/export", response_model=TimelineExport, status_code=202
)
async def start_export(
    timeline_id: str, payload: TimelineExportRequest | None = None
) -> TimelineExport:
    """書き出しを 1 本受け付ける（**202 即受付**。ffmpeg は裏で走る）。

    進捗は WS の ``timeline_export`` フレームと ``GET .../exports`` で追う。
    同じタイムラインで走っているものがあれば 409。
    """
    body = payload or TimelineExportRequest()
    try:
        return await service.start_export(timeline_id, body.model_dump())
    except service.TimelineError as exc:
        raise _http_error(exc) from exc


@router.get("/timelines/{timeline_id}/exports", response_model=list[TimelineExport])
async def list_exports(timeline_id: str) -> list[TimelineExport]:
    """書き出しの履歴（新しい順。``output_url`` つき）。"""
    if await service.get_timeline(timeline_id) is None:
        raise HTTPException(status_code=404, detail="timeline not found")
    return await service.list_exports(timeline_id)


@router.get("/exports/{export_id}", response_model=TimelineExport)
async def get_export(export_id: str) -> TimelineExport:
    export = await service.get_export(export_id)
    if export is None:
        raise HTTPException(status_code=404, detail="export not found")
    return export


@router.post("/exports/{export_id}/save-to-library", response_model=LibraryItem,
             status_code=201)
async def save_to_library(
    export_id: str, payload: TimelineExportSave | None = None
) -> LibraryItem:
    """完成した mp4 をライブラリ（``library/video/``）へコピーして登録する。"""
    body = payload or TimelineExportSave()
    try:
        return await service.save_export_to_library(export_id, body.name)
    except service.TimelineError as exc:
        raise _http_error(exc) from exc
