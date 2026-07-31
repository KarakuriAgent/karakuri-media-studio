#!/usr/bin/env python3
"""Pod の中でモデルを落とすための小さな HTTP API（deploy/runpod/README.md）。

アプリ（設定ページの [DL] / [全DL]）は Pod のファイルシステムに手が届かないので、
ここに依頼する。``entrypoint.sh`` が起動し、外向きには caddy が ComfyUI と**同じ
認証**で ``/studio/models/*`` をこのポートに渡す（Caddyfile の handle_path が
接頭辞を落とすので、ここから見えるパスは ``/download`` / ``/downloads``）。

エンドポイント::

    POST /download   {"filename": "x.safetensors", "url": "https://…",
                      "subfolder": "loras"}   -> 状態 1 件（すぐ返る）
    GET  /downloads                            -> 状態の一覧

状態は ``backend/app/models.py`` の ``ModelDownload`` と同じ形にしてある
（アプリはこれをそのまま WS の ``model_download`` フレームとして流す）::

    {"filename", "status", "received", "total", "error", "subfolder", "url", "path"}

実際のダウンロードは :mod:`download_models` をそのまま使う（``.part`` への追記と
``Range`` レジューム、リダイレクトごとの認証、指数バックオフの再試行つき）。
認証トークンは Pod の環境変数 ``HF_TOKEN`` / ``CIVITAI_API_KEY`` を使う。

進捗はメモリにしか持たない: Pod を作り直せば消えるが、置いたファイルは Network
Volume に残るので、アプリ側は「未検出」が消えることで完了を知れる。
"""

from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import download_models

#: 待ち受け（外には出さない。公開は caddy 経由だけ）
HOST = os.environ.get("MODEL_API_HOST", "127.0.0.1")
PORT = int(os.environ.get("MODEL_API_PORT", "8190"))
#: 置き場所（entrypoint.sh が ComfyUI の models ディレクトリを渡す）
MODELS_DIR = Path(os.environ.get("MODELS_DIR", "/workspace/ComfyUI/models"))
#: 進捗の更新間隔（バイト）。チャンクごとにロックを取らないための間引き。
PROGRESS_BYTES = 4 * 1024 * 1024

_states: dict[str, dict] = {}
_lock = threading.Lock()


class ApiError(Exception):
    """呼び出し側の指定不備（400 で返す）。"""


def _check_name(name: str, label: str) -> str:
    """ファイル名 1 要素の検証（区切り文字と ``..`` を許さない）。"""
    cleaned = (name or "").strip()
    if not cleaned:
        raise ApiError(f"{label}が空です")
    if "/" in cleaned or "\\" in cleaned or cleaned in {".", ".."}:
        raise ApiError(f"{label}にパス区切りは使えません: {name}")
    return cleaned


def resolve_target(subfolder: str, filename: str) -> Path:
    """保存先の絶対パス。models ディレクトリの外に出る指定は拒否する。"""
    base = MODELS_DIR.expanduser().resolve()
    name = _check_name(filename, "ファイル名")
    raw = (subfolder or "").strip().replace("\\", "/")
    if raw.startswith("/") or Path(raw).is_absolute():
        raise ApiError(f"置き場所に絶対パスは使えません: {subfolder}")
    relative = raw.strip("/")
    if relative:
        for part in relative.split("/"):
            _check_name(part, "置き場所")
    target = (base / relative / name).resolve()
    parent = target.parent
    if parent != base and base not in parent.parents:
        raise ApiError(f"models ディレクトリの外は指定できません: {subfolder}")
    return target


def _run(state: dict, target: Path) -> None:
    """1 件ぶんのダウンロード（スレッドで走る。例外は状態に残す）。"""
    last = 0

    def progress(received: int, total: int) -> None:
        nonlocal last
        if received - last < PROGRESS_BYTES and received >= last:
            return
        last = received
        with _lock:
            state["received"] = received
            state["total"] = total or None

    try:
        download_models.download(state["url"], target, progress)
    except Exception as exc:  # noqa: BLE001 - 失敗は状態として見せるだけ
        with _lock:
            state["status"] = "error"
            state["error"] = f"{type(exc).__name__}: {exc}"
        print(f"[model-api] FAILED {state['filename']}: {exc}", flush=True)
        return
    size = target.stat().st_size if target.exists() else state["received"]
    with _lock:
        state["status"] = "done"
        state["received"] = size
        state["total"] = state["total"] or size
        state["error"] = None
    print(f"[model-api] done {state['filename']} ({size} bytes)", flush=True)


def start(filename: str, url: str, subfolder: str = "") -> dict:
    """ダウンロードを 1 件始めて、その状態を返す。"""
    name = _check_name(filename, "ファイル名")
    address = (url or "").strip()
    if not address.startswith(("http://", "https://")):
        raise ApiError(f"http(s) の URL を指定してください: {url}")
    target = resolve_target(subfolder, name)

    with _lock:
        current = _states.get(name)
        if current is not None and current["status"] == "downloading":
            raise ApiError(f"すでにダウンロード中です: {name}")
        state = {
            "filename": name,
            "status": "downloading",
            "received": 0,
            "total": None,
            "error": None,
            "subfolder": (subfolder or "").strip().strip("/"),
            "url": address,
            "path": str(target),
        }
        _states[name] = state

    print(f"[model-api] downloading {name} -> {target}", flush=True)
    thread = threading.Thread(target=_run, args=(state, target), daemon=True)
    thread.start()
    return state


def snapshot() -> list[dict]:
    with _lock:
        return [dict(state) for state in _states.values()]


class Handler(BaseHTTPRequestHandler):
    server_version = "karakuri-model-api/1"

    def _send(self, status: int, payload) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler の作法
        if self.path.rstrip("/") in ("/downloads", ""):
            self._send(200, snapshot())
            return
        self._send(404, {"error": f"unknown path: {self.path}"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path.rstrip("/") != "/download":
            self._send(404, {"error": f"unknown path: {self.path}"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except ValueError as exc:
            self._send(400, {"error": f"invalid JSON: {exc}"})
            return
        try:
            state = start(
                str(payload.get("filename") or ""),
                str(payload.get("url") or ""),
                str(payload.get("subfolder") or ""),
            )
        except ApiError as exc:
            self._send(400, {"error": str(exc)})
            return
        self._send(200, state)

    def log_message(self, fmt: str, *args) -> None:
        # 既定の stderr ログはアクセスのたびに出て煩いので、こちらの print に寄せる
        pass


def main() -> int:
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"[model-api] listening on {HOST}:{PORT} (models: {MODELS_DIR})", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
