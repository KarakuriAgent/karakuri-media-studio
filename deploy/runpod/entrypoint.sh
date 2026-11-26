#!/usr/bin/env bash
# RunPod Pod の起動処理（deploy/runpod/README.md）。
#
# 何度走らせても同じ結果になるように書いてある（冪等）: すでに置いてあるものは
# 触らないので、2 回目以降の Pod は数十秒で ComfyUI が上がる。
#
#   1. /workspace/ComfyUI を COMFYUI_REF に合わせる（無ければ clone。ブランチ名なら
#      起動のたびに最新へ追従し、タグ・ハッシュならそこに固定される）
#   2. custom_nodes.txt のうち、まだ無いものだけ clone + checkout + pip install
#   3. models ディレクトリを用意する（モデル自体はアプリの [DL] / [全DL] から
#      Pod のダウンロード API 経由で入れる）
#   4. caddy（:8189、認証つき）→ cloudflared → watchdog → モデル DL API → ComfyUI
#      の順に起動
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

# COMFYUI_REF（ブランチ・タグ・コミットハッシュのいずれでも来る）が指すコミットを
# 返す。ブランチ名で来たときに clone 時点のコミットへ固定されないよう、まず
# origin/<ref>（＝fetch したての先端）を見て、無ければタグ・ハッシュとして解決する。
# どちらでも解決できなければ空文字。
resolve_ref() {
	git -C "${COMFY_DIR}" rev-parse --verify --quiet "refs/remotes/origin/${COMFYUI_REF}^{commit}" ||
		git -C "${COMFY_DIR}" rev-parse --verify --quiet "${COMFYUI_REF}^{commit}" ||
		true
}

if [ ! -d "${COMFY_DIR}/.git" ]; then
	log "cloning ComfyUI ${COMFYUI_REF} into ${COMFY_DIR}"
	git clone --filter=blob:none "${COMFYUI_REPO}" "${COMFY_DIR}"
	git -C "${COMFY_DIR}" checkout --detach "${COMFYUI_REF}"
else
	# すでに置いてある clone を COMFYUI_REF に追従させる。Network Volume は Pod を
	# 作り直しても残るので、これが無いと古いコミットのまま動き続けてしまう。
	# COMFYUI_REF がブランチのときは「起動のたびに最新へ」なので、毎回取り直す
	# （--filter=blob:none で clone してあるぶん、追加取得は軽い）。タグも取る。
	log "fetching ComfyUI updates from origin (${COMFYUI_REF})"
	if ! git -C "${COMFY_DIR}" fetch --tags --force origin; then
		# ネットワークが死んでいるだけかもしれない。手元の clone で起動は続けられる。
		log "警告: origin から取得できませんでした: 手元の clone のまま起動します" >&2
	fi

	head_commit="$(git -C "${COMFY_DIR}" rev-parse HEAD)"
	want_commit="$(resolve_ref)"
	if [ -z "${want_commit}" ]; then
		# fetch も解決も失敗した状態。clone はあるので、古いままでも起動はする。
		log "警告: COMFYUI_REF=${COMFYUI_REF} を解決できませんでした: 現在の $(git -C "${COMFY_DIR}" rev-parse --short HEAD) のまま起動します" >&2
	elif [ "${want_commit}" != "${head_commit}" ]; then
		# ComfyUI 本体はアプリから見れば使い捨てのランタイムなので、作業ツリーが
		# 入れ替わるのは想定どおり（ローカル変更があって失敗したら set -e で落ちる）
		log "updating ComfyUI to ${COMFYUI_REF} (currently $(git -C "${COMFY_DIR}" rev-parse --short HEAD))"
		git -C "${COMFY_DIR}" checkout --detach "${want_commit}"
		log "ComfyUI updated to $(git -C "${COMFY_DIR}" rev-parse --short HEAD)"
	else
		log "ComfyUI already present and up to date ($(git -C "${COMFY_DIR}" rev-parse --short HEAD))"
	fi
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
# 3. モデルの置き場所
# --------------------------------------------------------------------------
# 起動時の一括ダウンロードは行わない: モデルはアプリの設定ページ（モデル / LoRA
# タブの [DL] / [全DL]）から、Pod のダウンロード API 経由で必要なものだけ落とす
# （deploy/runpod/model_api.py）。Network Volume に残るので、2 回目以降の Pod は
# ここで何もしなくてよい。
mkdir -p "${MODELS_DIR}"

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

# モデル DL API: アプリの [DL] / [全DL] がここに依頼する（公開は caddy の
# /studio/models/* 経由だけ）。HF_TOKEN / CIVITAI_API_KEY はそのまま渡る。
log "starting model download API on 127.0.0.1:${MODEL_API_PORT:-8190}"
MODELS_DIR="${MODELS_DIR}" python3 "${HERE}/model_api.py" &
pids+=("$!")

# ComfyUI: 外には出さない（公開は caddy 経由だけ）
log "starting ComfyUI on 127.0.0.1:${COMFY_PORT}"
cd "${COMFY_DIR}"
# shellcheck disable=SC2086  # COMFY_ARGS は「追加の引数列」なので分割させたい
exec python3 main.py --listen 127.0.0.1 --port "${COMFY_PORT}" ${COMFY_ARGS:-}
