"""Agent mode tests (AGENT-MODE §4 / §5). Grok と ComfyUI は完全にモックする。"""

import json
import shutil
import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import (
    agent_protocol,
    agent_runner,
    agent_store,
    comfy,
    config,
    db,
    grok,
    jobs,
    nsfw,
)
from app.main import app
from app.models import AgentSession, Settings
from app.routers import assets as assets_router

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
        self.outputs = {
            "393": {"images": [{"filename": "img.png", "subfolder": "", "type": "temp"}]},
            "75": {"videos": [{"filename": "vid.mp4", "subfolder": "", "type": "output"}]},
        }

    async def upload_file(self, path, subfolder=None):
        self.uploads.append(str(path))
        return Path(path).name

    async def queue_prompt(self, workflow, client_id):
        if self.fail:
            raise comfy.ComfyError("ComfyUI is down")
        self.queued.append(workflow)
        return f"prompt-{len(self.queued)}"

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


@pytest.fixture
def env(tmp_path, monkeypatch, request):
    video = request.getfixturevalue("sample_video") if HAS_FFMPEG else None

    assets = tmp_path / "assets"
    outputs = tmp_path / "outputs"
    sessions = tmp_path / "agent-sessions"
    (assets / "audio").mkdir(parents=True)
    (assets / "image").mkdir(parents=True)
    outputs.mkdir()
    sessions.mkdir()

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(jobs, "ASSETS_DIR", assets)
    monkeypatch.setattr(jobs, "OUTPUTS_DIR", outputs)
    monkeypatch.setattr(jobs, "POLL_INTERVAL", 0.02)
    monkeypatch.setattr(assets_router, "ASSETS_DIR", assets)
    monkeypatch.setattr(agent_store, "AGENT_SESSIONS_DIR", sessions)
    monkeypatch.setattr(agent_runner, "POLL_INTERVAL", 0.02)
    # NSFW 自動判定は test_nsfw.py で検証する（ここでは Grok を呼ばせない）。
    monkeypatch.setattr(nsfw, "classify", _no_llm)
    monkeypatch.setattr(
        config,
        "_settings",
        Settings(grok_command="grok", grok_model="grok-4.5", grok_workdir=str(tmp_path)),
    )

    fake_comfy = FakeComfy(video)
    for name in ("upload_file", "queue_prompt", "get_history", "download_view",
                 "ws_url", "get_object_info"):
        monkeypatch.setattr(comfy, name, getattr(fake_comfy, name))

    cli = FakeCli()
    monkeypatch.setattr(grok, "_exec", cli)

    audio = assets / "audio" / "ref.mp3"
    audio.write_bytes(b"ID3")

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
                "audio": audio,
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
        "tasks": [{"label": "x", "job": job_body(env, audio_path=None)}],
    }
    with pytest.raises(agent_protocol.ActionError, match="audio_path"):
        agent_protocol.parse_action(action_answer(broken))

    stray = {
        "action": "plan",
        "tasks": [{"label": "x", "job": job_body(env, audio_path="/etc/passwd")}],
    }
    with pytest.raises(agent_protocol.ActionError, match="audio_path"):
        agent_protocol.parse_action(action_answer(stray))


def test_unknown_lora_is_rejected(env):
    payload = {
        "action": "plan",
        "tasks": [
            {
                "label": "x",
                "job": job_body(
                    env,
                    loras=[{"lora_name": "ghost.safetensors", "trigger_word": "g"}],
                ),
            }
        ],
    }
    with pytest.raises(agent_protocol.ActionError, match="存在しない LoRA"):
        agent_protocol.parse_action(
            action_answer(payload), known_loras={"kaori.safetensors"}
        )


def test_plan_over_the_task_limit_is_rejected(env):
    with pytest.raises(agent_protocol.ActionError, match="最大"):
        agent_protocol.parse_action(plan_answer(env, 3), max_tasks=2)


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
    assert len(env.comfy.queued) == 1

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
    assert len(env.comfy.queued) == 1  # 2 本目は保留

    response = env.client.post(
        f"/api/agent/sessions/{session['id']}/checkin", json={"choice": "そのまま"}
    )
    assert response.status_code == 200, response.text
    final = wait_status(env, session["id"], ("done", "stopped", "idle"))
    assert final["status"] == "done"
    assert len(env.comfy.queued) == 2


def test_every_job_mode_checks_in_after_each_job(env):
    session = start(env, checkin_mode="every_job")
    env.cli.answers = [plan_answer(env, 2), action_answer({"action": "run_task"}, "続けます")]
    say(env, session["id"])
    env.client.post(f"/api/agent/sessions/{session['id']}/approve", json={})

    paused = wait_status(env, session["id"], ("waiting_checkin", "stopped", "done"))
    assert paused["status"] == "waiting_checkin"
    assert len(env.comfy.queued) == 1


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
    assert len(env.comfy.queued) == 1


def test_auto_limit_stops_the_loop(env):
    session = start(env, checkin_mode="auto", auto_limit=1)
    env.cli.answers = [plan_answer(env, 2), "1本目ができました。次に進みます。"]
    say(env, session["id"])
    env.client.post(f"/api/agent/sessions/{session['id']}/approve", json={})

    final = wait_status(env, session["id"], ("stopped", "done", "idle"))
    assert final["status"] == "stopped"
    assert len(env.comfy.queued) == 1
    assert "上限" in final["messages"][-1]["content"]
    assert final["plan"]["tasks"][1]["status"] == "pending"


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
    queued_before = len(env.comfy.queued)

    env.cli.answers = [
        action_answer({"action": "continue", "job_id": job_id}, "続きを作ります。"),
    ]
    say(env, session["id"], "続きを作って")
    paused = wait_status(env, session["id"], ("waiting_checkin", "stopped", "done"))
    assert paused["status"] == "waiting_checkin"
    assert len(env.comfy.queued) == queued_before  # 承認前は投入しない
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
    assert len(env.comfy.queued) == queued_before + 1
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
    queued_before = len(env.comfy.queued)

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
    assert len(env.comfy.queued) == queued_before  # 1 本も追加されない
    # 応答済みの承認は消費されるので、同じ保留が二度走ることはない
    assert agent_runner.pending_approval(AgentSession(**final)) is None


def test_auto_mode_runs_out_of_plan_rerun_immediately(env):
    session = start(env, checkin_mode="auto", auto_limit=5)
    job_id = _first_job(env, session["id"])
    queued_before = len(env.comfy.queued)

    env.cli.answers = [
        action_answer({"action": "rerun", "job_id": job_id}, "やり直します。"),
        DONE_ANSWER,
    ]
    say(env, session["id"], "シードを引き直して")
    final = wait_status(env, session["id"], ("done", "stopped", "idle"))
    assert len(env.comfy.queued) == queued_before + 1  # 自走モードは即実行
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
