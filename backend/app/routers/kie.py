"""kie.ai（外部生成バックエンド）の照会 API（SPEC §5.2）。"""

from fastapi import APIRouter

from .. import backends
from ..kie import KieError, configured, get_credits
from ..models import KieCredits

router = APIRouter(prefix="/api/kie", tags=["kie"])


@router.get("/credits", response_model=KieCredits)
async def credits() -> KieCredits:
    """残クレジット（1 credit = $0.005）。

    未設定・照会失敗は本文の ``error`` で返す（HTTP エラーにはしない）: ヘッダーに
    出す表示用の値なので、取れなかったときに画面ごと壊れないほうがよい。
    """
    if not configured():
        return KieCredits(configured=False)
    try:
        return KieCredits(configured=True, credits=await get_credits())
    except KieError as exc:
        return KieCredits(configured=True, error=str(exc))


@router.post("/check", response_model=KieCredits)
async def check() -> KieCredits:
    """キーの有効性を確かめ直す（設定ページの「接続確認」）。

    確認結果はキャッシュされ、kie 系ワークフローを選択肢に出すかどうかを決める。
    """
    status = await backends.refresh("kie")
    if status.state == "not_configured":
        return KieCredits(configured=False, error=status.detail)
    if status.state == "error":
        return KieCredits(configured=True, error=status.detail)
    return await credits()
