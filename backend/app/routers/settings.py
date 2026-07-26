from fastapi import APIRouter

from ..config import load_settings, update_settings
from ..models import Settings, SettingsUpdate

router = APIRouter(prefix="/api", tags=["settings"])


@router.get("/settings", response_model=Settings)
async def get_settings() -> Settings:
    return load_settings()


@router.put("/settings", response_model=Settings)
async def put_settings(payload: SettingsUpdate) -> Settings:
    return update_settings(payload.model_dump(exclude_unset=True))
