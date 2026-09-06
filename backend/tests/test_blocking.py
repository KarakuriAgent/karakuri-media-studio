"""構図リファレンス動画（ブロッキング、SPEC §7.2）。

見るのは 4 つ: シーン定義の検証（上限・並び・id）、投影が幾何として正しいこと、
1 コマの描画、そして API（作成 → 再レンダ → プレビュー、外部 API 経路も 1 本）。
mp4 を焼くところだけ ffmpeg が要るので、無い環境では skip する。
"""

import shutil

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app import autotag, blocking, comfy, db, jobs, library
from app.main import app
from app.models import (
    BlockingCamera,
    BlockingCameraKeyframe,
    BlockingCameraMove,
    BlockingObject,
    BlockingObjectKeyframe,
    BlockingScene,
)
from app.routers import assets as assets_router

has_ffmpeg = pytest.mark.skipif(
    not shutil.which("ffmpeg"), reason="ffmpeg が無い環境では mp4 を焼けない"
)


def scene(**overrides) -> BlockingScene:
    """カメラ正面 5m に箱を 1 つ置いただけの最小のシーン。"""
    body = {
        "aspect_ratio": "16:9",
        "duration": 1.0,
        "camera": BlockingCamera(
            keyframes=[
                BlockingCameraKeyframe(
                    t=0.0, position=[0.0, 1.6, 5.0], look_at=[0.0, 1.6, 0.0]
                )
            ]
        ),
        "objects": [
            BlockingObject(
                id="hero",
                label="hero",
                shape="box",
                size=[1.0, 1.0, 1.0],
                keyframes=[BlockingObjectKeyframe(t=0.0, position=[0.0, 0.0, 0.0])],
            )
        ],
    }
    body.update(overrides)
    return BlockingScene(**body)


def obj(object_id: str, position, **overrides) -> BlockingObject:
    body = {
        "id": object_id,
        "shape": "box",
        "size": [1.0, 1.0, 1.0],
        "keyframes": [BlockingObjectKeyframe(t=0.0, position=list(position))],
    }
    body.update(overrides)
    return BlockingObject(**body)


# --------------------------------------------------------------------------
# シーン定義の検証
# --------------------------------------------------------------------------

def test_the_minimal_scene_is_accepted():
    prepared = blocking.prepare_scene(scene())
    assert prepared.objects[0].size == [1.0, 1.0, 1.0]


def test_a_missing_size_falls_back_to_the_shape_default():
    prepared = blocking.prepare_scene(
        scene(objects=[obj("hero", (0, 0, 0), shape="figure", size=None)])
    )
    assert tuple(prepared.objects[0].size) == blocking.DEFAULT_SIZES["figure"]


@pytest.mark.parametrize("duration", [0.1, 30.0])
def test_the_duration_has_limits(duration):
    with pytest.raises(blocking.BlockingError):
        blocking.prepare_scene(scene(duration=duration))


def test_there_is_a_limit_on_objects():
    many = [obj(f"o{index}", (index, 0, 0)) for index in range(blocking.MAX_OBJECTS + 1)]
    with pytest.raises(blocking.BlockingError):
        blocking.prepare_scene(scene(objects=many))
    with pytest.raises(blocking.BlockingError):
        blocking.prepare_scene(scene(objects=[]))


def test_keyframe_times_must_ascend():
    broken = obj("hero", (0, 0, 0))
    broken.keyframes = [
        BlockingObjectKeyframe(t=0.5, position=[0, 0, 0]),
        BlockingObjectKeyframe(t=0.2, position=[1, 0, 0]),
    ]
    with pytest.raises(blocking.BlockingError):
        blocking.prepare_scene(scene(objects=[broken]))


def test_there_is_a_limit_on_keyframes():
    crowded = obj("hero", (0, 0, 0))
    crowded.keyframes = [
        BlockingObjectKeyframe(t=index / 100, position=[0, 0, 0])
        for index in range(blocking.MAX_KEYFRAMES + 1)
    ]
    with pytest.raises(blocking.BlockingError):
        blocking.prepare_scene(scene(objects=crowded and [crowded]))


def test_object_ids_are_unique():
    with pytest.raises(blocking.BlockingError):
        blocking.prepare_scene(
            scene(objects=[obj("hero", (0, 0, 0)), obj("hero", (2, 0, 0))])
        )


def test_the_camera_must_start_at_zero():
    late = BlockingCamera(
        keyframes=[
            BlockingCameraKeyframe(t=0.4, position=[0, 1.6, 5], look_at=[0, 1.6, 0])
        ]
    )
    with pytest.raises(blocking.BlockingError):
        blocking.prepare_scene(scene(camera=late))


def test_an_unknown_shape_is_rejected_by_the_schema():
    with pytest.raises(ValidationError):
        BlockingObject(id="hero", shape="pyramid")


def test_an_unknown_aspect_ratio_is_rejected():
    with pytest.raises(blocking.BlockingError):
        blocking.resolution("5:2")


def test_a_follow_move_needs_an_existing_target():
    moving = BlockingCamera(
        keyframes=[BlockingCameraKeyframe(position=[0, 1.6, 5], look_at=[0, 1.6, 0])],
        move=BlockingCameraMove(type="follow", target="nobody"),
    )
    with pytest.raises(blocking.BlockingError):
        blocking.prepare_scene(scene(camera=moving))


def test_a_preset_expands_into_keyframes_and_is_idempotent():
    pushing = BlockingCamera(
        keyframes=[BlockingCameraKeyframe(position=[0, 1.6, 5], look_at=[0, 1.6, 0])],
        move=BlockingCameraMove(type="push_in", amount=2.0),
    )
    prepared = blocking.prepare_scene(scene(camera=pushing))
    keyframes = prepared.camera.keyframes
    assert [keyframe.t for keyframe in keyframes] == [0.0, 1.0]
    # カメラは 5m から 3m へ寄る（注視点はそのまま）
    assert keyframes[-1].position == pytest.approx([0.0, 1.6, 3.0])
    assert keyframes[-1].look_at == pytest.approx([0.0, 1.6, 0.0])
    again = blocking.prepare_scene(prepared)
    assert again.camera.keyframes[-1].position == pytest.approx([0.0, 1.6, 3.0])


def test_a_preset_can_be_left_unexpanded():
    """``expand_move=False`` は書かれたキーフレームをそのまま残す（保存用）。"""
    pushing = BlockingCamera(
        keyframes=[BlockingCameraKeyframe(position=[0, 1.6, 5], look_at=[0, 1.6, 0])],
        move=BlockingCameraMove(type="push_in", amount=2.0),
    )
    kept = blocking.prepare_scene(scene(camera=pushing), expand_move=False)
    assert len(kept.camera.keyframes) == 1
    assert kept.camera.keyframes[0].position == pytest.approx([0.0, 1.6, 5.0])
    assert kept.camera.move is not None and kept.camera.move.type == "push_in"
    # 検証と穴埋めは通っている（size は形状の既定で埋まる）
    assert kept.objects[0].size == pytest.approx([1.0, 1.0, 1.0])
    # 展開はいつでも掛け直せる
    assert len(blocking.prepare_scene(kept).camera.keyframes) == 2


# --------------------------------------------------------------------------
# 投影
# --------------------------------------------------------------------------

def test_an_object_straight_ahead_sits_on_the_centre_line():
    positions = blocking.screen_positions(scene(), 0.0)
    assert positions.width, positions.height == (768, 448)
    centre = positions.objects[0].center
    assert centre.x_percent == pytest.approx(50.0, abs=0.1)
    # カメラの高さ 1.6m にある箱の中心（y=0.5m）は画面の下半分
    assert centre.y_percent > 50.0


def test_the_further_object_is_the_smaller_one():
    near, far = obj("near", (0, 0, 0)), obj("far", (0, 0, -10))
    positions = blocking.screen_positions(scene(objects=[near, far]), 0.0)
    heights = {
        item.id: item.base.y_px - item.top.y_px for item in positions.objects
    }
    assert heights["near"] > heights["far"] > 0


def test_the_horizon_matches_the_camera_height_and_the_field_of_view():
    # 水平に構えたカメラでは、地平線はちょうど画面の中央に来る
    level = BlockingCamera(
        keyframes=[
            BlockingCameraKeyframe(position=[0, 1.6, 5], look_at=[0, 1.6, 0])
        ]
    )
    positions = blocking.screen_positions(scene(camera=level), 0.0)
    assert positions.horizon_y_percent == pytest.approx(50.0, abs=0.2)

    # 見下ろすと地平線は上へ上がる（下を向いた角度ぶん）
    down = BlockingCamera(
        keyframes=[BlockingCameraKeyframe(position=[0, 2.0, 5], look_at=[0, 0.0, 0])]
    )
    tilted = blocking.screen_positions(scene(camera=down), 0.0)
    assert tilted.horizon_y_percent is not None
    assert tilted.horizon_y_percent < 40.0


def test_the_floor_stays_below_the_horizon():
    positions = blocking.screen_positions(scene(), 0.0)
    assert positions.horizon_y_percent is not None
    assert positions.objects[0].base.y_percent > positions.horizon_y_percent


def test_facing_camera_turns_the_object_towards_the_lens():
    watcher = obj("hero", (3.0, 0.0, 0.0), shape="figure", facing="camera")
    prepared = blocking.prepare_scene(scene(objects=[watcher]))
    yaw = blocking.object_yaw(prepared, prepared.objects[0], 0.0)
    # カメラは (0, 1.6, 5)、対象は (3, 0, 0)。正面（+Z）から左へ振ると
    # レンズを向くので yaw は負（atan2(-3, 5) ≒ -31 度）
    assert yaw == pytest.approx(-30.96, abs=0.1)


# --------------------------------------------------------------------------
# 描画
# --------------------------------------------------------------------------

def test_a_frame_has_the_expected_size_and_colours():
    coloured = obj("hero", (0, 0, 0), color="#3050a0")
    sample = scene(objects=[coloured])
    frame = blocking.render_frame(sample, 0.0)
    assert frame.size == blocking.resolution("16:9")
    # 左上の隅は背景（低彩度のグレー）のまま
    assert frame.getpixel((0, 0)) == blocking.check_color(blocking.DEFAULT_BACKGROUND)
    # 箱の上端と中心の間（画面上の位置は screen_positions が知っている）は青い
    hero = blocking.screen_positions(sample, 0.0).objects[0]
    red, green, blue = frame.getpixel(
        (round(hero.center.x_px), round((hero.top.y_px + hero.center.y_px) / 2))
    )
    assert blue > red + 30 and blue > green + 20


def test_every_shape_draws_something():
    shapes = [
        obj(f"s{index}", (index * 1.5 - 3.0, 0, 0), shape=shape)
        for index, shape in enumerate(blocking.SHAPES)
    ]
    frame = blocking.render_frame(scene(objects=shapes), 0.0)
    assert len(frame.getcolors(maxcolors=1 << 16) or []) > 8


def test_the_aspect_ratio_decides_the_resolution():
    assert blocking.resolution("1:1") == (768, 768)
    assert blocking.resolution("9:16")[1] == blocking.LONG_EDGE
    for width, height in map(blocking.resolution, blocking.ASPECT_RATIOS):
        assert width % blocking.SIZE_STEP == 0 and height % blocking.SIZE_STEP == 0


# --------------------------------------------------------------------------
# 英文
# --------------------------------------------------------------------------

def test_the_location_map_carries_the_screen_positions():
    text = blocking.location_map(scene())
    assert "LOCATION MAP" in text and "CAMERA" in text
    assert "hero at x 50%" in text


def test_the_reference_note_names_the_video_and_stays_weak():
    note = blocking.reference_note(3)
    assert note.startswith("<Video 3> (camera path and blocking only): weak_reference")
    assert "previz" in note


# --------------------------------------------------------------------------
# mp4
# --------------------------------------------------------------------------

@has_ffmpeg
def test_the_video_has_one_frame_per_24th_of_a_second(tmp_path):
    out = tmp_path / "blocking.mp4"
    result = blocking.render_video(scene(duration=0.5), out)
    assert result.frames == round(0.5 * blocking.FPS)
    assert result.fps == 24
    assert out.is_file() and out.stat().st_size > 0


def test_a_missing_ffmpeg_is_reported_as_a_plain_error(tmp_path, monkeypatch):
    monkeypatch.setattr(blocking, "FFMPEG", "ffmpeg-does-not-exist")
    with pytest.raises(blocking.BlockingError):
        blocking.render_video(scene(duration=0.5), tmp_path / "nope.mp4")


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------

async def _no_llm(text: str) -> tuple[str, list[str]]:
    return "", []


@pytest.fixture
def env(tmp_path, monkeypatch):
    """DB と library をテスト用ディレクトリに閉じ込めたクライアント。"""
    assets = tmp_path / "assets"
    lib = tmp_path / "library"
    (assets / "image").mkdir(parents=True)
    lib.mkdir()

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(library, "LIBRARY_DIR", lib)
    monkeypatch.setattr(jobs, "LIBRARY_DIR", lib)
    monkeypatch.setattr(jobs, "ASSETS_DIR", assets)
    monkeypatch.setattr(assets_router, "ASSETS_DIR", assets)
    monkeypatch.setattr(autotag, "describe", _no_llm)

    async def offline():
        raise comfy.ComfyError("ComfyUI is down")

    monkeypatch.setattr(comfy, "get_object_info", lambda *a, **k: offline())

    with TestClient(app) as client:
        yield type("Env", (), {"client": client, "library": lib, "tmp": tmp_path})


def body(**overrides) -> dict:
    payload = {"scene": scene(**overrides).model_dump(mode="json")}
    return payload


@has_ffmpeg
def test_creating_a_blocking_video_registers_a_library_item(env):
    response = env.client.post(
        "/api/library/blocking",
        json={**body(duration=0.5), "name": "構図メモ", "tags": ["previz"]},
    )
    assert response.status_code == 201, response.text
    result = response.json()
    item = result["item"]
    assert item["kind"] == "video" and item["source"] == "blocking"
    assert item["blocking_version"] == 1
    assert item["blocking"]["objects"][0]["id"] == "hero"
    assert "blocking" in item["tags"] and "previz" in item["tags"]
    assert "LOCATION MAP" in result["location_map"]
    assert "weak_reference" in result["reference_note"]
    assert result["frames"] == round(0.5 * blocking.FPS)
    assert (env.library / "video" / item["path"].rsplit("/", 1)[-1]).is_file()


@has_ffmpeg
def test_rerendering_keeps_the_item_and_bumps_the_version(env):
    created = env.client.post("/api/library/blocking", json=body(duration=0.5))
    item = created.json()["item"]
    path = env.library / "video" / item["path"].rsplit("/", 1)[-1]
    before = path.read_bytes()

    moved = body(duration=0.5, objects=[obj("hero", (2.0, 0.0, -3.0))])
    response = env.client.post(f"/api/library/{item['id']}/blocking", json=moved)
    assert response.status_code == 200, response.text
    updated = response.json()["item"]
    assert updated["id"] == item["id"] and updated["path"] == item["path"]
    assert updated["blocking_version"] == 2
    assert updated["blocking"]["objects"][0]["keyframes"][0]["position"][0] == 2.0
    assert path.read_bytes() != before


def pushing_camera() -> BlockingCamera:
    """プリセット（push_in）を書いたカメラ。"""
    return BlockingCamera(
        keyframes=[BlockingCameraKeyframe(position=[0, 1.6, 5], look_at=[0, 1.6, 0])],
        move=BlockingCameraMove(type="push_in", amount=2.0),
    )


@has_ffmpeg
def test_the_saved_scene_keeps_the_camera_move_as_written(env):
    """保存するのは書かれたシーン定義（プリセットの展開結果は残さない）。"""
    payload = body(duration=0.5, camera=pushing_camera())
    created = env.client.post("/api/library/blocking", json=payload)
    assert created.status_code == 201, created.text
    result = created.json()
    item = result["item"]
    saved = item["blocking"]["camera"]
    assert len(saved["keyframes"]) == 1
    assert saved["keyframes"][0]["position"] == [0.0, 1.6, 5.0]
    assert saved["move"]["type"] == "push_in" and saved["move"]["amount"] == 2.0
    # 焼いた絵と英文はプリセットを展開したもの（保存の形とは別）
    assert "pushes in 2.00m" in result["location_map"]

    # 作り直しても同じ（展開結果を保存し直さない）
    again = env.client.post(f"/api/library/{item['id']}/blocking", json=payload)
    assert again.status_code == 200, again.text
    updated = again.json()["item"]
    assert updated["blocking_version"] == 2
    assert len(updated["blocking"]["camera"]["keyframes"]) == 1
    assert updated["blocking"]["camera"]["move"]["type"] == "push_in"


@has_ffmpeg
def test_a_failed_rerender_keeps_the_item_and_leaves_no_staging_file(env, monkeypatch):
    """焼き直しに失敗しても、途中のファイルも版番号も残さない。"""
    created = env.client.post("/api/library/blocking", json=body(duration=0.5))
    item = created.json()["item"]
    before = (env.library / "video" / item["path"].rsplit("/", 1)[-1]).read_bytes()

    monkeypatch.setattr(blocking, "FFMPEG", "ffmpeg-does-not-exist")
    response = env.client.post(
        f"/api/library/{item['id']}/blocking", json=body(duration=0.5)
    )
    assert response.status_code == 400
    assert list((env.library / "video").glob("*.rendering.mp4")) == []
    kept = env.client.get("/api/library").json()["items"][0]
    assert kept["id"] == item["id"] and kept["blocking_version"] == 1
    assert (env.library / "video" / item["path"].rsplit("/", 1)[-1]).read_bytes() == before


def test_rerendering_a_plain_item_is_a_bad_request(env):
    upload = env.client.post(
        "/api/library/upload",
        files={"file": ("clip.mp4", b"not really a video", "video/mp4")},
    )
    assert upload.status_code == 201, upload.text
    response = env.client.post(
        f"/api/library/{upload.json()['id']}/blocking", json=body(duration=0.5)
    )
    assert response.status_code == 400
    missing = env.client.post("/api/library/nope/blocking", json=body(duration=0.5))
    assert missing.status_code == 404


def test_the_preview_returns_a_png_without_saving_anything(env):
    response = env.client.post(
        "/api/library/blocking/preview", json={**body(), "t": 0.25}
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "image/png"
    assert response.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert env.client.get("/api/library").json()["total"] == 0


def test_the_location_map_endpoint_renders_nothing(env):
    response = env.client.post("/api/library/blocking/location-map", json=body())
    assert response.status_code == 200, response.text
    result = response.json()
    assert "hero at x 50%" in result["location_map"]
    assert result["reference_note"].startswith("<Video 1>")
    assert [round(sample["t"], 2) for sample in result["positions"]] == [0.0, 0.5, 1.0]


def test_a_broken_scene_is_a_bad_request(env):
    response = env.client.post(
        "/api/library/blocking/location-map", json=body(duration=99.0)
    )
    assert response.status_code == 400
    assert "duration" in response.json()["detail"]


def test_the_capabilities_carry_the_blocking_limits(env):
    response = env.client.get("/api/studio/capabilities")
    assert response.status_code == 200, response.text
    limits = response.json()["blocking"]
    assert limits["max_objects"] == blocking.MAX_OBJECTS
    assert limits["fps"] == 24
    assert "figure" in limits["shapes"] and "arc_left" in limits["camera_moves"]


@has_ffmpeg
def test_the_external_api_can_make_one_too(env):
    key = "blocking-test-key"
    assert env.client.put(
        "/api/settings", json={"external_api_key": key}
    ).status_code == 200
    response = env.client.post(
        "/api/v1/library/blocking",
        headers={"X-API-Key": key},
        json=body(duration=0.5),
    )
    assert response.status_code == 201, response.text
    assert response.json()["item"]["blocking_version"] == 1
    assert env.client.post("/api/v1/library/blocking", json=body()).status_code == 401
