"""編集タブのテロップ: 字幕クリップ -> ASS ファイル（焼き込み用）。

書き出しでは ``subtitles`` フィルタで映像へ焼き込むので、タイムライン上の
``source_kind='text'`` のクリップを 1 枚の ASS に書き出す必要がある。組み立ては
**純関数**（:func:`build_ass`）にしてあり、ffmpeg も字幕ライブラリも要らない。

見た目の指定は最小限（SPEC §7.3）:

- **位置** … ``bottom``（既定）/ ``top``
- **大きさ** … ``S`` / ``M``（既定）/ ``L``。タイムラインの高さに対する比で決める
  ので、720p でも 1080p でも同じ大きさに見える
- **色** … ``white``（既定）/ ``yellow``。どちらも黒い縁取りつき

スタイルは 1 つ（``Default``）だけ置き、位置・大きさ・色は 1 行ごとの上書きタグ
（``{\\an2}{\\fs48}{\\c&H00FFFF&}``）で付ける。組み合わせの数だけスタイルを作る
より、行と見た目が 1 対 1 で並ぶぶん読みやすい。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: 位置 -> ASS の配置（``\an``）。2 = 下中央 / 8 = 上中央
POSITIONS: dict[str, int] = {"bottom": 2, "top": 8}

#: 大きさ -> 画面の高さに対する文字サイズの比
SIZES: dict[str, float] = {"S": 0.045, "M": 0.060, "L": 0.080}

#: 色 -> ASS の色表記（``&HAABBGGRR``。ASS は BGR 順）
COLORS: dict[str, str] = {"white": "&H00FFFFFF", "yellow": "&H0000FFFF"}

#: 既定の見た目（指定が無い / 知らない値だったときに落ちる先）
DEFAULT_POSITION = "bottom"
DEFAULT_SIZE = "M"
DEFAULT_COLOR = "white"

#: 既定のフォント。日本語が出るものを順に並べる（ASS は最初の 1 つだけ見るので、
#: 実際に効くのは先頭。無ければ libass が代替を探す）
DEFAULT_FONT = "Noto Sans CJK JP"

#: 縁取りの太さ（文字サイズに対する比）。白でも黄でも黒で縁取る
OUTLINE_RATIO = 0.055

#: 下（上）の余白（画面の高さに対する比）
MARGIN_RATIO = 0.055


@dataclass
class SubtitleEvent:
    """焼き込む 1 行（タイムライン上の区間と本文と見た目）。"""

    start_ms: int
    end_ms: int
    text: str
    position: str = DEFAULT_POSITION
    size: str = DEFAULT_SIZE
    color: str = DEFAULT_COLOR

    @property
    def duration_ms(self) -> int:
        return max(0, self.end_ms - self.start_ms)


@dataclass
class SubtitleStyle:
    """テロップ 1 枚の見た目（``text_payload.style`` の中身）。"""

    position: str = DEFAULT_POSITION
    size: str = DEFAULT_SIZE
    color: str = DEFAULT_COLOR


def normalize_style(payload: Any) -> SubtitleStyle:
    """``text_payload.style`` を既知の値だけの :class:`SubtitleStyle` にする。

    知らない値・欠けている値は既定へ落とす（画面から来た JSON をそのまま
    信用しないため。書き出しの直前で落ちるより既定で焼けたほうがよい）。
    """
    data = payload if isinstance(payload, dict) else {}
    position = str(data.get("position") or "")
    size = str(data.get("size") or "").upper()
    color = str(data.get("color") or "")
    return SubtitleStyle(
        position=position if position in POSITIONS else DEFAULT_POSITION,
        size=size if size in SIZES else DEFAULT_SIZE,
        color=color if color in COLORS else DEFAULT_COLOR,
    )


def event_from_clip(
    start_ms: int, duration_ms: int, payload: Any
) -> SubtitleEvent | None:
    """text クリップ 1 つを :class:`SubtitleEvent` にする（本文が空なら None）。"""
    data = payload if isinstance(payload, dict) else {}
    text = str(data.get("text") or "").strip()
    if not text or duration_ms <= 0:
        return None
    style = normalize_style(data.get("style"))
    return SubtitleEvent(
        start_ms=max(0, int(start_ms)),
        end_ms=max(0, int(start_ms)) + int(duration_ms),
        text=text,
        position=style.position,
        size=style.size,
        color=style.color,
    )


def format_time(ms: int) -> str:
    """ミリ秒を ASS のタイムコード（``H:MM:SS.cc``）にする。

    ASS の刻みは 1/100 秒なので、**切り捨てず四捨五入**する（1 フレームぶんの
    ずれより、行の頭が前へ出ないほうを優先する場面が無いため）。
    """
    total = max(0, int(round(ms / 10)))  # センチ秒
    centis = total % 100
    seconds = (total // 100) % 60
    minutes = (total // 6000) % 60
    hours = total // 360000
    return f"{hours}:{minutes:02d}:{seconds:02d}.{centis:02d}"


def escape_text(text: str) -> str:
    """本文を ASS の 1 行に収める（改行は ``\\N``、制御文字は落とす）。

    ``{`` は上書きタグの始まりなので、本文に出てきたら無害な全角に寄せる
    （エスケープ記法が無いため。台詞に波括弧が出ることは実質無いが、出ても
    字幕が丸ごと消えるより文字が変わるほうがまし）。
    """
    cleaned = text.replace("\r\n", "\n").replace("\r", "\n")
    cleaned = cleaned.replace("{", "｛").replace("}", "｝")
    cleaned = "".join(
        char for char in cleaned if char == "\n" or char >= " " or char == "\t"
    )
    lines = [line.strip() for line in cleaned.split("\n")]
    return "\\N".join(line for line in lines if line)


def _override(event: SubtitleEvent, height: int) -> str:
    """1 行ぶんの上書きタグ（配置・文字サイズ・色・余白）。"""
    alignment = POSITIONS.get(event.position, POSITIONS[DEFAULT_POSITION])
    size = max(8, int(round(height * SIZES.get(event.size, SIZES[DEFAULT_SIZE]))))
    color = COLORS.get(event.color, COLORS[DEFAULT_COLOR])
    outline = max(1, int(round(size * OUTLINE_RATIO)))
    return f"{{\\an{alignment}\\fs{size}\\c{color}\\bord{outline}}}"


def build_ass(
    events: list[SubtitleEvent], width: int, height: int, *, font: str = DEFAULT_FONT
) -> str:
    """焼き込み用の ASS ファイルの中身（純関数）。

    ``PlayResX`` / ``PlayResY`` を書き出しの解像度に合わせるので、文字サイズは
    そのまま出力ピクセルの意味になる。本文が空の行と尺 0 の行は落とす。
    """
    safe_width = max(1, int(width))
    safe_height = max(1, int(height))
    base_size = int(round(safe_height * SIZES[DEFAULT_SIZE]))
    margin = int(round(safe_height * MARGIN_RATIO))

    header = [
        "[Script Info]",
        "; app.timeline_subtitles が組み立てたもの（手で編集しない）",
        "ScriptType: v4.00+",
        "WrapStyle: 2",
        "ScaledBorderAndShadow: yes",
        "YCbCr Matrix: TV.709",
        f"PlayResX: {safe_width}",
        f"PlayResY: {safe_height}",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour,"
        " OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut,"
        " ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow,"
        " Alignment, MarginL, MarginR, MarginV, Encoding",
        # 縁取りは黒（OutlineColour）、影は無し。既定は下中央。
        f"Style: Default,{font},{base_size},{COLORS[DEFAULT_COLOR]},"
        f"&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,"
        f"{max(1, int(round(base_size * OUTLINE_RATIO)))},0,"
        f"{POSITIONS[DEFAULT_POSITION]},{margin},{margin},{margin},1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV,"
        " Effect, Text",
    ]

    lines: list[str] = []
    for event in events:
        body = escape_text(event.text)
        if not body or event.duration_ms <= 0:
            continue
        lines.append(
            f"Dialogue: 0,{format_time(event.start_ms)},{format_time(event.end_ms)},"
            f"Default,,0,0,0,,{_override(event, safe_height)}{body}"
        )
    return "\n".join([*header, *lines]) + "\n"


# --------------------------------------------------------------------------
# 台詞 -> テロップの割り付け
# --------------------------------------------------------------------------

#: 台詞を分ける区切り（改行のほかに、日本語の句点と閉じ括弧で切る）
_BREAKS = ("\n", "。", "！", "？", "!", "?")

#: 1 枚のテロップに割り当てる最小の尺（ミリ秒）。これを割るなら分けない
MIN_SUBTITLE_MS = 700


def split_dialogue(dialogue: str) -> list[str]:
    """カットの台詞を、テロップ 1 枚ぶんずつに割る。

    改行が入っていればそれが正（脚本が意図した区切り）。無ければ句点・感嘆符・
    疑問符の**後ろ**で切る（区切り文字は前の行に残す）。囲みの鉤括弧は落とす。
    """
    text = (dialogue or "").strip()
    if not text:
        return []
    if "\n" in text:
        pieces = [line.strip() for line in text.splitlines()]
    else:
        pieces = []
        current = ""
        for char in text:
            current += char
            if char in _BREAKS:
                pieces.append(current.strip())
                current = ""
        if current.strip():
            pieces.append(current.strip())
    return [_strip_brackets(piece) for piece in pieces if _strip_brackets(piece)]


def _strip_brackets(text: str) -> str:
    """台詞を囲む鉤括弧・引用符を外す（テロップには出さない）。"""
    stripped = text.strip()
    pairs = (("「", "」"), ("『", "』"), ('"', '"'), ("“", "”"), ("'", "'"))
    for opening, closing in pairs:
        if stripped.startswith(opening) and stripped.endswith(closing):
            return stripped[len(opening) : -len(closing)].strip()
    return stripped


@dataclass
class PlacedSubtitle:
    """クリップ区間に割り付いたテロップ 1 枚。"""

    start_ms: int
    duration_ms: int
    text: str
    style: dict[str, str] = field(default_factory=dict)


def place_dialogue(
    start_ms: int, duration_ms: int, dialogue: str
) -> list[PlacedSubtitle]:
    """1 カットの台詞を、そのクリップ区間へ**等分**して並べる（純関数）。

    台詞が複数行あっても、どの行が何秒かは分からない（音声の解析はしない）。
    等分は乱暴だが、**ずれ方が読める**ぶん直しやすい——画面で 1 枚ずつ掴んで
    動かせるので、目安として置くところまでをここが受け持つ。

    区間が短くて等分すると 1 枚あたり :data:`MIN_SUBTITLE_MS` を割るときは、
    割れるところまでで打ち切って 1 枚にまとめる（一瞬で消える字幕を作らない）。
    """
    pieces = split_dialogue(dialogue)
    if not pieces or duration_ms <= 0:
        return []
    count = min(len(pieces), max(1, duration_ms // MIN_SUBTITLE_MS))
    if count < len(pieces):
        # 入りきらない後ろを最後の 1 枚へ畳む（切り捨てない）。
        pieces = [*pieces[: count - 1], "".join(pieces[count - 1 :])]

    placed: list[PlacedSubtitle] = []
    for index in range(count):
        # 端数は最後の 1 枚が吸う（合計がクリップの尺とぴったり合う）。
        begin = start_ms + duration_ms * index // count
        end = start_ms + duration_ms * (index + 1) // count
        placed.append(
            PlacedSubtitle(start_ms=begin, duration_ms=end - begin, text=pieces[index])
        )
    return placed
