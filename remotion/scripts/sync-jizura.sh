#!/usr/bin/env bash
# JIZURA（https://github.com/852wa/JIZURA, MIT）のエンジンを取り込み直す。
#
#   remotion/scripts/sync-jizura.sh [ref]     # ref 既定: main
#
# やること:
#   1. upstream を一時ディレクトリに浅く clone（指定があればその ref を出す）
#   2. remotion/vendor/jizura/src/ を差し替える（EXCLUDE のファイルは取らない）
#   3. LICENSE を上書きする
#   4. UPSTREAM.md の「コミット」「取り込み日」「ファイル」を書き直す
#   5. dist/jizura.js を作り直し、部品カタログも出し直す
#
# 取り込んだソースは**無改変**で置く（差分は upstream に投げる）。実行後は
# `cd remotion && npm run typecheck` と LyricMotion の試し焼きまで確認すること。
set -euo pipefail

REPO_URL="https://github.com/852wa/JIZURA"
REF="${1:-main}"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENDOR="$HERE/../vendor/jizura"
SRC_DIR="$VENDOR/src"
UPSTREAM_MD="$VENDOR/UPSTREAM.md"

# 取り込まないファイル（理由は UPSTREAM.md）
EXCLUDE=(11_export.js 12_ui.js)

TMP="$(mktemp -d)"
cleanup() { rm -rf "$TMP"; }
trap cleanup EXIT

echo "clone $REPO_URL ($REF) -> $TMP"
git clone --quiet --depth 1 --branch "$REF" "$REPO_URL" "$TMP/JIZURA" 2>/dev/null \
  || { git clone --quiet "$REPO_URL" "$TMP/JIZURA"; git -C "$TMP/JIZURA" checkout --quiet "$REF"; }

SHA="$(git -C "$TMP/JIZURA" rev-parse HEAD)"
TODAY="$(date +%Y-%m-%d)"

rm -f "$SRC_DIR"/*.js
mkdir -p "$SRC_DIR"
taken=()
for path in "$TMP/JIZURA/src"/*.js; do
  name="$(basename "$path")"
  skip=""
  for bad in "${EXCLUDE[@]}"; do [ "$name" = "$bad" ] && skip=1; done
  [ -n "$skip" ] && continue
  cp "$path" "$SRC_DIR/$name"
  chmod 644 "$SRC_DIR/$name"
  taken+=("$name")
done
cp "$TMP/JIZURA/LICENSE" "$VENDOR/LICENSE"
chmod 644 "$VENDOR/LICENSE"
echo "取り込み: ${#taken[@]} ファイル（除外: ${EXCLUDE[*]}）"

# UPSTREAM.md の見出し行を書き直す（行の形は UPSTREAM.md 側で固定してある）
python3 - "$UPSTREAM_MD" "$SHA" "$TODAY" "${taken[@]}" <<'PY'
import re, sys
path, sha, today, *files = sys.argv[1:]
text = open(path, encoding='utf-8').read()
text = re.sub(r'^- コミット: .*$', f'- コミット: `{sha}`', text, count=1, flags=re.M)
text = re.sub(r'^- 取り込み日: .*$', f'- 取り込み日: {today}', text, count=1, flags=re.M)
text = re.sub(r'^- ファイル: .*$', f'- ファイル: `src/*.js` のうち {len(files)} 本', text, count=1, flags=re.M)
open(path, 'w', encoding='utf-8').write(text)
print(f'UPSTREAM.md を更新: {sha[:12]} / {today} / {len(files)} 本')
PY

node "$HERE/build-jizura-bundle.mjs"
node "$HERE/export-jizura-catalog.mjs"
echo '完了。`cd remotion && npm run typecheck` と LyricMotion の試し焼きで確認してください。'
