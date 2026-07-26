from fastapi import APIRouter

from ..config import load_settings
from ..models import Health, HealthStatus

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health", response_model=Health)
async def health() -> Health:
    # Actual connectivity checks land in the ComfyUI / Grok work packages.
    settings = load_settings()
    comfy = HealthStatus(
        status="not_implemented" if settings.comfy_url else "not_configured",
        detail=f"comfy_url={settings.comfy_url}" if settings.comfy_url else "comfy_url is empty",
    )
    grok = HealthStatus(
        status="not_implemented" if settings.grok_command else "not_configured",
        detail=(
            f"command={settings.grok_command}, model={settings.grok_model}"
            if settings.grok_command
            else "grok_command is empty"
        ),
    )
    return Health(comfyui=comfy, grok=grok)
