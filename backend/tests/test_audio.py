"""音声生成（mode='audio'）: マニフェスト・注入・API・エージェント検証。

音声は画像→動画の連結とは独立したジョブなので、ここでは「音声ジョブが正しく
動くこと」と「既存の image / video ジョブに一切影響しないこと」の両方を見る。
"""

import pytest

from app.agent_protocol import ActionError, validate_job
from app.models import (
    GenerationParams,
    JobCreate,
    audio_workflow_problem,
    missing_job_fields,
)
from app.prompts import (
    audio_prompt_guides_section,
    audio_workflow_catalog_section,
    build_agent_system_prompt,
)
from app.models import AgentSessionCreate, Options
from app.workflow import build_audio_workflow, build_workflows, model_fields
from app.workflows import (
    AUDIO_CATEGORIES,
    DEFAULT_AUDIO_WORKFLOW,
    KEYSCALES,
    LANGUAGES,
    audio_catalog,
    audio_specs,
    get_audio_spec,
    get_spec,
    load_template,
    validate_spec,
)

ACE = "ace_step1_5_xl_sft"
SA3 = "stable_audio_3_medium_base"


# --------------------------------------------------------------------------
# manifests
# --------------------------------------------------------------------------

def test_both_audio_manifests_match_their_templates():
    specs = audio_specs()
    assert [spec.id for spec in specs] == [ACE, SA3]
    for spec in specs:
        assert spec.kind == "audio"
        # 音声に LoRA は無い（どちらのテンプレートにもローダーが無い）
        assert spec.lora_chain is None
        assert not spec.accepts_start_image
        assert spec.requires == ()
        assert validate_spec(spec, load_template(spec, use_cache=False)) == []


def test_default_audio_workflow_is_ace_step():
    assert DEFAULT_AUDIO_WORKFLOW == ACE
    assert get_audio_spec(None).id == ACE


def test_get_spec_rejects_a_cross_kind_lookup():
    with pytest.raises(Exception):
        get_spec(ACE, "video")


def test_audio_model_fields_are_configurable():
    keys = {field.key for field in model_fields() if field.kind == "audio"}
    # ACE-Step は DualCLIPLoader で 2 本のテキストエンコーダを読む
    assert f"{ACE}/105.clip_name1" in keys
    assert f"{ACE}/105.clip_name2" in keys
    assert f"{ACE}/104.unet_name" in keys
    assert f"{SA3}/52:25.ckpt_name" in keys


# --------------------------------------------------------------------------
# injection
# --------------------------------------------------------------------------

def _params(**overrides) -> GenerationParams:
    base = dict(mode="audio", job_id="job1", audio_prompt="a warm lofi loop")
    base.update(overrides)
    return GenerationParams(**base)


def test_ace_injects_the_duration_into_both_nodes():
    """94.duration（条件付け）と 98.seconds（空ラテント）は同期が必須。"""
    wf = build_audio_workflow(_params(audio_workflow=ACE, duration=45.4))
    # TextEncodeAceStepAudio1.5.duration は INT ウィジェット
    assert wf["94"]["inputs"]["duration"] == 45
    assert isinstance(wf["94"]["inputs"]["duration"], int)
    # EmptyAceStep1.5LatentAudio.seconds は FLOAT
    assert wf["98"]["inputs"]["seconds"] == pytest.approx(45.4)
    assert isinstance(wf["98"]["inputs"]["seconds"], float)


def test_ace_injects_every_exposed_field():
    wf = build_audio_workflow(
        _params(
            audio_workflow=ACE,
            audio_prompt="dreamy city-pop, female vocal, rhodes",
            lyrics="[Verse 1]\nthe last train hums",
            bpm=92,
            keyscale="F# minor",
            language="ja",
            audio_seed=4242,
        )
    )
    encode = wf["94"]["inputs"]
    assert encode["tags"] == "dreamy city-pop, female vocal, rhodes"
    assert encode["lyrics"] == "[Verse 1]\nthe last train hums"
    assert encode["bpm"] == 92
    assert encode["keyscale"] == "F# minor"
    assert encode["language"] == "ja"
    # 一つの PrimitiveInt が KSampler.seed と 94.seed の両方に配線されている
    assert wf["109"]["inputs"]["value"] == 4242
    assert wf["3"]["inputs"]["seed"] == ["109", 0]
    assert wf["94"]["inputs"]["seed"] == ["109", 0]
    assert wf["107"]["inputs"]["filename_prefix"] == "audio/job1"
    # テンプレート既定はそのまま（拍子は露出していない）
    assert encode["timesignature"] == "4"


def test_ace_ignores_stable_audio_only_fields():
    """モデル固有フィールドは inject キーの有無で自然にスキップされる。"""
    wf = build_audio_workflow(
        _params(audio_workflow=ACE, audio_category="SFX", reprompt=True)
    )
    assert "choice" not in wf.get("52:43", {}).get("inputs", {})
    assert "52:43" not in wf


def test_stable_audio_injects_duration_once_and_the_latent_follows():
    wf = build_audio_workflow(_params(audio_workflow=SA3, duration=12))
    assert wf["52:36"]["inputs"]["value"] == pytest.approx(12.0)
    # EmptyLatentAudio.seconds はその PrimitiveFloat へのリンクなので同期は不要
    assert wf["52:11"]["inputs"]["seconds"] == ["52:36", 0]


def test_stable_audio_injects_category_and_reprompt():
    wf = build_audio_workflow(
        _params(
            audio_workflow=SA3,
            audio_prompt="glass shattering on concrete. Length: 2 seconds",
            audio_category="SFX",
            reprompt=False,
            audio_seed=7,
        )
    )
    assert wf["52:31"]["inputs"]["value"].startswith("glass shattering")
    assert wf["52:43"]["inputs"]["choice"] == "SFX"
    # index=0 は「choice ウィジェットを使う」の意味。定数として固定する。
    assert wf["52:43"]["inputs"]["index"] == 0
    assert wf["52:35"]["inputs"]["value"] is False
    assert wf["52:3"]["inputs"]["seed"] == 7
    assert wf["19"]["inputs"]["filename_prefix"] == "audio/job1"


def test_stable_audio_ignores_ace_only_fields():
    wf = build_audio_workflow(_params(audio_workflow=SA3, lyrics="[Verse] la la", bpm=90))
    assert "94" not in wf
    assert all("lyrics" not in (node.get("inputs") or {}) for node in wf.values())


def test_build_workflows_returns_only_the_audio_stage():
    stages = build_workflows(_params(audio_workflow=ACE))
    assert list(stages) == ["audio"]


def test_model_overrides_apply_to_audio_templates():
    wf = build_audio_workflow(
        _params(audio_workflow=ACE),
        {f"{ACE}/104.unet_name": "other.safetensors"},
    )
    assert wf["104"]["inputs"]["unet_name"] == "other.safetensors"


# --------------------------------------------------------------------------
# job validation
# --------------------------------------------------------------------------

def test_audio_mode_only_requires_the_prompt():
    assert missing_job_fields("audio", **_EMPTY, audio_prompt="a song") == []
    assert missing_job_fields("audio", **_EMPTY, audio_prompt="  ") == ["audio_prompt"]


_EMPTY = dict(
    image_prompt=None,
    video_prompt=None,
    audio_path=None,
    source_image=None,
    end_image=None,
    reference_video=None,
)


def test_audio_mode_ignores_the_image_and_video_requirements():
    """qwen-image / flf2v の必須入力は音声ジョブには一切効かない。"""
    assert (
        missing_job_fields(
            "audio",
            **_EMPTY,
            audio_prompt="a song",
            image_workflow="qwen_image_edit_2511",
            video_workflow="ltx2_3_flf2v",
        )
        == []
    )


@pytest.mark.parametrize(
    "kwargs, needle",
    [
        ({"audio_workflow": "nope"}, "unknown workflow"),
        ({"duration": 900}, "10-600 seconds"),
        ({"duration": 3}, "10-600 seconds"),
        ({"keyscale": "Am"}, "unknown keyscale"),
        ({"language": "jp"}, "unknown language"),
        ({"bpm": 500}, "bpm must be between"),
    ],
)
def test_audio_workflow_problem_reports_out_of_range_values(kwargs, needle):
    problem = audio_workflow_problem("audio", kwargs.pop("audio_workflow", ACE), **kwargs)
    assert problem and needle in problem


def test_audio_workflow_problem_is_silent_for_other_modes():
    assert audio_workflow_problem("full", "nope", duration=9999) is None


def test_stable_audio_rejects_an_unknown_category():
    problem = audio_workflow_problem("audio", SA3, audio_category="Podcast")
    assert problem and "unknown audio_category" in problem


def test_ace_does_not_police_the_category_it_does_not_have():
    assert audio_workflow_problem("audio", ACE, audio_category="Podcast") is None


def test_job_create_accepts_an_audio_job():
    payload = JobCreate(mode="audio", audio_prompt="a lofi loop", duration=60)
    assert payload.audio_workflow == DEFAULT_AUDIO_WORKFLOW
    assert payload.mode == "audio"


def test_job_create_rejects_loras_on_an_audio_job():
    with pytest.raises(ValueError, match="no image or video stage"):
        JobCreate(
            mode="audio",
            audio_prompt="a lofi loop",
            loras=[{"lora_name": "x.safetensors"}],
        )


def test_job_create_rejects_an_out_of_range_duration():
    with pytest.raises(ValueError, match="10-600 seconds"):
        JobCreate(mode="audio", audio_prompt="a lofi loop", duration=5)


def test_existing_modes_are_unaffected_by_the_audio_fields():
    """既存ジョブは音声フィールドの既定値があっても今までどおり通る。"""
    payload = JobCreate(mode="image_only", image_prompt="a still")
    assert payload.audio_prompt == ""
    assert payload.mode == "image_only"
    # 音声の秒数レンジ（10-600s）は動画の短いクリップを巻き込まない
    clip = JobCreate(
        mode="i2v", video_workflow="ltx2_3_t2v", video_prompt="a clip", duration=2
    )
    assert clip.duration == 2


# --------------------------------------------------------------------------
# agent protocol
# --------------------------------------------------------------------------

def test_agent_accepts_a_well_formed_audio_task():
    job = validate_job(
        {
            "mode": "audio",
            "audio_workflow": SA3,
            "audio_prompt": "rain on a metal roof. Length: 30 seconds",
            "audio_category": "SFX",
            "duration": 30,
        },
        where="tasks[1]",
    )
    assert job.mode == "audio"
    assert job.audio_category == "SFX"


@pytest.mark.parametrize(
    "job, needle",
    [
        ({"mode": "audio", "audio_prompt": "x", "audio_workflow": "nope"},
         "使えるのは"),
        ({"mode": "audio", "duration": 60}, "audio_prompt"),
        ({"mode": "audio", "audio_prompt": "x", "duration": 1200}, "10-600 seconds"),
        ({"mode": "audio", "audio_prompt": "x", "keyscale": "Am"}, "unknown keyscale"),
        ({"mode": "audio", "audio_prompt": "x", "language": "jp"}, "unknown language"),
        ({"mode": "audio", "audio_prompt": "x", "video_prompt": "she dances"},
         "独立ジョブ"),
        ({"mode": "audio", "audio_prompt": "x", "source_image": "/assets/image/a.png"},
         "独立ジョブ"),
        ({"mode": "audio", "audio_prompt": "x", "audio_workflow": SA3,
          "lyrics": "[Verse] la"}, "歌詞を歌えません"),
        ({"mode": "audio", "audio_prompt": "x", "audio_category": "SFX"},
         "`audio_category` はありません"),
    ],
)
def test_agent_rejects_bad_audio_tasks_with_a_self_correcting_message(job, needle):
    with pytest.raises(ActionError) as caught:
        validate_job(job, where="tasks[1]")
    assert needle in str(caught.value)


def test_agent_still_accepts_the_existing_video_tasks():
    job = validate_job(
        {"mode": "image_only", "image_prompt": "a still"}, where="tasks[1]"
    )
    assert job.mode == "image_only"


# --------------------------------------------------------------------------
# system prompt
# --------------------------------------------------------------------------

def test_audio_catalog_section_lists_both_workflows_and_their_limits():
    section = audio_workflow_catalog_section()
    assert ACE in section and SA3 in section
    assert "10〜600 秒" in section
    assert "1〜380 秒" in section
    assert "独立したジョブ" in section


def test_audio_prompt_guides_cover_every_registered_audio_workflow():
    guides = audio_prompt_guides_section()
    for entry in audio_catalog():
        assert f"`{entry.id}`" in guides
    # ACE-Step 1.5 公式の構造タグは大文字始まり
    assert "[Chorus]" in guides
    # Stable Audio 公式テンプレートの締めくくり
    assert "Length: Y seconds" in guides


def test_agent_system_prompt_embeds_the_audio_sections():
    prompt = build_agent_system_prompt(AgentSessionCreate(goal="音楽を作りたい"), Options())
    assert "# AUDIO WORKFLOWS" in prompt
    assert "# AUDIO PROMPT GUIDES" in prompt
    # 画像・動画のカタログは今までどおり残っている
    assert "# IMAGE WORKFLOWS" in prompt
    assert "# VIDEO WORKFLOWS" in prompt


# --------------------------------------------------------------------------
# option lists
# --------------------------------------------------------------------------

def test_combo_option_lists_match_the_nodes():
    assert AUDIO_CATEGORIES == ("Music", "Instrument", "SFX", "One-shot")
    assert len(KEYSCALES) == 34
    assert "C major" in KEYSCALES and "F# minor" in KEYSCALES
    assert len(LANGUAGES) == 51
    assert LANGUAGES[-1] == "unknown"
    assert "ja" in LANGUAGES


@pytest.mark.parametrize(("workflow_id", "node_id"), [(ACE, "3"), (SA3, "52:3")])
def test_audio_steps_default_to_the_template_and_are_injected_when_set(
    workflow_id, node_id
):
    """`steps` 未指定（0）はテンプレート既定のまま、正の値だけが入る（§3.1）。"""
    default = load_template(get_audio_spec(workflow_id))[node_id]["inputs"]["steps"]
    unset = build_audio_workflow(_params(audio_workflow=workflow_id))
    assert unset[node_id]["inputs"]["steps"] == default

    wf = build_audio_workflow(_params(audio_workflow=workflow_id, steps=24))
    assert wf[node_id]["inputs"]["steps"] == 24
    assert isinstance(wf[node_id]["inputs"]["steps"], int)
