"""Job lifecycle tests. ComfyUI is fully mocked; ffmpeg is used for real."""

import asyncio
import shutil
import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import comfy, config, db, jobs, nsfw
from app.main import app
from app.workflow import resolution, resolution_for_image
from app.workflows import get_video_spec

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


def _submitted_size(workflow: dict, workflow_id: str) -> tuple[int, int]:
    spec = get_video_spec(workflow_id)
    return tuple(
        workflow[spec.inject[name].node_id]["inputs"][spec.inject[name].field]
        for name in ("width", "height")
    )


def test_start_frame_sets_the_output_aspect_ratio(env):
    """A portrait reference image beats the landscape preset (SPEC §3.1)."""
    portrait = env.assets / "image" / "portrait.png"
    Image.new("RGB", (1000, 1500), "black").save(portrait)

    created = env.client.post(
        "/api/jobs",
        json={
            "mode": "i2v",
            "video_workflow": "tx2_3_i2v",
            "video_prompt": "she turns around",
            "source_image": str(portrait),
            "aspect_ratio": "16:9 (Widescreen)",
            "megapixels": 1.0,
        },
    ).json()
    job = wait_for(env.client, created["id"])
    assert job["status"] == "done", job["error"]
    assert _submitted_size(env.comfy.queued[0], "tx2_3_i2v") == resolution_for_image(
        1000, 1500, 1.0, multiple=get_video_spec("tx2_3_i2v").resolution_multiple
    )


def test_unreadable_start_frame_falls_back_to_the_preset(env):
    """`start.png` is not a real image: the preset must still be honoured."""
    created = env.client.post(
        "/api/jobs",
        json={
            "mode": "i2v",
            "video_workflow": "tx2_3_i2v",
            "video_prompt": "she turns around",
            "source_image": str(env.start_image),
            "aspect_ratio": "16:9 (Widescreen)",
            "megapixels": 1.0,
        },
    ).json()
    job = wait_for(env.client, created["id"])
    assert job["status"] == "done", job["error"]
    assert _submitted_size(env.comfy.queued[0], "tx2_3_i2v") == resolution(
        "16:9 (Widescreen)", 1.0, multiple=get_video_spec("tx2_3_i2v").resolution_multiple
    )


@needs_ffmpeg
def test_full_mode_keeps_the_preset_for_the_generated_still(env):
    """The still the image stage produces already follows the preset."""
    portrait = env.assets / "image" / "portrait.png"
    Image.new("RGB", (1000, 1500), "black").save(portrait)

    created = env.client.post(
        "/api/jobs",
        json=full_body(
            env,
            video_workflow="tx2_3_i2v",
            source_image=str(portrait),
            aspect_ratio="16:9 (Widescreen)",
            megapixels=1.0,
        ),
    ).json()
    job = wait_for(env.client, created["id"])
    assert job["status"] == "done", job["error"]
    assert _submitted_size(env.comfy.queued[1], "tx2_3_i2v") == resolution(
        "16:9 (Widescreen)", 1.0, multiple=get_video_spec("tx2_3_i2v").resolution_multiple
    )


def test_reference_sheet_ignores_the_start_frame_size(env):
    """The IC-LoRA sheet sizes a ResizeAndPadImage target: preset only."""
    portrait = env.assets / "image" / "portrait.png"
    Image.new("RGB", (1000, 1500), "black").save(portrait)

    created = env.client.post(
        "/api/jobs",
        json={
            "mode": "i2v",
            "video_workflow": "ltx2_3_ic_lora_image",
            "video_prompt": "a character sheet",
            "source_image": str(portrait),
            "aspect_ratio": "16:9 (Widescreen)",
            "megapixels": 1.0,
        },
    ).json()
    job = wait_for(env.client, created["id"])
    assert job["status"] == "done", job["error"]
    assert _submitted_size(env.comfy.queued[0], "ltx2_3_ic_lora_image") == resolution(
        "16:9 (Widescreen)",
        1.0,
        multiple=get_video_spec("ltx2_3_ic_lora_image").resolution_multiple,
    )


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


VIDEO_LORA = {
    "lora_name": "motion.safetensors",
    "trigger_word": "slowmo",
    "strength": 0.9,
}


@needs_ffmpeg
def test_video_loras_are_snapshotted_and_injected(env):
    created = env.client.post(
        "/api/jobs",
        json=full_body(env, video_loras=[VIDEO_LORA], video_trigger_text="slowmo"),
    ).json()
    assert created["params"]["video_loras"] == [VIDEO_LORA]
    assert created["params"]["video_trigger_text"] == "slowmo"

    job = wait_for(env.client, created["id"])
    assert job["status"] == "done", job["error"]

    # spliced into the video graph only…
    video = graph_with(env, "340:346")
    assert video["app_video_lora_0"]["inputs"]["lora_name"] == "motion.safetensors"
    assert video["app_video_lora_0"]["inputs"]["strength_model"] == 0.9
    assert video["340:346"]["inputs"]["model"] == ["app_video_lora_0", 0]
    # …and the trigger word leads the video prompt
    assert video["340:319"]["inputs"]["value"].startswith("slowmo, ")
    # …never into the image graph
    assert not [n for n in graph_with(env, "30:3") if n.startswith("app_video_lora_")]


@needs_ffmpeg
def test_rerun_keeps_the_video_loras(env):
    first = env.client.post(
        "/api/jobs", json=full_body(env, video_loras=[VIDEO_LORA])
    ).json()
    wait_for(env.client, first["id"])
    again = env.client.post(f"/api/jobs/{first['id']}/rerun", json={}).json()
    assert again["params"]["video_loras"] == [VIDEO_LORA]
    assert wait_for(env.client, again["id"])["status"] == "done"


@needs_ffmpeg
def test_continue_keeps_the_video_loras(env):
    first = env.client.post(
        "/api/jobs", json=full_body(env, video_loras=[VIDEO_LORA])
    ).json()
    wait_for(env.client, first["id"])
    second = env.client.post(f"/api/jobs/{first['id']}/continue", json={}).json()
    assert second["params"]["video_loras"] == [VIDEO_LORA]


def test_a_job_without_video_loras_still_loads(env):
    """Params stored before the feature existed have no `video_loras` key."""
    created = env.client.post("/api/jobs", json=full_body(env)).json()
    assert created["params"]["video_loras"] == []
    job = jobs.Job(**{**created, "params": {k: v for k, v in created["params"].items()
                                            if k != "video_loras"}})
    assert jobs._generation_params(job, {}).video_loras == []


def test_video_loras_without_a_video_stage_are_422(env):
    response = env.client.post(
        "/api/jobs",
        json={
            "mode": "image_only",
            "image_prompt": "an image",
            "video_loras": [VIDEO_LORA],
        },
    )
    assert response.status_code == 422
    assert "video_loras" in response.text


# --------------------------------------------------------------------------
# ジョブ単位のモデル切り替え（SPEC §3.3）
# --------------------------------------------------------------------------

IMAGE_SLOT = "krea2_turbo/30:10.unet_name"
VIDEO_SLOT = "ltx2_3_id_lora/340:317.ckpt_name"


def _register_choices(
    monkeypatch,
    choices: dict[str, list[str]],
    overrides: dict[str, str] | None = None,
) -> None:
    """設定のモデル既定値と候補リストを差し替える。

    実際の ``runtime/config.json`` の内容（利用者が登録した上書き / 候補）に
    左右されないよう、どちらも明示的に置き換える（ファイルは書き換えない）。
    """
    monkeypatch.setattr(
        config,
        "_settings",
        # どちらも接続先ごとに持つ（SPEC §5）。テストは既定の 'local' 環境。
        config.load_settings().model_copy(
            update={
                # 接続先も固定する（実際の config.json に左右されないため）
                "comfy_target": "local",
                "model_overrides": {"local": overrides or {}},
                "model_choices": {"local": choices},
            }
        ),
    )


def _image_job(env, **overrides) -> dict:
    body = {"mode": "image_only", "image_prompt": "an image"}
    body.update(overrides)
    return env.client.post("/api/jobs", json=body)


def test_a_job_can_pick_a_model_from_the_choices(env, monkeypatch):
    _register_choices(monkeypatch, {IMAGE_SLOT: ["alt.safetensors"]})
    response = _image_job(env, model_overrides={IMAGE_SLOT: "alt.safetensors"})
    assert response.status_code == 201, response.text
    job = wait_for(env.client, response.json()["id"])
    assert job["status"] == "done", job["error"]
    assert job["params"]["model_overrides"] == {IMAGE_SLOT: "alt.safetensors"}
    # 設定の既定値ではなくジョブの指定がグラフに入る
    assert graph_with(env, "30:10")["30:10"]["inputs"]["unet_name"] == "alt.safetensors"


def test_a_model_outside_the_choices_is_422(env, monkeypatch):
    _register_choices(monkeypatch, {IMAGE_SLOT: ["alt.safetensors"]})
    response = _image_job(env, model_overrides={IMAGE_SLOT: "nope.safetensors"})
    assert response.status_code == 422
    assert "nope.safetensors" in response.text


def test_the_configured_default_is_always_selectable(env, monkeypatch):
    """設定の既定値は候補リストに書いていなくても選べる（候補の先頭に入る）。"""
    _register_choices(
        monkeypatch,
        {IMAGE_SLOT: ["alt.safetensors"]},
        overrides={IMAGE_SLOT: "configured.safetensors"},
    )
    response = _image_job(env, model_overrides={IMAGE_SLOT: "configured.safetensors"})
    assert response.status_code == 201, response.text


def test_a_slot_of_a_workflow_the_job_does_not_run_is_422(env, monkeypatch):
    """画像のみのジョブは動画ステージを走らせないので、動画スロットは指定できない。"""
    _register_choices(monkeypatch, {VIDEO_SLOT: ["alt.safetensors"]})
    response = _image_job(env, model_overrides={VIDEO_SLOT: "alt.safetensors"})
    assert response.status_code == 422
    assert "ltx2_3_id_lora" in response.text


def test_an_unknown_model_slot_is_422(env, monkeypatch):
    _register_choices(monkeypatch, {})
    response = _image_job(env, model_overrides={"nope/1.unet_name": "a.safetensors"})
    assert response.status_code == 422
    assert "nope/1.unet_name" in response.text


def test_a_slot_without_a_registered_choice_is_422(env, monkeypatch):
    """候補を登録していないスロットは、既定値以外にはできない。"""
    _register_choices(monkeypatch, {})
    response = _image_job(env, model_overrides={IMAGE_SLOT: "alt.safetensors"})
    assert response.status_code == 422


def test_rerun_keeps_the_job_model(env, monkeypatch):
    _register_choices(monkeypatch, {IMAGE_SLOT: ["alt.safetensors"]})
    first = _image_job(env, model_overrides={IMAGE_SLOT: "alt.safetensors"}).json()
    wait_for(env.client, first["id"])
    second = env.client.post(f"/api/jobs/{first['id']}/rerun", json={}).json()
    assert second["params"]["model_overrides"] == {IMAGE_SLOT: "alt.safetensors"}


@needs_ffmpeg
def test_continue_keeps_only_the_video_slots(env, monkeypatch):
    _register_choices(
        monkeypatch,
        {IMAGE_SLOT: ["alt-unet.safetensors"], VIDEO_SLOT: ["alt-ckpt.safetensors"]},
    )
    first = env.client.post(
        "/api/jobs",
        json=full_body(
            env,
            model_overrides={
                IMAGE_SLOT: "alt-unet.safetensors",
                VIDEO_SLOT: "alt-ckpt.safetensors",
            },
        ),
    ).json()
    wait_for(env.client, first["id"])
    second = env.client.post(f"/api/jobs/{first['id']}/continue", json={}).json()
    # 続き生成は動画ステージだけなので、画像スロットの指定は落ちる
    assert second["params"]["model_overrides"] == {VIDEO_SLOT: "alt-ckpt.safetensors"}


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


def test_overall_progress_counts_executed_nodes():
    """executing で通過したノード数と実行中ノードの端数で全体進捗が決まる。"""
    overall = jobs.OverallProgress()
    overall.start_stage(0, 4)
    assert overall.value == 0.0

    assert overall.node_started("1") == 0.0  # 0 完了 + 端数 0
    assert overall.node_progress("1", 5, 10) == pytest.approx(0.5 / 4)
    assert overall.node_started("2") == pytest.approx(1 / 4)
    # node 指定つきの progress は executing を待たずに次のノードへ進める
    assert overall.node_progress("3", 1, 2) == pytest.approx(2.5 / 4)
    assert overall.stage_finished() == 1.0


def test_overall_progress_counts_cached_nodes():
    """execution_cached のノードは実行済み扱い。"""
    overall = jobs.OverallProgress()
    overall.start_stage(0, 5)
    assert overall.nodes_cached(["1", "2", 3]) == pytest.approx(3 / 5)
    assert overall.node_started("4") == pytest.approx(3 / 5)
    assert overall.node_progress("4", 1, 2) == pytest.approx(3.5 / 5)
    # 実行中のノードがキャッシュ通知に含まれても二重には数えない
    assert overall.nodes_cached(["4"]) == pytest.approx(3.5 / 5)


def test_overall_progress_never_goes_backwards():
    overall = jobs.OverallProgress()
    overall.start_stage(0, 4)
    overall.node_started("1")
    assert overall.node_progress("1", 9, 10) == pytest.approx(0.9 / 4)
    # 同じノードの value が巻き戻っても配信値は下がらない
    assert overall.node_progress("1", 1, 10) == pytest.approx(0.9 / 4)
    assert overall.node_progress(None, 0, 0) == pytest.approx(0.9 / 4)
    # ノード総数が分からないステージでも直前の値を保つ
    overall_unknown = jobs.OverallProgress()
    overall_unknown.start_stage(0, 0)
    assert overall_unknown.node_started("1") == 0.0


def test_overall_progress_maps_two_stages_to_halves():
    """2 ステージなら stage1 が 0〜50%、stage2 が 50〜100%。"""
    overall = jobs.OverallProgress(2)
    overall.start_stage(0, 2)
    assert overall.node_started("1") == 0.0
    assert overall.node_progress("1", 1, 2) == pytest.approx(0.125)
    assert overall.stage_finished() == pytest.approx(0.5)

    overall.start_stage(1, 4)
    assert overall.value == pytest.approx(0.5)
    assert overall.node_started("10") == pytest.approx(0.5)
    assert overall.node_progress("10", 1, 2) == pytest.approx(0.5625)
    assert overall.node_started("11") == pytest.approx(0.625)
    assert overall.stage_finished() == 1.0


async def test_ws_progress_publishes_overall_progress(monkeypatch):
    """ComfyUI のイベント列がワークフロー全体を通した進捗に変換される。"""
    import websockets

    frames = [
        '{"type": "execution_cached", "data": {"prompt_id": "pid", "nodes": ["1"]}}',
        '{"type": "executing", "data": {"prompt_id": "pid", "node": "2"}}',
        '{"type": "progress", "data": {"prompt_id": "pid", "node": "2",'
        ' "value": 5, "max": 10}}',
        '{"type": "progress", "data": {"prompt_id": "other", "node": "9",'
        ' "value": 1, "max": 1}}',  # 別プロンプトは無視
        '{"type": "executing", "data": {"prompt_id": "pid", "node": null}}',
    ]

    class FakeSocket:
        async def __aiter__(self):
            for frame in frames:
                yield frame

    class FakeConnect:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return FakeSocket()

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(websockets, "connect", FakeConnect)
    published: list[tuple[str | None, float | None]] = []

    async def fake_publish(job_id, status, *, node=None, progress=None, **kwargs):
        published.append((node, progress))

    monkeypatch.setattr(jobs.ws, "publish", fake_publish)

    finished = asyncio.Event()
    overall = jobs.OverallProgress()
    overall.start_stage(0, 4)
    await jobs._ws_progress("cid", "pid", "job", finished, overall)

    assert finished.is_set()
    assert published == [
        (None, pytest.approx(0.25)),  # execution_cached: 1/4
        ("2", pytest.approx(0.25)),  # executing node 2
        ("2", pytest.approx(0.375)),  # 1 完了 + 0.5 ノード / 4
        (None, 1.0),  # executing node=None → ステージ完了
    ]


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


# --------------------------------------------------------------------------
# image workflow selection (SPEC §3)
# --------------------------------------------------------------------------

def test_the_image_workflow_is_selectable(env):
    response = env.client.post(
        "/api/jobs",
        json={
            "mode": "image_only",
            "image_workflow": "z_image_turbo",
            "image_prompt": "an image",
        },
    )
    assert response.status_code == 201, response.text
    job = wait_for(env.client, response.json()["id"])
    assert job["status"] == "done", job["error"]
    assert job["params"]["image_workflow"] == "z_image_turbo"
    assert job["workflow_json"]["image"]["workflow_id"] == "z_image_turbo"
    # z-image has no ResolutionSelector: the app injects the computed edges
    graph = graph_with(env, "57:13")
    width, height = resolution("4:3 (Standard)", 1.0)
    assert graph["57:13"]["inputs"]["width"] == width
    assert graph["57:13"]["inputs"]["height"] == height


def test_an_unknown_image_workflow_is_rejected(env):
    response = env.client.post(
        "/api/jobs",
        json={
            "mode": "image_only",
            "image_workflow": "nope",
            "image_prompt": "an image",
        },
    )
    assert response.status_code == 422
    assert "nope" in response.text


def test_the_editing_workflow_requires_a_source_image(env):
    for mode, extra in (("image_only", {}), ("full", {"video_prompt": "a video"})):
        response = env.client.post(
            "/api/jobs",
            json={
                "mode": mode,
                "image_workflow": "qwen_image_edit_2511",
                "image_prompt": "make the coat red",
                "audio_path": str(env.audio),
                **extra,
            },
        )
        assert response.status_code == 422, (mode, response.text)
        assert "source_image" in response.text


def test_the_editing_workflow_uses_the_source_image_as_its_input(env):
    response = env.client.post(
        "/api/jobs",
        json={
            "mode": "image_only",
            "image_workflow": "qwen_image_edit_2511",
            "image_prompt": "make the coat red",
            "source_image": str(env.start_image),
        },
    )
    assert response.status_code == 201, response.text
    job = wait_for(env.client, response.json()["id"])
    assert job["status"] == "done", job["error"]
    graph = graph_with(env, "41")
    assert graph["41"]["inputs"]["image"] == env.start_image.name
    assert graph["170:151"]["inputs"]["prompt"] == "make the coat red"


@needs_ffmpeg
def test_full_mode_edits_the_source_image_then_animates_it(env):
    """qwen + full: source_image feeds the edit, the edited still the video."""
    response = env.client.post(
        "/api/jobs",
        json=full_body(
            env,
            image_workflow="qwen_image_edit_2511",
            source_image=str(env.start_image),
        ),
    )
    assert response.status_code == 201, response.text
    job = wait_for(env.client, response.json()["id"])
    assert job["status"] == "done", job["error"]
    image_graph = graph_with(env, "41")
    assert image_graph["41"]["inputs"]["image"] == env.start_image.name
    # the video stage starts from the *generated* still, not the input picture
    video_graph = graph_with(env, "269")
    assert video_graph["269"]["inputs"]["image"] == Path(job["image_path"]).name


def _register_lora(env, name: str, family: str) -> dict:
    return env.client.post(
        "/api/loras",
        json={
            "display_name": name,
            "lora_name": f"{name}.safetensors",
            "trigger_word": name,
            "target": "image",
            "family": family,
        },
    ).json()


def test_an_image_lora_of_another_family_is_rejected(env):
    _register_lora(env, "kaori", "krea2")
    response = env.client.post(
        "/api/jobs",
        json={
            "mode": "image_only",
            "image_workflow": "anima",
            "image_prompt": "an image",
            "loras": [{"lora_name": "kaori.safetensors", "trigger_word": "kaori"}],
        },
    )
    assert response.status_code == 422
    assert "krea2" in response.text and "anima" in response.text


def test_an_image_lora_of_the_matching_family_is_accepted(env):
    _register_lora(env, "hana", "anima")
    response = env.client.post(
        "/api/jobs",
        json={
            "mode": "image_only",
            "image_workflow": "anima",
            "image_prompt": "an image",
            "loras": [{"lora_name": "hana.safetensors", "trigger_word": "hana"}],
        },
    )
    assert response.status_code == 201, response.text
    job = wait_for(env.client, response.json()["id"])
    assert job["status"] == "done", job["error"]
    graph = graph_with(env, "90:76")
    assert graph["app_lora_0"]["inputs"]["lora_name"] == "hana.safetensors"


def test_an_unregistered_lora_does_not_block_a_job(env):
    """A deleted LoRA must not make old jobs unrerunnable (family unknown)."""
    response = env.client.post(
        "/api/jobs",
        json={
            "mode": "image_only",
            "image_workflow": "anima",
            "image_prompt": "an image",
            "loras": [{"lora_name": "ghost.safetensors", "trigger_word": "g"}],
        },
    )
    assert response.status_code == 201, response.text


def test_the_artifact_picker_accepts_any_output_key():
    """SaveImageAdvanced does not have to name its list ``images``.

    ``_pick_output`` prefers the known keys and otherwise takes the first entry
    with a ``filename``, so a node that reports its files under another key is
    still downloadable.
    """
    known = {"195": {"images": [{"filename": "a.png"}]}}
    assert jobs._pick_output(known, "195")["filename"] == "a.png"

    unusual = {"195": {"result": [{"filename": "b.png", "subfolder": "x"}]}}
    assert jobs._pick_output(unusual, "195")["filename"] == "b.png"

    assert jobs._pick_output({"195": {"result": []}}, "195") is None
    assert jobs._pick_output({}, "195") is None


# --------------------------------------------------------------------------
# 音声ジョブ（mode='audio'）— 独立ジョブなので画像・動画は一切走らない
# --------------------------------------------------------------------------

def test_audio_job_runs_one_stage_and_stores_the_track(env):
    response = env.client.post(
        "/api/jobs",
        json={
            "mode": "audio",
            "audio_workflow": "ace_step1_5_xl_sft",
            "audio_prompt": "warm neo-soul, rhodes piano, brushed drums",
            "lyrics": "[Verse 1]\nthe last train hums",
            "duration": 30,
            "bpm": 92,
            "keyscale": "F# minor",
            "language": "ja",
            "seed": 1234,
        },
    )
    assert response.status_code == 201, response.text
    job = wait_for(env.client, response.json()["id"])
    assert job["status"] == "done", job["error"]

    # 1 プロンプトだけ（画像ステージも動画ステージも走っていない）
    assert len(env.comfy.queued) == 1
    assert list(job["workflow_json"]) == ["audio"]
    graph = env.comfy.queued[0]
    assert graph["94"]["inputs"]["tags"].startswith("warm neo-soul")
    assert graph["94"]["inputs"]["duration"] == 30
    assert graph["98"]["inputs"]["seconds"] == 30.0
    assert graph["109"]["inputs"]["value"] == 1234

    # 成果物は audio_output_path/-url のみ。動画・画像・ラストフレームは無い。
    assert job["audio_output_url"].endswith(".mp3")
    assert job["video_url"] is None and job["image_url"] is None
    assert job["last_frame_url"] is None
    assert Path(job["audio_output_path"]).is_file()
    # 入力側の audio_path（リファレンス音声）とは別枠のまま
    assert job["audio_path"] is None
    assert job["audio_prompt"].startswith("warm neo-soul")


def test_stable_audio_job_runs(env):
    response = env.client.post(
        "/api/jobs",
        json={
            "mode": "audio",
            "audio_workflow": "stable_audio_3_medium_base",
            "audio_prompt": "glass shattering on concrete. Length: 2 seconds",
            "audio_category": "SFX",
            "reprompt": False,
            "duration": 2,
        },
    )
    assert response.status_code == 201, response.text
    job = wait_for(env.client, response.json()["id"])
    assert job["status"] == "done", job["error"]
    graph = env.comfy.queued[0]
    assert graph["52:43"]["inputs"]["choice"] == "SFX"
    assert graph["52:36"]["inputs"]["value"] == 2.0
    assert graph["52:35"]["inputs"]["value"] is False


def test_audio_job_uploads_nothing(env):
    """音声ジョブは ComfyUI の input ディレクトリに何も送らない。"""
    response = env.client.post(
        "/api/jobs",
        json={"mode": "audio", "audio_prompt": "a lofi loop", "duration": 30},
    )
    assert response.status_code == 201, response.text
    wait_for(env.client, response.json()["id"])
    assert env.comfy.uploads == []


def test_audio_job_rejects_an_out_of_range_duration(env):
    response = env.client.post(
        "/api/jobs",
        json={"mode": "audio", "audio_prompt": "a lofi loop", "duration": 5},
    )
    assert response.status_code == 422
    assert "10-600 seconds" in response.text


def test_audio_job_can_be_rerun_with_a_new_seed(env):
    created = env.client.post(
        "/api/jobs",
        json={"mode": "audio", "audio_prompt": "a lofi loop", "duration": 30,
              "seed": 11},
    )
    job = wait_for(env.client, created.json()["id"])
    assert job["status"] == "done", job["error"]

    rerun = env.client.post(f"/api/jobs/{job['id']}/rerun", json={"seed": 22})
    assert rerun.status_code == 201, rerun.text
    again = wait_for(env.client, rerun.json()["id"])
    assert again["status"] == "done", again["error"]
    assert again["mode"] == "audio"
    assert env.comfy.queued[-1]["109"]["inputs"]["value"] == 22


def test_audio_job_cannot_be_continued(env):
    """continue は動画のラストフレームからの続きなので、音声には行き先が無い。"""
    created = env.client.post(
        "/api/jobs",
        json={"mode": "audio", "audio_prompt": "a lofi loop", "duration": 30},
    )
    job = wait_for(env.client, created.json()["id"])
    response = env.client.post(f"/api/jobs/{job['id']}/continue", json={})
    assert response.status_code == 422
    assert "last frame" in response.text


# --------------------------------------------------------------------------
# 選択式フィールドと尺の自動決定（SPEC §3.1、wan_dancer）
# --------------------------------------------------------------------------

def wan_body(env, **overrides) -> dict:
    body = {
        "mode": "i2v",
        "video_workflow": "wan_dancer",
        "source_image": str(env.start_image),
        "audio_path": str(env.audio),
    }
    body.update(overrides)
    return body


def _no_probe(*_args, **_kwargs):
    """ffprobe を使わない差し替え（長さ不明）。"""

    async def unknown():
        return None

    return unknown()


def _probe(seconds: float):
    async def measured(*_args, **_kwargs):
        return seconds

    return measured


def test_wan_needs_no_video_prompt(env, monkeypatch):
    monkeypatch.setattr(jobs, "probe_media_duration", _no_probe)
    response = env.client.post("/api/jobs", json=wan_body(env))
    assert response.status_code == 201, response.text
    job = wait_for(env.client, response.json()["id"])
    assert job["status"] == "done", job["error"]
    # テンプレートのテンプレ文がそのまま残る
    graph = graph_with(env, "696:685")
    assert "<dance style>" in graph["696:685"]["inputs"]["string"]


def test_ltx_still_requires_a_video_prompt(env):
    response = env.client.post(
        "/api/jobs",
        json={
            "mode": "i2v",
            "source_image": str(env.start_image),
            "audio_path": str(env.audio),
        },
    )
    assert response.status_code == 422
    assert "video_prompt" in response.text


def test_selects_are_injected_and_recorded(env, monkeypatch):
    monkeypatch.setattr(jobs, "probe_media_duration", _no_probe)
    response = env.client.post(
        "/api/jobs",
        json=wan_body(
            env,
            selects={"dance_style": "Latin Dance 拉丁舞", "motion_amplitude": "high 高"},
        ),
    )
    assert response.status_code == 201, response.text
    job = wait_for(env.client, response.json()["id"])
    assert job["status"] == "done", job["error"]
    assert job["params"]["selects"]["dance_style"] == "Latin Dance 拉丁舞"
    graph = graph_with(env, "696:695")
    assert graph["696:695"]["inputs"]["choice"] == "Latin Dance 拉丁舞"
    assert graph["696:695"]["inputs"]["index"] == 3
    assert graph["696:694"]["inputs"]["index"] == 2


def test_a_value_outside_the_choices_is_422(env):
    response = env.client.post(
        "/api/jobs", json=wan_body(env, selects={"dance_style": "Tango"})
    )
    assert response.status_code == 422
    assert "Tango" in response.text


def test_an_unknown_select_name_is_422(env):
    response = env.client.post("/api/jobs", json=wan_body(env, selects={"tempo": "fast"}))
    assert response.status_code == 422
    assert "tempo" in response.text


def test_selects_on_a_workflow_without_them_is_422(env):
    response = env.client.post(
        "/api/jobs",
        json=full_body(env, mode="i2v", source_image=str(env.start_image),
                       selects={"dance_style": "K-Pop 韩舞"}),
    )
    assert response.status_code == 422
    assert "dance_style" in response.text


def test_the_duration_follows_the_audio_length(env, monkeypatch):
    """未指定の尺は音声の実長から切り上げて決まる（25 秒固定にしない）。"""
    monkeypatch.setattr(jobs, "probe_media_duration", _probe(12.3))
    created = env.client.post("/api/jobs", json=wan_body(env))
    assert created.status_code == 201, created.text
    job = wait_for(env.client, created.json()["id"])
    assert job["status"] == "done", job["error"]
    # 12.3 秒 -> 15（実長以上で最小の選択肢）
    assert job["params"]["selects"]["duration"] == "15"
    graph = graph_with(env, "696:700")
    assert graph["696:700"]["inputs"]["choice"] == "15"
    assert graph["696:700"]["inputs"]["index"] == 2
    assert graph["696:494"]["inputs"]["duration"] == 15.0


def test_a_long_track_is_capped_at_the_longest_choice(env, monkeypatch):
    monkeypatch.setattr(jobs, "probe_media_duration", _probe(210.0))
    created = env.client.post("/api/jobs", json=wan_body(env))
    job = wait_for(env.client, created.json()["id"])
    assert job["params"]["selects"]["duration"] == "30"


def test_an_explicit_duration_wins_over_the_audio_length(env, monkeypatch):
    monkeypatch.setattr(jobs, "probe_media_duration", _probe(28.0))
    created = env.client.post(
        "/api/jobs", json=wan_body(env, selects={"duration": "10"})
    )
    job = wait_for(env.client, created.json()["id"])
    assert job["params"]["selects"]["duration"] == "10"
    assert graph_with(env, "696:494")["696:494"]["inputs"]["duration"] == 10.0


def test_an_unmeasurable_track_keeps_the_declared_default(env, monkeypatch):
    """ffprobe が無い環境でも登録は通り、既定の尺で走る。"""
    monkeypatch.setattr(jobs, "probe_media_duration", _no_probe)
    created = env.client.post("/api/jobs", json=wan_body(env))
    assert created.status_code == 201, created.text
    job = wait_for(env.client, created.json()["id"])
    assert job["status"] == "done", job["error"]
    assert "duration" not in job["params"]["selects"]
    assert graph_with(env, "696:700")["696:700"]["inputs"]["choice"] == "15"


def test_rerun_keeps_the_resolved_duration(env, monkeypatch):
    monkeypatch.setattr(jobs, "probe_media_duration", _probe(7.0))
    first = env.client.post("/api/jobs", json=wan_body(env)).json()
    wait_for(env.client, first["id"])
    assert first["params"]["selects"]["duration"] == "10"

    # 2 回目は測り直さない（同じ尺で再現する）
    monkeypatch.setattr(jobs, "probe_media_duration", _probe(30.0))
    second = env.client.post(f"/api/jobs/{first['id']}/rerun", json={}).json()
    assert second["params"]["selects"]["duration"] == "10"


def test_video_loras_are_rejected_for_a_workflow_without_a_chain(env):
    response = env.client.post(
        "/api/jobs", json=wan_body(env, video_loras=[VIDEO_LORA])
    )
    assert response.status_code == 422
    assert "wan_dancer" in response.text


@needs_ffmpeg
def test_continue_drops_the_selects_of_another_workflow(env, monkeypatch):
    monkeypatch.setattr(jobs, "probe_media_duration", _probe(10.0))
    first = env.client.post(
        "/api/jobs", json=wan_body(env, selects={"dance_style": "K-Pop 韩舞"})
    ).json()
    wait_for(env.client, first["id"])
    # 既定（LTX）へ続き生成すると、wan の選択項目は意味が無いので落ちる
    second = env.client.post(
        f"/api/jobs/{first['id']}/continue",
        json={"video_workflow": "ltx2_3_id_lora", "video_prompt": "a clip"},
    )
    assert second.status_code == 201, second.text
    assert second.json()["params"]["selects"] == {}


async def test_probe_media_duration_reads_a_real_file(sample_video):
    """ffprobe が使える環境では実長を返す（1 秒のテスト動画）。"""
    seconds = await jobs.probe_media_duration(sample_video)
    assert seconds is not None
    assert 0.5 < seconds < 2.0


async def test_probe_media_duration_survives_a_missing_binary(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "FFPROBE", "definitely-not-ffprobe")
    assert await jobs.probe_media_duration(tmp_path / "nope.mp3") is None


async def test_probe_media_duration_survives_an_unreadable_file(tmp_path):
    broken = tmp_path / "broken.mp3"
    broken.write_bytes(b"not audio")
    assert await jobs.probe_media_duration(broken) is None


# --------------------------------------------------------------------------
# 参照画像を複数取るワークフロー（SPEC §3.1、MiniMax H3 r2v）
# --------------------------------------------------------------------------

REF_WORKFLOW = "minimax_h3_r2v"


def _ref_assets(env, count: int) -> list[str]:
    """assets/image に参照画像を置いて、その `/assets/...` URL を返す。"""
    urls = []
    for index in range(count):
        path = env.assets / "image" / f"ref{index}.png"
        path.write_bytes(b"PNG")
        urls.append(f"/assets/image/ref{index}.png")
    return urls


def ref_body(env, count: int, **overrides) -> dict:
    body = {
        "mode": "i2v",
        "video_workflow": REF_WORKFLOW,
        "video_prompt": "The boy from <Picture 1> leaps off the roof.",
        "reference_images": _ref_assets(env, count),
    }
    body.update(overrides)
    return body


@pytest.mark.parametrize("count", [1, 3])
def test_reference_images_are_uploaded_and_wired(env, count):
    created = env.client.post("/api/jobs", json=ref_body(env, count))
    assert created.status_code == 201, created.text
    job = wait_for(env.client, created.json()["id"])
    assert job["status"] == "done", job["error"]

    # 1 枚ずつ ComfyUI に上がる
    uploaded = [Path(path).name for path in env.comfy.uploads]
    assert [f"ref{index}.png" for index in range(count)] == uploaded

    graph = graph_with(env, "136")
    loaders = sorted(key for key in graph if key.startswith("app_ref_image_"))
    assert len(loaders) == count
    for index in range(count):
        node_id = f"app_ref_image_{index}"
        assert graph[node_id]["inputs"]["image"] == f"ref{index}.png"
        assert graph["136"]["inputs"][f"ref_images.ref_image_{index}"] == [node_id, 0]
    # 雛形の LoadImage はグラフに残らない
    assert "137" not in graph


def test_the_reference_workflow_needs_at_least_one_image(env):
    answer = env.client.post("/api/jobs", json=ref_body(env, 0))
    assert answer.status_code == 422
    assert "reference_images" in answer.text


def test_the_reference_workflow_takes_no_start_frame(env):
    answer = env.client.post(
        "/api/jobs",
        json=ref_body(env, 1, source_image=str(env.start_image)),
    )
    assert answer.status_code == 422
    assert "source_image" in answer.text


def test_too_many_reference_images_are_422(env):
    answer = env.client.post("/api/jobs", json=ref_body(env, 10))
    assert answer.status_code == 422
    assert "9 件" in answer.text
