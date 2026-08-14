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

from . import (
    agent_protocol,
    agent_runner,
    canvas,
    grok,
    grok_session,
    prompts,
    push,
    studio,
    ws,
)
from .agent_protocol import ActionError
from .agent_store import attachment_path, attachments_dir, session_dir
from .config import load_settings
from .models import AgentAction, AgentMessage, CanvasChatSession, CanvasMessage

log = logging.getLogger(__name__)

#: 1 回の発言から回す Grok ターンの上限（暴走防止）の既定。スタジオのループより
#: 短いのは、キャンバスの操作が目録の読み書きだけで、待つべき生成が挟まらないため。
#: 実際に使う値は設定 ``canvas_max_turns``（0 = 無制限）。
MAX_TURNS = 8


def turn_limit() -> int:
    """いま効いている連続ターン上限（設定 ``canvas_max_turns``）。0 = 無制限。"""
    return load_settings().canvas_max_turns

_runs: dict[str, asyncio.Task[None]] = {}
_stop_requests: set[str] = set()
_activity: dict[str, str] = {}
#: 実行ごとの「いま開いているタブ」（話の id。作品共通なら入らない）
_tabs: dict[str, str] = {}
#: 実行中のキャンバスセッション（project_id → session_id）
_active_session: dict[str, str] = {}
_hosts: dict[str, grok_session.GrokSessionHost] = {}


def is_running(project_id: str) -> bool:
    """そのキャンバスでエージェントが走っているか（インメモリ）。"""
    task = _runs.get(project_id)
    return task is not None and not task.done()


def current_activity(project_id: str) -> str | None:
    """実行中の活動テキスト（インメモリ。DB には保存しない）。"""
    return _activity.get(project_id)


def _legacy_session_key(project_id: str) -> str:
    """移行したセッションの作業ディレクトリ名（添付が残る）。"""
    return f"canvas-{project_id}"


def workdir_for(session: CanvasChatSession) -> str:
    """この会話セッションの作業ディレクトリ。"""
    if session.grok_cwd:
        return session.grok_cwd
    return str(session_dir(f"canvas-sess-{session.id}"))


def workdir(project_id: str, session: CanvasChatSession | None = None) -> str:
    """このキャンバスの作業ディレクトリ。"""
    if session is not None:
        return workdir_for(session)
    return str(session_dir(_legacy_session_key(project_id)))


def attachment_dir(project_id: str, session: CanvasChatSession | None = None) -> Path:
    """チャットに添付されたファイルの置き場（``<workdir>/attachments/``）。"""
    if session is not None:
        path = Path(workdir_for(session)) / "attachments"
        path.mkdir(parents=True, exist_ok=True)
        return path
    return attachments_dir(_legacy_session_key(project_id))


def resolve_attachment(
    project_id: str, rel: str, session: CanvasChatSession | None = None
) -> Path | None:
    """``attachments/<file>`` の実在ファイルだけを解決する（ほかは ``None``）。"""
    if session is not None:
        root = Path(workdir_for(session))
        raw = (rel or "").strip()
        if not raw.startswith("attachments/"):
            return None
        candidate = (root / raw).resolve()
        attach = (root / "attachments").resolve()
        if candidate.parent != attach or not candidate.is_file():
            return None
        return candidate
    return attachment_path(_legacy_session_key(project_id), rel)


# --------------------------------------------------------------------------
# 会話への書き足し（canvas_messages が唯一の正）
# --------------------------------------------------------------------------

async def _publish(
    project_id: str,
    message: CanvasMessage | None = None,
    *,
    running: bool = True,
    session_id: str | None = None,
) -> None:
    await ws.publish_canvas(
        project_id,
        running=running,
        activity=_activity.get(project_id),
        message=message,
        session_id=session_id or _active_session.get(project_id),
    )


async def append(
    project_id: str,
    role: str,
    content: str,
    *,
    session_id: str | None = None,
    kind: str | None = None,
    data: dict[str, Any] | None = None,
    running: bool = True,
) -> CanvasMessage:
    """発言を 1 件残して WS に流す（ルーターのユーザー発言もここを通る）。"""
    sid = session_id or _active_session.get(project_id)
    message = await canvas.append_message(
        project_id, role, content, session_id=sid, kind=kind, data=data
    )
    await _publish(project_id, message, running=running, session_id=message.session_id)
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

def _as_agent_messages(messages: list[CanvasMessage]) -> list[AgentMessage]:
    return [
        AgentMessage(
            role=message.role,
            content=message.content,
            ts=message.ts,
            kind=message.kind,
            data=message.data,
        )
        for message in messages
    ]


def open_tab(project_id: str) -> str | None:
    """いま開いているタブ（``None`` = 作品共通）。"""
    return _tabs.get(project_id)


def _tab_info(project_id: str, detail) -> tuple[str, str]:
    tab = open_tab(project_id)
    return (
        agent_runner.canvas_tab_label(tab, detail),
        tab or canvas.COMMON_TAB,
    )


def snapshot_key_of(seq: int, tab: str | None) -> str:
    return f"{seq}:{tab or canvas.COMMON_TAB}"


async def _contract_text(project_id: str, session: CanvasChatSession) -> str:
    return prompts.build_canvas_contract(
        project_id=project_id,
        workdir=workdir_for(session),
        tools_enabled=bool(load_settings().agent_grok_args),
    )


async def _snapshot_text(project_id: str) -> str:
    detail = await studio.project_detail(project_id)
    if detail is None:
        raise LookupError(project_id)
    tab = open_tab(project_id)
    cards = await canvas.list_tab_cards(project_id, tab)
    label, tab_id = _tab_info(project_id, detail)
    return prompts.build_canvas_snapshot(
        project=agent_runner._studio_detail_text(detail),
        board=agent_runner.canvas_board_text(cards, detail, tab=tab),
        tabs=agent_runner.canvas_tabs_text(detail, tab),
        tab_id=tab_id,
        tab_label=label,
    )


async def _current_snapshot_key(project_id: str) -> str:
    seq = await studio.current_revision_seq(project_id)
    return snapshot_key_of(seq, open_tab(project_id))


def _canvas_prompt(
    session: CanvasChatSession,
    host: grok_session.GrokSessionHost,
    messages: list[CanvasMessage],
    *,
    contract: str,
    snapshot: str | None,
    tab_label: str,
    tab_id: str,
) -> str:
    past, current = agent_runner._split_transcript(_as_agent_messages(messages))
    if host.primed:
        return prompts.build_turn_batch(current, open_tab=(tab_label, tab_id))
    parts: list[str] = []
    cold = host.rebuild or not host.resumed
    if cold and (not host.use_acp) and not host.session_id:
        if contract.strip():
            parts.append(contract.strip())
    if cold:
        replay = prompts.build_replay(past)
        if replay.strip():
            parts.append(replay.strip())
    if snapshot:
        parts.append(snapshot.strip())
    batch = prompts.build_turn_batch(current, open_tab=(tab_label, tab_id))
    if batch.strip():
        parts.append(batch.strip())
    return "\n\n".join(parts) + "\n"


async def run_turn(
    project_id: str, session: CanvasChatSession
) -> tuple[str, AgentAction | None]:
    """Grok に 1 回尋ね、答えを会話に残してアクションを解釈する。"""
    messages = await canvas.list_messages(project_id, session.id)
    detail = await studio.project_detail(project_id)
    if detail is None:
        raise LookupError(project_id)
    tab_label, tab_id = _tab_info(project_id, detail)
    cwd = workdir_for(session)

    async def on_activity(activity: str | None) -> None:
        await _set_activity(project_id, activity)

    host = _hosts.get(project_id)
    if host is None or host._closed:
        host = grok_session.open_host(cwd, on_activity)
        contract = await _contract_text(project_id, session)
        try:
            grok_id = await host.start(contract, session.grok_session_id or None, cwd)
            host.resumed = bool(session.grok_session_id)
            host.rebuild = False
        except grok_session.GrokSessionGone:
            await host.close()
            host = grok_session.open_host(cwd, on_activity)
            grok_id = await host.start(contract, None, cwd)
            host.resumed = False
            host.rebuild = True
        host.primed = False
        _hosts[project_id] = host
        session.grok_session_id = grok_id or ""
        session.grok_cwd = cwd
        await canvas.update_session_grok(
            session.id, grok_session_id=grok_id or "", grok_cwd=cwd
        )
    else:
        contract = await _contract_text(project_id, session)

    current_key = await _current_snapshot_key(project_id)
    # 同じループの EVENT には現況を付けない。必要なのは Grok セッションの初通、
    # 組み直し、タブ変更、作品 revision の上昇。
    need_snapshot = (not host.primed) and (
        host.rebuild
        or not host.resumed
        or session.snapshot_key != current_key
    )
    snapshot = await _snapshot_text(project_id) if need_snapshot else None

    text = _canvas_prompt(
        session,
        host,
        messages,
        contract=contract,
        snapshot=snapshot,
        tab_label=tab_label,
        tab_id=tab_id,
    )
    try:
        answer = await host.prompt(text)
    except grok_session.GrokSessionGone:
        await host.close()
        host = grok_session.open_host(cwd, on_activity)
        grok_id = await host.start(contract, None, cwd)
        host.resumed = False
        host.rebuild = True
        host.primed = False
        _hosts[project_id] = host
        snapshot = await _snapshot_text(project_id)
        text = _canvas_prompt(
            session,
            host,
            messages,
            contract=contract,
            snapshot=snapshot,
            tab_label=tab_label,
            tab_id=tab_id,
        )
        answer = await host.prompt(text)
        session.grok_session_id = grok_id or ""
        await canvas.update_session_grok(session.id, grok_session_id=grok_id or "")
    host.primed = True
    if need_snapshot:
        session.snapshot_key = current_key
        await canvas.update_session_grok(session.id, snapshot_key=current_key)
    if host.session_id != (session.grok_session_id or ""):
        session.grok_session_id = host.session_id or ""
        await canvas.update_session_grok(
            session.id, grok_session_id=host.session_id or ""
        )

    action: AgentAction | None = None
    reason = ""
    try:
        action = agent_protocol.parse_action(answer)
    except ActionError as exc:
        reason = str(exc)
    if action is None and (reason or agent_protocol.looks_like_action_attempt(answer)):
        answer = await host.prompt(
            prompts.build_retry_turn(reason or "JSON を解釈できませんでした")
        )
        try:
            action = agent_protocol.parse_action(answer)
            reason = ""
        except ActionError as exc:
            reason = str(exc)

    await append(project_id, "assistant", answer, session_id=session.id)
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
    if action.action == "canvas_search_sessions":
        action.canvas.setdefault("project_id", project_id)
        mine = _active_session.get(project_id)
        if mine and not action.canvas.get("session_id"):
            action.canvas["session_id"] = mine
    elif action.action == "canvas_read_session":
        action.canvas["project_id"] = project_id
        mine = _active_session.get(project_id)
        if mine:
            action.canvas["exclude_id"] = mine
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


async def _loop(project_id: str, session_id: str) -> str | None:
    """キャンバスループ。終端の種別を返す（``None`` = 通知しない）。"""
    max_turns = turn_limit()
    turns = 0
    session = await canvas.get_session(project_id, session_id)
    if session is None:
        return None
    try:
        while max_turns == 0 or turns < max_turns:  # 0 = 無制限
            turns += 1
            if project_id in _stop_requests:
                await _event(project_id, "stopped", "実行を止めました。")
                return "stopped"
            try:
                _, action = await run_turn(project_id, session)
            except grok_session.GrokTurnCancelled:
                await _event(project_id, "stopped", "実行を止めました。")
                return "stopped"
            except grok.LLMError as exc:
                await _event(
                    project_id, "error", f"Grok を呼べませんでした: {exc}",
                    {"error": str(exc)},
                )
                return "failed"
            if project_id in _stop_requests:
                await _event(project_id, "stopped", "実行を止めました。")
                return "stopped"
            if action is None or await _apply(project_id, action):
                return "done"
        await _event(
            project_id,
            "turn_limit",
            f"連続 {max_turns} ターンで区切りました。続けるなら声をかけてください。",
        )
        return "done"
    except canvas.CanvasError:
        # プロジェクトごと消えた: 書き足す先が無いので黙って終える
        log.info("canvas %s disappeared while the agent was running", project_id)
        return None
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - 実行の失敗で API を落とさない
        log.exception("canvas agent failed (project %s)", project_id)
        return "failed"


async def _notify_canvas(outcome: str) -> None:
    if outcome == "done":
        title = "スタジオの作業が終わりました"
    else:
        title = "スタジオの作業が止まりました"
    await push.notify_all(title, title, url="/", tag="canvas")


async def _run(project_id: str, session_id: str) -> None:
    outcome: str | None = None
    try:
        outcome = await _loop(project_id, session_id)
    except asyncio.CancelledError:
        # lifespan の stop_all / プロセス終了では出さない
        outcome = None
        raise
    finally:
        _stop_requests.discard(project_id)
        _activity.pop(project_id, None)
        _tabs.pop(project_id, None)
        _active_session.pop(project_id, None)
        host = _hosts.pop(project_id, None)
        if host is not None:
            grok_id = host.session_id or ""
            await host.close()
            if grok_id:
                await canvas.update_session_grok(session_id, grok_session_id=grok_id)
        _runs.pop(project_id, None)
        await _publish(project_id, running=False, session_id=session_id)
        if outcome is not None:
            await _notify_canvas(outcome)


async def start(
    project_id: str,
    episode_id: str | None = None,
    session_id: str | None = None,
) -> None:
    """バックグラウンドで実行ループを始める（既に走っていれば何もしない）。

    ``episode_id`` は開いているタブ（``None`` = 作品共通）。この実行のあいだ
    だけ覚えておき、盤面の見せ方と新しいカードの置き場所に使う。
    """
    if is_running(project_id):
        return
    session = await canvas.ensure_session(project_id, session_id)
    _stop_requests.discard(project_id)
    if episode_id:
        _tabs[project_id] = episode_id
    else:
        _tabs.pop(project_id, None)
    _active_session[project_id] = session.id
    _runs[project_id] = asyncio.create_task(_run(project_id, session.id))
    await _publish(project_id, session_id=session.id)


def request_stop(project_id: str) -> None:
    """⏹: 実行中の Grok ターンを中断する（走っていなければ何もしない）。"""
    if not is_running(project_id):
        return
    _stop_requests.add(project_id)
    host = _hosts.get(project_id)
    if host is None:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(host.cancel())


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
    _active_session.clear()
    for host in _hosts.values():
        try:
            await host.close()
        except Exception:  # noqa: BLE001
            pass
    _hosts.clear()
