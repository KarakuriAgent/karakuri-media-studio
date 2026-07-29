"""Workflow template registry and per-template injection manifests (SPEC §3).

The app ships a folder of independent ComfyUI API-format graphs under
``workflow/``: one image workflow (Krea 2 turbo) and seven LTX 2.3 video
workflows.  Each one is described here by a :class:`WorkflowSpec` whose
``inject`` map names every node/field the app writes to.

Why node ids and not ``class_type`` + title?  Because the templates contain
several nodes that are indistinguishable that way — both CLIPTextEncode nodes
share the title 「CLIPテキストエンコード（プロンプト）」, there are two
``RandomNoise`` nodes per video graph and half a dozen ``ComfyMathExpression``
nodes titled 「数式」.  The manifests therefore pin node ids, and
:func:`validate_specs` (run by the health check and by the tests) asserts that
every pinned node still exists with the expected ``class_type`` so that editing
a template can never silently break the injector.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Iterable, Literal

from .paths import WORKFLOW_DIR

Workflow = dict[str, dict[str, Any]]

WorkflowKind = Literal["image", "video"]

#: Logical names of the assets a video workflow can require.  ``image`` is the
#: primary image input (start frame, first frame or reference sheet depending on
#: the workflow), ``audio`` a reference audio track, ``end_image`` the closing
#: frame of flf2v and ``video`` a reference clip.
InputName = Literal["image", "audio", "end_image", "video"]


class WorkflowSpecError(Exception):
    """A manifest does not match the template it describes."""


@dataclass(frozen=True)
class Target:
    """One injection point: ``node_id.field`` of a node of ``class_type``.

    ``field`` is empty for manifest entries that name a *node* rather than one
    of its inputs (the frame-count expression, the trigger concatenation node,
    the head of the LoRA chain).
    """

    node_id: str
    field: str
    class_type: str

    @property
    def key(self) -> str:
        return f"{self.node_id}.{self.field}" if self.field else self.node_id


def T(node_id: str, field: str, class_type: str) -> Target:
    return Target(node_id, field, class_type)


@dataclass(frozen=True)
class LoraChain:
    """Where the dynamic user-LoRA chain is spliced into a template (SPEC §3.4).

    The chain is one edge of the graph, cut open: ``head`` is the node whose
    ``MODEL`` output the chain starts from and ``consumers`` are the inputs that
    used to read it directly and are re-pointed at the chain's tail.  With no
    LoRA selected the consumers are wired straight back to ``head``, so the graph
    is identical to the template.

    ``placeholders`` are a template's own (strength 0) ``LoraLoaderModelOnly``
    stubs, which the app deletes before rebuilding the chain.  Only the image
    template has them; the LTX 2.3 templates splice in **after** their fixed
    LoRA nodes (distill / ID-LoRA / IC-LoRA), which must stay, so their
    ``placeholders`` is empty.
    """

    head: str
    placeholders: tuple[str, ...] = ()
    consumers: tuple[Target, ...] = ()


@dataclass(frozen=True)
class WorkflowSpec:
    id: str
    label: str
    kind: WorkflowKind
    relpath: str
    #: logical name -> injection target
    inject: dict[str, Target]
    #: node id that produces the artefact the job runner downloads
    output_node: str
    requires: tuple[InputName, ...] = ()
    #: what the workflow is for, in one or two Japanese sentences.  This is the
    #: single source of the catalog embedded in the Grok system prompts
    #: (:func:`video_catalog`), so keep it factual and short.
    description: str = ""
    #: how ``audio_path`` is used.  Empty means "there is no audio input": the
    #: model then generates the soundtrack together with the picture.
    audio_role: str = ""
    #: how to write ``video_prompt`` for this workflow (English, it goes
    #: straight into the LLM prompts).  Required for video workflows.
    prompt_hint: str = ""
    #: can this workflow be the second stage of a full (image -> video) job?
    accepts_start_image: bool = False
    #: UI label of the primary image input
    image_label: str = "開始フレーム"
    lora_chain: LoraChain | None = None
    notes: str = ""
    seeds: tuple[Target, ...] = ()
    #: extra targets keyed by logical name that are always forced to a constant
    constants: dict[str, Any] = field(default_factory=dict)

    @property
    def path(self):
        return WORKFLOW_DIR / self.relpath

    def targets(self) -> Iterable[Target]:
        yield from self.inject.values()
        yield from self.seeds
        if self.lora_chain is not None:
            yield from self.lora_chain.consumers

    def supports(self, name: str) -> bool:
        return name in self.inject

    def target(self, name: str) -> Target | None:
        return self.inject.get(name)


# --------------------------------------------------------------------------
# image: workflow/image/krea2/krea2_turbo.json
# --------------------------------------------------------------------------

KREA2_TURBO = WorkflowSpec(
    id="krea2_turbo",
    label="Krea 2 turbo",
    kind="image",
    relpath="image/krea2/krea2_turbo.json",
    output_node="29",
    description=(
        "テキストから静止画を 1 枚生成する。画像のみ（image_only）モードと、"
        "full モードの 1 段目に使う。"
    ),
    inject={
        "aspect_ratio": T("49", "aspect_ratio", "ResolutionSelector"),
        "megapixels": T("49", "megapixels", "ResolutionSelector"),
        "prompt": T("30:19", "value", "PrimitiveStringMultiline"),
        "seed": T("30:3", "seed", "KSampler"),
        # local TextGenerate refine is off: Grok already writes the final prompt
        "refine_enable": T("30:24", "value", "PrimitiveBoolean"),
        # StringConcatenate that prepends the LoRA trigger words
        "trigger_concat": T("30:27", "", "StringConcatenate"),
        "trigger_switch": T("30:28", "switch", "ComfySwitchNode"),
        # PreviewAny that carries the (possibly refined) prompt string
        "prompt_source": T("30:20", "", "PreviewAny"),
        "save_prefix": T("29", "filename_prefix", "SaveImage"),
    },
    lora_chain=LoraChain(
        head="30:10",
        placeholders=("30:61:60", "30:61:58", "30:61:57", "30:61:55", "30:61:62"),
        consumers=(T("30:3", "model", "KSampler"),),
    ),
    constants={"refine_enable": False},
)


# --------------------------------------------------------------------------
# video: workflow/video/ltx2.3/*.json
# --------------------------------------------------------------------------
#
# Video LoRA chain (SPEC §3.4): every LTX template already carries fixed LoRA
# nodes it cannot work without — the distilled-1.1 speed LoRA on the "dev"
# graphs, the talkvid ID-LoRA, the ingredients / union-control IC-LoRAs.  The
# user chain is therefore spliced in *after* the last of those on the path to
# the sampler, and only the sampler-side consumers are re-pointed:
#
# * t2v / i2v / ia2v: head = the distill ``LoraLoaderModelOnly``, consumers =
#   both ``CFGGuider.model`` inputs (the base and the upscale pass).  The Gemma
#   ``LoraLoader`` hanging off the same output is a text-encoder LoRA whose
#   MODEL output nothing reads, so it keeps the raw model.
# * id_lora: head = the distill LoRA as well, consumers = the first
#   ``CFGGuider`` *and* the talkvid ID-LoRA node, so the user LoRA applies to
#   both passes while staying in front of the ID-LoRA / LTXVReferenceAudio.
# * flf2v: head = the distill ``LoraLoaderModelOnly`` and the single
#   ``CFGGuider`` is the consumer.
# * ic_lora_*: head = the IC-LoRA node, consumer = ``KSampler.model``.  The
#   ``GetICLoRAParameters`` branch keeps reading the IC-LoRA model directly.

_DEV_NEGATIVE = "pc game, console game, video game, cartoon, childish, ugly"

LTX_T2V = WorkflowSpec(
    id="ltx2_3_t2v",
    label="テキスト→動画 (t2v)",
    kind="video",
    relpath="video/ltx2.3/ltx2_3_t2v.json",
    output_node="75",
    requires=(),
    description=(
        "テキストだけから動画を生成する。開始フレームは不要で、画面に写るものは"
        "すべてプロンプトで決まる。"
    ),
    prompt_hint=(
        "No start frame exists: the prompt has to establish the subject, the"
        " set, the wardrobe and the framing as well as the motion. Never open"
        ' with "Starting from the given first frame".'
    ),
    accepts_start_image=False,
    inject={
        "prompt": T("267:266", "value", "PrimitiveStringMultiline"),
        "negative": T("267:247", "text", "CLIPTextEncode"),
        "width": T("267:257", "value", "PrimitiveInt"),
        "height": T("267:258", "value", "PrimitiveInt"),
        "duration": T("267:225", "value", "PrimitiveInt"),
        "fps": T("267:260", "value", "PrimitiveInt"),
        "frames_expr": T("267:277", "", "ComfyMathExpression"),
        "prompt_enhance": T("267:330", "value", "PrimitiveBoolean"),
        "save_prefix": T("75", "filename_prefix", "SaveVideo"),
    },
    seeds=(
        T("267:216", "noise_seed", "RandomNoise"),
        T("267:237", "noise_seed", "RandomNoise"),
    ),
    lora_chain=LoraChain(
        head="267:232",
        consumers=(
            T("267:213", "model", "CFGGuider"),
            T("267:231", "model", "CFGGuider"),
        ),
    ),
    constants={"prompt_enhance": False},
    notes="ltx-2.3-22b-dev-fp8 / 開始画像なし",
)

LTX_I2V = WorkflowSpec(
    id="tx2_3_i2v",
    label="画像→動画 (i2v)",
    kind="video",
    relpath="video/ltx2.3/tx2_3_i2v.json",
    output_node="75",
    requires=("image",),
    description=(
        "開始フレーム画像から動画を生成する。被写体とセットは画像が決め、"
        "プロンプトは動きを担当する。"
    ),
    prompt_hint=(
        "The start frame supplies the looks of the subject and the set — never"
        ' contradict it. Open the motion description with "Starting from the'
        ' given first frame, …" and spend the paragraph on movement, body and'
        " face reactions, camera and sound."
    ),
    accepts_start_image=True,
    image_label="開始フレーム",
    inject={
        "prompt": T("320:319", "value", "PrimitiveStringMultiline"),
        "negative": T("320:313", "text", "CLIPTextEncode"),
        "width": T("320:312", "value", "PrimitiveInt"),
        "height": T("320:299", "value", "PrimitiveInt"),
        "duration": T("320:301", "value", "PrimitiveInt"),
        "fps": T("320:300", "value", "PrimitiveInt"),
        "frames_expr": T("320:323", "", "ComfyMathExpression"),
        "prompt_enhance": T("320:328", "value", "PrimitiveBoolean"),
        "image": T("269", "image", "LoadImage"),
        "save_prefix": T("75", "filename_prefix", "SaveVideo"),
    },
    seeds=(
        T("320:276", "noise_seed", "RandomNoise"),
        T("320:277", "noise_seed", "RandomNoise"),
    ),
    lora_chain=LoraChain(
        head="320:285",
        consumers=(
            T("320:282", "model", "CFGGuider"),
            T("320:314", "model", "CFGGuider"),
        ),
    ),
    constants={"prompt_enhance": False},
    notes="ltx-2.3-22b-dev-fp8 / 音声は生成側で合成",
)

LTX_IA2V = WorkflowSpec(
    id="tx2_3_ia2v",
    label="画像+音声→動画 (ia2v)",
    kind="video",
    relpath="video/ltx2.3/tx2_3_ia2v.json",
    output_node="341",
    requires=("image", "audio"),
    description=(
        "開始フレーム画像と音声ファイルから動画を生成する。渡した音声がそのまま"
        "クリップの音声トラックになり、映像はその音に合わせて動く。"
    ),
    audio_role=(
        "指定した音声ファイルがクリップの音声トラックそのものになる"
        "（`audio_path` 必須）。セリフは音声側で決まるのでプロンプトには書かない。"
    ),
    prompt_hint=(
        "The user's audio file *is* the clip's soundtrack, so the picture has to"
        ' follow it. Open with "Starting from the given first frame, …" and'
        " describe motion that matches that audio (speech rhythm, music,"
        " moans). Do NOT write spoken lines in double quotes — the words come"
        " from the file, not from the prompt; keep the audio sentence about"
        " ambience and body sounds only."
    ),
    accepts_start_image=True,
    image_label="開始フレーム",
    inject={
        "prompt": T("340:319", "value", "PrimitiveStringMultiline"),
        "negative": T("340:314", "text", "CLIPTextEncode"),
        "width": T("340:330", "value", "PrimitiveInt"),
        "height": T("340:324", "value", "PrimitiveInt"),
        "duration": T("340:331", "value", "PrimitiveFloat"),
        "fps": T("340:323", "value", "PrimitiveInt"),
        "frames_expr": T("340:329", "", "ComfyMathExpression"),
        "prompt_enhance": T("340:349", "value", "PrimitiveBoolean"),
        "image": T("269", "image", "LoadImage"),
        "audio": T("276", "audio", "LoadAudio"),
        "save_prefix": T("341", "filename_prefix", "SaveVideo"),
    },
    seeds=(
        T("340:285", "noise_seed", "RandomNoise"),
        T("340:286", "noise_seed", "RandomNoise"),
    ),
    lora_chain=LoraChain(
        head="340:293",
        consumers=(
            T("340:290", "model", "CFGGuider"),
            T("340:315", "model", "CFGGuider"),
        ),
    ),
    constants={"prompt_enhance": False},
    notes="ltx-2.3-22b-dev-fp8 / 音声をラテントに焼き込む",
)

LTX_ID_LORA = WorkflowSpec(
    id="ltx2_3_id_lora",
    label="画像+参照音声→動画・リップシンク (ID-LoRA)",
    kind="video",
    relpath="video/ltx2.3/ltx2_3_id_lora.json",
    output_node="341",
    requires=("image", "audio"),
    description=(
        "開始フレーム画像とリファレンス音声から、talkvid ID-LoRA で口の動きが"
        "揃った喋りの動画を生成する。音声そのものはモデルが生成し、リファレンス"
        "音声は声質とリップシンクの参照に使う。"
    ),
    audio_role=(
        "リファレンス音声（`audio_path` 必須）。声質と口の動きの参照に使うだけで、"
        "出力される音声はモデルが生成するので、セリフはプロンプトの二重引用符で"
        "指定する。"
    ),
    prompt_hint=(
        "The clip is a lip-synced talking performance: the reference audio drives"
        ' the voice and the mouth. Open with "Starting from the given first'
        ' frame, …", describe the delivery (how she speaks, expression, head and'
        " body movement while talking) and keep the spoken lines short inside"
        " double quotes — the model synthesizes them verbatim."
    ),
    accepts_start_image=True,
    image_label="開始フレーム",
    inject={
        "prompt": T("340:319", "value", "PrimitiveStringMultiline"),
        "negative": T("340:314", "text", "CLIPTextEncode"),
        "width": T("340:330", "value", "PrimitiveInt"),
        "height": T("340:324", "value", "PrimitiveInt"),
        "duration": T("340:331", "value", "PrimitiveFloat"),
        "fps": T("340:323", "value", "PrimitiveInt"),
        "frames_expr": T("340:329", "", "ComfyMathExpression"),
        "image": T("269", "image", "LoadImage"),
        "audio": T("276", "audio", "LoadAudio"),
        "save_prefix": T("341", "filename_prefix", "SaveVideo"),
    },
    seeds=(
        T("340:285", "noise_seed", "RandomNoise"),
        T("340:286", "noise_seed", "RandomNoise"),
    ),
    lora_chain=LoraChain(
        head="340:293",
        consumers=(
            T("340:290", "model", "CFGGuider"),
            # the ID-LoRA -> LTXVReferenceAudio -> 2nd CFGGuider branch
            T("340:346", "model", "LoraLoaderModelOnly"),
        ),
    ),
    notes="ltx-2.3-22b-dev-fp8 + talkvid ID-LoRA / LTXVReferenceAudio",
)

LTX_FLF2V = WorkflowSpec(
    id="ltx2_3_flf2v",
    label="最初と最後のフレーム指定 (flf2v)",
    kind="video",
    relpath="video/ltx2.3/ltx2_3_flf2v.json",
    output_node="68",
    requires=("image", "end_image"),
    description=(
        "最初のフレームと最後のフレームの画像を指定し、その間の動きを補間する。"
    ),
    prompt_hint=(
        "Both the first and the last frame are given: describe the *transition*"
        " between them — the path the body, the wardrobe and the camera take"
        " from the opening pose to the closing one — and make sure the arc ends"
        " exactly where the last frame is. Do not describe motion that would"
        " leave the character somewhere else."
    ),
    accepts_start_image=True,
    image_label="最初のフレーム",
    inject={
        "prompt": T("129:128", "text", "CLIPTextEncode"),
        "negative": T("129:112", "text", "CLIPTextEncode"),
        "width": T("129:113", "value", "PrimitiveInt"),
        "height": T("129:98", "value", "PrimitiveInt"),
        "duration": T("129:102", "value", "PrimitiveInt"),
        "fps": T("129:114", "value", "PrimitiveInt"),
        "frames_expr": T("129:130", "", "ComfyMathExpression"),
        "image": T("31", "image", "LoadImage"),
        "end_image": T("39", "image", "LoadImage"),
        "save_prefix": T("68", "filename_prefix", "SaveVideo"),
    },
    seeds=(T("129:100", "noise_seed", "RandomNoise"),),
    lora_chain=LoraChain(
        head="129:300",
        consumers=(T("129:116", "model", "CFGGuider"),),
    ),
    notes="ltx-2.3-22b-dev-fp8 + distilled-1.1 LoRA",
)

LTX_IC_LORA_IMAGE = WorkflowSpec(
    id="ltx2_3_ic_lora_image",
    label="リファレンスシート (IC-LoRA)",
    kind="video",
    relpath="video/ltx2.3/ltx2_3_ic_lora_image.json",
    output_node="68",
    requires=("image",),
    description=(
        "複数カットを並べたリファレンスシート画像から動画を生成する"
        "（Ingredients IC-LoRA）。画像は開始フレームではなく見た目の参照なので、"
        "full モードの 2 段目には使えない。"
    ),
    prompt_hint=(
        "The image input is a multi-panel reference sheet, not a first frame: it"
        " only fixes how the character and the props look. Describe the whole"
        " shot from scratch (subject, set, framing and motion, as in t2v) and"
        ' never write "Starting from the given first frame".'
    ),
    # the image is a multi-panel reference sheet, not a first frame
    accepts_start_image=False,
    image_label="リファレンスシート画像",
    inject={
        # prompt enhance is disabled, so the literal on_false branch is used
        "prompt": T("129:211", "on_false", "ComfySwitchNode"),
        "negative": T("129:112", "text", "CLIPTextEncode"),
        # resolution is taken from the padded reference sheet
        "width": T("722", "target_width", "ResizeAndPadImage"),
        "height": T("722", "target_height", "ResizeAndPadImage"),
        "duration": T("715", "value", "PrimitiveInt"),
        "fps": T("716", "value", "PrimitiveInt"),
        "frames_expr": T("717", "", "ComfyMathExpression"),
        "prompt_enhance": T("129:212", "value", "PrimitiveBoolean"),
        "image": T("724", "image", "LoadImage"),
        "save_prefix": T("68", "filename_prefix", "SaveVideo"),
    },
    seeds=(T("129:704", "seed", "KSampler"),),
    lora_chain=LoraChain(
        head="129:195",
        consumers=(T("129:704", "model", "KSampler"),),
    ),
    constants={"prompt_enhance": False},
    notes="ltx-2.3-22b-dev-fp8 + distilled-1.1 LoRA + ingredients IC-LoRA",
)

LTX_IC_LORA_MOTION = WorkflowSpec(
    id="ltx2_3_ic_lora_motion",
    label="参照動画からモーション転写 (IC-LoRA + MoGe)",
    kind="video",
    relpath="video/ltx2.3/ltx2_3_ic_lora_motion.json",
    output_node="68",
    requires=("image", "video"),
    description=(
        "参照動画のカメラワークとモーションを MoGe 深度経由で転写し、開始フレーム"
        "画像の被写体で動画を生成する（Union Control IC-LoRA）。クリップの長さは"
        "参照動画から切り出す区間の長さになる。"
    ),
    prompt_hint=(
        "Camera work and the choreography's timing come from the reference"
        " video, not from the prompt: do not describe camera movement or the"
        " tempo of the action. Spend the paragraph on who the subject is, the"
        " set, the wardrobe, expressions, materials and the audio, and keep the"
        " motion wording compatible with what the reference clip does."
    ),
    accepts_start_image=True,
    image_label="開始フレーム",
    inject={
        "prompt": T("129:211", "on_false", "ComfySwitchNode"),
        "negative": T("129:112", "text", "CLIPTextEncode"),
        "width": T("129:113", "value", "PrimitiveInt"),
        "height": T("129:98", "value", "PrimitiveInt"),
        "fps": T("129:114", "value", "PrimitiveInt"),
        # frame count follows the reference clip: the slice length is the knob
        "duration": T("692", "duration", "Video Slice"),
        "prompt_enhance": T("129:212", "value", "PrimitiveBoolean"),
        "image": T("200", "image", "LoadImage"),
        "video": T("199", "file", "LoadVideo"),
        "save_prefix": T("68", "filename_prefix", "SaveVideo"),
    },
    seeds=(T("129:704", "seed", "KSampler"),),
    lora_chain=LoraChain(
        head="129:195",
        consumers=(T("129:704", "model", "KSampler"),),
    ),
    constants={"prompt_enhance": False},
    notes="ltx-2.3-22b-dev-fp8 + distilled-1.1 LoRA + union-control IC-LoRA / MoGe 深度",
)


SPECS: tuple[WorkflowSpec, ...] = (
    KREA2_TURBO,
    LTX_T2V,
    LTX_I2V,
    LTX_IA2V,
    LTX_ID_LORA,
    LTX_FLF2V,
    LTX_IC_LORA_IMAGE,
    LTX_IC_LORA_MOTION,
)

BY_ID: dict[str, WorkflowSpec] = {spec.id: spec for spec in SPECS}

DEFAULT_IMAGE_WORKFLOW = KREA2_TURBO.id
#: the closest successor of the old combined graph (image + reference audio +
#: talkvid ID-LoRA), so existing jobs and agent plans keep their semantics
DEFAULT_VIDEO_WORKFLOW = LTX_ID_LORA.id

#: JobCreate field that carries each logical input
INPUT_FIELDS: dict[str, str] = {
    "image": "source_image",
    "audio": "audio_path",
    "end_image": "end_image",
    "video": "reference_video",
}

#: Japanese label of every logical input except ``image``, whose meaning differs
#: per workflow (see :attr:`WorkflowSpec.image_label`).
INPUT_LABELS: dict[str, str] = {
    "audio": "音声ファイル",
    "end_image": "最後のフレーム画像",
    "video": "参照動画",
}

#: audio handling of a workflow that has no audio input at all
GENERATED_AUDIO = (
    "モデルが映像と同時に音声（環境音・声・効果音）も生成する。`audio_path` は"
    "使わないので指定しない。"
)


def input_label(spec: WorkflowSpec, name: str) -> str:
    """Japanese label of one logical input of ``spec``."""
    return spec.image_label if name == "image" else INPUT_LABELS.get(name, name)


def image_specs() -> list[WorkflowSpec]:
    return [spec for spec in SPECS if spec.kind == "image"]


def video_specs() -> list[WorkflowSpec]:
    return [spec for spec in SPECS if spec.kind == "video"]


def get_spec(workflow_id: str, kind: WorkflowKind | None = None) -> WorkflowSpec:
    spec = BY_ID.get(workflow_id)
    if spec is None:
        raise WorkflowSpecError(f"unknown workflow: {workflow_id!r}")
    if kind is not None and spec.kind != kind:
        raise WorkflowSpecError(f"workflow {workflow_id!r} is not a {kind} workflow")
    return spec


def get_video_spec(workflow_id: str | None) -> WorkflowSpec:
    return get_spec(workflow_id or DEFAULT_VIDEO_WORKFLOW, "video")


def get_image_spec(workflow_id: str | None) -> WorkflowSpec:
    return get_spec(workflow_id or DEFAULT_IMAGE_WORKFLOW, "image")


# --------------------------------------------------------------------------
# catalog (the manifests as the Grok system prompts see them)
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class CatalogEntry:
    """One workflow as the system prompts describe it (SPEC §4.3 / AGENT-MODE §3.1).

    Everything here is derived from the :class:`WorkflowSpec`, so the prompts,
    the UI and the validators cannot drift apart.
    """

    id: str
    label: str
    kind: WorkflowKind
    description: str
    #: ``(JobCreate field, 日本語ラベル)`` of every input the workflow needs
    required_inputs: tuple[tuple[str, str], ...]
    #: ``(JobCreate field, 日本語ラベル)`` of inputs it accepts but does not need
    optional_inputs: tuple[tuple[str, str], ...]
    #: can it be the second stage of a full (image -> video) job, and therefore
    #: also the target of a ``continue``?
    accepts_start_image: bool
    #: how ``audio_path`` is used (never empty)
    audio: str
    prompt_hint: str
    notes: str

    @property
    def required_fields(self) -> tuple[str, ...]:
        return tuple(field for field, _ in self.required_inputs)


def catalog_entry(spec: WorkflowSpec) -> CatalogEntry:
    """Describe one workflow for the system prompts."""
    optional = tuple(
        name for name in INPUT_FIELDS if spec.supports(name) and name not in spec.requires
    )
    return CatalogEntry(
        id=spec.id,
        label=spec.label,
        kind=spec.kind,
        description=spec.description,
        required_inputs=tuple(
            (INPUT_FIELDS[name], input_label(spec, name)) for name in spec.requires
        ),
        optional_inputs=tuple(
            (INPUT_FIELDS[name], input_label(spec, name)) for name in optional
        ),
        accepts_start_image=spec.accepts_start_image,
        audio=spec.audio_role or GENERATED_AUDIO,
        prompt_hint=spec.prompt_hint,
        notes=spec.notes,
    )


def video_catalog() -> list[CatalogEntry]:
    """Every selectable video workflow, in UI / prompt order."""
    return [catalog_entry(spec) for spec in video_specs()]


# --------------------------------------------------------------------------
# template loading
# --------------------------------------------------------------------------

_cache: dict[str, Workflow] = {}


def load_template(spec: WorkflowSpec | str, *, use_cache: bool = True) -> Workflow:
    """Read one API-format template from ``workflow/``.

    Templates are read-only, and every consumer deep-copies before mutating, so
    they are cached per process.  ``use_cache=False`` forces a re-read (the
    tests and the health check use it to pick up edits).
    """
    resolved = spec if isinstance(spec, WorkflowSpec) else get_spec(spec)
    if use_cache and resolved.id in _cache:
        return _cache[resolved.id]
    with open(resolved.path, encoding="utf-8") as fh:
        template = json.load(fh)
    if not isinstance(template, dict):
        raise WorkflowSpecError(f"{resolved.path} is not an API-format workflow")
    if use_cache:
        _cache[resolved.id] = template
    return template


def clear_cache() -> None:
    _cache.clear()


# --------------------------------------------------------------------------
# manifest validation
# --------------------------------------------------------------------------

def validate_spec(spec: WorkflowSpec, template: Workflow | None = None) -> list[str]:
    """Problems found in ``spec`` against its template (empty list == fine)."""
    problems: list[str] = []
    try:
        tpl = template if template is not None else load_template(spec)
    except (OSError, ValueError) as exc:
        return [f"{spec.id}: template unreadable: {exc}"]

    def check(target: Target, origin: str) -> None:
        node = tpl.get(target.node_id)
        if not isinstance(node, dict):
            problems.append(f"{spec.id}.{origin}: node {target.node_id!r} is missing")
            return
        actual = node.get("class_type")
        if actual != target.class_type:
            problems.append(
                f"{spec.id}.{origin}: node {target.node_id!r} is {actual!r},"
                f" expected {target.class_type!r}"
            )
            return
        if target.field and target.field not in (node.get("inputs") or {}):
            problems.append(
                f"{spec.id}.{origin}: {target.node_id}.{target.field} does not exist"
            )

    for name, target in spec.inject.items():
        check(target, name)
    for index, target in enumerate(spec.seeds):
        check(target, f"seeds[{index}]")

    if spec.lora_chain is not None:
        chain = spec.lora_chain
        if not chain.consumers:
            problems.append(f"{spec.id}.lora_chain: no consumers declared")
        for index, consumer in enumerate(chain.consumers):
            check(consumer, f"lora_chain.consumers[{index}]")
            # the chain is spliced into an existing edge, so every consumer must
            # currently read the head (directly or through the placeholders it
            # replaces) — otherwise the manifest would silently rewire the graph
            node = tpl.get(consumer.node_id)
            link = (node.get("inputs") or {}).get(consumer.field) if isinstance(node, dict) else None
            if not isinstance(link, list) or len(link) != 2:
                problems.append(
                    f"{spec.id}.lora_chain.consumers[{index}]:"
                    f" {consumer.key} is not connected to a node"
                )
            elif link[0] != chain.head and link[0] not in chain.placeholders:
                problems.append(
                    f"{spec.id}.lora_chain.consumers[{index}]: {consumer.key} reads"
                    f" {link[0]!r}, expected the chain head {chain.head!r}"
                )
        if chain.head not in tpl:
            problems.append(
                f"{spec.id}.lora_chain.head: node {chain.head!r} is missing"
            )
        for node_id in chain.placeholders:
            node = tpl.get(node_id)
            if not isinstance(node, dict):
                problems.append(
                    f"{spec.id}.lora_chain: placeholder {node_id!r} is missing"
                )
            elif node.get("class_type") != "LoraLoaderModelOnly":
                problems.append(
                    f"{spec.id}.lora_chain: placeholder {node_id!r} is"
                    f" {node.get('class_type')!r}, expected 'LoraLoaderModelOnly'"
                )

    if spec.output_node not in tpl:
        problems.append(f"{spec.id}.output_node: node {spec.output_node!r} is missing")

    for name in spec.requires:
        if name not in spec.inject:
            problems.append(f"{spec.id}: requires {name!r} but has no injection point")
    if spec.accepts_start_image and "image" not in spec.inject:
        problems.append(f"{spec.id}: accepts_start_image but has no image input")

    # the catalog embedded in the Grok system prompts is generated from these,
    # so a new workflow must document itself (SPEC §4.3 / AGENT-MODE §3.1)
    if not spec.description.strip():
        problems.append(f"{spec.id}: description is empty")
    if spec.kind == "video" and not spec.prompt_hint.strip():
        problems.append(f"{spec.id}: prompt_hint is empty")
    if spec.audio_role and "audio" not in spec.inject:
        problems.append(f"{spec.id}: has an audio_role but no audio input")
    if "audio" in spec.inject and not spec.audio_role.strip():
        problems.append(f"{spec.id}: has an audio input but no audio_role")

    return problems


def validate_specs(*, use_cache: bool = True) -> list[str]:
    """Validate every manifest. Used by the health check and the test suite."""
    problems: list[str] = []
    for spec in SPECS:
        try:
            template = load_template(spec, use_cache=use_cache)
        except (OSError, ValueError, WorkflowSpecError) as exc:
            problems.append(f"{spec.id}: template unreadable: {exc}")
            continue
        problems += validate_spec(spec, template)
    return problems
