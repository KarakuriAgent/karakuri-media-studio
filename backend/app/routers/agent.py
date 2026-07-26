"""Agent mode API (AGENT-MODE §5.1).

同僚型エージェントのセッション管理。既存の /api/chat と /api/jobs の外部仕様には
一切触れず、ジョブの紐付けだけ ``jobs.chat_session_id`` を流用する（§5.2）。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from .. import agent_runner, agent_store, grok, prompts
from ..config import load_settings
from ..ids import new_id
from ..models import (
    AgentApprove,
    AgentCheckinReply,
    AgentMessage,
    AgentReply,
    AgentSendMessage,
    AgentSession,
    AgentSessionCreate,
    AgentSessionSummary,
)
from .options import get_options

router = APIRouter(prefix="/api/agent", tags=["agent"])


async def _require(session_id: str) -> AgentSession:
    session = await agent_store.load(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="agent session not found")
    return session


async def _turn(session_id: str) -> tuple[str, object]:
    try:
        return await agent_runner.run_turn(session_id)
    except grok.LLMError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


async def _reply(session_id: str, answer: str, action) -> AgentReply:
    session = await _require(session_id)
    return AgentReply(content=answer, action=action, session=session)


# --------------------------------------------------------------------------
# sessions
# --------------------------------------------------------------------------

@router.post("/sessions", response_model=AgentSession, status_code=201)
async def create_session(payload: AgentSessionCreate) -> AgentSession:
    """セッション開始: workdir を作り、options を焼き込んだシステムプロンプトを保存する。"""
    session_id = new_id()
    workdir = agent_store.session_dir(session_id)
    options = await get_options()
    settings = load_settings()
    system = prompts.build_agent_system_prompt(
        payload,
        options,
        workdir=str(workdir),
        max_tasks=settings.agent_max_plan_tasks or 5,
        tools_enabled=bool(settings.agent_grok_args),
    )
    session = AgentSession(
        id=session_id,
        created_at=agent_store.now(),
        title=payload.title or (payload.goal.strip()[:40] or "新規セッション"),
        status="idle",
        checkin_mode=payload.checkin_mode,
        auto_limit=payload.auto_limit,
        messages=[
            AgentMessage(role="system", content=system, ts=agent_store.now())
        ],
    )
    if payload.goal.strip():
        session.messages.append(
            AgentMessage(role="user", content=payload.goal.strip(), ts=agent_store.now())
        )
    return await agent_store.insert(session)


@router.get("/sessions", response_model=list[AgentSessionSummary])
async def list_sessions(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> list[AgentSessionSummary]:
    return await agent_store.list_sessions(limit=limit, offset=offset)


@router.get("/sessions/{session_id}", response_model=AgentSession)
async def get_session(session_id: str) -> AgentSession:
    return await _require(session_id)


@router.delete("/sessions/{session_id}", status_code=204, response_model=None)
async def delete_session(session_id: str) -> None:
    await agent_runner.request_stop(session_id)
    if not await agent_store.delete(session_id):
        raise HTTPException(status_code=404, detail="agent session not found")


# --------------------------------------------------------------------------
# conversation
# --------------------------------------------------------------------------

@router.post("/sessions/{session_id}/messages", response_model=AgentReply)
async def send_message(session_id: str, payload: AgentSendMessage) -> AgentReply:
    """ユーザー発言 → Grok ターン → アクション解釈（必要なら実行ループ起動）。"""
    content = (payload.content or "").strip()
    if not content:
        raise HTTPException(status_code=422, detail="content is empty")
    await _require(session_id)
    if agent_runner.is_running(session_id):
        raise HTTPException(
            status_code=409, detail="実行中です。停止するか完了を待ってください"
        )

    await agent_runner.append_message(
        session_id,
        AgentMessage(role="user", content=content, ts=agent_store.now()),
    )
    answer, action = await _turn(session_id)
    await _dispatch(session_id, action)
    return await _reply(session_id, answer, action)


@router.post("/sessions/{session_id}/approve", response_model=AgentReply)
async def approve(session_id: str, payload: AgentApprove | None = None) -> AgentReply:
    """プラン承認 → タスク実行ループ開始（AGENT-MODE §2 アクション承認）。"""
    body = payload or AgentApprove()
    session = await _require(session_id)
    if not session.plan.tasks:
        raise HTTPException(status_code=422, detail="承認できるプランがありません")
    if agent_runner.is_running(session_id):
        raise HTTPException(status_code=409, detail="すでに実行中です")

    if not body.approved:
        session.plan.approved = False
        await agent_store.update(session_id, plan=session.plan, status="idle")
        await agent_runner.append_message(
            session_id,
            AgentMessage(
                role="user",
                content=body.note or "プランを承認しませんでした。",
                ts=agent_store.now(),
            ),
        )
        return await _reply(session_id, "", None)

    session.plan.approved = True
    await agent_store.update(session_id, plan=session.plan)
    await agent_runner.append_message(
        session_id,
        AgentMessage(
            role="user",
            content=body.note or f"プラン v{session.plan.version} を承認しました。実行してください。",
            ts=agent_store.now(),
        ),
    )
    await agent_runner.start_loop(session_id)
    return await _reply(session_id, "", None)


@router.post("/sessions/{session_id}/checkin", response_model=AgentReply)
async def checkin(session_id: str, payload: AgentCheckinReply) -> AgentReply:
    """チェックインへの応答 → ループ再開。"""
    session = await _require(session_id)
    if session.status != "waiting_checkin":
        raise HTTPException(status_code=409, detail="チェックイン待ちではありません")
    answer = (payload.choice or payload.content or "").strip()
    if not answer:
        raise HTTPException(status_code=422, detail="content is empty")

    await agent_runner.append_message(
        session_id,
        AgentMessage(role="user", content=answer, ts=agent_store.now()),
    )
    await agent_runner.start_loop(session_id)
    return await _reply(session_id, "", None)


@router.post("/sessions/{session_id}/stop", response_model=AgentSession)
async def stop(session_id: str) -> AgentSession:
    """⏹: 実行中の ComfyUI ジョブは完了を待って中断する（§2）。"""
    await _require(session_id)
    await agent_runner.request_stop(session_id)
    return await _require(session_id)


# --------------------------------------------------------------------------
# artifacts
# --------------------------------------------------------------------------

@router.get("/sessions/{session_id}/artifacts/{name:path}")
async def get_artifact(session_id: str, name: str) -> FileResponse:
    """workdir 配下のファイル配信（パストラバーサルは 404）。"""
    await _require(session_id)
    path = agent_store.artifact_path(session_id, name)
    if path is None:
        raise HTTPException(status_code=404, detail="artifact not found")
    return FileResponse(path)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

async def _dispatch(session_id: str, action) -> None:
    """Apply the action of a synchronous turn, starting the loop when needed."""
    if action is None:
        session = await _require(session_id)
        if agent_runner.next_task(session) is not None:
            await agent_runner.start_loop(session_id)
        return
    if action.action in ("plan", "checkin", "done", "note"):
        await agent_runner.apply_action(session_id, action)
        return
    # 実行系アクションはバックグラウンドループに委ねる
    await agent_runner.start_loop(session_id, action)
