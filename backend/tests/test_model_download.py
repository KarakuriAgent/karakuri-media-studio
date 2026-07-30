"""不足モデルの自動ダウンロード（SPEC §3.3）。

保存先の検証（パストラバーサル）・ホストごとの認証ヘッダ・``.part`` からの rename・
同名の同時ダウンロード拒否を見る。ネットワークには一切出ず、``httpx.AsyncClient`` は
チャンクを返すだけの偽物に差し替える。
"""

import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient

from app import config, model_download
from app.main import app
from app.models import ModelDownload
from app.workflow import MODEL_FIELDS, MODEL_SUBFOLDERS, model_subfolder

HF_URL = "https://huggingface.co/org/repo/resolve/main/model.safetensors"
CIVITAI_URL = "https://civitai.com/api/download/models/1234"


@pytest.fixture
def client(tmp_path, monkeypatch):
    """設定が使い捨ての config.json に載るクライアント（進行中一覧も毎回空）。

    models ディレクトリは環境変数だけで決まるので、既定では未設定にしておく。
    """
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(config, "_settings", None)
    monkeypatch.delenv(model_download.MODELS_DIR_ENV, raising=False)
    model_download._downloads.clear()
    model_download._tasks.clear()
    with TestClient(app) as test_client:
        yield test_client
    model_download._downloads.clear()
    model_download._tasks.clear()
    config._settings = None


@pytest.fixture
def models_dir(tmp_path, monkeypatch, client):
    """書き込み可能な models ディレクトリを環境変数で指した状態。"""
    root = tmp_path / "ComfyUI" / "models"
    root.mkdir(parents=True)
    monkeypatch.setenv(model_download.MODELS_DIR_ENV, str(root))
    return root


# --------------------------------------------------------------------------
# 偽の httpx クライアント
# --------------------------------------------------------------------------

class FakeResponse:
    def __init__(self, chunks=(), *, total=None, status=200, location=None):
        self._chunks = chunks
        self.status_code = status
        self.reason_phrase = "Forbidden" if status == 403 else "OK"
        self.headers = {}
        if total is not None:
            self.headers["content-length"] = str(total)
        if location is not None:
            self.headers["location"] = location

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "boom",
                request=httpx.Request("GET", "https://example.invalid/x"),
                response=httpx.Response(self.status_code),
            )

    async def aiter_bytes(self, chunk_size=None):
        for chunk in self._chunks:
            if isinstance(chunk, Exception):
                raise chunk
            yield chunk


class FakeClient:
    """``httpx.AsyncClient`` の代役。

    リクエストごとの ``(URL, ヘッダ)`` を :attr:`calls` に積むので、リダイレクトを
    追ったときにどのホストへ何を送ったかを検証できる。用意したレスポンスは順に
    返し、尽きたら最後のものを使い回す（リダイレクトループの再現用）。
    """

    #: 生成時にクライアントへ渡された引数（既定ヘッダを載せていないことの確認用）
    client_kwargs: dict = {}
    calls: list[tuple[str, dict[str, str]]] = []

    def __init__(self, responses):
        self._responses = list(responses)

    def __call__(self, **kwargs):
        FakeClient.client_kwargs = kwargs
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    def stream(self, method, url, headers=None):
        FakeClient.calls.append((url, dict(headers or {})))
        response = (
            self._responses.pop(0) if len(self._responses) > 1 else self._responses[0]
        )

        class Ctx:
            async def __aenter__(self):
                return response

            async def __aexit__(self, *exc_info):
                return False

        return Ctx()


def fake_httpx(monkeypatch, responses) -> FakeClient:
    """``httpx.AsyncClient`` を偽物に差し替える（1 件でもリストでも渡せる）。"""
    if isinstance(responses, FakeResponse):
        responses = [responses]
    FakeClient.calls = []
    FakeClient.client_kwargs = {}
    fake = FakeClient(responses)
    monkeypatch.setattr(model_download.httpx, "AsyncClient", fake)
    return fake


def state_for(target, url=HF_URL, filename="model.safetensors") -> ModelDownload:
    return ModelDownload(filename=filename, url=url, path=str(target))


# --------------------------------------------------------------------------
# GET /api/models/dir-status
# --------------------------------------------------------------------------

def test_dir_status_is_not_configured_without_the_env_var(client):
    """環境変数が無ければ機能ごと無効（UI もダウンロード関連を出さない）。"""
    status = client.get("/api/models/dir-status").json()
    assert status == {
        "configured": False,
        "exists": False,
        "writable": False,
        "path": "",
    }


def test_dir_status_reports_a_missing_path(client, tmp_path, monkeypatch):
    missing = tmp_path / "nope" / "models"
    monkeypatch.setenv(model_download.MODELS_DIR_ENV, str(missing))
    status = client.get("/api/models/dir-status").json()
    assert status["configured"] is True
    assert status["exists"] is False
    assert status["writable"] is False
    assert status["path"] == str(missing)


def test_dir_status_reports_a_writable_path(client, models_dir):
    status = client.get("/api/models/dir-status").json()
    assert (status["configured"], status["exists"], status["writable"]) == (
        True,
        True,
        True,
    )


def test_dir_status_reports_a_read_only_path(client, models_dir):
    models_dir.chmod(0o500)
    try:
        status = client.get("/api/models/dir-status").json()
        assert status["exists"] is True
        assert status["writable"] is False
    finally:
        models_dir.chmod(0o700)


def test_the_models_dir_is_not_a_setting(client, tmp_path, monkeypatch):
    """保存先は環境変数だけ: 設定 API では読み書きできない。"""
    assert "comfy_models_dir" not in client.get("/api/settings").json()
    client.put("/api/settings", json={"comfy_models_dir": str(tmp_path)})
    assert "comfy_models_dir" not in client.get("/api/settings").json()
    assert client.get("/api/models/dir-status").json()["configured"] is False


def test_a_stale_models_dir_key_in_config_json_is_dropped(tmp_path, monkeypatch):
    """旧バージョンが書いた設定が残っていても無視される。"""
    path = tmp_path / "config.json"
    path.write_text(
        '{"comfy_url": "http://x", "comfy_models_dir": "/old/models"}',
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "CONFIG_PATH", path)
    monkeypatch.setattr(config, "_settings", None)
    try:
        settings = config.load_settings()
        assert settings.comfy_url == "http://x"
        assert "comfy_models_dir" not in settings.model_dump()
    finally:
        config._settings = None


# --------------------------------------------------------------------------
# 保存先の検証（パストラバーサル）
# --------------------------------------------------------------------------

def test_resolve_target_places_the_file_in_the_subfolder(client, models_dir):
    target = model_download.resolve_target("diffusion_models", "a.safetensors")
    assert target == models_dir / "diffusion_models" / "a.safetensors"
    # 置き場所を省略すると models ディレクトリ直下
    assert model_download.resolve_target("", "a.safetensors") == (
        models_dir / "a.safetensors"
    )


@pytest.mark.parametrize(
    "subfolder,filename",
    [
        ("../x", "a.safetensors"),
        ("loras/../../x", "a.safetensors"),
        ("/etc", "a.safetensors"),
        ("loras", "../../etc/passwd"),
        ("loras", "/etc/passwd"),
        ("loras", ".."),
        ("loras", "  "),
    ],
)
def test_resolve_target_rejects_traversal(client, models_dir, subfolder, filename):
    with pytest.raises(model_download.DownloadError):
        model_download.resolve_target(subfolder, filename)


def test_resolve_target_needs_the_env_var(client):
    with pytest.raises(model_download.DownloadError):
        model_download.resolve_target("loras", "a.safetensors")


@pytest.mark.parametrize(
    "subfolder,filename",
    [("../x", "a.safetensors"), ("loras", "../../etc/passwd")],
)
def test_post_download_rejects_traversal(client, models_dir, subfolder, filename):
    response = client.post(
        "/api/models/download",
        json={"filename": filename, "url": HF_URL, "subfolder": subfolder},
    )
    assert response.status_code == 400
    assert client.get("/api/models/downloads").json() == []


def test_post_download_rejects_a_non_http_url(client, models_dir):
    response = client.post(
        "/api/models/download",
        json={
            "filename": "a.safetensors",
            "url": "file:///etc/passwd",
            "subfolder": "loras",
        },
    )
    assert response.status_code == 400


def test_post_download_needs_an_existing_models_dir(client, tmp_path, monkeypatch):
    monkeypatch.setenv(model_download.MODELS_DIR_ENV, str(tmp_path / "missing"))
    response = client.post(
        "/api/models/download",
        json={"filename": "a.safetensors", "url": HF_URL, "subfolder": "loras"},
    )
    assert response.status_code == 400
    assert "models" in response.json()["detail"]


# --------------------------------------------------------------------------
# 認証ヘッダの出し分け
# --------------------------------------------------------------------------

def test_auth_headers_are_omitted_without_tokens(client):
    assert model_download.auth_headers(HF_URL) == {}
    assert model_download.auth_headers(CIVITAI_URL) == {}


def test_auth_headers_pick_the_matching_token(client):
    client.put(
        "/api/settings", json={"hf_token": "hf_xxx", "civitai_api_key": "civ_yyy"}
    )
    assert model_download.auth_headers(HF_URL) == {"Authorization": "Bearer hf_xxx"}
    assert model_download.auth_headers("https://cdn-lfs.hf.co/repo/file") == {
        "Authorization": "Bearer hf_xxx"
    }
    assert model_download.auth_headers(CIVITAI_URL) == {
        "Authorization": "Bearer civ_yyy"
    }
    # 無関係なホストにはトークンを渡さない
    assert model_download.auth_headers("https://example.com/model.safetensors") == {}
    assert model_download.auth_headers("https://huggingface.co.evil.test/x") == {}


async def test_download_sends_the_hugging_face_header(client, models_dir, monkeypatch):
    client.put("/api/settings", json={"hf_token": "hf_xxx"})
    fake_httpx(monkeypatch, FakeResponse([b"abc"], total=3))
    await model_download.download(state_for(models_dir / "a.safetensors"))
    assert FakeClient.calls == [(HF_URL, {"Authorization": "Bearer hf_xxx"})]
    # クライアントの既定ヘッダには載せない（リダイレクト先へ漏れるため）
    assert not FakeClient.client_kwargs.get("headers")
    assert FakeClient.client_kwargs["follow_redirects"] is False


# --------------------------------------------------------------------------
# リダイレクト（自分で追う: トークンを転送先に漏らさない）
# --------------------------------------------------------------------------

async def test_a_redirect_to_an_unrelated_host_drops_the_token(
    client, models_dir, monkeypatch
):
    client.put("/api/settings", json={"hf_token": "hf_xxx"})
    elsewhere = "https://mirror.example.com/file.safetensors"
    fake_httpx(
        monkeypatch,
        [
            FakeResponse(status=302, location=elsewhere),
            FakeResponse([b"abc"], total=3),
        ],
    )
    state = state_for(models_dir / "a.safetensors")
    await model_download.download(state)

    assert state.status == "done"
    assert (models_dir / "a.safetensors").read_bytes() == b"abc"
    assert FakeClient.calls == [
        (HF_URL, {"Authorization": "Bearer hf_xxx"}),
        (elsewhere, {}),
    ]


async def test_a_redirect_inside_the_family_keeps_the_token(
    client, models_dir, monkeypatch
):
    """HF → CDN（*.hf.co）のような同一ファミリーには引き続きトークンを付ける。"""
    client.put("/api/settings", json={"hf_token": "hf_xxx"})
    cdn = "https://cdn-lfs.hf.co/repo/file.safetensors"
    fake_httpx(
        monkeypatch,
        [
            FakeResponse(status=307, location=cdn),
            FakeResponse([b"abc"], total=3),
        ],
    )
    await model_download.download(state_for(models_dir / "a.safetensors"))
    assert FakeClient.calls == [
        (HF_URL, {"Authorization": "Bearer hf_xxx"}),
        (cdn, {"Authorization": "Bearer hf_xxx"}),
    ]


async def test_a_relative_location_is_resolved(client, models_dir, monkeypatch):
    fake_httpx(
        monkeypatch,
        [
            FakeResponse(status=303, location="/other/file.safetensors"),
            FakeResponse([b"abc"], total=3),
        ],
    )
    await model_download.download(state_for(models_dir / "a.safetensors"))
    assert FakeClient.calls[1][0] == "https://huggingface.co/other/file.safetensors"


async def test_a_redirect_loop_stops_at_the_limit(client, models_dir, monkeypatch):
    # 同じレスポンスを返し続ける（自分自身へのリダイレクト）
    fake_httpx(monkeypatch, FakeResponse(status=302, location=HF_URL))
    target = models_dir / "a.safetensors"
    state = state_for(target)
    await model_download.download(state)

    assert len(FakeClient.calls) == model_download.MAX_REDIRECTS + 1
    assert state.status == "error"
    assert "リダイレクト" in (state.error or "")
    assert not target.exists()
    assert not target.with_name(target.name + ".part").exists()


async def test_a_redirect_without_a_location_fails(client, models_dir, monkeypatch):
    fake_httpx(monkeypatch, FakeResponse(status=302))
    state = state_for(models_dir / "a.safetensors")
    await model_download.download(state)
    assert state.status == "error"
    assert "リダイレクト先" in (state.error or "")


async def test_a_redirect_to_a_non_http_scheme_fails(client, models_dir, monkeypatch):
    fake_httpx(monkeypatch, FakeResponse(status=302, location="file:///etc/passwd"))
    state = state_for(models_dir / "a.safetensors")
    await model_download.download(state)
    assert state.status == "error"
    assert "http(s)" in (state.error or "")


# --------------------------------------------------------------------------
# .part → rename
# --------------------------------------------------------------------------

async def test_download_writes_through_a_part_file(client, models_dir, monkeypatch):
    fake_httpx(monkeypatch, FakeResponse([b"ab", b"cd"], total=4))
    target = models_dir / "diffusion_models" / "a.safetensors"
    state = state_for(target)
    await model_download.download(state)

    assert target.read_bytes() == b"abcd"
    assert not target.with_name(target.name + ".part").exists()
    assert (state.status, state.received, state.total, state.error) == (
        "done",
        4,
        4,
        None,
    )


async def test_download_deletes_the_part_file_on_failure(
    client, models_dir, monkeypatch
):
    fake_httpx(monkeypatch, FakeResponse([b"ab", httpx.ReadError("cut")], total=4))
    target = models_dir / "a.safetensors"
    state = state_for(target)
    await model_download.download(state)

    assert not target.exists()
    assert not target.with_name(target.name + ".part").exists()
    assert state.status == "error"
    assert "ReadError" in (state.error or "")


async def test_download_reports_an_http_error(client, models_dir, monkeypatch):
    fake_httpx(monkeypatch, FakeResponse([], status=403))
    target = models_dir / "a.safetensors"
    state = state_for(target)
    await model_download.download(state)

    assert not target.exists()
    assert state.status == "error"
    assert "403" in (state.error or "")


async def test_download_tolerates_a_missing_content_length(
    client, models_dir, monkeypatch
):
    fake_httpx(monkeypatch, FakeResponse([b"abc"]))
    state = state_for(models_dir / "a.safetensors")
    await model_download.download(state)
    assert (state.status, state.total, state.received) == ("done", None, 3)


async def test_download_broadcasts_progress(client, models_dir, monkeypatch):
    """開始 / 完了のフレームが WS に流れること（type は model_download）。"""
    frames: list[dict] = []

    async def capture(payload):
        frames.append(payload)

    monkeypatch.setattr(model_download.ws.hub, "broadcast", capture)
    fake_httpx(monkeypatch, FakeResponse([b"abc"], total=3))
    await model_download.download(state_for(models_dir / "a.safetensors"))

    assert [frame["type"] for frame in frames] == ["model_download", "model_download"]
    assert frames[0]["status"] == "downloading"
    assert frames[-1] == {
        "type": "model_download",
        "filename": "model.safetensors",
        "status": "done",
        "received": 3,
        "total": 3,
        "error": None,
    }


# --------------------------------------------------------------------------
# 同時ダウンロードの拒否 / 進行中一覧
# --------------------------------------------------------------------------

def test_a_second_download_of_the_same_file_is_rejected(
    client, models_dir, monkeypatch
):
    release = asyncio.Event()

    async def never_ending(state):
        await release.wait()
        state.status = "done"

    monkeypatch.setattr(model_download, "download", never_ending)
    body = {
        "filename": "a.safetensors",
        "url": HF_URL,
        "subfolder": "diffusion_models",
    }
    first = client.post("/api/models/download", json=body)
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "downloading"
    assert first.json()["path"].endswith("/diffusion_models/a.safetensors")

    second = client.post("/api/models/download", json=body)
    assert second.status_code == 409

    # 別のファイルなら並走できる
    other = client.post(
        "/api/models/download", json={**body, "filename": "b.safetensors"}
    )
    assert other.status_code == 200
    assert {row["filename"] for row in client.get("/api/models/downloads").json()} == {
        "a.safetensors",
        "b.safetensors",
    }


def test_a_finished_download_can_be_started_again(client, models_dir, monkeypatch):
    async def finish(state):
        state.status = "error"
        state.error = "一度失敗した"

    monkeypatch.setattr(model_download, "download", finish)
    body = {"filename": "a.safetensors", "url": HF_URL, "subfolder": "loras"}
    assert client.post("/api/models/download", json=body).status_code == 200
    # 直前のタスクが終わっていれば再試行できる（一覧には結果が残る）
    for _ in range(20):
        if not model_download._tasks:
            break
        client.get("/api/models/dir-status")
    assert client.post("/api/models/download", json=body).status_code == 200


# --------------------------------------------------------------------------
# class_type → 置き場所のマッピング（SPEC §3.3）
# --------------------------------------------------------------------------

def test_every_model_field_has_a_subfolder():
    """テンプレートが使う全ローダーに置き場所が決まっていること。"""
    missing = sorted(pair for pair in MODEL_FIELDS if pair not in MODEL_SUBFOLDERS)
    assert missing == []


def test_unknown_loaders_get_an_empty_subfolder():
    assert model_subfolder("SomeNewLoader", "model_name") == ""


def test_models_endpoint_exposes_the_subfolder(client):
    rows = {row["key"]: row for row in client.get("/api/models").json()}
    assert rows["krea2_turbo/30:10.unet_name"]["subfolder"] == "diffusion_models"
    assert rows["tx2_3_i2v/320:316.ckpt_name"]["subfolder"] == "checkpoints"
