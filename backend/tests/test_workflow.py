"""Tests for the workflow injection engine (no ComfyUI required)."""

import copy

import pytest

from app.models import GenerationParams, LoraRef
from app.workflow import (
    LORA_NODE_PREFIX,
    WorkflowError,
    all_required_class_types,
    apply_model_overrides,
    build_workflow,
    load_template,
    ltx_frame_count,
    missing_triggers,
    model_fields,
    validate_workflow,
)


@pytest.fixture(scope="module")
def template() -> dict:
    return load_template()


def params(**overrides) -> GenerationParams:
    base = dict(
        mode="full",
        job_id="01JOBID",
        aspect_ratio="16:9 (Widescreen)",
        megapixels=1.5,
        loras=[LoraRef(lora_name="kaori.safetensors", trigger_word="kaori", strength=0.8)],
        trigger_text="kaori",
        image_prompt="IMAGE PROMPT",
        video_prompt="VIDEO PROMPT",
        negative_prompt="NEGATIVE",
        duration=8,
        fps=25,
        image_seed=1234,
        video_seeds=[11, 22],
        audio_name="ref.mp3",
        start_image_name="start.png",
    )
    base.update(overrides)
    return GenerationParams(**base)


# --- common ---------------------------------------------------------------

def test_template_is_not_mutated(template):
    before = copy.deepcopy(template)
    build_workflow(template, params())
    assert template == before


@pytest.mark.parametrize("mode", ["full", "i2v", "image_only"])
def test_orphan_wildcard_removed_and_valid(template, mode):
    wf = build_workflow(template, params(mode=mode))
    assert "365:391" not in wf
    validate_workflow(wf)  # must not raise


@pytest.mark.parametrize("mode", ["full", "i2v"])
def test_video_side_injection(template, mode):
    wf = build_workflow(template, params(mode=mode))
    assert wf["433:430"]["inputs"]["value"] == "VIDEO PROMPT"
    assert wf["433:413"]["inputs"]["text"] == "NEGATIVE"
    assert wf["432"]["inputs"]["audio"] == "ref.mp3"
    assert wf["433:394"]["inputs"]["noise_seed"] == 11
    assert wf["433:395"]["inputs"]["noise_seed"] == 22
    assert wf["433:331"]["inputs"]["value"] == 8
    assert wf["433:422"]["inputs"]["value"] == 25
    assert wf["75"]["inputs"]["filename_prefix"] == "video/01JOBID"


@pytest.mark.parametrize("mode", ["full", "image_only"])
def test_image_side_injection(template, mode):
    wf = build_workflow(template, params(mode=mode))
    assert wf["366"]["inputs"]["aspect_ratio"] == "16:9 (Widescreen)"
    assert wf["366"]["inputs"]["megapixels"] == 1.5
    assert wf["365:19"]["inputs"]["value"] == "IMAGE PROMPT"
    assert wf["365:3"]["inputs"]["seed"] == 1234
    assert wf["365:24"]["inputs"]["value"] is False
    # trigger words come first, the prompt link second (§3.4)
    assert wf["365:27"]["inputs"]["string_a"] == "kaori"
    assert wf["365:27"]["inputs"]["string_b"] == ["365:20", 0]
    assert wf["365:27"]["inputs"]["delimiter"] == ", "


def test_single_seed_is_used_for_both_samplers(template):
    wf = build_workflow(template, params(video_seeds=[7]))
    assert wf["433:394"]["inputs"]["noise_seed"] == 7
    assert wf["433:395"]["inputs"]["noise_seed"] == 7


def test_explicit_filename_prefix_overrides(template):
    wf = build_workflow(template, params(filename_prefix="custom/x"))
    assert wf["75"]["inputs"]["filename_prefix"] == "custom/x"


# --- mode A ---------------------------------------------------------------

def test_mode_a_drops_load_image_and_wires_generated_image(template):
    wf = build_workflow(template, params(mode="full"))
    assert "435" not in wf
    assert wf["433:431"]["inputs"]["input"] == ["365:8", 0]
    # both subgraphs present
    assert "365:3" in wf and "433:400" in wf and "393" in wf
    assert wf["393"]["class_type"] == "PreviewImage"


# --- mode B ---------------------------------------------------------------

def test_mode_b_drops_image_subgraph_and_wires_load_image(template):
    wf = build_workflow(template, params(mode="i2v"))
    assert not [n for n in wf if n.startswith("365:")]
    assert "393" not in wf
    assert "366" in wf  # shared with video resolution
    assert wf["433:431"]["inputs"]["input"] == ["435", 0]
    assert wf["435"]["inputs"]["image"] == "start.png"
    # resize dimensions still come from 366 via 433:330 / 433:424
    assert wf["433:431"]["inputs"]["resize_type.width"] == ["433:330", 0]
    assert wf["433:330"]["inputs"]["value"] == ["366", 0]
    assert not [n for n in wf if n.startswith(LORA_NODE_PREFIX)]


# --- mode C ---------------------------------------------------------------

def test_mode_c_drops_video_subgraph_and_saves_image(template):
    wf = build_workflow(template, params(mode="image_only"))
    assert not [n for n in wf if n.startswith("433:")]
    for node_id in ("75", "432", "435"):
        assert node_id not in wf
    assert wf["393"]["class_type"] == "SaveImage"
    assert wf["393"]["inputs"]["filename_prefix"] == "images/01JOBID"
    assert wf["393"]["inputs"]["images"] == ["365:8", 0]


# --- LoRA chain (§3.4) -----------------------------------------------------

def _lora_nodes(wf: dict) -> list[str]:
    return sorted(n for n in wf if n.startswith(LORA_NODE_PREFIX))


def test_lora_chain_zero(template):
    wf = build_workflow(template, params(loras=[]))
    assert "365:15" not in wf
    assert _lora_nodes(wf) == []
    assert wf["365:23"]["inputs"]["value"] is False
    # on_true must still point at an existing node
    assert wf["365:22"]["inputs"]["on_true"] == ["365:10", 0]


def test_lora_chain_one(template):
    wf = build_workflow(
        template, params(loras=[LoraRef(lora_name="a.safetensors", strength=0.7)])
    )
    assert "365:15" not in wf
    assert _lora_nodes(wf) == ["app_lora_0"]
    node = wf["app_lora_0"]
    assert node["class_type"] == "LoraLoaderModelOnly"
    assert node["inputs"]["lora_name"] == "a.safetensors"
    assert node["inputs"]["strength_model"] == 0.7
    assert node["inputs"]["model"] == ["365:10", 0]
    assert wf["365:23"]["inputs"]["value"] is True
    assert wf["365:22"]["inputs"]["on_true"] == ["app_lora_0", 0]


def test_lora_chain_three(template):
    loras = [
        LoraRef(lora_name=f"l{i}.safetensors", strength=float(i) / 10 + 0.5)
        for i in range(3)
    ]
    wf = build_workflow(template, params(loras=loras))
    assert _lora_nodes(wf) == ["app_lora_0", "app_lora_1", "app_lora_2"]
    assert wf["app_lora_0"]["inputs"]["model"] == ["365:10", 0]
    assert wf["app_lora_1"]["inputs"]["model"] == ["app_lora_0", 0]
    assert wf["app_lora_2"]["inputs"]["model"] == ["app_lora_1", 0]
    assert wf["365:22"]["inputs"]["on_true"] == ["app_lora_2", 0]
    assert [wf[f"app_lora_{i}"]["inputs"]["lora_name"] for i in range(3)] == [
        "l0.safetensors",
        "l1.safetensors",
        "l2.safetensors",
    ]


# --- trigger words (365:27, §3.4) ------------------------------------------

def _concat(template, **overrides) -> dict:
    return build_workflow(template, params(**overrides))["365:27"]["inputs"]


def test_triggers_are_prepended_before_the_prompt(template):
    inputs = _concat(
        template, trigger_text="kaori, sketch style", image_prompt="a woman dancing"
    )
    assert inputs["string_a"] == "kaori, sketch style"
    assert inputs["string_b"] == ["365:20", 0]
    assert inputs["delimiter"] == ", "


def test_triggers_already_in_the_prompt_are_dropped(template):
    inputs = _concat(
        template,
        trigger_text="kaori, sketch style",
        image_prompt="Kaori, an adult Japanese woman, dancing",
    )
    assert inputs["string_a"] == "sketch style"
    assert inputs["delimiter"] == ", "


def test_fully_covered_triggers_pass_the_prompt_through(template):
    inputs = _concat(
        template,
        trigger_text="kaori, sketch style",
        image_prompt="kaori in a SKETCH STYLE portrait",
    )
    assert inputs["string_a"] == ""
    assert inputs["delimiter"] == ""
    assert inputs["string_b"] == ["365:20", 0]


@pytest.mark.parametrize(
    "trigger_text, loras",
    [("", []), ("   ,  ,", []), ("", [LoraRef(lora_name="a.safetensors")])],
)
def test_empty_triggers_never_produce_a_leading_comma(template, trigger_text, loras):
    inputs = _concat(
        template, trigger_text=trigger_text, loras=loras, image_prompt="a woman"
    )
    assert inputs["string_a"] == ""
    assert inputs["delimiter"] == ""


def test_partial_word_matches_do_not_count_as_present(template):
    inputs = _concat(template, trigger_text="kaori", image_prompt="kaorina dances")
    assert inputs["string_a"] == "kaori"


@pytest.mark.parametrize(
    "trigger_text, prompt, expected",
    [
        ("kaori", "", "kaori"),
        ("kaori, kaori", "", "kaori"),  # duplicates collapse
        ("  kaori ,  yui  ", "", "kaori, yui"),
        ("kaori", "KAORI smiles", ""),
        ("kaori", "kaori-chan smiles", ""),  # hyphen is a word boundary
        ("sketch style", "a sketch style drawing", ""),
        ("", "anything", ""),
    ],
)
def test_missing_triggers(trigger_text, prompt, expected):
    assert missing_triggers(trigger_text, prompt) == expected


# --- frame count rounding --------------------------------------------------

@pytest.mark.parametrize(
    ("duration", "fps", "expected"),
    [
        (8, 25, 201),     # 200 = 8*25 -> already 8n+1, unchanged
        (10, 25, 249),    # 250 -> floor(250/8)*8+1
        (7, 30, 209),     # 210 -> 208+1
        (1, 8, 9),
        (0.5, 16, 9),
        (3.2, 25, 81),    # 80 -> exact
    ],
)
def test_ltx_frame_count(duration, fps, expected):
    frames = ltx_frame_count(duration, fps)
    assert frames == expected
    assert frames % 8 == 1
    assert frames <= duration * fps + 1


def test_frame_expression_is_pinned(template):
    wf = build_workflow(template, params(duration=10, fps=25))
    assert wf["433:329"]["inputs"]["expression"] == "a * 0 + b * 0 + 249"
    # links to duration / fps are preserved so the graph shape is unchanged
    assert wf["433:329"]["inputs"]["values.a"] == ["433:331", 0]
    assert wf["433:329"]["inputs"]["values.b"] == ["433:422", 0]
    # consumers still read from 433:329
    assert wf["433:423"]["inputs"]["length"] == ["433:329", 1]
    assert wf["433:348"]["inputs"]["frames_number"] == ["433:329", 1]


# --- validation ------------------------------------------------------------

def test_validate_detects_dangling_link(template):
    wf = build_workflow(template, params(mode="full"))
    wf["433:431"]["inputs"]["input"] = ["does_not_exist", 0]
    with pytest.raises(WorkflowError) as excinfo:
        validate_workflow(wf)
    assert "does_not_exist" in str(excinfo.value)


def test_validate_detects_deleted_node(template):
    wf = build_workflow(template, params(mode="full"))
    del wf["365:8"]  # still referenced by 433:431 / 393
    with pytest.raises(WorkflowError):
        validate_workflow(wf)


def test_validate_detects_missing_class_type(template):
    wf = build_workflow(template, params(mode="image_only"))
    wf["366"].pop("class_type")
    with pytest.raises(WorkflowError):
        validate_workflow(wf)


def test_build_raises_on_broken_template(template):
    broken = copy.deepcopy(template)
    del broken["366"]  # referenced by 365:5 and 433:330
    with pytest.raises(WorkflowError):
        build_workflow(broken, params(mode="full"))


# --- health check support --------------------------------------------------

def test_required_class_types(template):
    types = all_required_class_types(template)
    assert "ImpactWildcardEncode" not in types
    assert {"ResolutionSelector", "LoraLoaderModelOnly", "SaveImage", "SaveVideo"} <= types
    built = {
        node["class_type"]
        for mode in ("full", "i2v", "image_only")
        for node in build_workflow(template, params(mode=mode)).values()
    }
    assert built <= types


# --- model file overrides (§3.3) -------------------------------------------

def test_model_fields_extraction(template):
    fields = {f.key: f for f in model_fields(template)}
    assert set(fields) == {
        "365:10.unet_name",
        "365:11.clip_name",
        "365:12.vae_name",
        "433:335.ckpt_name",
        "433:346.lora_name",
        "433:426.model_name",
        "433:427.lora_name",
        "433:428.ckpt_name",
        "433:429.text_encoder",
        "433:429.ckpt_name",
    }
    # the dynamic LoRA chain replaces 365:15, so it is never configurable
    assert "365:15.lora_name" not in fields

    unet = fields["365:10.unet_name"]
    assert (unet.node_id, unet.field, unet.class_type) == (
        "365:10", "unet_name", "UNETLoader",
    )
    assert unet.default == template["365:10"]["inputs"]["unet_name"]
    assert unet.title
    assert all(f.default for f in fields.values())


def test_apply_model_overrides_ignores_empty_and_unknown():
    wf = {"a": {"class_type": "UNETLoader", "inputs": {"unet_name": "keep.safetensors"}}}
    apply_model_overrides(wf, {"a.unet_name": "", "zz.unet_name": "x", "bogus": "y"})
    assert wf["a"]["inputs"]["unet_name"] == "keep.safetensors"
    apply_model_overrides(wf, None)
    assert wf["a"]["inputs"]["unet_name"] == "keep.safetensors"


def test_build_workflow_applies_overrides(template):
    overrides = {
        "365:10.unet_name": "custom-unet.safetensors",
        "433:428.ckpt_name": "custom-ckpt.safetensors",
    }
    wf = build_workflow(template, params(mode="full"), overrides)
    assert wf["365:10"]["inputs"]["unet_name"] == "custom-unet.safetensors"
    assert wf["433:428"]["inputs"]["ckpt_name"] == "custom-ckpt.safetensors"
    # the template keeps its own defaults
    assert template["365:10"]["inputs"]["unet_name"] != "custom-unet.safetensors"


def test_overrides_for_pruned_nodes_are_ignored(template):
    # mode B drops the whole image subgraph: an override on 365:10 is a no-op
    wf = build_workflow(template, params(mode="i2v"), {"365:10.unet_name": "x.safetensors"})
    assert "365:10" not in wf
    validate_workflow(wf)

    # mode C drops the video graph
    wf = build_workflow(
        template, params(mode="image_only"), {"433:428.ckpt_name": "x.safetensors"}
    )
    assert "433:428" not in wf


def test_dynamic_lora_chain_is_not_overridable(template):
    wf = build_workflow(template, params(mode="full"), {"365:15.lora_name": "x.safetensors"})
    assert "365:15" not in wf
    assert wf[f"{LORA_NODE_PREFIX}0"]["inputs"]["lora_name"] == "kaori.safetensors"
