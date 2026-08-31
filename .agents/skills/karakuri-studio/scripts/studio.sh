#!/usr/bin/env bash
# Karakuri Media Studio の外部 API (/api/v1) を叩く curl ラッパー。
#
#   studio.sh GET  /projects
#   studio.sh GET  '/prompt-examples?mode=r2v'
#   studio.sh POST /projects '{"name":"新作"}'
#   studio.sh POST /jobs @/path/to/body.json
#   studio.sh PATCH /shots/<id> '{"prompt":"…","base_revision":12}'
#   studio.sh DELETE /takes/<id>
#   studio.sh wait-job <job_id> [interval_sec]   ジョブの完了まで待つ (既定 10 秒)
#   studio.sh wait-export <export_id> [interval_sec]
#
# 応答の body は標準出力へ、HTTP ステータスは成否によらず標準エラーへ 1 行
# (`-> 204`) 出す。body が空の 204 でも成否をこのラッパーだけで確かめられる。
#
# 接続先とキーの解決:
#   BASE = $KARAKURI_STUDIO_URL、無ければ <repo>/.env の HOST/PORT (既定 127.0.0.1:8000)
#   KEY  = $KARAKURI_STUDIO_API_KEY、無ければ <repo>/runtime/config.json の external_api_key
#   <repo> は $KARAKURI_STUDIO_REPO、無ければこのスクリプトの 4 つ上。
# キーの値は表示しない。
set -euo pipefail

die() { printf 'studio.sh: %s\n' "$*" >&2; exit 2; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
REPO="${KARAKURI_STUDIO_REPO:-$(cd "$SCRIPT_DIR/../../../.." && pwd -P)}"

# --- BASE ----------------------------------------------------------------
BASE="${KARAKURI_STUDIO_URL:-}"
if [[ -z "$BASE" ]]; then
  host=""; port=""
  if [[ -f "$REPO/.env" ]]; then
    host="$(sed -n 's/^HOST=//p' "$REPO/.env" | tail -n 1 | tr -d '"'\''[:space:]')"
    port="$(sed -n 's/^PORT=//p' "$REPO/.env" | tail -n 1 | tr -d '"'\''[:space:]')"
  fi
  [[ -n "$host" ]] || host="127.0.0.1"
  [[ -n "$port" ]] || port="8000"
  # 0.0.0.0 は「全インターフェイスで待受」の意味なので、宛先には使えない
  [[ "$host" == "0.0.0.0" || "$host" == "::" ]] && host="127.0.0.1"
  BASE="http://$host:$port"
fi
BASE="${BASE%/}"

# --- KEY -----------------------------------------------------------------
KEY="${KARAKURI_STUDIO_API_KEY:-}"
if [[ -z "$KEY" && -f "$REPO/runtime/config.json" ]]; then
  KEY="$(python3 - "$REPO/runtime/config.json" <<'PY' || true
import json, sys
try:
    with open(sys.argv[1], encoding="utf-8") as fh:
        print((json.load(fh).get("external_api_key") or "").strip())
except Exception:
    pass
PY
)"
fi
if [[ -z "$KEY" ]]; then
  die "API キーが見つかりません。環境変数 KARAKURI_STUDIO_API_KEY を設定するか、
  アプリの設定画面で外部 API キーを発行して $REPO/runtime/config.json に保存してください
  (キーの値はログや返答に貼らないこと)。"
fi

pretty() {
  python3 -c 'import json,sys
raw = sys.stdin.read()
try:
    print(json.dumps(json.loads(raw), ensure_ascii=False, indent=2))
except Exception:
    sys.stdout.write(raw)' 2>/dev/null || cat
}

# API を 1 回叩く。標準出力に body、戻り値はステータスの成否。
# $1 メソッド / $2 パス / $3 ボディ(任意。@file でファイル)
call() {
  local method="$1" path="$2" body="${3:-}" tmp status
  [[ "$path" == /* ]] || path="/$path"
  [[ "$path" == /api/v1/* ]] || path="/api/v1$path"
  tmp="$(mktemp)"
  local -a args=(-sS -o "$tmp" -w '%{http_code}' -X "$method"
                 -H "X-API-Key: $KEY" -H "Accept: application/json")
  if [[ -n "$body" ]]; then
    args+=(-H "Content-Type: application/json" --data-binary "$body")
  fi
  status="$(curl "${args[@]}" "$BASE$path" 2>&1)" || {
    rm -f "$tmp"
    die "接続できません ($BASE$path)。アプリが起動していなければ $REPO/run.sh を実行してください。"
  }
  pretty < "$tmp"
  rm -f "$tmp"
  case "$status" in
    # ステータスは常に stderr に出す（204 のようにボディが無い応答でも、
    # 成否をこのラッパーだけで確かめられるように）。stdout は body のまま。
    2*) printf -- '-> %s\n' "$status" >&2; return 0 ;;
    404) printf 'studio.sh: HTTP 404 (パスが違うか、外部 API キーが未設定です)\n' >&2 ;;
    401) printf 'studio.sh: HTTP 401 (API キーが一致しません)\n' >&2 ;;
    409) printf 'studio.sh: HTTP 409 (base_revision が古い。取得しなおしてから出し直す)\n' >&2 ;;
    429) printf 'studio.sh: HTTP 429 (未完了のジョブ / 書き出しが上限。完了を待つ)\n' >&2 ;;
    *)   printf 'studio.sh: HTTP %s\n' "$status" >&2 ;;
  esac
  return 1
}

# $1 パス / $2 終端状態の正規表現 / $3 間隔秒
poll() {
  local path="$1" terminal="$2" interval="$3" out status
  # 5 秒未満のポーリングはしない
  [[ "$interval" =~ ^[0-9]+$ ]] || interval=10
  (( interval < 5 )) && interval=5
  while :; do
    out="$(call GET "$path")" || { printf '%s\n' "$out"; return 1; }
    status="$(printf '%s' "$out" | python3 -c 'import json,sys
try:
    print(json.load(sys.stdin).get("status", ""))
except Exception:
    print("")')"
    if [[ "$status" =~ $terminal ]]; then
      printf '%s\n' "$out"
      [[ "$status" == "done" ]] && return 0 || return 1
    fi
    printf 'studio.sh: %s … (%s)\n' "${status:-unknown}" "$path" >&2
    sleep "$interval"
  done
}

case "${1:-}" in
  ""|-h|--help)
    sed -n '2,23p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    exit 0 ;;
  wait-job)
    [[ -n "${2:-}" ]] || die "使い方: studio.sh wait-job <job_id> [interval_sec]"
    poll "/jobs/$2" '^(done|failed|canceled)$' "${3:-10}" ;;
  wait-export)
    [[ -n "${2:-}" ]] || die "使い方: studio.sh wait-export <export_id> [interval_sec]"
    poll "/exports/$2" '^(done|failed)$' "${3:-10}" ;;
  GET|POST|PATCH|PUT|DELETE|HEAD)
    [[ -n "${2:-}" ]] || die "使い方: studio.sh $1 <path> [json|@file]"
    call "$1" "$2" "${3:-}" ;;
  *)
    die "不明なコマンド '$1'（GET/POST/PATCH/PUT/DELETE か wait-job / wait-export）" ;;
esac
