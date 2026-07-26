"""Workflow injection engine.

Pure functions only (no I/O other than reading the template file explicitly via
:func:`load_template`).  ``build_workflow`` takes the ``video-gen.json`` API-format
template plus :class:`GenerationParams` and returns a new dict ready to be POSTed
to ComfyUI ``/prompt``.

See docs/SPEC.md §2 (modes), §3 (injection points / LoRA chain) and §10-3.
"""

from __future__ import annotations

import copy
import json
import math
from typing import Any

from .models import GenerationParams
from .paths import WORKFLOW_TEMPLATE_PATH

Workflow = dict[str, dict[str, Any]]

# --- node ids referenced by the injector (see SPEC §3) ----------------------
N_SAVE_VIDEO = "75"
N_RESOLUTION = "366"
N_PREVIEW_IMAGE = "393"
N_LOAD_AUDIO = "432"
N_LOAD_IMAGE = "435"

N_IMG_KSAMPLER = "365:3"
N_IMG_VAE_DECODE = "365:8"
N_IMG_UNET = "365:10"
N_IMG_LORA = "365:15"
N_IMG_PROMPT = "365:19"
N_IMG_LORA_SWITCH = "365:22"
N_IMG_LORA_ENABLE = "365:23"
N_IMG_REFINE_ENABLE = "365:24"
N_IMG_TRIGGER_CONCAT = "365:27"
N_IMG_ORPHAN_WILDCARD = "365:391"

N_VID_SEED_A = "433:394"
N_VID_SEED_B = "433:395"
N_VID_NEGATIVE = "433:413"
N_VID_FRAMES_EXPR = "433:329"
N_VID_DURATION = "433:331"
N_VID_FPS = "433:422"
N_VID_PROMPT = "433:430"
N_VID_RESIZE = "433:431"

LORA_NODE_PREFIX = "app_lora_"

# LTX latent temporal compression: the number of frames handed to
# EmptyLTXVLatentVideo must satisfy frames == 8n + 1.
LTX_FRAME_MULTIPLE = 8


class WorkflowError(Exception):
    """Raised when the template is malformed or the built workflow is invalid."""


def load_template(path: str | None = None) -> Workflow:
    """Read the API-format workflow template from disk."""
    p = path or WORKFLOW_TEMPLATE_PATH
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


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


def _drop(wf: Workflow, node_ids: list[str]) -> None:
    for node_id in node_ids:
        wf.pop(node_id, None)


def _drop_prefix(wf: Workflow, prefix: str, keep: set[str] | None = None) -> None:
    keep = keep or set()
    for node_id in [k for k in wf if k.startswith(prefix) and k not in keep]:
        del wf[node_id]


def _set(wf: Workflow, node_id: str, field: str, value: Any) -> None:
    """Set ``inputs[field]`` if the node survived the mode-specific pruning."""
    node = wf.get(node_id)
    if node is not None:
        node.setdefault("inputs", {})[field] = value


def ltx_frame_count(duration: float, fps: int) -> int:
    """Frames for LTX: largest ``8n + 1`` value not exceeding ``duration * fps + 1``.

    LTX-Video compresses 8 frames into one latent frame plus a single key frame,
    so only ``8n + 1`` lengths are accepted.  The raw workflow expression
    ``a * b + 1`` happily produces e.g. 251 for 10s @ 25fps, which is rejected /
    silently truncated downstream, so the app rounds the count down here.
    """
    raw = max(0.0, float(duration)) * max(1, int(fps))
    return math.floor(raw / LTX_FRAME_MULTIPLE) * LTX_FRAME_MULTIPLE + 1


def _inject_frame_count(wf: Workflow, params: GenerationParams) -> None:
    """Pin ``433:329`` to the rounded frame count.

    Rather than trusting the expression evaluator of ``ComfyMathExpression`` to
    provide ``floor()`` (it is a custom node whose grammar we cannot verify from
    the API JSON alone), we compute the value in Python and rewrite the
    expression as ``a * 0 + b * 0 + <frames>``.  The node keeps both of its
    incoming links — so the graph stays structurally identical and its output
    types are unchanged — while the value is exactly the 8n+1 count we want.
    """
    frames = ltx_frame_count(params.duration, params.fps)
    _set(wf, N_VID_FRAMES_EXPR, "expression", f"a * 0 + b * 0 + {frames}")


def _build_lora_chain(wf: Workflow, params: GenerationParams) -> None:
    """Replace the single ``365:15`` LoRA loader with a dynamic chain (§3.4)."""
    _drop(wf, [N_IMG_LORA])
    if N_IMG_LORA_SWITCH not in wf:
        return

    loras = params.loras
    _set(wf, N_IMG_LORA_ENABLE, "value", bool(loras))

    if not loras:
        # Nothing to chain: point the (disabled) on_true branch back at the UNET
        # so the switch node still resolves to an existing node.
        _set(wf, N_IMG_LORA_SWITCH, "on_true", [N_IMG_UNET, 0])
        return

    upstream = [N_IMG_UNET, 0]
    for index, lora in enumerate(loras):
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
    _set(wf, N_IMG_LORA_SWITCH, "on_true", upstream)


# --------------------------------------------------------------------------
# validation
# --------------------------------------------------------------------------

def validate_workflow(wf: Workflow) -> None:
    """Check that every link points at a node that exists in the workflow.

    ComfyUI validates the whole submitted graph, so a dangling reference left
    behind by the mode-specific pruning would fail the job only after it has
    been queued.  ``build_workflow`` runs this before returning.
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
                problems.append(
                    f"{node_id}.{field} -> missing node '{value[0]}'"
                )
    if problems:
        raise WorkflowError("invalid workflow: " + "; ".join(sorted(problems)))


def required_class_types(wf: Workflow) -> set[str]:
    """class_types present in a workflow (used by the health check, SPEC §10-3)."""
    return {
        node["class_type"]
        for node in wf.values()
        if isinstance(node, dict) and node.get("class_type")
    }


def all_required_class_types(template: Workflow | None = None) -> set[str]:
    """class_types that can appear in any submitted workflow.

    Derived from the template minus nodes we always delete, plus the classes the
    injector introduces (``SaveImage`` for mode C, ``LoraLoaderModelOnly`` for
    the dynamic chain — already in the template but listed for clarity).
    """
    tpl = template if template is not None else load_template()
    types = required_class_types(tpl)
    orphan = tpl.get(N_IMG_ORPHAN_WILDCARD, {}).get("class_type")
    if orphan:
        types.discard(orphan)
    types.update({"SaveImage", "LoraLoaderModelOnly"})
    return types


# --------------------------------------------------------------------------
# main entry point
# --------------------------------------------------------------------------

def build_workflow(template: Workflow, params: GenerationParams) -> Workflow:
    """Build the API JSON for one job. Pure: ``template`` is never mutated."""
    wf: Workflow = copy.deepcopy(template)

    # §3.3: the orphan ImpactWildcardEncode is never submitted.
    _drop(wf, [N_IMG_ORPHAN_WILDCARD])

    # --- mode-specific topology (§2) --------------------------------------
    if params.mode == "full":
        # Mode A: image graph feeds the video graph; the unused LoadImage would
        # fail validation because its file does not exist on the server.
        _drop(wf, [N_LOAD_IMAGE])
        _set(wf, N_VID_RESIZE, "input", [N_IMG_VAE_DECODE, 0])
    elif params.mode == "i2v":
        # Mode B: drop the whole image subgraph (366 is shared with the video
        # resolution and stays) and feed LoadImage into the resize node.
        _drop_prefix(wf, "365:")
        _drop(wf, [N_PREVIEW_IMAGE])
        _set(wf, N_VID_RESIZE, "input", [N_LOAD_IMAGE, 0])
        _set(wf, N_LOAD_IMAGE, "image", params.start_image_name or "")
    elif params.mode == "image_only":
        # Mode C: drop the video graph and turn the preview into a real save.
        _drop_prefix(wf, "433:")
        _drop(wf, [N_SAVE_VIDEO, N_LOAD_AUDIO, N_LOAD_IMAGE])
        if N_PREVIEW_IMAGE in wf:
            wf[N_PREVIEW_IMAGE] = {
                "class_type": "SaveImage",
                "_meta": {"title": "Save Image"},
                "inputs": {
                    "filename_prefix": params.image_filename_prefix,
                    "images": [N_IMG_VAE_DECODE, 0],
                },
            }
    else:  # pragma: no cover - guarded by the pydantic Literal
        raise WorkflowError(f"unknown mode: {params.mode}")

    # --- common injections (§3.1 / §3.2) -----------------------------------
    _set(wf, N_RESOLUTION, "aspect_ratio", params.aspect_ratio)
    _set(wf, N_RESOLUTION, "megapixels", params.megapixels)

    # image side
    _set(wf, N_IMG_PROMPT, "value", params.image_prompt)
    _set(wf, N_IMG_KSAMPLER, "seed", params.image_seed)
    _set(wf, N_IMG_REFINE_ENABLE, "value", False)  # local LLM refine off (§3.2)
    _set(wf, N_IMG_TRIGGER_CONCAT, "string_b", params.trigger_text)
    if params.mode != "i2v":
        _build_lora_chain(wf, params)

    # video side
    _set(wf, N_VID_PROMPT, "value", params.video_prompt)
    _set(wf, N_VID_NEGATIVE, "text", params.negative_prompt)
    _set(wf, N_VID_DURATION, "value", params.duration)
    _set(wf, N_VID_FPS, "value", params.fps)
    _inject_frame_count(wf, params)
    seeds = list(params.video_seeds)
    if seeds:
        _set(wf, N_VID_SEED_A, "noise_seed", seeds[0])
        _set(wf, N_VID_SEED_B, "noise_seed", seeds[1] if len(seeds) > 1 else seeds[0])
    _set(wf, N_LOAD_AUDIO, "audio", params.audio_name)
    _set(wf, N_SAVE_VIDEO, "filename_prefix", params.video_filename_prefix)

    validate_workflow(wf)
    return wf
