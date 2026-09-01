"""フォント画像（SPEC §7.2）: 書体の一覧と、PIL による文字の描画。"""

from io import BytesIO

import pytest
from PIL import Image

from app import textimage


@pytest.fixture(autouse=True)
def fresh_cache():
    """書体一覧のキャッシュをテストごとに捨てる。"""
    textimage.clear_cache()
    yield
    textimage.clear_cache()


def rendered(**options) -> Image.Image:
    return Image.open(BytesIO(textimage.render_text(**options))).convert("RGBA")


# --------------------------------------------------------------------------
# 書体の一覧
# --------------------------------------------------------------------------

def test_fc_list_output_becomes_faces(monkeypatch):
    class Done:
        returncode = 0
        stdout = (
            "/usr/share/fonts/NotoSansCJK-Bold.ttc\t0\t"
            "Noto Sans CJK JP,ノトサンス\tBold\n"
            "/usr/share/fonts/DejaVuSans.ttf\t0\tDejaVu Sans\tBook\n"
            # 拡張子が違うものは通さない（PIL が開けない）
            "/usr/share/fonts/C059-Roman.pfb\t0\tC059\tRoman\n"
        )

    monkeypatch.setattr(textimage.subprocess, "run", lambda *a, **k: Done())
    faces = textimage.list_fonts()
    names = [face.name for face in faces]
    assert "Noto Sans CJK JP Bold" in names
    assert "DejaVu Sans Book" in names
    assert not any(name.startswith("C059") for name in names)
    # カンマ区切りの候補は先頭だけを使う
    noto = next(face for face in faces if face.name == "Noto Sans CJK JP Bold")
    assert noto.family == "Noto Sans CJK JP"
    assert noto.index == 0


def test_the_default_font_follows_the_preference_order(monkeypatch):
    class Done:
        returncode = 0
        stdout = (
            "/f/AAA.ttf\t0\tAAA Gothic\tRegular\n"
            "/f/Noto.ttc\t0\tNoto Sans CJK JP\tBold\n"
        )

    monkeypatch.setattr(textimage.subprocess, "run", lambda *a, **k: Done())
    assert textimage.default_font().name == "Noto Sans CJK JP Bold"


def test_an_unknown_font_is_rejected(monkeypatch):
    class Done:
        returncode = 0
        stdout = "/f/AAA.ttf\t0\tAAA Gothic\tRegular\n"

    monkeypatch.setattr(textimage.subprocess, "run", lambda *a, **k: Done())
    # 部分一致は通る
    assert textimage.find_font("aaa").name == "AAA Gothic"
    with pytest.raises(textimage.TextImageError):
        textimage.find_font("Comic Sans MS")


def test_a_missing_fc_list_falls_back_to_scanning(monkeypatch, tmp_path):
    def boom(*args, **kwargs):
        raise FileNotFoundError("fc-list")

    monkeypatch.setattr(textimage.subprocess, "run", boom)
    (tmp_path / "MyFont.ttf").write_bytes(b"not really a font")
    monkeypatch.setattr(textimage, "FONT_DIRS", (tmp_path,))
    assert [face.name for face in textimage.list_fonts()] == ["MyFont"]


# --------------------------------------------------------------------------
# 描画
# --------------------------------------------------------------------------

def test_text_is_drawn_on_a_transparent_canvas():
    image = rendered(text="BAN", size=64, color="#ffffff")
    assert image.mode == "RGBA"
    assert image.width > 0 and image.height > 0
    # 角は余白なので透明、どこかには不透明な画素がある
    assert image.getpixel((0, 0))[3] == 0
    assert image.getchannel("A").getextrema()[1] == 255


def test_a_solid_background_fills_the_canvas():
    image = rendered(text="BAN", size=48, background="#000000")
    assert image.getpixel((0, 0)) == (0, 0, 0, 255)


def test_more_lines_make_a_taller_image():
    one = rendered(text="BAN", size=48)
    two = rendered(text="BAN\nBAN", size=48)
    assert two.height > one.height


def test_padding_and_outline_grow_the_canvas():
    plain = rendered(text="A", size=48, padding=0)
    padded = rendered(text="A", size=48, padding=20)
    assert padded.width == plain.width + 40
    outlined = rendered(text="A", size=48, padding=0,
                        outline_color="#ff0000", outline_width=4)
    assert outlined.width > plain.width


def test_rotation_expands_the_canvas():
    upright = rendered(text="BAN", size=48)
    turned = rendered(text="BAN", size=48, rotate=90)
    # 90 度回せば縦横が入れ替わる（expand=True なので端は切れない）
    assert turned.height >= upright.width - 2


def test_empty_and_oversized_input_is_rejected():
    with pytest.raises(textimage.TextImageError):
        textimage.render_text("   ")
    with pytest.raises(textimage.TextImageError):
        textimage.render_text("A", size=textimage.MAX_SIZE + 1)
    with pytest.raises(textimage.TextImageError):
        textimage.render_text("A", align="middle")
    with pytest.raises(textimage.TextImageError):
        textimage.render_text("A", color="chartreuseish")
    with pytest.raises(textimage.TextImageError):
        textimage.render_text("A" * 200, size=512)
