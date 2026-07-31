"""ComfyUI の接続先プロファイル（SPEC §5）。

設定は ComfyCloud / RunPod / ローカルの 3 組を持ち、``comfy_target`` が「今どれを
使うか」を決める。ここで見るのは

- どのプロファイルが :mod:`app.comfy` の URL / 認証ヘッダに効くか
- 旧レイアウト（単一の ``comfy_url`` / ``comfy_api_key``）の読み込み時移行
- ``PUT /api/settings`` で接続先だけを保存できること（生成フォームのプルダウン）
"""

import json

import pytest
from fastapi.testclient import TestClient

from app import comfy, config
from app.main import app
from app.models import COMFY_CLOUD_URL, Settings

PROFILES = dict(
    local_comfy_url="http://127.0.0.1:8188",
    runpod_comfy_url="https://pod.example.com",
    runpod_comfy_api_key="pod-key",
    comfy_cloud_api_key="cloud-key",
)


@pytest.fixture
def client(tmp_path, monkeypatch):
    """設定が使い捨ての config.json に載るクライアント。"""
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(config, "_settings", None)
    with TestClient(app) as test_client:
        yield test_client
    config._settings = None


def use(monkeypatch, **overrides) -> Settings:
    settings = Settings(**{**PROFILES, **overrides})
    monkeypatch.setattr(config, "_settings", settings)
    return settings


# --------------------------------------------------------------------------
# 接続情報の解決
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("target", "url", "api_key"),
    [
        ("local", "http://127.0.0.1:8188", ""),
        ("runpod", "https://pod.example.com", "pod-key"),
        ("comfy_cloud", COMFY_CLOUD_URL, "cloud-key"),
    ],
)
def test_active_connection_follows_the_target(monkeypatch, target, url, api_key):
    settings = use(monkeypatch, comfy_target=target)
    try:
        assert settings.active_comfy_url() == url
        # ローカルはキー無し（プロファイルごとに別のキーを持つ）
        assert settings.active_comfy_api_key() == api_key
        assert comfy._base_url() == url
        assert bool(comfy._headers()) is bool(api_key)
    finally:
        config._settings = None


def test_the_cloud_endpoint_is_fixed(monkeypatch):
    """ComfyCloud の URL は設定項目ではなく定数（キーだけを設定に持つ）。"""
    assert COMFY_CLOUD_URL == "https://cloud.comfy.org"
    assert "comfy_cloud_url" not in Settings.model_fields
    use(monkeypatch, comfy_target="comfy_cloud")
    try:
        assert comfy._base_url() == COMFY_CLOUD_URL
    finally:
        config._settings = None


def test_cloud_target_uses_the_api_prefix(monkeypatch):
    use(monkeypatch, comfy_target="comfy_cloud")
    try:
        assert comfy._api_prefix() == "/api"
    finally:
        config._settings = None


def test_local_target_has_no_api_prefix(monkeypatch):
    use(monkeypatch, comfy_target="local")
    try:
        assert comfy._api_prefix() == ""
    finally:
        config._settings = None


def test_an_empty_profile_url_is_an_error(monkeypatch):
    use(monkeypatch, comfy_target="runpod", runpod_comfy_url="")
    try:
        with pytest.raises(comfy.ComfyError) as caught:
            comfy._base_url()
        assert "runpod" in str(caught.value)
    finally:
        config._settings = None


# --------------------------------------------------------------------------
# 旧レイアウトからの移行
# --------------------------------------------------------------------------

def load(tmp_path, monkeypatch, data: dict) -> Settings:
    path = tmp_path / "config.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(config, "CONFIG_PATH", path)
    monkeypatch.setattr(config, "_settings", None)
    return config.load_settings()


def test_legacy_local_url_moves_to_the_local_profile(tmp_path, monkeypatch):
    try:
        settings = load(tmp_path, monkeypatch, {"comfy_url": "http://192.168.0.5:8188"})
        assert settings.comfy_target == "local"
        assert settings.local_comfy_url == "http://192.168.0.5:8188"
        assert settings.comfy_cloud_api_key == ""
    finally:
        config._settings = None


def test_legacy_runpod_url_moves_to_the_runpod_profile(tmp_path, monkeypatch):
    try:
        settings = load(
            tmp_path,
            monkeypatch,
            {"comfy_url": "https://pod.example.com", "runpod_enabled": True},
        )
        assert settings.comfy_target == "runpod"
        assert settings.runpod_comfy_url == "https://pod.example.com"
        # ローカルの既定はそのまま残る（切り替えればすぐ使える）
        assert settings.local_comfy_url == "http://127.0.0.1:8188"
    finally:
        config._settings = None


def test_a_legacy_key_follows_its_url_to_the_runpod_profile(tmp_path, monkeypatch):
    """Pod の ComfyUI を認証付きで公開している構成でもキーを失わない。"""
    try:
        settings = load(
            tmp_path,
            monkeypatch,
            {
                "comfy_url": "https://pod.example.com",
                "comfy_api_key": "pod-key",
                "runpod_enabled": True,
            },
        )
        assert settings.comfy_target == "runpod"
        assert settings.runpod_comfy_api_key == "pod-key"
        assert settings.comfy_cloud_api_key == ""
    finally:
        config._settings = None


def test_legacy_cloud_url_and_key_move_to_the_cloud_profile(tmp_path, monkeypatch):
    try:
        settings = load(
            tmp_path,
            monkeypatch,
            {"comfy_url": "https://cloud.comfy.org", "comfy_api_key": "k"},
        )
        assert settings.comfy_target == "comfy_cloud"
        # URL は固定なので移さない（キーだけ引き継ぐ）
        assert settings.comfy_cloud_api_key == "k"
    finally:
        config._settings = None


def test_a_legacy_key_alone_lands_on_the_cloud_profile(tmp_path, monkeypatch):
    """URL が無くても API キーは ComfyCloud のものとして残す。"""
    try:
        settings = load(tmp_path, monkeypatch, {"comfy_api_key": "k"})
        assert settings.comfy_cloud_api_key == "k"
        assert settings.comfy_target == "local"
    finally:
        config._settings = None


def test_a_new_config_is_left_alone(tmp_path, monkeypatch):
    """`comfy_target` がある = 新レイアウト。旧キーが残っていても触らない。

    廃止した `comfy_cloud_url`（URL 固定化の前に書かれたもの）も同じ扱いで、
    設定ファイルに残っていてもエラーにならず、そのまま捨てられる。
    """
    try:
        settings = load(
            tmp_path,
            monkeypatch,
            {
                "comfy_target": "comfy_cloud",
                "comfy_url": "http://old:8188",
                "local_comfy_url": "http://192.168.0.5:8188",
                "comfy_cloud_url": "https://old.example.com",
            },
        )
        assert settings.comfy_target == "comfy_cloud"
        assert settings.local_comfy_url == "http://192.168.0.5:8188"
        assert "comfy_cloud_url" not in settings.model_dump()
    finally:
        config._settings = None


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------

def test_put_settings_saves_the_target_only(client):
    assert client.get("/api/settings").json()["comfy_target"] == "local"

    body = client.put("/api/settings", json={"comfy_target": "comfy_cloud"}).json()
    assert body["comfy_target"] == "comfy_cloud"
    # 他のプロファイルの接続情報は消えない
    assert body["local_comfy_url"] == "http://127.0.0.1:8188"
    assert config.load_settings().comfy_target == "comfy_cloud"


def test_put_settings_rejects_an_unknown_target(client):
    assert client.put("/api/settings", json={"comfy_target": "aws"}).status_code == 422


def test_options_reports_the_active_target(client, monkeypatch):
    async def down(*args, **kwargs):
        raise comfy.ComfyError("connection refused")

    monkeypatch.setattr(comfy, "get_object_info", down)
    client.put(
        "/api/settings",
        json={"comfy_target": "runpod", "runpod_comfy_url": "https://pod.example.com"},
    )
    body = client.get("/api/options").json()
    assert body["comfy_target"] == "runpod"
    assert body["comfy_url"] == "https://pod.example.com"
