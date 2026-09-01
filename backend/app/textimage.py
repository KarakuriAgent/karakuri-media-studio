"""フォント画像の生成（SPEC §7.2）: 文字を PIL で組んで PNG にする。

使い道は 2 つある:

1. **そのままスプライトとして使う**（決め台詞・ロゴ文字・カードの文字）。背景を
   透明にして出せるので、Remotion の ``sprite`` / ``imageSlam`` の ``src`` に
   そのまま書ける
2. **画像生成の字形参照**。日本語を描かせると誤字になるモデルでも、フォントで
   組んだ画像を参照（``<Picture 2>`` など）に添えると字形が直る——BAN!BAN!BAN! で
   確立した運用

書体はインストール済みのものだけを使う（Web フォントは取りに行かない。オフライン
でも同じ結果になるようにするため）。一覧は fontconfig（``fc-list``）から拾い、
無ければ :data:`FONT_DIRS` を走査する。TrueType コレクション（``.ttc``）は面
（``index``）ごとに 1 件として並べ、面が分からなければ 0 を使う。
"""

from __future__ import annotations

import logging
import math
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageColor, ImageDraw, ImageFont

log = logging.getLogger(__name__)

#: 走査するフォントの置き場（``fc-list`` が無い環境の逃げ道）
FONT_DIRS: tuple[Path, ...] = (
    Path("/usr/share/fonts"),
    Path("/usr/local/share/fonts"),
    Path.home() / ".local/share/fonts",
    Path.home() / ".fonts",
)

#: PIL が開けるフォントの拡張子
FONT_EXT: frozenset[str] = frozenset({".ttf", ".otf", ".ttc", ".otc"})

#: 既定の書体（前から順に探し、最初に見つかったものを使う）。
#: Remotion 側（``remotion/src/fonts.ts``）と揃えて CJK を先に置く
DEFAULT_FAMILIES: tuple[str, ...] = (
    "Noto Sans CJK JP Bold",
    "Noto Sans CJK JP",
    "Noto Sans JP Bold",
    "Noto Sans JP",
    "IPAexGothic",
    "DejaVu Sans Bold",
    "DejaVu Sans",
)

#: 文字の大きさ（px）の既定と上限
DEFAULT_SIZE = 160
MAX_SIZE = 1024

#: 生成する画像の 1 辺の上限（これを超える指定は 400）
MAX_EDGE = 4096

#: 文字の周りに空ける余白（px）の既定
DEFAULT_PADDING = 24

#: 行送り（フォントサイズに対する比）
LINE_SPACING = 0.25

#: 行揃え
ALIGNS: tuple[str, ...] = ("left", "center", "right")

#: 背景を透明にする指定
TRANSPARENT = "transparent"

#: ``fc-list`` の呼び出し名（テストが差し替える継ぎ目）
FC_LIST = "fc-list"

#: ``fc-list`` に許す秒数
FC_TIMEOUT = 10.0


class TextImageError(Exception):
    """文字を組めない（空文字・未知の書体・大きすぎる指定）。呼び出し側が 400 にする。"""


@dataclass(frozen=True)
class FontFace:
    """インストール済みの書体 1 面。"""

    #: 表示名（``"Noto Sans CJK JP Bold"``）。API の ``font`` に書く値
    name: str
    family: str
    style: str
    path: str
    #: TrueType コレクションの面番号（単体フォントは 0）
    index: int = 0


def _first(value: str) -> str:
    """``fc-list`` が返すカンマ区切りの候補から先頭だけ取る。"""
    return value.split(",")[0].strip()


def face_name(family: str, style: str) -> str:
    """一覧と ``font`` 指定に使う表示名（Regular は書体名だけ）。"""
    family = family.strip()
    style = style.strip()
    if not style or style.lower() == "regular":
        return family
    return f"{family} {style}"


def _from_fc_list() -> list[FontFace]:
    """``fc-list`` で書体を集める（使えなければ空リスト）。"""
    try:
        done = subprocess.run(
            [FC_LIST, "--format", "%{file}\t%{index}\t%{family}\t%{style}\n"],
            capture_output=True,
            text=True,
            timeout=FC_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.info("fc-list を実行できませんでした（フォントは走査で集めます）: %s", exc)
        return []
    if done.returncode != 0:
        return []
    faces: list[FontFace] = []
    for line in done.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 4:
            continue
        path, raw_index, family, style = parts[0], parts[1], parts[2], parts[3]
        if Path(path).suffix.lower() not in FONT_EXT:
            continue
        family, style = _first(family), _first(style)
        if not family:
            continue
        try:
            index = int(raw_index.strip() or 0)
        except ValueError:
            index = 0
        faces.append(
            FontFace(face_name(family, style), family, style, path, index)
        )
    return faces


def _from_scan() -> list[FontFace]:
    """フォントの置き場を走査して集める（``fc-list`` が無い環境の逃げ道）。

    書体名はファイル名から作るしかないので、コレクションは面 0 だけを見る。
    """
    faces: list[FontFace] = []
    for directory in FONT_DIRS:
        if not directory.is_dir():
            continue
        for path in sorted(directory.rglob("*")):
            if path.suffix.lower() not in FONT_EXT or not path.is_file():
                continue
            family = path.stem.replace("_", " ").replace("-", " ").strip()
            faces.append(FontFace(family, family, "", str(path), 0))
    return faces


def _dedupe(faces: list[FontFace]) -> list[FontFace]:
    """同じ表示名は先勝ちで 1 件にし、名前順に並べる。"""
    found: dict[str, FontFace] = {}
    for face in faces:
        found.setdefault(face.name.casefold(), face)
    return sorted(found.values(), key=lambda face: face.name.casefold())


@lru_cache(maxsize=1)
def list_fonts() -> tuple[FontFace, ...]:
    """使える書体の一覧（``fc-list`` 優先、無ければ走査）。プロセス内でキャッシュ。"""
    faces = _from_fc_list() or _from_scan()
    return tuple(_dedupe(faces))


def clear_cache() -> None:
    """書体一覧のキャッシュを捨てる（フォントを入れ直したときとテスト用）。"""
    list_fonts.cache_clear()


def default_font() -> FontFace | None:
    """既定の書体（:data:`DEFAULT_FAMILIES` の先頭から探す）。

    どれも無ければ一覧の先頭、一覧そのものが空なら None（PIL の内蔵フォントに
    倒す）。
    """
    faces = list_fonts()
    if not faces:
        return None
    by_name = {face.name.casefold(): face for face in faces}
    by_family = {face.family.casefold(): face for face in faces}
    for wanted in DEFAULT_FAMILIES:
        key = wanted.casefold()
        if key in by_name:
            return by_name[key]
        if key in by_family:
            return by_family[key]
    return faces[0]


def find_font(name: object) -> FontFace | None:
    """``font`` の指定から書体を選ぶ（空なら :func:`default_font`）。

    表示名 → 書体名 → 部分一致 → ファイル名の順に見る。どれにも当たらなければ
    :class:`TextImageError`（一覧の取り方を添える）。
    """
    wanted = str(name or "").strip()
    if not wanted:
        return default_font()
    faces = list_fonts()
    key = wanted.casefold()
    for face in faces:
        if face.name.casefold() == key:
            return face
    for face in faces:
        if face.family.casefold() == key:
            return face
    for face in faces:
        if key in face.name.casefold():
            return face
    for face in faces:
        if Path(face.path).name.casefold() == key:
            return face
    raise TextImageError(
        f"フォント '{wanted}' は見つかりません"
        "（使える書体は GET /api/v1/images/text/fonts で確認できます）"
    )


def load_font(face: FontFace | None, size: int) -> ImageFont.ImageFont:
    """書体を読み込む（読めなければ PIL の内蔵フォントに倒す）。"""
    if face is None:
        return ImageFont.load_default(size=size)
    try:
        return ImageFont.truetype(face.path, size, index=face.index)
    except OSError as exc:
        log.info("フォントを開けませんでした（内蔵フォントで描きます）: %s", exc)
        return ImageFont.load_default(size=size)


# --------------------------------------------------------------------------
# 検証（どれも純関数。ルーターは 400 に移すだけ）
# --------------------------------------------------------------------------

def check_text(value: object) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    if not text.strip():
        raise TextImageError("text が空です")
    return text


def check_size(value: object) -> int:
    try:
        size = int(value)
    except (TypeError, ValueError) as exc:
        raise TextImageError(f"size が整数ではありません: {value!r}") from exc
    if not 1 <= size <= MAX_SIZE:
        raise TextImageError(f"size は 1〜{MAX_SIZE} で指定してください（{size}）")
    return size


def check_align(value: object) -> str:
    align = str(value or "center").strip().lower()
    if align not in ALIGNS:
        raise TextImageError(
            f"unknown align '{align}' (allowed: {', '.join(ALIGNS)})"
        )
    return align


def check_color(value: object, *, default: str = "#ffffff") -> tuple[int, int, int, int]:
    """色（CSS 表記）を RGBA に直す。"""
    text = str(value or "").strip() or default
    try:
        return ImageColor.getcolor(text, "RGBA")  # type: ignore[return-value]
    except ValueError as exc:
        raise TextImageError(f"色を解釈できません: {text}") from exc


def check_background(value: object) -> tuple[int, int, int, int]:
    """``bg``（``transparent`` か色）を RGBA に直す。"""
    text = str(value or TRANSPARENT).strip() or TRANSPARENT
    if text.lower() == TRANSPARENT:
        return (0, 0, 0, 0)
    return check_color(text, default="#000000")


# --------------------------------------------------------------------------
# 描画
# --------------------------------------------------------------------------

def render_text(
    text: str,
    *,
    font: object = "",
    size: object = DEFAULT_SIZE,
    color: object = "#ffffff",
    outline_color: object = "",
    outline_width: object = 0,
    background: object = TRANSPARENT,
    rotate: object = 0,
    padding: object = DEFAULT_PADDING,
    align: object = "center",
) -> bytes:
    """文字を描いて **RGBA PNG のバイト列**を返す。

    改行はそのまま複数行になり、``align`` で行を揃える。``rotate``（度、反時計
    回りが正）は文字を組んだあとに掛けるので、回しても端が切れない
    （``expand=True``）。
    """
    body = check_text(text)
    pixels = check_size(size)
    alignment = check_align(align)
    fill = check_color(color)
    back = check_background(background)
    stroke = max(0, int(outline_width or 0))
    stroke_fill = check_color(outline_color, default="#000000") if stroke else None
    pad = max(0, int(padding if padding is not None else DEFAULT_PADDING))
    face = load_font(find_font(font), pixels)
    spacing = round(pixels * LINE_SPACING)

    # 実寸を測ってから、余白と縁取りのぶんだけ広いキャンバスを用意する
    ruler = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    box = ruler.multiline_textbbox(
        (0, 0), body, font=face, spacing=spacing, align=alignment,
        stroke_width=stroke,
    )
    # bbox は浮動小数で返ることがあるので、切り上げて整数の画素数にする
    width = max(1, math.ceil(box[2] - box[0])) + 2 * (pad + stroke)
    height = max(1, math.ceil(box[3] - box[1])) + 2 * (pad + stroke)
    if width > MAX_EDGE or height > MAX_EDGE:
        raise TextImageError(
            f"文字が大きすぎます（1 辺 {MAX_EDGE}px まで）: {width}x{height}"
        )

    canvas = Image.new("RGBA", (width, height), back)
    draw = ImageDraw.Draw(canvas)
    draw.multiline_text(
        (pad + stroke - math.floor(box[0]), pad + stroke - math.floor(box[1])),
        body,
        font=face,
        fill=fill,
        spacing=spacing,
        align=alignment,
        stroke_width=stroke,
        stroke_fill=stroke_fill,
    )

    angle = float(rotate or 0)
    if angle % 360:
        canvas = canvas.rotate(
            angle, resample=Image.BICUBIC, expand=True, fillcolor=back
        )
        if max(canvas.size) > MAX_EDGE:
            raise TextImageError(
                f"回転した結果が大きすぎます（1 辺 {MAX_EDGE}px まで）: {canvas.size}"
            )

    buffer = BytesIO()
    canvas.save(buffer, format="PNG")
    return buffer.getvalue()
