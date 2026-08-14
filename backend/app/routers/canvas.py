"""キャンバス API（ドラマスタジオの別ビュー）。

プロジェクトの一覧・詳細はスタジオのもの（``/api/studio/projects``）をそのまま
使い、ここが持つのは**キャンバスにしか無いもの**——カードの置き場所、表示位置、
チャット履歴——だけ。カードの中身（素材・場・Shot の項目）を直すのは
:mod:`app.routers.studio` の仕事で、こちらでは重複させない。

DB の操作は :mod:`app.canvas` に集約してあり、ここは HTTP の入り口だけを担当
する（:mod:`app.routers.studio` と同じ持ち方）。
"""

from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse

from .. import agent_store
from .. import canvas as service
from .. import canvas_agent
from .. import lora_samples
from .. import studio as studio_service
from ..ids import new_id
from ..models import (
    CanvasAgentRun,
    CanvasAgentStart,
    CanvasAgentState,
    CanvasAttachment,
    CanvasBoard,
    CanvasCard,
    CanvasCardCreate,
    CanvasCardPosition,
    CanvasCardUpdate,
    CanvasChatSession,
    CanvasMessage,
    CanvasMessageCreate,
    CanvasSessionCreate,
    CanvasSessionSearchHit,
    CanvasSessionUpdate,
    CanvasViewport,
)
from . import agent as agent_router
from . import assets as assets_router

router = APIRouter(prefix="/api/canvas", tags=["canvas"])

#: 添付の一覧に付ける見出し（エージェントモードと同じ文言）
ATTACHMENT_HEADER = agent_router.ATTACHMENT_HEADER


def _bad_request(exc: service.CanvasError) -> HTTPException:
    """キャンバス操作の失敗を HTTP に移す（「既にある」だけ 409）。"""
    if isinstance(exc, service.CanvasConflict):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


async def _require_project(project_id: str) -> None:
    if await studio_service.get_project(project_id) is None:
        raise HTTPException(status_code=404, detail="project not found")


#: 盤面のタブ。省略 or ``'common'`` = 作品共通（素材と未分類のカット）、
#: それ以外は話の id（その話の場・カット・生成結果だけが出る）。
_TAB = Query(
    None, description="タブ（省略 / 'common' = 作品共通、それ以外は話の id）"
)


async def _require_tab(project_id: str, episode_id: str | None) -> str | None:
    """タブの ``episode_id`` を確かめて内部表現（``None`` = 作品共通）に直す。"""
    tab = service.tab_of(episode_id)
    if tab is None:
        return None
    episode = await studio_service.get_episode(tab)
    if episode is None or episode.project_id != project_id:
        raise HTTPException(status_code=404, detail="episode not found")
    return tab


# --------------------------------------------------------------------------
# キャンバス 1 タブ（カード・表示位置・会話）
# --------------------------------------------------------------------------

@router.get("/projects/{project_id}", response_model=CanvasBoard)
async def get_board(
    project_id: str,
    episode_id: str | None = _TAB,
    session_id: str | None = Query(None),
) -> CanvasBoard:
    """1 タブぶんのカードの置き場所と会話。**中身はスタジオの詳細と合わせて使う**。

    カードはスタジオの中身の写しなので、まだカードが無いエンティティ（素材 /
    場 / Shot / Take）にはここで自動的にカードができる（履歴には残さない）。
    鏡は作品ぜんぶにかかり、返るのは開いているタブのカードだけ（開いていない
    話のカードも存在はする）。会話は ``session_id`` のセッション（省略時は最新）。
    """
    tab = await _require_tab(project_id, episode_id)
    if session_id:
        session = await service.get_session(project_id, session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="session not found")
    board = await service.board(project_id, tab, session_id)
    if board is None:
        raise HTTPException(status_code=404, detail="project not found")
    return board


@router.put("/projects/{project_id}/viewport", response_model=CanvasViewport)
async def set_viewport(
    project_id: str, payload: CanvasViewport, episode_id: str | None = _TAB
) -> CanvasViewport:
    """タブの表示位置を覚える（見え方だけなのでリビジョンには残らない）。"""
    tab = await _require_tab(project_id, episode_id)
    viewport = await service.set_viewport(project_id, payload, tab)
    if viewport is None:
        raise HTTPException(status_code=404, detail="project not found")
    return viewport


# --------------------------------------------------------------------------
# カード
# --------------------------------------------------------------------------

@router.get("/projects/{project_id}/cards", response_model=list[CanvasCard])
async def list_cards(
    project_id: str, episode_id: str | None = _TAB
) -> list[CanvasCard]:
    """1 タブぶんのカード（読むついでにスタジオの中身が映る）。"""
    await _require_project(project_id)
    tab = await _require_tab(project_id, episode_id)
    return await service.list_tab_cards(project_id, tab)


@router.post("/projects/{project_id}/cards", response_model=CanvasCard,
             status_code=201)
async def create_card(project_id: str, payload: CanvasCardCreate) -> CanvasCard:
    """カードを 1 枚**新しく作る**。

    素材 / 場 / Shot はスタジオ側の行も一緒に作る。text / model はキャンバス
    専用で、中身は ``data``。既にあるものはキャンバスを開けば自動で並ぶので、
    ここで置き直すことはない（media は Take が生まれたときにだけできる）。
    """
    await _require_project(project_id)
    try:
        return await service.create_card(project_id, payload)
    except service.CanvasError as exc:
        raise _bad_request(exc) from exc
    except studio_service.StudioError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/cards/{card_id}", response_model=CanvasCard)
async def update_card(card_id: str, payload: CanvasCardUpdate) -> CanvasCard:
    """指定した項目だけ変える（``data`` は text / model カードのみ）。"""
    try:
        card = await service.update_card(card_id, payload.model_dump())
    except service.CanvasError as exc:
        raise _bad_request(exc) from exc
    if card is None:
        raise HTTPException(status_code=404, detail="card not found")
    return card


@router.put("/cards/{card_id}/position", response_model=CanvasCard)
async def move_card(card_id: str, payload: CanvasCardPosition) -> CanvasCard:
    """置き場所だけ動かす（エンティティには触れない軽い更新）。"""
    card = await service.move_card(
        card_id, payload.x, payload.y, payload.w, payload.h, payload.z
    )
    if card is None:
        raise HTTPException(status_code=404, detail="card not found")
    return card


@router.delete("/cards/{card_id}", status_code=204)
async def delete_card(
    card_id: str,
    #: True = スタジオ側のエンティティごと消す。参照カードは写しなので
    #: これを立てないと 400（カードだけ消しても鏡がすぐ戻すため）
    delete_entity: bool = Query(False),
) -> None:
    try:
        removed = await service.delete_card(card_id, delete_entity=delete_entity)
    except service.CanvasError as exc:
        raise _bad_request(exc) from exc
    if not removed:
        raise HTTPException(status_code=404, detail="card not found")


# --------------------------------------------------------------------------
# 会話セッション
# --------------------------------------------------------------------------

@router.get("/projects/{project_id}/sessions", response_model=list[CanvasChatSession])
async def list_sessions(project_id: str) -> list[CanvasChatSession]:
    await _require_project(project_id)
    try:
        return await service.list_sessions(project_id)
    except service.CanvasError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post(
    "/projects/{project_id}/sessions",
    response_model=CanvasChatSession,
    status_code=201,
)
async def create_session(
    project_id: str, payload: CanvasSessionCreate | None = None
) -> CanvasChatSession:
    await _require_project(project_id)
    body = payload or CanvasSessionCreate()
    try:
        return await service.create_session(project_id, body.title)
    except service.CanvasError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get(
    "/projects/{project_id}/sessions/search",
    response_model=list[CanvasSessionSearchHit],
)
async def search_sessions(
    project_id: str,
    q: str = Query(""),
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    session_id: str | None = Query(None, description="除外する自分のセッション"),
) -> list[CanvasSessionSearchHit]:
    await _require_project(project_id)
    hits, _total = await service.search_sessions(
        project_id, q, exclude_id=session_id, offset=offset, limit=limit
    )
    return hits


@router.patch(
    "/projects/{project_id}/sessions/{session_id}",
    response_model=CanvasChatSession,
)
async def update_session(
    project_id: str, session_id: str, payload: CanvasSessionUpdate
) -> CanvasChatSession:
    if payload.title is None:
        session = await service.get_session(project_id, session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="session not found")
        return session
    session = await service.update_session(
        project_id, session_id, title=payload.title
    )
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    return session


@router.delete(
    "/projects/{project_id}/sessions/{session_id}", status_code=204
)
async def delete_session(project_id: str, session_id: str) -> None:
    session = await service.delete_session(project_id, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    from .. import grok_session as grok_sessions

    await grok_sessions.delete_remote_session(
        session.grok_session_id, session.grok_cwd or None
    )


# --------------------------------------------------------------------------
# 会話
# --------------------------------------------------------------------------

@router.get("/projects/{project_id}/messages", response_model=list[CanvasMessage])
async def list_messages(
    project_id: str, session_id: str | None = Query(None)
) -> list[CanvasMessage]:
    await _require_project(project_id)
    if session_id and await service.get_session(project_id, session_id) is None:
        raise HTTPException(status_code=404, detail="session not found")
    return await service.list_messages(project_id, session_id)


@router.post("/projects/{project_id}/messages", response_model=CanvasMessage,
             status_code=201)
async def append_message(
    project_id: str,
    payload: CanvasMessageCreate,
    session_id: str | None = Query(None),
) -> CanvasMessage:
    """発言を 1 件残すだけ（エージェントは動かさない）。"""
    content = payload.content.strip()
    if not content:
        raise HTTPException(status_code=422, detail="content が空です")
    try:
        return await service.append_message(
            project_id,
            payload.role,
            content,
            session_id=session_id,
            kind=payload.kind,
            data=payload.data,
        )
    except service.CanvasError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# --------------------------------------------------------------------------
# 添付ファイル（エージェントに見せるもの）
# --------------------------------------------------------------------------
#
# 置き場所はキャンバスの作業ディレクトリの ``attachments/``（:mod:`app.canvas_agent`）。
# grok CLI はそこを根に動くので、絶対パスでも workdir 相対でも開ける。素材
# （``assets/``）とは分ける: ここに来るのは「エージェントに見せたいだけ」の
# ファイルで、作品の持ち物にするかどうかはエージェントが決める。

#: 添付できる拡張子（エージェントモードの添付と同じ集合）
ATTACHMENT_EXT = agent_router.ATTACHMENT_EXT

#: 拡張子 -> プレビューの出し分けに使う種別
_ATTACHMENT_KINDS: list[tuple[set[str], str]] = [
    (assets_router.IMAGE_EXT, "image"),
    (assets_router.VIDEO_EXT, "video"),
    (assets_router.AUDIO_EXT, "audio"),
]


def attachment_kind(filename: str) -> str:
    ext = Path(filename or "").suffix.lower()
    for allowed, kind in _ATTACHMENT_KINDS:
        if ext in allowed:
            return kind
    return "document"


async def _session_for_files(
    project_id: str, session_id: str | None
) -> object:
    try:
        return await service.ensure_session(project_id, session_id)
    except service.CanvasError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _save_attachment(session, original: Path, data: bytes) -> CanvasAttachment:
    ext = original.suffix.lower()
    stem = lora_samples.safe_stem(original.stem, fallback="attachment")
    dest = canvas_agent.attachment_dir(session.project_id, session) / (
        f"{stem}_{new_id()}{ext}"
    )
    dest.write_bytes(data)
    return CanvasAttachment(
        name=original.name,
        path=f"{agent_store.ATTACHMENTS_DIR}/{dest.name}",
        abs_path=str(dest),
        kind=attachment_kind(original.name),  # type: ignore[arg-type]
    )


@router.post(
    "/projects/{project_id}/attachments",
    response_model=CanvasAttachment,
    status_code=201,
)
async def upload_attachment(
    project_id: str,
    file: UploadFile = File(...),
    session_id: str | None = Query(None),
) -> CanvasAttachment:
    """添付を 1 件保存する（発言に添えるのは返ってきた ``path``）。"""
    await _require_project(project_id)
    original = Path(file.filename or "upload")
    ext = original.suffix.lower()
    if ext not in ATTACHMENT_EXT:
        raise HTTPException(
            status_code=400,
            detail=f"unsupported extension '{ext}'"
            f" (allowed: {sorted(ATTACHMENT_EXT)})",
        )
    session = await _session_for_files(project_id, session_id)
    return _save_attachment(session, original, await file.read())


@router.post(
    "/projects/{project_id}/sessions/{session_id}/attachments",
    response_model=CanvasAttachment,
    status_code=201,
)
async def upload_session_attachment(
    project_id: str, session_id: str, file: UploadFile = File(...)
) -> CanvasAttachment:
    await _require_project(project_id)
    original = Path(file.filename or "upload")
    ext = original.suffix.lower()
    if ext not in ATTACHMENT_EXT:
        raise HTTPException(
            status_code=400,
            detail=f"unsupported extension '{ext}'"
            f" (allowed: {sorted(ATTACHMENT_EXT)})",
        )
    session = await _session_for_files(project_id, session_id)
    return _save_attachment(session, original, await file.read())


@router.get("/projects/{project_id}/attachments/{name:path}")
async def get_attachment(
    project_id: str,
    name: str,
    session_id: str | None = Query(None),
) -> FileResponse:
    """添付そのものを返す（履歴のサムネイル用。範囲外は 404）。"""
    await _require_project(project_id)
    session = None
    if session_id:
        session = await service.get_session(project_id, session_id)
    elif not session_id:
        session = await service.latest_session(project_id)
    path = canvas_agent.resolve_attachment(project_id, name, session)
    if path is None:
        # 移行セッションの旧 workdir も見る
        path = canvas_agent.resolve_attachment(project_id, name)
    if path is None:
        raise HTTPException(status_code=404, detail="attachment not found")
    return FileResponse(path)


def _verify_attachments(
    project_id: str, paths: list[str], session=None
) -> list[dict[str, str]]:
    """``attachments/`` 配下の実在ファイルだけを通す（それ以外は 400）。

    返すのは会話に残す形（種別と絶対パスつき）。エージェントにはこの絶対パスを
    そのまま伝え、ブラウザには ``path`` で配信する。
    """
    verified: list[dict[str, str]] = []
    for raw in paths:
        resolved = canvas_agent.resolve_attachment(project_id, raw, session)
        if resolved is None:
            resolved = canvas_agent.resolve_attachment(project_id, raw)
        if resolved is None:
            raise HTTPException(status_code=400, detail=f"unknown attachment '{raw}'")
        verified.append(
            {
                "path": raw.strip(),
                "name": resolved.name,
                "abs_path": str(resolved),
                "kind": attachment_kind(resolved.name),
            }
        )
    return verified


def attachment_block(attachments: list[dict[str, str]]) -> str:
    """添付の一覧（本文の後ろに足す。絶対パス・種別・元のファイル名つき）。"""
    lines = [
        f"- {item['abs_path']}（{item['kind']} / {item['name']}）"
        for item in attachments
    ]
    return "\n".join([ATTACHMENT_HEADER, *lines])


def with_attachments(content: str, attachments: list[dict[str, str]]) -> str:
    if not attachments:
        return content
    block = attachment_block(attachments)
    return f"{content}\n\n{block}" if content else block


# --------------------------------------------------------------------------
# エージェント（スタジオのツール一式 + 盤面の操作）
# --------------------------------------------------------------------------

def _agent_state(project_id: str, session_id: str | None = None) -> CanvasAgentState:
    return CanvasAgentState(
        project_id=project_id,
        running=canvas_agent.is_running(project_id),
        activity=canvas_agent.current_activity(project_id),
        session_id=session_id or canvas_agent._active_session.get(project_id),
    )


@router.get("/projects/{project_id}/agent", response_model=CanvasAgentState)
async def agent_state(project_id: str) -> CanvasAgentState:
    """実行中かどうか（WS を取りこぼしたブラウザ用のポーリング先）。"""
    await _require_project(project_id)
    return _agent_state(project_id)


@router.post("/projects/{project_id}/agent", response_model=CanvasAgentRun,
             status_code=202)
async def run_agent(
    project_id: str, payload: CanvasAgentStart
) -> CanvasAgentRun:
    """ユーザー発言を残し、エージェントの実行を始める。

    実行そのものはバックグラウンドで進み、応答とツール実行の結果は会話
    （``canvas_messages``）に足されながら WS（``type: "canvas"``）で届く。
    ``episode_id`` は**いま開いているタブ**で、エージェントはその盤面を中心に
    考える（置いた text / model カードもそのタブに載る）。

    ``attachments`` を添えると、その一覧（絶対パス・種別・元のファイル名）が
    発言の後ろに足されてエージェントに渡る。画面に出すのは元の本文なので、
    ユーザーが書いた文と添付は ``data`` に控える。
    """
    await _require_project(project_id)
    tab = await _require_tab(project_id, payload.episode_id)
    try:
        session = await service.ensure_session(project_id, payload.session_id)
    except service.CanvasError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    content = payload.content.strip()
    attachments = _verify_attachments(project_id, payload.attachments, session)
    if not content and not attachments:
        raise HTTPException(status_code=422, detail="content が空です")
    if canvas_agent.is_running(project_id):
        raise HTTPException(
            status_code=409, detail="実行中です。停止するか完了を待ってください"
        )
    data = dict(payload.data)
    if attachments:
        data["attachments"] = attachments
        data["text"] = content
    try:
        message = await canvas_agent.append(
            project_id,
            "user",
            with_attachments(content, attachments),
            session_id=session.id,
            data=data,
        )
    except service.CanvasError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await canvas_agent.start(project_id, tab, session.id)
    return CanvasAgentRun(
        **_agent_state(project_id, session.id).model_dump(), message=message
    )


@router.post("/projects/{project_id}/agent/stop", response_model=CanvasAgentState)
async def stop_agent(project_id: str) -> CanvasAgentState:
    """⏹: 次のターンの手前で止め、このランで投入したジョブも cancel する。"""
    await _require_project(project_id)
    canvas_agent.request_stop(project_id)
    return _agent_state(project_id)
