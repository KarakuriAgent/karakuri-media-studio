"""素材の下ごしらえ API（SPEC §7.2）: 透過キー・フォント画像・コンタクトシート。

抜き方そのものは ``test_sprites.py`` / ``test_textimage.py`` /
``test_contact_sheet.py`` で見ているので、ここは HTTP の入り口
（登録されるライブラリ項目・エラーの移し方・外部 API との対応）だけを見る。
"""

import asyncio
import shutil
from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from app import autotag, comfy, config, db, jobs, library
from app.main import app
from app.routers import assets as assets_router
from tests.test_library import _insert_job

KEY = "media-tools-test-key"

has_ffmpeg = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
    reason="ffmpeg / ffprobe が無い環境ではコマを抜けない",
)


async def _no_llm(text: str) -> tuple[str, list[str]]:
    return "", []


@pytest.fixture
def env(tmp_path, monkeypatch):
    """DB・assets・library・outputs をテスト用ディレクトリに閉じ込めたクライアント。"""
    assets = tmp_path / "assets"
    lib = tmp_path / "library"
    outputs = tmp_path / "outputs"
    (assets / "image").mkdir(parents=True)
    lib.mkdir()
    outputs.mkdir()

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(library, "LIBRARY_DIR", lib)
    monkeypatch.setattr(jobs, "LIBRARY_DIR", lib)
    monkeypatch.setattr(jobs, "ASSETS_DIR", assets)
    monkeypatch.setattr(assets_router, "ASSETS_DIR", assets)

    from app import media_ref

    monkeypatch.setattr(
        media_ref,
        "URL_ROOTS",
        {"/outputs/": outputs, "/library/": lib, "/assets/": assets},
    )

    async def offline():
        raise comfy.ComfyError("ComfyUI is down")

    monkeypatch.setattr(comfy, "get_object_info", lambda *a, **k: offline())
    monkeypatch.setattr(autotag, "describe", _no_llm)
    config.update_settings({"external_api_key": KEY})

    with TestClient(app) as client:
        yield type(
            "Env",
            (),
            {
                "client": client,
                "library": lib,
                "assets": assets,
                "outputs": outputs,
                "tmp": tmp_path,
            },
        )


def ring_png(background=(0, 0, 0), ink=(255, 255, 255)) -> bytes:
    """外周が ``ink``、内側にもう一度 ``background`` の穴がある的（まと）。"""
    image = Image.new("RGB", (120, 120), background)
    draw = ImageDraw.Draw(image)
    draw.rectangle([20, 20, 99, 99], fill=ink)
    draw.rectangle([50, 50, 69, 69], fill=background)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def upload_image(env, name: str = "logo.png", data: bytes | None = None) -> dict:
    response = env.client.post(
        "/api/library/image",
        files={"file": (name, data or ring_png(), "image/png")},
    )
    assert response.status_code == 201, response.text
    return response.json()


def stored(env, item: dict) -> Path:
    return Path(item["path"])


# --------------------------------------------------------------------------
# 透過キー
# --------------------------------------------------------------------------

def test_keying_a_library_item_adds_a_new_sprite(env):
    origin = upload_image(env)
    response = env.client.post(f"/api/library/{origin['id']}/key", json={})
    assert response.status_code == 201, response.text
    sprite = response.json()

    # 元の素材は残ったまま、別の 1 件が増える
    assert sprite["id"] != origin["id"]
    assert stored(env, origin).is_file()
    assert sprite["source"] == "sprite"
    assert library.SPRITE_TAG in sprite["tags"]
    assert sprite["url"].startswith("/library/image/")
    assert sprite["name"].endswith("（スプライト）")

    keyed = Image.open(stored(env, sprite)).convert("RGBA")
    alpha = keyed.getchannel("A")
    # トリム済みなので元より小さく、外は透明・内側の穴は残っている
    assert keyed.size < (120, 120)
    assert alpha.getpixel((keyed.width // 2, keyed.height // 2)) == 255


def test_keying_takes_the_method_and_the_name(env):
    origin = upload_image(env, "green.png", data=_solid_with_box())
    response = env.client.post(
        f"/api/library/{origin['id']}/key",
        json={
            "method": "chroma",
            "color": "#00ff00",
            "trim": False,
            "name": "決め台詞",
            "tags": ["ban"],
            "category": "prop",
        },
    )
    assert response.status_code == 201, response.text
    sprite = response.json()
    assert sprite["name"] == "決め台詞"
    assert sprite["category"] == "prop"
    assert sprite["tags"] == ["ban", library.SPRITE_TAG]
    assert Image.open(stored(env, sprite)).size == (100, 100)


def _solid_with_box() -> bytes:
    image = Image.new("RGB", (100, 100), (0, 255, 0))
    ImageDraw.Draw(image).rectangle([30, 30, 69, 69], fill=(220, 20, 40))
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def test_keying_a_video_or_a_missing_item_is_refused(env):
    video = env.client.post(
        "/api/library/video",
        files={"file": ("clip.mp4", b"not really", "video/mp4")},
    ).json()
    assert env.client.post(f"/api/library/{video['id']}/key", json={}).status_code == 400
    assert env.client.post("/api/library/ghost/key", json={}).status_code == 404


def test_an_unreadable_source_and_a_bad_method_are_400(env):
    broken = env.client.post(
        "/api/library/image",
        files={"file": ("broken.png", b"not a png", "image/png")},
    ).json()
    assert env.client.post(f"/api/library/{broken['id']}/key", json={}).status_code == 400

    origin = upload_image(env)
    bad = env.client.post(
        f"/api/library/{origin['id']}/key", json={"method": "chroma", "color": "nope"}
    )
    assert bad.status_code == 400


def test_keying_a_job_output_directly(env):
    path = env.outputs / "job1" / "image.png"
    path.parent.mkdir(parents=True)
    path.write_bytes(ring_png())
    asyncio.run(_insert_job("job1", image_path=str(path), nsfw=1))

    response = env.client.post(
        "/api/library/key-from-job", json={"job_id": "job1", "source": "image"}
    )
    assert response.status_code == 201, response.text
    sprite = response.json()
    assert sprite["source_job_id"] == "job1"
    assert sprite["source"] == "sprite"
    # NSFW は元ジョブから引き継ぐ（from-job と同じ）
    assert sprite["nsfw"] is True and sprite["nsfw_source"] == "auto"

    # 音声・動画の出力は抜けない
    assert env.client.post(
        "/api/library/key-from-job", json={"job_id": "job1", "source": "video"}
    ).status_code == 400
    assert env.client.post(
        "/api/library/key-from-job", json={"job_id": "ghost", "source": "image"}
    ).status_code == 404


def test_keying_a_media_ref(env):
    """``POST /library/key`` は ``MediaRef`` で指した画像を抜く（棚の外でもよい）。"""
    path = env.assets / "image" / "logo.png"
    path.write_bytes(ring_png())

    response = env.client.post(
        "/api/library/key", json={"source": {"path": "/assets/image/logo.png"}}
    )
    assert response.status_code == 201, response.text
    sprite = response.json()
    assert sprite["source"] == "sprite"
    assert library.SPRITE_TAG in sprite["tags"]
    assert sprite["name"] == "logo（スプライト）"
    # 元の素材はそのまま（コピーではなく、抜いた PNG が別に増える）
    assert path.is_file()

    # ジョブの出力も同じ入り口で抜ける（NSFW と元ジョブを引き継ぐ）
    output = env.outputs / "job9" / "image.png"
    output.parent.mkdir(parents=True)
    output.write_bytes(ring_png())
    asyncio.run(_insert_job("job9", image_path=str(output), nsfw=1))
    from_job = env.client.post(
        "/api/library/key", json={"source": {"job_id": "job9", "source": "image"}}
    )
    assert from_job.status_code == 201, from_job.text
    assert from_job.json()["source_job_id"] == "job9"
    assert from_job.json()["nsfw"] is True


def test_keying_a_media_ref_refuses_the_outside_and_the_missing(env):
    # 置き場の外は開かない（media_ref の関門）
    assert env.client.post(
        "/api/library/key", json={"source": {"path": "/etc/passwd"}}
    ).status_code == 400
    # 指定が無い / 2 つある
    assert env.client.post(
        "/api/library/key", json={"source": {}}
    ).status_code == 400
    assert env.client.post(
        "/api/library/key", json={"source": {"job_id": "a", "item_id": "b"}}
    ).status_code == 400
    assert env.client.post(
        "/api/library/key", json={"source": {"item_id": "ghost"}}
    ).status_code == 404


def test_flatten_repaints_the_sprite_in_one_colour(env):
    """``flatten`` は不透明部分を単色に塗る（白抜きロゴ用）。"""
    origin = upload_image(env, "mark.png", data=ring_png(ink=(220, 40, 40)))
    response = env.client.post(
        f"/api/library/{origin['id']}/key",
        json={"trim": False, "flatten": "#ffffff"},
    )
    assert response.status_code == 201, response.text
    keyed = Image.open(stored(env, response.json())).convert("RGBA")
    assert keyed.getpixel((30, 30)) == (255, 255, 255, 255)
    assert keyed.getchannel("A").getpixel((2, 2)) == 0

    assert env.client.post(
        f"/api/library/{origin['id']}/key", json={"flatten": "nope"}
    ).status_code == 400


def test_uploading_an_image_from_multipart(env):
    """``POST /library/image`` は手持ちの画像をそのまま棚に入れる。"""
    response = env.client.post(
        "/api/v1/library/image",
        files={"file": ("logo.png", ring_png(), "image/png")},
        data={"name": "ロゴ", "tags": "logo, ban", "nsfw": "true"},
        headers={"X-API-Key": KEY},
    )
    assert response.status_code == 201, response.text
    item = response.json()
    assert item["kind"] == "image"
    assert item["name"] == "ロゴ"
    assert item["tags"] == ["logo", "ban"]
    assert item["nsfw"] is True and item["nsfw_source"] == "manual"
    assert item["url"].startswith("/library/image/")
    assert stored(env, item).is_file()

    # そのまま抜ける（SKILL §10 の「手持ちの PNG」の段取り）
    keyed = env.client.post(
        f"/api/v1/library/{item['id']}/key", json={}, headers={"X-API-Key": KEY}
    )
    assert keyed.status_code == 201, keyed.text
    assert keyed.json()["nsfw"] is True

    # 鍵が無ければ 401、画像でない拡張子は 400
    assert env.client.post(
        "/api/v1/library/image", files={"file": ("a.png", ring_png(), "image/png")}
    ).status_code == 401
    assert env.client.post(
        "/api/v1/library/image",
        files={"file": ("clip.mp4", b"nope", "video/mp4")},
        headers={"X-API-Key": KEY},
    ).status_code == 400


def test_the_external_api_exposes_the_key_from_a_source(env):
    path = env.assets / "image" / "ext.png"
    path.write_bytes(ring_png())
    assert env.client.post(
        "/api/v1/library/key", json={"source": {"path": "/assets/image/ext.png"}}
    ).status_code == 401
    response = env.client.post(
        "/api/v1/library/key",
        json={"source": {"path": "/assets/image/ext.png"}, "flatten": "#00ffff"},
        headers={"X-API-Key": KEY},
    )
    assert response.status_code == 201, response.text
    assert response.json()["source"] == "sprite"


def test_the_external_api_exposes_the_same_key_endpoint(env):
    origin = upload_image(env)
    unauthorised = env.client.post(f"/api/v1/library/{origin['id']}/key", json={})
    assert unauthorised.status_code == 401
    response = env.client.post(
        f"/api/v1/library/{origin['id']}/key",
        json={"method": "black"},
        headers={"X-API-Key": KEY},
    )
    assert response.status_code == 201, response.text
    assert response.json()["source"] == "sprite"


# --------------------------------------------------------------------------
# フォント画像
# --------------------------------------------------------------------------

def test_the_font_list_names_a_default(env):
    response = env.client.get("/api/images/text/fonts")
    assert response.status_code == 200, response.text
    body = response.json()
    assert isinstance(body["fonts"], list)
    if body["fonts"]:
        assert body["default"]
        assert {"name", "family", "path", "index"} <= set(body["fonts"][0])


def test_a_text_image_becomes_a_library_item(env):
    response = env.client.post(
        "/api/images/text",
        json={"text": "BAN\nBAN", "size": 64, "color": "#ffffff",
              "outline": {"color": "#000000", "width": 4}},
    )
    assert response.status_code == 201, response.text
    item = response.json()
    assert item["source"] == "text"
    assert library.TEXT_TAG in item["tags"]
    assert item["name"].startswith("BAN")

    image = Image.open(stored(env, item)).convert("RGBA")
    assert image.getpixel((0, 0))[3] == 0  # 既定の背景は透明
    assert image.getchannel("A").getextrema()[1] == 255


def test_bad_text_requests_are_400(env):
    assert env.client.post("/api/images/text", json={"text": "  "}).status_code == 400
    assert env.client.post(
        "/api/images/text", json={"text": "A", "font": "No Such Font"}
    ).status_code == 400
    assert env.client.post(
        "/api/images/text", json={"text": "A", "size": 99999}
    ).status_code == 400


def test_the_external_api_exposes_the_text_endpoint(env):
    assert env.client.post("/api/v1/images/text", json={"text": "A"}).status_code == 401
    response = env.client.post(
        "/api/v1/images/text", json={"text": "A"}, headers={"X-API-Key": KEY}
    )
    assert response.status_code == 201, response.text


# --------------------------------------------------------------------------
# コンタクトシート
# --------------------------------------------------------------------------

def test_the_source_must_name_exactly_one_thing(env):
    assert env.client.post(
        "/api/videos/contact-sheet", json={"source": {}}
    ).status_code == 400
    assert env.client.post(
        "/api/videos/contact-sheet",
        json={"source": {"job_id": "a", "item_id": "b"}},
    ).status_code == 400
    assert env.client.post(
        "/api/videos/contact-sheet", json={"source": {"item_id": "ghost"}}
    ).status_code == 404
    # 置き場の外は開かない
    assert env.client.post(
        "/api/videos/contact-sheet", json={"source": {"path": "/etc/passwd"}}
    ).status_code == 400


@has_ffmpeg
def test_a_contact_sheet_is_built_from_a_video(env):
    video = env.outputs / "job1" / "video.mp4"
    video.parent.mkdir(parents=True)
    asyncio.run(_testsrc(video))

    response = env.client.post(
        "/api/videos/contact-sheet",
        json={
            "source": {"path": "/outputs/job1/video.mp4"},
            "seconds": [0.5, 1.5, 2.5, 3.5],
            "columns": 2,
            "width": 160,
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["seconds"] == [0.5, 1.5, 2.5, 3.5]
    item = body["item"]
    assert item["source"] == "contact-sheet"
    assert library.CONTACT_SHEET_TAG in item["tags"]

    sheet = Image.open(stored(env, item))
    assert sheet.format == "JPEG"
    assert sheet.width == 2 * 160


@has_ffmpeg
def test_a_contact_sheet_can_start_from_a_job(env):
    video = env.outputs / "job2" / "video.mp4"
    video.parent.mkdir(parents=True)
    asyncio.run(_testsrc(video))
    asyncio.run(_insert_job("job2", video_path=str(video)))

    response = env.client.post(
        "/api/v1/videos/contact-sheet",
        json={"source": {"job_id": "job2"}, "frames": [0, 24], "columns": 2},
        headers={"X-API-Key": KEY},
    )
    assert response.status_code == 201, response.text
    assert response.json()["seconds"] == [0.0, 1.0]


async def _testsrc(dest: Path) -> None:
    process = await asyncio.create_subprocess_exec(
        "ffmpeg", "-v", "error", "-y",
        "-f", "lavfi", "-i", "testsrc=size=320x180:rate=24:duration=4",
        "-pix_fmt", "yuv420p", str(dest),
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await process.communicate()
    assert process.returncode == 0, stderr.decode()
