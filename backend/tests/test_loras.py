"""LoRA レジストリとサンプル画像 API のテスト。"""

import pytest
from fastapi.testclient import TestClient

from app import config, db, lora_samples
from app.main import app

PAYLOAD = {
    "display_name": "サクラ",
    "lora_name": "sakura.safetensors",
    "trigger_word": "sakura",
}


@pytest.fixture
def env(tmp_path, monkeypatch):
    assets = tmp_path / "assets"
    assets.mkdir()
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(lora_samples, "ASSETS_DIR", assets)
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(config, "_settings", None)
    with TestClient(app) as client:
        yield type("Env", (), {"client": client, "assets": assets})
    config._settings = None


def create_lora(env) -> dict:
    response = env.client.post("/api/loras", json=PAYLOAD)
    assert response.status_code == 201, response.text
    return response.json()


def upload(env, lora_id: int, name: str = "face.png", body: bytes = b"img"):
    return env.client.post(
        f"/api/loras/{lora_id}/samples", files={"file": (name, body, "image/png")}
    )


async def test_an_existing_registry_is_migrated_to_image(tmp_path, monkeypatch):
    """A DB written before the split keeps its rows, all of them 画像用."""
    import aiosqlite

    path = tmp_path / "old.db"
    monkeypatch.setattr(db, "DB_PATH", path)
    async with aiosqlite.connect(path) as conn:
        await conn.execute(
            "CREATE TABLE loras (id INTEGER PRIMARY KEY, display_name TEXT NOT NULL,"
            " lora_name TEXT NOT NULL, trigger_word TEXT NOT NULL,"
            " default_strength REAL DEFAULT 1.0, default_audio TEXT,"
            " sort_order INTEGER DEFAULT 0)"
        )
        await conn.execute(
            "INSERT INTO loras (display_name, lora_name, trigger_word)"
            " VALUES ('サクラ', 'sakura.safetensors', 'sakura')"
        )
        await conn.commit()

    await db.init_db()

    async with db.get_db() as conn:
        async with conn.execute("SELECT * FROM loras") as cur:
            rows = [dict(row) for row in await cur.fetchall()]
    assert [row["target"] for row in rows] == ["image"]
    # 画像ワークフローが選択式になる前の行はすべて krea2 用だった
    assert [row["family"] for row in rows] == ["krea2"]
    assert rows[0]["lora_name"] == "sakura.safetensors"


async def test_a_registry_with_target_but_no_family_is_backfilled(tmp_path, monkeypatch):
    """target 分割済み・family 追加前の DB も krea2 として引き継がれる。"""
    import aiosqlite

    path = tmp_path / "old.db"
    monkeypatch.setattr(db, "DB_PATH", path)
    async with aiosqlite.connect(path) as conn:
        await conn.execute(
            "CREATE TABLE loras (id INTEGER PRIMARY KEY, display_name TEXT NOT NULL,"
            " lora_name TEXT NOT NULL, trigger_word TEXT NOT NULL,"
            " default_strength REAL DEFAULT 1.0, default_audio TEXT,"
            " sort_order INTEGER DEFAULT 0,"
            " sample_images TEXT NOT NULL DEFAULT '[]',"
            " target TEXT NOT NULL DEFAULT 'image')"
        )
        await conn.execute(
            "INSERT INTO loras (display_name, lora_name, trigger_word, target)"
            " VALUES ('サクラ', 'sakura.safetensors', 'sakura', 'image'),"
            " ('スローモ', 'motion.safetensors', 'slowmo', 'video')"
        )
        await conn.commit()

    await db.init_db()

    async with db.get_db() as conn:
        async with conn.execute("SELECT * FROM loras ORDER BY id") as cur:
            rows = [dict(row) for row in await cur.fetchall()]
    assert [row["family"] for row in rows] == ["krea2", "krea2"]
    # …and the API reports the same
    assert lora_samples.row_to_lora(rows[0]).family == "krea2"


def test_a_lora_is_registered_for_the_image_stage_by_default(env):
    created = create_lora(env)
    assert created["target"] == "image"
    assert created["family"] == "krea2"


def test_a_lora_can_be_registered_for_another_image_family(env):
    response = env.client.post(
        "/api/loras",
        json={**PAYLOAD, "display_name": "ハナ", "family": "anima"},
    )
    assert response.status_code == 201, response.text
    lora = response.json()
    assert lora["family"] == "anima"
    assert env.client.get(f"/api/loras/{lora['id']}").json()["family"] == "anima"


def test_the_family_can_be_changed(env):
    lora = create_lora(env)
    updated = env.client.put(f"/api/loras/{lora['id']}", json={"family": "z-image"})
    assert updated.status_code == 200, updated.text
    assert updated.json()["family"] == "z-image"
    # an update that does not mention the family keeps it
    kept = env.client.put(f"/api/loras/{lora['id']}", json={"trigger_word": "x"})
    assert kept.json()["family"] == "z-image"


def test_a_video_lora_can_be_registered(env):
    response = env.client.post(
        "/api/loras",
        json={**PAYLOAD, "display_name": "スローモ", "target": "video"},
    )
    assert response.status_code == 201, response.text
    lora = response.json()
    assert lora["target"] == "video"
    # …and it survives the round trip through the DB
    assert env.client.get(f"/api/loras/{lora['id']}").json()["target"] == "video"


def test_the_target_can_be_changed(env):
    lora = create_lora(env)
    updated = env.client.put(f"/api/loras/{lora['id']}", json={"target": "video"})
    assert updated.status_code == 200, updated.text
    assert updated.json()["target"] == "video"
    # an update that does not mention the target keeps it
    kept = env.client.put(f"/api/loras/{lora['id']}", json={"trigger_word": "x"})
    assert kept.json()["target"] == "video"


def test_an_unknown_target_is_rejected(env):
    response = env.client.post("/api/loras", json={**PAYLOAD, "target": "audio"})
    assert response.status_code == 422


def test_a_video_lora_keeps_samples_and_defaults(env):
    lora = env.client.post(
        "/api/loras",
        json={**PAYLOAD, "target": "video", "default_strength": 0.7,
              "trigger_word": "slowmo"},
    ).json()
    result = upload(env, lora["id"]).json()
    assert len(result["sample_images"]) == 1
    assert result["default_strength"] == 0.7
    assert result["trigger_word"] == "slowmo"


def test_options_exposes_the_target_so_the_form_can_filter(env):
    create_lora(env)
    env.client.post(
        "/api/loras",
        json={**PAYLOAD, "display_name": "スローモ", "target": "video"},
    )
    loras = env.client.get("/api/options").json()["loras"]
    assert {lora["display_name"]: lora["target"] for lora in loras} == {
        "サクラ": "image",
        "スローモ": "video",
    }


def test_new_lora_has_no_samples(env):
    lora = create_lora(env)
    assert lora["sample_images"] == []


def test_upload_stores_the_file_and_lists_its_url(env):
    lora = create_lora(env)
    result = upload(env, lora["id"], "顔 サンプル.png").json()
    assert len(result["sample_images"]) == 1
    url = result["sample_images"][0]
    assert url.startswith(f"/assets/lora_samples/{lora['id']}/")
    # 日本語・空白はサニタイズされる
    name = url.rsplit("/", 1)[1]
    assert name.endswith(".png")
    assert " " not in name
    assert (env.assets / "lora_samples" / str(lora["id"]) / name).is_file()
    # GET でも同じ一覧が返る
    assert env.client.get(f"/api/loras/{lora['id']}").json()["sample_images"] == [url]


def test_upload_rejects_unknown_extension(env):
    lora = create_lora(env)
    response = env.client.post(
        f"/api/loras/{lora['id']}/samples",
        files={"file": ("note.txt", b"x", "text/plain")},
    )
    assert response.status_code == 400


def test_upload_to_a_missing_lora_is_404(env):
    assert upload(env, 999).status_code == 404


def test_delete_sample_removes_file_and_entry(env):
    lora = create_lora(env)
    url = upload(env, lora["id"]).json()["sample_images"][0]
    name = url.rsplit("/", 1)[1]
    result = env.client.delete(f"/api/loras/{lora['id']}/samples/{name}")
    assert result.status_code == 200
    assert result.json()["sample_images"] == []
    assert not (env.assets / "lora_samples" / str(lora["id"]) / name).exists()

    assert (
        env.client.delete(f"/api/loras/{lora['id']}/samples/{name}").status_code == 404
    )


def test_delete_lora_removes_the_samples_dir(env):
    lora = create_lora(env)
    upload(env, lora["id"])
    directory = env.assets / "lora_samples" / str(lora["id"])
    assert directory.is_dir()
    assert env.client.delete(f"/api/loras/{lora['id']}").status_code == 204
    assert not directory.exists()
