#!/usr/bin/env bash
# 生成された動画を検分する。
#
#   inspect.sh <video_url_or_path> [interval_sec]
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

IS_URL=no
if [[ "$SRC" == http://* || "$SRC" == https://* ]]; then
  IS_URL=yes
  command -v curl >/dev/null 2>&1 || die "curl が見つかりません"
else
  [[ -f "$SRC" ]] || die "ファイルがありません: $SRC"
fi

# 出力先は workspace/tmp/inspect-XXXXXX（作業ファイルは workspace の中に置く）
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
WORKSPACE="${KARAKURI_WORKSPACE:-$(cd "$SCRIPT_DIR/../../../.." && pwd -P)}"
mkdir -p "$WORKSPACE/tmp"
OUT="$(mktemp -d "$WORKSPACE/tmp/inspect-XXXXXX")"

if [[ "$IS_URL" == yes ]]; then
  VIDEO="$OUT/source.mp4"
  curl -sSfL "$SRC" -o "$VIDEO" || die "ダウンロードに失敗しました: $SRC"
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
