# RunPod の Pod で ComfyUI を動かすためのイメージ（deploy/runpod/README.md）。
#
# 設計の要点:
#   * CUDA 12.8 ランタイム + PyTorch cu128。RTX PRO 6000 Blackwell・RTX 5090 (sm_120) と A6000 (sm_86) の
#     いずれでも動く組み合わせはこれ（Blackwell 系は cu128 より前のビルドでは動かない）。
#   * **ComfyUI 本体とカスタムノードとモデルはイメージに焼かない**。Network Volume
#     (/workspace) へ初回起動時に置き、以降の Pod はそれを再利用する。イメージを
#     数十 GB にせずに済み、モデルを足すのに再ビルドが要らない。
#   * Python の依存は /opt/venv（イメージ側）に入れる。pip のキャッシュだけ
#     /workspace に置くので、2 回目以降の起動は「入っているものを確認するだけ」で
#     ほぼ一瞬で終わる。
#   * 外に出るポートは caddy の 8189 だけ。ComfyUI は 127.0.0.1:8188 に閉じる。

# caddy は公式イメージからバイナリだけ借りる（apt リポジトリを足すより確実）
FROM caddy:2.10-alpine AS caddy

FROM nvidia/cuda:12.8.1-cudnn-runtime-ubuntu24.04

ARG DEBIAN_FRONTEND=noninteractive
# ComfyUI のバージョン。タグ or コミットハッシュで固定する（README 参照）。
# 現在はローカル開発環境と同じコミット（v0.27.0 の 15 コミット先）に合わせてある。
ARG COMFYUI_REF=6c62ca0b6bbee7ef293ca475f7904065af5bfb42
# torch 系 3 つは**必ず同じリリース列で固定**する。バラすと torchaudio /
# torchvision が別の torch 向けビルドになり、import 時に undefined symbol で落ちる
ARG TORCH_VERSION=2.8.0
ARG TORCHVISION_VERSION=0.23.0
ARG TORCHAUDIO_VERSION=2.8.0
ARG TORCH_INDEX=https://download.pytorch.org/whl/cu128

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    COMFYUI_REF=${COMFYUI_REF} \
    WORKSPACE=/workspace \
    COMFY_PORT=8188 \
    PROXY_PORT=8189 \
    VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        git \
        git-lfs \
        aria2 \
        ffmpeg \
        libgl1 \
        libglib2.0-0 \
        python3.12 \
        python3.12-venv \
        python3-pip \
        tini \
    && rm -rf /var/lib/apt/lists/*

# cloudflared（Cloudflare Tunnel のクライアント）。リリースの latest 直リンク。
RUN curl -fsSL -o /tmp/cloudflared.deb \
        https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb \
    && dpkg -i /tmp/cloudflared.deb \
    && rm -f /tmp/cloudflared.deb

COPY --from=caddy /usr/bin/caddy /usr/bin/caddy

# 起動時の pip install（ComfyUI 本体・カスタムノードの requirements）が torch 系を
# 別バージョンに入れ替えないよう、制約ファイルで釘を刺しておく
RUN mkdir -p /opt/runpod \
    && printf 'torch==%s\ntorchvision==%s\ntorchaudio==%s\n' \
        "${TORCH_VERSION}" "${TORCHVISION_VERSION}" "${TORCHAUDIO_VERSION}" \
        > /opt/runpod/constraints.txt
ENV PIP_CONSTRAINT=/opt/runpod/constraints.txt

# PyTorch を先に入れておく（一番大きいレイヤなので、他の変更で焼き直さない）
RUN python3.12 -m venv /opt/venv \
    && pip install --no-cache-dir --upgrade pip wheel \
    && pip install --no-cache-dir --index-url ${TORCH_INDEX} \
        torch==${TORCH_VERSION} \
        torchvision==${TORCHVISION_VERSION} \
        torchaudio==${TORCHAUDIO_VERSION}

# 起動スクリプトが使う分だけ（ComfyUI 本体の依存は初回起動時に入る）。
# requests は ComfyUI の requirements.txt に無いのに server.py が import する
# （frontend_management 経由）ため、無いと起動直後に落ちる。
RUN pip install --no-cache-dir httpx requests

WORKDIR /opt/runpod
COPY entrypoint.sh download_models.py watchdog.py ./
COPY Caddyfile custom_nodes.txt models.txt ./
RUN chmod +x entrypoint.sh download_models.py watchdog.py

# 認証つきプロキシだけを公開する（ComfyUI の 8188 は外に出さない）
EXPOSE 8189

# tini で PID 1 を持たせ、子プロセス（caddy / cloudflared / watchdog / ComfyUI）が
# 消えたときに Pod がゾンビを抱えないようにする。
ENTRYPOINT ["/usr/bin/tini", "--", "/opt/runpod/entrypoint.sh"]
