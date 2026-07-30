"""Model file name overrides and per-slot choices (SPEC §3.3).

The workflow templates ship with the file names of one particular ComfyUI
installation.  This router exposes them as defaults and lets the settings page
override any of them; only the entries that actually differ are persisted in
``Settings.model_overrides``.  Keys are scoped by workflow id
(``"<workflow_id>/<node_id>.<field>"``) because the templates reuse node ids.

The same keys also carry a候補リスト (``Settings.model_choices``): the model files a
job may switch to at run time.  A slot with two or more candidates gets a picker
in the generation form (``GET /api/options`` の ``model_slots``) and can be set per
job through ``JobCreate.model_overrides``.
"""

from typing import Any

from fastapi import APIRouter, HTTPException

from ..config import load_settings, update_settings
from ..models import ModelFieldState, ModelOverridesUpdate, Settings
from ..workflow import model_fields

router = APIRouter(prefix="/api", tags=["models"])


def _state(settings: Settings) -> list[ModelFieldState]:
    states: list[ModelFieldState] = []
    for field in model_fields():
        value = settings.model_overrides.get(field.key)
        states.append(
            ModelFieldState(
                **field.model_dump(),
                value=value if value else field.default,
                overridden=bool(value) and value != field.default,
                choices=list(settings.model_choices.get(field.key) or ()),
            )
        )
    return states


def _clean_choices(choices: dict[str, list[str]]) -> dict[str, list[str]]:
    """Strip / de-duplicate every candidate list and drop the keys left empty."""
    cleaned: dict[str, list[str]] = {}
    for key, names in choices.items():
        picked: list[str] = []
        for name in names:
            name = (name or "").strip()
            if name and name not in picked:
                picked.append(name)
        if picked:
            cleaned[key] = picked
    return cleaned


@router.get("/models", response_model=list[ModelFieldState])
async def get_models() -> list[ModelFieldState]:
    return _state(load_settings())


@router.put("/models", response_model=list[ModelFieldState])
async def put_models(payload: ModelOverridesUpdate) -> list[ModelFieldState]:
    """Replace the override map (and the candidate lists, when they are sent).

    Values equal to the default (or empty) are dropped, as are candidate lists
    that end up empty.  Omitting ``choices`` keeps the stored lists as they are.
    """
    defaults = {field.key: field.default for field in model_fields()}
    unknown = sorted((set(payload.overrides) | set(payload.choices or {})) - set(defaults))
    if unknown:
        raise HTTPException(
            status_code=422, detail=f"unknown model field(s): {', '.join(unknown)}"
        )
    patch: dict[str, Any] = {
        "model_overrides": {
            key: value.strip()
            for key, value in payload.overrides.items()
            if value.strip() and value.strip() != defaults[key]
        }
    }
    if payload.choices is not None:
        patch["model_choices"] = _clean_choices(payload.choices)
    return _state(update_settings(patch))
