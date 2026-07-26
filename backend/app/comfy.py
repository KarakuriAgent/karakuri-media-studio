"""ComfyUI HTTP client (SPEC §5).

The connection target is read from the settings on **every** request so that a
change in the settings screen takes effect without restarting the app.  All
transport / protocol failures are wrapped in :class:`ComfyError`.
"""

from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Any

import httpx

from .config import load_settings

DEFAULT_TIMEOUT = 30.0
UPLOAD_TIMEOUT = 300.0
DOWNLOAD_TIMEOUT = 600.0


class ComfyError(Exception):
    """Any failure while talking to ComfyUI (connection, timeout, HTTP status)."""


def _base_url() -> str:
    url = (load_settings().comfy_url or "").strip()
    if not url:
        raise ComfyError("comfy_url is not configured")
    return url.rstrip("/")


def _headers() -> dict[str, str]:
    api_key = (load_settings().comfy_api_key or "").strip()
    if not api_key:
        return {}
    # Comfy Cloud expects X-API-Key; some self-hosted auth proxies expect Bearer.
    return {"X-API-Key": api_key, "Authorization": f"Bearer {api_key}"}


def _api_prefix() -> str:
    """Comfy Cloud serves the compatible API under ``/api`` (local works bare)."""
    host = _base_url().split("://", 1)[-1].split("/", 1)[0].lower()
    return "/api" if host.endswith("comfy.org") else ""


def _client(timeout: float) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=_base_url(), headers=_headers(), timeout=timeout, follow_redirects=True
    )


async def _request(
    method: str,
    path: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    **kwargs: Any,
) -> httpx.Response:
    try:
        async with _client(timeout) as client:
            response = await client.request(method, path, **kwargs)
            response.raise_for_status()
            return response
    except httpx.HTTPStatusError as exc:
        body = exc.response.text[:500]
        raise ComfyError(
            f"ComfyUI {method} {path} failed: HTTP {exc.response.status_code} {body}"
        ) from exc
    except httpx.HTTPError as exc:
        raise ComfyError(f"ComfyUI {method} {path} failed: {exc}") from exc


def _json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError as exc:
        raise ComfyError(f"ComfyUI returned non-JSON response: {exc}") from exc


# --------------------------------------------------------------------------
# object_info
# --------------------------------------------------------------------------

async def get_object_info(class_type: str | None = None) -> dict[str, Any]:
    path = _api_prefix() + (f"/object_info/{class_type}" if class_type else "/object_info")
    data = _json(await _request("GET", path))
    if not isinstance(data, dict):
        raise ComfyError("unexpected /object_info payload")
    return data


def combo_options(info: dict[str, Any], class_type: str, field: str) -> list[str]:
    node = info.get(class_type)
    if not node:
        raise ComfyError(f"node class '{class_type}' is not available on ComfyUI")
    spec = (node.get("input", {}).get("required", {}) or {}).get(field)
    if spec is None:
        spec = (node.get("input", {}).get("optional", {}) or {}).get(field)
    if not isinstance(spec, list) or not spec:
        raise ComfyError(f"{class_type}.{field} has no options in /object_info")
    options = spec[0]
    if isinstance(options, dict):  # newer combo spec: {"options": [...]}
        options = options.get("options", [])
    if not isinstance(options, list):
        raise ComfyError(f"{class_type}.{field} is not a combo input")
    return [str(o) for o in options]


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
    """Upload an image *or audio* file to the ComfyUI input dir via /upload/image.

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


async def get_history(prompt_id: str) -> dict[str, Any]:
    """GET /history/{prompt_id}; returns the entry for that id ({} while queued)."""
    payload = _json(await _request("GET", f"{_api_prefix()}/history/{prompt_id}"))
    if not isinstance(payload, dict):
        raise ComfyError("unexpected /history payload")
    entry = payload.get(prompt_id, payload if "outputs" in payload else {})
    return entry if isinstance(entry, dict) else {}


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
        raise ComfyError(
            f"ComfyUI /view {filename} failed: HTTP {exc.response.status_code}"
        ) from exc
    except httpx.HTTPError as exc:
        raise ComfyError(f"ComfyUI /view {filename} failed: {exc}") from exc
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
