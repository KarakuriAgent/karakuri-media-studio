"""Grok Build CLI（サブスク枠の生成バックエンド）の照会 API（SPEC §5.2）。"""

from fastapi import APIRouter

from .. import grok_media
from ..models import HealthStatus

router = APIRouter(prefix="/api/grok", tags=["grok"])


@router.get("/status", response_model=HealthStatus)
async def status() -> HealthStatus:
    """枠を使わない軽い確認（コマンドが在るか・サインイン済みか）。"""
    return await grok_media.check_backend()


@router.post("/check", response_model=HealthStatus)
async def check() -> HealthStatus:
    """実際に CLI を 1 ターン回して確かめる（設定ページの「接続確認」）。

    起動時の確認はサブスク枠を使わない軽いもの（コマンドと ``~/.grok/auth.json``
    の存在）なので、本当に実行できるかどうかはここで確かめる。生成はせず短い返答を
    もらうだけなので、枠の消費は最小で済む。
    """
    return await grok_media.check_live()
