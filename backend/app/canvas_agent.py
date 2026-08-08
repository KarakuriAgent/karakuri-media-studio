"""キャンバスのチャットから走らせるエージェント（AGENT-MODE §5.4）。

```
ユーザー発言 ─→ Grok ターン ─→ action?
                    │ studio_* / canvas_* → 実行 → 結果イベント ─┐
                    │ done                → まとめて終了         │
                    │ なし（ただの返事）  → 終了                 └→ 次の Grok ターン
```

**キャンバス専用のエージェントは作らない。** ツールは :mod:`app.agent_runner` の
:func:`~app.agent_runner.run_tool`（スタジオの目録操作 + キャンバスの盤面操作）を
そのまま呼び、プロンプトも :mod:`app.prompts` の流儀で組む。ここが持つのは
「キャンバスの会話をどう回すか」だけ。

エージェントセッション（``agent_sessions``）は作らない: 会話の正は
``canvas_messages``（:mod:`app.canvas`）で、セッション行を並べて持つと同じ会話が
2 箇所に増えてしまう。プランの承認・生成本数の上限・成果物パネルといった
セッションの仕掛けはキャンバスには無く、必要になったらスタジオ側の
エージェントモードを開けばよい。work dir だけはエージェントセッションと同じ
置き場を ``canvas-<project_id>`` という名前で借りる（セッション行は作らない）。

進捗は WS（``type: "canvas"``）で流す。取りこぼしても
``GET /api/canvas/projects/{id}`` を取り直せば会話はそこに残っている。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from pathlib import Path

from . import agent_protocol, agent_runner, canvas, grok, prompts, studio, ws
from .agent_protocol import ActionError
from .agent_store import attachment_path, attachments_dir, session_dir
from .config import load_settings
from .models import AgentAction, AgentMessage, CanvasMessage

log = logging.getLogger(__name__)

#: 1 回の発言から回す Grok ターンの上限（暴走防止）。スタジオのループより短いのは、
#: キャンバスの操作が目録の読み書きだけで、待つべき生成が挟まらないため。
MAX_TURNS = 8

_runs: dict[str, asyncio.Task[None]] = {}
_stop_requests: set[str] = set()
_activity: dict[str, str] = {}
#: 実行ごとの「いま開いているタブ」（話の id。作品共通なら入らない）
_tabs: dict[str, str] = {}


def is_running(project_id: str) -> bool:
    """そのキャンバスでエージェントが走っているか（インメモリ）。"""
    task = _runs.get(project_id)
    return task is not None and not task.done()


def current_activity(project_id: str) -> str | None:
    """実行中の活動テキスト（インメモリ。DB には保存しない）。"""
    return _activity.get(project_id)


def _session_key(project_id: str) -> str:
    """エージェントセッションの置き場を借りるときの名前。"""
    return f"canvas-{project_id}"


def workdir(project_id: str) -> str:
    """このキャンバスの作業ディレクトリ（エージェントセッションと同じ置き場）。"""
    return str(session_dir(_session_key(project_id)))


def attachment_dir(project_id: str) -> Path:
    """チャットに添付されたファイルの置き場（``<workdir>/attachments/``）。

    grok CLI は作業ディレクトリを根に動くので、ここへ置いておけば
    ``attachments/<file>`` でも絶対パスでも開ける（エージェントモードの添付
    と同じ流儀）。
    """
    return attachments_dir(_session_key(project_id))


def resolve_attachment(project_id: str, rel: str) -> Path | None:
    """``attachments/<file>`` の実在ファイルだけを解決する（ほかは ``None``）。"""
    return attachment_path(_session_key(project_id), rel)


# --------------------------------------------------------------------------
# 会話への書き足し（canvas_messages が唯一の正）
# --------------------------------------------------------------------------

async def _publish(
    project_id: str, message: CanvasMessage | None = None, *, running: bool = True
) -> None:
    await ws.publish_canvas(
        project_id,
        running=running,
        activity=_activity.get(project_id),
        message=message,
    )


async def append(
    project_id: str,
    role: str,
    content: str,
    *,
    kind: str | None = None,
    data: dict[str, Any] | None = None,
    running: bool = True,
) -> CanvasMessage:
    """発言を 1 件残して WS に流す（ルーターのユーザー発言もここを通る）。"""
    message = await canvas.append_message(
        project_id, role, content, kind=kind, data=data
    )
    await _publish(project_id, message, running=running)
    return message


async def _event(
    project_id: str, kind: str, content: str, data: dict[str, Any] | None = None
) -> CanvasMessage:
    """ツールの結果やループの区切りを会話に残す。

    ``data`` はキーワード展開ではなく dict のまま受ける: ツールが返す data には
    ``project_id`` が入っていて、引数名とぶつかるため。
    """
    return await append(project_id, "event", content, kind=kind, data=data)


async def _set_activity(project_id: str, activity: str | None) -> None:
    if activity:
        _activity[project_id] = activity
    else:
        _activity.pop(project_id, None)
    if is_running(project_id):
        await _publish(project_id)


# --------------------------------------------------------------------------
# 1 ターン
# --------------------------------------------------------------------------

def _history(messages: list[CanvasMessage], system: str) -> list[AgentMessage]:
    """キャンバスの会話を、エージェントの transcript の形に均す。

    役割（user / assistant / event）は :class:`AgentMessage` とそのまま対応する
    ので、:func:`app.prompts.build_agent_conversation` を共通で使える。
    """
    return [
        AgentMessage(role="system", content=system, ts=""),
        *[
            AgentMessage(
                role=message.role,
                content=message.content,
                ts=message.ts,
                kind=message.kind,
                data=message.data,
            )
            for message in messages
        ],
    ]


def open_tab(project_id: str) -> str | None:
    """いま開いているタブ（``None`` = 作品共通）。"""
    return _tabs.get(project_id)


async def _system_prompt(project_id: str) -> str:
    """作品の現況と**開いているタブの盤面**を焼き込んだシステムプロンプト。

    盤面はタブ 1 枚ぶんに絞る（「この話のカットを〜」がそのまま通るように）。
    他のタブは件数の要約だけ渡し、必要なら `canvas_list_cards` で読ませる。
    """
    detail = await studio.project_detail(project_id)
    if detail is None:
        raise LookupError(project_id)
    tab = open_tab(project_id)
    cards = await canvas.list_tab_cards(project_id, tab)
    return prompts.build_canvas_system_prompt(
        project=agent_runner._studio_detail_text(detail),
        board=agent_runner.canvas_board_text(cards, detail, tab=tab),
        tabs=agent_runner.canvas_tabs_text(detail, tab),
        tab_id=tab or canvas.COMMON_TAB,
        tab_label=agent_runner.canvas_tab_label(tab, detail),
        workdir=workdir(project_id),
        tools_enabled=bool(load_settings().agent_grok_args),
    )


async def run_turn(project_id: str) -> tuple[str, AgentAction | None]:
    """Grok に 1 回尋ね、答えを会話に残してアクションを解釈する。

    解釈できないアクションはフォーマットの注意つきで 1 回だけ聞き直す
    （AGENT-MODE §3.1。スタジオの :func:`app.agent_runner.run_turn` と同じ）。
    """
    system = await _system_prompt(project_id)
    messages = await canvas.list_messages(project_id)

    async def on_activity(activity: str | None) -> None:
        await _set_activity(project_id, activity)

    client = grok.get_agent_client(workdir(project_id), on_activity)
    history = _history(messages, system)
    answer = await client.complete(prompts.build_agent_conversation(history))
    action: AgentAction | None = None
    reason = ""
    try:
        action = agent_protocol.parse_action(answer)
    except ActionError as exc:
        reason = str(exc)
    if action is None and (reason or agent_protocol.looks_like_action_attempt(answer)):
        retry = [*history, AgentMessage(role="assistant", content=answer, ts="")]
        answer = await client.complete(
            prompts.build_agent_conversation(
                retry, retry_reason=reason or "JSON を解釈できませんでした"
            )
        )
        try:
            action = agent_protocol.parse_action(answer)
            reason = ""
        except ActionError as exc:
            reason = str(exc)

    await append(project_id, "assistant", answer)
    if reason:
        await _event(
            project_id,
            "action_invalid",
            f"アクションを解釈できませんでした: {reason}",
            {"error": reason},
        )
    return answer, action


# --------------------------------------------------------------------------
# 実行ループ
# --------------------------------------------------------------------------

async def _apply(project_id: str, action: AgentAction) -> bool:
    """アクションを 1 つ実行する。``True`` を返したらそこで終わり。"""
    if action.action == "done":
        summary = action.summary or action.notes or "作業を完了しました。"
        await _event(project_id, "done", summary)
        return True
    if action.action not in agent_runner.TOOL_HANDLERS:
        # プランや生成ジョブはキャンバスの担当ではない（スタジオのエージェント
        # モードでやること）。次のターンで別の手を選べるよう、理由だけ残す。
        await _event(
            project_id,
            "action_unavailable",
            f"`{action.action}` はキャンバスからは実行できません。"
            "使えるのは studio_* / canvas_* / done です"
            "（生成の計画はエージェントモードで行ってください）。",
            {"action": action.action},
        )
        return False
    _place_on_open_tab(project_id, action)
    kind, text, data = await agent_runner.run_tool(action)
    await _event(project_id, kind, text, data)
    return False


#: ``canvas_place_card`` で「どのタブに置くか」を書ける kind。text / model は
#: カード自身が覚え、scene はその話の中に場ができる（カットの所属は場で決まり、
#: 素材は話に属さないので、どちらもここには入らない）。
_TAB_AWARE_KINDS = (*canvas.STANDALONE_KINDS, "scene")


def _place_on_open_tab(project_id: str, action: AgentAction) -> None:
    """新しいカードは、指定が無ければ**開いているタブ**に置く。

    エージェントに毎回 ``episode_id`` を書かせるより、開いている盤面に載る方が
    「この話に〜」という指示と一致する。
    """
    tab = open_tab(project_id)
    if tab is None or action.action != "canvas_place_card":
        return
    body = action.canvas.get("body") or {}
    if body.get("kind") in _TAB_AWARE_KINDS and body.get("episode_id") is None:
        body["episode_id"] = tab


async def _loop(project_id: str) -> None:
    try:
        for _ in range(MAX_TURNS):
            if project_id in _stop_requests:
                await _event(project_id, "stopped", "実行を止めました。")
                return
            try:
                _, action = await run_turn(project_id)
            except grok.LLMError as exc:
                await _event(
                    project_id, "error", f"Grok を呼べませんでした: {exc}",
                    {"error": str(exc)},
                )
                return
            if action is None or await _apply(project_id, action):
                return
        await _event(
            project_id,
            "turn_limit",
            f"連続 {MAX_TURNS} ターンで区切りました。続けるなら声をかけてください。",
        )
    except canvas.CanvasError:
        # プロジェクトごと消えた: 書き足す先が無いので黙って終える
        log.info("canvas %s disappeared while the agent was running", project_id)
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - 実行の失敗で API を落とさない
        log.exception("canvas agent failed (project %s)", project_id)


async def _run(project_id: str) -> None:
    try:
        await _loop(project_id)
    finally:
        _stop_requests.discard(project_id)
        _activity.pop(project_id, None)
        _tabs.pop(project_id, None)
        _runs.pop(project_id, None)
        await _publish(project_id, running=False)


async def start(project_id: str, episode_id: str | None = None) -> None:
    """バックグラウンドで実行ループを始める（既に走っていれば何もしない）。

    ``episode_id`` は開いているタブ（``None`` = 作品共通）。この実行のあいだ
    だけ覚えておき、盤面の見せ方と新しいカードの置き場所に使う。
    """
    if is_running(project_id):
        return
    _stop_requests.discard(project_id)
    if episode_id:
        _tabs[project_id] = episode_id
    else:
        _tabs.pop(project_id, None)
    _runs[project_id] = asyncio.create_task(_run(project_id))
    await _publish(project_id)


def request_stop(project_id: str) -> None:
    """次のターンの手前で止める（走っていなければ何もしない）。"""
    if is_running(project_id):
        _stop_requests.add(project_id)


async def stop_all() -> None:
    """走っているループを全部畳む（FastAPI の lifespan から）。"""
    tasks = list(_runs.values())
    for task in tasks:
        task.cancel()
    for task in tasks:
        try:
            await task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
    _runs.clear()
    _stop_requests.clear()
    _activity.clear()
    _tabs.clear()
