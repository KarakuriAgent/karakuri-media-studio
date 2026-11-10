"""取り込むときに、そのままでは使いにくい素材を使える形に直す。

- **SVG → PNG**（:func:`svg_to_png`）: 透過を保ったまま、長辺を指定の大きさ（既定
  :data:`DEFAULT_SVG_SIZE`）にして PNG にする。ラスタライズは ffmpeg の librsvg
  （コンテナの ffmpeg に入っている。依存を増やさない）。ffmpeg の librsvg は
  アルファを**乗算済み**で返すので、Pillow で乗算を外してから保存する（外さないと
  半透明の縁が黒ずむ）

素材パックの取り込み（:mod:`app.asset_packs`）とアップロード（:func:`app.library.add_upload`）
が :func:`app.library.store_source` を通して使う。元のファイルは
``library/originals/`` に残し、:class:`app.models.LibraryMediaInfo` の
``converted_from`` / ``original_url`` から辿れるようにする。
"""

from __future__ import annotations

import asyncio
import math
import re
import tempfile
import xml.etree.ElementTree as ElementTree
from io import BytesIO
from pathlib import Path

FFMPEG = "ffmpeg"

#: SVG を PNG にするときの長辺（px）の既定と範囲
DEFAULT_SVG_SIZE = 2048
MIN_SVG_SIZE = 16
MAX_SVG_SIZE = 8192
#: 受け付ける SVG の大きさ（バイト）
MAX_SVG_BYTES = 20 * 1024 * 1024
#: 1 回の変換に待つ最長の時間（秒）
SVG_TIMEOUT_SECONDS = 60.0

#: 変換して取り込む拡張子 -> 変換後の拡張子
SVG_EXT = ".svg"

_LENGTH = re.compile(r"^\s*([0-9]*\.?[0-9]+(?:[eE][-+]?[0-9]+)?)\s*(px|pt|pc|mm|cm|in)?\s*$")
_UNIT = {None: 1.0, "px": 1.0, "pt": 4 / 3, "pc": 16.0, "mm": 96 / 25.4, "cm": 96 / 2.54, "in": 96.0}


class ConvertError(Exception):
    """変換できない（理由はメッセージ）。"""


async def _run(*command: str, timeout: float) -> tuple[int, str]:
    """コマンドを走らせて ``(終了コード, stderr)``。起動できなければ 127。"""
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
    except (FileNotFoundError, OSError):
        return 127, f"{command[0]} を起動できません"
    try:
        _, err = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        return 124, "時間がかかりすぎました"
    return process.returncode or 0, err.decode("utf-8", "replace")


# --------------------------------------------------------------------------
# SVG
# --------------------------------------------------------------------------

def check_svg_size(value: object) -> int:
    """長辺の大きさ（空・0 なら既定）。範囲の外なら :class:`ConvertError`。"""
    if value in (None, "", 0):
        return DEFAULT_SVG_SIZE
    try:
        size = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ConvertError(f"svg_size は整数で書いてください: {value}") from exc
    if not MIN_SVG_SIZE <= size <= MAX_SVG_SIZE:
        raise ConvertError(f"svg_size は {MIN_SVG_SIZE}〜{MAX_SVG_SIZE} で書いてください: {size}")
    return size


def _length(value: str | None) -> float | None:
    if not value:
        return None
    match = _LENGTH.match(value)
    if not match:
        return None  # % や em は大きさとして使わない
    number = float(match.group(1)) * _UNIT[match.group(2)]
    return number if number > 0 and math.isfinite(number) else None


def svg_aspect(data: bytes) -> float:
    """SVG の縦横比（幅 / 高さ）。``viewBox`` か ``width`` / ``height`` から。分からなければ 1。"""
    try:
        for _event, element in ElementTree.iterparse(BytesIO(data), events=("start",)):
            if not element.tag.endswith("svg"):
                raise ConvertError("SVG ではありません（いちばん外が <svg> ではありません）")
            box = (element.get("viewBox") or "").replace(",", " ").split()
            if len(box) == 4:
                try:
                    width, height = float(box[2]), float(box[3])
                except ValueError:
                    width = height = 0.0
                if width > 0 and height > 0:
                    return width / height
            width_px = _length(element.get("width"))
            height_px = _length(element.get("height"))
            if width_px and height_px:
                return width_px / height_px
            return 1.0
    except ElementTree.ParseError as exc:
        raise ConvertError(f"SVG を読めません（{exc}）") from exc
    raise ConvertError("SVG を読めません（中身が空です）")


def raster_size(aspect: float, long_side: int) -> tuple[int, int]:
    """長辺を ``long_side`` にした大きさ（どちらも 1 以上）。"""
    if aspect >= 1:
        return long_side, max(1, round(long_side / aspect))
    return max(1, round(long_side * aspect)), long_side


def _unpremultiply(png: bytes) -> bytes:
    """乗算済みアルファの PNG を、ふつうの（乗算していない）RGBA の PNG にする。"""
    from PIL import Image

    with Image.open(BytesIO(png)) as image:
        rgba = image.convert("RGBA")
        straight = Image.frombytes("RGBa", rgba.size, rgba.tobytes()).convert("RGBA")
    out = BytesIO()
    straight.save(out, format="PNG", optimize=True)
    return out.getvalue()


async def svg_to_png(data: bytes, long_side: int = DEFAULT_SVG_SIZE) -> bytes:
    """SVG を、透過を保ったまま長辺 ``long_side`` px の PNG にする。

    SVG は他のファイルと切り離した一時フォルダに 1 つだけ置いてから読む
    （ffmpeg の librsvg はデータとして読むので、外のファイルや URL は参照しない）。
    """
    if len(data) > MAX_SVG_BYTES:
        raise ConvertError(f"SVG が大きすぎます（{MAX_SVG_BYTES // (1024 * 1024)}MB まで）")
    width, height = raster_size(svg_aspect(data), check_svg_size(long_side))
    with tempfile.TemporaryDirectory(prefix="karakuri-svg-") as folder:
        source = Path(folder) / "in.svg"
        target = Path(folder) / "out.png"
        source.write_bytes(data)
        code, err = await _run(
            FFMPEG, "-nostdin", "-v", "error", "-y",
            "-f", "svg_pipe", "-width", str(width), "-height", str(height),
            "-i", str(source), "-frames:v", "1", "-pix_fmt", "rgba",
            "-c:v", "png", "-f", "image2", str(target),
            timeout=SVG_TIMEOUT_SECONDS,
        )
        if code != 0 or not target.is_file():
            detail = err.strip().splitlines()[-1] if err.strip() else f"終了コード {code}"
            raise ConvertError(f"SVG を PNG にできませんでした（{detail}）")
        png = target.read_bytes()
    return await asyncio.to_thread(_unpremultiply, png)
