# Karakuri Media Studio ランタイムイメージ
#
# アプリ本体はイメージに焼かず、docker-compose.yml がリポジトリ全体を
# 「ホストと同じ絶対パス」にマウントして動かす（app.db や runtime/config.json に
# 絶対パスが保存されているため、パスを揃えると既存データがそのまま使える）。
# イメージが持つのは Python 依存パッケージと ffmpeg などの実行環境だけ。
FROM python:3.12-slim

# ffmpeg（書き出し）に加えて:
# - fonts-noto-cjk … 焼き込み字幕（ASS の Fontname = "Noto Sans CJK JP"）。
#   入れないと日本語が全部 □（豆腐）になる。
# - lib*（libnss3 以降） … Remotion が落としてくる chrome-headless-shell の
#   実行時依存。slim には無いので、入れないと libnspr4.so が解決できずに
#   `remotion compositions` がそのまま落ちる。
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg \
        fonts-noto-cjk \
        libnss3 \
        libnspr4 \
        libdbus-1-3 \
        libatk1.0-0 \
        libatk-bridge2.0-0 \
        libcups2 \
        libdrm2 \
        libxkbcommon0 \
        libxcomposite1 \
        libxdamage1 \
        libxfixes3 \
        libxrandr2 \
        libgbm1 \
        libasound2 \
        libpango-1.0-0 \
        libcairo2 \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt && rm /tmp/requirements.txt

# Codex CLI（gpt-image-2 画像生成、SPEC §5.4）。node 本体を公式イメージから
# 持ち込み、@openai/codex をグローバルに入れる。サインイン状態（~/.codex）は
# grok と同じくホストからマウントして持ち込む。
COPY --from=node:22-slim /usr/local/bin/node /usr/local/bin/node
COPY --from=node:22-slim /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/npm
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && npm install -g @openai/codex \
    && npm cache clean --force

# compose は `user: <ローカルUID>:<GID>` で起動する。その任意 UID でも HOME に
# 書き込めるよう、共用の HOME を用意しておく（grok CLI は ~/.grok を参照する。
# 実体はホストの ~/.grok を /home/app/.grok にマウントして持ち込む）。
RUN mkdir -p /home/app && chmod 0777 /home/app
ENV HOME=/home/app \
    PATH=/home/app/.grok/bin:$PATH \
    PYTHONUNBUFFERED=1
