"""NSFW フラグのテスト（NSFW §2 / §3 / §4）。Grok と ComfyUI は完全にモックする。"""

import shutil
import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import agent_runner, agent_store, comfy, config, db, grok, jobs, nsfw
from app.main import app
from app.models import AgentSession, AgentTask, JobCreate, Settings
from app.routers import agent as agent_router

from conftest import fake_outputs

HAS_FFMPEG = shutil.which("ffmpeg") is not None
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg is not installed")


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------

class FakeCli:
    """`grok -p` の応答を固定で返す（判定は毎回同じ答えでよい）。"""

    def __init__(self, answer: str = '```json\n{"nsfw": true}\n```'):
        self.answer: str | Exception = answer
        self.calls = 0

    async def __call__(self, argv, cwd, timeout):
        self.calls += 1
        if isinstance(self.answer, Exception):
            raise self.answer
        return (0, self.answer, "")


class FakeComfy:
    """test_jobs.py の FakeComfy を最小化したもの。"""

    def __init__(self, video: Path | None):
        self.video = video
        self.outputs = fake_outputs()

    async def upload_file(self, path, subfolder=None):
        return Path(path).name

    async def queue_prompt(self, workflow, client_id):
        return "prompt-1"

    async def get_history(self, prompt_id):
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
         "-i", "testsrc=size=64x64:rate=25:duration=1",
         "-pix_fmt", "yuv420p", str(dest)],
        check=True,
        capture_output=True,
    )
    return dest


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
    monkeypatch.setattr(agent_store, "AGENT_SESSIONS_DIR", sessions)
    monkeypatch.setattr(agent_runner, "POLL_INTERVAL", 0.02)
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
            {"client": client, "cli": cli, "assets": assets, "audio": audio},
        )


def image_body(**overrides) -> dict:
    body = {"mode": "image_only", "image_prompt": "a portrait"}
    body.update(overrides)
    return body


def wait_job(client, job_id, predicate, timeout: float = 10.0) -> dict:
    """条件を満たすまでジョブを取り直す（バックグラウンド判定の完了待ち）。"""
    deadline = time.time() + timeout
    job = {}
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if predicate(job):
            return job
        time.sleep(0.02)
    raise AssertionError(f"job {job_id} が条件を満たしませんでした: {job}")


def wait_session(client, session_id, predicate, timeout: float = 10.0) -> dict:
    deadline = time.time() + timeout
    session = {}
    while time.time() < deadline:
        session = client.get(f"/api/agent/sessions/{session_id}").json()
        if predicate(session):
            return session
        time.sleep(0.02)
    raise AssertionError(f"session {session_id} が条件を満たしませんでした")


def judged(job: dict) -> bool:
    return job["nsfw_source"] != ""


# --------------------------------------------------------------------------
# 判定ロジック（§2）
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "text, expected",
    [
        ("a nude woman on a bed", True),
        ("全裸で踊る女性", True),
        ("ランジェリー姿ではなく下着姿", True),
        ("NSFW tag test", True),
        ("erotic close-up", True),
        ("a cat sitting on a chair", False),
        ("かおりが街を歩く様子", False),
        ("", False),
    ],
)
def test_heuristic(text, expected):
    assert nsfw.heuristic(text) is expected


async def test_classify_reads_the_json_answer(monkeypatch):
    monkeypatch.setattr(grok, "_exec", FakeCli('```json\n{"nsfw": true}\n```'))
    assert await nsfw.classify("something") is True

    monkeypatch.setattr(grok, "_exec", FakeCli('{"nsfw": false}'))
    assert await nsfw.classify("something") is False


async def test_classify_returns_none_when_it_cannot_decide(monkeypatch):
    # CLI が失敗した
    monkeypatch.setattr(grok, "_exec", FakeCli(grok.LLMError("grok CLI が失敗しました")))
    assert await nsfw.classify("something") is None

    # JSON が取り出せない
    monkeypatch.setattr(grok, "_exec", FakeCli("わかりません"))
    assert await nsfw.classify("something") is None

    # 空文字は Grok を呼ばずに None
    cli = FakeCli()
    monkeypatch.setattr(grok, "_exec", cli)
    assert await nsfw.classify("   ") is None
    assert cli.calls == 0


async def test_classify_or_heuristic_falls_back(monkeypatch):
    monkeypatch.setattr(grok, "_exec", FakeCli(grok.LLMError("down")))
    assert await nsfw.classify_or_heuristic("a nude woman") == (True, "auto")
    assert await nsfw.classify_or_heuristic("a cat") == (False, "auto")

    monkeypatch.setattr(grok, "_exec", FakeCli('{"nsfw": true}'))
    assert await nsfw.classify_or_heuristic("a cat") == (True, "auto")


# --------------------------------------------------------------------------
# ジョブ（§3）
# --------------------------------------------------------------------------

def test_job_is_created_unjudged_then_flagged_by_the_background_check(env):
    created = env.client.post("/api/jobs", json=image_body()).json()
    # 判定は生成をブロックしない: 作成時点では未判定
    assert created["nsfw"] is False
    assert created["nsfw_source"] == ""

    job = wait_job(env.client, created["id"], judged)
    assert job["nsfw"] is True
    assert job["nsfw_source"] == "auto"
    assert env.cli.calls >= 1


def test_manual_flag_is_not_overwritten_by_the_auto_check(env):
    # Grok は true と答えるが、明示指定（manual）が勝つ
    created = env.client.post("/api/jobs", json=image_body(nsfw=False)).json()
    assert created["nsfw"] is False
    assert created["nsfw_source"] == "manual"

    wait_job(env.client, created["id"], lambda job: job["status"] == "done")
    time.sleep(0.2)
    job = env.client.get(f"/api/jobs/{created['id']}").json()
    assert job["nsfw"] is False
    assert job["nsfw_source"] == "manual"
    assert env.cli.calls == 0  # 判定そのものが走らない


def test_explicit_nsfw_true_is_manual(env):
    created = env.client.post("/api/jobs", json=image_body(nsfw=True)).json()
    assert created["nsfw"] is True
    assert created["nsfw_source"] == "manual"


def test_nsfw_endpoint_toggles_manually(env):
    created = env.client.post("/api/jobs", json=image_body(nsfw=False)).json()

    response = env.client.post(f"/api/jobs/{created['id']}/nsfw", json={"nsfw": True})
    assert response.status_code == 200, response.text
    assert response.json()["nsfw"] is True
    assert response.json()["nsfw_source"] == "manual"

    back = env.client.post(f"/api/jobs/{created['id']}/nsfw", json={"nsfw": False}).json()
    assert back["nsfw"] is False
    assert back["nsfw_source"] == "manual"

    assert env.client.post("/api/jobs/nope/nsfw", json={"nsfw": True}).status_code == 404


def test_rerun_inherits_the_nsfw_flag(env):
    created = env.client.post("/api/jobs", json=image_body(nsfw=True)).json()
    wait_job(env.client, created["id"], lambda job: job["status"] == "done")

    rerun = env.client.post(f"/api/jobs/{created['id']}/rerun", json={}).json()
    assert rerun["nsfw"] is True
    assert rerun["nsfw_source"] == "auto"  # 継承なので判定は走らない
    assert env.cli.calls == 0


@needs_ffmpeg
def test_continue_inherits_the_nsfw_flag(env):
    first = env.client.post(
        "/api/jobs",
        json={
            "mode": "full",
            "image_prompt": "a portrait",
            "video_prompt": "she dances",
            "audio_path": str(env.audio),
            "duration": 1,
            "nsfw": True,
        },
    ).json()
    wait_job(env.client, first["id"], lambda job: job["status"] == "done")

    second = env.client.post(f"/api/jobs/{first['id']}/continue", json={}).json()
    assert second["nsfw"] is True
    assert second["nsfw_source"] == "auto"
    assert env.cli.calls == 0


# --------------------------------------------------------------------------
# エージェントセッション（§4）
# --------------------------------------------------------------------------

def start_session(env, goal: str = "ダンス動画を作りたい") -> dict:
    response = env.client.post(
        "/api/agent/sessions",
        json={"goal": goal, "checkin_mode": "milestone", "auto_limit": 5},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_session_goal_is_classified_in_the_background(env):
    created = start_session(env)
    assert created["nsfw"] is False
    assert created["nsfw_source"] == ""

    session = wait_session(env.client, created["id"], lambda s: s["nsfw_source"] != "")
    assert session["nsfw"] is True
    assert session["nsfw_source"] == "auto"

    summary = env.client.get("/api/agent/sessions").json()[0]
    assert summary["nsfw"] is True


def test_session_nsfw_endpoint_toggles_manually(env):
    created = start_session(env, goal="")
    assert created["nsfw_source"] == ""  # goal が空なので判定は走らない
    assert env.cli.calls == 0

    session = env.client.post(
        f"/api/agent/sessions/{created['id']}/nsfw", json={"nsfw": True}
    ).json()
    assert session["nsfw"] is True
    assert session["nsfw_source"] == "manual"

    assert (
        env.client.post("/api/agent/sessions/nope/nsfw", json={"nsfw": True}).status_code
        == 404
    )


def test_jobs_of_an_nsfw_session_inherit_the_flag(env):
    created = start_session(env, goal="")
    env.client.post(f"/api/agent/sessions/{created['id']}/nsfw", json={"nsfw": True})

    task = AgentTask(id="t1", label="①", job=image_body())

    async def run():
        await agent_runner.execute_task(created["id"], task)

    env.client.portal.call(run)  # type: ignore[attr-defined]

    assert task.job_id
    job = env.client.get(f"/api/jobs/{task.job_id}").json()
    assert job["nsfw"] is True
    assert job["nsfw_source"] == "auto"
    assert env.cli.calls == 0  # セッションからの継承なので判定は走らない


def test_an_auto_flagged_job_raises_its_session(env):
    created = start_session(env, goal="")

    async def create():
        return await jobs.create_job(
            JobCreate(**image_body(), chat_session_id=created["id"])
        )

    job = env.client.portal.call(create)  # type: ignore[attr-defined]
    wait_job(env.client, job.id, judged)

    session = wait_session(env.client, created["id"], lambda s: s["nsfw"])
    assert session["nsfw"] is True
    assert session["nsfw_source"] == "auto"


def test_manual_session_flag_survives_an_auto_job(env):
    created = start_session(env, goal="")
    env.client.post(f"/api/agent/sessions/{created['id']}/nsfw", json={"nsfw": False})

    async def create():
        # 継承しない（inherit_nsfw=False）ので判定が走り、auto で NSFW になる
        return await jobs.create_job(
            JobCreate(**image_body(), chat_session_id=created["id"])
        )

    job = env.client.portal.call(create)  # type: ignore[attr-defined]
    wait_job(env.client, job.id, judged)
    time.sleep(0.2)

    session = env.client.get(f"/api/agent/sessions/{created['id']}").json()
    assert session["nsfw"] is False
    assert session["nsfw_source"] == "manual"


def test_first_message_classifies_a_session_created_without_a_goal(env):
    created = start_session(env, goal="")
    session = AgentSession(**created)

    async def classify():
        # send_message は Grok ターンも回すので、判定の仕掛けだけを直接確かめる
        agent_router._classify_session(session, "全裸のシーンにして")

    env.client.portal.call(classify)  # type: ignore[attr-defined]
    updated = wait_session(env.client, created["id"], lambda s: s["nsfw_source"] != "")
    assert updated["nsfw"] is True
    assert updated["nsfw_source"] == "auto"
