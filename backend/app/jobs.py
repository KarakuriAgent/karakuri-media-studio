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

``full`` mode chains **two** stages under one job id (SPEC §2): the image
workflow runs first, its still is downloaded and handed to the selected video
workflow as the start frame.  Both stages are stored in ``workflow_json``.

**バックエンド（SPEC §5.2）**: ステージのマニフェストが宣言する ``backend``
（``comfyui`` / ``kie``）を見て、:func:`_run_job_stages` が**ステージごとに**
実行経路を選ぶ。ComfyUI のステージ（:func:`_run_comfy_stage`）は上のとおりで、
kie.ai のステージ（:func:`_run_kie_stage`）はグラフの代わりにタスクを 1 つ投げて
ポーリングする。どちらも「``outputs/{job_id}/`` に成果物を置き、jobs 行を更新し、
WS に進捗を流す」ところは共通なので、履歴・ライブラリ・UI からは区別なく扱える。

そのため 1 ジョブの中でバックエンドをまたげる: **ローカル（ComfyUI）で画像を作り、
外部 API（kie.ai）で動画にする** `full` ジョブでは、1 段目の静止画を kie の
File Upload API で公開 URL にしてから 2 段目に渡す（:data:`_STAGE_BRIDGES`）。
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

from . import comfy, kie, nsfw as nsfw_service, runpod, ws
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
    select_problem,
    video_lora_problem,
    video_workflow_problem,
)
from .paths import ASSETS_DIR, LIBRARY_DIR, OUTPUTS_DIR
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
    INPUT_FIELDS,
    WorkflowSpec,
    WorkflowSpecError,
    backend_available,
    get_audio_spec,
    get_image_spec,
    get_video_spec,
)

log = logging.getLogger(__name__)

# Tunables (monkeypatched by the tests to keep them fast).
POLL_INTERVAL = 1.0
JOB_TIMEOUT = 6 * 60 * 60.0
FFMPEG = "ffmpeg"
FFPROBE = "ffprobe"
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

def _input_roots() -> dict[str, Path]:
    """ジョブの入力に使えるファイルの置き場: URL の接頭辞 -> 実ディレクトリ。

    アップロードした素材（``assets/``）と、取っておいた素材（``library/``、
    SPEC §7.2）。テストが差し替えられるようモジュール変数をその場で読む。
    """
    return {"/assets/": ASSETS_DIR, "/library/": LIBRARY_DIR}


def resolve_asset_path(value: str, *, field: str) -> Path:
    """Accept a path or URL inside ``assets/`` or ``library/``.

    ``"/assets/…"`` / ``"/library/…"`` URLs and absolute paths are both taken;
    a bare relative path keeps meaning ``assets/`` (that is what the upload
    endpoints have always returned).
    """
    raw = (value or "").strip()
    if not raw:
        raise JobValidationError(f"{field} is empty")
    roots = _input_roots()
    candidate: Path | None = None
    for prefix, directory in roots.items():
        if raw.startswith(prefix):
            candidate = directory / raw[len(prefix):]
            break
    if candidate is None:
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = ASSETS_DIR / raw
    allowed = [directory.resolve() for directory in roots.values()]
    resolved = candidate.resolve()
    if not any(root in resolved.parents for root in allowed):
        raise JobValidationError(
            f"{field} must point inside {' or '.join(str(root) for root in allowed)}"
        )
    if not resolved.is_file():
        raise JobValidationError(f"{field} not found: {resolved}")
    return resolved


async def probe_media_duration(path: str | Path) -> float | None:
    """Length of an audio / video file in seconds via ffprobe (None if unknown).

    ワークフローの尺を入力音声に合わせるのに使う（SPEC §3.1）。ffprobe が無い・
    読めないのは致命的ではないので、そのときは None を返して呼び出し側が既定値に
    落ちる。
    """
    try:
        process = await asyncio.create_subprocess_exec(
            FFPROBE,
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except (OSError, ValueError) as exc:
        log.info("ffprobe を実行できませんでした（%s の長さは既定値にします）: %s", path, exc)
        return None
    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        log.info(
            "ffprobe が %s の長さを読めませんでした: %s",
            path,
            stderr.decode("utf-8", "replace").strip()[:200],
        )
        return None
    try:
        seconds = float(stdout.decode("utf-8", "replace").strip())
    except ValueError:
        return None
    return seconds if seconds > 0 else None


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
    progress: float | None = None,
    **fields: Any,
) -> None:
    await _update(job_id, status=status, **fields)
    await ws.publish(job_id, status, message=message, progress=progress)


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
        model_slots(settings.overrides_for(), settings.choices_for()),
        job_workflow_ids(
            params.get("mode", ""),
            image_workflow=params.get("image_workflow"),
            video_workflow=params.get("video_workflow"),
            audio_workflow=params.get("audio_workflow"),
        ),
    )


def stage_specs(mode: str, params: dict[str, Any]) -> list[tuple[str, WorkflowSpec]]:
    """このジョブが走らせるステージ ``(名前, マニフェスト)`` を順番に（SPEC §2）。

    ``full`` だけが 2 段（画像 → 動画）。``audio`` は独立した 1 段。
    """
    stages: list[tuple[str, WorkflowSpec]] = []
    if mode == "audio":
        stages.append(("audio", get_audio_spec(params.get("audio_workflow"))))
    if mode in ("full", "image_only"):
        stages.append(("image", get_image_spec(params.get("image_workflow"))))
    if mode in ("full", "i2v"):
        stages.append(("video", get_video_spec(params.get("video_workflow"))))
    return stages


#: バックエンドをまたぐ 2 段ジョブのうち、橋渡しを実装してある向き（SPEC §5.2）。
#: ComfyUI で作った静止画は :func:`kie.upload_file` で公開 URL にできるので
#: 「ローカルで画像 → 外部 API で動画化」が通る。逆（kie の画像 → ComfyUI の動画）は
#: kie の画像ワークフローが入ってから実装する。
_STAGE_BRIDGES: frozenset[tuple[str, str]] = frozenset({("comfyui", "kie")})


def job_backends(mode: str, params: dict[str, Any]) -> list[str]:
    """ステージごとのバックエンド（SPEC §5.2）。実行順に並ぶ。"""
    return [spec.backend for _, spec in stage_specs(mode, params)]


def job_backend(mode: str, params: dict[str, Any]) -> str:
    """このジョブの代表バックエンド（1 段目のもの。表示・ログ用）。"""
    used = job_backends(mode, params)
    return used[0] if used else "comfyui"


def _bridge_problem(used: list[str]) -> str | None:
    """バックエンドをまたぐ連結が実装済みか（None == 問題なし、SPEC §5.2）。"""
    for first, second in zip(used, used[1:]):
        if first != second and (first, second) not in _STAGE_BRIDGES:
            return (
                f"生成バックエンド '{first}' のステージから '{second}' の"
                "ステージへの受け渡しはまだ実装されていません"
            )
    return None


def _backend_problem(params: dict[str, Any]) -> str | None:
    """バックエンドの都合でこのジョブが走れない理由（None == 問題なし、§5.2）。

    投入してから失敗させるのではなく、422 でその場で断る: 認証が確認できていない
    バックエンドと、橋渡しを実装していない向きのバックエンドまたぎ。
    """
    mode = params.get("mode", "")
    try:
        stages = stage_specs(mode, params)
    except WorkflowSpecError:
        return None  # 不正なワークフロー id は他の検証が拾う
    bridge = _bridge_problem([spec.backend for _, spec in stages])
    if bridge:
        return bridge
    for _, spec in stages:
        if not backend_available(spec.backend):
            return (
                f"workflow '{spec.id}' の生成バックエンド '{spec.backend}' は"
                "今この環境では使えません（API キーを設定して接続を確認してください）"
            )
    return None


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
        or select_problem(mode, video_workflow, params.get("selects"))
        or _model_override_problem(params)
        or _backend_problem(params)
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


async def _resolve_auto_selects(params: dict[str, Any]) -> None:
    """``auto`` を宣言した選択項目を入力から決める（未指定のときだけ、SPEC §3.1）。

    今のところ ``audio_duration``（入力音声の実長を選択肢に切り上げ）だけ。決めた
    値は params に残すので、再実行しても同じ尺で走る。測れなければ何もしない
    （ワークフローの既定値が使われる）。
    """
    mode = params.get("mode", "")
    if mode not in ("full", "i2v"):
        return
    try:
        spec = get_video_spec(params.get("video_workflow"))
    except WorkflowSpecError:
        return  # 不正なワークフロー id は _validate が既に弾いている
    selects = dict(params.get("selects") or {})
    for name, select in spec.selects.items():
        if select.auto != "audio_duration" or selects.get(name):
            continue
        audio = params.get("audio_path")
        seconds = await probe_media_duration(audio) if audio else None
        if seconds is None:
            continue
        selects[name] = select.round_up(seconds)
        log.info(
            "job の %s を音声の長さ %.1f 秒から %s に決めました",
            name, seconds, selects[name],
        )
    if selects:
        params["selects"] = selects


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
    # 尺などの「自動」項目は、入力ファイルが確定したここで決める（SPEC §3.1）。
    await _resolve_auto_selects(params)

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
        # 選択式フィールドの値（ワークフローが宣言したものだけ、§3.1）
        "selects": dict(payload.selects),
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


def _carried_selects(workflow_id: str, previous: Any) -> dict[str, str]:
    """``previous`` のうち、``workflow_id`` が宣言している選択項目だけ。"""
    if not isinstance(previous, dict):
        return {}
    try:
        spec = get_video_spec(workflow_id)
    except WorkflowSpecError:
        return {}
    return {
        str(name): str(value)
        for name, value in previous.items()
        if (select := spec.select(str(name))) is not None
        and str(value) in select.choices
    }


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
        # 選択項目は切り替え先が宣言しているものだけ引き継ぐ（別ワークフローの
        # 選択肢は意味が違うので落とす）
        "selects": _carried_selects(video_workflow, prev.get("selects")),
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
        # 旧ジョブの params には無いので既定は空（後方互換）
        selects={
            str(name): str(value) for name, value in (p.get("selects") or {}).items()
        },
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


class OverallProgress:
    """ジョブ全体（1〜2 ステージ）を通した 0..1 の進捗（SPEC §9）。

    ComfyUI の ``progress`` イベントはノードごとに 0→100% を繰り返すので、その
    ままでは進捗バーが何度も巻き戻る。ここでは「通過したノード数 + 実行中ノード
    の端数」をワークフローのノード総数で割って 1 ステージ分の割合に直し、さらに
    ``(stage_index + 割合) / total_stages`` で全ステージを通した値にする。ノード
    の重みはすべて等しいものとして扱う（実時間は事前に分からないため）。

    値は単調非減少: キャッシュ済みノードの通知やイベントの前後関係で計算値が下
    がっても、いちど出した値より小さくは配信しない。
    """

    def __init__(self, total_stages: int = 1) -> None:
        self.total_stages = max(int(total_stages), 1)
        self._stage_index = 0
        self._total_nodes = 0
        self._done: set[str] = set()
        self._current: str | None = None
        self._fraction = 0.0
        self._value = 0.0

    @property
    def value(self) -> float:
        """最後に配信した全体進捗（0..1）。"""
        return self._value

    def start_stage(self, index: int, total_nodes: int) -> float:
        """``index`` 番目のステージ（``total_nodes`` ノード）の開始を記録する。"""
        self._stage_index = max(int(index), 0)
        self._total_nodes = max(int(total_nodes or 0), 0)
        self._done = set()
        self._current = None
        self._fraction = 0.0
        return self._bump(self._stage_index / self.total_stages)

    def node_started(self, node: str) -> float:
        """``executing`` — 直前のノードを完了扱いにして ``node`` に移る。"""
        node = str(node)
        if self._current is not None and self._current != node:
            self._done.add(self._current)
        # 実行中のノードは端数側で数えるので、完了集合からは外しておく。
        self._done.discard(node)
        self._current = node
        self._fraction = 0.0
        return self._recompute()

    def node_progress(self, node: str | None, value: float, maximum: float) -> float:
        """``progress`` — 実行中ノードの ``value/max`` を端数として取り込む。"""
        if node is not None and str(node) != self._current:
            self.node_started(node)
        self._fraction = min(1.0, max(0.0, value / maximum)) if maximum else 0.0
        return self._recompute()

    def nodes_cached(self, nodes: Any) -> float:
        """``execution_cached`` — 実行がスキップされたノードを完了扱いにする。"""
        if isinstance(nodes, (list, tuple, set)):
            self._done.update(str(node) for node in nodes)
            if self._current is not None:
                self._done.discard(self._current)
        return self._recompute()

    def stage_fraction(self, fraction: float) -> float:
        """内訳の分からないステージ用: このステージの進み具合を直接与える。

        外部 API（kie.ai）は「キュー待ち / 生成中」しか教えてくれないので、
        ノード数からは計算できない。粗い目安を入れて進捗バーを進める。
        """
        return self._bump(
            (self._stage_index + min(1.0, max(0.0, fraction))) / self.total_stages
        )

    def stage_finished(self) -> float:
        """このステージの終端（``executing`` の node=None など）まで進める。"""
        self._current = None
        self._fraction = 0.0
        return self._bump((self._stage_index + 1) / self.total_stages)

    def _recompute(self) -> float:
        if not self._total_nodes:
            return self._value
        ratio = min(1.0, (len(self._done) + self._fraction) / self._total_nodes)
        return self._bump((self._stage_index + ratio) / self.total_stages)

    def _bump(self, value: float) -> float:
        self._value = min(1.0, max(self._value, value))
        return self._value


async def _ws_progress(
    client_id: str,
    prompt_id: str,
    job_id: str,
    finished: asyncio.Event,
    overall: OverallProgress | None = None,
) -> None:
    """Relay ComfyUI ``executing`` / ``progress`` events. Never raises.

    配信する ``progress`` はノード単位ではなくワークフロー全体を通した割合
    （:class:`OverallProgress`）。
    """
    try:
        import websockets
    except ImportError:  # pragma: no cover - dependency is pinned
        log.warning("websockets is not installed; falling back to history polling")
        return

    overall = overall or OverallProgress()
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
                    node = str(data.get("node") or "") or None
                    await ws.publish(
                        job_id,
                        "running",
                        node=node,
                        progress=overall.node_progress(
                            node, data.get("value") or 0, data.get("max") or 0
                        ),
                    )
                elif kind == "executing":
                    node = data.get("node")
                    if node is None:
                        await ws.publish(
                            job_id, "running", progress=overall.stage_finished()
                        )
                        finished.set()
                        return
                    await ws.publish(
                        job_id,
                        "running",
                        node=str(node),
                        progress=overall.node_started(node),
                    )
                elif kind == "execution_cached":
                    # キャッシュ再利用で実行されないノードも「通過済み」に数える。
                    await ws.publish(
                        job_id,
                        "running",
                        progress=overall.nodes_cached(data.get("nodes")),
                    )
                elif kind in ("execution_error", "execution_interrupted"):
                    finished.set()
                    return
                elif kind == "execution_success":
                    await ws.publish(
                        job_id, "running", progress=overall.stage_finished()
                    )
                    finished.set()
                    return
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - progress is best-effort (§5)
        log.info("ComfyUI progress socket unavailable (%s); polling instead", exc)


async def _wait_for_result(
    prompt_id: str,
    client_id: str,
    job_id: str,
    overall: OverallProgress | None = None,
) -> dict[str, Any]:
    """Wait for the prompt to finish; returns the ``/history`` entry.

    The WebSocket only speeds things up / feeds the progress bar — completion is
    always confirmed through ``/history`` so a dropped socket cannot stall a job.
    """
    finished = asyncio.Event()
    watcher = asyncio.create_task(
        _ws_progress(client_id, prompt_id, job_id, finished, overall)
    )
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
    overall: OverallProgress | None = None,
    stage_index: int = 0,
) -> dict[str, Any]:
    """Queue one ComfyUI prompt for ``job_id`` and wait for its ``/history`` entry.

    The built graph is persisted before queueing so a failed run can still be
    inspected, and again afterwards with the prompt id.

    ``overall`` を渡すと、このステージのノード総数（``len(workflow)``）を分母に
    した全体進捗が WS に流れる。
    """
    overall = overall or OverallProgress()
    overall.start_stage(stage_index, len(workflow))
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
    await ws.publish(
        job_id,
        "running",
        message=f"{label}: queued on ComfyUI ({prompt_id})",
        progress=overall.value,
    )
    return await _wait_for_result(prompt_id, client_id, job_id, overall)


async def _prepare_comfy(job: Job) -> tuple[GenerationParams, dict[str, str]]:
    """ComfyUI のステージを 1 つでも走らせる前の下ごしらえ。

    Pod の起動（SPEC §5.1）と入力ファイルのアップロードは**ジョブに 1 度**でよい
    ので、最初の ComfyUI ステージの直前に 1 回だけ呼ぶ（1 段目が kie のジョブでは
    そもそも呼ばれない）。返すのは「アップロード名を反映したパラメータ」と
    「設定の既定値にジョブ単位の指定を重ねたモデルスロット」（SPEC §3.3）。
    """
    job_id = job.id
    await runpod.ensure_pod_running(
        lambda text: ws.publish(job_id, "running", message=text)
    )
    await ws.publish(job_id, "running", message="uploading assets")

    uploads: dict[str, str] = {}
    for field, param_name in _UPLOADS.items():
        path = job.params.get(field)
        if path:
            uploads[param_name] = await comfy.upload_file(path)

    overrides = {
        **load_settings().overrides_for(),
        **(job.params.get("model_overrides") or {}),
    }
    return _generation_params(job, uploads), overrides


async def _run_comfy_stage(
    job_id: str,
    stage: str,
    spec: WorkflowSpec,
    params: GenerationParams,
    overrides: dict[str, str],
    stages: dict[str, Any],
    label: str,
    overall: OverallProgress,
    stage_index: int,
    job_dir: Path,
) -> Path:
    """ComfyUI で 1 ステージ分のグラフを走らせ、成果物のパスを返す。"""
    builders = {
        "image": build_image_workflow,
        "video": build_video_workflow,
        "audio": build_audio_workflow,
    }
    workflow = builders[stage](params, overrides, spec=spec)
    entry = await _run_stage(
        job_id, stage, spec, workflow, stages, label, overall, stage_index
    )
    kind, stem, _ = _STAGE_ARTIFACTS[stage]
    return await _download_artifact(entry, spec.output_node, job_dir, stem, kind)


# --------------------------------------------------------------------------
# kie.ai バックエンド（SPEC §5.2）
# --------------------------------------------------------------------------

#: kie.ai の状態語 -> そのステージの進捗の目安（内訳が取れないので粗い刻み）
_KIE_PROGRESS = {"waiting": 0.05, "queuing": 0.15, "generating": 0.5}

#: 状態語の日本語（WS のメッセージに添える）
_KIE_LABELS = {
    "waiting": "受付待ち",
    "queuing": "キュー待ち",
    "generating": "生成中",
}

#: ステージ名 -> (成果物の種類, outputs/ に置くときのファイル名, jobs の列)。
#: バックエンドに依らず同じ置き場・同じ命名なので、履歴と UI からは区別が付かない。
_STAGE_ARTIFACTS = {
    "image": ("image", "image", "image_path"),
    "video": ("video", "video", "video_path"),
    "audio": ("audio", "audio", "audio_output_path"),
}


async def _kie_uploads(spec: WorkflowSpec, params_dict: dict[str, Any]) -> dict[str, str]:
    """入力ファイルを kie に置いて ``{論理名: 公開 URL}`` にする。

    外部モデルは入力画像・音声を**公開 URL でしか**受け取らないので、ComfyUI の
    ``/upload/image`` にあたる下ごしらえがここ（SPEC §5.2）。
    """
    uploads: dict[str, str] = {}
    for name, field in INPUT_FIELDS.items():
        if not spec.supports(name):
            continue
        path = params_dict.get(field)
        if path:
            uploads[name] = await kie.upload_file(path)
    return uploads


async def _run_kie_stage(
    job_id: str,
    stage: str,
    spec: WorkflowSpec,
    params: GenerationParams,
    params_dict: dict[str, Any],
    stages: dict[str, Any],
    label: str,
    overall: OverallProgress,
    stage_index: int,
) -> kie.TaskState:
    """kie.ai にタスクを 1 つ投げ、仕上がるまで待つ。

    ComfyUI の :func:`_run_stage` と同じ役回り: 投入した内容を先に
    ``workflow_json`` へ書いてから投げる（失敗しても何を送ったか分かる）。
    """
    overall.start_stage(stage_index, 0)
    uploads = await _kie_uploads(spec, params_dict)
    request = kie.build_request(spec, params, uploads)
    stages[stage] = {
        "workflow_id": spec.id,
        "backend": "kie",
        "task_id": None,
        "request": request.as_dict(),
    }
    await _update(job_id, workflow_json=json.dumps(stages, ensure_ascii=False))

    api = kie.task_api(request.api)
    task_id = await kie.create_task(request.model, request.input, api=api)
    stages[stage]["task_id"] = task_id
    await _update(job_id, workflow_json=json.dumps(stages, ensure_ascii=False))
    await ws.publish(
        job_id,
        "running",
        message=f"{label}: kie.ai に投入しました ({task_id})",
        progress=overall.stage_fraction(_KIE_PROGRESS["waiting"]),
    )

    async def relay(state: kie.TaskState) -> None:
        text = _KIE_LABELS.get(state.label, state.label)
        await ws.publish(
            job_id,
            "running",
            message=f"{label}: 外部 API 生成中 ({text})",
            progress=overall.stage_fraction(_KIE_PROGRESS.get(state.label, 0.5)),
        )

    state = await kie.wait_for_task(task_id, api=api, on_progress=relay)
    await ws.publish(job_id, "running", progress=overall.stage_finished())
    return state


#: ステージ名 -> 進捗メッセージの見出し（バックエンドが変わっても同じ表示）
_STAGE_LABELS = {"image": "画像生成", "video": "動画生成", "audio": "音声生成"}


def _stage_label(stage: str, index: int, total: int) -> str:
    """「画像生成 (1/2)」のような見出し（1 段のジョブでは番号を付けない）。"""
    label = _STAGE_LABELS.get(stage, stage)
    return f"{label} ({index + 1}/{total})" if total > 1 else label


async def _run_job_stages(job: Job) -> dict[str, Any]:
    """ジョブのステージを順に、**そのステージのバックエンドで**実行する。

    ディスパッチはジョブ単位ではなく**ステージ単位**（SPEC §5.2）。`full` の
    2 段が別々のバックエンドでも、成果物の置き場（``outputs/{job_id}/``）と
    jobs 行の列・WS の進捗表示は共通なので、履歴と UI からは区別が付かない。

    ステージ間の橋渡しは「1 段目の静止画を 2 段目の開始フレームにする」1 点だけ
    で、渡し方だけが 2 段目のバックエンドで変わる: ComfyUI なら input ディレクトリ
    へ再アップロード、kie.ai なら File Upload API で公開 URL にしてから
    ``imageUrls`` に入れる（受け取り側は :func:`_kie_uploads`）。実装済みの向きは
    :data:`_STAGE_BRIDGES`（投入時に :func:`_backend_problem` が弾く）。

    消費クレジットは kie のステージの分だけ合算して履歴に残す（失敗したタスクは
    kie 側で返金されるので数えない）。
    """
    job_id = job.id
    job_dir = OUTPUTS_DIR / job.id
    stages: dict[str, Any] = {}
    updates: dict[str, Any] = {}
    spent = 0.0
    charged = False
    # ComfyUI の下ごしらえ（Pod 起動・入力のアップロード）は最初に必要になった
    # ときだけ、1 度だけ行う。
    comfy_params: GenerationParams | None = None
    overrides: dict[str, str] = {}

    all_stages = stage_specs(job.mode, job.params)
    total = len(all_stages)
    overall = OverallProgress(total)
    await _set_status(job_id, "running", message=_stage_label(all_stages[0][0], 0, total))
    for index, (stage, spec) in enumerate(all_stages):
        label = _stage_label(stage, index, total)
        if index:  # 1 段目の見出しは上の _set_status がもう流している
            await ws.publish(job_id, "running", message=label, progress=overall.value)
        if spec.backend == "comfyui":
            if comfy_params is None:
                comfy_params, overrides = await _prepare_comfy(job)
            saved = await _run_comfy_stage(
                job_id, stage, spec, comfy_params, overrides, stages, label,
                overall, index, job_dir,
            )
        elif spec.backend == "kie":
            state = await _run_kie_stage(
                job_id, stage, spec, _generation_params(job, {}), job.params,
                stages, label, overall, index,
            )
            if state.credits is not None:
                spent += state.credits
                charged = True
            kind, stem, _ = _STAGE_ARTIFACTS[stage]
            saved = (await kie.download_results(state, job_dir, stem, kind))[0]
        else:
            raise JobError(
                f"生成バックエンド '{spec.backend}' はまだ実装されていません"
            )

        column = _STAGE_ARTIFACTS[stage][2]
        updates[column] = str(saved)
        # 途中で落ちても手元に残るよう、ステージごとに確定させる（1 段目の画像は
        # それだけで見る価値があるし、開始フレームとして使い回せる）。
        await _update(job_id, **{column: str(saved)})
        if stage == "video":
            updates["last_frame_path"] = str(
                await extract_last_frame(saved, job_dir / "last_frame.png")
            )
        if stage == "image" and index + 1 < total:
            # 2 段目はこの静止画を開始フレームとして読む。kie は job.params の
            # パスから File Upload API に載せるので、パスを差し替えるだけでよい。
            job.params["source_image"] = str(saved)
            if all_stages[index + 1][1].backend == "comfyui":
                # ComfyUI は input ディレクトリのファイル名で受け取る。生成した
                # 静止画は既にアスペクト比プリセットに従っているので、
                # `source_image` 由来の実寸はもう当たらない。
                comfy_params = comfy_params.model_copy(  # type: ignore[union-attr]
                    update={
                        "start_image_name": await comfy.upload_file(saved),
                        "start_image_size": None,
                    }
                )

    if charged:
        updates["credits_consumed"] = spent
    return updates


async def run_job(job_id: str) -> None:
    """Execute one job end to end. Failures are recorded, never raised.

    どのバックエンドで走らせるかはジョブが選んだワークフローのマニフェストが
    決める（SPEC §5.2）。失敗の記録・キャンセルの扱いはバックエンドに依らず
    共通なので、ここだけが jobs 行の終端を書く。
    """
    job = await get_job(job_id)
    if job is None:
        log.warning("job %s disappeared before it could run", job_id)
        return
    try:
        updates = await _run_job_stages(job)
        await _set_status(
            job_id, "done", message="done", progress=1.0, error=None, **updates
        )
    except asyncio.CancelledError:
        await _set_status(job_id, "canceled", message="canceled", error="canceled")
        raise
    except Exception as exc:  # noqa: BLE001 - any failure marks the job failed (§5)
        # RunPod の起動失敗は文言がそのままユーザー向けなので、型名を前置しない。
        # （ComfyUI 側のエラー本文は comfy._body_excerpt が短く畳んでいる。ジョブ
        #  実行中の失敗に「投入すれば自動起動します」の案内は当たらないので、
        #  display_error による言い換えは読み取り系エンドポイントだけに留める）
        detail = (
            str(exc)
            if isinstance(
                exc,
                (JobError, JobValidationError, runpod.RunPodError, kie.KieError),
            )
            else f"{type(exc).__name__}: {exc}"
        )
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
