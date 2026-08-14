"""Web Push: VAPID 公開鍵と購読の登録 / 解除。"""

from fastapi import APIRouter, HTTPException, Query, Request

from .. import push
from ..models import PushEndpointIn, PushSubscriptionIn, PushVapidPublicKey

router = APIRouter(prefix="/api/push", tags=["push"])


@router.get("/vapid-public-key", response_model=PushVapidPublicKey)
async def vapid_public_key() -> PushVapidPublicKey:
    return PushVapidPublicKey(public_key=push.public_key())


@router.post("/subscriptions", status_code=204)
async def upsert_subscription(payload: PushSubscriptionIn) -> None:
    await push.upsert_subscription(
        payload.endpoint, payload.keys.p256dh, payload.keys.auth
    )


@router.delete("/subscriptions", status_code=204)
async def delete_subscription(
    request: Request,
    endpoint: str | None = Query(None),
) -> None:
    if not endpoint:
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001 - 空 body は query 側で扱う
            body = None
        if isinstance(body, dict):
            parsed = PushEndpointIn.model_validate(body)
            endpoint = parsed.endpoint
    if not endpoint:
        raise HTTPException(status_code=422, detail="endpoint is required")
    await push.delete_subscription(endpoint)
