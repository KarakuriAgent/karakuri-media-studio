"""編集タブ API（タイムライン -> クリップ -> 書き出し）。

DB と ffmpeg は :mod:`app.timeline` に集約してあり、ここは HTTP の入り口だけ
（:mod:`app.routers.studio` と同じ持ち方）。プレフィックスもスタジオと同じ
``/api/studio`` にして、画面から見た住所を 1 つに揃えてある。
"""

from fastapi import APIRouter, HTTPException, Query, Request

from .. import timeline as service
from ..models import (
    LibraryItem,
    StudioTimeline,
    StudioTimelineCreate,
    StudioTimelineDetail,
    StudioTimelineUpdate,
    TimelineClipInsert,
    TimelineClipsUpdate,
    TimelineExport,
    TimelineExportRequest,
    TimelineExportSave,
    TimelineFx,
    TimelineFxEventCreate,
    TimelineFxEventUpdate,
    TimelineFxUpdate,
    TimelineMediaPage,
    TimelineMissingFix,
    TimelineMissingReport,
    TimelineSubtitleRequest,
    TimelineSyncPreview,
    TimelineSyncRequest,
    TimelineTrackCreate,
    TimelineTrackUpdate,
)

router = APIRouter(prefix="/api/studio", tags=["studio"])


def _serving_base_url(request: Request) -> str:
    """レンダラー（同じ機械で走る Remotion）から見た、このアプリの URL。

    Host ヘッダではなく**待受のポート**から組み立てる（リバースプロキシ越しの
    ホスト名を渡すと、自分の素材を外まで取りに行くことになるため）。
    """
    server = request.scope.get("server") or (None, None)
    return service.local_base_url(server[1])


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


@router.post(
    "/timelines/{timeline_id}/clips/insert", response_model=StudioTimelineDetail
)
async def insert_clip(
    timeline_id: str, payload: TimelineClipInsert
) -> StudioTimelineDetail:
    """クリップを 1 つ差し込む（重なる既存クリップを前後に分割して割り込む）。

    下のクリップの切り出しは動かさない（後半は続きから再生される）ので、
    **トラックの全長は変わらない**。``base_revision`` を添えると、それ以降に同じ
    タイムラインが触られていた場合だけ 409。
    """
    try:
        return await service.insert_clip(timeline_id, payload, actor="user")
    except service.TimelineError as exc:
        raise _http_error(exc) from exc


# --------------------------------------------------------------------------
# トラック（音声 A1… と字幕 T1）
# --------------------------------------------------------------------------

@router.post("/timelines/{timeline_id}/tracks", response_model=StudioTimelineDetail,
             status_code=201)
async def add_track(
    timeline_id: str, payload: TimelineTrackCreate | None = None
) -> StudioTimelineDetail:
    """トラックを 1 本足す（映像トラックは V1 の 1 本きりなので 400）。"""
    try:
        return await service.add_track(timeline_id, payload or TimelineTrackCreate())
    except service.TimelineError as exc:
        raise _http_error(exc) from exc


@router.patch(
    "/timelines/{timeline_id}/tracks/{track_id}", response_model=StudioTimelineDetail
)
async def update_track(
    timeline_id: str, track_id: str, payload: TimelineTrackUpdate
) -> StudioTimelineDetail:
    """名前・ミュート・ロックを変える（送らなかった項目はそのまま）。"""
    try:
        return await service.update_track(timeline_id, track_id, payload)
    except service.TimelineError as exc:
        raise _http_error(exc) from exc


@router.delete(
    "/timelines/{timeline_id}/tracks/{track_id}", response_model=StudioTimelineDetail
)
async def delete_track(timeline_id: str, track_id: str) -> StudioTimelineDetail:
    """トラックを 1 本消す（載っていたクリップも一緒に消える）。"""
    try:
        return await service.delete_track(timeline_id, track_id)
    except service.TimelineError as exc:
        raise _http_error(exc) from exc


# --------------------------------------------------------------------------
# FX トラック（タイムラインに載せる演出。SPEC §7.3）
# --------------------------------------------------------------------------

@router.get("/timelines/{timeline_id}/fx", response_model=TimelineFx)
async def get_fx(timeline_id: str) -> TimelineFx:
    """このタイムラインに載せた演出（``FxOverlay`` の props と同じ名前）。"""
    fx = await service.get_fx(timeline_id)
    if fx is None:
        raise HTTPException(status_code=404, detail="timeline not found")
    return fx


@router.put("/timelines/{timeline_id}/fx", response_model=TimelineFx)
async def replace_fx(timeline_id: str, payload: TimelineFxUpdate) -> TimelineFx:
    """演出を丸ごと置き換える（``FxOverlay`` の props をそのまま投げられる）。"""
    try:
        return await service.replace_fx(timeline_id, payload, actor="user")
    except service.TimelineError as exc:
        raise _http_error(exc) from exc


@router.post(
    "/timelines/{timeline_id}/fx/events", response_model=TimelineFx, status_code=201
)
async def add_fx_event(
    timeline_id: str, payload: TimelineFxEventCreate
) -> TimelineFx:
    """演出のイベントを 1 つ足す。"""
    try:
        return await service.add_fx_event(timeline_id, payload, actor="user")
    except service.TimelineError as exc:
        raise _http_error(exc) from exc


@router.patch(
    "/timelines/{timeline_id}/fx/events/{event_id}", response_model=TimelineFx
)
async def update_fx_event(
    timeline_id: str, event_id: str, payload: TimelineFxEventUpdate
) -> TimelineFx:
    """イベントを 1 件だけ書き換える（``event`` は浅いマージ）。"""
    try:
        return await service.update_fx_event(
            timeline_id, event_id, payload, actor="user"
        )
    except service.TimelineError as exc:
        raise _http_error(exc) from exc


@router.delete(
    "/timelines/{timeline_id}/fx/events/{event_id}", response_model=TimelineFx
)
async def delete_fx_event(
    timeline_id: str, event_id: str, base_revision: int | None = None
) -> TimelineFx:
    """イベントを 1 件消す。"""
    try:
        return await service.delete_fx_event(
            timeline_id, event_id, base_revision=base_revision, actor="user"
        )
    except service.TimelineError as exc:
        raise _http_error(exc) from exc


# --------------------------------------------------------------------------
# 素材ビン
# --------------------------------------------------------------------------

@router.get("/projects/{project_id}/media", response_model=TimelineMediaPage)
async def list_media(
    project_id: str,
    kind: str = Query("video", pattern="^(video|audio|image)$"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> TimelineMediaPage:
    """タイムラインへ足せる素材の 1 ページ。

    テイク（映像のみ）・ライブラリ・終わった単発ジョブ・作品の素材ファイルを
    新しい順に混ぜて返す。長さ（``duration_ms``）はこのページのぶんだけ調べる。
    """
    try:
        return await service.list_media(project_id, kind, limit=limit, offset=offset)
    except service.TimelineError as exc:
        raise _http_error(exc) from exc


# --------------------------------------------------------------------------
# テロップの一括生成
# --------------------------------------------------------------------------

@router.post(
    "/timelines/{timeline_id}/generate-subtitles",
    response_model=StudioTimelineDetail,
)
async def generate_subtitles(
    timeline_id: str, payload: TimelineSubtitleRequest | None = None
) -> StudioTimelineDetail:
    """V1 の各クリップの元カットの台詞から、テロップを一括で置き直す。

    字幕トラックの中身は**置き換わる**（積み増さない）。画面側で確認を取る。
    """
    body = payload or TimelineSubtitleRequest()
    try:
        return await service.generate_subtitles(timeline_id, body.track_id)
    except service.TimelineError as exc:
        raise _http_error(exc) from exc


# --------------------------------------------------------------------------
# 脚本との差分
# --------------------------------------------------------------------------

@router.get("/timelines/{timeline_id}/sync-preview", response_model=TimelineSyncPreview)
async def sync_preview(timeline_id: str) -> TimelineSyncPreview:
    """作ったあとに脚本で起きた差分（増えた / 採用が変わった / 消えたカット）。"""
    try:
        return await service.sync_preview(timeline_id)
    except service.TimelineError as exc:
        raise _http_error(exc) from exc


@router.post("/timelines/{timeline_id}/sync", response_model=StudioTimelineDetail)
async def apply_sync(
    timeline_id: str, payload: TimelineSyncRequest | None = None
) -> StudioTimelineDetail:
    """差分のうち、body で選ばれたものだけ反映する。"""
    try:
        return await service.apply_sync(timeline_id, payload or TimelineSyncRequest())
    except service.TimelineError as exc:
        raise _http_error(exc) from exc


# --------------------------------------------------------------------------
# メディア欠落のリカバリ
# --------------------------------------------------------------------------

@router.get("/timelines/{timeline_id}/missing", response_model=TimelineMissingReport)
async def missing_report(timeline_id: str) -> TimelineMissingReport:
    """実ファイルが見つからないクリップと、同じカットの差し替え候補。"""
    try:
        return await service.missing_report(timeline_id)
    except service.TimelineError as exc:
        raise _http_error(exc) from exc


@router.post(
    "/timelines/{timeline_id}/missing/resolve", response_model=StudioTimelineDetail
)
async def resolve_missing(
    timeline_id: str, payload: TimelineMissingFix | None = None
) -> StudioTimelineDetail:
    """欠落クリップを別テイクへ差し替える / まとめて消す。"""
    try:
        return await service.resolve_missing(
            timeline_id, payload or TimelineMissingFix()
        )
    except service.TimelineError as exc:
        raise _http_error(exc) from exc


# --------------------------------------------------------------------------
# 書き出し
# --------------------------------------------------------------------------

@router.post(
    "/timelines/{timeline_id}/export", response_model=TimelineExport, status_code=202
)
async def start_export(
    request: Request, timeline_id: str, payload: TimelineExportRequest | None = None
) -> TimelineExport:
    """書き出しを 1 本受け付ける（**202 即受付**。ffmpeg は裏で走る）。

    進捗は WS の ``timeline_export`` フレームと ``GET .../exports`` で追う。
    同じタイムラインで走っているものがあれば 409。``fx: true`` なら焼き上がった
    mp4 に演出を載せる Remotion ジョブが続けて走る（Remotion 連携が OFF なら 400）。
    """
    body = payload or TimelineExportRequest()
    try:
        return await service.start_export(
            timeline_id, body.model_dump(), base_url=_serving_base_url(request)
        )
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
