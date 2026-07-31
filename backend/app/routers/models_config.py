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

どちらも**接続先（ComfyCloud / RunPod / ローカル）ごと**に持つ（SPEC §5）: 同じ
ファイルがどの環境にも在るとは限らないため。``?target=`` を省略すると現在の
接続先が対象になり、設定ページは環境プルダウンで選んだ環境を明示的に渡す。
"""

from typing import Any

from fastapi import APIRouter, HTTPException

from ..config import load_settings, update_settings
from ..models import ComfyTarget, ModelFieldState, ModelOverridesUpdate, Settings
from ..workflow import model_fields

router = APIRouter(prefix="/api", tags=["models"])


def _state(
    settings: Settings, target: ComfyTarget | None = None
) -> list[ModelFieldState]:
    overrides = settings.overrides_for(target)
    choices = settings.choices_for(target)
    states: list[ModelFieldState] = []
    for field in model_fields():
        value = overrides.get(field.key)
        states.append(
            ModelFieldState(
                **field.model_dump(),
                value=value if value else field.default,
                overridden=bool(value) and value != field.default,
                choices=list(choices.get(field.key) or ()),
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
async def get_models(target: ComfyTarget | None = None) -> list[ModelFieldState]:
    """その接続先のモデル指定（``target`` 省略時は現在の接続先）。"""
    return _state(load_settings(), target)


@router.put("/models", response_model=list[ModelFieldState])
async def put_models(payload: ModelOverridesUpdate) -> list[ModelFieldState]:
    """Replace the override map (and the candidate lists, when they are sent).

    Values equal to the default (or empty) are dropped, as are candidate lists
    that end up empty.  Omitting ``choices`` keeps the stored lists as they are.

    書き込む先は ``target``（省略すると現在の接続先）だけで、他の環境の指定は
    そのまま残る。
    """
    defaults = {field.key: field.default for field in model_fields()}
    unknown = sorted((set(payload.overrides) | set(payload.choices or {})) - set(defaults))
    if unknown:
        raise HTTPException(
            status_code=422, detail=f"unknown model field(s): {', '.join(unknown)}"
        )
    settings = load_settings()
    target = payload.target or settings.comfy_target
    overrides = dict(settings.model_overrides)
    overrides[target] = {
        key: value.strip()
        for key, value in payload.overrides.items()
        if value.strip() and value.strip() != defaults[key]
    }
    patch: dict[str, Any] = {"model_overrides": overrides}
    if payload.choices is not None:
        choices = dict(settings.model_choices)
        choices[target] = _clean_choices(payload.choices)
        patch["model_choices"] = choices
    return _state(update_settings(patch), target)
