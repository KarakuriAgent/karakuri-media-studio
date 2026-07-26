#!/usr/bin/env bash
# Video Studio 起動スクリプト
#
#   ./run.sh          本番モード: frontend をビルドして FastAPI から SPA ごと配信
#   ./run.sh --dev    開発モード: uvicorn --reload と vite dev を並行起動
#
# 環境変数:
#   HOST / PORT        バックエンドの待受 (既定 127.0.0.1 / 8000)
#   PYTHON             venv 作成に使う python (既定 python3.12 → python3)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8000}"
VENV="$ROOT/.venv"

DEV=0
[[ "${1:-}" == "--dev" ]] && DEV=1

log() { printf '\033[36m[run]\033[0m %s\n' "$*"; }
die() { printf '\033[31m[run]\033[0m %s\n' "$*" >&2; exit 1; }

# --- Python 環境 ---------------------------------------------------------
if [[ ! -x "$VENV/bin/python" ]]; then
  PY="${PYTHON:-}"
  if [[ -z "$PY" ]]; then
    if command -v python3.12 >/dev/null 2>&1; then PY=python3.12; else PY=python3; fi
  fi
  command -v "$PY" >/dev/null 2>&1 || die "python が見つかりません ($PY)"
  log "venv を作成: $VENV ($PY)"
  "$PY" -m venv "$VENV"
  "$VENV/bin/pip" install --upgrade pip
  "$VENV/bin/pip" install -r "$ROOT/backend/requirements.txt"
fi

command -v ffmpeg >/dev/null 2>&1 || log "警告: ffmpeg が見つかりません（ラストフレーム抽出が失敗します）"

# --- フロントエンド ------------------------------------------------------
need_node() {
  command -v npm >/dev/null 2>&1 || die "npm が見つかりません（Node.js を導入してください）"
}

if [[ ! -d "$ROOT/frontend/node_modules" ]]; then
  need_node
  log "npm install"
  (cd "$ROOT/frontend" && npm install)
fi

if [[ $DEV -eq 0 && ! -f "$ROOT/frontend/dist/index.html" ]]; then
  need_node
  log "npm run build"
  (cd "$ROOT/frontend" && npm run build)
fi

# --- 起動 ----------------------------------------------------------------
export PYTHONPATH="$ROOT/backend${PYTHONPATH:+:$PYTHONPATH}"

if [[ $DEV -eq 1 ]]; then
  log "開発モード: backend http://$HOST:$PORT / frontend http://localhost:5173"
  pids=()
  cleanup() { trap - INT TERM EXIT; for pid in "${pids[@]}"; do kill "$pid" 2>/dev/null || true; done; }
  trap cleanup INT TERM EXIT

  "$VENV/bin/uvicorn" app.main:app --host "$HOST" --port "$PORT" --reload \
    --reload-dir "$ROOT/backend" &
  pids+=($!)
  (cd "$ROOT/frontend" && npm run dev) &
  pids+=($!)
  wait -n
  exit $?
fi

log "http://$HOST:$PORT で起動します"
exec "$VENV/bin/uvicorn" app.main:app --host "$HOST" --port "$PORT"
