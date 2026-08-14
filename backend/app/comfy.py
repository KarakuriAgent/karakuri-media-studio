"""ComfyUI HTTP client (SPEC §5).

The connection target is read from the settings on **every** request so that a
change in the settings screen (or of the 接続先 dropdown in the generation form)
takes effect without restarting the app: which of the ComfyCloud / RunPod /
local profiles is used comes from ``Settings.comfy_target``.  All transport /
protocol failures are wrapped in :class:`ComfyError`.
"""

from __future__ import annotations

import mimetypes
import re
from pathlib import Path
from typing import Any

import httpx

from .config import load_settings
from .models import ComfyTarget

DEFAULT_TIMEOUT = 30.0
UPLOAD_TIMEOUT = 300.0
DOWNLOAD_TIMEOUT = 600.0


class ComfyError(Exception):
    """Any failure while talking to ComfyUI (connection, timeout, HTTP status).

    ``unreachable`` は「ComfyUI 自体に届いていない」失敗（接続不可・タイムアウト
    と、リバースプロキシ / Cloudflare Tunnel が返すゲートウェイ系ステータス）。
    :func:`display_error` がこれを見て、接続先に合わせた案内文に置き換える。
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        unreachable: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.unreachable = unreachable


#: ComfyUI へ届く前に潰れたことを表すステータス（Cloudflare Tunnel の 5xx を含む。
#: 530 = Origin DNS error / 1033 tunnel error、52x = origin unreachable）
UNREACHABLE_STATUSES = frozenset({502, 503, 504, 520, 521, 522, 523, 524, 530})


def _body_excerpt(response: httpx.Response) -> str:
    """エラー本文の抜粋。HTML はページごと出さない（UI に生 HTML を流さない）。

    Pod が落ちているときの Cloudflare Tunnel は数 KB のエラーページを返すので、
    そのまま連結すると UI のバナーがページ 1 枚分の HTML で埋まる。HTML と分かる
    ものは `<title>` だけを拾い、無ければ種別だけを書く。
    """
    text = (response.text or "").strip()
    content_type = response.headers.get("content-type", "").lower()
    if not ("html" in content_type or text[:200].lstrip().lower().startswith(
        ("<!doctype html", "<html")
    )):
        return text[:500]
    match = re.search(r"<title[^>]*>(.*?)</title>", text[:4000], re.I | re.S)
    title = " ".join(match.group(1).split())[:120] if match else ""
    return f"({title})" if title else "(HTML エラーページ)"


def display_error(exc: ComfyError) -> str:
    """UI に出す文言（接続先に合わせた案内。SPEC §5 / §5.1）。

    RunPod 運用では「Pod を落としているあいだは繋がらない」のが普通の状態なので、
    Cloudflare Tunnel の生のエラーではなく、次に何をすればよいかを出す。
    """
    if not exc.unreachable:
        return str(exc)
    settings = load_settings()
    if settings.comfy_target != "runpod":
        return str(exc)
    if settings.runpod_enabled:
        return (
            "RunPod の ComfyUI が起動していません。ジョブを投入すると自動で Pod を"
            "起動します（モデル名などは手入力でも続行できます）。"
        )
    return (
        "RunPod の ComfyUI に接続できません。設定画面から Pod を起動するか、"
        "RunPod ComfyUI URL を確認してください。"
    )


def profile(target: ComfyTarget | None = None) -> tuple[str, str]:
    """その接続先の (URL, API キー)。``None`` なら現在の接続先。

    ``target`` を渡せるのは、設定ページが「いま繋いでいない環境」を相手にする
    ことがあるため（不足モデルの一括判定など、SPEC §3.3 / §5）。
    """
    settings = load_settings()
    if target is None or target == settings.comfy_target:
        return settings.active_comfy_url(), settings.active_comfy_api_key()
    switched = settings.model_copy(update={"comfy_target": target})
    return switched.active_comfy_url(), switched.active_comfy_api_key()


def _base_url(target: ComfyTarget | None = None) -> str:
    url = (profile(target)[0] or "").strip()
    if not url:
        chosen = target or load_settings().comfy_target
        raise ComfyError(f"ComfyUI URL is not configured for target '{chosen}'")
    return url.rstrip("/")


def _headers(target: ComfyTarget | None = None) -> dict[str, str]:
    api_key = (profile(target)[1] or "").strip()
    if not api_key:
        return {}
    # Comfy Cloud expects X-API-Key; some self-hosted auth proxies expect Bearer.
    return {"X-API-Key": api_key, "Authorization": f"Bearer {api_key}"}


def is_cloud(target: ComfyTarget | None = None) -> bool:
    """その接続先が Comfy Cloud か（URL のホストで見る、SPEC §5）。

    プロファイル名（``comfy_target``）ではなくホスト名で判定するのは、``runpod``
    のプロファイルに Cloud の URL を入れる使い方もできるため。URL が未設定なら
    「クラウドではない」扱いにする（ここで例外を投げると、接続先を決めるだけの
    呼び出し側が全部 try で囲む羽目になる）。
    """
    url = (profile(target)[0] or "").strip()
    host = url.split("://", 1)[-1].split("/", 1)[0].lower()
    return host.endswith("comfy.org")


def _api_prefix(target: ComfyTarget | None = None) -> str:
    """Comfy Cloud serves the compatible API under ``/api`` (local works bare)."""
    # URL が未設定なら _base_url が ComfyError を投げる（従来どおり）
    _base_url(target)
    return "/api" if is_cloud(target) else ""


def _client(timeout: float, target: ComfyTarget | None = None) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=_base_url(target),
        headers=_headers(target),
        timeout=timeout,
        follow_redirects=True,
    )


async def _request(
    method: str,
    path: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    target: ComfyTarget | None = None,
    **kwargs: Any,
) -> httpx.Response:
    try:
        async with _client(timeout, target) as client:
            response = await client.request(method, path, **kwargs)
            response.raise_for_status()
            return response
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        body = _body_excerpt(exc.response)
        raise ComfyError(
            f"ComfyUI {method} {path} failed: HTTP {status} {body}".rstrip(),
            status_code=status,
            unreachable=status in UNREACHABLE_STATUSES,
        ) from exc
    except httpx.HTTPError as exc:
        # 接続不可・タイムアウト: そもそも ComfyUI に届いていない
        raise ComfyError(
            f"ComfyUI {method} {path} failed: {exc}", unreachable=True
        ) from exc


def _json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError as exc:
        raise ComfyError(f"ComfyUI returned non-JSON response: {exc}") from exc


# --------------------------------------------------------------------------
# object_info
# --------------------------------------------------------------------------

async def get_object_info(
    class_type: str | None = None, *, target: ComfyTarget | None = None
) -> dict[str, Any]:
    """``/object_info``（``target`` を渡すと、その接続先の ComfyUI に聞く）。"""
    path = _api_prefix(target) + (
        f"/object_info/{class_type}" if class_type else "/object_info"
    )
    data = _json(await _request("GET", path, target=target))
    if not isinstance(data, dict):
        raise ComfyError("unexpected /object_info payload")
    return data


def combo_options(info: dict[str, Any], class_type: str, field: str) -> list[str]:
    """Extract combo choices from an /object_info entry.

    Known spec shapes across ComfyUI versions / Comfy Cloud:
      classic:  [["a", "b"], {...}]
      dict:     [{"options": ["a", "b"]}, {...}]
      V3/cloud: ["COMBO", {"options": ["a", "b"]}]
    """
    node = info.get(class_type)
    if not node:
        raise ComfyError(f"node class '{class_type}' is not available on ComfyUI")
    spec = (node.get("input", {}).get("required", {}) or {}).get(field)
    if spec is None:
        spec = (node.get("input", {}).get("optional", {}) or {}).get(field)
    if not isinstance(spec, list) or not spec:
        raise ComfyError(f"{class_type}.{field} has no options in /object_info")
    options = spec[0]
    config = spec[1] if len(spec) > 1 and isinstance(spec[1], dict) else {}
    if isinstance(options, str):
        options = config.get("options", config.get("values", config.get("choices")))
    elif isinstance(options, dict):
        options = options.get("options", options.get("values", []))
    if not isinstance(options, list):
        raise ComfyError(
            f"{class_type}.{field} is not a combo input (spec: {str(spec)[:300]})"
        )
    return [str(o) for o in options]


#: 接続先（URL）-> ラテント連続性のカスタムノードが揃っているか。
#: ``/object_info`` は全ノードの定義を返す重い応答なので、接続先ごとに 1 回だけ
#: 聞いて覚えておく（custom node の入れ替えはアプリの再起動を伴う運用なので、
#: プロセス内のキャッシュで足りる）。
_latent_context_cache: dict[str, bool] = {}


def clear_latent_context_cache() -> None:
    """:func:`latent_context_support` の覚え書きを捨てる（設定変更・テスト用）。"""
    _latent_context_cache.clear()


async def latent_context_support(target: ComfyTarget | None = None) -> bool:
    """その接続先に Motion Context 系のカスタムノードが揃っているか。

    ラテント連続性（``minimax_h3_r2v_context``）は
    :data:`app.workflows.LATENT_CONTEXT_CLASS_TYPES` のノードを全部使うので、
    1 つでも欠けていたら False。接続先そのものに届かないときは
    :class:`ComfyError` がそのまま上がる（「入っていない」と混同しないため）。
    """
    from .workflows import LATENT_CONTEXT_CLASS_TYPES

    key = _base_url(target)
    cached = _latent_context_cache.get(key)
    if cached is not None:
        return cached
    info = await get_object_info(target=target)
    available = all(name in info for name in LATENT_CONTEXT_CLASS_TYPES)
    _latent_context_cache[key] = available
    return available


async def get_aspect_ratio_options() -> list[str]:
    """aspect_ratio choices of ResolutionSelector (`366`)."""
    return combo_options(
        await get_object_info("ResolutionSelector"), "ResolutionSelector", "aspect_ratio"
    )


async def get_lora_files() -> list[str]:
    """LoRA file names known to ComfyUI (LoraLoaderModelOnly.lora_name)."""
    return combo_options(
        await get_object_info("LoraLoaderModelOnly"),
        "LoraLoaderModelOnly",
        "lora_name",
    )


# --------------------------------------------------------------------------
# uploads / queue / results
# --------------------------------------------------------------------------

async def upload_file(path: str | Path, subfolder: str | None = None) -> str:
    """Upload an image, *audio* or *video* file to the ComfyUI input dir.

    ``/upload/image`` stores whatever it is given in the input directory, which
    is where LoadImage / LoadAudio / LoadVideo look for their files.

    Returns the name to put into the workflow (``subfolder/name`` when the
    server stored it in a subfolder).
    """
    src = Path(path)
    if not src.is_file():
        raise ComfyError(f"file not found: {src}")
    content_type = mimetypes.guess_type(src.name)[0] or "application/octet-stream"
    data: dict[str, str] = {"type": "input", "overwrite": "true"}
    if subfolder:
        data["subfolder"] = subfolder
    files = {"image": (src.name, src.read_bytes(), content_type)}
    payload = _json(
        await _request(
            "POST",
            _api_prefix() + "/upload/image",
            data=data,
            files=files,
            timeout=UPLOAD_TIMEOUT,
        )
    )
    name = payload.get("name") if isinstance(payload, dict) else None
    if not name:
        raise ComfyError(f"unexpected /upload/image response: {payload!r}")
    stored_subfolder = payload.get("subfolder") or ""
    return f"{stored_subfolder}/{name}" if stored_subfolder else str(name)


async def queue_prompt(workflow: dict[str, Any], client_id: str) -> str:
    """POST /prompt and return the prompt_id."""
    payload = _json(
        await _request(
            "POST",
            _api_prefix() + "/prompt",
            json={"prompt": workflow, "client_id": client_id},
        )
    )
    prompt_id = payload.get("prompt_id") if isinstance(payload, dict) else None
    if not prompt_id:
        raise ComfyError(f"ComfyUI rejected the prompt: {payload!r}")
    return str(prompt_id)


async def interrupt() -> None:
    """Best-effort ``POST /interrupt``. Comfy Cloud には無いので呼ばない。"""
    if is_cloud():
        return
    await _request("POST", "/interrupt", timeout=2.0)


async def get_history(prompt_id: str) -> dict[str, Any]:
    """Job state in local ``/history`` entry shape ({} while queued).

    Local ComfyUI: ``GET /history/{prompt_id}``.  Comfy Cloud has no history
    endpoint (404: "Use /api/jobs/{prompt_id} instead"), so the cloud job
    object is fetched and translated into the same shape.
    """
    if _api_prefix():
        return await _get_cloud_job(prompt_id)
    payload = _json(await _request("GET", f"/history/{prompt_id}"))
    if not isinstance(payload, dict):
        raise ComfyError("unexpected /history payload")
    entry = payload.get(prompt_id, payload if "outputs" in payload else {})
    return entry if isinstance(entry, dict) else {}


# Comfy Cloud job statuses -> local history semantics
_CLOUD_ERROR_STATUSES = {"failed", "cancelled", "canceled", "error"}


async def _get_cloud_job(prompt_id: str) -> dict[str, Any]:
    """GET /api/jobs/{prompt_id} (Comfy Cloud) -> local history entry shape."""
    payload = _json(await _request("GET", f"/api/jobs/{prompt_id}"))
    if not isinstance(payload, dict):
        raise ComfyError("unexpected /api/jobs payload")
    job = payload.get("job") if isinstance(payload.get("job"), dict) else payload
    status = str(job.get("status") or "").lower()
    outputs = job.get("outputs") if isinstance(job.get("outputs"), dict) else {}

    if status in _CLOUD_ERROR_STATUSES:
        message = (
            job.get("error")
            or job.get("error_message")
            or job.get("status_message")
            or f"Comfy Cloud job {status}"
        )
        return {
            "status": {
                "status_str": "error",
                "completed": False,
                "messages": [
                    ["execution_error", {"exception_message": str(message)}]
                ],
            }
        }
    if status == "completed":
        if not outputs:
            raise ComfyError(
                f"Comfy Cloud job {prompt_id} completed but returned no outputs"
            )
        return {
            "status": {"status_str": "success", "completed": True, "messages": []},
            "outputs": outputs,
        }
    # pending / in_progress (or unknown): keep polling
    return {
        "status": {
            "status_str": status or "pending",
            "completed": False,
            "messages": [],
        }
    }


async def download_view(
    filename: str,
    subfolder: str,
    type_: str,
    dest_path: str | Path,
) -> Path:
    """GET /view and stream the result into ``dest_path``."""
    dest = Path(dest_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    params = {"filename": filename, "subfolder": subfolder or "", "type": type_}
    try:
        async with _client(DOWNLOAD_TIMEOUT) as client:
            # Comfy Cloud answers with a 302 to a signed URL; follow_redirects
            # on the client covers both cloud and local.
            async with client.stream(
                "GET", _api_prefix() + "/view", params=params
            ) as response:
                response.raise_for_status()
                with dest.open("wb") as fh:
                    async for chunk in response.aiter_bytes():
                        fh.write(chunk)
    except httpx.HTTPStatusError as exc:
        # ボディはストリームなので読まない（本文は元から出していない）
        status = exc.response.status_code
        raise ComfyError(
            f"ComfyUI /view {filename} failed: HTTP {status}",
            status_code=status,
            unreachable=status in UNREACHABLE_STATUSES,
        ) from exc
    except httpx.HTTPError as exc:
        raise ComfyError(
            f"ComfyUI /view {filename} failed: {exc}", unreachable=True
        ) from exc
    except OSError as exc:
        raise ComfyError(f"could not write {dest}: {exc}") from exc
    return dest


def ws_url(client_id: str) -> str:
    """WebSocket URL for progress events (used by the job runner in WP3)."""
    base = _base_url()
    scheme = "wss" if base.startswith("https://") else "ws"
    host = base.split("://", 1)[-1]
    return f"{scheme}://{host}/ws?clientId={client_id}"


def ws_headers() -> dict[str, str]:
    """Auth headers for the progress WebSocket (needed by Comfy Cloud)."""
    return _headers()
