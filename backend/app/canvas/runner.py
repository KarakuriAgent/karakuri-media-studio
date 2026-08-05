"""Canvas エージェントのターンループ（オンデマンド方式）。

``POST /api/canvas/projects/{id}/messages`` のリクエストの中で観測・編集系の
アクションを回し切り、``message`` / ``done`` が出たらその本文を返す。ターン数は
:data:`MAX_TURNS` で頭打ちにする（暴走防止）。

LLM クライアントは必ず :mod:`app.canvas.llm` のファクトリ経由で取る（provider を
増やすときにここを触らなくて済むように、``grok`` は直接 import しない）。
"""

from __future__ import annotations

import asyncio
import json
import logging

from . import generate, llm, prompt, protocol, store
from .models import CanvasMessage, CanvasNode, CanvasNodeCreate, CanvasProgress
from .protocol import CanvasAction

log = logging.getLogger(__name__)

#: 1 ユーザー発言あたりの連続ターン上限
MAX_TURNS = 12

#: ターンを回しているプロジェクト（thinking の唯一の情報源。DB には保存しない）
_thinking: set[str] = set()
#: 停止要求（次のターンの手前で見る）
_stop_requests: set[str] = set()
#: 完了待ち中の generate（プロジェクトごとに 1 本。stop / 削除で cancel する）
_generating: dict[str, asyncio.Task] = {}


class AlreadyRunningError(RuntimeError):
    """既にターンが走っているプロジェクトで会話を始めようとした。"""

    def __init__(self, project_id: str) -> None:
        super().__init__(f"canvas project {project_id} is already running")
        self.project_id = project_id


def is_thinking(project_id: str) -> bool:
    """ターンが走っているか（インメモリ。DB には保存しない）。"""
    return project_id in _thinking


def try_start(project_id: str) -> bool:
    """実行権を予約する（走っていなければ立てて True、走っていれば False）。

    **await を挟まない** check-and-set であることが肝心: 同時 POST が両方とも
    「実行中でない」と判断してターンループを 2 本走らせないよう、入口の
    ``send_message`` はここで席を取ってから他の await に進む。

    True を返したら、必ず :func:`run_conversation` (``reserved=True``) に渡すか
    :func:`release` で解放すること。
    """
    if project_id in _thinking:
        return False
    _thinking.add(project_id)
    return True


async def release(project_id: str) -> None:
    """:func:`try_start` の予約を解放する（ターンを始めずに終わるとき）。"""
    _stop_requests.discard(project_id)
    _thinking.discard(project_id)
    await _publish_thinking(project_id, False)


def is_generating(project_id: str) -> bool:
    """バックグラウンドの完了待ちが走っているか。"""
    task = _generating.get(project_id)
    return task is not None and not task.done()


def cancel_generate(project_id: str) -> None:
    """完了待ちのタスクを畳む（ジョブ自体は ComfyUI 側で走り続ける）。"""
    task = _generating.pop(project_id, None)
    if task is not None and not task.done():
        task.cancel()


async def request_stop(project_id: str) -> None:
    """次のターンの手前でループを畳む（実行中のターン自体は待つ）。"""
    if is_thinking(project_id):
        _stop_requests.add(project_id)
    cancel_generate(project_id)


def reset() -> None:
    """テスト用: インメモリ状態を捨てる。"""
    _thinking.clear()
    _stop_requests.clear()
    for task in list(_generating.values()):
        if not task.done():
            task.cancel()
    _generating.clear()


async def _publish_thinking(project_id: str, value: bool) -> None:
    """thinking の状態を WS に流すだけ（集合の出し入れは呼び出し側の責務）。"""
    await store.publish(
        CanvasProgress(project_id=project_id, event="thinking", thinking=value)
    )


async def _event(
    project_id: str, kind: str, content: str, *, node_id: str | None = None
) -> None:
    """アクションの結果をトランスクリプトに残し、次のターンで読ませる。"""
    await store.append_message(project_id, "event", content, kind=kind)
    await store.publish(
        CanvasProgress(
            project_id=project_id, event="message", node_id=node_id, message=content
        )
    )


# --------------------------------------------------------------------------
# 1 ターン
# --------------------------------------------------------------------------

async def _run_turn(
    project_id: str, provider: str = llm.DEFAULT_PROVIDER
) -> tuple[str, CanvasAction | None]:
    """LLM を 1 回（不正なら再試行込みで最大 2 回）呼び、応答とアクションを返す。"""
    client = llm.get_client(provider, store.project_dir(project_id))
    messages = await store.list_messages(project_id)
    nodes = await store.list_nodes(project_id)
    # 目次はカードの増減で変わるので毎ターン焼き直す。
    system = prompt.build_canvas_system_prompt(nodes)

    answer = await client.complete(prompt.build_canvas_conversation(system, messages))
    action: CanvasAction | None = None
    reason = ""
    try:
        action = protocol.parse_action(answer)
    except protocol.CanvasActionError as exc:
        reason = str(exc)

    if action is None and (reason or protocol.looks_like_action_attempt(answer)):
        history = [*messages, _assistant_stub(project_id, answer)]
        answer = await client.complete(
            prompt.build_canvas_conversation(
                system,
                history,
                retry_reason=reason or "JSON を解釈できませんでした",
            )
        )
        try:
            action = protocol.parse_action(answer)
            reason = ""
        except protocol.CanvasActionError as exc:
            action, reason = None, str(exc)

    await store.append_message(project_id, "assistant", answer)
    await store.publish(CanvasProgress(project_id=project_id, event="message"))
    if reason:
        await _event(
            project_id,
            "action_invalid",
            f"アクションを解釈できませんでした: {reason}",
        )
    return answer, action


def _assistant_stub(project_id: str, content: str) -> CanvasMessage:
    """再試行プロンプトに載せるだけの assistant 発言（DB には書かない）。"""
    return CanvasMessage(
        id="",
        project_id=project_id,
        ts=store.now(),
        role="assistant",
        content=content,
    )


# --------------------------------------------------------------------------
# アクションの実行
# --------------------------------------------------------------------------

def _index_text(nodes: list[CanvasNode]) -> str:
    if not nodes:
        return "カードはまだ 1 枚もありません。"
    lines = [f"- {n.id} | {n.kind} | {n.title or '(無題)'}" for n in nodes]
    return "キャンバスのカード一覧:\n" + "\n".join(lines)


async def _apply(project_id: str, action: CanvasAction) -> None:
    """観測・編集アクションを実行し、結果を EVENT として書く。"""
    name = action.action

    if name == "list_nodes":
        nodes = await store.list_nodes(project_id)
        await _event(project_id, "list_nodes", _index_text(nodes))
        return

    if name == "read_node":
        node = await store.load_node(project_id, action.node_id or "")
        if node is None:
            await _event(
                project_id,
                "read_node",
                f"カード {action.node_id} は見つかりませんでした。"
                "list_nodes で id を確かめてください。",
            )
            return
        body = json.dumps(node.data, ensure_ascii=False, indent=2)
        await _event(
            project_id,
            "read_node",
            f"### {node.title or '(無題)'} (kind: {node.kind}, id: {node.id})\n{body}",
            node_id=node.id,
        )
        return

    if name == "create_node":
        node = await store.insert_node(
            project_id,
            CanvasNodeCreate(
                kind=action.kind,  # type: ignore[arg-type]
                title=action.title,
                data=action.data,
                x=action.x or 0.0,
                y=action.y or 0.0,
            ),
        )
        await store.publish(
            CanvasProgress(
                project_id=project_id, event="node_created", node_id=node.id
            )
        )
        await _event(
            project_id,
            "create_node",
            f"{node.kind} カード「{node.title or '(無題)'}」を作成しました"
            f" (id {node.id})",
            node_id=node.id,
        )
        return

    if name == "update_node":
        node = await store.load_node(project_id, action.node_id or "")
        if node is None:
            await _event(
                project_id,
                "update_node",
                f"カード {action.node_id} は見つかりませんでした。",
            )
            return
        try:
            data = protocol.validate_data(node.kind, action.data)
        except protocol.CanvasActionError as exc:
            await _event(project_id, "update_node", f"更新できませんでした: {exc}")
            return
        updated = await store.update_node(
            project_id,
            node.id,
            title=action.title or None,
            data=data,
        )
        await store.publish(
            CanvasProgress(project_id=project_id, event="node_updated", node_id=node.id)
        )
        title = (updated.title if updated else node.title) or "(無題)"
        await _event(
            project_id,
            "update_node",
            f"カード「{title}」を更新しました (id {node.id})",
            node_id=node.id,
        )
        return

    if name == "move_node":
        node = await store.load_node(project_id, action.node_id or "")
        if node is None:
            await _event(
                project_id,
                "move_node",
                f"カード {action.node_id} は見つかりませんでした。",
            )
            return
        await store.update_node(project_id, node.id, x=action.x, y=action.y)
        await store.publish(
            CanvasProgress(project_id=project_id, event="node_updated", node_id=node.id)
        )
        await _event(
            project_id,
            "move_node",
            f"カード「{node.title or '(無題)'}」を ({action.x:.0f}, {action.y:.0f})"
            " へ移動しました",
            node_id=node.id,
        )
        return

    if name == "best_practices":
        await _event(
            project_id,
            "best_practices",
            generate.best_practices_text(action.workflow),
        )
        return

    # generate: 投入だけ同期で行い、完了待ちと media カード配置は裏に回す
    # （ループは次のターンへ進み、完了は EVENT + WS でユーザーに届く）。
    await _start_generate(project_id, action)


async def _start_generate(project_id: str, action: CanvasAction) -> None:
    node = await store.load_node(project_id, action.model_node_id or "")
    if node is None:
        await _event(
            project_id,
            "generate_failed",
            f"model カード {action.model_node_id} は見つかりませんでした。"
            "list_nodes で id を確かめてください。",
        )
        return
    if node.kind != "model":
        await _event(
            project_id,
            "generate_failed",
            f"カード「{node.title or '(無題)'}」は {node.kind} カードです。"
            "generate には model カードの id を指定してください。",
            node_id=node.id,
        )
        return
    job = await generate.start_generate(project_id, node, action)
    if job is None:
        return
    task = asyncio.create_task(generate.await_generate(project_id, node, action, job))
    # 終わったタスクも「直近の generate」の控えとして残す（次の generate が
    # 上書きし、cancel_generate / reset で捨てる）。テストはこれを await する。
    _generating[project_id] = task
    task.add_done_callback(_log_generate_failure)


def _log_generate_failure(task: asyncio.Task) -> None:
    """完了待ちが例外で落ちたら握りつぶさずログに残す。"""
    if task.cancelled():
        return
    error = task.exception()
    if error is not None:
        log.warning("canvas generate task failed", exc_info=error)


# --------------------------------------------------------------------------
# 会話
# --------------------------------------------------------------------------

async def run_conversation(project_id: str, *, reserved: bool = False) -> str:
    """ユーザー発言を追記済みの状態から message / done が出るまで回す。

    ``reserved=True`` は「呼び出し側が :func:`try_start` で席を取ってある」の意。
    False のときはここで取り、取れなければ :class:`AlreadyRunningError` を投げる
    （thinking を二重に立てない / 他人の予約を解放しないため）。
    """
    if not reserved and not try_start(project_id):
        raise AlreadyRunningError(project_id)
    try:
        project = await store.load_project(project_id)
        provider = project.llm if project else llm.DEFAULT_PROVIDER
        await _publish_thinking(project_id, True)
        last_text = ""
        for _ in range(MAX_TURNS):
            if project_id in _stop_requests:
                await _event(project_id, "stopped", "停止しました。")
                return last_text or "停止しました。"
            answer, action = await _run_turn(project_id, provider)
            last_text = answer
            if action is None:
                return answer
            if action.action in protocol.TERMINAL_ACTIONS:
                return action.content or answer
            await _apply(project_id, action)
        return last_text + "\n（連続ターンが上限に達したため打ち切りました）"
    finally:
        await release(project_id)
