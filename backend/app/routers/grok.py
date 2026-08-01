"""Grok Build CLI（サブスク枠の生成バックエンド）の照会 API（SPEC §5.2）。"""

from fastapi import APIRouter

from .. import backends, grok_media
from ..backends import BackendStatus
from ..models import BackendInfo

router = APIRouter(prefix="/api/grok", tags=["grok"])


def _info(status: BackendStatus) -> BackendInfo:
    return BackendInfo(
        backend=status.backend,
        status=status.state,
        detail=status.detail,
        available=status.available,
    )


@router.post("/check", response_model=BackendInfo)
async def check() -> BackendInfo:
    """実際に CLI を 1 ターン回して確かめる（設定ページの「接続確認」）。

    起動時・設定保存時の確認はサブスク枠を使わない軽いもの（コマンドと
    ``~/.grok/auth.json`` の存在）なので、実行できるかどうかはここで確かめる。
    結果はキャッシュされ、grok_cli 系ワークフローを選択肢に出すかどうかを決める。
    """
    status = await grok_media.check_live()
    backends.store(status)
    return _info(status)
