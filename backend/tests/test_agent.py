"""Agent mode tests (AGENT-MODE §4 / §5). Grok と ComfyUI は完全にモックする。"""

import asyncio
import io
import json
import shutil
import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import (
    acp,
    agent_protocol,
    agent_runner,
    agent_store,
    autotag,
    comfy,
    config,
    db,
    grok,
    jobs,
    library,
    lora_samples,
    model_sources,
    prompts,
    nsfw,
    sheets,
)
from app.main import app
from app.models import AgentPlan, AgentSession, AgentTask, Settings
from app.routers import assets as assets_router
from app.workflows import DEFAULT_VIDEO_WORKFLOW, video_specs

from conftest import fake_outputs

HAS_FFMPEG = shutil.which("ffmpeg") is not None
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg is not installed")


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------

class FakeCli:
    """Replays scripted `grok -p` answers (test_chat.py と同じ方式)."""

    def __init__(self, answers=()):
        self.answers = list(answers)
        self.calls: list[list[str]] = []
        self.cwds: list[str] = []

    async def __call__(self, argv, cwd, timeout):
        self.calls.append(list(argv))
        self.cwds.append(str(cwd))
        if not self.answers:
            raise AssertionError("fake grok CLI ran out of scripted answers")
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return (0, answer, "")

    @property
    def prompts(self) -> list[str]:
        return [argv[argv.index("-p") + 1] for argv in self.calls if "-p" in argv]


class FakeComfy:
    """test_jobs.py の FakeComfy を最小化したもの。"""

    def __init__(self, video: Path | None):
        self.video = video
        self.uploads: list[str] = []
        self.queued: list[dict] = []
        self.history_calls = 0
        self.fail = False
        self.outputs = fake_outputs()

    async def upload_file(self, path, subfolder=None):
        self.uploads.append(str(path))
        return Path(path).name

    async def queue_prompt(self, workflow, client_id):
        if self.fail:
            raise comfy.ComfyError("ComfyUI is down")
        self.queued.append(workflow)
        return f"prompt-{len(self.queued)}"

    @property
    def job_count(self) -> int:
        """How many *jobs* were submitted (a full job is 2 ComfyUI prompts)."""
        seen: set[str] = set()
        for workflow in self.queued:
            for node in workflow.values():
                prefix = (node.get("inputs") or {}).get("filename_prefix")
                if isinstance(prefix, str) and "/" in prefix:
                    seen.add(prefix.split("/", 1)[1])
        return len(seen)

    async def get_history(self, prompt_id):
        self.history_calls += 1
        return {"status": {"status_str": "success"}, "outputs": self.outputs}

    async def download_view(self, filename, subfolder, type_, dest_path):
        dest = Path(dest_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if filename.endswith(".mp4") and self.video is not None:
            shutil.copy2(self.video, dest)
        else:
            dest.write_bytes(b"fake-image")
        return dest

    async def get_object_info(self):
        raise comfy.ComfyError("ComfyUI is not reachable")

    def ws_url(self, client_id):
        return f"ws://127.0.0.1:1/ws?clientId={client_id}"


@pytest.fixture(scope="session")
def sample_video(tmp_path_factory) -> Path:
    if not HAS_FFMPEG:
        pytest.skip("ffmpeg is not installed")
    dest = tmp_path_factory.mktemp("media") / "sample.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi",
         "-i", "testsrc=size=64x64:rate=25:duration=3",
         "-pix_fmt", "yuv420p", str(dest)],
        check=True,
        capture_output=True,
    )
    return dest


async def _no_llm(text: str) -> None:
    """NSFW 判定の LLM を使わない差し替え（scripted answer を消費させない）。"""
    return None


async def _no_description(text: str) -> tuple[str, list[str]]:
    """タグ自動生成の LLM を使わない差し替え。"""
    return "", []


@pytest.fixture
def env(tmp_path, monkeypatch, request):
    video = request.getfixturevalue("sample_video") if HAS_FFMPEG else None

    assets = tmp_path / "assets"
    outputs = tmp_path / "outputs"
    sessions = tmp_path / "agent-sessions"
    lib = tmp_path / "library"
    (assets / "audio").mkdir(parents=True)
    (assets / "image").mkdir(parents=True)
    (assets / "video").mkdir(parents=True)
    outputs.mkdir()
    sessions.mkdir()
    lib.mkdir()

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(library, "LIBRARY_DIR", lib)
    monkeypatch.setattr(jobs, "LIBRARY_DIR", lib)
    monkeypatch.setattr(jobs, "ASSETS_DIR", assets)
    monkeypatch.setattr(jobs, "OUTPUTS_DIR", outputs)
    monkeypatch.setattr(jobs, "POLL_INTERVAL", 0.02)
    monkeypatch.setattr(assets_router, "ASSETS_DIR", assets)
    monkeypatch.setattr(lora_samples, "ASSETS_DIR", assets)
    monkeypatch.setattr(agent_store, "AGENT_SESSIONS_DIR", sessions)
    monkeypatch.setattr(agent_runner, "POLL_INTERVAL", 0.02)
    # NSFW 自動判定は test_nsfw.py で検証する（ここでは Grok を呼ばせない）。
    monkeypatch.setattr(nsfw, "classify", _no_llm)
    # ライブラリのタグ自動生成も同様（test_autotag.py で検証する）。scripted な
    # 答えを背景タスクに横取りされないよう、既定では何も返さない。
    monkeypatch.setattr(autotag, "describe", _no_description)
    monkeypatch.setattr(
        config,
        "_settings",
        Settings(
            grok_command="grok",
            grok_model="grok-4.5",
            grok_workdir=str(tmp_path),
            # ACP はプロセスを本当に起動するので、ここでは FakeCli の通る
            # ワンショット実行に固定する（ACP 自体は test_acp.py で検証）。
            agent_use_acp=False,
        ),
    )

    fake_comfy = FakeComfy(video)
    for name in ("upload_file", "queue_prompt", "get_history", "download_view",
                 "ws_url", "get_object_info"):
        monkeypatch.setattr(comfy, name, getattr(fake_comfy, name))

    cli = FakeCli()
    monkeypatch.setattr(grok, "_exec", cli)

    audio = assets / "audio" / "ref.mp3"
    audio.write_bytes(b"ID3")
    # ワークフロー別の必要入力（end_image / reference_video）を試すための素材
    image = assets / "image" / "frame.png"
    image.write_bytes(b"\x89PNG fake")
    clip = assets / "video" / "ref.mp4"
    clip.write_bytes(b"\x00\x00\x00\x18ftypmp42")

    with TestClient(app) as client:
        yield type(
            "Env",
            (),
            {
                "client": client,
                "cli": cli,
                "comfy": fake_comfy,
                "assets": assets,
                "outputs": outputs,
                "sessions": sessions,
                "library": lib,
                "audio": audio,
                "image": image,
                "clip": clip,
            },
        )


# --------------------------------------------------------------------------
# scripted Grok answers
# --------------------------------------------------------------------------

def job_body(env, **overrides) -> dict:
    body = {
        "mode": "full",
        "image_prompt": "an image",
        "video_prompt": "a video",
        "negative_prompt": "bad",
        "aspect_ratio": "9:16",
        "megapixels": 1.0,
        "loras": [],
        "trigger_text": "",
        "duration": 2,
        "fps": 25,
        "audio_path": str(env.audio),
        "source_image": None,
        "seed": None,
    }
    body.update(overrides)
    return body


def plan_answer(env, count: int = 1) -> str:
    tasks = [
        {"label": f"①{i}", "job": job_body(env)} for i in range(1, count + 1)
    ]
    payload = {"action": "plan", "notes": f"{count}本の案です", "tasks": tasks}
    return "プランを作りました。\n\n```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```\n"


def action_answer(payload: dict, text: str = "了解しました。") -> str:
    return f"{text}\n\n```json\n{json.dumps(payload, ensure_ascii=False)}\n```\n"


DONE_ANSWER = action_answer({"action": "done", "summary": "3本納品しました"}, "完了です。")


def start(env, **overrides) -> dict:
    body = {"checkin_mode": "milestone", "auto_limit": 5, "goal": "ダンス動画を作りたい"}
    body.update(overrides)
    response = env.client.post("/api/agent/sessions", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def say(env, session_id: str, content: str = "3本つくって"):
    return env.client.post(
        f"/api/agent/sessions/{session_id}/messages", json={"content": content}
    )


def wait_status(env, session_id: str, statuses, timeout: float = 30.0) -> dict:
    deadline = time.time() + timeout
    session = {}
    while time.time() < deadline:
        session = env.client.get(f"/api/agent/sessions/{session_id}").json()
        if session["status"] in statuses:
            return session
        time.sleep(0.05)
    raise AssertionError(f"session stuck in {session.get('status')!r}")


def kinds(session: dict) -> list[str]:
    return [m["kind"] for m in session["messages"] if m["kind"]]


# --------------------------------------------------------------------------
# protocol (AGENT-MODE §4)
# --------------------------------------------------------------------------

def test_parse_plan_action(env):
    action = agent_protocol.parse_action(plan_answer(env, 2))
    assert action is not None
    assert action.action == "plan"
    assert len(action.tasks) == 2
    assert action.tasks[0].label == "①1"
    assert action.tasks[0].job["audio_path"] == str(env.audio)
    assert action.tasks[0].id  # 生成された task id


def test_plain_text_has_no_action(env):
    assert agent_protocol.parse_action("どんな雰囲気にしますか？") is None


@pytest.mark.parametrize(
    "payload, needle",
    [
        ({"action": "fly"}, "未知の action"),
        ({"action": "plan", "tasks": []}, "1 件以上"),
        ({"action": "continue"}, "job_id"),
        ({"action": "inspect"}, "job_id"),
        ({"action": "checkin"}, "question"),
        ({"action": "note"}, "filename"),
    ],
)
def test_invalid_actions_raise(env, payload, needle):
    with pytest.raises(agent_protocol.ActionError, match=needle):
        agent_protocol.parse_action(action_answer(payload))


def test_plan_job_goes_through_the_existing_validation(env):
    broken = {
        "action": "plan",
        "tasks": [{"label": "x", "job": job_body(env, image_prompt="")}],
    }
    with pytest.raises(agent_protocol.ActionError, match="image_prompt"):
        agent_protocol.parse_action(action_answer(broken))

    stray = {
        "action": "plan",
        "tasks": [{"label": "x", "job": job_body(env, audio_path="/etc/passwd")}],
    }
    with pytest.raises(agent_protocol.ActionError, match="audio_path"):
        agent_protocol.parse_action(action_answer(stray))


KNOWN_LORAS = {"kaori.safetensors": "image", "motion.safetensors": "video"}


def _plan_with(env, **job_overrides) -> str:
    return action_answer(
        {
            "action": "plan",
            "tasks": [{"label": "x", "job": job_body(env, **job_overrides)}],
        }
    )


def test_unknown_lora_is_rejected(env):
    answer = _plan_with(
        env, loras=[{"lora_name": "ghost.safetensors", "trigger_word": "g"}]
    )
    with pytest.raises(agent_protocol.ActionError, match="存在しない LoRA"):
        agent_protocol.parse_action(answer, known_loras=KNOWN_LORAS)


def test_video_loras_need_a_workflow_that_can_take_them(env):
    """今ある動画ワークフローは LoRA チェーンを持たないので、指定は弾かれる。"""
    answer = _plan_with(
        env, video_loras=[{"lora_name": "motion.safetensors", "trigger_word": "slowmo"}]
    )
    with pytest.raises(agent_protocol.ActionError, match="video LoRAs"):
        agent_protocol.parse_action(answer, known_loras=KNOWN_LORAS)


def test_a_video_lora_cannot_be_used_as_an_image_lora(env):
    answer = _plan_with(
        env, loras=[{"lora_name": "motion.safetensors", "trigger_word": "slowmo"}]
    )
    with pytest.raises(agent_protocol.ActionError, match="`loras` には指定できません"):
        agent_protocol.parse_action(answer, known_loras=KNOWN_LORAS)


def test_loras_matching_their_target_are_accepted(env):
    answer = _plan_with(
        env,
        loras=[{"lora_name": "kaori.safetensors", "trigger_word": "kaori"}],
    )
    action = agent_protocol.parse_action(answer, known_loras=KNOWN_LORAS)
    assert action is not None
    job = action.tasks[0].job
    assert job["loras"][0]["lora_name"] == "kaori.safetensors"


def _plan(env, **job_overrides) -> str:
    payload = {
        "action": "plan",
        "tasks": [{"label": "x", "job": job_body(env, **job_overrides)}],
    }
    return action_answer(payload)


# --- ワークフロー選択 (SPEC §3 / AGENT-MODE §3.1) ---------------------------

def test_plan_can_select_another_video_workflow(env):
    action = agent_protocol.parse_action(
        _plan(
            env,
            mode="i2v",
            video_workflow="minimax_h3_i2v",
            source_image=str(env.image),
            end_image=str(env.image),
            audio_path=None,
        )
    )
    job = action.tasks[0].job
    assert job["video_workflow"] == "minimax_h3_i2v"
    assert job["end_image"] == str(env.image)


def test_a_missing_workflow_input_names_the_workflow(env):
    """i2v の source_image 不足は plan 検証で弾き、ワークフロー名込みで説明する。"""
    with pytest.raises(agent_protocol.ActionError) as excinfo:
        agent_protocol.parse_action(
            _plan(
                env,
                mode="i2v",
                video_workflow="minimax_h3_i2v",
                audio_path=None,
            )
        )
    message = str(excinfo.value)
    assert "source_image" in message
    assert "minimax_h3_i2v" in message


# --------------------------------------------------------------------------
# ジョブ単位のモデル切り替え（SPEC §3.3）
# --------------------------------------------------------------------------

IMAGE_SLOT = "krea2_turbo/30:10.unet_name"
VIDEO_SLOT = "minimax_h3_i2v/105:6.unet_name"


def _register_choices(monkeypatch, choices: dict[str, list[str]]) -> None:
    monkeypatch.setattr(
        config,
        "_settings",
        # 候補リストは接続先ごと（SPEC §5）。既定の 'local' 環境に入れる。
        config.load_settings().model_copy(
            update={"comfy_target": "local", "model_choices": {"local": choices}}
        ),
    )


def test_a_registered_model_choice_is_accepted(env, monkeypatch):
    _register_choices(monkeypatch, {IMAGE_SLOT: ["alt.safetensors"]})
    action = agent_protocol.parse_action(
        _plan_with(env, model_overrides={IMAGE_SLOT: "alt.safetensors"})
    )
    assert action is not None
    assert action.tasks[0].job["model_overrides"] == {IMAGE_SLOT: "alt.safetensors"}


def test_a_model_outside_the_choices_is_rejected(env, monkeypatch):
    _register_choices(monkeypatch, {IMAGE_SLOT: ["alt.safetensors"]})
    with pytest.raises(agent_protocol.ActionError) as excinfo:
        agent_protocol.parse_action(
            _plan_with(env, model_overrides={IMAGE_SLOT: "ghost.safetensors"})
        )
    assert "ghost.safetensors" in str(excinfo.value)
    assert "MODEL CHOICES" in str(excinfo.value)


def test_a_model_slot_of_a_workflow_the_job_skips_is_rejected(env, monkeypatch):
    """画像のみのジョブに動画スロットを混ぜたら、実行前に弾く。"""
    _register_choices(monkeypatch, {VIDEO_SLOT: ["alt.safetensors"]})
    with pytest.raises(agent_protocol.ActionError) as excinfo:
        agent_protocol.parse_action(
            _plan_with(
                env,
                mode="image_only",
                video_prompt="",
                audio_path=None,
                model_overrides={VIDEO_SLOT: "alt.safetensors"},
            )
        )
    assert "minimax_h3_i2v" in str(excinfo.value)


def test_system_prompt_lists_the_model_choices(env, monkeypatch):
    _register_choices(monkeypatch, {IMAGE_SLOT: ["alt.safetensors"]})
    system = start(env)["messages"][0]["content"]
    assert "使用モデルの切り替え" in system
    assert f"`{IMAGE_SLOT}`" in system
    assert "`alt.safetensors`" in system


def test_system_prompt_says_no_model_choice_is_registered(env):
    system = start(env)["messages"][0]["content"]
    assert "使用モデルの切り替え候補は登録されていません" in system
    assert "`model_overrides` は指定しないでください" in system


# --------------------------------------------------------------------------
# 取得元ページ（AGENT-MODE §3.1）
# --------------------------------------------------------------------------

HF_LORA_URL = "https://huggingface.co/org/sakura-lora/resolve/main/sakura.safetensors"


def _register_urls(monkeypatch, urls: dict[str, str]) -> None:
    monkeypatch.setattr(
        config,
        "_settings",
        config.load_settings().model_copy(update={"model_download_urls": urls}),
    )


def test_system_prompt_lists_the_source_page_of_a_lora(env, monkeypatch):
    """LoRA の取得元 URL は「調べに行ける配布ページ」として焼き込む。"""
    assert env.client.post(
        "/api/loras",
        json={"display_name": "サクラ", "lora_name": "sakura.safetensors",
              "trigger_word": "sakura"},
    ).status_code == 201
    _register_urls(monkeypatch, {"sakura.safetensors": HF_LORA_URL})

    system = start(env)["messages"][0]["content"]
    assert "# MODEL SOURCES" in system
    assert "page: https://huggingface.co/org/sakura-lora" in system
    assert f"download: {HF_LORA_URL}" in system
    assert "トリガーワード" in system


def test_system_prompt_has_no_sources_section_without_urls(env):
    assert "# MODEL SOURCES" not in start(env)["messages"][0]["content"]


def test_a_source_without_a_known_page_shows_only_the_download_url(env, monkeypatch):
    url = "https://example.com/files/sakura.safetensors"
    assert env.client.post(
        "/api/loras",
        json={"display_name": "サクラ", "lora_name": "sakura.safetensors",
              "trigger_word": "sakura"},
    ).status_code == 201
    _register_urls(monkeypatch, {"sakura.safetensors": url})

    system = start(env)["messages"][0]["content"]
    assert f"download: {url}" in system
    assert "page: " not in system


def test_a_session_still_starts_when_the_sources_cannot_be_resolved(env, monkeypatch):
    """取得元の解決（Civitai API）が落ちてもセッション作成は止めない。"""
    async def boom(options):
        raise RuntimeError("civitai down")

    monkeypatch.setattr(model_sources, "collect", boom)
    system = start(env)["messages"][0]["content"]
    assert "# MODEL SOURCES" not in system


def test_the_default_workflow_requires_its_start_frame(env):
    """既定 (i2v) の開始フレーム必須はワークフロー由来として報告される。"""
    with pytest.raises(agent_protocol.ActionError) as excinfo:
        agent_protocol.parse_action(_plan(env, mode="i2v", audio_path=None))
    assert "source_image" in str(excinfo.value)
    assert "minimax_h3_i2v" in str(excinfo.value)


def test_a_workflow_without_a_start_frame_cannot_run_in_full_mode(env):
    with pytest.raises(agent_protocol.ActionError) as excinfo:
        agent_protocol.parse_action(
            _plan(env, mode="full", video_workflow="minimax_h3_t2v", audio_path=None)
        )
        # t2v は開始フレームを受け取れない -> full では使えない
    assert "minimax_h3_t2v" in str(excinfo.value)


def test_an_unknown_video_workflow_lists_the_real_ones(env):
    with pytest.raises(agent_protocol.ActionError) as excinfo:
        agent_protocol.parse_action(_plan(env, video_workflow="ghost_workflow"))
    message = str(excinfo.value)
    assert "ghost_workflow" in message
    assert "minimax_h3_i2v" in message


@pytest.mark.parametrize(
    "field, workflow",
    [
        ("end_image", "minimax_h3_i2v"),
    ],
)
def test_stray_workflow_assets_are_rejected(env, field, workflow):
    """end_image も assets 配下の実在チェックを通す。"""
    overrides = {
        "mode": "i2v",
        "video_workflow": workflow,
        "source_image": str(env.image),
        "audio_path": None,
        field: "/etc/passwd",
    }
    with pytest.raises(agent_protocol.ActionError, match=field):
        agent_protocol.parse_action(_plan(env, **overrides))


def test_image_only_ignores_the_video_workflow(env):
    action = agent_protocol.parse_action(
        _plan(env, mode="image_only", video_workflow="minimax_h3_t2v", audio_path=None)
    )
    assert action.tasks[0].job["mode"] == "image_only"


def test_plan_over_the_task_limit_is_rejected(env):
    with pytest.raises(agent_protocol.ActionError, match="最大"):
        agent_protocol.parse_action(plan_answer(env, 3), max_tasks=2)


def test_plan_is_unlimited_without_a_max(env):
    """上限を渡さなければ（毎ジョブ確認 / 節目のみ確認）タスク数は自由。"""
    action = agent_protocol.parse_action(plan_answer(env, 8))
    assert len(action.tasks) == 8


def test_completed_tasks_do_not_count_against_the_limit(env):
    """完了済みタスクの再掲は新規ジョブとして数えない（改訂プランは全置き換え）。"""
    action = agent_protocol.parse_action(plan_answer(env, 7), max_tasks=2, done_tasks=5)
    assert len(action.tasks) == 7
    with pytest.raises(agent_protocol.ActionError, match="完了済み"):
        agent_protocol.parse_action(plan_answer(env, 8), max_tasks=2, done_tasks=5)


@pytest.mark.parametrize(
    "mode, expected",
    [("every_job", (None, 0)), ("milestone", (None, 0)), ("auto", (5, 1))],
)
def test_plan_task_limits_apply_to_auto_only(mode, expected):
    session = AgentSession(
        id="s1",
        created_at="2024-01-01T00:00:00Z",
        checkin_mode=mode,
        plan=AgentPlan(
            tasks=[
                AgentTask(id="t1", label="done", status="done"),
                AgentTask(id="t2", label="pending"),
            ]
        ),
    )
    assert agent_runner.plan_task_limits(session) == expected


def test_long_plan_passes_in_a_non_auto_session(env):
    """節目のみ確認では上限なし（承認 + チェックインで人間が必ず挟まる）。"""
    session = start(env, checkin_mode="milestone")
    env.cli.answers = [plan_answer(env, 8)]
    reply = say(env, session["id"]).json()
    assert len(reply["action"]["tasks"]) == 8
    assert reply["session"]["status"] == "planning"


def test_auto_session_rejects_too_many_new_tasks(env):
    """自走モードは 1 回のプラン提案で新規 5 件まで（既定）。"""
    session = start(env, checkin_mode="auto", auto_limit=20)
    env.cli.answers = [plan_answer(env, 6), plan_answer(env, 6)]
    reply = say(env, session["id"]).json()
    assert reply["action"] is None
    assert "最大 5 件" in reply["session"]["messages"][-1]["content"]


def test_unusable_action_triggers_one_retry(env):
    session = start(env)
    env.cli.answers = [
        action_answer({"action": "plan", "tasks": []}),  # 不正
        plan_answer(env, 1),  # リマインダー後の再送
    ]
    reply = say(env, session["id"]).json()
    assert reply["action"]["action"] == "plan"
    assert len(env.cli.calls) == 2
    assert "could not be used" in env.cli.prompts[-1]
    assert reply["session"]["status"] == "planning"


def test_retry_that_keeps_failing_is_reported_as_an_event(env):
    session = start(env)
    env.cli.answers = [
        action_answer({"action": "plan", "tasks": []}),
        action_answer({"action": "plan", "tasks": []}),
    ]
    reply = say(env, session["id"]).json()
    assert reply["action"] is None
    assert "action_invalid" in kinds(reply["session"])
    assert len(env.cli.calls) == 2  # 再試行は 1 回だけ


# --------------------------------------------------------------------------
# session lifecycle (AGENT-MODE §5.1)
# --------------------------------------------------------------------------

def test_session_starts_with_a_system_prompt_and_a_workdir(env):
    session = start(env, title="ダンス")
    assert session["status"] == "idle"
    assert session["title"] == "ダンス"
    system = session["messages"][0]
    assert system["role"] == "system"
    assert "# ACTION PROTOCOL" in system["content"]
    assert str(env.audio) in system["content"]  # 選択肢を焼き込む
    assert "check-in mode" in system["content"].lower()
    assert (env.sessions / session["id"]).is_dir()
    # goal はシステムプロンプトに焼き込まれる（発言は最初の /messages が作る）
    assert "ダンス動画を作りたい" in system["content"]
    assert len(session["messages"]) == 1

    listing = env.client.get("/api/agent/sessions").json()
    assert [s["id"] for s in listing] == [session["id"]]
    assert listing[0]["task_count"] == 0


def test_system_prompt_carries_the_workflow_catalog(env):
    """全ワークフローの用途・必要入力・必須フィールドが焼き込まれる（§3.1）。"""
    system = start(env)["messages"][0]["content"]
    assert "# VIDEO WORKFLOWS" in system
    for spec in video_specs():
        assert f"`{spec.id}`" in system
        assert spec.description in system
        assert spec.prompt_hint in system
    # ワークフロー別の必須フィールドは missing_job_fields 由来
    assert "`video_prompt`, `source_image`" in system
    assert f"selects `{DEFAULT_VIDEO_WORKFLOW}`" in system


def test_system_prompt_lists_the_video_assets(env):
    """reference_video に使える動画アセットも選択肢として出す。"""
    system = start(env)["messages"][0]["content"]
    assert "Video assets (reference_video)" in system
    assert str(env.clip) in system
    assert "Image assets (source_image / end_image)" in system
    assert str(env.image) in system


def test_system_prompt_separates_image_and_video_loras(env):
    for payload in (
        {"display_name": "サクラ", "lora_name": "sakura.safetensors",
         "trigger_word": "sakura"},
        {"display_name": "スローモ", "lora_name": "motion.safetensors",
         "trigger_word": "slowmo", "target": "video"},
    ):
        assert env.client.post("/api/loras", json=payload).status_code == 201

    system = start(env)["messages"][0]["content"]
    image_section = system.index("画像用 LoRA")
    video_section = system.index("動画用 LoRA")
    assert image_section < video_section
    # each file name is listed under its own heading
    assert 0 < system.index("`sakura.safetensors`") - image_section < (
        video_section - image_section
    )
    assert system.index("`motion.safetensors`") > video_section
    assert "`video_loras`" in system
    assert "入れ替えられません" in system


def test_system_prompt_says_which_lists_are_empty(env):
    system = start(env)["messages"][0]["content"]
    assert "leave `loras` and `video_loras`" in system


def test_known_lora_names_carry_the_target(env):
    env.client.post(
        "/api/loras",
        json={"display_name": "スローモ", "lora_name": "motion.safetensors",
              "trigger_word": "slowmo", "target": "video"},
    )
    known = asyncio.run(agent_runner.known_lora_names())
    assert known == {"motion.safetensors": "video"}


def test_session_copies_lora_samples_into_the_workdir(env):
    """LoRA のサンプル画像は workdir へコピーされ、参照指示が焼き込まれる。"""
    lora = env.client.post(
        "/api/loras",
        json={
            "display_name": "サクラ",
            "lora_name": "sakura.safetensors",
            "trigger_word": "sakura",
        },
    ).json()
    uploaded = env.client.post(
        f"/api/loras/{lora['id']}/samples",
        files={"file": ("face.png", b"\x89PNG fake", "image/png")},
    )
    assert uploaded.status_code == 201, uploaded.text
    assert len(uploaded.json()["sample_images"]) == 1

    session = start(env)
    workdir = env.sessions / session["id"]
    copies = list((workdir / "lora_samples" / "sakura").glob("*.png"))
    assert len(copies) == 1
    assert copies[0].read_bytes() == b"\x89PNG fake"

    system = session["messages"][0]["content"]
    assert f"reference image: `lora_samples/sakura/{copies[0].name}`" in system
    assert "compare the output" in system


def test_session_without_samples_has_no_reference_section(env):
    env.client.post(
        "/api/loras",
        json={
            "display_name": "サクラ",
            "lora_name": "sakura.safetensors",
            "trigger_word": "sakura",
        },
    )
    session = start(env)
    system = session["messages"][0]["content"]
    assert "reference image" not in system
    assert not (env.sessions / session["id"] / "lora_samples").exists()


def test_missing_session_is_404(env):
    assert env.client.get("/api/agent/sessions/nope").status_code == 404
    assert say(env, "nope").status_code == 404
    assert env.client.post("/api/agent/sessions/nope/stop").status_code == 404


def test_empty_message_is_422(env):
    session = start(env)
    assert say(env, session["id"], "  ").status_code == 422


def test_delete_removes_the_workdir(env):
    session = start(env)
    workdir = env.sessions / session["id"]
    (workdir / "memo.md").write_text("hi", encoding="utf-8")
    assert env.client.delete(f"/api/agent/sessions/{session['id']}").status_code == 204
    assert not workdir.exists()
    assert env.client.delete(f"/api/agent/sessions/{session['id']}").status_code == 404


# --------------------------------------------------------------------------
# plan -> approve -> run -> done (AGENT-MODE §5.3)
# --------------------------------------------------------------------------

def test_plan_approve_run_done(env):
    session = start(env)
    env.cli.answers = [plan_answer(env, 1), DONE_ANSWER]

    reply = say(env, session["id"]).json()
    assert reply["action"]["action"] == "plan"
    planning = reply["session"]
    assert planning["status"] == "planning"
    assert planning["plan"]["version"] == 1
    assert planning["plan"]["approved"] is False
    assert len(planning["plan"]["tasks"]) == 1
    assert not env.comfy.queued  # 承認前は 1 本も生成しない

    approved = env.client.post(f"/api/agent/sessions/{session['id']}/approve", json={})
    assert approved.status_code == 200, approved.text

    final = wait_status(env, session["id"], ("done", "stopped", "idle"))
    assert final["status"] == "done"
    assert final["plan"]["tasks"][0]["status"] == "done"
    assert final["plan"]["tasks"][0]["job_id"]
    assert kinds(final) == [
        "plan_proposed", "job_started", "job_done", "done"
    ]
    assert env.comfy.job_count == 1

    # 生成物は成果物として登録され、ジョブはセッションに紐付く
    artifact_kinds = {a["kind"] for a in final["artifacts"]}
    assert "plan" in artifact_kinds and "image" in artifact_kinds
    job_id = final["plan"]["tasks"][0]["job_id"]
    job = env.client.get(f"/api/jobs/{job_id}").json()
    assert job["status"] == "done"


def test_approve_without_a_plan_is_422(env):
    session = start(env)
    response = env.client.post(f"/api/agent/sessions/{session['id']}/approve", json={})
    assert response.status_code == 422


def test_failed_job_is_retried_once(env):
    env.comfy.fail = True
    session = start(env)
    env.cli.answers = [
        plan_answer(env, 1),
        action_answer({"action": "note", "content": "様子を見ます"}, "失敗しました"),
        DONE_ANSWER,
    ]
    say(env, session["id"])
    env.client.post(f"/api/agent/sessions/{session['id']}/approve", json={})

    final = wait_status(env, session["id"], ("done", "stopped", "idle"))
    event_kinds = kinds(final)
    assert event_kinds.count("job_failed") == 2  # 初回 + 自動リトライ 1 回
    assert "task_retry" in event_kinds
    assert final["plan"]["tasks"][0]["status"] == "failed"


# --------------------------------------------------------------------------
# checkin / stop / limits (AGENT-MODE §2 / §7)
# --------------------------------------------------------------------------

CHECKIN_ANSWER = action_answer(
    {"action": "checkin", "question": "②の照明を変えますか？", "options": ["変える", "そのまま"]},
    "①が完了しました。",
)


def test_checkin_pauses_and_the_reply_resumes_the_loop(env):
    session = start(env)
    env.cli.answers = [plan_answer(env, 2), CHECKIN_ANSWER, DONE_ANSWER]
    say(env, session["id"])
    env.client.post(f"/api/agent/sessions/{session['id']}/approve", json={})

    paused = wait_status(env, session["id"], ("waiting_checkin", "stopped", "done"))
    assert paused["status"] == "waiting_checkin"
    assert paused["messages"][-1]["role"] == "checkin"
    assert paused["messages"][-1]["data"]["options"] == ["変える", "そのまま"]
    assert env.comfy.job_count == 1  # 2 本目は保留

    response = env.client.post(
        f"/api/agent/sessions/{session['id']}/checkin", json={"choice": "そのまま"}
    )
    assert response.status_code == 200, response.text
    final = wait_status(env, session["id"], ("done", "stopped", "idle"))
    assert final["status"] == "done"
    assert env.comfy.job_count == 2


def test_every_job_mode_checks_in_after_each_job(env):
    session = start(env, checkin_mode="every_job")
    env.cli.answers = [plan_answer(env, 2), action_answer({"action": "run_task"}, "続けます")]
    say(env, session["id"])
    env.client.post(f"/api/agent/sessions/{session['id']}/approve", json={})

    paused = wait_status(env, session["id"], ("waiting_checkin", "stopped", "done"))
    assert paused["status"] == "waiting_checkin"
    assert env.comfy.job_count == 1


def test_checkin_on_a_running_session_is_409(env):
    session = start(env)
    response = env.client.post(
        f"/api/agent/sessions/{session['id']}/checkin", json={"content": "はい"}
    )
    assert response.status_code == 409


def test_stop_halts_a_waiting_session(env):
    session = start(env)
    env.cli.answers = [plan_answer(env, 2), CHECKIN_ANSWER]
    say(env, session["id"])
    env.client.post(f"/api/agent/sessions/{session['id']}/approve", json={})
    wait_status(env, session["id"], ("waiting_checkin", "stopped", "done"))

    stopped = env.client.post(f"/api/agent/sessions/{session['id']}/stop").json()
    assert stopped["status"] == "stopped"
    assert env.comfy.job_count == 1


def _run_to_limit_checkin(env, tasks: int = 3):
    """auto_limit=1 のセッションを 1 本生成させ、上限チェックインで止める。"""
    session = start(env, checkin_mode="auto", auto_limit=1)
    env.cli.answers = [plan_answer(env, tasks), "1本目ができました。次に進みます。"]
    say(env, session["id"])
    env.client.post(f"/api/agent/sessions/{session['id']}/approve", json={})

    paused = wait_status(env, session["id"], ("waiting_checkin", "stopped", "done"))
    assert paused["status"] == "waiting_checkin"
    assert env.comfy.job_count == 1
    assert "limit_reached" in kinds(paused)
    assert "上限" in paused["messages"][-1]["content"]
    assert paused["messages"][-1]["role"] == "checkin"
    assert paused["plan"]["tasks"][1]["status"] == "pending"
    return session


def test_auto_limit_asks_before_going_over(env):
    _run_to_limit_checkin(env)


def test_auto_limit_checkin_approval_buys_another_round(env):
    session = _run_to_limit_checkin(env)
    env.cli.answers = ["2本目ができました。次に進みます。"]
    env.client.post(
        f"/api/agent/sessions/{session['id']}/checkin", json={"choice": "続ける"}
    )

    # 承認 1 回で auto_limit（1 本）ぶんだけ進み、次の上限でまた確認が入る。
    again = wait_status(env, session["id"], ("waiting_checkin", "stopped", "done"))
    assert again["status"] == "waiting_checkin"
    assert env.comfy.job_count == 2
    assert kinds(again).count("limit_reached") == 2
    assert again["plan"]["tasks"][2]["status"] == "pending"


def test_auto_limit_checkin_decline_stops_the_session(env):
    session = _run_to_limit_checkin(env)
    env.client.post(
        f"/api/agent/sessions/{session['id']}/checkin", json={"choice": "止める"}
    )

    final = wait_status(env, session["id"], ("stopped", "done", "idle"))
    assert final["status"] == "stopped"
    assert env.comfy.job_count == 1  # 追加生成なし
    assert "上限" in final["messages"][-1]["content"]
    assert final["plan"]["tasks"][1]["status"] == "pending"
    # 断った直後に Grok ターンを回さない（scripted answer を消費していない）
    assert not env.cli.answers


def test_turn_limit_stops_the_loop(env, monkeypatch):
    monkeypatch.setattr(agent_runner, "MAX_TURNS", 2)
    session = start(env)
    env.cli.answers = [plan_answer(env, 2)] + ["まだ考え中です。"] * 4
    say(env, session["id"])
    env.client.post(f"/api/agent/sessions/{session['id']}/approve", json={})

    final = wait_status(env, session["id"], ("stopped", "done", "idle"))
    assert final["status"] == "stopped"
    assert "連続ターン" in final["messages"][-1]["content"]


# --------------------------------------------------------------------------
# inspect / note / artifacts
# --------------------------------------------------------------------------

@needs_ffmpeg
def test_inspect_extracts_frames_into_the_workdir(env):
    session = start(env)
    env.cli.answers = [plan_answer(env, 1), "確認します。", DONE_ANSWER]
    say(env, session["id"])
    env.client.post(f"/api/agent/sessions/{session['id']}/approve", json={})
    done = wait_status(env, session["id"], ("done", "stopped", "idle"))
    job_id = done["plan"]["tasks"][0]["job_id"]

    env.cli.answers = [
        action_answer({"action": "inspect", "job_id": job_id, "interval": 1}, "検分します。"),
        DONE_ANSWER,
    ]
    say(env, session["id"], "1本目を検分して")
    final = wait_status(env, session["id"], ("done", "stopped", "idle"))

    frames = sorted((env.sessions / session["id"] / f"inspect_{job_id}").glob("*.png"))
    assert frames
    assert "inspect_result" in kinds(final)
    frame_artifacts = [a for a in final["artifacts"] if a["kind"] == "frame"]
    assert frame_artifacts
    # タイトルはタスクの label 基準（フロントは job ごとに 1 カードへまとめる）
    assert frame_artifacts[0]["title"] == "①1 フレーム検分 1"
    assert all(a["job_id"] == job_id for a in frame_artifacts)

    # 成果物は workdir から配信される
    served = env.client.get(frame_artifacts[0]["url"])
    assert served.status_code == 200
    assert served.content


def test_note_is_registered_as_an_artifact(env):
    session = start(env)
    env.cli.answers = [
        action_answer(
            {"action": "note", "title": "リサーチ", "content": "トレンドまとめ"},
            "調べました。",
        )
    ]
    reply = say(env, session["id"], "トレンドを調べて").json()
    notes = [a for a in reply["session"]["artifacts"] if a["kind"] == "note"]
    assert notes and notes[0]["title"] == "リサーチ"
    served = env.client.get(notes[0]["url"])
    assert served.status_code == 200
    assert "トレンドまとめ" in served.text


def test_note_kind_research_becomes_a_research_artifact(env):
    session = start(env)
    env.cli.answers = [
        action_answer(
            {
                "action": "note",
                "title": "トレンド調査",
                "kind": "research",
                "content": "Web検索のまとめ",
            },
            "調べました。",
        )
    ]
    reply = say(env, session["id"], "トレンドを調べて").json()
    assert reply["action"]["kind"] == "research"
    research = [a for a in reply["session"]["artifacts"] if a["kind"] == "research"]
    assert research and research[0]["title"] == "トレンド調査"
    assert env.client.get(research[0]["url"]).status_code == 200


def test_note_defaults_to_the_note_kind(env):
    action = agent_protocol.parse_action(
        action_answer({"action": "note", "content": "メモ"})
    )
    assert action is not None and action.kind == "note"


def test_rename_updates_an_artifact_title_by_name(env):
    session = start(env)
    env.cli.answers = [
        action_answer(
            {"action": "note", "title": "メモ1", "content": "下書き"}, "書きました。"
        ),
        action_answer(
            {
                "action": "rename",
                "name": "note_1.md",
                "title": "夕暮れ屋上ダンス・企画メモ",
            },
            "名前を付け直します。",
        ),
    ]
    say(env, session["id"], "メモして")
    reply = say(env, session["id"], "名前を付けて").json()

    assert reply["action"]["action"] == "rename"
    notes = [a for a in reply["session"]["artifacts"] if a["kind"] == "note"]
    assert notes and notes[0]["title"] == "夕暮れ屋上ダンス・企画メモ"
    assert "artifact_renamed" in kinds(reply["session"])


def test_rename_targets_a_jobs_artifacts_by_kind(env):
    session = start(env)
    env.cli.answers = [plan_answer(env, 1), DONE_ANSWER]
    say(env, session["id"])
    env.client.post(f"/api/agent/sessions/{session['id']}/approve", json={})
    done = wait_status(env, session["id"], ("done", "stopped", "idle"))
    job_id = done["plan"]["tasks"][0]["job_id"]
    before = [a for a in done["artifacts"] if a["kind"] == "image"]
    assert before

    env.cli.answers = [
        action_answer(
            {
                "action": "rename",
                "job_id": job_id,
                "kind": "image",
                "title": "夕暮れ屋上ダンス・引きカメラ",
            },
            "名前を付け直します。",
        )
    ]
    artifacts = say(env, session["id"], "名前を付けて").json()["session"]["artifacts"]
    images = [a for a in artifacts if a["kind"] == "image"]
    assert [a["title"] for a in images] == ["夕暮れ屋上ダンス・引きカメラ"]
    # 他の種別は触らない
    assert all(a["title"].startswith("プラン") for a in artifacts if a["kind"] == "plan")


def test_rename_of_a_missing_artifact_is_reported(env):
    session = start(env)
    env.cli.answers = [
        action_answer(
            {"action": "rename", "name": "nope.md", "title": "新しい名前"},
            "名前を付け直します。",
        )
    ]
    reply = say(env, session["id"], "名前を付けて").json()
    assert "action_failed" in kinds(reply["session"])
    assert reply["session"]["artifacts"] == []


def test_rename_without_a_target_or_title_is_rejected(env):
    with pytest.raises(agent_protocol.ActionError, match="title"):
        agent_protocol.parse_action(
            action_answer({"action": "rename", "name": "note_1.md"})
        )
    with pytest.raises(agent_protocol.ActionError, match="job_id"):
        agent_protocol.parse_action(
            action_answer({"action": "rename", "title": "新しい名前"})
        )


def test_rename_publishes_the_updated_artifact_on_the_websocket(env):
    session = start(env)
    env.cli.answers = [
        action_answer({"action": "note", "title": "メモ1", "content": "下書き"}),
        action_answer(
            {"action": "rename", "name": "note_1.md", "title": "夕暮れ屋上ダンス・企画メモ"}
        ),
    ]
    say(env, session["id"], "メモして")
    with env.client.websocket_connect("/api/ws") as socket:
        say(env, session["id"], "名前を付けて")
        titles = []
        for _ in range(20):
            message = socket.receive_json()
            if message["type"] == "agent" and message.get("artifact"):
                titles.append(message["artifact"]["title"])
                break
    assert titles == ["夕暮れ屋上ダンス・企画メモ"]


def test_artifact_path_traversal_is_refused(env, tmp_path):
    session = start(env)
    secret = tmp_path / "secret.txt"
    secret.write_text("top secret", encoding="utf-8")
    (env.sessions / session["id"] / "memo.md").write_text("ok", encoding="utf-8")

    assert agent_store.artifact_path(session["id"], "memo.md") is not None
    for bad in ("../secret.txt", "../../secret.txt", "/etc/passwd", ""):
        assert agent_store.artifact_path(session["id"], bad) is None

    base = f"/api/agent/sessions/{session['id']}/artifacts"
    assert env.client.get(f"{base}/memo.md").status_code == 200
    assert env.client.get(f"{base}/..%2Fsecret.txt").status_code == 404
    assert env.client.get(f"{base}/nope.txt").status_code == 404


# --------------------------------------------------------------------------
# attachments
# --------------------------------------------------------------------------

def attach(env, session_id: str, name: str = "photo.png", body: bytes = b"\x89PNG"):
    return env.client.post(
        f"/api/agent/sessions/{session_id}/attachments",
        files={"file": (name, body, "application/octet-stream")},
    )


def test_attachment_upload_lands_in_the_workdir(env):
    session = start(env)
    response = attach(env, session["id"], "my photo.png")
    assert response.status_code == 201, response.text
    body = response.json()

    assert body["path"] == f"attachments/{body['name']}"
    assert body["name"].startswith("my_photo_") and body["name"].endswith(".png")
    saved = env.sessions / session["id"] / "attachments" / body["name"]
    assert saved.read_bytes() == b"\x89PNG"
    # 既存の artifacts 配信でそのままプレビューできる
    served = env.client.get(
        f"/api/agent/sessions/{session['id']}/artifacts/{body['path']}"
    )
    assert served.status_code == 200


def test_attachment_upload_refuses_unknown_extensions(env):
    session = start(env)
    assert attach(env, session["id"], "evil.exe").status_code == 400


def test_attachment_upload_on_a_missing_session_is_404(env):
    assert attach(env, "nope").status_code == 404


def test_message_embeds_attachment_paths_for_the_agent(env):
    session = start(env)
    uploaded = attach(env, session["id"]).json()
    env.cli.answers = ["拝見しました。"]

    response = env.client.post(
        f"/api/agent/sessions/{session['id']}/messages",
        json={"content": "この写真に寄せて", "attachments": [uploaded["path"]]},
    )
    assert response.status_code == 200, response.text

    users = [m for m in response.json()["session"]["messages"] if m["role"] == "user"]
    assert users[-1]["content"].startswith("この写真に寄せて")
    assert uploaded["path"] in users[-1]["content"]
    # UI 用: 添付とユーザー本文は data に分けて残す
    assert users[-1]["data"]["attachments"] == [uploaded["path"]]
    assert users[-1]["data"]["text"] == "この写真に寄せて"
    # Grok へ渡すプロンプトにもパスが載る
    assert uploaded["path"] in env.cli.prompts[-1]


def test_message_can_be_attachments_only(env):
    session = start(env)
    uploaded = attach(env, session["id"], "notes.txt", b"hello").json()
    env.cli.answers = ["読みました。"]

    response = env.client.post(
        f"/api/agent/sessions/{session['id']}/messages",
        json={"content": "", "attachments": [uploaded["path"]]},
    )
    assert response.status_code == 200, response.text
    users = [m for m in response.json()["session"]["messages"] if m["role"] == "user"]
    assert users[-1]["content"].startswith(
        "[Attached files — open them from your working directory to inspect]"
    )
    assert uploaded["path"] in users[-1]["content"]


def test_message_without_content_or_attachments_is_refused(env):
    session = start(env)
    response = env.client.post(
        f"/api/agent/sessions/{session['id']}/messages",
        json={"content": "  ", "attachments": []},
    )
    assert response.status_code == 422


def test_message_refuses_attachments_outside_the_attachments_dir(env):
    session = start(env)
    (env.sessions / session["id"] / "memo.md").write_text("ok", encoding="utf-8")
    bad_paths = [
        "attachments/../memo.md",
        "attachments/nope.png",
        "memo.md",
        "/etc/passwd",
    ]
    for bad in bad_paths:
        response = env.client.post(
            f"/api/agent/sessions/{session['id']}/messages",
            json={"content": "見て", "attachments": [bad]},
        )
        assert response.status_code == 400, bad
    assert env.cli.calls == []


# --------------------------------------------------------------------------
# continue / rerun (既存 jobs.py の再利用)
# --------------------------------------------------------------------------

@needs_ffmpeg
def test_continue_reuses_the_existing_job_service(env):
    # 自走モード: プラン外 continue も承認ゲートなしで走る
    session = start(env, checkin_mode="auto")
    job_id = _first_job(env, session["id"])

    env.cli.answers = [
        action_answer(
            {"action": "continue", "job_id": job_id, "video_prompt": "she keeps dancing"},
            "続きを作ります。",
        ),
        DONE_ANSWER,
    ]
    say(env, session["id"], "続きを作って")
    wait_status(env, session["id"], ("done", "stopped", "idle"))

    listing = env.client.get("/api/jobs").json()
    chained = [j for j in listing if j["params"].get("continued_from") == job_id]
    assert len(chained) == 1
    assert chained[0]["mode"] == "i2v"
    assert chained[0]["video_prompt"] == "she keeps dancing"


@needs_ffmpeg
def test_continue_can_switch_the_video_workflow(env):
    """continue でもワークフローと追加入力（end_image）を指定できる。"""
    session = start(env, checkin_mode="auto")
    job_id = _first_job(env, session["id"])

    env.cli.answers = [
        action_answer(
            {
                "action": "continue",
                "job_id": job_id,
                "video_workflow": "minimax_h3_i2v",
                "end_image": str(env.image),
                "video_prompt": "she reaches the door",
            },
            "最後のフレームまで繋ぎます。",
        ),
        DONE_ANSWER,
    ]
    say(env, session["id"], "続きを作って")
    wait_status(env, session["id"], ("done", "stopped", "idle"))

    listing = env.client.get("/api/jobs").json()
    chained = [j for j in listing if j["params"].get("continued_from") == job_id]
    assert len(chained) == 1
    assert chained[0]["params"]["video_workflow"] == "minimax_h3_i2v"
    assert chained[0]["params"]["end_image"] == str(env.image)


def _first_job(env, session_id: str) -> str:
    """プランを 1 本走らせて job_id を返すヘルパ（continue / rerun の土台）。"""
    env.cli.answers = [plan_answer(env, 1), DONE_ANSWER]
    say(env, session_id)
    env.client.post(f"/api/agent/sessions/{session_id}/approve", json={})
    done = wait_status(env, session_id, ("done", "stopped", "idle"))
    return done["plan"]["tasks"][0]["job_id"]


@needs_ffmpeg
def test_out_of_plan_continue_waits_for_approval(env):
    """プラン外の continue は承認待ちになり、「実行する」で初めて走る（§2 / §7）。"""
    session = start(env)
    job_id = _first_job(env, session["id"])
    queued_before = env.comfy.job_count

    env.cli.answers = [
        action_answer({"action": "continue", "job_id": job_id}, "続きを作ります。"),
    ]
    say(env, session["id"], "続きを作って")
    paused = wait_status(env, session["id"], ("waiting_checkin", "stopped", "done"))
    assert paused["status"] == "waiting_checkin"
    assert env.comfy.job_count == queued_before  # 承認前は投入しない
    checkin = paused["messages"][-1]
    assert checkin["role"] == "checkin" and checkin["kind"] == "approval"
    assert checkin["data"]["options"] == ["実行する", "やめる"]
    assert checkin["data"]["action"]["action"] == "continue"  # 保留アクションを永続化
    assert "approval_required" in kinds(paused)

    env.cli.answers = [DONE_ANSWER]
    response = env.client.post(
        f"/api/agent/sessions/{session['id']}/checkin", json={"choice": "実行する"}
    )
    assert response.status_code == 200, response.text
    final = wait_status(env, session["id"], ("done", "stopped", "idle"))
    assert env.comfy.job_count == queued_before + 1
    chained = [
        j
        for j in env.client.get("/api/jobs").json()
        if j["params"].get("continued_from") == job_id
    ]
    assert len(chained) == 1
    assert "action_skipped" not in kinds(final)


def test_declined_out_of_plan_rerun_is_skipped(env):
    session = start(env)
    job_id = _first_job(env, session["id"])
    queued_before = env.comfy.job_count

    env.cli.answers = [
        action_answer({"action": "rerun", "job_id": job_id}, "やり直します。"),
    ]
    say(env, session["id"], "シードを引き直して")
    wait_status(env, session["id"], ("waiting_checkin", "stopped", "done"))

    env.cli.answers = [DONE_ANSWER]
    env.client.post(
        f"/api/agent/sessions/{session['id']}/checkin", json={"choice": "やめる"}
    )
    final = wait_status(env, session["id"], ("done", "stopped", "idle"))
    assert "action_skipped" in kinds(final)
    assert env.comfy.job_count == queued_before  # 1 本も追加されない
    # 応答済みの承認は消費されるので、同じ保留が二度走ることはない
    assert agent_runner.pending_approval(AgentSession(**final)) is None


def test_auto_mode_runs_out_of_plan_rerun_immediately(env):
    session = start(env, checkin_mode="auto", auto_limit=5)
    job_id = _first_job(env, session["id"])
    queued_before = env.comfy.job_count

    env.cli.answers = [
        action_answer({"action": "rerun", "job_id": job_id}, "やり直します。"),
        DONE_ANSWER,
    ]
    say(env, session["id"], "シードを引き直して")
    final = wait_status(env, session["id"], ("done", "stopped", "idle"))
    assert env.comfy.job_count == queued_before + 1  # 自走モードは即実行
    assert "approval_required" not in kinds(final)


def test_rerun_of_an_unknown_job_is_reported(env):
    session = start(env, checkin_mode="auto")
    env.cli.answers = [
        action_answer({"action": "rerun", "job_id": "nope"}, "やり直します。"),
        DONE_ANSWER,
    ]
    say(env, session["id"], "やり直して")
    final = wait_status(env, session["id"], ("done", "stopped", "idle"))
    assert "action_failed" in kinds(final)


# --------------------------------------------------------------------------
# WebSocket (type: "agent")
# --------------------------------------------------------------------------

def test_ws_publishes_agent_frames(env):
    session = start(env)
    env.cli.answers = [plan_answer(env, 1), DONE_ANSWER]
    with env.client.websocket_connect("/api/ws") as socket:
        say(env, session["id"])
        env.client.post(f"/api/agent/sessions/{session['id']}/approve", json={})
        wait_status(env, session["id"], ("done", "stopped", "idle"))
        frames = []
        for _ in range(40):
            message = socket.receive_json()
            if message["type"] == "agent":
                frames.append(message)
                if message["status"] == "done":
                    break
    assert frames
    assert frames[-1]["session_id"] == session["id"]
    assert {f["status"] for f in frames} >= {"running", "done"}


# --------------------------------------------------------------------------
# grok client wiring (AGENT-MODE §3.4 / §6)
# --------------------------------------------------------------------------

def test_agent_client_uses_the_session_workdir_and_extra_args(env, monkeypatch):
    monkeypatch.setattr(
        config,
        "_settings",
        Settings(
            grok_command="grok",
            grok_model="grok-4.5",
            agent_grok_args=["--allow-tools"],
            agent_grok_timeout=300.0,
            agent_use_acp=False,
        ),
    )
    session = start(env)
    env.cli.answers = ["どんな雰囲気にしますか？"]
    say(env, session["id"])
    argv = env.cli.calls[-1]
    assert argv[:3] == ["grok", "--model", "grok-4.5"]
    assert "--allow-tools" in argv
    assert env.cli.cwds[-1] == str(env.sessions / session["id"])


def test_agent_default_settings_enable_tools(env):
    """既定は --permission-mode auto（grok 0.2.112 実機確認済み、AGENT-MODE §3.4）。"""
    session = start(env)
    env.cli.answers = ["どんな雰囲気にしますか？"]
    say(env, session["id"])
    argv = env.cli.calls[-1]
    index = argv.index("--permission-mode")
    assert argv[index + 1] == "auto"
    # ツールが使える構成なので TOOLS セクションがシステムプロンプトに入る
    assert "# TOOLS" in env.cli.prompts[-1]


async def test_agent_client_falls_back_to_plain_run_on_unknown_flag(monkeypatch):
    """古い CLI が未知フラグで失敗しても素の -p 実行に落ちてターンは生きる。"""

    async def fake_exec(argv, cwd, timeout):
        if "--permission-mode" in argv:
            return (2, "", "error: unexpected argument '--permission-mode'")
        return (0, "こんにちは", "")

    monkeypatch.setattr(grok, "_exec", fake_exec)
    client = grok.GrokCliClient(
        command="grok",
        model="grok-4.5",
        workdir=".",
        extra_args=["--permission-mode", "auto"],
    )
    assert await client.complete("hi") == "こんにちは"


# --------------------------------------------------------------------------
# thinking フラグ（「Grok が考えています…」の情報源）
# --------------------------------------------------------------------------

def test_turn_marks_the_session_as_thinking(env, monkeypatch):
    """run_turn の最中だけ thinking が立ち、終了時に必ず下がる。"""
    session = start(env)
    inner: list[bool] = []
    inner_frames: list[bool | None] = []
    base = env.cli

    async def spy(argv, cwd, timeout):
        inner.append(agent_runner.is_thinking(session["id"]))
        return await base(argv, cwd, timeout)

    monkeypatch.setattr(grok, "_exec", spy)
    env.cli.answers = ["どんな雰囲気にしますか？"]
    with env.client.websocket_connect("/api/ws") as socket:
        assert say(env, session["id"]).status_code == 200
        for _ in range(6):
            message = socket.receive_json()
            if message["type"] == "agent" and message["thinking"] is not None:
                inner_frames.append(message["thinking"])
            if inner_frames[-1:] == [False]:
                break

    assert inner == [True]  # ターン中は立っている
    assert agent_runner.is_thinking(session["id"]) is False
    assert inner_frames[:2] == [True, False]  # WS で立ち上がりと解除を通知


def test_get_session_reports_the_thinking_flag(env):
    session = start(env)
    url = f"/api/agent/sessions/{session['id']}"
    assert env.client.get(url).json()["thinking"] is False
    agent_runner._thinking.add(session["id"])
    try:
        assert env.client.get(url).json()["thinking"] is True
    finally:
        agent_runner._thinking.discard(session["id"])
    assert env.client.get(url).json()["thinking"] is False


def test_thinking_is_cleared_when_the_turn_fails(env):
    """LLMError で 502 になってもフラグは残さない（try/finally）。"""
    session = start(env)
    env.cli.answers = [grok.LLMError("grok CLI が失敗しました")]
    response = say(env, session["id"])
    assert response.status_code == 502
    assert agent_runner.is_thinking(session["id"]) is False
    assert env.client.get(f"/api/agent/sessions/{session['id']}").json()["thinking"] is False


def test_llm_error_inside_the_loop_stops_the_session(env):
    """ループ内の LLMError は running のまま固まらず stopped に落ちる。"""
    session = start(env)
    env.cli.answers = [plan_answer(env, 1), grok.LLMError("grok CLI が失敗しました")]
    say(env, session["id"])
    env.client.post(f"/api/agent/sessions/{session['id']}/approve", json={})

    final = wait_status(env, session["id"], ("stopped", "done", "idle"))
    assert final["status"] == "stopped"
    assert "stopped" in kinds(final)
    assert "Grok の呼び出しに失敗しました" in final["messages"][-1]["content"]
    assert agent_runner.is_thinking(session["id"]) is False
    assert final["thinking"] is False


# --------------------------------------------------------------------------
# 状態遷移 / 二重起動ガード
# --------------------------------------------------------------------------

def test_message_during_a_checkin_answers_it(env):
    """チェックイン待ちのメイン入力は自由回答としてループを再開する。"""
    session = start(env)
    env.cli.answers = [plan_answer(env, 2), CHECKIN_ANSWER, DONE_ANSWER]
    say(env, session["id"])
    env.client.post(f"/api/agent/sessions/{session['id']}/approve", json={})
    paused = wait_status(env, session["id"], ("waiting_checkin", "stopped", "done"))
    assert paused["status"] == "waiting_checkin"

    response = say(env, session["id"], "そのままで進めて")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["action"] is None  # チェックイン応答なので同期ターンは走らない
    final = wait_status(env, session["id"], ("done", "stopped", "idle"))
    assert final["status"] == "done"
    assert env.comfy.job_count == 2
    # 自由回答は 1 度だけ記録され、チェックインは応答済みになる
    assert [m["content"] for m in final["messages"]].count("そのままで進めて") == 1
    answered = [m for m in final["messages"] if m["role"] == "checkin"]
    assert answered[-1]["data"]["resolved"] is True


def test_message_during_a_checkin_can_approve_a_pending_action(env):
    """メイン入力からの肯定回答でも保留中のプラン外アクションが走る。"""
    session = start(env)
    job_id = _first_job(env, session["id"])
    queued_before = env.comfy.job_count
    env.cli.answers = [
        action_answer({"action": "rerun", "job_id": job_id}, "やり直します。"),
    ]
    say(env, session["id"], "シードを引き直して")
    wait_status(env, session["id"], ("waiting_checkin", "stopped", "done"))

    env.cli.answers = [DONE_ANSWER]
    assert say(env, session["id"], "実行する").status_code == 200
    final = wait_status(env, session["id"], ("done", "stopped", "idle"))
    assert env.comfy.job_count == queued_before + 1
    assert "action_skipped" not in kinds(final)


def test_plain_checkin_is_marked_resolved(env):
    """種別を問わず応答済みマークが付く（フロントの「応答済み」判定の根拠）。"""
    session = start(env)
    env.cli.answers = [plan_answer(env, 2), CHECKIN_ANSWER, DONE_ANSWER]
    say(env, session["id"])
    env.client.post(f"/api/agent/sessions/{session['id']}/approve", json={})
    paused = wait_status(env, session["id"], ("waiting_checkin", "stopped", "done"))
    assert paused["messages"][-1]["data"].get("resolved") is None

    env.client.post(
        f"/api/agent/sessions/{session['id']}/checkin", json={"choice": "そのまま"}
    )
    final = wait_status(env, session["id"], ("done", "stopped", "idle"))
    checkins = [m for m in final["messages"] if m["role"] == "checkin"]
    assert checkins[-1]["data"]["resolved"] is True


def test_message_and_approve_are_409_while_the_loop_runs(env, monkeypatch):
    session = start(env)
    env.cli.answers = [plan_answer(env, 1)]
    say(env, session["id"])
    monkeypatch.setattr(agent_runner, "is_running", lambda _id: True)

    assert say(env, session["id"], "追加で").status_code == 409
    approve = env.client.post(f"/api/agent/sessions/{session['id']}/approve", json={})
    assert approve.status_code == 409
    assert "すでに実行中です" in approve.json()["detail"]


def test_checkin_outside_a_checkin_is_409(env):
    session = start(env)
    response = env.client.post(
        f"/api/agent/sessions/{session['id']}/checkin", json={"content": "はい"}
    )
    assert response.status_code == 409
    assert "チェックイン待ちではありません" in response.json()["detail"]


async def test_start_loop_never_runs_twice(env, monkeypatch):
    """approve / checkin の連打でループが 2 本走らない（判定と登録の間で await しない）。"""
    session = start(env)
    runs: list[str] = []

    async def fake_loop(session_id, action=None):
        runs.append(session_id)
        await asyncio.sleep(0.1)

    monkeypatch.setattr(agent_runner, "_loop", fake_loop)
    await asyncio.gather(
        agent_runner.start_loop(session["id"]),
        agent_runner.start_loop(session["id"]),
        agent_runner.start_loop(session["id"]),
    )
    assert runs == [session["id"]]
    assert agent_runner.is_running(session["id"]) is True
    running = await agent_store.load(session["id"])
    assert running is not None and running.status == "running"

    await asyncio.sleep(0.2)
    assert agent_runner.is_running(session["id"]) is False


def test_stop_during_a_running_loop_ends_it(env):
    """実行中の stop はジョブ完了を待って stopped に落ち、WS でも通知される。"""
    session = start(env)
    env.cli.answers = [plan_answer(env, 3)] + ["まだ考え中です。"] * 6
    say(env, session["id"])
    with env.client.websocket_connect("/api/ws") as socket:
        env.client.post(f"/api/agent/sessions/{session['id']}/approve", json={})
        wait_status(env, session["id"], ("running", "waiting_checkin", "done", "stopped"))
        env.client.post(f"/api/agent/sessions/{session['id']}/stop")
        final = wait_status(env, session["id"], ("stopped", "done", "idle"))
        statuses = []
        for _ in range(60):
            message = socket.receive_json()
            if message["type"] == "agent":
                statuses.append(message["status"])
                if message["status"] == "stopped":
                    break
    assert final["status"] == "stopped"
    assert "stopped" in statuses
    assert "ユーザーの操作で停止しました。" == final["messages"][-1]["content"]


def test_approve_during_a_checkin_is_409(env):
    """未応答のチェックインを飛び越えた再開は拒否する（状態ずれ防止）。"""
    session = start(env)
    env.cli.answers = [plan_answer(env, 2), CHECKIN_ANSWER]
    say(env, session["id"])
    env.client.post(f"/api/agent/sessions/{session['id']}/approve", json={})
    wait_status(env, session["id"], ("waiting_checkin", "stopped", "done"))

    response = env.client.post(f"/api/agent/sessions/{session['id']}/approve", json={})
    assert response.status_code == 409
    assert "チェックイン" in response.json()["detail"]


def test_stop_during_a_turn_drops_the_returned_action(env, monkeypatch):
    """ターン中に停止したら、返ってきたアクションは実行しない（新規投入を防ぐ）。"""
    session = start(env)
    env.cli.answers = [plan_answer(env, 1)]
    say(env, session["id"])
    queued_before = env.comfy.job_count

    async def stopping_exec(argv, cwd, timeout):
        # ターンの最中に ⏹ 停止が押された状況を再現する
        await agent_runner.request_stop(session["id"])
        return (0, action_answer({"action": "rerun", "job_id": "nope"}, "やり直します。"), "")

    monkeypatch.setattr(grok, "_exec", stopping_exec)
    env.client.post(f"/api/agent/sessions/{session['id']}/approve", json={})

    final = wait_status(env, session["id"], ("stopped", "done", "idle"))
    assert final["status"] == "stopped"
    assert final["messages"][-1]["content"] == "ユーザーの操作で停止しました。"
    # 破棄されたので rerun は試されない（試されていれば action_failed が出る）
    assert "action_failed" not in kinds(final)
    assert env.comfy.job_count == queued_before + 1  # プランの 1 本だけ


# --------------------------------------------------------------------------
# ACP（実行中ステータス）
# --------------------------------------------------------------------------

def test_agent_client_is_acp_by_default(env, monkeypatch):
    """既定ではエージェントのターンを ACP（grok agent stdio）で回す。"""
    monkeypatch.setattr(
        config,
        "_settings",
        Settings(grok_command="grok", grok_model="grok-4.5", agent_grok_timeout=42.0),
    )
    client = grok.get_agent_client("/tmp/session-x")
    assert isinstance(client, acp.AcpAgentClient)
    assert client.argv() == ["grok", "agent", "-m", "grok-4.5", "stdio"]
    assert client.timeout == 42.0
    # ACP を開始できなければ従来のワンショットに落ちる
    assert isinstance(client.fallback_client(), grok.GrokCliClient)


def test_agent_client_can_be_forced_back_to_oneshot(env, monkeypatch):
    monkeypatch.setattr(
        config,
        "_settings",
        Settings(grok_command="grok", grok_model="grok-4.5", agent_use_acp=False),
    )
    assert isinstance(grok.get_agent_client("/tmp/session-x"), grok.GrokCliClient)


def test_activity_is_published_and_polled(env):
    """ACP のコールバックで activity が WS とセッション API に出る。"""
    session = start(env)
    session_id = session["id"]

    async def scenario() -> None:
        await agent_runner._set_thinking(session_id, True)
        await agent_runner._set_activity(session_id, "ツール実行中: run_terminal_command")
        assert agent_runner.current_activity(session_id) == "ツール実行中: run_terminal_command"
        assert env.client.get(f"/api/agent/sessions/{session_id}").json()["activity"] == (
            "ツール実行中: run_terminal_command"
        )
        # ターン終了で消える
        await agent_runner._set_thinking(session_id, False)

    asyncio.run(scenario())
    assert agent_runner.current_activity(session_id) is None
    assert env.client.get(f"/api/agent/sessions/{session_id}").json()["activity"] is None


# --------------------------------------------------------------------------
# 画像ワークフローの検証とカタログ (SPEC §3 / AGENT-MODE §3.1)
# --------------------------------------------------------------------------

KNOWN_FAMILIES = {"kaori.safetensors": "krea2", "hana.safetensors": "anima"}
KNOWN_LORAS_WITH_ANIMA = {**KNOWN_LORAS, "hana.safetensors": "image"}


def test_an_unknown_image_workflow_is_rejected(env):
    answer = _plan_with(env, image_workflow="nope")
    with pytest.raises(agent_protocol.ActionError, match="nope"):
        agent_protocol.parse_action(answer)


def test_the_editing_workflow_without_a_source_image_is_rejected(env):
    for mode in ("full", "image_only"):
        answer = _plan_with(
            env, mode=mode, image_workflow="qwen_image_edit_2511"
        )
        with pytest.raises(agent_protocol.ActionError, match="source_image"):
            agent_protocol.parse_action(answer)


def test_the_editing_workflow_with_a_source_image_is_accepted(env):
    answer = _plan_with(
        env,
        mode="image_only",
        image_workflow="qwen_image_edit_2511",
        source_image=str(env.image),
        video_prompt="",
        audio_path=None,
    )
    action = agent_protocol.parse_action(answer)
    assert action is not None
    assert action.tasks[0].job["image_workflow"] == "qwen_image_edit_2511"


def test_an_image_lora_of_the_wrong_family_is_rejected(env):
    answer = _plan_with(
        env,
        image_workflow="anima",
        loras=[{"lora_name": "kaori.safetensors", "trigger_word": "kaori"}],
    )
    with pytest.raises(agent_protocol.ActionError, match="anima"):
        agent_protocol.parse_action(
            answer,
            known_loras=KNOWN_LORAS_WITH_ANIMA,
            known_families=KNOWN_FAMILIES,
        )


def test_an_image_lora_of_the_matching_family_is_accepted(env):
    answer = _plan_with(
        env,
        image_workflow="anima",
        loras=[{"lora_name": "hana.safetensors", "trigger_word": "hana"}],
    )
    action = agent_protocol.parse_action(
        answer,
        known_loras=KNOWN_LORAS_WITH_ANIMA,
        known_families=KNOWN_FAMILIES,
    )
    assert action is not None


def test_the_family_check_is_skipped_without_an_image_stage(env):
    answer = _plan_with(
        env,
        mode="i2v",
        image_prompt="",
        source_image=str(env.image),
        image_workflow="anima",
        loras=[{"lora_name": "kaori.safetensors", "trigger_word": "kaori"}],
    )
    assert (
        agent_protocol.parse_action(
            answer,
            known_loras=KNOWN_LORAS_WITH_ANIMA,
            known_families=KNOWN_FAMILIES,
        )
        is not None
    )


def test_known_lora_families_only_covers_image_loras(env):
    env.client.post(
        "/api/loras",
        json={"display_name": "ハナ", "lora_name": "hana.safetensors",
              "trigger_word": "hana", "target": "image", "family": "anima"},
    )
    env.client.post(
        "/api/loras",
        json={"display_name": "スローモ", "lora_name": "motion.safetensors",
              "trigger_word": "slowmo", "target": "video"},
    )
    families = asyncio.run(agent_runner.known_lora_families())
    assert families == {"hana.safetensors": "anima"}


def test_the_system_prompt_carries_the_image_workflow_catalog(env):
    system = start(env)["messages"][0]["content"]
    assert "# IMAGE WORKFLOWS (the `image_workflow` field of a job)" in system
    for workflow_id in ("krea2_turbo", "anima", "z_image_turbo", "qwen_image_edit_2511"):
        assert f"`{workflow_id}`" in system
    # every family's guide is embedded, because the agent picks the workflow
    for heading in (
        "IMAGE PROMPT SPEC — Krea 2 turbo",
        "IMAGE PROMPT SPEC — Anima",
        "IMAGE PROMPT SPEC — Tongyi Z-Image turbo",
        "IMAGE PROMPT SPEC — Qwen-Image Edit 2511",
    ):
        assert heading in system
    assert '"image_workflow": "krea2_turbo"' in system


def test_the_system_prompt_lists_each_image_loras_family(env):
    env.client.post(
        "/api/loras",
        json={"display_name": "ハナ", "lora_name": "hana.safetensors",
              "trigger_word": "hana", "target": "image", "family": "anima"},
    )
    system = start(env)["messages"][0]["content"]
    assert "family `anima`" in system
    assert "image_workflow` のファミリーと一致" in system


def test_the_system_prompt_explains_attachments(env):
    system = start(env)["messages"][0]["content"]
    assert "[Attached files" in system
    assert "attachments/" in system


# --------------------------------------------------------------------------
# ライブラリ（SPEC §7.2）
# --------------------------------------------------------------------------

def test_library_action_keeps_a_job_output(env):
    session = start(env)
    env.cli.answers = [plan_answer(env, 1), DONE_ANSWER]
    say(env, session["id"])
    env.client.post(f"/api/agent/sessions/{session['id']}/approve", json={})
    done = wait_status(env, session["id"], ("done", "stopped", "idle"))
    job_id = done["plan"]["tasks"][0]["job_id"]

    env.cli.answers = [
        action_answer(
            {
                "action": "library",
                "job_id": job_id,
                "source": "image",
                "title": "夕暮れ屋上ダンス・決め絵",
            },
            "取っておきます。",
        )
    ]
    reply = say(env, session["id"], "この画像を残して").json()
    assert reply["action"]["action"] == "library"
    assert "library_added" in kinds(reply["session"])

    items = env.client.get("/api/library").json()["items"]
    assert [item["name"] for item in items] == ["夕暮れ屋上ダンス・決め絵"]
    assert items[0]["source_job_id"] == job_id
    # 実体は library/ にコピーされ、次のジョブの入力に使える
    assert Path(items[0]["path"]).is_file()
    assert jobs.resolve_asset_path(items[0]["path"], field="source_image").is_file()


def test_library_action_reports_a_missing_output(env):
    session = start(env)
    env.cli.answers = [
        action_answer(
            {"action": "library", "job_id": "ghost", "source": "image"},
            "取っておきます。",
        )
    ]
    reply = say(env, session["id"], "残して").json()
    assert "action_failed" in kinds(reply["session"])
    assert env.client.get("/api/library").json()["items"] == []


def test_library_action_needs_a_job_and_a_known_source(env):
    with pytest.raises(agent_protocol.ActionError, match="job_id"):
        agent_protocol.parse_action(action_answer({"action": "library"}))
    with pytest.raises(agent_protocol.ActionError, match="source"):
        agent_protocol.parse_action(
            action_answer({"action": "library", "job_id": "j1", "source": "thumbnail"})
        )


def test_system_prompt_lists_the_library(env):
    """CHOICES にライブラリのパスが出て、入力に使えると分かる（SPEC §7.2）。"""
    created = env.client.post(
        "/api/library/image", files={"file": ("ref.png", b"PNG", "image/png")}
    )
    assert created.status_code == 201, created.text
    item = created.json()

    system = start(env)["messages"][0]["content"]
    assert "Library（取っておいた素材" in system
    assert item["path"] in system
    assert "ref.png" in system
    assert "source_image / end_image" in system


def test_system_prompt_says_the_library_is_empty(env):
    system = start(env)["messages"][0]["content"]
    assert "Library（取っておいた素材" in system
    assert "- (none)" in system


def test_a_plan_can_use_a_library_path_as_an_input(env):
    """ライブラリのパスは assets と同じようにジョブ入力として検証を通る。"""
    item = env.client.post(
        "/api/library/image", files={"file": ("start.png", b"PNG", "image/png")}
    ).json()
    action = agent_protocol.parse_action(
        _plan_with(env, mode="i2v", source_image=item["path"])
    )
    assert action is not None
    assert action.tasks[0].job["source_image"] == item["path"]

    # /library/... の URL でも同じく通る
    by_url = agent_protocol.parse_action(
        _plan_with(env, mode="i2v", source_image=item["url"])
    )
    assert by_url is not None


def add_to_library(env, kind: str, name: str, tags: str = "") -> dict:
    created = env.client.post(
        f"/api/library/{kind}",
        files={"file": (name, b"DATA", "application/octet-stream")},
        data={"tags": tags},
    )
    assert created.status_code == 201, created.text
    return created.json()


def test_library_action_can_attach_tags(env):
    session = start(env)
    env.cli.answers = [plan_answer(env, 1), DONE_ANSWER]
    say(env, session["id"])
    env.client.post(f"/api/agent/sessions/{session['id']}/approve", json={})
    done = wait_status(env, session["id"], ("done", "stopped", "idle"))
    job_id = done["plan"]["tasks"][0]["job_id"]

    env.cli.answers = [
        action_answer(
            {
                "action": "library",
                "job_id": job_id,
                "source": "image",
                "title": "決め絵",
                "tags": ["キャラ", "サクラ"],
            },
            "取っておきます。",
        )
    ]
    say(env, session["id"], "残して")
    assert env.client.get("/api/library").json()["items"][0]["tags"] == [
        "キャラ",
        "サクラ",
    ]


def test_library_search_returns_matches_as_an_event(env):
    picture = add_to_library(env, "image", "sakura.png", "キャラ")
    add_to_library(env, "image", "haikei.png", "背景")

    session = start(env)
    env.cli.answers = [
        action_answer(
            {"action": "library_search", "q": "sakura"}, "探します。"
        )
    ]
    reply = say(env, session["id"], "サクラの素材ある？").json()
    assert reply["action"]["action"] == "library_search"

    events = [
        m for m in reply["session"]["messages"] if m["kind"] == "library_search_result"
    ]
    assert len(events) == 1
    text = events[0]["content"]
    assert picture["path"] in text
    assert "haikei.png" not in text
    assert "1 件中 1〜1 件目" in text
    assert events[0]["data"]["total"] == 1


def test_library_search_can_filter_by_tag_and_kind(env):
    add_to_library(env, "image", "a.png", "夜景")
    track = add_to_library(env, "audio", "b.mp3", "夜景")

    session = start(env)
    env.cli.answers = [
        action_answer(
            {"action": "library_search", "tag": "夜景", "kind": "audio"}, "探します。"
        )
    ]
    reply = say(env, session["id"], "夜景の音は？").json()
    text = [
        m for m in reply["session"]["messages"] if m["kind"] == "library_search_result"
    ][0]["content"]
    assert track["path"] in text
    assert "a.png" not in text


def test_library_search_tells_how_to_get_the_next_page(env):
    total = agent_runner.LIBRARY_SEARCH_LIMIT + 5
    for index in range(total):
        add_to_library(env, "image", f"pic{index}.png")

    session = start(env)
    env.cli.answers = [action_answer({"action": "library_search"}, "探します。")]
    reply = say(env, session["id"], "全部見せて").json()
    event = [
        m for m in reply["session"]["messages"] if m["kind"] == "library_search_result"
    ][0]
    assert f"{total} 件中 1〜{agent_runner.LIBRARY_SEARCH_LIMIT} 件目" in event["content"]
    assert "まだ 5 件あります" in event["content"]
    assert f'"offset": {agent_runner.LIBRARY_SEARCH_LIMIT}' in event["content"]
    assert event["data"]["returned"] == agent_runner.LIBRARY_SEARCH_LIMIT

    # 続きを offset で取りに行ける
    env.cli.answers = [
        action_answer(
            {"action": "library_search", "offset": agent_runner.LIBRARY_SEARCH_LIMIT},
            "続きです。",
        )
    ]
    reply = say(env, session["id"], "続き").json()
    latest = [
        m for m in reply["session"]["messages"] if m["kind"] == "library_search_result"
    ][-1]
    assert f"{total} 件中 {agent_runner.LIBRARY_SEARCH_LIMIT + 1}〜{total} 件目" in (
        latest["content"]
    )
    assert "まだ" not in latest["content"]


def test_library_search_reports_no_match(env):
    add_to_library(env, "image", "a.png")
    session = start(env)
    env.cli.answers = [
        action_answer({"action": "library_search", "q": "ghost"}, "探します。")
    ]
    reply = say(env, session["id"], "探して").json()
    text = [
        m for m in reply["session"]["messages"] if m["kind"] == "library_search_result"
    ][0]["content"]
    assert "該当なし" in text


def test_library_search_rejects_an_unknown_kind(env):
    with pytest.raises(agent_protocol.ActionError, match="kind"):
        agent_protocol.parse_action(
            action_answer({"action": "library_search", "kind": "model"})
        )
    # 条件なし（全件の 1 ページ目）は許す
    action = agent_protocol.parse_action(action_answer({"action": "library_search"}))
    assert action is not None
    assert (action.query, action.tag, action.library_kind, action.offset) == (
        "",
        None,
        None,
        0,
    )


def test_system_prompt_shows_tags_and_points_at_the_search(env):
    add_to_library(env, "image", "sakura.png", "キャラ,立ち絵")
    system = start(env)["messages"][0]["content"]
    assert "[キャラ, 立ち絵]" in system
    assert "library_search" in system
    assert "上の一覧が現時点の全件です" in system


def test_system_prompt_says_how_many_entries_are_hidden(env):
    hidden = 3
    for index in range(prompts.LIBRARY_PROMPT_LIMIT + hidden):
        add_to_library(env, "image", f"pic{index}.png")
    system = start(env)["messages"][0]["content"]
    assert f"ここに出していない素材が {hidden} 件あります" in system
    assert f"…ほか {hidden} 件" in system
    assert f"image（source_image / end_image、全 {prompts.LIBRARY_PROMPT_LIMIT + hidden} 件）" in system


def test_library_action_reports_an_already_registered_output(env):
    """二重登録はエラーではなく「もう棚にある」という案内にする（SPEC §7.2）。"""
    session = start(env)
    env.cli.answers = [plan_answer(env, 1), DONE_ANSWER]
    say(env, session["id"])
    env.client.post(f"/api/agent/sessions/{session['id']}/approve", json={})
    done = wait_status(env, session["id"], ("done", "stopped", "idle"))
    job_id = done["plan"]["tasks"][0]["job_id"]

    keep = action_answer(
        {"action": "library", "job_id": job_id, "source": "image", "title": "決め絵"},
        "取っておきます。",
    )
    env.cli.answers = [keep]
    say(env, session["id"], "残して")

    env.cli.answers = [keep]
    reply = say(env, session["id"], "もう一度残して").json()
    assert "library_exists" in kinds(reply["session"])
    assert "action_failed" not in kinds(reply["session"])
    event = [m for m in reply["session"]["messages"] if m["kind"] == "library_exists"][0]
    assert "既にライブラリにあります" in event["content"]
    assert "決め絵" in event["content"]
    # コピーは増えない
    assert len(env.client.get("/api/library").json()["items"]) == 1


def test_library_action_asks_grok_for_japanese_tags(env, monkeypatch):
    """エージェントが tags を書かなければ、背景で日本語タグが付く（SPEC §7.2）。"""
    async def describe(text: str) -> tuple[str, list[str]]:
        return "夕暮れ屋上のダンス", ["女性", "屋上", "夕暮れ"]

    monkeypatch.setattr(autotag, "describe", describe)

    session = start(env)
    env.cli.answers = [plan_answer(env, 1), DONE_ANSWER]
    say(env, session["id"])
    env.client.post(f"/api/agent/sessions/{session['id']}/approve", json={})
    done = wait_status(env, session["id"], ("done", "stopped", "idle"))
    job_id = done["plan"]["tasks"][0]["job_id"]

    env.cli.answers = [
        action_answer(
            {"action": "library", "job_id": job_id, "source": "image"},
            "取っておきます。",
        )
    ]
    say(env, session["id"], "残して")

    deadline = time.time() + 5.0
    item = {}
    while time.time() < deadline:
        rows = env.client.get("/api/library").json()["items"]
        if rows and rows[0]["tags"]:
            item = rows[0]
            break
        time.sleep(0.05)
    assert item.get("tags") == ["女性", "屋上", "夕暮れ"]
    # title を書かなかったので表示名も日本語に置き換わる
    assert item["name"] == "夕暮れ屋上のダンス"


# --------------------------------------------------------------------------
# ライブラリの分類とリファレンスシート（SPEC §7.2、AGENT-MODE §3.1）
# --------------------------------------------------------------------------

def png_bytes(color=(255, 0, 0)) -> bytes:
    """テスト用のべた塗り PNG（シート合成は本物の画像を要求する）。"""
    buffer = io.BytesIO()
    Image.new("RGB", (64, 64), color).save(buffer, format="PNG")
    return buffer.getvalue()


def material(env, name: str, category: str = "") -> dict:
    """分類つきの画像素材を棚に置く（category が空なら未分類）。"""
    created = env.client.post(
        "/api/library/image",
        files={"file": (name, png_bytes(), "image/png")},
        data={"category": category},
    )
    assert created.status_code == 201, created.text
    return created.json()


def event_of(reply: dict, kind: str) -> dict:
    return [m for m in reply["session"]["messages"] if m["kind"] == kind][-1]


def library_ids(env) -> list[str]:
    return [item["id"] for item in env.client.get("/api/library").json()["items"]]


def run_action(env, payload: dict, said: str = "お願い") -> dict:
    """アクション 1 つを走らせて返信 JSON を返す（セッションは使い捨て）。"""
    session = start(env)
    env.cli.answers = [action_answer(payload, "やります。")]
    return say(env, session["id"], said).json()


def test_library_search_can_filter_by_category(env):
    hero = material(env, "hero.png", "character")
    material(env, "room.png", "background")

    reply = run_action(
        env, {"action": "library_search", "category": "character"}, "キャラ素材ある？"
    )
    assert reply["action"]["category"] == "character"
    text = event_of(reply, "library_search_result")["content"]
    assert hero["path"] in text
    assert "room.png" not in text
    # 絞り込み条件も本文に出るので、同じ条件で offset を進められる
    assert "category=character" in text


def test_library_search_can_ask_for_the_uncategorized(env):
    plain = material(env, "plain.png")
    material(env, "hero.png", "character")

    reply = run_action(
        env,
        {"action": "library_search", "category": library.UNCATEGORIZED},
        "分類していない素材は？",
    )
    text = event_of(reply, "library_search_result")["content"]
    assert plain["path"] in text
    assert "hero.png" not in text


def test_library_search_shows_the_category_of_every_hit(env):
    material(env, "hero.png", "character")
    material(env, "plain.png")

    reply = run_action(env, {"action": "library_search"}, "全部見せて")
    text = event_of(reply, "library_search_result")["content"]
    assert "（image / character）" in text
    # 未分類も明示値で書いておく（そのまま category にコピーできる）
    assert f"（image / {library.UNCATEGORIZED}）" in text


def test_library_search_rejects_an_unknown_category(env):
    with pytest.raises(agent_protocol.ActionError, match="category"):
        agent_protocol.parse_action(
            action_answer({"action": "library_search", "category": "monster"})
        )
    # 省略は「分類で絞らない」
    action = agent_protocol.parse_action(action_answer({"action": "library_search"}))
    assert action is not None and action.category is None


def test_library_action_can_set_a_category(env):
    session = start(env)
    env.cli.answers = [plan_answer(env, 1), DONE_ANSWER]
    say(env, session["id"])
    env.client.post(f"/api/agent/sessions/{session['id']}/approve", json={})
    done = wait_status(env, session["id"], ("done", "stopped", "idle"))
    job_id = done["plan"]["tasks"][0]["job_id"]

    env.cli.answers = [
        action_answer(
            {
                "action": "library",
                "job_id": job_id,
                "source": "image",
                "title": "サクラ・正面",
                "category": "character",
            },
            "取っておきます。",
        )
    ]
    reply = say(env, session["id"], "キャラとして残して").json()
    assert env.client.get("/api/library").json()["items"][0]["category"] == "character"
    assert "分類: character" in event_of(reply, "library_added")["content"]


def test_library_action_rejects_an_unknown_category(env):
    with pytest.raises(agent_protocol.ActionError, match="category"):
        agent_protocol.parse_action(
            action_answer(
                {
                    "action": "library",
                    "job_id": "j1",
                    "source": "image",
                    "category": "monster",
                }
            )
        )


def test_library_sheet_composes_a_sheet_from_the_library(env):
    hero = material(env, "hero.png", "character")
    sword = material(env, "sword.png", "prop")

    reply = run_action(
        env,
        {
            "action": "library_sheet",
            "item_ids": [hero["id"], sword["id"]],
            "name": "サクラのシート",
        },
        "シートを作って",
    )
    assert reply["action"]["action"] == "library_sheet"

    event = event_of(reply, "library_sheet_added")
    rows = env.client.get("/api/library").json()["items"]
    sheet = [row for row in rows if row["id"] == event["data"]["library_id"]][0]
    assert sheet["name"] == "サクラのシート"
    assert sheet["category"] == "character"
    assert sheet["tags"] == [library.SHEET_TAG]

    # id・パス・URL をそのまま返す（次のターンで書き写せる）
    assert (event["data"]["path"], event["data"]["url"]) == (sheet["path"], sheet["url"])
    assert event["data"]["item_ids"] == [hero["id"], sword["id"]]
    for shown in (sheet["id"], sheet["path"], sheet["url"], "source_image"):
        assert shown in event["content"]

    # 出来上がったシートはそのまま次のジョブの source_image に使える
    assert jobs.resolve_asset_path(sheet["path"], field="source_image").is_file()


def test_library_sheet_takes_the_requested_size(env):
    hero = material(env, "hero.png", "character")
    reply = run_action(
        env,
        {
            "action": "library_sheet",
            "item_ids": [hero["id"]],
            "width": 640,
            "height": 1136,
        },
        "縦のシートで",
    )
    with Image.open(event_of(reply, "library_sheet_added")["data"]["path"]) as image:
        assert image.size == (640, 1136)


def test_library_sheet_falls_back_to_the_default_size(env):
    hero = material(env, "hero.png", "character")
    reply = run_action(
        env, {"action": "library_sheet", "item_ids": [hero["id"]]}, "シート"
    )
    with Image.open(event_of(reply, "library_sheet_added")["data"]["path"]) as image:
        assert image.size == (sheets.DEFAULT_WIDTH, sheets.DEFAULT_HEIGHT)


def test_library_sheet_reports_an_unknown_id(env):
    hero = material(env, "hero.png", "character")
    reply = run_action(
        env,
        {"action": "library_sheet", "item_ids": [hero["id"], "ghost"]},
        "シートを作って",
    )
    assert "library_sheet_added" not in kinds(reply["session"])
    assert "ghost" in event_of(reply, "action_failed")["content"]
    # 素材はそのまま、壊れたシートは棚に残らない
    assert library_ids(env) == [hero["id"]]


def test_library_sheet_reports_a_material_that_is_not_an_image(env):
    hero = material(env, "hero.png", "character")
    track = add_to_library(env, "audio", "bgm.mp3")
    reply = run_action(
        env,
        {"action": "library_sheet", "item_ids": [hero["id"], track["id"]]},
        "シートを作って",
    )
    assert "action_failed" in kinds(reply["session"])
    assert sorted(library_ids(env)) == sorted([hero["id"], track["id"]])


def test_library_sheet_reports_a_canvas_that_is_too_large(env):
    hero = material(env, "hero.png", "character")
    reply = run_action(
        env,
        {
            "action": "library_sheet",
            "item_ids": [hero["id"]],
            "width": sheets.MAX_EDGE + 8,
            "height": 720,
        },
        "巨大なシート",
    )
    assert "action_failed" in kinds(reply["session"])
    assert library_ids(env) == [hero["id"]]


def test_library_sheet_reports_too_many_materials(env):
    picked = [
        material(env, f"{index}.png", "prop")["id"]
        for index in range(sheets.MAX_ITEMS + 1)
    ]
    reply = run_action(
        env, {"action": "library_sheet", "item_ids": picked}, "全部でシート"
    )
    assert "action_failed" in kinds(reply["session"])
    assert len(library_ids(env)) == sheets.MAX_ITEMS + 1


def test_library_sheet_needs_item_ids(env):
    for payload in (
        {"action": "library_sheet"},
        {"action": "library_sheet", "item_ids": []},
        {"action": "library_sheet", "item_ids": ["  "]},
        {"action": "library_sheet", "item_ids": "abc"},
    ):
        with pytest.raises(agent_protocol.ActionError, match="item_ids"):
            agent_protocol.parse_action(action_answer(payload))


def test_library_sheet_keeps_the_given_order_and_defaults(env):
    action = agent_protocol.parse_action(
        action_answer({"action": "library_sheet", "item_ids": ["b", "a"]})
    )
    assert action is not None
    # 並び順はレイアウトそのものなので、勝手に整列しない
    assert action.item_ids == ["b", "a"]
    assert (action.title, action.width, action.height) == ("", None, None)


def test_library_sheet_rejects_a_size_that_is_not_a_number(env):
    with pytest.raises(agent_protocol.ActionError, match="width"):
        agent_protocol.parse_action(
            action_answer(
                {"action": "library_sheet", "item_ids": ["a"], "width": "おおきめ"}
            )
        )


def test_system_prompt_explains_the_character_sheet_flow(env):
    material(env, "hero.png", "character")
    system = start(env)["messages"][0]["content"]
    # 棚の分類が見え、アクションの表にシート合成が載っている
    assert "（character）" in system
    assert "`library_sheet`" in system
    assert "`category`" in system
    # 参照ワークフローへの渡し方まで案内する
    assert "reference_images" in system
    assert "minimax_h3_r2v" in system


# --------------------------------------------------------------------------
# 選択式フィールド（SPEC §3.1）
# --------------------------------------------------------------------------

def test_the_system_prompt_lists_the_declared_selects(env):
    """選択式を宣言したワークフロー（MiniMax H3 turbo の `low_vram`）だけ案内が出る。"""
    system = start(env)["messages"][0]["content"]
    assert "選択項目（ジョブの `selects`" in system
    assert "`low_vram`" in system
    # 選べる値と、省略したときの既定（OFF）まで書いてある
    assert "`off`, `on`" in system
    assert "省略すると `off`" in system


def test_selects_on_a_workflow_without_them_is_rejected(env):
    with pytest.raises(agent_protocol.ActionError) as excinfo:
        agent_protocol.parse_action(_plan(env, selects={"dance_style": "K-Pop 韩舞"}))
    assert "dance_style" in str(excinfo.value)


# --------------------------------------------------------------------------
# 接続先ごとの MiniMax H3 の版の選び分け（local だけ opt を勧める）
# --------------------------------------------------------------------------

#: カスタムノード（`app.workflows.OPTIONAL_CLASS_TYPES`）前提のワークフロー
CUSTOM_NODE_WORKFLOWS = (
    "minimax_h3_i2v_turbo",
    "minimax_h3_r2v_turbo",
    "minimax_h3_i2v_opt",
    "minimax_h3_r2v_opt",
)


def _set_target(monkeypatch, target: str) -> None:
    monkeypatch.setattr(
        config,
        "_settings",
        config.load_settings().model_copy(update={"comfy_target": target}),
    )


def test_the_catalog_has_no_target_section_without_a_target():
    """既存の呼び出し（接続先を渡さない）では節ごと出ない。"""
    section = prompts.workflow_catalog_section()
    assert "この環境の接続先" not in section
    assert "# VIDEO WORKFLOWS" in section


def test_local_prefers_the_opt_workflows():
    section = prompts.workflow_catalog_section("local")
    assert "この環境の接続先: `local`" in section
    target_section = section.split("## この環境の接続先")[1]
    assert "`minimax_h3_i2v_opt`" in target_section
    assert "ユーザーがワークフローを名指ししたとき" in section


def test_runpod_prefers_the_plain_workflows():
    section = prompts.workflow_catalog_section("runpod")
    assert "この環境の接続先: `runpod`" in section
    assert "`_turbo` / `_opt` は選ばないこと" in section
    # opt を勧める文言は出ない（カタログの一覧には全ワークフローが載るので、
    # 判定は接続先の節そのものを見る）
    target_section = section.split("## この環境の接続先")[1]
    assert "_opt` 版" not in target_section
    assert "ユーザーがワークフローを名指ししたとき" in section
    # 自前の ComfyUI なので一覧からは何も落とさない
    for workflow_id in CUSTOM_NODE_WORKFLOWS:
        assert f"`{workflow_id}`" in section


def test_comfy_cloud_drops_the_custom_node_workflows_from_the_catalog():
    """Comfy Cloud ではカスタムノード前提の turbo / opt を列挙ごと出さない。"""
    section = prompts.workflow_catalog_section("comfy_cloud")
    assert "この環境の接続先: `comfy_cloud`" in section
    for workflow_id in CUSTOM_NODE_WORKFLOWS:
        assert f"`{workflow_id}`" not in section
    for workflow_id in ("minimax_h3_t2v", "minimax_h3_i2v", "minimax_h3_r2v"):
        assert f"`{workflow_id}`" in section
    target_section = section.split("## この環境の接続先")[1]
    assert "`_turbo` / `_opt` は選ばないこと" in target_section
    # 名指しなら従う、という但し書きは出さない（この接続先では動かないため）
    assert "ユーザーがワークフローを名指ししたとき" not in section


def test_the_catalog_can_be_limited_to_the_form_choices():
    """`only` に無い id はカタログに出さない（options の一覧と揃える）。"""
    section = prompts.workflow_catalog_section("local", {"minimax_h3_i2v"})
    assert "`minimax_h3_i2v`" in section
    assert "`minimax_h3_t2v` —" not in section


def test_the_session_prompt_follows_the_configured_target(env, monkeypatch):
    _set_target(monkeypatch, "local")
    system = start(env)["messages"][0]["content"]
    assert "この環境の接続先: `local`" in system
    assert "`minimax_h3_i2v_opt`" in system.split("## この環境の接続先")[1]

    _set_target(monkeypatch, "comfy_cloud")
    system = start(env)["messages"][0]["content"]
    assert "この環境の接続先: `comfy_cloud`" in system
    assert "`_turbo` / `_opt` は選ばないこと" in system


def test_the_comfy_cloud_session_prompt_omits_the_custom_node_workflows(
    env, monkeypatch
):
    """VIDEO WORKFLOWS のカタログにも CHOICES にも turbo / opt が出ない。"""
    _set_target(monkeypatch, "comfy_cloud")
    system = start(env)["messages"][0]["content"]
    for workflow_id in CUSTOM_NODE_WORKFLOWS:
        assert workflow_id not in system
    for workflow_id in ("minimax_h3_t2v", "minimax_h3_i2v", "minimax_h3_r2v"):
        assert f"`{workflow_id}`" in system

    # local では従来どおり全件載る
    _set_target(monkeypatch, "local")
    system = start(env)["messages"][0]["content"]
    for workflow_id in CUSTOM_NODE_WORKFLOWS:
        assert f"`{workflow_id}`" in system
