"""GET /api/options: the form choices, including the workflow catalogue (SPEC §9)."""

import pytest
from fastapi.testclient import TestClient

from app import comfy, config
from app.main import app
from app.routers import assets as assets_router
from app.workflows import (
    DEFAULT_VIDEO_WORKFLOW,
    audio_specs,
    get_spec,
    video_specs,
)


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
        "minimax_h3_t2i",
        "minimax_h3_t2i_opt",
        "minimax_h3_t2i_turbo",
        "minimax_h3_i2i",
        "minimax_h3_i2i_opt",
        "minimax_h3_i2i_turbo",
        "minimax_h3_r2i",
        "minimax_h3_r2i_opt",
        "minimax_h3_r2i_turbo",
        "grok_imagine_t2i",
        "grok_imagine_edit",
    ]
    assert [w["family"] for w in options["image_workflows"]] == [
        "krea2",
        "anima",
        "z-image",
        "qwen-image",
        *["minimax-h3-image"] * 9,
        "grok-imagine",
        "grok-imagine",
    ]
    # 編集系のワークフローだけが入力画像を必要とする
    assert images["krea2_turbo"]["requires"] == []
    assert images["qwen_image_edit_2511"]["requires"] == ["image"]
    assert images["qwen_image_edit_2511"]["image_label"] == "編集元画像"
    assert images["grok_imagine_edit"]["requires"] == ["image"]
    assert images["grok_imagine_edit"]["image_label"] == "編集元画像"
    # ComfyUI 非依存のワークフローは backend でそれと分かる（SPEC §5.2）
    assert images["krea2_turbo"]["backend"] == "comfyui"
    assert images["grok_imagine_t2i"]["backend"] == "grok_cli"
    assert images["grok_imagine_edit"]["backend"] == "grok_cli"
    # …and the only one that does not take an aspect ratio / megapixel target
    assert "aspect_ratio" not in images["qwen_image_edit_2511"]["supports"]
    assert {"width", "height"} <= set(images["z_image_turbo"]["supports"])

    videos = {w["id"]: w for w in options["video_workflows"]}
    assert set(videos) == {spec.id for spec in video_specs()}
    assert videos[DEFAULT_VIDEO_WORKFLOW]["accepts_start_image"] is True

    t2v = videos["minimax_h3_t2v"]
    assert t2v["requires"] == []
    assert t2v["accepts_start_image"] is False
    assert {"prompt", "width", "height", "duration"} <= set(t2v["supports"])

    i2v = videos["minimax_h3_i2v"]
    assert i2v["requires"] == ["image"]
    assert i2v["image_label"] == "開始フレーム"
    # 任意の最終フレームは requires ではなく supports の側に出る
    assert "end_image" not in i2v["requires"]
    assert "end_image" in i2v["supports"]

    # 参照モードは開始フレームと排他（外部 API 側の制約と同じ扱い）
    r2v = videos["minimax_h3_r2v"]
    assert r2v["accepts_start_image"] is False
    assert r2v["requires"] == []
    assert r2v["multi_inputs"]


def test_workflows_carry_the_two_stage_picker_labels(client):
    """モデル（family_label）→ モード（mode_label）の 2 段プルダウン用（SPEC §8）。"""
    options = client.get("/api/options").json()
    videos = {w["id"]: w for w in options["video_workflows"]}

    # モード名にモデル名を重ねない（1 段目に出るため）
    h3 = videos["minimax_h3_t2v"]
    assert h3["family_label"] == "MiniMax H3"
    assert h3["mode_label"] == "テキスト→動画・音声つき (t2v)"
    assert "MiniMax" in h3["label"]  # 単独で読む label は今までどおり

    # 同じモデルのモードは同じ 1 段目に集まる
    assert {
        videos[spec_id]["family_label"]
        for spec_id in (
            "minimax_h3_t2v",
            "minimax_h3_i2v",
            "minimax_h3_i2v_turbo",
            "minimax_h3_r2v",
            "minimax_h3_r2v_turbo",
        )
    } == {"MiniMax H3"}

    images = {w["id"]: w for w in options["image_workflows"]}
    assert images["krea2_turbo"]["family_label"] == "Krea 2"
    assert images["krea2_turbo"]["mode_label"] == "turbo"


def test_the_minimax_workflows_declare_their_own_megapixels(client):
    """H3 は 0.4MP 前提（1.0MP のままだと VRAM が足りない、SPEC §3.1）。"""
    videos = {w["id"]: w for w in client.get("/api/options").json()["video_workflows"]}
    for workflow_id in (
        "minimax_h3_t2v",
        "minimax_h3_i2v",
        "minimax_h3_i2v_turbo",
        "minimax_h3_r2v",
        "minimax_h3_r2v_turbo",
    ):
        assert videos[workflow_id]["default_megapixels"] == 0.4
    # 宣言の無いワークフロー（画像側）は 0（フォームのグローバル既定のまま）
    images = {w["id"]: w for w in client.get("/api/options").json()["image_workflows"]}
    assert images["krea2_turbo"]["default_megapixels"] == 0.0


def test_the_minimax_turbo_workflows_are_offered(client):
    """turbo（4 ステップ版）も一覧に出て、素の版と同じ入力を受け取る。"""
    videos = {w["id"]: w for w in client.get("/api/options").json()["video_workflows"]}
    for turbo_id, plain_id in (
        ("minimax_h3_i2v_turbo", "minimax_h3_i2v"),
        ("minimax_h3_r2v_turbo", "minimax_h3_r2v"),
    ):
        turbo, plain = videos[turbo_id], videos[plain_id]
        assert sorted(turbo["supports"]) == sorted(plain["supports"])
        assert turbo["family_label"] == "MiniMax H3"
        assert "Turbo" in turbo["mode_label"]
        assert turbo["accepts_start_image"] == plain["accepts_start_image"]
        assert turbo["multi_inputs"] == plain["multi_inputs"]


#: カスタムノード（`app.workflows.OPTIONAL_CLASS_TYPES`）を使うワークフロー
CUSTOM_NODE_WORKFLOWS = (
    "minimax_h3_i2v_turbo",
    "minimax_h3_r2v_turbo",
    "minimax_h3_i2v_opt",
    "minimax_h3_r2v_opt",
)
#: それらを外しても残る MiniMax H3 の素の版
PLAIN_WORKFLOWS = ("minimax_h3_t2v", "minimax_h3_i2v", "minimax_h3_r2v")


#: ドラマスタジオが内部で解決するだけのバリアント（`WorkflowSpec.studio_only`）。
#: プロジェクトの「ラテント連続性」×「動画生成品質」から `app.studio._plan_render`
#: が id を組み立てるもので、手動の生成フォームには出さない（SPEC §2.2）。
STUDIO_ONLY_WORKFLOWS = tuple(
    f"minimax_h3_{mode}_save{suffix}"
    for mode in ("t2v", "i2v", "r2v")
    for suffix in ("", "_turbo", "_opt")
) + tuple(
    f"minimax_h3_r2v_context{suffix}" for suffix in ("", "_turbo", "_opt")
)


def test_the_studio_only_variants_are_not_offered(client):
    """`_save` / `_context` 系は生成フォームの選択肢に出ない（id 直指定は生きる）。"""
    options = client.get("/api/options").json()
    ids = [w["id"] for w in options["video_workflows"]]
    for workflow_id in STUDIO_ONLY_WORKFLOWS:
        assert workflow_id not in ids
        # 選択肢から外しただけで、宣言そのものは残っている（スタジオの解決・
        # ジョブの実行・外部 API の id 直指定はこちらを通る）
        assert get_spec(workflow_id, "video").studio_only is True
    for workflow_id in PLAIN_WORKFLOWS:
        assert workflow_id in ids


def _set_target(monkeypatch, target: str) -> None:
    monkeypatch.setattr(
        config,
        "_settings",
        config.load_settings().model_copy(update={"comfy_target": target}),
    )


def test_comfy_cloud_hides_the_custom_node_workflows(client, monkeypatch):
    """Comfy Cloud には任意のカスタムノードを入れられないので turbo / opt は出さない。"""
    _set_target(monkeypatch, "comfy_cloud")
    options = client.get("/api/options").json()
    assert options["comfy_target"] == "comfy_cloud"
    ids = [w["id"] for w in options["video_workflows"]]
    for workflow_id in CUSTOM_NODE_WORKFLOWS:
        assert workflow_id not in ids
    # 素の版（と他のモデル）はそのまま残る
    for workflow_id in PLAIN_WORKFLOWS:
        assert workflow_id in ids
    assert options["default_video_workflow"] in ids
    # 画像側も同じ判定を通る: MiniMax H3 Image は素の版もカスタムノード
    # （ComfyUI-MiniMax-H3-Image-Studio）が要るので、base / opt / turbo とも
    # 丸ごと落ち、他のモデルは残る。
    image_ids = [w["id"] for w in options["image_workflows"]]
    for workflow_id in ("minimax_h3_t2i", "minimax_h3_t2i_opt", "minimax_h3_r2i_turbo"):
        assert workflow_id not in image_ids
    assert "krea2_turbo" in image_ids
    assert options["default_image_workflow"] in image_ids
    # 音声のワークフローはカスタムノードを使わないので 1 件も減らない
    assert [w["id"] for w in options["audio_workflows"]] == [
        spec.id for spec in audio_specs()
    ]


@pytest.mark.parametrize("target", ["local", "runpod"])
def test_the_other_targets_still_offer_every_workflow(client, monkeypatch, target):
    """自前の ComfyUI（local / runpod）は従来どおり全件出す。"""
    _set_target(monkeypatch, target)
    ids = [w["id"] for w in client.get("/api/options").json()["video_workflows"]]
    assert ids == [spec.id for spec in video_specs()]
    for workflow_id in CUSTOM_NODE_WORKFLOWS:
        assert workflow_id in ids


def test_family_label_carries_the_supplier_note():
    """供給元の注記はモデル側に付く（モードごとに変わるものではない）。

    ローカル実行のファミリーには注記が無く、素のラベルがそのまま出る。外部
    バックエンドで走るものだけ「（サブスク CLI）」のような注記が付く。
    """
    from app.workflows import FAMILY_LABELS, FAMILY_NOTES, family_label

    assert FAMILY_NOTES == {"grok-imagine": "サブスク CLI"}
    assert FAMILY_LABELS["minimax-h3"] == "MiniMax H3"
    assert family_label("krea2") == "Krea 2"
    assert family_label("grok-imagine") == "Grok Imagine（サブスク CLI）"
    assert family_label("unknown") == "unknown"


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
    assert options["default_audio_workflow"] == "minimax_music_3"

    audio = {w["id"]: w for w in options["audio_workflows"]}
    assert list(audio) == ["minimax_music_3", "stable_audio_3_medium_base"]
    assert [w["family"] for w in options["audio_workflows"]] == [
        "minimax-music",
        "stable-audio",
    ]
    # 音声は単体ジョブ: 入力アセットも開始フレームも取らない
    assert all(w["requires"] == [] for w in audio.values())
    assert all(w["accepts_start_image"] is False for w in audio.values())

    mmm3 = audio["minimax_music_3"]
    assert {"prompt", "lyrics", "duration", "steps", "seed"} <= set(
        mmm3["supports"]
    )
    assert (mmm3["min_duration"], mmm3["max_duration"]) == (1.0, 300.0)
    assert mmm3["default_duration"] == 60.0

    sa3 = audio["stable_audio_3_medium_base"]
    assert {"audio_category", "reprompt"} <= set(sa3["supports"])
    assert "lyrics" not in sa3["supports"]
    assert (sa3["min_duration"], sa3["max_duration"]) == (1.0, 380.0)

    # 音声は画像・動画のリストには混ざらない
    assert all(w["kind"] == "image" for w in options["image_workflows"])
    assert all(w["kind"] == "video" for w in options["video_workflows"])


UNET_SLOT = "krea2_turbo/30:10.unet_name"


def _register_models(monkeypatch, choices: dict[str, list[str]]) -> None:
    """モデルの既定値と候補リストを差し替える（実際の config.json には依存しない）。"""
    monkeypatch.setattr(
        config,
        "_settings",
        # モデル指定は接続先ごとに持つ（SPEC §5）。テストは既定の 'local' 環境。
        config.load_settings().model_copy(
            update={
                # 接続先も固定する（実際の config.json が RunPod でも同じ結果に）
                "comfy_target": "local",
                "model_overrides": {"local": {}},
                "model_choices": {"local": choices},
            }
        ),
    )


def test_model_slots_only_list_the_switchable_ones(client, monkeypatch):
    """候補が 2 件以上あるスロットだけがフォームのセレクトになる（SPEC §3.3）。"""
    _register_models(monkeypatch, {})
    assert client.get("/api/options").json()["model_slots"] == []

    _register_models(monkeypatch, {UNET_SLOT: ["alt.safetensors"]})
    slots = client.get("/api/options").json()["model_slots"]
    assert [slot["key"] for slot in slots] == [UNET_SLOT]
    slot = slots[0]
    assert slot["workflow_id"] == "krea2_turbo"
    assert slot["kind"] == "image"
    assert slot["choices"] == [slot["default"], "alt.safetensors"]
    assert slot["label"]


def test_model_files_are_empty_while_comfy_is_down(client, monkeypatch):
    _register_models(monkeypatch, {})
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


def test_steps_are_advertised_only_where_the_template_has_a_sampler_knob(client):
    """ステップ数の欄はマニフェストの `steps` 宣言だけで出し分かれる（§3.1）。"""
    options = client.get("/api/options").json()
    supports = {
        w["id"]: set(w["supports"])
        for key in ("image_workflows", "video_workflows", "audio_workflows")
        for w in options[key]
    }
    for workflow_id in (
        "krea2_turbo",
        "anima",
        "z_image_turbo",
        "minimax_h3_t2v",
        "minimax_h3_i2v",
        "minimax_h3_r2v",
        "minimax_h3_i2v_turbo",
        "minimax_h3_r2v_turbo",
        "minimax_music_3",
        "stable_audio_3_medium_base",
    ):
        assert "steps" in supports[workflow_id], workflow_id
    # ManualSigmas / PrimitiveInt スイッチ構成のものは steps の概念を持たない
    for workflow_id in ("qwen_image_edit_2511",):
        assert "steps" not in supports[workflow_id], workflow_id
