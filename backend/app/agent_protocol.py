"""Agent action protocol (AGENT-MODE §4).

The Grok CLI is a stateless text in / text out process, so "tool calls" are
defined here as JSON objects carried by the answer:

    {"action": "plan", "notes": "...", "tasks": [{"label": "...", "job": {...}}]}

Extraction reuses :func:`app.grok.iter_json_objects` (```json fence first, then
any fence, then balanced ``{…}`` blocks).  A ``plan`` task's ``job`` is the
:class:`~app.models.JobCreate` schema itself and goes through the very same
validation as ``POST /api/jobs`` (per-mode required fields, asset resolution)
plus a LoRA-existence check against the choices burnt into the system prompt.
Anything invalid raises :class:`ActionError`, which the caller turns into one
format-reminder retry (AGENT-MODE §3.1).
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from . import grok
from .ids import new_id
from .jobs import JobValidationError, resolve_asset_path
from .models import AgentAction, AgentTask, JobContinue, JobCreate

ACTION_NAMES = (
    "plan",
    "run_task",
    "continue",
    "rerun",
    "inspect",
    "note",
    "rename",
    "checkin",
    "done",
)

# Fields a continue / rerun action may override (既存 API と同じ差分項目)
CONTINUE_FIELDS = tuple(JobContinue.model_fields)
RERUN_FIELDS = ("seed", "randomize_seed")

MAX_PLAN_TASKS = 5


class ActionError(Exception):
    """不正なアクション JSON（リマインダー付きで 1 回だけ再試行する）。"""


def _pydantic_detail(exc: ValidationError) -> str:
    parts = []
    for error in exc.errors()[:3]:
        location = ".".join(str(x) for x in error.get("loc", ())) or "(root)"
        parts.append(f"{location}: {error.get('msg', '')}")
    return " / ".join(parts)


def find_action_payload(text: str) -> dict[str, Any] | None:
    """First JSON object of the answer that carries an ``action`` key."""
    for parsed in grok.iter_json_objects(text):
        if isinstance(parsed, dict) and isinstance(parsed.get("action"), str):
            return parsed
    return None


def looks_like_action_attempt(text: str) -> bool:
    """True when the answer tried to deliver an action but we could not use it."""
    return grok.has_json_fence(text) or find_action_payload(text) is not None


# --------------------------------------------------------------------------
# job validation (jobs.py と同じ経路)
# --------------------------------------------------------------------------

def validate_job(
    raw: Any, *, where: str, known_loras: set[str] | None = None
) -> JobCreate:
    """Validate one ``job`` object exactly like ``POST /api/jobs`` would."""
    if not isinstance(raw, dict):
        raise ActionError(f"{where}: job は JobCreate 形式のオブジェクトで指定してください")
    unknown = [k for k in raw if k not in JobCreate.model_fields]
    if unknown:
        raise ActionError(
            f"{where}: 未知のフィールドがあります: {', '.join(sorted(unknown))}"
        )
    try:
        payload = JobCreate(**raw)
    except ValidationError as exc:
        raise ActionError(f"{where}: {_pydantic_detail(exc)}") from exc

    if known_loras is not None:
        missing = [
            lora.lora_name for lora in payload.loras if lora.lora_name not in known_loras
        ]
        if missing:
            raise ActionError(
                f"{where}: 存在しない LoRA です: {', '.join(missing)}"
                "（システムプロンプトの一覧にあるファイル名のみ使用できます）"
            )

    # 実在しないアセットは 422 と同じ扱いにする（jobs.resolve_asset_path）
    try:
        if payload.audio_path:
            resolve_asset_path(payload.audio_path, field="audio_path")
        if payload.source_image:
            resolve_asset_path(payload.source_image, field="source_image")
    except JobValidationError as exc:
        raise ActionError(f"{where}: {exc}") from exc
    return payload


# --------------------------------------------------------------------------
# action parsing
# --------------------------------------------------------------------------

def _tasks(raw: Any, *, max_tasks: int, known_loras: set[str] | None) -> list[AgentTask]:
    if not isinstance(raw, list) or not raw:
        raise ActionError("plan には tasks 配列（1 件以上）が必要です")
    if len(raw) > max_tasks:
        raise ActionError(f"1 プランのジョブは最大 {max_tasks} 件です（{len(raw)} 件ありました）")
    tasks: list[AgentTask] = []
    for index, item in enumerate(raw, start=1):
        where = f"tasks[{index}]"
        if not isinstance(item, dict):
            raise ActionError(f"{where}: オブジェクトで指定してください")
        job = validate_job(item.get("job"), where=where, known_loras=known_loras)
        tasks.append(
            AgentTask(
                id=new_id(),
                label=str(item.get("label") or f"タスク{index}"),
                job=job.model_dump(),
            )
        )
    return tasks


def _overrides(payload: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    """Pick the diff fields, whether they are nested under ``overrides`` or flat."""
    nested = payload.get("overrides")
    source = nested if isinstance(nested, dict) else payload
    return {k: source[k] for k in fields if k in source and source[k] is not None}


def parse_action(
    text: str,
    *,
    known_loras: set[str] | None = None,
    max_tasks: int = MAX_PLAN_TASKS,
) -> AgentAction | None:
    """Parse the (optional) action of one Grok answer. Raises :class:`ActionError`."""
    payload = find_action_payload(text)
    if payload is None:
        return None

    name = str(payload.get("action") or "").strip()
    if name not in ACTION_NAMES:
        raise ActionError(
            f"未知の action '{name}' です。使えるのは plan / run_task / continue /"
            " rerun / inspect / note / rename / checkin / done です"
        )

    action = AgentAction(action=name)  # type: ignore[arg-type]
    action.notes = str(payload.get("notes") or "")

    if name == "plan":
        action.tasks = _tasks(
            payload.get("tasks"), max_tasks=max_tasks, known_loras=known_loras
        )
    elif name == "run_task":
        task_id = payload.get("task_id")
        action.task_id = str(task_id) if task_id is not None else None
    elif name in ("continue", "rerun"):
        job_id = payload.get("job_id")
        if not job_id:
            raise ActionError(f"{name} には対象の job_id が必要です")
        action.job_id = str(job_id)
        fields = CONTINUE_FIELDS if name == "continue" else RERUN_FIELDS
        action.overrides = _overrides(payload, fields)
    elif name == "inspect":
        job_id = payload.get("job_id")
        if not job_id:
            raise ActionError("inspect には対象の job_id が必要です")
        action.job_id = str(job_id)
        try:
            interval = float(payload.get("interval") or 1.0)
        except (TypeError, ValueError) as exc:
            raise ActionError("inspect の interval は秒数（数値）で指定してください") from exc
        action.interval = max(0.1, min(interval, 60.0))
    elif name == "note":
        action.title = str(payload.get("title") or "メモ")
        filename = payload.get("filename")
        action.filename = str(filename) if filename else None
        action.content = str(payload.get("content") or "")
        if not action.filename and not action.content.strip():
            raise ActionError("note には filename か content のどちらかが必要です")
        # リサーチまとめは research 種別の成果物にする（既定は note）
        action.kind = "research" if payload.get("kind") == "research" else "note"
    elif name == "rename":
        title = str(payload.get("title") or "").strip()
        if not title:
            raise ActionError("rename には新しい title（作品名）が必要です")
        action.title = title
        target = payload.get("name")
        action.name = str(target) if target else None
        job_id = payload.get("job_id")
        action.job_id = str(job_id) if job_id else None
        artifact_kind = payload.get("kind")
        action.artifact_kind = str(artifact_kind) if artifact_kind else None
        if not action.name and not action.job_id:
            raise ActionError(
                "rename には対象成果物の name（ファイル名）か job_id が必要です"
            )
    elif name == "checkin":
        question = str(payload.get("question") or payload.get("content") or "").strip()
        if not question:
            raise ActionError("checkin には question（確認したいこと）が必要です")
        action.question = question
        options = payload.get("options")
        if isinstance(options, list):
            action.options = [str(o) for o in options if str(o).strip()][:6]
    elif name == "done":
        action.summary = str(payload.get("summary") or payload.get("notes") or "")
    return action
