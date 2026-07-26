"""Grok chat API tests (SPEC §4 / §9). The CLI is replaced by a fake process."""

import json

import pytest
from fastapi.testclient import TestClient

from app import config, db, grok, jobs
from app.main import app
from app.models import ChatSessionCreate, Settings
from app.prompts import build_conversation, build_system_prompt


class FakeCli:
    """Replays scripted `grok -p` answers and records the argv it was given."""

    def __init__(self, answers=(), error=None):
        self.answers = list(answers)
        self.error = error
        self.calls: list[list[str]] = []
        self.cwds: list[str] = []

    async def __call__(self, argv, cwd, timeout):
        self.calls.append(list(argv))
        self.cwds.append(str(cwd))
        if self.error is not None:
            raise self.error
        if not self.answers:
            raise AssertionError("fake grok CLI ran out of scripted answers")
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        if isinstance(answer, tuple):  # (returncode, stdout, stderr)
            return answer
        return (0, answer, "")

    @property
    def prompts(self) -> list[str]:
        return [argv[argv.index("-p") + 1] for argv in self.calls if "-p" in argv]


JSON_ANSWER = """\
プロンプトができました。

```json
{
  "image_prompt": "a single still frame from a Japanese adult video, adult woman dancing",
  "video_prompt": "Starting from the given first frame, she dances happily.",
  "notes": "照明は暖色にしました"
}
```
"""

QUESTION_ANSWER = """\
いくつか教えてください。
- 場所はどこですか？
- 服装は？
"""


@pytest.fixture
def env(tmp_path, monkeypatch):
    assets = tmp_path / "assets"
    (assets / "image").mkdir(parents=True)
    (assets / "audio").mkdir(parents=True)
    workdir = tmp_path / "grok-workdir"
    workdir.mkdir()

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(jobs, "ASSETS_DIR", assets)
    monkeypatch.setattr(
        config,
        "_settings",
        Settings(grok_command="grok", grok_model="grok-4.5", grok_workdir=str(workdir)),
    )

    fake = FakeCli()
    monkeypatch.setattr(grok, "_exec", fake)

    start_image = assets / "image" / "start.png"
    start_image.write_bytes(b"PNG")

    with TestClient(app) as client:
        yield type(
            "Env",
            (),
            {
                "client": client,
                "cli": fake,
                "workdir": workdir,
                "assets": assets,
                "start_image": start_image,
            },
        )


def start(env, **overrides) -> dict:
    body = {"mode": "full", "duration": 6, "prompt_template": "natural"}
    body.update(overrides)
    response = env.client.post("/api/chat/sessions", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def say(env, session_id: str, content: str = "かおりが楽しそうにダンスをしている"):
    return env.client.post(
        f"/api/chat/sessions/{session_id}/messages", json={"content": content}
    )


# --------------------------------------------------------------------------
# session lifecycle
# --------------------------------------------------------------------------

def test_session_starts_with_a_system_message(env):
    session = start(env, trigger_text="kaori", image_prompt_draft="draft image")
    assert len(session["messages"]) == 1
    system = session["messages"][0]
    assert system["role"] == "system"
    assert "kaori" in system["content"]
    assert "draft image" in system["content"]
    assert "6 seconds" in system["content"]


def test_display_names_are_mapped_to_trigger_words(env):
    session = start(
        env,
        loras=[
            {
                "lora_name": "kohei06__yui__kaori.safetensors",
                "trigger_word": "kaori",
                "strength": 1.0,
                "display_name": "かおり",
            }
        ],
        trigger_text="kaori",
    )
    system = session["messages"][0]["content"]
    assert "「かおり」 -> trigger word `kaori`" in system
    # the old "never repeat the trigger words" rule is gone
    assert "MUST NOT repeat them" not in system
    assert "as the subject's name" in system


def test_interview_then_final_json(env):
    session = start(env)
    env.cli.answers = [QUESTION_ANSWER, JSON_ANSWER]

    first = say(env, session["id"]).json()
    assert first["role"] == "assistant"
    assert first["result"] is None
    assert "場所" in first["content"]

    second = say(env, session["id"], "おまかせで").json()
    assert second["result"]["video_prompt"].startswith("Starting from the given")
    assert second["result"]["notes"] == "照明は暖色にしました"

    # the whole transcript is persisted, system message first
    stored = env.client.get(f"/api/chat/sessions/{session['id']}").json()
    roles = [m["role"] for m in stored["messages"]]
    assert roles == ["system", "user", "assistant", "user", "assistant"]

    # every turn re-sends system prompt + history (stateless CLI, SPEC §4.3)
    last_prompt = env.cli.prompts[-1]
    assert "# ROLE" in last_prompt
    assert "おまかせで" in last_prompt
    assert "場所はどこですか" in last_prompt
    assert env.cli.calls[-1][:3] == ["grok", "--model", "grok-4.5"]
    assert env.cli.cwds[-1] == str(env.workdir)


def test_json_without_a_fence_is_accepted(env):
    session = start(env)
    env.cli.answers = [
        'こちらです {"image_prompt": "an image", "video_prompt": null, "notes": "n"} 以上'
    ]
    reply = say(env, session["id"]).json()
    assert reply["result"] == {
        "image_prompt": "an image",
        "video_prompt": None,
        "notes": "n",
    }


def test_unparsable_fence_triggers_one_retry(env):
    session = start(env)
    env.cli.answers = ["```json\n{broken,,}\n```", JSON_ANSWER]
    reply = say(env, session["id"]).json()
    assert reply["result"]["image_prompt"].startswith("a single still frame")
    assert len(env.cli.calls) == 2
    assert "could not be parsed" in env.cli.prompts[-1]


def test_retry_that_keeps_failing_returns_text_only(env):
    session = start(env)
    env.cli.answers = ["```json\n{oops}\n```", "```json\nstill broken\n```"]
    reply = say(env, session["id"]).json()
    assert reply["result"] is None
    assert "still broken" in reply["content"]
    assert len(env.cli.calls) == 2  # exactly one retry


def test_question_without_a_fence_is_not_retried(env):
    session = start(env)
    env.cli.answers = [QUESTION_ANSWER]
    assert say(env, session["id"]).json()["result"] is None
    assert len(env.cli.calls) == 1


def test_missing_session_is_404(env):
    assert env.client.get("/api/chat/sessions/nope").status_code == 404
    assert say(env, "nope").status_code == 404


def test_empty_message_is_422(env):
    session = start(env)
    assert say(env, session["id"], "   ").status_code == 422


# --------------------------------------------------------------------------
# modes
# --------------------------------------------------------------------------

def test_i2v_copies_the_start_frame_into_the_workdir(env):
    session = start(
        env, mode="i2v", start_image_path=str(env.start_image), duration=8
    )
    system = session["messages"][0]["content"]
    copied = list(env.workdir.glob("start_frame_*.png"))
    assert len(copied) == 1
    assert copied[0].name in system
    assert "`video_prompt` only" in system
    assert "# IMAGE PROMPT SPEC" not in system  # no image prompt in mode B


def test_i2v_with_a_stray_start_image_is_422(env, tmp_path):
    stray = tmp_path / "outside.png"
    stray.write_bytes(b"PNG")
    response = env.client.post(
        "/api/chat/sessions", json={"mode": "i2v", "start_image_path": str(stray)}
    )
    assert response.status_code == 422


def test_image_only_drops_the_video_spec(env):
    session = start(env, mode="image_only")
    system = session["messages"][0]["content"]
    assert "`image_prompt` only" in system
    assert "# VIDEO PROMPT SPEC" not in system


def test_tagged_template_is_selected(env):
    system = start(env, prompt_template="tagged")["messages"][0]["content"]
    assert "[VISUAL]" in system and "[SPEECH]" in system
    assert "Prompt template: NATURAL" not in system


# --------------------------------------------------------------------------
# CLI failures -> 502
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "error, needle",
    [
        (grok.LLMError("grok CLI が 120 秒以内に応答しませんでした（タイムアウト）"), "タイムアウト"),
        (grok.LLMError("'grok' コマンドが見つかりません。"), "見つかりません"),
    ],
)
def test_cli_errors_are_502(env, error, needle):
    session = start(env)
    env.cli.error = error
    response = say(env, session["id"])
    assert response.status_code == 502
    assert needle in response.json()["detail"]


def test_auth_failure_is_reported_clearly(env):
    session = start(env)
    env.cli.answers = [(1, "", "Error: not authenticated, please sign in")]
    response = say(env, session["id"])
    assert response.status_code == 502
    assert "認証" in response.json()["detail"]
    assert len(env.cli.calls) == 1  # auth errors are not retried


def test_model_flag_failure_falls_back_to_no_model(env):
    session = start(env)
    env.cli.answers = [(2, "", "unknown option '--model'"), JSON_ANSWER]
    reply = say(env, session["id"]).json()
    assert reply["result"]["image_prompt"]
    assert "--model" in env.cli.calls[0]
    assert "--model" not in env.cli.calls[1]


# --------------------------------------------------------------------------
# health
# --------------------------------------------------------------------------

def test_health_reports_grok_version(env):
    env.cli.answers = ["grok 0.4.2\n"]
    grok_health = env.client.get("/api/health").json()["grok"]
    assert grok_health["status"] == "ok"
    assert "0.4.2" in grok_health["detail"]
    assert env.cli.calls[-1] == ["grok", "--version"]


def test_health_reports_a_missing_cli(env):
    env.cli.error = grok.LLMError("'grok' コマンドが見つかりません")
    grok_health = env.client.get("/api/health").json()["grok"]
    assert grok_health["status"] == "error"
    assert "見つかりません" in grok_health["detail"]


# --------------------------------------------------------------------------
# unit tests
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "text, expected",
    [
        ('```json\n{"image_prompt": "a"}\n```', {"image_prompt": "a"}),
        ('```\n{"video_prompt": "v"}\n```', {"video_prompt": "v"}),
        ('noise {"notes": "n", "video_prompt": "v"} noise', {"video_prompt": "v"}),
        # a fenced block wins over an earlier inline object
        (
            '{"image_prompt": "早い"}\n```json\n{"image_prompt": "fenced"}\n```',
            {"image_prompt": "fenced"},
        ),
        # nested braces inside the object survive the brace scanner
        ('{"image_prompt": "a {b} c", "notes": null}', {"image_prompt": "a {b} c"}),
        # a brace inside a string must not confuse the scanner
        (
            '{"image_prompt": "she says \\"{\\" loudly"}',
            {"image_prompt": 'she says "{" loudly'},
        ),
    ],
)
def test_extract_result_variants(text, expected):
    result = grok.extract_result(text)
    assert result is not None
    for key, value in expected.items():
        assert result[key] == value


@pytest.mark.parametrize(
    "text",
    [
        "",
        "ただの質問です。場所はどこですか？",
        "```json\n{broken}\n```",
        '{"notes": "notes only"}',  # no prompt -> not a final proposal
        '{"image_prompt": 12}',  # wrong type
        '{"other": "x"}',
    ],
)
def test_extract_result_rejects(text):
    assert grok.extract_result(text) is None


def test_extract_result_normalizes_missing_keys():
    result = grok.extract_result('{"image_prompt": "a", "video_prompt": "  "}')
    assert result == {"image_prompt": "a", "video_prompt": None, "notes": None}


def test_has_json_fence():
    assert grok.has_json_fence("```json\n{}\n```")
    assert grok.has_json_fence("```\nx\n```")
    assert not grok.has_json_fence("質問です")


def test_build_conversation_layout():
    ctx = ChatSessionCreate(mode="full", duration=10)
    system = build_system_prompt(ctx)
    from app.models import ChatMessage

    messages = [
        ChatMessage(role="system", content=system, ts="t"),
        ChatMessage(role="user", content="踊って", ts="t"),
        ChatMessage(role="assistant", content="どこで？", ts="t"),
        ChatMessage(role="user", content="ぜんぶ任せます", ts="t"),
    ]
    text = build_conversation(messages)
    assert text.index("# ROLE") < text.index("# CONVERSATION SO FAR")
    assert text.index("踊って") < text.index("どこで？") < text.index("ぜんぶ任せます")
    assert "could not be parsed" not in text
    assert "could not be parsed" in build_conversation(messages, retry=True)


def test_build_conversation_of_a_fresh_session_has_no_history_header():
    ctx = ChatSessionCreate()
    from app.models import ChatMessage

    text = build_conversation(
        [ChatMessage(role="system", content=build_system_prompt(ctx), ts="t")]
    )
    assert "# CONVERSATION SO FAR" not in text


async def test_exec_reports_a_missing_command(tmp_path):
    with pytest.raises(grok.LLMError, match="見つかりません"):
        await grok._exec(["definitely-not-a-real-binary-xyz"], tmp_path, 5.0)


async def test_exec_times_out(tmp_path):
    with pytest.raises(grok.LLMError, match="タイムアウト"):
        await grok._exec(["python3", "-c", "import time; time.sleep(5)"], tmp_path, 0.2)


async def test_exec_returns_stdout(tmp_path):
    code, out, err = await grok._exec(
        ["python3", "-c", "print('hello')"], tmp_path, 10.0
    )
    assert (code, out.strip()) == (0, "hello")
    assert err == ""


async def test_client_reads_settings(monkeypatch, tmp_path):
    monkeypatch.setattr(
        config,
        "_settings",
        Settings(grok_command="mygrok", grok_model="", grok_workdir=str(tmp_path)),
    )
    calls = []

    async def fake(argv, cwd, timeout):
        calls.append(argv)
        return (0, "ok", "")

    monkeypatch.setattr(grok, "_exec", fake)
    client = grok.get_client()
    assert await client.complete("hi") == "ok"
    assert calls == [["mygrok", "-p", "hi"]]  # no --model when it is unset


def test_json_answer_is_valid_json_fixture():
    """Guard the fixture itself so a broken sample cannot mask a real bug."""
    body = JSON_ANSWER.split("```json")[1].split("```")[0]
    assert set(json.loads(body)) == {"image_prompt", "video_prompt", "notes"}
