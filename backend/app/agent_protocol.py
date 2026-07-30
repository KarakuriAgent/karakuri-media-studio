"""Agent action protocol (AGENT-MODE §4).

The Grok CLI is a stateless text in / text out process, so "tool calls" are
defined here as JSON objects carried by the answer:

    {"action": "plan", "notes": "...", "tasks": [{"label": "...", "job": {...}}]}

Extraction reuses :func:`app.grok.iter_json_objects` (```json fence first, then
any fence, then balanced ``{…}`` blocks).  A ``plan`` task's ``job`` is the
:class:`~app.models.JobCreate` schema itself and goes through the very same
validation as ``POST /api/jobs`` (per-mode *and* per-video-workflow required
fields, asset resolution for every logical input) plus a LoRA-existence check
against the choices burnt into the system prompt.  Workflow problems are
reported before pydantic gets a chance, so the message names the workflow and
its missing inputs instead of just the field.
Anything invalid raises :class:`ActionError`, which the caller turns into one
format-reminder retry (AGENT-MODE §3.1).
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from . import grok
from . import library as library_service
from .config import load_settings
from .ids import new_id
from .jobs import JobValidationError, resolve_asset_path
from .models import (
    AgentAction,
    AgentTask,
    JobContinue,
    JobCreate,
    audio_lora_problem,
    audio_workflow_problem,
    image_workflow_problem,
    job_workflow_ids,
    missing_job_fields,
    model_override_problem,
    select_problem,
    video_workflow_problem,
)
from .workflow import model_slots
from .workflows import (
    AUDIO_CATEGORIES,
    DEFAULT_AUDIO_WORKFLOW,
    DEFAULT_IMAGE_WORKFLOW,
    DEFAULT_VIDEO_WORKFLOW,
    INPUT_FIELDS,
    WorkflowSpecError,
    audio_specs,
    get_audio_spec,
    get_image_spec,
    image_specs,
    video_specs,
)

ACTION_NAMES = (
    "plan",
    "run_task",
    "continue",
    "rerun",
    "inspect",
    "note",
    "rename",
    "library",
    "library_search",
    "checkin",
    "done",
)

# Fields a continue / rerun action may override (既存 API と同じ差分項目)
CONTINUE_FIELDS = tuple(JobContinue.model_fields)
RERUN_FIELDS = ("seed", "randomize_seed")

# 自走（auto）セッションだけに効く「1 回のプラン提案あたりの新規ジョブ数」の既定値
MAX_PLAN_TASKS = 5

# library アクションが取っておけるジョブ出力（app.library.SOURCES と同じ区分）
LIBRARY_SOURCES = tuple(library_service.SOURCES)


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

def _text(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _image_workflow_detail(raw: dict[str, Any], mode: str) -> str | None:
    """画像ワークフロー依存の問題（不明 ID / 編集系の入力画像不足）を説明する。"""
    if mode not in ("full", "image_only"):
        return None  # 画像ステージを走らせないので無関係
    workflow = _text(raw.get("image_workflow")) or DEFAULT_IMAGE_WORKFLOW
    known = ", ".join(f"`{spec.id}`" for spec in image_specs())
    problem = image_workflow_problem(mode, workflow)
    if problem:
        return f"{problem}（使えるのは {known} です）"
    spec = get_image_spec(workflow)
    if "image" in spec.requires and not (_text(raw.get("source_image")) or "").strip():
        return (
            f"image_workflow `{spec.id}` は入力画像を編集するワークフローなので"
            " `source_image` が必須です（mode が \"full\" でも画像ステージの入力に"
            "なるので省略できません）。CHOICES の画像アセットから選ぶか、"
            "テキストから生成したいなら他の image_workflow を選んでください"
        )
    return None


#: fields that mean the model confused the audio schema with the image / video
#: one.  An audio run ignores them, and a plan that shows a video prompt or a
#: start frame on an audio task reads as if the two were chained — which they
#: never are.  Purely numeric settings (`fps`, `megapixels`, …) are left out on
#: purpose: they are noise, not a misunderstanding worth a retry.
_NON_AUDIO_FIELDS = (
    "image_prompt", "video_prompt", "loras", "video_loras",
    "source_image", "end_image", "reference_video", "audio_path",
)


def _audio_workflow_detail(raw: dict[str, Any]) -> str | None:
    """音声ジョブ固有の問題（不明なワークフロー / 範囲外の秒数 / 誤ったフィールド）。

    音声は独立ジョブなので、画像・動画のフィールドを混ぜてきたら黙って捨てずに
    指摘する（プランを読んだユーザーが「効いている」と誤解するのを防ぐ）。
    """
    workflow = _text(raw.get("audio_workflow")) or DEFAULT_AUDIO_WORKFLOW
    known = ", ".join(f"`{spec.id}`" for spec in audio_specs())
    try:
        spec = get_audio_spec(workflow)
    except WorkflowSpecError as exc:
        return f"{exc}（使えるのは {known} です）"

    stray = [
        name for name in _NON_AUDIO_FIELDS if raw.get(name) not in (None, "", [], {})
    ]
    if stray:
        return (
            f"`mode: \"audio\"` は音声ワークフローだけを走らせる独立ジョブなので、"
            f"{', '.join(f'`{name}`' for name in stray)} は使われません"
            "（画像・動画と連結することはできません）。音声ジョブから外し、"
            "画像や動画が必要なら別のタスクに分けてください"
        )

    problem = audio_workflow_problem(
        "audio",
        workflow,
        duration=raw.get("duration"),
        audio_category=_text(raw.get("audio_category")),
        keyscale=_text(raw.get("keyscale")),
        language=_text(raw.get("language")),
        bpm=raw.get("bpm") if isinstance(raw.get("bpm"), int) else None,
    )
    if problem:
        return problem
    if lora_problem := audio_lora_problem(
        "audio", raw.get("loras") or [], raw.get("video_loras") or []
    ):
        return lora_problem

    if not (_text(raw.get("audio_prompt")) or "").strip():
        return (
            f"audio_workflow `{spec.id}` を走らせるには `audio_prompt`"
            "（作りたい音の説明）が必要です"
        )
    if raw.get("lyrics") and not spec.supports("lyrics"):
        return (
            f"audio_workflow `{spec.id}` は歌詞を歌えません（`lyrics` は"
            " ACE-Step だけの入力です）。歌モノにするなら"
            f" `audio_workflow: \"{DEFAULT_AUDIO_WORKFLOW}\"` を使ってください"
        )
    if raw.get("audio_category") and not spec.supports("audio_category"):
        return (
            f"audio_workflow `{spec.id}` に `audio_category` はありません"
            f"（{', '.join(AUDIO_CATEGORIES)} のカテゴリは Stable Audio 専用です）"
        )
    return None


def _model_override_detail(raw: dict[str, Any], mode: str) -> str | None:
    """ジョブ単位のモデル指定（`model_overrides`）が候補に収まっているか（SPEC §3.3）。

    Web UI と同じ :func:`~app.models.model_override_problem` を通すので、
    「候補外のファイル名」「別ワークフローのキー」はプラン検証の段階で弾かれる。
    """
    requested = raw.get("model_overrides")
    if not requested:
        return None
    settings = load_settings()
    problem = model_override_problem(
        requested,
        model_slots(settings.model_overrides, settings.model_choices),
        job_workflow_ids(
            mode,
            image_workflow=_text(raw.get("image_workflow")),
            video_workflow=_text(raw.get("video_workflow")),
            audio_workflow=_text(raw.get("audio_workflow")),
        ),
    )
    if problem:
        return f"{problem}（MODEL CHOICES の一覧を確認してください）"
    return None


def _select_detail(raw: dict[str, Any], mode: str) -> str | None:
    """選択式フィールドの指定が使えるか（SPEC §3.1）。

    Web UI と同じ :func:`~app.models.select_problem` を通すので、選択肢外の値も
    宣言していない名前もプラン検証の段階で弾ける（実行時ではなく plan 時）。
    """
    problem = select_problem(mode, _text(raw.get("video_workflow")), raw.get("selects"))
    if problem:
        return f"{problem}（VIDEO WORKFLOWS の選択項目を確認してください）"
    return None


def _workflow_detail(raw: dict[str, Any]) -> str | None:
    """ワークフロー依存の問題を、pydantic の短い文言より前に説明する。

    ``JobCreate`` 自身も同じ :func:`~app.models.missing_job_fields` で弾くが、
    「mode 'full' requires: audio_path」だけでは Grok が原因（選んだワークフローの
    必要入力）に気づけない。plan 検証の段階でワークフロー名込みのメッセージに
    差し替え、システムプロンプトのカタログを見直させる。
    """
    mode = raw.get("mode", "full")
    workflow = _text(raw.get("video_workflow")) or DEFAULT_VIDEO_WORKFLOW
    if mode == "audio":
        return (
            _audio_workflow_detail(raw)
            or _select_detail(raw, mode)
            or _model_override_detail(raw, mode)
        )
    if not isinstance(mode, str) or mode not in ("full", "i2v", "image_only"):
        return None  # mode 自体の誤りは pydantic に任せる

    detail = _select_detail(raw, mode) or _model_override_detail(raw, mode)
    if detail:
        return detail

    image_detail = _image_workflow_detail(raw, mode)
    if image_detail:
        return image_detail
    if mode == "image_only":
        return None  # 動画ステージを走らせないのでワークフローは無関係

    known = ", ".join(f"`{spec.id}`" for spec in video_specs())
    try:
        problem = video_workflow_problem(mode, workflow)
    except WorkflowSpecError as exc:
        return f"{exc}（使えるのは {known} です）"
    if problem:
        return (
            f"{problem} / `video_workflow` を full 対応のものに変えるか、"
            '`mode` を "i2v" にしてください（使えるのは ' + known + "）"
        )

    try:
        missing = missing_job_fields(
            mode,
            image_prompt=_text(raw.get("image_prompt")),
            video_prompt=_text(raw.get("video_prompt")),
            audio_path=_text(raw.get("audio_path")),
            source_image=_text(raw.get("source_image")),
            end_image=_text(raw.get("end_image")),
            reference_video=_text(raw.get("reference_video")),
            video_workflow=workflow,
            image_workflow=_text(raw.get("image_workflow")),
        )
    except WorkflowSpecError as exc:
        return str(exc)
    if not missing:
        return None
    asset_fields = set(INPUT_FIELDS.values())
    prompt_missing = [name for name in missing if name not in asset_fields]
    asset_missing = [name for name in missing if name in asset_fields]
    details = []
    if prompt_missing:
        details.append(f"mode '{mode}' には {', '.join(prompt_missing)} が必要です")
    if asset_missing:
        details.append(
            f"video_workflow `{workflow}` を mode '{mode}' で使うには"
            f" {', '.join(asset_missing)} が必要です"
            "（VIDEO WORKFLOWS の必要入力を確認してください）"
        )
    return " / ".join(details)


_TARGET_LABEL = {"image": "画像用", "video": "動画用"}
_FIELD_TARGET = {"loras": "image", "video_loras": "video"}


def _check_loras(
    payload: JobCreate,
    known_loras: dict[str, str],
    where: str,
    known_families: dict[str, str] | None = None,
) -> None:
    """Every referenced LoRA must exist *and* be registered for that stage.

    ``known_loras`` maps ``lora_name`` -> ``'image'`` / ``'video'`` (the registry
    column).  A画像用 LoRA in ``video_loras`` would silently be spliced into the
    LTX graph, so it is rejected with a message that names the right field.

    ``known_families`` maps ``lora_name`` -> the image model family it was
    trained for.  An image LoRA of another family than the job's
    ``image_workflow`` would load but produce garbage, so it is rejected too.
    """
    for field, expected in _FIELD_TARGET.items():
        for lora in getattr(payload, field):
            actual = known_loras.get(lora.lora_name)
            if actual is None:
                raise ActionError(
                    f"{where}: 存在しない LoRA です: {lora.lora_name}"
                    "（システムプロンプトの一覧にあるファイル名のみ使用できます）"
                )
            if actual != expected:
                other = "video_loras" if expected == "image" else "loras"
                raise ActionError(
                    f"{where}: `{lora.lora_name}` は"
                    f"{_TARGET_LABEL.get(actual, actual)} LoRA なので"
                    f" `{field}` には指定できません（`{other}` に入れてください）"
                )

    if not known_families or payload.mode not in ("full", "image_only"):
        return
    spec = get_image_spec(payload.image_workflow)
    for lora in payload.loras:
        family = known_families.get(lora.lora_name)
        if family is not None and family != spec.family:
            raise ActionError(
                f"{where}: `{lora.lora_name}` は {family} 用の画像 LoRA なので"
                f" image_workflow `{spec.id}`（{spec.family}）では使えません。"
                f"{spec.family} 用の LoRA を選ぶか、image_workflow を"
                f" {family} のものに変えてください"
            )


def validate_job(
    raw: Any,
    *,
    where: str,
    known_loras: dict[str, str] | None = None,
    known_families: dict[str, str] | None = None,
) -> JobCreate:
    """Validate one ``job`` object exactly like ``POST /api/jobs`` would."""
    if not isinstance(raw, dict):
        raise ActionError(f"{where}: job は JobCreate 形式のオブジェクトで指定してください")
    unknown = [k for k in raw if k not in JobCreate.model_fields]
    if unknown:
        raise ActionError(
            f"{where}: 未知のフィールドがあります: {', '.join(sorted(unknown))}"
        )
    detail = _workflow_detail(raw)
    if detail:
        raise ActionError(f"{where}: {detail}")
    try:
        payload = JobCreate(**raw)
    except ValidationError as exc:
        raise ActionError(f"{where}: {_pydantic_detail(exc)}") from exc

    if known_loras is not None:
        _check_loras(payload, known_loras, where, known_families)

    # 実在しないアセットは 422 と同じ扱いにする（jobs.resolve_asset_path）。
    # 対象はマニフェストの論理入力すべて（開始画像・音声・最終フレーム・参照動画）。
    try:
        for field in INPUT_FIELDS.values():
            value = getattr(payload, field, None)
            if value:
                resolve_asset_path(value, field=field)
    except JobValidationError as exc:
        raise ActionError(f"{where}: {exc}") from exc
    return payload


# --------------------------------------------------------------------------
# action parsing
# --------------------------------------------------------------------------

def _tasks(
    raw: Any,
    *,
    max_tasks: int | None,
    done_tasks: int,
    known_loras: dict[str, str] | None,
    known_families: dict[str, str] | None = None,
) -> list[AgentTask]:
    if not isinstance(raw, list) or not raw:
        raise ActionError("plan には tasks 配列（1 件以上）が必要です")
    # 上限は「1 回のプラン提案で新しく走るジョブ数」。プラン改訂は前のプランを
    # 丸ごと置き換える（agent_runner._apply_plan）ので、完了済みタスクの再掲分は
    # 差し引いてから数える。``max_tasks=None`` は無制限（毎ジョブ確認 / 節目確認）。
    if max_tasks is not None:
        new_tasks = len(raw) - done_tasks
        if new_tasks > max_tasks:
            detail = (
                f"（{len(raw)} 件中、完了済みの再掲 {done_tasks} 件を除いて"
                f" {new_tasks} 件ありました）"
                if done_tasks
                else f"（{new_tasks} 件ありました）"
            )
            raise ActionError(
                f"1 回のプラン提案で新しく走らせられるジョブは最大 {max_tasks} 件です"
                + detail
            )
    tasks: list[AgentTask] = []
    for index, item in enumerate(raw, start=1):
        where = f"tasks[{index}]"
        if not isinstance(item, dict):
            raise ActionError(f"{where}: オブジェクトで指定してください")
        job = validate_job(
            item.get("job"),
            where=where,
            known_loras=known_loras,
            known_families=known_families,
        )
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
    known_loras: dict[str, str] | None = None,
    known_families: dict[str, str] | None = None,
    max_tasks: int | None = None,
    done_tasks: int = 0,
) -> AgentAction | None:
    """Parse the (optional) action of one Grok answer. Raises :class:`ActionError`.

    ``max_tasks`` limits how many **new** jobs one plan proposal may add
    (``None`` = no limit); ``done_tasks`` is how many already finished tasks the
    session's current plan holds, so a revision may re-list them for free.
    Only the self-driving check-in mode passes a limit — see
    :func:`app.agent_runner.plan_task_limits`.
    """
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
            payload.get("tasks"),
            max_tasks=max_tasks,
            done_tasks=done_tasks,
            known_loras=known_loras,
            known_families=known_families,
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
    elif name == "library":
        job_id = payload.get("job_id")
        if not job_id:
            raise ActionError("library には対象の job_id が必要です")
        action.job_id = str(job_id)
        source = str(payload.get("source") or "").strip()
        if source not in LIBRARY_SOURCES:
            raise ActionError(
                "library の source は"
                f" {' / '.join(LIBRARY_SOURCES)} のいずれかで指定してください"
            )
        action.source = source
        action.title = str(payload.get("title") or "").strip()
        action.tags = library_service.normalize_tags(payload.get("tags"))
    elif name == "library_search":
        action.query = str(payload.get("q") or payload.get("query") or "").strip()
        tag = str(payload.get("tag") or "").strip()
        action.tag = tag or None
        kind = str(payload.get("kind") or "").strip()
        if kind and kind not in library_service.KINDS:
            raise ActionError(
                "library_search の kind は"
                f" {' / '.join(library_service.KINDS)} のいずれか"
                "（省略すると全種別）で指定してください"
            )
        action.library_kind = kind or None
        try:
            action.offset = max(0, int(payload.get("offset") or 0))
        except (TypeError, ValueError) as exc:
            raise ActionError(
                "library_search の offset は 0 以上の整数で指定してください"
            ) from exc
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
