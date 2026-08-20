"""編集タブの書き出し: EDL -> ffmpeg コマンド -> 1 本の mp4。

組み立て（:func:`build_command`）と実行（:func:`run_export`）を分けてある。
組み立ては純関数なので、ffmpeg を持たない環境でもテストで固定できる。

フェーズ 1 で焼けるのは **V1（``kind='video'``）の take クリップだけ**:

- クリップごとに ``trim`` + ``setpts`` で切り出し、``scale`` + ``pad`` +
  ``setsar`` + ``fps`` でタイムラインの規格（幅・高さ・fps・SAR）へ正規化する。
  音声も ``atrim`` + ``asetpts`` + ``aresample`` + ``aformat`` で揃える。
- 音声を持たないソースと ``gap``（隙間）のクリップには、その尺ぶんの黒
  （``color``）と無音（``anullsrc``）を ``lavfi`` から作って充てる。全クリップが
  「映像 1 本 + 音声 1 本」になるので、そのまま ``concat`` で繋げる。
- 出力は H.264 + AAC / yuv420p / faststart で
  ``outputs/exports/{export_id}/final.mp4``（``/outputs`` で配信できる）。

進捗は ``-progress pipe:1`` の ``out_time_us`` を読んで
``timeline_exports.progress`` に書き、WS（``type: "timeline_export"``）へ流す。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

#: ffmpeg / ffprobe の呼び出し名（:mod:`app.jobs` と同じ流儀でテストが差し替える）
FFMPEG = "ffmpeg"

#: 音声の規格（クリップごとにばらばらな入力をここへ揃えてから連結する）
AUDIO_RATE = 48000
AUDIO_LAYOUT = "stereo"

#: 出力のエンコード設定
VIDEO_CODEC = ("libx264", "-preset", "medium", "-crf", "18")
AUDIO_BITRATE = "192k"


class TimelineExportError(Exception):
    """書き出しの失敗（``timeline_exports.error`` に入る）。"""


@dataclass
class ExportClip:
    """書き出しに載せるクリップ 1 つ（DB の行から解決済みのもの）。

    ``path`` が None なら隙間（``gap``）で、その尺ぶんの黒＋無音になる。
    """

    #: ソースの実ファイル（None = 隙間）
    path: str | None
    #: ソースの中の切り出し位置（ミリ秒）
    in_ms: int
    out_ms: int
    #: タイムライン上の尺（ミリ秒）。等速なので ``out_ms - in_ms`` と同じ
    duration_ms: int
    #: ソースが音声トラックを持っているか（持たなければ無音を充てる）
    has_audio: bool = True
    #: 音量調整（0 なら ``volume`` フィルタを挟まない）
    gain_db: float = 0.0


@dataclass
class ExportSpec:
    """1 回の書き出しの規格と中身。"""

    width: int
    height: int
    fps: float
    clips: list[ExportClip] = field(default_factory=list)

    @property
    def duration_ms(self) -> int:
        return sum(max(0, clip.duration_ms) for clip in self.clips)


def _seconds(ms: int) -> str:
    """ミリ秒を ffmpeg のフィルタに書ける秒表記にする（小数 3 桁）。"""
    return f"{ms / 1000:.3f}"


def _fps(value: float) -> str:
    """fps を ffmpeg に渡す表記にする（整数なら整数のまま）。"""
    return str(int(value)) if float(value).is_integer() else f"{value:.4f}"


def build_command(spec: ExportSpec, output: str | Path) -> list[str]:
    """``spec`` を 1 本の mp4 に焼く ffmpeg のコマンドライン。

    純関数（ファイルも ffmpeg も触らない）なのでテストで固定できる。クリップが
    1 つも無い（または尺が全部 0）なら :class:`TimelineExportError`。
    """
    clips = [clip for clip in spec.clips if clip.duration_ms > 0]
    if not clips:
        raise TimelineExportError("書き出せるクリップがありません")
    if spec.width <= 0 or spec.height <= 0 or spec.fps <= 0:
        raise TimelineExportError("タイムラインの規格（幅・高さ・fps）が不正です")

    width, height = spec.width, spec.height
    fps = _fps(spec.fps)

    inputs: list[str] = []
    filters: list[str] = []
    concat_labels: list[str] = []
    index = 0  # 次に足す入力の番号

    for position, clip in enumerate(clips):
        duration = _seconds(clip.duration_ms)
        video_label = f"v{position}"
        audio_label = f"a{position}"

        if clip.path is None:
            # 隙間: 黒 + 無音をその尺ぶん作る（ソースが無いので trim は要らない）。
            inputs += [
                "-f", "lavfi",
                "-t", duration,
                "-i", f"color=c=black:s={width}x{height}:r={fps}",
            ]
            filters.append(f"[{index}:v]setsar=1[{video_label}]")
            index += 1
            inputs += [
                "-f", "lavfi",
                "-t", duration,
                "-i", f"anullsrc=channel_layout={AUDIO_LAYOUT}:sample_rate={AUDIO_RATE}",
            ]
            filters.append(f"[{index}:a]asetpts=PTS-STARTPTS[{audio_label}]")
            index += 1
            concat_labels += [f"[{video_label}]", f"[{audio_label}]"]
            continue

        source_index = index
        inputs += ["-i", str(clip.path)]
        index += 1
        start, end = _seconds(clip.in_ms), _seconds(clip.out_ms)
        # 切り出し -> タイムラインの解像度へ収めて余白を足す -> SAR と fps を揃える。
        # scale の force_original_aspect_ratio=decrease + pad なので、比の違う
        # ソースは切らずに黒帯が付く（crop で切り落とすより素材を失わない）。
        filters.append(
            f"[{source_index}:v]trim=start={start}:end={end},"
            f"setpts=PTS-STARTPTS,"
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,"
            f"setsar=1,fps={fps}[{video_label}]"
        )

        if clip.has_audio:
            chain = (
                f"[{source_index}:a]atrim=start={start}:end={end},"
                f"asetpts=PTS-STARTPTS,"
                f"aresample={AUDIO_RATE},"
                f"aformat=sample_fmts=fltp:channel_layouts={AUDIO_LAYOUT}"
            )
            if clip.gain_db:
                chain += f",volume={clip.gain_db:g}dB"
            filters.append(f"{chain}[{audio_label}]")
        else:
            # 音声を持たないソース。concat は全クリップに同じ本数の
            # ストリームを要求するので、尺ぶんの無音を足して形を揃える。
            inputs += [
                "-f", "lavfi",
                "-t", duration,
                "-i", f"anullsrc=channel_layout={AUDIO_LAYOUT}:sample_rate={AUDIO_RATE}",
            ]
            filters.append(f"[{index}:a]asetpts=PTS-STARTPTS[{audio_label}]")
            index += 1

        concat_labels += [f"[{video_label}]", f"[{audio_label}]"]

    filters.append(
        f"{''.join(concat_labels)}concat=n={len(clips)}:v=1:a=1[outv][outa]"
    )

    return [
        FFMPEG,
        "-hide_banner",
        "-nostdin",
        "-y",
        "-loglevel", "error",
        "-progress", "pipe:1",
        *inputs,
        "-filter_complex", ";".join(filters),
        "-map", "[outv]",
        "-map", "[outa]",
        "-c:v", VIDEO_CODEC[0],
        *VIDEO_CODEC[1:],
        "-pix_fmt", "yuv420p",
        "-r", fps,
        "-c:a", "aac",
        "-b:a", AUDIO_BITRATE,
        "-movflags", "+faststart",
        str(output),
    ]


def parse_progress_line(line: str) -> float | None:
    """``-progress pipe:1`` の 1 行から、焼けた秒数を取り出す（無関係な行は None）。

    ffmpeg は ``out_time_us=1234567`` / ``out_time_ms=…`` / ``out_time=00:00:01.23``
    を流す。``out_time_us`` を正としつつ、古い ffmpeg のために ``out_time_ms``
    （実体はマイクロ秒）も受ける。
    """
    key, _, raw = line.strip().partition("=")
    value = raw.strip()
    if key not in ("out_time_us", "out_time_ms") or not value:
        return None
    try:
        micros = float(value)
    except ValueError:
        return None
    return max(0.0, micros / 1_000_000)


async def run_export(
    spec: ExportSpec,
    output: Path,
    *,
    on_progress: Callable[[float], Awaitable[None]] | None = None,
) -> Path:
    """``spec`` を ``output`` に焼く（進捗があれば ``on_progress(0.0〜1.0)``）。

    失敗したら :class:`TimelineExportError`（メッセージに stderr の末尾）。
    """
    command = build_command(spec, output)
    output.parent.mkdir(parents=True, exist_ok=True)
    total = spec.duration_ms / 1000

    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise TimelineExportError(
            f"書き出しには ffmpeg が要りますが見つかりませんでした（{exc}）"
        ) from exc
    except (OSError, ValueError) as exc:
        raise TimelineExportError(f"ffmpeg を起動できませんでした: {exc}") from exc

    async def _pump_progress() -> None:
        """``-progress pipe:1`` を 1 行ずつ読んで進捗に直す。

        ``communicate()`` は使わない（あちらも stdout を読むので、同じストリームを
        2 つのコルーチンが待つことになって落ちる）。stdout / stderr を自分で
        並行に読み切り、そのあと :meth:`wait` で終了を待つ。
        """
        assert process.stdout is not None
        while True:
            raw = await process.stdout.readline()
            if not raw:
                return
            seconds = parse_progress_line(raw.decode("utf-8", "replace"))
            if seconds is None or on_progress is None or total <= 0:
                continue
            try:
                await on_progress(min(0.99, seconds / total))
            except Exception:  # noqa: BLE001 - 進捗の通知で書き出しを壊さない
                log.debug("書き出しの進捗通知に失敗しました", exc_info=True)

    async def _read_stderr() -> bytes:
        assert process.stderr is not None
        return await process.stderr.read()

    _, stderr = await asyncio.gather(_pump_progress(), _read_stderr())
    await process.wait()

    if process.returncode != 0:
        detail = (stderr or b"").decode("utf-8", "replace").strip()[-800:]
        raise TimelineExportError(f"ffmpeg が失敗しました: {detail}")
    if not output.is_file() or output.stat().st_size == 0:
        raise TimelineExportError("ffmpeg は終了しましたが出力が空でした")
    return output
