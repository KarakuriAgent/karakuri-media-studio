#!/usr/bin/env python3
"""アイドルの Pod を自分で terminate する見張り番。

RunPod の Pod は起動している間ずっと課金されるので、「キューが空で何も実行して
いない」状態が ``IDLE_TIMEOUT_MINUTES``（既定 10 分）続いたら RunPod REST API で
自分自身を消す。アプリ側（``backend/app/runpod.py``）は起動しかしないので、
**止めるのはここだけの責任**。アプリが落ちていても課金は止まる。

判定は ComfyUI の 2 つのエンドポイントだけを見る:

- ``GET /queue``  … ``queue_running`` / ``queue_pending`` が両方空か
- ``GET /prompt`` … ``exec_info.queue_remaining`` が 0 か（/queue の裏取り）

誤爆を防ぐための決め事:

- 起動直後は ``STARTUP_GRACE_MINUTES``（既定 15 分）のあいだ絶対に消さない。
  初回はモデルのダウンロードと ComfyUI の起動でここまでかかることがある
- ComfyUI に**繋がらない**間はアイドル時間を数えない（まだ起動中とみなす）。
  「起動しきらない Pod が消えない」のは困るが、それはアプリ側の起動待ち
  タイムアウトでユーザーに見えるので、ここでは黙って消さないほうを選ぶ
- ``RUNPOD_API_KEY`` か ``RUNPOD_POD_ID`` が無ければ監視だけして何もしない
  （ローカルで動かしたときに自分を消そうとしない）

環境変数:
    RUNPOD_API_KEY         RunPod REST API キー（無ければ terminate しない）
    RUNPOD_POD_ID          自分の Pod ID（RunPod が自動で入れる）
    IDLE_TIMEOUT_MINUTES   アイドル判定の継続時間（既定 10）
    STARTUP_GRACE_MINUTES  起動直後の猶予（既定 15）
    COMFY_URL              ComfyUI の URL（既定 http://127.0.0.1:8188）
    POLL_SECONDS           ポーリング間隔（既定 30）
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

RUNPOD_API = "https://rest.runpod.io/v1"


def env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    try:
        return float(raw) if raw else default
    except ValueError:
        log(f"{name} の値が数値ではありません: {raw!r}（既定 {default} を使います）")
        return default


def log(message: str) -> None:
    print(f"[watchdog] {message}", flush=True)


def get_json(url: str, *, headers: dict[str, str] | None = None, timeout: float = 10.0):
    request = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read()
    return json.loads(body) if body else None


def is_idle(comfy_url: str) -> bool | None:
    """キューが空で実行中も無ければ ``True``。繋がらなければ ``None``。"""
    try:
        queue = get_json(f"{comfy_url}/queue")
        prompt = get_json(f"{comfy_url}/prompt")
    except (urllib.error.URLError, OSError, ValueError, TimeoutError) as exc:
        log(f"ComfyUI に問い合わせできません: {exc}")
        return None
    if not isinstance(queue, dict) or not isinstance(prompt, dict):
        return None
    running = queue.get("queue_running") or []
    pending = queue.get("queue_pending") or []
    remaining = (prompt.get("exec_info") or {}).get("queue_remaining", 0)
    return not running and not pending and not remaining


def terminate(pod_id: str, api_key: str) -> bool:
    """自分の Pod を消す（DELETE /v1/pods/{id}）。"""
    request = urllib.request.Request(
        f"{RUNPOD_API}/pods/{pod_id}",
        method="DELETE",
        headers={"Authorization": f"Bearer {api_key}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30.0) as response:
            log(f"terminate 要求を送りました (HTTP {response.status})")
        return True
    except urllib.error.HTTPError as exc:
        log(f"terminate に失敗しました: HTTP {exc.code} {exc.read()[:200]!r}")
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        log(f"terminate に失敗しました: {exc}")
    return False


def main() -> int:
    comfy_url = os.environ.get("COMFY_URL", "http://127.0.0.1:8188").rstrip("/")
    idle_timeout = env_float("IDLE_TIMEOUT_MINUTES", 10.0) * 60.0
    grace = env_float("STARTUP_GRACE_MINUTES", 15.0) * 60.0
    poll = env_float("POLL_SECONDS", 30.0)
    api_key = os.environ.get("RUNPOD_API_KEY", "").strip()
    pod_id = os.environ.get("RUNPOD_POD_ID", "").strip()

    if not api_key or not pod_id:
        log(
            "RUNPOD_API_KEY / RUNPOD_POD_ID が無いので、監視のみ行います"
            "（自動 terminate はしません）"
        )

    started = time.monotonic()
    idle_since: float | None = None
    log(
        f"監視開始: idle={idle_timeout / 60:.0f}分 / grace={grace / 60:.0f}分 / "
        f"poll={poll:.0f}秒 / {comfy_url}"
    )

    while True:
        time.sleep(poll)
        now = time.monotonic()
        idle = is_idle(comfy_url)

        if idle is None or not idle:
            # 繋がらない（まだ起動中）か、仕事をしている。どちらも計測をやり直す。
            if idle_since is not None:
                log("アイドル計測をリセットしました")
            idle_since = None
            continue

        if idle_since is None:
            idle_since = now
            log("アイドルになりました")
            continue

        if now - started < grace:
            continue  # 起動直後は消さない（初回のモデル取得で長くかかる）
        if now - idle_since < idle_timeout:
            continue

        log(f"{idle_timeout / 60:.0f} 分アイドルが続いたので Pod を終了します")
        if not api_key or not pod_id:
            log("terminate に必要な環境変数が無いので何もしません")
            idle_since = now  # 数え直す（ログを垂れ流さない）
            continue
        if terminate(pod_id, api_key):
            return 0
        idle_since = now  # 失敗したら次のアイドル継続で再挑戦する


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        sys.exit(0)
