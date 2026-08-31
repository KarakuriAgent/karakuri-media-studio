"""CLI アダプタ（:mod:`app.llm_cli`）のテスト。

どの CLI を選んでも「起動の argv」「契約（rules）の渡し方」「認証エラーの
見分け」が変わるだけで、上のレイヤーは同じ形で回る（SPEC §4.1）。
"""

from __future__ import annotations

import sqlite3

import pytest

from app import config, db, grok, grok_session, llm_cli
from app.models import Settings


@pytest.fixture
def settings(monkeypatch):
    """既定（grok）の設定を 1 つ持たせる。テストごとに差し替える。"""
    values = Settings(grok_command="grok", grok_model="grok-4.5")
    monkeypatch.setattr(config, "_settings", values)
    return values


def use(monkeypatch, **overrides) -> Settings:
    fields = {"grok_command": "grok", "grok_model": "grok-4.5", **overrides}
    values = Settings(**fields)
    monkeypatch.setattr(config, "_settings", values)
    return values


# --------------------------------------------------------------------------
# argv の組み立て
# --------------------------------------------------------------------------

def test_grok_argv_is_unchanged():
    """既定の grok は、これまでと同じコマンドを組む。"""
    assert llm_cli.GROK.acp_argv("grok", "grok-4.5") == [
        "grok", "agent", "-m", "grok-4.5", "stdio",
    ]
    assert llm_cli.GROK.acp_argv("grok", "") == ["grok", "agent", "stdio"]
    assert llm_cli.GROK.oneshot_argv("やあ", "grok", "grok-4.5") == [
        "grok", "--model", "grok-4.5", "-p", "やあ",
    ]
    assert llm_cli.GROK.oneshot_argv(
        "やあ", "grok", "", extra=["--permission-mode", "auto"], output_json=True
    ) == [
        "grok", "--permission-mode", "auto", "--output-format", "json", "-p", "やあ",
    ]
    assert llm_cli.GROK.oneshot_argv("やあ", "grok", "", resume_id="s1") == [
        "grok", "-p", "やあ", "--resume", "s1",
    ]
    assert llm_cli.GROK.version_argv("grok") == ["grok", "--version"]


@pytest.mark.parametrize(
    "adapter, acp, oneshot",
    [
        (llm_cli.CLAUDE, ["claude-agent-acp"], ["claude", "-p", "やあ"]),
        (llm_cli.CODEX, ["codex-acp"], ["codex", "exec", "やあ"]),
        (llm_cli.CURSOR, ["cursor-agent", "acp"], ["cursor-agent", "-p", "やあ"]),
    ],
)
def test_other_clis_have_their_own_argv(adapter, acp, oneshot):
    assert adapter.acp_argv() == acp
    assert adapter.oneshot_argv("やあ") == oneshot
    # 続きも json 包装も持たない: 毎ターン履歴を組み直す経路に落ちる
    assert not adapter.supports_resume
    assert not adapter.supports_json_output
    assert adapter.oneshot_argv("やあ", resume_id="s1", output_json=True) == oneshot


def test_a_command_with_arguments_replaces_the_defaults():
    """設定に引数まで書いたら、そのまま使う（起動の仕方が変わっても追随できる）。"""
    assert llm_cli.CURSOR.acp_argv("cursor-agent acp") == ["cursor-agent", "acp"]
    assert llm_cli.GROK.acp_argv("npx grok agent stdio", "m") == [
        "npx", "grok", "agent", "stdio", "-m", "m",
    ]
    # 1 語だけなら既定の引数が付く
    assert llm_cli.CURSOR.acp_argv("cursor-agent") == ["cursor-agent", "acp"]
    assert llm_cli.CURSOR.acp_argv("agent") == ["agent", "acp"]
    # ACP はサブコマンド。``--acp`` フラグは今の Cursor CLI には無い
    assert "--acp" not in llm_cli.CURSOR.acp_argv()


def test_a_model_is_only_sent_when_the_cli_takes_one():
    # モデル未指定なら、どの CLI でもフラグごと出さない
    assert llm_cli.CODEX.oneshot_argv("やあ", "codex", "") == ["codex", "exec", "やあ"]
    assert llm_cli.CODEX.oneshot_argv("やあ", "codex", "gpt-5") == [
        "codex", "-m", "gpt-5", "exec", "やあ",
    ]


def test_oneshot_attempts_drop_the_extra_flags_before_the_model(monkeypatch):
    """追加フラグは CLI 固有。モデルを残したまま外す形を先に試す。

    ``--permission-mode`` は grok 専用なので cursor では ``unknown option``。
    ここを飛ばすと、通るのがモデル指定ごと落ちた argv だけになってしまう。
    """
    use(monkeypatch)
    client = grok.GrokCliClient(
        command="cursor-agent",
        model="composer-1",
        extra_args=["--permission-mode", "auto"],
        adapter=llm_cli.CURSOR,
    )
    assert client._attempts("やあ") == [
        ["cursor-agent", "--model", "composer-1",
         "--permission-mode", "auto", "-p", "やあ"],
        ["cursor-agent", "--model", "composer-1", "-p", "やあ"],
        ["cursor-agent", "--permission-mode", "auto", "-p", "やあ"],
        ["cursor-agent", "-p", "やあ"],
    ]


def test_oneshot_attempts_stay_short_without_a_model_or_extras(monkeypatch):
    use(monkeypatch)
    client = grok.GrokCliClient(
        command="cursor-agent", model="", extra_args=[], adapter=llm_cli.CURSOR
    )
    assert client._attempts("やあ") == [["cursor-agent", "-p", "やあ"]]


async def test_a_rejected_model_is_not_returned_as_an_answer(monkeypatch, tmp_path):
    """cursor-agent は知らないモデルでも exit 0。回答として返してはいけない。"""
    use(monkeypatch)
    calls: list[list[str]] = []

    async def fake_exec(argv, cwd, timeout):
        calls.append(list(argv))
        if "--model" in argv:
            return 0, "Cannot use this model: nope. Available models: auto\n", ""
        return 0, "こんにちは\n", ""

    monkeypatch.setattr(grok, "_exec", fake_exec)
    client = grok.GrokCliClient(
        command="cursor-agent",
        model="nope",
        workdir=tmp_path,
        extra_args=[],
        adapter=llm_cli.CURSOR,
    )
    assert await client.complete("やあ") == "こんにちは"
    assert calls == [
        ["cursor-agent", "--model", "nope", "-p", "やあ"],
        ["cursor-agent", "-p", "やあ"],
    ]


def test_only_cursor_screens_stdout_for_a_rejected_model():
    assert llm_cli.CURSOR.looks_like_model_error(
        "  Cannot use this model: nope. Available models: auto"
    )
    # 本文の途中に出てくるだけのものは巻き込まない
    assert not llm_cli.CURSOR.looks_like_model_error(
        "エラーの意味は `Cannot use this model` です"
    )
    for adapter in (llm_cli.GROK, llm_cli.CLAUDE, llm_cli.CODEX):
        assert not adapter.looks_like_model_error("Cannot use this model: nope")


# --------------------------------------------------------------------------
# モデル指定（括弧付き表記と ACP の configOption）
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "spec, expected",
    [
        ("grok-4.6[effort=xhigh,fast=false]",
         ("grok-4.6", [("effort", "xhigh"), ("fast", "false")])),
        (" grok-4.6 [ effort = xhigh , fast = false ] ",
         ("grok-4.6", [("effort", "xhigh"), ("fast", "false")])),
        ("grok-4.6", ("grok-4.6", [])),
        ("cursor-grok-4.6-xhigh", ("cursor-grok-4.6-xhigh", [])),
        ("grok-4.6[]", ("grok-4.6", [])),
        ("", ("", [])),
        # 壊れた表記は直さず、全体を名前として CLI に渡す
        ("grok-4.6[effort=xhigh", ("grok-4.6[effort=xhigh", [])),
        ("grok-4.6[effort]", ("grok-4.6[effort]", [])),
        ("[effort=xhigh]", ("[effort=xhigh]", [])),
    ],
)
def test_parse_model_spec_splits_the_parameters(spec, expected):
    assert llm_cli.parse_model_spec(spec) == expected


def test_cursor_sends_the_model_as_config_options():
    """cursor の ACP は --model が無いので model / effort / fast を個別に送る。"""
    assert llm_cli.CURSOR.acp_config_options("grok-4.6[effort=xhigh,fast=false]") == [
        ("model", "grok-4.6"),
        ("effort", "xhigh"),
        ("fast", "false"),
    ]
    # 素の id はそのまま model へ（余計な変換はしない）
    assert llm_cli.CURSOR.acp_config_options("cursor-grok-4.6-xhigh") == [
        ("model", "cursor-grok-4.6-xhigh"),
    ]
    # モデル未指定なら CLI の既定に任せる
    assert llm_cli.CURSOR.acp_config_options("") == []
    assert llm_cli.CURSOR.acp_config_options("  ") == []


def test_only_cursor_declares_the_parameterized_model_picker():
    assert llm_cli.CURSOR.acp_client_meta == {"parameterizedModelPicker": True}
    assert llm_cli.CURSOR.acp_model_via_config is True
    for adapter in (llm_cli.GROK, llm_cli.CLAUDE, llm_cli.CODEX):
        assert adapter.acp_client_meta == {}
        assert adapter.acp_config_options("grok-4.6[effort=xhigh]") == []


# --------------------------------------------------------------------------
# 契約（rules）の渡し方
# --------------------------------------------------------------------------

def test_grok_passes_rules_through_the_acp_meta(tmp_path):
    assert llm_cli.GROK.rules_mode == "meta"
    assert llm_cli.GROK.write_rules(tmp_path, "契約") is None
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(
    "adapter, filename",
    [
        (llm_cli.CLAUDE, "CLAUDE.md"),
        (llm_cli.CODEX, "AGENTS.md"),
        (llm_cli.CURSOR, "AGENTS.md"),
    ],
)
def test_file_rules_are_written_into_the_workdir(adapter, filename, tmp_path):
    path = adapter.write_rules(tmp_path, "  # ROLE\n契約の本文  ")
    assert path == tmp_path / filename
    assert path.read_text(encoding="utf-8") == "# ROLE\n契約の本文\n"
    # 開き直すたびに上書きしてよい
    adapter.write_rules(tmp_path, "新しい契約")
    assert path.read_text(encoding="utf-8") == "新しい契約\n"


def test_empty_rules_write_nothing(tmp_path):
    assert llm_cli.CLAUDE.write_rules(tmp_path, "   ") is None
    assert not list(tmp_path.iterdir())


def test_only_claude_doubles_the_contract_into_the_prompt():
    """CLAUDE.md を読むかは設定次第なので、claude だけプロンプトにも埋める。"""
    assert llm_cli.CLAUDE.rules_in_prompt is True
    assert [a.id for a in llm_cli.ADAPTERS.values() if a.rules_in_prompt] == ["claude"]


def test_the_host_writes_the_rules_file_before_starting(tmp_path, monkeypatch):
    """ホストは ACP を起動する前に契約ファイルを置く（起動時に読まれるため）。"""
    use(monkeypatch, agent_cli="codex", agent_use_acp=False)
    host = grok_session.open_host(tmp_path)
    assert host.adapter is llm_cli.CODEX
    host.adapter.write_rules(host.workdir, "契約")
    assert (tmp_path / "AGENTS.md").read_text(encoding="utf-8") == "契約\n"


def test_wants_contract_follows_the_adapter(tmp_path, monkeypatch):
    use(monkeypatch, agent_cli="grok")
    acp_host = grok_session.open_host(tmp_path, use_acp=True)
    assert acp_host.wants_contract() is False  # _meta.rules で渡っている
    oneshot = grok_session.open_host(tmp_path, use_acp=False)
    assert oneshot.wants_contract() is True  # 新規のワンショットは埋め込む
    oneshot.session_id = "s1"
    assert oneshot.wants_contract() is False  # 続きなら不要

    use(monkeypatch, agent_cli="claude")
    claude = grok_session.open_host(tmp_path, use_acp=True)
    assert claude.wants_contract() is True  # CLAUDE.md の取りこぼし対策


# --------------------------------------------------------------------------
# 設定からの解決
# --------------------------------------------------------------------------

def test_the_selected_cli_comes_from_the_settings(monkeypatch):
    use(monkeypatch, agent_cli="cursor")
    assert llm_cli.active_adapter().id == "cursor"
    use(monkeypatch)
    assert llm_cli.active_adapter().id == "grok"
    # 知らない値は既定の grok（設定ファイルが壊れていても動かす）
    assert llm_cli.adapter_for("nope").id == "grok"


def test_commands_fall_back_from_overrides_to_defaults(monkeypatch):
    values = use(monkeypatch, agent_cli="claude")
    assert llm_cli.command_for(llm_cli.CLAUDE, values) == "claude-agent-acp"
    assert llm_cli.command_for(llm_cli.CLAUDE, values, oneshot=True) == "claude"

    values = use(
        monkeypatch,
        agent_cli="claude",
        agent_cli_commands={
            "claude": "npx @zed-industries/claude-code-acp",
            "claude_oneshot": "/opt/claude",
        },
    )
    assert llm_cli.command_for(llm_cli.CLAUDE, values) == (
        "npx @zed-industries/claude-code-acp"
    )
    assert llm_cli.command_for(llm_cli.CLAUDE, values, oneshot=True) == "/opt/claude"


def test_grok_keeps_reading_the_legacy_command_setting(monkeypatch):
    values = use(monkeypatch, grok_command="mygrok")
    assert llm_cli.command_for(llm_cli.GROK, values) == "mygrok"
    assert llm_cli.command_for(llm_cli.GROK, values, oneshot=True) == "mygrok"
    assert llm_cli.model_for(llm_cli.GROK, values) == "grok-4.5"


def test_models_are_per_cli(monkeypatch):
    values = use(monkeypatch, agent_cli="claude", agent_cli_models={"claude": "opus"})
    assert llm_cli.model_for(llm_cli.CLAUDE, values) == "opus"
    # 未指定なら CLI の既定に任せる（フラグを出さない）
    values = use(monkeypatch, agent_cli="codex")
    assert llm_cli.model_for(llm_cli.CODEX, values) == ""


def test_auth_markers_are_cli_specific():
    assert llm_cli.CLAUDE.looks_like_auth_error("Please run /login to continue")
    assert not llm_cli.GROK.looks_like_auth_error("Please run /login to continue")
    # 共通のものはどの CLI でも拾う
    for adapter in llm_cli.ADAPTERS.values():
        assert adapter.looks_like_auth_error("Error: not authenticated")


def test_install_hints_and_labels_come_from_the_command_name():
    assert "Claude Code" in llm_cli.install_hint_for("/usr/local/bin/claude")
    assert "Grok Build CLI" in llm_cli.install_hint_for("grok")
    assert llm_cli.install_hint_for("mystery-binary") == ""
    assert llm_cli.label_for("codex-acp") == "Codex"
    assert llm_cli.label_for("mystery-binary") == "mystery-binary"


# --------------------------------------------------------------------------
# 選択中の CLI が全経路に効く
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_the_oneshot_client_runs_the_selected_cli(monkeypatch, tmp_path):
    """英訳・自動タグ・ヘルスチェックが通るワンショット経路も選択に従う。"""
    use(monkeypatch, agent_cli="codex", grok_workdir=str(tmp_path))
    calls: list[list[str]] = []

    async def fake(argv, cwd, timeout, env=None):
        calls.append(list(argv))
        return (0, "ok", "")

    monkeypatch.setattr(grok, "_exec", fake)
    assert await grok.get_client().complete("やあ") == "ok"
    assert calls == [["codex", "exec", "やあ"]]

    calls.clear()
    health = await grok.check_grok()
    assert health.status == "ok"
    assert calls == [["codex", "--version"]]


@pytest.mark.asyncio
async def test_switching_the_cli_forgets_saved_sessions(monkeypatch, tmp_path):
    """CLI を変えたら、保存済みの続き用セッション id を捨てる。"""
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    await db.init_db()
    async with db.get_db() as conn:
        await conn.execute(
            "INSERT INTO chat_sessions (id, created_at, job_id, messages,"
            " grok_session_id, grok_cwd) VALUES ('c1','t',NULL,'[]','s-1','/w')"
        )
        await conn.commit()

    await llm_cli.forget_saved_sessions()

    async with db.get_db() as conn:
        async with conn.execute(
            "SELECT grok_session_id, grok_cwd FROM chat_sessions WHERE id='c1'"
        ) as cur:
            row = await cur.fetchone()
        assert row["grok_session_id"] == ""
        assert row["grok_cwd"] == "/w"  # 作業ディレクトリと会話はそのまま


# --------------------------------------------------------------------------
# 設定 API から切り替える
# --------------------------------------------------------------------------

def test_put_settings_switches_the_cli_and_clears_sessions(tmp_path, monkeypatch):
    """設定ページで CLI を変えると、保存済みセッション id が消える。"""
    from fastapi.testclient import TestClient

    from app.main import app

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(
        config, "CONFIG_PATH", tmp_path / "config.json"
    )
    monkeypatch.setattr(config, "_settings", None)

    with TestClient(app) as client:
        created = client.post("/api/chat/sessions", json={"mode": "full"})
        assert created.status_code == 201, created.text
        session_id = created.json()["id"]
        # 続き用の id が入っている状態を作る
        with sqlite3.connect(db.DB_PATH) as conn:
            conn.execute(
                "UPDATE chat_sessions SET grok_session_id = 's-1' WHERE id = ?",
                (session_id,),
            )

        body = client.put("/api/settings", json={"agent_cli": "claude"}).json()
        assert body["agent_cli"] == "claude"
        assert llm_cli.active_adapter().id == "claude"

        stored = client.get(f"/api/chat/sessions/{session_id}").json()
        assert stored["grok_session_id"] == ""
        # 会話そのものは残る
        assert stored["messages"][0]["role"] == "system"

        # 知らない CLI は 422（設定を壊さない）
        assert client.put("/api/settings", json={"agent_cli": "gpt"}).status_code == 422
