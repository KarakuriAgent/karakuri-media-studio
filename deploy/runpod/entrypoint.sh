#!/usr/bin/env bash
# RunPod Pod の起動処理（deploy/runpod/README.md）。
#
# 何度走らせても同じ結果になるように書いてある（冪等）: すでに置いてあるものは
# 触らないので、2 回目以降の Pod は数十秒で ComfyUI が上がる。
#
#   1. /workspace/ComfyUI が無ければ clone（バージョンは COMFYUI_REF で固定）
#   2. custom_nodes.txt のうち、まだ無いものだけ clone + checkout + pip install
#   3. models.txt のうち、まだ無いものだけダウンロード
#   4. caddy（:8189、認証つき）→ cloudflared → watchdog → ComfyUI の順に起動
#
# ComfyUI は 127.0.0.1:8188 でしか待ち受けない。外に見えるのは caddy の 8189 だけ。

set -euo pipefail

WORKSPACE="${WORKSPACE:-/workspace}"
COMFY_DIR="${WORKSPACE}/ComfyUI"
MODELS_DIR="${COMFY_DIR}/models"
COMFY_PORT="${COMFY_PORT:-8188}"
COMFYUI_REF="${COMFYUI_REF:-master}"
COMFYUI_REPO="${COMFYUI_REPO:-https://github.com/comfyanonymous/ComfyUI.git}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# pip のダウンロードキャッシュを Network Volume に置く（Pod を作り直しても効く）
export PIP_CACHE_DIR="${WORKSPACE}/.cache/pip"
export HF_HOME="${WORKSPACE}/.cache/huggingface"

log() { printf '[entrypoint] %s\n' "$*"; }

# --------------------------------------------------------------------------
# 1. ComfyUI 本体
# --------------------------------------------------------------------------
mkdir -p "${WORKSPACE}" "${PIP_CACHE_DIR}" "${HF_HOME}"

if [ ! -d "${COMFY_DIR}/.git" ]; then
	log "cloning ComfyUI ${COMFYUI_REF} into ${COMFY_DIR}"
	git clone --filter=blob:none "${COMFYUI_REPO}" "${COMFY_DIR}"
	git -C "${COMFY_DIR}" checkout --detach "${COMFYUI_REF}"
else
	log "ComfyUI already present ($(git -C "${COMFY_DIR}" rev-parse --short HEAD))"
fi

log "installing ComfyUI requirements"
pip install --no-input -r "${COMFY_DIR}/requirements.txt"

# --------------------------------------------------------------------------
# 2. custom nodes
# --------------------------------------------------------------------------
install_custom_nodes() {
	local manifest="${HERE}/custom_nodes.txt"
	[ -f "${manifest}" ] || return 0
	mkdir -p "${COMFY_DIR}/custom_nodes"

	local url ref name dir
	while read -r url ref _rest; do
		case "${url}" in '' | '#'*) continue ;; esac
		if [ -z "${ref:-}" ]; then
			log "custom_nodes.txt: コミットが指定されていません: ${url}" >&2
			continue
		fi
		name="$(basename "${url}" .git)"
		dir="${COMFY_DIR}/custom_nodes/${name}"
		if [ -d "${dir}" ]; then
			log "custom node ${name} already present"
			continue
		fi
		log "installing custom node ${name} (${ref})"
		git clone --filter=blob:none "${url}" "${dir}"
		git -C "${dir}" checkout --detach "${ref}"
		if [ -f "${dir}/requirements.txt" ]; then
			pip install --no-input -r "${dir}/requirements.txt"
		fi
	done <"${manifest}"
}
install_custom_nodes

# --------------------------------------------------------------------------
# 3. モデル
# --------------------------------------------------------------------------
mkdir -p "${MODELS_DIR}"
# イメージに焼いた models.txt に加えて、Network Volume に置いた
# models.local.txt（手元の設定から作ったもの）も読む。イメージを作り直さずに
# モデル構成を変えられる（deploy/runpod/README.md）。
manifests=()
if [ -f "${HERE}/models.txt" ]; then
	manifests+=("${HERE}/models.txt")
fi
LOCAL_MANIFEST="${LOCAL_MANIFEST:-${WORKSPACE}/models.local.txt}"

# Pod にファイルを送る手段（ssh / runpodctl / Jupyter）が無いこともあるので、
# テンプレートの環境変数からも渡せるようにしてある。MODELS_LOCAL_B64 が入って
# いれば、それを base64 デコードしたものが models.local.txt になる（テンプレート
# 側を常に正とするので毎回上書き）。個人ごとの URL をイメージに焼かずに済む。
if [ -n "${MODELS_LOCAL_B64:-}" ]; then
	tmp_manifest="${LOCAL_MANIFEST}.tmp.$$"
	# デコードに失敗したときに既存のマニフェストを壊さないよう、一時ファイル経由で置く
	if printf '%s' "${MODELS_LOCAL_B64}" | base64 -d >"${tmp_manifest}" 2>/dev/null; then
		mv "${tmp_manifest}" "${LOCAL_MANIFEST}"
		log "wrote ${LOCAL_MANIFEST} from MODELS_LOCAL_B64"
	else
		rm -f "${tmp_manifest}"
		log "MODELS_LOCAL_B64 を base64 デコードできませんでした: 無視します" >&2
	fi
fi

if [ -f "${LOCAL_MANIFEST}" ]; then
	log "using local model manifest ${LOCAL_MANIFEST}"
	manifests+=("${LOCAL_MANIFEST}")
fi
if [ "${#manifests[@]}" -gt 0 ]; then
	log "checking models in ${MODELS_DIR}"
	# 1 件落とせなくても Pod は上げる（使わないワークフローのモデルかもしれない。
	# 実際に足りなければジョブが ComfyUI 側のエラーで失敗し、UI に理由が出る）。
	# すでに置いてあるファイルは飛ばすので、両方に出ている行は 1 度しか落ちない。
	python3 "${HERE}/download_models.py" "${manifests[@]}" "${MODELS_DIR}" ||
		log "一部のモデルを取得できませんでした（上のログを参照）"
fi

# --------------------------------------------------------------------------
# 4. 各プロセスの起動
# --------------------------------------------------------------------------
pids=()

shutdown() {
	log "shutting down"
	for pid in "${pids[@]:-}"; do
		[ -n "${pid}" ] && kill "${pid}" 2>/dev/null || true
	done
}
trap shutdown EXIT INT TERM

# caddy: 認証つきで :8189 -> 127.0.0.1:8188。COMFY_API_KEY が空なら素通し。
if [ -z "${COMFY_API_KEY:-}" ]; then
	log "COMFY_API_KEY is empty: the proxy will NOT require authentication"
fi
export COMFY_API_KEY="${COMFY_API_KEY:-}"
log "starting caddy on :${PROXY_PORT:-8189}"
caddy run --config "${HERE}/Caddyfile" --adapter caddyfile &
pids+=("$!")

# cloudflared: トークンがあるときだけ。Named Tunnel の設定（どのホスト名を
# 127.0.0.1:8189 に向けるか）は Cloudflare 側に持たせる。
if [ -n "${CF_TUNNEL_TOKEN:-}" ]; then
	log "starting cloudflared tunnel"
	cloudflared tunnel --no-autoupdate run --token "${CF_TUNNEL_TOKEN}" &
	pids+=("$!")
else
	log "CF_TUNNEL_TOKEN is not set: no tunnel (expose the port from RunPod instead)"
fi

# watchdog: アイドルが続いたら自分の Pod を terminate する
log "starting idle watchdog"
COMFY_URL="http://127.0.0.1:${COMFY_PORT}" python3 "${HERE}/watchdog.py" &
pids+=("$!")

# ComfyUI: 外には出さない（公開は caddy 経由だけ）
log "starting ComfyUI on 127.0.0.1:${COMFY_PORT}"
cd "${COMFY_DIR}"
# shellcheck disable=SC2086  # COMFY_ARGS は「追加の引数列」なので分割させたい
exec python3 main.py --listen 127.0.0.1 --port "${COMFY_PORT}" ${COMFY_ARGS:-}
