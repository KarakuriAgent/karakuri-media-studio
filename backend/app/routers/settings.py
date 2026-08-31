from fastapi import APIRouter

from .. import chat_agent, llm_cli, remotion
from ..config import load_settings, update_settings
from ..models import Settings, SettingsUpdate

router = APIRouter(prefix="/api", tags=["settings"])


@router.get("/settings", response_model=Settings)
async def get_settings() -> Settings:
    return load_settings()


@router.put("/settings", response_model=Settings)
async def put_settings(payload: SettingsUpdate) -> Settings:
    before = load_settings().agent_cli
    saved = update_settings(payload.model_dump(exclude_unset=True))
    # Remotion のプロジェクトを指し替えたかもしれないので、composition の
    # 一覧キャッシュは捨てる（TTL を待たずに新しい場所を読み直す）。
    remotion.clear_cache()
    if saved.agent_cli != before:
        # CLI が変わった: 保存済みの続き用セッション id は別 CLI では通じない
        # （SPEC §4.1）。開きっぱなしの相談ホストも畳んで新しい CLI で開き直す。
        await llm_cli.forget_saved_sessions()
        await chat_agent.stop_all()
    return saved
