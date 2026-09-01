"""透過キー（SPEC §7.2）: floodfill 方式のルミナンスキー・クロマキー・トリム。"""

from io import BytesIO

import pytest
from PIL import Image, ImageDraw

from app import sprites


def keyed(path, **options) -> Image.Image:
    """抜いた結果を RGBA で開く。"""
    return Image.open(BytesIO(sprites.key_image(path, **options))).convert("RGBA")


def ring(tmp_path, name: str = "ring.png", background=(0, 0, 0), ink=(255, 255, 255)):
    """外周が ``ink``、その内側にもう一度 ``background`` の穴がある的（まと）。

    「文字の内側の黒」を再現する形。floodfill を使わずに閾値だけで抜くと、中央の
    穴まで透明になってしまう。
    """
    image = Image.new("RGB", (200, 200), background)
    draw = ImageDraw.Draw(image)
    draw.rectangle([50, 50, 149, 149], fill=ink)
    draw.rectangle([80, 80, 119, 119], fill=background)
    path = tmp_path / name
    image.save(path)
    return path


# --------------------------------------------------------------------------
# ルミナンスキー（black / white）
# --------------------------------------------------------------------------

def test_black_key_removes_the_outside_and_keeps_the_hole(tmp_path):
    result = keyed(ring(tmp_path), method="black", trim=False)
    assert result.size == (200, 200)
    alpha = result.getchannel("A")
    # 外側の背景は抜ける
    assert alpha.getpixel((5, 5)) == 0
    # 描いたところは残る
    assert alpha.getpixel((60, 60)) == 255
    # 内側の同じ黒は「穴」なので残る（floodfill 方式の要点）
    assert alpha.getpixel((100, 100)) == 255


def test_white_key_is_the_same_with_the_brightness_flipped(tmp_path):
    path = ring(tmp_path, "white.png", background=(255, 255, 255), ink=(0, 0, 0))
    alpha = keyed(path, method="white", trim=False).getchannel("A")
    assert alpha.getpixel((5, 5)) == 0
    assert alpha.getpixel((60, 60)) == 255
    assert alpha.getpixel((100, 100)) == 255


def test_the_edge_is_a_ramp_not_a_step(tmp_path):
    """閾値の前後は α が中間値になる（ジャギを残さない）。"""
    image = Image.new("RGB", (32, 8), (0, 0, 0))
    for x in range(32):
        # 左から右へ、じわじわ明るくする
        image.paste((x * 2, x * 2, x * 2), [x, 0, x + 1, 8])
    path = tmp_path / "gradient.png"
    image.save(path)
    alpha = keyed(path, method="black", trim=False).getchannel("A")
    values = [alpha.getpixel((x, 4)) for x in range(32)]
    assert values[0] == 0 and values[-1] == 255
    assert any(0 < value < 255 for value in values)


# --------------------------------------------------------------------------
# クロマキー
# --------------------------------------------------------------------------

def test_chroma_key_removes_the_named_colour(tmp_path):
    image = Image.new("RGB", (100, 100), (0, 255, 0))
    ImageDraw.Draw(image).rectangle([30, 30, 69, 69], fill=(220, 20, 40))
    path = tmp_path / "green.png"
    image.save(path)
    alpha = keyed(path, method="chroma", color="#00ff00", trim=False).getchannel("A")
    assert alpha.getpixel((5, 5)) == 0
    assert alpha.getpixel((50, 50)) == 255


def test_chroma_key_keeps_the_inner_colour_too(tmp_path):
    """クロマキーは floodfill を使わないので、内側の同じ色も抜ける（仕様）。"""
    image = Image.new("RGB", (100, 100), (0, 255, 0))
    draw = ImageDraw.Draw(image)
    draw.rectangle([20, 20, 79, 79], fill=(220, 20, 40))
    draw.rectangle([40, 40, 59, 59], fill=(0, 255, 0))
    path = tmp_path / "donut.png"
    image.save(path)
    alpha = keyed(path, method="chroma", color="#00ff00", trim=False).getchannel("A")
    assert alpha.getpixel((50, 50)) == 0


# --------------------------------------------------------------------------
# 単色化（flatten）
# --------------------------------------------------------------------------

def test_flatten_repaints_the_kept_part_in_one_colour(tmp_path):
    # 赤い的を黒背景から抜いて、残った部分だけを白く塗る（白抜きロゴ）
    path = ring(tmp_path, "red.png", ink=(220, 40, 40))
    result = keyed(path, method="black", trim=False, flatten="#ffffff")
    assert result.getpixel((60, 60)) == (255, 255, 255, 255)
    # α は塗り替えない（外は透明、内側の穴は残る）
    alpha = result.getchannel("A")
    assert alpha.getpixel((5, 5)) == 0
    assert alpha.getpixel((100, 100)) == 255
    # 透明なところも色だけは差し替わる（見えないので害は無い）
    assert result.getpixel((5, 5))[:3] == (255, 255, 255)


def test_flatten_is_off_unless_a_colour_is_given(tmp_path):
    path = ring(tmp_path, "red2.png", ink=(220, 40, 40))
    for flatten in (None, "", "  "):
        result = keyed(path, method="black", trim=False, flatten=flatten)
        assert result.getpixel((60, 60))[:3] == (220, 40, 40)


def test_a_bad_flatten_colour_is_rejected(tmp_path):
    with pytest.raises(sprites.SpriteError):
        sprites.key_image(ring(tmp_path), method="black", flatten="not-a-colour")


# --------------------------------------------------------------------------
# トリムと検証
# --------------------------------------------------------------------------

def test_trim_crops_to_the_opaque_bounding_box(tmp_path):
    image = Image.new("RGB", (200, 120), (0, 0, 0))
    ImageDraw.Draw(image).rectangle([40, 30, 99, 89], fill=(255, 255, 255))
    path = tmp_path / "box.png"
    image.save(path)
    assert keyed(path, method="black", trim=False).size == (200, 120)
    trimmed = keyed(path, method="black", trim=True)
    # ぼかしのぶん 1〜2px 広がることがあるので、おおよそで見る
    assert 60 <= trimmed.width <= 64
    assert 60 <= trimmed.height <= 64


def test_an_empty_result_is_reported(tmp_path):
    path = tmp_path / "all-black.png"
    Image.new("RGB", (32, 32), (0, 0, 0)).save(path)
    with pytest.raises(sprites.SpriteError):
        sprites.key_image(path, method="black", trim=True)


def test_unknown_method_and_tolerance_are_rejected(tmp_path):
    path = ring(tmp_path)
    with pytest.raises(sprites.SpriteError):
        sprites.key_image(path, method="magic")
    with pytest.raises(sprites.SpriteError):
        sprites.key_image(path, tolerance=2)
    with pytest.raises(sprites.SpriteError):
        sprites.key_image(path, method="chroma", color="not-a-colour")


def test_an_unreadable_file_is_reported(tmp_path):
    path = tmp_path / "broken.png"
    path.write_bytes(b"not a png")
    with pytest.raises(sprites.SpriteError):
        sprites.key_image(path)


def test_rembg_explains_how_to_install_it(tmp_path):
    try:
        import rembg  # noqa: F401
    except Exception:
        pass
    else:
        pytest.skip("rembg が入っている環境では 400 にならない")
    with pytest.raises(sprites.SpriteError) as info:
        sprites.key_image(ring(tmp_path), method="rembg")
    assert "rembg" in str(info.value)
