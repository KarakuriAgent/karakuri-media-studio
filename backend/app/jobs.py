"""Job execution service (SPEC §5 / §6 / §9).

One in-process asyncio queue with a single worker (SPEC §9 "ジョブ管理: アプリ内
asyncio キュー", §5 "同時実行は 1 ジョブ").  The worker is started/stopped from the
FastAPI lifespan.

Per job the runner:

1. uploads the reference audio / start frame to ComfyUI (``/upload/image``),
2. builds the API workflow from the job params (:mod:`app.workflow`),
3. queues it (``/prompt``) and follows the progress over the ComfyUI WebSocket,
   falling back to ``/history`` polling whenever the socket is unavailable,
4. downloads the artefacts into ``outputs/{job_id}/`` and extracts the last
   frame with ffmpeg,
5. records everything in the ``jobs`` table and broadcasts each transition to
   the browser over :mod:`app.ws`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite

from . import comfy, ws
from .config import load_settings
from .db import get_db
from .ids import new_id
from .models import (
    DEFAULT_NEGATIVE_PROMPT,
    GenerationParams,
    Job,
    JobContinue,
    JobCreate,
    JobRerun,
    LoraRef,
    missing_job_fields,
)
from .paths import ASSETS_DIR, OUTPUTS_DIR
from .workflow import build_workflow, load_template

log = logging.getLogger(__name__)

# Tunables (monkeypatched by the tests to keep them fast).
POLL_INTERVAL = 1.0
JOB_TIMEOUT = 6 * 60 * 60.0
FFMPEG = "ffmpeg"
SEED_MAX = 2**31 - 1

# history["outputs"][node] keys that may hold produced files
_FILE_LIST_KEYS = ("images", "videos", "gifs", "files", "audio", "video")

N_PREVIEW_IMAGE = "393"
N_SAVE_VIDEO = "75"

VIDEO_EXTS = {".mp4", ".webm", ".mkv", ".mov", ".avi", ".gif"}


class JobError(Exception):
    """A job could not be completed; the message is shown in the UI."""


class JobValidationError(Exception):
    """Invalid job request (mapped to HTTP 422 by the router)."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------
# assets
# --------------------------------------------------------------------------

def resolve_asset_path(value: str, *, field: str) -> Path:
    """Accept an absolute path inside ``assets/`` or an ``/assets/...`` URL."""
    raw = (value or "").strip()
    if not raw:
        raise JobValidationError(f"{field} is empty")
    if raw.startswith("/assets/"):
        candidate = ASSETS_DIR / raw[len("/assets/"):]
    else:
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = ASSETS_DIR / raw
    root = ASSETS_DIR.resolve()
    resolved = candidate.resolve()
    if root not in resolved.parents:
        raise JobValidationError(f"{field} must point inside {root}")
    if not resolved.is_file():
        raise JobValidationError(f"{field} not found: {resolved}")
    return resolved


def _output_url(path: str | None) -> str | None:
    if not path:
        return None
    try:
        return "/outputs/" + Path(path).resolve().relative_to(
            OUTPUTS_DIR.resolve()
        ).as_posix()
    except (ValueError, OSError):
        return None


def copy_into_assets(src: str | Path, kind: str = "image") -> Path:
    """Copy an arbitrary file (e.g. a job's last frame) into ``assets/{kind}/``."""
    source = Path(src)
    if not source.is_file():
        raise JobValidationError(f"file not found: {source}")
    dest_dir = ASSETS_DIR / kind
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{source.stem}_{new_id()}{source.suffix}"
    shutil.copy2(source, dest)
    return dest


# --------------------------------------------------------------------------
# persistence
# --------------------------------------------------------------------------

def _loads(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def row_to_job(row: aiosqlite.Row, *, include_workflow: bool = True) -> Job:
    data = dict(row)
    data["params"] = _loads(data.get("params"))
    data["workflow_json"] = _loads(data.get("workflow_json")) if include_workflow else {}
    data["image_url"] = _output_url(data.get("image_path"))
    data["video_url"] = _output_url(data.get("video_path"))
    data["last_frame_url"] = _output_url(data.get("last_frame_path"))
    return Job(**data)


async def get_job(job_id: str, *, include_workflow: bool = True) -> Job | None:
    async with get_db() as conn:
        async with conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)) as cur:
            row = await cur.fetchone()
    return row_to_job(row, include_workflow=include_workflow) if row else None


async def list_jobs(limit: int = 50, offset: int = 0) -> list[Job]:
    async with get_db() as conn:
        async with conn.execute(
            "SELECT * FROM jobs ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ) as cur:
            rows = await cur.fetchall()
    # The full API JSON is large; the list view only needs the metadata.
    return [row_to_job(r, include_workflow=False) for r in rows]


async def _update(job_id: str, **fields: Any) -> None:
    if not fields:
        return
    assignments = ", ".join(f"{k} = :{k}" for k in fields)
    async with get_db() as conn:
        await conn.execute(
            f"UPDATE jobs SET {assignments} WHERE id = :id", {**fields, "id": job_id}
        )
        await conn.commit()


async def _set_status(
    job_id: str,
    status: str,
    *,
    message: str | None = None,
    **fields: Any,
) -> None:
    await _update(job_id, status=status, **fields)
    await ws.publish(job_id, status, message=message)


async def delete_job(job_id: str) -> bool:
    async with get_db() as conn:
        cur = await conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        await conn.commit()
        deleted = cur.rowcount > 0
    if deleted:
        shutil.rmtree(OUTPUTS_DIR / job_id, ignore_errors=True)
    return deleted


async def _link_chat_session(chat_session_id: str | None, job_id: str) -> None:
    if not chat_session_id:
        return
    async with get_db() as conn:
        await conn.execute(
            "UPDATE chat_sessions SET job_id = ? WHERE id = ?",
            (job_id, chat_session_id),
        )
        await conn.commit()


# --------------------------------------------------------------------------
# creation
# --------------------------------------------------------------------------

def _seeds(seed: int | None) -> dict[str, Any]:
    """Resolve the seed. A random one is recorded so the run stays reproducible."""
    value = int(seed) if seed is not None else random.randint(0, SEED_MAX)
    return {"seed": value, "image_seed": value, "video_seeds": [value, value]}


def _validate(params: dict[str, Any]) -> None:
    missing = missing_job_fields(
        params.get("mode", ""),
        image_prompt=params.get("image_prompt"),
        video_prompt=params.get("video_prompt"),
        audio_path=params.get("audio_path"),
        source_image=params.get("source_image"),
    )
    if missing:
        raise JobValidationError(
            f"mode '{params.get('mode')}' requires: {', '.join(missing)}"
        )


async def _insert_job(
    *,
    mode: str,
    params: dict[str, Any],
    user_input: str | None,
    chat_session_id: str | None,
) -> Job:
    """Validate, persist a ``queued`` row and hand it to the worker."""
    _validate(params)

    # Fail fast on unusable asset paths (422 rather than a failed job).
    audio_path = params.get("audio_path")
    source_image = params.get("source_image")
    if audio_path:
        params["audio_path"] = str(resolve_asset_path(audio_path, field="audio_path"))
    if source_image:
        params["source_image"] = str(
            resolve_asset_path(source_image, field="source_image")
        )

    job_id = new_id()
    params["job_id"] = job_id
    row = {
        "id": job_id,
        "created_at": _now(),
        "mode": mode,
        "status": "queued",
        "user_input": user_input,
        "image_prompt": params.get("image_prompt") or None,
        "video_prompt": params.get("video_prompt") or None,
        "grok_raw": None,
        "params": json.dumps(params, ensure_ascii=False),
        "workflow_json": "{}",
        "comfy_prompt_id": None,
        "image_path": None,
        "video_path": None,
        "last_frame_path": None,
        "source_image": params.get("source_image"),
        "audio_path": params.get("audio_path"),
        "error": None,
    }
    async with get_db() as conn:
        await conn.execute(
            "INSERT INTO jobs (id, created_at, mode, status, user_input, image_prompt,"
            " video_prompt, grok_raw, params, workflow_json, comfy_prompt_id,"
            " image_path, video_path, last_frame_path, source_image, audio_path, error)"
            " VALUES (:id, :created_at, :mode, :status, :user_input, :image_prompt,"
            " :video_prompt, :grok_raw, :params, :workflow_json, :comfy_prompt_id,"
            " :image_path, :video_path, :last_frame_path, :source_image, :audio_path,"
            " :error)",
            row,
        )
        await conn.commit()

    await _link_chat_session(chat_session_id, job_id)
    await ws.publish(job_id, "queued", message="queued")
    await runner.submit(job_id)

    job = await get_job(job_id)
    assert job is not None
    return job


def _params_from_create(payload: JobCreate) -> dict[str, Any]:
    params: dict[str, Any] = {
        "mode": payload.mode,
        "aspect_ratio": payload.aspect_ratio,
        "megapixels": payload.megapixels,
        "loras": [lora.model_dump() for lora in payload.loras],
        "trigger_text": payload.trigger_text,
        "image_prompt": payload.image_prompt,
        "video_prompt": payload.video_prompt,
        "negative_prompt": payload.negative_prompt,
        "duration": payload.duration,
        "fps": payload.fps,
        "audio_path": payload.audio_path,
        "source_image": payload.source_image,
    }
    params.update(_seeds(payload.seed))
    return params


async def create_job(payload: JobCreate) -> Job:
    return await _insert_job(
        mode=payload.mode,
        params=_params_from_create(payload),
        user_input=payload.user_input,
        chat_session_id=payload.chat_session_id,
    )


async def rerun_job(job_id: str, payload: JobRerun) -> Job:
    """New job from the stored *params* (rebuilt, not replayed from workflow_json)."""
    source = await get_job(job_id)
    if source is None:
        raise LookupError(job_id)
    params = dict(source.params)
    params.pop("job_id", None)
    if payload.seed is not None:
        params.update(_seeds(payload.seed))
    elif payload.randomize_seed:
        params.update(_seeds(None))
    else:
        params.update(_seeds(params.get("seed")))
    return await _insert_job(
        mode=params.get("mode", source.mode),
        params=params,
        user_input=source.user_input,
        chat_session_id=None,
    )


async def continue_job(job_id: str, payload: JobContinue) -> Job:
    """Start a mode-B job from the last frame of ``job_id`` (SPEC §2)."""
    source = await get_job(job_id)
    if source is None:
        raise LookupError(job_id)
    if not source.last_frame_path or not Path(source.last_frame_path).is_file():
        raise JobValidationError("source job has no last frame to continue from")

    start_image = copy_into_assets(source.last_frame_path, "image")

    prev = dict(source.params)
    params: dict[str, Any] = {
        "mode": "i2v",
        "aspect_ratio": payload.aspect_ratio or prev.get("aspect_ratio", "4:3 (Standard)"),
        "megapixels": (
            payload.megapixels
            if payload.megapixels is not None
            else prev.get("megapixels", 1.0)
        ),
        "loras": prev.get("loras", []),
        "trigger_text": prev.get("trigger_text", ""),
        "image_prompt": prev.get("image_prompt", ""),
        "video_prompt": payload.video_prompt or prev.get("video_prompt", ""),
        "negative_prompt": (
            payload.negative_prompt
            or prev.get("negative_prompt")
            or DEFAULT_NEGATIVE_PROMPT
        ),
        "duration": (
            payload.duration if payload.duration is not None else prev.get("duration", 10.0)
        ),
        "fps": payload.fps if payload.fps is not None else prev.get("fps", 25),
        "audio_path": payload.audio_path or prev.get("audio_path"),
        "source_image": str(start_image),
        "continued_from": source.id,
    }
    params.update(_seeds(payload.seed))
    return await _insert_job(
        mode="i2v",
        params=params,
        user_input=payload.user_input or source.user_input,
        chat_session_id=payload.chat_session_id,
    )


# --------------------------------------------------------------------------
# execution
# --------------------------------------------------------------------------

def _generation_params(job: Job, audio_name: str, start_image_name: str) -> GenerationParams:
    p = job.params
    return GenerationParams(
        mode=job.mode,
        job_id=job.id,
        aspect_ratio=p.get("aspect_ratio", "4:3 (Standard)"),
        megapixels=float(p.get("megapixels", 1.0)),
        loras=[LoraRef(**lora) for lora in p.get("loras", [])],
        trigger_text=p.get("trigger_text", ""),
        image_prompt=p.get("image_prompt", ""),
        video_prompt=p.get("video_prompt", ""),
        negative_prompt=p.get("negative_prompt") or DEFAULT_NEGATIVE_PROMPT,
        duration=float(p.get("duration", 10.0)),
        fps=int(p.get("fps", 25)),
        image_seed=int(p.get("image_seed", 0)),
        video_seeds=[int(s) for s in p.get("video_seeds", [])],
        audio_name=audio_name,
        start_image_name=start_image_name,
    )


def _pick_output(outputs: dict[str, Any], node_id: str) -> dict[str, Any] | None:
    """First produced file of ``node_id`` in a ``/history`` outputs mapping."""
    node = outputs.get(node_id)
    if not isinstance(node, dict):
        return None
    keys = [k for k in _FILE_LIST_KEYS if k in node]
    keys += [k for k in node if k not in keys]
    for key in keys:
        values = node.get(key)
        if isinstance(values, dict):
            values = [values]
        if not isinstance(values, list):
            continue
        for item in values:
            if isinstance(item, dict) and item.get("filename"):
                return item
    return None


def _history_error(entry: dict[str, Any]) -> str | None:
    status = entry.get("status")
    if not isinstance(status, dict):
        return None
    if status.get("status_str") != "error":
        return None
    details: list[str] = []
    for message in status.get("messages") or []:
        if isinstance(message, list) and len(message) == 2 and isinstance(message[1], dict):
            data = message[1]
            text = data.get("exception_message") or data.get("exception_type")
            node = data.get("node_type") or data.get("node_id")
            if text:
                details.append(f"{node}: {text}" if node else str(text))
    return "ComfyUI reported an execution error" + (
        ": " + "; ".join(details) if details else ""
    )


async def _ws_progress(
    client_id: str, prompt_id: str, job_id: str, finished: asyncio.Event
) -> None:
    """Relay ComfyUI ``executing`` / ``progress`` events. Never raises."""
    try:
        import websockets
    except ImportError:  # pragma: no cover - dependency is pinned
        log.warning("websockets is not installed; falling back to history polling")
        return

    url = comfy.ws_url(client_id)
    try:
        async with websockets.connect(
            url,
            max_size=None,
            open_timeout=10,
            additional_headers=comfy.ws_headers() or None,
        ) as socket:
            async for raw in socket:
                if isinstance(raw, (bytes, bytearray)):
                    continue  # binary preview frames
                try:
                    message = json.loads(raw)
                except ValueError:
                    continue
                if not isinstance(message, dict):
                    continue
                kind = message.get("type")
                data = message.get("data") or {}
                if not isinstance(data, dict):
                    continue
                mine = data.get("prompt_id")
                if mine is not None and mine != prompt_id:
                    continue

                if kind == "progress":
                    maximum = data.get("max") or 0
                    value = data.get("value") or 0
                    await ws.publish(
                        job_id,
                        "running",
                        node=str(data.get("node") or "") or None,
                        progress=(value / maximum) if maximum else None,
                    )
                elif kind == "executing":
                    node = data.get("node")
                    if node is None:
                        finished.set()
                        return
                    await ws.publish(job_id, "running", node=str(node))
                elif kind in ("execution_error", "execution_interrupted"):
                    finished.set()
                    return
                elif kind == "execution_success":
                    finished.set()
                    return
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - progress is best-effort (§5)
        log.info("ComfyUI progress socket unavailable (%s); polling instead", exc)


async def _wait_for_result(prompt_id: str, client_id: str, job_id: str) -> dict[str, Any]:
    """Wait for the prompt to finish; returns the ``/history`` entry.

    The WebSocket only speeds things up / feeds the progress bar — completion is
    always confirmed through ``/history`` so a dropped socket cannot stall a job.
    """
    finished = asyncio.Event()
    watcher = asyncio.create_task(_ws_progress(client_id, prompt_id, job_id, finished))
    loop = asyncio.get_running_loop()
    deadline = loop.time() + JOB_TIMEOUT
    try:
        while True:
            try:
                await asyncio.wait_for(finished.wait(), timeout=POLL_INTERVAL)
            except (asyncio.TimeoutError, TimeoutError):
                pass
            finished.clear()

            entry = await comfy.get_history(prompt_id)
            error = _history_error(entry)
            if error:
                raise JobError(error)
            outputs = entry.get("outputs")
            if isinstance(outputs, dict) and outputs:
                return entry
            if loop.time() > deadline:
                raise JobError(
                    f"timed out after {JOB_TIMEOUT:.0f}s waiting for ComfyUI"
                )
    finally:
        watcher.cancel()
        try:
            await watcher
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass


async def extract_last_frame(video_path: Path, dest: Path) -> Path:
    """SPEC §6: ``ffmpeg -sseof -0.5 -i video.mp4 -update 1 -q:v 1 last_frame.png``.

    ``-sseof`` fails on clips shorter than the offset, so a full-decode variant
    (``-update 1`` keeps overwriting the same file, leaving the final frame)
    is used as a fallback.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    attempts = (
        [FFMPEG, "-y", "-sseof", "-0.5", "-i", str(video_path),
         "-update", "1", "-q:v", "1", str(dest)],
        [FFMPEG, "-y", "-i", str(video_path),
         "-update", "1", "-q:v", "1", str(dest)],
    )
    last_error = ""
    for cmd in attempts:
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise JobError(
                f"ffmpeg is required for last-frame extraction but was not found ({exc})"
            ) from exc
        _, stderr = await proc.communicate()
        if proc.returncode == 0 and dest.is_file() and dest.stat().st_size > 0:
            return dest
        last_error = (stderr or b"").decode("utf-8", "replace").strip()[-500:]
    raise JobError(f"ffmpeg could not extract the last frame: {last_error}")


async def _download_outputs(job: Job, entry: dict[str, Any]) -> dict[str, Any]:
    """Persist the artefacts of one job into ``outputs/{job_id}/`` (SPEC §6)."""
    outputs = entry.get("outputs") or {}
    job_dir = OUTPUTS_DIR / job.id
    job_dir.mkdir(parents=True, exist_ok=True)
    updates: dict[str, Any] = {}

    if job.mode in ("full", "image_only"):
        item = _pick_output(outputs, N_PREVIEW_IMAGE)
        if item is None:
            raise JobError(f"no image output on node {N_PREVIEW_IMAGE}")
        # Mode A previews live in the temp dir and vanish on restart -> save now.
        default_type = "temp" if job.mode == "full" else "output"
        suffix = Path(str(item["filename"])).suffix or ".png"
        dest = job_dir / f"image{suffix}"
        await comfy.download_view(
            str(item["filename"]),
            str(item.get("subfolder") or ""),
            str(item.get("type") or default_type),
            dest,
        )
        updates["image_path"] = str(dest)

    if job.mode in ("full", "i2v"):
        item = _pick_output(outputs, N_SAVE_VIDEO)
        if item is None:
            raise JobError(f"no video output on node {N_SAVE_VIDEO}")
        suffix = Path(str(item["filename"])).suffix
        if suffix.lower() not in VIDEO_EXTS:
            suffix = ".mp4"
        dest = job_dir / f"video{suffix}"
        await comfy.download_view(
            str(item["filename"]),
            str(item.get("subfolder") or ""),
            str(item.get("type") or "output"),
            dest,
        )
        updates["video_path"] = str(dest)
        updates["last_frame_path"] = str(
            await extract_last_frame(dest, job_dir / "last_frame.png")
        )

    return updates


async def run_job(job_id: str) -> None:
    """Execute one job end to end. Failures are recorded, never raised."""
    job = await get_job(job_id)
    if job is None:
        log.warning("job %s disappeared before it could run", job_id)
        return
    try:
        await _set_status(job_id, "running", message="uploading assets")

        audio_name = ""
        if job.params.get("audio_path"):
            audio_name = await comfy.upload_file(job.params["audio_path"])
        start_image_name = ""
        if job.params.get("source_image"):
            start_image_name = await comfy.upload_file(job.params["source_image"])

        workflow = build_workflow(
            load_template(),
            _generation_params(job, audio_name, start_image_name),
            load_settings().model_overrides,
        )
        await _update(job_id, workflow_json=json.dumps(workflow, ensure_ascii=False))

        client_id = str(uuid.uuid4())
        prompt_id = await comfy.queue_prompt(workflow, client_id)
        await _update(job_id, comfy_prompt_id=prompt_id)
        await ws.publish(job_id, "running", message=f"queued on ComfyUI ({prompt_id})")

        entry = await _wait_for_result(prompt_id, client_id, job_id)
        updates = await _download_outputs(job, entry)
        await _set_status(job_id, "done", message="done", error=None, **updates)
    except asyncio.CancelledError:
        await _set_status(job_id, "canceled", message="canceled", error="canceled")
        raise
    except Exception as exc:  # noqa: BLE001 - any failure marks the job failed (§5)
        detail = str(exc) if isinstance(exc, JobError) else f"{type(exc).__name__}: {exc}"
        log.exception("job %s failed", job_id)
        await _set_status(job_id, "failed", message=detail, error=detail)


# --------------------------------------------------------------------------
# queue / worker
# --------------------------------------------------------------------------

class JobRunner:
    """Single-consumer asyncio queue (SPEC §5: one job at a time)."""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[str] | None = None
        self._task: asyncio.Task[None] | None = None
        self.current: str | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def _ensure_queue(self) -> asyncio.Queue[str]:
        if self._queue is None:
            self._queue = asyncio.Queue()
        return self._queue

    async def start(self) -> None:
        self._ensure_queue()
        if not self.running:
            self._task = asyncio.create_task(self._worker(), name="job-worker")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self._queue = None
        self.current = None

    async def submit(self, job_id: str) -> None:
        await self._ensure_queue().put(job_id)
        # Auto-start so that jobs created outside the FastAPI lifespan (scripts,
        # tests) are still executed.
        await self.start()

    def pending(self) -> int:
        return self._queue.qsize() if self._queue else 0

    async def _worker(self) -> None:
        queue = self._ensure_queue()
        while True:
            job_id = await queue.get()
            self.current = job_id
            try:
                await run_job(job_id)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - never let the worker die
                log.exception("unexpected failure while running job %s", job_id)
            finally:
                self.current = None
                queue.task_done()


runner = JobRunner()
