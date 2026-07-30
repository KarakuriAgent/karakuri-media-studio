"""Job execution service (SPEC §5 / §6 / §9).

One in-process asyncio queue with a single worker (SPEC §9 "ジョブ管理: アプリ内
asyncio キュー", §5 "同時実行は 1 ジョブ").  The worker is started/stopped from the
FastAPI lifespan.

Per job the runner:

1. uploads the reference audio / input images / reference clip to ComfyUI
   (``/upload/image``),
2. builds the API workflow of the first stage from the job params
   (:mod:`app.workflow`),
3. queues it (``/prompt``) and follows the progress over the ComfyUI WebSocket,
   falling back to ``/history`` polling whenever the socket is unavailable,
4. downloads the artefacts into ``outputs/{job_id}/`` and extracts the last
   frame with ffmpeg,
5. records everything in the ``jobs`` table and broadcasts each transition to
   the browser over :mod:`app.ws`.

``full`` mode chains **two** ComfyUI prompts under one job id (SPEC §2): the
image workflow runs first, its still is downloaded and re-uploaded to the
ComfyUI input directory, and the selected video workflow then uses it as the
start frame.  Both graphs are stored in ``workflow_json``.
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
from PIL import Image

from . import comfy, nsfw as nsfw_service, ws
from .config import load_settings
from .db import get_db
from .ids import new_id
from .models import (
    GenerationParams,
    Job,
    JobContinue,
    JobCreate,
    JobRerun,
    LoraRef,
    audio_lora_problem,
    audio_workflow_problem,
    image_lora_family_problem,
    image_workflow_problem,
    job_workflow_ids,
    missing_job_fields,
    model_override_problem,
    video_lora_problem,
    video_workflow_problem,
)
from .paths import ASSETS_DIR, OUTPUTS_DIR
from .workflow import (
    build_audio_workflow,
    build_image_workflow,
    build_video_workflow,
    model_slots,
    scoped_model_overrides,
)
from .workflows import (
    DEFAULT_AUDIO_WORKFLOW,
    DEFAULT_IMAGE_WORKFLOW,
    DEFAULT_VIDEO_WORKFLOW,
    WorkflowSpec,
    WorkflowSpecError,
    get_audio_spec,
    get_image_spec,
    get_video_spec,
)

log = logging.getLogger(__name__)

# Tunables (monkeypatched by the tests to keep them fast).
POLL_INTERVAL = 1.0
JOB_TIMEOUT = 6 * 60 * 60.0
FFMPEG = "ffmpeg"
SEED_MAX = 2**31 - 1

# history["outputs"][node] keys that may hold produced files
_FILE_LIST_KEYS = ("images", "videos", "gifs", "files", "audio", "video")

VIDEO_EXTS = {".mp4", ".webm", ".mkv", ".mov", ".avi", ".gif"}

# asset params that are uploaded to the ComfyUI input dir before a run, mapped to
# the GenerationParams field that receives the stored file name
_UPLOADS = {
    "audio_path": "audio_name",
    "source_image": "start_image_name",
    "end_image": "end_image_name",
    "reference_video": "reference_video_name",
}


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


def read_image_size(path: str | Path) -> tuple[int, int] | None:
    """``(width, height)`` of an image file, or ``None`` if it cannot be read.

    Only the header is decoded.  A failure is not fatal: the caller falls back
    to the aspect-ratio preset.
    """
    try:
        with Image.open(path) as image:
            width, height = image.size
    except (OSError, ValueError) as exc:
        log.warning("could not read image size of %s: %s", path, exc)
        return None
    if width <= 0 or height <= 0:
        log.warning("image %s reports a degenerate size %sx%s", path, width, height)
        return None
    return width, height


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
    data["audio_output_url"] = _output_url(data.get("audio_output_path"))
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
    return {
        "seed": value,
        "image_seed": value,
        "video_seeds": [value, value],
        "audio_seed": value,
    }


def _model_override_problem(params: dict[str, Any]) -> str | None:
    """ジョブ単位のモデル指定を設定の候補リストと突き合わせる（SPEC §3.3）。"""
    requested = params.get("model_overrides")
    if not requested:
        return None
    settings = load_settings()
    return model_override_problem(
        requested,
        model_slots(settings.model_overrides, settings.model_choices),
        job_workflow_ids(
            params.get("mode", ""),
            image_workflow=params.get("image_workflow"),
            video_workflow=params.get("video_workflow"),
            audio_workflow=params.get("audio_workflow"),
        ),
    )


def _validate(params: dict[str, Any]) -> None:
    mode = params.get("mode", "")
    video_workflow = params.get("video_workflow")
    image_workflow = params.get("image_workflow")
    problem = (
        image_workflow_problem(mode, image_workflow)
        or video_workflow_problem(mode, video_workflow)
        or audio_workflow_problem(
            mode,
            params.get("audio_workflow"),
            duration=params.get("duration"),
            audio_category=params.get("audio_category"),
            keyscale=params.get("keyscale"),
            language=params.get("language"),
            bpm=params.get("bpm"),
        )
        or audio_lora_problem(
            mode, params.get("loras") or [], params.get("video_loras") or []
        )
        or video_lora_problem(mode, video_workflow, params.get("video_loras") or [])
        or _model_override_problem(params)
    )
    if problem:
        raise JobValidationError(problem)
    try:
        missing = missing_job_fields(
            mode,
            image_prompt=params.get("image_prompt"),
            video_prompt=params.get("video_prompt"),
            audio_path=params.get("audio_path"),
            source_image=params.get("source_image"),
            end_image=params.get("end_image"),
            reference_video=params.get("reference_video"),
            video_workflow=video_workflow,
            image_workflow=image_workflow,
            audio_prompt=params.get("audio_prompt"),
        )
    except WorkflowSpecError as exc:
        raise JobValidationError(str(exc)) from exc
    if missing:
        raise JobValidationError(f"mode '{mode}' requires: {', '.join(missing)}")


async def lora_families(names: list[str]) -> dict[str, str]:
    """``{lora_name: family}`` for the image LoRAs the registry still knows.

    Names that are not (or no longer) registered are simply absent, so a rerun
    of an old job whose LoRA has since been deleted is not blocked by this.
    """
    if not names:
        return {}
    placeholders = ", ".join("?" for _ in names)
    async with get_db() as conn:
        async with conn.execute(
            "SELECT lora_name, family FROM loras"
            f" WHERE target = 'image' AND lora_name IN ({placeholders})",
            names,
        ) as cur:
            rows = await cur.fetchall()
    return {row["lora_name"]: row["family"] or "krea2" for row in rows}


async def _validate_lora_families(params: dict[str, Any]) -> None:
    """422 when an image LoRA was trained for another model family (SPEC §3.4)."""
    names = [
        str(lora.get("lora_name") or "")
        for lora in params.get("loras") or []
        if isinstance(lora, dict) and lora.get("lora_name")
    ]
    known = await lora_families(names)
    problem = image_lora_family_problem(
        params.get("mode", ""),
        params.get("image_workflow"),
        [known[name] for name in names if name in known],
    )
    if problem:
        raise JobValidationError(problem)


def _resolve_nsfw(explicit: bool | None, inherit: bool) -> tuple[bool | None, str]:
    """明示指定は manual、継承は auto、未指定は判定待ち（'' + バックグラウンド判定）。"""
    if explicit is not None:
        return explicit, "manual"
    if inherit:
        return True, "auto"
    return None, ""


async def _insert_job(
    *,
    mode: str,
    params: dict[str, Any],
    user_input: str | None,
    chat_session_id: str | None,
    nsfw: bool | None = None,
    nsfw_source: str = "",
) -> Job:
    """Validate, persist a ``queued`` row and hand it to the worker."""
    _validate(params)
    await _validate_lora_families(params)

    # Fail fast on unusable asset paths (422 rather than a failed job).
    for field in _UPLOADS:
        value = params.get(field)
        if value:
            params[field] = str(resolve_asset_path(value, field=field))

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
        "audio_prompt": params.get("audio_prompt") or None,
        "grok_raw": None,
        "params": json.dumps(params, ensure_ascii=False),
        "workflow_json": "{}",
        "comfy_prompt_id": None,
        "image_path": None,
        "video_path": None,
        "last_frame_path": None,
        "source_image": params.get("source_image"),
        "audio_path": params.get("audio_path"),
        "audio_output_path": None,
        "error": None,
        "nsfw": 1 if nsfw else 0,
        "nsfw_source": nsfw_source if nsfw is not None else "",
    }
    async with get_db() as conn:
        await conn.execute(
            "INSERT INTO jobs (id, created_at, mode, status, user_input, image_prompt,"
            " video_prompt, audio_prompt, grok_raw, params, workflow_json,"
            " comfy_prompt_id, image_path, video_path, last_frame_path, source_image,"
            " audio_path, audio_output_path, error, nsfw, nsfw_source)"
            " VALUES (:id, :created_at, :mode, :status, :user_input, :image_prompt,"
            " :video_prompt, :audio_prompt, :grok_raw, :params, :workflow_json,"
            " :comfy_prompt_id, :image_path, :video_path, :last_frame_path,"
            " :source_image, :audio_path, :audio_output_path, :error, :nsfw,"
            " :nsfw_source)",
            row,
        )
        await conn.commit()

    await _link_chat_session(chat_session_id, job_id)
    await ws.publish(job_id, "queued", message="queued")
    await runner.submit(job_id)

    if nsfw is None:
        # 判定は生成をブロックしない: 投げっぱなしで走らせる。
        nsfw_service.spawn(
            nsfw_service.classify_job(
                job_id,
                nsfw_service.job_text(
                    params.get("image_prompt"),
                    params.get("video_prompt"),
                    user_input,
                    params.get("audio_prompt"),
                ),
                session_id=chat_session_id,
            ),
            key=f"job:{job_id}",
        )

    job = await get_job(job_id)
    assert job is not None
    return job


def _params_from_create(payload: JobCreate) -> dict[str, Any]:
    params: dict[str, Any] = {
        "mode": payload.mode,
        "image_workflow": payload.image_workflow,
        "video_workflow": payload.video_workflow,
        "audio_workflow": payload.audio_workflow,
        "aspect_ratio": payload.aspect_ratio,
        "megapixels": payload.megapixels,
        "loras": [lora.model_dump() for lora in payload.loras],
        "trigger_text": payload.trigger_text,
        "video_loras": [lora.model_dump() for lora in payload.video_loras],
        "video_trigger_text": payload.video_trigger_text,
        "image_prompt": payload.image_prompt,
        "video_prompt": payload.video_prompt,
        "negative_prompt": payload.negative_prompt,
        # mode 'audio' only (kept in params for rerun / inspection)
        "audio_prompt": payload.audio_prompt,
        "lyrics": payload.lyrics,
        "bpm": payload.bpm,
        "keyscale": payload.keyscale,
        "language": payload.language,
        "audio_category": payload.audio_category,
        "reprompt": payload.reprompt,
        "duration": payload.duration,
        "fps": payload.fps,
        "audio_path": payload.audio_path,
        "source_image": payload.source_image,
        "end_image": payload.end_image,
        "reference_video": payload.reference_video,
        # このジョブだけのモデル指定（設定の model_overrides の上に重ねる、§3.3）
        "model_overrides": dict(payload.model_overrides),
    }
    params.update(_seeds(payload.seed))
    return params


async def create_job(payload: JobCreate, *, inherit_nsfw: bool = False) -> Job:
    """``inherit_nsfw``: 呼び出し元（エージェントセッション等）が NSFW のとき True。"""
    nsfw, source = _resolve_nsfw(payload.nsfw, inherit_nsfw)
    return await _insert_job(
        mode=payload.mode,
        params=_params_from_create(payload),
        user_input=payload.user_input,
        chat_session_id=payload.chat_session_id,
        nsfw=nsfw,
        nsfw_source=source,
    )


async def set_nsfw(job_id: str, nsfw: bool) -> Job | None:
    """手動トグル: manual として保存し、WS で画面に伝える。"""
    if await get_job(job_id, include_workflow=False) is None:
        return None
    await _update(job_id, nsfw=1 if nsfw else 0, nsfw_source="manual")
    job = await get_job(job_id, include_workflow=False)
    if job is not None:
        await ws.publish(job_id, job.status, nsfw=job.nsfw)
    return job


async def rerun_job(job_id: str, payload: JobRerun, *, inherit_nsfw: bool = False) -> Job:
    """New job from the stored *params* (rebuilt, not replayed from workflow_json).

    NSFW フラグは元ジョブから継承する（継承時は判定をスキップ）。
    """
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
    nsfw, nsfw_source = _resolve_nsfw(None, source.nsfw or inherit_nsfw)
    return await _insert_job(
        mode=params.get("mode", source.mode),
        params=params,
        user_input=source.user_input,
        chat_session_id=None,
        nsfw=nsfw,
        nsfw_source=nsfw_source,
    )


def _continuable_workflow(workflow_id: str | None) -> str:
    """A video workflow that can start from a last frame (SPEC §2).

    ``continue`` feeds a still into the video stage, so a workflow that cannot
    take a start frame (t2v, the IC-LoRA reference sheet) falls back to the
    default one instead of failing the request.
    """
    try:
        spec = get_video_spec(workflow_id)
    except WorkflowSpecError:
        return DEFAULT_VIDEO_WORKFLOW
    return spec.id if spec.accepts_start_image else DEFAULT_VIDEO_WORKFLOW


async def continue_job(
    job_id: str, payload: JobContinue, *, inherit_nsfw: bool = False
) -> Job:
    """Start a mode-B job from the last frame of ``job_id`` (SPEC §2).

    NSFW フラグは元ジョブから継承する（継承時は判定をスキップ）。
    """
    source = await get_job(job_id)
    if source is None:
        raise LookupError(job_id)
    if not source.last_frame_path or not Path(source.last_frame_path).is_file():
        raise JobValidationError("source job has no last frame to continue from")

    start_image = copy_into_assets(source.last_frame_path, "image")

    prev = dict(source.params)
    video_workflow = _continuable_workflow(
        payload.video_workflow or prev.get("video_workflow")
    )
    params: dict[str, Any] = {
        "mode": "i2v",
        # kept for the record only: a continuation never runs an image stage
        "image_workflow": prev.get("image_workflow") or DEFAULT_IMAGE_WORKFLOW,
        "video_workflow": video_workflow,
        "aspect_ratio": payload.aspect_ratio or prev.get("aspect_ratio", "4:3 (Standard)"),
        "megapixels": (
            payload.megapixels
            if payload.megapixels is not None
            else prev.get("megapixels", 1.0)
        ),
        "loras": prev.get("loras", []),
        "trigger_text": prev.get("trigger_text", ""),
        "video_loras": prev.get("video_loras", []),
        "video_trigger_text": prev.get("video_trigger_text", ""),
        "image_prompt": prev.get("image_prompt", ""),
        "video_prompt": payload.video_prompt or prev.get("video_prompt", ""),
        "negative_prompt": payload.negative_prompt or prev.get("negative_prompt") or "",
        "duration": (
            payload.duration if payload.duration is not None else prev.get("duration", 10.0)
        ),
        "fps": payload.fps if payload.fps is not None else prev.get("fps", 25),
        "audio_path": payload.audio_path or prev.get("audio_path"),
        "source_image": str(start_image),
        "end_image": payload.end_image or prev.get("end_image"),
        "reference_video": payload.reference_video or prev.get("reference_video"),
        # 動画ステージだけを走らせるので、動画ワークフローのスロットだけ引き継ぐ
        # （切り替えで既定ワークフローに戻された場合は元の指定が落ちる）
        "model_overrides": scoped_model_overrides(
            payload.model_overrides
            if payload.model_overrides is not None
            else prev.get("model_overrides"),
            [video_workflow],
        ),
        "continued_from": source.id,
    }
    params.update(_seeds(payload.seed))
    nsfw, nsfw_source = _resolve_nsfw(None, source.nsfw or inherit_nsfw)
    return await _insert_job(
        mode="i2v",
        params=params,
        user_input=payload.user_input or source.user_input,
        chat_session_id=payload.chat_session_id,
        nsfw=nsfw,
        nsfw_source=nsfw_source,
    )


# --------------------------------------------------------------------------
# execution
# --------------------------------------------------------------------------

def _generation_params(job: Job, uploads: dict[str, str]) -> GenerationParams:
    """The injector's view of a job. ``uploads`` maps param field -> ComfyUI name."""
    p = job.params
    # 参照画像があれば、その実比で動画の幅・高さを決める（読めなければプリセット）。
    source_image = p.get("source_image")
    return GenerationParams(
        mode=job.mode,
        job_id=job.id,
        image_workflow=p.get("image_workflow") or DEFAULT_IMAGE_WORKFLOW,
        video_workflow=p.get("video_workflow") or DEFAULT_VIDEO_WORKFLOW,
        audio_workflow=p.get("audio_workflow") or DEFAULT_AUDIO_WORKFLOW,
        aspect_ratio=p.get("aspect_ratio", "4:3 (Standard)"),
        megapixels=float(p.get("megapixels", 1.0)),
        start_image_size=read_image_size(source_image) if source_image else None,
        loras=[LoraRef(**lora) for lora in p.get("loras", [])],
        trigger_text=p.get("trigger_text", ""),
        # 旧ジョブの params には無いので既定は空（後方互換）
        video_loras=[LoraRef(**lora) for lora in p.get("video_loras", [])],
        video_trigger_text=p.get("video_trigger_text", ""),
        image_prompt=p.get("image_prompt", ""),
        video_prompt=p.get("video_prompt", ""),
        negative_prompt=p.get("negative_prompt") or "",
        duration=float(p.get("duration", 10.0)),
        fps=int(p.get("fps", 25)),
        # 音声ジョブ用（旧ジョブの params には無いので既定値のまま）
        audio_prompt=p.get("audio_prompt", ""),
        lyrics=p.get("lyrics", ""),
        bpm=int(p.get("bpm", 120)),
        keyscale=p.get("keyscale") or "C major",
        language=p.get("language") or "en",
        audio_category=p.get("audio_category") or "Music",
        reprompt=bool(p.get("reprompt", False)),
        image_seed=int(p.get("image_seed", 0)),
        video_seeds=[int(s) for s in p.get("video_seeds", [])],
        audio_seed=int(p.get("audio_seed", p.get("seed", 0) or 0)),
        **{field: uploads.get(field, "") for field in _UPLOADS.values()},
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


async def _download_artifact(
    entry: dict[str, Any], node_id: str, dest_dir: Path, stem: str, kind: str
) -> Path:
    """Download the file ``node_id`` produced into ``dest_dir/stem<suffix>``."""
    item = _pick_output(entry.get("outputs") or {}, node_id)
    if item is None:
        raise JobError(f"no {kind} output on node {node_id}")
    suffix = Path(str(item["filename"])).suffix
    if kind == "video" and suffix.lower() not in VIDEO_EXTS:
        suffix = ".mp4"
    if kind == "image" and not suffix:
        suffix = ".png"
    # SaveAudioMP3 always writes .mp3, but a template swapped for SaveAudio /
    # SaveAudioOpus keeps its own extension; only a missing one is filled in.
    if kind == "audio" and not suffix:
        suffix = ".mp3"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{stem}{suffix}"
    await comfy.download_view(
        str(item["filename"]),
        str(item.get("subfolder") or ""),
        str(item.get("type") or "output"),
        dest,
    )
    return dest


async def _run_stage(
    job_id: str,
    stage: str,
    spec: WorkflowSpec,
    workflow: dict[str, Any],
    stages: dict[str, Any],
    label: str,
) -> dict[str, Any]:
    """Queue one ComfyUI prompt for ``job_id`` and wait for its ``/history`` entry.

    The built graph is persisted before queueing so a failed run can still be
    inspected, and again afterwards with the prompt id.
    """
    stages[stage] = {"workflow_id": spec.id, "prompt_id": None, "graph": workflow}
    await _update(job_id, workflow_json=json.dumps(stages, ensure_ascii=False))

    client_id = str(uuid.uuid4())
    prompt_id = await comfy.queue_prompt(workflow, client_id)
    stages[stage]["prompt_id"] = prompt_id
    await _update(
        job_id,
        comfy_prompt_id=prompt_id,
        workflow_json=json.dumps(stages, ensure_ascii=False),
    )
    await ws.publish(job_id, "running", message=f"{label}: queued on ComfyUI ({prompt_id})")
    return await _wait_for_result(prompt_id, client_id, job_id)


async def run_job(job_id: str) -> None:
    """Execute one job end to end (1 or 2 stages). Failures are recorded, never raised."""
    job = await get_job(job_id)
    if job is None:
        log.warning("job %s disappeared before it could run", job_id)
        return
    try:
        await _set_status(job_id, "running", message="uploading assets")

        uploads: dict[str, str] = {}
        for field, param_name in _UPLOADS.items():
            path = job.params.get(field)
            if path:
                uploads[param_name] = await comfy.upload_file(path)

        params = _generation_params(job, uploads)
        # 設定の既定値の上にジョブ単位の指定を重ねる（SPEC §3.3）
        overrides = {
            **load_settings().model_overrides,
            **(job.params.get("model_overrides") or {}),
        }
        job_dir = OUTPUTS_DIR / job.id
        stages: dict[str, Any] = {}
        updates: dict[str, Any] = {}

        two_stage = job.mode == "full"
        # 音声は独立した 1 ステージのジョブ: 画像・動画の分岐には一切入らない。
        if job.mode == "audio":
            audio_spec = get_audio_spec(params.audio_workflow)
            label = "音声生成"
            await ws.publish(job_id, "running", message=label)
            entry = await _run_stage(
                job_id,
                "audio",
                audio_spec,
                build_audio_workflow(params, overrides, spec=audio_spec),
                stages,
                label,
            )
            track = await _download_artifact(
                entry, audio_spec.output_node, job_dir, "audio", "audio"
            )
            updates["audio_output_path"] = str(track)

        if job.mode in ("full", "image_only"):
            image_spec = get_image_spec(params.image_workflow)
            label = "画像生成 (1/2)" if two_stage else "画像生成"
            await ws.publish(job_id, "running", message=label)
            entry = await _run_stage(
                job_id,
                "image",
                image_spec,
                build_image_workflow(params, overrides, spec=image_spec),
                stages,
                label,
            )
            image = await _download_artifact(
                entry, image_spec.output_node, job_dir, "image", "image"
            )
            updates["image_path"] = str(image)
            # Persist right away: if the video stage fails, the generated still is
            # still worth showing (and re-usable as a start frame).
            await _update(job_id, image_path=str(image))
            if two_stage:
                # The video stage reads the still from the ComfyUI input dir.
                # That still already follows the aspect-ratio preset, so any size
                # taken from `source_image` no longer applies.
                params = params.model_copy(
                    update={
                        "start_image_name": await comfy.upload_file(image),
                        "start_image_size": None,
                    }
                )

        if job.mode in ("full", "i2v"):
            video_spec = get_video_spec(params.video_workflow)
            label = "動画生成 (2/2)" if two_stage else "動画生成"
            await ws.publish(job_id, "running", message=label)
            entry = await _run_stage(
                job_id,
                "video",
                video_spec,
                build_video_workflow(params, overrides, spec=video_spec),
                stages,
                label,
            )
            video = await _download_artifact(
                entry, video_spec.output_node, job_dir, "video", "video"
            )
            updates["video_path"] = str(video)
            updates["last_frame_path"] = str(
                await extract_last_frame(video, job_dir / "last_frame.png")
            )

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
