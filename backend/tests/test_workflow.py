"""Tests for the workflow injection engine (no ComfyUI required)."""

import copy
from dataclasses import replace

import pytest

from app import prompts
from app.models import (
    GenerationParams,
    LoraRef,
    context_latent_problem,
    missing_job_fields,
    reference_problem,
    select_problem,
    video_workflow_problem,
)
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
    image_megapixels,
    build_image_workflow,
    latent_upscale_choice,
    build_video_workflow,
    build_workflows,
    missing_triggers,
    model_fields,
    model_slots,
    parse_aspect_ratio,
    resolution,
    resolution_for_image,
    scoped_model_overrides,
    selectable_model_slots,
    supported_on_target,
    validate_manifests,
    validate_workflow,
    video_resolution,
)
from app.workflows import (
    ANIMA,
    DEFAULT_FRAME_GRID,
    DEFAULT_MEGAPIXELS,
    DEFAULT_IMAGE_WORKFLOW,
    DEFAULT_VIDEO_WORKFLOW,
    GENERATED_AUDIO,
    INPUT_FIELDS,
    LATENT_UPSCALE_NAME,
    LATENT_UPSCALER_CLASS,
    MINIMAX_H3_IMAGE_FIDELITY_NAME,
    MINIMAX_H3_IMAGE_FIT_NAME,
    MINIMAX_H3_IMAGE_QUALITY_CHOICES,
    MINIMAX_H3_IMAGE_QUALITY_LABELS,
    MINIMAX_H3_IMAGE_QUALITY_NAME,
    MINIMAX_H3_IMAGE_REF_DETAIL_NAME,
    MINIMAX_H3_IMAGE_STILL_NAME,
    MINIMAX_H3_IMAGE_STRATEGY_NAME,
    MINIMAX_H3_LOW_VRAM_NAME,
    OPTIONAL_CLASS_TYPES,
    SelectSpec,
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
    get_video_spec,
    load_template,
    validate_external_spec,
    validate_spec,
    validate_specs,
    specs_of_kind,
    video_catalog,
)

#: 動画ワークフローは**全件**（手動フォームに出さない ``studio_only`` の
#: ラテント保存版・連続カット版もテンプレートは組めなければならない）
VIDEO_IDS = [spec.id for spec in specs_of_kind("video")]
IMAGE_IDS = [spec.id for spec in image_specs()]
#: ComfyUI のテンプレートを持つ画像ワークフローだけ（外部バックエンドは除く）
COMFY_IMAGE_IDS = [
    spec.id for spec in image_specs() if spec.backend == "comfyui"
]


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
    for spec in specs_of_kind("video"):
        assert spec.prompt_hint.strip(), spec.id


def test_catalog_inputs_are_derived_from_the_manifest():
    for spec in specs_of_kind("video"):
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
    assert entries["minimax_h3_i2v"].required_inputs == (
        ("source_image", "開始フレーム"),
    )
    # 任意の最終フレームは「任意入力」の側に出る
    assert entries["minimax_h3_i2v"].optional_inputs == (
        ("end_image", "最後のフレーム画像"),
    )
    # 参照専用のワークフローは開始フレームを取らない
    assert entries["minimax_h3_r2v"].required_inputs == ()
    assert entries["minimax_h3_r2v"].reference_inputs


def test_catalog_explains_how_audio_is_used():
    # カタログに載るのは選択肢に出るものだけ（`studio_only` は除かれる）
    entries = video_catalog()
    assert entries
    # 音声入力を持たないワークフローはモデル生成音声だと明言する
    for entry in entries:
        assert entry.audio == GENERATED_AUDIO, entry.id


def test_an_undocumented_workflow_is_a_manifest_problem():
    """カタログはマニフェスト由来なので、説明なしの追加は健全性チェックで落ちる。"""
    spec = replace(get_spec("minimax_h3_t2v"), description="", prompt_hint="")
    problems = validate_spec(spec)
    assert any("description is empty" in p for p in problems)
    assert any("prompt_hint is empty" in p for p in problems)


def test_a_lora_chain_consumer_that_does_not_read_the_head_is_reported():
    """The chain is spliced into an existing edge, so the wiring is validated."""
    spec = KREA2_TURBO
    stray = replace(
        spec,
        lora_chain=replace(
            spec.lora_chain,
            consumers=(spec.lora_chain.consumers[0],),
            head="30:11",
            placeholders=(),
        ),
    )
    assert any("expected the chain head" in p for p in validate_spec(stray))

    missing = replace(spec, lora_chain=replace(spec.lora_chain, head="nope"))
    assert any("lora_chain.head" in p for p in validate_spec(missing))

    empty = replace(spec, lora_chain=replace(spec.lora_chain, consumers=()))
    assert any("no consumers" in p for p in validate_spec(empty))


def test_a_lora_chain_consumer_of_the_wrong_type_is_reported():
    spec = KREA2_TURBO
    retyped = replace(
        spec,
        lora_chain=replace(
            spec.lora_chain,
            consumers=(T("30:19", "model", "KSampler"),),
        ),
    )
    assert any("30:19" in p for p in validate_spec(retyped))


def test_an_audio_input_without_an_audio_role_is_reported():
    spec = get_spec("minimax_h3_i2v")
    # 音声入力を持つのに使い道を書いていないワークフローは健全性チェックで落ちる
    spec = replace(spec, audio_role="", inject={**spec.inject, "audio": spec.inject["image"]})
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
        problems = (
            validate_spec(spec)
            if spec.backend == "comfyui"
            else validate_external_spec(spec)
        )
        assert problems == [], spec.id


def test_image_families_are_one_per_folder():
    # LoRA 登録・プロンプトガイドの単位。グラフを持たない外部バックエンドの
    # ファミリー（grok-imagine）は LoRA を差せないので並ばない（SPEC §5.2）。
    assert image_families() == [
        "krea2",
        "anima",
        "z-image",
        "qwen-image",
        "minimax-h3-image",
    ]
    # 1 ファミリーに複数のワークフローがあってもよい（minimax-h3-image は
    # t2i / i2i / r2i × base / opt / turbo の 9 本）ので、重複を潰して比べる。
    comfy_families: list[str] = []
    for entry in image_catalog():
        if get_spec(entry.id, "image").backend != "comfyui":
            continue
        if entry.family not in comfy_families:
            comfy_families.append(entry.family)
    assert comfy_families == image_families()
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
    for workflow_id in COMFY_IMAGE_IDS:
        wf = build_image_workflow(params(image_workflow=workflow_id))
        spec = get_spec(workflow_id, "image")
        assert value(wf, spec, "prompt") == "IMAGE PROMPT"
        validate_workflow(wf)


def test_image_templates_are_not_mutated():
    for workflow_id in COMFY_IMAGE_IDS:
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


def test_no_video_workflow_declares_a_lora_chain_today():
    """今ある動画モデル（MiniMax H3）は LoRA を挿せる場所を持たない。

    宣言を持つワークフローが増えたらこの一覧が埋まり、以下のパラメトライズ済みの
    テストがそのまま効く。
    """
    assert LORA_VIDEO_IDS == []


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


def test_video_loras_do_not_leak_into_the_image_workflow():
    wf = build_image_workflow(
        params(loras=[], video_loras=[LoraRef(lora_name="v.safetensors")])
    )
    assert _video_lora_nodes(wf) == []


def test_image_loras_do_not_leak_into_the_video_workflow():
    wf = _video("minimax_h3_t2v", loras=[LoraRef(lora_name="i.safetensors")])
    assert [n for n in wf if n.startswith(LORA_NODE_PREFIX)] == []


# --- video trigger words (§3.4) --------------------------------------------

def _video_prompt(workflow_id: str = "minimax_h3_t2v", **overrides) -> str:
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
    # 解像度の注入先は「1 パス目」なので、素の挙動を見るためにラテント
    # アップスケールは切る（on のときの解像度は専用のテストで見る）
    spec = get_spec(workflow_id)
    wf = build_video_workflow(
        params(video_workflow=workflow_id, selects={LATENT_UPSCALE_NAME: "off"})
    )
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
    spec = get_spec("minimax_h3_i2v")
    wf = build_video_workflow(params(video_workflow=spec.id, video_seeds=[7]))
    assert [wf[t.node_id]["inputs"][t.field] for t in spec.seeds] == [7] * len(
        spec.seeds
    )


@pytest.mark.parametrize("workflow_id", VIDEO_IDS)
def test_empty_negative_keeps_the_template_default(workflow_id):
    spec = get_spec(workflow_id)
    if not spec.supports("negative"):
        pytest.skip(f"{workflow_id} has no negative prompt")
    template_value = value(load_template(spec), spec, "negative")
    wf = build_video_workflow(params(video_workflow=workflow_id, negative_prompt="  "))
    assert value(wf, spec, "negative") == template_value


@pytest.mark.parametrize("workflow_id", VIDEO_IDS)
def test_duration_nodes_keep_their_declared_type(workflow_id):
    """PrimitiveInt must not receive a float and PrimitiveFloat must stay float."""
    spec = get_spec(workflow_id)
    if not spec.supports("duration"):
        pytest.skip(f"{workflow_id} takes no duration")
    wf = build_video_workflow(params(video_workflow=workflow_id, duration=7.4))
    got = value(wf, spec, "duration")
    if spec.target("duration").class_type == "PrimitiveInt":
        assert isinstance(got, int) and got == 7
    else:
        assert got == pytest.approx(7.4)


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


# --- 参照素材の動的展開・画像ステージ（MiniMax H3 Image r2i、SPEC §3.1）------

IMAGE_REF_SPEC_IDS = ("minimax_h3_r2i", "minimax_h3_r2i_opt", "minimax_h3_r2i_turbo")


def _image_ref_wf(workflow_id: str, images: int):
    spec = get_spec(workflow_id, "image")
    wf = build_image_workflow(
        params(
            mode="image_only",
            image_workflow=workflow_id,
            reference_image_names=[f"ref{index}.png" for index in range(images)],
        )
    )
    validate_workflow(wf)
    return spec, wf


@pytest.mark.parametrize("workflow_id", IMAGE_REF_SPEC_IDS)
@pytest.mark.parametrize("count", [1, 2, 9])
def test_image_reference_images_grow_one_loader_each(workflow_id, count):
    """1 枚目は必須の source_image、2 枚目以降が reference_image_2 … に繋がる。"""
    spec, wf = _image_ref_wf(workflow_id, count)
    fan = spec.ref_media
    loaders = sorted(key for key in wf if key.startswith(REF_IMAGE_NODE_PREFIX))
    assert loaders == sorted(
        f"{REF_IMAGE_NODE_PREFIX}{i}" for i in range(count)
    )
    inputs = wf[fan.node.node_id]["inputs"]
    assert inputs["source_image"] == [f"{REF_IMAGE_NODE_PREFIX}0", 0]
    for index in range(1, count):
        node_id = f"{REF_IMAGE_NODE_PREFIX}{index}"
        assert wf[node_id]["class_type"] == "LoadImage"
        assert wf[node_id]["inputs"]["image"] == f"ref{index}.png"
        # ノードの入力名は <Picture N> と同じ番号（index + 1）
        assert inputs[f"reference_image_{index + 1}"] == [node_id, 0]
    numbered = [key for key in inputs if key.startswith(fan.image_prefix)]
    assert len(numbered) == count - 1
    # テンプレートの雛形ノードは消える（雛形のファイル名で失敗しないように）
    assert fan.image_loader.node_id not in wf


@pytest.mark.parametrize("workflow_id", IMAGE_REF_SPEC_IDS)
def test_image_reference_surplus_is_dropped(workflow_id):
    spec, wf = _image_ref_wf(workflow_id, 12)
    assert spec.multi_inputs == {"reference_images": 9}
    grown = [key for key in wf if key.startswith(REF_IMAGE_NODE_PREFIX)]
    assert len(grown) == 9


def test_image_workflows_without_a_declaration_ignore_reference_images():
    """宣言の無い画像ワークフローに参照画像を渡してもグラフは変わらない。"""
    for workflow_id in ("krea2_turbo", "minimax_h3_t2i", "minimax_h3_i2i"):
        wf = build_image_workflow(
            params(
                mode="image_only",
                image_workflow=workflow_id,
                reference_image_names=["ref0.png"],
            )
        )
        assert not [
            key for key in wf if key.startswith(REF_IMAGE_NODE_PREFIX)
        ]
        validate_workflow(wf)


# --- MiniMax H3 Image の選択式つまみ（SPEC §3.1）-----------------------------

H3_IMAGE_IDS = [
    f"minimax_h3_{mode}{suffix}"
    for mode in ("t2i", "i2i", "r2i")
    for suffix in ("", "_opt", "_turbo")
]


def _h3_image_wf(workflow_id: str, **selects):
    """選択式を渡して画像グラフを組む（r2i には最低 1 枚の参照画像が要る）。"""
    wf = build_image_workflow(
        params(
            mode="image_only",
            image_workflow=workflow_id,
            reference_image_names=["ref0.png"],
            selects=selects,
        )
    )
    validate_workflow(wf)
    return wf


@pytest.mark.parametrize("workflow_id", H3_IMAGE_IDS)
def test_every_h3_image_workflow_offers_the_frame_profile(workflow_id):
    """フレーム枚数は 3 モード × 3 バリアントすべてで選べる（品質のつまみ）。"""
    spec = get_spec(workflow_id, "image")
    select = spec.select(MINIMAX_H3_IMAGE_QUALITY_NAME)
    assert select is not None
    assert select.choices == MINIMAX_H3_IMAGE_QUALITY_CHOICES
    assert select.fallback == "recommended | 5 frames"
    # ``CustomCombo`` ではないので番号を書く先は持たない
    assert select.index_field == ""


@pytest.mark.parametrize("workflow_id", H3_IMAGE_IDS)
def test_the_frame_profile_defaults_to_five_frames(workflow_id):
    """未指定ならテンプレートの現状値（推奨の 5 フレーム）のまま。"""
    spec = get_spec(workflow_id, "image")
    target = spec.select(MINIMAX_H3_IMAGE_QUALITY_NAME).target
    default = load_template(spec)[target.node_id]["inputs"][target.field]
    assert default == "recommended | 5 frames"
    assert _h3_image_wf(workflow_id)["5"]["inputs"]["quality_profile"] == default


@pytest.mark.parametrize("workflow_id", H3_IMAGE_IDS)
@pytest.mark.parametrize("choice", MINIMAX_H3_IMAGE_QUALITY_CHOICES)
def test_the_frame_profile_is_injected_verbatim(workflow_id, choice):
    """枚数はノードの enum 文字列そのままで入る（デコード側は latent から読む）。"""
    wf = _h3_image_wf(workflow_id, quality_profile=choice)
    assert wf["5"]["inputs"]["quality_profile"] == choice
    # ``H3ImageDecode`` は枚数の入力を持たないので、注入点は 1 つで足りる
    assert set(wf["11"]["inputs"]) == {"samples", "vae"}


@pytest.mark.parametrize("workflow_id", H3_IMAGE_IDS)
def test_the_frame_strategy_is_injected_into_the_selector(workflow_id):
    wf = _h3_image_wf(workflow_id, frame_strategy="stable_quality")
    assert wf["12"]["inputs"]["strategy"] == "stable_quality"
    assert wf["12"]["class_type"] == "H3ImageFrameSelector"


@pytest.mark.parametrize("workflow_id", H3_IMAGE_IDS)
def test_only_the_editing_modes_offer_the_source_aware_strategies(workflow_id):
    """元画像との近さを見る選び方は、選択ノードに source_image が来るモードだけ。"""
    spec = get_spec(workflow_id, "image")
    choices = spec.select(MINIMAX_H3_IMAGE_STRATEGY_NAME).choices
    editing = "t2i" not in workflow_id
    for name in ("balanced_edit", "most_similar_to_source"):
        assert (name in choices) is editing, workflow_id
    # 番号のつまみを出していないので manual_index は載せない
    assert "manual_index" not in choices


@pytest.mark.parametrize("workflow_id", H3_IMAGE_IDS)
@pytest.mark.parametrize(
    ("choice", "expected"), [("on", True), ("off", False), ("bogus", True)]
)
def test_optimize_for_still_is_written_as_a_boolean(workflow_id, choice, expected):
    """選択式は文字列だが、BOOLEAN の入力には真偽値で入れる（SPEC §3.1）。

    リスト外の値は既定（`on`）に落ちるので `True` になる。
    """
    wf = _h3_image_wf(workflow_id, optimize_for_still=choice)
    assert wf["5"]["inputs"]["optimize_for_still"] is expected


@pytest.mark.parametrize("workflow_id", H3_IMAGE_IDS)
def test_only_the_editing_modes_offer_the_source_knobs(workflow_id):
    spec = get_spec(workflow_id, "image")
    editing = "t2i" not in workflow_id
    for name in (MINIMAX_H3_IMAGE_FIDELITY_NAME, MINIMAX_H3_IMAGE_FIT_NAME):
        assert (spec.select(name) is not None) is editing, workflow_id
    # 参照画像の解像度は r2i だけ（参照を複数取るのはこのモードだけ）
    reference = "r2i" in workflow_id
    assert (
        spec.select(MINIMAX_H3_IMAGE_REF_DETAIL_NAME) is not None
    ) is reference, workflow_id


@pytest.mark.parametrize("workflow_id", [i for i in H3_IMAGE_IDS if "t2i" not in i])
@pytest.mark.parametrize("choice", ["0.00", "0.50", "1.00"])
def test_source_fidelity_is_written_as_a_float(workflow_id, choice):
    """FLOAT の widget なので、選択式の文字列は実数に直してから入れる。"""
    wf = _h3_image_wf(workflow_id, source_fidelity=choice)
    got = wf["5"]["inputs"]["source_fidelity"]
    assert isinstance(got, float) and got == pytest.approx(float(choice))


@pytest.mark.parametrize("workflow_id", [i for i in H3_IMAGE_IDS if "t2i" not in i])
def test_source_fit_and_reference_detail_are_injected(workflow_id):
    wf = _h3_image_wf(workflow_id, source_fit="contain_pad")
    assert wf["5"]["inputs"]["source_fit"] == "contain_pad"
    if "r2i" in workflow_id:
        wf = _h3_image_wf(workflow_id, reference_detail="max_identity_2048")
        assert wf["5"]["inputs"]["reference_detail"] == "max_identity_2048"


def test_the_h3_image_selects_survive_a_rerun():
    """ジョブの params に残るので、再実行でも同じ値が使われる。"""
    original = params(
        mode="image_only",
        image_workflow="minimax_h3_i2i_turbo",
        selects={
            MINIMAX_H3_IMAGE_QUALITY_NAME: "maximum quality | 20 frames (slow)",
            MINIMAX_H3_IMAGE_FIDELITY_NAME: "0.90",
        },
    )
    restored = GenerationParams(**original.model_dump())
    inputs = build_image_workflow(restored)["5"]["inputs"]
    assert inputs["quality_profile"] == "maximum quality | 20 frames (slow)"
    assert inputs["source_fidelity"] == pytest.approx(0.9)


def test_the_h3_image_selects_reach_the_agent_catalog():
    names = {
        name: default
        for name, _l, _c, default, _a, _h, _labels in catalog_entry(
            get_spec("minimax_h3_r2i", "image")
        ).selects
    }
    assert names == {
        MINIMAX_H3_IMAGE_QUALITY_NAME: "recommended | 5 frames",
        MINIMAX_H3_IMAGE_STRATEGY_NAME: "decode_recommended",
        MINIMAX_H3_IMAGE_STILL_NAME: "on",
        MINIMAX_H3_IMAGE_FIDELITY_NAME: "0.75",
        MINIMAX_H3_IMAGE_FIT_NAME: "crop_center",
        MINIMAX_H3_IMAGE_REF_DETAIL_NAME: "match_generation_area",
    }
    # every select carries a hint and a Japanese label for the form
    for _name, label, choices, _default, _auto, hint, _labels in catalog_entry(
        get_spec("minimax_h3_r2i", "image")
    ).selects:
        assert label.strip() and hint.strip() and choices


@pytest.mark.parametrize("workflow_id", H3_IMAGE_IDS)
def test_the_h3_image_selects_are_accepted_by_the_job_validator(workflow_id):
    """フォーム / API / エージェントが通る検証も同じ宣言を見る（SPEC §3.1）。"""
    spec = get_spec(workflow_id, "image")
    picked = {name: select.choices[-1] for name, select in spec.selects.items()}
    assert select_problem("image_only", None, picked, image_workflow=workflow_id) is None
    problem = select_problem(
        "image_only", None, {"nope": "x"}, image_workflow=workflow_id
    )
    assert problem and "nope" in problem
    bad = select_problem(
        "image_only",
        None,
        {MINIMAX_H3_IMAGE_QUALITY_NAME: "42 frames"},
        image_workflow=workflow_id,
    )
    assert bad and "42 frames" in bad


# --- 選択肢の日本語ラベル（表示だけ、SPEC §3.1）------------------------------

@pytest.mark.parametrize("workflow_id", H3_IMAGE_IDS)
def test_every_h3_image_choice_has_a_japanese_label(workflow_id):
    """つまみの選択肢はどれも画面用の日本語を持つ（送る値は enum のまま）。"""
    spec = get_spec(workflow_id, "image")
    for name, select in spec.selects.items():
        for choice in select.choices:
            label = select.label_of(choice)
            assert label, f"{workflow_id}.{name}: {choice}"
            # 数字の選択肢（保持強度の 0.50 など）はそのままでも読めるので、
            # 「生の値と違うこと」を要求するのは英語の enum だけ
            if name != MINIMAX_H3_IMAGE_FIDELITY_NAME:
                assert label != choice, f"{workflow_id}.{name}: {choice}"
        # ラベルは飾りなので、選択肢そのものは生の enum のまま
        assert set(select.choice_labels) <= set(select.choices)


def test_the_frame_strategy_labels_are_readable_japanese():
    spec = get_spec("minimax_h3_r2i", "image")
    select = spec.select(MINIMAX_H3_IMAGE_STRATEGY_NAME)
    assert select.label_of("decode_recommended") == "おまかせ（推奨フレーム）"
    assert select.label_of("most_similar_to_source") == "元画像に最も近い"
    # t2i には無い選び方のラベルは、その spec の宣言にも入らない
    t2i = get_spec("minimax_h3_t2i", "image").select(MINIMAX_H3_IMAGE_STRATEGY_NAME)
    assert "most_similar_to_source" not in t2i.choice_labels


def test_the_frame_profile_labels_name_the_frame_count():
    select = get_spec("minimax_h3_t2i", "image").select(
        MINIMAX_H3_IMAGE_QUALITY_NAME
    )
    assert select.choice_labels == MINIMAX_H3_IMAGE_QUALITY_LABELS
    assert select.label_of("recommended | 5 frames") == "標準（5 フレーム）"
    assert "20 フレーム" in select.label_of("maximum quality | 20 frames (slow)")


def test_a_select_without_labels_falls_back_to_the_raw_value():
    """宣言は任意: 無ければ生の値をそのまま出す（既存の選択式を壊さない）。"""
    plain = SelectSpec(label="何か", choices=("a", "b"))
    assert plain.choice_labels == {}
    assert plain.label_of("a") == "a" and plain.label_of("b") == "b"
    # 一部だけ宣言しても、残りは生の値のまま
    partial = SelectSpec(label="何か", choices=("a", "b"), choice_labels={"a": "あ"})
    assert partial.label_of("a") == "あ"
    assert partial.label_of("b") == "b"
    # 知らない値を訊かれても落ちない（フォームの持ち越しの値など）
    assert partial.label_of("zzz") == "zzz"


def test_a_mistyped_label_key_is_a_manifest_error():
    """ラベルはフォールバックがあるので、打ち間違えても黙って生の値が出るだけ。
    気づけるようにマニフェスト検証で弾く。"""
    spec = get_spec("minimax_h3_t2i", "image")
    select = spec.select(MINIMAX_H3_IMAGE_QUALITY_NAME)
    broken = replace(
        spec,
        selects={
            **spec.selects,
            MINIMAX_H3_IMAGE_QUALITY_NAME: replace(
                select, choice_labels={"7 frames": "ななまい"}
            ),
        },
    )
    problems = validate_spec(broken)
    assert any("choice_labels" in problem for problem in problems), problems


def test_the_labels_do_not_change_what_is_injected():
    """ラベルは表示だけ: グラフに入るのは選んだ生の値のまま。"""
    wf = _h3_image_wf("minimax_h3_r2i", frame_strategy="most_similar_to_source")
    assert wf["12"]["inputs"]["strategy"] == "most_similar_to_source"


def test_the_agent_catalog_shows_the_value_and_the_label():
    """エージェントが書くのは生の値。日本語は手がかりとして併記されるだけ。"""
    from app.prompts import image_workflow_catalog_section

    section = image_workflow_catalog_section()
    line = next(
        line
        for line in section.splitlines()
        if line.strip().startswith(f"- `{MINIMAX_H3_IMAGE_STRATEGY_NAME}`")
    )
    assert "`decode_recommended`（おまかせ（推奨フレーム））" in line
    # 生の値だけでも読めること（バッククォート内は enum のまま）
    for value in ("stable_quality", "best_quality", "sharpest"):
        assert f"`{value}`" in line


def test_the_options_payload_carries_the_labels():
    """`GET /api/options` の選択式にラベルが乗る（フロントが描く元）。"""
    from app.routers.options import _workflow_option

    option = _workflow_option(get_spec("minimax_h3_r2i", "image"))
    select = next(
        item for item in option.selects if item.name == MINIMAX_H3_IMAGE_QUALITY_NAME
    )
    assert select.choices == list(MINIMAX_H3_IMAGE_QUALITY_CHOICES)
    assert select.choice_labels == MINIMAX_H3_IMAGE_QUALITY_LABELS
    # 選択肢に無い値がラベルに混ざっていないこと（マニフェスト検証と同じ約束）
    assert set(select.choice_labels) <= set(select.choices)


def test_the_h3_image_workflows_round_to_a_32_pixel_grid():
    """幅・高さは spec の resolution_multiple で丸める（H3 は 32、既定は 8）。"""
    wf = build_image_workflow(
        params(mode="image_only", image_workflow="minimax_h3_t2i", megapixels=0.98)
    )
    got = wf["5"]["inputs"]
    assert got["width"] % 32 == 0 and got["height"] % 32 == 0
    assert (got["width"], got["height"]) == (1344, 768)
    # 既定の 8 グリッドのワークフローは今までどおり
    z_image = build_image_workflow(
        params(mode="image_only", image_workflow="z_image_turbo", megapixels=0.98)
    )
    assert z_image["57:13"]["inputs"]["width"] % 8 == 0


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

#: ``MiniMaxH3TurboLoRA``（と `low_vram` の選択式）を持つ turbo。r2v turbo だけは
#: ``LoraLoaderModelOnly`` 2 段 + ラテントアップスケールの 2 パス構成なので外す
#: （そちらは下の r2v turbo 専用のテストで見る）。
TURBO_LORA_PAIRS = [
    ("minimax_h3_i2v_turbo", "minimax_h3_i2v"),
]

#: UNETLoader から BasicGuider までに直列で入っている高速化ノード
TURBO_CHAIN = [
    "MiniMaxH3TurboLoRA",
    "PathchSageAttentionKJ",
    "MiniMaxH3MemoryEfficientSageAttentionPatch",
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


@pytest.mark.parametrize(("turbo_id", "plain_id"), TURBO_LORA_PAIRS)
def test_the_turbo_templates_chain_the_speedup_nodes_in_series(turbo_id, plain_id):
    """UNETLoader -> TurboLoRA -> Sage -> MemEffSage -> SolAttn -> SigmaShift
    -> Spectrum."""
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
def test_the_turbo_workflows_share_the_prompt_guide_of_the_plain_ones(
    turbo_id, plain_id
):
    """プロンプトの書き方は素の版と同じなので、Chat に出すガイドも同じ。"""
    assert prompts.video_guide_for(turbo_id) == prompts.video_guide_for(plain_id) != ""
    assert turbo_id in prompts.MULTI_CUT_WORKFLOWS


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
        # r2v turbo は参照 LoRA を重ねるので、ウェイトは ref2va ではなく fl2va
        ("minimax_h3_r2v_turbo", "minimax_h3_fl2va_pruned_w4a8_mixed.safetensors"),
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


def test_the_optional_custom_nodes_are_not_required_by_the_health_check():
    """任意のカスタムノードなので、入れていない環境でも赤にしない（SPEC §3.1）。"""
    required = all_required_class_types()
    assert not (required & OPTIONAL_CLASS_TYPES)
    # 実際に組み上がるグラフには載っている（動画は turbo と連続カット、
    # 画像は t2i / i2i / r2i の 3 モードで全部そろう）。
    # ``MinimaxH3LatentUpscaler3D`` だけはテンプレートに書いておらず、
    # `latent_upscale` が on のときに組み替えで足される。
    used: set[str] = set()
    for workflow_id in (
        "minimax_h3_i2v_turbo",
        "minimax_h3_r2v_turbo",
        "minimax_h3_r2v_context",
        "minimax_h3_t2i",
        "minimax_h3_i2i",
        "minimax_h3_r2i",
    ):
        used |= {node["class_type"] for node in load_template(workflow_id).values()}
    assert LATENT_UPSCALER_CLASS not in used
    used |= {
        node["class_type"]
        for node in build_video_workflow(
            params(
                video_workflow="minimax_h3_r2v_turbo",
                selects={LATENT_UPSCALE_NAME: "on"},
            )
        ).values()
    }
    assert OPTIONAL_CLASS_TYPES <= used


def test_the_turbo_templates_have_plain_numeric_node_ids():
    for turbo_id, _ in TURBO_PAIRS:
        assert all(key.isdigit() for key in load_template(turbo_id))


def turbo_lora_inputs(wf: dict) -> dict:
    return next(
        node["inputs"]
        for node in wf.values()
        if node["class_type"] == "MiniMaxH3TurboLoRA"
    )


@pytest.mark.parametrize(("turbo_id", "plain_id"), TURBO_LORA_PAIRS)
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


@pytest.mark.parametrize(("turbo_id", "_plain_id"), TURBO_LORA_PAIRS)
def test_low_vram_defaults_to_off(turbo_id, _plain_id):
    """未指定でもテンプレートの現状値（False）のまま。"""
    assert turbo_lora_inputs(load_template(turbo_id))["low_vram"] is False
    wf = build_video_workflow(params(video_workflow=turbo_id))
    assert turbo_lora_inputs(wf)["low_vram"] is False


@pytest.mark.parametrize(("turbo_id", "_plain_id"), TURBO_LORA_PAIRS)
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
    entry = catalog_entry(get_spec("minimax_h3_i2v_turbo", "video"))
    assert (MINIMAX_H3_LOW_VRAM_NAME, "off") in {
        (name, default)
        for name, _l, _c, default, _a, _h, _labels in entry.selects
    }


# --- MiniMax H3 r2v の opt / turbo（fl2va + 参照 LoRA）----------------------

#: r2v の opt / turbo が使う 2 本の LoRA
REF_LORA = "minimax_h3_ref_lora_rank_256_bf16.safetensors"
TURBO_LORA = "minimax_h3_fl2v_turbo_4step_v1.1_768p_comfyui_bf16.safetensors"

#: opt テンプレートから r2v turbo を作るときの差分（これ以外は完全に同じ）
_R2V_TURBO_TWEAKS = {
    ("144", "model"): ["143", 0],
    ("124", "steps"): 4,
    ("123", "sampler_name"): "euler",
}

#: r2v の opt / turbo / save / context の組（テンプレートは全部 1 パス）
R2V_QUALITY_PAIRS = [
    ("minimax_h3_r2v_opt", "minimax_h3_r2v_turbo"),
    ("minimax_h3_r2v_save_opt", "minimax_h3_r2v_save_turbo"),
    ("minimax_h3_r2v_context_opt", "minimax_h3_r2v_context_turbo"),
]


@pytest.mark.parametrize(("opt_id", "turbo_id"), R2V_QUALITY_PAIRS)
def test_the_r2v_turbo_template_is_the_opt_one_plus_the_distilled_lora(
    opt_id, turbo_id
):
    """turbo との差はノード 143（蒸留 LoRA）と steps / sampler だけ。"""
    opt = load_template(opt_id)
    turbo = copy.deepcopy(load_template(turbo_id))
    assert set(turbo) - set(opt) == {"143"}
    assert turbo["143"]["class_type"] == "LoraLoaderModelOnly"
    assert turbo["143"]["inputs"] == {
        "lora_name": TURBO_LORA,
        "strength_model": 1.0,
        "model": ["127", 0],
    }
    del turbo["143"]
    for (node_id, field), value_ in _R2V_TURBO_TWEAKS.items():
        assert turbo[node_id]["inputs"][field] == value_
        turbo[node_id]["inputs"][field] = opt[node_id]["inputs"][field]
    assert opt == turbo


@pytest.mark.parametrize(
    "workflow_id",
    [pair[index] for pair in R2V_QUALITY_PAIRS for index in (0, 1)],
)
def test_the_r2v_quality_templates_load_fl2va_with_the_ref_lora(workflow_id):
    """ref2va の量子化ウェイトではなく fl2va + 参照 LoRA を使う。"""
    template = load_template(workflow_id)
    assert template["127"]["inputs"]["unet_name"] == (
        "minimax_h3_fl2va_pruned_w4a8_mixed.safetensors"
    )
    assert template["144"]["class_type"] == "LoraLoaderModelOnly"
    assert template["144"]["inputs"]["lora_name"] == REF_LORA
    assert template["144"]["inputs"]["strength_model"] == 1.0
    # 高速化チェーンの頭は必ず参照 LoRA
    assert template["151"]["inputs"]["model"] == ["144", 0]
    # 専用ローダー（MiniMaxH3TurboLoRA）はもう使わない
    assert "MiniMaxH3TurboLoRA" not in {
        node["class_type"] for node in template.values()
    }


@pytest.mark.parametrize(
    "workflow_id",
    [pair[index] for pair in R2V_QUALITY_PAIRS for index in (0, 1)],
)
def test_the_r2v_quality_templates_are_a_single_pass(workflow_id):
    """テンプレートにアップスケールは焼き込まない（`latent_upscale` の仕事）。"""
    template = load_template(workflow_id)
    classes = {node["class_type"] for node in template.values()}
    assert not (
        classes
        & {
            LATENT_UPSCALER_CLASS,
            "LTXVSeparateAVLatent",
            "LTXVConcatAVLatent",
            "ManualSigmas",
        }
    )
    samplers = [
        key for key, node in template.items()
        if node["class_type"] == "SamplerCustomAdvanced"
    ]
    assert samplers == ["125"]
    # デコーダは 1 パス目を直接読む
    assert template["122"]["inputs"]["samples"] == ["125", 0]
    assert template["121"]["inputs"]["samples"] == ["125", 0]


def test_the_r2v_turbo_stacks_the_two_loras_before_the_speedup_chain():
    """UNETLoader -> Turbo LoRA -> Ref LoRA -> Sage -> …（opt と同じ連鎖）。"""
    wf = build_video_workflow(
        params(
            video_workflow="minimax_h3_r2v_turbo",
            selects={LATENT_UPSCALE_NAME: "off"},
        )
    )
    validate_workflow(wf)
    assert wf["127"]["inputs"]["unet_name"] == (
        "minimax_h3_fl2va_pruned_w4a8_mixed.safetensors"
    )
    assert wf["143"]["inputs"]["lora_name"] == TURBO_LORA
    assert wf["143"]["inputs"]["model"] == ["127", 0]
    assert wf["144"]["inputs"]["lora_name"] == REF_LORA
    assert wf["144"]["inputs"]["model"] == ["143", 0]
    upstream = ["144", 0]
    for class_type in OPT_CHAIN:
        node_id = next(
            key for key, node in wf.items() if node["class_type"] == class_type
        )
        assert wf[node_id]["inputs"]["model"] == upstream, class_type
        upstream = [node_id, 0]
    assert "MiniMaxH3TurboLoRA" not in {
        node["class_type"] for node in wf.values()
    }


@pytest.mark.parametrize(("_opt_id", "turbo_id"), R2V_QUALITY_PAIRS)
def test_the_r2v_turbo_samples_in_four_euler_steps(_opt_id, turbo_id):
    template = load_template(turbo_id)
    assert template["124"]["inputs"]["steps"] == 4
    assert template["123"]["inputs"]["sampler_name"] == "euler"


def test_the_r2v_turbo_has_no_low_vram_switch():
    """``MiniMaxH3TurboLoRA`` を使わないので書き込む先が無い。"""
    for _opt_id, turbo_id in R2V_QUALITY_PAIRS:
        spec = get_spec(turbo_id, "video")
        assert spec.select(MINIMAX_H3_LOW_VRAM_NAME) is None


def test_the_r2v_turbo_takes_the_same_inputs_as_the_plain_r2v():
    turbo = get_spec("minimax_h3_r2v_turbo", "video")
    plain = get_spec("minimax_h3_r2v", "video")
    assert turbo.supported_names() == plain.supported_names()
    assert turbo.requires == plain.requires
    assert turbo.multi_inputs == plain.multi_inputs


# --- ラテントアップスケール（選択式 `latent_upscale`、SPEC §3.1）------------

#: 対象は minimax-h3 family の**動画**ワークフロー全部（画像は対象外）
MINIMAX_H3_VIDEO_IDS = [
    spec.id for spec in specs_of_kind("video") if spec.family == "minimax-h3"
]

#: 2 パス目の sigmas（全バリアント共通の固定値）
UPSCALE_SIGMAS = "0.9035, 0.6316, 0.3158, 0.0000"

#: 1 パス目を回す解像度（メガピクセル）
FIRST_PASS_MEGAPIXELS = 0.2


def upscale_params(workflow_id, choice="on", **kwargs):
    """r2v / context 版も含めて必要な入力がそろったジョブ。"""
    spec = get_spec(workflow_id, "video")
    extra = {}
    if spec.multi_inputs:
        extra["reference_image_names"] = ["ref0.png"]
    return params(
        video_workflow=workflow_id,
        selects={LATENT_UPSCALE_NAME: choice},
        **extra,
        **kwargs,
    )


@pytest.mark.parametrize("workflow_id", MINIMAX_H3_VIDEO_IDS)
def test_every_minimax_h3_video_workflow_offers_latent_upscale_on_by_default(
    workflow_id,
):
    spec = get_spec(workflow_id, "video")
    select = spec.select(LATENT_UPSCALE_NAME)
    assert select is not None
    assert select.choices == ("on", "off")
    assert select.fallback == "on"
    # 注入先ではなくグラフの組み替えで効く
    assert select.rewrites_graph and select.target is None
    assert spec.upscale is not None
    assert spec.upscale.first_pass_megapixels == FIRST_PASS_MEGAPIXELS
    assert spec.upscale.sigmas == UPSCALE_SIGMAS


def test_only_minimax_h3_video_workflows_declare_latent_upscale():
    declared = {spec.id for spec in SPECS if spec.select(LATENT_UPSCALE_NAME)}
    assert declared == set(MINIMAX_H3_VIDEO_IDS)


@pytest.mark.parametrize("workflow_id", MINIMAX_H3_VIDEO_IDS)
def test_latent_upscale_off_leaves_the_template_untouched(workflow_id):
    """off なら組み替えは起きず、on との差はアップスケールのぶんだけ。"""
    off = build_video_workflow(upscale_params(workflow_id, "off"))
    template_classes = {
        node["class_type"] for node in load_template(workflow_id).values()
    }
    assert {node["class_type"] for node in off.values()} <= template_classes | {
        "LoadImage",
        "LoadVideo",
        "LoadAudio",
        "GetVideoComponents",
    }
    assert LATENT_UPSCALER_CLASS not in {
        node["class_type"] for node in off.values()
    }
    # 未指定でも既定は on なので、off を選んだときだけこのグラフになる
    default = build_video_workflow(upscale_params(workflow_id, ""))
    assert default != off


@pytest.mark.parametrize("workflow_id", MINIMAX_H3_VIDEO_IDS)
def test_latent_upscale_on_runs_the_first_pass_at_a_fifth_of_a_megapixel(
    workflow_id,
):
    spec = get_spec(workflow_id, "video")
    job = upscale_params(workflow_id, "on")
    wf = build_video_workflow(job)
    validate_workflow(wf)
    first = resolution(
        job.aspect_ratio,
        FIRST_PASS_MEGAPIXELS,
        multiple=spec.resolution_multiple,
    )
    final = resolution(
        job.aspect_ratio, job.megapixels, multiple=spec.resolution_multiple
    )
    assert first != final
    # 注入先（MiniMaxH3ImageToVideo / ReferenceToVideo）は 1 パス目の解像度
    assert (value(wf, spec, "width"), value(wf, spec, "height")) == first
    # 最終解像度はアップスケーラが受け取る
    upscaler = wf[spec.upscale.upscaler_node]["inputs"]
    assert (upscaler["mode.width"], upscaler["mode.height"]) == final


@pytest.mark.parametrize("workflow_id", MINIMAX_H3_VIDEO_IDS)
def test_latent_upscale_on_wires_the_second_pass(workflow_id):
    """1 パス目 -> Separate -> Upscaler -> Concat -> 2 パス目 -> デコーダ。"""
    spec = get_spec(workflow_id, "video")
    up = spec.upscale
    wf = build_video_workflow(upscale_params(workflow_id, "on"))
    validate_workflow(wf)

    assert wf[up.separate_node]["class_type"] == "LTXVSeparateAVLatent"
    # 分けるのは 1 パス目の denoised_output（出力 1）
    assert wf[up.separate_node]["inputs"]["av_latent"] == [up.sampler, 1]

    upscaler = wf[up.upscaler_node]
    assert upscaler["class_type"] == LATENT_UPSCALER_CLASS
    assert upscaler["inputs"]["model_name"] == (
        "minimax_h3_latent_upscaler_3d_bf16.safetensors"
    )
    # megapixels モードではなく target dimensions（DynamicCombo の入力名）
    assert upscaler["inputs"]["mode"] == "target dimensions"
    assert "mode.megapixels" not in upscaler["inputs"]
    assert upscaler["inputs"]["align"] == 32
    assert upscaler["inputs"]["device"] == "cuda"
    assert upscaler["inputs"]["precision"] == "bf16"
    assert upscaler["inputs"]["latent"] == [up.separate_node, 0]

    concat = wf[up.concat_node]
    assert concat["class_type"] == "LTXVConcatAVLatent"
    assert concat["inputs"]["video_latent"] == [up.upscaler_node, 0]
    assert concat["inputs"]["audio_latent"] == [up.separate_node, 1]

    assert wf[up.sigmas_node]["class_type"] == "ManualSigmas"
    assert wf[up.sigmas_node]["inputs"]["sigmas"] == UPSCALE_SIGMAS

    # 2 パス目は 1 パス目と noise / guider / sampler を共有する
    first_inputs = wf[up.sampler]["inputs"]
    second = wf[up.second_sampler_node]
    assert second["class_type"] == "SamplerCustomAdvanced"
    assert second["inputs"] == {
        "sigmas": [up.sigmas_node, 0],
        "latent_image": [up.concat_node, 0],
        "noise": first_inputs["noise"],
        "guider": first_inputs["guider"],
        "sampler": first_inputs["sampler"],
    }

    # デコードするのは 2 パス目の出力だけ
    decoders = [
        key
        for key, node in wf.items()
        if node["class_type"] in ("VAEDecode", "VAEDecodeAudio")
    ]
    assert decoders
    for key in decoders:
        assert wf[key]["inputs"]["samples"] == [up.second_sampler_node, 0]


@pytest.mark.parametrize(
    "workflow_id",
    [wid for wid in MINIMAX_H3_VIDEO_IDS if "_save" in wid or "_context" in wid],
)
def test_latent_upscale_keeps_the_saved_latent_on_the_first_pass(workflow_id):
    """1 本目の保存と 1 個目の Motion Context は 1 パス目のまま。"""
    spec = get_spec(workflow_id, "video")
    up = spec.upscale
    wf = build_video_workflow(upscale_params(workflow_id, "on"))
    validate_workflow(wf)
    save = next(
        node
        for node in wf.values()
        if node["class_type"] == "MiniMaxH3MotionContextSaveLatent"
    )
    assert save["inputs"]["latent"] == [up.sampler, 0]
    trim = [
        node
        for node in wf.values()
        if node["class_type"] == "MiniMaxH3MotionContextTrim"
    ]
    if trim:
        # Trim はデコード**後**（= 2 パス目のあと）に掛かる
        decoders = {
            key
            for key, node in wf.items()
            if node["class_type"] in ("VAEDecode", "VAEDecodeAudio")
        }
        assert trim[0]["inputs"]["images"][0] in decoders
        assert trim[0]["inputs"]["audio"][0] in decoders


# --- 2 段引き継ぎ（ラテント連続性 × latent_upscale = on、SPEC §3.1）---------

#: ラテントを保存するバリアント（``*_save*`` / ``*_context*``）
LATENT_SAVE_IDS = [
    wid for wid in MINIMAX_H3_VIDEO_IDS if "_save" in wid or "_context" in wid
]
#: そのうち連続カット版（Motion Context を持つ）
LATENT_CONTEXT_IDS = [wid for wid in MINIMAX_H3_VIDEO_IDS if "_context" in wid]

#: 直前カットの 2 本（1 パス目 / 2 パス目）
PREV_LATENT = "/comfy/output/h3_context/prev_00002_.safetensors"
PREV_LATENT_HIRES = "/comfy/output/h3_context/prev_hires_00002_.safetensors"


def saves(wf: dict) -> list[dict]:
    return [
        node
        for node in wf.values()
        if node["class_type"] == "MiniMaxH3MotionContextSaveLatent"
    ]


@pytest.mark.parametrize("workflow_id", LATENT_SAVE_IDS)
def test_latent_upscale_saves_the_second_pass_latent_too(workflow_id):
    """on なら 1 パス目と 2 パス目のラテントを両方保存する（2 段引き継ぎ）。"""
    spec = get_spec(workflow_id, "video")
    up = spec.upscale
    wf = build_video_workflow(upscale_params(workflow_id, "on"))
    validate_workflow(wf)

    hires = wf[up.hires_save_node]
    assert hires["class_type"] == "MiniMaxH3MotionContextSaveLatent"
    # 保存するのは 2 パス目の出力で、保存先は 1 本目と区別できる名前
    assert hires["inputs"]["latent"] == [up.second_sampler_node, 0]
    assert hires["inputs"]["filename_prefix"] == "h3_context/01JOBID_hires"
    first_save = next(
        node for node in wf.values()
        if node["class_type"] == "MiniMaxH3MotionContextSaveLatent"
    )
    assert first_save["inputs"]["filename_prefix"] == "h3_context/01JOBID"
    # パスは 2 本目の PreviewAny 経由で持ち帰る
    preview = wf[up.hires_preview_node]
    assert preview["class_type"] == "PreviewAny"
    assert preview["inputs"]["source"] == [up.hires_save_node, 0]
    assert len(saves(wf)) == 2


@pytest.mark.parametrize("workflow_id", LATENT_SAVE_IDS)
def test_latent_upscale_off_saves_one_latent(workflow_id):
    """off は従来どおり 1 本だけ（2 本目のノードも足さない）。"""
    spec = get_spec(workflow_id, "video")
    wf = build_video_workflow(upscale_params(workflow_id, "off"))
    validate_workflow(wf)
    assert len(saves(wf)) == 1
    for node_id in spec.upscale.node_ids():
        assert node_id not in wf


@pytest.mark.parametrize("workflow_id", LATENT_CONTEXT_IDS)
def test_latent_upscale_wires_a_second_motion_context(workflow_id):
    """連続カット版 + on: 2 パス目は高解像度ラテントの Motion Context で回す。"""
    spec = get_spec(workflow_id, "video")
    up = spec.upscale
    wf = build_video_workflow(
        upscale_params(
            workflow_id,
            "on",
            reference_video_name="previous.mp4",
            context_latent_path=PREV_LATENT,
            context_latent_hires_path=PREV_LATENT_HIRES,
        )
    )
    validate_workflow(wf)

    # 1 個目（1 パス目）は 0.2MP のラテントを読んだまま
    first_context_id, first_context = next(
        (key, node)
        for key, node in wf.items()
        if node["class_type"] == "MiniMaxH3MotionContext"
    )
    first_load_id = spec.inject["context_latent"].node_id
    assert wf[first_load_id]["inputs"]["latent_path"] == PREV_LATENT
    assert first_context["inputs"]["context_latent"] == [first_load_id, 0]

    # 2 個目は高解像度のほうを読む
    load = wf[up.hires_load_node]
    assert load["class_type"] == "MiniMaxH3MotionContextLoadLatent"
    assert load["inputs"]["latent_path"] == PREV_LATENT_HIRES

    context = wf[up.hires_context_node]
    assert context["class_type"] == "MiniMaxH3MotionContext"
    # conditioning / vae / つまみは 1 個目と同じ
    assert context["inputs"]["conditioning"] == first_context["inputs"]["conditioning"]
    assert context["inputs"]["vae"] == first_context["inputs"]["vae"]
    assert context["inputs"]["context_length"] == "22"
    assert context["inputs"]["audio_context_length"] == 0
    # 形の参照先は 2 パス目のラテント（Concat の出力）、読むのは高解像度のほう
    assert context["inputs"]["latent"] == [up.concat_node, 0]
    assert context["inputs"]["context_latent"] == [up.hires_load_node, 0]

    # 2 個目の Guider はモデル連鎖を 1 個目と共有し、CONDITIONING だけ差し替え
    guider = wf[up.hires_guider_node]
    first_guider = next(
        node
        for node in wf.values()
        if node["class_type"] == "BasicGuider"
        and node["inputs"]["conditioning"] == [first_context_id, 0]
    )
    assert guider["class_type"] == "BasicGuider"
    assert guider["inputs"]["model"] == first_guider["inputs"]["model"]
    assert guider["inputs"]["conditioning"] == [up.hires_context_node, 0]

    # 2 パス目のサンプラーだけが 2 個目の Guider を使う（1 パス目は据え置き）
    assert wf[up.second_sampler_node]["inputs"]["guider"] == [up.hires_guider_node, 0]
    assert wf[up.sampler]["inputs"]["guider"] != [up.hires_guider_node, 0]

    # Trim は 1 個目の trim_frames のまま
    trim = next(
        node
        for node in wf.values()
        if node["class_type"] == "MiniMaxH3MotionContextTrim"
    )
    assert trim["inputs"]["trim_frames"] == [first_context_id, 1]


@pytest.mark.parametrize("workflow_id", LATENT_CONTEXT_IDS)
def test_latent_upscale_falls_back_to_one_stage_without_a_hires_latent(workflow_id):
    """前カットに 2 本目が無ければ 2 個目は組まず、1 段引き継ぎで通す。"""
    spec = get_spec(workflow_id, "video")
    up = spec.upscale
    wf = build_video_workflow(
        upscale_params(
            workflow_id,
            "on",
            reference_video_name="previous.mp4",
            context_latent_path=PREV_LATENT,
        )
    )
    validate_workflow(wf)
    for node_id in (up.hires_load_node, up.hires_context_node, up.hires_guider_node):
        assert node_id not in wf
    # 2 パス目は 1 パス目と同じ guider を共有したまま
    assert (
        wf[up.second_sampler_node]["inputs"]["guider"]
        == wf[up.sampler]["inputs"]["guider"]
    )
    # 2 パス目のラテントの保存だけは付く（次のカットが 2 段で続けられる）
    assert len(saves(wf)) == 2


def test_latent_upscale_adds_no_second_context_without_motion_context():
    """保存のみの版（``*_save``）は 2 個目の Motion Context を持たない。"""
    spec = get_spec("minimax_h3_r2v_save", "video")
    up = spec.upscale
    wf = build_video_workflow(
        upscale_params(
            "minimax_h3_r2v_save",
            "on",
            context_latent_hires_path=PREV_LATENT_HIRES,
        )
    )
    validate_workflow(wf)
    assert up.hires_save_node in wf
    for node_id in (up.hires_load_node, up.hires_context_node, up.hires_guider_node):
        assert node_id not in wf


def test_latent_upscale_adds_no_hires_save_without_a_save_node():
    """保存を持たない素の版は、2 段引き継ぎのノードを一切足さない。"""
    spec = get_spec("minimax_h3_t2v", "video")
    up = spec.upscale
    wf = build_video_workflow(
        upscale_params("minimax_h3_t2v", "on", context_latent_hires_path=PREV_LATENT_HIRES)
    )
    validate_workflow(wf)
    for node_id in (
        up.hires_save_node,
        up.hires_preview_node,
        up.hires_load_node,
        up.hires_context_node,
        up.hires_guider_node,
    ):
        assert node_id not in wf


def test_latent_upscale_is_pinned_off_on_comfy_cloud():
    """アップスケーラを入れられない接続先では on を選ばせない（SPEC §5.1）。"""
    select = get_spec("minimax_h3_t2v", "video").select(LATENT_UPSCALE_NAME)
    assert select.choices_for_target("comfy_cloud") == ("off",)
    assert select.fallback_for_target("comfy_cloud") == "off"
    for target in ("local", "runpod", ""):
        assert select.choices_for_target(target) == ("on", "off")
        assert select.fallback_for_target(target) == "on"
    # カタログ（エージェント向け）も同じ一覧になる
    entry = catalog_entry(get_spec("minimax_h3_t2v", "video"), "comfy_cloud")
    assert (LATENT_UPSCALE_NAME, ("off",), "off") in {
        (name, choices, default)
        for name, _l, choices, default, _a, _h, _labels in entry.selects
    }


def test_latent_upscale_choice_falls_back_to_the_default():
    spec = get_spec("minimax_h3_t2v", "video")
    assert latent_upscale_choice(spec, upscale_params("minimax_h3_t2v", "")) == "on"
    assert (
        latent_upscale_choice(spec, upscale_params("minimax_h3_t2v", "bogus")) == "on"
    )
    assert latent_upscale_choice(spec, upscale_params("minimax_h3_t2v", "off")) == "off"
    # 宣言を持たないワークフロー（画像側など）は常に空
    assert latent_upscale_choice(KREA2_TURBO, params()) == ""


def test_the_upscaler_model_file_is_a_settings_slot():
    """テンプレートに無いノードでも設定ページから差し替えられる（SPEC §3.3）。"""
    spec = get_spec("minimax_h3_r2v_turbo", "video")
    key = f"{spec.id}/{spec.upscale.upscaler_node}.model_name"
    fields = {f.key: f for f in model_fields()}
    assert key in fields
    assert fields[key].subfolder == "latent_upscale_models"
    assert fields[key].default == "minimax_h3_latent_upscaler_3d_bf16.safetensors"
    wf = build_video_workflow(
        upscale_params("minimax_h3_r2v_turbo", "on"),
        {key: "other_upscaler.safetensors"},
    )
    assert wf[spec.upscale.upscaler_node]["inputs"]["model_name"] == (
        "other_upscaler.safetensors"
    )


@pytest.mark.parametrize("turbo_id", ["minimax_h3_t2v_turbo", "minimax_h3_i2v_turbo"])
def test_the_t2v_i2v_turbo_keep_the_low_vram_switch_alongside_latent_upscale(turbo_id):
    """`latent_upscale` を足しても既存の選択式は残る。"""
    spec = get_spec(turbo_id, "video")
    assert set(spec.selects) == {MINIMAX_H3_LOW_VRAM_NAME, LATENT_UPSCALE_NAME}
    assert spec.select(MINIMAX_H3_LOW_VRAM_NAME).target.class_type == (
        "MiniMaxH3TurboLoRA"
    )


# --- MiniMax H3 opt（turbo から蒸留 LoRA を抜いた 20 ステップ版）------------

#: opt テンプレート -> 素の対応版 / 派生元の turbo
OPT_PAIRS = [
    ("minimax_h3_i2v_opt", "minimax_h3_i2v", "minimax_h3_i2v_turbo"),
    ("minimax_h3_r2v_opt", "minimax_h3_r2v", "minimax_h3_r2v_turbo"),
]

#: opt の高速化ノード（turbo から ``MiniMaxH3TurboLoRA`` を抜いたもの）
OPT_CHAIN = [class_type for class_type in TURBO_CHAIN if class_type != "MiniMaxH3TurboLoRA"]


@pytest.mark.parametrize(("opt_id", "plain_id", "_turbo_id"), OPT_PAIRS)
def test_the_opt_workflows_take_the_same_inputs_as_the_plain_ones(
    opt_id, plain_id, _turbo_id
):
    opt = get_spec(opt_id, "video")
    plain = get_spec(plain_id, "video")
    assert opt.supported_names() == plain.supported_names()
    assert opt.requires == plain.requires
    assert opt.multi_inputs == plain.multi_inputs
    assert opt.optional_loaders == plain.optional_loaders
    assert opt.family == plain.family == "minimax-h3"
    assert opt.frames == plain.frames
    assert "Optimized" in opt.label and "Optimized" in opt.mode_label


@pytest.mark.parametrize(("opt_id", "_plain_id", "_turbo_id"), OPT_PAIRS)
def test_the_opt_templates_drop_the_distilled_lora(opt_id, _plain_id, _turbo_id):
    """UNETLoader -> Sage -> MemEffSage -> SolAttn -> SigmaShift -> Spectrum。"""
    template = load_template(opt_id)
    assert "MiniMaxH3TurboLoRA" not in {
        node["class_type"] for node in template.values()
    }
    wf = build_video_workflow(params(video_workflow=opt_id))
    validate_workflow(wf)
    by_class = {node["class_type"]: key for key, node in wf.items()}
    upstream = [by_class["UNETLoader"], 0]
    # r2v の opt は fl2va + 参照 LoRA なので、連鎖の頭に LoRA が 1 段入る
    if "LoraLoaderModelOnly" in by_class:
        ref_lora = by_class["LoraLoaderModelOnly"]
        assert wf[ref_lora]["inputs"]["lora_name"] == REF_LORA
        assert wf[ref_lora]["inputs"]["model"] == upstream
        upstream = [ref_lora, 0]
    for class_type in OPT_CHAIN:
        node_id = by_class[class_type]
        assert wf[node_id]["inputs"]["model"] == upstream, class_type
        upstream = [node_id, 0]
    # turbo と同じく guider は末尾、scheduler は SigmaShift の手前
    assert wf[by_class["BasicGuider"]]["inputs"]["model"] == [
        by_class["SpectrumApplyMiniMaxH3"],
        0,
    ]
    assert wf[by_class["BasicScheduler"]]["inputs"]["model"] == [
        by_class["SolAttnPatch"],
        0,
    ]


@pytest.mark.parametrize(("opt_id", "_plain_id", "_turbo_id"), OPT_PAIRS)
def test_the_opt_templates_sample_in_twenty_steps(opt_id, _plain_id, _turbo_id):
    """蒸留 LoRA が無いので、素の版と同じ 20 ステップ。"""
    wf = build_video_workflow(params(video_workflow=opt_id))
    scheduler = next(
        node for node in wf.values() if node["class_type"] == "BasicScheduler"
    )
    assert scheduler["inputs"]["steps"] == 20
    assert scheduler["inputs"]["scheduler"] == "simple"
    assert scheduler["inputs"]["denoise"] == 1


@pytest.mark.parametrize(
    ("opt_id", "unet"),
    [
        ("minimax_h3_i2v_opt", "minimax_h3_fl2va_pruned_w4a8_mixed.safetensors"),
        # r2v の opt も参照 LoRA を重ねるので、ウェイトは ref2va ではなく fl2va
        ("minimax_h3_r2v_opt", "minimax_h3_fl2va_pruned_w4a8_mixed.safetensors"),
    ],
)
def test_the_opt_templates_keep_the_quantised_weights(opt_id, unet):
    wf = build_video_workflow(params(video_workflow=opt_id))
    by_class: dict[str, list[str]] = {}
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


@pytest.mark.parametrize(("opt_id", "_plain_id", "_turbo_id"), OPT_PAIRS)
def test_the_opt_workflows_have_no_low_vram_switch(opt_id, _plain_id, _turbo_id):
    """書き込む先の ``MiniMaxH3TurboLoRA`` がテンプレートに無いので持たない。"""
    assert get_spec(opt_id, "video").select(MINIMAX_H3_LOW_VRAM_NAME) is None


@pytest.mark.parametrize(("opt_id", "plain_id", "_turbo_id"), OPT_PAIRS)
def test_the_opt_workflows_share_the_prompt_guide_of_the_plain_ones(
    opt_id, plain_id, _turbo_id
):
    assert prompts.video_guide_for(opt_id) == prompts.video_guide_for(plain_id) != ""
    assert opt_id in prompts.MULTI_CUT_WORKFLOWS


#: opt が「turbo から蒸留 LoRA を抜いただけ」になっている組。r2v は turbo 側が
#: 2 パス構成に変わったので、逆に opt を土台にした差分を別テストで見る。
OPT_TURBO_DIFF_PAIRS = [
    ("minimax_h3_i2v_opt", "minimax_h3_i2v", "minimax_h3_i2v_turbo"),
]


@pytest.mark.parametrize(("opt_id", "_plain_id", "turbo_id"), OPT_TURBO_DIFF_PAIRS)
def test_the_opt_templates_differ_from_turbo_only_in_the_lora_and_the_steps(
    opt_id, _plain_id, turbo_id
):
    """テンプレートの差分はノード 150 の削除・151 の付け替え・steps だけ。"""
    opt = load_template(opt_id)
    turbo = copy.deepcopy(load_template(turbo_id))
    assert "150" in turbo and "150" not in opt
    del turbo["150"]
    turbo["151"]["inputs"]["model"] = ["127", 0]
    turbo["124"]["inputs"]["steps"] = 20
    assert opt == turbo


def test_the_opt_templates_have_plain_numeric_node_ids():
    for opt_id, _plain_id, _turbo_id in OPT_PAIRS:
        assert all(key.isdigit() for key in load_template(opt_id))


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
def test_the_default_frame_grid_rounds_down(duration, fps, expected):
    """宣言の無いワークフローの既定（``8n + 1`` を要求より短い側へ丸める）。"""
    frames = DEFAULT_FRAME_GRID.frames(duration, fps)
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
    # MiniMax H3 は 17k+5 を 24fps 固定で
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


# --- the video grid is per workflow (video latents use a coarser grid) ------

@pytest.mark.parametrize(
    "size", [(1920, 1060), (1920, 1080), (1000, 1500), (100, 1000), None]
)
def test_video_edges_follow_the_latent_grid(size):
    """Every video workflow lands on its own multiple, start frame or preset."""
    for spec in specs_of_kind("video"):
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
    spec = get_spec("minimax_h3_i2v")
    assert spec.resolution_multiple == 32
    width, height = video_resolution(
        spec,
        params(
            mode="i2v",
            video_workflow="minimax_h3_i2v",
            megapixels=1.0,
            start_image_size=(1920, 1060),
        ),
    )
    assert width % 32 == 0 and height % 32 == 0


@pytest.mark.parametrize(
    "workflow_id", ["minimax_h3_i2v", "minimax_h3_i2v_turbo"]
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
            selects={LATENT_UPSCALE_NAME: "off"},
        )
    )
    expected = resolution_for_image(
        1000, 1500, 1.0, multiple=spec.resolution_multiple
    )
    assert (value(wf, spec, "width"), value(wf, spec, "height")) == expected
    # portrait image => portrait output, not the 16:9 preset
    assert value(wf, spec, "height") > value(wf, spec, "width")


def test_a_workflow_without_a_start_frame_ignores_the_start_frame_size():
    """開始フレームを取らないワークフロー（参照モード）はプリセットのまま。"""
    spec = get_spec("minimax_h3_r2v")
    wf = build_video_workflow(
        params(
            mode="i2v",
            video_workflow="minimax_h3_r2v",
            reference_image_names=["ref0.png"],
            aspect_ratio="16:9 (Widescreen)",
            megapixels=1.0,
            start_image_size=(1000, 1500),
            selects={LATENT_UPSCALE_NAME: "off"},
        )
    )
    expected = resolution("16:9 (Widescreen)", 1.0, multiple=spec.resolution_multiple)
    assert (value(wf, spec, "width"), value(wf, spec, "height")) == expected


def test_without_a_start_frame_size_the_preset_is_used():
    """No readable reference image (or none at all) => unchanged behaviour."""
    spec = get_spec("minimax_h3_i2v")
    wf = build_video_workflow(
        params(
            mode="i2v",
            video_workflow="minimax_h3_i2v",
            aspect_ratio="16:9 (Widescreen)",
            megapixels=1.0,
            start_image_size=None,
            selects={LATENT_UPSCALE_NAME: "off"},
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
    assert _missing("i2v", "minimax_h3_t2v") == []


def test_i2v_needs_a_start_frame():
    assert _missing("i2v", "minimax_h3_i2v") == ["source_image"]
    assert _missing("i2v", "minimax_h3_i2v", source_image="/assets/image/a.png") == []
    # full mode generates the first frame itself
    assert _missing("full", "minimax_h3_i2v") == []


def test_image_only_ignores_the_video_workflow():
    assert missing_job_fields(
        "image_only",
        image_prompt="",
        video_prompt=None,
        audio_path=None,
        source_image=None,
        video_workflow="minimax_h3_i2v",
    ) == ["image_prompt"]


@pytest.mark.parametrize("workflow_id", ["minimax_h3_t2v", "minimax_h3_r2v"])
def test_workflows_without_a_start_frame_cannot_run_in_full_mode(workflow_id):
    assert video_workflow_problem("full", workflow_id)
    assert video_workflow_problem("i2v", workflow_id) is None


def test_start_frame_capable_workflows_are_fine_in_full_mode():
    assert video_workflow_problem("full", "minimax_h3_i2v") is None
    assert video_workflow_problem("full", "nope")  # unknown id is reported too
    assert video_workflow_problem("image_only", "nope") is None


# --- 参照素材が使える組み合わせ（画像ステージも含む、SPEC §3.1）--------------

def _refs(count: int = 1) -> dict[str, list[str]]:
    return {"reference_images": [f"ref{i}.png" for i in range(count)]}


def test_the_image_stage_can_take_reference_images():
    """MiniMax H3 Image r2i は画像ステージで参照画像を受け取る。"""
    for mode in ("image_only", "full"):
        assert reference_problem(
            mode,
            "minimax_h3_i2v",
            _refs(3),
            image_workflow="minimax_h3_r2i",
        ) is None


def test_an_image_workflow_without_a_declaration_refuses_reference_images():
    problem = reference_problem(
        "image_only", None, _refs(1), image_workflow="krea2_turbo"
    )
    assert problem and "krea2_turbo" in problem and "reference_images" in problem


def test_the_image_reference_workflow_needs_at_least_one_reference():
    problem = reference_problem(
        "image_only", None, {}, image_workflow="minimax_h3_r2i"
    )
    assert problem and "minimax_h3_r2i" in problem
    # 宣言の無い画像ワークフローでは何も言わない
    assert reference_problem(
        "image_only", None, {}, image_workflow="krea2_turbo"
    ) is None


def test_too_many_image_reference_images_are_refused():
    problem = reference_problem(
        "image_only", None, _refs(10), image_workflow="minimax_h3_r2i"
    )
    assert problem and "9 件" in problem


def test_the_video_stage_reference_rules_are_unchanged():
    assert reference_problem("i2v", "minimax_h3_r2v", _refs(2)) is None
    problem = reference_problem("i2v", "minimax_h3_i2v", _refs(1))
    assert problem and "minimax_h3_i2v" in problem
    # 参照素材を受け取るステージを 1 つも走らせない mode
    assert reference_problem("audio", None, _refs(1))


# --------------------------------------------------------------------------
# validation
# --------------------------------------------------------------------------

def test_validate_detects_dangling_link():
    wf = build_video_workflow(params(video_workflow="minimax_h3_i2v"))
    wf["105:16"]["inputs"]["model"] = ["does_not_exist", 0]
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
        "MiniMaxH3ImageToVideo",
        "MiniMaxH3ReferenceToVideo",
        "LoadVideo",
    } <= types

    built = set()
    for workflow_id in COMFY_IMAGE_IDS:
        built |= {
            node["class_type"]
            for node in build_image_workflow(params(image_workflow=workflow_id)).values()
        }
    for workflow_id in VIDEO_IDS:
        built |= {
            node["class_type"]
            for node in build_video_workflow(params(video_workflow=workflow_id)).values()
        }
    # 任意のカスタムノード（turbo / opt と MiniMax H3 Image）は意図的に
    # 外れている（SPEC §3.1）
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
    assert "minimax_h3_i2v/105:6.unet_name" in fields
    assert "minimax_h3_r2v/127.unet_name" in fields
    assert "minimax_h3_i2v_turbo/127.unet_name" in fields

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
        "minimax_h3_i2v/105:6.unet_name": "b.safetensors",
        "minimax_h3_t2v/105:6.unet_name": "",  # 空値は落とす
    }
    assert scoped_model_overrides(overrides, ["minimax_h3_i2v", "minimax_h3_t2v"]) == {
        "minimax_h3_i2v/105:6.unet_name": "b.safetensors"
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
        "minimax_h3_i2v/105:6.unet_name": "custom-unet2.safetensors",
        "minimax_h3_r2v/127.unet_name": "other-unet.safetensors",
    }
    image = build_image_workflow(params(), overrides)
    assert image["30:10"]["inputs"]["unet_name"] == "custom-unet.safetensors"

    i2v = build_video_workflow(params(video_workflow="minimax_h3_i2v"), overrides)
    assert i2v["105:6"]["inputs"]["unet_name"] == "custom-unet2.safetensors"

    # r2v shares node ids with the turbo templates but not the override scope
    r2v = build_video_workflow(
        params(video_workflow="minimax_h3_r2v", reference_image_names=["ref0.png"]),
        overrides,
    )
    assert r2v["127"]["inputs"]["unet_name"] == "other-unet.safetensors"
    turbo = build_video_workflow(
        params(video_workflow="minimax_h3_i2v_turbo"), overrides
    )
    assert turbo["127"]["inputs"]["unet_name"] != "other-unet.safetensors"

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
STEPS_VIDEO_IDS = [
    spec.id for spec in specs_of_kind("video") if spec.supports("steps")
]


def test_the_steps_targets_are_the_samplers_of_their_template():
    """`steps` の注入先は必ずサンプラー側（KSampler / BasicScheduler / H3 の Advanced Sampling）。"""
    declared = [spec for spec in SPECS if spec.supports("steps")]
    assert declared, "steps を宣言したワークフローが 1 つも無い"
    for spec in declared:
        target = spec.target("steps")
        assert target.field == "steps"
        assert target.class_type in (
            "KSampler",
            "BasicScheduler",
            "H3SamplingSettings",
        ), spec.id


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
    """`steps` を宣言していないワークフローに渡しても何も起きない。"""
    spec = get_spec("minimax_h3_t2v", "video")
    stripped = replace(
        spec, inject={k: v for k, v in spec.inject.items() if k != "steps"}
    )
    assert not stripped.supports("steps")
    plain = build_video_workflow(
        params(video_workflow=spec.id, steps=0), spec=stripped
    )
    with_steps = build_video_workflow(
        params(video_workflow=spec.id, steps=42), spec=stripped
    )
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


# --------------------------------------------------------------------------
# 連続カット（MiniMax H3 r2v + Motion Context）
# --------------------------------------------------------------------------

def context_nodes(wf: dict) -> dict:
    """class_type -> そのノード（連続カットのテンプレートは各 1 個ずつ）。

    2 段引き継ぎ（``latent_upscale`` = on）が足す 2 個目の SaveLatent /
    LoadLatent / MotionContext / BasicGuider は**テンプレートのうしろ**に付くので、
    先に見つけたほう（＝テンプレート側の 1 パス目のノード）を残す。
    """
    found: dict = {}
    for node in wf.values():
        found.setdefault(node["class_type"], node)
    return found


def test_the_context_workflow_wires_the_previous_clip_in():
    wf = build_video_workflow(
        params(
            video_workflow="minimax_h3_r2v_context",
            reference_video_name="previous.mp4",
            context_latent_path="/comfy/output/h3_context/prev_00002_.safetensors",
            reference_image_names=["ref.png"],
        )
    )
    by_class = context_nodes(wf)
    # 直前カットの動画はアップロード名、AV ラテントは ComfyUI 側のパスそのまま
    assert by_class["LoadVideo"]["inputs"]["file"] == "previous.mp4"
    assert (
        by_class["MiniMaxH3MotionContextLoadLatent"]["inputs"]["latent_path"]
        == "/comfy/output/h3_context/prev_00002_.safetensors"
    )
    # このカットぶんの保存先はジョブごとに分ける
    assert (
        by_class["MiniMaxH3MotionContextSaveLatent"]["inputs"]["filename_prefix"]
        == "h3_context/01JOBID"
    )
    # サンプラーは Motion Context を通した CONDITIONING を読む
    context_id = next(
        key for key, node in wf.items() if node["class_type"] == "MiniMaxH3MotionContext"
    )
    guider = by_class["BasicGuider"]
    assert guider["inputs"]["conditioning"] == [context_id, 0]
    # 出力はピン留めフレームを落としたあとの映像・音声から組み立てる
    trim_id = next(
        key
        for key, node in wf.items()
        if node["class_type"] == "MiniMaxH3MotionContextTrim"
    )
    assert by_class["CreateVideo"]["inputs"]["images"] == [trim_id, 0]
    assert by_class["CreateVideo"]["inputs"]["audio"] == [trim_id, 1]
    assert wf[trim_id]["inputs"]["trim_frames"] == [context_id, 1]


def test_the_context_workflow_uses_the_sample_settings():
    """Motion Context のつまみはテンプレートの固定値（本家ノードの既定に合わせる）。"""
    inputs = context_nodes(
        build_video_workflow(params(video_workflow="minimax_h3_r2v_context"))
    )["MiniMaxH3MotionContext"]["inputs"]
    # context_length は文字列コンボ（"22" / "5" / "39" / "56"）で、既定の "22"
    assert inputs["context_length"] == "22"
    # 0 = 音の窓は映像の窓に追従する
    assert inputs["audio_context_length"] == 0
    # 本家 v0.2.0 には無い入力（送ると ComfyUI のバリデーションで弾かれる）
    for gone in ("encode_mode", "anchor_mode", "crop", "audio_mode"):
        assert gone not in inputs


def test_only_the_context_workflow_declares_a_latent_output():
    spec = get_video_spec("minimax_h3_r2v_context")
    assert spec.latent_output_node
    node = load_template(spec)[spec.latent_output_node]
    assert node["class_type"] == "PreviewAny"
    assert get_video_spec("minimax_h3_r2v").latent_output_node == ""


# --------------------------------------------------------------------------
# ラテント保存版（連鎖の起点になる通常カット）
# --------------------------------------------------------------------------

#: (保存付きバリアント, 素の版)
SAVE_PAIRS = [
    ("minimax_h3_t2v_save", "minimax_h3_t2v"),
    ("minimax_h3_i2v_save", "minimax_h3_i2v"),
    ("minimax_h3_r2v_save", "minimax_h3_r2v"),
]


@pytest.mark.parametrize(("save_id", "plain_id"), SAVE_PAIRS)
def test_the_save_templates_only_add_the_two_saving_nodes(save_id, plain_id):
    """素の版に SaveLatent -> PreviewAny を足しただけ（他は一切変えない）。"""
    plain = load_template(plain_id)
    save = load_template(save_id)
    assert set(save) - set(plain) == {"155", "156"}
    assert not set(plain) - set(save)
    for key, node in plain.items():
        assert save[key] == node, key
    # 保存するのはサンプラー出力の AV ラテント
    sampler = save["155"]["inputs"]["latent"][0]
    assert save[sampler]["class_type"] == "SamplerCustomAdvanced"
    assert save["155"]["class_type"] == "MiniMaxH3MotionContextSaveLatent"
    assert save["155"]["inputs"]["filename_prefix"] == "h3_context/clip"
    # パスは PreviewAny 経由で持ち帰る（:func:`app.jobs._pick_text`）
    assert save["156"]["class_type"] == "PreviewAny"
    assert save["156"]["inputs"]["source"] == ["155", 0]
    # Motion Context の読み込み・Trim は入れない（起点のカットなので）
    assert not {node["class_type"] for node in save.values()} & {
        "MiniMaxH3MotionContext",
        "MiniMaxH3MotionContextLoadLatent",
        "MiniMaxH3MotionContextTrim",
    }


@pytest.mark.parametrize(("save_id", "plain_id"), SAVE_PAIRS)
def test_the_save_specs_declare_the_latent_output(save_id, plain_id):
    spec = get_video_spec(save_id)
    assert spec.latent_output_node == "156"
    assert load_template(spec)["156"]["class_type"] == "PreviewAny"
    # 引き継ぎ元は受け取らない（LoadLatent が無いので）
    assert not spec.supports("context_latent")
    # 保存先はジョブごとに分ける
    wf = build_video_workflow(params(video_workflow=save_id))
    assert wf["155"]["inputs"]["filename_prefix"] == "h3_context/01JOBID"
    # 入力の形は素の版と同じ
    plain = get_video_spec(plain_id)
    assert spec.requires == plain.requires
    assert spec.multi_inputs == plain.multi_inputs
    assert spec.accepts_start_image == plain.accepts_start_image


@pytest.mark.parametrize(("save_id", "plain_id"), SAVE_PAIRS)
def test_the_save_workflows_are_hidden_on_comfy_cloud(save_id, plain_id):
    """SaveLatent はカスタムノードなので Comfy Cloud では選ばせない。"""
    assert not supported_on_target(get_video_spec(save_id), "comfy_cloud")
    assert supported_on_target(get_video_spec(save_id), "local")
    # 素の版は今までどおり Comfy Cloud でも使える
    assert supported_on_target(get_video_spec(plain_id), "comfy_cloud")


@pytest.mark.parametrize(("save_id", "plain_id"), SAVE_PAIRS)
def test_the_save_workflows_share_the_prompt_guide_of_the_plain_ones(save_id, plain_id):
    assert prompts.video_guide_for(save_id) == prompts.video_guide_for(plain_id) != ""
    assert save_id in prompts.MULTI_CUT_WORKFLOWS


def test_the_context_workflow_is_hidden_on_comfy_cloud():
    """カスタムノード頼みなので Comfy Cloud では選ばせない（SPEC §2.2）。"""
    spec = get_video_spec("minimax_h3_r2v_context")
    assert not supported_on_target(spec, "comfy_cloud")
    assert supported_on_target(spec, "local")


def test_the_context_latent_is_required_by_the_context_workflow_only():
    """連続カットは引き継ぎ元が要り、他のワークフローには渡せない（SPEC §2.2）。"""
    assert context_latent_problem("i2v", "minimax_h3_r2v_context", None)
    assert not context_latent_problem(
        "i2v", "minimax_h3_r2v_context", "/comfy/output/h3_context/a_00001_.safetensors"
    )
    # 宣言の無いワークフローには渡せない
    assert context_latent_problem("i2v", "minimax_h3_r2v", "/comfy/output/a.safetensors")
    assert not context_latent_problem("i2v", "minimax_h3_r2v", None)
    # 動画ステージを走らせないモードでは何も言わない
    assert not context_latent_problem("image_only", "minimax_h3_r2v_context", None)


# --- 画像ステージの解像度予算（default_megapixels、SPEC §3.1）-----------------

def test_an_unspecified_megapixels_follows_the_image_workflow():
    """`megapixels` を送ってこないジョブは、そのモデルの想定画角で回す。

    MiniMax H3 Image の native canvas は約 0.98MP。グローバル既定の 0.4MP のまま
    回すと、ネイティブの 4 割の解像度で生成することになる。
    """
    spec = get_spec("minimax_h3_t2i", "image")
    assert spec.default_megapixels == pytest.approx(0.98)
    unset = params(mode="image_only", image_workflow=spec.id, megapixels=DEFAULT_MEGAPIXELS)
    assert image_megapixels(spec, unset) == pytest.approx(0.98)
    width, height = resolution("16:9 (Widescreen)", 0.98, multiple=32)
    built = build_image_workflow(unset)
    assert (built["5"]["inputs"]["width"], built["5"]["inputs"]["height"]) == (
        width,
        height,
    )


def test_an_explicit_megapixels_is_respected():
    """明示した値は勝手に上げ下げしない（0.4MP を選んだジョブは 0.4MP のまま）。"""
    spec = get_spec("minimax_h3_t2i", "image")
    for asked in (0.2, 0.7, 2.0):
        picked = params(mode="image_only", image_workflow=spec.id, megapixels=asked)
        assert image_megapixels(spec, picked) == pytest.approx(asked)


def test_workflows_without_a_declaration_keep_the_global_default():
    """宣言を持たない既存の画像ワークフローの挙動は変わらない。"""
    for workflow_id in ("krea2_turbo", "anima", "z_image_turbo"):
        spec = get_spec(workflow_id, "image")
        assert spec.default_megapixels == 0.0
        unset = params(
            mode="image_only", image_workflow=workflow_id, megapixels=DEFAULT_MEGAPIXELS
        )
        assert image_megapixels(spec, unset) == pytest.approx(DEFAULT_MEGAPIXELS)
    # ResolutionSelector に入る値もグローバル既定のまま
    wf = build_image_workflow(
        params(
            mode="image_only", image_workflow="krea2_turbo", megapixels=DEFAULT_MEGAPIXELS
        )
    )
    assert wf["49"]["inputs"]["megapixels"] == pytest.approx(DEFAULT_MEGAPIXELS)
