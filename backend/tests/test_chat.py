"""Grok chat API tests (SPEC §4 / §9). The CLI is replaced by a fake process."""

import json

import pytest
from fastapi.testclient import TestClient

from app import config, db, grok, jobs, library
from app.main import app
from app.models import ChatSessionCreate, Settings
from app.prompts import build_conversation, build_system_prompt
from app.workflows import get_video_spec


class FakeCli:
    """Replays scripted `grok -p` answers and records the argv it was given."""

    def __init__(self, answers=(), error=None):
        self.answers = list(answers)
        self.error = error
        self.calls: list[list[str]] = []
        self.cwds: list[str] = []
        self.timeouts: list[float | None] = []

    async def __call__(self, argv, cwd, timeout):
        self.calls.append(list(argv))
        self.cwds.append(str(cwd))
        self.timeouts.append(timeout)
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
    lib = tmp_path / "library"
    lib.mkdir()
    workdir = tmp_path / "grok-workdir"
    workdir.mkdir()

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(jobs, "ASSETS_DIR", assets)
    monkeypatch.setattr(jobs, "LIBRARY_DIR", lib)
    monkeypatch.setattr(library, "LIBRARY_DIR", lib)
    monkeypatch.setattr(
        config,
        "_settings",
        Settings(grok_command="grok", grok_model="grok-4.5", grok_workdir=str(workdir)),
    )

    fake = FakeCli()
    monkeypatch.setattr(grok, "_exec", fake)

    start_image = assets / "image" / "start.png"
    start_image.write_bytes(b"PNG")
    end_image = assets / "image" / "end.png"
    end_image.write_bytes(b"PNG")

    with TestClient(app) as client:
        yield type(
            "Env",
            (),
            {
                "client": client,
                "cli": fake,
                "workdir": workdir,
                "assets": assets,
                "library": lib,
                "start_image": start_image,
                "end_image": end_image,
            },
        )


def add_to_library(
    env, kind: str, name: str, tags: str = "", category: str = ""
) -> dict:
    """ライブラリに 1 件登録して、その行（``path`` / ``url`` 付き）を返す。"""
    suffix = {"image": ".png", "video": ".mp4", "audio": ".wav"}[kind]
    response = env.client.post(
        f"/api/library/{kind}",
        files={"file": (f"{name}{suffix}", b"data", "application/octet-stream")},
        data={"tags": tags, "category": category},
    )
    assert response.status_code == 201, response.text
    item = response.json()
    renamed = env.client.patch(f"/api/library/{item['id']}", json={"name": name})
    assert renamed.status_code == 200, renamed.text
    return renamed.json()


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


def test_video_loras_get_their_own_trigger_section(env):
    session = start(
        env,
        video_loras=[
            {
                "lora_name": "motion.safetensors",
                "trigger_word": "slowmo",
                "strength": 1.0,
                "display_name": "スローモ",
            }
        ],
        video_trigger_text="slowmo",
    )
    system = session["messages"][0]["content"]
    assert "Active **video** LoRA trigger words: `slowmo`." in system
    assert "「スローモ」 -> trigger word `slowmo`" in system
    assert "belong in `video_prompt`" in system


def test_without_video_loras_there_is_no_video_trigger_section(env):
    system = start(env)["messages"][0]["content"]
    assert "**video** LoRA trigger words" not in system


def test_an_image_only_session_never_mentions_video_loras(env):
    system = start(
        env,
        mode="image_only",
        video_loras=[
            {"lora_name": "motion.safetensors", "trigger_word": "slowmo",
             "strength": 1.0, "display_name": "スローモ"}
        ],
        video_trigger_text="slowmo",
    )["messages"][0]["content"]
    assert "**video** LoRA trigger words" not in system


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
        # 音声セッション用のフィールドは画像・動画のセッションでは常に null
        "audio_prompt": None,
        "lyrics": None,
        "negative_tags": None,
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


# --------------------------------------------------------------------------
# selected video workflow (SPEC §4.3)
# --------------------------------------------------------------------------

def test_the_selected_workflow_is_described_in_the_context(env):
    spec = get_video_spec("minimax_h3_i2v")
    system = start(env, video_workflow=spec.id)["messages"][0]["content"]
    assert f"Selected video workflow: **`{spec.id}`**" in system
    assert spec.description in system
    assert spec.prompt_hint in system
    assert "`source_image`（開始フレーム）" in system


def test_the_default_workflow_is_used_when_the_form_omits_it(env):
    spec = get_video_spec(None)
    system = start(env)["messages"][0]["content"]
    assert f"**`{spec.id}`**" in system
    assert spec.audio_role in system


def test_a_workflow_without_a_start_frame_asks_about_the_looks_too(env):
    """t2v を選ぶと i2v モードでも「開始フレームがある」前提にしない。"""
    system = start(env, mode="i2v", video_workflow="minimax_h3_t2v")["messages"][0][
        "content"
    ]
    assert "`video_prompt` only" in system
    assert "This workflow gets no start frame" in system
    assert "already fixed by the given image" not in system


def test_a_start_frame_workflow_keeps_the_i2v_wording(env):
    system = start(env, mode="i2v", video_workflow="minimax_h3_i2v")["messages"][0][
        "content"
    ]
    assert "already fixed by the given image" in system


def test_image_only_has_no_workflow_section(env):
    system = start(env, mode="image_only", video_workflow="minimax_h3_i2v")["messages"][
        0
    ]["content"]
    assert "Selected video workflow" not in system


def test_tagged_template_is_selected(env):
    """H3 は talkvid タグを埋め込まない。公式フィールドだけが動画契約。"""
    system = start(env, prompt_template="tagged")["messages"][0]["content"]
    assert "[VISUAL]" not in system and "[SPEECH]" not in system
    assert "Prompt template: NATURAL" not in system
    assert "VIDEO PROMPT SPEC — MiniMax H3" in system
    assert "integrated_multimodal_description" in system
    assert "[Shot 1]" in system
    assert "<d>" in system


def test_default_video_system_prompt_is_official_h3(env):
    i2v = start(env, mode="i2v", video_workflow="minimax_h3_i2v")["messages"][0][
        "content"
    ]
    t2v = start(env, mode="i2v", video_workflow="minimax_h3_t2v")["messages"][0][
        "content"
    ]
    for system in (i2v, t2v):
        assert "VIDEO PROMPT SPEC — MiniMax H3" in system
        assert "integrated_multimodal_description" in system
        assert "FEW-SHOT EXAMPLES — MiniMax H3" in system
        assert "follow the FEW-SHOT video examples closely" not in system
        assert "Starting from the given first frame" not in system
        assert "one continuous shot (no cuts" not in system


def test_image_only_and_audio_keep_h3_guides_out(env):
    image = start(env, mode="image_only")["messages"][0]["content"]
    audio = start(env, mode="audio")["messages"][0]["content"]
    assert "# VIDEO PROMPT SPEC" not in image
    assert "# VIDEO PROMPT SPEC" not in audio
    assert "FEW-SHOT EXAMPLES — MiniMax H3" not in image
    assert "FEW-SHOT EXAMPLES — MiniMax H3" not in audio


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
    assert result == {
        "image_prompt": "a",
        "video_prompt": None,
        "notes": None,
        "audio_prompt": None,
        "lyrics": None,
        "negative_tags": None,
    }


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


def _chat_timeout(env, monkeypatch, timeout: float) -> float | None:
    """設定を差し替えて 1 往復し、CLI に渡った制限時間を返す。"""
    monkeypatch.setattr(
        config, "_settings", config.load_settings().model_copy(
            update={"agent_grok_timeout": timeout}
        )
    )
    session = start(env)
    env.cli.answers = [QUESTION_ANSWER]
    assert say(env, session["id"]).status_code == 200
    return env.cli.timeouts[-1]


def test_chat_uses_the_configured_grok_timeout(env, monkeypatch):
    """相談の CLI 呼び出しにも agent_grok_timeout が効く（既定 120 秒固定ではない）。"""
    assert _chat_timeout(env, monkeypatch, 900.0) == 900.0


def test_chat_timeout_zero_means_no_timeout(env, monkeypatch):
    assert _chat_timeout(env, monkeypatch, 0.0) is None


def test_json_answer_is_valid_json_fixture():
    """Guard the fixture itself so a broken sample cannot mask a real bug."""
    body = JSON_ANSWER.split("```json")[1].split("```")[0]
    assert set(json.loads(body)) == {"image_prompt", "video_prompt", "notes"}


# --------------------------------------------------------------------------
# 画像モデルごとのプロンプトガイド (SPEC §4.3)
# --------------------------------------------------------------------------

def test_the_image_prompt_spec_follows_the_selected_image_workflow():
    krea2 = build_system_prompt(ChatSessionCreate(mode="image_only"))
    assert "IMAGE PROMPT SPEC — Krea 2 turbo" in krea2
    assert "IMAGE PROMPT SPEC — Anima" not in krea2
    # Krea 2 keeps its own few-shot image examples
    assert "FEW-SHOT EXAMPLES — image (Krea 2)" in krea2

    anima = build_system_prompt(
        ChatSessionCreate(mode="image_only", image_workflow="anima")
    )
    assert "IMAGE PROMPT SPEC — Anima" in anima
    assert "IMAGE PROMPT SPEC — Krea 2 turbo" not in anima
    assert "score_7" in anima
    # the Krea 2 prose examples would teach the wrong style here
    assert "FEW-SHOT EXAMPLES — image (Krea 2)" not in anima

    z_image = build_system_prompt(
        ChatSessionCreate(mode="image_only", image_workflow="z_image_turbo")
    )
    assert "IMAGE PROMPT SPEC — Tongyi Z-Image turbo" in z_image
    assert "Never write a negative prompt" in z_image

    qwen = build_system_prompt(
        ChatSessionCreate(mode="image_only", image_workflow="qwen_image_edit_2511")
    )
    assert "IMAGE PROMPT SPEC — Qwen-Image Edit 2511" in qwen
    assert "edit instruction" in qwen


def test_the_selected_image_workflow_is_named_in_the_context():
    system = build_system_prompt(
        ChatSessionCreate(mode="image_only", image_workflow="qwen_image_edit_2511")
    )
    assert "Selected image workflow: **`qwen_image_edit_2511`**" in system
    assert "model family `qwen-image`" in system
    assert "`source_image`" in system


def test_an_unknown_image_workflow_falls_back_to_the_default_spec():
    system = build_system_prompt(
        ChatSessionCreate(mode="image_only", image_workflow="nope")
    )
    assert "IMAGE PROMPT SPEC — Krea 2 turbo" in system


def test_a_video_only_session_embeds_no_image_spec():
    system = build_system_prompt(
        ChatSessionCreate(mode="i2v", image_workflow="anima")
    )
    assert "IMAGE PROMPT SPEC" not in system
    assert "FEW-SHOT EXAMPLES — MiniMax H3" in system


# --------------------------------------------------------------------------
# 音声モード（mode='audio'）のプロンプトジェネレータ
# --------------------------------------------------------------------------

AUDIO_JSON_ANSWER = """\
できました。

```json
{
  "audio_prompt": "dreamy city-pop ballad, female vocal, warm rhodes, brushed drums",
  "lyrics": "[Verse]\\n最終列車が街を抜ける\\n\\n[Chorus]\\nもう一度だけ",
  "notes": "しっとりめにしました"
}
```
"""


def test_audio_session_embeds_the_selected_models_guide(env):
    mmm3 = start(env, mode="audio", audio_workflow="minimax_music_3")
    system = mmm3["messages"][0]["content"]
    assert "AUDIO PROMPT SPEC — MiniMax Music 3" in system
    assert "Stable Audio 3 Medium" not in system
    # 音声の会話にシーン・カメラ・LoRA の話は出さない
    assert "IMAGE PROMPT SPEC" not in system
    assert "VIDEO PROMPT SPEC" not in system

    sa3 = start(env, mode="audio", audio_workflow="stable_audio_3_medium_base")
    system = sa3["messages"][0]["content"]
    assert "AUDIO PROMPT SPEC — Stable Audio 3 Medium" in system
    assert "AUDIO PROMPT SPEC — MiniMax Music 3" not in system
    # 歌えないモデルなので歌詞の相談をさせない
    assert "このモデルは歌いません" in system


def test_audio_session_context_carries_the_length_and_the_fields(env):
    system = start(
        env, mode="audio", audio_workflow="minimax_music_3", duration=90
    )["messages"][0]["content"]
    assert "**90 秒**" in system
    assert "1〜300 秒" in system
    assert "`lyrics`" in system

    sa3 = start(
        env, mode="audio", audio_workflow="stable_audio_3_medium_base", duration=30
    )["messages"][0]["content"]
    assert "1〜380 秒" in sa3
    assert "`audio_category`" in sa3


def test_audio_session_embeds_the_drafts(env):
    system = start(
        env,
        mode="audio",
        audio_prompt_draft="lofi hip hop",
        lyrics_draft="[Verse 1]\nドラフト",
    )["messages"][0]["content"]
    assert "lofi hip hop" in system
    assert "[Verse 1]" in system


def test_audio_session_ignores_a_lyrics_draft_the_model_cannot_use(env):
    system = start(
        env,
        mode="audio",
        audio_workflow="stable_audio_3_medium_base",
        lyrics_draft="[Verse 1]\nドラフト",
    )["messages"][0]["content"]
    assert "Existing lyrics draft" not in system


def test_audio_session_falls_back_to_the_default_workflow(env):
    system = start(env, mode="audio", audio_workflow="nope")["messages"][0]["content"]
    assert "AUDIO PROMPT SPEC — MiniMax Music 3" in system


def test_audio_result_carries_the_prompt_lyrics_and_suggestions(env):
    session = start(env, mode="audio")
    env.cli.answers = [AUDIO_JSON_ANSWER]
    reply = say(env, session["id"]).json()
    result = reply["result"]
    assert result["audio_prompt"].startswith("dreamy city-pop")
    assert result["lyrics"].startswith("[Verse]")
    assert result["notes"] == "しっとりめにしました"
    # 画像・動画のフィールドは音声セッションでは常に null
    assert result["image_prompt"] is None
    assert result["video_prompt"] is None


def test_audio_question_turn_returns_no_result(env):
    session = start(env, mode="audio")
    env.cli.answers = ["どんなジャンルにしますか？"]
    assert say(env, session["id"]).json()["result"] is None


def test_build_audio_system_prompt_is_used_by_build_system_prompt():
    audio = build_system_prompt(
        ChatSessionCreate(mode="audio", audio_workflow="minimax_music_3")
    )
    assert "# AUDIO PROMPT SPEC" in audio
    # 画像・動画のセッションは今までどおり
    image = build_system_prompt(ChatSessionCreate(mode="image_only"))
    assert "# AUDIO PROMPT SPEC" not in image
    assert "# IMAGE PROMPT SPEC" in image


# --------------------------------------------------------------------------
# フォームの現在値の受け渡し（参照素材・解像度・end_image・除外指定、SPEC §4.3）
# --------------------------------------------------------------------------

def test_reference_material_becomes_a_tag_table(env):
    picture = add_to_library(
        env, "image", "サクラ", tags="銀髪,メイド服", category="character"
    )
    clip = add_to_library(env, "video", "走り", category="prop")
    track = add_to_library(env, "audio", "声")
    system = start(
        env,
        mode="i2v",
        video_workflow="minimax_h3_r2v",
        reference_images=[picture["url"]],
        reference_videos=[clip["url"]],
        reference_audios=[track["url"]],
    )["messages"][0]["content"]
    assert "`<Picture 1>` = 「サクラ」(character) [銀髪, メイド服]" in system
    assert "`<Video 1>` = 「走り」(prop)" in system
    # 参照動画のサウンドトラックが `<Audio 1>` を取るので、参照音声は 2 番から
    assert "`<Audio 2>` = 「声」" in system
    assert "存在しない `<Picture N>`" in system


def test_unregistered_reference_material_shows_only_its_filename(env):
    stray = env.assets / "image" / "stray.png"
    stray.write_bytes(b"PNG")
    system = start(
        env,
        mode="i2v",
        video_workflow="minimax_h3_r2v",
        reference_images=[str(stray)],
    )["messages"][0]["content"]
    assert "`<Picture 1>` = file `stray.png` (not in library)" in system


def test_a_reference_workflow_without_material_asks_the_user(env):
    system = start(env, mode="i2v", video_workflow="minimax_h3_r2v")["messages"][0][
        "content"
    ]
    assert "none is attached yet" in system
    assert "ユーザーに確認" in system


def test_a_workflow_without_references_gets_no_reference_section(env):
    system = start(env, mode="i2v", video_workflow="minimax_h3_i2v")["messages"][0][
        "content"
    ]
    assert "Reference material" not in system


def test_the_motion_context_workflow_says_so(env):
    system = start(env, mode="i2v", video_workflow="minimax_h3_r2v_context")[
        "messages"
    ][0]["content"]
    assert "Motion Context" in system
    # 書き方そのものは素の r2v と同じガイド
    assert "MiniMax H3 reference mode" in system


def test_the_aspect_ratio_is_passed_with_an_orientation_hint(env):
    system = start(
        env, aspect_ratio="9:16 (Portrait Widescreen)", megapixels=0.4
    )["messages"][0]["content"]
    assert "Aspect ratio: **9:16 (Portrait Widescreen)**（portrait）" in system
    assert "0.4 megapixels" in system


def test_without_an_aspect_ratio_nothing_is_said_about_it(env):
    assert "Aspect ratio:" not in start(env)["messages"][0]["content"]


def test_an_end_image_asks_for_a_transition(env):
    system = start(
        env,
        mode="i2v",
        video_workflow="minimax_h3_i2v",
        start_image_path=str(env.start_image),
        end_image_path=str(env.end_image),
    )["messages"][0]["content"]
    assert "`end_image` が指定されている" in system
    assert "遷移（transition）" in system
    # start と同じく作業ディレクトリに置く
    assert len(list(env.workdir.glob("end_frame_*.png"))) == 1


def test_without_an_end_image_nothing_is_said_about_it(env):
    system = start(
        env,
        mode="i2v",
        video_workflow="minimax_h3_i2v",
        start_image_path=str(env.start_image),
    )["messages"][0]["content"]
    # ワークフローの説明には出てくるので、CONTEXT の「指定されている」の一文だけ見る
    assert "`end_image` が指定されている" not in system
    assert not list(env.workdir.glob("end_frame_*"))


def test_an_editing_image_workflow_gets_its_input_picture(env):
    session = start(
        env,
        mode="image_only",
        image_workflow="qwen_image_edit_2511",
        start_image_path=str(env.start_image),
    )
    system = session["messages"][0]["content"]
    copied = list(env.workdir.glob("start_frame_*.png"))
    assert len(copied) == 1
    assert copied[0].name in system
    assert "編集指示" in system


def test_the_negative_prompt_is_folded_into_the_body(env):
    system = start(env, negative_prompt="blurry, watermark")["messages"][0]["content"]
    assert "blurry, watermark" in system
    assert "ネガティブプロンプトを読まない" in system


def test_an_image_only_session_ignores_the_negative_prompt(env):
    system = start(env, mode="image_only", negative_prompt="blurry")["messages"][0][
        "content"
    ]
    assert "ユーザーの除外希望" not in system


def test_the_comfy_target_preference_is_injected(env, monkeypatch):
    monkeypatch.setattr(
        config,
        "_settings",
        Settings(
            grok_command="grok",
            grok_workdir=str(env.workdir),
            comfy_target="comfy_cloud",
        ),
    )
    system = start(env)["messages"][0]["content"]
    assert "この環境の接続先: `comfy_cloud`" in system


def test_the_selected_audio_category_is_spelled_out(env):
    system = start(
        env,
        mode="audio",
        audio_workflow="stable_audio_3_medium_base",
        audio_category="SFX",
    )["messages"][0]["content"]
    assert "選択済みのカテゴリ（`audio_category`）は **SFX**" in system


def test_audio_values_the_model_does_not_read_are_left_out(env):
    system = start(
        env,
        mode="audio",
        audio_workflow="minimax_music_3",
        audio_category="SFX",
    )["messages"][0]["content"]
    assert "`audio_category`" not in system


def test_reference_tags_number_audio_after_the_videos():
    from app.models import ChatReference
    from app.prompts import reference_tags

    references = [
        ChatReference(kind="image", filename="a.png"),
        ChatReference(kind="video", filename="b.mp4"),
        ChatReference(kind="video", filename="c.mp4"),
        ChatReference(kind="audio", filename="d.wav"),
    ]
    assert reference_tags(references) == [
        "<Picture 1>",
        "<Video 1>",
        "<Video 2>",
        "<Audio 3>",
    ]
