"""Codex CLI（ChatGPT サブスク枠の生成バックエンド）の照会 API（SPEC §5.4）。"""

from fastapi import APIRouter

from .. import backends, codex_media
from ..backends import BackendStatus
from ..models import BackendInfo

router = APIRouter(prefix="/api/codex", tags=["codex"])


def _info(status: BackendStatus) -> BackendInfo:
    return BackendInfo(
        backend=status.backend,
        status=status.state,
        detail=status.detail,
        available=status.available,
    )


@router.post("/check", response_model=BackendInfo)
async def check() -> BackendInfo:
    """実際に ``codex login status`` を回して確かめる（設定ページの「接続確認」）。

    起動時・設定保存時の確認はファイルを見るだけ（コマンドと
    ``~/.codex/auth.json`` の中身）なので、CLI が本当に動くかどうかはここで
    確かめる。画像は生成しないのでサブスク枠は減らない。結果はキャッシュされ、
    codex_cli 系ワークフローを選択肢に出すかどうかを決める。
    """
    status = await codex_media.check_live()
    backends.store(status)
    return _info(status)
