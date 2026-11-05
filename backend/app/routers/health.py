from fastapi import APIRouter

from .. import comfy, grok, llm_cli
from ..config import load_settings
from ..models import Health, HealthStatus
from ..workflow import (
    WorkflowError,
    all_required_class_types,
    optional_select_class_types,
    validate_manifests,
)

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
    optional = _optional_note(set(info), settings.comfy_target)
    if missing:
        return HealthStatus(
            status="error",
            detail="missing custom nodes on ComfyUI: " + ", ".join(missing) + optional,
        )
    return HealthStatus(
        status="ok",
        detail=f"{url} ({len(required)} node classes verified){optional}",
    )


def _optional_note(available: set[str], comfy_target: str) -> str:
    """on にしたときだけ要るカスタムノードのうち、足りないもの（無ければ空）。

    MiniMax H3 動画の補正（``fast_derope`` / ``face_detail`` / ``anime_cadence``）は
    既定 off なので、足りなくてもインジケーターは赤くしない（``status`` は変えない）。
    on にする前に入れるべきものを ``detail`` の末尾で知らせるだけ。Comfy Cloud では
    そもそも選べない（off に固定）ので何も言わない。
    """
    if comfy_target == "comfy_cloud":
        return ""
    parts = []
    for name, class_types in optional_select_class_types().items():
        lacking = [class_type for class_type in class_types if class_type not in available]
        if lacking:
            parts.append(f"{name}: " + ", ".join(lacking))
    if not parts:
        return ""
    return " / optional (needed only when switched on): " + "; ".join(parts)


@router.get("/health", response_model=Health)
async def health() -> Health:
    comfyui = await check_comfyui()
    grok_status = await grok.check_grok()
    adapter = llm_cli.active_adapter()
    return Health(
        comfyui=comfyui,
        grok=grok_status,
        cli=adapter.id,  # type: ignore[arg-type]
        cli_label=adapter.label,
    )
