"""Workflow template registry and per-template injection manifests (SPEC §3).

The app ships a folder of independent ComfyUI API-format graphs under
``workflow/``: four image workflows (Krea 2 turbo, Anima, Z-Image turbo and
Qwen-Image Edit 2511), seven LTX 2.3 video workflows and two audio workflows
(ACE-Step 1.5 XL and Stable Audio 3 Medium).  Each one is described here by a
:class:`WorkflowSpec` whose ``inject`` map names every node/field the app writes
to.

Audio is a **stand-alone** kind: an audio job runs exactly one graph and is
never chained with the image / video stages (SPEC §2 knows nothing about it).

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

WorkflowKind = Literal["image", "video", "audio"]

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


#: 選択式フィールドの「自動決定」の種類。``audio_duration`` は入力音声の実長から
#: 決める（wan_dancer の尺）。空文字は自動なし（既定値をそのまま使う）。
AutoSource = Literal["", "audio_duration"]


def _is_number(value: str) -> bool:
    try:
        float(value)
    except ValueError:
        return False
    return True


@dataclass(frozen=True)
class SelectSpec:
    """One *selectable* injection point: the app offers a fixed list of strings.

    テンプレートが自由記述ではなくコンボボックスで挙動を決めるワークフロー
    （wan_dancer の踊りの種類など）のための汎用の仕組み。宣言すると

    * 生成フォームがこのリストからの ``select`` を自動で描画し、
    * ジョブは ``selects`` にその値を持ち（リスト外は 422）、
    * エージェントのカタログにも選択肢が載る。

    ComfyUI の ``CustomCombo`` は選んだ文字列（``choice``）と 0 始まりの番号
    （``index``）の両方を持ち、**グラフが読むのは番号側**（``choice`` は表示用）。
    そのため :attr:`index_field` にも同じ選択の番号を書き込む。

    ``numeric_target`` は「選んだ値を数値としても別のノードに入れる」場合に使う
    （wan_dancer の尺はコンボと ``TrimAudioDuration`` の両方に入れないと、音声だけ
    25 秒で切られる）。
    """

    #: UI の見出し（日本語）
    label: str
    #: 選べる値。**テンプレートの option と同じ文字列**であること
    choices: tuple[str, ...]
    #: 選んだ文字列を書き込む先（``CustomCombo.choice`` など）
    target: Target
    #: 未指定のときに使う値（空なら ``choices[0]``）
    default: str = ""
    #: 選択の番号を書き込むフィールド（``CustomCombo`` は必須。空なら書かない）
    index_field: str = "index"
    #: 選んだ値を数値としても入れる先（``float`` に変換して書き込む）
    numeric_target: Target | None = None
    #: 未指定のときの自動決定（:data:`AutoSource`）
    auto: AutoSource = ""
    #: UI とエージェント向けの一言（省略時の挙動など）
    hint: str = ""

    @property
    def fallback(self) -> str:
        """未指定・不正な値のときに使う値。"""
        if self.default and self.default in self.choices:
            return self.default
        return self.choices[0] if self.choices else ""

    def round_up(self, value: float) -> str:
        """``value`` 以上で最小の（数値として読める）選択肢。無ければ最大のもの。

        wan_dancer の尺を音声の実長から決めるのに使う: 曲が途中で切れないよう
        切り上げ、選択肢の上限で止める。
        """
        numeric = sorted(
            (float(choice), choice)
            for choice in self.choices
            if _is_number(choice)
        )
        if not numeric:
            return self.fallback
        for threshold, choice in numeric:
            if value <= threshold + 1e-6:
                return choice
        return numeric[-1][1]

    def index_of(self, choice: str) -> int:
        """``choice`` の 0 始まりの番号（不明なら既定値の番号）。"""
        try:
            return self.choices.index(choice)
        except ValueError:
            return self.choices.index(self.fallback) if self.fallback in self.choices else 0


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


#: Model families, one per ``workflow/<kind>/<folder>``.  A registered LoRA is
#: trained for exactly one family, so the family decides which image workflow a
#: LoRA may be used with (SPEC §3.4).
ImageFamily = Literal["krea2", "anima", "z-image", "qwen-image"]

#: 日本語ラベル（設定画面の LoRA フォームと一覧バッジ）
FAMILY_LABELS: dict[str, str] = {
    "krea2": "Krea 2",
    "anima": "Anima",
    "z-image": "Z-Image",
    "qwen-image": "Qwen-Image Edit",
    "ltx2.3": "LTX 2.3",
    "wan": "Wan 2.2",
    "ace-step": "ACE-Step 1.5",
    "stable-audio": "Stable Audio 3",
}

#: LoRA registrations default to this family (the only image workflow that
#: existed before the selector), so the DB migration can backfill with it.
DEFAULT_FAMILY = "krea2"


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
    #: model family (= the ``workflow/<kind>/<folder>`` name).  Image LoRAs are
    #: only offered for the family of the selected image workflow; the video
    #: templates all share the ``ltx2.3`` family and ignore it.
    family: str = DEFAULT_FAMILY
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
    #: audio workflows only: the clip length the model supports, in seconds.
    #: ``duration`` outside ``[min_duration, max_duration]`` is rejected before
    #: the job is queued (0.0 == no limit declared).
    min_duration: float = 0.0
    max_duration: float = 0.0
    #: audio workflows only: the length the UI / the agent start from
    default_duration: float = 0.0
    #: can this workflow be the second stage of a full (image -> video) job?
    accepts_start_image: bool = False
    #: UI label of the primary image input
    image_label: str = "開始フレーム"
    #: 動画の幅・高さを丸める単位。LTX の latent は 32px 単位なので、32 の倍数で
    #: ないと端が数 px 欠ける。さらに union-control IC-LoRA は参照動画を 0.5 倍
    #: 解像度で latent エンコードするため、その半分（= 元の 64 の倍数）でないと
    #: latent の形が合わずに実行時に落ちる。
    resolution_multiple: int = 8
    lora_chain: LoraChain | None = None
    notes: str = ""
    seeds: tuple[Target, ...] = ()
    #: extra targets keyed by logical name that are always forced to a constant
    constants: dict[str, Any] = field(default_factory=dict)
    #: 選択式フィールド（論理名 -> :class:`SelectSpec`）。宣言のないワークフロー
    #: では空なので、フォームにもジョブにも何も増えない。
    selects: dict[str, SelectSpec] = field(default_factory=dict)
    #: ``video_prompt`` が必須か。プロンプトをコンボから組み立てるワークフロー
    #: （wan_dancer）は False で、書かれた場合だけ注入する。
    prompt_required: bool = True

    @property
    def path(self):
        return WORKFLOW_DIR / self.relpath

    def targets(self) -> Iterable[Target]:
        yield from self.inject.values()
        yield from self.seeds
        for select in self.selects.values():
            yield select.target
            if select.numeric_target is not None:
                yield select.numeric_target
        if self.lora_chain is not None:
            yield from self.lora_chain.consumers

    def select(self, name: str) -> SelectSpec | None:
        return self.selects.get(name)

    def supports(self, name: str) -> bool:
        return name in self.inject

    def target(self, name: str) -> Target | None:
        return self.inject.get(name)


# --------------------------------------------------------------------------
# image: workflow/image/*/*.json
# --------------------------------------------------------------------------

KREA2_TURBO = WorkflowSpec(
    id="krea2_turbo",
    label="Krea 2 turbo",
    kind="image",
    family="krea2",
    relpath="image/krea2/krea2_turbo.json",
    output_node="29",
    description=(
        "Text-to-image. Writes one still from `image_prompt` alone; the"
        " resolution comes from `aspect_ratio` + `megapixels`. Use it for"
        " `mode: \"image_only\"` and as the first stage of `mode: \"full\"`."
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

ANIMA = WorkflowSpec(
    id="anima",
    label="Anima",
    kind="image",
    family="anima",
    relpath="image/anima/anima.json",
    output_node="46",
    description=(
        "Text-to-image, anime / illustration oriented (Anima base). Same shape"
        " as krea2: `image_prompt` only, resolution from `aspect_ratio` +"
        " `megapixels`. Usable for `mode: \"image_only\"` and as the first stage"
        " of `mode: \"full\"`."
    ),
    inject={
        "aspect_ratio": T("91", "aspect_ratio", "ResolutionSelector"),
        "megapixels": T("91", "megapixels", "ResolutionSelector"),
        "prompt": T("90:77", "text", "CLIPTextEncode"),
        "seed": T("90:76", "seed", "KSampler"),
        "save_prefix": T("46", "filename_prefix", "SaveImage"),
    },
    lora_chain=LoraChain(
        head="90:78",
        placeholders=("90:83",),
        consumers=(T("90:76", "model", "KSampler"),),
    ),
    notes="anima-base-v1.0 / negative は既定値のまま",
)

Z_IMAGE_TURBO = WorkflowSpec(
    id="z_image_turbo",
    label="Z-Image turbo",
    kind="image",
    family="z-image",
    relpath="image/z-image/z_image_turbo.json",
    output_node="9",
    description=(
        "Text-to-image, 8-step distilled Tongyi Z-Image turbo. `image_prompt`"
        " only; the template has no ResolutionSelector, so the app computes the"
        " width / height from `aspect_ratio` + `megapixels` itself. Usable for"
        " `mode: \"image_only\"` and as the first stage of `mode: \"full\"`."
    ),
    inject={
        # no ResolutionSelector in this graph: the app injects plain integers
        "width": T("57:13", "width", "EmptySD3LatentImage"),
        "height": T("57:13", "height", "EmptySD3LatentImage"),
        "prompt": T("57:27", "text", "CLIPTextEncode"),
        "seed": T("57:3", "seed", "KSampler"),
        "save_prefix": T("9", "filename_prefix", "SaveImage"),
    },
    lora_chain=LoraChain(
        head="57:28",
        placeholders=("57:63",),
        consumers=(T("57:11", "model", "ModelSamplingAuraFlow"),),
    ),
    notes="z_image_turbo_bf16 / 8 steps・CFG 1（ネガティブは ConditioningZeroOut）",
)

QWEN_IMAGE_EDIT = WorkflowSpec(
    id="qwen_image_edit_2511",
    label="Qwen-Image Edit 2511",
    kind="image",
    family="qwen-image",
    relpath="image/qwen-image/qwen_image_edit_2511.json",
    output_node="195",
    requires=("image",),
    description=(
        "Image **editing**, not text-to-image: it rewrites the picture given in"
        " `source_image` following the instruction in `image_prompt`, so"
        " `source_image` is REQUIRED in every mode that runs the image stage"
        " (including `mode: \"full\"`, where the edited still then becomes the"
        " video's start frame). The output resolution is derived from the input"
        " image (FluxKontextImageScale), so `aspect_ratio` / `megapixels` are"
        " ignored by this workflow. Write `image_prompt` as an edit instruction"
        ' ("change X to Y, keep everything else unchanged"), never as a full'
        " scene description."
    ),
    image_label="編集元画像",
    inject={
        "image": T("41", "image", "LoadImage"),
        "prompt": T("170:151", "prompt", "TextEncodeQwenImageEditPlus"),
        "seed": T("170:169", "seed", "KSampler"),
        "save_prefix": T("195", "filename_prefix", "SaveImageAdvanced"),
    },
    # The template's own `170:153` LoraLoaderModelOnly is the Lightning 4-steps
    # speed LoRA and must stay, so it is *not* a placeholder.  The user chain is
    # spliced in front of it — at the CFGNorm output both branches of the
    # `170:163` Switch (Model) read — so the user LoRA applies whether the
    # 4-steps LoRA is switched on or off.
    lora_chain=LoraChain(
        head="170:152",
        consumers=(
            T("170:153", "model", "LoraLoaderModelOnly"),
            T("170:163", "on_false", "ComfySwitchNode"),
        ),
    ),
    notes="qwen_image_edit_2511 + Lightning 4steps LoRA / 解像度は入力画像から自動",
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
    family="ltx2.3",
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
    resolution_multiple=32,
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
    family="ltx2.3",
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
    resolution_multiple=32,
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
    family="ltx2.3",
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
    resolution_multiple=32,
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
    family="ltx2.3",
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
    resolution_multiple=32,
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
    family="ltx2.3",
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
    resolution_multiple=32,
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
    family="ltx2.3",
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
        " only fixes how the character and the props look. Write the prompt in"
        ' the two parts this IC-LoRA expects. Start with "Reference sheet:"'
        " followed by one short clause per panel, in the order the panels are"
        " laid out (left to right, top to bottom), naming what each panel shows"
        ' — then "Generated video:" followed by the shot itself (subject, set,'
        " framing, motion and audio, written from scratch as in t2v). Never"
        ' write "Starting from the given first frame".'
    ),
    # the image is a multi-panel reference sheet, not a first frame
    accepts_start_image=False,
    image_label="リファレンスシート画像",
    resolution_multiple=32,
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
    family="ltx2.3",
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
    resolution_multiple=64,
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



# --------------------------------------------------------------------------
# video: workflow/video/wan/*.json
# --------------------------------------------------------------------------

#: 尺の選択肢（テンプレートの Duration コンボと同じ並び）。番号がそのまま
#: WanDancerPadKeyframesList の num_segments（= 5 秒単位のセグメント数）になる。
WAN_DURATIONS: tuple[str, ...] = ("5", "10", "15", "20", "25", "30")

WAN_DANCER = WorkflowSpec(
    id="wan_dancer",
    label="画像+音声→ダンス動画 (Wan Dancer)",
    kind="video",
    family="wan",
    relpath="video/wan/wan_dancer.json",
    output_node="699",
    requires=("image", "audio"),
    description=(
        "開始フレーム画像と音楽ファイルから、その曲に合わせて踊る動画を生成する"
        "（Wan 2.2 WanDancerVideo）。渡した音声がそのままクリップの音声トラックに"
        "なり、映像はビートに合わせて踊る。プロンプトは自由記述ではなく"
        "「踊りの種類」「動きの大きさ」の選択で決まり、尺は音声の長さに自動で"
        "合わせる（5〜30 秒）。"
    ),
    audio_role=(
        "指定した音声ファイルがクリップの音声トラックそのものになる"
        "（`audio_path` 必須）。踊りはこの曲に合わせて付くので、"
        "リズムのはっきりした曲を渡す。尺も既定ではこの音声の長さに合わせる。"
    ),
    prompt_hint=(
        "This workflow builds its own Chinese prompt from the `dance_style` and"
        " `motion_amplitude` selections, so **`video_prompt` is optional** —"
        " leave it out unless the user asked for something the two selections"
        " cannot express. When you do set it, write the Wan-style Chinese"
        " template string; keep the `<dance style>` placeholder in it if you"
        " want the selected dance to be substituted"
        ' (e.g. "一个人正在跳舞，舞蹈种类是<dance style>，在霓虹灯的舞台上").'
    ),
    accepts_start_image=True,
    image_label="開始フレーム",
    prompt_required=False,
    inject={
        # Global 側のテンプレ文（<dance style> が選択値に置換される）
        "prompt": T("696:685", "string", "StringReplace"),
        "negative": T("696:629", "text", "CLIPTextEncode"),
        "width": T("696:398", "value", "PrimitiveInt"),
        "height": T("696:400", "value", "PrimitiveInt"),
        "image": T("547", "image", "LoadImage"),
        "audio": T("548", "audio", "LoadAudio"),
        "save_prefix": T("699", "filename_prefix", "SaveVideo"),
    },
    selects={
        "dance_style": SelectSpec(
            label="踊りの種類",
            choices=(
                "Chinese Classic Dance 古典舞",
                "K-Pop 韩舞",
                "Street Dance 街舞",
                "Latin Dance 拉丁舞",
                "Tap Dance 踢踏舞",
            ),
            target=T("696:695", "choice", "CustomCombo"),
            default="K-Pop 韩舞",
            hint="プロンプトの <dance style> に入る踊りの種類。",
        ),
        "motion_amplitude": SelectSpec(
            label="動きの大きさ",
            choices=("low 低", "medium 中等", "high 高", "max 最大"),
            target=T("696:694", "choice", "CustomCombo"),
            default="medium 中等",
            hint="大きいほど激しく動くが、破綻もしやすい。",
        ),
        "duration": SelectSpec(
            label="尺（秒）",
            choices=WAN_DURATIONS,
            target=T("696:700", "choice", "CustomCombo"),
            # 音声の長さが測れなかったときの落としどころ（真ん中）
            default="15",
            # 音声もこの秒数で切る（既定の 25 秒固定だと曲が途中で切れる）
            numeric_target=T("696:494", "duration", "TrimAudioDuration"),
            auto="audio_duration",
            hint="省略すると音声の長さに合わせて 5〜30 秒から自動で決める。",
        ),
    },
    seeds=(
        T("696:654", "noise_seed", "RandomNoise"),
        T("696:667", "noise_seed", "SamplerCustom"),
    ),
    notes=(
        "wan2.2 global/local の 2 段 UNet + lightx2v LoRA / 既定 720x1280 /"
        " ユーザー LoRA を挿すチェーンは持たない"
    ),
)


# --------------------------------------------------------------------------
# audio: workflow/audio/*.json
# --------------------------------------------------------------------------
#
# Audio workflows are stand-alone: they never take a start frame, never produce
# one, and are not part of the image -> video chain.  They have no LoRA chain
# either (neither template carries a LoRA loader), so ``lora_chain`` stays None
# and the LoRA pickers simply never offer them.

ACE_STEP_1_5 = WorkflowSpec(
    id="ace_step1_5_xl_sft",
    label="ACE-Step 1.5 XL（音楽・歌もの）",
    kind="audio",
    family="ace-step",
    relpath="audio/ace_step1_5_xl_sft.json",
    output_node="107",
    description=(
        "Song generation: writes a full music track, with **vocals** when"
        " `lyrics` are given and an instrumental when they are not."
        " `audio_prompt` is the *caption* of the track (style, instruments,"
        " production, voice); `bpm` / `keyscale` / `language` steer the"
        " arrangement. Use it whenever the user wants music or a song."
    ),
    prompt_hint=(
        "A caption of the track, not a scene: style / genre, mood and"
        " atmosphere, the instruments and how each one sounds, production"
        " style, tempo feel and — when there are lyrics — the voice. Comma"
        " separated keywords and plain prose both work; be specific rather"
        " than vague and keep it consistent with `lyrics`. The words to sing"
        " go in `lyrics`, never in `audio_prompt`."
    ),
    # 公式スペックは 10 秒〜600 秒（github.com/ace-step/ACE-Step-1.5 README）。
    # ComfyUI ノード側の受付幅（duration 0-2000 / seconds 1-1000）はもっと広いが、
    # モデルが品質を保証するのは 600 秒まで。
    min_duration=10.0,
    max_duration=600.0,
    default_duration=120.0,
    inject={
        # tags = the prompt describing the track
        "prompt": T("94", "tags", "TextEncodeAceStepAudio1.5"),
        "lyrics": T("94", "lyrics", "TextEncodeAceStepAudio1.5"),
        # the length lives in two places and must stay in sync: the conditioning
        # (94.duration) and the empty latent the sampler fills (98.seconds)
        "duration": T("94", "duration", "TextEncodeAceStepAudio1.5"),
        "latent_seconds": T("98", "seconds", "EmptyAceStep1.5LatentAudio"),
        "bpm": T("94", "bpm", "TextEncodeAceStepAudio1.5"),
        "keyscale": T("94", "keyscale", "TextEncodeAceStepAudio1.5"),
        "language": T("94", "language", "TextEncodeAceStepAudio1.5"),
        # one PrimitiveInt feeds both KSampler.seed and 94.seed
        "seed": T("109", "value", "PrimitiveInt"),
        "save_prefix": T("107", "filename_prefix", "SaveAudioMP3"),
    },
    notes="acestep_v1.5_xl_sft / 出力 MP3・歌詞ありでボーカル、なしでインスト",
)

STABLE_AUDIO_3 = WorkflowSpec(
    id="stable_audio_3_medium_base",
    label="Stable Audio 3 Medium（効果音・環境音・音楽）",
    kind="audio",
    family="stable-audio",
    relpath="audio/stable_audio_3_medium_base.json",
    output_node="19",
    description=(
        "General-purpose audio: sound effects, one-shots, single instrument"
        " takes and instrumental music. `audio_prompt` is a short natural"
        " description, `audio_category` picks the built-in prompt template"
        " (Music / Instrument / SFX / One-shot) and `reprompt` lets a local LLM"
        " expand the description before it is encoded. It does **not** sing:"
        " use ACE-Step for songs with lyrics."
    ),
    prompt_hint=(
        "One short natural-language description of the sound itself — what is"
        " heard, then genre / instruments / mood / tempo (music) or the source,"
        " the material and the space (SFX). No lyrics, no tag soup."
    ),
    # Stable Audio 3 medium は 6 分 20 秒（380 秒）まで
    # （github.com/Stability-AI/stable-audio-3 docs/guides/prompting.md）。
    min_duration=1.0,
    max_duration=380.0,
    default_duration=60.0,
    inject={
        "prompt": T("52:31", "value", "PrimitiveStringMultiline"),
        # EmptyLatentAudio.seconds reads this float, so one injection is enough
        "duration": T("52:36", "value", "PrimitiveFloat"),
        "audio_category": T("52:43", "choice", "CustomCombo"),
        "category_index": T("52:43", "index", "CustomCombo"),
        "reprompt": T("52:35", "value", "PrimitiveBoolean"),
        "seed": T("52:3", "seed", "KSampler"),
        "save_prefix": T("19", "filename_prefix", "SaveAudioMP3"),
    },
    # `index: 0` means "use the `choice` widget"; pinning it keeps the category
    # injection authoritative whatever the node's index handling does.
    constants={"category_index": 0},
    notes="stable_audio_3_medium_base / 出力 MP3・カテゴリ別の内蔵プロンプト展開あり",
)

#: the categories the Stable Audio template's CustomCombo offers
AUDIO_CATEGORIES: tuple[str, ...] = ("Music", "Instrument", "SFX", "One-shot")

# --- ACE-Step 1.5 enums (comfy_extras/nodes_ace.py, TextEncodeAceStepAudio1.5)
# The node declares these as COMBO widgets, so a value outside the list fails
# the whole prompt on ComfyUI's side.  Mirrored here so the form, the agent
# catalog and the job validator all offer / accept exactly the same set.

#: 17 roots x {major, minor}
KEYSCALES: tuple[str, ...] = tuple(
    f"{root} {quality}"
    for root in (
        "C", "C#", "Db", "D", "D#", "Eb", "E", "F", "F#", "Gb", "G", "G#",
        "Ab", "A", "A#", "Bb", "B",
    )
    for quality in ("major", "minor")
)

#: 50 languages plus ``unknown`` (auto / instrumental)
LANGUAGES: tuple[str, ...] = (
    "ar", "az", "bg", "bn", "ca", "cs", "da", "de", "el", "en", "es", "fa",
    "fi", "fr", "he", "hi", "hr", "ht", "hu", "id", "is", "it", "ja", "ko",
    "la", "lt", "ms", "ne", "nl", "no", "pa", "pl", "pt", "ro", "ru", "sa",
    "sk", "sr", "sv", "sw", "ta", "te", "th", "tl", "tr", "uk", "ur", "vi",
    "yue", "zh", "unknown",
)

#: the node's own INT bounds for ``bpm``
BPM_RANGE: tuple[int, int] = (10, 300)


SPECS: tuple[WorkflowSpec, ...] = (
    KREA2_TURBO,
    ANIMA,
    Z_IMAGE_TURBO,
    QWEN_IMAGE_EDIT,
    LTX_T2V,
    LTX_I2V,
    LTX_IA2V,
    LTX_ID_LORA,
    LTX_FLF2V,
    LTX_IC_LORA_IMAGE,
    LTX_IC_LORA_MOTION,
    WAN_DANCER,
    ACE_STEP_1_5,
    STABLE_AUDIO_3,
)

BY_ID: dict[str, WorkflowSpec] = {spec.id: spec for spec in SPECS}

DEFAULT_IMAGE_WORKFLOW = KREA2_TURBO.id
#: the closest successor of the old combined graph (image + reference audio +
#: talkvid ID-LoRA), so existing jobs and agent plans keep their semantics
DEFAULT_VIDEO_WORKFLOW = LTX_ID_LORA.id
DEFAULT_AUDIO_WORKFLOW = ACE_STEP_1_5.id

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


def audio_specs() -> list[WorkflowSpec]:
    return [spec for spec in SPECS if spec.kind == "audio"]


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


def get_audio_spec(workflow_id: str | None) -> WorkflowSpec:
    return get_spec(workflow_id or DEFAULT_AUDIO_WORKFLOW, "audio")


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
    #: model family (``krea2`` / ``anima`` / ``z-image`` / ``qwen-image`` / ``ltx2.3``)
    family: str
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
    #: logical knobs the manifest exposes (``prompt``, ``lyrics``, ``bpm``, …).
    #: The audio catalog lists them so the agent knows which extra fields the
    #: workflow reads and which it ignores.
    supports: tuple[str, ...] = ()
    #: audio workflows: the accepted / suggested clip length in seconds
    min_duration: float = 0.0
    max_duration: float = 0.0
    default_duration: float = 0.0
    #: 選択式フィールド ``(論理名, 見出し, 選択肢, 既定値, 自動か, 一言)``。
    #: 宣言のないワークフローでは空なので、カタログにも何も出ない（SPEC §3.1）。
    selects: tuple[tuple[str, str, tuple[str, ...], str, bool, str], ...] = ()

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
        family=spec.family,
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
        supports=tuple(sorted(spec.inject)),
        min_duration=spec.min_duration,
        max_duration=spec.max_duration,
        default_duration=spec.default_duration,
        selects=tuple(
            (
                name,
                select.label,
                select.choices,
                select.fallback,
                bool(select.auto),
                select.hint,
            )
            for name, select in spec.selects.items()
        ),
    )


def video_catalog() -> list[CatalogEntry]:
    """Every selectable video workflow, in UI / prompt order."""
    return [catalog_entry(spec) for spec in video_specs()]


def audio_catalog() -> list[CatalogEntry]:
    """Every selectable audio workflow, in UI / prompt order."""
    return [catalog_entry(spec) for spec in audio_specs()]


def image_catalog() -> list[CatalogEntry]:
    """Every selectable image workflow, in UI / prompt order."""
    return [catalog_entry(spec) for spec in image_specs()]


def image_families() -> list[str]:
    """The families an image LoRA can be registered for, in UI order."""
    seen: list[str] = []
    for spec in image_specs():
        if spec.family not in seen:
            seen.append(spec.family)
    return seen


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

    for name, select in spec.selects.items():
        check(select.target, f"selects[{name}]")
        if select.numeric_target is not None:
            check(select.numeric_target, f"selects[{name}].numeric_target")
        if not select.choices:
            problems.append(f"{spec.id}.selects[{name}]: no choices declared")
        if not select.label.strip():
            problems.append(f"{spec.id}.selects[{name}]: label is empty")
        if select.default and select.default not in select.choices:
            problems.append(
                f"{spec.id}.selects[{name}]: default {select.default!r} is not"
                " one of the choices"
            )
        node = tpl.get(select.target.node_id)
        inputs = (node.get("inputs") or {}) if isinstance(node, dict) else {}
        if select.index_field and select.index_field not in inputs:
            problems.append(
                f"{spec.id}.selects[{name}]: {select.target.node_id}"
                f".{select.index_field} does not exist"
            )
        # テンプレートの option と選択肢がずれていると、番号を書いても別の値が
        # 選ばれる（グラフは番号で n 行目を引く）。option を持つノードだけ確認する。
        options = [
            str(value)
            for key, value in inputs.items()
            if key.startswith("option") and str(value).strip()
        ]
        if options and list(select.choices) != options:
            problems.append(
                f"{spec.id}.selects[{name}]: choices {list(select.choices)!r} do"
                f" not match the template options {options!r}"
            )

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

    # audio workflows are stand-alone one-stage graphs: they take no picture,
    # produce no start frame and declare the clip length the model supports
    if spec.kind == "audio":
        if not spec.prompt_hint.strip():
            problems.append(f"{spec.id}: prompt_hint is empty")
        if spec.accepts_start_image or "image" in spec.inject:
            problems.append(f"{spec.id}: an audio workflow takes no image input")
        if spec.lora_chain is not None:
            problems.append(f"{spec.id}: audio workflows have no LoRA chain")
        for name in ("prompt", "duration", "seed", "save_prefix"):
            if name not in spec.inject:
                problems.append(f"{spec.id}: audio workflow has no {name!r} target")
        if not 0 < spec.min_duration <= spec.default_duration <= spec.max_duration:
            problems.append(
                f"{spec.id}: min/default/max duration must be ordered and positive"
                f" (got {spec.min_duration} / {spec.default_duration}"
                f" / {spec.max_duration})"
            )

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
