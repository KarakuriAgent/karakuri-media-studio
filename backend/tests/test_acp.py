"""ACP クライアント (`app.acp`) のテスト。

本物の grok CLI の代わりに、同じ JSON-RPC を喋る偽の stdio エージェント
（テンポラリの Python スクリプト）を subprocess で立てて検証する。
"""

from __future__ import annotations

import asyncio
import stat
import sys

import pytest

from app import acp
from app.acp import AcpAgentClient
from app.grok import LLMClient, LLMError
from app.grok_session import GrokSessionGone, GrokSessionHost, GrokTurnCancelled
from app.models import HealthStatus, Settings

# --------------------------------------------------------------------------
# 偽 stdio エージェント
# --------------------------------------------------------------------------

#: 通常の 1 ターン: thought → tool_call(+update) → message chunk 2 回 → 完了。
NORMAL_AGENT = r'''
import json, sys

def send(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()

def notify(update):
    send({"jsonrpc": "2.0", "method": "session/update",
          "params": {"sessionId": "s1", "update": update}})

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    msg = json.loads(line)
    method = msg.get("method")
    if method == "initialize":
        send({"jsonrpc": "2.0", "id": msg["id"], "result": {"protocolVersion": 1}})
    elif method == "session/new":
        assert msg["params"]["mcpServers"] == []
        send({"jsonrpc": "2.0", "id": msg["id"],
              "result": {"sessionId": "s1", "cwd": msg["params"]["cwd"]}})
    elif method == "session/prompt":
        send({"jsonrpc": "2.0", "method": "_x.ai/telemetry", "params": {}})
        notify({"sessionUpdate": "agent_thought_chunk", "content": {"type": "text", "text": "うーん"}})
        notify({"sessionUpdate": "agent_thought_chunk", "content": {"type": "text", "text": "…"}})
        notify({"sessionUpdate": "tool_call", "toolCallId": "t1",
                "title": "run_terminal_command", "status": "pending"})
        notify({"sessionUpdate": "tool_call_update", "toolCallId": "t1", "status": "completed"})
        notify({"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "こんに"}})
        notify({"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "ちは\n"}})
        send({"jsonrpc": "2.0", "id": msg["id"], "result": {"stopReason": "end_turn"},
              "prompt": msg["params"]["prompt"]})
        break
'''

#: ツール実行の前に許可を求めてくるエージェント。
PERMISSION_AGENT = r'''
import json, sys

def send(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()

chosen = None
prompt_id = None
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    msg = json.loads(line)
    method = msg.get("method")
    if method == "initialize":
        send({"jsonrpc": "2.0", "id": msg["id"], "result": {"protocolVersion": 1}})
    elif method == "session/new":
        send({"jsonrpc": "2.0", "id": msg["id"], "result": {"sessionId": "s1"}})
    elif method == "session/prompt":
        prompt_id = msg["id"]
        send({"jsonrpc": "2.0", "id": 9001, "method": "session/request_permission",
              "params": {"sessionId": "s1",
                         "toolCall": {"toolCallId": "t1", "title": "ls"},
                         "options": [
                             {"optionId": "reject-once", "kind": "reject_once", "name": "拒否"},
                             {"optionId": "allow-always", "kind": "allow_always", "name": "常に許可"},
                         ]}})
    elif method is None and msg.get("id") == 9001:
        chosen = msg["result"]["outcome"]["optionId"]
        send({"jsonrpc": "2.0", "method": "session/update",
              "params": {"sessionId": "s1", "update": {
                  "sessionUpdate": "agent_message_chunk",
                  "content": {"type": "text", "text": "選択=" + str(chosen)}}}})
        send({"jsonrpc": "2.0", "id": prompt_id, "result": {"stopReason": "end_turn"}})
        break
'''

#: initialize にエラーを返す（フォールバック条件）。
BROKEN_AGENT = r'''
import json, sys
for line in sys.stdin:
    msg = json.loads(line)
    sys.stdout.write(json.dumps(
        {"jsonrpc": "2.0", "id": msg["id"],
         "error": {"code": -32600, "message": "unsupported"}}) + "\n")
    sys.stdout.flush()
    break
'''

#: 1 行が StreamReader の既定 limit（64KiB）を大きく超える通知を送るエージェント。
HUGE_UPDATE_AGENT = r'''
import json, sys

BIG = "あ" * 200000  # UTF-8 で約 600KB、JSON 1 行に収まる

def send(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    msg = json.loads(line)
    method = msg.get("method")
    if method == "initialize":
        send({"jsonrpc": "2.0", "id": msg["id"], "result": {"protocolVersion": 1}})
    elif method == "session/new":
        send({"jsonrpc": "2.0", "id": msg["id"], "result": {"sessionId": "s1"}})
    elif method == "session/prompt":
        send({"jsonrpc": "2.0", "method": "session/update",
              "params": {"sessionId": "s1", "update": {
                  "sessionUpdate": "agent_thought_chunk",
                  "content": {"type": "text", "text": BIG}}}})
        send({"jsonrpc": "2.0", "method": "session/update",
              "params": {"sessionId": "s1", "update": {
                  "sessionUpdate": "agent_message_chunk",
                  "content": {"type": "text", "text": BIG}}}})
        send({"jsonrpc": "2.0", "method": "session/update",
              "params": {"sessionId": "s1", "update": {
                  "sessionUpdate": "agent_message_chunk",
                  "content": {"type": "text", "text": "END"}}}})
        send({"jsonrpc": "2.0", "id": msg["id"], "result": {"stopReason": "end_turn"}})
        break
'''

#: session/load 成功 → prompt。_meta.rules を session/new で記録する。
LOAD_OK_AGENT = r'''
import json, sys

def send(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()

rules = ""
loaded = False
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    msg = json.loads(line)
    method = msg.get("method")
    if method == "initialize":
        send({"jsonrpc": "2.0", "id": msg["id"], "result": {"protocolVersion": 1}})
    elif method == "session/new":
        rules = ((msg.get("params") or {}).get("_meta") or {}).get("rules") or ""
        send({"jsonrpc": "2.0", "id": msg["id"],
              "result": {"sessionId": "s-new"}})
    elif method == "session/load":
        loaded = True
        send({"jsonrpc": "2.0", "id": msg["id"],
              "result": {"sessionId": msg["params"]["sessionId"]}})
    elif method == "session/prompt":
        text = "loaded" if loaded else ("rules=" + rules[:20])
        send({"jsonrpc": "2.0", "method": "session/update",
              "params": {"sessionId": "s1", "update": {
                  "sessionUpdate": "agent_message_chunk",
                  "content": {"type": "text", "text": text}}}})
        send({"jsonrpc": "2.0", "id": msg["id"], "result": {"stopReason": "end_turn"}})
'''

#: session/load がエラーを返す。
LOAD_FAIL_AGENT = r'''
import json, sys

def send(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    msg = json.loads(line)
    method = msg.get("method")
    if method == "initialize":
        send({"jsonrpc": "2.0", "id": msg["id"], "result": {"protocolVersion": 1}})
    elif method == "session/load":
        send({"jsonrpc": "2.0", "id": msg["id"],
              "error": {"code": -32000, "message": "session not found"}})
        break
    elif method == "session/new":
        send({"jsonrpc": "2.0", "id": msg["id"], "result": {"sessionId": "s1"}})
'''

#: 何も返さず居座る（タイムアウト検証用）。
HANGING_AGENT = r'''
import sys, time
sys.stdin.readline()
time.sleep(120)
'''

#: session/prompt を受けたら返さず、session/cancel が来たら cancelled で応答する。
CANCEL_AGENT = r'''
import json, sys

def send(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()

prompt_id = None
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    msg = json.loads(line)
    method = msg.get("method")
    if method == "initialize":
        send({"jsonrpc": "2.0", "id": msg["id"], "result": {"protocolVersion": 1}})
    elif method == "session/new":
        send({"jsonrpc": "2.0", "id": msg["id"], "result": {"sessionId": "s1"}})
    elif method == "session/prompt":
        prompt_id = msg["id"]
        send({"jsonrpc": "2.0", "method": "session/update",
              "params": {"sessionId": "s1", "update": {
                  "sessionUpdate": "agent_thought_chunk",
                  "content": {"type": "text", "text": "考え中"}}}})
    elif method == "session/cancel":
        assert prompt_id is not None
        assert "id" not in msg
        assert msg.get("params", {}).get("sessionId") == "s1"
        send({"jsonrpc": "2.0", "id": prompt_id, "result": {"stopReason": "cancelled"}})
        break
'''


def write_agent(tmp_path, source: str, name: str = "fake-grok") -> str:
    """実行可能な偽エージェントスクリプトを書き出してパスを返す。"""
    path = tmp_path / name
    path.write_text(f"#!{sys.executable}\n{source}", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IRUSR)
    return str(path)


@pytest.fixture(autouse=True)
def settings(monkeypatch, tmp_path):
    """config.json を読ませない（既定値だけで動かす）。"""
    values = Settings(grok_command="grok", grok_model="grok-4.5", grok_workdir=str(tmp_path))
    monkeypatch.setattr(acp, "load_settings", lambda: values)
    return values


def client(tmp_path, source: str, **kwargs) -> AcpAgentClient:
    return AcpAgentClient(
        command=write_agent(tmp_path, source),
        model="grok-4.5",
        workdir=tmp_path / "workdir",
        timeout=kwargs.pop("timeout", 20.0),
        **kwargs,
    )


# --------------------------------------------------------------------------


def test_argv_passes_the_model_to_grok_agent_stdio(tmp_path):
    agent = AcpAgentClient(command="grok", model="grok-4.5", workdir=tmp_path)
    assert agent.argv() == ["grok", "agent", "-m", "grok-4.5", "stdio"]
    plain = AcpAgentClient(command="grok", model="", workdir=tmp_path)
    assert plain.argv() == ["grok", "agent", "stdio"]


@pytest.mark.asyncio
async def test_complete_runs_a_full_turn_and_reports_activity(tmp_path):
    seen: list[str | None] = []
    agent = client(tmp_path, NORMAL_AGENT, on_activity=seen.append)

    answer = await agent.complete("こんにちは")

    assert answer == "こんにちは"
    # チャンク毎ではなく、状態が変わったときだけ通知される。
    assert seen == [
        acp.ACTIVITY_THINKING,
        acp.tool_activity("run_terminal_command"),
        acp.ACTIVITY_ANSWERING,
        None,  # ターン終了でクリア
    ]


@pytest.mark.asyncio
async def test_complete_awaits_async_activity_callbacks(tmp_path):
    seen: list[str | None] = []

    async def on_activity(activity: str | None) -> None:
        seen.append(activity)

    await client(tmp_path, NORMAL_AGENT, on_activity=on_activity).complete("やあ")
    assert acp.ACTIVITY_THINKING in seen and seen[-1] is None


@pytest.mark.asyncio
async def test_session_new_uses_the_workdir(tmp_path):
    """session/new の cwd はセッション作業ディレクトリ（無ければ作る）。"""
    agent = client(tmp_path, NORMAL_AGENT)
    await agent.complete("やあ")
    assert agent.workdir.is_dir()


@pytest.mark.asyncio
async def test_permission_requests_are_auto_approved(tmp_path):
    answer = await client(tmp_path, PERMISSION_AGENT).complete("ls して")
    # allow を含む kind の最初の選択肢が選ばれる（reject が先頭でも拾わない）。
    assert answer == "選択=allow-always"


@pytest.mark.asyncio
async def test_huge_single_line_update_is_read_without_error(tmp_path):
    """64KiB を大きく超える 1 行の通知でも欠けずに読める。

    ``StreamReader.readline()`` のままだと LimitOverrunError で落ちていた箇所。
    """
    seen: list[str | None] = []
    agent = client(tmp_path, HUGE_UPDATE_AGENT, on_activity=seen.append)

    answer = await agent.complete("大きいのを返して")

    assert answer == "あ" * 200000 + "END"
    assert seen == [acp.ACTIVITY_THINKING, acp.ACTIVITY_ANSWERING, None]


@pytest.mark.asyncio
async def test_line_reader_splits_across_chunks_and_keeps_the_last_line(tmp_path):
    """チャンク境界をまたぐ行を組み立て、EOF 時のバッファ残りも最終行として返す。"""
    stream = asyncio.StreamReader()
    payload = b"a" * 300000
    stream.feed_data(payload + b"\n" + b"tail")
    stream.feed_eof()

    reader = acp._LineReader(stream)
    assert await reader.readline() == payload + b"\n"
    assert await reader.readline() == b"tail"
    assert await reader.readline() == b""


@pytest.mark.asyncio
async def test_timeout_kills_the_process(tmp_path):
    agent = client(tmp_path, HANGING_AGENT, timeout=0.3)
    with pytest.raises(LLMError, match="タイムアウト"):
        await agent.complete("やあ")


@pytest.mark.asyncio
async def test_broken_initialize_falls_back_to_the_oneshot_client(tmp_path):
    calls: list[str] = []

    class Fallback(LLMClient):
        async def complete(self, prompt: str) -> str:
            calls.append(prompt)
            return "ワンショットの答え"

        async def health(self) -> HealthStatus:
            return HealthStatus(status="ok")

    seen: list[str | None] = []
    agent = client(tmp_path, BROKEN_AGENT, fallback=Fallback(), on_activity=seen.append)

    assert await agent.complete("やあ") == "ワンショットの答え"
    assert calls == ["やあ"]


@pytest.mark.asyncio
async def test_missing_command_falls_back(tmp_path):
    class Fallback(LLMClient):
        async def complete(self, prompt: str) -> str:
            return "ワンショット"

        async def health(self) -> HealthStatus:
            return HealthStatus(status="ok")

    agent = AcpAgentClient(
        command=str(tmp_path / "does-not-exist"),
        workdir=tmp_path,
        timeout=5.0,
        fallback=Fallback(),
    )
    assert await agent.complete("やあ") == "ワンショット"


@pytest.mark.asyncio
async def test_cancelling_the_turn_kills_the_process(tmp_path):
    agent = client(tmp_path, HANGING_AGENT, timeout=60.0)
    task = asyncio.create_task(agent.complete("やあ"))
    await asyncio.sleep(0.3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_session_load_succeeds(tmp_path, monkeypatch):
    command = write_agent(tmp_path, LOAD_OK_AGENT)
    monkeypatch.setattr(
        acp,
        "load_settings",
        lambda: Settings(
            grok_command=command, grok_model="", grok_workdir=str(tmp_path)
        ),
    )
    host = GrokSessionHost(tmp_path / "workdir", timeout=20.0, use_acp=True)
    host.command = command
    host.model = ""
    try:
        sid = await host.start("契約文", "s1")
        assert sid == "s1"
        assert await host.prompt("# USER\n続き") == "loaded"
    finally:
        await host.close()


@pytest.mark.asyncio
async def test_session_load_failure_is_gone(tmp_path, monkeypatch):
    command = write_agent(tmp_path, LOAD_FAIL_AGENT)
    host = GrokSessionHost(tmp_path / "workdir", timeout=20.0, use_acp=True)
    host.command = command
    host.model = ""
    with pytest.raises(GrokSessionGone):
        await host.start("契約文", "missing")
    await host.close()


@pytest.mark.asyncio
async def test_session_cancel_raises_turn_cancelled(tmp_path):
    """session/cancel で prompt が cancelled になり、エージェントは居座らない。"""
    command = write_agent(tmp_path, CANCEL_AGENT)
    host = GrokSessionHost(tmp_path / "workdir", timeout=20.0, use_acp=True)
    host.command = command
    host.model = ""
    try:
        sid = await host.start("契約文", None)
        assert sid == "s1"
        task = asyncio.create_task(host.prompt("# USER\nやあ"))
        await asyncio.sleep(0.2)
        await host.cancel()
        with pytest.raises(GrokTurnCancelled):
            await asyncio.wait_for(task, timeout=5.0)
    finally:
        await host.close()


@pytest.mark.asyncio
async def test_session_new_carries_rules(tmp_path, monkeypatch):
    command = write_agent(tmp_path, LOAD_OK_AGENT)
    host = GrokSessionHost(tmp_path / "workdir", timeout=20.0, use_acp=True)
    host.command = command
    host.model = ""
    try:
        sid = await host.start("THE_RULES_BLOCK", None)
        assert sid == "s-new"
        assert await host.prompt("# USER\nやあ") == "rules=THE_RULES_BLOCK"
    finally:
        await host.close()


async def _permission_choice(tmp_path, *, allow_tools: bool) -> str:
    """許可要求つきのターンを 1 回回して、ホストが選んだ選択肢を返す。"""
    command = write_agent(tmp_path, PERMISSION_AGENT)
    host = GrokSessionHost(
        tmp_path / "workdir", timeout=20.0, use_acp=True, allow_tools=allow_tools
    )
    host.command = command
    host.model = ""
    try:
        await host.start("契約文", None)
        return await host.prompt("# USER\nls して")
    finally:
        await host.close()


@pytest.mark.asyncio
async def test_a_host_approves_tool_permissions_by_default(tmp_path):
    assert await _permission_choice(tmp_path, allow_tools=True) == "選択=allow-always"


@pytest.mark.asyncio
async def test_a_tool_free_host_rejects_tool_permissions(tmp_path):
    """相談専用のチャット（``allow_tools=False``）はツールを使わせない。"""
    assert await _permission_choice(tmp_path, allow_tools=False) == "選択=reject-once"
