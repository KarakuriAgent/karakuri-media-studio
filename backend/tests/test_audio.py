"""音声生成（mode='audio'）: マニフェスト・注入・API。

音声は画像→動画の連結とは独立したジョブなので、ここでは「音声ジョブが正しく
動くこと」と「既存の image / video ジョブに一切影響しないこと」の両方を見る。
"""

import pytest

from app.models import (
    GenerationParams,
    JobCreate,
    audio_workflow_problem,
    missing_job_fields,
)
from app.prompts import (
    audio_prompt_guides_section,
    audio_workflow_catalog_section,
)
from app.workflow import build_audio_workflow, build_workflows, model_fields
from app.workflows import (
    AUDIO_CATEGORIES,
    DEFAULT_AUDIO_WORKFLOW,
    audio_catalog,
    audio_specs,
    get_audio_spec,
    get_spec,
    load_template,
    validate_spec,
)

MMM3 = "minimax_music_3"
SA3 = "stable_audio_3_medium_base"


# --------------------------------------------------------------------------
# manifests
# --------------------------------------------------------------------------

def test_both_audio_manifests_match_their_templates():
    specs = audio_specs()
    assert [spec.id for spec in specs] == [MMM3, SA3]
    for spec in specs:
        assert spec.kind == "audio"
        # 音声に LoRA は無い（どちらのテンプレートにもローダーが無い）
        assert spec.lora_chain is None
        assert not spec.accepts_start_image
        assert spec.requires == ()
        assert validate_spec(spec, load_template(spec, use_cache=False)) == []


def test_default_audio_workflow_is_minimax_music_3():
    assert DEFAULT_AUDIO_WORKFLOW == MMM3
    assert get_audio_spec(None).id == MMM3


def test_get_spec_rejects_a_cross_kind_lookup():
    with pytest.raises(Exception):
        get_spec(MMM3, "video")


def test_audio_model_fields_are_configurable():
    keys = {field.key for field in model_fields() if field.kind == "audio"}
    assert f"{MMM3}/37:6.unet_name" in keys
    assert f"{MMM3}/37:3.clip_name" in keys
    assert f"{MMM3}/37:7.vae_name" in keys
    assert f"{SA3}/52:25.ckpt_name" in keys


# --------------------------------------------------------------------------
# injection
# --------------------------------------------------------------------------

def _params(**overrides) -> GenerationParams:
    base = dict(mode="audio", job_id="job1", audio_prompt="a warm lofi loop")
    base.update(overrides)
    return GenerationParams(**base)


def test_minimax_music_injects_the_duration_once_and_the_latent_follows():
    """37:13.max_duration だけ入れれば、空ラテントはその出力を読む。"""
    wf = build_audio_workflow(_params(audio_workflow=MMM3, duration=45.4))
    # MiniMaxMusic3TextEncode.max_duration は FLOAT ウィジェット
    assert wf["37:13"]["inputs"]["max_duration"] == pytest.approx(45.4)
    assert isinstance(wf["37:13"]["inputs"]["max_duration"], float)
    # EmptyMiniMaxMusic3LatentAudio.seconds は 37:13 の 2 番目の出力へのリンク
    assert wf["37:15"]["inputs"]["seconds"] == ["37:13", 1]


def test_minimax_music_injects_every_exposed_field():
    wf = build_audio_workflow(
        _params(
            audio_workflow=MMM3,
            audio_prompt="Global Metadata: dreamy city-pop, around 80 BPM",
            lyrics="[Verse]\nthe last train hums",
            audio_seed=4242,
        )
    )
    encode = wf["37:13"]["inputs"]
    assert encode["caption"] == "Global Metadata: dreamy city-pop, around 80 BPM"
    assert encode["lyrics"] == "[Verse]\nthe last train hums"
    # 一つの SeedNode が KSampler.seed と 37:13.seed の両方に配線されている
    assert wf["37:38"]["inputs"]["seed"] == 4242
    assert isinstance(wf["37:38"]["inputs"]["seed"], int)
    assert wf["37:9"]["inputs"]["seed"] == ["37:38", 0]
    assert wf["37:13"]["inputs"]["seed"] == ["37:38", 0]
    assert wf["35"]["inputs"]["filename_prefix"] == "audio/job1"
    # テンプレート既定はそのまま（cfg_scale / top_k は露出していない）
    assert encode["cfg_scale"] == pytest.approx(1.7)
    assert encode["top_k"] == 50


def test_minimax_music_ignores_stable_audio_only_fields():
    """モデル固有フィールドは inject キーの有無で自然にスキップされる。"""
    wf = build_audio_workflow(
        _params(audio_workflow=MMM3, audio_category="SFX", reprompt=True)
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


def test_stable_audio_ignores_the_lyrics_it_cannot_sing():
    wf = build_audio_workflow(_params(audio_workflow=SA3, lyrics="[Verse] la la"))
    assert "37:13" not in wf
    assert all("lyrics" not in (node.get("inputs") or {}) for node in wf.values())


def test_build_workflows_returns_only_the_audio_stage():
    stages = build_workflows(_params(audio_workflow=MMM3))
    assert list(stages) == ["audio"]


def test_model_overrides_apply_to_audio_templates():
    wf = build_audio_workflow(
        _params(audio_workflow=MMM3),
        {f"{MMM3}/37:6.unet_name": "other.safetensors"},
    )
    assert wf["37:6"]["inputs"]["unet_name"] == "other.safetensors"


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
    """qwen-image / i2v の必須入力は音声ジョブには一切効かない。"""
    assert (
        missing_job_fields(
            "audio",
            **_EMPTY,
            audio_prompt="a song",
            image_workflow="qwen_image_edit_2511",
            video_workflow="minimax_h3_i2v",
        )
        == []
    )


@pytest.mark.parametrize(
    "kwargs, needle",
    [
        ({"audio_workflow": "nope"}, "unknown workflow"),
        ({"duration": 900}, "1-300 seconds"),
        ({"duration": 0.5}, "1-300 seconds"),
    ],
)
def test_audio_workflow_problem_reports_out_of_range_values(kwargs, needle):
    problem = audio_workflow_problem("audio", kwargs.pop("audio_workflow", MMM3), **kwargs)
    assert problem and needle in problem


def test_audio_workflow_problem_is_silent_for_other_modes():
    assert audio_workflow_problem("full", "nope", duration=9999) is None


def test_stable_audio_rejects_an_unknown_category():
    problem = audio_workflow_problem("audio", SA3, audio_category="Podcast")
    assert problem and "unknown audio_category" in problem


def test_minimax_music_does_not_police_the_category_it_does_not_have():
    assert audio_workflow_problem("audio", MMM3, audio_category="Podcast") is None


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
    with pytest.raises(ValueError, match="1-300 seconds"):
        JobCreate(mode="audio", audio_prompt="a lofi loop", duration=900)


def test_existing_modes_are_unaffected_by_the_audio_fields():
    """既存ジョブは音声フィールドの既定値があっても今までどおり通る。"""
    payload = JobCreate(mode="image_only", image_prompt="a still")
    assert payload.audio_prompt == ""
    assert payload.mode == "image_only"
    # 音声の秒数レンジ（1-300s）は動画の短いクリップを巻き込まない
    clip = JobCreate(
        mode="i2v", video_workflow="minimax_h3_t2v", video_prompt="a clip", duration=0.5
    )
    assert clip.duration == 0.5


# --------------------------------------------------------------------------
# system prompt
# --------------------------------------------------------------------------

def test_audio_catalog_section_lists_both_workflows_and_their_limits():
    section = audio_workflow_catalog_section()
    assert MMM3 in section and SA3 in section
    assert "1〜300 秒" in section
    assert "1〜380 秒" in section
    assert "独立したジョブ" in section


def test_audio_prompt_guides_cover_every_registered_audio_workflow():
    guides = audio_prompt_guides_section()
    for entry in audio_catalog():
        assert f"`{entry.id}`" in guides
    # MiniMax Music 3 公式のセクションタグは大文字始まり
    assert "[Chorus]" in guides
    # 公式の Structured Caption の 3 セクション
    for head in ("Global Metadata:", "Vocal Details:", "Arrangement:"):
        assert head in guides
    # Stable Audio 公式テンプレートの締めくくり
    assert "Length: Y seconds" in guides


# --------------------------------------------------------------------------
# option lists
# --------------------------------------------------------------------------

def test_combo_option_lists_match_the_nodes():
    assert AUDIO_CATEGORIES == ("Music", "Instrument", "SFX", "One-shot")


@pytest.mark.parametrize(("workflow_id", "node_id"), [(MMM3, "37:9"), (SA3, "52:3")])
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
