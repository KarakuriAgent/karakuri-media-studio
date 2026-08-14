"""Web Push (browser vendor + this FastAPI). No extra relay server.

VAPID 秘密鍵は ``runtime/vapid.json`` に初回生成して保存し、公開鍵だけを
``GET /api/push/vapid-public-key`` で出す。送信失敗はログだけで、呼び出し元の
本処理は落とさない。
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from datetime import datetime, timezone
from typing import Any

from .db import get_db
from .paths import RUNTIME_DIR

log = logging.getLogger(__name__)

VAPID_PATH = RUNTIME_DIR / "vapid.json"
VAPID_CLAIMS = {"sub": "mailto:studio@localhost"}


class SubscriptionGone(Exception):
    """Push サービスが 404 / 410 を返した（購読を消す）。"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _application_server_key(public_key: Any) -> str:
    from cryptography.hazmat.primitives import serialization

    raw = public_key.public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def load_or_create_vapid() -> tuple[str, str]:
    """``(private_pem, public_urlb64)``。無ければ作って ``runtime/vapid.json`` に残す。"""
    if VAPID_PATH.is_file():
        try:
            data = json.loads(VAPID_PATH.read_text(encoding="utf-8"))
            private = data.get("private_key") or ""
            public = data.get("public_key") or ""
            if private and public:
                return private, public
        except (OSError, ValueError):
            log.warning("broken VAPID file at %s; regenerating", VAPID_PATH)

    from py_vapid import Vapid

    vapid = Vapid()
    vapid.generate_keys()
    private = vapid.private_pem().decode("ascii")
    public = _application_server_key(vapid.public_key)
    VAPID_PATH.parent.mkdir(parents=True, exist_ok=True)
    VAPID_PATH.write_text(
        json.dumps({"private_key": private, "public_key": public}, indent=2),
        encoding="utf-8",
    )
    try:
        VAPID_PATH.chmod(0o600)
    except OSError:
        pass
    return private, public


def public_key() -> str:
    return load_or_create_vapid()[1]


def send_webpush(
    subscription_info: dict[str, Any],
    data: str,
    vapid_private_key: str,
    vapid_claims: dict[str, str] | None = None,
) -> None:
    """1 件送る。テストでモックする入口（実 FCM は叩かない）。"""
    from py_vapid import Vapid
    from pywebpush import WebPushException, webpush

    # runtime/vapid.json には PEM を置いている。webpush() の文字列経路は
    # raw / DER しか受けないので、ここで Vapid オブジェクトにする。
    key = vapid_private_key.strip()
    if key.startswith("-----BEGIN"):
        vapid_private_key = Vapid.from_pem(key.encode("utf-8"))

    try:
        webpush(
            subscription_info=subscription_info,
            data=data,
            vapid_private_key=vapid_private_key,
            vapid_claims=vapid_claims or dict(VAPID_CLAIMS),
        )
    except WebPushException as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status in (404, 410):
            raise SubscriptionGone(str(exc)) from exc
        raise


async def upsert_subscription(endpoint: str, p256dh: str, auth: str) -> None:
    created = _now()
    async with get_db() as conn:
        await conn.execute(
            "INSERT INTO push_subscriptions (endpoint, p256dh, auth, created_at)"
            " VALUES (?, ?, ?, ?)"
            " ON CONFLICT(endpoint) DO UPDATE SET p256dh = excluded.p256dh,"
            " auth = excluded.auth",
            (endpoint, p256dh, auth, created),
        )
        await conn.commit()


async def delete_subscription(endpoint: str) -> bool:
    async with get_db() as conn:
        cur = await conn.execute(
            "DELETE FROM push_subscriptions WHERE endpoint = ?", (endpoint,)
        )
        await conn.commit()
        return cur.rowcount > 0


async def list_subscriptions() -> list[dict[str, str]]:
    async with get_db() as conn:
        async with conn.execute(
            "SELECT endpoint, p256dh, auth FROM push_subscriptions"
        ) as cur:
            rows = await cur.fetchall()
    return [
        {"endpoint": row["endpoint"], "p256dh": row["p256dh"], "auth": row["auth"]}
        for row in rows
    ]


async def _deliver(sub: dict[str, str], payload: str, private_key: str) -> None:
    info = {
        "endpoint": sub["endpoint"],
        "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]},
    }
    try:
        await asyncio.to_thread(send_webpush, info, payload, private_key)
    except SubscriptionGone:
        log.info("dropping gone push subscription %s", sub["endpoint"])
        await delete_subscription(sub["endpoint"])
    except Exception:  # noqa: BLE001 - 1 件の失敗で他の購読や本処理を止めない
        log.exception("web push failed for %s", sub["endpoint"])


async def notify_all(title: str, body: str, *, url: str = "/", tag: str = "") -> None:
    """全購読へ JSON ``{title, body, url, tag}`` を送る。失敗はログだけ。"""
    try:
        subs = await list_subscriptions()
        if not subs:
            return
        private_key, _ = load_or_create_vapid()
        payload = json.dumps(
            {"title": title, "body": body, "url": url, "tag": tag},
            ensure_ascii=False,
        )
        await asyncio.gather(
            *[_deliver(sub, payload, private_key) for sub in subs],
            return_exceptions=True,
        )
    except Exception:  # noqa: BLE001 - 呼び出し元のジョブ / ループは落とさない
        log.exception("web push notify_all failed")
