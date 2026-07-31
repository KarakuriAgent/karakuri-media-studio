"""登録済みモデル・LoRA の「取得元ページ」（AGENT-MODE §3.1）。

設定の ``model_download_urls``（ファイル名 -> ダウンロード URL、SPEC §3.3）は
ダウンロード用の直リンクなので、そのままエージェントに渡しても**使い方を調べる**
役には立たない。ここでは配布ページ URL に変換し、エージェントのシステムプロンプト
（CHOICES の隣、MODEL SOURCES セクション）に焼き込むための一覧を組み立てる。

変換のしかた:

- Hugging Face: ``…/resolve/<rev>/<path>`` や ``…/blob/<rev>/<path>`` から
  ``https://huggingface.co/<org>/<repo>`` を切り出すだけ（ネットワーク不要）。
- Civitai: ダウンロード URL は ``…/api/download/models/<versionId>`` で modelId を
  含まないため、``https://civitai.com/api/v1/model-versions/<versionId>`` を 1 回
  叩いて ``modelId`` を引く。結果は ``Settings.model_page_urls``（ダウンロード URL
  -> ページ URL）にキャッシュするので、2 回目以降は API を叩かない。失敗しても
  例外は投げず、ページ URL 無し（ダウンロード URL だけ）として扱う。
"""

from __future__ import annotations

import asyncio
import logging
from urllib.parse import parse_qs, urlparse, urlunparse

import httpx

from .config import load_settings, update_settings
from .model_download import CIVITAI_HOSTS, HF_HOSTS, auth_headers
from .models import Lora, ModelSource, Options
from .workflow import model_slots

log = logging.getLogger(__name__)

#: 調べ物のための 1 回きりの問い合わせなので短く諦める
TIMEOUT = httpx.Timeout(10.0)
CIVITAI_VERSION_API = "https://civitai.com/api/v1/model-versions/{version_id}"
#: 1 ファイルにつきプロンプトへ出す「使われている場所」の上限
MAX_USAGE = 4


def _host_is(url: str, known: tuple[str, ...]) -> bool:
    """ホスト名の完全一致かサブドメイン一致（``model_download.auth_headers`` と同じ判定）。"""
    host = (urlparse(url).hostname or "").lower()
    return any(host == name or host.endswith(f".{name}") for name in known)


def host_of(url: str) -> str:
    """``'huggingface'`` / ``'civitai'`` / ``''``（それ以外）。"""
    if _host_is(url, HF_HOSTS):
        return "huggingface"
    if _host_is(url, CIVITAI_HOSTS):
        return "civitai"
    return ""


#: リポジトリ名の前に付く名前空間（`huggingface.co/datasets/<org>/<repo>` 等）
_HF_NAMESPACES = ("datasets", "spaces")
#: リポジトリページではなくファイルを指すことを示すパス要素
_HF_FILE_MARKERS = ("resolve", "blob", "raw", "tree")


def huggingface_page_url(url: str) -> str:
    """HF のファイル URL からリポジトリページ URL を切り出す（無理なら空）。

    ``https://huggingface.co/org/repo/resolve/main/dir/file.safetensors``
    -> ``https://huggingface.co/org/repo``。リポジトリページを直接登録して
    あった場合はそれをそのまま（クエリだけ落として）返す。

    CDN のホスト（``cdn-lfs.hf.co`` 等）はパスがリポジトリ名になっていないので
    対象外にする（``/repos/ab/cd/…`` を org/repo と誤読しないため）。
    """
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().removeprefix("www.")
    if host not in HF_HOSTS:
        return ""
    parts = [part for part in parsed.path.split("/") if part]
    prefix: list[str] = []
    if parts and parts[0] in _HF_NAMESPACES:
        prefix, parts = parts[:1], parts[1:]
    if len(parts) < 2 or parts[0] in _HF_FILE_MARKERS:
        return ""
    path = "/".join([*prefix, parts[0], parts[1]])
    return urlunparse((parsed.scheme or "https", parsed.netloc, f"/{path}", "", "", ""))


def civitai_version_id(url: str) -> str:
    """Civitai のダウンロード URL から versionId を取り出す（無ければ空）。"""
    parsed = urlparse(url)
    parts = [part for part in parsed.path.split("/") if part]
    if parts[:3] == ["api", "download", "models"] and len(parts) >= 4:
        candidate = parts[3]
        if candidate.isdigit():
            return candidate
    return ""


def _civitai_known_page(url: str) -> str:
    """既にモデルページ（``/models/<id>``）が登録されていればそれを返す。"""
    parsed = urlparse(url)
    parts = [part for part in parsed.path.split("/") if part]
    if parts[:1] == ["models"] and len(parts) >= 2 and parts[1].isdigit():
        version = (parse_qs(parsed.query).get("modelVersionId") or [""])[0]
        query = f"modelVersionId={version}" if version.isdigit() else ""
        return urlunparse(
            (parsed.scheme or "https", parsed.netloc, f"/models/{parts[1]}", "", query, "")
        )
    return ""


def offline_page_url(url: str) -> str:
    """ネットワーク無しで分かるページ URL（分からなければ空）。"""
    host = host_of(url)
    if host == "huggingface":
        return huggingface_page_url(url)
    if host == "civitai":
        return _civitai_known_page(url)
    return ""


async def _civitai_page_url(client: httpx.AsyncClient, url: str) -> str:
    """Civitai API で versionId -> modelId を引き、モデルページ URL を組み立てる。"""
    version_id = civitai_version_id(url)
    if not version_id:
        return ""
    api = CIVITAI_VERSION_API.format(version_id=version_id)
    try:
        response = await client.get(api, headers=auth_headers(api))
        response.raise_for_status()
        model_id = (response.json() or {}).get("modelId")
    except Exception as exc:  # noqa: BLE001 - 調べ物の補助なので落とさない
        log.info("civitai model-version lookup failed (%s): %s", version_id, exc)
        return ""
    if not isinstance(model_id, int) and not str(model_id or "").isdigit():
        return ""
    return f"https://civitai.com/models/{model_id}?modelVersionId={version_id}"


async def resolve_page_urls(urls: list[str]) -> dict[str, str]:
    """ダウンロード URL -> 配布ページ URL（解決できなかったものは入らない）。

    HF は文字列処理だけで済ませ、Civitai だけを（キャッシュに無いものに限り）
    API で引く。引けた結果は ``Settings.model_page_urls`` に書き戻す。
    """
    settings = load_settings()
    cache = dict(settings.model_page_urls)
    resolved: dict[str, str] = {}
    pending: list[str] = []
    for url in dict.fromkeys(u for u in urls if u):
        page = offline_page_url(url)
        if page:
            resolved[url] = page
        elif cached := cache.get(url):
            resolved[url] = cached
        elif host_of(url) == "civitai" and civitai_version_id(url):
            pending.append(url)

    if not pending:
        return resolved

    async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
        pages = await asyncio.gather(
            *(_civitai_page_url(client, url) for url in pending)
        )
    found = {url: page for url, page in zip(pending, pages) if page}
    if found:
        resolved.update(found)
        update_settings({"model_page_urls": {**cache, **found}})
    return resolved


def _lora_usage(lora: Lora) -> list[str]:
    target = "動画用" if lora.target == "video" else "画像用"
    if lora.target != "video" and lora.family:
        target += f"（family `{lora.family}`）"
    return [target]


def _model_usage(options: Options) -> dict[str, list[str]]:
    """モデルファイル名 -> 使っているワークフロー / スロットの説明。

    候補リストが 1 件のスロット（テンプレート既定のまま）も対象にするため、
    ``options.model_slots``（2 件以上のものだけ）ではなく全スロットを見る。
    """
    settings = load_settings()
    usage: dict[str, list[str]] = {}
    for slot in model_slots(settings.overrides_for(), settings.choices_for()):
        where = f"`{slot.workflow_id}`: {slot.label or slot.class_type}"
        for name in slot.choices:
            entries = usage.setdefault(name, [])
            if where not in entries:
                entries.append(where)
    return usage


async def collect(options: Options) -> list[ModelSource]:
    """エージェントに見せる取得元一覧（LoRA が先、次にモデルファイル）。

    設定に取得元 URL が登録されているファイルだけが対象。URL が 1 件も無ければ
    空リストを返し、プロンプトにはセクションごと出さない。
    """
    urls = load_settings().model_download_urls
    if not urls:
        return []

    sources: list[ModelSource] = []
    seen: set[str] = set()
    for lora in options.loras:
        url = (urls.get(lora.lora_name) or "").strip()
        if not url or lora.lora_name in seen:
            continue
        seen.add(lora.lora_name)
        sources.append(
            ModelSource(
                filename=lora.lora_name,
                kind="lora",
                label=lora.display_name,
                usage=_lora_usage(lora),
                download_url=url,
                host=host_of(url),
            )
        )

    for name, where in _model_usage(options).items():
        url = (urls.get(name) or "").strip()
        if not url or name in seen:
            continue
        seen.add(name)
        sources.append(
            ModelSource(
                filename=name,
                usage=where[:MAX_USAGE],
                download_url=url,
                host=host_of(url),
            )
        )

    pages = await resolve_page_urls([source.download_url for source in sources])
    for source in sources:
        source.page_url = pages.get(source.download_url, "")
    return sources
