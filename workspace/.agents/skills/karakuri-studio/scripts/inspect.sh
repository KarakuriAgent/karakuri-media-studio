#!/usr/bin/env bash
# 生成された動画を検分する。
#
#   inspect.sh <video_url_or_path> [interval_sec]
#
# <video_url_or_path> はジョブ / Take の video_url（`/outputs/<job_id>/video.mp4` の
# ような相対 URL のままでよい）、http(s) の URL、ローカルのファイルのどれか。
# 相対 URL の接続先は studio.sh と同じく $KARAKURI_STUDIO_URL、無ければ
# <repo>/.env の HOST/PORT（0.0.0.0 は 127.0.0.1 に読み替え。_common.sh）。
#
# URL なら作業用ディレクトリへ落とし、ffprobe で尺・解像度・音声トラックの有無を
# 出し、ffmpeg で interval_sec 秒ごと (既定 1 秒) のフレームを PNG に切り出す。
# 出力先のパスを最後に表示するので、その PNG を Read で見て絵柄を確かめる。
#
# 出力先は <workspace>/tmp/inspect-XXXXXX。<workspace> は $KARAKURI_WORKSPACE、
# 無ければこのスクリプトの 4 つ上（作業フォルダ）。作業ファイルはそこに置く
# 決まりなので、出た PNG はそのまま Read で開いてよい。
set -euo pipefail

die() { printf 'inspect.sh: %s\n' "$*" >&2; exit 2; }

SRC="${1:-}"
INTERVAL="${2:-1}"
[[ -n "$SRC" ]] || die "使い方: inspect.sh <video_url_or_path> [interval_sec]"
[[ "$INTERVAL" =~ ^[0-9]+(\.[0-9]+)?$ ]] || die "interval_sec は数値で指定してください"

command -v ffmpeg >/dev/null 2>&1 || die "ffmpeg が見つかりません"
command -v ffprobe >/dev/null 2>&1 || die "ffprobe が見つかりません"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=_common.sh
source "$SCRIPT_DIR/_common.sh"

URL="$(karakuri_resolve_url "$SRC" "$(karakuri_base_url "$(karakuri_repo_dir "$SCRIPT_DIR")")")"
if [[ -n "$URL" ]]; then
  command -v curl >/dev/null 2>&1 || die "curl が見つかりません"
else
  [[ -f "$SRC" ]] || die "ファイルがありません: $SRC"
fi

# 出力先は workspace/tmp/inspect-XXXXXX（作業ファイルは workspace の中に置く）
WORKSPACE="${KARAKURI_WORKSPACE:-$(cd "$SCRIPT_DIR/../../../.." && pwd -P)}"
mkdir -p "$WORKSPACE/tmp"
OUT="$(mktemp -d "$WORKSPACE/tmp/inspect-XXXXXX")"

if [[ -n "$URL" ]]; then
  VIDEO="$OUT/source.mp4"
  curl -sSfL "$URL" -o "$VIDEO" || die "ダウンロードに失敗しました: $URL"
else
  VIDEO="$SRC"
fi

printf '== %s\n' "$SRC"
ffprobe -v error -show_entries format=duration,size -show_entries \
  stream=index,codec_type,codec_name,width,height,r_frame_rate,channels \
  -of default=noprint_wrappers=1 "$VIDEO"

if ffprobe -v error -select_streams a -show_entries stream=index \
     -of csv=p=0 "$VIDEO" | grep -q .; then
  printf 'audio: あり\n'
else
  printf 'audio: なし\n'
fi

ffmpeg -v error -y -i "$VIDEO" -vf "fps=1/$INTERVAL" "$OUT/frame_%02d.png"
printf 'frames: %s\n' "$OUT"
ls -1 "$OUT"/frame_*.png 2>/dev/null || printf 'フレームを抽出できませんでした\n' >&2
