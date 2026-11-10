"""素材を測る: 音の長さとラウドネス、画像の大きさと透過、動画の長さと大きさ。

素材パックの取り込み（:mod:`app.asset_packs`）が、ライブラリへ登録するときに
:class:`app.models.LibraryMediaInfo` として持たせる。測れなかった項目は None の
まま返し、取り込みそのものは止めない（測れないことは素材の欠陥ではないため）。

ffprobe / ffmpeg の呼び出し名は :mod:`app.timeline_export` と同じく差し替えられる
ようにモジュールの定数に置く。
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
from pathlib import Path

from .models import LibraryMediaInfo

log = logging.getLogger(__name__)

FFMPEG = "ffmpeg"
FFPROBE = "ffprobe"

#: 1 回の測定に待つ最長の時間（秒）。長い BGM の loudnorm でもこれで足りる
MEASURE_TIMEOUT_SECONDS = 120.0


async def _run(*command: str) -> tuple[int, str, str]:
    """コマンドを走らせて ``(終了コード, stdout, stderr)``。起動できなければ 127。"""
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except (FileNotFoundError, OSError):
        return 127, "", ""
    try:
        out, err = await asyncio.wait_for(
            process.communicate(), timeout=MEASURE_TIMEOUT_SECONDS
        )
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        return 124, "", ""
    return (
        process.returncode or 0,
        out.decode("utf-8", "replace"),
        err.decode("utf-8", "replace"),
    )


def _float(value: object) -> float | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


async def probe(path: Path) -> dict:
    """ffprobe の ``format`` と ``streams``（読めなければ空の dict）。"""
    code, out, _ = await _run(
        FFPROBE, "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
    )
    if code != 0 or not out.strip():
        return {}
    try:
        return json.loads(out)
    except ValueError:
        return {}


def parse_loudnorm(stderr: str) -> tuple[float | None, float | None]:
    """``loudnorm=print_format=json`` の出力から ``(積分ラウドネス, 真のピーク)``。

    JSON は stderr の最後の ``{ … }`` に出る。無音は ``-inf`` になるので None。
    """
    start = stderr.rfind("{")
    end = stderr.rfind("}")
    if start < 0 or end <= start:
        return None, None
    try:
        data = json.loads(stderr[start : end + 1])
    except ValueError:
        return None, None
    return _float(data.get("input_i")), _float(data.get("input_tp"))


async def loudness(path: Path) -> tuple[float | None, float | None]:
    """音の積分ラウドネス（LUFS）と真のピーク（dBTP）。"""
    code, _, err = await _run(
        FFMPEG, "-hide_banner", "-nostats", "-i", str(path),
        "-af", "loudnorm=print_format=json", "-f", "null", "-",
    )
    if code != 0:
        return None, None
    return parse_loudnorm(err)


def image_info(path: Path) -> LibraryMediaInfo:
    """画像の大きさと、透過している画素があるか（Pillow）。"""
    try:
        from PIL import Image

        with Image.open(path) as image:
            width, height = image.size
            has_alpha = False
            if image.mode in ("RGBA", "LA", "PA") or "transparency" in image.info:
                alpha = image.convert("RGBA").getchannel("A")
                low, _high = alpha.getextrema()
                has_alpha = low < 255
            return LibraryMediaInfo(width=width, height=height, has_alpha=has_alpha)
    except Exception:  # noqa: BLE001 - 測れないだけで取り込みは止めない
        log.debug("画像を測れませんでした: %s", path, exc_info=True)
        return LibraryMediaInfo()


async def measure(path: str | Path, kind: str) -> LibraryMediaInfo:
    """素材を測る（測れなかった項目は None）。"""
    target = Path(path)
    if kind == "image":
        return await asyncio.to_thread(image_info, target)
    info = LibraryMediaInfo()
    data = await probe(target)
    duration = _float((data.get("format") or {}).get("duration"))
    if duration is not None:
        info.duration_ms = int(round(duration * 1000))
    streams = data.get("streams") or []
    if kind == "video":
        video = next((s for s in streams if s.get("codec_type") == "video"), None)
        if video is not None:
            info.width = int(video.get("width") or 0) or None
            info.height = int(video.get("height") or 0) or None
        info.has_audio = any(s.get("codec_type") == "audio" for s in streams) if data else None
    if kind == "audio":
        info.loudness_lufs, info.true_peak_db = await loudness(target)
    return info
