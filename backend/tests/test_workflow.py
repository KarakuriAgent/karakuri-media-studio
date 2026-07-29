"""Tests for the workflow injection engine (no ComfyUI required)."""

import copy
from dataclasses import replace

import pytest

from app.models import GenerationParams, LoraRef, missing_job_fields, video_workflow_problem
from app.workflow import (
    ASPECT_RATIOS,
    LORA_NODE_PREFIX,
    VIDEO_LORA_NODE_PREFIX,
    WorkflowError,
    all_required_class_types,
    apply_model_overrides,
    build_image_workflow,
    build_video_workflow,
    build_workflows,
    ltx_frame_count,
    missing_triggers,
    model_fields,
    parse_aspect_ratio,
    resolution,
    resolution_for_image,
    validate_manifests,
    validate_workflow,
)
from app.workflows import (
    ANIMA,
    DEFAULT_IMAGE_WORKFLOW,
    DEFAULT_VIDEO_WORKFLOW,
    GENERATED_AUDIO,
    INPUT_FIELDS,
    QWEN_IMAGE_EDIT,
    SPECS,
    KREA2_TURBO,
    T,
    WorkflowSpecError,
    Z_IMAGE_TURBO,
    catalog_entry,
    get_spec,
    image_catalog,
    image_families,
    image_specs,
    load_template,
    validate_spec,
    validate_specs,
    video_catalog,
    video_specs,
)

VIDEO_IDS = [spec.id for spec in video_specs()]
IMAGE_IDS = [spec.id for spec in image_specs()]


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
        end_image_name="end.png",
        reference_video_name="ref.mp4",
    )
    base.update(overrides)
    return GenerationParams(**base)


def inputs(wf: dict, spec, name: str) -> dict:
    return wf[spec.inject[name].node_id]["inputs"]


def value(wf: dict, spec, name: str):
    target = spec.inject[name]
    return wf[target.node_id]["inputs"][target.field]


# --------------------------------------------------------------------------
# manifests
# --------------------------------------------------------------------------

def test_every_manifest_matches_its_template():
    assert validate_specs(use_cache=False) == []
    validate_manifests()  # must not raise


def test_manifest_mismatch_is_reported(tmp_path, monkeypatch):
    """A renamed / retyped node must be caught, not silently ignored."""
    from app import workflows

    broken = copy.deepcopy(load_template(KREA2_TURBO))
    broken["30:19"]["class_type"] = "SomethingElse"
    del broken["49"]
    monkeypatch.setattr(
        workflows, "load_template", lambda spec, use_cache=True: broken
    )
    problems = workflows.validate_spec(KREA2_TURBO, broken)
    assert any("30:19" in p for p in problems)
    assert any("49" in p for p in problems)


def test_unknown_workflow_id():
    with pytest.raises(WorkflowSpecError):
        get_spec("nope")


def test_ids_are_unique_and_files_exist():
    assert len({spec.id for spec in SPECS}) == len(SPECS)
    for spec in SPECS:
        assert spec.path.is_file(), spec.path
    assert get_spec(DEFAULT_IMAGE_WORKFLOW).kind == "image"
    assert get_spec(DEFAULT_VIDEO_WORKFLOW).kind == "video"


def test_templates_are_not_mutated():
    before = copy.deepcopy(load_template(KREA2_TURBO))
    build_image_workflow(params())
    assert load_template(KREA2_TURBO) == before

    for workflow_id in VIDEO_IDS:
        spec = get_spec(workflow_id)
        snapshot = copy.deepcopy(load_template(spec))
        build_video_workflow(params(video_workflow=workflow_id))
        assert load_template(spec) == snapshot


# --------------------------------------------------------------------------
# prompt catalog (SPEC §4.3 / AGENT-MODE §3.1)
# --------------------------------------------------------------------------

def test_every_workflow_documents_itself():
    for spec in SPECS:
        assert spec.description.strip(), spec.id
    for spec in video_specs():
        assert spec.prompt_hint.strip(), spec.id


def test_catalog_inputs_are_derived_from_the_manifest():
    for spec in video_specs():
        entry = catalog_entry(spec)
        assert entry.required_fields == tuple(
            INPUT_FIELDS[name] for name in spec.requires
        )
        assert entry.accepts_start_image == spec.accepts_start_image
        assert entry.audio.strip()
        # 全論理入力が必須か任意のどちらかに一度だけ現れる
        listed = entry.required_inputs + entry.optional_inputs
        assert len(listed) == len({field for field, _ in listed})


def test_catalog_labels_the_image_input_per_workflow():
    entries = {entry.id: entry for entry in video_catalog()}
    assert entries["ltx2_3_flf2v"].required_inputs == (
        ("source_image", "最初のフレーム"),
        ("end_image", "最後のフレーム画像"),
    )
    assert entries["ltx2_3_ic_lora_image"].required_inputs == (
        ("source_image", "リファレンスシート画像"),
    )
    assert entries["ltx2_3_ic_lora_motion"].required_inputs[-1] == (
        "reference_video",
        "参照動画",
    )


def test_catalog_explains_how_audio_is_used():
    entries = {entry.id: entry for entry in video_catalog()}
    # 音声入力を持たないワークフローはモデル生成音声だと明言する
    for workflow_id in ("ltx2_3_t2v", "tx2_3_i2v", "ltx2_3_flf2v"):
        assert entries[workflow_id].audio == GENERATED_AUDIO
    assert "音声トラック" in entries["tx2_3_ia2v"].audio
    assert "リファレンス音声" in entries["ltx2_3_id_lora"].audio


def test_an_undocumented_workflow_is_a_manifest_problem():
    """カタログはマニフェスト由来なので、説明なしの追加は健全性チェックで落ちる。"""
    spec = replace(get_spec("ltx2_3_t2v"), description="", prompt_hint="")
    problems = validate_spec(spec)
    assert any("description is empty" in p for p in problems)
    assert any("prompt_hint is empty" in p for p in problems)


def test_a_lora_chain_consumer_that_does_not_read_the_head_is_reported():
    """The chain is spliced into an existing edge, so the wiring is validated."""
    spec = get_spec("ltx2_3_t2v")
    stray = replace(
        spec,
        lora_chain=replace(
            spec.lora_chain, consumers=(spec.lora_chain.consumers[0],), head="267:236"
        ),
    )
    assert any("expected the chain head" in p for p in validate_spec(stray))

    missing = replace(spec, lora_chain=replace(spec.lora_chain, head="nope"))
    assert any("lora_chain.head" in p for p in validate_spec(missing))

    empty = replace(spec, lora_chain=replace(spec.lora_chain, consumers=()))
    assert any("no consumers" in p for p in validate_spec(empty))


def test_a_lora_chain_consumer_of_the_wrong_type_is_reported():
    spec = get_spec("ltx2_3_t2v")
    retyped = replace(
        spec,
        lora_chain=replace(
            spec.lora_chain,
            consumers=(T("267:213", "model", "KSampler"),),
        ),
    )
    assert any("267:213" in p for p in validate_spec(retyped))


def test_an_audio_input_without_an_audio_role_is_reported():
    spec = replace(get_spec("ltx2_3_id_lora"), audio_role="")
    assert any("audio_role" in p for p in validate_spec(spec))


# --------------------------------------------------------------------------
# image stage (krea2)
# --------------------------------------------------------------------------

def test_image_injection():
    wf = build_image_workflow(params())
    spec = KREA2_TURBO
    assert value(wf, spec, "aspect_ratio") == "16:9 (Widescreen)"
    assert value(wf, spec, "megapixels") == 1.5
    assert value(wf, spec, "prompt") == "IMAGE PROMPT"
    assert value(wf, spec, "seed") == 1234
    # the local TextGenerate refine stays off: Grok writes the final prompt
    assert value(wf, spec, "refine_enable") is False
    assert value(wf, spec, "save_prefix") == "images/01JOBID"
    validate_workflow(wf)


# --------------------------------------------------------------------------
# image stage: the other model families
# --------------------------------------------------------------------------

def test_every_image_manifest_validates():
    for spec in image_specs():
        assert validate_spec(spec) == [], spec.id


def test_image_families_are_one_per_folder():
    assert image_families() == ["krea2", "anima", "z-image", "qwen-image"]
    assert [entry.family for entry in image_catalog()] == image_families()
    # every image workflow documents itself for the Grok catalog
    for entry in image_catalog():
        assert entry.description.strip()


def test_anima_injection():
    wf = build_image_workflow(params(image_workflow="anima"), spec=ANIMA)
    assert value(wf, ANIMA, "aspect_ratio") == "16:9 (Widescreen)"
    assert value(wf, ANIMA, "megapixels") == 1.5
    assert value(wf, ANIMA, "prompt") == "IMAGE PROMPT"
    assert value(wf, ANIMA, "seed") == 1234
    assert value(wf, ANIMA, "save_prefix") == "images/01JOBID"
    # the negative prompt is left at the template default
    assert wf["90:75"]["inputs"]["text"].startswith("worst quality")
    validate_workflow(wf)


def test_anima_lora_chain():
    wf = build_image_workflow(
        params(image_workflow="anima", loras=[LoraRef(lora_name="a.safetensors")]),
        spec=ANIMA,
    )
    # the template placeholder is replaced by the dynamic chain
    assert "90:83" not in wf
    assert wf["app_lora_0"]["inputs"]["model"] == ["90:78", 0]
    assert wf["90:76"]["inputs"]["model"] == ["app_lora_0", 0]

    empty = build_image_workflow(
        params(image_workflow="anima", loras=[]), spec=ANIMA
    )
    assert empty["90:76"]["inputs"]["model"] == ["90:78", 0]


def test_z_image_computes_its_own_resolution():
    """No ResolutionSelector in this graph: the app injects plain integers."""
    wf = build_image_workflow(
        params(image_workflow="z_image_turbo"), spec=Z_IMAGE_TURBO
    )
    width, height = resolution("16:9 (Widescreen)", 1.5)
    assert value(wf, Z_IMAGE_TURBO, "width") == width
    assert value(wf, Z_IMAGE_TURBO, "height") == height
    assert value(wf, Z_IMAGE_TURBO, "prompt") == "IMAGE PROMPT"
    assert value(wf, Z_IMAGE_TURBO, "seed") == 1234
    assert value(wf, Z_IMAGE_TURBO, "save_prefix") == "images/01JOBID"
    validate_workflow(wf)


def test_z_image_lora_chain():
    wf = build_image_workflow(
        params(
            image_workflow="z_image_turbo",
            loras=[LoraRef(lora_name="a.safetensors")],
        ),
        spec=Z_IMAGE_TURBO,
    )
    assert "57:63" not in wf
    assert wf["app_lora_0"]["inputs"]["model"] == ["57:28", 0]
    assert wf["57:11"]["inputs"]["model"] == ["app_lora_0", 0]


def test_qwen_edit_injection():
    wf = build_image_workflow(
        params(image_workflow="qwen_image_edit_2511"), spec=QWEN_IMAGE_EDIT
    )
    # the picture to edit comes from the uploaded source_image
    assert value(wf, QWEN_IMAGE_EDIT, "image") == "start.png"
    assert value(wf, QWEN_IMAGE_EDIT, "prompt") == "IMAGE PROMPT"
    assert value(wf, QWEN_IMAGE_EDIT, "seed") == 1234
    assert value(wf, QWEN_IMAGE_EDIT, "save_prefix") == "images/01JOBID"
    # the size follows the input picture, so no aspect ratio is injected
    assert not QWEN_IMAGE_EDIT.supports("aspect_ratio")
    assert not QWEN_IMAGE_EDIT.supports("megapixels")
    validate_workflow(wf)


def test_qwen_edit_user_lora_applies_to_both_switch_branches():
    """The 4-steps Lightning LoRA stays; the user chain goes in front of it."""
    wf = build_image_workflow(
        params(
            image_workflow="qwen_image_edit_2511",
            loras=[LoraRef(lora_name="a.safetensors", strength=0.6)],
        ),
        spec=QWEN_IMAGE_EDIT,
    )
    # the template's own Lightning loader is NOT a placeholder
    assert wf["170:153"]["inputs"]["lora_name"].startswith("Qwen-Image-Edit-2511")
    assert wf["app_lora_0"]["inputs"]["model"] == ["170:152", 0]
    # both branches of the Switch (Model) read the user chain
    assert wf["170:153"]["inputs"]["model"] == ["app_lora_0", 0]
    assert wf["170:163"]["inputs"]["on_false"] == ["app_lora_0", 0]
    assert wf["170:163"]["inputs"]["on_true"] == ["170:153", 0]
    validate_workflow(wf)

    empty = build_image_workflow(
        params(image_workflow="qwen_image_edit_2511", loras=[]),
        spec=QWEN_IMAGE_EDIT,
    )
    assert empty["170:153"]["inputs"]["model"] == ["170:152", 0]
    assert empty["170:163"]["inputs"]["on_false"] == ["170:152", 0]


def test_image_workflow_is_selected_by_id():
    for workflow_id in IMAGE_IDS:
        wf = build_image_workflow(params(image_workflow=workflow_id))
        spec = get_spec(workflow_id, "image")
        assert value(wf, spec, "prompt") == "IMAGE PROMPT"
        validate_workflow(wf)


def test_image_templates_are_not_mutated():
    for workflow_id in IMAGE_IDS:
        spec = get_spec(workflow_id)
        snapshot = copy.deepcopy(load_template(spec))
        build_image_workflow(params(image_workflow=workflow_id))
        assert load_template(spec) == snapshot


def test_explicit_filename_prefix_overrides():
    assert (
        value(build_image_workflow(params(filename_prefix="custom/x")), KREA2_TURBO,
              "save_prefix")
        == "custom/x"
    )


# --- LoRA chain (§3.4) -----------------------------------------------------

def _lora_nodes(wf: dict) -> list[str]:
    return sorted(n for n in wf if n.startswith(LORA_NODE_PREFIX))


def test_lora_chain_zero():
    wf = build_image_workflow(params(loras=[]))
    assert _lora_nodes(wf) == []
    # every placeholder loader is gone and the sampler reads the UNET directly
    for node_id in KREA2_TURBO.lora_chain.placeholders:
        assert node_id not in wf
    assert wf["30:3"]["inputs"]["model"] == ["30:10", 0]
    validate_workflow(wf)


def test_lora_chain_one():
    wf = build_image_workflow(
        params(loras=[LoraRef(lora_name="a.safetensors", strength=0.7)])
    )
    assert _lora_nodes(wf) == ["app_lora_0"]
    node = wf["app_lora_0"]
    assert node["class_type"] == "LoraLoaderModelOnly"
    assert node["inputs"]["lora_name"] == "a.safetensors"
    assert node["inputs"]["strength_model"] == 0.7
    assert node["inputs"]["model"] == ["30:10", 0]
    assert wf["30:3"]["inputs"]["model"] == ["app_lora_0", 0]


def test_lora_chain_three():
    loras = [
        LoraRef(lora_name=f"l{i}.safetensors", strength=float(i) / 10 + 0.5)
        for i in range(3)
    ]
    wf = build_image_workflow(params(loras=loras))
    assert _lora_nodes(wf) == ["app_lora_0", "app_lora_1", "app_lora_2"]
    assert wf["app_lora_0"]["inputs"]["model"] == ["30:10", 0]
    assert wf["app_lora_1"]["inputs"]["model"] == ["app_lora_0", 0]
    assert wf["app_lora_2"]["inputs"]["model"] == ["app_lora_1", 0]
    assert wf["30:3"]["inputs"]["model"] == ["app_lora_2", 0]
    assert [wf[f"app_lora_{i}"]["inputs"]["lora_name"] for i in range(3)] == [
        "l0.safetensors",
        "l1.safetensors",
        "l2.safetensors",
    ]


def test_lora_chain_can_exceed_the_template_placeholders():
    loras = [LoraRef(lora_name=f"l{i}.safetensors") for i in range(7)]
    wf = build_image_workflow(params(loras=loras))
    assert len(_lora_nodes(wf)) == 7
    validate_workflow(wf)


# --- video LoRA chain (§3.4) ------------------------------------------------

def _video_lora_nodes(wf: dict) -> list[str]:
    return sorted(n for n in wf if n.startswith(VIDEO_LORA_NODE_PREFIX))


def _video(workflow_id: str, **overrides) -> dict:
    spec = get_spec(workflow_id, "video")
    return build_video_workflow(params(video_workflow=workflow_id, **overrides), spec=spec)


@pytest.mark.parametrize("workflow_id", VIDEO_IDS)
def test_every_video_workflow_declares_a_lora_chain(workflow_id):
    assert get_spec(workflow_id, "video").lora_chain is not None


@pytest.mark.parametrize("workflow_id", VIDEO_IDS)
def test_no_video_lora_keeps_the_template_wiring(workflow_id):
    spec = get_spec(workflow_id, "video")
    wf = _video(workflow_id, video_loras=[])
    template = load_template(spec)
    assert _video_lora_nodes(wf) == []
    for consumer in spec.lora_chain.consumers:
        # unchanged except that a placeholder-free chain is normalised to head
        assert wf[consumer.node_id]["inputs"][consumer.field] == [spec.lora_chain.head, 0]
        assert (
            template[consumer.node_id]["inputs"][consumer.field][0]
            == spec.lora_chain.head
        )
    validate_workflow(wf)


@pytest.mark.parametrize("workflow_id", VIDEO_IDS)
def test_video_loras_are_chained_between_head_and_consumers(workflow_id):
    spec = get_spec(workflow_id, "video")
    loras = [
        LoraRef(lora_name="v0.safetensors", strength=0.6),
        LoraRef(lora_name="v1.safetensors", strength=1.2),
    ]
    wf = _video(workflow_id, video_loras=loras)

    assert _video_lora_nodes(wf) == ["app_video_lora_0", "app_video_lora_1"]
    first, second = wf["app_video_lora_0"], wf["app_video_lora_1"]
    assert first["class_type"] == "LoraLoaderModelOnly"
    assert first["inputs"] == {
        "lora_name": "v0.safetensors",
        "strength_model": 0.6,
        "model": [spec.lora_chain.head, 0],
    }
    assert second["inputs"]["lora_name"] == "v1.safetensors"
    assert second["inputs"]["strength_model"] == 1.2
    assert second["inputs"]["model"] == ["app_video_lora_0", 0]
    for consumer in spec.lora_chain.consumers:
        assert wf[consumer.node_id]["inputs"][consumer.field] == ["app_video_lora_1", 0]
    validate_workflow(wf)


def test_the_video_chain_keeps_the_templates_fixed_loras():
    """The distill / ID-LoRA nodes stay: the user chain is spliced after them."""
    wf = _video("ltx2_3_id_lora", video_loras=[LoraRef(lora_name="v.safetensors")])
    # distill LoRA (the head) still hangs off the checkpoint
    assert wf["340:293"]["inputs"]["model"] == ["340:317", 0]
    # …and the talkvid ID-LoRA now reads the user chain
    assert wf["340:346"]["inputs"]["model"] == ["app_video_lora_0", 0]
    assert wf["340:349"]["inputs"]["model"] == ["340:346", 0]


def test_video_loras_do_not_leak_into_the_image_workflow():
    wf = build_image_workflow(
        params(loras=[], video_loras=[LoraRef(lora_name="v.safetensors")])
    )
    assert _video_lora_nodes(wf) == []


def test_image_loras_do_not_leak_into_the_video_workflow():
    wf = _video("ltx2_3_t2v", loras=[LoraRef(lora_name="i.safetensors")])
    assert [n for n in wf if n.startswith(LORA_NODE_PREFIX)] == []


# --- video trigger words (§3.4) --------------------------------------------

def _video_prompt(workflow_id: str = "ltx2_3_t2v", **overrides) -> str:
    spec = get_spec(workflow_id, "video")
    wf = _video(workflow_id, **overrides)
    return value(wf, spec, "prompt")


def test_video_triggers_are_prepended_to_the_video_prompt():
    got = _video_prompt(
        video_trigger_text="slowmo, neon",
        video_prompt="a woman dancing",
    )
    assert got == "slowmo, neon, a woman dancing"


def test_video_triggers_already_present_are_not_repeated():
    got = _video_prompt(
        video_trigger_text="slowmo, neon",
        video_prompt="SLOWMO shot of a woman dancing",
    )
    assert got == "neon, SLOWMO shot of a woman dancing"


def test_video_triggers_default_to_the_selected_loras():
    got = _video_prompt(
        video_trigger_text="",
        video_loras=[LoraRef(lora_name="v.safetensors", trigger_word="slowmo")],
        video_prompt="a woman dancing",
    )
    assert got == "slowmo, a woman dancing"


def test_no_video_trigger_leaves_the_prompt_untouched():
    assert _video_prompt(video_prompt="a woman dancing") == "a woman dancing"


# --- trigger words (30:27 / 30:28, §3.4) -----------------------------------

def _concat(**overrides) -> dict:
    return inputs(build_image_workflow(params(**overrides)), KREA2_TURBO, "trigger_concat")


def test_triggers_are_prepended_before_the_prompt():
    got = _concat(trigger_text="kaori, sketch style", image_prompt="a woman dancing")
    assert got["string_a"] == "kaori, sketch style"
    assert got["string_b"] == ["30:20", 0]
    assert got["delimiter"] == ", "
    wf = build_image_workflow(
        params(trigger_text="kaori", image_prompt="a woman dancing")
    )
    assert value(wf, KREA2_TURBO, "trigger_switch") is True


def test_triggers_already_in_the_prompt_are_dropped():
    got = _concat(
        trigger_text="kaori, sketch style",
        image_prompt="Kaori, an adult Japanese woman, dancing",
    )
    assert got["string_a"] == "sketch style"
    assert got["delimiter"] == ", "


def test_fully_covered_triggers_pass_the_prompt_through():
    wf = build_image_workflow(
        params(
            trigger_text="kaori, sketch style",
            image_prompt="kaori in a SKETCH STYLE portrait",
        )
    )
    got = inputs(wf, KREA2_TURBO, "trigger_concat")
    assert got["string_a"] == ""
    assert got["delimiter"] == ""
    assert got["string_b"] == ["30:20", 0]
    # the concatenation is bypassed entirely
    assert value(wf, KREA2_TURBO, "trigger_switch") is False


@pytest.mark.parametrize(
    "trigger_text, loras",
    [("", []), ("   ,  ,", []), ("", [LoraRef(lora_name="a.safetensors")])],
)
def test_empty_triggers_never_produce_a_leading_comma(trigger_text, loras):
    got = _concat(trigger_text=trigger_text, loras=loras, image_prompt="a woman")
    assert got["string_a"] == ""
    assert got["delimiter"] == ""


def test_partial_word_matches_do_not_count_as_present():
    assert _concat(trigger_text="kaori", image_prompt="kaorina dances")["string_a"] == "kaori"


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


# --------------------------------------------------------------------------
# video stage — every template
# --------------------------------------------------------------------------

@pytest.mark.parametrize("workflow_id", VIDEO_IDS)
def test_video_injection(workflow_id):
    spec = get_spec(workflow_id)
    wf = build_video_workflow(params(video_workflow=workflow_id))
    validate_workflow(wf)

    assert value(wf, spec, "prompt") == "VIDEO PROMPT"
    assert value(wf, spec, "negative") == "NEGATIVE"
    assert value(wf, spec, "save_prefix") == "video/01JOBID"
    assert value(wf, spec, "fps") == 25
    # 1.5 MP @ 16:9 -> the same numbers ResolutionSelector would produce
    assert (value(wf, spec, "width"), value(wf, spec, "height")) == resolution(
        "16:9 (Widescreen)", 1.5
    )
    assert value(wf, spec, "duration") == pytest.approx(8)
    # prompt enhancement is always off (Grok writes the prompt)
    if spec.supports("prompt_enhance"):
        assert value(wf, spec, "prompt_enhance") is False
    # every declared asset input got the uploaded file name
    expected = {
        "image": "start.png",
        "end_image": "end.png",
        "audio": "ref.mp3",
        "video": "ref.mp4",
    }
    for name in spec.requires:
        assert value(wf, spec, name) == expected[name]


@pytest.mark.parametrize("workflow_id", VIDEO_IDS)
def test_video_seeds_are_injected(workflow_id):
    spec = get_spec(workflow_id)
    wf = build_video_workflow(params(video_workflow=workflow_id))
    got = [wf[t.node_id]["inputs"][t.field] for t in spec.seeds]
    assert got == [11, 22][: len(got)]


def test_single_seed_is_used_for_every_sampler():
    spec = get_spec("tx2_3_i2v")
    wf = build_video_workflow(params(video_workflow=spec.id, video_seeds=[7]))
    assert [wf[t.node_id]["inputs"][t.field] for t in spec.seeds] == [7, 7]


@pytest.mark.parametrize("workflow_id", VIDEO_IDS)
def test_empty_negative_keeps_the_template_default(workflow_id):
    spec = get_spec(workflow_id)
    template_value = value(load_template(spec), spec, "negative")
    wf = build_video_workflow(params(video_workflow=workflow_id, negative_prompt="  "))
    assert value(wf, spec, "negative") == template_value


def test_int_and_float_duration_nodes_are_typed():
    """PrimitiveInt must not receive a float and PrimitiveFloat must stay float."""
    int_spec = get_spec("tx2_3_i2v")  # Duration is a PrimitiveInt
    float_spec = get_spec("tx2_3_ia2v")  # Duration is a PrimitiveFloat
    int_wf = build_video_workflow(params(video_workflow=int_spec.id, duration=7.4))
    float_wf = build_video_workflow(params(video_workflow=float_spec.id, duration=7.4))
    assert value(int_wf, int_spec, "duration") == 7
    assert isinstance(value(int_wf, int_spec, "duration"), int)
    assert value(float_wf, float_spec, "duration") == 7.4


def test_motion_workflow_slices_the_reference_clip():
    spec = get_spec("ltx2_3_ic_lora_motion")
    wf = build_video_workflow(params(video_workflow=spec.id, duration=6))
    # duration drives "Video Slice" instead of a PrimitiveInt: the frame count of
    # this workflow follows the reference clip.
    assert wf["692"]["inputs"]["duration"] == 6.0
    assert not spec.supports("frames_expr")


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


@pytest.mark.parametrize("workflow_id", VIDEO_IDS)
def test_frame_expression_is_pinned(workflow_id):
    spec = get_spec(workflow_id)
    if not spec.supports("frames_expr"):
        pytest.skip(f"{workflow_id} derives its length from the reference clip")
    node_id = spec.inject["frames_expr"].node_id
    template_inputs = load_template(spec)[node_id]["inputs"]
    wf = build_video_workflow(params(video_workflow=workflow_id, duration=10, fps=25))
    got = wf[node_id]["inputs"]
    assert got["expression"].endswith("249")
    # the value links are untouched, so the graph shape does not change
    for key, link in template_inputs.items():
        if key.startswith("values."):
            assert got[key] == link
            assert f"{key.split('.', 1)[1]} * 0" in got["expression"]


# --- resolution ------------------------------------------------------------

@pytest.mark.parametrize(
    "label, expected",
    [
        ("1:1 (Square)", (1, 1)),
        ("16:9 (Widescreen)", (16, 9)),
        ("9:16 (Portrait Widescreen)", (9, 16)),
        ("21:9", (21, 9)),  # unknown label, still parsable
    ],
)
def test_parse_aspect_ratio(label, expected):
    assert parse_aspect_ratio(label) == expected


@pytest.mark.parametrize("label", ["", "square", "0:5", "x:y (Nope)"])
def test_parse_aspect_ratio_rejects_garbage(label):
    with pytest.raises(WorkflowError):
        parse_aspect_ratio(label)


@pytest.mark.parametrize(
    "label, megapixels, expected",
    [
        ("1:1 (Square)", 1.0, (1024, 1024)),
        ("16:9 (Widescreen)", 1.0, (1368, 768)),
        ("4:3 (Standard)", 1.0, (1184, 888)),
    ],
)
def test_resolution_matches_resolution_selector(label, megapixels, expected):
    assert resolution(label, megapixels) == expected


@pytest.mark.parametrize("label", list(ASPECT_RATIOS))
def test_resolution_is_always_a_multiple_of_eight(label):
    width, height = resolution(label, 1.3)
    assert width % 8 == 0 and height % 8 == 0
    assert width >= 8 and height >= 8


# --- reference image drives the aspect ratio (SPEC §3.1) --------------------

def test_resolution_for_image_matches_the_equivalent_ratio():
    # 1920x1080 is 16:9, so it must land on the very same edges as the preset
    assert resolution_for_image(1920, 1080, 1.0) == resolution("16:9 (Widescreen)", 1.0)


@pytest.mark.parametrize(
    "size", [(1920, 1080), (1000, 1500), (100, 1000), (1000, 100), (3, 4)]
)
def test_resolution_for_image_keeps_the_budget_and_the_grid(size):
    width, height = resolution_for_image(*size, 1.0)
    assert width % 8 == 0 and height % 8 == 0
    assert width >= 8 and height >= 8
    # same pixel budget (rounding to a multiple of 8 costs at most a few %)
    assert abs(width * height - 1024 * 1024) / (1024 * 1024) < 0.1
    # and the image's own ratio, within the rounding error
    assert abs(width / height - size[0] / size[1]) < 0.1 * (size[0] / size[1])


@pytest.mark.parametrize("size", [(0, 100), (100, 0), (-8, 8)])
def test_resolution_for_image_rejects_a_degenerate_size(size):
    with pytest.raises(WorkflowError):
        resolution_for_image(*size, 1.0)


@pytest.mark.parametrize(
    "workflow_id", ["tx2_3_i2v", "tx2_3_ia2v", "ltx2_3_id_lora", "ltx2_3_flf2v",
                    "ltx2_3_ic_lora_motion"]
)
def test_start_frame_size_overrides_the_aspect_ratio_preset(workflow_id):
    spec = get_spec(workflow_id)
    wf = build_video_workflow(
        params(
            mode="i2v",
            video_workflow=workflow_id,
            aspect_ratio="16:9 (Widescreen)",
            megapixels=1.0,
            start_image_size=(1000, 1500),
        )
    )
    expected = resolution_for_image(1000, 1500, 1.0)
    assert (value(wf, spec, "width"), value(wf, spec, "height")) == expected
    # portrait image => portrait output, not the 16:9 preset
    assert value(wf, spec, "height") > value(wf, spec, "width")


def test_reference_sheet_ignores_the_start_frame_size():
    """The IC-LoRA sheet sizes a ResizeAndPadImage target: preset only."""
    spec = get_spec("ltx2_3_ic_lora_image")
    wf = build_video_workflow(
        params(
            mode="i2v",
            video_workflow="ltx2_3_ic_lora_image",
            aspect_ratio="16:9 (Widescreen)",
            megapixels=1.0,
            start_image_size=(1000, 1500),
        )
    )
    expected = resolution("16:9 (Widescreen)", 1.0)
    assert (value(wf, spec, "width"), value(wf, spec, "height")) == expected


def test_without_a_start_frame_size_the_preset_is_used():
    """No readable reference image (or none at all) => unchanged behaviour."""
    spec = get_spec("tx2_3_i2v")
    wf = build_video_workflow(
        params(
            mode="i2v",
            video_workflow="tx2_3_i2v",
            aspect_ratio="16:9 (Widescreen)",
            megapixels=1.0,
            start_image_size=None,
        )
    )
    expected = resolution("16:9 (Widescreen)", 1.0)
    assert (value(wf, spec, "width"), value(wf, spec, "height")) == expected


# --------------------------------------------------------------------------
# stage selection per mode (SPEC §2)
# --------------------------------------------------------------------------

def test_full_mode_builds_both_stages():
    stages = build_workflows(params(mode="full"))
    assert sorted(stages) == ["image", "video"]


def test_image_only_builds_the_image_stage_only():
    assert list(build_workflows(params(mode="image_only"))) == ["image"]


def test_i2v_builds_the_video_stage_only():
    assert list(build_workflows(params(mode="i2v"))) == ["video"]


def test_unknown_workflow_becomes_a_workflow_error():
    with pytest.raises(WorkflowError):
        build_workflows(params(mode="i2v", video_workflow="nope"))


# --------------------------------------------------------------------------
# per-workflow requirements (SPEC §2 / §9)
# --------------------------------------------------------------------------

def _missing(mode: str, workflow_id: str, **fields) -> list[str]:
    payload = {
        "image_prompt": "i",
        "video_prompt": "v",
        "audio_path": None,
        "source_image": None,
        "end_image": None,
        "reference_video": None,
    }
    payload.update(fields)
    return missing_job_fields(mode, video_workflow=workflow_id, **payload)


def test_t2v_needs_no_assets():
    assert _missing("i2v", "ltx2_3_t2v") == []


def test_i2v_needs_a_start_frame():
    assert _missing("i2v", "tx2_3_i2v") == ["source_image"]
    assert _missing("i2v", "tx2_3_i2v", source_image="/assets/image/a.png") == []


def test_id_lora_needs_image_and_audio():
    assert _missing("i2v", "ltx2_3_id_lora") == ["source_image", "audio_path"]


def test_flf2v_needs_two_images():
    assert _missing("i2v", "ltx2_3_flf2v") == ["source_image", "end_image"]
    # full mode generates the first frame, so only the closing one is required
    assert _missing("full", "ltx2_3_flf2v") == ["end_image"]


def test_motion_needs_a_reference_clip():
    assert _missing("i2v", "ltx2_3_ic_lora_motion") == ["source_image", "reference_video"]


def test_image_only_ignores_the_video_workflow():
    assert missing_job_fields(
        "image_only",
        image_prompt="",
        video_prompt=None,
        audio_path=None,
        source_image=None,
        video_workflow="ltx2_3_ic_lora_motion",
    ) == ["image_prompt"]


@pytest.mark.parametrize("workflow_id", ["ltx2_3_t2v", "ltx2_3_ic_lora_image"])
def test_workflows_without_a_start_frame_cannot_run_in_full_mode(workflow_id):
    assert video_workflow_problem("full", workflow_id)
    assert video_workflow_problem("i2v", workflow_id) is None


def test_start_frame_capable_workflows_are_fine_in_full_mode():
    assert video_workflow_problem("full", "ltx2_3_id_lora") is None
    assert video_workflow_problem("full", "nope")  # unknown id is reported too
    assert video_workflow_problem("image_only", "nope") is None


# --------------------------------------------------------------------------
# validation
# --------------------------------------------------------------------------

def test_validate_detects_dangling_link():
    wf = build_video_workflow(params(video_workflow="tx2_3_i2v"))
    wf["320:290"]["inputs"]["input"] = ["does_not_exist", 0]
    with pytest.raises(WorkflowError) as excinfo:
        validate_workflow(wf)
    assert "does_not_exist" in str(excinfo.value)


def test_validate_detects_deleted_node():
    wf = build_image_workflow(params())
    del wf["30:8"]  # still referenced by 29 (SaveImage)
    with pytest.raises(WorkflowError):
        validate_workflow(wf)


def test_validate_detects_missing_class_type():
    wf = build_image_workflow(params())
    wf["49"].pop("class_type")
    with pytest.raises(WorkflowError):
        validate_workflow(wf)


def test_build_raises_on_broken_template():
    broken = copy.deepcopy(load_template(KREA2_TURBO))
    del broken["49"]  # referenced by 30:5
    with pytest.raises(WorkflowError):
        build_image_workflow(params(), template=broken)


# --------------------------------------------------------------------------
# health check support (§10-3)
# --------------------------------------------------------------------------

def test_required_class_types_cover_every_built_workflow():
    types = all_required_class_types()
    assert {
        "ResolutionSelector",
        "LoraLoaderModelOnly",
        "SaveImage",
        "SaveVideo",
        "LTXVReferenceAudio",
        "MoGeInference",
        "LoadVideo",
    } <= types

    built = {node["class_type"] for node in build_image_workflow(params()).values()}
    for workflow_id in VIDEO_IDS:
        built |= {
            node["class_type"]
            for node in build_video_workflow(params(video_workflow=workflow_id)).values()
        }
    assert built <= types


# --------------------------------------------------------------------------
# model file overrides (§3.3)
# --------------------------------------------------------------------------

def test_model_fields_are_scoped_by_workflow():
    fields = {f.key: f for f in model_fields()}
    assert "krea2_turbo/30:10.unet_name" in fields
    assert "krea2_turbo/30:11.clip_name" in fields
    assert "krea2_turbo/30:12.vae_name" in fields
    # the same node id exists in several video templates -> the scope separates them
    assert "tx2_3_i2v/320:316.ckpt_name" in fields
    assert "ltx2_3_id_lora/340:346.lora_name" in fields
    assert "ltx2_3_ic_lora_motion/697:32.model_name" in fields

    unet = fields["krea2_turbo/30:10.unet_name"]
    assert (unet.workflow_id, unet.node_id, unet.field, unet.class_type) == (
        "krea2_turbo", "30:10", "unet_name", "UNETLoader",
    )
    assert unet.default == load_template(KREA2_TURBO)["30:10"]["inputs"]["unet_name"]
    assert unet.workflow_label and unet.title
    assert all(f.default for f in fields.values())
    assert all(f.key.startswith(f"{f.workflow_id}/") for f in fields.values())


def test_the_dynamic_lora_placeholders_are_not_configurable():
    keys = {f.key for f in model_fields()}
    for node_id in KREA2_TURBO.lora_chain.placeholders:
        assert f"krea2_turbo/{node_id}.lora_name" not in keys


def test_no_old_style_model_names_leak_in():
    """The templates must not bring back the pre-workflow/ model files."""
    defaults = " ".join(f.default for f in model_fields()).lower()
    assert "redcraft" not in defaults
    assert "sexgod" not in defaults
    assert "pinkcherry" not in defaults


def test_apply_model_overrides_ignores_empty_unknown_and_other_workflows():
    wf = {"a": {"class_type": "UNETLoader", "inputs": {"unet_name": "keep.safetensors"}}}
    apply_model_overrides(
        wf,
        {
            "wf/a.unet_name": "",          # empty
            "other/a.unet_name": "x",      # another workflow
            "wf/zz.unet_name": "x",        # unknown node
            "a.unet_name": "legacy",       # unscoped legacy key
            "bogus": "y",
        },
        "wf",
    )
    assert wf["a"]["inputs"]["unet_name"] == "keep.safetensors"
    apply_model_overrides(wf, None, "wf")
    assert wf["a"]["inputs"]["unet_name"] == "keep.safetensors"
    apply_model_overrides(wf, {"wf/a.unet_name": "mine.safetensors"}, "wf")
    assert wf["a"]["inputs"]["unet_name"] == "mine.safetensors"


def test_build_applies_scoped_overrides_only():
    overrides = {
        "krea2_turbo/30:10.unet_name": "custom-unet.safetensors",
        "tx2_3_i2v/320:316.ckpt_name": "custom-ckpt.safetensors",
        "ltx2_3_id_lora/340:317.ckpt_name": "other-ckpt.safetensors",
    }
    image = build_image_workflow(params(), overrides)
    assert image["30:10"]["inputs"]["unet_name"] == "custom-unet.safetensors"

    i2v = build_video_workflow(params(video_workflow="tx2_3_i2v"), overrides)
    assert i2v["320:316"]["inputs"]["ckpt_name"] == "custom-ckpt.safetensors"

    # the id_lora template shares node ids with ia2v but not the override scope
    id_lora = build_video_workflow(params(video_workflow="ltx2_3_id_lora"), overrides)
    assert id_lora["340:317"]["inputs"]["ckpt_name"] == "other-ckpt.safetensors"
    ia2v = build_video_workflow(params(video_workflow="tx2_3_ia2v"), overrides)
    assert ia2v["340:317"]["inputs"]["ckpt_name"] != "other-ckpt.safetensors"

    # the templates keep their own defaults
    assert load_template(KREA2_TURBO)["30:10"]["inputs"]["unet_name"] != (
        "custom-unet.safetensors"
    )


def test_dynamic_lora_chain_is_not_overridable():
    placeholder = KREA2_TURBO.lora_chain.placeholders[0]
    wf = build_image_workflow(
        params(), {f"krea2_turbo/{placeholder}.lora_name": "x.safetensors"}
    )
    assert placeholder not in wf
    assert wf[f"{LORA_NODE_PREFIX}0"]["inputs"]["lora_name"] == "kaori.safetensors"
