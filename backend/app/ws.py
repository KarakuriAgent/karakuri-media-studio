"""Frontend-facing WebSocket hub (SPEC §9 ``WS /api/ws``).

The job runner calls :func:`publish` on every state transition and on every
ComfyUI progress event; every browser connected to ``/api/ws`` receives the
same JSON payload::

    {"type": "job", "job_id": …, "status": …, "node": …, "progress": …, "message": …}

A plain set of connections is enough: the app is a single-user local tool and
messages are fire-and-forget (a dead socket is simply dropped).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from .models import (
    AgentArtifact,
    AgentProgress,
    CanvasMessage,
    CanvasProgress,
    JobProgress,
    LibraryItem,
    LibraryProgress,
    ModelDownload,
    ModelDownloadProgress,
)

log = logging.getLogger(__name__)

router = APIRouter(tags=["ws"])


class Hub:
    """Broadcast-only connection registry."""

    def __init__(self) -> None:
        self._connections: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    @property
    def count(self) -> int:
        return len(self._connections)

    async def register(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._connections.add(websocket)

    async def unregister(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._connections.discard(websocket)

    async def broadcast(self, payload: dict[str, Any]) -> None:
        async with self._lock:
            targets = list(self._connections)
        dead: list[WebSocket] = []
        for websocket in targets:
            try:
                await websocket.send_json(payload)
            except Exception:  # noqa: BLE001 - a broken socket must not kill a job
                dead.append(websocket)
        if dead:
            async with self._lock:
                self._connections.difference_update(dead)


hub = Hub()


async def publish(
    job_id: str,
    status: str,
    *,
    node: str | None = None,
    progress: float | None = None,
    message: str | None = None,
    nsfw: bool | None = None,
) -> None:
    """Broadcast one job event. Never raises."""
    try:
        payload = JobProgress(
            job_id=job_id,
            status=status,  # type: ignore[arg-type]
            node=node,
            progress=progress,
            message=message,
            nsfw=nsfw,
        ).model_dump()
    except Exception:  # noqa: BLE001 - unknown status must not break the job
        payload = {
            "type": "job",
            "job_id": job_id,
            "status": status,
            "node": node,
            "progress": progress,
            "message": message,
            "nsfw": nsfw,
        }
    await hub.broadcast(payload)


async def publish_agent(
    session_id: str,
    status: str,
    *,
    task_id: str | None = None,
    task_status: str | None = None,
    job_id: str | None = None,
    artifact: AgentArtifact | None = None,
    message: str | None = None,
    thinking: bool | None = None,
    activity: str | None = None,
) -> None:
    """Broadcast one agent event (``type: "agent"``). Never raises."""
    try:
        payload = AgentProgress(
            session_id=session_id,
            status=status,  # type: ignore[arg-type]
            task_id=task_id,
            task_status=task_status,  # type: ignore[arg-type]
            job_id=job_id,
            artifact=artifact,
            message=message,
            thinking=thinking,
            activity=activity,
        ).model_dump()
    except Exception:  # noqa: BLE001 - an unknown status must not break the loop
        payload = {
            "type": "agent",
            "session_id": session_id,
            "status": status,
            "task_id": task_id,
            "task_status": task_status,
            "job_id": job_id,
            "artifact": artifact.model_dump() if artifact else None,
            "message": message,
            "thinking": thinking,
            "activity": activity,
        }
    await hub.broadcast(payload)


async def publish_canvas(
    project_id: str,
    *,
    running: bool,
    activity: str | None = None,
    message: CanvasMessage | None = None,
) -> None:
    """Broadcast one canvas agent event (``type: "canvas"``). Never raises.

    会話の正は ``canvas_messages`` なので、ここで流すのは「いま足された 1 件」と
    実行中かどうかだけ。取りこぼしたブラウザは盤面を取り直せば追いつける。
    """
    try:
        payload = CanvasProgress(
            project_id=project_id,
            running=running,
            activity=activity,
            message=message,
        ).model_dump()
    except Exception:  # noqa: BLE001 - 通知の失敗で実行を壊さない
        payload = {
            "type": "canvas",
            "project_id": project_id,
            "running": running,
            "activity": activity,
            "message": message.model_dump() if message else None,
        }
    await hub.broadcast(payload)


async def publish_library(item: LibraryItem) -> None:
    """Broadcast one library update (``type: "library"``). Never raises.

    自動タグ生成のように、登録のあとから内容が変わったことを画面に伝える。
    """
    try:
        payload = LibraryProgress(
            item_id=item.id, kind=item.kind, name=item.name, tags=item.tags
        ).model_dump()
    except Exception:  # noqa: BLE001 - 通知の失敗で登録を壊さない
        payload = {
            "type": "library",
            "item_id": item.id,
            "kind": item.kind,
            "name": item.name,
            "tags": list(item.tags),
        }
    await hub.broadcast(payload)


async def publish_model_download(state: ModelDownload) -> None:
    """不足モデルのダウンロード進捗を配信（``type: "model_download"``、SPEC §3.3）。

    Never raises: 通知に失敗してもダウンロードは続ける。
    """
    try:
        payload = ModelDownloadProgress(
            filename=state.filename,
            status=state.status,
            received=state.received,
            total=state.total,
            error=state.error,
        ).model_dump()
    except Exception:  # noqa: BLE001 - 通知の失敗でダウンロードを壊さない
        payload = {
            "type": "model_download",
            "filename": state.filename,
            "status": state.status,
            "received": state.received,
            "total": state.total,
            "error": state.error,
        }
    await hub.broadcast(payload)


@router.websocket("/api/ws")
async def job_events(websocket: WebSocket) -> None:
    await websocket.accept()
    await hub.register(websocket)
    try:
        while True:
            # The client is not expected to send anything; receiving keeps the
            # connection alive and surfaces the disconnect.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # noqa: BLE001
        log.debug("ws connection closed: %s", exc)
    finally:
        await hub.unregister(websocket)
