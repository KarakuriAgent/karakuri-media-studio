from fastapi import APIRouter

from .. import backends
from ..config import load_settings, update_settings
from ..models import Settings, SettingsUpdate

router = APIRouter(prefix="/api", tags=["settings"])


@router.get("/settings", response_model=Settings)
async def get_settings() -> Settings:
    return load_settings()


@router.put("/settings", response_model=Settings)
async def put_settings(payload: SettingsUpdate) -> Settings:
    saved = update_settings(payload.model_dump(exclude_unset=True))
    # 認証情報が変わったかもしれないので、外部バックエンドの可用性を確認し直す
    # （キーを入れた直後に kie 系ワークフローが選べるようになる、SPEC §5.2）。
    await backends.refresh_all()
    return saved
