"""リファレンスシートの合成（SPEC §7.2）: レイアウト規則と描画。"""

from io import BytesIO

import pytest
from PIL import Image

from app import sheets


def png(width: int = 64, height: int = 64, color=(255, 0, 0)) -> bytes:
    """テスト用のべた塗り画像（PNG バイト列）。"""
    buffer = BytesIO()
    Image.new("RGB", (width, height), color).save(buffer, format="PNG")
    return buffer.getvalue()


def write(path, width: int = 64, height: int = 64, color=(255, 0, 0)):
    path.write_bytes(png(width, height, color))
    return path


def open_sheet(data: bytes) -> Image.Image:
    return Image.open(BytesIO(data)).convert("RGB")


# --------------------------------------------------------------------------
# レイアウト規則
# --------------------------------------------------------------------------

def test_layout_keeps_the_given_order_and_covers_the_canvas():
    panels = sheets.plan_layout(["character", "prop", "background"], 1280, 720)
    assert len(panels) == 3
    # 渡した順に、主役 / 脇役の別がそのまま付く
    assert [panel.main for panel in panels] == [True, False, False]
    # どのパネルもキャンバスの内側にあり、面積の合計はキャンバスちょうど
    for panel in panels:
        assert panel.x >= 0 and panel.y >= 0
        assert panel.x + panel.width <= 1280
        assert panel.y + panel.height <= 720
    assert sum(panel.area for panel in panels) == 1280 * 720


@pytest.mark.parametrize(
    "categories",
    [
        ["character", "prop"],
        ["character", "character", "prop"],
        ["character", "prop", "background", "prop"],
        ["prop", "character", "background", "prop", "prop"],
        ["character"] + ["prop"] * 7,
        ["character"] * 2 + ["background"] * 6,
    ],
)
def test_character_panels_are_always_larger(categories):
    """キャラクターのパネルは、どの組み合わせでも脇役より広い（規則 3）。"""
    panels = sheets.plan_layout(categories, 1280, 720)
    mains = [panel.area for panel in panels if panel.main]
    subs = [panel.area for panel in panels if not panel.main]
    assert min(mains) > max(subs)


def test_panels_do_not_overlap():
    panels = sheets.plan_layout(["character", "character", "prop", "prop"], 1280, 720)
    for first in range(len(panels)):
        for second in range(first + 1, len(panels)):
            a, b = panels[first], panels[second]
            apart = (
                a.x + a.width <= b.x
                or b.x + b.width <= a.x
                or a.y + a.height <= b.y
                or b.y + b.height <= a.y
            )
            assert apart, f"{a} と {b} が重なっている"


def test_one_kind_only_uses_the_whole_canvas():
    """片方の群しか無ければ左右に仕切らず、キャンバス全体を格子にする（規則 2）。

    枚数が格子にちょうど収まる（2 枚 = 2x1、4 枚 = 2x2）ときは隙間なく埋まる。
    """
    for categories in (["character", "character"], ["prop", "background", None, "prop"]):
        panels = sheets.plan_layout(categories, 1280, 720)
        assert min(panel.x for panel in panels) == 0
        assert max(panel.x + panel.width for panel in panels) == 1280
        assert sum(panel.area for panel in panels) == 1280 * 720


def test_uncategorized_items_are_small_panels():
    """未分類（None）と background / prop は同じ「脇役」（規則 1）。"""
    panels = sheets.plan_layout(["character", None, "background", "prop"], 1280, 720)
    assert [panel.main for panel in panels] == [True, False, False, False]


def test_the_grid_follows_the_canvas_shape():
    """セルの縦横比はキャンバスに近づける（規則 4）: 縦長なら縦に積む。"""
    landscape = sheets.plan_layout(["prop", "prop"], 1280, 720)
    assert [panel.x for panel in landscape] == [0, 640]  # 横に 2 枚
    portrait = sheets.plan_layout(["prop", "prop"], 720, 1280)
    assert [panel.y for panel in portrait] == [0, 640]  # 縦に 2 枚


def test_layout_rejects_bad_input():
    with pytest.raises(sheets.SheetError):
        sheets.plan_layout([])
    with pytest.raises(sheets.SheetError):
        sheets.plan_layout(["prop"] * (sheets.MAX_ITEMS + 1))
    with pytest.raises(sheets.SheetError):
        sheets.plan_layout(["prop"], 0, 720)
    with pytest.raises(sheets.SheetError):
        sheets.plan_layout(["prop"], sheets.MAX_EDGE + 8, 720)


# --------------------------------------------------------------------------
# 描画
# --------------------------------------------------------------------------

def test_render_makes_a_png_of_the_requested_size(tmp_path):
    sources = [
        (write(tmp_path / "hero.png", color=(255, 0, 0)), "character"),
        (write(tmp_path / "sword.png", color=(0, 255, 0)), "prop"),
    ]
    sheet = open_sheet(sheets.render_sheet(sources))
    assert sheet.size == (sheets.DEFAULT_WIDTH, sheets.DEFAULT_HEIGHT)

    wide = open_sheet(sheets.render_sheet(sources, 640, 640))
    assert wide.size == (640, 640)


def test_render_uses_a_black_background(tmp_path):
    """余白は黒（モデルカードの指定）。四隅は必ず余白に当たる。"""
    sources = [
        (write(tmp_path / "hero.png", 32, 32, (255, 0, 0)), "character"),
        (write(tmp_path / "sword.png", 32, 32, (0, 255, 0)), "prop"),
    ]
    sheet = open_sheet(sheets.render_sheet(sources))
    corners = [(0, 0), (1279, 0), (0, 719), (1279, 719)]
    assert [sheet.getpixel(point) for point in corners] == [sheets.BACKGROUND] * 4


def test_render_places_each_panel_where_the_layout_says(tmp_path):
    """パネルの中心にはその素材の色が出る（＝並び順どおりに置かれている）。"""
    colors = [(255, 0, 0), (0, 255, 0), (0, 0, 255)]
    categories = ["character", "prop", "background"]
    sources = [
        (write(tmp_path / f"{index}.png", 64, 64, color), category)
        for index, (color, category) in enumerate(zip(colors, categories))
    ]
    panels = sheets.plan_layout(categories)
    sheet = open_sheet(sheets.render_sheet(sources))
    for panel, color in zip(panels, colors):
        center = (panel.x + panel.width // 2, panel.y + panel.height // 2)
        assert sheet.getpixel(center) == color


def test_render_keeps_the_aspect_ratio_of_each_source(tmp_path):
    """細長い素材は引き伸ばさず、パネルの内側に収める（規則 5）。"""
    sources = [(write(tmp_path / "wide.png", 200, 50, (255, 0, 0)), "character")]
    sheet = open_sheet(sheets.render_sheet(sources, 400, 400))
    # 4:1 の素材を正方形のパネルに入れると、上下に黒帯が残る
    assert sheet.getpixel((200, 200)) == (255, 0, 0)
    assert sheet.getpixel((200, 20)) == sheets.BACKGROUND
    assert sheet.getpixel((200, 380)) == sheets.BACKGROUND


def test_render_scales_small_sources_up(tmp_path):
    """小さい素材でもパネルいっぱいに引き伸ばす（多少の劣化は許容）。"""
    sources = [(write(tmp_path / "tiny.png", 8, 8, (255, 0, 0)), "character")]
    sheet = open_sheet(sheets.render_sheet(sources, 400, 400))
    inner = 400 - 2 * sheets.PANEL_GAP
    assert sheet.getpixel((200, 200)) == (255, 0, 0)
    # 余白ぶんだけ内側から色が始まる
    assert sheet.getpixel((200, 400 - sheets.PANEL_GAP - 1)) == (255, 0, 0)
    assert sheet.getpixel((200, sheets.PANEL_GAP - 1)) == sheets.BACKGROUND
    assert inner > 0


def test_render_rejects_a_file_that_is_not_an_image(tmp_path):
    broken = tmp_path / "broken.png"
    broken.write_bytes(b"not an image")
    with pytest.raises(sheets.SheetError):
        sheets.render_sheet([(broken, "character")])
