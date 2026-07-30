from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .workflows import (
    AUDIO_CATEGORIES,
    BPM_RANGE,
    KEYSCALES,
    LANGUAGES,
    DEFAULT_AUDIO_WORKFLOW,
    DEFAULT_FAMILY,
    DEFAULT_IMAGE_WORKFLOW,
    DEFAULT_VIDEO_WORKFLOW,
    INPUT_FIELDS,
    WorkflowSpecError,
    get_audio_spec,
    get_image_spec,
    get_video_spec,
)

#: ``audio`` is a stand-alone mode: it runs one audio graph and is never
#: chained with the image / video stages (which is why every ``mode in (...)``
#: test below simply does not list it).
JobMode = Literal["full", "i2v", "image_only", "audio"]
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
    # エージェントのターンを ACP (`grok agent stdio`) で回すか。ACP だと実行中の
    # 活動（思考 / ツール実行）を UI に出せる。False なら従来のワンショット実行。
    agent_use_acp: bool = True
    # {"<workflow_id>/<node_id>.<field>": "file.safetensors"} — only the entries
    # that differ from the workflow template are stored (SPEC §3.3).  Unscoped
    # keys from an older layout are ignored.
    model_overrides: dict[str, str] = Field(default_factory=dict)
    # 同じキー形式で「そのスロットで選べるモデルファイル名」を持つ（SPEC §3.3）。
    # 2 件以上あるスロットは生成フォーム / エージェントが実行時に選べるようになる。
    # 候補が空のキーは保存しない。
    model_choices: dict[str, list[str]] = Field(default_factory=dict)
    # 不足モデルの自動ダウンロード（SPEC §3.3）。保存先の models ディレクトリは
    # **環境変数 COMFY_MODELS_DIR だけ**が決める（設定には持たない）: Docker では
    # 同じパスをマウントしていないと書けないので、UI からパスを入れられても
    # 意味がないため。ここに置くのは認証情報と URL だけ。
    #: gated / クローズドなリポジトリ用の Hugging Face トークン
    hf_token: str = ""
    civitai_api_key: str = ""
    #: {"<ファイル名>": "<ダウンロード URL>"}。同じファイルが複数スロットに出る
    #: ので、スロットキーではなくファイル名で持つ。
    model_download_urls: dict[str, str] = Field(default_factory=dict)


class SettingsUpdate(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    comfy_url: str | None = None
    comfy_api_key: str | None = None
    grok_command: str | None = None
    grok_model: str | None = None
    grok_workdir: str | None = None
    model_overrides: dict[str, str] | None = None
    model_choices: dict[str, list[str]] | None = None
    hf_token: str | None = None
    civitai_api_key: str | None = None
    model_download_urls: dict[str, str] | None = None
    agent_grok_args: list[str] | None = None
    agent_grok_timeout: float | None = None
    agent_max_plan_tasks: int | None = None
    agent_use_acp: bool | None = None


class ModelField(BaseModel):
    """One model-file input of one workflow template (SPEC §3.3)."""

    key: str  # f"{workflow_id}/{node_id}.{field}"
    workflow_id: str = ""
    workflow_label: str = ""
    #: which stage the owning workflow belongs to (settings page grouping)
    kind: Literal["image", "video", "audio"] = "image"
    node_id: str
    field: str
    class_type: str
    title: str = ""
    default: str = ""
    #: 不足していたときにダウンロードする既定の置き場所（models ディレクトリ
    #: からの相対パス、例 ``"diffusion_models"``）。ローダーが未知で決められない
    #: ときは空になり、UI で入力させる（SPEC §3.3）。
    subfolder: str = ""


class ModelFieldState(ModelField):
    """A :class:`ModelField` with the currently effective value applied."""

    value: str = ""
    overridden: bool = False
    #: そのスロットで選べるモデルファイル名（設定ページで登録した候補リスト）
    choices: list[str] = Field(default_factory=list)


class ModelOverridesUpdate(BaseModel):
    """PUT /api/models body.

    ``choices`` を省略（``None``）すると、保存済みの候補リストはそのまま残る
    （既定値の上書きだけを送っていた旧クライアントとの後方互換）。
    """

    overrides: dict[str, str] = Field(default_factory=dict)
    choices: dict[str, list[str]] | None = None


class ModelSlot(BaseModel):
    """実行時に切り替えられるモデル 1 スロット（SPEC §3.3）。

    ``default`` は現在の既定値（設定の上書き → 無ければテンプレートの値）、
    ``choices`` はそのスロットで選べるファイル名（``default`` を先頭に含む）。
    ジョブ単位の ``model_overrides`` はこの候補の中からしか選べない。
    """

    key: str  # f"{workflow_id}/{node_id}.{field}"
    workflow_id: str = ""
    workflow_label: str = ""
    kind: Literal["image", "video", "audio"] = "image"
    node_id: str
    field: str
    class_type: str
    #: 表示用のラベル（テンプレートのノード名。空ならキーを出す）
    label: str = ""
    default: str = ""
    choices: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------
# 不足モデルの自動ダウンロード（SPEC §3.3）
# --------------------------------------------------------------------------

class ModelsDirStatus(BaseModel):
    """``GET /api/models/dir-status``: models ディレクトリに書けるか。

    保存先は環境変数 ``COMFY_MODELS_DIR`` だけで決まる。**未設定なら機能ごと
    無効**で、UI はダウンロード関連を一切出さない（Comfy Cloud 利用などでは
    それが正常な状態）。設定されているのに書けない場合（Docker でホストの
    models ディレクトリを同じ絶対パスにマウントしていない等）は ``exists`` /
    ``writable`` が false になり、UI はボタンを無効化して理由を出す。
    """

    #: 環境変数 ``COMFY_MODELS_DIR`` が設定されているか
    configured: bool = False
    exists: bool = False
    writable: bool = False
    path: str = ""


class ModelDownloadRequest(BaseModel):
    """``POST /api/models/download`` のボディ。

    ``subfolder`` は models ディレクトリからの相対パス（``ModelField.subfolder``
    をそのまま送ればよい）。空なら models ディレクトリ直下に置く。
    """

    filename: str
    url: str
    subfolder: str = ""


class ModelDownloadProgress(BaseModel):
    """WS /api/ws に流すダウンロードの進捗（``type: "model_download"``）。

    ``total`` は ``Content-Length`` が返らないサーバーでは ``None`` になる。
    """

    type: Literal["model_download"] = "model_download"
    filename: str
    status: Literal["downloading", "done", "error"] = "downloading"
    received: int = 0
    total: int | None = None
    error: str | None = None


class ModelDownload(ModelDownloadProgress):
    """``GET /api/models/downloads`` の 1 件（進捗＋保存先）。"""

    subfolder: str = ""
    url: str = ""
    #: 保存先の絶対パス（検証済み）
    path: str = ""


#: どちらのワークフローに挿す LoRA か（SPEC §3.4）。'image' は画像ワークフロー、
#: 'video' は LTX 2.3 の動画ワークフローに注入される。
LoraTarget = Literal["image", "video"]


class Lora(BaseModel):
    id: int
    display_name: str
    lora_name: str
    trigger_word: str
    default_strength: float = 1.0
    default_audio: str | None = None
    sort_order: int = 0
    target: LoraTarget = "image"
    # どの画像モデルファミリー向けに学習された LoRA か（krea2 / anima /
    # z-image / qwen-image）。同じファミリーの画像ワークフローでしか使えない。
    # target='video' の行では無視される（動画は LTX 2.3 のみ）。
    family: str = DEFAULT_FAMILY
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
    target: LoraTarget = "image"
    family: str = DEFAULT_FAMILY


class LoraUpdate(BaseModel):
    display_name: str | None = None
    lora_name: str | None = None
    trigger_word: str | None = None
    default_strength: float | None = None
    default_audio: str | None = None
    sort_order: int | None = None
    target: LoraTarget | None = None
    family: str | None = None


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
    audio_workflow: str = DEFAULT_AUDIO_WORKFLOW

    aspect_ratio: str = "4:3 (Standard)"
    megapixels: float = 1.0
    # 参照画像（開始フレーム）の実寸 (w, h)。分かっている場合、動画側の幅・高さは
    # `aspect_ratio` プリセットではなくこの比から計算される（SPEC §3.1）。
    start_image_size: tuple[int, int] | None = None

    # 画像ワークフロー（Krea 2）に挿す LoRA
    loras: list[LoraRef] = Field(default_factory=list)
    trigger_text: str = ""  # already-concatenated / user-edited trigger words
    # 動画ワークフロー（LTX 2.3）に挿す LoRA。`video_trigger_text` が空なら
    # `video_loras` のトリガーワードを連結したものが使われる。
    video_loras: list[LoraRef] = Field(default_factory=list)
    video_trigger_text: str = ""

    image_prompt: str = ""
    video_prompt: str = ""
    negative_prompt: str = DEFAULT_NEGATIVE_PROMPT

    # `duration` is the clip length of the video stage **and** the track length
    # of an audio job (both in seconds) — a job only ever runs one of them.
    duration: float = 10.0
    fps: int = 25

    # --- audio job knobs (mode 'audio' only, see workflow.build_audio_workflow)
    audio_prompt: str = ""
    #: ACE-Step: the words to sing, with [verse] / [chorus] structure tags.
    #: Empty == instrumental.
    lyrics: str = ""
    bpm: int = 120
    keyscale: str = "C major"
    language: str = "en"
    #: Stable Audio: which built-in prompt template to use
    audio_category: str = AUDIO_CATEGORIES[0]
    #: Stable Audio: expand `audio_prompt` with the graph's own local LLM first
    reprompt: bool = False

    #: 選択式フィールドの値（論理名 -> 選んだ文字列。SPEC §3.1）。宣言のない
    #: ワークフローでは常に空。
    selects: dict[str, str] = Field(default_factory=dict)

    image_seed: int = 0
    video_seeds: list[int] = Field(default_factory=list)
    audio_seed: int = 0

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

    @property
    def audio_filename_prefix(self) -> str:
        return self.filename_prefix or f"audio/{self.job_id}"


class Job(BaseModel):
    id: str
    created_at: str
    mode: JobMode
    status: JobStatus
    user_input: str | None = None
    image_prompt: str | None = None
    video_prompt: str | None = None
    #: mode 'audio' only — what the audio model was asked to produce
    audio_prompt: str | None = None
    grok_raw: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    workflow_json: dict[str, Any] = Field(default_factory=dict)
    comfy_prompt_id: str | None = None
    image_path: str | None = None
    video_path: str | None = None
    last_frame_path: str | None = None
    source_image: str | None = None
    #: the *reference* audio a video job was given (an input, not a result)
    audio_path: str | None = None
    #: the track a mode 'audio' job produced (an output)
    audio_output_path: str | None = None
    error: str | None = None

    # NSFW フラグ: nsfw_source は '' = 未判定 / 'auto' / 'manual'
    nsfw: bool = False
    nsfw_source: str = ""

    # convenience for the SPA (derived from the paths above, see jobs._row_to_job)
    image_url: str | None = None
    video_url: str | None = None
    last_frame_url: str | None = None
    audio_output_url: str | None = None


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
    image_workflow: str | None = None,
    audio_prompt: str | None = None,
) -> list[str]:
    """Required fields for a mode + image / video workflow (SPEC §2 / §3.1).

    The asset requirements come from the selected workflows' manifests, so e.g.
    t2v needs no start frame while flf2v needs two images.  In ``full`` mode the
    *video* start frame is produced by the image stage and therefore not
    required as an input — but an **editing** image workflow (qwen-image) still
    needs its own ``source_image`` in every mode that runs the image stage.
    Empty list == valid.

    ``mode: "audio"`` is stand-alone: it runs one audio graph, needs nothing but
    an ``audio_prompt`` and never touches the image / video requirements below.
    """
    if mode == "audio":
        return [] if (audio_prompt or "").strip() else ["audio_prompt"]

    missing: list[str] = []
    if mode in ("full", "image_only") and not (image_prompt or "").strip():
        missing.append("image_prompt")
    # プロンプトを選択肢から組み立てるワークフロー（wan_dancer）では video_prompt
    # は任意。書かれた場合だけテンプレートに注入される（SPEC §3.1）。
    if (
        mode in ("full", "i2v")
        and not (video_prompt or "").strip()
        and get_video_spec(video_workflow).prompt_required
    ):
        missing.append("video_prompt")

    if mode in ("full", "image_only"):
        image_spec = get_image_spec(image_workflow)
        for name in image_spec.requires:
            field = INPUT_FIELDS[name]
            if field not in missing and not (
                {"image": source_image}.get(name) or ""
            ).strip():
                missing.append(field)

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


def select_problem(
    mode: str,
    video_workflow: str | None,
    selects: Any,
) -> str | None:
    """選択式フィールドの指定が使えるか（None == 問題なし、SPEC §3.1）。

    宣言のない名前と、選択肢に無い値を拒否する。動画ステージを走らせないモード
    では何も見ない（選択式は今のところ動画ワークフローだけの仕組み）。
    """
    if not selects:
        return None
    if not isinstance(selects, dict):
        return "selects は {\"<名前>\": \"<選んだ値>\"} 形式のオブジェクトで指定してください"
    if mode not in ("full", "i2v"):
        return f"mode '{mode}' は動画ステージを走らせないので selects は指定できません"
    try:
        spec = get_video_spec(video_workflow)
    except WorkflowSpecError as exc:
        return str(exc)
    for name, value in selects.items():
        select = spec.select(str(name))
        if select is None:
            known = ", ".join(f"`{key}`" for key in spec.selects) or "なし"
            return (
                f"video_workflow `{spec.id}` に選択項目 `{name}` はありません"
                f"（使えるのは {known}）"
            )
        if str(value) not in select.choices:
            return (
                f"`{name}` に {value!r} は使えません（使えるのは "
                + ", ".join(f"{choice!r}" for choice in select.choices)
                + "）"
            )
    return None


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


def image_workflow_problem(mode: str, image_workflow: str | None) -> str | None:
    """Why this image workflow cannot be used in this mode (None == fine).

    Only the id itself is checked here; the assets it needs come out of
    :func:`missing_job_fields` so the message lists every missing field at once.
    """
    if mode not in ("full", "image_only"):
        return None
    try:
        get_image_spec(image_workflow)
    except WorkflowSpecError as exc:
        return str(exc)
    return None


def audio_workflow_problem(
    mode: str,
    audio_workflow: str | None,
    *,
    duration: float | None = None,
    audio_category: str | None = None,
    keyscale: str | None = None,
    language: str | None = None,
    bpm: int | None = None,
) -> str | None:
    """Why this audio job cannot run (None == fine).

    Only ``mode: "audio"`` is checked: every other mode ignores the audio
    fields entirely, so an unknown ``audio_workflow`` there is harmless.

    ``keyscale`` / ``language`` / ``bpm`` are COMBO / INT widgets of
    ``TextEncodeAceStepAudio1.5``: ComfyUI rejects the whole prompt when a
    value is outside its declared set, so they are caught here (422) instead of
    failing the job halfway through.
    """
    if mode != "audio":
        return None
    try:
        spec = get_audio_spec(audio_workflow)
    except WorkflowSpecError as exc:
        return str(exc)
    if duration is not None and not (
        spec.min_duration <= float(duration) <= spec.max_duration
    ):
        return (
            f"audio workflow '{spec.id}' supports a duration of"
            f" {spec.min_duration:g}-{spec.max_duration:g} seconds,"
            f" got {float(duration):g}"
        )
    if (
        audio_category
        and spec.supports("audio_category")
        and audio_category not in AUDIO_CATEGORIES
    ):
        return (
            f"unknown audio_category '{audio_category}';"
            f" use one of {', '.join(AUDIO_CATEGORIES)}"
        )
    if keyscale and spec.supports("keyscale") and keyscale not in KEYSCALES:
        return (
            f"unknown keyscale '{keyscale}'; use \"<root> major\" or"
            ' "<root> minor" (e.g. "C major", "F# minor")'
        )
    if language and spec.supports("language") and language not in LANGUAGES:
        return (
            f"unknown language '{language}'; use an ISO code from the model's"
            " list (en, ja, zh, …) or 'unknown'"
        )
    if bpm is not None and spec.supports("bpm") and not BPM_RANGE[0] <= int(bpm) <= BPM_RANGE[1]:
        return f"bpm must be between {BPM_RANGE[0]} and {BPM_RANGE[1]}, got {int(bpm)}"
    return None


def audio_lora_problem(mode: str, loras: list[Any], video_loras: list[Any]) -> str | None:
    """Audio workflows carry no LoRA chain at all (None == fine)."""
    if mode != "audio" or not (loras or video_loras):
        return None
    return "mode 'audio' runs no image or video stage, so LoRAs cannot be used"


def image_lora_family_problem(
    mode: str, image_workflow: str | None, families: list[str]
) -> str | None:
    """Why the picked image LoRAs do not fit the image workflow (None == fine).

    ``families`` are the registry families of the LoRAs in ``loras`` (unknown /
    unregistered ones are left out by the caller).  A LoRA trained for another
    model family would be loaded but produce garbage, so it is rejected.
    """
    if not families or mode not in ("full", "image_only"):
        return None
    try:
        spec = get_image_spec(image_workflow)
    except WorkflowSpecError as exc:
        return str(exc)
    wrong = sorted({f for f in families if f != spec.family})
    if wrong:
        return (
            f"image workflow '{spec.id}' takes {spec.family} LoRAs,"
            f" but {', '.join(wrong)} LoRA(s) were selected"
        )
    return None


def job_workflow_ids(
    mode: str,
    *,
    image_workflow: str | None = None,
    video_workflow: str | None = None,
    audio_workflow: str | None = None,
) -> list[str]:
    """このジョブが実際に走らせるワークフロー ID（SPEC §2）。

    ジョブ単位のモデル指定（``model_overrides``）のスコープに使う: 走らせない
    ワークフローのスロットを指定しても効かないので、ここに無い ID のキーは
    :func:`model_override_problem` が拒否する。
    """
    if mode == "audio":
        return [audio_workflow or DEFAULT_AUDIO_WORKFLOW]
    ids: list[str] = []
    if mode in ("full", "image_only"):
        ids.append(image_workflow or DEFAULT_IMAGE_WORKFLOW)
    if mode in ("full", "i2v"):
        ids.append(video_workflow or DEFAULT_VIDEO_WORKFLOW)
    return ids


def model_override_problem(
    overrides: Any,
    slots: list[ModelSlot],
    workflow_ids: list[str],
) -> str | None:
    """ジョブ単位のモデル指定が使えるか（None == 問題なし、SPEC §3.3）。

    ``slots`` は :func:`app.workflow.model_slots` が返す全スロット（候補が 1 件
    以下のものも含む）。不明なキー・このジョブが走らせないワークフローのキー・
    候補リストに無い値は、黙って捨てずに拒否する（設定した本人は効いたと思って
    しまうため）。
    """
    if not overrides:
        return None
    if not isinstance(overrides, dict):
        return (
            "model_overrides は"
            ' {"<workflow_id>/<node_id>.<field>": "<ファイル名>"}'
            " 形式のオブジェクトで指定してください"
        )
    by_key = {slot.key: slot for slot in slots}
    for key, value in overrides.items():
        slot = by_key.get(str(key))
        if slot is None:
            return f"不明なモデルスロットです: {key}"
        if slot.workflow_id not in workflow_ids:
            return (
                f"モデルスロット `{key}` はワークフロー `{slot.workflow_id}` の"
                f"ものなので、このジョブ（{', '.join(workflow_ids) or 'なし'}）では"
                "指定できません"
            )
        name = str(value or "").strip()
        if not name:
            return f"モデルスロット `{key}` の値が空です"
        if name not in slot.choices:
            return (
                f"モデルスロット `{key}` に `{name}` は使えません"
                f"（使えるのは {', '.join(slot.choices)} です。"
                "候補は設定ページの「モデル」タブで追加します）"
            )
    return None


def video_lora_problem(
    mode: str, video_workflow: str | None, video_loras: list[Any]
) -> str | None:
    """Why the selected workflow cannot take ``video_loras`` (None == fine).

    A run without a video stage has nowhere to put them, and a template whose
    manifest declares no ``lora_chain`` cannot be spliced — both are rejected
    rather than silently dropping the LoRAs the user picked (SPEC §3.4).
    """
    if not video_loras:
        return None
    if mode not in ("full", "i2v"):
        return f"mode '{mode}' runs no video stage, so video_loras cannot be used"
    try:
        spec = get_video_spec(video_workflow)
    except WorkflowSpecError as exc:
        return str(exc)
    if spec.lora_chain is None:
        return f"video workflow '{spec.id}' does not support video LoRAs"
    return None


class JobCreate(BaseModel):
    """POST /api/jobs body."""

    # `model_overrides` would otherwise collide with pydantic's `model_` namespace.
    model_config = ConfigDict(protected_namespaces=())

    mode: JobMode = "full"

    # ids of the templates to run (see app/workflows.py)
    video_workflow: str = DEFAULT_VIDEO_WORKFLOW
    image_workflow: str = DEFAULT_IMAGE_WORKFLOW
    #: only used by `mode: "audio"`, ignored everywhere else
    audio_workflow: str = DEFAULT_AUDIO_WORKFLOW

    image_prompt: str = ""
    video_prompt: str = ""
    negative_prompt: str = DEFAULT_NEGATIVE_PROMPT

    # --- mode 'audio' only ------------------------------------------------
    audio_prompt: str = ""
    #: ACE-Step: the words to sing ([Verse] / [Chorus] …). Empty == instrumental.
    lyrics: str = ""
    bpm: int = 120
    keyscale: str = "C major"
    language: str = "en"
    #: Stable Audio: Music / Instrument / SFX / One-shot
    audio_category: str = AUDIO_CATEGORIES[0]
    #: Stable Audio: expand the prompt with the graph's own local LLM first
    reprompt: bool = False

    aspect_ratio: str = "4:3 (Standard)"
    megapixels: float = 1.0

    # 画像ワークフロー用 LoRA（target='image' で登録したもの）
    loras: list[LoraRef] = Field(default_factory=list)
    trigger_text: str = ""
    # 動画ワークフロー用 LoRA（target='video' で登録したもの）
    video_loras: list[LoraRef] = Field(default_factory=list)
    video_trigger_text: str = ""

    duration: float = 10.0
    fps: int = 25

    # absolute path inside assets/ or the "/assets/..." URL returned by the
    # asset upload endpoints.
    audio_path: str | None = None
    source_image: str | None = None
    end_image: str | None = None
    reference_video: str | None = None

    seed: int | None = None  # None -> random (recorded in params)

    # 選択式フィールドの値（`GET /api/options` の workflow の `selects` にある
    # 論理名 -> 選んだ文字列）。省略した項目はワークフローの既定値、`auto` を
    # 宣言している項目（wan_dancer の尺）は入力から自動で決まる（SPEC §3.1）。
    selects: dict[str, str] = Field(default_factory=dict)

    # このジョブだけで使うモデルファイル名（SPEC §3.3）。キーは設定と同じ
    # `"<workflow_id>/<node_id>.<field>"`、値は設定の候補リスト（`model_choices`）に
    # あるファイル名。設定の `model_overrides` の上に重ねられる。
    model_overrides: dict[str, str] = Field(default_factory=dict)

    chat_session_id: str | None = None
    user_input: str | None = None

    # 明示指定された NSFW フラグ（manual 扱い）。None なら自動判定に任せる。
    nsfw: bool | None = None

    @model_validator(mode="after")
    def _check_required(self) -> "JobCreate":
        problem = (
            image_workflow_problem(self.mode, self.image_workflow)
            or video_workflow_problem(self.mode, self.video_workflow)
            or audio_workflow_problem(
                self.mode,
                self.audio_workflow,
                duration=self.duration,
                audio_category=self.audio_category,
                keyscale=self.keyscale,
                language=self.language,
                bpm=self.bpm,
            )
            or audio_lora_problem(self.mode, self.loras, self.video_loras)
            or video_lora_problem(self.mode, self.video_workflow, self.video_loras)
        )
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
            image_workflow=self.image_workflow,
            audio_prompt=self.audio_prompt,
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

    model_config = ConfigDict(protected_namespaces=())

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
    # 続き生成で使うモデル（省略すると元ジョブの指定を引き継ぐ。切り替わった先の
    # ワークフローに属さないキーは落とされる）
    model_overrides: dict[str, str] | None = None
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
    # the templates the form has selected: their characteristics decide how the
    # prompts have to be written (SPEC §4.3).  The image workflow's model family
    # picks which IMAGE PROMPT SPEC the system prompt embeds.
    video_workflow: str = DEFAULT_VIDEO_WORKFLOW
    image_workflow: str = DEFAULT_IMAGE_WORKFLOW
    #: mode 'audio' のセッションで使う音声ワークフロー（そのモデルのプロンプト
    #: ガイドが system prompt に埋め込まれる）
    audio_workflow: str = DEFAULT_AUDIO_WORKFLOW
    loras: list[ChatLoraRef] = Field(default_factory=list)
    trigger_text: str = ""
    video_loras: list[ChatLoraRef] = Field(default_factory=list)
    video_trigger_text: str = ""
    #: 動画のクリップ長 / 音声モードでは曲・音の長さ（どちらも秒）
    duration: float = 10.0
    image_prompt_draft: str = ""
    video_prompt_draft: str = ""
    audio_prompt_draft: str = ""
    lyrics_draft: str = ""
    prompt_template: PromptTemplate = "natural"
    # mode B start frame (assets path or "/assets/..." URL); copied into the
    # grok work dir so the CLI can look at it.
    start_image_path: str | None = None


class ChatSendMessage(BaseModel):
    content: str


class PromptResult(BaseModel):
    """Final proposal parsed out of the Grok answer.

    ``mode: "audio"`` sessions fill the audio fields instead of the image /
    video prompts; the numeric / COMBO ones are optional suggestions the form
    only applies when the selected workflow actually has them.
    """

    image_prompt: str | None = None
    video_prompt: str | None = None
    audio_prompt: str | None = None
    #: ACE-Step: the words to sing, with [Verse] / [Chorus] structure tags
    lyrics: str | None = None
    bpm: int | None = None
    keyscale: str | None = None
    language: str | None = None
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


# --------------------------------------------------------------------------
# library (SPEC §7.2)
# --------------------------------------------------------------------------

#: ライブラリに入れられる素材の種別（そのまま library/<kind>/ の置き場になる）
LibraryKind = Literal["image", "video", "audio"]

#: ジョブのどの出力をライブラリに入れるか（ResultPane のタブと同じ区分）
LibrarySource = Literal["image", "last_frame", "video", "audio"]


class LibraryItem(BaseModel):
    """ライブラリの 1 件（履歴とは独立に取っておく素材）。"""

    id: str
    created_at: str
    kind: LibraryKind
    #: 表示名（既定はファイル名 / ジョブのプロンプト。あとから変更できる）
    name: str
    #: ファイルの絶対パス（ジョブの入力にはこれか ``url`` を指定できる）
    path: str
    #: ``/library/<kind>/<file>``（静的配信 URL）
    url: str
    nsfw: bool = False
    #: '' = 未判定 / 'auto' = 元ジョブから継承 / 'manual' = 手動指定
    nsfw_source: str = ""
    #: 生成物から登録した場合の元ジョブ id（アップロードなら None）
    source_job_id: str | None = None
    #: 元ジョブのどの出力か（重複登録の判定に使う。アップロード・旧行は None）
    source: LibrarySource | None = None
    #: 分類タグ（検索・絞り込み用。順序は登録したまま）
    tags: list[str] = Field(default_factory=list)


class LibraryFromJob(BaseModel):
    """POST /api/library/from-job body（生成物をライブラリに入れる）。"""

    job_id: str
    source: LibrarySource
    #: 表示名（空ならジョブのプロンプトから決める）
    name: str = ""
    tags: list[str] = Field(default_factory=list)


class LibraryUpdate(BaseModel):
    """PATCH /api/library/{id} body（指定した項目だけ変える）。"""

    name: str | None = None
    nsfw: bool | None = None
    tags: list[str] | None = None


class LibraryProgress(BaseModel):
    """WS /api/ws に流すライブラリの更新（自動タグ生成の反映など、SPEC §7.2）。"""

    type: Literal["library"] = "library"
    item_id: str
    kind: LibraryKind
    name: str
    tags: list[str] = Field(default_factory=list)


class LibraryPage(BaseModel):
    """GET /api/library のレスポンス（絞り込み結果の 1 ページ）。

    ``total`` は絞り込み後の総件数なので、``items`` を数えるだけでは分からない
    「まだ何件あるか」がクライアント（と、エージェントの検索）に伝わる。
    """

    items: list[LibraryItem] = Field(default_factory=list)
    #: 絞り込み条件に合う総件数（このページの件数ではない）
    total: int = 0
    limit: int = 0
    offset: int = 0
    #: ライブラリに登録されている全タグ（絞り込み UI の補完用）
    tags: list[str] = Field(default_factory=list)


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
    "library", "library_search", "checkin", "done",
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

    kind: Literal["plan", "note", "research", "frame", "image", "video", "audio"]
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
    # 実行中の活動（「思考中」「ツール実行中: …」。未実行なら None）
    activity: str | None = None


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
    # 添付ファイルの workdir 相対パス（``attachments/<name>``）。本文が空でも
    # 添付だけで送信できる。
    attachments: list[str] = Field(default_factory=list)


class AgentAttachment(BaseModel):
    """POST .../attachments のレスポンス（workdir 相対パスを返す）。"""

    name: str
    path: str


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
    # library アクション: ジョブのどの出力を取っておくか（SPEC §7.2）
    source: str | None = None
    # library / library_search アクション: 付けるタグ / 絞り込み条件
    tags: list[str] = Field(default_factory=list)
    query: str = ""
    tag: str | None = None
    #: 検索対象の素材種別（'image' / 'video' / 'audio'。None は全種別）
    library_kind: str | None = None
    #: 検索結果の読み出し位置（ページング）
    offset: int = 0
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
    # 実行中の活動テキスト（None = この通知では変化なし / ターン終了）
    activity: str | None = None


class WorkflowSelect(BaseModel):
    """One selectable field of a workflow (SPEC §3.1 / §8)。フォームが select を描く。"""

    #: 論理名（ジョブの `selects` のキー）
    name: str
    label: str
    choices: list[str] = Field(default_factory=list)
    #: 未指定のときに使われる値
    default: str = ""
    #: True なら「自動」を選べる（未指定で入力から決まる。wan_dancer の尺）
    auto: bool = False
    hint: str = ""


class WorkflowOption(BaseModel):
    """One selectable workflow template (SPEC §3 / §8)."""

    id: str
    label: str
    kind: Literal["image", "video", "audio"]
    #: model family — image LoRAs of another family cannot be used with it
    family: str = DEFAULT_FAMILY
    notes: str = ""
    #: logical inputs the workflow needs: image / audio / end_image / video
    requires: list[str] = Field(default_factory=list)
    #: logical knobs the workflow exposes (prompt, negative, duration, fps, …)
    supports: list[str] = Field(default_factory=list)
    #: can it be the second stage of a full (image -> video) job?
    accepts_start_image: bool = False
    #: UI label of the primary image input
    image_label: str = "開始フレーム"
    #: 選択式フィールド（無いワークフローでは空）
    selects: list[WorkflowSelect] = Field(default_factory=list)
    #: `video_prompt` が必須か（False ならプロンプト欄は任意扱い）
    prompt_required: bool = True
    #: 動画用 LoRA を挿せるか（テンプレートに LoRA チェーンがあるか）
    accepts_video_loras: bool = False
    #: audio workflows: the clip length the model supports, in seconds
    min_duration: float = 0.0
    max_duration: float = 0.0
    default_duration: float = 0.0


class Options(BaseModel):
    """Choices for the generation form (SPEC §9 GET /api/options)."""

    model_config = ConfigDict(protected_namespaces=())

    comfy_connected: bool = False
    comfy_error: str | None = None
    comfy_url: str = ""
    image_workflows: list[WorkflowOption] = Field(default_factory=list)
    video_workflows: list[WorkflowOption] = Field(default_factory=list)
    audio_workflows: list[WorkflowOption] = Field(default_factory=list)
    default_video_workflow: str = DEFAULT_VIDEO_WORKFLOW
    default_image_workflow: str = DEFAULT_IMAGE_WORKFLOW
    default_audio_workflow: str = DEFAULT_AUDIO_WORKFLOW
    #: the ACE-Step / Stable Audio COMBO choices, for the 音声 form
    audio_categories: list[str] = Field(default_factory=lambda: list(AUDIO_CATEGORIES))
    keyscales: list[str] = Field(default_factory=lambda: list(KEYSCALES))
    languages: list[str] = Field(default_factory=lambda: list(LANGUAGES))
    aspect_ratios: list[str] = Field(default_factory=list)
    lora_files: list[str] = Field(default_factory=list)
    #: 実行時に切り替えられるモデルスロット（候補が 2 件以上あるものだけ、§3.3）
    model_slots: list[ModelSlot] = Field(default_factory=list)
    #: ComfyUI が持つモデルファイル一覧。キーは `"<class_type>.<field>"`
    #: （設定ページの候補入力の datalist 補完用。取得できなかったものは入らない）
    model_files: dict[str, list[str]] = Field(default_factory=dict)
    loras: list[Lora] = Field(default_factory=list)
    audio_assets: list["Asset"] = Field(default_factory=list)
    image_assets: list["Asset"] = Field(default_factory=list)
    video_assets: list["Asset"] = Field(default_factory=list)
    #: ライブラリの全件（新しい順）。入力欄の「ライブラリから選択」と、
    #: エージェントの CHOICES がここを読む（SPEC §7.2）。
    library: list["LibraryItem"] = Field(default_factory=list)
    negative_presets: dict[str, str] = Field(default_factory=dict)
