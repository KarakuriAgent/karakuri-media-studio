"""Job lifecycle tests. ComfyUI is fully mocked; ffmpeg is used for real."""

import asyncio
import shutil
import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import comfy, db, jobs, nsfw
from app.main import app

from conftest import fake_outputs

HAS_FFMPEG = shutil.which("ffmpeg") is not None
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg is not installed")


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------

def _make_video(path: Path, duration: float) -> Path:
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi",
            "-i", f"testsrc=size=64x64:rate=25:duration={duration}",
            "-pix_fmt", "yuv420p", str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


@pytest.fixture(scope="session")
def sample_video(tmp_path_factory) -> Path:
    if not HAS_FFMPEG:
        pytest.skip("ffmpeg is not installed")
    return _make_video(tmp_path_factory.mktemp("media") / "sample.mp4", 1.0)


class FakeComfy:
    """Records the calls the job runner makes and replays canned answers."""

    def __init__(self, video: Path | None):
        self.video = video
        self.uploads: list[str] = []
        self.queued: list[dict] = []
        self.history_calls = 0
        self.queue_error: Exception | None = None
        self.history_status = "success"
        self.outputs = fake_outputs()

    async def upload_file(self, path, subfolder=None):
        self.uploads.append(str(path))
        return Path(path).name

    async def queue_prompt(self, workflow, client_id):
        if self.queue_error:
            raise self.queue_error
        self.queued.append(workflow)
        return f"prompt-{len(self.queued)}"

    async def get_history(self, prompt_id):
        self.history_calls += 1
        if self.history_status == "error":
            return {
                "status": {
                    "status_str": "error",
                    "messages": [
                        ["execution_error", {"node_type": "KSampler",
                                             "exception_message": "OOM"}]
                    ],
                },
                "outputs": {},
            }
        if self.history_calls < 2:  # still queued on the first poll
            return {}
        return {"status": {"status_str": "success", "completed": True},
                "outputs": self.outputs}

    async def download_view(self, filename, subfolder, type_, dest_path):
        dest = Path(dest_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if filename.endswith(".mp4") and self.video is not None:
            shutil.copy2(self.video, dest)
        else:
            dest.write_bytes(b"fake-image")
        return dest

    def ws_url(self, client_id):
        # Refused immediately -> exercises the /history polling fallback.
        return f"ws://127.0.0.1:1/ws?clientId={client_id}"


async def _no_llm(text: str) -> None:
    """NSFW 判定の LLM を使わない差し替え（ヒューリスティックに落ちる）。"""
    return None


@pytest.fixture
def env(tmp_path, monkeypatch, request):
    """Isolated DB / assets / outputs plus a mocked ComfyUI, wrapped in a client."""
    video = None
    if HAS_FFMPEG:
        video = request.getfixturevalue("sample_video")

    assets = tmp_path / "assets"
    outputs = tmp_path / "outputs"
    (assets / "audio").mkdir(parents=True)
    (assets / "image").mkdir(parents=True)
    outputs.mkdir()

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(jobs, "ASSETS_DIR", assets)
    monkeypatch.setattr(jobs, "OUTPUTS_DIR", outputs)
    monkeypatch.setattr(jobs, "POLL_INTERVAL", 0.02)
    # NSFW 自動判定は test_nsfw.py で検証する。ここでは Grok を呼ばせない
    # （ヒューリスティックだけが走る）。
    monkeypatch.setattr(nsfw, "classify", _no_llm)

    fake = FakeComfy(video)
    for name in ("upload_file", "queue_prompt", "get_history", "download_view", "ws_url"):
        monkeypatch.setattr(comfy, name, getattr(fake, name))

    audio = assets / "audio" / "ref.mp3"
    audio.write_bytes(b"ID3")
    start_image = assets / "image" / "start.png"
    start_image.write_bytes(b"PNG")
    end_image = assets / "image" / "end.png"
    end_image.write_bytes(b"PNG")
    (assets / "video").mkdir(parents=True, exist_ok=True)
    reference_video = assets / "video" / "ref.mp4"
    reference_video.write_bytes(b"\x00\x00\x00 ftypmp42")

    with TestClient(app) as client:
        yield type(
            "Env",
            (),
            {
                "client": client,
                "comfy": fake,
                "assets": assets,
                "outputs": outputs,
                "audio": audio,
                "start_image": start_image,
                "end_image": end_image,
                "reference_video": reference_video,
            },
        )


def graph_with(env, node_id: str) -> dict:
    """The last submitted graph that contains ``node_id`` (one stage of a job)."""
    for workflow in reversed(env.comfy.queued):
        if node_id in workflow:
            return workflow
    raise AssertionError(f"no submitted workflow contains {node_id}")


def full_body(env, **overrides) -> dict:
    body = {
        "mode": "full",
        "image_prompt": "an image",
        "video_prompt": "a video",
        "audio_path": str(env.audio),
        "duration": 2,
        "fps": 25,
    }
    body.update(overrides)
    return body


def wait_for(client, job_id, statuses=("done", "failed"), timeout=30.0) -> dict:
    deadline = time.time() + timeout
    job = {}
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in statuses:
            return job
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} stuck in {job.get('status')!r}")


# --------------------------------------------------------------------------
# happy paths
# --------------------------------------------------------------------------

@needs_ffmpeg
def test_full_job_runs_two_chained_stages(env):
    response = env.client.post("/api/jobs", json=full_body(env))
    assert response.status_code == 201, response.text
    created = response.json()
    assert created["status"] == "queued"
    assert created["params"]["seed"] > 0  # random seed recorded for reproducibility
    assert created["params"]["video_workflow"] == "ltx2_3_id_lora"

    job = wait_for(env.client, created["id"])
    assert job["status"] == "done", job["error"]
    assert Path(job["image_path"]).is_file()
    assert Path(job["video_path"]).is_file()
    assert Path(job["last_frame_path"]).is_file()
    assert job["video_url"] == f"/outputs/{job['id']}/video.mp4"

    # SPEC §2: one job id, two ComfyUI prompts, both graphs persisted
    assert len(env.comfy.queued) == 2
    stages = job["workflow_json"]
    assert sorted(stages) == ["image", "video"]
    assert stages["image"]["workflow_id"] == "krea2_turbo"
    assert stages["video"]["workflow_id"] == "ltx2_3_id_lora"
    assert stages["image"]["prompt_id"] == "prompt-1"
    assert stages["video"]["prompt_id"] == "prompt-2"
    assert job["comfy_prompt_id"] == "prompt-2"

    image_graph, video_graph = env.comfy.queued
    assert image_graph["30:3"]["inputs"]["seed"] == job["params"]["seed"]
    assert video_graph["276"]["inputs"]["audio"] == "ref.mp3"
    # the generated still was uploaded and wired in as the start frame
    assert env.comfy.uploads[0] == str(env.audio)
    assert env.comfy.uploads[-1] == job["image_path"]
    assert video_graph["269"]["inputs"]["image"] == Path(job["image_path"]).name


def test_image_only_job_runs_the_image_stage_only(env):
    body = {"mode": "image_only", "image_prompt": "just an image"}
    created = env.client.post("/api/jobs", json=body).json()
    job = wait_for(env.client, created["id"])
    assert job["status"] == "done", job["error"]
    assert Path(job["image_path"]).is_file()
    assert job["video_path"] is None
    assert job["last_frame_path"] is None
    assert len(env.comfy.queued) == 1
    assert env.comfy.queued[0]["29"]["class_type"] == "SaveImage"
    assert list(job["workflow_json"]) == ["image"]


def test_t2v_job_needs_no_input_assets(env):
    created = env.client.post(
        "/api/jobs",
        json={
            "mode": "i2v",
            "video_workflow": "ltx2_3_t2v",
            "video_prompt": "a machine assembles itself",
        },
    ).json()
    job = wait_for(env.client, created["id"])
    assert job["status"] == "done", job["error"]
    assert len(env.comfy.queued) == 1
    assert env.comfy.queued[0]["267:266"]["inputs"]["value"] == "a machine assembles itself"
    assert list(job["workflow_json"]) == ["video"]


def test_flf2v_job_uploads_both_frames(env):
    created = env.client.post(
        "/api/jobs",
        json={
            "mode": "i2v",
            "video_workflow": "ltx2_3_flf2v",
            "video_prompt": "the camera drops",
            "source_image": str(env.start_image),
            "end_image": str(env.end_image),
        },
    ).json()
    job = wait_for(env.client, created["id"])
    assert job["status"] == "done", job["error"]
    workflow = env.comfy.queued[0]
    assert workflow["31"]["inputs"]["image"] == "start.png"
    assert workflow["39"]["inputs"]["image"] == "end.png"
    assert str(env.end_image) in env.comfy.uploads


def test_motion_job_uploads_the_reference_clip(env):
    created = env.client.post(
        "/api/jobs",
        json={
            "mode": "i2v",
            "video_workflow": "ltx2_3_ic_lora_motion",
            "video_prompt": "a slow dolly",
            "source_image": str(env.start_image),
            "reference_video": str(env.reference_video),
            "duration": 4,
        },
    ).json()
    job = wait_for(env.client, created["id"])
    assert job["status"] == "done", job["error"]
    workflow = env.comfy.queued[0]
    assert workflow["199"]["inputs"]["file"] == "ref.mp4"
    assert workflow["692"]["inputs"]["duration"] == 4.0


def test_full_mode_rejects_a_workflow_without_a_start_frame(env):
    response = env.client.post(
        "/api/jobs", json=full_body(env, video_workflow="ltx2_3_t2v")
    )
    assert response.status_code == 422
    assert "start frame" in response.text


def test_unknown_video_workflow_is_422(env):
    response = env.client.post("/api/jobs", json=full_body(env, video_workflow="nope"))
    assert response.status_code == 422
    assert "nope" in response.text


def test_fixed_seed_is_used_verbatim(env):
    created = env.client.post(
        "/api/jobs", json=full_body(env, seed=4242)
    ).json()
    assert created["params"]["seed"] == 4242
    wait_for(env.client, created["id"])


def test_list_and_delete(env, tmp_path):
    created = env.client.post("/api/jobs", json=full_body(env)).json()
    job = wait_for(env.client, created["id"])
    job_dir = env.outputs / job["id"]
    assert job_dir.is_dir()

    listing = env.client.get("/api/jobs?limit=10&offset=0").json()
    assert [j["id"] for j in listing] == [job["id"]]
    assert listing[0]["workflow_json"] == {}  # trimmed in the list view

    assert env.client.delete(f"/api/jobs/{job['id']}").status_code == 204
    assert env.client.get(f"/api/jobs/{job['id']}").status_code == 404
    assert not job_dir.exists()
    assert env.client.delete(f"/api/jobs/{job['id']}").status_code == 404


def test_chat_session_is_linked(env):
    session_id = "chat-1"

    async def seed():
        async with db.get_db() as conn:
            await conn.execute(
                "INSERT INTO chat_sessions (id, created_at, messages) VALUES (?,?,?)",
                (session_id, "2026-01-01T00:00:00Z", "[]"),
            )
            await conn.commit()

    env.client.portal.call(seed)  # type: ignore[attr-defined]
    created = env.client.post(
        "/api/jobs", json=full_body(env, chat_session_id=session_id, user_input="踊って")
    ).json()

    async def linked():
        async with db.get_db() as conn:
            async with conn.execute(
                "SELECT job_id FROM chat_sessions WHERE id = ?", (session_id,)
            ) as cur:
                row = await cur.fetchone()
        return row["job_id"]

    assert env.client.portal.call(linked) == created["id"]  # type: ignore[attr-defined]
    assert created["user_input"] == "踊って"


# --------------------------------------------------------------------------
# validation (SPEC §9 -> 422)
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "body, missing",
    [
        ({"mode": "full", "image_prompt": "i", "video_prompt": "v"}, "audio_path"),
        ({"mode": "full", "video_prompt": "v", "audio_path": "A"}, "image_prompt"),
        ({"mode": "full", "image_prompt": "i", "audio_path": "A"}, "video_prompt"),
        ({"mode": "i2v", "video_prompt": "v", "audio_path": "A"}, "source_image"),
        ({"mode": "i2v", "source_image": "S", "video_prompt": "v"}, "audio_path"),
        ({"mode": "i2v", "audio_path": "A", "source_image": "S"}, "video_prompt"),
        ({"mode": "image_only"}, "image_prompt"),
    ],
)
def test_missing_fields_are_422(env, body, missing):
    payload = dict(body)
    if payload.get("audio_path") == "A":
        payload["audio_path"] = str(env.audio)
    if payload.get("source_image") == "S":
        payload["source_image"] = str(env.start_image)
    response = env.client.post("/api/jobs", json=payload)
    assert response.status_code == 422
    assert missing in response.text


def test_asset_outside_assets_dir_is_422(env, tmp_path):
    stray = tmp_path / "outside.mp3"
    stray.write_bytes(b"x")
    response = env.client.post("/api/jobs", json=full_body(env, audio_path=str(stray)))
    assert response.status_code == 422
    assert "audio_path" in response.text


def test_assets_url_form_is_accepted(env):
    created = env.client.post(
        "/api/jobs", json=full_body(env, audio_path="/assets/audio/ref.mp3")
    ).json()
    assert created["audio_path"] == str(env.audio)
    wait_for(env.client, created["id"])


# --------------------------------------------------------------------------
# failures
# --------------------------------------------------------------------------

def test_comfy_failure_marks_job_failed(env):
    env.comfy.queue_error = comfy.ComfyError("ComfyUI is down")
    created = env.client.post("/api/jobs", json=full_body(env)).json()
    job = wait_for(env.client, created["id"])
    assert job["status"] == "failed"
    assert "ComfyUI is down" in job["error"]


def test_execution_error_from_history_marks_job_failed(env):
    env.comfy.history_status = "error"
    created = env.client.post("/api/jobs", json=full_body(env)).json()
    job = wait_for(env.client, created["id"])
    assert job["status"] == "failed"
    assert "OOM" in job["error"]


def test_missing_video_output_marks_job_failed(env):
    env.comfy.outputs = {"29": {"images": [{"filename": "i.png", "type": "output"}]}}
    created = env.client.post("/api/jobs", json=full_body(env)).json()
    job = wait_for(env.client, created["id"])
    assert job["status"] == "failed"
    assert "no video output" in job["error"]
    # the image stage still ran: its graph and its still are kept
    assert list(job["workflow_json"]) == ["image", "video"]
    assert Path(job["image_path"]).is_file()


def test_missing_image_output_fails_before_the_video_stage(env):
    env.comfy.outputs = {"341": {"videos": [{"filename": "v.mp4", "type": "output"}]}}
    created = env.client.post("/api/jobs", json=full_body(env)).json()
    job = wait_for(env.client, created["id"])
    assert job["status"] == "failed"
    assert "no image output" in job["error"]
    assert len(env.comfy.queued) == 1  # the video stage was never queued


def test_missing_ffmpeg_marks_job_failed(env, monkeypatch):
    monkeypatch.setattr(jobs, "FFMPEG", "ffmpeg-does-not-exist")
    created = env.client.post("/api/jobs", json=full_body(env)).json()
    job = wait_for(env.client, created["id"])
    assert job["status"] == "failed"
    assert "ffmpeg" in job["error"]


# --------------------------------------------------------------------------
# rerun / continue
# --------------------------------------------------------------------------

@needs_ffmpeg
def test_rerun_rebuilds_with_new_seed(env):
    first = env.client.post("/api/jobs", json=full_body(env, seed=7)).json()
    wait_for(env.client, first["id"])

    same = env.client.post(
        f"/api/jobs/{first['id']}/rerun", json={"randomize_seed": False}
    ).json()
    assert same["params"]["seed"] == 7
    wait_for(env.client, same["id"])

    fixed = env.client.post(
        f"/api/jobs/{first['id']}/rerun", json={"seed": 99}
    ).json()
    assert fixed["params"]["seed"] == 99
    assert fixed["id"] != first["id"]
    wait_for(env.client, fixed["id"])

    random_run = env.client.post(f"/api/jobs/{first['id']}/rerun", json={}).json()
    job = wait_for(env.client, random_run["id"])
    assert job["status"] == "done", job["error"]
    # rebuilt from params, not replayed from the stored workflow_json
    assert graph_with(env, "30:3")["30:3"]["inputs"]["seed"] == job["params"]["seed"]

    assert env.client.post("/api/jobs/nope/rerun", json={}).status_code == 404


@needs_ffmpeg
def test_continue_chains_from_last_frame(env):
    first = env.client.post("/api/jobs", json=full_body(env)).json()
    done = wait_for(env.client, first["id"])
    assert done["status"] == "done", done["error"]

    second = env.client.post(
        f"/api/jobs/{first['id']}/continue",
        json={"video_prompt": "she keeps dancing", "duration": 3},
    ).json()
    assert second["mode"] == "i2v"
    assert second["video_prompt"] == "she keeps dancing"
    assert second["params"]["continued_from"] == first["id"]
    assert second["params"]["duration"] == 3

    # the last frame was copied into assets/ so it can be reused later
    start = Path(second["source_image"])
    assert start.is_file()
    assert start.parent == env.assets / "image"

    job = wait_for(env.client, second["id"])
    assert job["status"] == "done", job["error"]
    workflow = env.comfy.queued[-1]
    assert workflow["269"]["inputs"]["image"] == start.name
    assert "30:19" not in workflow  # the video stage runs on its own
    assert list(job["workflow_json"]) == ["video"]
    assert str(start) in env.comfy.uploads

    # chaining once more works too
    third = env.client.post(f"/api/jobs/{job['id']}/continue", json={}).json()
    assert third["mode"] == "i2v"
    assert wait_for(env.client, third["id"])["status"] == "done"


def test_continue_without_last_frame_is_422(env):
    created = env.client.post(
        "/api/jobs", json={"mode": "image_only", "image_prompt": "i"}
    ).json()
    wait_for(env.client, created["id"])
    response = env.client.post(f"/api/jobs/{created['id']}/continue", json={})
    assert response.status_code == 422
    assert "last frame" in response.text
    assert env.client.post("/api/jobs/nope/continue", json={}).status_code == 404


# --------------------------------------------------------------------------
# unit tests
# --------------------------------------------------------------------------

@needs_ffmpeg
async def test_extract_last_frame(tmp_path, sample_video):
    dest = await jobs.extract_last_frame(sample_video, tmp_path / "last.png")
    assert dest.is_file() and dest.stat().st_size > 0


@needs_ffmpeg
async def test_extract_last_frame_falls_back_on_short_clip(tmp_path):
    short = _make_video(tmp_path / "short.mp4", 0.2)  # shorter than -sseof 0.5
    dest = await jobs.extract_last_frame(short, tmp_path / "last.png")
    assert dest.is_file() and dest.stat().st_size > 0


async def test_extract_last_frame_without_ffmpeg(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "FFMPEG", "ffmpeg-does-not-exist")
    with pytest.raises(jobs.JobError, match="ffmpeg"):
        await jobs.extract_last_frame(tmp_path / "x.mp4", tmp_path / "last.png")


async def test_ws_progress_survives_a_dead_socket(monkeypatch):
    """A refused ComfyUI socket must not raise (polling takes over)."""
    monkeypatch.setattr(comfy, "ws_url", lambda cid: f"ws://127.0.0.1:1/ws?clientId={cid}")
    finished = asyncio.Event()
    await jobs._ws_progress("cid", "pid", "job", finished)
    assert not finished.is_set()


async def test_hub_broadcasts_and_drops_dead_sockets():
    from app.ws import Hub, hub, publish

    class FakeSocket:
        def __init__(self, broken=False):
            self.broken = broken
            self.sent: list[dict] = []

        async def send_json(self, payload):
            if self.broken:
                raise RuntimeError("closed")
            self.sent.append(payload)

    local = Hub()
    good, bad = FakeSocket(), FakeSocket(broken=True)
    await local.register(good)  # type: ignore[arg-type]
    await local.register(bad)  # type: ignore[arg-type]
    await local.broadcast({"hello": "world"})
    assert good.sent == [{"hello": "world"}]
    assert local.count == 1

    listener = FakeSocket()
    await hub.register(listener)  # type: ignore[arg-type]
    try:
        await publish("job-1", "running", node="365:3", progress=0.5, message="hi")
    finally:
        await hub.unregister(listener)  # type: ignore[arg-type]
    assert listener.sent == [
        {
            "type": "job",
            "job_id": "job-1",
            "status": "running",
            "node": "365:3",
            "progress": 0.5,
            "message": "hi",
            "nsfw": None,
        }
    ]


def test_ws_endpoint_receives_job_events(env):
    with env.client.websocket_connect("/api/ws") as socket:
        created = env.client.post(
            "/api/jobs", json={"mode": "image_only", "image_prompt": "i"}
        ).json()
        statuses = []
        for _ in range(6):
            message = socket.receive_json()
            statuses.append(message["status"])
            assert message["job_id"] == created["id"]
            if message["status"] in ("done", "failed"):
                break
    assert statuses[0] == "queued"
    assert "running" in statuses
    assert statuses[-1] == "done"
