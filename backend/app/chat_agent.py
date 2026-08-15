"""生成タブの相談チャットを Grok の継続セッションで回す（SPEC §4.3）。

```
1 通目 ─→ host.start(契約, cwd) ─→ session/prompt(履歴 + 発言)
2 通目 ─→ 同じ host に         ─→ session/prompt(発言だけ)
```

**正本は DB の ``chat_sessions.messages``。** Grok 側のセッションは続き用の
キャッシュなので、消えていたら（:class:`~app.grok_session.GrokSessionGone`、
プロセス終了、アプリ再起動）履歴を組み直して新しいセッションを開き直す。
``session/load`` による再開はしない: 相談は短く、組み直しの方が確実。

契約（システムプロンプト）の渡し方は CLI ごと（:mod:`app.llm_cli`）: grok は
``session/new`` の ``_meta.rules``、claude / codex / cursor は作業ディレクトリの
``CLAUDE.md`` / ``AGENTS.md``。ワンショットにフォールバックしたときだけ初回
プロンプトに埋め込まれる（:func:`app.prompts.build_conversation` = 従来どおりの
全文送信）。

このチャットは**相談専用**なのでツールは使わせない: ``--permission-mode auto``
は付けず（``extra_args=[]``）、ACP の ``session/request_permission`` は拒否する
（``allow_tools=False``）。

進捗は WS（``type: "chat"``）で流す。取りこぼしても
``GET /api/chat/sessions/{id}`` を取り直せば会話はそこに残っている。
"""

from __future__ import annotations

import logging
from pathlib import Path

from . import grok, grok_session, prompts, ws
from .agent_store import session_dir
from .models import ChatMessage, ChatState

log = logging.getLogger(__name__)

#: セッション id → 使い回している Grok ホスト
_hosts: dict[str, grok_session.GrokSessionHost] = {}
#: 実行中の活動テキスト（インメモリ。DB には保存しない）
_activity: dict[str, str] = {}
#: いま Grok のターンが走っているセッション
_running: set[str] = set()


def workdir(session_id: str, stored: str = "") -> str:
    """このチャットの作業ディレクトリ（``runtime/agent-sessions/chat-<id>/``）。

    エージェント / キャンバスと同じ置き場を借りる（セッション行は作らない）。
    入力画像のコピー先でもあるので、grok の cwd と必ず同じ場所にする。
    """
    if stored:
        return stored
    return str(session_dir(f"chat-{session_id}"))


def workdir_path(session_id: str, stored: str = "") -> Path:
    return Path(workdir(session_id, stored))


def is_running(session_id: str) -> bool:
    return session_id in _running


def current_activity(session_id: str) -> str | None:
    return _activity.get(session_id)


def state(session_id: str) -> ChatState:
    return ChatState(
        session_id=session_id,
        running=is_running(session_id),
        activity=current_activity(session_id),
    )


async def _publish(session_id: str) -> None:
    await ws.publish_chat(
        session_id,
        running=is_running(session_id),
        activity=current_activity(session_id),
    )


async def _set_activity(session_id: str, activity: str | None) -> None:
    if activity:
        _activity[session_id] = activity
    else:
        _activity.pop(session_id, None)
    if is_running(session_id):
        await _publish(session_id)


# --------------------------------------------------------------------------
# ホスト（1 チャット = 1 ホスト）
# --------------------------------------------------------------------------

async def bind_host(
    session_id: str,
    *,
    contract: str,
    cwd: str,
    saved_session_id: str = "",
    force_new: bool = False,
) -> grok_session.GrokSessionHost:
    """使い回せるホストがあれば返し、無ければ start する。"""
    existing = _hosts.get(session_id)
    if existing is not None and not existing._closed and not force_new:
        return existing
    if existing is not None:
        await existing.close()
        _hosts.pop(session_id, None)

    async def on_activity(activity: str | None) -> None:
        await _set_activity(session_id, activity)

    saved = "" if force_new else (saved_session_id or "")

    def open_new() -> grok_session.GrokSessionHost:
        # 相談はツールを使わない: 追加フラグ無し・許可要求は拒否。
        return grok_session.open_host(
            cwd, on_activity, extra_args=[], allow_tools=False
        )

    host = open_new()
    try:
        await host.start(contract, saved or None, cwd)
        host.resumed = bool(saved)
        host.rebuild = False
    except grok_session.GrokSessionGone:
        await host.close()
        host = open_new()
        await host.start(contract, None, cwd)
        host.resumed = False
        host.rebuild = True
    host.primed = False
    _hosts[session_id] = host
    return host


async def release_host(session_id: str) -> None:
    """このチャットのホストを閉じる（セッション削除・停止のあと片付け）。"""
    host = _hosts.pop(session_id, None)
    _activity.pop(session_id, None)
    if host is not None:
        await host.close()


# --------------------------------------------------------------------------
# プロンプトの組み立て
# --------------------------------------------------------------------------

def _continuing(host: grok_session.GrokSessionHost) -> bool:
    """このホストが「続き」を持てるか（ACP、または resume できる id がある）。"""
    return bool(host.use_acp or host.session_id)


def _split(messages: list[ChatMessage]) -> tuple[list[ChatMessage], list[ChatMessage]]:
    """最後の assistant より前（system 除く）と、それ以降。"""
    cut = 0
    for index, message in enumerate(messages):
        if message.role == "assistant":
            cut = index + 1
    past = [m for m in messages[:cut] if m.role != "system"]
    current = [m for m in messages[cut:] if m.role != "system"]
    return past, current


def build_prompt(
    host: grok_session.GrokSessionHost,
    messages: list[ChatMessage],
    contract: str = "",
) -> str:
    """このターンで送る本文（``messages`` は system 込みの全履歴）。

    ``contract`` は、契約をプロンプトにも埋める CLI（claude）のための保険。
    渡さなければ ``messages`` の system 発言を使う。
    """
    if host.primed and _continuing(host):
        # 続きの発言だけ: 契約も履歴も CLI 側のセッションが覚えている。
        _, current = _split(messages)
        return "\n".join(
            prompts.build_chat_turn(m.content) for m in current if m.role == "user"
        )
    if not _continuing(host):
        # ワンショット（ACP 不可 / 設定オフ）: 従来どおり全文を組み直す。
        return prompts.build_conversation(messages)
    past, current = _split(messages)
    parts: list[str] = []
    if host.wants_contract():
        text = (contract or _system_of(messages)).strip()
        if text:
            parts.append(text)
    if past:
        parts.append(prompts.build_chat_replay(past).strip())
    parts += [
        prompts.build_chat_turn(m.content).strip() for m in current if m.role == "user"
    ]
    return "\n\n".join(part for part in parts if part) + "\n"


def _system_of(messages: list[ChatMessage]) -> str:
    for message in messages:
        if message.role == "system":
            return message.content
    return ""


def build_retry_prompt(
    host: grok_session.GrokSessionHost, messages: list[ChatMessage]
) -> str:
    """JSON を読めなかったときの作り直し（1 回だけ）。"""
    if _continuing(host):
        return prompts.build_chat_retry_turn()
    return prompts.build_conversation(messages, retry=True)


# --------------------------------------------------------------------------
# 1 ターン
# --------------------------------------------------------------------------

class ChatBusy(Exception):
    """同じチャットで別のターンがまだ走っている。"""


async def run_turn(
    session_id: str,
    *,
    contract: str,
    messages: list[ChatMessage],
    cwd: str,
    saved_session_id: str = "",
) -> tuple[str, dict | None, str]:
    """Grok に 1 回尋ねる。``(答え, 解釈できた最終案, grok session id)``。

    :raises ChatBusy: 同じチャットのターンがまだ走っている
    :raises grok_session.GrokTurnCancelled: 停止された
    :raises grok.LLMError: Grok を呼べなかった
    """
    if session_id in _running:
        raise ChatBusy(session_id)
    _running.add(session_id)
    await _publish(session_id)
    try:
        return await _run_turn(
            session_id,
            contract=contract,
            messages=messages,
            cwd=cwd,
            saved_session_id=saved_session_id,
        )
    except grok_session.GrokTurnCancelled:
        # 止めたホストはもう使い回せない。次の発言は DB の履歴から組み直す。
        await release_host(session_id)
        raise
    finally:
        _running.discard(session_id)
        _activity.pop(session_id, None)
        await _publish(session_id)


async def _run_turn(
    session_id: str,
    *,
    contract: str,
    messages: list[ChatMessage],
    cwd: str,
    saved_session_id: str,
) -> tuple[str, dict | None, str]:
    host = await bind_host(
        session_id, contract=contract, cwd=cwd, saved_session_id=saved_session_id
    )
    try:
        answer = await host.prompt(build_prompt(host, messages, contract))
    except grok_session.GrokSessionGone:
        # Grok 側のセッションが消えた: 履歴を組み直して新しい会話で続ける。
        host = await bind_host(
            session_id, contract=contract, cwd=cwd, force_new=True
        )
        host.rebuild = True
        host.primed = False
        answer = await host.prompt(build_prompt(host, messages, contract))
    host.primed = True

    result = grok.extract_result(answer)
    if result is None and grok.has_json_fence(answer):
        # JSON を出そうとして失敗している: 形式を思い出させて 1 回だけやり直す。
        retry_messages = [
            *messages,
            ChatMessage(role="assistant", content=answer, ts=messages[-1].ts),
        ]
        answer = await host.prompt(build_retry_prompt(host, retry_messages))
        result = grok.extract_result(answer)
    return answer, result, host.session_id or ""


# --------------------------------------------------------------------------
# 停止
# --------------------------------------------------------------------------

async def request_stop(session_id: str) -> ChatState:
    """⏹: 走っている Grok のターンを止める（ワンショットはプロセスを kill）。

    止めたホストは ``_cancelled`` のまま使い回せないので閉じる。次の発言では
    DB の履歴から組み直した新しいセッションが開く。
    """
    host = _hosts.pop(session_id, None)
    if host is not None:
        try:
            await host.cancel()
        except Exception:  # noqa: BLE001 - 停止処理の失敗で API を落とさない
            log.debug("チャットのターンを止められませんでした", exc_info=True)
        try:
            await host.close()
        except Exception:  # noqa: BLE001
            log.debug("チャットのホストを閉じられませんでした", exc_info=True)
    return state(session_id)


async def stop_all() -> None:
    """開いているホストを全部畳む（FastAPI の lifespan から）。"""
    hosts = list(_hosts.values())
    _hosts.clear()
    _activity.clear()
    _running.clear()
    for host in hosts:
        try:
            await host.close()
        except Exception:  # noqa: BLE001
            pass


__all__ = [
    "ChatBusy",
    "bind_host",
    "build_prompt",
    "current_activity",
    "is_running",
    "release_host",
    "request_stop",
    "run_turn",
    "state",
    "stop_all",
    "workdir",
    "workdir_path",
]
