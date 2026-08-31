"""登録済みモデル・LoRA の「取得元ページ」（SPEC §3.3）。

ダウンロード URL -> 配布ページ URL の変換（HF は文字列処理、Civitai は API 1 回 +
キャッシュ）と、一覧の組み立てを見る。ネットワークには出ず、
``httpx.AsyncClient`` は用意した JSON を返すだけの偽物に差し替える。
"""

import asyncio

import httpx
import pytest

from app import config, model_sources
from app.models import Lora, Options, Settings

HF_URL = "https://huggingface.co/org/repo/resolve/main/sub/model.safetensors"
HF_PAGE = "https://huggingface.co/org/repo"
CIVITAI_URL = "https://civitai.com/api/download/models/456"
CIVITAI_PAGE = "https://civitai.com/models/123?modelVersionId=456"


@pytest.fixture
def settings(tmp_path, monkeypatch):
    """使い捨ての config.json に載る設定（キャッシュの書き戻しも tmp に入る）。"""
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(config, "_settings", Settings())
    yield config.load_settings
    config._settings = None


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "boom",
                request=httpx.Request("GET", "https://civitai.com/x"),
                response=httpx.Response(self.status_code),
            )

    def json(self):
        return self._payload


class FakeClient:
    """``httpx.AsyncClient`` の代役。問い合わせた URL を :attr:`calls` に積む。"""

    calls: list[tuple[str, dict]] = []

    def __init__(self, response):
        self._response = response

    def __call__(self, **kwargs):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def get(self, url, headers=None):
        FakeClient.calls.append((url, dict(headers or {})))
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


def fake_httpx(monkeypatch, response) -> None:
    FakeClient.calls = []
    monkeypatch.setattr(model_sources.httpx, "AsyncClient", FakeClient(response))


def run(coro):
    return asyncio.run(coro)


# --------------------------------------------------------------------------
# Hugging Face（ネットワーク不要）
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "url,expected",
    [
        (HF_URL, HF_PAGE),
        ("https://huggingface.co/org/repo/blob/main/model.safetensors", HF_PAGE),
        ("https://huggingface.co/org/repo/resolve/main/f.bin?download=true", HF_PAGE),
        ("https://huggingface.co/org/repo", HF_PAGE),
        (
            "https://huggingface.co/datasets/org/repo/resolve/main/f.bin",
            "https://huggingface.co/datasets/org/repo",
        ),
        # CDN 直リンクはリポジトリが読み取れないので変換しない
        ("https://cdn-lfs.hf.co/repos/ab/cd/model.safetensors", ""),
        ("https://huggingface.co/org", ""),
    ],
)
def test_huggingface_urls_become_repository_pages(url, expected):
    assert model_sources.offline_page_url(url) == expected


def test_an_unknown_host_has_no_page():
    assert model_sources.offline_page_url("https://example.com/model.safetensors") == ""
    assert model_sources.host_of("https://example.com/x") == ""


def test_the_hosts_are_recognised_including_subdomains():
    assert model_sources.host_of(HF_URL) == "huggingface"
    assert model_sources.host_of("https://cdn-lfs.hf.co/x") == "huggingface"
    assert model_sources.host_of(CIVITAI_URL) == "civitai"


# --------------------------------------------------------------------------
# Civitai（API 1 回 + キャッシュ）
# --------------------------------------------------------------------------

def test_a_civitai_download_url_is_resolved_through_the_api(settings, monkeypatch):
    fake_httpx(monkeypatch, FakeResponse({"modelId": 123}))
    pages = run(model_sources.resolve_page_urls([CIVITAI_URL]))
    assert pages == {CIVITAI_URL: CIVITAI_PAGE}
    assert FakeClient.calls[0][0] == "https://civitai.com/api/v1/model-versions/456"


def test_the_civitai_api_key_is_sent_when_configured(settings, monkeypatch):
    monkeypatch.setattr(
        config, "_settings", Settings(civitai_api_key="secret")
    )
    fake_httpx(monkeypatch, FakeResponse({"modelId": 123}))
    run(model_sources.resolve_page_urls([CIVITAI_URL]))
    assert FakeClient.calls[0][1]["Authorization"] == "Bearer secret"


def test_the_resolved_page_is_cached_in_the_settings(settings, monkeypatch):
    fake_httpx(monkeypatch, FakeResponse({"modelId": 123}))
    run(model_sources.resolve_page_urls([CIVITAI_URL]))
    assert settings().model_page_urls == {CIVITAI_URL: CIVITAI_PAGE}

    # 2 回目は API を叩かない
    fake_httpx(monkeypatch, FakeResponse({"modelId": 999}))
    pages = run(model_sources.resolve_page_urls([CIVITAI_URL]))
    assert pages == {CIVITAI_URL: CIVITAI_PAGE}
    assert FakeClient.calls == []


def test_a_failed_lookup_leaves_the_download_url_alone(settings, monkeypatch):
    fake_httpx(monkeypatch, FakeResponse({}, status=404))
    assert run(model_sources.resolve_page_urls([CIVITAI_URL])) == {}
    assert settings().model_page_urls == {}


def test_a_network_error_is_not_raised(settings, monkeypatch):
    fake_httpx(monkeypatch, httpx.ConnectError("down"))
    assert run(model_sources.resolve_page_urls([CIVITAI_URL])) == {}


def test_a_civitai_model_page_is_used_as_is(settings, monkeypatch):
    fake_httpx(monkeypatch, FakeResponse({"modelId": 123}))
    pages = run(model_sources.resolve_page_urls([CIVITAI_PAGE]))
    assert pages == {CIVITAI_PAGE: CIVITAI_PAGE}
    assert FakeClient.calls == []  # ページなので API は不要


# --------------------------------------------------------------------------
# 一覧の組み立て
# --------------------------------------------------------------------------

def _lora(name: str, **overrides) -> Lora:
    body = {
        "id": 1,
        "display_name": "サクラ",
        "lora_name": name,
        "trigger_word": "sakura",
    }
    body.update(overrides)
    return Lora(**body)


def test_collect_is_empty_without_registered_urls(settings):
    assert run(model_sources.collect(Options(loras=[_lora("a.safetensors")]))) == []


def test_collect_lists_a_lora_with_its_page(settings, monkeypatch):
    monkeypatch.setattr(
        config,
        "_settings",
        Settings(model_download_urls={"sakura.safetensors": HF_URL}),
    )
    sources = run(model_sources.collect(Options(loras=[_lora("sakura.safetensors")])))
    assert len(sources) == 1
    source = sources[0]
    assert (source.kind, source.filename, source.label) == (
        "lora",
        "sakura.safetensors",
        "サクラ",
    )
    assert source.page_url == HF_PAGE
    assert source.download_url == HF_URL
    assert source.host == "huggingface"
    assert source.usage == ["画像用（family `krea2`）"]


def test_collect_marks_a_video_lora(settings, monkeypatch):
    monkeypatch.setattr(
        config,
        "_settings",
        Settings(model_download_urls={"motion.safetensors": HF_URL}),
    )
    sources = run(
        model_sources.collect(
            Options(loras=[_lora("motion.safetensors", target="video")])
        )
    )
    assert sources[0].usage == ["動画用"]


def test_collect_lists_a_model_slot_file(settings, monkeypatch):
    """ワークフローのテンプレート既定ファイルも、URL が登録されていれば出す。"""
    from app.workflow import model_slots

    slot = next(s for s in model_slots() if s.default)
    monkeypatch.setattr(
        config, "_settings", Settings(model_download_urls={slot.default: HF_URL})
    )
    sources = run(model_sources.collect(Options()))
    entry = next(s for s in sources if s.filename == slot.default)
    assert entry.kind == "model"
    assert entry.page_url == HF_PAGE
    assert any(slot.workflow_id in where for where in entry.usage)


def test_collect_lists_a_registered_candidate(settings, monkeypatch):
    """候補リスト（model_choices）に足しただけのファイルも対象。"""
    from app.workflow import model_slots

    slot = next(s for s in model_slots() if s.default)
    monkeypatch.setattr(
        config,
        "_settings",
        Settings(
            model_choices={"local": {slot.key: ["alt.safetensors"]}},
            model_download_urls={"alt.safetensors": HF_URL},
        ),
    )
    sources = run(model_sources.collect(Options()))
    assert [s.filename for s in sources] == ["alt.safetensors"]


def test_a_lora_is_not_listed_twice(settings, monkeypatch):
    monkeypatch.setattr(
        config,
        "_settings",
        Settings(model_download_urls={"sakura.safetensors": HF_URL}),
    )
    options = Options(loras=[_lora("sakura.safetensors"), _lora("sakura.safetensors")])
    assert len(run(model_sources.collect(options))) == 1
