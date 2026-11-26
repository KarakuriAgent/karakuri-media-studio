"""10Eros Max（MiniMax H3 の派生チェックポイント）の動画ワークフロー（SPEC §2.2）。

ComfyUI は要らない: テンプレートとスペックの宣言が噛み合うか、ジョブを組んだ
グラフが作者推奨のサンプリング（lcm + simple・8 ステップ・VSA・SigmaShift 12/3）
になっているか、高速化 LoRA と Spectrum が入っていないかを見る。
"""
import pytest

from app import prompts
from app.h3_examples import default_examples_for_workflow
from app.models import GenerationParams, LoraRef
from app.workflow import (
    VIDEO_LORA_NODE_PREFIX,
    build_video_workflow,
    model_fields,
    supported_on_target,
    validate_workflow,
)
from app.workflows import (
    EROS_MAX_FAMILY,
    EROS_MAX_FIRST_PASS_MEGAPIXELS,
    EROS_MAX_UNET,
    EROS_MAX_UPSCALE_SIGMAS,
    FAMILY_LABELS,
    LATENT_UPSCALE_NAME,
    LOCAL_ONLY_FAMILIES,
    REFINE_SELECT_NAMES,
    get_spec,
    load_template,
    specs_of_kind,
    validate_spec,
    video_catalog,
    video_specs,
)

#: 10Eros の全バリアント（素 3 + ラテント保存 3 + 連続カット 1）
EROS_IDS = [spec.id for spec in specs_of_kind("video") if spec.family == EROS_MAX_FAMILY]

#: 10Eros の id -> 入力の形が同じ MiniMax H3 の版（テンプレートの写し元の opt）
H3_COUNTERPARTS = {
    "10eros_max_t2v": "minimax_h3_t2v_opt",
    "10eros_max_i2v": "minimax_h3_i2v_opt",
    "10eros_max_r2v": "minimax_h3_r2v_opt",
    "10eros_max_t2v_save": "minimax_h3_t2v_save_opt",
    "10eros_max_i2v_save": "minimax_h3_i2v_save_opt",
    "10eros_max_r2v_save": "minimax_h3_r2v_save_opt",
    "10eros_max_r2v_context": "minimax_h3_r2v_context_opt",
}

#: UNETLoader から BasicGuider までの直列（LoRA も Spectrum も無い）
EROS_CHAIN = [
    "ModelAttentionBackend",
    "BlockSparseAttention",
    "MiniMaxH3SigmaShift",
]

PREV_LATENT = "h3_context/clip_00001_.latent"


def eros_params(workflow_id: str, *, upscale: str = "off", **overrides) -> GenerationParams:
    """必要な入力をそろえた 10Eros のジョブ。"""
    spec = get_spec(workflow_id, "video")
    base = dict(
        mode="i2v",
        video_workflow=workflow_id,
        job_id="01JOBID",
        aspect_ratio="16:9 (Widescreen)",
        megapixels=0.8,
        video_prompt="VIDEO PROMPT",
        duration=5,
        fps=24,
        video_seeds=[11],
        start_image_name="start.png",
        reference_video_name="prev.mp4",
        context_latent_path=PREV_LATENT,
        selects={LATENT_UPSCALE_NAME: upscale},
    )
    if spec.multi_inputs:
        base["reference_image_names"] = ["ref0.png"]
    base.update(overrides)
    return GenerationParams(**base)


def by_class(wf: dict) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for key, node in wf.items():
        found.setdefault(node["class_type"], []).append(key)
    return found


def test_the_family_has_every_variant():
    assert sorted(EROS_IDS) == sorted(H3_COUNTERPARTS)
    assert FAMILY_LABELS[EROS_MAX_FAMILY] == "10Eros Max"
    # 手動フォーム / カタログに出るのは素の 3 本だけ（_save / _context は studio_only）
    selectable = [spec.id for spec in video_specs() if spec.family == EROS_MAX_FAMILY]
    assert selectable == ["10eros_max_t2v", "10eros_max_i2v", "10eros_max_r2v"]


@pytest.mark.parametrize("workflow_id", EROS_IDS)
def test_the_manifest_matches_the_template(workflow_id):
    spec = get_spec(workflow_id, "video")
    assert spec.relpath.startswith("video/10eros-max/")
    assert validate_spec(spec, load_template(spec)) == []
    # 注入先のノードがテンプレートに実在する
    template = load_template(spec)
    for name, target in spec.inject.items():
        assert target.node_id in template, (name, target)
        assert template[target.node_id]["class_type"] == target.class_type, name


@pytest.mark.parametrize(("eros_id", "h3_id"), sorted(H3_COUNTERPARTS.items()))
def test_the_inputs_are_the_same_as_minimax_h3(eros_id, h3_id):
    """入力の形は写し元の H3 opt と同じ（steps も受け取る）。"""
    eros = get_spec(eros_id, "video")
    h3 = get_spec(h3_id, "video")
    assert eros.supported_names() == h3.supported_names()
    assert eros.supports("steps")
    assert eros.requires == h3.requires
    assert eros.multi_inputs == h3.multi_inputs
    assert eros.optional_loaders == h3.optional_loaders
    assert eros.frames == h3.frames
    assert eros.accepts_start_image == h3.accepts_start_image
    assert eros.studio_only == h3.studio_only
    assert eros.latent_output_node == h3.latent_output_node


@pytest.mark.parametrize("workflow_id", EROS_IDS)
def test_the_template_has_no_speedup_lora_and_no_spectrum(workflow_id):
    """turbo は焼き込み済み: 高速化 LoRA（Pika H3 Flex など）も Spectrum も持たない。"""
    template = load_template(workflow_id)
    classes = {node["class_type"] for node in template.values()}
    assert "LoraLoaderModelOnly" not in classes
    assert "SpectrumApplyMiniMaxH3" not in classes
    names = " ".join(str(node["inputs"]) for node in template.values()).lower()
    assert "pika" not in names


@pytest.mark.parametrize("workflow_id", EROS_IDS)
def test_the_graph_follows_the_author_recipe(workflow_id):
    """UNET -> ModelAttentionBackend -> BlockSparse(VSA) -> SigmaShift -> Guider / Scheduler。"""
    wf = build_video_workflow(eros_params(workflow_id))
    validate_workflow(wf)
    classes = by_class(wf)
    unet = classes["UNETLoader"][0]
    assert wf[unet]["inputs"]["unet_name"] == EROS_MAX_UNET
    upstream = [unet, 0]
    for class_type in EROS_CHAIN:
        (node_id,) = classes[class_type]
        assert wf[node_id]["inputs"]["model"] == upstream, class_type
        upstream = [node_id, 0]
    shift = classes["MiniMaxH3SigmaShift"][0]
    assert wf[classes["BasicGuider"][0]]["inputs"]["model"] == [shift, 0]
    assert wf[classes["BasicScheduler"][0]]["inputs"]["model"] == [shift, 0]

    attention = wf[classes["ModelAttentionBackend"][0]]["inputs"]
    assert attention["attention"] == "comfy kitchen attention"
    sparse = wf[classes["BlockSparseAttention"][0]]["inputs"]
    assert sparse["selection"] == "vsa"
    assert sparse["selection.keep_percent"] == 10
    assert sparse["start_percent"] == 0.2
    assert sparse["end_percent"] == 1.0
    assert "selection.tau" not in sparse
    assert wf[shift]["inputs"]["shift_video"] == 12
    assert wf[shift]["inputs"]["shift_audio"] == 3

    sampler = wf[classes["KSamplerSelect"][0]]["inputs"]
    assert sampler["sampler_name"] == "lcm"
    scheduler = wf[classes["BasicScheduler"][0]]["inputs"]
    assert scheduler["scheduler"] == "simple"
    assert scheduler["steps"] == 8
    assert scheduler["denoise"] == 1


@pytest.mark.parametrize("workflow_id", EROS_IDS)
def test_the_clip_and_vaes_are_shared_with_minimax_h3(workflow_id):
    wf = build_video_workflow(eros_params(workflow_id))
    classes = by_class(wf)
    assert (
        wf[classes["CLIPLoader"][0]]["inputs"]["clip_name"]
        == "qwen3vl_32b_heretic_minimax_h3_nvfp4.safetensors"
    )
    vaes = {wf[key]["inputs"]["vae_name"] for key in classes["VAELoader"]}
    assert vaes == {
        "minimax_h3_sol_vae_int8_convrot.safetensors",
        "minimax_h3_audio_vae_fp32.safetensors",
    }


@pytest.mark.parametrize("workflow_id", EROS_IDS)
@pytest.mark.parametrize("steps", [6, 12])
def test_the_steps_are_injected(workflow_id, steps):
    spec = get_spec(workflow_id, "video")
    wf = build_video_workflow(eros_params(workflow_id, steps=steps))
    assert wf[spec.target("steps").node_id]["inputs"]["steps"] == steps


@pytest.mark.parametrize("workflow_id", EROS_IDS)
def test_the_hybrid_unet_serves_every_mode(workflow_id):
    """t2v / i2v / r2v とも同じ UNET（fl2va / ref2va の別ウェイトは使わない）。"""
    template = load_template(workflow_id)
    unets = {
        node["inputs"]["unet_name"]
        for node in template.values()
        if node["class_type"] == "UNETLoader"
    }
    assert unets == {EROS_MAX_UNET}


@pytest.mark.parametrize("workflow_id", EROS_IDS)
def test_latent_upscale_uses_the_eros_second_pass(workflow_id):
    spec = get_spec(workflow_id, "video")
    select = spec.select(LATENT_UPSCALE_NAME)
    assert select is not None and select.fallback == "on"
    assert spec.upscale is not None
    assert spec.upscale.first_pass_megapixels == EROS_MAX_FIRST_PASS_MEGAPIXELS == 0.4
    assert spec.upscale.sigmas == EROS_MAX_UPSCALE_SIGMAS

    wf = build_video_workflow(eros_params(workflow_id, upscale="on", megapixels=1.0))
    validate_workflow(wf)
    assert wf[spec.upscale.sigmas_node]["inputs"]["sigmas"] == EROS_MAX_UPSCALE_SIGMAS
    second = wf[spec.upscale.second_sampler_node]["inputs"]
    assert second["sigmas"] == [spec.upscale.sigmas_node, 0]
    # 1 パス目は 0.4MP、アップスケーラの目標は指定解像度（1.0MP）
    width = wf[spec.inject["width"].node_id]["inputs"]["width"]
    height = wf[spec.inject["height"].node_id]["inputs"]["height"]
    assert abs(width * height - 0.4 * 1024 * 1024) < 0.1 * 1024 * 1024
    upscaler = wf[spec.upscale.upscaler_node]["inputs"]
    assert upscaler["width"] * upscaler["height"] > 0.8 * 1024 * 1024


def test_the_first_pass_never_exceeds_the_final_resolution():
    """指定解像度が 0.4MP 以下なら 1 パス目も同じ解像度（拡大はせず仕上げだけ）。"""
    spec = get_spec("10eros_max_t2v", "video")
    wf = build_video_workflow(
        eros_params("10eros_max_t2v", upscale="on", megapixels=0.3)
    )
    first = (
        wf[spec.inject["width"].node_id]["inputs"]["width"],
        wf[spec.inject["height"].node_id]["inputs"]["height"],
    )
    upscaler = wf[spec.upscale.upscaler_node]["inputs"]
    assert first == (upscaler["width"], upscaler["height"])


@pytest.mark.parametrize("workflow_id", EROS_IDS)
def test_no_refine_selects(workflow_id):
    """補正（DMAD 4step LoRA を重ねる描き直し）は turbo 焼き込みと二重になるので持たない。"""
    spec = get_spec(workflow_id, "video")
    assert spec.refine is None
    assert not any(name in spec.selects for name in REFINE_SELECT_NAMES)


@pytest.mark.parametrize("workflow_id", EROS_IDS)
def test_the_user_lora_chain_sits_before_the_patches(workflow_id):
    wf = build_video_workflow(
        eros_params(
            workflow_id,
            video_loras=[LoraRef(lora_name="motion.safetensors", strength=0.5)],
        )
    )
    validate_workflow(wf)
    lora = f"{VIDEO_LORA_NODE_PREFIX}0"
    assert wf[lora]["inputs"]["model"] == ["127", 0]
    assert wf["151"]["inputs"]["model"] == [lora, 0]


@pytest.mark.parametrize("workflow_id", EROS_IDS)
def test_hidden_on_comfy_cloud(workflow_id):
    """Comfy Cloud には 10Eros のウェイトが無い（本体ノードだけでも出さない）。"""
    spec = get_spec(workflow_id, "video")
    assert EROS_MAX_FAMILY in LOCAL_ONLY_FAMILIES
    assert not supported_on_target(spec, "comfy_cloud")
    assert supported_on_target(spec, "local")
    assert supported_on_target(spec, "runpod")


def test_the_agent_catalog_drops_the_family_on_comfy_cloud():
    cloud = {
        entry.id
        for entry in prompts._catalog_entries(video_catalog(), comfy_target="comfy_cloud")
    }
    assert cloud and not any(name.startswith("10eros_max") for name in cloud)
    local = {
        entry.id
        for entry in prompts._catalog_entries(video_catalog(), comfy_target="local")
    }
    assert {"10eros_max_t2v", "10eros_max_i2v", "10eros_max_r2v"} <= local


@pytest.mark.parametrize(("eros_id", "h3_id"), sorted(H3_COUNTERPARTS.items()))
def test_the_prompt_guide_is_shared_with_minimax_h3(eros_id, h3_id):
    assert prompts.video_guide_for(eros_id) == prompts.video_guide_for(h3_id) != ""
    assert eros_id in prompts.MULTI_CUT_WORKFLOWS
    assert (eros_id in prompts.MOTION_CONTEXT_WORKFLOWS) == (
        h3_id in prompts.MOTION_CONTEXT_WORKFLOWS
    )


@pytest.mark.parametrize(
    ("eros_id", "h3_id"),
    [
        ("10eros_max_t2v", "minimax_h3_t2v"),
        ("10eros_max_i2v_save", "minimax_h3_i2v"),
        ("10eros_max_r2v_context", "minimax_h3_r2v"),
    ],
)
def test_the_examples_are_shared_with_minimax_h3(eros_id, h3_id):
    picked = [example.id for example in default_examples_for_workflow(eros_id)]
    assert picked == [example.id for example in default_examples_for_workflow(h3_id)]


def test_the_unet_is_a_configurable_model_slot():
    slots = [
        field
        for field in model_fields()
        if field.family == EROS_MAX_FAMILY and field.class_type == "UNETLoader"
    ]
    assert slots and all(field.default == EROS_MAX_UNET for field in slots)
