"""Comfy Cloud translation of GET /api/jobs/{prompt_id} into history shape."""

import httpx
import pytest

from app import comfy


@pytest.fixture
def cloud(monkeypatch):
    """Force cloud mode and let each test stub the HTTP payload."""
    monkeypatch.setattr(comfy, "_api_prefix", lambda: "/api")

    calls: dict = {}

    def install(payload, status_code=200):
        async def fake_request(method, path, **kwargs):
            calls["method"] = method
            calls["path"] = path
            response = httpx.Response(status_code, json=payload)
            if status_code >= 400:
                raise comfy.ComfyError(
                    f"ComfyUI {method} {path} failed: HTTP {status_code}"
                )
            return response

        monkeypatch.setattr(comfy, "_request", fake_request)
        return calls

    return install


async def test_cloud_uses_jobs_endpoint(cloud):
    calls = cloud({"status": "pending"})
    await comfy.get_history("p1")
    assert calls["path"] == "/api/jobs/p1"


async def test_pending_keeps_polling_shape(cloud):
    cloud({"status": "in_progress"})
    entry = await comfy.get_history("p1")
    assert "outputs" not in entry
    assert entry["status"]["status_str"] == "in_progress"


async def test_completed_returns_outputs(cloud):
    outputs = {"75": {"videos": [{"filename": "v.mp4", "subfolder": "", "type": "output"}]}}
    cloud({"status": "completed", "outputs": outputs})
    entry = await comfy.get_history("p1")
    assert entry["outputs"] == outputs
    assert entry["status"]["status_str"] == "success"


async def test_completed_wrapped_in_job_key(cloud):
    outputs = {"393": {"images": [{"filename": "i.png", "subfolder": "", "type": "output"}]}}
    cloud({"job": {"status": "completed", "outputs": outputs}})
    entry = await comfy.get_history("p1")
    assert entry["outputs"] == outputs


async def test_failed_maps_to_history_error(cloud):
    cloud({"status": "failed", "error": "out of credits"})
    entry = await comfy.get_history("p1")
    status = entry["status"]
    assert status["status_str"] == "error"
    assert "out of credits" in status["messages"][0][1]["exception_message"]


async def test_completed_without_outputs_raises(cloud):
    cloud({"status": "completed"})
    with pytest.raises(comfy.ComfyError, match="no outputs"):
        await comfy.get_history("p1")
