"""GET /api/options: the form choices, including the workflow catalogue (SPEC §9)."""

import pytest
from fastapi.testclient import TestClient

from app import comfy, config
from app.main import app
from app.routers import assets as assets_router
from app.workflows import DEFAULT_VIDEO_WORKFLOW, video_specs


@pytest.fixture
def client(tmp_path, monkeypatch):
    """ComfyUI is offline and assets live in a throwaway dir."""
    monkeypatch.setattr(assets_router, "ASSETS_DIR", tmp_path / "assets")

    async def offline():
        raise comfy.ComfyError("ComfyUI is down")

    monkeypatch.setattr(comfy, "get_object_info", lambda *a, **k: offline())
    with TestClient(app) as test_client:
        yield test_client


def test_workflow_catalogue_is_exposed(client):
    options = client.get("/api/options").json()
    # ComfyUI being down must not hide the workflow list (it is local data)
    assert options["comfy_error"]
    assert options["default_video_workflow"] == DEFAULT_VIDEO_WORKFLOW
    assert options["default_image_workflow"] == "krea2_turbo"

    images = {w["id"]: w for w in options["image_workflows"]}
    assert list(images) == [
        "krea2_turbo",
        "anima",
        "z_image_turbo",
        "qwen_image_edit_2511",
    ]
    assert [w["family"] for w in options["image_workflows"]] == [
        "krea2",
        "anima",
        "z-image",
        "qwen-image",
    ]
    # the editing workflow is the only image one that needs an input picture
    assert images["krea2_turbo"]["requires"] == []
    assert images["qwen_image_edit_2511"]["requires"] == ["image"]
    assert images["qwen_image_edit_2511"]["image_label"] == "編集元画像"
    # …and the only one that does not take an aspect ratio / megapixel target
    assert "aspect_ratio" not in images["qwen_image_edit_2511"]["supports"]
    assert {"width", "height"} <= set(images["z_image_turbo"]["supports"])

    videos = {w["id"]: w for w in options["video_workflows"]}
    assert set(videos) == {spec.id for spec in video_specs()}
    assert videos[DEFAULT_VIDEO_WORKFLOW]["accepts_start_image"] is True

    t2v = videos["ltx2_3_t2v"]
    assert t2v["requires"] == []
    assert t2v["accepts_start_image"] is False
    assert {"prompt", "negative", "width", "height", "duration", "fps"} <= set(
        t2v["supports"]
    )

    flf2v = videos["ltx2_3_flf2v"]
    assert flf2v["requires"] == ["image", "end_image"]
    assert flf2v["image_label"] == "最初のフレーム"

    motion = videos["ltx2_3_ic_lora_motion"]
    assert motion["requires"] == ["image", "video"]
    # the frame count follows the reference clip, so there is no expression to pin
    assert "frames_expr" not in motion["supports"]

    sheet = videos["ltx2_3_ic_lora_image"]
    assert sheet["accepts_start_image"] is False
    assert sheet["image_label"] == "リファレンスシート画像"


def test_negative_presets_include_the_template_default(client):
    presets = client.get("/api/options").json()["negative_presets"]
    # an empty value means "keep whatever the template ships with" (SPEC §3.1)
    assert presets["template"] == ""
    assert presets["current"].startswith("pc game")


def test_video_assets_are_listed(client, tmp_path):
    clip = tmp_path / "assets" / "video" / "ref.mp4"
    clip.parent.mkdir(parents=True, exist_ok=True)
    clip.write_bytes(b"\x00\x00\x00 ftypmp42")
    options = client.get("/api/options").json()
    assert [a["name"] for a in options["video_assets"]] == ["ref.mp4"]
    assert options["video_assets"][0]["kind"] == "video"


def test_video_upload_endpoint(client):
    created = client.post(
        "/api/assets/video", files={"file": ("clip.mp4", b"data", "video/mp4")}
    )
    assert created.status_code == 201, created.text
    asset = created.json()
    assert asset["kind"] == "video"
    assert asset["url"].startswith("/assets/video/")
    assert [a["url"] for a in client.get("/api/assets/video").json()] == [asset["url"]]


def test_video_upload_rejects_a_wrong_extension(client):
    response = client.post(
        "/api/assets/video", files={"file": ("clip.png", b"data", "image/png")}
    )
    assert response.status_code == 400
    assert ".png" in response.text


def test_audio_workflows_are_exposed(client):
    options = client.get("/api/options").json()
    assert options["default_audio_workflow"] == "ace_step1_5_xl_sft"

    audio = {w["id"]: w for w in options["audio_workflows"]}
    assert list(audio) == ["ace_step1_5_xl_sft", "stable_audio_3_medium_base"]
    assert [w["family"] for w in options["audio_workflows"]] == [
        "ace-step",
        "stable-audio",
    ]
    # 音声は単体ジョブ: 入力アセットも開始フレームも取らない
    assert all(w["requires"] == [] for w in audio.values())
    assert all(w["accepts_start_image"] is False for w in audio.values())

    ace = audio["ace_step1_5_xl_sft"]
    assert {"prompt", "lyrics", "duration", "bpm", "keyscale", "language"} <= set(
        ace["supports"]
    )
    assert (ace["min_duration"], ace["max_duration"]) == (10.0, 600.0)
    assert ace["default_duration"] == 120.0

    sa3 = audio["stable_audio_3_medium_base"]
    assert {"audio_category", "reprompt"} <= set(sa3["supports"])
    assert "lyrics" not in sa3["supports"]
    assert (sa3["min_duration"], sa3["max_duration"]) == (1.0, 380.0)

    # 音声は画像・動画のリストには混ざらない
    assert all(w["kind"] == "image" for w in options["image_workflows"])
    assert all(w["kind"] == "video" for w in options["video_workflows"])


UNET_SLOT = "krea2_turbo/30:10.unet_name"


def test_model_slots_only_list_the_switchable_ones(client, monkeypatch):
    """候補が 2 件以上あるスロットだけがフォームのセレクトになる（SPEC §3.3）。"""
    assert client.get("/api/options").json()["model_slots"] == []

    monkeypatch.setattr(
        config,
        "_settings",
        config.load_settings().model_copy(
            update={"model_choices": {UNET_SLOT: ["alt.safetensors"]}}
        ),
    )
    slots = client.get("/api/options").json()["model_slots"]
    assert [slot["key"] for slot in slots] == [UNET_SLOT]
    slot = slots[0]
    assert slot["workflow_id"] == "krea2_turbo"
    assert slot["kind"] == "image"
    assert slot["choices"] == [slot["default"], "alt.safetensors"]
    assert slot["label"]


def test_model_files_are_empty_while_comfy_is_down(client):
    assert client.get("/api/options").json()["model_files"] == {}


def test_model_files_come_from_object_info(monkeypatch, tmp_path):
    """設定ページの候補入力の datalist 用に class_type.field ごとに返る。"""
    monkeypatch.setattr(assets_router, "ASSETS_DIR", tmp_path / "assets")

    async def info():
        return {
            "UNETLoader": {
                "input": {"required": {"unet_name": [["a.safetensors", "b.safetensors"]]}}
            },
            "ResolutionSelector": {
                "input": {"required": {"aspect_ratio": [["1:1 (Square)"]]}}
            },
        }

    monkeypatch.setattr(comfy, "get_object_info", lambda *a, **k: info())
    with TestClient(app) as client:
        options = client.get("/api/options").json()
    assert options["comfy_connected"] is True
    assert options["model_files"]["UNETLoader.unet_name"] == [
        "a.safetensors",
        "b.safetensors",
    ]
    # /object_info に無い class_type は黙って省く（エラーにしない）
    assert "VAELoader.vae_name" not in options["model_files"]


def test_audio_combo_choices_are_exposed(client):
    """ComfyUI が落ちていてもローカル定義の選択肢は返る。"""
    options = client.get("/api/options").json()
    assert options["audio_categories"] == ["Music", "Instrument", "SFX", "One-shot"]
    assert "C major" in options["keyscales"] and len(options["keyscales"]) == 34
    assert options["languages"][-1] == "unknown"
