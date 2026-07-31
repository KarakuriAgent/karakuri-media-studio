"""ComfyUI に届かなかったときのエラー文言（SPEC §5 / §5.1）。

RunPod 運用では Pod を落としているあいだ必ず繋がらないので、

- Cloudflare Tunnel が返す**エラーページの生 HTML を UI に流さない**
- 接続先が RunPod の到達不能は「起動していません」の案内に言い換える

を見る。ネットワークには出ず、``httpx`` の応答は組み立てたものを使う。
"""

import httpx
import pytest
from fastapi.testclient import TestClient

from app import comfy, config, grok
from app.main import app
from app.models import HealthStatus, Settings

#: Cloudflare Tunnel が Pod 停止中に返すエラーページ（実物を短くしたもの）
TUNNEL_HTML = (
    "<!DOCTYPE html><html lang=\"en-US\"><head><title>"
    "pod.example.com | 530: Origin DNS error</title>"
    "<style>" + "body{}" * 200 + "</style></head><body>"
    + "<p>Error 1033</p>" * 50
    + "</body></html>"
)


def use(monkeypatch, **overrides) -> Settings:
    settings = Settings(
        runpod_comfy_url="https://pod.example.com",
        local_comfy_url="http://127.0.0.1:8188",
        **overrides,
    )
    monkeypatch.setattr(config, "_settings", settings)
    return settings


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    yield
    config._settings = None


@pytest.fixture
def client(monkeypatch):
    """/api/options / /api/health を叩くクライアント（grok は常に ok）。"""

    async def grok_ok() -> HealthStatus:
        return HealthStatus(status="ok")

    monkeypatch.setattr(grok, "check_grok", grok_ok)
    with TestClient(app) as test_client:
        yield test_client


def fail_with(monkeypatch, response: httpx.Response | Exception):
    """``httpx.AsyncClient`` を、必ずこの応答（か例外）を返す偽物に差し替える。"""

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_info):
            return False

        async def request(self, method, path, **kwargs):
            if isinstance(response, Exception):
                raise response
            response.request = httpx.Request(method, f"https://x{path}")
            return response

    monkeypatch.setattr(comfy.httpx, "AsyncClient", FakeClient)


def html_response(status: int = 530) -> httpx.Response:
    return httpx.Response(
        status,
        text=TUNNEL_HTML,
        headers={"content-type": "text/html; charset=UTF-8"},
    )


# --------------------------------------------------------------------------
# 本文の抜粋
# --------------------------------------------------------------------------

async def test_an_html_error_page_is_not_dumped(monkeypatch):
    use(monkeypatch, comfy_target="local")
    fail_with(monkeypatch, html_response(530))

    with pytest.raises(comfy.ComfyError) as caught:
        await comfy.get_object_info()

    message = str(caught.value)
    assert "<html" not in message and "<style" not in message
    assert len(message) < 200
    # タイトルだけは残す（何が起きたかの手がかり）
    assert "530" in message and "Origin DNS error" in message
    assert caught.value.status_code == 530
    assert caught.value.unreachable is True


async def test_a_plain_text_error_body_is_kept(monkeypatch):
    use(monkeypatch, comfy_target="local")
    fail_with(
        monkeypatch,
        httpx.Response(
            400, text="invalid prompt", headers={"content-type": "text/plain"}
        ),
    )

    with pytest.raises(comfy.ComfyError) as caught:
        await comfy.get_object_info()

    assert "invalid prompt" in str(caught.value)
    assert caught.value.unreachable is False


async def test_a_transport_failure_is_unreachable(monkeypatch):
    use(monkeypatch, comfy_target="local")
    fail_with(monkeypatch, httpx.ConnectError("connection refused"))

    with pytest.raises(comfy.ComfyError) as caught:
        await comfy.get_object_info()

    assert caught.value.unreachable is True


# --------------------------------------------------------------------------
# 表示用の言い換え
# --------------------------------------------------------------------------

def test_a_stopped_pod_is_explained_with_the_autostart(monkeypatch):
    use(monkeypatch, comfy_target="runpod", runpod_enabled=True)
    error = comfy.ComfyError("HTTP 530", status_code=530, unreachable=True)

    message = comfy.display_error(error)

    assert "RunPod の ComfyUI が起動していません" in message
    assert "自動で Pod を起動します" in message


def test_without_autostart_the_user_is_told_what_to_do(monkeypatch):
    use(monkeypatch, comfy_target="runpod", runpod_enabled=False)
    error = comfy.ComfyError("HTTP 530", status_code=530, unreachable=True)

    message = comfy.display_error(error)

    assert "設定画面から Pod を起動する" in message


def test_other_targets_keep_the_original_message(monkeypatch):
    use(monkeypatch, comfy_target="local")
    error = comfy.ComfyError("connection refused", unreachable=True)

    assert comfy.display_error(error) == "connection refused"


def test_a_real_error_is_never_swallowed(monkeypatch):
    """到達したうえでの失敗（400 など）は言い換えない。"""
    use(monkeypatch, comfy_target="runpod", runpod_enabled=True)
    error = comfy.ComfyError("HTTP 400 invalid prompt", status_code=400)

    assert comfy.display_error(error) == "HTTP 400 invalid prompt"


# --------------------------------------------------------------------------
# エンドポイント
# --------------------------------------------------------------------------

def test_options_reports_the_friendly_message(client, monkeypatch):
    monkeypatch.setattr(
        config,
        "_settings",
        Settings(
            comfy_target="runpod",
            runpod_comfy_url="https://pod.example.com",
            runpod_enabled=True,
        ),
    )
    fail_with(monkeypatch, html_response(530))

    body = client.get("/api/options").json()

    assert body["comfy_connected"] is False
    assert "RunPod の ComfyUI が起動していません" in body["comfy_error"]
    assert "<html" not in body["comfy_error"]


def test_health_reports_the_friendly_message(client, monkeypatch):
    monkeypatch.setattr(
        config,
        "_settings",
        Settings(
            comfy_target="runpod",
            runpod_comfy_url="https://pod.example.com",
            runpod_enabled=True,
        ),
    )
    fail_with(monkeypatch, html_response(530))

    status = client.get("/api/health").json()["comfyui"]

    assert status["status"] == "error"
    assert "RunPod の ComfyUI が起動していません" in status["detail"]
    assert "<html" not in status["detail"]
