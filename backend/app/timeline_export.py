"""編集タブの書き出し: EDL -> ffmpeg コマンド -> 1 本の mp4。

組み立て（:func:`build_command`）と実行（:func:`run_export`）を分けてある。
組み立ては純関数なので、ffmpeg を持たない環境でもテストで固定できる。

焼けるもの:

- **映像トラック（V1）** … クリップごとに ``trim`` + ``setpts`` で切り出し、
  ``scale`` + ``pad``（または ``crop``）+ ``setsar`` + ``fps`` でタイムラインの
  規格へ正規化する。音声も ``atrim`` + ``asetpts`` + ``aresample`` + ``aformat``
  で揃える。速度を変えたクリップは ``setpts`` / ``atempo`` が入る（映像は出力の
  ``fps`` で間引かれる。:data:`AUDIO_SPEED_MAX` を超える速さの音は無音にする）。
- **静止画クリップ** … ``-loop 1 -t`` で尺ぶんの映像にしてから同じ規格へ。
- **繋ぎ（トランジション）** … **オーバーラップ方式**。繋ぎのないところは
  ``concat`` のまま繋ぎ、繋ぎのあるところで ``xfade`` / ``acrossfade`` に切り替える
  （全長はその分だけ縮む）。
- **音声トラック（A1…）** … ``atrim`` + ``afade`` + ``volume`` + ``adelay`` で
  置き場所へずらし、映像の音と ``amix`` する。長さは映像側に合わせて切る。
- **テロップ（T1）** … ASS ファイル（:mod:`app.timeline_subtitles`）を
  ``subtitles`` フィルタで焼き込む。
- **ラウドネス正規化** … **2 パス**（:func:`run_export`）。1 パス目で映像と混ぜた音
  （WAV・32bit 浮動小数）を別々に書き、混ぜた音の積分ラウドネスと真のピークを測って
  から、目標との差だけ音量を掛ける（真のピークが上限を超えるなら、4 倍に
  オーバーサンプルしたリミッターで抑えて測り直す）。2 パス目で映像はそのまま写し、
  音だけ AAC にして 1 本にまとめる。焼き上がりを測り直し、目標から
  :data:`LOUDNORM_TOLERANCE_LU` を超えてずれた・真のピークが上限を超えたら警告にする。
- **映像クリップの加工**（:class:`ClipEffects`）… 止め絵（``in_ms`` の 1 コマを
  ``tpad`` の末尾静止で尺いっぱいに伸ばす）・逆再生（``reverse`` / ``areverse``）・
  寄り（``scale`` + ``crop``）・コマ打ち（``fps`` で間引いてから出力の fps へ戻す）・
  色（``eq``）・シャープ（``unsharp``）・ノイズ（``noise``）。どれも規格へ揃えたあと・``trim=end_frame``
  の前に挟むので、書き出しの枚数は加工の有無で変わらない。
- **ダッキング**（:class:`ExportDucking`）… 音声トラックごとに「別の音声トラックが
  鳴っている間だけ下げる」。``clips`` はきっかけのトラックのクリップの区間から
  ``volume`` の式（ちょうど N dB・立ち上がりと戻りは直線）を組み、``signal`` は
  ``sidechaincompress`` で実際の音の大きさに反応させる（下げ幅は ``mix`` で N dB に
  抑える目安）。

音声を持たないソースと ``gap``（隙間）のクリップには、その尺ぶんの黒
（``color``）と無音（``anullsrc``）を ``lavfi`` から作って充てる。全クリップが
「映像 1 本 + 音声 1 本」になるので、繋ぎ方に関わらず形が揃う。

**境界はすべてフレーム番号が正本**（:func:`frame_count` / :func:`frame_plan`）。
量子化するのはクリップの**尺ではなくタイムライン上の位置**で、クリップの枚数は
隣の境界との差にする（尺をひとつずつ丸めると誤差がクリップの数だけ積み上がる）。
秒のまま切ると端数フレームが捨てられて連結後に映像が先走るので、各クリップは
1 フレームぶん余分に取ってから ``trim=end_frame=<枚数>`` でちょうどその枚数に
切る。素材の切り出し位置（``in_ms``）も同じ規則でフレーム番号にする。音も
``apad`` + ``atrim`` で同じ長さへ揃える（映像と音の尺がずれたまま ``concat`` に
渡さない）。1 フレームに満たないクリップ（数ミリ秒の隙間）は落とす。素材の実尺が「切り出し位置 + 尺」に届かないクリップは
``tpad=stop_mode=clone`` の末尾静止で埋め、どれだけ足りなかったかを
:func:`pad_warnings` が ``PAD <クリップ名> <不足秒>s`` として返す。

出力は H.264 + AAC / yuv420p / faststart で ``outputs/exports/{export_id}/final.mp4``
（``/outputs`` で配信できる）。進捗は ``-progress pipe:1`` の ``out_time_us`` を
読んで ``timeline_exports.progress`` に書き、WS（``type: "timeline_export"``）へ流す。
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import NamedTuple

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

#: リタイムの範囲（``timeline_clips.speed``）。上は「長い区間を一瞬で巻き戻す」
#: ような演出を 1 本のクリップで書けるよう 30 倍まで取る（映像は ``fps`` で間引く）
SPEED_MIN = 0.25
SPEED_MAX = 30.0

#: 音を ``atempo`` で速めて鳴らす上限。これより速いクリップの音は鳴らさない
#: （無音を充てる）。``atempo`` を何段も重ねると声も音楽も聞き取れない早回しに
#: なるうえ、尺の短いクリップでは数十ミリ秒のぶつ切りになるため。早回しの音が
#: 要るなら音声トラックに効果音を置く
AUDIO_SPEED_MAX = 4.0

#: ``atempo`` が 1 段で受けられる範囲（外れる速度は掛け算に分解する）
ATEMPO_MIN = 0.5
ATEMPO_MAX = 2.0

#: ラウドネス正規化の狙い（配信向けの一般的な値）
LOUDNORM_TARGET_LUFS = -14.0
LOUDNORM_TRUE_PEAK_DB = -1.5
LOUDNORM_RANGE = 11.0
#: 焼き上がりが目標からこれ以上ずれたら警告にする（LU）
LOUDNORM_TOLERANCE_LU = 0.5
#: 正規化で狙う精度（LU）。これより大きくずれていたら音量を掛け直す
LOUDNORM_STEP_LU = 0.1
#: リミッターの天井の余裕（dB）。AAC にすると真のピークが少し上がるので、上限より
#: これだけ下で抑える
LOUDNORM_PEAK_MARGIN_DB = 1.0
#: リミッターを掛けたときに音量を掛け直す最多の回数（測るのは混ぜた音の WAV
#: だけなので 1 回は軽い）
LOUDNORM_MAX_PASSES = 10
#: 焼き上がり（AAC）を測って、外れていたら焼き直す最多の回数（1 回目を含む）
LOUDNORM_ENCODE_ATTEMPTS = 3
#: 真のピークを測る・抑えるときのオーバーサンプルの周波数
TRUE_PEAK_RATE = 192000

#: 末尾静止（``tpad``）に持たせる余裕（秒）。素材がちょうど足りている場合でも、
#: 丸めで最後の 1 フレームが出ないことがあるので必ずこれだけは伸ばしておく
#: （伸ばしたぶんは ``trim=end_frame`` で切り落とされるので害はない）。
PAD_MARGIN_SECONDS = 0.25

#: 縦横比が変わるときの収め方（黒帯 / 中央を切り出す）
FIT_PAD = "pad"
FIT_CROP = "crop"

#: 止め絵の 1 コマを探す窓（秒）。素材の fps がタイムラインより低いと 1 コマぶんの
#: 窓にフレームが入らないことがあるので、広めに取ってから先頭の 1 枚だけ使う
FREEZE_WINDOW_SECONDS = 0.5

#: 寄り（拡大率）・コマ打ち・色・ノイズの範囲。検証（:mod:`app.timeline`）と共有する
ZOOM_MIN = 1.0
ZOOM_MAX = 4.0
STEP_MIN = 1
STEP_MAX = 4
SATURATION_RANGE = (0.0, 3.0)
CONTRAST_RANGE = (0.0, 3.0)
BRIGHTNESS_RANGE = (-1.0, 1.0)
GAMMA_RANGE = (0.1, 4.0)
NOISE_RANGE = (0.0, 1.0)
#: シャープ（``unsharp``）の強さと半径（ピクセル。行列は ``2 * 半径 + 1`` 四方）
SHARPEN_AMOUNT_RANGE = (0.0, 3.0)
SHARPEN_RADIUS_RANGE = (1, 5)

#: ノイズ 1.0 のときの ``noise=alls``（0〜100）。巻き戻し風の荒れがはっきり出る強さ
NOISE_MAX_STRENGTH = 64

#: ダッキングの種類と範囲（検証と共有する）
DUCKING_MODES = ("clips", "signal")
DUCK_AMOUNT_RANGE = (0.0, 40.0)
DUCK_ATTACK_RANGE = (1, 5000)
DUCK_RELEASE_RANGE = (1, 10000)
DUCK_THRESHOLD_RANGE = (-60.0, 0.0)
#: ``signal`` のときの圧縮比（下げ幅そのものは ``mix`` で抑える）
DUCK_RATIO = 20

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
class ClipEffects:
    """映像クリップの加工（``timeline_clips.effects`` を解決したもの）。

    既定値はどれも「何もしない」。:attr:`active` が False なら、コマンドは加工を
    足す前と 1 文字も変わらない。
    """

    #: ``in_ms`` の 1 コマをクリップの尺のあいだ止める（音は無音）
    freeze: bool = False
    #: 切り出した区間を逆に再生する（音も逆再生）
    reverse: bool = False
    #: 寄り（拡大率。1.0 = そのまま）と、切り出す中心（画面比。0.5 = 真ん中）
    zoom: float = 1.0
    zoom_x: float = 0.5
    zoom_y: float = 0.5
    #: コマ打ち（1 = そのまま、2 = 2 コマ打ち …）
    step: int = 1
    #: 色（``eq``）。既定値なら挟まない
    saturation: float = 1.0
    contrast: float = 1.0
    brightness: float = 0.0
    gamma: float = 1.0
    #: ノイズ（0〜1）
    noise: float = 0.0
    #: シャープ（アンシャープマスク）の強さ（0 = しない）と半径（ピクセル）
    sharpen: float = 0.0
    sharpen_radius: int = 2

    @property
    def active(self) -> bool:
        # 強さ 0 のシャープは半径が何であっても「何もしない」。
        plain = ClipEffects(sharpen_radius=self.sharpen_radius)
        return self != plain


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
    #: 加工（None = 加工なし）
    effects: ClipEffects | None = None

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
    #: 置かれているトラック（ダッキングの相手を探すのに使う。空 = どれでもない）
    track: str = ""

    @property
    def duration_ms(self) -> int:
        return max(0, self.out_ms - self.in_ms)


@dataclass
class ExportDucking:
    """音声トラック 1 本ぶんのダッキング（``timeline_tracks.ducking``）。

    ``target`` のトラックを、``trigger`` のトラックが鳴っている間 ``amount_db``
    だけ下げる。``clips`` はきっかけのクリップの区間から式を組み（ちょうど N dB）、
    ``signal`` は ``sidechaincompress`` で実際の音に反応させる。
    """

    target: str
    trigger: str
    amount_db: float = 8.0
    attack_ms: int = 100
    release_ms: int = 300
    mode: str = "clips"
    threshold_db: float = -30.0


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
    #: 音声トラックのダッキング
    ducking: list[ExportDucking] = field(default_factory=list)

    @property
    def frames(self) -> list[ClipFrames]:
        """クリップごとのフレーム番号（:func:`frame_plan`）。"""
        if self.fps <= 0:
            return []
        return frame_plan(self.clips, self.fps)

    @property
    def total_frames(self) -> int:
        """繋ぎの重なりを引いた**フレーム数**（焼き上がりの正本）。

        最後のクリップの終わりのフレーム番号、つまり ``round(全長 * fps)`` そのもの
        （:func:`frame_plan` は境界を量子化するので、途中のクリップの丸めは
        ここへ効いてこない）。``ffprobe -count_frames`` の実測と一致するはずで、
        合わなければ :func:`frame_warning` が警告を出す。
        """
        plan = self.frames
        return max(0, plan[-1].end) if plan else 0

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


def frame_count(position_ms: int, fps: float) -> int:
    """ミリ秒を**フレーム番号**へ量子化する（境界の正本。``round(t * fps)``）。

    秒のまま ``-ss`` / ``-t`` に渡すと端数フレームが捨てられて、連結後に映像が
    最大 2 フレーム先走る（BAN!BAN!BAN! で実測）。書き出しの長さに関わる計算は
    すべてここを通してフレーム番号にしてから行う。

    渡すのは**タイムライン上の位置**（またはソースの中の切り出し位置）であって
    尺ではない。クリップの枚数は :func:`frame_plan` が「隣との差」として出す。
    """
    if fps <= 0:
        raise TimelineExportError(f"fps が不正です: {fps}")
    return max(0, int(round(max(0, position_ms) / 1000 * fps)))


class ClipFrames(NamedTuple):
    """クリップ 1 つが焼き上がりの中で占めるフレーム番号（``[start, end)``）。"""

    #: タイムライン上の開始フレーム番号
    start: int
    #: 終わりのフレーム番号（この番号は含まない）
    end: int
    #: 前のクリップと重なる枚数（繋ぎが無ければ 0）
    overlap: int = 0

    @property
    def count(self) -> int:
        """このクリップの枚数（``end - start``）。"""
        return max(0, self.end - self.start)


def frame_plan(clips: list[ExportClip], fps: float) -> list[ClipFrames]:
    """クリップの並びを**境界**でフレーム番号へ量子化する（純関数）。

    クリップごとに ``round(尺 * fps)`` すると丸め誤差がクリップの数だけ積み上がる
    （BAN!BAN!BAN! の 51 クリップで 4 フレーム不足した）。タイムライン上の
    **位置**を量子化して隣との差を枚数にすれば、隣り合うクリップは境界を共有する
    ので誤差は累積せず、総枚数は必ず ``round(全長 * fps)`` になる。

    繋ぎ（トランジション）のあるところは、前のクリップの終わりと自分の始まりが
    重なる。その重なりも同じ境界から出す（``overlap = 前の終わり - 自分の始まり``）
    ので、``xfade`` を挟んでも全長は 1 フレームもずれない。
    """
    if fps <= 0:
        raise TimelineExportError(f"fps が不正です: {fps}")
    plan: list[ClipFrames] = []
    cursor_ms = 0  # 直前のクリップの終わり（ミリ秒）
    previous_end = 0  # 直前のクリップの終わり（フレーム番号）
    for index, clip in enumerate(clips):
        duration_ms = max(0, int(clip.duration_ms))
        overlap_ms = 0
        if index > 0 and clip.transition_kind in TRANSITIONS:
            # 重なりは前のクリップの尺までしか取れない（build_command と同じ）。
            overlap_ms = max(
                0, min(clip.overlap_ms, max(0, int(clips[index - 1].duration_ms)))
            )
        start_ms = max(0, cursor_ms - overlap_ms)
        end_ms = start_ms + duration_ms
        start = frame_count(start_ms, fps)
        plan.append(
            ClipFrames(
                start,
                frame_count(end_ms, fps),
                max(0, previous_end - start) if index > 0 else 0,
            )
        )
        cursor_ms = end_ms
        previous_end = plan[-1].end
    return plan


def _frame_seconds(frames: float, fps: float) -> str:
    """フレーム数を ffmpeg のフィルタに書ける秒表記にする（小数 6 桁）。

    フレーム境界は 3 桁では割り切れない（1/30 秒 = 0.0333…）ので、秒表記へ
    落とすところだけ桁を増やす。
    """
    return f"{frames / fps:.6f}"


def shortfall_ms(clip: ExportClip) -> int:
    """素材の実尺が「切り出し位置 + 尺」にどれだけ届いていないか（足りていれば 0）。

    測れていない（``source_duration_ms`` が None）・隙間・静止画・止め絵は 0
    （止め絵は 1 コマしか使わない。``in_ms`` が素材の外なら最後のコマを使う）。
    """
    if clip.path is None or clip.kind == "image" or clip.source_duration_ms is None:
        return 0
    if clip.effects is not None and clip.effects.freeze:
        return 0
    return max(0, int(clip.out_ms) - int(clip.source_duration_ms))


def pad_warnings(spec: ExportSpec) -> list[str]:
    """末尾静止で埋めたクリップの警告（``PAD カット名 0.42s``）を並べる。

    1 フレームぶんの不足は丸めの範囲なので黙って埋める（警告にしない）。
    止め絵の ``in_ms`` が素材の外にあるときは ``FREEZE カット名 …`` を出す
    （最後のコマで止める）。
    """
    if spec.fps <= 0:
        return []
    frame_ms = 1000 / spec.fps
    warnings: list[str] = []
    for index, clip in enumerate(spec.clips):
        if (
            clip.effects is not None
            and clip.effects.freeze
            and clip.path is not None
            and clip.kind != "image"
            and clip.source_duration_ms is not None
            and clip.in_ms >= clip.source_duration_ms
        ):
            name = clip.name or f"クリップ {index + 1}"
            warnings.append(
                f"FREEZE {name} の止める位置 {clip.in_ms / 1000:.2f}s が素材"
                f"（{clip.source_duration_ms / 1000:.2f}s）の外です。最後のコマで止めました"
            )
            continue
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
    return (
        f"フレーム数が計画と違います（計画 {expected}f / 実測 {actual}f"
        f" / 差 {actual - expected:+d}f）"
    )


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


def _even(value: float) -> int:
    """偶数へ切り上げる（yuv420p は幅・高さが偶数でないと通らない）。"""
    number = int(round(value))
    return number + (number % 2)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, float(value)))


def effect_filters(
    effects: ClipEffects | None, width: int, height: int, fps_value: float
) -> tuple[list[str], list[str]]:
    """加工を ``(末尾静止の前に挟むもの, 後に挟むもの)`` のフィルタにする（純関数）。

    寄りとコマ打ちは画の並びそのものを変えるので末尾静止（``tpad``）の**前**、
    色・シャープ・ノイズは末尾静止で伸ばしたコマにも掛けたいので**後**に置く（止め絵に
    ノイズを掛けると、止まった画の上で粒だけが動く）。どちらもフレームの枚数は
    変えない（最後の ``trim=end_frame`` がちょうどの枚数に切る）。
    """
    before: list[str] = []
    after: list[str] = []
    if effects is None:
        return before, after
    zoom = _clamp(effects.zoom, ZOOM_MIN, ZOOM_MAX)
    if zoom > 1.0 + 1e-9:
        scaled_w, scaled_h = _even(width * zoom), _even(height * zoom)
        x = int(round((scaled_w - width) * _clamp(effects.zoom_x, 0.0, 1.0)))
        y = int(round((scaled_h - height) * _clamp(effects.zoom_y, 0.0, 1.0)))
        # 拡大した幅と高さはそれぞれ偶数へ丸めるので、縦横の倍率がわずかに
        # ずれる（1080x1920 の 1.22 倍は 1318x2342 = 横 1.2204・縦 1.2198）。
        # ``scale`` は表示の比を保とうとして SAR を 1 からずらすので、切り出した
        # あとで SAR を 1 に戻す（戻さないと、隣のクリップと ``concat`` で
        # 「Input link parameters do not match」になって書き出しが落ちる）。
        before.append(
            f"scale={scaled_w}:{scaled_h},crop={width}:{height}:{x}:{y},setsar=1"
        )
    step = int(_clamp(effects.step, STEP_MIN, STEP_MAX))
    if step > 1:
        # n コマに 1 枚だけ残してから出力の fps に戻す（同じ画が n 枚続く）。
        before.append(f"fps={_fps(fps_value / step)},fps={_fps(fps_value)}")
    eq: list[str] = []
    if abs(effects.saturation - 1.0) >= 1e-9:
        eq.append(f"saturation={_number(_clamp(effects.saturation, *SATURATION_RANGE))}")
    if abs(effects.contrast - 1.0) >= 1e-9:
        eq.append(f"contrast={_number(_clamp(effects.contrast, *CONTRAST_RANGE))}")
    if abs(effects.brightness) >= 1e-9:
        eq.append(f"brightness={_number(_clamp(effects.brightness, *BRIGHTNESS_RANGE))}")
    if abs(effects.gamma - 1.0) >= 1e-9:
        eq.append(f"gamma={_number(_clamp(effects.gamma, *GAMMA_RANGE))}")
    if eq:
        after.append("eq=" + ":".join(eq))
    amount = _clamp(effects.sharpen, *SHARPEN_AMOUNT_RANGE)
    if amount > 1e-9:
        # 明るさの輪郭だけを立てる（色差は 0 = そのまま）。ノイズより前に置く
        # （あとに置くと粒まで尖る）。
        size = 2 * int(_clamp(effects.sharpen_radius, *SHARPEN_RADIUS_RANGE)) + 1
        after.append(f"unsharp={size}:{size}:{_number(round(amount, 3))}:{size}:{size}:0")
    strength = int(round(_clamp(effects.noise, *NOISE_RANGE) * NOISE_MAX_STRENGTH))
    if strength > 0:
        after.append(f"noise=alls={strength}:allf=t")
    return before, after


def _chain(*parts: str) -> str:
    """空でないフィルタだけを ``,`` で繋ぐ。"""
    return ",".join(part for part in parts if part)


def duck_intervals(
    spans: list[tuple[int, int]], attack_ms: int, release_ms: int
) -> list[tuple[int, int]]:
    """きっかけの区間（ミリ秒）を、下げたままにする**まとまり**へ寄せる（純関数）。

    戻り始め（終わり + 戻り）が次の下げ始め（始まり - 立ち上がり）に届く区間は
    1 つにまとめる（声の切れ目ごとに細かく上下させない）。まとまりどうしの
    台形は重ならないので、足し合わせても 1 を超えない。
    """
    ordered = sorted((max(0, a), max(0, b)) for a, b in spans if b > a)
    merged: list[list[int]] = []
    for start, end in ordered:
        if merged and start - attack_ms <= merged[-1][1] + release_ms:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(start, end) for start, end in merged]


def duck_expression(
    spans: list[tuple[int, int]], amount_db: float, attack_ms: int, release_ms: int
) -> str | None:
    """``volume`` の式（``eval=frame``）: きっかけの区間で ``amount_db`` 下げる。

    まとまりごとに台形（始まりの ``attack_ms`` 前から下げ始めて始まりで下げ切り、
    終わりから ``release_ms`` かけて戻る）を作って足し、``10^(-N*S/20)`` にする。
    区間が無い・下げ幅が 0 なら None（フィルタを挟まない）。
    """
    if amount_db <= 0:
        return None
    attack = max(1, int(attack_ms)) / 1000
    release = max(1, int(release_ms)) / 1000
    terms = []
    for start, end in duck_intervals(spans, attack_ms, release_ms):
        begin = start / 1000 - attack
        finish = end / 1000 + release
        terms.append(
            f"clip(min((t-{begin:.3f})/{attack:.3f}\\,({finish:.3f}-t)/{release:.3f})\\,0\\,1)"
        )
    if not terms:
        return None
    return f"pow(10\\,-{amount_db:g}*({'+'.join(terms)})/20)"


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
    frames: int,
    width: int,
    height: int,
    fps_value: float,
    fit: str,
) -> tuple[str, str]:
    """クリップ 1 つを規格へ正規化して ``(映像ラベル, 音声ラベル)`` を返す。

    枚数（``frames``）は :func:`frame_plan` が境界から出したものを受け取る
    （ここで尺を量子化し直すと丸めが累積する）。**1 フレームぶん余分に取ってから
    ``trim=end_frame`` でちょうどその枚数に切る**ので、端数フレームが捨てられて
    連結後に先走ることがない。音も同じ長さへ ``apad`` + ``atrim`` で揃える。

    加工（:class:`ClipEffects`）は規格へ揃えたあと、最後の ``trim=end_frame`` の
    前に挟む（:func:`effect_filters`）。
    """
    fps = _fps(fps_value)
    duration = _frame_seconds(frames, fps_value)
    # 余分に取ったぶんを落として、ちょうど frames 枚にする最後の段
    exact = f"trim=end_frame={frames},setpts=PTS-STARTPTS"
    video_label = f"v{position}"
    audio_label = f"a{position}"
    scale = _scale_chain(width, height, fit)
    effects = clip.effects if clip.effects is not None and clip.effects.active else None
    before, after = effect_filters(effects, width, height, fps_value)

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
            f"[{index}:v]"
            + _chain(scale, "setsar=1", f"fps={fps}", *before, *after, exact)
            + f"[{video_label}]"
        )
        graph.silence(duration, audio_label)
        return video_label, audio_label

    source_index = graph.add_input("-i", str(clip.path))
    speed = float(clip.speed or 1.0)
    # 切り出し位置もタイムラインと同じ規則でフレーム番号にする（秒のまま渡すと
    # フレームの途中から切り出されて、境界基準で数えた枚数と噛み合わない）。
    start_frame = frame_count(clip.in_ms, fps_value)

    if effects is not None and effects.freeze:
        # 止め絵: in_ms の 1 コマだけを取り出し、末尾静止で尺いっぱいに伸ばす。
        # 素材の外を指していたら最後のコマで止める（pad_warnings が知らせる）。
        if clip.source_duration_ms is not None:
            last = max(0, frame_count(clip.source_duration_ms, fps_value) - 1)
            start_frame = min(start_frame, last)
        start = _frame_seconds(start_frame, fps_value)
        window = f"{start_frame / fps_value + FREEZE_WINDOW_SECONDS:.6f}"
        hold = frames / fps_value + PAD_MARGIN_SECONDS
        graph.filters.append(
            f"[{source_index}:v]"
            + _chain(
                f"trim=start={start}:end={window}",
                "setpts=PTS-STARTPTS",
                scale,
                "setsar=1",
                f"fps={fps}",
                "trim=end_frame=1",
                *before,
                f"tpad=stop_mode=clone:stop_duration={hold:.3f}",
                *after,
                exact,
            )
            + f"[{video_label}]"
        )
        # 止めているあいだの音は鳴らさない（同じ瞬間の音を伸ばす手段が無い）。
        graph.silence(duration, audio_label)
        return video_label, audio_label

    reverse = effects is not None and effects.reverse
    start = _frame_seconds(start_frame, fps_value)
    # 出口は ``out_ms`` ではなくフレーム数から逆算する（丸めた尺をそのまま渡すと
    # 最後の 1 フレームが落ちる）。素材側は速度ぶん伸びるので掛け戻す。
    # 逆再生は余分の 1 フレームを取らない（反転すると余分が先頭に来てしまう。
    # 足りないぶんは末尾静止が埋める）。
    extra = 0 if reverse else 1
    end = _frame_seconds(start_frame + (frames + extra) * speed, fps_value)
    # 素材が足りないぶんは末尾フレームの静止（tpad）で埋める。足りていても
    # 丸めのぶんだけは伸ばしておく（余りは exact が切り落とす）。
    stop = shortfall_ms(clip) / 1000 + PAD_MARGIN_SECONDS
    # 切り出し -> タイムラインの解像度へ収める -> SAR と fps を揃える。
    # 既定（pad）は force_original_aspect_ratio=decrease + pad なので、比の違う
    # ソースは切らずに黒帯が付く（crop を選べば中央を切り出す）。
    setpts = "PTS-STARTPTS" if abs(speed - 1.0) < 1e-9 else (
        f"(PTS-STARTPTS)/{_number(speed)}"
    )
    if reverse:
        # 切り出した区間を反転してから速度を掛ける（反転はタイムスタンプを
        # 並べ直すので、反転のあとにもう一度頭を 0 に揃える）。
        retime = f"setpts=PTS-STARTPTS,reverse,setpts={setpts}"
    else:
        retime = f"setpts={setpts}"
    graph.filters.append(
        f"[{source_index}:v]"
        + _chain(
            f"trim=start={start}:end={end}",
            retime,
            scale,
            "setsar=1",
            f"fps={fps}",
            *before,
            f"tpad=stop_mode=clone:stop_duration={stop:.3f}",
            *after,
            exact,
        )
        + f"[{video_label}]"
    )

    if clip.has_audio and speed <= AUDIO_SPEED_MAX + 1e-9:
        # 音は素材の尺ぶんだけ切り出し、足りなければ無音で埋めてから映像と
        # 同じ長さに切る（映像と音の長さが違うまま concat に渡さない）。
        audio_end = _frame_seconds(start_frame + frames * speed, fps_value)
        chain = (
            f"[{source_index}:a]atrim=start={start}:end={audio_end},"
            f"asetpts=PTS-STARTPTS,"
        )
        if reverse:
            chain += "areverse,asetpts=PTS-STARTPTS,"
        chain += (
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
        # 音声を持たないソース（と、音を鳴らさない速さ :data:`AUDIO_SPEED_MAX`
        # を超えたクリップ）。繋ぎ方に関わらず全クリップに同じ本数の
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


def _sidechain_duck(
    graph: _Graph,
    ducks: list[ExportDucking],
    track_labels: dict[str, list[str]],
    mixed: list[str],
) -> list[str]:
    """``signal`` のダッキングを ``sidechaincompress`` で組み、混ぜる並びを返す。

    下げるトラックとそのきっかけのトラックをそれぞれ 1 本にまとめ、きっかけは
    ``asplit`` で「混ぜる側」と「横から聞かせる側」に分ける。圧縮比は強めにして、
    下げ幅は ``mix``（圧縮した音と元の音の混ぜ具合）で ``amount_db`` に抑える
    （きっかけが大きいほど ``amount_db`` に近づく目安）。
    """
    def bus(track: str, name: str) -> str:
        labels = track_labels[track]
        if len(labels) == 1:
            return labels[0]
        joined = "".join(f"[{label}]" for label in labels)
        graph.filters.append(
            f"{joined}amix=inputs={len(labels)}:duration=longest"
            f":dropout_transition=0:normalize=0[{name}]"
        )
        return name

    targets = {duck.target for duck in ducks}
    # きっかけ自身が下げられる側でもあると順序が決まらないので、そこは飛ばす
    # （検証で断っているが、念のため）。
    usable = [duck for duck in ducks if duck.trigger not in targets]
    replaced: dict[str, str] = {}
    triggers: dict[str, list[ExportDucking]] = {}
    for duck in usable:
        triggers.setdefault(duck.trigger, []).append(duck)
    for t_index, (trigger, group) in enumerate(triggers.items()):
        source = bus(trigger, f"dk{t_index}s")
        outs = [f"dk{t_index}m"] + [f"dk{t_index}c{n}" for n in range(len(group))]
        graph.filters.append(
            f"[{source}]asplit={len(outs)}" + "".join(f"[{out}]" for out in outs)
        )
        replaced[trigger] = outs[0]
        for n, duck in enumerate(group):
            target = bus(duck.target, f"dk{t_index}t{n}")
            threshold = 10 ** (_clamp(duck.threshold_db, *DUCK_THRESHOLD_RANGE) / 20)
            mix = 1 - 10 ** (-_clamp(duck.amount_db, *DUCK_AMOUNT_RANGE) / 20)
            ducked = f"dk{t_index}d{n}"
            graph.filters.append(
                f"[{target}][{outs[n + 1]}]sidechaincompress="
                f"threshold={max(threshold, 0.000977):.6f}:ratio={DUCK_RATIO}"
                f":attack={max(0.01, float(duck.attack_ms)):g}"
                f":release={max(0.01, float(duck.release_ms)):g}"
                f":mix={mix:.4f}[{ducked}]"
            )
            replaced[duck.target] = ducked
    result = [mixed[0]]
    seen: set[str] = set()
    for track, labels in track_labels.items():
        if track in replaced:
            if track not in seen:
                result.append(replaced[track])
                seen.add(track)
        else:
            result.extend(labels)
    return result


def build_command(
    spec: ExportSpec,
    output: str | Path,
    *,
    audio_output: str | Path | None = None,
) -> list[str]:
    """``spec`` を 1 本の mp4 に焼く ffmpeg のコマンドライン。

    純関数（ファイルも ffmpeg も触らない）なのでテストで固定できる。クリップが
    1 つも無い（または尺が全部 0）なら :class:`TimelineExportError`。

    ``audio_output`` を渡すと 2 パスの正規化の 1 パス目になる: ``output`` には映像
    だけを書き、混ぜた音は正規化せずに ``audio_output``（WAV・32bit 浮動小数。
    混ぜた直後は 0dBFS を超えうるので整数にしない）へ書く。渡さなければ
    ``spec.loudnorm`` のとき ``loudnorm`` の 1 パスで済ませる（2 パスを使わない
    呼び出しのための残し）。

    **1 フレームに満たないクリップは落とす**（自動配置や差し込みが作る数ミリ秒の
    隙間がこれになる）。``concat`` に尺 0 のセグメントを渡すと ffmpeg が
    ``Invalid argument`` で落ちるうえ、境界基準で数えているので落としても全長は
    変わらない。
    """
    if spec.width <= 0 or spec.height <= 0 or spec.fps <= 0:
        raise TimelineExportError("タイムラインの規格（幅・高さ・fps）が不正です")
    plan = frame_plan(spec.clips, spec.fps)
    kept = [(clip, item) for clip, item in zip(spec.clips, plan) if item.count > 0]
    if not kept:
        raise TimelineExportError("書き出せるクリップがありません")
    clips = [clip for clip, _ in kept]
    counts = [item for _, item in kept]

    width, height = spec.width, spec.height
    fps = _fps(spec.fps)
    fit = spec.fit if spec.fit in (FIT_PAD, FIT_CROP) else FIT_PAD
    graph = _Graph()

    # 1) クリップごとに規格へ正規化する（尺はフレーム数へ量子化される）。
    labels = [
        _add_clip(
            graph, clip, position, counts[position].count,
            width, height, spec.fps, fit,
        )
        for position, clip in enumerate(clips)
    ]

    # 2) 繋ぎ（オーバーラップ）で区切って「まとまり」を作る。繋ぎのないところは
    #    これまでどおり concat のままなので、繋ぎを使わないタイムラインの
    #    コマンドはフェーズ 1 と 1 文字も変わらない。
    runs: list[list[int]] = [[0]]
    # 重なりもフレーム数（:func:`frame_plan` が境界から出したもの）で持つ。
    overlap_frames: list[int] = []
    for position, clip in enumerate(clips[1:], start=1):
        overlap = counts[position].overlap
        if overlap > 0 and clip.transition_kind in TRANSITIONS:
            runs.append([position])
            overlap_frames.append(overlap)
        else:
            runs[-1].append(position)

    # 繋ぎも音声トラックもテロップも無いときは、``concat`` の出口をそのまま
    # ``[outv]`` / ``[outa]`` にする（余計な通過フィルタを挟まない）。
    single = len(runs) == 1
    video_ends_here = single and not spec.subtitles_path
    one_pass_loudnorm = spec.loudnorm and audio_output is None
    audio_ends_here = single and not spec.audio_clips and not one_pass_loudnorm

    run_labels = [
        _join_run(
            graph,
            [labels[position] for position in run],
            "outv" if video_ends_here else f"r{number}v",
            "outa" if audio_ends_here else f"r{number}a",
        )
        for number, run in enumerate(runs)
    ]
    # offset もフレーム数で数える（クリップ側の量子化と揃えないと、繋ぎのある
    # タイムラインだけ全長が 1 フレームずれる）。
    run_durations = [
        sum(counts[position].count for position in run) for run in runs
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
    #    ダッキング（``clips``）はクリップごとに volume の式を足すだけ、
    #    ``signal`` はトラックをまとめてから sidechaincompress を通す。
    ducks = {
        duck.target: duck
        for duck in spec.ducking
        if duck.target and duck.trigger and duck.target != duck.trigger
        and duck.amount_db > 0
    }
    spans_by_track: dict[str, list[tuple[int, int]]] = {}
    for audio in spec.audio_clips:
        if audio.duration_ms > 0 and audio.track:
            spans_by_track.setdefault(audio.track, []).append(
                (audio.start_ms, audio.start_ms + audio.duration_ms)
            )
    track_labels: dict[str, list[str]] = {}
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
        duck = ducks.get(audio.track)
        if duck is not None and duck.mode != "signal":
            # adelay のあとなので t はタイムライン上の秒になる。
            expression = duck_expression(
                spans_by_track.get(duck.trigger, []),
                duck.amount_db,
                duck.attack_ms,
                duck.release_ms,
            )
            if expression:
                chain += f",volume={expression}:eval=frame"
        label = f"m{number}"
        graph.filters.append(f"{chain}[{label}]")
        mixed.append(label)
        track_labels.setdefault(audio.track, []).append(label)

    signal = [
        duck for duck in ducks.values()
        if duck.mode == "signal"
        and track_labels.get(duck.target)
        and track_labels.get(duck.trigger)
    ]
    if signal:
        mixed = _sidechain_duck(graph, signal, track_labels, mixed)

    if len(mixed) > 1:
        joined = "".join(f"[{label}]" for label in mixed)
        target = "mixa" if one_pass_loudnorm else "outa"
        graph.filters.append(
            f"{joined}amix=inputs={len(mixed)}:duration=first"
            f":dropout_transition=0:normalize=0[{target}]"
        )
        audio_label = target

    # 6) ラウドネス正規化（1 パス）。音声側の最後の段。2 パス（audio_output）の
    #    ときはここで掛けず、混ぜた音をそのまま書き出して :func:`run_export` が測る。
    if one_pass_loudnorm:
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
    video_args = [
        "-c:v", VIDEO_CODEC[0],
        *VIDEO_CODEC[1:],
        "-pix_fmt", "yuv420p",
        "-r", fps,
        *(("-frames:v", str(total_frames)) if total_frames > 0 else ()),
    ]
    head = [
        FFMPEG,
        "-hide_banner",
        "-nostdin",
        "-y",
        "-loglevel", "error",
        "-progress", "pipe:1",
        *graph.inputs,
        "-filter_complex", ";".join(graph.filters),
    ]
    if audio_output is not None:
        # 2 パスの 1 パス目: 映像だけの mp4 と、正規化する前の混ぜた音（WAV）。
        # 音は映像の尺ちょうどに切る（混ぜた段は duration=first なので揃っている）。
        duration = _frame_seconds(total_frames, spec.fps) if total_frames > 0 else None
        return [
            *head,
            "-map", "[outv]",
            *video_args,
            "-an",
            "-movflags", "+faststart",
            str(output),
            "-map", "[outa]",
            *(("-t", duration) if duration else ()),
            "-c:a", "pcm_f32le",
            "-ar", str(AUDIO_RATE),
            "-f", "wav",
            str(audio_output),
        ]
    return [
        *head,
        "-map", "[outv]",
        "-map", "[outa]",
        *video_args,
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


# --------------------------------------------------------------------------
# ラウドネス正規化（2 パス）
# --------------------------------------------------------------------------

def _db_to_linear(value: float) -> float:
    return 10 ** (value / 20)


def loudness_filters(
    gain_db: float,
    limit: bool,
    ceiling_db: float = LOUDNORM_TRUE_PEAK_DB - LOUDNORM_PEAK_MARGIN_DB,
) -> list[str]:
    """混ぜた音に掛ける音量（とリミッター）のフィルタ（純関数）。

    リミッターは真のピーク（サンプルの間の山）を抑えるため、:data:`TRUE_PEAK_RATE`
    へオーバーサンプルしてから ``alimiter`` に通し、元の周波数へ戻す。天井は
    真のピークの上限から :data:`LOUDNORM_PEAK_MARGIN_DB` 下げたところ。自動の
    音量合わせ（``level``）は切り、先読みの遅れは ``latency`` で埋め戻す（音が
    映像からずれない）。天井は ``ceiling_db``（dBTP）。
    """
    filters: list[str] = []
    if abs(gain_db) >= 0.005:
        filters.append(f"volume={gain_db:.3f}dB")
    if limit:
        ceiling = _db_to_linear(max(-24.0, min(0.0, ceiling_db)))
        filters += [
            f"aresample={TRUE_PEAK_RATE}",
            f"alimiter=limit={ceiling:.6f}:attack=1:release=50:level=0:latency=1",
            f"aresample={AUDIO_RATE}",
        ]
    return filters


def parse_loudness(stderr: str) -> tuple[float | None, float | None]:
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

    def number(key: str) -> float | None:
        try:
            value = float(data.get(key))
        except (TypeError, ValueError):
            return None
        return value if math.isfinite(value) else None

    return number("input_i"), number("input_tp")


async def _run_ffmpeg(command: list[str]) -> tuple[int, str]:
    """ffmpeg を走らせて ``(終了コード, stderr)``。起動できなければ例外にする。"""
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise TimelineExportError(
            f"書き出しには ffmpeg が要りますが見つかりませんでした（{exc}）"
        ) from exc
    except (OSError, ValueError) as exc:
        raise TimelineExportError(f"ffmpeg を起動できませんでした: {exc}") from exc
    _, stderr = await process.communicate()
    return process.returncode or 0, (stderr or b"").decode("utf-8", "replace")


async def measure_loudness(
    path: str | Path, filters: list[str] | None = None
) -> tuple[float | None, float | None]:
    """音（と、``filters`` を掛けたあとの音）の積分ラウドネスと真のピーク。

    ``loudnorm`` の測るだけの使い方（``print_format=json``。真のピークは
    192kHz にオーバーサンプルして測る）。測れなければ ``(None, None)``。
    """
    chain = ",".join([*(filters or []), "loudnorm=print_format=json"])
    code, stderr = await _run_ffmpeg([
        FFMPEG, "-hide_banner", "-nostdin", "-nostats", "-i", str(path),
        "-map", "0:a:0", "-af", chain, "-f", "null", "-",
    ])
    if code != 0:
        log.warning("ラウドネスを測れませんでした（%s）: %s", path, stderr.strip()[-300:])
        return None, None
    return parse_loudness(stderr)


async def plan_loudness(
    mix: Path,
    target: float = LOUDNORM_TARGET_LUFS,
    ceiling: float = LOUDNORM_TRUE_PEAK_DB - LOUDNORM_PEAK_MARGIN_DB,
) -> list[str]:
    """混ぜた音を ``target``（LUFS）へ、真のピークを ``ceiling``（dBTP）以下に合わせる
    フィルタを決める（測りながら）。

    1. 混ぜた音を測り、目標との差（``gain``）を出す
    2. 差だけ上げても真のピークが天井（上限 - 余裕）に収まるなら、音量だけ
       （直線。目標にちょうど合う）
    3. 収まらなければリミッターを足し、掛けたあとの音を測り直して、残った差を
       ``gain`` に足す（リミッターが山を削るとラウドネスが下がるため）。
       :data:`LOUDNORM_STEP_LU` 以内に入るか :data:`LOUDNORM_MAX_PASSES` 回まで。
       積分ラウドネスはゲート（ブロックの -10 LU 下を捨てる）のせいで ``gain`` に
       対して単調でない（山を削ると静かなブロックがゲートを跨いで数 LU 跳ぶ）ので、
       目標を挟む 2 点が見つかったらその間を二分する

    無音・測れない音は何も掛けない（空の並び）。
    """
    loudness, peak = await measure_loudness(mix)
    if loudness is None:
        return []
    gain = target - loudness
    if peak is not None and peak + gain <= ceiling:
        return loudness_filters(gain, limit=False)
    filters = loudness_filters(gain, limit=True, ceiling_db=ceiling)
    best: tuple[float, list[str]] | None = None
    low: float | None = None  # 目標に届かなかった gain の最大
    high: float | None = None  # 目標を超えた gain の最小
    for _ in range(LOUDNORM_MAX_PASSES):
        reached, _peak = await measure_loudness(mix, filters)
        if reached is None:
            break
        miss = target - reached
        if best is None or abs(miss) < best[0]:
            best = (abs(miss), filters)
        if abs(miss) <= LOUDNORM_STEP_LU:
            break
        if miss > 0:
            low = gain if low is None else max(low, gain)
        else:
            high = gain if high is None else min(high, gain)
        step = gain + miss
        if low is not None and high is not None and not (low < step < high):
            step = (low + high) / 2
        gain = step
        filters = loudness_filters(gain, limit=True, ceiling_db=ceiling)
    return best[1] if best is not None else filters


def finish_command(
    video: str | Path, mix: str | Path, output: str | Path, filters: list[str]
) -> list[str]:
    """2 パス目: 映像はそのまま写し、混ぜた音に ``filters`` を掛けて AAC にまとめる。"""
    chain = [
        *filters,
        f"aformat=sample_fmts=fltp:sample_rates={AUDIO_RATE}"
        f":channel_layouts={AUDIO_LAYOUT}",
    ]
    return [
        FFMPEG, "-hide_banner", "-nostdin", "-y", "-loglevel", "error",
        "-i", str(video),
        "-i", str(mix),
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-c:v", "copy",
        "-af", ",".join(chain),
        "-c:a", "aac",
        "-b:a", AUDIO_BITRATE,
        "-movflags", "+faststart",
        str(output),
    ]


def loudness_warning(
    loudness: float | None, peak: float | None
) -> str | None:
    """焼き上がりのラウドネスが目標から外れていたときの警告（収まっていれば None）。"""
    if loudness is None:
        return None
    problems: list[str] = []
    if abs(loudness - LOUDNORM_TARGET_LUFS) > LOUDNORM_TOLERANCE_LU:
        problems.append(
            f"ラウドネス {loudness:.1f} LUFS（目標 {LOUDNORM_TARGET_LUFS:g}"
            f" ±{LOUDNORM_TOLERANCE_LU:g}）"
        )
    if peak is not None and peak > LOUDNORM_TRUE_PEAK_DB + 0.05:
        problems.append(
            f"真のピーク {peak:.1f} dBTP（上限 {LOUDNORM_TRUE_PEAK_DB:g}）"
        )
    if not problems:
        return None
    return "LOUDNESS " + "・".join(problems)


async def run_export(
    spec: ExportSpec,
    output: Path,
    *,
    on_progress: Callable[[float], Awaitable[None]] | None = None,
    notes: list[str] | None = None,
) -> Path:
    """``spec`` を ``output`` に焼く（進捗があれば ``on_progress(0.0〜1.0)``）。

    ``spec.loudnorm`` なら 2 パスで正規化する（:func:`plan_loudness`）: 映像だけの
    mp4 と混ぜた音（WAV）を書き、音を測って音量を決め、映像を写しながら音だけ
    AAC にしてまとめる。焼き上がりを測り直し、目標から外れていれば ``notes`` に
    警告（``LOUDNESS …``）を足す。途中のファイルは消す。

    失敗したら :class:`TimelineExportError`（メッセージに stderr の末尾）。
    """
    output.parent.mkdir(parents=True, exist_ok=True)
    if not spec.loudnorm:
        await _run_main(build_command(spec, output), spec, output, on_progress, 0.99)
        return output

    video = output.with_name(f"{output.stem}.video.mp4")
    mix = output.with_name(f"{output.stem}.mix.wav")
    attempts: list[Path] = []
    try:
        await _run_main(
            build_command(spec, video, audio_output=mix), spec, video, on_progress, 0.9
        )
        if not mix.is_file() or mix.stat().st_size == 0:
            raise TimelineExportError("混ぜた音を書き出せませんでした")
        target = LOUDNORM_TARGET_LUFS
        ceiling = LOUDNORM_TRUE_PEAK_DB - LOUDNORM_PEAK_MARGIN_DB
        best: tuple[float, Path, float | None, float | None] | None = None
        for attempt in range(LOUDNORM_ENCODE_ATTEMPTS):
            trial = output.with_name(f"{output.stem}.try{attempt}{output.suffix}")
            attempts.append(trial)
            filters = await plan_loudness(mix, target, ceiling)
            code, stderr = await _run_ffmpeg(finish_command(video, mix, trial, filters))
            if code != 0:
                raise TimelineExportError(
                    f"ffmpeg が失敗しました（音の正規化）: {stderr.strip()[-800:]}"
                )
            if not trial.is_file() or trial.stat().st_size == 0:
                raise TimelineExportError("ffmpeg は終了しましたが出力が空でした")
            loudness, peak = await measure_loudness(trial)
            if loudness is None:
                best = (0.0, trial, loudness, peak)
                break
            miss = LOUDNORM_TARGET_LUFS - loudness
            over = (peak if peak is not None else -99.0) - LOUDNORM_TRUE_PEAK_DB
            score = max(0.0, abs(miss) - LOUDNORM_STEP_LU) + max(0.0, over)
            if best is None or score < best[0]:
                best = (score, trial, loudness, peak)
            if score <= 0:
                break
            # AAC にすると高い音が削れてラウドネスが少し下がり（ノイズの多い音で
            # 0.3 LU ほど）、リミッターで潰した山がサンプルの間でまた立つ（強く
            # 潰した音で 1 dB ほど）。ずれたぶんだけ狙いと天井をずらして焼き直し、
            # いちばん目標に近いものを残す。
            target += miss
            if over > 0:
                ceiling -= over + 0.2
        assert best is not None
        _score, chosen, loudness, peak = best
        os.replace(chosen, output)
    finally:
        for leftover in (video, mix, *attempts):
            try:
                leftover.unlink(missing_ok=True)
            except OSError:
                log.debug("途中のファイルを消せませんでした: %s", leftover, exc_info=True)

    log.info(
        "書き出しのラウドネス %s: %s LUFS / %s dBTP", output, loudness, peak
    )
    warning = loudness_warning(loudness, peak)
    if warning and notes is not None:
        notes.append(warning)
    if on_progress is not None:
        try:
            await on_progress(0.99)
        except Exception:  # noqa: BLE001 - 進捗の通知で書き出しを壊さない
            log.debug("書き出しの進捗通知に失敗しました", exc_info=True)
    return output


async def _run_main(
    command: list[str],
    spec: ExportSpec,
    output: Path,
    on_progress: Callable[[float], Awaitable[None]] | None,
    ceiling: float,
) -> None:
    """組み立てた書き出しのコマンドを走らせる（進捗は ``0〜ceiling`` に収める）。"""
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
                await on_progress(min(ceiling, seconds / total * ceiling))
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
