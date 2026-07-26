from typing import Any, Literal

from pydantic import BaseModel, Field

JobMode = Literal["full", "i2v", "image_only"]
JobStatus = Literal["queued", "prompting", "running", "done", "failed", "canceled"]


class Settings(BaseModel):
    comfy_url: str = "http://127.0.0.1:8188"
    comfy_api_key: str = ""
    grok_command: str = "grok"
    grok_model: str = "grok-4.5"
    grok_workdir: str = ""


class SettingsUpdate(BaseModel):
    comfy_url: str | None = None
    comfy_api_key: str | None = None
    grok_command: str | None = None
    grok_model: str | None = None
    grok_workdir: str | None = None


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


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str
    ts: str


class ChatSession(BaseModel):
    id: str
    created_at: str
    job_id: str | None = None
    messages: list[ChatMessage] = Field(default_factory=list)


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
