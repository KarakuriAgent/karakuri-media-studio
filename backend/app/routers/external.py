"""外部公開 API（``/api/v1``。docs/EXTERNAL-API.md）。

外部のエージェントが、脚本づくりから生成・検分・素材の整理・つなぎまでを
自分で回すための API。公開するのは話づくり（プロジェクト / 話 / 場 / Shot /
素材）と生成（Shot のレンダリングと汎用ジョブ）とライブラリ、それに編集タブ
一式（タイムライン / トラック / クリップ / 書き出し）と、リビジョン履歴
（一覧 / 差分 / 復元）で、**削除はプロジェクト以外**。プロジェクトだけは
リビジョンごとカスケードで消えて復元できないので、外部には出さず人に頼む
運用にする。

ここに置くのは HTTP の入り口と 2 つの安全弁だけで、ビジネスロジックは持たない:

- **API キー**（:func:`require_external_key`）: 設定 ``external_api_key`` が空なら
  外部 API という機能ごと存在しないふるまい（404）、キーが違えば 401。
- **暴走ガード**（:func:`_check_pending_jobs` / :func:`_check_running_exports`）:
  未完了のジョブ（Shot の Take を含む）や走っている書き出しが
  ``external_max_pending_takes`` に達していたら投入を 429 で拒む。数えるプールは
  「生成」と「書き出し」の 2 つで、それぞれ別の錠で直列化する。内部 API
  （UI からの操作）には掛けない。

残りは :mod:`app.studio` / :mod:`app.jobs` / :mod:`app.timeline` の既存関数を
そのまま呼ぶだけの薄いラッパーで、エラーの移し方も内部 API
（:mod:`app.routers.studio` / :mod:`app.routers.timelines`）と揃える。
"""

import asyncio
import secrets
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from pydantic import ValidationError
from starlette.datastructures import UploadFile as FormUploadFile

from .. import autotag
from .. import jobs as job_service
from .. import remotion as remotion_service
from .. import library, sheets, studio as service, timeline as timeline_service
from .. import ui_state, ws
from ..config import load_settings
from ..drafting_guide import GUIDE_VERSION, build_drafting_guide
from ..h3_examples import (
    CATEGORIES,
    MODES,
    H3Example,
    available_categories,
    available_modes,
    select_examples,
)
from ..models import (
    ASSET_FILE_ROLE_KINDS,
    ContactSheet,
    ContactSheetResult,
    DraftingGuide,
    FontList,
    Job,
    JobContinue,
    JobCreate,
    JobFromForm,
    JobRerun,
    LibraryFromJob,
    LibraryItem,
    LibraryKey,
    LibraryKeyFromJob,
    LibraryKeySource,
    LibraryPage,
    LibrarySheet,
    LibraryUpdate,
    Options,
    PromptExample,
    PromptExamples,
    RemotionCompositions,
    StoryCreate,
    StoryResult,
    StudioAsset,
    StudioAssetCreate,
    StudioAssetFile,
    StudioAssetFromJob,
    StudioAssetUpdate,
    StudioCapabilities,
    StudioEpisode,
    StudioEpisodeCreate,
    StudioEpisodeUpdate,
    StudioProject,
    StudioProjectCreate,
    StudioProjectDetail,
    StudioProjectSummary,
    StudioProjectUpdate,
    StudioRenderRequest,
    StudioReorder,
    StudioRevision,
    StudioRevisionDiff,
    StudioRevisionRestore,
    StudioScene,
    StudioSceneCreate,
    StudioSceneUpdate,
    StudioShot,
    StudioShotCreate,
    StudioShotPreview,
    StudioShotReorder,
    StudioShotUpdate,
    StudioTake,
    StudioTimeline,
    StudioTimelineCreate,
    StudioTimelineDetail,
    StudioTimelineUpdate,
    TextImage,
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
    UiFormState,
    UiFormUpdate,
    UiNavigate,
)
from .assets import save_upload
from .library import MAX_LIMIT as LIBRARY_MAX_LIMIT
from .library import _bad_request as _library_bad_request
from .library import key_job_output, key_library_item, key_media_source
from .library import upload_detecting_kind, upload_to_library
from .media import create_contact_sheet, create_text_image, font_list
from .options import get_options
from .studio import _kind_of, get_capabilities
from .timelines import _http_error as _timeline_error, _serving_base_url

#: 外部 API の操作は履歴に「外部エージェントの変更」として残す（UI 由来と
#: 区別する）。過去行に残る 'agent' はこれを分ける前の書き込み。
ACTOR = "external"

#: 書き出しの「数えてから投入する」を直列化する錠。生成の錠
#: （:data:`app.studio.PENDING_JOBS_LOCK`）とは分ける: 書き出しの受付は初回に
#: ffprobe を回して遅く、同じ錠に相乗りさせると無関係なレンダリング投入まで
#: 待たせてしまう。数えるプールが別（:func:`_check_running_exports`）なので、
#: 錠を分けても数え落としは起きない。
_EXPORTS_LOCK = asyncio.Lock()


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


def _first_message(exc: ValidationError) -> str:
    """pydantic の検証エラーを 1 行のメッセージにする（スタジオ側と同じ形）。"""
    for error in exc.errors():
        return str(error.get("msg", "")).removeprefix("Value error, ")
    return str(exc)


def _pending_limit() -> int:
    return max(0, int(load_settings().external_max_pending_takes or 0))


async def _check_running(
    count: Callable[[], Awaitable[int]], noun: str
) -> int:
    """走っているものが上限に達していたら 429（0 = 無制限）。返り値は上限。"""
    limit = _pending_limit()
    if limit <= 0:
        return 0
    running = await count()
    if running >= limit:
        raise HTTPException(
            status_code=429,
            detail=(
                f"{noun}が {running} 件あります"
                f"（上限 {limit} 件）。完了を待ってから投入してください"
            ),
        )
    return limit


async def _check_pending_jobs() -> int:
    """未完了ジョブ（Shot の Take を含む）のガード。返り値は上限。

    Take は必ずジョブを 1 本持つので、未完了ジョブを数えれば Take も数えた
    ことになる。Shot のレンダリングと汎用ジョブで**同じプールを分け合う**
    （別々に数えると、どちらも上限まで投入できてしまう）。
    """
    return await _check_running(job_service.count_pending_jobs, "未完了のジョブ")


# --------------------------------------------------------------------------
# プロジェクト
# --------------------------------------------------------------------------

@router.get("/projects", response_model=list[StudioProjectSummary])
async def list_projects() -> list[StudioProjectSummary]:
    return await service.list_projects()


@router.post("/projects", response_model=StudioProject, status_code=201)
async def create_project(payload: StudioProjectCreate) -> StudioProject:
    try:
        return await service.create_project(payload, actor=ACTOR)
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
            project_id,
            actor=ACTOR,
            base_revision=payload.base_revision,
            **payload.changes(),
        )
    except service.StudioError as exc:
        raise _bad_request(exc) from exc
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    return project


# --------------------------------------------------------------------------
# リビジョン履歴（409 の読み解きと、消したものの戻し道）
# --------------------------------------------------------------------------

@router.get("/projects/{project_id}/revisions", response_model=list[StudioRevision])
async def list_revisions(
    project_id: str,
    entity_kind: str | None = Query(
        None, description="shot / asset など。entity_id と併せて 1 件の履歴に絞る"
    ),
    entity_id: str | None = Query(None),
) -> list[StudioRevision]:
    """新しい順の見出し一覧（中身は含めない）。

    PATCH が 409 で弾かれたら、ここで**そのエンティティの履歴**
    （``entity_kind`` / ``entity_id``）を引き、下の ``diff`` で人が何を変えたかを
    読んでから書き直す。
    """
    if await service.get_project(project_id) is None:
        raise HTTPException(status_code=404, detail="project not found")
    return await service.list_revisions(
        project_id, entity_kind=entity_kind, entity_id=entity_id
    )


@router.get("/projects/{project_id}/revisions/{seq}/diff",
            response_model=StudioRevisionDiff)
async def diff_revision(project_id: str, seq: int) -> StudioRevisionDiff:
    """そのリビジョンで**何が変わったか**（直前のリビジョンとの差分）。"""
    diff = await service.diff_revision(project_id, seq)
    if diff is None:
        raise HTTPException(status_code=404, detail="revision not found")
    return diff


@router.post("/projects/{project_id}/revisions/{seq}/restore",
             response_model=StudioProjectDetail)
async def restore_revision(
    project_id: str, seq: int, payload: StudioRevisionRestore | None = None
) -> StudioProjectDetail:
    """そのリビジョンの内容へ書き戻す（ファイル実体とジョブは残る）。

    ボディは任意で、``entity`` / ``id``（と ``fields``）を送るとその 1 件
    （その項目だけ）の部分復元になる。送らなければプロジェクト丸ごと。
    消しすぎたカットや素材はこれで戻せる（書き換える前の状態も 1 リビジョンとして
    残るので、復元そのものもやり直せる）。
    """
    target = payload or StudioRevisionRestore()
    try:
        detail = await service.restore_revision(
            project_id,
            seq,
            entity=target.entity,
            entity_id=target.id,
            fields=target.fields,
            actor=ACTOR,
        )
    except service.StudioError as exc:
        raise _bad_request(exc) from exc
    if detail is None:
        raise HTTPException(status_code=404, detail="revision not found")
    return detail


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
    try:
        episode = await service.update_episode(
            episode_id,
            actor=ACTOR,
            base_revision=payload.base_revision,
            **payload.changes(),
        )
    except service.StudioError as exc:
        raise _bad_request(exc) from exc
    if episode is None:
        raise HTTPException(status_code=404, detail="episode not found")
    return episode


@router.delete("/episodes/{episode_id}", status_code=204)
async def delete_episode(episode_id: str) -> None:
    """配下の場ごと消す（そこにいた Shot は未分類に戻る）。"""
    if not await service.delete_episode(episode_id, actor=ACTOR):
        raise HTTPException(status_code=404, detail="episode not found")


@router.post("/projects/{project_id}/episodes/reorder",
             response_model=list[StudioEpisode])
async def reorder_episodes(
    project_id: str, payload: StudioReorder
) -> list[StudioEpisode]:
    """``ids`` の並び順をそのまま ``sort_order`` にする（この作品の話を全件送る）。"""
    if await service.get_project(project_id) is None:
        raise HTTPException(status_code=404, detail="project not found")
    try:
        return await service.reorder_episodes(project_id, payload.ids, actor=ACTOR)
    except service.StudioError as exc:
        raise _bad_request(exc) from exc


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
            scene_id,
            actor=ACTOR,
            base_revision=payload.base_revision,
            **payload.changes(),
        )
    except service.StudioError as exc:
        raise _bad_request(exc) from exc
    if scene is None:
        raise HTTPException(status_code=404, detail="scene not found")
    return scene


@router.delete("/scenes/{scene_id}", status_code=204)
async def delete_scene(scene_id: str) -> None:
    """場だけ消す（そこにいた Shot は未分類に戻る）。"""
    if not await service.delete_scene(scene_id, actor=ACTOR):
        raise HTTPException(status_code=404, detail="scene not found")


@router.post("/episodes/{episode_id}/scenes/reorder",
             response_model=list[StudioScene])
async def reorder_scenes(episode_id: str, payload: StudioReorder) -> list[StudioScene]:
    """``ids`` の並び順をそのまま ``sort_order`` にする（この話の場を全件送る）。"""
    if await service.get_episode(episode_id) is None:
        raise HTTPException(status_code=404, detail="episode not found")
    try:
        return await service.reorder_scenes(episode_id, payload.ids, actor=ACTOR)
    except service.StudioError as exc:
        raise _bad_request(exc) from exc


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
        shot = await service.update_shot(
            shot_id,
            payload.changes(),
            actor=ACTOR,
            base_revision=payload.base_revision,
        )
    except service.StudioError as exc:
        raise _bad_request(exc) from exc
    if shot is None:
        raise HTTPException(status_code=404, detail="shot not found")
    return shot


@router.post("/projects/{project_id}/shots/reorder",
             response_model=list[StudioShot])
async def reorder_shots(
    project_id: str, payload: StudioShotReorder
) -> list[StudioShot]:
    """``shot_ids`` の並び順をそのまま ``sort_order`` にする。

    並び順は**場の中**のものなので、1 つの場（または未分類グループ）の Shot を
    全件、過不足なく並べたものを送る。作品の Shot 全件も受け取れる（その場合は
    場ごとに切り分けて書き戻す）。
    """
    if await service.get_project(project_id) is None:
        raise HTTPException(status_code=404, detail="project not found")
    try:
        return await service.reorder_shots(project_id, payload.shot_ids, actor=ACTOR)
    except service.StudioError as exc:
        raise _bad_request(exc) from exc


@router.get("/shots/{shot_id}/prompt-preview", response_model=StudioShotPreview)
async def preview_shot_prompt(shot_id: str) -> StudioShotPreview:
    """このカットを今生成したら**実際に投入されるもの**（読み取りだけ）。

    レンダリングの前にここで確認する。生成と同じ組み立てを通すが、Grok の
    英訳は走らせない（``will_translate`` で入るかどうかだけ伝える。使える
    英語キャッシュがあれば False）。組み立てられないカットも 400 ではなく、
    理由を ``error`` に入れた 200 で返す。組み立てはできるが材料が足りなくて
    投入だけができない（連続カットの引き継ぎ元がまだ無い）ときは ``error``
    ではなく ``render_blocker`` に理由が入る。
    """
    preview = await service.preview_shot(shot_id)
    if preview is None:
        raise HTTPException(status_code=404, detail="shot not found")
    return preview


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
        if isinstance(upload, FormUploadFile) and upload.filename:
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
            asset_id,
            actor=ACTOR,
            base_revision=payload.base_revision,
            **payload.changes(),
        )
    except service.StudioError as exc:
        raise _bad_request(exc) from exc
    if asset is None:
        raise HTTPException(status_code=404, detail="studio asset not found")
    return asset


@router.post("/assets/{asset_id}/file", response_model=StudioAsset)
async def replace_asset_file(
    asset_id: str, file: UploadFile = File(...)
) -> StudioAsset:
    """素材のメインのファイルを差し替える（メタデータのみの素材にも付けられる）。

    実体は ``assets/<kind>/`` に置き、種別は添付の拡張子から決まる。
    """
    if await service.get_asset(asset_id) is None:
        raise HTTPException(status_code=404, detail="studio asset not found")
    kind = _kind_of(file.filename or "")
    saved = await save_upload(file, kind)
    try:
        asset = await service.update_asset(
            asset_id, actor=ACTOR, path=saved.path, kind=kind
        )
    except service.StudioError as exc:
        raise _bad_request(exc) from exc
    if asset is None:
        raise HTTPException(status_code=404, detail="studio asset not found")
    return asset


@router.get("/assets/{asset_id}/files", response_model=list[StudioAssetFile])
async def list_asset_files(asset_id: str) -> list[StudioAssetFile]:
    """素材に付いている追加リファレンスの一覧（メインのファイルは含まない）。"""
    files = await service.list_asset_files(asset_id)
    if files is None:
        raise HTTPException(status_code=404, detail="studio asset not found")
    return files


@router.post("/assets/{asset_id}/files", response_model=StudioAssetFile,
             status_code=201)
async def add_asset_file(
    asset_id: str,
    file: UploadFile = File(...),
    #: image = 追加画像 / voice = 声サンプル / video = 動画リファレンス
    role: str = Form("image"),
    caption: str = Form(""),
) -> StudioAssetFile:
    """素材にリファレンスを 1 本足す（別アングルなど。メインのファイルは変わらない）。"""
    if await service.get_asset(asset_id) is None:
        raise HTTPException(status_code=404, detail="studio asset not found")
    kind = ASSET_FILE_ROLE_KINDS.get(role)
    if kind is None:
        raise HTTPException(
            status_code=400,
            detail=f"unknown role '{role}'"
            f" (allowed: {', '.join(ASSET_FILE_ROLE_KINDS)})",
        )
    saved = await save_upload(file, kind)
    try:
        return await service.add_asset_file(
            asset_id, role=role, path=saved.path, caption=caption, actor=ACTOR
        )
    except service.StudioError as exc:
        raise _bad_request(exc) from exc


@router.delete("/asset-files/{file_id}", status_code=204)
async def delete_asset_file(file_id: str) -> None:
    """リファレンスを 1 本外す（ファイル実体は ``assets/`` に残る）。"""
    if not await service.delete_asset_file(file_id, actor=ACTOR):
        raise HTTPException(status_code=404, detail="studio asset file not found")


@router.delete("/assets/{asset_id}", status_code=204)
async def delete_asset(asset_id: str) -> None:
    """素材を目録から外す（ファイル実体は ``assets/`` に残る）。"""
    if not await service.delete_asset(asset_id, actor=ACTOR):
        raise HTTPException(status_code=404, detail="studio asset not found")


# --------------------------------------------------------------------------
# Take（Shot の生成）
# --------------------------------------------------------------------------

@router.post("/shots/{shot_id}/translate", response_model=StudioShot)
async def translate_shot(shot_id: str) -> StudioShot:
    """組み立て済み本文の英訳を開始する（Grok は裏で走り、完了は Shot を見る）。"""
    try:
        shot = await service.translate_shot(shot_id, actor=ACTOR)
    except service.StudioError as exc:
        raise _bad_request(exc) from exc
    if shot is None:
        raise HTTPException(status_code=404, detail="shot not found")
    return shot


@router.get("/shots/{shot_id}/takes", response_model=list[StudioTake])
async def list_takes(shot_id: str) -> list[StudioTake]:
    if await service.get_shot(shot_id) is None:
        raise HTTPException(status_code=404, detail="shot not found")
    return await service.list_takes(shot_id)


@router.post("/shots/{shot_id}/render", response_model=StudioTake, status_code=201)
async def render_shot(
    shot_id: str, payload: StudioRenderRequest | None = None
) -> StudioTake:
    """Shot を 1 回生成する（未完了 Take が上限に達していれば 429）。

    ボディは内部 API と同じ任意の上書き（解像度・尺・ステップ数・シード）で、
    送らなければ今までどおり Shot / プロジェクトの設定で焼く。
    """
    if await service.get_shot(shot_id) is None:
        raise HTTPException(status_code=404, detail="shot not found")
    # 数えてから投入するまでを錠で括る（並行リクエストが数え合いになって、
    # 上限に達していても全部すり抜けるのを防ぐ）
    async with service.PENDING_JOBS_LOCK:
        await _check_pending_jobs()
        try:
            return await service.render_shot(shot_id, payload)
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


@router.post("/takes/{take_id}/cancel", response_model=StudioTake)
async def cancel_take(take_id: str) -> StudioTake:
    """走っている Take を止める（行は残る。状態はジョブから導出される）。"""
    take = await service.cancel_take(take_id)
    if take is None:
        raise HTTPException(status_code=404, detail="take not found")
    return take


@router.delete("/takes/{take_id}", status_code=204)
async def delete_take(take_id: str) -> None:
    """Take を目録から外す（実行中ならジョブも止める。成果物は履歴に残る）。"""
    if not await service.delete_take(take_id, actor=ACTOR):
        raise HTTPException(status_code=404, detail="take not found")


# --------------------------------------------------------------------------
# 汎用ジョブ（素材の静止画・BGM / SE など、Shot を通さない生成）
# --------------------------------------------------------------------------

@router.get("/jobs", response_model=list[Job])
async def list_jobs(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> list[Job]:
    """新しい順のジョブ一覧（ワークフロー JSON は含まない）。"""
    return await job_service.list_jobs(limit=limit, offset=offset)


@router.post("/jobs", response_model=Job, status_code=201)
async def create_job(payload: JobFromForm | JobCreate) -> Job:
    """ジョブを 1 件作ってキューに載せる（未完了ジョブが上限に達していれば 429）。

    Shot を通さない生成の入り口で、素材にする静止画（``image_only``）や
    BGM / SE（``audio``）もここから作る。モードごとの必須項目を満たして
    いなければ 422。

    ``mode: "remotion"`` は同梱の Remotion プロジェクトのレンダリング（SPEC §5.2）。
    ``remotion_composition``（``GET /remotion/compositions`` に出る ID）と
    ``remotion_props`` が要り、連携が有効でなければ 400。
    出来た mp4 はほかのジョブと同じく ``video_url`` に出る。

    ``mode: "audio_analysis"`` は音源解析（SPEC §5.2）。``analysis`` に解析する
    音源（``audio``）と、あれば歌詞（``lyrics``）・ステム（``stems``）・回す解析
    （``tasks``）を渡す。生成物は ``outputs/{job_id}/analysis.json`` 1 つで、
    ``GET /jobs/{id}`` の ``analysis_url`` に出る。解析用の依存が入っていなければ
    400（何を入れればよいかを本文で返す）。

    ``{"from_form": true}`` を入れると、いま画面に出ている**生成フォームの
    下書き**（``/api/v1/ui/generate-form``）をそのまま投入する。一緒に送った
    項目はその上から重ねる（「今のフォームで、尺だけ 5 秒にして流して」）。
    写せない下書き（ワークフロー id が壊れている等）は 400。
    """
    if isinstance(payload, JobFromForm):
        payload = await _from_form_job(payload)
    # 数えてから投入するまでを錠で括る（並行リクエストが数え合いになって、
    # 上限に達していても全部すり抜けるのを防ぐ）
    async with service.PENDING_JOBS_LOCK:
        await _check_pending_jobs()
        try:
            return await job_service.create_job(payload)
        except job_service.JobBackendUnavailable as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except job_service.JobValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc


async def _from_form_job(payload: JobFromForm) -> JobCreate:
    """保存中の下書きに、一緒に送られた項目を重ねて :class:`JobCreate` にする。

    一緒に送られた項目だけを上書きに使いたいので、既定値で埋まったモデルでは
    なく ``extra``（宣言していないキー）をそのまま重ねる。
    """
    draft = await ui_state.get()
    try:
        fields = ui_state.job_fields(draft.values)
    except ui_state.UiStateError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    overrides = dict(payload.model_extra or {})
    try:
        return JobCreate(**{**fields, **overrides})
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=_first_message(exc)) from exc


@router.get("/jobs/{job_id}", response_model=Job)
async def get_job(job_id: str) -> Job:
    """ジョブの状態と成果物（読み取りのみ。完了待ちはここをポーリングする）。

    成果物は種類ごとに別の項目に出る: 画像は ``image_url``、動画は ``video_url``、
    音声は ``audio_output_url``、音源解析の JSON は ``analysis_url``。
    """
    job = await job_service.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return job


@router.post("/jobs/{job_id}/cancel", response_model=Job)
async def cancel_job(job_id: str) -> Job:
    """実行中・待ちのジョブを 1 件止める。終端状態は冪等にそのまま返す。"""
    job = await job_service.cancel_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return job


@router.post("/jobs/{job_id}/rerun", response_model=Job, status_code=201)
async def rerun_job(job_id: str, payload: JobRerun | None = None) -> Job:
    """保存したパラメータからワークフローを組み直して焼き直す（既定はシード振り直し）。"""
    async with service.PENDING_JOBS_LOCK:
        await _check_pending_jobs()
        try:
            return await job_service.rerun_job(job_id, payload or JobRerun())
        except LookupError as exc:
            raise HTTPException(status_code=404, detail="job not found") from exc
        except job_service.JobBackendUnavailable as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except job_service.JobValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/jobs/{job_id}/continue", response_model=Job, status_code=201)
async def continue_job(job_id: str, payload: JobContinue | None = None) -> Job:
    """このジョブの最終フレームから続きを生成する（SPEC §2 のモード B）。"""
    async with service.PENDING_JOBS_LOCK:
        await _check_pending_jobs()
        try:
            return await job_service.continue_job(job_id, payload or JobContinue())
        except LookupError as exc:
            raise HTTPException(status_code=404, detail="job not found") from exc
        except job_service.JobValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc


# --------------------------------------------------------------------------
# Remotion（SPEC §5.2）
# --------------------------------------------------------------------------

@router.get("/remotion/compositions", response_model=RemotionCompositions)
async def remotion_compositions() -> RemotionCompositions:
    """同梱の Remotion プロジェクト（``remotion/``）が持つ composition の ID 一覧。

    ここに出た ID を ``POST /api/v1/jobs`` に ``{"mode": "remotion",
    "remotion_composition": …, "remotion_props": {…}}`` で渡すとレンダリングが
    ふつうのジョブとしてキューに載る（進捗は ``GET /api/v1/jobs/{id}``）。

    連携が有効でない・依存が入っていない・``npx remotion`` が失敗した場合は
    400（理由をそのまま返す）。
    """
    try:
        return RemotionCompositions(compositions=await remotion_service.list_compositions())
    except remotion_service.RemotionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


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
        limit = await _check_pending_jobs()
    try:
        return await service.create_story(
            payload, actor=ACTOR, pending_limit=limit
        )
    except service.StudioError as exc:
        raise _bad_request(exc) from exc


# --------------------------------------------------------------------------
# ライブラリ（履歴とは別に取っておく素材の棚。削除は公開しない）
# --------------------------------------------------------------------------

@router.get("/library", response_model=LibraryPage)
async def list_library(
    kind: str | None = Query(None, pattern="^(image|video|audio)$"),
    #: 分類（未指定 = 全件 / 'none' = 未分類のみ）
    category: str | None = Query(None),
    #: 表示名とタグへの部分一致（大文字小文字は無視）
    q: str = Query(""),
    #: タグの完全一致
    tag: str | None = Query(None),
    limit: int = Query(library.DEFAULT_LIMIT, ge=1, le=LIBRARY_MAX_LIMIT),
    offset: int = Query(0, ge=0),
) -> LibraryPage:
    """絞り込んだ 1 ページ分（``total`` で「まだ何件あるか」が分かる）。"""
    try:
        items, total = await library.search_items(
            kind=kind, category=category, query=q, tag=tag, limit=limit, offset=offset
        )
    except library.LibraryError as exc:
        raise _library_bad_request(exc) from exc
    return LibraryPage(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
        tags=await library.all_tags(),
    )


@router.post("/library/from-job", response_model=LibraryItem, status_code=201)
async def add_library_from_job(payload: LibraryFromJob) -> LibraryItem:
    """ジョブの出力をライブラリへ登録する（NSFW フラグは元ジョブを引き継ぐ）。

    同じ出力が既に棚にあれば 409（本文に既存のアイテムを添える）。
    """
    job = await job_service.get_job(payload.job_id, include_workflow=False)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    try:
        item = await library.add_from_job(
            job, payload.source, payload.name, payload.tags, payload.category
        )
    except library.LibraryDuplicate as exc:
        raise HTTPException(
            status_code=409,
            detail={"message": str(exc), "item": exc.item.model_dump(mode="json")},
        ) from exc
    except library.LibraryError as exc:
        raise _library_bad_request(exc) from exc
    # 表示名とタグを Grok に考えさせる（指定済みのものは触らない、SPEC §7.2）
    autotag.spawn_for(item, job, named=bool(payload.name.strip()))
    return item


@router.post("/library/sheet", response_model=LibraryItem, status_code=201)
async def create_library_sheet(payload: LibrarySheet) -> LibraryItem:
    """ライブラリの画像を 1 枚のリファレンスシートに合成して登録する。

    ``item_ids`` の並び順に左上から置き、``character`` の素材だけ大きい
    パネルになる。``/library/{item_id}`` より先に定義しておく。
    """
    try:
        return await library.add_sheet(
            payload.item_ids,
            payload.name,
            payload.width or sheets.DEFAULT_WIDTH,
            payload.height or sheets.DEFAULT_HEIGHT,
        )
    except library.LibraryError as exc:
        raise _library_bad_request(exc) from exc


@router.patch("/library/{item_id}", response_model=LibraryItem)
async def update_library_item(item_id: str, payload: LibraryUpdate) -> LibraryItem:
    """表示名 / NSFW フラグ / タグ / 分類の変更（指定した項目だけ）。

    ``category`` に ``"none"`` を送ると未分類に戻す（送らなければそのまま）。
    """
    try:
        item = await library.update_item(
            item_id,
            name=payload.name,
            nsfw=payload.nsfw,
            tags=payload.tags,
            category=payload.category,
        )
    except library.LibraryError as exc:
        raise _library_bad_request(exc) from exc
    if item is None:
        raise HTTPException(status_code=404, detail="library item not found")
    return item


@router.post("/library/image", response_model=LibraryItem, status_code=201)
async def upload_library_image(
    file: UploadFile = File(...),
    #: 表示名（空なら元のファイル名）
    name: str = Form(""),
    #: multipart はリストを送りにくいのでカンマ区切りで受ける
    tags: str = Form(""),
    #: 分類（空なら未分類）
    category: str = Form(""),
    nsfw: bool = Form(False),
) -> LibraryItem:
    """手元の画像をライブラリへ入れる（multipart。``/library/{item_id}`` より先）。

    Docker で動かしているときは、ホストの絶対パスはアプリから見えないので
    **こちらを使う**（JSON の ``path`` はコンテナの中のパスとして解釈される）。
    入れた項目の ``id`` をそのまま ``POST /library/{id}/key`` に渡せる。
    """
    return await upload_to_library(
        "image", file, name=name, tags=tags, category=category, nsfw=nsfw
    )


@router.post("/library/audio", response_model=LibraryItem, status_code=201)
async def upload_library_audio(
    file: UploadFile = File(...),
    #: 表示名（空なら元のファイル名）
    name: str = Form(""),
    #: multipart はリストを送りにくいのでカンマ区切りで受ける
    tags: str = Form(""),
    #: 分類（空なら未分類）
    category: str = Form(""),
    nsfw: bool = Form(False),
) -> LibraryItem:
    """手元の音源をライブラリへ入れる（multipart。``/library/{item_id}`` より先）。

    **タイムラインに置ける音は棚（library）の音だけ**なので、MV の音源はここから
    入れる（作品の素材 ``assets`` に上げた音は素材ビンに出てこない）。返った
    ``id`` を ``PUT /timelines/{id}/clips`` の ``source_kind: "library"`` /
    ``source_id`` に渡して A1 へ置く。
    """
    return await upload_to_library(
        "audio", file, name=name, tags=tags, category=category, nsfw=nsfw
    )


@router.post("/library/upload", response_model=LibraryItem, status_code=201)
async def upload_library_any(
    file: UploadFile = File(...),
    name: str = Form(""),
    tags: str = Form(""),
    category: str = Form(""),
    nsfw: bool = Form(False),
) -> LibraryItem:
    """種別を書かずに 1 ファイル入れる（拡張子 / MIME で image / video / audio）。

    ``/library/image`` と ``/library/audio`` の汎用版（``/library/{item_id}``
    より先に定義しておく）。
    """
    return await upload_detecting_kind(
        file, name=name, tags=tags, category=category, nsfw=nsfw
    )


@router.post("/library/key", response_model=LibraryItem, status_code=201)
async def key_library_source(payload: LibraryKeySource) -> LibraryItem:
    """``source`` で指した画像の背景を抜いてスプライトにする（SPEC §7.2）。

    ``source`` は ``job_id``（+ ``source``）/ ``item_id`` / ``export_id`` /
    ``path``（``/assets/…`` の World Bible 素材も指せる）のどれか 1 つ。
    ``/library/{item_id}`` より先に定義しておく。
    """
    return await key_media_source(payload)


@router.post("/library/key-from-job", response_model=LibraryItem, status_code=201)
async def key_library_from_job(payload: LibraryKeyFromJob) -> LibraryItem:
    """ジョブの生成画像の背景を抜いてスプライトにする（SPEC §7.2）。

    棚に入れてから抜く 2 手を 1 手にする入り口。``/library/{item_id}`` より先に
    定義しておく。
    """
    return await key_job_output(payload)


@router.post("/library/{item_id}/key", response_model=LibraryItem, status_code=201)
async def key_library_item_route(
    item_id: str, payload: LibraryKey | None = None
) -> LibraryItem:
    """棚の画像の背景を抜いて、透過 PNG の**新しい素材**にする（元は触らない）。

    ``method`` は ``black`` / ``white``（floodfill 方式のルミナンスキー。文字の
    内側の同色は穴として残る）/ ``chroma``（``color`` との距離）/ ``rembg``
    （任意依存。入っていなければ 400）。できた PNG の ``url`` を Remotion の
    ``sprite`` / ``imageSlam`` の ``src`` にそのまま書ける。``flatten`` に色を
    書くと、抜いたあとの不透明部分をその色一色に塗る（白抜きロゴ用）。
    """
    return await key_library_item(item_id, payload or LibraryKey())


# --------------------------------------------------------------------------
# 素材の下ごしらえ（フォント画像とコンタクトシート）
# --------------------------------------------------------------------------

@router.get("/images/text/fonts", response_model=FontList)
async def list_text_fonts() -> FontList:
    """使える書体の一覧（``POST /images/text`` の ``font`` に書く名前）。"""
    return font_list()


@router.post("/images/text", response_model=LibraryItem, status_code=201)
async def create_text_image_route(payload: TextImage) -> LibraryItem:
    """フォントで組んだ文字を PNG にしてライブラリへ登録する（SPEC §7.2）。

    背景は既定で透明なので、そのままスプライトとして貼れる。日本語が誤字になる
    画像生成に**字形の参照**として添えるのにも使う。
    """
    return await create_text_image(payload)


@router.post("/videos/contact-sheet", response_model=ContactSheetResult,
             status_code=201)
async def create_contact_sheet_route(payload: ContactSheet) -> ContactSheetResult:
    """動画のコマを 1 枚のグリッド画像に束ねてライブラリへ登録する（SPEC §7.2）。

    ``source`` は ``job_id`` / ``item_id`` / ``export_id`` / ``path`` のどれか
    1 つ。抜く秒は ``seconds`` → ``range`` → ``frames`` の順に見て、どれも
    無ければ尺を等分した位置になる。**演出の配置を触ったら必ずこれで確かめる。**
    """
    return await create_contact_sheet(payload)


# --------------------------------------------------------------------------
# 画面（生成フォームの下書きと、ブラウザの画面移動）
# --------------------------------------------------------------------------
#
# 「エージェントがフォームを埋めて、人が確かめてから押す」「エージェントが人の
# 画面を目的の場所へ連れて行く」ための 2 本。どちらも DB / WS を経由して、開いて
# いるブラウザへ届く（:mod:`app.ui_state` / :mod:`app.routers.ui`）。

@router.get("/ui/generate-form", response_model=UiFormState)
async def get_generate_form() -> UiFormState:
    """生成フォームの下書き（値と ``revision``）。

    ``revision`` は保存のたびに 1 つ上がる連番。書き換えるときに
    ``base_revision`` として返すと、その間に人が触っていれば 409 で弾かれる。
    """
    return await ui_state.get()


@router.patch("/ui/generate-form", response_model=UiFormState)
async def patch_generate_form(payload: UiFormUpdate) -> UiFormState:
    """下書きの**送ったキーだけ**を書き換える（触れなかった項目は今のまま）。

    ``base_revision`` を省略すると現在値を見ずに上書きする。付けた場合、それが
    今より古ければ 409（body に現在値が入る）、未来なら 400。
    """
    try:
        state = await ui_state.patch(
            payload.values,
            updated_by="external",
            base_revision=payload.base_revision,
        )
    except ui_state.UiStateConflict as exc:
        raise HTTPException(
            status_code=409,
            detail={"message": str(exc), "current": exc.current.model_dump()},
        ) from exc
    except ui_state.UiStateError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await ws.publish_form(state.revision, state.updated_by, state.values)
    return state


@router.post("/ui/navigate", status_code=204)
async def navigate(payload: UiNavigate) -> None:
    """開いているブラウザの表示を切り替えさせる（生成 / スタジオ / 設定）。

    ``project_id`` / ``shot_id`` は実在と噛み合わせを確かめてから流す
    （存在しないものへ飛ばして画面を空にしないため）。ブラウザが 1 つも
    開いていなくても成功する（誰も受け取らないだけ）。
    """
    project_id = (payload.project_id or "").strip() or None
    shot_id = (payload.shot_id or "").strip() or None
    if payload.view != "studio" and (project_id or shot_id):
        raise HTTPException(
            status_code=400,
            detail=f"view '{payload.view}' では project_id / shot_id は指定できません",
        )
    if shot_id and not project_id:
        raise HTTPException(
            status_code=400, detail="shot_id を渡すなら project_id も必要です"
        )
    if project_id and await service.get_project(project_id) is None:
        raise HTTPException(status_code=404, detail="project not found")
    if shot_id:
        shot = await service.get_shot(shot_id)
        if shot is None:
            raise HTTPException(status_code=404, detail="shot not found")
        if shot.project_id != project_id:
            raise HTTPException(
                status_code=400, detail="そのカットは指定の作品のものではありません"
            )
    await ws.publish_navigate(payload.view, project_id, shot_id)


# --------------------------------------------------------------------------
# 参照系（接続先でできること・フォームの選択肢・この API の仕様書）
# --------------------------------------------------------------------------

@router.get("/capabilities", response_model=StudioCapabilities)
async def capabilities() -> StudioCapabilities:
    """いまの接続先でスタジオの追加機能（ラテント連続性 / アップスケール）が
    使えるか。接続できないときも 500 にはせず「使えない」＋理由を返す。"""
    return await get_capabilities()


@router.get("/options", response_model=Options)
async def options() -> Options:
    """生成フォームの選択肢（読み取り専用）。

    ``aspect_ratio`` の正しい表記・ワークフローの一覧と制約・LoRA・ライブラリ
    などが入る。接続先の URL は外に出さない（ComfyUI の所在は秘密のため
    ``comfy_url`` は空にする）。ComfyUI が落ちていることは ``comfy_error`` に
    入って 200 で返る。
    """
    payload = await get_options()
    payload.comfy_url = ""
    return payload


def _collect_schema_refs(node: Any, schemas: dict[str, Any], kept: set[str]) -> None:
    """``node`` から辿れる ``#/components/schemas/...`` を再帰的に集める。"""
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
            name = ref.rsplit("/", 1)[-1]
            if name not in kept and name in schemas:
                kept.add(name)
                _collect_schema_refs(schemas[name], schemas, kept)
        for value in node.values():
            _collect_schema_refs(value, schemas, kept)
    elif isinstance(node, list):
        for value in node:
            _collect_schema_refs(value, schemas, kept)


@router.get("/openapi.json")
async def openapi_subset(request: Request) -> dict[str, Any]:
    """この API（``/api/v1``）だけに絞った OpenAPI（エージェントに読ませる用）。

    アプリ全体のスキーマから ``/api/v1`` で始まるパスと、そこから ``$ref`` で
    辿れるスキーマだけを抜き出した縮小版。内部 API（UI 用）は載らない。
    """
    full = request.app.openapi()
    paths = {
        path: item
        for path, item in full.get("paths", {}).items()
        if path.startswith(f"{router.prefix}/")
    }
    schemas = full.get("components", {}).get("schemas", {})
    kept: set[str] = set()
    _collect_schema_refs(paths, schemas, kept)
    subset: dict[str, Any] = {
        "openapi": full.get("openapi", "3.1.0"),
        "info": full.get("info", {}),
        "paths": paths,
    }
    if kept:
        subset["components"] = {
            "schemas": {name: schemas[name] for name in sorted(kept)}
        }
    return subset


# --------------------------------------------------------------------------
# 脚本ドラフト作成ガイド
# --------------------------------------------------------------------------

@router.get("/prompt-guide", response_model=DraftingGuide)
async def prompt_guide() -> DraftingGuide:
    """脚本ドラフトの書き方（上の一括投入に渡す脚本を書くための手引き）。

    外部のエージェントがそのままプロンプトに貼れる日本語 Markdown を返す。
    本文はアプリ内の定数から組み立てるので（:mod:`app.drafting_guide`）、
    尺の範囲や H3 の規約が変われば、この応答も一緒に変わる。
    """
    return build_drafting_guide()


@router.get("/prompt-examples", response_model=PromptExamples)
async def prompt_examples(
    mode: str | None = Query(None, description="t2v / i2v / fl2v / l2v / r2v / edit"),
    category: str | None = Query(None, description="cinematic / dialogue / …"),
    id: str | None = Query(None, description="H3-E4 のような例の id"),
    limit: int | None = Query(None, ge=1, le=50),
) -> PromptExamples:
    """MiniMax H3 の実例（上のガイドが参照している few-shot の全集）。

    絞り込みを 1 つも指定しなければ**索引**（本文なし）を返し、``id`` か
    ``mode`` / ``category`` を指定するとその本文まで返す。選び方は内蔵
    エージェントの `get_prompt_examples` と同じ関数
    （:func:`app.h3_examples.select_examples`）を通す。
    """
    if mode and mode not in MODES:
        raise HTTPException(
            status_code=400,
            detail=f"unknown mode (use one of {', '.join(MODES)})",
        )
    if category and category not in CATEGORIES:
        raise HTTPException(
            status_code=400,
            detail=f"unknown category (use one of {', '.join(CATEGORIES)})",
        )
    if id:
        picked = select_examples(ids=(id,))
        if not picked:
            raise HTTPException(status_code=404, detail="example not found")
    else:
        picked = select_examples(
            mode=mode, category=category, tier=None, limit=limit
        )
    # 索引（絞り込み無し）は本文を落として軽くする
    with_body = bool(id or mode or category)
    return PromptExamples(
        guide_version=GUIDE_VERSION,
        modes=available_modes(),
        categories=available_categories(),
        total=len(picked),
        examples=[_prompt_example(x, with_body) for x in picked],
    )


def _prompt_example(example: H3Example, with_body: bool) -> PromptExample:
    return PromptExample(
        id=example.id,
        mode=example.mode,
        categories=list(example.categories),
        summary=example.summary,
        tier=example.tier,
        source=example.source,
        note=example.note,
        body=example.body if with_body else None,
    )


# --------------------------------------------------------------------------
# 編集タブ（タイムライン -> トラック -> クリップ -> 書き出し）
# --------------------------------------------------------------------------

async def _check_running_exports() -> int:
    """走っている書き出しのガード。返り値は上限。

    :func:`_check_pending_jobs` と同じ ``external_max_pending_takes`` を、
    ffmpeg を回す書き出しにも掛ける。ただし**数えるプールは別**で、GPU を使う
    生成（ジョブ / Take）とは互いの枠を食い合わない（走るのが CPU の ffmpeg で、
    生成の待ち行列とは詰まり方が違うため）。同じタイムラインの二重書き出しは
    :func:`app.timeline.start_export` が 409 で断るが、別々のタイムラインへ
    次々投入されるぶんはここで止める。
    """
    return await _check_running(
        timeline_service.count_running_exports, "走っている書き出し"
    )


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
        return await timeline_service.create_timeline(
            project_id, payload or StudioTimelineCreate()
        )
    except timeline_service.TimelineError as exc:
        raise _timeline_error(exc) from exc


@router.get("/projects/{project_id}/timelines", response_model=list[StudioTimeline])
async def list_timelines(project_id: str) -> list[StudioTimeline]:
    """その作品のタイムライン（古い順。中身は含めない）。"""
    if await service.get_project(project_id) is None:
        raise HTTPException(status_code=404, detail="project not found")
    return await timeline_service.list_timelines(project_id)


@router.get("/timelines/{timeline_id}", response_model=StudioTimelineDetail)
async def get_timeline(timeline_id: str) -> StudioTimelineDetail:
    """トラックとクリップ込みのフル EDL。

    クリップにはソースを解決した ``video_url`` / ``source_duration_ms`` と、
    実ファイルが無いことを示す ``missing`` が付く。
    """
    detail = await timeline_service.timeline_detail(timeline_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="timeline not found")
    return detail


@router.patch("/timelines/{timeline_id}", response_model=StudioTimeline)
async def update_timeline(
    timeline_id: str, payload: StudioTimelineUpdate
) -> StudioTimeline:
    """指定した項目だけ変える（送らなければ今の値のまま）。"""
    try:
        timeline = await timeline_service.update_timeline(
            timeline_id, payload.model_dump()
        )
    except timeline_service.TimelineError as exc:
        raise _timeline_error(exc) from exc
    if timeline is None:
        raise HTTPException(status_code=404, detail="timeline not found")
    return timeline


@router.delete("/timelines/{timeline_id}", status_code=204)
async def delete_timeline(timeline_id: str) -> None:
    """タイムラインとその中身を消す（書き出したファイルは残る）。

    脚本や素材と違って作り直しが利く（同じ話からいつでも組み直せる）ので、
    これだけは外部にも開ける。
    """
    if not await timeline_service.delete_timeline(timeline_id):
        raise HTTPException(status_code=404, detail="timeline not found")


@router.put("/timelines/{timeline_id}/clips", response_model=StudioTimelineDetail)
async def replace_clips(
    timeline_id: str, payload: TimelineClipsUpdate
) -> StudioTimelineDetail:
    """クリップを丸ごと置き換える（EDL 全置換）。

    同じトラックの中で重なっているもの、``in_ms >= out_ms`` のもの、尺と切り出しの
    長さが食い違うもの（フェーズ 1 は等速のみ）は 400 で断る。
    """
    try:
        detail = await timeline_service.replace_clips(timeline_id, payload.clips)
    except timeline_service.TimelineError as exc:
        raise _timeline_error(exc) from exc
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

    下のクリップの切り出しは動かさないので**トラックの全長は変わらない**。
    音源基準で組んだ並びを崩さずに、短いカットを割り込ませるための入り口。
    ``base_revision`` を添えると、それ以降に同じタイムラインが触られていた
    場合だけ 409。
    """
    try:
        return await timeline_service.insert_clip(
            timeline_id, payload, actor="external"
        )
    except timeline_service.TimelineError as exc:
        raise _timeline_error(exc) from exc


@router.post(
    "/timelines/{timeline_id}/tracks",
    response_model=StudioTimelineDetail,
    status_code=201,
)
async def add_track(
    timeline_id: str, payload: TimelineTrackCreate | None = None
) -> StudioTimelineDetail:
    """トラックを 1 本足す（映像トラックは V1 の 1 本きりなので 400）。"""
    try:
        return await timeline_service.add_track(
            timeline_id, payload or TimelineTrackCreate()
        )
    except timeline_service.TimelineError as exc:
        raise _timeline_error(exc) from exc


@router.patch(
    "/timelines/{timeline_id}/tracks/{track_id}", response_model=StudioTimelineDetail
)
async def update_track(
    timeline_id: str, track_id: str, payload: TimelineTrackUpdate
) -> StudioTimelineDetail:
    """名前・ミュート・ロックを変える（送らなかった項目はそのまま）。"""
    try:
        return await timeline_service.update_track(timeline_id, track_id, payload)
    except timeline_service.TimelineError as exc:
        raise _timeline_error(exc) from exc


@router.delete(
    "/timelines/{timeline_id}/tracks/{track_id}", response_model=StudioTimelineDetail
)
async def delete_track(timeline_id: str, track_id: str) -> StudioTimelineDetail:
    """トラックを 1 本消す（載っていたクリップも一緒に消える）。"""
    try:
        return await timeline_service.delete_track(timeline_id, track_id)
    except timeline_service.TimelineError as exc:
        raise _timeline_error(exc) from exc


# --------------------------------------------------------------------------
# FX トラック（タイムラインに載せる演出。SPEC §7.3）
# --------------------------------------------------------------------------
#
# 演出は**タイムラインに保存する**のが正: ここへ入れておけば編集画面のプレビュー
# に重なり、人が秒・位置を直したり要らないものを消したりでき、``fx: true`` の
# 書き出しでそのまま焼ける。``mode: "remotion"`` のジョブへ props を直接投げる
# のは、手元で 1 本だけ確かめたいときの近道。

@router.get("/timelines/{timeline_id}/fx", response_model=TimelineFx)
async def get_fx(timeline_id: str) -> TimelineFx:
    """このタイムラインに載せた演出。

    ``theme`` / ``seed`` / ``ambient`` / ``backgroundColor`` は ``FxOverlay`` の
    props と同じ名前。``events`` は ``{id, enabled, event}`` の配列で、``event``
    が ``FxOverlay`` の 1 イベントそのもの。
    """
    fx = await timeline_service.get_fx(timeline_id)
    if fx is None:
        raise HTTPException(status_code=404, detail="timeline not found")
    return fx


@router.put("/timelines/{timeline_id}/fx", response_model=TimelineFx)
async def replace_fx(timeline_id: str, payload: TimelineFxUpdate) -> TimelineFx:
    """演出を丸ごと置き換える（``FxOverlay`` の props をそのまま投げられる）。

    ``events`` は生のイベント（``{"type": …, "t": …}``）でも、GET が返す
    ``{"id", "enabled", "event"}`` の形でも受ける（``id`` を省略すると採番）。
    ``base`` / ``audio`` / ``fps`` / ``width`` / ``height`` /
    ``durationInSeconds`` は**タイムラインが持っている**ので無視する。

    検証は「``event`` がオブジェクトで ``type`` が文字列・``t`` が数値」まで。
    中身の正本は Remotion の zod スキーマなので、細かい誤りはプレビューと
    レンダで出る。
    """
    try:
        return await timeline_service.replace_fx(
            timeline_id, payload, actor="external"
        )
    except timeline_service.TimelineError as exc:
        raise _timeline_error(exc) from exc


@router.post(
    "/timelines/{timeline_id}/fx/events", response_model=TimelineFx, status_code=201
)
async def add_fx_event(
    timeline_id: str, payload: TimelineFxEventCreate
) -> TimelineFx:
    """演出のイベントを 1 つ足す（``sort_order`` を省略すると末尾）。"""
    try:
        return await timeline_service.add_fx_event(
            timeline_id, payload, actor="external"
        )
    except timeline_service.TimelineError as exc:
        raise _timeline_error(exc) from exc


@router.patch(
    "/timelines/{timeline_id}/fx/events/{event_id}", response_model=TimelineFx
)
async def update_fx_event(
    timeline_id: str, event_id: str, payload: TimelineFxEventUpdate
) -> TimelineFx:
    """イベントを 1 件だけ書き換える（``event`` は浅いマージ・``enabled``）。"""
    try:
        return await timeline_service.update_fx_event(
            timeline_id, event_id, payload, actor="external"
        )
    except timeline_service.TimelineError as exc:
        raise _timeline_error(exc) from exc


@router.delete(
    "/timelines/{timeline_id}/fx/events/{event_id}", response_model=TimelineFx
)
async def delete_fx_event(
    timeline_id: str, event_id: str, base_revision: int | None = None
) -> TimelineFx:
    """イベントを 1 件消す。"""
    try:
        return await timeline_service.delete_fx_event(
            timeline_id, event_id, base_revision=base_revision, actor="external"
        )
    except timeline_service.TimelineError as exc:
        raise _timeline_error(exc) from exc


@router.get("/projects/{project_id}/media", response_model=TimelineMediaPage)
async def list_timeline_media(
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
        return await timeline_service.list_media(
            project_id, kind, limit=limit, offset=offset
        )
    except timeline_service.TimelineError as exc:
        raise _timeline_error(exc) from exc


@router.post(
    "/timelines/{timeline_id}/generate-subtitles",
    response_model=StudioTimelineDetail,
)
async def generate_subtitles(
    timeline_id: str, payload: TimelineSubtitleRequest | None = None
) -> StudioTimelineDetail:
    """V1 の各クリップの元カットの台詞から、テロップを一括で置き直す。

    字幕トラックの中身は**置き換わる**（積み増さない）。
    """
    body = payload or TimelineSubtitleRequest()
    try:
        return await timeline_service.generate_subtitles(timeline_id, body.track_id)
    except timeline_service.TimelineError as exc:
        raise _timeline_error(exc) from exc


@router.get("/timelines/{timeline_id}/sync-preview", response_model=TimelineSyncPreview)
async def sync_preview(timeline_id: str) -> TimelineSyncPreview:
    """作ったあとに脚本で起きた差分（増えた / 採用が変わった / 消えたカット）。"""
    try:
        return await timeline_service.sync_preview(timeline_id)
    except timeline_service.TimelineError as exc:
        raise _timeline_error(exc) from exc


@router.post("/timelines/{timeline_id}/sync", response_model=StudioTimelineDetail)
async def apply_sync(
    timeline_id: str, payload: TimelineSyncRequest | None = None
) -> StudioTimelineDetail:
    """上の差分のうち、body で選ばれたものだけ反映する。"""
    try:
        return await timeline_service.apply_sync(
            timeline_id, payload or TimelineSyncRequest()
        )
    except timeline_service.TimelineError as exc:
        raise _timeline_error(exc) from exc


@router.get("/timelines/{timeline_id}/missing", response_model=TimelineMissingReport)
async def missing_report(timeline_id: str) -> TimelineMissingReport:
    """実ファイルが見つからないクリップと、同じカットの差し替え候補。"""
    try:
        return await timeline_service.missing_report(timeline_id)
    except timeline_service.TimelineError as exc:
        raise _timeline_error(exc) from exc


@router.post(
    "/timelines/{timeline_id}/resolve-missing", response_model=StudioTimelineDetail
)
async def resolve_missing(
    timeline_id: str, payload: TimelineMissingFix | None = None
) -> StudioTimelineDetail:
    """欠落クリップを別テイクへ差し替える / まとめて消す。"""
    try:
        return await timeline_service.resolve_missing(
            timeline_id, payload or TimelineMissingFix()
        )
    except timeline_service.TimelineError as exc:
        raise _timeline_error(exc) from exc


@router.post(
    "/timelines/{timeline_id}/export", response_model=TimelineExport, status_code=202
)
async def start_export(
    request: Request,
    timeline_id: str,
    payload: TimelineExportRequest | None = None,
) -> TimelineExport:
    """書き出しを 1 本受け付ける（**202 即受付**。ffmpeg は裏で走る）。

    進捗は ``GET /api/v1/exports/{id}`` をポーリングして追う。同じタイムライン
    で走っているものがあれば 409、走っている書き出しが上限に達していれば 429。
    メディア欠落のまま焼こうとすると 400（直し方は ``GET .../missing``）。

    ``fx: true`` を付けると、焼き上がった mp4 を下地に FX トラックの演出を載せる
    Remotion ジョブが続けて走る（``fx_job_id`` / ``fx_status`` / ``fx_video_url``
    に出る）。Remotion 連携が無効なら 400。
    """
    body = payload or TimelineExportRequest()
    # 数えてから投入するまでを錠で括る（並行リクエストが数え合いになって、
    # 上限に達していても全部すり抜けるのを防ぐ）
    async with _EXPORTS_LOCK:
        await _check_running_exports()
        try:
            return await timeline_service.start_export(
                timeline_id,
                body.model_dump(),
                base_url=_serving_base_url(request),
            )
        except timeline_service.TimelineError as exc:
            raise _timeline_error(exc) from exc


@router.get(
    "/timelines/{timeline_id}/exports", response_model=list[TimelineExport]
)
async def list_exports(timeline_id: str) -> list[TimelineExport]:
    """このタイムラインの書き出しの履歴（新しい順）。

    焼き上がりの ``fps`` / ``width`` / ``height`` / ``frames`` / ``duration_ms``
    と ``warnings`` が入っているので、``POST /export`` の ``id`` を控え損ねた
    ときはここから拾える。
    """
    if await timeline_service.get_timeline(timeline_id) is None:
        raise HTTPException(status_code=404, detail="timeline not found")
    return await timeline_service.list_exports(timeline_id)


@router.get("/exports/{export_id}", response_model=TimelineExport)
async def get_export(export_id: str) -> TimelineExport:
    """書き出しの状態と成果物（完了待ちはここをポーリングする）。"""
    export = await timeline_service.get_export(export_id)
    if export is None:
        raise HTTPException(status_code=404, detail="export not found")
    return export


@router.post("/exports/{export_id}/save-to-library", response_model=LibraryItem,
             status_code=201)
async def save_export_to_library(
    export_id: str, payload: TimelineExportSave | None = None
) -> LibraryItem:
    """完成した mp4 をライブラリ（``library/video/``）へコピーして登録する。"""
    body = payload or TimelineExportSave()
    try:
        return await timeline_service.save_export_to_library(export_id, body.name)
    except timeline_service.TimelineError as exc:
        raise _timeline_error(exc) from exc
