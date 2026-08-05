"""Canvas Studio API（カード型無限キャンバス）。

既存のエージェントモード（/api/agent）とは何も共有しない独立サブシステムで、
ルーターの作りだけ routers/agent.py に合わせている。プロジェクトとカードの
CRUD に加え、P2 でエージェントとの会話（POST …/messages / stop）を持つ。
"""

from __future__ import annotations

import json
import logging
import re

from fastapi import APIRouter, HTTPException, Query
from pydantic import ValidationError

from .. import grok, workflows
from ..canvas import llm, runner, store
from ..canvas.models import (
    CanvasMessage,
    CanvasNode,
    CanvasNodeCreate,
    CanvasNodeUpdate,
    CanvasProgress,
    CanvasProject,
    CanvasProjectCreate,
    CanvasProjectDetail,
    CanvasProjectUpdate,
    CanvasReply,
    CanvasSendMessage,
    ModelData,
    validate_node_data,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/canvas", tags=["canvas"])


async def _require(project_id: str) -> CanvasProject:
    project = await store.load_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="canvas project not found")
    return project


async def _require_node(project_id: str, node_id: str) -> CanvasNode:
    node = await store.load_node(project_id, node_id)
    if node is None:
        raise HTTPException(status_code=404, detail="canvas node not found")
    return node


async def _detail(project_id: str) -> CanvasProjectDetail:
    project = await _require(project_id)
    return CanvasProjectDetail(
        **project.model_dump(),
        nodes=await store.list_nodes(project_id),
        messages=await store.list_messages(project_id),
        # thinking は runner のインメモリ状態（DB には保存しない）。
        thinking=runner.is_thinking(project_id),
    )


def _validated_data(kind: str, data: dict) -> dict:
    """kind のスキーマで data を検証・正規化する（違反は 422）。"""
    try:
        normalized = validate_node_data(kind, data)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if kind == "model":
        _check_workflow(ModelData(**normalized))
    return normalized


def _check_workflow(data: ModelData) -> None:
    """model カードのワークフロー ID が実在し、target と一致するか確かめる。

    未選択（空文字）は通す: 作りかけのカードを保存できないと、先にワークフローを
    決めないとカードすら置けなくなるため。
    """
    if not data.workflow:
        return
    try:
        workflows.get_spec(data.workflow, kind=data.target)
    except workflows.WorkflowSpecError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


# --------------------------------------------------------------------------
# projects
# --------------------------------------------------------------------------

@router.post("/projects", response_model=CanvasProject, status_code=201)
async def create_project(payload: CanvasProjectCreate) -> CanvasProject:
    return await store.create_project(title=payload.title)


@router.get("/projects", response_model=list[CanvasProject])
async def list_projects(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> list[CanvasProject]:
    return await store.list_projects(limit=limit, offset=offset)


@router.get("/projects/{project_id}", response_model=CanvasProjectDetail)
async def get_project(project_id: str) -> CanvasProjectDetail:
    return await _detail(project_id)


@router.patch("/projects/{project_id}", response_model=CanvasProject)
async def update_project(
    project_id: str, payload: CanvasProjectUpdate
) -> CanvasProject:
    await _require(project_id)
    if payload.llm is not None and payload.llm not in llm.PROVIDERS:
        raise HTTPException(
            status_code=422,
            detail=f"unknown canvas llm provider: {payload.llm}"
            f" (allowed: {', '.join(llm.PROVIDERS)})",
        )
    updated = await store.update_project(
        project_id,
        title=payload.title,
        llm=payload.llm,
        viewport=payload.viewport,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="canvas project not found")
    return updated


@router.delete("/projects/{project_id}", status_code=204, response_model=None)
async def delete_project(project_id: str) -> None:
    # 消えたプロジェクトに media カードを生やそうとしないよう、先に畳む。
    runner.cancel_generate(project_id)
    if not await store.delete_project(project_id):
        raise HTTPException(status_code=404, detail="canvas project not found")


# --------------------------------------------------------------------------
# nodes
# --------------------------------------------------------------------------

@router.post(
    "/projects/{project_id}/nodes", response_model=CanvasNode, status_code=201
)
async def create_node(project_id: str, payload: CanvasNodeCreate) -> CanvasNode:
    await _require(project_id)
    payload.data = _validated_data(payload.kind, payload.data)
    node = await store.insert_node(project_id, payload)
    await store.publish(
        CanvasProgress(project_id=project_id, event="node_created", node_id=node.id)
    )
    return node


@router.patch(
    "/projects/{project_id}/nodes/{node_id}", response_model=CanvasNode
)
async def update_node(
    project_id: str, node_id: str, payload: CanvasNodeUpdate
) -> CanvasNode:
    await _require(project_id)
    node = await _require_node(project_id, node_id)
    data = (
        _validated_data(node.kind, payload.data) if payload.data is not None else None
    )
    updated = await store.update_node(
        project_id,
        node_id,
        title=payload.title,
        data=data,
        x=payload.x,
        y=payload.y,
        w=payload.w,
        h=payload.h,
        z=payload.z,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="canvas node not found")
    await store.publish(
        CanvasProgress(project_id=project_id, event="node_updated", node_id=node_id)
    )
    return updated


@router.delete(
    "/projects/{project_id}/nodes/{node_id}", status_code=204, response_model=None
)
async def delete_node(project_id: str, node_id: str) -> None:
    await _require(project_id)
    if not await store.delete_node(project_id, node_id):
        raise HTTPException(status_code=404, detail="canvas node not found")
    await store.publish(
        CanvasProgress(project_id=project_id, event="node_deleted", node_id=node_id)
    )


# --------------------------------------------------------------------------
# conversation
# --------------------------------------------------------------------------

@router.get("/projects/{project_id}/messages", response_model=list[CanvasMessage])
async def list_messages(project_id: str) -> list[CanvasMessage]:
    await _require(project_id)
    return await store.list_messages(project_id)


#: @ 参照（models.ELEMENT_REFERENCE と同じ文字クラス。``\w`` は日本語も拾う）
MENTION_RE = re.compile(r"@([\w-]+)")

MENTION_HEADER = "[Referenced cards — full contents]"


async def expand_mentions(project_id: str, content: str) -> tuple[str, list[str]]:
    """``@カード名`` を参照カードの全文に展開する（本文と参照 id を返す）。

    候補はタイトル完全一致のみ。存在しないタイトルはそのまま素通しする
    （ただのメールアドレスや記号として書いただけ、ということがあるため）。
    """
    nodes = await store.list_nodes(project_id)
    by_title = {node.title: node for node in nodes if node.title}
    # 同じカードを 2 回書いても展開は 1 度だけ（出現順は保つ）
    titles = list(dict.fromkeys(MENTION_RE.findall(content)))
    unique = [by_title[name] for name in titles if name in by_title]
    if not unique:
        return content, []
    blocks = [
        f"### {node.title} (kind: {node.kind}, id: {node.id})\n"
        + json.dumps(node.data, ensure_ascii=False, indent=2)
        for node in unique
    ]
    expanded = f"{content}\n\n{MENTION_HEADER}\n" + "\n\n".join(blocks)
    return expanded, [node.id for node in unique]


@router.post("/projects/{project_id}/messages", response_model=CanvasReply)
async def send_message(project_id: str, payload: CanvasSendMessage) -> CanvasReply:
    content = payload.content.strip()
    if not content:
        raise HTTPException(status_code=422, detail="content が空です")
    # 実行権の予約はここで（await を挟む前に）取る: 途中に await があると、その隙に
    # 入った同時 POST が両方ともガードを抜けてターンループが 2 本走ってしまう。
    if not runner.try_start(project_id):
        raise HTTPException(
            status_code=409, detail="実行中です。停止するか完了を待ってください"
        )
    try:
        await _require(project_id)
        expanded, mentions = await expand_mentions(project_id, content)
        # 展開後の本文を LLM に渡す一方、UI が元発言だけ出せるよう data に控える。
        await store.append_message(
            project_id,
            "user",
            expanded,
            data={"text": content, "mentions": mentions},
        )
        await store.publish(CanvasProgress(project_id=project_id, event="message"))
    except BaseException:
        # 会話を始められなかったので席を返す（run_conversation に入れば以降の
        # 解放はそちらの finally が持つ）。
        await runner.release(project_id)
        raise
    try:
        reply = await runner.run_conversation(project_id, reserved=True)
    except grok.LLMError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return CanvasReply(content=reply, project=await _detail(project_id))


@router.post("/projects/{project_id}/stop", response_model=CanvasProjectDetail)
async def stop(project_id: str) -> CanvasProjectDetail:
    await _require(project_id)
    await runner.request_stop(project_id)
    return await _detail(project_id)
