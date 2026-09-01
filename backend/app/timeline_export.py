"""編集タブの書き出し: EDL -> ffmpeg コマンド -> 1 本の mp4。

組み立て（:func:`build_command`）と実行（:func:`run_export`）を分けてある。
組み立ては純関数なので、ffmpeg を持たない環境でもテストで固定できる。

焼けるもの:

- **映像トラック（V1）** … クリップごとに ``trim`` + ``setpts`` で切り出し、
  ``scale`` + ``pad``（または ``crop``）+ ``setsar`` + ``fps`` でタイムラインの
  規格へ正規化する。音声も ``atrim`` + ``asetpts`` + ``aresample`` + ``aformat``
  で揃える。速度を変えたクリップは ``setpts`` / ``atempo`` が入る。
- **静止画クリップ** … ``-loop 1 -t`` で尺ぶんの映像にしてから同じ規格へ。
- **繋ぎ（トランジション）** … **オーバーラップ方式**。繋ぎのないところは
  ``concat`` のまま繋ぎ、繋ぎのあるところで ``xfade`` / ``acrossfade`` に切り替える
  （全長はその分だけ縮む）。
- **音声トラック（A1…）** … ``atrim`` + ``afade`` + ``volume`` + ``adelay`` で
  置き場所へずらし、映像の音と ``amix`` する。長さは映像側に合わせて切る。
- **テロップ（T1）** … ASS ファイル（:mod:`app.timeline_subtitles`）を
  ``subtitles`` フィルタで焼き込む。
- **ラウドネス正規化** … 最後に ``loudnorm``（1 パス）。

音声を持たないソースと ``gap``（隙間）のクリップには、その尺ぶんの黒
（``color``）と無音（``anullsrc``）を ``lavfi`` から作って充てる。全クリップが
「映像 1 本 + 音声 1 本」になるので、繋ぎ方に関わらず形が揃う。

**境界はすべてフレーム番号が正本**（:func:`frame_count`）。秒のまま切ると端数
フレームが捨てられて連結後に映像が先走るので、各クリップは 1 フレームぶん余分に
取ってから ``trim=end_frame=<枚数>`` でちょうどその枚数に切る。音も
``apad`` + ``atrim`` で同じ長さへ揃える（映像と音の尺がずれたまま ``concat`` に
渡さない）。素材の実尺が「切り出し位置 + 尺」に届かないクリップは
``tpad=stop_mode=clone`` の末尾静止で埋め、どれだけ足りなかったかを
:func:`pad_warnings` が ``PAD <クリップ名> <不足秒>s`` として返す。

出力は H.264 + AAC / yuv420p / faststart で ``outputs/exports/{export_id}/final.mp4``
（``/outputs`` で配信できる）。進捗は ``-progress pipe:1`` の ``out_time_us`` を
読んで ``timeline_exports.progress`` に書き、WS（``type: "timeline_export"``）へ流す。
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

#: 画面に出す繋ぎの種別 -> ffmpeg ``xfade`` の ``transition``。
#: 画面の選択肢はここのキーが正本（増やすならここと UI の両方）。
TRANSITIONS: dict[str, str] = {
    "crossfade": "fade",
    "fadeblack": "fadeblack",
    "fadewhite": "fadewhite",
    "wipeleft": "wipeleft",
    "wiperight": "wiperight",
    "slideleft": "slideleft",
    "slideright": "slideright",
    "circleopen": "circleopen",
    "pixelize": "pixelize",
}

#: 繋ぎの長さの範囲（ミリ秒）。UI のスライダーと検証で共有する
TRANSITION_MIN_MS = 200
TRANSITION_MAX_MS = 2000

#: リタイムの範囲（``timeline_clips.speed``）
SPEED_MIN = 0.25
SPEED_MAX = 4.0

#: ``atempo`` が 1 段で受けられる範囲（外れる速度は掛け算に分解する）
ATEMPO_MIN = 0.5
ATEMPO_MAX = 2.0

#: ラウドネス正規化の狙い（配信向けの一般的な値）
LOUDNORM_TARGET_LUFS = -14.0
LOUDNORM_TRUE_PEAK_DB = -1.5
LOUDNORM_RANGE = 11.0

#: 末尾静止（``tpad``）に持たせる余裕（秒）。素材がちょうど足りている場合でも、
#: 丸めで最後の 1 フレームが出ないことがあるので必ずこれだけは伸ばしておく
#: （伸ばしたぶんは ``trim=end_frame`` で切り落とされるので害はない）。
PAD_MARGIN_SECONDS = 0.25

#: 縦横比が変わるときの収め方（黒帯 / 中央を切り出す）
FIT_PAD = "pad"
FIT_CROP = "crop"

#: 書き出しプリセット -> ``(幅, 高さ)``。``timeline`` はタイムラインの規格のまま
PRESETS: dict[str, tuple[int, int] | None] = {
    "timeline": None,
    "1080p": (1920, 1080),
    "vertical": (1080, 1920),
    "720p": (1280, 720),
}


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
    #: タイムライン上の尺（ミリ秒）。``(out_ms - in_ms) / speed``
    duration_ms: int
    #: ソースが音声トラックを持っているか（持たなければ無音を充てる）
    has_audio: bool = True
    #: 音量調整（0 なら ``volume`` フィルタを挟まない）
    gain_db: float = 0.0
    #: 再生速度（1.0 = 等速）
    speed: float = 1.0
    #: ``video`` = 動画 / ``image`` = 静止画（``-loop 1`` で尺ぶんに伸ばす）
    kind: str = "video"
    #: **前の**クリップとの繋ぎ（None = カット）
    transition_kind: str | None = None
    transition_ms: int = 0
    #: ソースそのものの長さ（ミリ秒）。``out_ms`` に届かないときは末尾静止
    #: （``tpad``）で埋めて :func:`pad_warnings` に出す。None = 測れていない
    source_duration_ms: int | None = None
    #: 警告に出すときの呼び名（カットの見出し。空なら通し番号で呼ぶ）
    name: str = ""

    @property
    def overlap_ms(self) -> int:
        """前のクリップと重なる長さ（繋ぎが無ければ 0）。"""
        if not self.transition_kind or self.transition_ms <= 0:
            return 0
        return self.transition_ms


@dataclass
class ExportAudioClip:
    """音声トラック（A1…）に置かれたクリップ 1 つ。

    映像と違って自由配置なので、``start_ms``（タイムライン上の置き場所）を
    ``adelay`` で作る。尺より長いソースは ``out_ms`` で切る（ループはしない）。
    """

    path: str
    #: タイムライン上の置き場所（ミリ秒）
    start_ms: int
    #: ソースの中の切り出し位置（ミリ秒）
    in_ms: int
    out_ms: int
    gain_db: float = 0.0
    fade_in_ms: int = 0
    fade_out_ms: int = 0

    @property
    def duration_ms(self) -> int:
        return max(0, self.out_ms - self.in_ms)


@dataclass
class ExportSpec:
    """1 回の書き出しの規格と中身。"""

    width: int
    height: int
    fps: float
    clips: list[ExportClip] = field(default_factory=list)
    #: 音声トラックのクリップ（BGM / SE）
    audio_clips: list[ExportAudioClip] = field(default_factory=list)
    #: 焼き込む字幕ファイル（ASS）。None なら焼き込まない
    subtitles_path: str | None = None
    #: 縦横比が変わるときの収め方（:data:`FIT_PAD` / :data:`FIT_CROP`）
    fit: str = FIT_PAD
    #: ラウドネス正規化を掛けるか
    loudnorm: bool = False

    @property
    def total_frames(self) -> int:
        """繋ぎの重なりを引いた**フレーム数**（焼き上がりの正本）。

        クリップごとに :func:`frame_count` で量子化してから足し引きするので、
        ここで出る枚数が ``ffprobe -count_frames`` の実測と一致するはず
        （合わなければ :func:`frame_warning` が警告を出す）。
        """
        if self.fps <= 0:
            return 0
        total = 0
        for index, clip in enumerate(self.clips):
            total += frame_count(clip.duration_ms, self.fps)
            if index > 0:
                total -= frame_count(clip.overlap_ms, self.fps)
        return max(0, total)

    @property
    def duration_ms(self) -> int:
        """繋ぎの重なりを引いたタイムラインの全長（フレーム数から逆算）。"""
        if self.fps <= 0:
            return 0
        return int(round(self.total_frames / self.fps * 1000))


# --------------------------------------------------------------------------
# 純関数の小道具（どれも ffmpeg を触らない。テストで固定できる）
# --------------------------------------------------------------------------

def _seconds(ms: int) -> str:
    """ミリ秒を ffmpeg のフィルタに書ける秒表記にする（小数 3 桁）。"""
    return f"{ms / 1000:.3f}"


def frame_count(duration_ms: int, fps: float) -> int:
    """ミリ秒をフレーム数へ量子化する（境界の正本。``round(t * fps)``）。

    秒のまま ``-ss`` / ``-t`` に渡すと端数フレームが捨てられて、連結後に映像が
    最大 2 フレーム先走る（BAN!BAN!BAN! で実測）。書き出しの長さに関わる計算は
    すべてここを通してフレーム番号にしてから行う。
    """
    if fps <= 0:
        raise TimelineExportError(f"fps が不正です: {fps}")
    return max(0, int(round(max(0, duration_ms) / 1000 * fps)))


def _frame_seconds(frames: int, fps: float) -> str:
    """フレーム数を ffmpeg のフィルタに書ける秒表記にする（小数 6 桁）。

    フレーム境界は 3 桁では割り切れない（1/30 秒 = 0.0333…）ので、秒表記へ
    落とすところだけ桁を増やす。
    """
    return f"{frames / fps:.6f}"


def shortfall_ms(clip: ExportClip) -> int:
    """素材の実尺が「切り出し位置 + 尺」にどれだけ届いていないか（足りていれば 0）。

    測れていない（``source_duration_ms`` が None）・隙間・静止画は 0。
    """
    if clip.path is None or clip.kind == "image" or clip.source_duration_ms is None:
        return 0
    return max(0, int(clip.out_ms) - int(clip.source_duration_ms))


def pad_warnings(spec: ExportSpec) -> list[str]:
    """末尾静止で埋めたクリップの警告（``PAD カット名 0.42s``）を並べる。

    1 フレームぶんの不足は丸めの範囲なので黙って埋める（警告にしない）。
    """
    if spec.fps <= 0:
        return []
    frame_ms = 1000 / spec.fps
    warnings: list[str] = []
    for index, clip in enumerate(spec.clips):
        short = shortfall_ms(clip)
        if short <= frame_ms:
            continue
        name = clip.name or f"クリップ {index + 1}"
        warnings.append(f"PAD {name} {short / 1000:.2f}s")
    return warnings


def frame_warning(expected: int, actual: int | None) -> str | None:
    """焼き上がりの総フレーム数が計画と違ったときの警告（同じなら None）。"""
    if actual is None or actual == expected:
        return None
    return f"フレーム数が計画と違います（計画 {expected}f / 実測 {actual}f）"


def _fps(value: float) -> str:
    """fps を ffmpeg に渡す表記にする（整数なら整数のまま）。"""
    return str(int(value)) if float(value).is_integer() else f"{value:.4f}"


def _number(value: float) -> str:
    """速度などの実数を短く書く（``1.5`` / ``0.25``）。"""
    return f"{value:g}"


def atempo_chain(speed: float) -> list[float]:
    """``atempo`` は 0.5〜2.0 しか取れないので、積が ``speed`` になる並びに割る。

    たとえば 4.0 は ``[2.0, 2.0]``、0.25 は ``[0.5, 0.5]``、1.0 は ``[1.0]``。
    2 の冪で削ってから端数を最後に置くので、段数は最小になる。
    """
    if speed <= 0:
        raise TimelineExportError(f"速度が不正です: {speed}")
    factors: list[float] = []
    remaining = float(speed)
    while remaining > ATEMPO_MAX + 1e-9:
        factors.append(ATEMPO_MAX)
        remaining /= ATEMPO_MAX
    while remaining < ATEMPO_MIN - 1e-9:
        factors.append(ATEMPO_MIN)
        remaining /= ATEMPO_MIN
    factors.append(round(remaining, 6))
    return factors


def transition_offsets(
    durations: list[int], overlaps: list[int]
) -> tuple[list[int], int]:
    """繋ぎの ``xfade`` の ``offset`` と、繋ぎ終わった全長を出す（純関数）。

    ``durations`` は繋ぎで区切られた**まとまり**の尺、``overlaps`` はその間の
    重なり（``len(durations) - 1`` 個）。``xfade`` は「ここまで積み上げたものの
    末尾から ``offset`` 秒のところで次を重ね始める」ので、
    ``offset = これまでの全長 - 重なり`` になり、全長は重なったぶん縮む。
    """
    if not durations:
        return [], 0
    if len(overlaps) != len(durations) - 1:
        raise TimelineExportError("繋ぎの数がまとまりの数と合いません")
    total = max(0, durations[0])
    offsets: list[int] = []
    for duration, overlap in zip(durations[1:], overlaps):
        offsets.append(max(0, total - overlap))
        total = max(0, total + max(0, duration) - max(0, overlap))
    return offsets, total


def escape_filter_path(path: str | Path) -> str:
    """フィルタの引数に書けるようファイルパスを逃がす（``subtitles=filename=…``）。

    フィルタグラフはまず ``;`` / ``,`` / ``[`` / ``]`` で切られ、そのあと各引数が
    ``:`` で切られるので、いずれもバックスラッシュで逃がす（バックスラッシュ
    自身を最初に倍にする）。
    """
    text = str(path).replace("\\", "\\\\")
    for char in (":", "'", ",", ";", "[", "]"):
        text = text.replace(char, "\\" + char)
    return text


def resolve_format(
    preset: str, width: int, height: int
) -> tuple[int, int]:
    """プリセットから書き出しの幅・高さを決める（未知のプリセットは規格のまま）。"""
    size = PRESETS.get(preset)
    return size if size else (int(width), int(height))


def _scale_chain(width: int, height: int, fit: str) -> str:
    """規格の枠へ収めるフィルタ（黒帯 / 中央切り出し）。"""
    if fit == FIT_CROP:
        return (
            f"scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height}"
        )
    return (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2"
    )


# --------------------------------------------------------------------------
# コマンドの組み立て
# --------------------------------------------------------------------------

class _Graph:
    """フィルタグラフを組みながら入力の本数を数える小さな入れ物。"""

    def __init__(self) -> None:
        self.inputs: list[str] = []
        self.filters: list[str] = []
        self.index = 0

    def add_input(self, *args: str) -> int:
        """``-i`` を 1 本足して、その入力番号を返す。"""
        self.inputs += list(args)
        current = self.index
        self.index += 1
        return current

    def silence(self, duration: str, label: str) -> None:
        """尺ぶんの無音を作って ``label`` に出す。"""
        index = self.add_input(
            "-f", "lavfi",
            "-t", duration,
            "-i", f"anullsrc=channel_layout={AUDIO_LAYOUT}:sample_rate={AUDIO_RATE}",
        )
        self.filters.append(f"[{index}:a]asetpts=PTS-STARTPTS[{label}]")


def _add_clip(
    graph: _Graph,
    clip: ExportClip,
    position: int,
    width: int,
    height: int,
    fps_value: float,
    fit: str,
) -> tuple[str, str]:
    """クリップ 1 つを規格へ正規化して ``(映像ラベル, 音声ラベル)`` を返す。

    尺の正本はフレーム数（:func:`frame_count`）。**1 フレームぶん余分に取ってから
    ``trim=end_frame`` でちょうどその枚数に切る**ので、端数フレームが捨てられて
    連結後に先走ることがない。音も同じ長さへ ``apad`` + ``atrim`` で揃える。
    """
    fps = _fps(fps_value)
    frames = frame_count(clip.duration_ms, fps_value)
    duration = _frame_seconds(frames, fps_value)
    # 余分に取ったぶんを落として、ちょうど frames 枚にする最後の段
    exact = f"trim=end_frame={frames},setpts=PTS-STARTPTS"
    video_label = f"v{position}"
    audio_label = f"a{position}"
    scale = _scale_chain(width, height, fit)

    if clip.path is None:
        # 隙間: 黒 + 無音をその尺ぶん作る（ソースが無いので切り出しは要らない）。
        index = graph.add_input(
            "-f", "lavfi",
            "-t", _frame_seconds(frames + 1, fps_value),
            "-i", f"color=c=black:s={width}x{height}:r={fps}",
        )
        graph.filters.append(f"[{index}:v]setsar=1,{exact}[{video_label}]")
        graph.silence(duration, audio_label)
        return video_label, audio_label

    if clip.kind == "image":
        # 静止画: ``-loop 1`` で尺ぶんの映像にしてから規格へ。音は必ず無音。
        index = graph.add_input(
            "-loop", "1", "-t", _frame_seconds(frames + 1, fps_value),
            "-i", str(clip.path),
        )
        graph.filters.append(
            f"[{index}:v]{scale},setsar=1,fps={fps},{exact}[{video_label}]"
        )
        graph.silence(duration, audio_label)
        return video_label, audio_label

    source_index = graph.add_input("-i", str(clip.path))
    speed = float(clip.speed or 1.0)
    start = _seconds(clip.in_ms)
    # 出口は ``out_ms`` ではなくフレーム数から逆算する（丸めた尺をそのまま渡すと
    # 最後の 1 フレームが落ちる）。素材側は速度ぶん伸びるので掛け戻す。
    end = f"{(clip.in_ms + (frames + 1) / fps_value * 1000 * speed) / 1000:.3f}"
    # 素材が足りないぶんは末尾フレームの静止（tpad）で埋める。足りていても
    # 丸めのぶんだけは伸ばしておく（余りは exact が切り落とす）。
    stop = shortfall_ms(clip) / 1000 + PAD_MARGIN_SECONDS
    # 切り出し -> タイムラインの解像度へ収める -> SAR と fps を揃える。
    # 既定（pad）は force_original_aspect_ratio=decrease + pad なので、比の違う
    # ソースは切らずに黒帯が付く（crop を選べば中央を切り出す）。
    setpts = "PTS-STARTPTS" if abs(speed - 1.0) < 1e-9 else (
        f"(PTS-STARTPTS)/{_number(speed)}"
    )
    graph.filters.append(
        f"[{source_index}:v]trim=start={start}:end={end},"
        f"setpts={setpts},"
        f"{scale},"
        f"setsar=1,fps={fps},"
        f"tpad=stop_mode=clone:stop_duration={stop:.3f},"
        f"{exact}[{video_label}]"
    )

    if clip.has_audio:
        # 音は素材の尺ぶんだけ切り出し、足りなければ無音で埋めてから映像と
        # 同じ長さに切る（映像と音の長さが違うまま concat に渡さない）。
        audio_end = f"{(clip.in_ms + frames / fps_value * 1000 * speed) / 1000:.3f}"
        chain = (
            f"[{source_index}:a]atrim=start={start}:end={audio_end},"
            f"asetpts=PTS-STARTPTS,"
            f"aresample={AUDIO_RATE},"
            f"aformat=sample_fmts=fltp:channel_layouts={AUDIO_LAYOUT}"
        )
        if abs(speed - 1.0) >= 1e-9:
            for factor in atempo_chain(speed):
                if abs(factor - 1.0) >= 1e-9:
                    chain += f",atempo={_number(factor)}"
        if clip.gain_db:
            chain += f",volume={clip.gain_db:g}dB"
        chain += f",apad=whole_dur={duration},atrim=end={duration},asetpts=PTS-STARTPTS"
        graph.filters.append(f"{chain}[{audio_label}]")
    else:
        # 音声を持たないソース。繋ぎ方に関わらず全クリップに同じ本数の
        # ストリームが要るので、尺ぶんの無音を足して形を揃える。
        graph.silence(duration, audio_label)

    return video_label, audio_label


def _join_run(
    graph: _Graph,
    labels: list[tuple[str, str]],
    video_label: str,
    audio_label: str,
) -> tuple[str, str]:
    """繋ぎを挟まない連続したクリップを ``concat`` で 1 本にする。

    クリップが 1 つでも ``concat=n=1`` を通す（ラベルの付け替えとして働くので、
    あとの段が「まとまり」を一様に扱える）。
    """
    joined = "".join(f"[{video}][{audio}]" for video, audio in labels)
    graph.filters.append(
        f"{joined}concat=n={len(labels)}:v=1:a=1[{video_label}][{audio_label}]"
    )
    return video_label, audio_label


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
    fit = spec.fit if spec.fit in (FIT_PAD, FIT_CROP) else FIT_PAD
    graph = _Graph()

    # 1) クリップごとに規格へ正規化する（尺はフレーム数へ量子化される）。
    labels = [
        _add_clip(graph, clip, position, width, height, spec.fps, fit)
        for position, clip in enumerate(clips)
    ]

    # 2) 繋ぎ（オーバーラップ）で区切って「まとまり」を作る。繋ぎのないところは
    #    これまでどおり concat のままなので、繋ぎを使わないタイムラインの
    #    コマンドはフェーズ 1 と 1 文字も変わらない。
    runs: list[list[int]] = [[0]]
    overlaps: list[int] = []
    for position, clip in enumerate(clips[1:], start=1):
        overlap = min(clip.overlap_ms, clips[position - 1].duration_ms)
        if overlap > 0 and clip.transition_kind in TRANSITIONS:
            runs.append([position])
            overlaps.append(overlap)
        else:
            runs[-1].append(position)

    # 繋ぎも音声トラックもテロップも無いときは、``concat`` の出口をそのまま
    # ``[outv]`` / ``[outa]`` にする（余計な通過フィルタを挟まない）。
    single = len(runs) == 1
    video_ends_here = single and not spec.subtitles_path
    audio_ends_here = single and not spec.audio_clips and not spec.loudnorm

    run_labels = [
        _join_run(
            graph,
            [labels[position] for position in run],
            "outv" if video_ends_here else f"r{number}v",
            "outa" if audio_ends_here else f"r{number}a",
        )
        for number, run in enumerate(runs)
    ]
    # 重なりと offset もフレーム数で数える（クリップ側の量子化と揃えないと、
    # 繋ぎのあるタイムラインだけ全長が 1 フレームずれる）。
    overlap_frames = [frame_count(overlap, spec.fps) for overlap in overlaps]
    run_durations = [
        sum(frame_count(clips[position].duration_ms, spec.fps) for position in run)
        for run in runs
    ]
    offsets, _total = transition_offsets(run_durations, overlap_frames)

    # 3) まとまりどうしを xfade / acrossfade で重ねていく。
    video_label, audio_label = run_labels[0]
    for number, (offset, overlap) in enumerate(
        zip(offsets, overlap_frames), start=1
    ):
        kind = TRANSITIONS[clips[runs[number][0]].transition_kind or ""]
        next_video, next_audio = run_labels[number]
        merged_video, merged_audio = f"x{number}v", f"x{number}a"
        graph.filters.append(
            f"[{video_label}][{next_video}]xfade=transition={kind}"
            f":duration={_frame_seconds(overlap, spec.fps)}"
            f":offset={_frame_seconds(offset, spec.fps)}"
            f"[{merged_video}]"
        )
        graph.filters.append(
            f"[{audio_label}][{next_audio}]"
            f"acrossfade=d={_frame_seconds(overlap, spec.fps)}"
            f":c1=tri:c2=tri[{merged_audio}]"
        )
        video_label, audio_label = merged_video, merged_audio

    # 4) テロップの焼き込み（ASS）。
    if spec.subtitles_path:
        # 映像側の最後の段なので、そのまま出口のラベルへ出す。
        graph.filters.append(
            f"[{video_label}]subtitles=filename="
            f"{escape_filter_path(spec.subtitles_path)}[outv]"
        )
        video_label = "outv"

    # 5) 音声トラック（BGM / SE）を重ねる。長さは映像側に合わせて切る
    #    （duration=first。ループはしないので、足りなければそこで終わる）。
    mixed = [audio_label]
    for number, audio in enumerate(spec.audio_clips):
        if audio.duration_ms <= 0:
            continue
        source_index = graph.add_input("-i", str(audio.path))
        chain = (
            f"[{source_index}:a]"
            f"atrim=start={_seconds(audio.in_ms)}:end={_seconds(audio.out_ms)},"
            f"asetpts=PTS-STARTPTS,"
            f"aresample={AUDIO_RATE},"
            f"aformat=sample_fmts=fltp:channel_layouts={AUDIO_LAYOUT}"
        )
        if audio.gain_db:
            chain += f",volume={audio.gain_db:g}dB"
        if audio.fade_in_ms > 0:
            chain += f",afade=t=in:st=0:d={_seconds(audio.fade_in_ms)}"
        if audio.fade_out_ms > 0:
            begin = max(0, audio.duration_ms - audio.fade_out_ms)
            chain += (
                f",afade=t=out:st={_seconds(begin)}"
                f":d={_seconds(audio.fade_out_ms)}"
            )
        if audio.start_ms > 0:
            chain += f",adelay={int(audio.start_ms)}:all=1"
        label = f"m{number}"
        graph.filters.append(f"{chain}[{label}]")
        mixed.append(label)

    if len(mixed) > 1:
        joined = "".join(f"[{label}]" for label in mixed)
        target = "mixa" if spec.loudnorm else "outa"
        graph.filters.append(
            f"{joined}amix=inputs={len(mixed)}:duration=first"
            f":dropout_transition=0:normalize=0[{target}]"
        )
        audio_label = target

    # 6) ラウドネス正規化（1 パス）。音声側の最後の段。
    if spec.loudnorm:
        graph.filters.append(
            f"[{audio_label}]loudnorm=I={LOUDNORM_TARGET_LUFS:g}"
            f":TP={LOUDNORM_TRUE_PEAK_DB:g}:LRA={LOUDNORM_RANGE:g}[outa]"
        )
        audio_label = "outa"

    # 出力ラベルへ寄せる（繋ぎも音声も無いときは concat がそのまま [outv][outa]）。
    if video_label != "outv":
        graph.filters.append(f"[{video_label}]null[outv]")
    if audio_label != "outa":
        graph.filters.append(f"[{audio_label}]anull[outa]")

    # 出力そのものもフレーム数で締める（フィルタ側で 1 枚多く出ても、
    # ここで計画どおりの枚数に落ちる）。
    total_frames = spec.total_frames

    return [
        FFMPEG,
        "-hide_banner",
        "-nostdin",
        "-y",
        "-loglevel", "error",
        "-progress", "pipe:1",
        *graph.inputs,
        "-filter_complex", ";".join(graph.filters),
        "-map", "[outv]",
        "-map", "[outa]",
        "-c:v", VIDEO_CODEC[0],
        *VIDEO_CODEC[1:],
        "-pix_fmt", "yuv420p",
        "-r", fps,
        *(("-frames:v", str(total_frames)) if total_frames > 0 else ()),
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
