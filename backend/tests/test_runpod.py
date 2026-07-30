"""RunPod Pod の自動起動（SPEC §5.1）。

ネットワークには一切出ない: ComfyUI の疎通確認 (`comfy.get_object_info`) と
`httpx.AsyncClient` を偽物に差し替え、「疎通するなら何もしない」「落ちていれば
Pod を 1 つだけ作って待つ」「待っても繋がらなければエラー」「同時に呼ばれても
作成は 1 回」を見る。
"""

import asyncio

import httpx
import pytest

from app import comfy, config, runpod
from app.models import Settings

POD_ID = "pod-abc123"


@pytest.fixture
def settings(monkeypatch, tmp_path):
    """RunPod 自動起動が有効な設定（保存先は使い捨て）。"""
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    loaded = Settings(
        comfy_url="https://comfy.example.com",
        runpod_enabled=True,
        runpod_api_key="rp-key",
        runpod_template_id="tmpl-1",
        runpod_gpu_type="NVIDIA RTX A6000",
        runpod_network_volume_id="vol-1",
    )
    monkeypatch.setattr(config, "_settings", loaded)
    # 待ち時間は詰める（ロジックは同じ）
    monkeypatch.setattr(runpod, "POLL_INTERVAL", 0.0)
    monkeypatch.setattr(runpod, "BOOT_TIMEOUT", 0.5)
    monkeypatch.setattr(runpod, "_pod_id", None)
    yield loaded
    config._settings = None


class FakeResponse:
    def __init__(self, payload=None, status=200):
        self._payload = payload
        self.status_code = status
        self.text = "" if payload is None else str(payload)
        self.content = b"" if payload is None else b"{}"

    def json(self):
        return self._payload


class FakeClient:
    """``httpx.AsyncClient`` の代役。リクエストを :attr:`calls` に積む。"""

    calls: list[tuple[str, str, dict]] = []
    response = FakeResponse({"id": POD_ID, "desiredStatus": "RUNNING"})

    def __init__(self, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def request(self, method, url, headers=None, json=None):
        FakeClient.calls.append((method, url, dict(headers or {})))
        if isinstance(FakeClient.response, Exception):
            raise FakeClient.response
        return FakeClient.response


@pytest.fixture
def fake_api(monkeypatch):
    FakeClient.calls = []
    FakeClient.response = FakeResponse({"id": POD_ID, "desiredStatus": "RUNNING"})
    monkeypatch.setattr(runpod.httpx, "AsyncClient", FakeClient)
    return FakeClient


def reachability(monkeypatch, answers):
    """``comfy.get_object_info`` の答えを順に決める（True = 疎通する）。

    尽きたら最後の答えを使い回す。呼ばれた回数はリストの長さで数える。
    """
    seen: list[bool] = []

    async def fake_object_info(class_type=None):
        alive = answers[len(seen)] if len(seen) < len(answers) else answers[-1]
        seen.append(alive)
        if not alive:
            raise comfy.ComfyError("connection refused")
        return {}

    monkeypatch.setattr(runpod.comfy, "get_object_info", fake_object_info)
    return seen


# --------------------------------------------------------------------------
# 起動しない場合
# --------------------------------------------------------------------------

def test_disabled_settings_do_nothing(settings, fake_api, monkeypatch):
    settings.runpod_enabled = False
    seen = reachability(monkeypatch, [False])

    asyncio.run(runpod.ensure_pod_running())

    assert fake_api.calls == []
    # 疎通確認すらしない（設定が無効なら経路ごと素通り）
    assert seen == []


def test_reachable_comfy_is_left_alone(settings, fake_api, monkeypatch):
    seen = reachability(monkeypatch, [True])

    asyncio.run(runpod.ensure_pod_running())

    assert fake_api.calls == []
    assert seen == [True]


# --------------------------------------------------------------------------
# 起動する場合
# --------------------------------------------------------------------------

def test_pod_is_created_and_waited_for(settings, fake_api, monkeypatch):
    # 1 回目: 落ちている -> 作成 -> 2 回目のポーリングで上がる
    reachability(monkeypatch, [False, False, True])
    messages: list[str] = []

    async def progress(text):
        messages.append(text)

    asyncio.run(runpod.ensure_pod_running(progress))

    assert len(fake_api.calls) == 1
    method, url, headers = fake_api.calls[0]
    assert (method, url) == ("POST", f"{runpod.API_BASE}/pods")
    assert headers["Authorization"] == "Bearer rp-key"
    assert runpod.current_pod_id() == POD_ID
    assert messages[0].startswith("RunPod")
    assert messages[-1] == "ComfyUI に接続しました"


def test_create_pod_sends_the_configured_template_and_gpu(settings, fake_api, monkeypatch):
    reachability(monkeypatch, [False, True])
    sent: dict = {}

    async def capture(self, method, url, headers=None, json=None):
        sent.update(json or {})
        FakeClient.calls.append((method, url, dict(headers or {})))
        return FakeClient.response

    monkeypatch.setattr(FakeClient, "request", capture)

    asyncio.run(runpod.ensure_pod_running())

    assert sent["templateId"] == "tmpl-1"
    assert sent["gpuTypeIds"] == ["NVIDIA RTX A6000"]
    assert sent["networkVolumeId"] == "vol-1"
    assert sent["cloudType"] == "COMMUNITY"


def test_timeout_is_reported_as_an_error(settings, fake_api, monkeypatch):
    reachability(monkeypatch, [False])

    with pytest.raises(runpod.RunPodError) as caught:
        asyncio.run(runpod.ensure_pod_running())

    assert "接続できませんでした" in str(caught.value)


def test_api_failure_is_reported_as_an_error(settings, fake_api, monkeypatch):
    reachability(monkeypatch, [False])
    FakeClient.response = FakeResponse(None, status=400)
    FakeClient.response.text = "no gpu available"

    with pytest.raises(runpod.RunPodError) as caught:
        asyncio.run(runpod.ensure_pod_running())

    assert "no gpu available" in str(caught.value)


def test_transport_failure_is_reported_as_an_error(settings, fake_api, monkeypatch):
    reachability(monkeypatch, [False])
    FakeClient.response = httpx.ConnectError("boom")

    with pytest.raises(runpod.RunPodError) as caught:
        asyncio.run(runpod.ensure_pod_running())

    assert "接続できません" in str(caught.value)


def test_missing_api_key_is_reported_before_any_request(settings, fake_api, monkeypatch):
    settings.runpod_api_key = ""
    reachability(monkeypatch, [False])

    with pytest.raises(runpod.RunPodError):
        asyncio.run(runpod.ensure_pod_running())

    assert fake_api.calls == []


def test_missing_template_is_reported_before_any_request(settings, fake_api, monkeypatch):
    settings.runpod_template_id = ""
    reachability(monkeypatch, [False])

    with pytest.raises(runpod.RunPodError):
        asyncio.run(runpod.ensure_pod_running())

    assert fake_api.calls == []


# --------------------------------------------------------------------------
# single-flight
# --------------------------------------------------------------------------

def test_concurrent_calls_create_one_pod(settings, fake_api, monkeypatch):
    """同時に 2 本走っても Pod は 1 つ。

    1 本目が作成 → 起動を確認して抜けたあと、2 本目はロックを取った時点で
    もう一度疎通を確かめ、通っているので何もしない。
    """
    reachability(monkeypatch, [False, True])

    async def both():
        await asyncio.gather(
            runpod.ensure_pod_running(), runpod.ensure_pod_running()
        )

    asyncio.run(both())

    assert len(fake_api.calls) == 1
