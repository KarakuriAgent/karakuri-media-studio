from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

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
    # longer timeout research / inspection turns need.
    agent_grok_args: list[str] = Field(default_factory=list)
    agent_grok_timeout: float = 300.0
    agent_max_plan_tasks: int = 5
    # {"<node_id>.<field>": "file.safetensors"} — only the entries that differ
    # from the workflow template are stored (SPEC §3.3).
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
    """One model-file input of the workflow template (SPEC §3.3)."""

    key: str  # f"{node_id}.{field}"
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

    audio_name: str = ""  # file name on the ComfyUI input directory
    start_image_name: str = ""  # mode B: file name on the ComfyUI input directory

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
) -> list[str]:
    """Per-mode required fields (SPEC §2 / §3.1). Empty list == valid."""
    missing: list[str] = []
    if mode in ("full", "i2v") and not (audio_path or "").strip():
        missing.append("audio_path")
    if mode == "i2v" and not (source_image or "").strip():
        missing.append("source_image")
    if mode in ("full", "image_only") and not (image_prompt or "").strip():
        missing.append("image_prompt")
    if mode in ("full", "i2v") and not (video_prompt or "").strip():
        missing.append("video_prompt")
    return missing


class JobCreate(BaseModel):
    """POST /api/jobs body."""

    mode: JobMode = "full"

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

    seed: int | None = None  # None -> random (recorded in params)

    chat_session_id: str | None = None
    user_input: str | None = None

    @model_validator(mode="after")
    def _check_required(self) -> "JobCreate":
        missing = missing_job_fields(
            self.mode,
            image_prompt=self.image_prompt,
            video_prompt=self.video_prompt,
            audio_path=self.audio_path,
            source_image=self.source_image,
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

    video_prompt: str | None = None
    negative_prompt: str | None = None
    aspect_ratio: str | None = None
    megapixels: float | None = None
    duration: float | None = None
    fps: int | None = None
    audio_path: str | None = None
    seed: int | None = None
    chat_session_id: str | None = None
    user_input: str | None = None


class JobProgress(BaseModel):
    """Payload broadcast on WS /api/ws."""

    type: Literal["job"] = "job"
    job_id: str
    status: JobStatus
    node: str | None = None
    progress: float | None = None
    message: str | None = None


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
    kind: Literal["audio", "image"]
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
    "plan", "run_task", "continue", "rerun", "inspect", "note", "checkin", "done"
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
    overrides: dict[str, Any] = Field(default_factory=dict)


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


class Options(BaseModel):
    """Choices for the generation form (SPEC §9 GET /api/options)."""

    comfy_connected: bool = False
    comfy_error: str | None = None
    comfy_url: str = ""
    aspect_ratios: list[str] = Field(default_factory=list)
    lora_files: list[str] = Field(default_factory=list)
    loras: list[Lora] = Field(default_factory=list)
    audio_assets: list["Asset"] = Field(default_factory=list)
    image_assets: list["Asset"] = Field(default_factory=list)
    negative_presets: dict[str, str] = Field(default_factory=dict)
