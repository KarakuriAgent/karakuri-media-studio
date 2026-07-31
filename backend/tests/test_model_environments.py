"""接続先ごとのモデル指定・LoRA 登録・ダウンロード（SPEC §3.3 / §5）。

ここで見るのは

- 旧レイアウト（接続先を分ける前の 1 組だけ）が 3 環境へ複製されること
- ``GET/PUT /api/models?target=`` が選んだ環境だけを読み書きすること
- LoRA が環境ごとに出ること（``comfy_target`` が NULL の行は全環境で出る）
- ``POST /api/models/download`` の振り分け（ローカルは自前、RunPod は Pod の API、
  ComfyCloud は 400）と ``download-all`` の対象選び

ネットワークには出ない: Pod の API は偽の ``httpx.AsyncClient`` で受ける。
"""

import json

import pytest
from fastapi.testclient import TestClient

from app import comfy, config, model_download
from app.main import app
from app.models import Settings

UNET_KEY = "krea2_turbo/30:10.unet_name"
POD_URL = "https://pod.example.com"


@pytest.fixture
def client(tmp_path, monkeypatch):
    """設定も DB も使い捨てのクライアント（接続先は既定のローカル）。"""
    from app import db, paths

    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(config, "_settings", None)
    monkeypatch.setattr(paths, "DB_PATH", tmp_path / "app.db")
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "app.db")
    model_download._downloads.clear()
    model_download._tasks.clear()
    with TestClient(app) as test_client:
        yield test_client
    model_download._downloads.clear()
    model_download._tasks.clear()
    config._settings = None


def use_runpod(client) -> None:
    client.put(
        "/api/settings",
        json={
            "comfy_target": "runpod",
            "runpod_comfy_url": POD_URL,
            "runpod_comfy_api_key": "pod-key",
        },
    )


# --------------------------------------------------------------------------
# 旧レイアウトからの移行
# --------------------------------------------------------------------------

def test_a_flat_config_is_copied_to_every_target(tmp_path, monkeypatch):
    """接続先を分ける前の指定は 3 環境すべてに複製される（消さない）。"""
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "model_overrides": {UNET_KEY: "mine.safetensors"},
                "model_choices": {UNET_KEY: ["a.safetensors", "b.safetensors"]},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "CONFIG_PATH", path)
    monkeypatch.setattr(config, "_settings", None)
    try:
        settings = config.load_settings()
        for target in ("local", "runpod", "comfy_cloud"):
            assert settings.overrides_for(target) == {UNET_KEY: "mine.safetensors"}
            assert settings.choices_for(target) == {
                UNET_KEY: ["a.safetensors", "b.safetensors"]
            }
    finally:
        config._settings = None


def test_a_per_target_config_is_left_alone(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps({"model_overrides": {"local": {UNET_KEY: "only-local.safetensors"}}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "CONFIG_PATH", path)
    monkeypatch.setattr(config, "_settings", None)
    try:
        settings = config.load_settings()
        assert settings.overrides_for("local") == {UNET_KEY: "only-local.safetensors"}
        assert settings.overrides_for("runpod") == {}
    finally:
        config._settings = None


# --------------------------------------------------------------------------
# GET / PUT /api/models
# --------------------------------------------------------------------------

def by_key(rows: list[dict]) -> dict[str, dict]:
    return {row["key"]: row for row in rows}


def test_models_are_saved_per_target(client):
    client.put(
        "/api/models",
        json={"overrides": {UNET_KEY: "runpod.safetensors"}, "target": "runpod"},
    )
    client.put(
        "/api/models",
        json={"overrides": {UNET_KEY: "local.safetensors"}, "target": "local"},
    )

    runpod_rows = by_key(client.get("/api/models?target=runpod").json())
    assert runpod_rows[UNET_KEY]["value"] == "runpod.safetensors"
    local_rows = by_key(client.get("/api/models?target=local").json())
    assert local_rows[UNET_KEY]["value"] == "local.safetensors"
    # target 省略 = 現在の接続先（ここではローカル）
    assert by_key(client.get("/api/models").json())[UNET_KEY]["value"] == (
        "local.safetensors"
    )
    settings = config.load_settings()
    assert settings.overrides_for("runpod") == {UNET_KEY: "runpod.safetensors"}
    assert settings.overrides_for("local") == {UNET_KEY: "local.safetensors"}


def test_the_job_runner_uses_the_current_target(client):
    """ジョブが見るのは「いま繋いでいる環境」の指定。"""
    client.put(
        "/api/models",
        json={"overrides": {UNET_KEY: "runpod.safetensors"}, "target": "runpod"},
    )
    assert config.load_settings().overrides_for() == {}  # まだローカル接続

    client.put("/api/settings", json={"comfy_target": "runpod"})
    assert config.load_settings().overrides_for() == {UNET_KEY: "runpod.safetensors"}


# --------------------------------------------------------------------------
# LoRA
# --------------------------------------------------------------------------

def create_lora(client, name: str, target: str | None) -> dict:
    body = {
        "display_name": name,
        "lora_name": f"{name}.safetensors",
        "trigger_word": name,
        "comfy_target": target,
    }
    response = client.post("/api/loras", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def test_loras_are_listed_per_target(client):
    create_lora(client, "local-only", "local")
    create_lora(client, "pod-only", "runpod")
    create_lora(client, "shared", None)

    names = lambda rows: sorted(row["display_name"] for row in rows)  # noqa: E731
    assert names(client.get("/api/loras?target=local").json()) == [
        "local-only",
        "shared",
    ]
    assert names(client.get("/api/loras?target=runpod").json()) == [
        "pod-only",
        "shared",
    ]
    # 省略時は現在の接続先（ローカル）
    assert names(client.get("/api/loras").json()) == ["local-only", "shared"]


def test_the_form_options_follow_the_current_target(client, monkeypatch):
    async def offline(*args, **kwargs):
        raise comfy.ComfyError("down")

    monkeypatch.setattr(comfy, "get_object_info", offline)
    create_lora(client, "pod-only", "runpod")
    create_lora(client, "shared", None)

    assert [lora["display_name"] for lora in client.get("/api/options").json()["loras"]] == [
        "shared"
    ]
    use_runpod(client)
    assert sorted(
        lora["display_name"] for lora in client.get("/api/options").json()["loras"]
    ) == ["pod-only", "shared"]


# --------------------------------------------------------------------------
# ダウンロードの振り分け
# --------------------------------------------------------------------------

class FakePod:
    """Pod のダウンロード API の代役（``httpx.AsyncClient`` を差し替える）。"""

    calls: list[tuple[str, str, dict | None]] = []
    states: list[dict] = []
    status = 200

    def __init__(self, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def request(self, method, url, headers=None, json=None):
        FakePod.calls.append((method, url, json))
        FakePod.headers = dict(headers or {})
        if method == "POST":
            state = {
                "filename": json["filename"],
                "status": "downloading",
                "received": 0,
                "total": None,
                "error": None,
                "subfolder": json.get("subfolder", ""),
                "url": json["url"],
                "path": f"/workspace/ComfyUI/models/{json['filename']}",
            }
            FakePod.states.append(state)
            return FakeResponse(FakePod.status, state)
        return FakeResponse(FakePod.status, FakePod.states)


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


@pytest.fixture
def pod(monkeypatch):
    FakePod.calls = []
    FakePod.states = []
    FakePod.status = 200
    monkeypatch.setattr(model_download.httpx, "AsyncClient", FakePod)
    # ポーリングは即座に回して終わらせる（実時間を待たない）
    monkeypatch.setattr(model_download, "POD_POLL_INTERVAL", 0.0)
    return FakePod


def test_cloud_target_cannot_download(client):
    client.put("/api/settings", json={"comfy_target": "comfy_cloud"})
    response = client.post(
        "/api/models/download",
        json={"filename": "a.safetensors", "url": "https://example.com/a"},
    )
    assert response.status_code == 400
    assert "Comfy Cloud" in response.json()["detail"]


def test_runpod_target_asks_the_pod(client, pod):
    use_runpod(client)
    response = client.post(
        "/api/models/download",
        json={
            "filename": "a.safetensors",
            "url": "https://example.com/a",
            "subfolder": "loras",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "downloading"
    method, url, payload = pod.calls[0]
    assert (method, url) == ("POST", f"{POD_URL}/studio/models/download")
    assert payload == {
        "filename": "a.safetensors",
        "url": "https://example.com/a",
        "subfolder": "loras",
    }
    # ComfyUI と同じ認証ヘッダで通す（Caddy がそれを見る）
    assert pod.headers["X-API-Key"] == "pod-key"
    # 手元の一覧にも載る（UI は落とし先を区別せずに進捗を出せる）
    assert [item["filename"] for item in client.get("/api/models/downloads").json()] == [
        "a.safetensors"
    ]


def test_the_pod_list_is_merged_back(client, pod):
    use_runpod(client)
    pod.states.append(
        {
            "filename": "b.safetensors",
            "status": "done",
            "received": 10,
            "total": 10,
            "error": None,
            "subfolder": "loras",
            "url": "https://example.com/b",
            "path": "/workspace/ComfyUI/models/loras/b.safetensors",
        }
    )
    rows = client.get("/api/models/downloads?target=runpod").json()
    assert [row["filename"] for row in rows] == ["b.safetensors"]
    assert rows[0]["status"] == "done"


def test_a_pod_without_the_api_is_explained(client, pod):
    use_runpod(client)
    pod.status = 404
    response = client.post(
        "/api/models/download",
        json={"filename": "a.safetensors", "url": "https://example.com/a"},
    )
    assert response.status_code == 400
    assert "イメージを作り直して" in response.json()["detail"]


def test_local_target_needs_the_models_dir(client, monkeypatch):
    """環境変数が無くても UI はボタンを出す: 押した理由がここで返る。"""
    monkeypatch.delenv(model_download.MODELS_DIR_ENV, raising=False)
    response = client.post(
        "/api/models/download",
        json={"filename": "a.safetensors", "url": "https://example.com/a"},
    )
    assert response.status_code == 400
    assert model_download.MODELS_DIR_ENV in response.json()["detail"]


# --------------------------------------------------------------------------
# 全DL
# --------------------------------------------------------------------------

def object_info(installed: list[str]) -> dict:
    """``UNETLoader.unet_name`` と LoRA 一覧だけを持つ ``/object_info``。"""
    combo = [installed, {}]
    return {
        "UNETLoader": {"input": {"required": {"unet_name": combo}}},
        "LoraLoaderModelOnly": {"input": {"required": {"lora_name": combo}}},
    }


def test_download_all_starts_the_missing_registered_files(client, pod, monkeypatch):
    use_runpod(client)
    create_lora(client, "kaori", "runpod")
    client.put(
        "/api/settings",
        json={
            "model_download_urls": {
                "kaori.safetensors": "https://example.com/kaori",
            }
        },
    )

    async def info(*args, **kwargs):
        # LoRA だけが未検出（UNet 側は「在る」ことにする）
        return object_info(["krea2_turbo_fp8_scaled.safetensors"])

    monkeypatch.setattr(comfy, "get_object_info", info)
    body = client.post("/api/models/download-all", json={"target": "runpod"}).json()

    assert [item["filename"] for item in body["started"]] == ["kaori.safetensors"]
    assert pod.calls[0][1].endswith("/studio/models/download")


def test_download_all_reports_files_without_a_url(client, pod, monkeypatch):
    use_runpod(client)
    create_lora(client, "kaori", "runpod")

    async def info(*args, **kwargs):
        return object_info(["krea2_turbo_fp8_scaled.safetensors"])

    monkeypatch.setattr(comfy, "get_object_info", info)
    body = client.post("/api/models/download-all", json={"target": "runpod"}).json()

    assert body["started"] == []
    # URL が無いものは名前で報告する（UI が「登録してください」と出せる）
    assert "kaori.safetensors" in body["missing_urls"]


def test_download_all_needs_a_reachable_comfyui(client, monkeypatch):
    async def offline(*args, **kwargs):
        raise comfy.ComfyError("connection refused", unreachable=True)

    monkeypatch.setattr(comfy, "get_object_info", offline)
    response = client.post("/api/models/download-all", json={"target": "local"})
    assert response.status_code == 400
    assert "判定できません" in response.json()["detail"]


def test_download_all_is_rejected_for_the_cloud(client):
    response = client.post("/api/models/download-all", json={"target": "comfy_cloud"})
    assert response.status_code == 400
