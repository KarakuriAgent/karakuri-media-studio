#!/usr/bin/env bash
# docker compose の薄いラッパー。
#
#   ./compose.sh up -d --build      # 起動
#   ./compose.sh logs -f            # ログ
#   ./compose.sh down               # 停止
#
# やること:
# 1. リポジトリの実体パス (readlink -f) に移動してから compose を実行する。
#    app.db や runtime/config.json には絶対パスが記録されているため、
#    シンボリックリンク経由 (~/workspace など) で起動するとコンテナ内の
#    マウント位置と食い違って壊れる。ここで必ず実体パスに揃える。
# 2. UID / GID をローカルユーザーに合わせて export する
#    （コンテナが作るファイルの所有者をローカルユーザーにするため）。
set -euo pipefail

cd "$(readlink -f "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)")"

# UID は bash が常に定義している読み取り専用変数なので export だけ行う
export UID 2>/dev/null || true
export GID="${GID:-$(id -g)}"

exec docker compose "$@"
