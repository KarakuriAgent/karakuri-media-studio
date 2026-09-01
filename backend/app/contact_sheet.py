"""コンタクトシート（SPEC §7.2）: 動画から何コマか抜いて 1 枚のグリッドにする。

外部エージェントが「いま焼いた mp4 で、演出が狙った位置・狙った秒に出ているか」を
**1 リクエストで目視できる**ようにするための道具。手元で見るときは
``.agents/skills/karakuri-studio/scripts/inspect.sh``（1 秒ごとの PNG を並べる）が
あるが、API から使うにはコマを 1 枚ずつ取りに行くのは重いので、こちらは**必要な
秒だけを 1 枚の jpg に束ねて**返す。

ffmpeg でコマを抜くところ（:func:`extract_frame`）以外はすべて純関数にしてあり、
どの秒を抜くかの決め方（:func:`plan_seconds`）とグリッドの寸法
（:func:`build_grid`）は ffmpeg が無くてもテストできる。
"""

from __future__ import annotations

import asyncio
import logging
import math
import tempfile
from io import BytesIO
from pathlib import Path
from typing import Sequence

from PIL import Image, ImageDraw, UnidentifiedImageError

from . import textimage

log = logging.getLogger(__name__)

#: ffmpeg / ffprobe の呼び出し名（:mod:`app.jobs` と同じ流儀でテストが差し替える）
FFMPEG = "ffmpeg"
FFPROBE = "ffprobe"

#: 1 コマの抜き出しに許す秒数
FRAME_TIMEOUT = 60.0

#: 既定の列数とコマの幅（px）
DEFAULT_COLUMNS = 4
DEFAULT_CELL_WIDTH = 480

#: コマの幅の下限・上限
MIN_CELL_WIDTH = 64
MAX_CELL_WIDTH = 1920

#: 1 枚に載せられるコマ数（多すぎると読めないし、抜くのに時間が掛かる）
MAX_FRAMES = 64

#: 秒の指定が無いときに等間隔で抜く枚数
DEFAULT_COUNT = 12

#: ラベル帯の高さ（コマの幅に対する比）と、その中の文字の大きさ
LABEL_RATIO = 0.075
LABEL_TEXT_RATIO = 0.55

#: シートの色（背景・ラベル帯・文字）
BACKGROUND = (12, 12, 14)
LABEL_BACKGROUND = (28, 28, 32)
LABEL_COLOR = (235, 235, 240)

#: 保存する jpg の品質
JPEG_QUALITY = 90


class ContactSheetError(Exception):
    """シートを組めない（コマが抜けない・指定が不正）。呼び出し側が 400 にする。"""


# --------------------------------------------------------------------------
# 純関数（ffmpeg を触らない。テストで固定できる）
# --------------------------------------------------------------------------

def _as_number(value: object, default: float) -> object:
    """未指定（None / 空文字）だけを既定値に倒す（0 は「0 の指定」として通す）。"""
    return default if value is None or value == "" else value


def check_columns(value: object) -> int:
    try:
        columns = int(_as_number(value, DEFAULT_COLUMNS))  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ContactSheetError(f"columns が整数ではありません: {value!r}") from exc
    if not 1 <= columns <= MAX_FRAMES:
        raise ContactSheetError(
            f"columns は 1〜{MAX_FRAMES} で指定してください（{columns}）"
        )
    return columns


def check_width(value: object) -> int:
    try:
        width = int(_as_number(value, DEFAULT_CELL_WIDTH))  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ContactSheetError(f"width が整数ではありません: {value!r}") from exc
    if not MIN_CELL_WIDTH <= width <= MAX_CELL_WIDTH:
        raise ContactSheetError(
            f"width は {MIN_CELL_WIDTH}〜{MAX_CELL_WIDTH} で指定してください（{width}）"
        )
    return width


def _from_range(span: dict) -> list[float]:
    """``{start, end, step}`` を秒の並びにする（``end`` を含む）。"""
    try:
        start = float(_as_number(span.get("start"), 0))  # type: ignore[arg-type]
        end = float(_as_number(span.get("end"), 0))  # type: ignore[arg-type]
        step = float(_as_number(span.get("step"), 1))  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ContactSheetError(f"range の値が数値ではありません: {span!r}") from exc
    if step <= 0:
        raise ContactSheetError("range.step は正の数で指定してください")
    if end < start:
        raise ContactSheetError("range.end は range.start 以上で指定してください")
    count = int(math.floor((end - start) / step)) + 1
    if count > MAX_FRAMES:
        raise ContactSheetError(
            f"range から {count} コマになります（{MAX_FRAMES} コマまで）。"
            "step を大きくするか区間を狭めてください"
        )
    return [round(start + index * step, 3) for index in range(count)]


def plan_seconds(
    *,
    seconds: Sequence[float] | None = None,
    span: dict | None = None,
    frames: Sequence[int] | None = None,
    fps: float | None = None,
    duration: float | None = None,
) -> list[float]:
    """どの秒を抜くかを決める（``seconds`` → ``range`` → ``frames`` → 等間隔）。

    ``frames``（フレーム番号）は ``fps`` が分かるときだけ秒に直せる。どれも
    指定が無ければ、尺を :data:`DEFAULT_COUNT` 等分した位置（各区間の中央）を返す
    ——「全体をざっと見る」ときにいちばんよく使う並び。

    ``duration`` が分かっていれば、その範囲に収まる秒だけを残す（末尾ぴったりは
    コマが無いので少し内側に寄せる）。
    """
    values: list[float]
    if seconds:
        values = [float(value) for value in seconds]
    elif span:
        values = _from_range(span)
    elif frames:
        if not fps or fps <= 0:
            raise ContactSheetError(
                "frames で指定するには fps が要ります"
                "（動画から読めなかったので seconds か range で指定してください）"
            )
        values = [round(int(number) / fps, 3) for number in frames]
    elif duration and duration > 0:
        step = duration / DEFAULT_COUNT
        values = [round(step * (index + 0.5), 3) for index in range(DEFAULT_COUNT)]
    else:
        raise ContactSheetError(
            "seconds / range / frames のどれかを指定してください"
            "（動画の尺が読めないので既定の等間隔も使えません）"
        )
    if not values:
        raise ContactSheetError("抜くコマが 1 枚もありません")
    if len(values) > MAX_FRAMES:
        raise ContactSheetError(
            f"コマは {MAX_FRAMES} 枚までです（{len(values)} 枚）"
        )
    if any(value < 0 for value in values):
        raise ContactSheetError("秒は 0 以上で指定してください")
    if duration and duration > 0:
        # 末尾ちょうどはコマが無いことがあるので、わずかに内側へ寄せる
        last = max(0.0, duration - 0.05)
        values = [min(value, last) for value in values]
    return values


def frame_label(second: float, fps: float | None) -> str:
    """コマに焼くラベル（秒と、fps が分かればフレーム番号）。"""
    if fps and fps > 0:
        return f"{second:.2f}s  #{round(second * fps)}"
    return f"{second:.2f}s"


def cell_size(
    frame_size: tuple[int, int], width: int, labels: bool
) -> tuple[int, int, int]:
    """1 コマぶんの ``(幅, 高さ, ラベル帯の高さ)``。

    高さは元コマの縦横比から決め、``labels`` のときだけ下にラベル帯を足す。
    """
    source_width, source_height = frame_size
    if source_width <= 0 or source_height <= 0:
        raise ContactSheetError(f"コマの大きさが不正です: {frame_size}")
    height = max(1, round(width * source_height / source_width))
    band = max(12, round(width * LABEL_RATIO)) if labels else 0
    return width, height, band


def grid_size(count: int, columns: int) -> tuple[int, int]:
    """``count`` 枚を ``columns`` 列に並べたときの ``(列数, 行数)``。

    コマが列数より少ないときは列も詰める（4 列指定で 2 枚なら 2 列 1 行）。
    """
    if count <= 0:
        raise ContactSheetError("抜くコマが 1 枚もありません")
    cols = max(1, min(columns, count))
    return cols, math.ceil(count / cols)


def build_grid(
    cells: Sequence[tuple[Image.Image, str]],
    *,
    columns: int = DEFAULT_COLUMNS,
    width: int = DEFAULT_CELL_WIDTH,
    labels: bool = True,
) -> Image.Image:
    """``(コマ, ラベル)`` の並びを 1 枚のグリッドに貼る（左上から順に）。

    セルの大きさは**先頭のコマ**の縦横比で決め、以降のコマもその枠に合わせて
    縮小する（解像度の違う素材が混ざっても格子は崩れない）。
    """
    if not cells:
        raise ContactSheetError("抜くコマが 1 枚もありません")
    cols, rows = grid_size(len(cells), columns)
    cell_width, cell_height, band = cell_size(cells[0][0].size, width, labels)
    canvas = Image.new(
        "RGB", (cols * cell_width, rows * (cell_height + band)), BACKGROUND
    )
    draw = ImageDraw.Draw(canvas)
    font = textimage.load_font(
        textimage.default_font(), max(9, round(band * LABEL_TEXT_RATIO))
    )
    for position, (frame, label) in enumerate(cells):
        col, row = position % cols, position // cols
        left = col * cell_width
        top = row * (cell_height + band)
        canvas.paste(
            frame.convert("RGB").resize((cell_width, cell_height), Image.LANCZOS),
            (left, top),
        )
        if band:
            draw.rectangle(
                [left, top + cell_height, left + cell_width - 1, top + cell_height + band - 1],
                fill=LABEL_BACKGROUND,
            )
            draw.text(
                (left + 6, top + cell_height + band // 2),
                label,
                font=font,
                fill=LABEL_COLOR,
                anchor="lm",
            )
    return canvas


# --------------------------------------------------------------------------
# ffmpeg（コマを抜く）
# --------------------------------------------------------------------------

async def _run(argv: list[str], *, timeout: float = FRAME_TIMEOUT) -> tuple[int, str]:
    try:
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except (OSError, ValueError) as exc:
        raise ContactSheetError(
            f"'{argv[0]}' を実行できませんでした（ffmpeg が要ります）: {exc}"
        ) from exc
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except asyncio.TimeoutError as exc:
        process.kill()
        await process.wait()
        raise ContactSheetError(
            f"'{argv[0]}' が {timeout:.0f} 秒以内に応答しませんでした"
        ) from exc
    output = stdout.decode("utf-8", "replace") or stderr.decode("utf-8", "replace")
    return process.returncode or 0, output


async def probe_video(path: str | Path) -> tuple[float | None, float | None]:
    """動画の ``(尺, fps)``（読めなければ None）。"""
    code, output = await _run(
        [
            FFPROBE, "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=r_frame_rate",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1",
            str(path),
        ],
        timeout=30.0,
    )
    if code != 0:
        log.info("ffprobe が %s を読めませんでした: %s", path, output.strip()[:200])
        return None, None
    duration: float | None = None
    fps: float | None = None
    for line in output.splitlines():
        key, _, value = line.partition("=")
        value = value.strip()
        if key.strip() == "duration":
            try:
                duration = float(value) or None
            except ValueError:
                duration = None
        elif key.strip() == "r_frame_rate" and "/" in value:
            numerator, _, denominator = value.partition("/")
            try:
                fps = float(numerator) / float(denominator)
            except (ValueError, ZeroDivisionError):
                fps = None
    return duration, fps


async def extract_frame(video: str | Path, second: float, dest: Path) -> Image.Image:
    """``second`` のコマを 1 枚抜いて開く。"""
    code, output = await _run([
        FFMPEG, "-v", "error", "-y",
        "-ss", f"{max(0.0, second):.3f}",
        "-i", str(video),
        "-frames:v", "1",
        str(dest),
    ])
    if code != 0 or not dest.is_file() or dest.stat().st_size == 0:
        raise ContactSheetError(
            f"{second:.2f} 秒のコマを抜けませんでした: {output.strip()[:200]}"
        )
    try:
        with Image.open(dest) as frame:
            frame.load()
            return frame.convert("RGB")
    except (OSError, UnidentifiedImageError) as exc:
        raise ContactSheetError(f"{second:.2f} 秒のコマを読めませんでした") from exc


async def render_contact_sheet(
    video: str | Path,
    *,
    seconds: Sequence[float] | None = None,
    span: dict | None = None,
    frames: Sequence[int] | None = None,
    columns: int = DEFAULT_COLUMNS,
    width: int = DEFAULT_CELL_WIDTH,
    labels: bool = True,
) -> tuple[bytes, list[float]]:
    """動画からコマを抜いて 1 枚のシートに束ね、``(jpg のバイト列, 抜いた秒)``。"""
    source = Path(video)
    if not source.is_file():
        raise ContactSheetError(f"動画が見つかりません: {source}")
    cols = check_columns(columns)
    cell = check_width(width)
    duration, fps = await probe_video(source)
    wanted = plan_seconds(
        seconds=seconds, span=span, frames=frames, fps=fps, duration=duration
    )
    cells: list[tuple[Image.Image, str]] = []
    with tempfile.TemporaryDirectory(prefix="contact-sheet-") as workdir:
        for index, second in enumerate(wanted):
            dest = Path(workdir) / f"frame_{index:03d}.png"
            cells.append(
                (await extract_frame(source, second, dest), frame_label(second, fps))
            )
        sheet = build_grid(cells, columns=cols, width=cell, labels=labels)
    buffer = BytesIO()
    sheet.save(buffer, format="JPEG", quality=JPEG_QUALITY)
    return buffer.getvalue(), wanted
