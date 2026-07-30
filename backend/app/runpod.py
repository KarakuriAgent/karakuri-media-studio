"""RunPod の Pod で動かす ComfyUI の自動起動（SPEC §5.1）。

ComfyUI を RunPod の Pod（GPU 時間貸し）に置くと、使っていない間は Pod を落として
おきたい。落ちている状態でジョブを投げても ComfyUI には届かないので、**ジョブの
投入直前に疎通を確かめ、通らなければ Pod を立ち上げて待つ**のがこのモジュール。

- 疎通確認は :mod:`app.comfy` をそのまま使う（設定の ``comfy_url`` /
  ``comfy_api_key`` が唯一の接続情報。Pod 側は Cloudflare Tunnel で固定ホスト名を
  持つので、起動のたびにアプリの設定を書き換える必要はない）
- Pod の作成は RunPod REST API（``https://rest.runpod.io/v1/pods``）。GPU が確保
  できない等のエラーは**そのままユーザー向けのエラーにする**（別の GPU や別の
  クラウドへ勝手に振り替えると、想定より高い課金が黙って起きるため）
- 起動待ちは初回のモデルダウンロードを見込んで長め（既定 15 分）
- 同時に複数のジョブが走っても Pod を 2 つ作らないよう、:class:`asyncio.Lock` で
  single-flight にする（待たされた 2 本目は、ロックを取った時点でもう一度疎通を
  確かめてから何もせず抜ける）

Pod の**停止**はアプリ側では行わない。アイドルで自分を terminate するのは Pod の
中の watchdog（``deploy/runpod/watchdog.py``）の役目で、アプリが落ちていても課金が
止まるようにしてある。
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from . import comfy
from .config import load_settings

log = logging.getLogger(__name__)

#: RunPod REST API のベース URL
API_BASE = "https://rest.runpod.io/v1"
#: API 1 コールのタイムアウト（作成は数秒で返る）
API_TIMEOUT = 60.0
#: 起動待ちの全体タイムアウト（秒）。初回は Network Volume へのモデル配置が
#: 走るため、ComfyUI が上がるまで 10 分以上かかることがある。
BOOT_TIMEOUT = 15 * 60.0
#: 疎通確認の間隔（秒）
POLL_INTERVAL = 10.0

#: Pod 作成時の cloudType（COMMUNITY のほうが安い。確保できなければエラー）
CLOUD_TYPE = "COMMUNITY"

#: 進捗コールバック（起動待ちの間、ジョブの WS メッセージとして流す）
ProgressCallback = Callable[[str], Awaitable[None]]

#: 直近に作った Pod の id（プロセスが生きている間だけ覚えている）。次のジョブでも
#: 疎通しないときは、この Pod を作り直さずに状態を見てエラー文言に添える。
_pod_id: str | None = None
#: 起動処理の single-flight（同時ジョブで Pod を 2 つ作らない）
_lock = asyncio.Lock()


class RunPodError(Exception):
    """Pod の作成・起動待ちの失敗（ジョブの失敗理由としてそのまま出す）。"""


# --------------------------------------------------------------------------
# REST API
# --------------------------------------------------------------------------

def _api_key() -> str:
    key = (load_settings().runpod_api_key or "").strip()
    if not key:
        raise RunPodError("RunPod API キーが設定されていません（設定 → 接続 / Grok）")
    return key


async def _request(method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
    """RunPod REST API を 1 回叩く。失敗は :class:`RunPodError` にまとめる。"""
    headers = {"Authorization": f"Bearer {_api_key()}"}
    try:
        async with httpx.AsyncClient(timeout=API_TIMEOUT) as client:
            response = await client.request(
                method, f"{API_BASE}{path}", headers=headers, json=payload
            )
    except httpx.HTTPError as exc:
        raise RunPodError(f"RunPod API に接続できません: {exc}") from exc
    if response.status_code >= 400:
        body = response.text[:500]
        raise RunPodError(
            f"RunPod API {method} {path} が失敗しました: "
            f"HTTP {response.status_code} {body}"
        )
    if not response.content:
        return None
    try:
        return response.json()
    except ValueError as exc:
        raise RunPodError(f"RunPod API が JSON を返しませんでした: {exc}") from exc


async def create_pod() -> str:
    """設定どおりの Pod を 1 つ作り、その id を返す。

    ``templateId`` にイメージ・公開ポート・環境変数をまとめてあるので、ここで
    渡すのは「どの GPU で・どの Network Volume を付けて」だけ。GPU が確保できない
    場合は RunPod が 4xx を返すので、そのままエラーにする（フォールバック無し）。
    """
    settings = load_settings()
    template_id = (settings.runpod_template_id or "").strip()
    if not template_id:
        raise RunPodError("RunPod テンプレート ID が設定されていません")
    gpu_type = (settings.runpod_gpu_type or "").strip()
    if not gpu_type:
        raise RunPodError("RunPod の GPU 種別が設定されていません")

    payload: dict[str, Any] = {
        "name": "karakuri-media-studio",
        "templateId": template_id,
        "cloudType": CLOUD_TYPE,
        "computeType": "GPU",
        "gpuTypeIds": [gpu_type],
        "gpuCount": 1,
    }
    volume_id = (settings.runpod_network_volume_id or "").strip()
    if volume_id:
        payload["networkVolumeId"] = volume_id

    data = await _request("POST", "/pods", payload)
    pod_id = data.get("id") if isinstance(data, dict) else None
    if not pod_id:
        raise RunPodError(f"RunPod が Pod の id を返しませんでした: {data!r}")
    log.info("runpod: created pod %s (%s, %s)", pod_id, gpu_type, CLOUD_TYPE)
    return str(pod_id)


async def get_pod(pod_id: str) -> dict[str, Any] | None:
    """Pod の現在の状態（消えていれば ``None``）。エラー文言に添えるだけの補助。"""
    try:
        data = await _request("GET", f"/pods/{pod_id}")
    except RunPodError as exc:
        log.debug("runpod: could not read pod %s: %s", pod_id, exc)
        return None
    return data if isinstance(data, dict) else None


# --------------------------------------------------------------------------
# 起動
# --------------------------------------------------------------------------

async def comfy_reachable() -> bool:
    """``comfy_url`` の ComfyUI に届くか（``/object_info` の到達性）。"""
    try:
        await comfy.get_object_info()
    except comfy.ComfyError:
        return False
    return True


async def _notify(on_progress: ProgressCallback | None, message: str) -> None:
    if on_progress is None:
        return
    try:
        await on_progress(message)
    except Exception:  # noqa: BLE001 - 進捗通知の失敗で起動を止めない
        log.debug("runpod: progress callback failed", exc_info=True)


async def ensure_pod_running(on_progress: ProgressCallback | None = None) -> None:
    """ComfyUI に繋がる状態にして戻る。繋がらないままなら :class:`RunPodError`。

    設定で無効なとき、および既に疎通しているときは何もしない（このため、ジョブの
    実行経路に無条件に置いてよい）。
    """
    settings = load_settings()
    if not settings.runpod_enabled:
        return

    global _pod_id
    async with _lock:
        # ロック待ちの間に別のジョブが起動を済ませていることがある。
        if await comfy_reachable():
            return

        await _notify(on_progress, "RunPod の Pod を起動しています…")
        _pod_id = await create_pod()
        pod_id = _pod_id

        deadline = time.monotonic() + BOOT_TIMEOUT
        announced = 0.0
        while time.monotonic() < deadline:
            await asyncio.sleep(POLL_INTERVAL)
            if await comfy_reachable():
                log.info("runpod: pod %s is serving ComfyUI", pod_id)
                await _notify(on_progress, "ComfyUI に接続しました")
                return
            waited = time.monotonic() - (deadline - BOOT_TIMEOUT)
            # 1 分ごとに「まだ待っている」ことだけ知らせる（初回はモデルの
            # ダウンロードで 10 分以上かかることがあるため）。
            if waited - announced >= 60.0:
                announced = waited
                await _notify(
                    on_progress,
                    f"Pod の起動を待っています…（{int(waited // 60)} 分経過）",
                )

        detail = ""
        pod = await get_pod(pod_id)
        if pod:
            detail = f"（Pod {pod_id} の状態: {pod.get('desiredStatus') or '不明'}）"
        raise RunPodError(
            f"RunPod の Pod を起動しましたが、{int(BOOT_TIMEOUT // 60)} 分以内に "
            f"ComfyUI ({settings.comfy_url}) へ接続できませんでした{detail}。"
            " Pod のログと Cloudflare Tunnel の設定を確認してください。"
        )


def current_pod_id() -> str | None:
    """このプロセスが最後に作った Pod の id（テスト・デバッグ用）。"""
    return _pod_id
