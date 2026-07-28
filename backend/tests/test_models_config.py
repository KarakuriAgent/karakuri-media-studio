"""Model file name overrides: GET/PUT /api/models (SPEC §3.3)."""

import pytest
from fastapi.testclient import TestClient

from app import config
from app.main import app
from app.workflow import model_fields

UNET_KEY = "krea2_turbo/30:10.unet_name"


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A client whose settings live in a throwaway config.json."""
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(config, "_settings", None)
    yield TestClient(app)
    config._settings = None


def by_key(rows: list[dict]) -> dict[str, dict]:
    return {row["key"]: row for row in rows}


def test_get_returns_defaults(client):
    rows = client.get("/api/models").json()
    assert len(rows) == len(model_fields())
    unet = by_key(rows)[UNET_KEY]
    assert unet["class_type"] == "UNETLoader"
    assert unet["node_id"] == "30:10"
    assert unet["workflow_id"] == "krea2_turbo"
    assert unet["workflow_label"]
    assert unet["field"] == "unet_name"
    assert unet["value"] == unet["default"] != ""
    assert unet["overridden"] is False
    assert "krea2_turbo/30:61:62.lora_name" not in by_key(rows)


def test_put_saves_override_and_persists(client):
    rows = client.put("/api/models", json={"overrides": {UNET_KEY: "mine.safetensors"}})
    assert rows.status_code == 200
    unet = by_key(rows.json())[UNET_KEY]
    assert unet["value"] == "mine.safetensors"
    assert unet["overridden"] is True
    assert unet["default"] != "mine.safetensors"

    # persisted for the next request (and for the job runner)
    assert by_key(client.get("/api/models").json())[UNET_KEY]["value"] == "mine.safetensors"
    assert config.load_settings().model_overrides == {UNET_KEY: "mine.safetensors"}


def test_put_drops_default_valued_and_empty_entries(client):
    default = by_key(client.get("/api/models").json())[UNET_KEY]["default"]
    client.put("/api/models", json={"overrides": {UNET_KEY: "mine.safetensors"}})

    other = "tx2_3_i2v/320:316.ckpt_name"
    rows = client.put(
        "/api/models",
        json={"overrides": {UNET_KEY: default, other: "  "}},
    ).json()
    assert by_key(rows)[UNET_KEY]["overridden"] is False
    assert by_key(rows)[UNET_KEY]["value"] == default
    assert config.load_settings().model_overrides == {}


def test_put_rejects_unknown_key(client):
    response = client.put("/api/models", json={"overrides": {"nope.field": "x"}})
    assert response.status_code == 422
    assert "nope.field" in response.json()["detail"]
    assert config.load_settings().model_overrides == {}


def test_put_rejects_the_dynamic_lora_node(client):
    response = client.put("/api/models", json={"overrides": {"krea2_turbo/30:61:62.lora_name": "x"}})
    assert response.status_code == 422
