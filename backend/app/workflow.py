"""Workflow injection engine.

Pure functions only: :func:`build_image_workflow` / :func:`build_video_workflow`
take one API-format template from ``workflow/`` (see :mod:`app.workflows` for
the templates and their injection manifests) plus a :class:`GenerationParams`
and return a new dict ready to be POSTed to ComfyUI ``/prompt``.

A job is one or two ComfyUI prompts (SPEC §2):

* ``image_only`` — the image workflow only,
* ``i2v``        — the selected video workflow only,
* ``full``       — image workflow, then the generated still is uploaded and fed
  to the selected video workflow as its start frame.

See docs/SPEC.md §2 (modes), §3 (injection points / LoRA chain) and §10-3.
"""

from __future__ import annotations

import copy
import math
import re
from typing import Any

from .models import GenerationParams, ModelField
from .workflows import (
    SPECS,
    Target,
    Workflow,
    WorkflowSpec,
    WorkflowSpecError,
    get_image_spec,
    get_video_spec,
    load_template,
    validate_specs,
)

LORA_NODE_PREFIX = "app_lora_"

# --- model file inputs (SPEC §3.3) -----------------------------------------
# (class_type, input field) pairs whose value is a model file name on the
# ComfyUI host.  They are environment specific, so the template value is only a
# default: the settings page can override any of them (see `model_fields`).
MODEL_FIELDS: set[tuple[str, str]] = {
    ("UNETLoader", "unet_name"),
    ("CLIPLoader", "clip_name"),
    ("VAELoader", "vae_name"),
    ("CheckpointLoaderSimple", "ckpt_name"),
    ("LTXVAudioVAELoader", "ckpt_name"),
    ("LTXAVTextEncoderLoader", "text_encoder"),
    ("LTXAVTextEncoderLoader", "ckpt_name"),
    ("LatentUpscaleModelLoader", "model_name"),
    ("LoadMoGeModel", "model_name"),
    ("LoraLoaderModelOnly", "lora_name"),
    ("LoraLoader", "lora_name"),
}

# LTX latent temporal compression: the number of frames handed to
# EmptyLTXVLatentVideo must satisfy frames == 8n + 1.
LTX_FRAME_MULTIPLE = 8

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


def _coerce(class_type: str, field: str, value: Any) -> Any:
    """Match the input type the target node declares.

    ``PrimitiveInt`` rejects a float and ``PrimitiveFloat`` a bool, so the
    injected numbers are cast according to the node the manifest points at.
    """
    if class_type == "PrimitiveInt" and isinstance(value, (int, float)):
        return int(round(float(value)))
    if class_type == "PrimitiveFloat" and isinstance(value, (int, float)):
        return float(value)
    if class_type == "Video Slice" and field == "duration":
        return float(value)
    return value


def _inject(wf: Workflow, spec: WorkflowSpec, name: str, value: Any) -> bool:
    """Write ``value`` to the target ``name`` of ``spec``. False if unsupported."""
    target = spec.target(name)
    if target is None or not target.field:
        return False
    _set(wf, target.node_id, target.field, _coerce(target.class_type, target.field, value))
    return True


def _node(wf: Workflow, target: Target) -> dict[str, Any] | None:
    node = wf.get(target.node_id)
    return node if isinstance(node, dict) else None


def ltx_frame_count(duration: float, fps: int) -> int:
    """Frames for LTX: largest ``8n + 1`` value not exceeding ``duration * fps + 1``.

    LTX-Video compresses 8 frames into one latent frame plus a single key frame,
    so only ``8n + 1`` lengths are accepted.  The raw workflow expression
    ``a * b + 1`` happily produces e.g. 251 for 10s @ 25fps, which is rejected /
    silently truncated downstream, so the app rounds the count down here.
    """
    raw = max(0.0, float(duration)) * max(1, int(fps))
    return math.floor(raw / LTX_FRAME_MULTIPLE) * LTX_FRAME_MULTIPLE + 1


def _inject_frame_count(wf: Workflow, spec: WorkflowSpec, params: GenerationParams) -> None:
    """Pin the workflow's frame-count ``ComfyMathExpression`` to the rounded value.

    Rather than trusting the expression evaluator to provide ``floor()``, the
    value is computed in Python and the expression rewritten as
    ``a * 0 + b * 0 + <frames>``.  The node keeps all of its incoming links — so
    the graph stays structurally identical and its output types are unchanged —
    while the value is exactly the 8n+1 count we want.
    """
    target = spec.target("frames_expr")
    if target is None:
        return
    node = _node(wf, target)
    if node is None:
        return
    frames = ltx_frame_count(params.duration, params.fps)
    inputs = node.setdefault("inputs", {})
    zeroed = [f"{key.split('.', 1)[1]} * 0" for key in inputs if key.startswith("values.")]
    inputs["expression"] = " + ".join([*zeroed, str(frames)])


# --- resolution (SPEC §3.1) -------------------------------------------------
# The image workflow has a ResolutionSelector node that takes the aspect ratio
# and megapixel target directly; the LTX 2.3 templates want plain width /
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


def resolution(
    aspect_ratio: str, megapixels: float, multiple: int = RESOLUTION_MULTIPLE
) -> tuple[int, int]:
    """Width / height for an aspect ratio and a megapixel target.

    Same formula as ComfyUI's ``ResolutionSelector`` (round each edge to the
    nearest multiple of 8), so the video workflows land on the very resolution
    the image workflow would have produced.
    """
    w_ratio, h_ratio = parse_aspect_ratio(aspect_ratio)
    total_pixels = max(0.1, float(megapixels)) * 1024 * 1024
    scale = math.sqrt(total_pixels / (w_ratio * h_ratio))
    width = round(w_ratio * scale / multiple) * multiple
    height = round(h_ratio * scale / multiple) * multiple
    return max(multiple, width), max(multiple, height)


# --- model file names (SPEC §3.3) ------------------------------------------

def _scoped(workflow_id: str, node_id: str, field: str) -> str:
    return f"{workflow_id}/{node_id}.{field}"


def split_override_key(key: str) -> tuple[str, str, str]:
    """``"krea2_turbo/30:10.unet_name"`` -> ``(workflow_id, node_id, field)``."""
    workflow_id, _, rest = key.partition("/")
    node_id, _, field = rest.rpartition(".")
    return workflow_id, node_id, field


def model_fields(specs: tuple[WorkflowSpec, ...] | None = None) -> list[ModelField]:
    """Every configurable model-file input of every template (SPEC §3.3).

    The template value becomes the *default*; :func:`apply_model_overrides`
    replaces it with whatever the user configured on the settings page.  Keys are
    scoped by workflow id because the same node id exists in several templates.
    """
    found: list[ModelField] = []
    for spec in specs if specs is not None else SPECS:
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
                        node_id=node_id,
                        field=field,
                        class_type=class_type,
                        title=str((node.get("_meta") or {}).get("title") or ""),
                        default="" if value is None else str(value),
                    )
                )
    return found


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


def _build_lora_chain(wf: Workflow, spec: WorkflowSpec, params: GenerationParams) -> None:
    """Replace the template's placeholder LoRA loaders with a dynamic chain (§3.4)."""
    chain = spec.lora_chain
    if chain is None:
        return
    for node_id in chain.placeholders:
        wf.pop(node_id, None)

    upstream: list[Any] = [chain.head, 0]
    for index, lora in enumerate(params.loras):
        node_id = f"{LORA_NODE_PREFIX}{index}"
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
    _set(wf, chain.consumer.node_id, chain.consumer.field, upstream)


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
    """
    types: set[str] = {"LoraLoaderModelOnly"}
    for spec in SPECS:
        types |= required_class_types(load_template(spec))
    return types


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
    _inject(wf, resolved, "prompt", params.image_prompt)
    _inject(wf, resolved, "seed", params.image_seed)
    for name, value in resolved.constants.items():
        _inject(wf, resolved, name, value)
    _inject_triggers(wf, resolved, params)
    _build_lora_chain(wf, resolved, params)
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

    _inject(wf, resolved, "prompt", params.video_prompt)
    # An empty negative keeps the template's own default (SPEC §3.1).
    if params.negative_prompt.strip():
        _inject(wf, resolved, "negative", params.negative_prompt)

    width, height = resolution(params.aspect_ratio, params.megapixels)
    _inject(wf, resolved, "width", width)
    _inject(wf, resolved, "height", height)
    _inject(wf, resolved, "duration", params.duration)
    _inject(wf, resolved, "fps", params.fps)
    _inject_frame_count(wf, resolved, params)

    seeds = list(params.video_seeds) or [0]
    for index, target in enumerate(resolved.seeds):
        _set(wf, target.node_id, target.field, seeds[index % len(seeds)])

    _inject(wf, resolved, "image", params.start_image_name)
    _inject(wf, resolved, "end_image", params.end_image_name)
    _inject(wf, resolved, "audio", params.audio_name)
    _inject(wf, resolved, "video", params.reference_video_name)
    for name, value in resolved.constants.items():
        _inject(wf, resolved, name, value)
    _inject(wf, resolved, "save_prefix", params.video_filename_prefix)

    validate_workflow(wf)
    return wf


def build_workflows(
    params: GenerationParams, overrides: dict[str, str] | None = None
) -> dict[str, Workflow]:
    """Both stages of a job, keyed by stage name (``"image"`` / ``"video"``).

    Used by the tests and by anything that wants to inspect a job without
    running it; the job runner builds the stages one at a time because the video
    stage needs the file name of the uploaded still.
    """
    stages: dict[str, Workflow] = {}
    try:
        if params.mode in ("full", "image_only"):
            stages["image"] = build_image_workflow(params, overrides)
        if params.mode in ("full", "i2v"):
            stages["video"] = build_video_workflow(params, overrides)
    except WorkflowSpecError as exc:
        raise WorkflowError(str(exc)) from exc
    if not stages:  # pragma: no cover - guarded by the pydantic Literal
        raise WorkflowError(f"unknown mode: {params.mode}")
    return stages
