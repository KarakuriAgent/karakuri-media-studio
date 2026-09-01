"""スプライト（透過 PNG）の下ごしらえ（SPEC §7.2）: 背景を抜いて RGBA にする。

演出用の素材（ロゴ文字・雷マーク・小物・キャラのデフォルメ絵）は、画像生成に
「黒背景・単体・中央・影なし」で描かせてから**背景だけ抜く**のがいちばん確実
だった（BAN!BAN!BAN! の `fx/render_final.py` の ``key_black()``）。ここはその
「抜く」ところだけを持つモジュールで、DB もファイル登録も知らない（登録は
:func:`app.library.add_keyed`）。

抜き方は 4 つ:

* ``black`` / ``white`` — **ルミナンスキー**。明るさで前景と背景を分けるが、
  単純な閾値だと「文字の内側の黒」（縁取りの中や ``の`` の穴）まで抜けてしまう。
  そこで**外側から floodfill** して「画像の縁と地続きの背景」だけを背景と見なし、
  内側の同じ色は**穴として残す**。境界は閾値の前後を α の傾斜（:func:`_ramp`）に
  して滑らかにする
* ``chroma`` — 指定色との距離で α を決める（グリーンバックなど）。floodfill は
  使わない: 単色の背景は内側に入り込まないので、距離だけで足りる
* ``rembg`` — 任意依存（``backend/requirements-optional.txt``）。入っていなければ
  :class:`SpriteError`（ルーターは 400）で入れ方を案内する

どの方式でも、最後に ``trim`` で不透明部分の bbox に切り詰められる（Remotion の
``sprite`` / ``imageSlam`` は幅を画面比で指定するので、余白が付いたままだと
「大きく置いたのに小さく見える」ことになる）。
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image, ImageChops, ImageColor, ImageDraw, ImageFilter, ImageOps
from PIL import UnidentifiedImageError

#: 抜き方（``POST /api/v1/library/{id}/key`` の ``method``）
METHODS: tuple[str, ...] = ("black", "white", "chroma", "rembg")

#: 閾値の既定（0..1）。0.1 = 255 階調の 26 で、BAN の ``key_black(thresh=26)`` と同じ
DEFAULT_TOLERANCE = 0.1

#: クロマキーの既定色（グリーンバック）
DEFAULT_COLOR = "#00ff00"

#: α の傾斜を作る幅。閾値 ``cut`` の前後をこの割合で挟む
#: （``low = cut * RAMP_LOW`` 〜 ``high = cut * RAMP_HIGH + RAMP_MIN``）
RAMP_LOW = 0.6
RAMP_HIGH = 1.4
RAMP_MIN = 2

#: 抜いたあとに掛けるぼかし（px）。ジャギを消すが、これ以上大きいと輪郭が痩せる
EDGE_BLUR = 0.6

#: floodfill が塗る印。0（背景）でも 255（前景）でもない値なら何でもよい
_FILL_MARK = 128

#: トリムのときに「不透明」と見なす α の下限
TRIM_ALPHA = 16

#: 扱う画像の 1 辺の上限（:mod:`app.sheets` と同じ。これ以上は素材として大きすぎる）
MAX_EDGE = 8192


class SpriteError(Exception):
    """背景を抜けない（読めない画像・未知の方式・空の結果）。呼び出し側が 400 にする。"""


def check_method(method: object) -> str:
    """抜き方の名前を検証する。"""
    name = str(method or "").strip().lower() or "black"
    if name not in METHODS:
        raise SpriteError(
            f"unknown method '{name}' (allowed: {', '.join(METHODS)})"
        )
    return name


def check_tolerance(value: object) -> float:
    """許容差（0..1）を検証する。"""
    try:
        tolerance = float(value)
    except (TypeError, ValueError) as exc:
        raise SpriteError(f"tolerance が数値ではありません: {value!r}") from exc
    if not 0.0 <= tolerance <= 1.0:
        raise SpriteError(f"tolerance は 0〜1 で指定してください（{tolerance}）")
    return tolerance


def check_color(value: object) -> tuple[int, int, int]:
    """色（CSS 表記）を RGB に直す。"""
    text = str(value or "").strip() or DEFAULT_COLOR
    try:
        color = ImageColor.getrgb(text)
    except ValueError as exc:
        raise SpriteError(f"色を解釈できません: {text}") from exc
    return color[:3]


def open_image(source: str | Path | bytes) -> Image.Image:
    """素材を RGB で開く（読めなければ :class:`SpriteError`）。"""
    try:
        handle = BytesIO(source) if isinstance(source, bytes) else Path(source)
        with Image.open(handle) as image:
            image.load()
            if max(image.size) > MAX_EDGE:
                raise SpriteError(
                    f"画像が大きすぎます（1 辺 {MAX_EDGE}px まで）: {image.size}"
                )
            # 透過つきの素材は白背景に合成してから抜く（α が既にあるものを
            # ルミナンスキーに掛けると、透明部分の色が不定で結果が暴れる）
            if image.mode in ("RGBA", "LA", "P"):
                rgba = image.convert("RGBA")
                flat = Image.new("RGB", rgba.size, (255, 255, 255))
                flat.paste(rgba, mask=rgba.split()[3])
                return flat
            return image.convert("RGB")
    except (OSError, UnidentifiedImageError) as exc:
        name = source if isinstance(source, (str, Path)) else "(バイト列)"
        raise SpriteError(f"画像を読み込めません: {Path(str(name)).name}") from exc


# --------------------------------------------------------------------------
# 純関数の小道具（どれも PIL だけ。テストで固定できる）
# --------------------------------------------------------------------------

def luminance(image: Image.Image) -> Image.Image:
    """各画素の**最大チャンネル**を明るさとして取り出す（BAN と同じ ``max(axis=2)``）。

    ふつうの輝度（0.299R + 0.587G + 0.114B）だと純青の文字が黒と区別しづらい。
    最大チャンネルなら「どれか 1 色でも乗っていれば前景」になるので、黒背景から
    彩度の高い描き文字を抜くのに向く。
    """
    red, green, blue = image.convert("RGB").split()
    return ImageChops.lighter(ImageChops.lighter(red, green), blue)


def distance_to(image: Image.Image, color: tuple[int, int, int]) -> Image.Image:
    """指定色からの距離（チャンネル差の最大）を L 画像で返す。"""
    solid = Image.new("RGB", image.size, color)
    red, green, blue = ImageChops.difference(image.convert("RGB"), solid).split()
    return ImageChops.lighter(ImageChops.lighter(red, green), blue)


def _band(tolerance: float) -> tuple[float, float]:
    """許容差（0..1）から α の傾斜の下端・上端（0..255）を出す。"""
    cut = tolerance * 255
    return cut * RAMP_LOW, cut * RAMP_HIGH + RAMP_MIN


def _ramp(value: Image.Image, low: float, high: float) -> Image.Image:
    """``low`` 以下を 0、``high`` 以上を 255 にした線形の傾斜（L 画像）。"""
    span = max(1.0, high - low)

    def convert(level: int) -> int:
        if level <= low:
            return 0
        if level >= high:
            return 255
        return round((level - low) * 255 / span)

    return value.point(convert)


def interior_mask(foreground: Image.Image) -> Image.Image:
    """**外側と地続きの背景だけ**を 0 にしたマスク（L 画像。それ以外は 255）。

    ``foreground`` は「前景なら 255、背景なら 0」の二値画像。周囲に 2px の背景を
    足してから ``(0, 0)`` を floodfill し、塗られたところ（= 画像の縁と繋がった
    背景）だけを 0 にする。塗られなかった 0 の領域は**文字の内側の穴**なので
    255（= 残す）に倒す。BAN の ``key_black()`` と同じ考え方。
    """
    width, height = foreground.size
    padded = Image.new("L", (width + 4, height + 4), 0)
    padded.paste(foreground, (2, 2))
    ImageDraw.floodfill(padded, (0, 0), _FILL_MARK)
    inner = padded.crop((2, 2, 2 + width, 2 + height))
    return inner.point(lambda level: 0 if level == _FILL_MARK else 255)


def luminance_alpha(
    image: Image.Image, *, invert: bool = False, tolerance: float = DEFAULT_TOLERANCE
) -> Image.Image:
    """ルミナンスキーの α（``black`` / ``white``）。

    ``invert`` を立てると白背景を抜く（明るさを反転して同じ処理に乗せる）。
    外側の背景では明るさの傾斜をそのまま α にするので境界が滑らかになり、
    内側（floodfill が届かなかったところ）は 255 のまま残る。
    """
    level = luminance(image)
    if invert:
        level = ImageOps.invert(level)
    low, high = _band(tolerance)
    ramp = _ramp(level, low, high)
    # 二値化は傾斜の中点で切る（BAN の閾値と同じ位置になる）
    binary = level.point(lambda value: 255 if value > (low + high) / 2 else 0)
    return ImageChops.lighter(ramp, interior_mask(binary))


def chroma_alpha(
    image: Image.Image,
    color: tuple[int, int, int],
    *,
    tolerance: float = DEFAULT_TOLERANCE,
) -> Image.Image:
    """クロマキーの α（指定色に近いほど透明）。"""
    low, high = _band(tolerance)
    return _ramp(distance_to(image, color), low, high)


def flatten_to_color(image: Image.Image, color: tuple[int, int, int]) -> Image.Image:
    """α はそのままに、RGB だけを単色で塗り替える（RGBA 画像）。

    白抜きロゴのような「形だけ使いたい」素材のための後処理。抜いたあとに掛ける
    ので、境界の半透明はそのまま残り、輪郭のなじみも変わらない。
    """
    solid = Image.new("RGBA", image.size, (*color, 255))
    solid.putalpha(image.getchannel("A"))
    return solid


def trim_to_content(image: Image.Image) -> Image.Image:
    """不透明な部分の bbox に切り詰める（全部透明なら :class:`SpriteError`）。"""
    alpha = image.getchannel("A")
    box = alpha.point(lambda value: 255 if value > TRIM_ALPHA else 0).getbbox()
    if box is None:
        raise SpriteError(
            "抜いた結果が空になりました"
            "（method / tolerance を見直してください）"
        )
    return image.crop(box)


# --------------------------------------------------------------------------
# rembg（任意依存）
# --------------------------------------------------------------------------

def _rembg(data: bytes) -> Image.Image:
    """``rembg`` で背景を落とす（入っていなければ入れ方を案内する）。"""
    try:
        from rembg import remove  # type: ignore[import-not-found]
    except Exception as exc:  # ImportError だけでなく重い依存の失敗も拾う
        raise SpriteError(
            "method 'rembg' はこの環境では使えません"
            "（`pip install -r backend/requirements-optional.txt` か"
            " `pip install rembg` で使えるようになります）"
        ) from exc
    try:
        return Image.open(BytesIO(remove(data))).convert("RGBA")
    except Exception as exc:
        raise SpriteError(f"rembg が失敗しました: {exc}") from exc


# --------------------------------------------------------------------------
# 入り口
# --------------------------------------------------------------------------

def key_image(
    source: str | Path,
    *,
    method: str = "black",
    color: object = DEFAULT_COLOR,
    tolerance: object = DEFAULT_TOLERANCE,
    trim: bool = True,
    flatten: str | None = None,
) -> bytes:
    """``source`` の背景を抜いて **RGBA PNG のバイト列**を返す。

    元のファイルは触らない（呼び出し側が新しいライブラリ項目にする）。
    ``flatten`` に色（CSS 表記）を渡すと、抜いたあとに残った部分を**その色一色**に
    塗る（α は保つ）。白抜きロゴのように「形だけ欲しい」ときに使う。
    """
    name = check_method(method)
    amount = check_tolerance(tolerance)
    if name == "rembg":
        keyed = _rembg(Path(source).read_bytes())
    else:
        image = open_image(source)
        if name == "chroma":
            alpha = chroma_alpha(image, check_color(color), tolerance=amount)
        else:
            alpha = luminance_alpha(image, invert=(name == "white"), tolerance=amount)
        if EDGE_BLUR > 0:
            alpha = alpha.filter(ImageFilter.GaussianBlur(EDGE_BLUR))
        keyed = image.convert("RGBA")
        keyed.putalpha(alpha)
    if str(flatten or "").strip():
        keyed = flatten_to_color(keyed, check_color(flatten))
    if trim:
        keyed = trim_to_content(keyed)
    buffer = BytesIO()
    keyed.save(buffer, format="PNG")
    return buffer.getvalue()
