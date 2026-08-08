from fastapi import APIRouter

from .. import comfy, grok
from ..config import load_settings
from ..models import Health, HealthStatus
from ..workflow import WorkflowError, all_required_class_types, validate_manifests

router = APIRouter(prefix="/api", tags=["health"])


async def check_comfyui() -> HealthStatus:
    """/object_info reachability + presence of every class_type we submit (§10-3).

    The injection manifests are verified against the templates at the same time,
    so a hand-edited ``workflow/*.json`` shows up here rather than as a failed
    job (SPEC §3).
    """
    settings = load_settings()
    url = settings.active_comfy_url()
    if not url:
        return HealthStatus(
            status="not_configured",
            detail=f"接続先 '{settings.comfy_target}' の ComfyUI URL が未設定です",
        )
    try:
        info = await comfy.get_object_info()
        validate_manifests()
        required = all_required_class_types()
    except comfy.ComfyError as exc:
        return HealthStatus(status="error", detail=comfy.display_error(exc))
    except (WorkflowError, OSError, ValueError) as exc:
        return HealthStatus(status="error", detail=f"workflow template: {exc}")

    missing = sorted(required - set(info))
    if missing:
        return HealthStatus(
            status="error",
            detail="missing custom nodes on ComfyUI: " + ", ".join(missing),
        )
    return HealthStatus(
        status="ok",
        detail=f"{url} ({len(required)} node classes verified)",
    )


@router.get("/health", response_model=Health)
async def health() -> Health:
    comfyui = await check_comfyui()
    grok_status = await grok.check_grok()
    return Health(comfyui=comfyui, grok=grok_status)
