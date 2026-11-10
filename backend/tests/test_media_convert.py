"""取り込みの変換（SVG -> PNG）と、アップロード・素材パックからの使われ方。

実際の変換は ffmpeg（librsvg 入り）があるときだけ走らせる。
"""

import asyncio
import json
import shutil
import subprocess
from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import asset_packs, db, jobs, library, media_convert
from app.main import app

KEY = "test-key-media-convert"


def _has_librsvg() -> bool:
    if shutil.which("ffmpeg") is None:
        return False
    found = subprocess.run(["ffmpeg", "-hide_banner", "-decoders"],
                           capture_output=True, text=True, check=False)
    return "librsvg" in found.stdout


HAS_LIBRSVG = _has_librsvg()
needs_librsvg = pytest.mark.skipif(not HAS_LIBRSVG, reason="ffmpeg の librsvg が無い")

# 左に不透明の赤い四角、右に半透明の青い四角（幅 200 x 高さ 100）
SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 100">'
    b'<rect x="0" y="0" width="100" height="100" fill="#ff0000"/>'
    b'<rect x="100" y="0" width="100" height="100" fill="#0000ff" fill-opacity="0.5"/>'
    b"</svg>"
)


def test_svg_aspect_reads_viewbox_and_lengths():
    assert media_convert.svg_aspect(SVG) == 2.0
    tall = b'<svg xmlns="http://www.w3.org/2000/svg" width="10mm" height="40mm"/>'
    assert media_convert.svg_aspect(tall) == pytest.approx(0.25)
    unknown = b'<svg xmlns="http://www.w3.org/2000/svg" width="100%"/>'
    assert media_convert.svg_aspect(unknown) == 1.0
    with pytest.raises(media_convert.ConvertError, match="SVG ではありません"):
        media_convert.svg_aspect(b"<html/>")
    with pytest.raises(media_convert.ConvertError, match="読めません"):
        media_convert.svg_aspect(b"<svg")


def test_raster_size_puts_the_long_side_on_the_given_size():
    assert media_convert.raster_size(2.0, 2048) == (2048, 1024)
    assert media_convert.raster_size(0.25, 2048) == (512, 2048)
    assert media_convert.check_svg_size(None) == 2048
    assert media_convert.check_svg_size(0) == 2048
    with pytest.raises(media_convert.ConvertError):
        media_convert.check_svg_size(9000)


@needs_librsvg
def test_svg_becomes_a_straight_alpha_png():
    png = asyncio.run(media_convert.svg_to_png(SVG, 400))
    with Image.open(BytesIO(png)) as image:
        assert image.format == "PNG" and image.mode == "RGBA"
        assert image.size == (400, 200)
        red = image.getpixel((100, 100))
        blue = image.getpixel((300, 100))
    assert red == (255, 0, 0, 255)
    # 乗算済みのままだと (0, 0, 128, 128) になる
    assert blue[3] in range(126, 130) and blue[2] >= 250 and blue[:2] == (0, 0)


@pytest.fixture
def env(tmp_path, monkeypatch):
    lib = tmp_path / "library"
    lib.mkdir()
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(library, "LIBRARY_DIR", lib)
    monkeypatch.setattr(jobs, "LIBRARY_DIR", lib)
    monkeypatch.setattr(asset_packs, "PACKS_DIR", tmp_path / "packs")

    async def fake_sleep(seconds):
        return None

    monkeypatch.setattr(asset_packs, "sleep", fake_sleep)
    with TestClient(app) as client:
        assert client.put("/api/settings", json={"external_api_key": KEY}).status_code == 200
        yield type("Env", (), {"client": client, "tmp": tmp_path, "library": lib})


def call(env, method, path, **kwargs):
    return env.client.request(method, path, headers={"X-API-Key": KEY}, **kwargs)


@needs_librsvg
def test_an_uploaded_svg_is_stored_as_png_with_the_original_kept(env):
    for path in ("/api/v1/library/image", "/api/v1/library/upload"):
        response = call(env, "POST", path,
                        files={"file": ("mark.svg", SVG, "image/svg+xml")},
                        data={"svg_size": "300"})
        assert response.status_code == 201, response.text
        item = response.json()
        assert item["kind"] == "image" and item["url"].endswith(".png")
        assert item["name"] == "mark.svg"
        with Image.open(item["path"]) as image:
            assert image.size == (300, 150)
        info = item["media_info"]
        assert info["converted_from"] == "svg"
        assert info["has_alpha"] is True and (info["width"], info["height"]) == (300, 150)
        assert info["original_url"].startswith("/library/originals/")
        assert Path(info["original_path"]).read_bytes() == SVG

    # 消すと元の SVG も消える
    assert call(env, "DELETE", f"/api/v1/library/{item['id']}").status_code == 204
    assert not Path(info["original_path"]).exists()

    # 既定は長辺 2048
    default = call(env, "POST", "/api/v1/library/image",
                   files={"file": ("big.svg", SVG, "image/svg+xml")}).json()
    assert (default["media_info"]["width"], default["media_info"]["height"]) == (2048, 1024)


def test_a_broken_svg_upload_is_refused(env):
    broken = call(env, "POST", "/api/v1/library/image",
                  files={"file": ("bad.svg", b"<svg", "image/svg+xml")})
    assert broken.status_code == 400
    too_big = call(env, "POST", "/api/v1/library/image",
                   files={"file": ("a.svg", SVG, "image/svg+xml")},
                   data={"svg_size": "99999"})
    assert too_big.status_code == 400
    # 画像の棚以外には SVG は入らない
    audio = call(env, "POST", "/api/v1/library/audio",
                 files={"file": ("a.svg", SVG, "image/svg+xml")})
    assert audio.status_code == 400


@needs_librsvg
def test_a_pack_svg_inside_a_zip_is_imported_as_png(env, monkeypatch):
    import zipfile

    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("marks/sweat.svg", SVG)
    inbox = env.tmp / "packs" / "svg-pack" / "inbox"
    inbox.mkdir(parents=True)
    (inbox / "marks.zip").write_bytes(buffer.getvalue())
    (inbox / "anger.svg").write_bytes(SVG)
    manifest = {
        "pack": "svg-pack",
        "sources": [{"id": "hand", "name": "Hand", "acquire": "manual"}],
        "items": [
            {"member": "marks/sweat.svg", "archive": "marks.zip", "source": "hand",
             "svg_size": 512},
            {"file": "anger.svg", "source": "hand"},
        ],
    }
    response = call(env, "POST", "/api/v1/library/packs/import",
                    json={"manifest": manifest, "accept_sources": ["hand"]})
    assert response.status_code == 202, response.text
    import time

    deadline = time.time() + 20
    while time.time() < deadline:
        result = call(env, "GET", f"/api/v1/library/packs/imports/{response.json()['id']}").json()
        if result["status"] in ("done", "failed"):
            break
        time.sleep(0.05)
    assert result["status"] == "done", result
    assert sorted(entry["file"] for entry in result["imported"]) == ["anger.svg", "sweat.svg"]
    items = {item["name"]: item for item in call(env, "GET", "/api/v1/library").json()["items"]}
    sweat = items["sweat.svg"]
    assert sweat["url"].endswith(".png")
    assert (sweat["media_info"]["width"], sweat["media_info"]["height"]) == (512, 256)
    assert sweat["media_info"]["converted_from"] == "svg"
    assert sweat["license"]["member"] == "marks/sweat.svg"
    assert items["anger.svg"]["media_info"]["width"] == 2048


def test_a_pack_svg_size_out_of_range_is_refused():
    body = {
        "pack": "p", "sources": [{"id": "s", "name": "S", "acquire": "manual"}],
        "items": [{"file": "a.svg", "source": "s", "svg_size": 4}],
    }
    with pytest.raises(asset_packs.AssetPackError, match="svg_size"):
        asset_packs.parse_manifest(json.dumps(body))


# --------------------------------------------------------------------------
# 動画（ProRes 4444 -> アルファ付きの WebM）
# --------------------------------------------------------------------------

def _has_encoders(*names: str) -> bool:
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        return False
    found = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"],
                           capture_output=True, text=True, check=False).stdout
    return all(name in found for name in names)


needs_vp9 = pytest.mark.skipif(
    not _has_encoders("libvpx-vp9", "prores_ks"), reason="ffmpeg の libvpx-vp9 / prores_ks が無い"
)


def _prores_4444(path: Path) -> bytes:
    subprocess.run([
        "ffmpeg", "-v", "error", "-y", "-f", "lavfi",
        "-i", "color=c=red@0.5:s=64x48:d=0.5:r=12,format=yuva444p10le",
        "-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuva444p10le", str(path),
    ], check=True)
    return path.read_bytes()


def test_the_webm_command_keeps_alpha():
    command = media_convert.webm_command(Path("a.mov"), Path("b.webm"), alpha=True, audio=False)
    assert "libvpx-vp9" in command and "yuva420p" in command
    assert command[command.index("-auto-alt-ref") + 1] == "0" and "-an" in command
    plain = media_convert.webm_command(Path("a.mov"), Path("b.webm"), alpha=False, audio=True)
    assert "yuv420p" in plain and "libopus" in plain and "-auto-alt-ref" not in plain


@needs_vp9
def test_an_uploaded_prores_4444_becomes_an_alpha_webm(env, tmp_path):
    data = _prores_4444(tmp_path / "mark.mov")
    response = call(env, "POST", "/api/v1/library/upload",
                    files={"file": ("mark.mov", data, "video/quicktime")})
    assert response.status_code == 201, response.text
    item = response.json()
    assert item["kind"] == "video" and item["url"].endswith(".webm")
    info = item["media_info"]
    assert info["converted_from"] == "prores"
    assert info["has_alpha"] is True and (info["width"], info["height"]) == (64, 48)
    assert Path(info["original_path"]).read_bytes() == data
    probed = json.loads(subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", item["path"]],
        capture_output=True, text=True, check=True,
    ).stdout)
    stream = probed["streams"][0]
    assert stream["codec_name"] == "vp9" and stream["tags"].get("alpha_mode") == "1"


@needs_vp9
def test_a_playable_video_is_stored_as_it_is(env, tmp_path):
    webm = tmp_path / "ok.webm"
    subprocess.run([
        "ffmpeg", "-v", "error", "-y", "-f", "lavfi",
        "-i", "color=c=blue:s=32x32:d=0.3:r=12", "-c:v", "libvpx-vp9", str(webm),
    ], check=True)
    item = call(env, "POST", "/api/v1/library/upload",
                files={"file": ("ok.webm", webm.read_bytes(), "video/webm")}).json()
    assert item["url"].endswith(".webm") and item["media_info"] is None


@needs_vp9
def test_a_pack_prores_is_converted_and_keeps_its_provenance(env, tmp_path):
    inbox = env.tmp / "packs" / "fx-pack" / "inbox"
    inbox.mkdir(parents=True)
    data = _prores_4444(inbox / "sparkle.mov")
    manifest = {
        "pack": "fx-pack",
        "sources": [{"id": "hand", "name": "Hand", "acquire": "manual"}],
        "items": [{"file": "sparkle.mov", "source": "hand", "category": "manga-mark"}],
    }
    import time

    response = call(env, "POST", "/api/v1/library/packs/import",
                    json={"manifest": manifest, "accept_sources": ["hand"]})
    deadline = time.time() + 60
    while time.time() < deadline:
        result = call(env, "GET", f"/api/v1/library/packs/imports/{response.json()['id']}").json()
        if result["status"] in ("done", "failed"):
            break
        time.sleep(0.1)
    assert [entry["file"] for entry in result["imported"]] == ["sparkle.mov"], result
    item = call(env, "GET", "/api/v1/library?kind=video").json()["items"][0]
    assert item["url"].endswith(".webm")
    assert item["media_info"]["converted_from"] == "prores"
    assert item["media_info"]["has_alpha"] is True
    assert item["license"]["sha256"] == __import__("hashlib").sha256(data).hexdigest()
