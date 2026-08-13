"""外部公開 API（``/api/v1``。docs/EXTERNAL-API.md）。

外部のエージェント（ログを監視して話を納品するブリッジなど）から、スタジオの
話づくりとレンダリングを行うための API。公開する範囲は Grok エージェントに
許している操作（:data:`app.agent_protocol.STUDIO_ACTIONS`）と同じで、削除は
Shot だけ。

ここに置くのは HTTP の入り口と 2 つの安全弁だけで、ビジネスロジックは持たない:

- **API キー**（:func:`require_external_key`）: 設定 ``external_api_key`` が空なら
  外部 API という機能ごと存在しないふるまい（404）、キーが違えば 401。
- **暴走ガード**（:func:`_check_pending_takes`）: 未完了 Take が
  ``external_max_pending_takes`` に達していたらレンダリング投入を 429 で拒む。
  内部 API（UI からの操作）には掛けない。

残りは :mod:`app.studio` / :mod:`app.jobs` の既存関数をそのまま呼ぶだけの
薄いラッパーで、エラーの移し方も内部 API（:mod:`app.routers.studio`）と揃える。
"""

import secrets

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import ValidationError
from starlette.datastructures import UploadFile

from .. import jobs as job_service
from .. import library, studio as service
from ..config import load_settings
from ..models import (
    Job,
    StoryCreate,
    StoryResult,
    StudioAsset,
    StudioAssetCreate,
    StudioAssetFromJob,
    StudioAssetUpdate,
    StudioEpisode,
    StudioEpisodeCreate,
    StudioEpisodeUpdate,
    StudioProject,
    StudioProjectCreate,
    StudioProjectDetail,
    StudioProjectSummary,
    StudioProjectUpdate,
    StudioScene,
    StudioSceneCreate,
    StudioSceneUpdate,
    StudioShot,
    StudioShotCreate,
    StudioShotUpdate,
    StudioTake,
)
from .assets import save_upload
from .studio import _kind_of

#: 外部 API の操作は履歴に「エージェントの変更」として残す（UI 由来と区別する）
ACTOR = "agent"


def require_external_key(
    x_api_key: str | None = Header(None, alias="X-API-Key"),
) -> None:
    """``X-API-Key`` を設定のキーと定数時間で比べる（ルーター全体の依存関係）。

    キーが設定されていないあいだは ``/api/v1`` を丸ごと 404 にする: 外部 API は
    「キーを入れたら有効になる」機能で、無効なうちは存在を匂わせない。
    """
    configured = (load_settings().external_api_key or "").strip()
    if not configured:
        raise HTTPException(status_code=404, detail="Not Found")
    # ``compare_digest`` は非 ASCII の str を受け取れない（TypeError）。ヘッダは
    # Starlette が latin-1 で、設定は UTF-8 で持っているので、どちらも bytes に
    # 揃えてから比べる（全角混じりのキーでも 500 にせず 401 を返す）。
    if not x_api_key or not secrets.compare_digest(
        x_api_key.encode("utf-8"), configured.encode("utf-8")
    ):
        raise HTTPException(status_code=401, detail="invalid API key")


router = APIRouter(
    prefix="/api/v1",
    tags=["external"],
    dependencies=[Depends(require_external_key)],
)


def _bad_request(exc: service.StudioError) -> HTTPException:
    """スタジオ操作の失敗を HTTP に移す（内部 API と同じ移し方）。"""
    if isinstance(exc, service.StudioConflict):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


def _pending_limit() -> int:
    return max(0, int(load_settings().external_max_pending_takes or 0))


async def _check_pending_takes() -> int:
    """未完了 Take が上限に達していたら 429（0 = 無制限）。返り値は上限。"""
    limit = _pending_limit()
    if limit <= 0:
        return 0
    pending = await service.count_pending_takes()
    if pending >= limit:
        raise HTTPException(
            status_code=429,
            detail=(
                f"未完了の Take が {pending} 件あります"
                f"（上限 {limit} 件）。完了を待ってから投入してください"
            ),
        )
    return limit


# --------------------------------------------------------------------------
# プロジェクト
# --------------------------------------------------------------------------

@router.get("/projects", response_model=list[StudioProjectSummary])
async def list_projects() -> list[StudioProjectSummary]:
    return await service.list_projects()


@router.post("/projects", response_model=StudioProject, status_code=201)
async def create_project(payload: StudioProjectCreate) -> StudioProject:
    try:
        return await service.create_project(
            payload.name,
            payload.code,
            payload.synopsis,
            payload.world_notes,
            payload.auto_translate,
            payload.latent_continuity,
            payload.nsfw,
            payload.quality,
            actor=ACTOR,
        )
    except service.StudioError as exc:
        raise _bad_request(exc) from exc


@router.get("/projects/{project_id}", response_model=StudioProjectDetail)
async def get_project(project_id: str) -> StudioProjectDetail:
    detail = await service.project_detail(project_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="project not found")
    return detail


@router.patch("/projects/{project_id}", response_model=StudioProject)
async def update_project(
    project_id: str, payload: StudioProjectUpdate
) -> StudioProject:
    try:
        project = await service.update_project(
            project_id, actor=ACTOR, **payload.model_dump()
        )
    except service.StudioError as exc:
        raise _bad_request(exc) from exc
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    return project


# --------------------------------------------------------------------------
# 話（エピソード）と場（シーン）
# --------------------------------------------------------------------------

@router.post("/projects/{project_id}/episodes", response_model=StudioEpisode,
             status_code=201)
async def create_episode(
    project_id: str, payload: StudioEpisodeCreate
) -> StudioEpisode:
    try:
        return await service.create_episode(project_id, payload, actor=ACTOR)
    except service.StudioError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.patch("/episodes/{episode_id}", response_model=StudioEpisode)
async def update_episode(
    episode_id: str, payload: StudioEpisodeUpdate
) -> StudioEpisode:
    episode = await service.update_episode(
        episode_id, actor=ACTOR, **payload.model_dump()
    )
    if episode is None:
        raise HTTPException(status_code=404, detail="episode not found")
    return episode


@router.post("/episodes/{episode_id}/scenes", response_model=StudioScene,
             status_code=201)
async def create_scene(episode_id: str, payload: StudioSceneCreate) -> StudioScene:
    try:
        return await service.create_scene(episode_id, payload, actor=ACTOR)
    except service.StudioError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.patch("/scenes/{scene_id}", response_model=StudioScene)
async def update_scene(scene_id: str, payload: StudioSceneUpdate) -> StudioScene:
    """指定した項目だけ変える（``episode_id`` を送ると別の話へ引っ越す）。"""
    try:
        scene = await service.update_scene(
            scene_id, actor=ACTOR, **payload.model_dump()
        )
    except service.StudioError as exc:
        raise _bad_request(exc) from exc
    if scene is None:
        raise HTTPException(status_code=404, detail="scene not found")
    return scene


# --------------------------------------------------------------------------
# 脚本（Shot）
# --------------------------------------------------------------------------

@router.post("/projects/{project_id}/shots", response_model=StudioShot,
             status_code=201)
async def create_shot(project_id: str, payload: StudioShotCreate) -> StudioShot:
    try:
        return await service.create_shot(project_id, payload, actor=ACTOR)
    except service.StudioError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.patch("/shots/{shot_id}", response_model=StudioShot)
async def update_shot(shot_id: str, payload: StudioShotUpdate) -> StudioShot:
    """指定した項目だけ変える（``scene_id`` などは null を明示すると外れる）。"""
    try:
        shot = await service.update_shot(shot_id, payload.changes(), actor=ACTOR)
    except service.StudioError as exc:
        raise _bad_request(exc) from exc
    if shot is None:
        raise HTTPException(status_code=404, detail="shot not found")
    return shot


@router.delete("/shots/{shot_id}", status_code=204)
async def delete_shot(shot_id: str) -> None:
    if not await service.delete_shot(shot_id, actor=ACTOR):
        raise HTTPException(status_code=404, detail="shot not found")


# --------------------------------------------------------------------------
# World Bible の素材
# --------------------------------------------------------------------------

def _asset_body(data: dict) -> StudioAssetCreate:
    try:
        return StudioAssetCreate(**data)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc


@router.post("/projects/{project_id}/assets", response_model=StudioAsset,
             status_code=201)
async def add_asset(project_id: str, request: Request) -> StudioAsset:
    """素材を World Bible に登録する（JSON と multipart の両方を受ける）。

    - JSON: :class:`app.models.StudioAssetCreate`。``path`` に同じマシン上の
      絶対パスを書くと実体が ``assets/<kind>/`` へ複製される（省略すると
      メタデータのみの素材）。
    - multipart: ``file`` にファイルを添付する（内部 API と同じ受け口）。
      種別は添付の拡張子から決め、``name`` を省くとファイル名の主部になる。
    """
    if await service.get_project(project_id) is None:
        raise HTTPException(status_code=404, detail="project not found")
    content_type = request.headers.get("content-type", "")
    path = ""
    if content_type.startswith("multipart/form-data"):
        form = await request.form()
        upload = form.get("file")
        fields = {
            key: value for key, value in form.items() if isinstance(value, str)
        }
        fields.pop("project_id", None)
        if isinstance(upload, UploadFile) and upload.filename:
            fields.setdefault("kind", _kind_of(upload.filename))
            saved = await save_upload(upload, str(fields["kind"]))
            path = saved.path
            fields.setdefault("name", upload.filename.rsplit(".", 1)[0])
        if "locked" in fields:
            fields["locked"] = fields["locked"].lower() in ("1", "true", "on")
        payload = _asset_body(fields)
    else:
        payload = _asset_body(await request.json())
        path = payload.path
    try:
        return await service.add_asset(
            project_id,
            name=payload.name,
            kind=payload.kind,
            path=path,
            category=payload.category,
            caption=payload.caption,
            prompt_caption=payload.prompt_caption,
            profile=payload.profile,
            locked=payload.locked,
            sort_order=payload.sort_order,
            actor=ACTOR,
        )
    except service.StudioError as exc:
        raise _bad_request(exc) from exc


@router.post("/projects/{project_id}/assets/from-job", response_model=StudioAsset,
             status_code=201)
async def add_asset_from_job(
    project_id: str, payload: StudioAssetFromJob
) -> StudioAsset:
    """生成済みジョブの出力を素材として登録する（``@名前`` で参照できる）。

    ``source`` はジョブのどの出力を取るか（image / last_frame / video / audio）。
    種別はそこから決まるので、本文の ``kind`` と ``path`` は見ない。
    """
    job = await job_service.get_job(payload.job_id, include_workflow=False)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    attribute, kind, label = library.SOURCES[payload.source]
    stored = getattr(job, attribute, None)
    if not stored:
        raise HTTPException(
            status_code=400, detail=f"job '{job.id}' には{label}がありません"
        )
    try:
        dest = job_service.copy_into_assets(stored, kind)
    except job_service.JobValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        return await service.add_asset(
            project_id,
            name=payload.name,
            kind=kind,
            path=str(dest),
            category=payload.category,
            caption=payload.caption,
            prompt_caption=payload.prompt_caption,
            profile=payload.profile,
            locked=payload.locked,
            sort_order=payload.sort_order,
            actor=ACTOR,
        )
    except service.StudioError as exc:
        raise _bad_request(exc) from exc


@router.patch("/assets/{asset_id}", response_model=StudioAsset)
async def update_asset(asset_id: str, payload: StudioAssetUpdate) -> StudioAsset:
    try:
        asset = await service.update_asset(
            asset_id, actor=ACTOR, **payload.model_dump()
        )
    except service.StudioError as exc:
        raise _bad_request(exc) from exc
    if asset is None:
        raise HTTPException(status_code=404, detail="studio asset not found")
    return asset


# --------------------------------------------------------------------------
# Take（Shot の生成）
# --------------------------------------------------------------------------

@router.get("/shots/{shot_id}/takes", response_model=list[StudioTake])
async def list_takes(shot_id: str) -> list[StudioTake]:
    if await service.get_shot(shot_id) is None:
        raise HTTPException(status_code=404, detail="shot not found")
    return await service.list_takes(shot_id)


@router.post("/shots/{shot_id}/render", response_model=StudioTake, status_code=201)
async def render_shot(shot_id: str) -> StudioTake:
    """Shot を 1 回生成する（未完了 Take が上限に達していれば 429）。"""
    if await service.get_shot(shot_id) is None:
        raise HTTPException(status_code=404, detail="shot not found")
    # 数えてから投入するまでを錠で括る（並行リクエストが数え合いになって、
    # 上限に達していても全部すり抜けるのを防ぐ）
    async with service.PENDING_TAKES_LOCK:
        await _check_pending_takes()
        try:
            return await service.render_shot(shot_id)
        except service.StudioError as exc:
            raise _bad_request(exc) from exc


@router.post("/takes/{take_id}/select", response_model=StudioTake)
async def select_take(take_id: str) -> StudioTake:
    take = await service.select_take(take_id, actor=ACTOR)
    if take is None:
        raise HTTPException(status_code=404, detail="take not found")
    return take


@router.post("/takes/{take_id}/reject", response_model=StudioTake)
async def reject_take(take_id: str) -> StudioTake:
    take = await service.reject_take(take_id, actor=ACTOR)
    if take is None:
        raise HTTPException(status_code=404, detail="take not found")
    return take


@router.get("/jobs/{job_id}", response_model=Job)
async def get_job(job_id: str) -> Job:
    """ジョブの状態と成果物（読み取りのみ。完了待ちはここをポーリングする）。"""
    job = await job_service.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return job


# --------------------------------------------------------------------------
# 一括投入（docs/EXTERNAL-API.md §2）
# --------------------------------------------------------------------------

@router.post("/stories", response_model=StoryResult, status_code=201)
async def create_story(payload: StoryCreate) -> StoryResult:
    """話 1 本ぶんの脚本（話 -> 場 -> Shot）をまとめて作る。

    作成は 1 トランザクションで、途中の検証に落ちたら全ロールバックして 400
    （中途半端な脚本は残さない）。``render`` を立てるとコミット後に 1 カット
    ずつ生成を投入し、成否をカットごとに返す（投入に失敗しても脚本は残る）。
    """
    limit = 0
    if payload.render:
        # ここは「そもそも受け付けるか」の門前払い（429）。実効的な上限は
        # :func:`app.studio.create_story` がカットごとに錠の中で見ているので、
        # ここでは錠を取らない（取ると中で取り直せずデッドロックする）。
        limit = await _check_pending_takes()
    try:
        return await service.create_story(
            payload, actor=ACTOR, pending_limit=limit
        )
    except service.StudioError as exc:
        raise _bad_request(exc) from exc
