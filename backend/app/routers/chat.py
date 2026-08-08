"""Chat-style prompt authoring API (SPEC §4.3 / §9).

Grok's CLI is stateless, so the transcript lives here: the session row keeps
``[{role, content, ts}]`` with the assembled system prompt as ``messages[0]``
and every turn re-sends the whole thing.
"""

from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException

from .. import grok, prompts
from ..config import load_settings
from ..db import get_db
from ..ids import new_id
from ..jobs import JobValidationError, resolve_asset_path
from ..models import (
    ChatMessage,
    ChatReply,
    ChatSendMessage,
    ChatSession,
    ChatSessionCreate,
    PromptResult,
)
from ..paths import GROK_WORKDIR, resolve_workdir

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chat", tags=["chat"])


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _workdir() -> Path:
    return resolve_workdir(load_settings().grok_workdir, GROK_WORKDIR)


def _copy_start_image(source: str, session_id: str) -> str:
    """Put the mode-B start frame next to the CLI so it can look at it (§4.3)."""
    src = resolve_asset_path(source, field="start_image_path")
    workdir = _workdir()
    workdir.mkdir(parents=True, exist_ok=True)
    dest = workdir / f"start_frame_{session_id}{src.suffix or '.png'}"
    shutil.copy2(src, dest)
    return dest.name


# --------------------------------------------------------------------------
# persistence
# --------------------------------------------------------------------------

def _row_to_session(row) -> ChatSession:
    try:
        raw = json.loads(row["messages"] or "[]")
    except ValueError:
        raw = []
    messages = [ChatMessage(**m) for m in raw if isinstance(m, dict)]
    return ChatSession(
        id=row["id"],
        created_at=row["created_at"],
        job_id=row["job_id"],
        messages=messages,
    )


async def _load_session(session_id: str) -> ChatSession:
    async with get_db() as conn:
        async with conn.execute(
            "SELECT * FROM chat_sessions WHERE id = ?", (session_id,)
        ) as cur:
            row = await cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="chat session not found")
    return _row_to_session(row)


def _dump(messages: list[ChatMessage]) -> str:
    return json.dumps([m.model_dump() for m in messages], ensure_ascii=False)


async def _save_messages(session_id: str, messages: list[ChatMessage]) -> None:
    async with get_db() as conn:
        await conn.execute(
            "UPDATE chat_sessions SET messages = ? WHERE id = ?",
            (_dump(messages), session_id),
        )
        await conn.commit()


# --------------------------------------------------------------------------
# endpoints
# --------------------------------------------------------------------------

@router.post("/sessions", response_model=ChatSession, status_code=201)
async def create_session(payload: ChatSessionCreate) -> ChatSession:
    """Start a session; the form snapshot becomes the stored system message."""
    session_id = new_id()

    start_image_filename = None
    if payload.mode == "i2v" and (payload.start_image_path or "").strip():
        try:
            start_image_filename = _copy_start_image(
                payload.start_image_path or "", session_id
            )
        except JobValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except OSError as exc:  # copying is best effort: text-only fallback
            log.warning("could not stage the start frame for grok: %s", exc)

    system = ChatMessage(
        role="system",
        content=prompts.build_system_prompt(payload, start_image_filename),
        ts=_now(),
    )
    session = ChatSession(id=session_id, created_at=_now(), messages=[system])
    async with get_db() as conn:
        await conn.execute(
            "INSERT INTO chat_sessions (id, created_at, job_id, messages)"
            " VALUES (?,?,?,?)",
            (session.id, session.created_at, None, _dump(session.messages)),
        )
        await conn.commit()
    return session


@router.get("/sessions/{session_id}", response_model=ChatSession)
async def get_session(session_id: str) -> ChatSession:
    return await _load_session(session_id)


@router.post("/sessions/{session_id}/messages", response_model=ChatReply)
async def send_message(session_id: str, payload: ChatSendMessage) -> ChatReply:
    """Append a user turn, ask Grok, store and return its answer."""
    content = (payload.content or "").strip()
    if not content:
        raise HTTPException(status_code=422, detail="content is empty")

    session = await _load_session(session_id)
    messages = [*session.messages, ChatMessage(role="user", content=content, ts=_now())]

    client = grok.get_client()
    try:
        answer = await client.complete(prompts.build_conversation(messages))
        result = grok.extract_result(answer)
        if result is None and grok.has_json_fence(answer):
            # It tried to deliver JSON but we could not parse it: one retry
            # with an explicit format reminder (SPEC §4.1).
            retry_messages = [
                *messages,
                ChatMessage(role="assistant", content=answer, ts=_now()),
            ]
            answer = await client.complete(
                prompts.build_conversation(retry_messages, retry=True)
            )
            result = grok.extract_result(answer)
    except grok.LLMError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    messages.append(ChatMessage(role="assistant", content=answer, ts=_now()))
    await _save_messages(session_id, messages)
    return ChatReply(
        content=answer, result=PromptResult(**result) if result else None
    )
