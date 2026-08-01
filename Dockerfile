# Karakuri Media Studio ランタイムイメージ
#
# アプリ本体はイメージに焼かず、docker-compose.yml がリポジトリ全体を
# 「ホストと同じ絶対パス」にマウントして動かす（app.db や runtime/config.json に
# 絶対パスが保存されているため、パスを揃えると既存データがそのまま使える）。
# イメージが持つのは Python 依存パッケージと ffmpeg だけ。
FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt && rm /tmp/requirements.txt

# Codex CLI（gpt-image-2 画像生成、SPEC §5.4）。node 本体を公式イメージから
# 持ち込み、@openai/codex をグローバルに入れる。サインイン状態（~/.codex）は
# grok と同じくホストからマウントして持ち込む。
COPY --from=node:22-slim /usr/local/bin/node /usr/local/bin/node
COPY --from=node:22-slim /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/npm
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && npm install -g @openai/codex \
    && npm cache clean --force

# compose は `user: <ローカルUID>:<GID>` で起動する。その任意 UID でも HOME に
# 書き込めるよう、共用の HOME を用意しておく（grok CLI は ~/.grok を参照する。
# 実体はホストの ~/.grok を /home/app/.grok にマウントして持ち込む）。
RUN mkdir -p /home/app && chmod 0777 /home/app
ENV HOME=/home/app \
    PATH=/home/app/.grok/bin:$PATH \
    PYTHONUNBUFFERED=1
