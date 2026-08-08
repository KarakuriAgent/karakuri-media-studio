"""Tests for the workflow injection engine (no ComfyUI required)."""

import copy
from dataclasses import replace

import pytest

from app.models import GenerationParams, LoraRef, missing_job_fields, video_workflow_problem
from app.workflow import (
    ASPECT_RATIOS,
    LORA_NODE_PREFIX,
    REF_AUDIO_NODE_PREFIX,
    REF_IMAGE_NODE_PREFIX,
    REF_VIDEO_NODE_PREFIX,
    REF_VIDEO_PARTS_NODE_PREFIX,
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
    model_slots,
    parse_aspect_ratio,
    resolution,
    resolution_for_image,
    scoped_model_overrides,
    selectable_model_slots,
    validate_manifests,
    validate_workflow,
    video_resolution,
)
from app.workflows import (
    ANIMA,
    DEFAULT_IMAGE_WORKFLOW,
    DEFAULT_VIDEO_WORKFLOW,
    GENERATED_AUDIO,
    INPUT_FIELDS,
    MINIMAX_H3_LOW_VRAM_NAME,
    OPTIONAL_CLASS_TYPES,
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
        # テンプレートを持たないワークフローは除外する（SPEC §5.2）
        if spec.backend != "comfyui":
            continue
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


#: LoRA チェーンを持つ動画ワークフロー。Wan 系は差せる場所が無いので対象外
#: （動画 LoRA を指定したジョブは 422、フォームは欄ごと出さない）。
LORA_VIDEO_IDS = [
    workflow_id
    for workflow_id in VIDEO_IDS
    if get_spec(workflow_id, "video").lora_chain is not None
]


def test_the_ltx_workflows_declare_a_lora_chain():
    assert LORA_VIDEO_IDS == [
        workflow_id
        for workflow_id in VIDEO_IDS
        if get_spec(workflow_id, "video").family == "ltx2.3"
    ]


@pytest.mark.parametrize("workflow_id", LORA_VIDEO_IDS)
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


@pytest.mark.parametrize("workflow_id", LORA_VIDEO_IDS)
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
    # CFG を使わないモデル（MiniMax H3）には negative そのものが無い
    if spec.supports("negative"):
        assert value(wf, spec, "negative") == "NEGATIVE"
    assert value(wf, spec, "save_prefix") == "video/01JOBID"
    # 1.5 MP @ 16:9, rounded to the grid the workflow's latents need
    assert (value(wf, spec, "width"), value(wf, spec, "height")) == resolution(
        "16:9 (Widescreen)", 1.5, multiple=spec.resolution_multiple
    )
    # fps / 秒数を持たないワークフローもある
    if spec.supports("fps"):
        assert value(wf, spec, "fps") == 25
    if spec.supports("duration"):
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
    if not spec.supports("negative"):
        pytest.skip(f"{workflow_id} has no negative prompt")
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


# --- 参照素材の動的展開（RefMediaFan、SPEC §3.1）----------------------------

REF_SPEC_ID = "minimax_h3_r2v"


def _ref_wf(images: int = 0, videos: int = 0, audios: int = 0):
    spec = get_spec(REF_SPEC_ID)
    wf = build_video_workflow(
        params(
            video_workflow=REF_SPEC_ID,
            reference_image_names=[f"ref{index}.png" for index in range(images)],
            reference_video_names=[f"clip{index}.mp4" for index in range(videos)],
            reference_audio_names=[f"track{index}.wav" for index in range(audios)],
        )
    )
    validate_workflow(wf)
    return spec, wf


@pytest.mark.parametrize("count", [0, 1, 2, 3, 9])
def test_reference_images_grow_one_loader_each(count):
    spec, wf = _ref_wf(images=count)
    fan = spec.ref_media
    loaders = sorted(key for key in wf if key.startswith(REF_IMAGE_NODE_PREFIX))
    assert loaders == [f"{REF_IMAGE_NODE_PREFIX}{i}" for i in range(count)]
    # 渡した順に ref_image_0, _1, … へ繋がる（プロンプトの <Picture N> の順）
    inputs = wf[fan.node.node_id]["inputs"]
    for index in range(count):
        node_id = f"{REF_IMAGE_NODE_PREFIX}{index}"
        assert wf[node_id]["class_type"] == "LoadImage"
        assert wf[node_id]["inputs"]["image"] == f"ref{index}.png"
        assert inputs[f"{fan.image_prefix}{index}"] == [node_id, 0]
    # 未指定ぶんの入力は残らない（雛形のファイル名で失敗しないように）
    assert len([k for k in inputs if k.startswith(fan.image_prefix)]) == count
    # テンプレートの雛形ノードは種類を問わず必ず消える
    for loader in fan.loaders():
        assert loader.node_id not in wf


@pytest.mark.parametrize("count", [0, 1, 3])
def test_reference_videos_carry_their_own_soundtrack(count):
    """参照動画は LoadVideo -> GetVideoComponents で映像と音声に割れ、同じ番号の
    ref_video_N / ref_video_audio_N の両方に繋がる（ノード側のペアリング）。"""
    spec, wf = _ref_wf(videos=count)
    fan = spec.ref_media
    inputs = wf[fan.node.node_id]["inputs"]
    for index in range(count):
        loader_id = f"{REF_VIDEO_NODE_PREFIX}{index}"
        parts_id = f"{REF_VIDEO_PARTS_NODE_PREFIX}{index}"
        assert wf[loader_id]["class_type"] == "LoadVideo"
        assert wf[loader_id]["inputs"]["file"] == f"clip{index}.mp4"
        assert wf[parts_id]["class_type"] == "GetVideoComponents"
        assert wf[parts_id]["inputs"]["video"] == [loader_id, 0]
        assert inputs[f"{fan.video_prefix}{index}"] == [parts_id, 0]
        assert inputs[f"{fan.video_audio_prefix}{index}"] == [parts_id, 1]
    assert len([k for k in inputs if k.startswith(fan.video_prefix)]) == count
    assert len([k for k in inputs if k.startswith(fan.video_audio_prefix)]) == count


@pytest.mark.parametrize("count", [0, 1, 3])
def test_reference_audios_grow_one_load_audio_each(count):
    spec, wf = _ref_wf(audios=count)
    fan = spec.ref_media
    inputs = wf[fan.node.node_id]["inputs"]
    for index in range(count):
        node_id = f"{REF_AUDIO_NODE_PREFIX}{index}"
        assert wf[node_id]["class_type"] == "LoadAudio"
        assert wf[node_id]["inputs"]["audio"] == f"track{index}.wav"
        assert inputs[f"{fan.audio_prefix}{index}"] == [node_id, 0]
    assert len([k for k in inputs if k.startswith(fan.audio_prefix)]) == count


def test_every_reference_kind_can_be_mixed():
    """3 種類を混ぜても番号は種類ごとに 0 から振り直される（タグの通し番号）。"""
    spec, wf = _ref_wf(images=2, videos=2, audios=1)
    fan = spec.ref_media
    inputs = wf[fan.node.node_id]["inputs"]
    assert [k for k in inputs if k.startswith(fan.image_prefix)] == [
        f"{fan.image_prefix}0", f"{fan.image_prefix}1"
    ]
    assert [k for k in inputs if k.startswith(fan.video_prefix)] == [
        f"{fan.video_prefix}0", f"{fan.video_prefix}1"
    ]
    assert [k for k in inputs if k.startswith(fan.video_audio_prefix)] == [
        f"{fan.video_audio_prefix}0", f"{fan.video_audio_prefix}1"
    ]
    assert [k for k in inputs if k.startswith(fan.audio_prefix)] == [
        f"{fan.audio_prefix}0"
    ]


def test_one_reference_image_matches_the_single_input_case():
    """1 枚のときはテンプレートと同じ形（ref_image_0 に 1 本だけ）。"""
    spec, wf = _ref_wf(images=1)
    template = load_template(spec)
    fan = spec.ref_media
    prefix = fan.image_prefix
    before = [k for k in template[fan.node.node_id]["inputs"] if k.startswith(prefix)]
    after = [k for k in wf[fan.node.node_id]["inputs"] if k.startswith(prefix)]
    assert before == after == [f"{prefix}0"]


def test_surplus_reference_material_is_dropped():
    """宣言した上限を超えたぶんは繋がない（投入前に 422 になるので最後の砦）。"""
    spec, wf = _ref_wf(images=12, videos=5, audios=5)
    assert spec.multi_inputs == {
        "reference_images": 9,
        "reference_videos": 3,
        "reference_audios": 3,
    }
    for prefix, name in (
        (REF_IMAGE_NODE_PREFIX, "reference_images"),
        (REF_VIDEO_NODE_PREFIX, "reference_videos"),
        (REF_AUDIO_NODE_PREFIX, "reference_audios"),
    ):
        grown = [key for key in wf if key.startswith(prefix)]
        # 参照動画のノード id は LoadVideo と GetVideoComponents で接頭辞が
        # 重なるので、デコーダぶんを除いて数える
        grown = [key for key in grown if not key.startswith(REF_VIDEO_PARTS_NODE_PREFIX)]
        assert len(grown) == spec.multi_inputs[name]


def test_only_the_declared_workflow_grows_reference_loaders():
    """宣言の無いワークフローに参照素材を渡してもグラフは変わらない。"""
    for workflow_id in VIDEO_IDS:
        if get_spec(workflow_id).ref_media is not None:
            continue
        wf = build_video_workflow(
            params(
                video_workflow=workflow_id,
                reference_image_names=["ref0.png"],
                reference_video_names=["clip0.mp4"],
                reference_audio_names=["track0.wav"],
            )
        )
        assert not [
            key
            for key in wf
            if key.startswith(
                (REF_IMAGE_NODE_PREFIX, REF_VIDEO_NODE_PREFIX, REF_AUDIO_NODE_PREFIX)
            )
        ]


# --- 任意入力の枝落とし（optional_loaders、SPEC §3.1）------------------------

def _minimax_i2v_wf(end_image: str = ""):
    spec = get_spec("minimax_h3_i2v")
    wf = build_video_workflow(
        params(
            video_workflow=spec.id,
            start_image_name="first.png",
            end_image_name=end_image,
        )
    )
    validate_workflow(wf)
    return spec, wf


def test_minimax_i2v_wires_the_end_image_when_it_is_given():
    spec, wf = _minimax_i2v_wf("last.png")
    loader = spec.inject["end_image"]
    assert wf[loader.node_id]["inputs"]["image"] == "last.png"
    assert wf["105:104"]["inputs"]["last_frame"] == [loader.node_id, 0]
    assert wf["105:104"]["inputs"]["first_frame"] == ["114", 0]


def test_minimax_i2v_drops_the_end_image_loader_when_it_is_not_given():
    """渡されなければ雛形ごと落ちる（テンプレートのファイル名を探しに行かない）。"""
    spec, wf = _minimax_i2v_wf()
    loader = spec.inject["end_image"]
    assert loader.node_id not in wf
    assert "last_frame" not in wf["105:104"]["inputs"]
    # 開始フレームのほうは必須なので残る
    assert wf["105:104"]["inputs"]["first_frame"] == ["114", 0]


def test_the_end_image_is_an_optional_input_of_minimax_i2v():
    spec = get_spec("minimax_h3_i2v")
    assert spec.supports("end_image")
    assert "end_image" not in spec.requires
    entry = catalog_entry(spec)
    assert ("end_image", "最後のフレーム画像") in entry.optional_inputs


# --- MiniMax H3 turbo（4 ステップ版、SPEC §3.1）-----------------------------

#: turbo テンプレート -> 素の対応版
TURBO_PAIRS = [
    ("minimax_h3_i2v_turbo", "minimax_h3_i2v"),
    ("minimax_h3_r2v_turbo", "minimax_h3_r2v"),
]

#: UNETLoader から BasicGuider までに直列で入っている高速化ノード
TURBO_CHAIN = [
    "MiniMaxH3TurboLoRA",
    "PathchSageAttentionKJ",
    "SolAttnPatch",
    "MiniMaxH3SigmaShift",
    "SpectrumApplyMiniMaxH3",
]


@pytest.mark.parametrize(("turbo_id", "plain_id"), TURBO_PAIRS)
def test_the_turbo_workflows_take_the_same_inputs_as_the_plain_ones(
    turbo_id, plain_id
):
    turbo = get_spec(turbo_id, "video")
    plain = get_spec(plain_id, "video")
    assert turbo.supported_names() == plain.supported_names()
    assert turbo.requires == plain.requires
    assert turbo.multi_inputs == plain.multi_inputs
    assert turbo.optional_loaders == plain.optional_loaders
    assert turbo.family == plain.family == "minimax-h3"
    assert turbo.frames == plain.frames
    assert "Turbo" in turbo.label and "Turbo" in turbo.mode_label


@pytest.mark.parametrize(("turbo_id", "plain_id"), TURBO_PAIRS)
def test_the_turbo_templates_chain_the_speedup_nodes_in_series(turbo_id, plain_id):
    """UNETLoader -> TurboLoRA -> Sage -> SolAttn -> SigmaShift -> Spectrum."""
    wf = build_video_workflow(params(video_workflow=turbo_id))
    validate_workflow(wf)
    by_class = {node["class_type"]: key for key, node in wf.items()}
    upstream = [by_class["UNETLoader"], 0]
    for class_type in TURBO_CHAIN:
        node_id = by_class[class_type]
        assert wf[node_id]["inputs"]["model"] == upstream, class_type
        upstream = [node_id, 0]
    # guider は末尾（Spectrum）、scheduler は SigmaShift の手前（Sol-Attn）
    assert wf[by_class["BasicGuider"]]["inputs"]["model"] == [
        by_class["SpectrumApplyMiniMaxH3"],
        0,
    ]
    assert wf[by_class["BasicScheduler"]]["inputs"]["model"] == [
        by_class["SolAttnPatch"],
        0,
    ]


@pytest.mark.parametrize(("turbo_id", "plain_id"), TURBO_PAIRS)
def test_the_turbo_templates_sample_in_four_steps(turbo_id, plain_id):
    wf = build_video_workflow(params(video_workflow=turbo_id))
    scheduler = next(
        node for node in wf.values() if node["class_type"] == "BasicScheduler"
    )
    assert scheduler["inputs"]["steps"] == 4
    assert scheduler["inputs"]["scheduler"] == "simple"
    assert scheduler["inputs"]["denoise"] == 1


@pytest.mark.parametrize(
    ("turbo_id", "unet"),
    [
        ("minimax_h3_i2v_turbo", "minimax_h3_fl2va_pruned_w4a8_mixed.safetensors"),
        ("minimax_h3_r2v_turbo", "minimax_h3_ref2va_pruned_w4a8_mixed.safetensors"),
    ],
)
def test_the_turbo_templates_load_the_quantised_weights(turbo_id, unet):
    wf = build_video_workflow(params(video_workflow=turbo_id))
    by_class = {}
    for key, node in wf.items():
        by_class.setdefault(node["class_type"], []).append(key)
    assert wf[by_class["UNETLoader"][0]]["inputs"]["unet_name"] == unet
    assert (
        wf[by_class["CLIPLoader"][0]]["inputs"]["clip_name"]
        == "qwen3vl_32b_heretic_minimax_h3_nvfp4.safetensors"
    )
    vaes = {wf[key]["inputs"]["vae_name"] for key in by_class["VAELoader"]}
    assert vaes == {
        "minimax_h3_video_vae_int8_convrot.safetensors",
        "minimax_h3_audio_vae_fp32.safetensors",
    }


def test_the_turbo_only_custom_nodes_are_not_required_by_the_health_check():
    """任意のカスタムノードなので、入れていない環境でも赤にしない（SPEC §3.1）。"""
    required = all_required_class_types()
    assert not (required & OPTIONAL_CLASS_TYPES)
    # テンプレート側には確かに載っている
    turbo = load_template("minimax_h3_r2v_turbo")
    assert OPTIONAL_CLASS_TYPES <= {node["class_type"] for node in turbo.values()}


def test_the_turbo_templates_have_plain_numeric_node_ids():
    for turbo_id, _ in TURBO_PAIRS:
        assert all(key.isdigit() for key in load_template(turbo_id))


def turbo_lora_inputs(wf: dict) -> dict:
    return next(
        node["inputs"]
        for node in wf.values()
        if node["class_type"] == "MiniMaxH3TurboLoRA"
    )


@pytest.mark.parametrize(("turbo_id", "plain_id"), TURBO_PAIRS)
def test_the_turbo_workflows_offer_the_low_vram_switch(turbo_id, plain_id):
    """turbo だけが `low_vram` を選択式で持つ（素の版はノードごと無い）。"""
    turbo = get_spec(turbo_id, "video")
    select = turbo.select(MINIMAX_H3_LOW_VRAM_NAME)
    assert select is not None
    assert select.choices == ("off", "on")
    assert select.fallback == "off"
    # ``CustomCombo`` ではないので番号を書く先は持たない
    assert select.index_field == ""
    assert get_spec(plain_id, "video").select(MINIMAX_H3_LOW_VRAM_NAME) is None


@pytest.mark.parametrize(("turbo_id", "_plain_id"), TURBO_PAIRS)
def test_low_vram_defaults_to_off(turbo_id, _plain_id):
    """未指定でもテンプレートの現状値（False）のまま。"""
    assert turbo_lora_inputs(load_template(turbo_id))["low_vram"] is False
    wf = build_video_workflow(params(video_workflow=turbo_id))
    assert turbo_lora_inputs(wf)["low_vram"] is False


@pytest.mark.parametrize(("turbo_id", "_plain_id"), TURBO_PAIRS)
@pytest.mark.parametrize(
    ("choice", "expected"), [("on", True), ("off", False), ("bogus", False)]
)
def test_low_vram_is_written_as_a_boolean(turbo_id, _plain_id, choice, expected):
    """選択式は文字列だが、BOOLEAN の入力には真偽値で入れる（SPEC §3.1）。"""
    wf = build_video_workflow(
        params(video_workflow=turbo_id, selects={MINIMAX_H3_LOW_VRAM_NAME: choice})
    )
    validate_workflow(wf)
    assert turbo_lora_inputs(wf)["low_vram"] is expected


def test_low_vram_survives_a_rerun():
    """ジョブの params に残るので、再実行でも同じ値が使われる。"""
    original = params(
        video_workflow="minimax_h3_i2v_turbo",
        selects={MINIMAX_H3_LOW_VRAM_NAME: "on"},
    )
    restored = GenerationParams(**original.model_dump())
    assert turbo_lora_inputs(build_video_workflow(restored))["low_vram"] is True


def test_low_vram_is_offered_to_the_agent_catalog():
    entry = catalog_entry(get_spec("minimax_h3_r2v_turbo", "video"))
    assert (MINIMAX_H3_LOW_VRAM_NAME, "off") in {
        (name, default) for name, _label, _choices, default, _auto, _hint in entry.selects
    }


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


@pytest.mark.parametrize(
    ("duration", "expected"),
    [
        (5, 124),     # 5s * 24fps = 120 -> 17*7 + 5
        (1, 39),      # 24 -> 17*2 + 5
        (0.1, 5),     # 2.4 -> the 5-frame floor of the node
        (15, 362),    # 360 -> 17*21 + 5 (the top of the trained range)
        (6, 158),
    ],
)
def test_minimax_h3_frame_count(duration, expected):
    """MiniMax H3 は 24fps 固定で 17k+5 に**切り上げ**（ジョブの fps は見ない）。"""
    grid = get_spec("minimax_h3_t2v").frames
    frames = grid.frames(duration, fps=25)
    assert frames == expected
    assert frames % 17 == 5 % 17
    assert frames >= duration * 24


@pytest.mark.parametrize("workflow_id", VIDEO_IDS)
def test_frame_expression_is_pinned(workflow_id):
    spec = get_spec(workflow_id)
    if not spec.supports("frames_expr"):
        pytest.skip(f"{workflow_id} pins no frame-count expression")
    node_id = spec.inject["frames_expr"].node_id
    template_inputs = load_template(spec)[node_id]["inputs"]
    wf = build_video_workflow(params(video_workflow=workflow_id, duration=10, fps=25))
    got = wf[node_id]["inputs"]
    # LTX は 8n+1 を 25fps で、MiniMax H3 は 17k+5 を 24fps 固定で
    assert got["expression"].endswith(str(spec.frames.frames(10, 25)))
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


# --- the video grid is per workflow (LTX latent = 32px, ref 0.5x = 64px) ----

def test_union_control_ic_lora_rounds_both_edges_to_64():
    """1920x1060 @1.0MP used to give 1376x760, which crashes the 0.5x ref encode."""
    spec = get_spec("ltx2_3_ic_lora_motion")
    width, height = video_resolution(
        spec,
        params(
            mode="i2v",
            video_workflow="ltx2_3_ic_lora_motion",
            megapixels=1.0,
            start_image_size=(1920, 1060),
        ),
    )
    assert width % 64 == 0 and height % 64 == 0


@pytest.mark.parametrize(
    "size", [(1920, 1060), (1920, 1080), (1000, 1500), (100, 1000), None]
)
def test_ltx_video_edges_follow_the_latent_grid(size):
    """Every LTX workflow lands on its own multiple, start frame or preset."""
    for spec in video_specs():
        if spec.family != "ltx2.3":
            continue
        width, height = video_resolution(
            spec,
            params(
                mode="i2v",
                video_workflow=spec.id,
                megapixels=1.0,
                start_image_size=size,
            ),
        )
        multiple = spec.resolution_multiple
        assert multiple % 32 == 0, spec.id
        assert width % multiple == 0 and height % multiple == 0, spec.id


def test_i2v_rounds_both_edges_to_32():
    spec = get_spec("tx2_3_i2v")
    assert spec.resolution_multiple == 32
    width, height = video_resolution(
        spec,
        params(
            mode="i2v",
            video_workflow="tx2_3_i2v",
            megapixels=1.0,
            start_image_size=(1920, 1060),
        ),
    )
    assert width % 32 == 0 and height % 32 == 0


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
    expected = resolution_for_image(
        1000, 1500, 1.0, multiple=spec.resolution_multiple
    )
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
    expected = resolution("16:9 (Widescreen)", 1.0, multiple=spec.resolution_multiple)
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
    expected = resolution("16:9 (Widescreen)", 1.0, multiple=spec.resolution_multiple)
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
    # turbo だけが使う任意のカスタムノードは意図的に外れている（SPEC §3.1）
    assert built - types == OPTIONAL_CLASS_TYPES


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


UNET_SLOT = "krea2_turbo/30:10.unet_name"


def test_model_slots_put_the_effective_default_in_front():
    slots = {slot.key: slot for slot in model_slots()}
    unet = slots[UNET_SLOT]
    # 何も設定していなければ既定値 = テンプレートの値、候補はそれ 1 件だけ
    assert unet.default == load_template(KREA2_TURBO)["30:10"]["inputs"]["unet_name"]
    assert unet.choices == [unet.default]
    assert unet.label

    # 設定の上書きが既定値になり、候補リストの先頭に来る（重複はまとめる）
    slots = {
        slot.key: slot
        for slot in model_slots(
            {UNET_SLOT: "mine.safetensors"},
            {UNET_SLOT: ["  ", "alt.safetensors", "mine.safetensors", "alt.safetensors"]},
        )
    }
    unet = slots[UNET_SLOT]
    assert unet.default == "mine.safetensors"
    assert unet.choices == ["mine.safetensors", "alt.safetensors"]


def test_selectable_model_slots_need_two_candidates():
    # 候補を 1 件だけ、しかも既定値と同じにしても「選ぶ意味」は無い
    default = load_template(KREA2_TURBO)["30:10"]["inputs"]["unet_name"]
    assert selectable_model_slots({}, {UNET_SLOT: [default]}) == []
    assert selectable_model_slots() == []

    selectable = selectable_model_slots({}, {UNET_SLOT: ["alt.safetensors"]})
    assert [slot.key for slot in selectable] == [UNET_SLOT]
    assert selectable[0].choices == [default, "alt.safetensors"]


def test_scoped_model_overrides_keeps_only_the_given_workflows():
    overrides = {
        UNET_SLOT: "a.safetensors",
        "tx2_3_i2v/320:316.ckpt_name": "b.safetensors",
        "ltx2_3_t2v/267:221.ckpt_name": "",  # 空値は落とす
    }
    assert scoped_model_overrides(overrides, ["tx2_3_i2v", "ltx2_3_t2v"]) == {
        "tx2_3_i2v/320:316.ckpt_name": "b.safetensors"
    }
    assert scoped_model_overrides(None, ["krea2_turbo"]) == {}


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


# --------------------------------------------------------------------------
# sampling steps (§3.1)
# --------------------------------------------------------------------------

#: `steps` を宣言しているワークフロー（宣言 = UI に欄が出る）
STEPS_IMAGE_IDS = [spec.id for spec in image_specs() if spec.supports("steps")]
STEPS_VIDEO_IDS = [spec.id for spec in video_specs() if spec.supports("steps")]


def test_the_steps_targets_are_the_samplers_of_their_template():
    """`steps` の注入先は必ずサンプラー側（KSampler / BasicScheduler）。"""
    declared = [spec for spec in SPECS if spec.supports("steps")]
    assert declared, "steps を宣言したワークフローが 1 つも無い"
    for spec in declared:
        target = spec.target("steps")
        assert target.field == "steps"
        assert target.class_type in ("KSampler", "BasicScheduler"), spec.id


@pytest.mark.parametrize("workflow_id", STEPS_IMAGE_IDS)
def test_steps_are_left_at_the_template_default_when_unset(workflow_id):
    spec = get_spec(workflow_id, "image")
    target = spec.target("steps")
    default = load_template(spec)[target.node_id]["inputs"]["steps"]
    wf = build_image_workflow(params(image_workflow=workflow_id, steps=0))
    assert wf[target.node_id]["inputs"]["steps"] == default


@pytest.mark.parametrize("workflow_id", STEPS_IMAGE_IDS)
def test_steps_are_injected_into_the_image_sampler(workflow_id):
    spec = get_spec(workflow_id, "image")
    wf = build_image_workflow(params(image_workflow=workflow_id, steps=12))
    assert wf[spec.target("steps").node_id]["inputs"]["steps"] == 12


@pytest.mark.parametrize("workflow_id", STEPS_VIDEO_IDS)
def test_steps_are_injected_into_the_video_sampler(workflow_id):
    spec = get_spec(workflow_id, "video")
    target = spec.target("steps")
    default = load_template(spec)[target.node_id]["inputs"]["steps"]
    unset = build_video_workflow(params(video_workflow=workflow_id, steps=0))
    assert unset[target.node_id]["inputs"]["steps"] == default
    wf = build_video_workflow(params(video_workflow=workflow_id, steps=6))
    assert wf[target.node_id]["inputs"]["steps"] == 6


def test_steps_are_an_int_even_when_given_as_a_float():
    """KSampler / BasicScheduler の steps は INT。float を入れると全体が落ちる。"""
    wf = build_image_workflow(params(steps=12.0))
    value = wf[KREA2_TURBO.target("steps").node_id]["inputs"]["steps"]
    assert isinstance(value, int) and value == 12


def test_a_workflow_without_a_steps_target_ignores_the_value():
    """宣言していないワークフロー（LTX の t2v）に steps を渡しても何も起きない。"""
    spec = get_spec("ltx2_3_t2v", "video")
    assert not spec.supports("steps")
    plain = build_video_workflow(params(video_workflow="ltx2_3_t2v", steps=0))
    with_steps = build_video_workflow(params(video_workflow="ltx2_3_t2v", steps=42))
    assert plain == with_steps


def test_the_minimax_turbo_lora_is_a_switchable_model_field():
    """4step 蒸留 LoRA も設定・実行時に差し替えられる（SPEC §3.3）。"""
    fields = {f.key: f for f in model_fields()}
    key = "minimax_h3_i2v_turbo/150.lora_name"
    assert key in fields
    assert fields[key].class_type == "MiniMaxH3TurboLoRA"
    assert fields[key].default.endswith(".safetensors")

    wf = build_video_workflow(
        params(video_workflow="minimax_h3_i2v_turbo"), {key: "other.safetensors"}
    )
    assert wf["150"]["inputs"]["lora_name"] == "other.safetensors"
