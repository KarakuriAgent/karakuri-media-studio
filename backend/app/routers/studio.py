"""ドラマスタジオ API。

プロジェクト・World Bible の素材・脚本（Shot）・Take の CRUD と、Shot の生成
投入。DB とジョブ投入は :mod:`app.studio` に集約してあり、ここは HTTP の
入り口だけを担当する（:mod:`app.routers.library` と同じ持ち方）。
"""

from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from .. import comfy
from .. import studio as service
from ..models import (
    ASSET_FILE_ROLE_KINDS,
    StudioAsset,
    StudioAssetFile,
    StudioAssetUpdate,
    StudioCapabilities,
    StudioDemoCreate,
    StudioEpisode,
    StudioEpisodeCreate,
    StudioEpisodeUpdate,
    StudioProject,
    StudioProjectCreate,
    StudioProjectDetail,
    StudioProjectSummary,
    StudioProjectUpdate,
    StudioReorder,
    StudioRevision,
    StudioRevisionDetail,
    StudioScene,
    StudioSceneCreate,
    StudioSceneUpdate,
    StudioShot,
    StudioShotCreate,
    StudioShotPreview,
    StudioShotReorder,
    StudioShotUpdate,
    StudioTake,
)
from .assets import ALLOWED_EXT, save_upload

router = APIRouter(prefix="/api/studio", tags=["studio"])


def _kind_of(filename: str, fallback: str = "image") -> str:
    """拡張子から素材の種別（image / video / audio）を当てる。

    アップロードの受け口はどれも ``assets/<kind>/`` なので、種別が決まらない
    ものはここで弾かず、``save_upload`` の拡張子チェックに任せる。
    """
    ext = Path(filename or "").suffix.lower()
    for kind, allowed in ALLOWED_EXT.items():
        if ext in allowed:
            return kind
    return fallback


def _bad_request(exc: service.StudioError) -> HTTPException:
    """スタジオ操作の失敗を HTTP に移す（「既にある」だけ 409）。"""
    if isinstance(exc, service.StudioConflict):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


# --------------------------------------------------------------------------
# 接続先でできること
# --------------------------------------------------------------------------

@router.get("/capabilities", response_model=StudioCapabilities)
async def get_capabilities() -> StudioCapabilities:
    """いまの接続先でスタジオの追加機能が使えるか（画面のトグルの出し分け用）。

    ``/object_info`` をひとつ聞くだけなので、接続できないときは 500 にせず
    「使えない」＋理由を返す（設定していないだけのこともあるため）。
    """
    try:
        return StudioCapabilities(
            latent_continuity=await comfy.latent_context_support()
        )
    except comfy.ComfyError as exc:
        return StudioCapabilities(
            latent_continuity=False, error=comfy.display_error(exc)
        )


# --------------------------------------------------------------------------
# プロジェクト
# --------------------------------------------------------------------------

@router.get("/projects", response_model=list[StudioProjectSummary])
async def list_projects() -> list[StudioProjectSummary]:
    """一覧（Shot / 素材 / Take / 採用済み Take の件数つき）。"""
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
        )
    except service.StudioError as exc:
        raise _bad_request(exc) from exc


@router.post("/demo", response_model=StudioProjectDetail, status_code=201)
async def create_demo_project(payload: StudioDemoCreate) -> StudioProjectDetail:
    """デモ作品を 1 本まるごと作る（:mod:`app.studio_demo`）。

    素材はメタデータのみ（ファイルなし）で登録される。同じ作品コードの
    プロジェクトが既にあれば 409。
    """
    try:
        return await service.create_demo_project(payload.code)
    except service.StudioError as exc:
        raise _bad_request(exc) from exc


@router.get("/projects/{project_id}", response_model=StudioProjectDetail)
async def get_project(project_id: str) -> StudioProjectDetail:
    """素材・Shot・Take（ジョブの状態と成果物つき）を含む 1 画面ぶん。"""
    detail = await service.project_detail(project_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="project not found")
    return detail


@router.patch("/projects/{project_id}", response_model=StudioProject)
async def update_project(
    project_id: str, payload: StudioProjectUpdate
) -> StudioProject:
    try:
        project = await service.update_project(project_id, **payload.model_dump())
    except service.StudioError as exc:
        raise _bad_request(exc) from exc
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    return project


@router.delete("/projects/{project_id}", status_code=204)
async def delete_project(project_id: str) -> None:
    if not await service.delete_project(project_id):
        raise HTTPException(status_code=404, detail="project not found")


# --------------------------------------------------------------------------
# World Bible の素材
# --------------------------------------------------------------------------

@router.post("/projects/{project_id}/assets", response_model=StudioAsset,
             status_code=201)
async def add_asset(
    project_id: str,
    #: 省略すると**メタデータのみの素材**（名前とキャプションだけ）になる
    file: UploadFile | None = File(None),
    #: `@名前` で呼ぶ識別名（省略するとファイル名の主部。ファイルなしでは必須）
    name: str = Form(""),
    kind: str = Form("image"),
    category: str = Form("reference"),
    caption: str = Form(""),
    prompt_caption: str = Form(""),
    locked: bool = Form(False),
) -> StudioAsset:
    """素材を World Bible に登録する。

    ファイルを付ければ実体を既存のアップロード先（``assets/<kind>/``）に置き、
    付けなければ「設定だけ書いた素材」として登録する。後者は参照には添付
    されず、``@名前`` は投入時に説明文へ展開される。
    """
    if await service.get_project(project_id) is None:
        raise HTTPException(status_code=404, detail="project not found")
    path = ""
    if file is not None and file.filename:
        saved = await save_upload(file, kind)
        path = saved.path
        name = name or file.filename.rsplit(".", 1)[0]
    try:
        return await service.add_asset(
            project_id,
            name=name,
            kind=kind,
            path=path,
            category=category,
            caption=caption,
            prompt_caption=prompt_caption,
            locked=locked,
        )
    except service.StudioError as exc:
        raise _bad_request(exc) from exc


@router.patch("/assets/{asset_id}", response_model=StudioAsset)
async def update_asset(asset_id: str, payload: StudioAssetUpdate) -> StudioAsset:
    try:
        asset = await service.update_asset(asset_id, **payload.model_dump())
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

    実体は今までどおり ``assets/<kind>/`` に置き、種別は拡張子から決まる
    （``file`` の中身で image / video / audio を選ぶ）。
    """
    if await service.get_asset(asset_id) is None:
        raise HTTPException(status_code=404, detail="studio asset not found")
    kind = _kind_of(file.filename or "")
    saved = await save_upload(file, kind)
    try:
        asset = await service.update_asset(asset_id, path=saved.path, kind=kind)
    except service.StudioError as exc:
        raise _bad_request(exc) from exc
    if asset is None:
        raise HTTPException(status_code=404, detail="studio asset not found")
    return asset


@router.get("/assets/{asset_id}/files", response_model=list[StudioAssetFile])
async def list_asset_files(asset_id: str) -> list[StudioAssetFile]:
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
    """素材にリファレンスを 1 本足す（メインのファイルは変わらない）。"""
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
            asset_id, role=role, path=saved.path, caption=caption
        )
    except service.StudioError as exc:
        raise _bad_request(exc) from exc


@router.delete("/asset-files/{file_id}", status_code=204)
async def delete_asset_file(file_id: str) -> None:
    """リファレンスを 1 本外す（ファイル実体は ``assets/`` に残る）。"""
    if not await service.delete_asset_file(file_id):
        raise HTTPException(status_code=404, detail="studio asset file not found")


@router.delete("/assets/{asset_id}", status_code=204)
async def delete_asset(asset_id: str) -> None:
    if not await service.delete_asset(asset_id):
        raise HTTPException(status_code=404, detail="studio asset not found")


# --------------------------------------------------------------------------
# 話（エピソード）と場（シーン）
# --------------------------------------------------------------------------

@router.post("/projects/{project_id}/episodes", response_model=StudioEpisode,
             status_code=201)
async def create_episode(
    project_id: str, payload: StudioEpisodeCreate
) -> StudioEpisode:
    try:
        return await service.create_episode(project_id, payload)
    except service.StudioError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/projects/{project_id}/episodes/reorder",
             response_model=list[StudioEpisode])
async def reorder_episodes(
    project_id: str, payload: StudioReorder
) -> list[StudioEpisode]:
    """``ids`` の並び順をそのまま ``sort_order`` にする（全件を送る）。"""
    if await service.get_project(project_id) is None:
        raise HTTPException(status_code=404, detail="project not found")
    try:
        return await service.reorder_episodes(project_id, payload.ids)
    except service.StudioError as exc:
        raise _bad_request(exc) from exc


@router.patch("/episodes/{episode_id}", response_model=StudioEpisode)
async def update_episode(
    episode_id: str, payload: StudioEpisodeUpdate
) -> StudioEpisode:
    episode = await service.update_episode(episode_id, **payload.model_dump())
    if episode is None:
        raise HTTPException(status_code=404, detail="episode not found")
    return episode


@router.delete("/episodes/{episode_id}", status_code=204)
async def delete_episode(episode_id: str) -> None:
    """配下の場ごと消す（そこにいた Shot は未分類に戻る）。"""
    if not await service.delete_episode(episode_id):
        raise HTTPException(status_code=404, detail="episode not found")


@router.post("/episodes/{episode_id}/scenes", response_model=StudioScene,
             status_code=201)
async def create_scene(episode_id: str, payload: StudioSceneCreate) -> StudioScene:
    try:
        return await service.create_scene(episode_id, payload)
    except service.StudioError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/episodes/{episode_id}/scenes/reorder",
             response_model=list[StudioScene])
async def reorder_scenes(episode_id: str, payload: StudioReorder) -> list[StudioScene]:
    """``ids`` の並び順をそのまま ``sort_order`` にする（この話の場を全件送る）。"""
    if await service.get_episode(episode_id) is None:
        raise HTTPException(status_code=404, detail="episode not found")
    try:
        return await service.reorder_scenes(episode_id, payload.ids)
    except service.StudioError as exc:
        raise _bad_request(exc) from exc


@router.patch("/scenes/{scene_id}", response_model=StudioScene)
async def update_scene(scene_id: str, payload: StudioSceneUpdate) -> StudioScene:
    """指定した項目だけ変える（``episode_id`` を送ると別の話へ引っ越す）。"""
    try:
        scene = await service.update_scene(scene_id, **payload.model_dump())
    except service.StudioError as exc:
        raise _bad_request(exc) from exc
    if scene is None:
        raise HTTPException(status_code=404, detail="scene not found")
    return scene


@router.delete("/scenes/{scene_id}", status_code=204)
async def delete_scene(scene_id: str) -> None:
    """場だけ消す（そこにいた Shot は未分類に戻る）。"""
    if not await service.delete_scene(scene_id):
        raise HTTPException(status_code=404, detail="scene not found")


# --------------------------------------------------------------------------
# リビジョン履歴
# --------------------------------------------------------------------------

@router.get("/projects/{project_id}/revisions", response_model=list[StudioRevision])
async def list_revisions(project_id: str) -> list[StudioRevision]:
    """新しい順の見出し一覧（中身は含めない）。"""
    if await service.get_project(project_id) is None:
        raise HTTPException(status_code=404, detail="project not found")
    return await service.list_revisions(project_id)


@router.get("/projects/{project_id}/revisions/{seq}",
            response_model=StudioRevisionDetail)
async def get_revision(project_id: str, seq: int) -> StudioRevisionDetail:
    revision = await service.get_revision(project_id, seq)
    if revision is None:
        raise HTTPException(status_code=404, detail="revision not found")
    return revision


@router.post("/projects/{project_id}/revisions/{seq}/restore",
             response_model=StudioProjectDetail)
async def restore_revision(project_id: str, seq: int) -> StudioProjectDetail:
    """そのリビジョンの内容へ書き戻す（Take と実ファイルは残る）。"""
    detail = await service.restore_revision(project_id, seq)
    if detail is None:
        raise HTTPException(status_code=404, detail="revision not found")
    return detail


# --------------------------------------------------------------------------
# 脚本（Shot）
# --------------------------------------------------------------------------

@router.post("/projects/{project_id}/shots", response_model=StudioShot,
             status_code=201)
async def create_shot(project_id: str, payload: StudioShotCreate) -> StudioShot:
    try:
        return await service.create_shot(project_id, payload)
    except service.StudioError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/projects/{project_id}/shots/reorder",
             response_model=list[StudioShot])
async def reorder_shots(
    project_id: str, payload: StudioShotReorder
) -> list[StudioShot]:
    """``shot_ids`` の並び順をそのまま ``sort_order`` にする（全件を送る）。"""
    if await service.get_project(project_id) is None:
        raise HTTPException(status_code=404, detail="project not found")
    try:
        return await service.reorder_shots(project_id, payload.shot_ids)
    except service.StudioError as exc:
        raise _bad_request(exc) from exc


@router.patch("/shots/{shot_id}", response_model=StudioShot)
async def update_shot(shot_id: str, payload: StudioShotUpdate) -> StudioShot:
    """指定した項目だけ変える。

    ``scene_id`` / ``selected_take_id`` / 生成設定は **null を明示すると外れる**
    （送らなければ今の値のまま）。
    """
    try:
        shot = await service.update_shot(shot_id, payload.changes())
    except service.StudioError as exc:
        raise _bad_request(exc) from exc
    if shot is None:
        raise HTTPException(status_code=404, detail="shot not found")
    return shot


@router.get("/shots/{shot_id}/prompt-preview", response_model=StudioShotPreview)
async def preview_shot_prompt(shot_id: str) -> StudioShotPreview:
    """このカットを今生成したら**実際に投入されるもの**（読み取りだけ）。

    生成と同じ組み立てを通すが、Grok の英訳は走らせない（``will_translate``
    で入るかどうかだけ伝える）。組み立てられないカットも 400 ではなく、理由を
    ``error`` に入れた 200 で返す。
    """
    preview = await service.preview_shot(shot_id)
    if preview is None:
        raise HTTPException(status_code=404, detail="shot not found")
    return preview


@router.delete("/shots/{shot_id}", status_code=204)
async def delete_shot(shot_id: str) -> None:
    if not await service.delete_shot(shot_id):
        raise HTTPException(status_code=404, detail="shot not found")


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
    """Shot を 1 回生成する（ワークフローは t2v / i2v / r2v から自動で決まる）。"""
    if await service.get_shot(shot_id) is None:
        raise HTTPException(status_code=404, detail="shot not found")
    try:
        return await service.render_shot(shot_id)
    except service.StudioError as exc:
        raise _bad_request(exc) from exc


@router.post("/takes/{take_id}/select", response_model=StudioTake)
async def select_take(take_id: str) -> StudioTake:
    take = await service.select_take(take_id)
    if take is None:
        raise HTTPException(status_code=404, detail="take not found")
    return take


@router.post("/takes/{take_id}/reject", response_model=StudioTake)
async def reject_take(take_id: str) -> StudioTake:
    take = await service.reject_take(take_id)
    if take is None:
        raise HTTPException(status_code=404, detail="take not found")
    return take


@router.delete("/takes/{take_id}", status_code=204)
async def delete_take(take_id: str) -> None:
    if not await service.delete_take(take_id):
        raise HTTPException(status_code=404, detail="take not found")
