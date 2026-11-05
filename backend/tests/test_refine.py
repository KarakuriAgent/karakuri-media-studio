"""MiniMax H3 動画の補正（fast_derope / face_detail / anime_cadence、SPEC §3.1）。

テンプレートは増やさず、選択式が on のときだけビルダーが最終デコードの後ろに
ノードを足す。カスタムノード（ComfyUI-H3-* / ComfyUI-MAINodes）が要るので
Comfy Cloud では off に固定される。ここではグラフの組み替え・接続先ごとの
選択肢・ジョブ投入時の固定・ヘルスチェックの知らせ方を見る。
"""

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app import comfy, config, grok, jobs
from app.main import app
from app.models import GenerationParams, HealthStatus, LoraRef, Settings
from app.workflow import (
    REFINE_BASE_LORA_PREFIX,
    REFINE_DMAD_LORA_NODE,
    REFINE_DMAD_SHIFT_NODE,
    REFINE_TAE_NODE,
    all_required_class_types,
    build_video_workflow,
    model_fields,
    model_subfolder,
    optional_select_class_types,
    refine_choices,
)
from app.workflows import (
    ANIME_CADENCE_CLASS_TYPES,
    ANIME_CADENCE_NAME,
    FACE_DETAIL_CLASS_TYPES,
    FACE_DETAIL_NAME,
    FAST_DEROPE_CLASS_TYPES,
    FAST_DEROPE_NAME,
    LATENT_UPSCALE_NAME,
    REFINE_NODE_PREFIX,
    REFINE_SELECT_NAMES,
    catalog_entry,
    get_spec,
    specs_of_kind,
    validate_spec,
)

#: 全 MiniMax H3 動画ワークフロー（t2v / i2v / r2v × base / opt / turbo × save / context）
H3_VIDEO_IDS = [
    spec.id
    for spec in specs_of_kind("video")
    if spec.family == "minimax-h3" and spec.backend == "comfyui"
]

DEROPE = REFINE_NODE_PREFIX + "derope_"
FACE = REFINE_NODE_PREFIX + "face_"
CADENCE = REFINE_NODE_PREFIX + "cadence"
ALL_ON = {name: "on" for name in REFINE_SELECT_NAMES}


def h3_params(workflow_id: str, *, upscale: str = "off", **selects) -> GenerationParams:
    """必要な入力をそろえた MiniMax H3 動画のジョブ。"""
    spec = get_spec(workflow_id, "video")
    extra = {}
    if spec.multi_inputs:
        extra["reference_image_names"] = ["ref0.png"]
    if spec.accepts_start_image:
        extra["start_image_name"] = "start.png"
    return GenerationParams(
        mode="i2v",
        job_id="01JOB",
        video_workflow=workflow_id,
        video_prompt="VIDEO PROMPT",
        duration=5,
        aspect_ratio="16:9 (Widescreen)",
        megapixels=0.5,
        selects={LATENT_UPSCALE_NAME: upscale, **selects},
        **extra,
    )


def refine_ids(wf: dict) -> set[str]:
    return {node_id for node_id in wf if node_id.startswith(REFINE_NODE_PREFIX)}


def video_input(wf: dict) -> list:
    """デコード結果を最終的に受け取る入力（連続カット版は Trim の images）。"""
    for node in wf.values():
        if node.get("class_type") == "MiniMaxH3MotionContextTrim":
            return node["inputs"]["images"]
    create = next(n for n in wf.values() if n.get("class_type") == "CreateVideo")
    return create["inputs"]["images"]


def decoders(wf: dict, sampler: str) -> tuple[str, str]:
    video = next(
        node_id
        for node_id, node in wf.items()
        if node.get("class_type") == "VAEDecode"
        and node["inputs"].get("samples") == [sampler, 0]
    )
    audio = next(
        node_id
        for node_id, node in wf.items()
        if node.get("class_type") == "VAEDecodeAudio"
        and node["inputs"].get("samples") == [sampler, 0]
    )
    return video, audio


# --------------------------------------------------------------------------
# 宣言
# --------------------------------------------------------------------------

@pytest.mark.parametrize("workflow_id", H3_VIDEO_IDS)
def test_every_h3_video_workflow_offers_the_three_refine_selects_off_by_default(
    workflow_id,
):
    spec = get_spec(workflow_id, "video")
    assert spec.refine is not None
    for name in REFINE_SELECT_NAMES:
        select = spec.select(name)
        assert select is not None, name
        assert set(select.choices) == {"on", "off"}
        assert select.fallback == "off"
        # 注入先ではなくグラフの組み替えで効く
        assert select.rewrites_graph and select.target is None
        assert select.restricted_choice == "off"
    assert validate_spec(spec) == []


@pytest.mark.parametrize(
    ("name", "required"),
    [
        (FAST_DEROPE_NAME, FAST_DEROPE_CLASS_TYPES),
        (FACE_DETAIL_NAME, FACE_DETAIL_CLASS_TYPES),
        (ANIME_CADENCE_NAME, ANIME_CADENCE_CLASS_TYPES),
    ],
)
def test_the_refine_selects_are_pinned_off_on_comfy_cloud(name, required):
    """カスタムノードが要るので Comfy Cloud では off だけ。local / runpod は両方。"""
    select = get_spec("minimax_h3_t2v", "video").select(name)
    assert select.requires_class_types == required
    assert select.choices_for_target("comfy_cloud") == ("off",)
    assert select.fallback_for_target("comfy_cloud") == "off"
    for target in ("local", "runpod", ""):
        assert set(select.choices_for_target(target)) == {"on", "off"}
        assert select.fallback_for_target(target) == "off"


def test_the_fast_derope_and_face_detail_need_mainodes():
    """FastDerope と FaceDetail は ComfyUI-MAINodes の H3V2VInit を使う。"""
    assert "H3V2VInit" in FAST_DEROPE_CLASS_TYPES
    assert "H3V2VInit" in FACE_DETAIL_CLASS_TYPES
    assert {"H3JerkOracle", "H3TimeSmear", "H3ExactRecover"} <= set(
        FAST_DEROPE_CLASS_TYPES
    )
    # pass 2 のシグマは本体の ManualSigmas なので要求しない
    assert "H3InjectSchedule" not in FAST_DEROPE_CLASS_TYPES
    assert ANIME_CADENCE_CLASS_TYPES == ("AnimeCadence",)


def test_the_catalog_lists_only_off_on_comfy_cloud():
    spec = get_spec("minimax_h3_i2v_turbo", "video")
    cloud = {
        name: choices
        for name, _l, choices, _d, _a, _h, _labels in catalog_entry(
            spec, "comfy_cloud"
        ).selects
    }
    local = {
        name: choices
        for name, _l, choices, _d, _a, _h, _labels in catalog_entry(spec, "local").selects
    }
    for name in REFINE_SELECT_NAMES:
        assert cloud[name] == ("off",)
        assert set(local[name]) == {"on", "off"}
    # latent_upscale は本体同梱なので Comfy Cloud でも両方
    assert set(cloud[LATENT_UPSCALE_NAME]) == {"on", "off"}


def test_a_refine_select_without_a_refine_spec_is_reported():
    spec = get_spec("minimax_h3_t2v", "video")
    assert any("refine" in p for p in validate_spec(replace(spec, refine=None)))
    # Comfy Cloud で絞る宣言が無ければ止める
    loose = replace(
        spec,
        selects={
            **spec.selects,
            FAST_DEROPE_NAME: replace(
                spec.selects[FAST_DEROPE_NAME], requires_class_types=()
            ),
        },
    )
    assert any("requires_class_types" in p for p in validate_spec(loose))


def test_refine_choices_fall_back_to_off():
    spec = get_spec("minimax_h3_t2v", "video")
    assert refine_choices(spec, h3_params("minimax_h3_t2v")) == {
        name: False for name in REFINE_SELECT_NAMES
    }
    picked = refine_choices(
        spec, h3_params("minimax_h3_t2v", fast_derope="on", face_detail="bogus")
    )
    assert picked == {
        FAST_DEROPE_NAME: True,
        FACE_DETAIL_NAME: False,
        ANIME_CADENCE_NAME: False,
    }


# --------------------------------------------------------------------------
# グラフの組み替え
# --------------------------------------------------------------------------

@pytest.mark.parametrize("workflow_id", H3_VIDEO_IDS)
@pytest.mark.parametrize("upscale", ["on", "off"])
def test_all_off_leaves_the_graph_untouched(workflow_id, upscale):
    """既定（全部 off）は補正のノードを 1 つも足さない。"""
    implicit = build_video_workflow(h3_params(workflow_id, upscale=upscale))
    explicit = build_video_workflow(
        h3_params(
            workflow_id,
            upscale=upscale,
            **{name: "off" for name in REFINE_SELECT_NAMES},
        )
    )
    assert refine_ids(implicit) == set()
    assert implicit == explicit


@pytest.mark.parametrize("workflow_id", H3_VIDEO_IDS)
@pytest.mark.parametrize("upscale", ["on", "off"])
def test_all_on_chains_derope_face_cadence_after_the_final_decode(workflow_id, upscale):
    spec = get_spec(workflow_id, "video")
    up = spec.upscale
    wf = build_video_workflow(h3_params(workflow_id, upscale=upscale, **ALL_ON))
    final = up.second_sampler_node if upscale == "on" else up.sampler
    decode, audio = decoders(wf, final)

    # FastDerope: 最終デコードを土台に、最終のサンプラーのラテントで動きを測る
    assert wf[f"{DEROPE}oracle"]["inputs"]["samples"] == [final, 0]
    assert wf[f"{DEROPE}preview"]["inputs"]["samples"] == [final, 0]
    assert wf[f"{DEROPE}preview"]["inputs"]["vae"] == [REFINE_TAE_NODE, 0]
    assert wf[f"{DEROPE}splice"]["inputs"]["baseline"] == [decode, 0]
    assert wf[f"{DEROPE}gate"]["inputs"]["static_images"] == [decode, 0]
    # → FaceDetail（Derope の出力を読む）
    assert wf[f"{FACE}atlas"]["inputs"]["images"] == [f"{DEROPE}splice", 0]
    assert wf[f"{FACE}stitch"]["inputs"]["images"] == [f"{DEROPE}splice", 0]
    # → AnimeCadence → CreateVideo / Trim
    assert wf[CADENCE]["inputs"]["images"] == [f"{FACE}stitch", 0]
    assert video_input(wf) == [CADENCE, 0]
    # 音声は元の生成のまま（付け替えない）
    create = next(n for n in wf.values() if n["class_type"] == "CreateVideo")
    trim = next(
        (n for n in wf.values() if n["class_type"] == "MiniMaxH3MotionContextTrim"),
        None,
    )
    assert (trim or create)["inputs"]["audio"] == [audio, 0]
    if trim is not None:
        # 連続カット版: Trim の手前で補正し、CreateVideo は Trim を読んだまま
        assert create["inputs"]["images"][0] == next(
            node_id for node_id, node in wf.items() if node is trim
        )
    # 描き直しは最終デコードと同じ VAE で仕上げる
    video_vae = wf[decode]["inputs"]["vae"]
    assert wf[f"{DEROPE}decode"]["inputs"]["vae"] == video_vae
    assert wf[f"{FACE}decode"]["inputs"]["vae"] == video_vae
    assert wf[f"{FACE}encode"]["inputs"]["vae"] == video_vae


@pytest.mark.parametrize(
    ("name", "present", "absent"),
    [
        (FAST_DEROPE_NAME, f"{DEROPE}splice", (f"{FACE}atlas", CADENCE)),
        (FACE_DETAIL_NAME, f"{FACE}stitch", (f"{DEROPE}splice", CADENCE, REFINE_TAE_NODE)),
        (
            ANIME_CADENCE_NAME,
            CADENCE,
            (f"{DEROPE}splice", f"{FACE}atlas", REFINE_DMAD_LORA_NODE),
        ),
    ],
)
def test_each_refine_select_switches_on_independently(name, present, absent):
    wf = build_video_workflow(h3_params("minimax_h3_t2v", **{name: "on"}))
    decode, _ = decoders(wf, "105:14")
    assert present in wf
    for node_id in absent:
        assert node_id not in wf
    assert video_input(wf) == [present, 0]
    first = {
        FAST_DEROPE_NAME: (f"{DEROPE}splice", "baseline"),
        FACE_DETAIL_NAME: (f"{FACE}atlas", "images"),
        ANIME_CADENCE_NAME: (CADENCE, "images"),
    }[name]
    assert wf[first[0]]["inputs"][first[1]] == [decode, 0]


def test_the_refine_parameters_follow_the_author_examples():
    wf = build_video_workflow(h3_params("minimax_h3_t2v", **ALL_ON))
    gate = wf[f"{DEROPE}gate"]["inputs"]
    assert (gate["threshold"], gate["min_windows"], gate["handle_frames"]) == (0.09, 5, 12)
    assert wf[f"{DEROPE}smear"]["inputs"]["dilation"] == 4
    # pass 2 は ManualSigmas で DMAD 4 steps を 0.8 から（作者の 11/3 の推奨。
    # ポーズ・フレーミングを pass 1 のまま保つ）
    schedule = wf[f"{DEROPE}sigmas"]
    assert schedule["class_type"] == "ManualSigmas"
    assert schedule["inputs"] == {"sigmas": "0.8, 0.6, 0.4, 0.2, 0.0"}
    assert wf[f"{DEROPE}sampler"]["inputs"]["sigmas"] == [f"{DEROPE}sigmas", 0]
    window = wf[f"{DEROPE}window"]["inputs"]
    assert (window["window"], window["anchor_stride"]) == (4, 8)
    v2v = wf[f"{DEROPE}v2v"]["inputs"]
    assert v2v["time_varying"] is True
    assert v2v["mask"] == [f"{DEROPE}freeze", 0]
    splice = wf[f"{DEROPE}splice"]["inputs"]
    assert splice["frozen"] is True and splice["restore_static"] == 0.03
    atlas = wf[f"{FACE}atlas"]["inputs"]
    assert (atlas["tile"], atlas["context"], atlas["context_scale"]) == (512, True, 0.5)
    sigmas = wf[f"{FACE}sigmas"]["inputs"]
    assert (sigmas["steps"], sigmas["sigma_at_5x"]) == (4, 0.8)
    cadence = wf[CADENCE]["inputs"]
    assert (cadence["mode"], cadence["pick"]) == ("auto", "sharpest")
    # 描き直しパスはどちらも euler
    sampler = wf[f"{DEROPE}sampler"]["inputs"]["sampler"]
    assert wf[sampler[0]]["inputs"]["sampler_name"] == "euler"
    assert wf[f"{FACE}sampler"]["inputs"]["sampler"] == sampler


def test_the_dmad_lora_comes_before_its_sigma_shift():
    """作者の例: DMAD LoRA（0.8）→ MiniMaxH3SigmaShift（12 / 2）→（Derope は）Window Attention。"""
    wf = build_video_workflow(h3_params("minimax_h3_t2v", **ALL_ON))
    lora = wf[REFINE_DMAD_LORA_NODE]
    assert lora["class_type"] == "LoraLoaderModelOnly"
    assert lora["inputs"]["lora_name"] == (
        "minimax_h3_DMAD_4step_full_lora_avg_rank_39_bf16.safetensors"
    )
    assert lora["inputs"]["strength_model"] == 0.8
    shift = wf[REFINE_DMAD_SHIFT_NODE]
    assert shift["inputs"]["model"] == [REFINE_DMAD_LORA_NODE, 0]
    assert (shift["inputs"]["shift_video"], shift["inputs"]["shift_audio"]) == (12.0, 2.0)
    assert wf[f"{DEROPE}window"]["inputs"]["model"] == [REFINE_DMAD_SHIFT_NODE, 0]
    assert wf[f"{DEROPE}guider"]["inputs"]["model"] == [f"{DEROPE}window", 0]
    # 顔パスは Window Attention を通さない
    assert wf[f"{FACE}guider"]["inputs"]["model"] == [REFINE_DMAD_SHIFT_NODE, 0]


def test_the_dmad_branch_keeps_the_user_loras_on_the_base_model():
    """素の版: ユーザーの動画 LoRA の末尾に DMAD を載せる。"""
    params = h3_params("minimax_h3_t2v", **ALL_ON).model_copy(
        update={"video_loras": [LoraRef(lora_name="style.safetensors", strength=0.7)]}
    )
    wf = build_video_workflow(params)
    assert wf[REFINE_DMAD_LORA_NODE]["inputs"]["model"] == ["app_video_lora_0", 0]
    assert not any(node_id.startswith(REFINE_BASE_LORA_PREFIX) for node_id in wf)


def test_the_dmad_branch_skips_the_opt_speed_patches():
    """opt: 高速化パッチ（Sol-Attn・Spectrum）の手前、UNET（+ ユーザー LoRA）から分岐する。"""
    wf = build_video_workflow(h3_params("minimax_h3_i2v_opt", **ALL_ON))
    assert wf[REFINE_DMAD_LORA_NODE]["inputs"]["model"] == ["127", 0]


@pytest.mark.parametrize(
    ("workflow_id", "pika"),
    [("minimax_h3_i2v_turbo", "150"), ("minimax_h3_r2v_context_turbo", "143")],
)
def test_turbo_branches_dmad_from_the_base_model_without_the_pika_lora(
    workflow_id, pika
):
    """turbo: DMAD も高速化 LoRA なので、Pika H3 Flex LoRA を通さずに UNET から組み直す。"""
    params = h3_params(workflow_id, **ALL_ON).model_copy(
        update={
            "video_loras": [
                LoraRef(lora_name="a.safetensors", strength=0.7),
                LoraRef(lora_name="b.safetensors", strength=0.5),
            ]
        }
    )
    wf = build_video_workflow(params)
    # 1 パス目はテンプレートどおり Pika → ユーザー LoRA
    assert wf["app_video_lora_0"]["inputs"]["model"] == [pika, 0]
    # DMAD 側はユーザー LoRA を同じ順・同じ強さで UNET から組み直したもの
    first = wf[f"{REFINE_BASE_LORA_PREFIX}0"]["inputs"]
    second = wf[f"{REFINE_BASE_LORA_PREFIX}1"]["inputs"]
    assert first["model"] == ["127", 0]
    assert (first["lora_name"], first["strength_model"]) == ("a.safetensors", 0.7)
    assert second["model"] == [f"{REFINE_BASE_LORA_PREFIX}0", 0]
    assert (second["lora_name"], second["strength_model"]) == ("b.safetensors", 0.5)
    assert wf[REFINE_DMAD_LORA_NODE]["inputs"]["model"] == [
        f"{REFINE_BASE_LORA_PREFIX}1",
        0,
    ]
    # Pika を読む補正のノードは無い
    for node_id in refine_ids(wf):
        assert wf[node_id]["inputs"].get("model") != [pika, 0]


def test_turbo_without_user_loras_branches_from_the_unet():
    wf = build_video_workflow(h3_params("minimax_h3_r2v_turbo", **ALL_ON))
    assert wf[REFINE_DMAD_LORA_NODE]["inputs"]["model"] == ["127", 0]


def test_the_derope_pass_runs_at_the_final_resolution_with_two_passes():
    """latent_upscale = on: pass 2 は 2 パス目（最終解像度）に掛ける。"""
    spec = get_spec("minimax_h3_i2v", "video")
    wf = build_video_workflow(h3_params("minimax_h3_i2v", upscale="on", **ALL_ON))
    final = wf[spec.upscale.upscaler_node]["inputs"]
    cond = wf[f"{DEROPE}cond"]["inputs"]
    assert (cond["width"], cond["height"]) == (final["width"], final["height"])
    # 1 パス目は 0.2MP なので最終解像度とは違う
    first = wf[spec.inject["width"].node_id]["inputs"]
    assert first["width"] != final["width"]


def test_the_i2v_derope_conditioning_pins_the_segment_start_with_taeh3():
    wf = build_video_workflow(h3_params("minimax_h3_i2v_turbo", **ALL_ON))
    cond = wf[f"{DEROPE}cond"]
    assert cond["class_type"] == "MiniMaxH3ImageToVideo"
    assert cond["inputs"]["first_frame"] == [f"{DEROPE}first_frame", 0]
    assert "last_frame" not in cond["inputs"]
    assert cond["inputs"]["vae"] == [REFINE_TAE_NODE, 0]
    assert cond["inputs"]["prompt"] == [f"{DEROPE}prompt", 0]
    assert cond["inputs"]["length"] == [f"{DEROPE}smear", 2]
    # Retime Prompt は 1 パス目と同じ（注入済みの）プロンプトを読む
    assert wf[f"{DEROPE}prompt"]["inputs"]["prompt"] == "VIDEO PROMPT"
    # 顔パスの条件付けはキャンバスの大きさ・元の尺で、キーフレームは持たない
    face = wf[f"{FACE}cond"]["inputs"]
    assert face["width"] == [f"{FACE}atlas", 4]
    assert face["height"] == [f"{FACE}atlas", 5]
    assert "first_frame" not in face and "last_frame" not in face
    assert face["length"] == wf["136"]["inputs"]["length"]


def test_the_r2v_refine_conditioning_keeps_the_references():
    wf = build_video_workflow(h3_params("minimax_h3_r2v", **ALL_ON))
    for node_id in (f"{DEROPE}cond", f"{FACE}cond"):
        cond = wf[node_id]
        assert cond["class_type"] == "MiniMaxH3ReferenceToVideo"
        assert cond["inputs"]["ref_images.ref_image_0"] == wf["136"]["inputs"][
            "ref_images.ref_image_0"
        ]
    # r2v のプロンプトは PrimitiveStringMultiline から来る
    assert wf[f"{DEROPE}prompt"]["inputs"]["prompt"] == wf["136"]["inputs"]["prompt"]


def test_the_face_pass_pins_the_render_audio():
    wf = build_video_workflow(h3_params("minimax_h3_t2v", face_detail="on"))
    _, audio = decoders(wf, "105:14")
    encode = wf[f"{FACE}audio_encode"]["inputs"]
    assert encode["audio"] == [audio, 0]
    assert encode["vae"] == wf[audio]["inputs"]["vae"]
    v2v = wf[f"{FACE}v2v"]["inputs"]
    assert v2v["audio_latent"] == [f"{FACE}audio_encode", 0]
    assert v2v["audio_mode"] == "pin the original outright (0.0)"
    assert v2v["mask"] == [f"{FACE}atlas", 1]
    assert wf[f"{FACE}sampler"]["inputs"]["sigmas"] == [f"{FACE}sigmas", 0]


def test_the_refine_passes_share_the_render_noise():
    wf = build_video_workflow(h3_params("minimax_h3_t2v", **ALL_ON))
    noise = wf["105:14"]["inputs"]["noise"]
    assert wf[f"{DEROPE}sampler"]["inputs"]["noise"] == noise
    assert wf[f"{FACE}sampler"]["inputs"]["noise"] == noise


# --------------------------------------------------------------------------
# モデルファイル（設定ページのスロット）
# --------------------------------------------------------------------------

def test_the_dmad_lora_and_taeh3_are_settings_slots():
    spec = get_spec("minimax_h3_i2v_turbo", "video")
    fields = {f.key: f for f in model_fields()}
    lora = fields[f"{spec.id}/{REFINE_DMAD_LORA_NODE}.lora_name"]
    assert lora.subfolder == "loras"
    tae = fields[f"{spec.id}/{REFINE_TAE_NODE}.tae_name"]
    assert tae.default == "taeh3_full.safetensors"
    assert tae.subfolder == "vae_approx"
    assert model_subfolder("FastDeropeTAELoader", "tae_name") == "vae_approx"


def test_model_overrides_reach_the_refine_nodes():
    spec = get_spec("minimax_h3_t2v", "video")
    overrides = {
        f"{spec.id}/{REFINE_DMAD_LORA_NODE}.lora_name": "h3/turbo/dmad.safetensors",
        f"{spec.id}/{REFINE_TAE_NODE}.tae_name": "taeh3(1).safetensors",
    }
    wf = build_video_workflow(h3_params(spec.id, **ALL_ON), overrides)
    assert wf[REFINE_DMAD_LORA_NODE]["inputs"]["lora_name"] == "h3/turbo/dmad.safetensors"
    assert wf[REFINE_TAE_NODE]["inputs"]["tae_name"] == "taeh3(1).safetensors"


def test_the_refine_nodes_are_not_required_by_the_health_check():
    """既定 off なので、入っていなくても接続インジケーターは赤くしない。"""
    required = all_required_class_types()
    for class_type in (
        *FAST_DEROPE_CLASS_TYPES,
        *FACE_DETAIL_CLASS_TYPES,
        *ANIME_CADENCE_CLASS_TYPES,
    ):
        assert class_type not in required
    optional = optional_select_class_types()
    assert optional[FAST_DEROPE_NAME] == FAST_DEROPE_CLASS_TYPES
    assert optional[FACE_DETAIL_NAME] == FACE_DETAIL_CLASS_TYPES
    assert optional[ANIME_CADENCE_NAME] == ANIME_CADENCE_CLASS_TYPES
    assert LATENT_UPSCALE_NAME not in optional


# --------------------------------------------------------------------------
# ジョブ投入（接続先ごとの固定）
# --------------------------------------------------------------------------

def _with_target(monkeypatch, target: str) -> None:
    monkeypatch.setattr(config, "_settings", Settings(comfy_target=target))


def _job(**selects) -> dict:
    params = {
        "mode": "i2v",
        "video_workflow": "minimax_h3_t2v",
        "video_prompt": "a cat",
    }
    if selects:
        params["selects"] = dict(selects)
    return params


def test_comfy_cloud_pins_the_refine_selects_off(monkeypatch):
    _with_target(monkeypatch, "comfy_cloud")
    params = _job()
    jobs._pin_target_selects(params)
    assert params["selects"] == {name: "off" for name in REFINE_SELECT_NAMES}


@pytest.mark.parametrize("name", REFINE_SELECT_NAMES)
def test_comfy_cloud_refuses_a_refine_select_set_to_on(monkeypatch, name):
    _with_target(monkeypatch, "comfy_cloud")
    with pytest.raises(jobs.JobValidationError, match=name):
        jobs._pin_target_selects(_job(**{name: "on"}))


@pytest.mark.parametrize("target", ["local", "runpod"])
def test_local_targets_leave_the_refine_selects_alone(monkeypatch, target):
    _with_target(monkeypatch, target)
    params = _job()
    jobs._pin_target_selects(params)
    assert "selects" not in params
    params = _job(**ALL_ON)
    jobs._pin_target_selects(params)
    assert params["selects"] == ALL_ON


@pytest.fixture
def api(tmp_path, monkeypatch):
    """ComfyUI に繋がない API（ジョブは投入直前の検証まで）。"""
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")

    async def offline(*_args, **_kwargs):
        raise comfy.ComfyError("ComfyUI is down")

    monkeypatch.setattr(comfy, "get_object_info", offline)
    with TestClient(app) as client:
        yield client
    config._settings = None


def test_posting_a_refine_on_job_to_comfy_cloud_is_refused(api, monkeypatch):
    """Comfy Cloud で on を送ると、投入前に 422 で断る（キューに積まない）。"""
    _with_target(monkeypatch, "comfy_cloud")
    response = api.post(
        "/api/jobs",
        json={
            "mode": "i2v",
            "video_workflow": "minimax_h3_t2v",
            "video_prompt": "a cat",
            "selects": {FAST_DEROPE_NAME: "on"},
        },
    )
    assert response.status_code == 422
    assert FAST_DEROPE_NAME in response.json()["detail"]
    assert "comfy_cloud" in response.json()["detail"]


def test_the_options_offer_only_off_on_comfy_cloud(api, monkeypatch):
    for target, expected in (("comfy_cloud", ["off"]), ("local", ["off", "on"])):
        _with_target(monkeypatch, target)
        options = api.get("/api/options").json()
        t2v = next(w for w in options["video_workflows"] if w["id"] == "minimax_h3_t2v")
        selects = {select["name"]: select for select in t2v["selects"]}
        for name in REFINE_SELECT_NAMES:
            assert selects[name]["choices"] == expected, (target, name)
            assert selects[name]["default"] == "off"
        # 外部 API の /api/v1/options も同じ表を返す（キーは要らない作りではないので
        # 中身の同一性は options ルーターの共有で担保している）


# --------------------------------------------------------------------------
# ヘルスチェック
# --------------------------------------------------------------------------

@pytest.fixture
def health_client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(
        config, "_settings", Settings(local_comfy_url="http://comfy:8188")
    )

    async def grok_ok() -> HealthStatus:
        return HealthStatus(status="ok")

    monkeypatch.setattr(grok, "check_grok", grok_ok)
    with TestClient(app) as client:
        yield client
    config._settings = None


def _object_info(monkeypatch, names) -> None:
    async def info(*_args, **_kwargs):
        return {name: {} for name in names}

    monkeypatch.setattr(comfy, "get_object_info", info)


def test_health_mentions_the_missing_refine_nodes_without_turning_red(
    health_client, monkeypatch
):
    _object_info(monkeypatch, all_required_class_types())
    status = health_client.get("/api/health").json()["comfyui"]
    assert status["status"] == "ok"
    assert "optional" in status["detail"]
    assert "fast_derope: " in status["detail"]
    assert "FastDeropeMotionGate" in status["detail"]
    assert "anime_cadence: AnimeCadence" in status["detail"]


def test_health_is_quiet_when_the_refine_nodes_are_installed(
    health_client, monkeypatch
):
    _object_info(
        monkeypatch,
        set(all_required_class_types())
        | set(FAST_DEROPE_CLASS_TYPES)
        | set(FACE_DETAIL_CLASS_TYPES)
        | set(ANIME_CADENCE_CLASS_TYPES),
    )
    status = health_client.get("/api/health").json()["comfyui"]
    assert status["status"] == "ok"
    assert "optional" not in status["detail"]


def test_health_names_only_what_is_missing(health_client, monkeypatch):
    _object_info(
        monkeypatch,
        (set(all_required_class_types()) | set(FAST_DEROPE_CLASS_TYPES))
        - {"H3V2VInit"},
    )
    detail = health_client.get("/api/health").json()["comfyui"]["detail"]
    # MAINodes が無いと FastDerope も FaceDetail も動かない
    assert "fast_derope: H3V2VInit" in detail
    assert "face_detail: H3FaceAtlas, H3FaceAtlasSigmas, H3FaceAtlasStitch, H3V2VInit" in detail
