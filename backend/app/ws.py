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
    ChatProgress,
    JobProgress,
    LibraryItem,
    LibraryProgress,
    ModelDownload,
    ModelDownloadProgress,
    StudioEvent,
    TimelineExportProgress,
    UiFormProgress,
    UiNavigateEvent,
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


async def publish_chat(
    session_id: str, *, running: bool, activity: str | None = None
) -> None:
    """Broadcast one prompt-chat event (``type: "chat"``). Never raises.

    会話の正本は ``chat_sessions.messages`` なので、ここで流すのは「Grok の
    ターンが走っているか」と実行中の活動テキストだけ。
    """
    try:
        payload = ChatProgress(
            session_id=session_id, running=running, activity=activity
        ).model_dump()
    except Exception:  # noqa: BLE001 - 通知の失敗で相談を壊さない
        payload = {
            "type": "chat",
            "session_id": session_id,
            "running": running,
            "activity": activity,
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


async def publish_timeline_export(
    export_id: str,
    timeline_id: str,
    status: str,
    *,
    progress: float = 0.0,
    output_url: str | None = None,
    error: str | None = None,
) -> None:
    """編集タブの書き出し進捗を配信（``type: "timeline_export"``）。Never raises.

    書き出しの正は ``timeline_exports`` なので、ここで流すのは状態と進捗だけ。
    取りこぼしたブラウザは履歴（``GET /timelines/{id}/exports``）で追いつける。
    """
    try:
        payload = TimelineExportProgress(
            export_id=export_id,
            timeline_id=timeline_id,
            status=status,  # type: ignore[arg-type]
            progress=progress,
            output_url=output_url,
            error=error,
        ).model_dump()
    except Exception:  # noqa: BLE001 - 通知の失敗で書き出しを壊さない
        payload = {
            "type": "timeline_export",
            "export_id": export_id,
            "timeline_id": timeline_id,
            "status": status,
            "progress": progress,
            "output_url": output_url,
            "error": error,
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


async def publish_studio(
    project_id: str,
    entity: str,
    entity_id: str = "",
    op: str = "update",
) -> None:
    """スタジオの更新を配信（``type: "studio"``）。Never raises.

    外部エージェントが API から脚本や素材を書き換えたときに、開いているブラウザへ
    「その作品が動いた」とだけ伝える。正本は DB なので、受け取り側は詳細を取り
    直すだけ。取りこぼしより重複のほうが安いので、迷ったら流す。
    """
    try:
        payload = StudioEvent(
            project_id=project_id,
            entity=entity,  # type: ignore[arg-type]
            id=entity_id,
            op=op,  # type: ignore[arg-type]
        ).model_dump()
    except Exception:  # noqa: BLE001 - 通知の失敗で編集を壊さない
        payload = {
            "type": "studio",
            "project_id": project_id,
            "entity": entity,
            "id": entity_id,
            "op": op,
        }
    await hub.broadcast(payload)


async def publish_form(
    revision: int, updated_by: str, values: dict[str, Any]
) -> None:
    """生成フォームの下書きが変わったことを配信（``type: "form"``）。Never raises.

    値そのものを載せる（正本は ``ui_state`` だが、フォームは 1 件しかなく小さい）。
    自分が書いた ``revision`` は送り主のブラウザ側で読み飛ばす。
    """
    try:
        payload = UiFormProgress(
            revision=revision, updated_by=updated_by, values=values
        ).model_dump()
    except Exception:  # noqa: BLE001 - 通知の失敗で保存を壊さない
        payload = {
            "type": "form",
            "revision": revision,
            "updated_by": updated_by,
            "values": values,
        }
    await hub.broadcast(payload)


async def publish_navigate(
    view: str, project_id: str | None = None, shot_id: str | None = None
) -> None:
    """ブラウザの画面を動かす指示を配信（``type: "ui"``、``op: "navigate"``）。"""
    try:
        payload = UiNavigateEvent(
            view=view,  # type: ignore[arg-type]
            project_id=project_id,
            shot_id=shot_id,
        ).model_dump()
    except Exception:  # noqa: BLE001 - 通知の失敗で API を壊さない
        payload = {
            "type": "ui",
            "op": "navigate",
            "view": view,
            "project_id": project_id,
            "shot_id": shot_id,
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
