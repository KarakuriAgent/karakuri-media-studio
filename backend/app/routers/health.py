from fastapi import APIRouter

from .. import comfy, grok
from ..config import load_settings
from ..models import Health, HealthStatus
from ..workflow import WorkflowError, all_required_class_types

router = APIRouter(prefix="/api", tags=["health"])


async def check_comfyui() -> HealthStatus:
    """/object_info reachability + presence of every class_type we submit (§10-3)."""
    settings = load_settings()
    if not settings.comfy_url:
        return HealthStatus(status="not_configured", detail="comfy_url is empty")
    try:
        info = await comfy.get_object_info()
        required = all_required_class_types()
    except comfy.ComfyError as exc:
        return HealthStatus(status="error", detail=str(exc))
    except (WorkflowError, OSError, ValueError) as exc:
        return HealthStatus(
            status="error", detail=f"workflow template unreadable: {exc}"
        )

    missing = sorted(required - set(info))
    if missing:
        return HealthStatus(
            status="error",
            detail="missing custom nodes on ComfyUI: " + ", ".join(missing),
        )
    return HealthStatus(
        status="ok",
        detail=f"{settings.comfy_url} ({len(required)} node classes verified)",
    )


@router.get("/health", response_model=Health)
async def health() -> Health:
    comfyui = await check_comfyui()
    grok_status = await grok.check_grok()
    return Health(comfyui=comfyui, grok=grok_status)
