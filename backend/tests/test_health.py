"""GET /api/health: ComfyUI reachability + class_type / manifest checks (SPEC §10-3)."""

import pytest
from fastapi.testclient import TestClient

from app import comfy, config, grok
from app.main import app
from app.models import HealthStatus, Settings
from app.workflow import all_required_class_types

REQUIRED = sorted(all_required_class_types())


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(config, "_settings", Settings(local_comfy_url="http://comfy:8188"))

    async def grok_ok() -> HealthStatus:
        return HealthStatus(status="ok")

    monkeypatch.setattr(grok, "check_grok", grok_ok)
    with TestClient(app) as test_client:
        yield test_client
    config._settings = None


def _object_info(monkeypatch, names) -> None:
    async def info(*args, **kwargs):
        return {name: {} for name in names}

    monkeypatch.setattr(comfy, "get_object_info", info)


def test_ok_when_every_class_type_exists(client, monkeypatch):
    _object_info(monkeypatch, REQUIRED)
    body = client.get("/api/health").json()
    assert body["comfyui"]["status"] == "ok"
    assert f"{len(REQUIRED)} node classes" in body["comfyui"]["detail"]


def test_missing_custom_nodes_are_listed(client, monkeypatch):
    _object_info(monkeypatch, [n for n in REQUIRED if n != "LTXVReferenceAudio"])
    status = client.get("/api/health").json()["comfyui"]
    assert status["status"] == "error"
    assert "LTXVReferenceAudio" in status["detail"]


def test_the_class_types_span_every_template(client, monkeypatch):
    """Which workflow a job will use is unknown up front, so the union is checked."""
    _object_info(monkeypatch, REQUIRED)
    assert {"MoGeInference", "LoadVideo", "LTXVReferenceAudio", "ResolutionSelector"} <= set(
        REQUIRED
    )


def test_unreachable_comfyui_is_reported(client, monkeypatch):
    async def down(*args, **kwargs):
        raise comfy.ComfyError("connection refused")

    monkeypatch.setattr(comfy, "get_object_info", down)
    status = client.get("/api/health").json()["comfyui"]
    assert status["status"] == "error"
    assert "connection refused" in status["detail"]


def test_a_manifest_mismatch_is_reported(client, monkeypatch):
    """A hand-edited workflow/*.json must surface here, not as a failed job."""
    from app.routers import health as health_router

    _object_info(monkeypatch, REQUIRED)

    def broken() -> None:
        from app.workflow import WorkflowError

        raise WorkflowError("workflow manifest mismatch: krea2_turbo.prompt: ...")

    monkeypatch.setattr(health_router, "validate_manifests", broken)
    status = client.get("/api/health").json()["comfyui"]
    assert status["status"] == "error"
    assert "manifest mismatch" in status["detail"]


def test_not_configured_without_a_comfy_url(client, monkeypatch):
    monkeypatch.setattr(config, "_settings", Settings(local_comfy_url=""))
    assert client.get("/api/health").json()["comfyui"]["status"] == "not_configured"
