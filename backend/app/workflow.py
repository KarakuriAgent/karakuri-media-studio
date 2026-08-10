"""Workflow injection engine.

Pure functions only: :func:`build_image_workflow` / :func:`build_video_workflow`
take one API-format template from ``workflow/`` (see :mod:`app.workflows` for
the templates and their injection manifests) plus a :class:`GenerationParams`
and return a new dict ready to be POSTed to ComfyUI ``/prompt``.

A job is one or two ComfyUI prompts (SPEC §2):

* ``image_only`` — the image workflow only,
* ``i2v``        — the selected video workflow only,
* ``full``       — image workflow, then the generated still is uploaded and fed
  to the selected video workflow as its start frame,
* ``audio``      — the selected audio workflow only.  Stand-alone: it is never
  chained with the image / video stages.

See docs/SPEC.md §2 (modes), §3 (injection points / LoRA chain) and §10-3.
"""

from __future__ import annotations

import copy
import math
import re
from typing import Any

from .models import GenerationParams, LoraRef, ModelField, ModelSlot
from .workflows import (
    OPTIONAL_CLASS_TYPES,
    REF_AUDIOS_NAME,
    REF_IMAGES_NAME,
    REF_VIDEOS_NAME,
    LoraChain,
    Target,
    Workflow,
    WorkflowSpec,
    WorkflowSpecError,
    comfy_specs,
    family_label,
    get_audio_spec,
    get_image_spec,
    get_video_spec,
    load_template,
    validate_specs,
)

#: node id prefix of the image (Krea 2) LoRA chain
LORA_NODE_PREFIX = "app_lora_"
#: node id prefix of the video LoRA chain
VIDEO_LORA_NODE_PREFIX = "app_video_lora_"
#: node id prefixes of the dynamic reference-material loaders (MiniMax H3 r2v).
#: 参照動画は ``LoadVideo`` と ``GetVideoComponents`` の 2 ノードで 1 本になる。
REF_IMAGE_NODE_PREFIX = "app_ref_image_"
REF_VIDEO_NODE_PREFIX = "app_ref_video_"
REF_VIDEO_PARTS_NODE_PREFIX = "app_ref_video_parts_"
REF_AUDIO_NODE_PREFIX = "app_ref_audio_"

#: ``GetVideoComponents`` の出力（images / audio / fps / bit_depth の 0 と 1）
REF_VIDEO_IMAGES_SLOT = 0
REF_VIDEO_AUDIO_SLOT = 1

#: 論理入力（:data:`app.workflows.InputName`）-> :class:`GenerationParams` が持つ
#: 「ComfyUI に上げたファイル名」のフィールド。空なら「そのファイルは渡されて
#: いない」で、任意入力の枝を落とす判断に使う（:func:`_prune_optional_loaders`）。
UPLOAD_NAME_FIELDS: dict[str, str] = {
    "image": "start_image_name",
    "end_image": "end_image_name",
    "audio": "audio_name",
    "video": "reference_video_name",
}

# --- model file inputs (SPEC §3.3) -----------------------------------------
# (class_type, input field) pairs whose value is a model file name on the
# ComfyUI host.  They are environment specific, so the template value is only a
# default: the settings page can override any of them (see `model_fields`).
MODEL_FIELDS: set[tuple[str, str]] = {
    ("UNETLoader", "unet_name"),
    ("CLIPLoader", "clip_name"),
    # ACE-Step 1.5 loads two Qwen text encoders through one DualCLIPLoader
    ("DualCLIPLoader", "clip_name1"),
    ("DualCLIPLoader", "clip_name2"),
    ("VAELoader", "vae_name"),
    ("CheckpointLoaderSimple", "ckpt_name"),
    ("CLIPVisionLoader", "clip_name"),
    ("LatentUpscaleModelLoader", "model_name"),
    ("LoadMoGeModel", "model_name"),
    ("LoraLoaderModelOnly", "lora_name"),
    ("LoraLoader", "lora_name"),
    # MiniMax H3 turbo の 4step 蒸留 LoRA（専用ローダーだが中身はただの LoRA）
    ("MiniMaxH3TurboLoRA", "lora_name"),
}

# 不足モデルをダウンロードするときの既定の置き場所（SPEC §3.3）。値は ComfyUI 側の
# ローダーが `folder_paths.get_filename_list(...)` に渡すフォルダ名そのもので、
# models ディレクトリ（環境変数 COMFY_MODELS_DIR）からの相対パスになる。
# 未知のローダーは「当てずっぽうで checkpoints に置く」と ComfyUI から見えない
# ファイルが増えるだけなので、空を返して UI に入力させる。
MODEL_SUBFOLDERS: dict[tuple[str, str], str] = {
    ("CheckpointLoaderSimple", "ckpt_name"): "checkpoints",
    ("UNETLoader", "unet_name"): "diffusion_models",
    # GGUF 版ローダーはテンプレートには出てこないが置き場所は同じ
    ("UnetLoaderGGUF", "unet_name"): "diffusion_models",
    ("CLIPLoader", "clip_name"): "text_encoders",
    ("DualCLIPLoader", "clip_name1"): "text_encoders",
    ("DualCLIPLoader", "clip_name2"): "text_encoders",
    ("CLIPVisionLoader", "clip_name"): "clip_vision",
    ("VAELoader", "vae_name"): "vae",
    ("LoraLoader", "lora_name"): "loras",
    ("LoraLoaderModelOnly", "lora_name"): "loras",
    ("MiniMaxH3TurboLoRA", "lora_name"): "loras",
    # 空間アップスケーラと MoGe は専用フォルダ（nodes_hunyuan.py / nodes_moge.py）
    ("LatentUpscaleModelLoader", "model_name"): "latent_upscale_models",
    ("LoadMoGeModel", "model_name"): "geometry_estimation",
}

# ResolutionSelector rounds the computed edges to a multiple of 8
# (comfy_extras/nodes_resolution.py).
RESOLUTION_MULTIPLE = 8


class WorkflowError(Exception):
    """Raised when the template is malformed or the built workflow is invalid."""


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _is_link(value: Any) -> bool:
    """True if ``value`` looks like a ComfyUI link: ``[node_id, slot]``."""
    return (
        isinstance(value, list)
        and len(value) == 2
        and isinstance(value[0], str)
        and isinstance(value[1], int)
        and not isinstance(value[1], bool)
    )


def _set(wf: Workflow, node_id: str, field: str, value: Any) -> None:
    """Set ``inputs[field]`` if the node exists in the workflow."""
    node = wf.get(node_id)
    if node is not None:
        node.setdefault("inputs", {})[field] = value


# Numeric inputs of ordinary (non-Primitive) nodes whose declared type the
# injected value has to match.  ComfyUI validates INT / FLOAT strictly, so an
# INT widget fed a float (or the other way round) fails the whole prompt.
_INT_INPUTS: set[tuple[str, str]] = {
    ("TextEncodeAceStepAudio1.5", "duration"),
    ("TextEncodeAceStepAudio1.5", "bpm"),
    ("CustomCombo", "index"),
    # サンプリング回数（SPEC §3.1）。UI が空欄なら注入しない = テンプレート既定。
    ("KSampler", "steps"),
    ("BasicScheduler", "steps"),
}
_FLOAT_INPUTS: set[tuple[str, str]] = {
    ("Video Slice", "duration"),
    ("EmptyAceStep1.5LatentAudio", "seconds"),
}

#: BOOLEAN を宣言している入力。選択式フィールド（:class:`app.workflows.SelectSpec`）
#: は選択肢を**文字列**で持つので、こういう入力に入れる前に真偽値へ直す
#: （``"on"`` -> ``True`` / それ以外 -> ``False``）。ComfyUI は BOOLEAN の widget に
#: 文字列を入れると型検証で prompt ごと落ちる。
_BOOL_INPUTS: set[tuple[str, str]] = {
    ("MiniMaxH3TurboLoRA", "low_vram"),
}

#: :data:`_BOOL_INPUTS` で「ON」とみなす文字列（大文字小文字は問わない）
_TRUE_WORDS = frozenset({"on", "true", "yes", "1"})


def _coerce(class_type: str, field: str, value: Any) -> Any:
    """Match the input type the target node declares.

    ``PrimitiveInt`` rejects a float and ``PrimitiveFloat`` a bool, so the
    injected numbers are cast according to the node the manifest points at.

    BOOLEAN の入力（:data:`_BOOL_INPUTS`）には、選択式フィールドが持っている
    文字列を真偽値に直して入れる。
    """
    if (class_type, field) in _BOOL_INPUTS:
        if isinstance(value, str):
            return value.strip().lower() in _TRUE_WORDS
        return bool(value)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return value
    if class_type == "PrimitiveInt" or (class_type, field) in _INT_INPUTS:
        return int(round(float(value)))
    if class_type == "PrimitiveFloat" or (class_type, field) in _FLOAT_INPUTS:
        return float(value)
    return value


def _inject(wf: Workflow, spec: WorkflowSpec, name: str, value: Any) -> bool:
    """Write ``value`` to the target ``name`` of ``spec``. False if unsupported."""
    target = spec.target(name)
    if target is None or not target.field:
        return False
    _set(wf, target.node_id, target.field, _coerce(target.class_type, target.field, value))
    return True


def _inject_selects(wf: Workflow, spec: WorkflowSpec, params: GenerationParams) -> None:
    """Write the picked value of every selectable field (SPEC §3.1).

    ``CustomCombo`` は選んだ文字列と 0 始まりの番号を持ち、**グラフが読むのは
    番号側**（``choice`` は表示用）なので両方書く。``numeric_target`` があれば
    同じ値を数値としても入れる（尺は音声のトリム長も決める）。
    不明・リスト外の値は既定値に落とす（検証は :func:`app.models.select_problem`
    が済ませているので、ここは最後の砦）。
    """
    for name, select in spec.selects.items():
        choice = (params.selects.get(name) or "").strip()
        if choice not in select.choices:
            choice = select.fallback
        # 注入先が無いのは ComfyUI 以外のバックエンドの宣言（グラフに書く先が
        # 無い）。ここは ComfyUI のグラフを組む関数なので何もしない。
        if not choice or select.target is None:
            continue
        # 書き込み先が BOOLEAN を宣言していれば真偽値に直す（それ以外の
        # 選択式は文字列のまま。:func:`_coerce` は文字列に触らない）。
        _set(
            wf,
            select.target.node_id,
            select.target.field,
            _coerce(select.target.class_type, select.target.field, choice),
        )
        if select.index_field:
            _set(wf, select.target.node_id, select.index_field, select.index_of(choice))
        if select.numeric_target is not None:
            try:
                value: Any = float(choice)
            except ValueError:
                continue
            _set(
                wf,
                select.numeric_target.node_id,
                select.numeric_target.field,
                _coerce(select.numeric_target.class_type, select.numeric_target.field, value),
            )


def _node(wf: Workflow, target: Target) -> dict[str, Any] | None:
    node = wf.get(target.node_id)
    return node if isinstance(node, dict) else None


def _inject_steps(wf: Workflow, spec: WorkflowSpec, params: GenerationParams) -> None:
    """Write the sampling step count, **only when the job asks for one** (§3.1).

    ``steps`` は「未指定ならテンプレートの既定値のまま」という約束のつまみで、
    既定値はワークフローごとに大きく違う（turbo 系は 4、ACE-Step は 50）。
    ``0`` や負の値は未指定なので、注入せずにテンプレートの値を残す。
    """
    if params.steps > 0:
        _inject(wf, spec, "steps", params.steps)


def _inject_frame_count(wf: Workflow, spec: WorkflowSpec, params: GenerationParams) -> None:
    """Pin the workflow's frame-count ``ComfyMathExpression`` to the rounded value.

    Rather than trusting the expression evaluator to provide ``floor()``, the
    value is computed in Python and the expression rewritten as
    ``a * 0 + b * 0 + <frames>``.  The node keeps all of its incoming links — so
    the graph stays structurally identical and its output types are unchanged —
    while the value is exactly the count the model's latent grid accepts
    (:class:`app.workflows.FrameGrid`: MiniMax H3 is 17k+5 @ 24fps).
    """
    target = spec.target("frames_expr")
    if target is None:
        return
    node = _node(wf, target)
    if node is None:
        return
    frames = spec.frames.frames(params.duration, params.fps)
    inputs = node.setdefault("inputs", {})
    zeroed = [f"{key.split('.', 1)[1]} * 0" for key in inputs if key.startswith("values.")]
    inputs["expression"] = " + ".join([*zeroed, str(frames)])


# --- resolution (SPEC §3.1) -------------------------------------------------
# The image workflow has a ResolutionSelector node that takes the aspect ratio
# and megapixel target directly; the video templates want plain width /
# height integers, so the same computation is done here (identical to
# ComfyUI's comfy_extras/nodes_resolution.py).

ASPECT_RATIOS: dict[str, tuple[int, int]] = {
    "1:1 (Square)": (1, 1),
    "2:3 (Portrait Photo)": (2, 3),
    "3:2 (Photo)": (3, 2),
    "3:4 (Portrait Standard)": (3, 4),
    "4:3 (Standard)": (4, 3),
    "9:16 (Portrait Widescreen)": (9, 16),
    "16:9 (Widescreen)": (16, 9),
    "21:9 (Ultrawide)": (21, 9),
}

_RATIO_RE = re.compile(r"^\s*(\d+)\s*:\s*(\d+)")


def parse_aspect_ratio(aspect_ratio: str) -> tuple[int, int]:
    """``"16:9 (Widescreen)"`` -> ``(16, 9)``.

    Unknown labels are still accepted as long as they start with ``W:H`` so a
    ComfyUI version that adds a ratio does not need an app update.
    """
    known = ASPECT_RATIOS.get(aspect_ratio)
    if known:
        return known
    match = _RATIO_RE.match(aspect_ratio or "")
    if not match:
        raise WorkflowError(f"unsupported aspect ratio: {aspect_ratio!r}")
    width, height = int(match.group(1)), int(match.group(2))
    if width <= 0 or height <= 0:
        raise WorkflowError(f"unsupported aspect ratio: {aspect_ratio!r}")
    return width, height


def _fit_ratio(
    w_ratio: float, h_ratio: float, megapixels: float, multiple: int
) -> tuple[int, int]:
    """Edges of ``w_ratio:h_ratio`` scaled to ``megapixels``, rounded to ``multiple``."""
    total_pixels = max(0.1, float(megapixels)) * 1024 * 1024
    scale = math.sqrt(total_pixels / (w_ratio * h_ratio))
    width = round(w_ratio * scale / multiple) * multiple
    height = round(h_ratio * scale / multiple) * multiple
    return max(multiple, width), max(multiple, height)


def resolution(
    aspect_ratio: str, megapixels: float, multiple: int = RESOLUTION_MULTIPLE
) -> tuple[int, int]:
    """Width / height for an aspect ratio and a megapixel target.

    Same formula as ComfyUI's ``ResolutionSelector`` (round each edge to the
    nearest multiple of 8), so the video workflows land on the very resolution
    the image workflow would have produced.
    """
    w_ratio, h_ratio = parse_aspect_ratio(aspect_ratio)
    return _fit_ratio(w_ratio, h_ratio, megapixels, multiple)


def resolution_for_image(
    image_width: int,
    image_height: int,
    megapixels: float,
    multiple: int = RESOLUTION_MULTIPLE,
) -> tuple[int, int]:
    """Width / height following a reference image's own aspect ratio.

    Keeps the megapixel budget the user asked for but takes the ratio from the
    image, so the start frame is not centre-cropped by the workflow's
    ``ResizeImageMaskNode``.  Extreme ratios are followed as-is.
    """
    if image_width <= 0 or image_height <= 0:
        raise WorkflowError(f"invalid image size: {image_width}x{image_height}")
    return _fit_ratio(image_width, image_height, megapixels, multiple)


def video_resolution(spec: WorkflowSpec, params: GenerationParams) -> tuple[int, int]:
    """Width / height of the video stage.

    A workflow that takes a start frame follows the reference image's aspect
    ratio whenever its size is known, in preference to the user's preset — the
    template centre-crops anything that does not match.  Workflows without a
    start frame (t2v, the reference-material modes) always use the preset.

    Both edges are rounded to ``spec.resolution_multiple`` rather than the
    image-side default of 8, because video latents use a coarser spatial grid
    than images (MiniMax H3 wants multiples of 32).
    """
    size = params.start_image_size
    multiple = spec.resolution_multiple
    if spec.accepts_start_image and size:
        return resolution_for_image(
            size[0], size[1], params.megapixels, multiple=multiple
        )
    return resolution(params.aspect_ratio, params.megapixels, multiple=multiple)


# --- model file names (SPEC §3.3) ------------------------------------------

def _scoped(workflow_id: str, node_id: str, field: str) -> str:
    return f"{workflow_id}/{node_id}.{field}"


def split_override_key(key: str) -> tuple[str, str, str]:
    """``"krea2_turbo/30:10.unet_name"`` -> ``(workflow_id, node_id, field)``."""
    workflow_id, _, rest = key.partition("/")
    node_id, _, field = rest.rpartition(".")
    return workflow_id, node_id, field


def model_subfolder(class_type: str, field: str) -> str:
    """そのモデル入力の既定の置き場所（models ディレクトリ相対、SPEC §3.3）。

    未知のローダーでは空文字を返す（UI で置き場所を入力してもらう）。
    """
    return MODEL_SUBFOLDERS.get((class_type, field), "")


def model_fields(specs: tuple[WorkflowSpec, ...] | None = None) -> list[ModelField]:
    """Every configurable model-file input of every template (SPEC §3.3).

    The template value becomes the *default*; :func:`apply_model_overrides`
    replaces it with whatever the user configured on the settings page.  Keys are
    scoped by workflow id because the same node id exists in several templates.
    """
    found: list[ModelField] = []
    for spec in specs if specs is not None else comfy_specs():
        template = load_template(spec)
        # the dynamic LoRA chain replaces these, so their names never ship
        excluded = set(spec.lora_chain.placeholders) if spec.lora_chain else set()
        for node_id, node in template.items():
            if not isinstance(node, dict) or node_id in excluded:
                continue
            class_type = node.get("class_type") or ""
            inputs = node.get("inputs")
            if not isinstance(inputs, dict):
                continue
            for field, value in inputs.items():
                if (class_type, field) not in MODEL_FIELDS or _is_link(value):
                    continue
                found.append(
                    ModelField(
                        key=_scoped(spec.id, node_id, field),
                        workflow_id=spec.id,
                        workflow_label=spec.label,
                        kind=spec.kind,
                        family=spec.family,
                        family_label=family_label(spec.family),
                        node_id=node_id,
                        field=field,
                        class_type=class_type,
                        title=str((node.get("_meta") or {}).get("title") or ""),
                        default="" if value is None else str(value),
                        subfolder=model_subfolder(class_type, field),
                    )
                )
    return found


def model_slots(
    overrides: dict[str, str] | None = None,
    choices: dict[str, list[str]] | None = None,
    specs: tuple[WorkflowSpec, ...] | None = None,
) -> list[ModelSlot]:
    """Every model-file input as a *switchable slot* (SPEC §3.3).

    ``default`` is the value a job uses when it says nothing (the settings
    override, or the template value), and ``choices`` is the list the user
    registered for that slot with the default in front.  A job's own
    ``model_overrides`` may only pick from ``choices``.
    """
    slots: list[ModelSlot] = []
    for field in model_fields(specs):
        override = ((overrides or {}).get(field.key) or "").strip()
        default = override or field.default
        allowed = [default] if default else []
        for name in (choices or {}).get(field.key) or ():
            name = (name or "").strip()
            if name and name not in allowed:
                allowed.append(name)
        slots.append(
            ModelSlot(
                key=field.key,
                workflow_id=field.workflow_id,
                workflow_label=field.workflow_label,
                kind=field.kind,
                node_id=field.node_id,
                field=field.field,
                class_type=field.class_type,
                label=field.title,
                default=default,
                choices=allowed,
            )
        )
    return slots


def selectable_model_slots(
    overrides: dict[str, str] | None = None,
    choices: dict[str, list[str]] | None = None,
    specs: tuple[WorkflowSpec, ...] | None = None,
) -> list[ModelSlot]:
    """The slots worth showing a picker for: 2 or more candidates (SPEC §3.3)."""
    return [
        slot
        for slot in model_slots(overrides, choices, specs)
        if len(slot.choices) > 1
    ]


def scoped_model_overrides(
    overrides: dict[str, str] | None, workflow_ids: list[str] | set[str]
) -> dict[str, str]:
    """The entries of ``overrides`` that belong to one of ``workflow_ids``.

    ``continue`` may switch the video workflow, in which case the source job's
    per-job model picks no longer apply — they are dropped here rather than
    failing the request.
    """
    allowed = set(workflow_ids)
    return {
        key: value
        for key, value in (overrides or {}).items()
        if value and split_override_key(key)[0] in allowed
    }


def apply_model_overrides(
    wf: Workflow, overrides: dict[str, str] | None, workflow_id: str
) -> None:
    """Replace model file names in ``wf`` with the configured values.

    Keys are ``"<workflow_id>/<node_id>.<field>"``.  Entries for another
    workflow, for a node that is not in this template, and empty values are
    silently ignored — the workflow keeps the template default.  (Keys left over
    from the pre-``workflow/`` layout have no ``workflow_id`` prefix and are
    therefore ignored as well, which is the intended migration path.)
    """
    for key, value in (overrides or {}).items():
        if not value:
            continue
        scope, node_id, field = split_override_key(key)
        if scope != workflow_id or not node_id or not field:
            continue
        node = wf.get(node_id)
        if node is None:
            continue
        inputs = node.get("inputs")
        if isinstance(inputs, dict) and field in inputs and not _is_link(inputs[field]):
            inputs[field] = value


# --- LoRA trigger words (SPEC §3.4) ----------------------------------------

def split_triggers(trigger_text: str) -> list[str]:
    """Comma-separated trigger text -> deduplicated, stripped words."""
    words: list[str] = []
    seen: set[str] = set()
    for part in trigger_text.split(","):
        word = part.strip()
        key = word.casefold()
        if word and key not in seen:
            seen.add(key)
            words.append(word)
    return words


def _mentions(text: str, word: str) -> bool:
    """True if ``word`` already appears in ``text`` (case-insensitive, whole word)."""
    pattern = rf"(?<!\w){re.escape(word)}(?!\w)"
    return re.search(pattern, text, re.IGNORECASE) is not None


def missing_triggers(trigger_text: str, image_prompt: str) -> str:
    """The trigger words that ``image_prompt`` does not already contain (§3.4).

    Grok is told to weave the trigger word into the prompt itself, so the app
    only fills in what is missing; an empty result means "prepend nothing".
    """
    return ", ".join(
        word for word in split_triggers(trigger_text)
        if not _mentions(image_prompt, word)
    )


def _inject_triggers(wf: Workflow, spec: WorkflowSpec, params: GenerationParams) -> None:
    """Prepend the missing trigger words to the image prompt.

    The template concatenates ``string_a`` + ``string_b``; we put the trigger
    literal into ``string_a`` and the prompt link into ``string_b`` so the
    trigger comes **first**, the position the LoRA was trained with.  When
    nothing has to be prepended the switch in front of the concatenation is
    turned off so the prompt passes through untouched — never with a leading
    ", ".
    """
    concat = spec.target("trigger_concat")
    source = spec.target("prompt_source")
    if concat is None or source is None:
        return
    node = _node(wf, concat)
    if node is None:
        return
    prefix = missing_triggers(params.trigger_text, params.image_prompt)
    inputs = node.setdefault("inputs", {})
    inputs["string_a"] = prefix
    inputs["string_b"] = [source.node_id, 0]
    inputs["delimiter"] = ", " if prefix else ""
    _inject(wf, spec, "trigger_switch", bool(prefix))


def video_trigger_text(params: GenerationParams) -> str:
    """Trigger words of the video LoRAs (the user's edit wins, §3.4)."""
    explicit = params.video_trigger_text.strip()
    if explicit:
        return explicit
    return ", ".join(
        lora.trigger_word.strip()
        for lora in params.video_loras
        if lora.trigger_word.strip()
    )


def prepend_triggers(trigger_text: str, prompt: str) -> str:
    """``prompt`` with the trigger words it does not contain yet in front (§3.4).

    The video templates have no StringConcatenate to switch on, so the video
    prompt is prefixed here.  Nothing to add == the prompt is returned as is, so
    a prompt that already names the character never grows a leading ", ".
    """
    missing = missing_triggers(trigger_text, prompt)
    if not missing:
        return prompt
    return f"{missing}, {prompt}" if prompt.strip() else missing


def _build_lora_chain(
    wf: Workflow,
    chain: LoraChain | None,
    loras: list[LoraRef],
    *,
    prefix: str,
) -> None:
    """Splice a dynamic ``LoraLoaderModelOnly`` chain into ``chain`` (§3.4).

    The template's own placeholder loaders (image workflow only) are deleted,
    one node per selected LoRA is chained from ``chain.head``, and every
    consumer is re-pointed at the tail.  With no LoRA selected the consumers go
    straight back to the head, which is what the template already says.
    """
    if chain is None:
        return
    for node_id in chain.placeholders:
        wf.pop(node_id, None)

    upstream: list[Any] = [chain.head, 0]
    for index, lora in enumerate(loras):
        node_id = f"{prefix}{index}"
        wf[node_id] = {
            "class_type": "LoraLoaderModelOnly",
            "_meta": {"title": f"LoRA {index}: {lora.lora_name}"},
            "inputs": {
                "lora_name": lora.lora_name,
                "strength_model": lora.strength,
                "model": upstream,
            },
        }
        upstream = [node_id, 0]
    for consumer in chain.consumers:
        _set(wf, consumer.node_id, consumer.field, upstream)


def _loader_node(loader: Target, value: Any, title: str) -> dict[str, Any]:
    """One freshly grown ``LoadImage`` / ``LoadVideo`` / ``LoadAudio`` node."""
    return {
        "class_type": loader.class_type,
        "_meta": {"title": title},
        "inputs": {loader.field: value},
    }


def _build_ref_media(
    wf: Workflow, spec: WorkflowSpec, materials: dict[str, list[str]]
) -> dict[str, list[str]]:
    """Grow one loader per reference material and wire them up (SPEC §3.1).

    The mirror image of :func:`_build_lora_chain` for the reference inputs of
    MiniMax H3 r2v (:class:`app.workflows.RefMediaFan`): the template's single
    placeholder loader of each kind and **all** of the node's ``ref_*`` links
    are dropped, then one loader per uploaded file is created and connected as
    ``ref_image_0``, ``ref_image_1``, …  The order is the order the job lists
    them in, which is what the prompt's ``<Picture i>`` / ``<Video k>`` /
    ``<Audio j>`` tags refer to.

    A reference video needs two nodes: ``LoadVideo`` feeds a
    ``GetVideoComponents`` whose frames (output 0) go to ``ref_video_N`` and
    whose soundtrack (output 1) goes to ``ref_video_audio_N``.  **The pairing is
    by number**, so the soundtrack always travels with its video — that is also
    why the app has no separate list for it.  The node ignores a soundtrack with
    no video behind it, and a silent video simply produces no ``<Audio j>``.

    Nothing to connect means the variable inputs disappear entirely — the node
    accepts zero references, and leaving a placeholder behind would make ComfyUI
    look for a file name that only exists in the template.

    Returns the file names it actually wired per logical name (the surplus over
    the declared limit is dropped; the request is refused long before this by
    :func:`app.models.reference_problem`).
    """
    fan = spec.ref_media
    if fan is None:
        return {}
    node = _node(wf, fan.node)
    if node is None:
        return {}
    inputs = node.setdefault("inputs", {})
    for key in [key for key in inputs if key.startswith(fan.prefixes())]:
        del inputs[key]
    for loader in fan.loaders():
        wf.pop(loader.node_id, None)

    def picked(name: str) -> list[str]:
        limit = spec.multi_inputs.get(name, 0)
        return [item for item in materials.get(name) or [] if item][:limit]

    wired: dict[str, list[str]] = {}

    wired[REF_IMAGES_NAME] = picked(REF_IMAGES_NAME)
    for index, image in enumerate(wired[REF_IMAGES_NAME]):
        node_id = f"{REF_IMAGE_NODE_PREFIX}{index}"
        wf[node_id] = _loader_node(
            fan.image_loader, image, f"参照画像 {index + 1}（<Picture {index + 1}>）"
        )
        inputs[f"{fan.image_prefix}{index}"] = [node_id, 0]

    if REF_VIDEOS_NAME in fan.names():
        assert fan.video_loader is not None and fan.video_decoder is not None
        wired[REF_VIDEOS_NAME] = picked(REF_VIDEOS_NAME)
        for index, video in enumerate(wired[REF_VIDEOS_NAME]):
            node_id = f"{REF_VIDEO_NODE_PREFIX}{index}"
            parts_id = f"{REF_VIDEO_PARTS_NODE_PREFIX}{index}"
            wf[node_id] = _loader_node(
                fan.video_loader, video, f"参照動画 {index + 1}（<Video {index + 1}>）"
            )
            wf[parts_id] = {
                "class_type": fan.video_decoder.class_type,
                "_meta": {"title": f"参照動画 {index + 1} の映像と音声"},
                "inputs": {fan.video_decoder.field: [node_id, 0]},
            }
            inputs[f"{fan.video_prefix}{index}"] = [parts_id, REF_VIDEO_IMAGES_SLOT]
            inputs[f"{fan.video_audio_prefix}{index}"] = [parts_id, REF_VIDEO_AUDIO_SLOT]

    if REF_AUDIOS_NAME in fan.names():
        assert fan.audio_loader is not None
        wired[REF_AUDIOS_NAME] = picked(REF_AUDIOS_NAME)
        for index, audio in enumerate(wired[REF_AUDIOS_NAME]):
            node_id = f"{REF_AUDIO_NODE_PREFIX}{index}"
            wf[node_id] = _loader_node(
                fan.audio_loader, audio, f"参照音声 {index + 1}"
            )
            inputs[f"{fan.audio_prefix}{index}"] = [node_id, 0]

    return wired


def _prune_optional_loaders(
    wf: Workflow, spec: WorkflowSpec, params: GenerationParams
) -> None:
    """Drop the loader of every optional input the job did not supply (§3.1).

    ``inject`` alone can only write a file *name*, so an optional input left
    empty would still leave the template's placeholder loader in the graph and
    ComfyUI would fail looking for a file that only exists in the template.  The
    workflows that declare :attr:`app.workflows.WorkflowSpec.optional_loaders`
    (MiniMax H3 i2v's ``end_image`` -> ``last_frame``) therefore have the loader
    **and every link that reads it** removed — the receiving input is optional on
    the node itself, so its absence is what "not given" means.
    """
    for name in spec.optional_loaders:
        target = spec.inject.get(name)
        supplied = getattr(params, UPLOAD_NAME_FIELDS.get(name, ""), "")
        if target is None or str(supplied or "").strip():
            continue
        wf.pop(target.node_id, None)
        for node in wf.values():
            inputs = node.get("inputs") if isinstance(node, dict) else None
            if not isinstance(inputs, dict):
                continue
            for field in [
                field
                for field, value in inputs.items()
                if _is_link(value) and value[0] == target.node_id
            ]:
                del inputs[field]


# --------------------------------------------------------------------------
# validation
# --------------------------------------------------------------------------

def validate_workflow(wf: Workflow) -> None:
    """Check that every link points at a node that exists in the workflow.

    ComfyUI validates the whole submitted graph, so a dangling reference would
    fail the job only after it has been queued.  The builders run this before
    returning.
    """
    problems: list[str] = []
    for node_id, node in wf.items():
        if not isinstance(node, dict):
            problems.append(f"{node_id}: node is not an object")
            continue
        if not node.get("class_type"):
            problems.append(f"{node_id}: missing class_type")
        inputs = node.get("inputs", {})
        if not isinstance(inputs, dict):
            problems.append(f"{node_id}: inputs is not an object")
            continue
        for field, value in inputs.items():
            if _is_link(value) and value[0] not in wf:
                problems.append(f"{node_id}.{field} -> missing node '{value[0]}'")
    if problems:
        raise WorkflowError("invalid workflow: " + "; ".join(sorted(problems)))


def required_class_types(wf: Workflow) -> set[str]:
    """class_types present in a workflow (used by the health check, SPEC §10-3)."""
    return {
        node["class_type"]
        for node in wf.values()
        if isinstance(node, dict) and node.get("class_type")
    }


def all_required_class_types() -> set[str]:
    """class_types that can appear in any submitted workflow (every template).

    ``LoraLoaderModelOnly`` is listed explicitly because the dynamic image-LoRA
    chain introduces it even though the template placeholders are removed.

    :data:`app.workflows.OPTIONAL_CLASS_TYPES`（MiniMax H3 turbo だけが使う任意の
    カスタムノード）は**除く**: 入れていない環境で接続インジケーターが赤くなって
    しまうため、そのワークフローを実際に選んだときだけ ComfyUI 側で失敗させる。
    """
    types: set[str] = {"LoraLoaderModelOnly"}
    for spec in comfy_specs():
        types |= required_class_types(load_template(spec))
    return types - OPTIONAL_CLASS_TYPES


def validate_manifests() -> None:
    """Raise if any manifest no longer matches its template (SPEC §3)."""
    problems = validate_specs(use_cache=False)
    if problems:
        raise WorkflowError("workflow manifest mismatch: " + "; ".join(problems))


# --------------------------------------------------------------------------
# builders
# --------------------------------------------------------------------------

def build_image_workflow(
    params: GenerationParams,
    overrides: dict[str, str] | None = None,
    *,
    spec: WorkflowSpec | None = None,
    template: Workflow | None = None,
) -> Workflow:
    """API JSON for the image stage. Pure: the template is never mutated."""
    resolved = spec or get_image_spec(params.image_workflow)
    wf: Workflow = copy.deepcopy(
        template if template is not None else load_template(resolved)
    )

    # After the (non-existent) pruning and before the LoRA chain so that a
    # user-set placeholder lora_name cannot leak in.
    apply_model_overrides(wf, overrides, resolved.id)

    _inject(wf, resolved, "aspect_ratio", params.aspect_ratio)
    _inject(wf, resolved, "megapixels", float(params.megapixels))
    # Templates without a ResolutionSelector (z-image) take plain integers, so
    # the same computation ComfyUI's node does is done here (SPEC §3.1).
    if resolved.supports("width") or resolved.supports("height"):
        width, height = resolution(params.aspect_ratio, params.megapixels)
        _inject(wf, resolved, "width", width)
        _inject(wf, resolved, "height", height)
    # Editing workflows (qwen-image) read the picture the job supplies in
    # `source_image`; the job runner uploads it under `start_image_name` before
    # the image stage runs, exactly like the video stage's start frame.
    _inject(wf, resolved, "image", params.start_image_name)
    _inject(wf, resolved, "prompt", params.image_prompt)
    _inject(wf, resolved, "seed", params.image_seed)
    _inject_steps(wf, resolved, params)
    for name, value in resolved.constants.items():
        _inject(wf, resolved, name, value)
    _inject_triggers(wf, resolved, params)
    _build_lora_chain(
        wf, resolved.lora_chain, params.loras, prefix=LORA_NODE_PREFIX
    )
    _inject(wf, resolved, "save_prefix", params.image_filename_prefix)

    validate_workflow(wf)
    return wf


def build_video_workflow(
    params: GenerationParams,
    overrides: dict[str, str] | None = None,
    *,
    spec: WorkflowSpec | None = None,
    template: Workflow | None = None,
) -> Workflow:
    """API JSON for the video stage. Pure: the template is never mutated."""
    resolved = spec or get_video_spec(params.video_workflow)
    wf: Workflow = copy.deepcopy(
        template if template is not None else load_template(resolved)
    )

    apply_model_overrides(wf, overrides, resolved.id)

    # The video LoRA's trigger words go in front of the prompt (SPEC §3.4).
    # プロンプトをコンボから組み立てるワークフローでは、空の
    # `video_prompt` でテンプレートの既定文を潰さない。
    if params.video_prompt.strip() or resolved.prompt_required:
        _inject(
            wf,
            resolved,
            "prompt",
            prepend_triggers(video_trigger_text(params), params.video_prompt),
        )
    # An empty negative keeps the template's own default (SPEC §3.1).
    if params.negative_prompt.strip():
        _inject(wf, resolved, "negative", params.negative_prompt)

    width, height = video_resolution(resolved, params)
    _inject(wf, resolved, "width", width)
    _inject(wf, resolved, "height", height)
    _inject(wf, resolved, "duration", params.duration)
    _inject(wf, resolved, "fps", params.fps)
    _inject_steps(wf, resolved, params)
    _inject_frame_count(wf, resolved, params)

    seeds = list(params.video_seeds) or [0]
    for index, target in enumerate(resolved.seeds):
        _set(wf, target.node_id, target.field, seeds[index % len(seeds)])

    _inject(wf, resolved, "image", params.start_image_name)
    _inject(wf, resolved, "end_image", params.end_image_name)
    _inject(wf, resolved, "audio", params.audio_name)
    _inject(wf, resolved, "video", params.reference_video_name)
    # 渡されなかった任意の入力は、雛形のローダーごとグラフから外す（§3.1）
    _prune_optional_loaders(wf, resolved, params)
    # 参照素材は 1 つの注入点では表せない: 渡された件数ぶんノードを生やす（§3.1）
    _build_ref_media(
        wf,
        resolved,
        {
            REF_IMAGES_NAME: list(params.reference_image_names),
            REF_VIDEOS_NAME: list(params.reference_video_names),
            REF_AUDIOS_NAME: list(params.reference_audio_names),
        },
    )
    _build_lora_chain(
        wf, resolved.lora_chain, params.video_loras, prefix=VIDEO_LORA_NODE_PREFIX
    )
    _inject_selects(wf, resolved, params)
    for name, value in resolved.constants.items():
        _inject(wf, resolved, name, value)
    _inject(wf, resolved, "save_prefix", params.video_filename_prefix)

    validate_workflow(wf)
    return wf


def build_audio_workflow(
    params: GenerationParams,
    overrides: dict[str, str] | None = None,
    *,
    spec: WorkflowSpec | None = None,
    template: Workflow | None = None,
) -> Workflow:
    """API JSON for a stand-alone audio job. Pure: the template is never mutated.

    Audio is not part of the image / video chain: nothing here reads a start
    frame, an aspect ratio or a LoRA list.  Model-specific knobs (``lyrics``,
    ``bpm``, ``audio_category``, …) are injected through the same
    :func:`_inject` as everything else, so a workflow whose manifest does not
    declare one simply skips it.
    """
    resolved = spec or get_audio_spec(params.audio_workflow)
    wf: Workflow = copy.deepcopy(
        template if template is not None else load_template(resolved)
    )

    apply_model_overrides(wf, overrides, resolved.id)

    _inject(wf, resolved, "prompt", params.audio_prompt)
    _inject(wf, resolved, "lyrics", params.lyrics)
    # ACE-Step wants the length twice (conditioning + empty latent); Stable
    # Audio's latent reads the same PrimitiveFloat, so it only declares one.
    _inject(wf, resolved, "duration", params.duration)
    _inject(wf, resolved, "latent_seconds", params.duration)
    _inject(wf, resolved, "bpm", params.bpm)
    _inject(wf, resolved, "keyscale", params.keyscale)
    _inject(wf, resolved, "language", params.language)
    _inject(wf, resolved, "audio_category", params.audio_category)
    _inject(wf, resolved, "reprompt", params.reprompt)
    _inject(wf, resolved, "seed", params.audio_seed)
    _inject_steps(wf, resolved, params)
    for name, value in resolved.constants.items():
        _inject(wf, resolved, name, value)
    _inject(wf, resolved, "save_prefix", params.audio_filename_prefix)

    validate_workflow(wf)
    return wf


def build_workflows(
    params: GenerationParams, overrides: dict[str, str] | None = None
) -> dict[str, Workflow]:
    """Every stage of a job, keyed by stage name (``image`` / ``video`` / ``audio``).

    Used by the tests and by anything that wants to inspect a job without
    running it; the job runner builds the stages one at a time because the video
    stage needs the file name of the uploaded still.
    """
    stages: dict[str, Workflow] = {}
    try:
        if params.mode == "audio":
            stages["audio"] = build_audio_workflow(params, overrides)
        if params.mode in ("full", "image_only"):
            stages["image"] = build_image_workflow(params, overrides)
        if params.mode in ("full", "i2v"):
            stages["video"] = build_video_workflow(params, overrides)
    except WorkflowSpecError as exc:
        raise WorkflowError(str(exc)) from exc
    if not stages:  # pragma: no cover - guarded by the pydantic Literal
        raise WorkflowError(f"unknown mode: {params.mode}")
    return stages
