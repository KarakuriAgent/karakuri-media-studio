"""Canvas Studio のモデル。既存 app/models.py とは独立させる。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

CanvasNodeKind = Literal[
    "style", "character", "location", "object", "script",
    "storyboard", "media", "text", "model",
]
CanvasRole = Literal["user", "assistant", "event"]

#: model カードの生成対象（既存 WorkflowKind と同じ語彙）
ModelTarget = Literal["image", "video", "audio"]


class CanvasViewport(BaseModel):
    x: float = 0.0
    y: float = 0.0
    zoom: float = 1.0


class CanvasProject(BaseModel):
    id: str
    created_at: str
    updated_at: str
    title: str = ""
    #: キャンバスエージェントの LLM provider（canvas.llm.PROVIDERS）
    llm: str = "grok"
    viewport: CanvasViewport = Field(default_factory=CanvasViewport)


class CanvasProjectCreate(BaseModel):
    title: str = ""


class CanvasProjectUpdate(BaseModel):
    title: str | None = None
    llm: str | None = None
    viewport: CanvasViewport | None = None


class CanvasNode(BaseModel):
    id: str
    project_id: str
    created_at: str
    updated_at: str
    kind: CanvasNodeKind
    title: str = ""
    data: dict[str, Any] = Field(default_factory=dict)
    x: float = 0.0
    y: float = 0.0
    w: float = 320.0
    h: float = 220.0
    z: int = 0


class CanvasNodeCreate(BaseModel):
    kind: CanvasNodeKind
    title: str = ""
    data: dict[str, Any] = Field(default_factory=dict)
    x: float = 0.0
    y: float = 0.0
    w: float = 320.0
    h: float = 220.0


class CanvasNodeUpdate(BaseModel):
    """PATCH …/nodes/{id}: 送った項目だけ変える（library の PATCH と同じ流儀）。"""

    title: str | None = None
    data: dict[str, Any] | None = None
    x: float | None = None
    y: float | None = None
    w: float | None = None
    h: float | None = None
    z: int | None = None


class CanvasMessage(BaseModel):
    id: str
    project_id: str
    ts: str
    role: CanvasRole
    content: str
    kind: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)


class CanvasSendMessage(BaseModel):
    content: str


class CanvasProjectDetail(CanvasProject):
    """GET …/projects/{id}: プロジェクト + 全カード + 会話。"""

    nodes: list[CanvasNode] = Field(default_factory=list)
    messages: list[CanvasMessage] = Field(default_factory=list)
    #: runner のインメモリ状態（DB には保存しない。agent の thinking と同じ扱い）
    thinking: bool = False


class CanvasReply(BaseModel):
    """POST …/messages のレスポンス。"""

    content: str = ""
    project: CanvasProjectDetail


class CanvasProgress(BaseModel):
    """WS /api/ws に流す canvas イベント（``type: "canvas"``）。"""

    type: Literal["canvas"] = "canvas"
    project_id: str
    #: node_created / node_updated / node_deleted / message / thinking / job
    event: str
    node_id: str | None = None
    job_id: str | None = None
    thinking: bool | None = None
    message: str | None = None


# --------------------------------------------------------------------------
# kind ごとの data スキーマ（検証は create/update 時に dispatch）
# --------------------------------------------------------------------------

class StyleData(BaseModel):
    model_config = ConfigDict(extra="forbid")
    description: str = ""      # 画風・トーンの説明（日本語可）
    palette: str = ""          # 色調・カラーパレットのメモ
    references: list[str] = Field(default_factory=list)  # 参照画像 URL（/library/... 等）
    notes: str = ""


class CharacterData(BaseModel):
    model_config = ConfigDict(extra="forbid")
    description: str = ""      # ひとこと紹介
    appearance: str = ""       # 外見（生成プロンプトに使える具体性で）
    personality: str = ""
    voice: str = ""            # 声・話し方（音声生成用）
    images: list[str] = Field(default_factory=list)  # 参照画像 URL
    notes: str = ""


class LocationData(BaseModel):
    model_config = ConfigDict(extra="forbid")
    description: str = ""
    mood: str = ""             # 時間帯・天候・雰囲気
    images: list[str] = Field(default_factory=list)
    notes: str = ""


class ObjectData(BaseModel):
    model_config = ConfigDict(extra="forbid")
    description: str = ""
    images: list[str] = Field(default_factory=list)
    notes: str = ""


class ScriptScene(BaseModel):
    model_config = ConfigDict(extra="forbid")
    no: int = 0
    heading: str = ""          # シーン見出し（場所・時間）
    body: str = ""             # ト書き・セリフ


class ScriptData(BaseModel):
    model_config = ConfigDict(extra="forbid")
    synopsis: str = ""
    scenes: list[ScriptScene] = Field(default_factory=list)
    notes: str = ""


class StoryboardCut(BaseModel):
    """既存 AgentStoryboardCut と同じ形（コピー。import はしない = 独立性の維持）。"""

    model_config = ConfigDict(extra="forbid")
    no: int = 0
    scene: str = ""
    description: str = ""
    camera: str = ""
    audio: str = ""
    duration: float | None = None
    prompt: str = ""           # 想定生成プロンプト（英語）
    image: str = ""            # 紐付けたラフ画像の URL


class StoryboardData(BaseModel):
    model_config = ConfigDict(extra="forbid")
    notes: str = ""
    cuts: list[StoryboardCut] = Field(default_factory=list)


class MediaData(BaseModel):
    """生成結果 / 手動アップロード素材のカード。"""

    model_config = ConfigDict(extra="forbid")
    media_type: Literal["image", "video", "audio"] = "image"
    url: str = ""              # /outputs/... /library/... /assets/...
    job_id: str | None = None  # generate アクション由来なら元ジョブ
    prompt: str = ""           # そのとき使ったプロンプト（再生成の手がかり）
    caption: str = ""


class TextData(BaseModel):
    model_config = ConfigDict(extra="forbid")
    body: str = ""


class ModelLoraRef(BaseModel):
    """既存 LoraRef と同じ形（models.LoraRef のコピー）。"""

    model_config = ConfigDict(extra="forbid")
    lora_name: str
    trigger_word: str = ""
    strength: float = 1.0


class ModelParams(BaseModel):
    """model カードに書ける生成パラメータ。JobCreate へのマッピングは generate.py。"""

    model_config = ConfigDict(extra="forbid")
    aspect_ratio: str = "4:3 (Standard)"
    megapixels: float = 1.0
    duration: float = 10.0      # 動画の尺 / 音声の長さ（秒）
    fps: int = 25
    sage_attention: bool | None = None   # None = サーバー設定の既定値
    easy_cache: bool | None = None
    loras: list[ModelLoraRef] = Field(default_factory=list)        # 画像 LoRA
    video_loras: list[ModelLoraRef] = Field(default_factory=list)  # 動画 LoRA
    negative_prompt: str = ""            # 空 = JobCreate の既定値に任せる
    selects: dict[str, str] = Field(default_factory=dict)
    model_overrides: dict[str, str] = Field(default_factory=dict)


class ModelData(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target: ModelTarget = "image"
    workflow: str = ""          # 既存カタログのワークフロー ID（例 "krea2_turbo"）
    params: ModelParams = Field(default_factory=ModelParams)
    note: str = ""              # 「何用のモデル設定か」のメモ


#: kind -> data の検証モデル
NODE_DATA_MODELS: dict[str, type[BaseModel]] = {
    "style": StyleData, "character": CharacterData, "location": LocationData,
    "object": ObjectData, "script": ScriptData, "storyboard": StoryboardData,
    "media": MediaData, "text": TextData, "model": ModelData,
}


def validate_node_data(kind: str, data: dict[str, Any]) -> dict[str, Any]:
    """kind のスキーマで検証して正規化した dict を返す（不正は ValidationError）。"""
    return NODE_DATA_MODELS[kind](**data).model_dump()
