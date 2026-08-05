"""Canvas Studio の永続化と作業ディレクトリ（agent_store.py と同じ流儀）。

agent_sessions が JSON ブロブ 1 行なのに対し、こちらはカード・会話を正規化した
行で持つ（カード 1 枚の移動で全体を書き直さないため）。外部キーは既存テーブルに
合わせて宣言せず、プロジェクト削除時にここが nodes / messages を消す。
"""

from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .. import ws
from ..db import get_db
from ..ids import new_id
from ..paths import RUNTIME_DIR
from .models import (
    CanvasMessage,
    CanvasNode,
    CanvasNodeCreate,
    CanvasProgress,
    CanvasProject,
    CanvasViewport,
)

log = logging.getLogger(__name__)

#: 1 プロジェクト 1 作業ディレクトリ（LLM CLI がここで走る）
CANVAS_PROJECTS_DIR = RUNTIME_DIR / "canvas-projects"


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def project_dir(project_id: str) -> Path:
    """``runtime/canvas-projects/<id>/``（必要になった時点で作る）。"""
    path = CANVAS_PROJECTS_DIR / project_id
    path.mkdir(parents=True, exist_ok=True)
    return path


# --------------------------------------------------------------------------
# WS
# --------------------------------------------------------------------------

async def publish(progress: CanvasProgress) -> None:
    """Broadcast one canvas event (``type: "canvas"``). Never raises."""
    try:
        await ws.hub.broadcast(progress.model_dump())
    except Exception:  # noqa: BLE001 - 通知の失敗で操作を壊さない
        log.debug("canvas ws publish failed", exc_info=True)


# --------------------------------------------------------------------------
# row -> model
# --------------------------------------------------------------------------

def _loads(value: Any, fallback: Any) -> Any:
    try:
        parsed = json.loads(value or "null")
    except (TypeError, ValueError):
        return fallback
    return fallback if parsed is None else parsed


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _row_to_project(row) -> CanvasProject:
    return CanvasProject(
        id=row["id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        title=row["title"] or "",
        llm=row["llm"] or "grok",
        viewport=CanvasViewport(**_loads(row["viewport"], {})),
    )


def _row_to_node(row) -> CanvasNode:
    return CanvasNode(
        id=row["id"],
        project_id=row["project_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        kind=row["kind"],
        title=row["title"] or "",
        data=_loads(row["data"], {}),
        x=row["x"],
        y=row["y"],
        w=row["w"],
        h=row["h"],
        z=row["z"],
    )


def _row_to_message(row) -> CanvasMessage:
    return CanvasMessage(
        id=row["id"],
        project_id=row["project_id"],
        ts=row["ts"],
        role=row["role"],
        content=row["content"] or "",
        kind=row["kind"],
        data=_loads(row["data"], {}),
    )


# --------------------------------------------------------------------------
# projects
# --------------------------------------------------------------------------

async def create_project(title: str = "", llm: str = "grok") -> CanvasProject:
    project = CanvasProject(
        id=new_id(), created_at=now(), updated_at=now(), title=title, llm=llm
    )
    async with get_db() as conn:
        await conn.execute(
            "INSERT INTO canvas_projects (id, created_at, updated_at, title, llm,"
            " viewport) VALUES (?,?,?,?,?,?)",
            (
                project.id,
                project.created_at,
                project.updated_at,
                project.title,
                project.llm,
                _dumps(project.viewport.model_dump()),
            ),
        )
        await conn.commit()
    project_dir(project.id)
    return project


async def load_project(project_id: str) -> CanvasProject | None:
    async with get_db() as conn:
        async with conn.execute(
            "SELECT * FROM canvas_projects WHERE id = ?", (project_id,)
        ) as cur:
            row = await cur.fetchone()
    return _row_to_project(row) if row else None


async def list_projects(limit: int = 50, offset: int = 0) -> list[CanvasProject]:
    async with get_db() as conn:
        async with conn.execute(
            "SELECT * FROM canvas_projects ORDER BY updated_at DESC, id DESC"
            " LIMIT ? OFFSET ?",
            (limit, offset),
        ) as cur:
            rows = await cur.fetchall()
    return [_row_to_project(row) for row in rows]


async def update_project(project_id: str, **fields: Any) -> CanvasProject | None:
    """Partial update; ``viewport`` はここで JSON 化する。``updated_at`` は常に更新。"""
    values: dict[str, Any] = {}
    for key, value in fields.items():
        if value is None:
            continue
        if key == "viewport":
            values[key] = _dumps(
                value.model_dump() if isinstance(value, CanvasViewport) else value
            )
        else:
            values[key] = value
    values["updated_at"] = now()
    assignments = ", ".join(f"{k} = :{k}" for k in values)
    async with get_db() as conn:
        await conn.execute(
            f"UPDATE canvas_projects SET {assignments} WHERE id = :id",
            {**values, "id": project_id},
        )
        await conn.commit()
    return await load_project(project_id)


async def touch_project(project_id: str) -> None:
    """カード・会話の書き込みで親プロジェクトの ``updated_at`` を進める。"""
    async with get_db() as conn:
        await conn.execute(
            "UPDATE canvas_projects SET updated_at = ? WHERE id = ?",
            (now(), project_id),
        )
        await conn.commit()


async def delete_project(project_id: str) -> bool:
    """プロジェクト本体とカード・会話・作業ディレクトリを消す（アプリ層カスケード）。"""
    async with get_db() as conn:
        cur = await conn.execute(
            "DELETE FROM canvas_projects WHERE id = ?", (project_id,)
        )
        deleted = cur.rowcount > 0
        if deleted:
            await conn.execute(
                "DELETE FROM canvas_nodes WHERE project_id = ?", (project_id,)
            )
            await conn.execute(
                "DELETE FROM canvas_messages WHERE project_id = ?", (project_id,)
            )
        await conn.commit()
    if deleted:
        shutil.rmtree(CANVAS_PROJECTS_DIR / project_id, ignore_errors=True)
    return deleted


# --------------------------------------------------------------------------
# nodes
# --------------------------------------------------------------------------

async def insert_node(project_id: str, payload: CanvasNodeCreate) -> CanvasNode:
    """カードを 1 枚足す（``z`` は作成順に単調増加させて最前面に置く）。"""
    node = CanvasNode(
        id=new_id(),
        project_id=project_id,
        created_at=now(),
        updated_at=now(),
        kind=payload.kind,
        title=payload.title,
        data=payload.data,
        x=payload.x,
        y=payload.y,
        w=payload.w,
        h=payload.h,
    )
    async with get_db() as conn:
        async with conn.execute(
            "SELECT COALESCE(MAX(z), 0) + 1 AS next FROM canvas_nodes"
            " WHERE project_id = ?",
            (project_id,),
        ) as cur:
            row = await cur.fetchone()
        node.z = int(row["next"]) if row else 1
        await conn.execute(
            "INSERT INTO canvas_nodes (id, project_id, created_at, updated_at, kind,"
            " title, data, x, y, w, h, z) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                node.id,
                node.project_id,
                node.created_at,
                node.updated_at,
                node.kind,
                node.title,
                _dumps(node.data),
                node.x,
                node.y,
                node.w,
                node.h,
                node.z,
            ),
        )
        await conn.commit()
    await touch_project(project_id)
    return node


async def load_node(project_id: str, node_id: str) -> CanvasNode | None:
    async with get_db() as conn:
        async with conn.execute(
            "SELECT * FROM canvas_nodes WHERE project_id = ? AND id = ?",
            (project_id, node_id),
        ) as cur:
            row = await cur.fetchone()
    return _row_to_node(row) if row else None


async def list_nodes(project_id: str) -> list[CanvasNode]:
    async with get_db() as conn:
        async with conn.execute(
            "SELECT * FROM canvas_nodes WHERE project_id = ? ORDER BY z, id",
            (project_id,),
        ) as cur:
            rows = await cur.fetchall()
    return [_row_to_node(row) for row in rows]


async def update_node(
    project_id: str, node_id: str, **fields: Any
) -> CanvasNode | None:
    """送られた項目だけ変える（``data`` は全量置換、``updated_at`` は常に更新）。"""
    values: dict[str, Any] = {}
    for key, value in fields.items():
        if value is None:
            continue
        values[key] = _dumps(value) if key == "data" else value
    values["updated_at"] = now()
    assignments = ", ".join(f"{k} = :{k}" for k in values)
    async with get_db() as conn:
        await conn.execute(
            f"UPDATE canvas_nodes SET {assignments}"
            " WHERE id = :id AND project_id = :project_id",
            {**values, "id": node_id, "project_id": project_id},
        )
        await conn.commit()
    await touch_project(project_id)
    return await load_node(project_id, node_id)


async def delete_node(project_id: str, node_id: str) -> bool:
    async with get_db() as conn:
        cur = await conn.execute(
            "DELETE FROM canvas_nodes WHERE project_id = ? AND id = ?",
            (project_id, node_id),
        )
        await conn.commit()
        deleted = cur.rowcount > 0
    if deleted:
        await touch_project(project_id)
    return deleted


# --------------------------------------------------------------------------
# messages
# --------------------------------------------------------------------------

async def append_message(
    project_id: str,
    role: str,
    content: str,
    kind: str | None = None,
    data: dict[str, Any] | None = None,
) -> CanvasMessage:
    message = CanvasMessage(
        id=new_id(),
        project_id=project_id,
        ts=now(),
        role=role,  # type: ignore[arg-type]
        content=content,
        kind=kind,
        data=data or {},
    )
    async with get_db() as conn:
        await conn.execute(
            "INSERT INTO canvas_messages (id, project_id, ts, role, content, kind,"
            " data) VALUES (?,?,?,?,?,?,?)",
            (
                message.id,
                message.project_id,
                message.ts,
                message.role,
                message.content,
                message.kind,
                _dumps(message.data),
            ),
        )
        await conn.commit()
    await touch_project(project_id)
    return message


async def list_messages(project_id: str) -> list[CanvasMessage]:
    async with get_db() as conn:
        async with conn.execute(
            "SELECT * FROM canvas_messages WHERE project_id = ? ORDER BY ts, id",
            (project_id,),
        ) as cur:
            rows = await cur.fetchall()
    return [_row_to_message(row) for row in rows]
