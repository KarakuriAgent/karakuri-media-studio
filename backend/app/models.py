from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .workflows import (
    DEFAULT_IMAGE_WORKFLOW,
    DEFAULT_VIDEO_WORKFLOW,
    INPUT_FIELDS,
    WorkflowSpecError,
    get_video_spec,
)

JobMode = Literal["full", "i2v", "image_only"]
JobStatus = Literal["queued", "prompting", "running", "done", "failed", "canceled"]


class Settings(BaseModel):
    # `model_overrides` would otherwise collide with pydantic's `model_` namespace.
    model_config = ConfigDict(protected_namespaces=())

    comfy_url: str = "http://127.0.0.1:8188"
    comfy_api_key: str = ""
    grok_command: str = "grok"
    grok_model: str = "grok-4.5"
    grok_workdir: str = ""
    # Agent mode (AGENT-MODE §3.4): extra CLI flags (tool permissions) and the
    # longer timeout research / inspection turns need. `--permission-mode auto`
    # is confirmed on grok 0.2.112 to enable file read/write (incl. viewing
    # images) and web search in headless `-p` runs.
    agent_grok_args: list[str] = Field(
        default_factory=lambda: ["--permission-mode", "auto"]
    )
    agent_grok_timeout: float = 300.0
    agent_max_plan_tasks: int = 5
    # {"<workflow_id>/<node_id>.<field>": "file.safetensors"} — only the entries
    # that differ from the workflow template are stored (SPEC §3.3).  Unscoped
    # keys from an older layout are ignored.
    model_overrides: dict[str, str] = Field(default_factory=dict)


class SettingsUpdate(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    comfy_url: str | None = None
    comfy_api_key: str | None = None
    grok_command: str | None = None
    grok_model: str | None = None
    grok_workdir: str | None = None
    model_overrides: dict[str, str] | None = None
    agent_grok_args: list[str] | None = None
    agent_grok_timeout: float | None = None
    agent_max_plan_tasks: int | None = None


class ModelField(BaseModel):
    """One model-file input of one workflow template (SPEC §3.3)."""

    key: str  # f"{workflow_id}/{node_id}.{field}"
    workflow_id: str = ""
    workflow_label: str = ""
    node_id: str
    field: str
    class_type: str
    title: str = ""
    default: str = ""


class ModelFieldState(ModelField):
    """A :class:`ModelField` with the currently effective value applied."""

    value: str = ""
    overridden: bool = False


class ModelOverridesUpdate(BaseModel):
    """PUT /api/models body."""

    overrides: dict[str, str] = Field(default_factory=dict)


class Lora(BaseModel):
    id: int
    display_name: str
    lora_name: str
    trigger_word: str
    default_strength: float = 1.0
    default_audio: str | None = None
    sort_order: int = 0
    # サンプル画像の URL（/assets/lora_samples/<id>/<file>）。登録・削除は
    # 専用エンドポイント経由のみで、Create / Update では触れない。
    sample_images: list[str] = Field(default_factory=list)


class LoraCreate(BaseModel):
    display_name: str
    lora_name: str
    trigger_word: str
    default_strength: float = 1.0
    default_audio: str | None = None
    sort_order: int = 0


class LoraUpdate(BaseModel):
    display_name: str | None = None
    lora_name: str | None = None
    trigger_word: str | None = None
    default_strength: float | None = None
    default_audio: str | None = None
    sort_order: int | None = None


# Default of the LTX 2.3 "dev" templates (t2v / i2v / ia2v / id_lora).  An empty
# negative means "keep whatever the selected template ships with" (SPEC §3.1).
DEFAULT_NEGATIVE_PROMPT = "pc game, console game, video game, cartoon, childish, ugly"


class LoraRef(BaseModel):
    """One LoRA selected for a job (snapshot of the registry entry)."""

    lora_name: str
    trigger_word: str = ""
    strength: float = 1.0


class GenerationParams(BaseModel):
    """Everything the workflow injector needs for one job (SPEC §3)."""

    mode: JobMode = "full"
    job_id: str = ""

    # which templates to run (see app/workflows.py)
    image_workflow: str = DEFAULT_IMAGE_WORKFLOW
    video_workflow: str = DEFAULT_VIDEO_WORKFLOW

    aspect_ratio: str = "4:3 (Standard)"
    megapixels: float = 1.0

    loras: list[LoraRef] = Field(default_factory=list)
    trigger_text: str = ""  # already-concatenated / user-edited trigger words

    image_prompt: str = ""
    video_prompt: str = ""
    negative_prompt: str = DEFAULT_NEGATIVE_PROMPT

    duration: float = 10.0
    fps: int = 25

    image_seed: int = 0
    video_seeds: list[int] = Field(default_factory=list)

    # file names on the ComfyUI input directory (uploaded by the job runner)
    audio_name: str = ""
    start_image_name: str = ""
    end_image_name: str = ""
    reference_video_name: str = ""

    filename_prefix: str | None = None  # explicit override

    @property
    def video_filename_prefix(self) -> str:
        return self.filename_prefix or f"video/{self.job_id}"

    @property
    def image_filename_prefix(self) -> str:
        return self.filename_prefix or f"images/{self.job_id}"


class Job(BaseModel):
    id: str
    created_at: str
    mode: JobMode
    status: JobStatus
    user_input: str | None = None
    image_prompt: str | None = None
    video_prompt: str | None = None
    grok_raw: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    workflow_json: dict[str, Any] = Field(default_factory=dict)
    comfy_prompt_id: str | None = None
    image_path: str | None = None
    video_path: str | None = None
    last_frame_path: str | None = None
    source_image: str | None = None
    audio_path: str | None = None
    error: str | None = None

    # NSFW フラグ: nsfw_source は '' = 未判定 / 'auto' / 'manual'
    nsfw: bool = False
    nsfw_source: str = ""

    # convenience for the SPA (derived from the paths above, see jobs._row_to_job)
    image_url: str | None = None
    video_url: str | None = None
    last_frame_url: str | None = None


# --------------------------------------------------------------------------
# job API payloads (SPEC §9)
# --------------------------------------------------------------------------

def missing_job_fields(
    mode: str,
    *,
    image_prompt: str | None,
    video_prompt: str | None,
    audio_path: str | None,
    source_image: str | None,
    end_image: str | None = None,
    reference_video: str | None = None,
    video_workflow: str | None = None,
) -> list[str]:
    """Required fields for a mode + video workflow (SPEC §2 / §3.1).

    The asset requirements come from the selected video workflow's manifest, so
    e.g. t2v needs no start frame while flf2v needs two images.  In ``full``
    mode the start frame is produced by the image stage and therefore not
    required as an input.  Empty list == valid.
    """
    missing: list[str] = []
    if mode in ("full", "image_only") and not (image_prompt or "").strip():
        missing.append("image_prompt")
    if mode in ("full", "i2v") and not (video_prompt or "").strip():
        missing.append("video_prompt")
    if mode not in ("full", "i2v"):
        return missing

    spec = get_video_spec(video_workflow)
    provided = {
        "image": source_image,
        "audio": audio_path,
        "end_image": end_image,
        "video": reference_video,
    }
    for name in spec.requires:
        if name == "image" and mode == "full":
            continue  # the image stage generates the start frame
        if not (provided.get(name) or "").strip():
            missing.append(INPUT_FIELDS[name])
    return missing


def video_workflow_problem(mode: str, video_workflow: str | None) -> str | None:
    """Why this workflow cannot be used in this mode (None == fine)."""
    if mode not in ("full", "i2v"):
        return None
    try:
        spec = get_video_spec(video_workflow)
    except WorkflowSpecError as exc:
        return str(exc)
    if mode == "full" and not spec.accepts_start_image:
        return (
            f"video workflow '{spec.id}' cannot take a generated start frame;"
            " use it in 動画生成 mode instead"
        )
    return None


class JobCreate(BaseModel):
    """POST /api/jobs body."""

    mode: JobMode = "full"

    # id of the video template to run (see app/workflows.py); the image template
    # is currently the only one there is, hence no field for it.
    video_workflow: str = DEFAULT_VIDEO_WORKFLOW

    image_prompt: str = ""
    video_prompt: str = ""
    negative_prompt: str = DEFAULT_NEGATIVE_PROMPT

    aspect_ratio: str = "4:3 (Standard)"
    megapixels: float = 1.0

    loras: list[LoraRef] = Field(default_factory=list)
    trigger_text: str = ""

    duration: float = 10.0
    fps: int = 25

    # absolute path inside assets/ or the "/assets/..." URL returned by the
    # asset upload endpoints.
    audio_path: str | None = None
    source_image: str | None = None
    end_image: str | None = None
    reference_video: str | None = None

    seed: int | None = None  # None -> random (recorded in params)

    chat_session_id: str | None = None
    user_input: str | None = None

    # 明示指定された NSFW フラグ（manual 扱い）。None なら自動判定に任せる。
    nsfw: bool | None = None

    @model_validator(mode="after")
    def _check_required(self) -> "JobCreate":
        problem = video_workflow_problem(self.mode, self.video_workflow)
        if problem:
            raise ValueError(problem)
        missing = missing_job_fields(
            self.mode,
            image_prompt=self.image_prompt,
            video_prompt=self.video_prompt,
            audio_path=self.audio_path,
            source_image=self.source_image,
            end_image=self.end_image,
            reference_video=self.reference_video,
            video_workflow=self.video_workflow,
        )
        if missing:
            raise ValueError(
                f"mode '{self.mode}' requires: {', '.join(missing)}"
            )
        return self


class JobRerun(BaseModel):
    """POST /api/jobs/{id}/rerun body (all optional)."""

    seed: int | None = None
    randomize_seed: bool = True


class JobContinue(BaseModel):
    """POST /api/jobs/{id}/continue body (all optional overrides)."""

    video_workflow: str | None = None
    video_prompt: str | None = None
    negative_prompt: str | None = None
    aspect_ratio: str | None = None
    megapixels: float | None = None
    duration: float | None = None
    fps: int | None = None
    audio_path: str | None = None
    # extra inputs of the workflow the continuation switches to (flf2v needs a
    # closing frame, the motion IC-LoRA a reference clip); omitted means "keep
    # whatever the source job used".
    end_image: str | None = None
    reference_video: str | None = None
    seed: int | None = None
    chat_session_id: str | None = None
    user_input: str | None = None


class NsfwUpdate(BaseModel):
    """POST /api/jobs/{id}/nsfw と POST /api/agent/sessions/{id}/nsfw の body。"""

    nsfw: bool


class JobProgress(BaseModel):
    """Payload broadcast on WS /api/ws."""

    type: Literal["job"] = "job"
    job_id: str
    status: JobStatus
    node: str | None = None
    progress: float | None = None
    message: str | None = None
    # NSFW フラグが確定したときだけ入る（未指定は None）。
    nsfw: bool | None = None


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str
    ts: str


class ChatSession(BaseModel):
    id: str
    created_at: str
    job_id: str | None = None
    messages: list[ChatMessage] = Field(default_factory=list)


PromptTemplate = Literal["natural", "tagged"]


class ChatLoraRef(LoraRef):
    """A selected LoRA as the chat sees it: the trigger word plus the human name.

    ``display_name`` lets the system prompt map e.g. 「サクラ」 -> ``sakura`` so Grok can
    resolve the Japanese name the user actually types.  Job params keep the
    plain :class:`LoraRef` snapshot.
    """

    display_name: str = ""


class ChatSessionCreate(BaseModel):
    """POST /api/chat/sessions body: a snapshot of the generation form (§4.3)."""

    mode: JobMode = "full"
    # the video template the form has selected: its characteristics decide how
    # `video_prompt` has to be written (SPEC §4.3).
    video_workflow: str = DEFAULT_VIDEO_WORKFLOW
    loras: list[ChatLoraRef] = Field(default_factory=list)
    trigger_text: str = ""
    duration: float = 10.0
    image_prompt_draft: str = ""
    video_prompt_draft: str = ""
    prompt_template: PromptTemplate = "natural"
    # mode B start frame (assets path or "/assets/..." URL); copied into the
    # grok work dir so the CLI can look at it.
    start_image_path: str | None = None


class ChatSendMessage(BaseModel):
    content: str


class PromptResult(BaseModel):
    """Final proposal parsed out of the Grok answer."""

    image_prompt: str | None = None
    video_prompt: str | None = None
    notes: str | None = None


class ChatReply(BaseModel):
    """POST /api/chat/sessions/{id}/messages response."""

    role: Literal["assistant"] = "assistant"
    content: str
    result: PromptResult | None = None


class Asset(BaseModel):
    name: str
    kind: Literal["audio", "image", "video"]
    path: str
    url: str
    size: int


class HealthStatus(BaseModel):
    status: Literal["ok", "not_configured", "not_implemented", "error"]
    detail: str | None = None


class Health(BaseModel):
    app: Literal["ok"] = "ok"
    comfyui: HealthStatus
    grok: HealthStatus


# --------------------------------------------------------------------------
# agent mode (AGENT-MODE §4 / §5)
# --------------------------------------------------------------------------

AgentStatus = Literal[
    "idle", "planning", "running", "waiting_checkin", "stopped", "done"
]
AgentCheckinMode = Literal["every_job", "milestone", "auto"]
AgentActionName = Literal[
    "plan", "run_task", "continue", "rerun", "inspect", "note", "rename",
    "checkin", "done",
]
AgentTaskStatus = Literal["pending", "running", "done", "failed", "skipped"]


class AgentMessage(BaseModel):
    """One entry of the制作記録 transcript (AGENT-MODE §4).

    ``event`` は task_started / task_done / task_failed / inspect_result 等の
    システムイベント、``checkin`` はユーザーへの確認吹き出し。
    """

    role: Literal["system", "user", "assistant", "event", "checkin"]
    content: str
    ts: str
    kind: str | None = None  # event / checkin の種別
    data: dict[str, Any] = Field(default_factory=dict)


class AgentTask(BaseModel):
    """One planned job. ``job`` is a validated :class:`JobCreate` snapshot."""

    id: str = ""
    label: str = ""
    job: dict[str, Any] = Field(default_factory=dict)
    status: AgentTaskStatus = "pending"
    job_id: str | None = None
    error: str | None = None
    retries: int = 0


class AgentPlan(BaseModel):
    version: int = 0
    notes: str = ""
    approved: bool = False
    tasks: list[AgentTask] = Field(default_factory=list)


class AgentArtifact(BaseModel):
    """成果物パネルの 1 カード（AGENT-MODE §1）。"""

    kind: Literal["plan", "note", "research", "frame", "image", "video"]
    title: str = ""
    ts: str
    name: str = ""  # workdir 相対のファイル名（外部成果物は空）
    url: str | None = None
    job_id: str | None = None
    text: str | None = None


class AgentSession(BaseModel):
    id: str
    created_at: str
    title: str = ""
    status: AgentStatus = "idle"
    checkin_mode: AgentCheckinMode = "milestone"
    auto_limit: int = 5
    messages: list[AgentMessage] = Field(default_factory=list)
    plan: AgentPlan = Field(default_factory=AgentPlan)
    artifacts: list[AgentArtifact] = Field(default_factory=list)
    # NSFW フラグ（'' = 未判定 / 'auto' / 'manual'）
    nsfw: bool = False
    nsfw_source: str = ""
    # Grok ターンの実行中フラグ（agent_runner のインメモリ状態。DB には保存しない）
    thinking: bool = False


class AgentSessionSummary(BaseModel):
    """GET /api/agent/sessions の一覧行（メッセージ本体は含めない）。"""

    id: str
    created_at: str
    title: str = ""
    status: AgentStatus = "idle"
    checkin_mode: AgentCheckinMode = "milestone"
    auto_limit: int = 5
    message_count: int = 0
    task_count: int = 0
    artifact_count: int = 0
    nsfw: bool = False
    nsfw_source: str = ""


class AgentSessionCreate(BaseModel):
    """POST /api/agent/sessions body (AGENT-MODE §5.1)."""

    title: str = ""
    goal: str = ""
    checkin_mode: AgentCheckinMode = "milestone"
    auto_limit: int = Field(default=5, ge=1, le=50)


class AgentSendMessage(BaseModel):
    content: str


class AgentApprove(BaseModel):
    """POST .../approve body."""

    approved: bool = True
    note: str = ""


class AgentCheckinReply(BaseModel):
    """POST .../checkin body."""

    content: str = ""
    choice: str | None = None


class AgentAction(BaseModel):
    """Parsed action object (AGENT-MODE §4). Unused fields stay at defaults."""

    action: AgentActionName
    notes: str = ""
    summary: str = ""
    question: str = ""
    options: list[str] = Field(default_factory=list)
    tasks: list[AgentTask] = Field(default_factory=list)
    task_id: str | None = None
    job_id: str | None = None
    interval: float = 1.0
    title: str = ""
    filename: str | None = None
    content: str = ""
    kind: Literal["note", "research"] = "note"  # note アクションの成果物種別
    # rename アクション: 対象成果物の指定（name か job_id[+ artifact_kind]）
    name: str | None = None
    artifact_kind: str | None = None
    overrides: dict[str, Any] = Field(default_factory=dict)
    # プラン外 continue / rerun がユーザー承認を得たか（Grok は指定できない）
    approved: bool = False


class AgentReply(BaseModel):
    """POST .../messages / approve / checkin のレスポンス。"""

    content: str = ""
    action: AgentAction | None = None
    session: AgentSession


class AgentProgress(BaseModel):
    """Payload broadcast on WS /api/ws for agent sessions (AGENT-MODE §5.1)."""

    type: Literal["agent"] = "agent"
    session_id: str
    status: AgentStatus
    task_id: str | None = None
    task_status: AgentTaskStatus | None = None
    job_id: str | None = None
    artifact: AgentArtifact | None = None
    message: str | None = None
    # Grok ターンが走っているか（None = この通知では変化なし）
    thinking: bool | None = None


class WorkflowOption(BaseModel):
    """One selectable workflow template (SPEC §3 / §8)."""

    id: str
    label: str
    kind: Literal["image", "video"]
    notes: str = ""
    #: logical inputs the workflow needs: image / audio / end_image / video
    requires: list[str] = Field(default_factory=list)
    #: logical knobs the workflow exposes (prompt, negative, duration, fps, …)
    supports: list[str] = Field(default_factory=list)
    #: can it be the second stage of a full (image -> video) job?
    accepts_start_image: bool = False
    #: UI label of the primary image input
    image_label: str = "開始フレーム"


class Options(BaseModel):
    """Choices for the generation form (SPEC §9 GET /api/options)."""

    comfy_connected: bool = False
    comfy_error: str | None = None
    comfy_url: str = ""
    image_workflows: list[WorkflowOption] = Field(default_factory=list)
    video_workflows: list[WorkflowOption] = Field(default_factory=list)
    default_video_workflow: str = DEFAULT_VIDEO_WORKFLOW
    aspect_ratios: list[str] = Field(default_factory=list)
    lora_files: list[str] = Field(default_factory=list)
    loras: list[Lora] = Field(default_factory=list)
    audio_assets: list["Asset"] = Field(default_factory=list)
    image_assets: list["Asset"] = Field(default_factory=list)
    video_assets: list["Asset"] = Field(default_factory=list)
    negative_presets: dict[str, str] = Field(default_factory=dict)
