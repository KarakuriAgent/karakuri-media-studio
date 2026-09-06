#!/usr/bin/env bash
# Karakuri Media Studio のセットアップ状態を見る / 記録するツール。
#
#   setup.sh status                        保存状態＋自動検出を段階ごとに出し、
#                                          最後に「次にやる段階」を 1 行
#   setup.sh check [--json]                自動検出だけ
#   setup.sh mark <step> <done|skipped|failed> [note]
#   setup.sh choose <key> <value>          選択の記録 (launch / comfy_target …)
#   setup.sh reset [--yes]                 状態ファイルを消す
#
# 段階 (SKILL.md 参照):
#   S0 環境確認 / S1 起動方法の選択 / S2 起動と疎通 / S3 ComfyUI の接続先 /
#   S4 custom node とモデル / S5 grok CLI / S6 外部 API キー /
#   S7 任意機能 / S8 動作確認
#
# 状態ファイル: <repo>/runtime/setup-state.json（gitignore 済み）
#   {"version":1,"choices":{…},"steps":{"S0":{"status":"done","updated_at":…,"note":…}}}
#
# 接続先の解決は studio.sh と同じ:
#   BASE = $KARAKURI_STUDIO_URL、無ければ <repo>/.env の HOST/PORT（既定 127.0.0.1:8000）
#   <repo> は $KARAKURI_STUDIO_REPO、無ければこのスクリプトの 4 つ上。
# **API キーやトークンの値は絶対に表示しない**（有無だけを出す）。
set -euo pipefail

die() { printf 'setup.sh: %s\n' "$*" >&2; exit 2; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
REPO="${KARAKURI_STUDIO_REPO:-$(cd "$SCRIPT_DIR/../../../.." && pwd -P)}"
STATE="$REPO/runtime/setup-state.json"
CONFIG="$REPO/runtime/config.json"

STEP_IDS=(S0 S1 S2 S3 S4 S5 S6 S7 S8)
step_label() {
  case "$1" in
    S0) echo "環境確認（python / node / npm / ffmpeg / git / docker / GPU）" ;;
    S1) echo "起動方法の選択（ホスト or Docker、HOST / PORT）" ;;
    S2) echo "起動と疎通（GET /api/health が返る）" ;;
    S3) echo "ComfyUI の接続先（local / comfy_cloud / runpod）" ;;
    S4) echo "custom node とモデル" ;;
    S5) echo "grok CLI（インストールとサインインは人の作業）" ;;
    S6) echo "外部 API キーの発行" ;;
    S7) echo "任意機能（Remotion / 音源解析 / RunPod / モデル DL）" ;;
    S8) echo "動作確認（画像ジョブ 1 本）" ;;
    *)  echo "不明な段階" ;;
  esac
}

# --- .env / BASE ---------------------------------------------------------
env_value() {  # $1 キー名 → <repo>/.env の値（無ければ空）
  [[ -f "$REPO/.env" ]] || return 0
  sed -n "s/^$1=//p" "$REPO/.env" | tail -n 1 | tr -d '"'\''' | sed 's/[[:space:]]*$//'
}

ENV_HOST="$(env_value HOST)"
ENV_PORT="$(env_value PORT)"
ENV_MODELS_DIR="${COMFY_MODELS_DIR:-$(env_value COMFY_MODELS_DIR)}"

BASE="${KARAKURI_STUDIO_URL:-}"
if [[ -z "$BASE" ]]; then
  host="${ENV_HOST:-127.0.0.1}"
  port="${ENV_PORT:-8000}"
  # 0.0.0.0 は「全インターフェイスで待受」の意味なので宛先には使えない
  [[ "$host" == "0.0.0.0" || "$host" == "::" ]] && host="127.0.0.1"
  BASE="http://$host:$port"
fi
BASE="${BASE%/}"

# --- 自動検出 ------------------------------------------------------------
have() { command -v "$1" >/dev/null 2>&1; }

# 「見つからない」は空文字で表す（表示側で「なし」にする）
PY_BIN=""; PY_VER=""; PY_OK=no
for cand in python3.13 python3.12 python3; do
  if have "$cand"; then
    v="$("$cand" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])' 2>/dev/null || true)"
    [[ -n "$v" ]] || continue
    if [[ -z "$PY_BIN" ]]; then PY_BIN="$cand"; PY_VER="$v"; fi
    major="${v%%.*}"; rest="${v#*.}"; minor="${rest%%.*}"
    if (( major > 3 || (major == 3 && minor >= 12) )); then
      PY_BIN="$cand"; PY_VER="$v"; PY_OK=yes; break
    fi
  fi
done

NODE_VER=""; NODE_OK=no
if have node; then
  NODE_VER="$(node --version 2>/dev/null | tr -d 'v' || true)"
  major="${NODE_VER%%.*}"
  [[ "$major" =~ ^[0-9]+$ ]] && (( major >= 18 )) && NODE_OK=yes
fi

NPM_VER=""; have npm && NPM_VER="$(npm --version 2>/dev/null || true)"
FFMPEG_VER=""; have ffmpeg && FFMPEG_VER="$(ffmpeg -version 2>/dev/null | head -n 1 | awk '{print $3}')"
GIT_VER=""; have git && GIT_VER="$(git --version 2>/dev/null | awk '{print $3}')"
DOCKER_VER=""; have docker && DOCKER_VER="$(docker --version 2>/dev/null | awk '{print $3}' | tr -d ',')"
GPU=""
if have nvidia-smi; then
  GPU="$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -n 1 || true)"
fi

dir_state() { [[ -d "$1" ]] && echo yes || echo no; }
VENV="$(dir_state "$REPO/.venv")"
FRONT_MODULES="$(dir_state "$REPO/frontend/node_modules")"
FRONT_DIST="$([[ -f "$REPO/frontend/dist/index.html" ]] && echo yes || echo no)"
REMOTION_MODULES="$(dir_state "$REPO/remotion/node_modules")"

# --- アプリの疎通（GET /api/health）--------------------------------------
APP="down"; COMFYUI_STATUS=""; COMFYUI_DETAIL=""; GROK_STATUS=""; GROK_DETAIL=""; CLI_LABEL=""
HEALTH_RAW="$(curl -sS -m 8 "$BASE/api/health" 2>/dev/null || true)"
if [[ -n "$HEALTH_RAW" ]]; then
  read -r APP COMFYUI_STATUS GROK_STATUS CLI_LABEL <<<"$(
    printf '%s' "$HEALTH_RAW" | python3 -c 'import json,sys
try:
    d = json.load(sys.stdin)
except Exception:
    raise SystemExit(0)
print(d.get("app") or "up",
      (d.get("comfyui") or {}).get("status") or "-",
      (d.get("grok") or {}).get("status") or "-",
      (d.get("cli_label") or "-").replace(" ", "_"))' 2>/dev/null || true
  )" || true
  detail() {
    printf '%s' "$HEALTH_RAW" | python3 -c 'import json,sys
try:
    print(((json.load(sys.stdin).get(sys.argv[1]) or {}).get("detail") or "").replace("\n", " "))
except Exception:
    pass' "$1" 2>/dev/null || true
  }
  COMFYUI_DETAIL="$(detail comfyui)"
  GROK_DETAIL="$(detail grok)"
  [[ -n "$APP" ]] || APP="up"
else
  APP="down"
fi

# --- runtime/config.json（値は読まない。有無だけ）-------------------------
CFG_TARGET=""; CFG_API_KEY="no"; CFG_REMOTION="no"; CFG_AUDIO_PY=""; CFG_AUDIO_PY_EXISTS="-"
if [[ -f "$CONFIG" ]]; then
  read -r CFG_TARGET CFG_API_KEY CFG_REMOTION <<<"$(
    python3 -c 'import json,sys
try:
    with open(sys.argv[1], encoding="utf-8") as fh:
        d = json.load(fh)
except Exception:
    d = {}
print(d.get("comfy_target") or "-",
      "yes" if (d.get("external_api_key") or "").strip() else "no",
      "yes" if d.get("remotion_enabled") else "no")' "$CONFIG" 2>/dev/null || echo "- no no"
  )" || true
  CFG_AUDIO_PY="$(python3 -c 'import json,sys
try:
    with open(sys.argv[1], encoding="utf-8") as fh:
        print((json.load(fh).get("audio_analysis_python") or "").strip())
except Exception:
    pass' "$CONFIG" 2>/dev/null || true)"
  if [[ -n "$CFG_AUDIO_PY" ]]; then
    CFG_AUDIO_PY_EXISTS="$([[ -x "$CFG_AUDIO_PY" ]] && echo yes || echo no)"
  fi
fi

# --- 段階ごとの自動判定 ---------------------------------------------------
# ok = 自動検出だけで満たしていると言える / ng = 満たしていない / ? = 人の判断が要る
auto_verdict() {
  case "$1" in
    S0) if [[ "$PY_OK" == yes && "$NODE_OK" == yes && -n "$NPM_VER" && -n "$FFMPEG_VER" && -n "$GIT_VER" ]];
        then echo ok; else echo ng; fi ;;
    S1) [[ -n "$(choice_value launch)" ]] && echo ok || echo ng ;;
    S2) [[ "$APP" == "ok" || "$APP" == "up" ]] && echo ok || echo ng ;;
    S3) [[ "$COMFYUI_STATUS" == "ok" ]] && echo ok || echo ng ;;
    S4) [[ "$COMFYUI_STATUS" == "ok" ]] && echo ok || echo ng ;;
    S5) [[ "$GROK_STATUS" == "ok" ]] && echo ok || echo ng ;;
    S6) [[ "$CFG_API_KEY" == "yes" ]] && echo ok || echo ng ;;
    *)  echo '?' ;;
  esac
}

# --- 状態ファイル ---------------------------------------------------------
state_get() {  # $1 step → "status<TAB>updated_at<TAB>note"
  [[ -f "$STATE" ]] || return 0
  python3 -c 'import json,sys
try:
    with open(sys.argv[1], encoding="utf-8") as fh:
        d = json.load(fh)
except Exception:
    raise SystemExit(0)
s = (d.get("steps") or {}).get(sys.argv[2]) or {}
if s:
    print("\t".join([str(s.get("status") or "-"),
                     str(s.get("updated_at") or ""),
                     str(s.get("note") or "").replace("\n", " ")]))' "$STATE" "$1" 2>/dev/null || true
}

choice_value() {  # $1 key → 値（無ければ空）
  [[ -f "$STATE" ]] || return 0
  python3 -c 'import json,sys
try:
    with open(sys.argv[1], encoding="utf-8") as fh:
        d = json.load(fh)
except Exception:
    raise SystemExit(0)
print(str((d.get("choices") or {}).get(sys.argv[2]) or ""))' "$STATE" "$1" 2>/dev/null || true
}

state_write() {  # $1 種別(step|choice) $2 キー $3 値 $4 メモ
  mkdir -p "$(dirname "$STATE")"
  python3 -c 'import json, os, sys
from datetime import datetime, timezone

path, kind, key, value = sys.argv[1:5]
note = sys.argv[5] if len(sys.argv) > 5 else ""
try:
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError
except Exception:
    data = {}
data.setdefault("version", 1)
data.setdefault("choices", {})
data.setdefault("steps", {})
now = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
if kind == "step":
    entry = {"status": value, "updated_at": now}
    if note:
        entry["note"] = note
    data["steps"][key] = entry
else:
    data["choices"][key] = value
data["updated_at"] = now
tmp = path + ".tmp"
with open(tmp, "w", encoding="utf-8") as fh:
    json.dump(data, fh, ensure_ascii=False, indent=2)
    fh.write("\n")
os.replace(tmp, path)' "$STATE" "$1" "$2" "$3" "${4:-}"
}

# --- 出力 ----------------------------------------------------------------
# ラベルは全角を 2 幅として数えて桁を揃える（printf の %-22s はバイト/文字数のため）
show() {
  local label="$1" ascii wide width pad
  ascii="${label//[^ -~]/}"
  wide=$(( ${#label} - ${#ascii} ))
  width=$(( ${#label} + wide ))
  pad=$(( 22 - width ))
  (( pad < 1 )) && pad=1
  printf '  %s%*s %s\n' "$label" "$pad" "" "$2"
}
or_none() { [[ -n "$1" ]] && printf '%s' "$1" || printf 'なし'; }

print_check() {
  echo "== 自動検出 =="
  show "repo" "$REPO"
  show "python" "$(or_none "${PY_VER:+$PY_VER ($PY_BIN)}")$([[ "$PY_OK" == no ]] && echo '  ← 3.12 以上が要る' || true)"
  show "node" "$(or_none "$NODE_VER")$([[ "$NODE_OK" == no ]] && echo '  ← 18 以上が要る' || true)"
  show "npm" "$(or_none "$NPM_VER")"
  show "ffmpeg" "$(or_none "$FFMPEG_VER")"
  show "git" "$(or_none "$GIT_VER")"
  show "docker" "$(or_none "$DOCKER_VER")"
  show "GPU" "$(or_none "$GPU")"
  show ".venv" "$VENV"
  show "frontend/node_modules" "$FRONT_MODULES"
  show "frontend/dist" "$FRONT_DIST"
  show "remotion/node_modules" "$REMOTION_MODULES"
  show ".env HOST/PORT" "$(or_none "${ENV_HOST:-}${ENV_PORT:+:$ENV_PORT}")"
  show "COMFY_MODELS_DIR" "$(or_none "$ENV_MODELS_DIR")"
  show "アプリ" "$([[ "$APP" == down ]] && echo "未起動 ($BASE)" || echo "$APP ($BASE)")"
  show "health comfyui" "$(or_none "${COMFYUI_STATUS:+$COMFYUI_STATUS${COMFYUI_DETAIL:+ — $COMFYUI_DETAIL}}")"
  show "health grok" "$(or_none "${GROK_STATUS:+$GROK_STATUS${GROK_DETAIL:+ — $GROK_DETAIL}}")"
  show "LLM CLI" "$(or_none "$CLI_LABEL")"
  show "comfy_target" "$(or_none "$CFG_TARGET")"
  show "外部 API キー" "$([[ "$CFG_API_KEY" == yes ]] && echo "設定済み（値は表示しない）" || echo "未設定")"
  show "remotion_enabled" "$CFG_REMOTION"
  show "audio_analysis_python" "$(or_none "${CFG_AUDIO_PY:+$CFG_AUDIO_PY（実在: $CFG_AUDIO_PY_EXISTS）}")"
}

print_check_json() {
  python3 -c 'import json, sys
keys = ["repo","base","python","python_ok","node","node_ok","npm","ffmpeg","git","docker","gpu",
        "venv","frontend_node_modules","frontend_dist","remotion_node_modules",
        "env_host","env_port","comfy_models_dir","app","comfyui","comfyui_detail",
        "grok","grok_detail","cli_label","comfy_target","external_api_key","remotion_enabled",
        "audio_analysis_python","audio_analysis_python_exists"]
print(json.dumps(dict(zip(keys, sys.argv[1:])), ensure_ascii=False, indent=2))' \
    "$REPO" "$BASE" "$PY_VER" "$PY_OK" "$NODE_VER" "$NODE_OK" "$NPM_VER" "$FFMPEG_VER" "$GIT_VER" \
    "$DOCKER_VER" "$GPU" "$VENV" "$FRONT_MODULES" "$FRONT_DIST" "$REMOTION_MODULES" \
    "$ENV_HOST" "$ENV_PORT" "$ENV_MODELS_DIR" "$APP" "$COMFYUI_STATUS" "$COMFYUI_DETAIL" \
    "$GROK_STATUS" "$GROK_DETAIL" "$CLI_LABEL" "$CFG_TARGET" "$CFG_API_KEY" "$CFG_REMOTION" \
    "$CFG_AUDIO_PY" "$CFG_AUDIO_PY_EXISTS"
}

print_status() {
  echo "== 段階 =="
  [[ -f "$STATE" ]] || echo "  （状態ファイルはまだありません: $STATE）"
  local next="" step saved status updated note verdict
  for step in "${STEP_IDS[@]}"; do
    saved="$(state_get "$step")"
    status="-"; updated=""; note=""
    if [[ -n "$saved" ]]; then
      IFS=$'\t' read -r status updated note <<<"$saved" || true
    fi
    verdict="$(auto_verdict "$step")"
    printf '  %-3s %-8s %s\n' "$step" "[$status]" "$(step_label "$step")"
    printf '      %s\n' "自動検出: $verdict${updated:+  記録: $updated}${note:+  メモ: $note}"
    if [[ -z "$next" && "$status" != "done" && "$status" != "skipped" ]]; then
      next="$step"
    fi
  done
  echo
  if [[ -z "$next" ]]; then
    echo "次にやる段階: なし（S0〜S8 はすべて done / skipped）"
  else
    verdict="$(auto_verdict "$next")"
    if [[ "$verdict" == ok ]]; then
      echo "次にやる段階: $next $(step_label "$next") — 自動検出は満たしている。確認して setup.sh mark $next done で記録する"
    else
      echo "次にやる段階: $next $(step_label "$next")"
    fi
  fi
}

print_choices() {
  [[ -f "$STATE" ]] || return 0
  local out
  out="$(python3 -c 'import json,sys
try:
    with open(sys.argv[1], encoding="utf-8") as fh:
        ch = json.load(fh).get("choices") or {}
except Exception:
    ch = {}
for k, v in ch.items():
    print("  %-22s %s" % (k, v))' "$STATE" 2>/dev/null || true)"
  [[ -n "$out" ]] || return 0
  echo "== 記録した選択 =="
  printf '%s\n' "$out"
  echo
}

case "${1:-}" in
  ""|-h|--help)
    sed -n '2,26p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    exit 0 ;;
  status)
    print_check
    echo
    print_choices
    print_status ;;
  check)
    if [[ "${2:-}" == "--json" ]]; then print_check_json; else print_check; fi ;;
  mark)
    [[ -n "${2:-}" && -n "${3:-}" ]] || die "使い方: setup.sh mark <step> <done|skipped|failed> [note]"
    step="$2"; status="$3"
    printf '%s\n' "${STEP_IDS[@]}" | grep -qx "$step" || die "不明な段階 '$step'（S0〜S8）"
    case "$status" in done|skipped|failed) ;; *) die "status は done / skipped / failed" ;; esac
    state_write step "$step" "$status" "${4:-}"
    printf 'setup.sh: %s = %s%s\n' "$step" "$status" "${4:+（$4）}" ;;
  choose)
    [[ -n "${2:-}" && -n "${3:-}" ]] || die "使い方: setup.sh choose <key> <value>"
    case "$2" in
      *key*|*token*|*secret*|*password*) die "キーやトークンの値は状態ファイルに記録しない" ;;
    esac
    state_write choice "$2" "$3"
    printf 'setup.sh: %s = %s\n' "$2" "$3" ;;
  reset)
    [[ -f "$STATE" ]] || { echo "setup.sh: 状態ファイルはありません（$STATE）"; exit 0; }
    if [[ "${2:-}" != "--yes" && "${2:-}" != "-y" ]]; then
      printf 'setup.sh: %s を削除します。よろしいですか [y/N]: ' "$STATE"
      read -r reply || reply=""
      [[ "$reply" == y || "$reply" == Y ]] || { echo "setup.sh: 中止しました"; exit 1; }
    fi
    rm -f "$STATE"
    echo "setup.sh: 削除しました（$STATE）" ;;
  *)
    die "不明なコマンド '$1'（status / check / mark / choose / reset）" ;;
esac
