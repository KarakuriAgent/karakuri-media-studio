"""Model file name overrides (SPEC §3.3).

The workflow template ships with the file names of one particular ComfyUI
installation.  This router exposes them as defaults and lets the settings page
override any of them; only the entries that actually differ are persisted in
``Settings.model_overrides``.
"""

from fastapi import APIRouter, HTTPException

from ..config import load_settings, update_settings
from ..models import ModelFieldState, ModelOverridesUpdate
from ..workflow import load_template, model_fields

router = APIRouter(prefix="/api", tags=["models"])


def _state(overrides: dict[str, str]) -> list[ModelFieldState]:
    states: list[ModelFieldState] = []
    for field in model_fields(load_template()):
        value = overrides.get(field.key)
        states.append(
            ModelFieldState(
                **field.model_dump(),
                value=value if value else field.default,
                overridden=bool(value) and value != field.default,
            )
        )
    return states


@router.get("/models", response_model=list[ModelFieldState])
async def get_models() -> list[ModelFieldState]:
    return _state(load_settings().model_overrides)


@router.put("/models", response_model=list[ModelFieldState])
async def put_models(payload: ModelOverridesUpdate) -> list[ModelFieldState]:
    """Replace the override map. Values equal to the default (or empty) are dropped."""
    defaults = {field.key: field.default for field in model_fields(load_template())}
    unknown = sorted(set(payload.overrides) - set(defaults))
    if unknown:
        raise HTTPException(
            status_code=422, detail=f"unknown model field(s): {', '.join(unknown)}"
        )
    overrides = {
        key: value.strip()
        for key, value in payload.overrides.items()
        if value.strip() and value.strip() != defaults[key]
    }
    settings = update_settings({"model_overrides": overrides})
    return _state(settings.model_overrides)
