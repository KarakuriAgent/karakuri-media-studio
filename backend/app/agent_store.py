"""Persistence and work dir helpers for agent sessions (AGENT-MODE §5.2).

One row per session in ``agent_sessions`` (messages / plan / artifacts are JSON
blobs) plus one work dir ``runtime/agent-sessions/<id>/`` that holds everything
the Grok CLI writes (memos, research, inspection frames).
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .db import get_db
from .models import (
    AgentArtifact,
    AgentMessage,
    AgentPlan,
    AgentSession,
    AgentSessionSummary,
)
from .paths import AGENT_SESSIONS_DIR


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------
# work dir
# --------------------------------------------------------------------------

def session_dir(session_id: str) -> Path:
    """``runtime/agent-sessions/<id>/`` (created on demand)."""
    path = AGENT_SESSIONS_DIR / session_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def artifact_path(session_id: str, name: str) -> Path | None:
    """Resolve ``name`` inside the session work dir, refusing anything outside."""
    raw = (name or "").strip()
    if not raw or raw.startswith("/") or "\x00" in raw:
        return None
    root = session_dir(session_id).resolve()
    candidate = (root / raw).resolve()
    if candidate != root and root not in candidate.parents:
        return None  # パストラバーサル
    return candidate if candidate.is_file() else None


# --------------------------------------------------------------------------
# persistence
# --------------------------------------------------------------------------

def _loads(value: Any, fallback: Any) -> Any:
    try:
        parsed = json.loads(value or "null")
    except (TypeError, ValueError):
        return fallback
    return fallback if parsed is None else parsed


def _row_to_session(row) -> AgentSession:
    return AgentSession(
        id=row["id"],
        created_at=row["created_at"],
        title=row["title"] or "",
        status=row["status"],
        checkin_mode=row["checkin_mode"],
        auto_limit=row["auto_limit"],
        messages=[
            AgentMessage(**m)
            for m in _loads(row["messages"], [])
            if isinstance(m, dict)
        ],
        plan=AgentPlan(**_loads(row["plan"], {})),
        artifacts=[
            AgentArtifact(**a)
            for a in _loads(row["artifacts"], [])
            if isinstance(a, dict)
        ],
        nsfw=bool(row["nsfw"]),
        nsfw_source=row["nsfw_source"] or "",
    )


def _dump(values: list[Any] | Any) -> str:
    if isinstance(values, list):
        return json.dumps([v.model_dump() for v in values], ensure_ascii=False)
    return json.dumps(values.model_dump(), ensure_ascii=False)


async def insert(session: AgentSession) -> AgentSession:
    async with get_db() as conn:
        await conn.execute(
            "INSERT INTO agent_sessions (id, created_at, title, status, checkin_mode,"
            " auto_limit, messages, plan, artifacts, nsfw, nsfw_source)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                session.id,
                session.created_at,
                session.title,
                session.status,
                session.checkin_mode,
                session.auto_limit,
                _dump(session.messages),
                _dump(session.plan),
                _dump(session.artifacts),
                1 if session.nsfw else 0,
                session.nsfw_source,
            ),
        )
        await conn.commit()
    session_dir(session.id)
    return session


async def load(session_id: str) -> AgentSession | None:
    async with get_db() as conn:
        async with conn.execute(
            "SELECT * FROM agent_sessions WHERE id = ?", (session_id,)
        ) as cur:
            row = await cur.fetchone()
    return _row_to_session(row) if row else None


async def list_sessions(limit: int = 50, offset: int = 0) -> list[AgentSessionSummary]:
    async with get_db() as conn:
        async with conn.execute(
            "SELECT * FROM agent_sessions ORDER BY created_at DESC, id DESC"
            " LIMIT ? OFFSET ?",
            (limit, offset),
        ) as cur:
            rows = await cur.fetchall()
    sessions = [_row_to_session(r) for r in rows]
    return [
        AgentSessionSummary(
            id=s.id,
            created_at=s.created_at,
            title=s.title,
            status=s.status,
            checkin_mode=s.checkin_mode,
            auto_limit=s.auto_limit,
            message_count=len([m for m in s.messages if m.role != "system"]),
            task_count=len(s.plan.tasks),
            artifact_count=len(s.artifacts),
            nsfw=s.nsfw,
            nsfw_source=s.nsfw_source,
        )
        for s in sessions
    ]


async def update(session_id: str, **fields: Any) -> None:
    """Partial update; ``messages`` / ``plan`` / ``artifacts`` are serialized here."""
    if not fields:
        return
    values: dict[str, Any] = {}
    for key, value in fields.items():
        if key in ("messages", "artifacts", "plan"):
            values[key] = _dump(value)
        else:
            values[key] = value
    assignments = ", ".join(f"{k} = :{k}" for k in values)
    async with get_db() as conn:
        await conn.execute(
            f"UPDATE agent_sessions SET {assignments} WHERE id = :id",
            {**values, "id": session_id},
        )
        await conn.commit()


async def delete(session_id: str) -> bool:
    async with get_db() as conn:
        cur = await conn.execute(
            "DELETE FROM agent_sessions WHERE id = ?", (session_id,)
        )
        await conn.commit()
        deleted = cur.rowcount > 0
    if deleted:
        shutil.rmtree(AGENT_SESSIONS_DIR / session_id, ignore_errors=True)
    return deleted
