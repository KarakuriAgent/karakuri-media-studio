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
