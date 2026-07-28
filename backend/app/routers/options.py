from fastapi import APIRouter

from .. import comfy, lora_samples
from ..config import load_settings
from ..db import get_db
from ..models import DEFAULT_NEGATIVE_PROMPT, Options, WorkflowOption
from ..workflows import (
    DEFAULT_VIDEO_WORKFLOW,
    WorkflowSpec,
    image_specs,
    video_specs,
)
from .assets import AUDIO_EXT, IMAGE_EXT, VIDEO_EXT, list_assets

router = APIRouter(prefix="/api", tags=["options"])

NEGATIVE_PRESETS = {
    # empty == keep whatever the selected template ships with (SPEC §3.1)
    "template": "",
    "current": DEFAULT_NEGATIVE_PROMPT,
    "author": (
        "blurry, oversaturated, pixelated, low resolution, grainy, distorted, "
        "noise, compression artifacts, jpeg artifacts, glitches, watermark, "
        "text, logo, signature, copyright, subtitles, distorted sound, "
        "saturated sound, loud"
    ),
}


def _workflow_option(spec: WorkflowSpec) -> WorkflowOption:
    return WorkflowOption(
        id=spec.id,
        label=spec.label,
        kind=spec.kind,
        notes=spec.notes,
        requires=list(spec.requires),
        supports=sorted(spec.inject),
        accepts_start_image=spec.accepts_start_image,
        image_label=spec.image_label,
    )


@router.get("/options", response_model=Options)
async def get_options() -> Options:
    """Form choices. ComfyUI being down is reported inline, never as an HTTP error."""
    settings = load_settings()
    options = Options(
        comfy_url=settings.comfy_url,
        audio_assets=list_assets("audio", AUDIO_EXT),
        image_assets=list_assets("image", IMAGE_EXT),
        video_assets=list_assets("video", VIDEO_EXT),
        negative_presets=NEGATIVE_PRESETS,
        image_workflows=[_workflow_option(spec) for spec in image_specs()],
        video_workflows=[_workflow_option(spec) for spec in video_specs()],
        default_video_workflow=DEFAULT_VIDEO_WORKFLOW,
    )

    async with get_db() as conn:
        async with conn.execute("SELECT * FROM loras ORDER BY sort_order, id") as cur:
            rows = await cur.fetchall()
    options.loras = [lora_samples.row_to_lora(r) for r in rows]

    try:
        info = await comfy.get_object_info()
    except comfy.ComfyError as exc:
        options.comfy_error = str(exc)
        return options

    options.comfy_connected = True
    errors: list[str] = []
    for target, class_type, field in (
        ("aspect_ratios", "ResolutionSelector", "aspect_ratio"),
        ("lora_files", "LoraLoaderModelOnly", "lora_name"),
    ):
        try:
            setattr(options, target, comfy.combo_options(info, class_type, field))
        except comfy.ComfyError as exc:
            errors.append(str(exc))
    if errors:
        options.comfy_error = "; ".join(errors)
    return options
