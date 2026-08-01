"""リファレンスシートの合成（SPEC §7.2）: 素材を 1 枚のシート画像にまとめる。

LTX-2.3 Ingredients IC-LoRA（:data:`app.workflows.LTX_IC_LORA_IMAGE`）は「複数の
パネルを並べた 1 枚の画像」を参照入力に取る。Lightricks のモデルカードが言う
シートの作法はこう:

* 背景は**黒**。キャラクター・小物・背景をそれぞれ**独立したパネル**として置く
  （横一列に限らず、格子状でよい）。パネルにテキストラベルは入れない
* **大きく写っているパネルほど精密に再現される**ので、主役（キャラクター）は
  大きく、脇役（小物・背景）は小さく置く
* シートのアスペクト比は出力動画に合わせるのが望ましい（ワークフロー側の
  ``ResizeAndPadImage`` が黒でパディングするので、比が合っていれば余白が出ない）

ここはその「並べ方」だけを持つモジュールで、DB もファイル登録も知らない
（登録は :func:`app.library.add_sheet`）。レイアウトは :func:`plan_layout` に
純粋な計算として切り出してあり、描画 :func:`render_sheet` はそれに従って貼るだけ。

レイアウト規則（決定的。同じ入力からは必ず同じ絵になる）:

1. 素材を「主役」（カテゴリ ``character``）と「脇役」（``background`` /
   ``prop`` / 未分類）の 2 群に分け、**渡された順序**のまま扱う
2. どちらか一方しか無ければ、キャンバス全体を 1 つの格子に切って並べる
3. 両方あれば、キャンバスを**左右に分割**して左を主役、右を脇役に割り当てる。
   分割位置は重み（主役 1 件 = :data:`MAIN_WEIGHT`、脇役 1 件 = 1）の比で、
   結果として**主役のパネルは必ず脇役のパネルより広くなる**
   （格子の詰まり具合は最悪でも 1/2 より良いため、重み 2 が逆転しない）
4. 各領域の格子は「セルの縦横比がキャンバスの縦横比にいちばん近くなる」列数を
   選ぶ（同点なら列数の少ないほう）。行は上から、列は左から、順序どおりに埋める
5. 各パネルの中では、元画像の縦横比を保ったまま内側に収める（contain）。
   セルの縁には :data:`PANEL_GAP` px の余白を空け、小さい素材は拡大する
   （多少の劣化より、パネルを大きく使うほうがモデルによく効く）
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Iterable, Sequence

from PIL import Image, ImageOps, UnidentifiedImageError

#: 既定のシートサイズ（16:9。ワークフローの ``ResizeAndPadImage`` の既定と同じ）
DEFAULT_WIDTH = 1280
DEFAULT_HEIGHT = 720

#: 1 枚のシートに載せられる素材の数。実用上は 2〜8 枚（1 枚でも作れはする）
MIN_ITEMS = 1
MAX_ITEMS = 8

#: キャンバスの上限（これ以上大きくしても IC-LoRA 側で縮められる）
MAX_EDGE = 4096

#: 主役（character）1 件ぶんの面積の重み。脇役は 1
MAIN_WEIGHT = 2.0

#: パネルの周囲に空ける余白（px）。パネル同士がくっついて 1 枚の絵に見えるのを防ぐ
PANEL_GAP = 8

#: シートの背景色（モデルカードが指定する黒）
BACKGROUND = (0, 0, 0)

#: 大きいパネルに置くカテゴリ（:data:`app.library.CATEGORIES` の 1 つ）
MAIN_CATEGORY = "character"


class SheetError(Exception):
    """シートを組めない（枚数・サイズ・読めない画像）。呼び出し側が 400 に変換する。"""


@dataclass(frozen=True)
class Panel:
    """シート上の 1 パネルの置き場所（左上の座標と大きさ、単位は px）。"""

    x: int
    y: int
    width: int
    height: int
    #: 主役（character）のパネルか
    main: bool

    @property
    def area(self) -> int:
        return self.width * self.height


def is_main(category: object) -> bool:
    """そのカテゴリを大きいパネルに置くか（character だけが主役）。"""
    return category == MAIN_CATEGORY


def check_count(count: int) -> None:
    """素材の枚数を検証する（0 枚と多すぎは組めない）。"""
    if count < MIN_ITEMS:
        raise SheetError("リファレンスシートには素材を 1 枚以上選んでください")
    if count > MAX_ITEMS:
        raise SheetError(
            f"リファレンスシートに載せられるのは {MAX_ITEMS} 枚までです（{count} 枚）"
        )


def check_canvas(width: int, height: int) -> tuple[int, int]:
    """キャンバスの大きさを検証する（正の整数・上限 :data:`MAX_EDGE`）。"""
    if width <= 0 or height <= 0:
        raise SheetError(f"シートの大きさが不正です: {width}x{height}")
    if width > MAX_EDGE or height > MAX_EDGE:
        raise SheetError(
            f"シートの大きさは {MAX_EDGE}px までです（{width}x{height}）"
        )
    return int(width), int(height)


def _grid(count: int, region_width: int, region_height: int, target: float) -> tuple[int, int]:
    """``count`` 個を収める格子（列数, 行数）。

    セルの縦横比が ``target``（キャンバスの縦横比）にいちばん近くなる列数を選ぶ。
    比較は対数の差でとるので、横長・縦長どちらのずれも同じ重さで効く。同点のとき
    （2 枚を横に並べるか縦に積むか、など）はキャンバスの向きに合わせる: 横長なら
    列を増やし、縦長なら行を増やす。
    """
    wide = target >= 1
    best: tuple[float, int, int] | None = None
    for cols in range(1, count + 1):
        rows = math.ceil(count / cols)
        aspect = (region_width / cols) / (region_height / rows)
        score = abs(math.log(aspect / target))
        if best is None or score < best[0] or (score == best[0] and wide):
            best = (score, cols, rows)
    assert best is not None
    return best[1], best[2]


def _split(mains: int, subs: int, width: int) -> int:
    """主役側（左）に割り当てる幅。重みの比で切り、両側に最低 1px は残す。"""
    main_weight = MAIN_WEIGHT * mains
    edge = round(width * main_weight / (main_weight + subs))
    return max(1, min(width - 1, edge))


def _fill(
    panels: list[Panel | None],
    members: Sequence[int],
    main: bool,
    box: tuple[int, int, int, int],
    target: float,
) -> None:
    """領域 ``box`` を格子に切って ``members`` の順に埋める（``panels`` を書き換える）。"""
    x, y, width, height = box
    cols, rows = _grid(len(members), width, height, target)
    for position, index in enumerate(members):
        col, row = position % cols, position // cols
        # 端数は round で配ってから差を取る（セル同士に隙間や重なりを作らない）
        left = x + round(col * width / cols)
        right = x + round((col + 1) * width / cols)
        top = y + round(row * height / rows)
        bottom = y + round((row + 1) * height / rows)
        panels[index] = Panel(left, top, right - left, bottom - top, main)


def plan_layout(
    categories: Sequence[object],
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
) -> list[Panel]:
    """カテゴリの並びから各パネルの置き場所を決める（モジュール冒頭の規則）。

    戻り値は ``categories`` と同じ順序・同じ長さ。画像は開かないので、レイアウトの
    検証はこの関数だけで完結する。
    """
    check_count(len(categories))
    width, height = check_canvas(width, height)
    mains = [index for index, value in enumerate(categories) if is_main(value)]
    subs = [index for index, value in enumerate(categories) if not is_main(value)]
    target = width / height
    panels: list[Panel | None] = [None] * len(categories)
    if not mains or not subs:
        # 片方しか無いときは仕切らず、キャンバス全体を 1 つの格子として使う
        members = mains or subs
        _fill(panels, members, bool(mains), (0, 0, width, height), target)
    else:
        edge = _split(len(mains), len(subs), width)
        _fill(panels, mains, True, (0, 0, edge, height), target)
        _fill(panels, subs, False, (edge, 0, width - edge, height), target)
    assert all(panel is not None for panel in panels)
    return [panel for panel in panels if panel is not None]


def _contain(image: Image.Image, panel: Panel) -> tuple[Image.Image, int, int]:
    """パネルの内側に縦横比を保って収めた画像と、貼り付け位置。

    余白（:data:`PANEL_GAP`）を引いた枠にちょうど収まるまで拡大縮小する。小さい
    素材を引き伸ばすと粗くなるが、パネルを大きく使うほうが IC-LoRA には効くので
    そのまま拡大する（縮小・拡大とも lanczos）。
    """
    inner_width = max(1, panel.width - 2 * PANEL_GAP)
    inner_height = max(1, panel.height - 2 * PANEL_GAP)
    fitted = ImageOps.contain(image, (inner_width, inner_height), Image.LANCZOS)
    x = panel.x + (panel.width - fitted.width) // 2
    y = panel.y + (panel.height - fitted.height) // 2
    return fitted, x, y


def _open(path: Path) -> Image.Image:
    try:
        with Image.open(path) as image:
            return image.convert("RGB")
    except (OSError, UnidentifiedImageError) as exc:
        raise SheetError(f"画像を読み込めません: {Path(path).name}") from exc


def render_sheet(
    sources: Iterable[tuple[Path, object]],
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
) -> bytes:
    """``(画像のパス, カテゴリ)`` の並びから 1 枚のシートを描いて PNG で返す。

    並べ方は :func:`plan_layout`（モジュール冒頭のレイアウト規則）のとおり。
    """
    items = list(sources)
    panels = plan_layout([category for _, category in items], width, height)
    canvas = Image.new("RGB", (width, height), BACKGROUND)
    for (path, _), panel in zip(items, panels):
        frame = _open(Path(path))
        fitted, x, y = _contain(frame, panel)
        canvas.paste(fitted, (x, y))
    buffer = BytesIO()
    canvas.save(buffer, format="PNG")
    return buffer.getvalue()
