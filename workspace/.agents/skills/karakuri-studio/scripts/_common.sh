# studio.sh / inspect.sh が source する共通部品（単体では実行しない）。
#
# 接続先の解決:
#   BASE = $KARAKURI_STUDIO_URL、無ければ <repo>/.env の HOST/PORT (既定 127.0.0.1:8000)。
#   HOST が 0.0.0.0 / :: なら「全インターフェイスで待受」の意味なので 127.0.0.1 に読み替える。
#   <repo> は $KARAKURI_STUDIO_REPO、無ければこのファイルの 5 つ上。

# karakuri_repo_dir <script_dir>: アプリ本体のディレクトリを出す
karakuri_repo_dir() {
  if [[ -n "${KARAKURI_STUDIO_REPO:-}" ]]; then
    printf '%s\n' "$KARAKURI_STUDIO_REPO"
  else
    (cd "$1/../../../../.." && pwd -P)
  fi
}

# karakuri_base_url <repo_dir>: 接続先 (http://HOST:PORT、末尾の / なし) を出す
karakuri_base_url() {
  local repo="$1" base host="" port=""
  base="${KARAKURI_STUDIO_URL:-}"
  if [[ -z "$base" ]]; then
    if [[ -f "$repo/.env" ]]; then
      host="$(sed -n 's/^HOST=//p' "$repo/.env" | tail -n 1 | tr -d '"'\''[:space:]')"
      port="$(sed -n 's/^PORT=//p' "$repo/.env" | tail -n 1 | tr -d '"'\''[:space:]')"
    fi
    [[ -n "$host" ]] || host="127.0.0.1"
    [[ -n "$port" ]] || port="8000"
    # 0.0.0.0 は「全インターフェイスで待受」の意味なので、宛先には使えない
    [[ "$host" == "0.0.0.0" || "$host" == "::" ]] && host="127.0.0.1"
    base="http://$host:$port"
  fi
  printf '%s\n' "${base%/}"
}

# karakuri_resolve_url <src> <base>: 動画の指定を取得先の URL にする。
#   http(s):// で始まればそのまま、/ で始まり**ローカルにファイルが無ければ**
#   アプリからの相対 URL（ジョブの video_url の /outputs/... など）として BASE を前に
#   付ける。それ以外（ローカルのファイル）は空文字を出す。
karakuri_resolve_url() {
  local src="$1" base="$2"
  if [[ "$src" == http://* || "$src" == https://* ]]; then
    printf '%s\n' "$src"
  elif [[ "$src" == /* && ! -e "$src" ]]; then
    printf '%s%s\n' "$base" "$src"
  else
    printf '\n'
  fi
}
