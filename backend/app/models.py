import re
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
    MULTI_INPUT_EXTS,
    MULTI_INPUT_FIELDS,
    MULTI_INPUT_LABELS,
    WorkflowSpec,
    WorkflowSpecError,
    get_audio_spec,
    get_image_spec,
    get_video_spec,
)

#: ``audio`` is a stand-alone mode: it runs one audio graph and is never
#: chained with the image / video stages (which is why every ``mode in (...)``
#: test below simply does not list it).
#:
#: ``veo_extend`` / ``veo_1080p`` は**フォームから選ぶモードではない**（SPEC §2）:
#: 生成済みの Veo ジョブに対して履歴から掛ける追加操作で、kie.ai に残っている
#: 元タスクの ``taskId`` を使う。ジョブ 1 本として履歴・進捗・ライブラリに乗せる
#: ためにモードとして持つが、生成フォームの選択肢にもエージェントの計画にも
#: 出さない（:data:`FOLLOWUP_MODES`）。
JobMode = Literal[
    "full", "i2v", "image_only", "audio", "veo_extend", "veo_1080p"
]
JobStatus = Literal["queued", "prompting", "running", "done", "failed", "canceled"]

#: 生成済みジョブへの追加操作のモード（`veo_extend` = +7 秒の延長 /
#: `veo_1080p` = 1080P 版の取得）。どちらも新しく生成し直すのではなく、
#: kie.ai 側に残っている元タスクに追加の仕事を頼む（SPEC §5.2 / issue #26）。
FOLLOWUP_MODES: tuple[str, ...] = ("veo_extend", "veo_1080p")

#: 追加操作を掛けられるモデルファミリー（今は Veo だけ）
FOLLOWUP_FAMILY = "veo"

#: ComfyUI の接続先プロファイル（SPEC §5）。設定には 3 つ分の接続情報を持ち、
#: ``Settings.comfy_target`` が「今どれを使うか」を決める。生成フォームの
#: プルダウンはこの値だけを書き換える。
ComfyTarget = Literal["local", "runpod", "comfy_cloud"]

#: ComfyCloud のエンドポイント（固定。設定項目にはしない）。ホストが
#: ``comfy.org`` なので :func:`app.comfy._api_prefix` が Cloud 互換モードに入る。
COMFY_CLOUD_URL = "https://cloud.comfy.org"


class Settings(BaseModel):
    # `model_overrides` would otherwise collide with pydantic's `model_` namespace.
    model_config = ConfigDict(protected_namespaces=())

    # --- ComfyUI の接続先（SPEC §5）-------------------------------------
    #: 現在の接続先。旧レイアウト（単一の `comfy_url` / `comfy_api_key`）からの
    #: 移行は `app.config.load_settings` が読み込み時に行う。
    comfy_target: ComfyTarget = "local"
    #: ローカル（同じマシン / LAN）の ComfyUI。API キーは使わない。
    local_comfy_url: str = "http://127.0.0.1:8188"
    #: RunPod の Pod 上の ComfyUI（Cloudflare Tunnel の固定ホスト名）。
    #: 自動起動の設定は下の `runpod_*` 群。
    runpod_comfy_url: str = ""
    #: Pod の ComfyUI を認証付きで公開している場合のキー（不要なら空のまま）
    runpod_comfy_api_key: str = ""
    #: ComfyCloud の API キー（URL は `COMFY_CLOUD_URL` 固定なので設定に持たない）
    comfy_cloud_api_key: str = ""
    #: kie.ai（外部生成バックエンド、SPEC §5.2）の API キー。環境変数
    #: ``KIE_API_KEY`` が設定されていればそちらが優先される。空のあいだは
    #: kie 系ワークフローが選択肢に出ない。
    kie_api_key: str = ""
    grok_command: str = "grok"
    grok_model: str = "grok-4.5"
    grok_workdir: str = ""
    #: Grok Build CLI をメディア生成に使うときの作業ディレクトリ（SPEC §5.2）。
    #: 空なら `runtime/grok-media-workdir`。プロンプト用の `grok_workdir` とは
    #: 分ける（CLI が `.grok/generated-media/` に書き散らすため）。
    grok_media_workdir: str = ""
    #: 1 枚（1 本）の生成に許す秒数。エージェントが画像生成ツールを回して
    #: ファイルを保存し終えるまでなので、チャットより長めに取る。
    grok_media_timeout: float = 300.0
    #: Codex CLI（ChatGPT サブスク枠の画像生成、SPEC §5.4）のコマンド名。
    #: 認証は `codex login` が `~/.codex/auth.json` に書くので、設定に持つのは
    #: コマンド名と制限時間だけ（API キーは使わない）。
    codex_command: str = "codex"
    #: 1 枚の生成に許す秒数。`codex exec` はスキル呼び出し・画像生成・コピーまで
    #: を 1 ターンで回すので、チャットより長めに取る。
    codex_timeout: float = 300.0
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
    # モデルの指定は**接続先ごと**に持つ（SPEC §3.3 / §5）: どのファイルが在るかは
    # ComfyUI の環境ごとに違うので、ローカルで使うファイル名を Pod や ComfyCloud に
    # 押し付けても意味がない。キーは接続先、値は従来と同じ形。
    # {"local": {"<workflow_id>/<node_id>.<field>": "file.safetensors"}, …} で、
    # ワークフローのテンプレート既定と違うものだけを保存する。旧レイアウト（接続先の
    # 無い 1 組だけ）は読み込み時に 3 環境へ複製される（app.config._migrated）。
    model_overrides: dict[ComfyTarget, dict[str, str]] = Field(default_factory=dict)
    # 同じキー形式で「そのスロットで選べるモデルファイル名」を接続先ごとに持つ
    # （SPEC §3.3）。2 件以上あるスロットは生成フォーム / エージェントが実行時に
    # 選べるようになる。候補が空のキーは保存しない。
    model_choices: dict[ComfyTarget, dict[str, list[str]]] = Field(
        default_factory=dict
    )
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
    #: {"<ダウンロード URL>": "<配布ページ URL>"} — エージェントに渡す「調べに行ける
    #: ページ」の解決結果キャッシュ（app.model_sources）。Civitai は versionId から
    #: modelId を API で引かないとページが分からないので、一度引いた結果を残す。
    #: 設定画面からは触らない（`SettingsUpdate` に無い）自動生成のキャッシュ。
    model_page_urls: dict[str, str] = Field(default_factory=dict)
    # RunPod の Pod で ComfyUI を動かす場合の自動起動（SPEC §5.1）。接続先が
    # `runpod` で、かつ有効なときだけ、ジョブ投入の直前に `runpod_comfy_url` の
    # 疎通を確かめ、落ちていれば Pod を立ち上げる。
    #: 自動起動を使うか（false ならジョブは `runpod_comfy_url` に直接投げる）
    runpod_enabled: bool = False
    #: RunPod の REST API キー（https://rest.runpod.io/v1 に Bearer で送る）
    runpod_api_key: str = ""
    #: 起動する Pod テンプレートの ID（deploy/runpod/ のイメージを登録したもの）
    runpod_template_id: str = ""
    #: 確保する GPU の種類（RunPod の gpuTypeId。例 "NVIDIA RTX PRO 6000 Blackwell Workstation Edition"）
    runpod_gpu_type: str = "NVIDIA RTX PRO 6000 Blackwell Workstation Edition"
    #: /workspace にマウントする Network Volume の ID（ComfyUI 本体とモデルの置き場）
    runpod_network_volume_id: str = ""

    def active_comfy_url(self) -> str:
        """いま使う ComfyUI の URL（`comfy_target` のプロファイルのもの）。"""
        if self.comfy_target == "comfy_cloud":
            return COMFY_CLOUD_URL
        if self.comfy_target == "runpod":
            return self.runpod_comfy_url
        return self.local_comfy_url

    def active_comfy_api_key(self) -> str:
        """いま使う API キー（ローカルは常にキー無し）。"""
        if self.comfy_target == "comfy_cloud":
            return self.comfy_cloud_api_key
        if self.comfy_target == "runpod":
            return self.runpod_comfy_api_key
        return ""

    def overrides_for(self, target: "ComfyTarget | None" = None) -> dict[str, str]:
        """その接続先のモデル指定（省略すると現在の接続先、SPEC §3.3）。"""
        return dict(self.model_overrides.get(target or self.comfy_target) or {})

    def choices_for(
        self, target: "ComfyTarget | None" = None
    ) -> dict[str, list[str]]:
        """その接続先の候補リスト（省略すると現在の接続先）。"""
        stored = self.model_choices.get(target or self.comfy_target) or {}
        return {key: list(names) for key, names in stored.items()}


class SettingsUpdate(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    comfy_target: ComfyTarget | None = None
    local_comfy_url: str | None = None
    runpod_comfy_url: str | None = None
    runpod_comfy_api_key: str | None = None
    comfy_cloud_api_key: str | None = None
    kie_api_key: str | None = None
    grok_command: str | None = None
    grok_model: str | None = None
    grok_workdir: str | None = None
    grok_media_workdir: str | None = None
    grok_media_timeout: float | None = None
    codex_command: str | None = None
    codex_timeout: float | None = None
    model_overrides: dict[ComfyTarget, dict[str, str]] | None = None
    model_choices: dict[ComfyTarget, dict[str, list[str]]] | None = None
    hf_token: str | None = None
    civitai_api_key: str | None = None
    model_download_urls: dict[str, str] | None = None
    runpod_enabled: bool | None = None
    runpod_api_key: str | None = None
    runpod_template_id: str | None = None
    runpod_gpu_type: str | None = None
    runpod_network_volume_id: str | None = None
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

    ``target`` は書き込む接続先（省略すると現在の接続先）。設定ページの環境
    プルダウンで選んだ環境の指定だけを差し替え、他の環境はそのまま残る。
    """

    overrides: dict[str, str] = Field(default_factory=dict)
    choices: dict[str, list[str]] | None = None
    target: ComfyTarget | None = None


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

    保存先は環境変数 ``COMFY_MODELS_DIR`` だけで決まる（**ローカル接続のときだけ
    使う**: RunPod では Pod 側の models ディレクトリに落とすので関係ない）。
    UI はこの状態でダウンロード欄を隠したりはせず、押されたときに 400 の理由を
    出す。設定されているのに書けない場合（Docker でホストの models ディレクトリを
    同じ絶対パスにマウントしていない等）は ``exists`` / ``writable`` が false。
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

    ``target`` は落とす先の環境（省略すると現在の接続先）。``local`` はこのアプリが
    自分で落とし、``runpod`` は Pod のダウンロード API に依頼する（SPEC §3.3）。
    ``comfy_cloud`` はファイルシステムに触れないので 400 になる。
    """

    filename: str
    url: str
    subfolder: str = ""
    target: ComfyTarget | None = None


class ModelDownloadAllRequest(BaseModel):
    """``POST /api/models/download-all`` のボディ（不足モデルの一括取得）。

    「未検出（ComfyUI のファイル一覧に無い）かつ取得元 URL が登録済み」のものを
    まとめて開始する。すでに走っているものは飛ばす。
    """

    target: ComfyTarget | None = None


class ModelDownloadAllResult(BaseModel):
    """``POST /api/models/download-all`` のレスポンス。"""

    #: 開始したダウンロード（進捗は WS の ``model_download`` で流れる）
    started: list["ModelDownload"] = Field(default_factory=list)
    #: 未検出だが取得元 URL が無くて開始できなかったファイル名
    missing_urls: list[str] = Field(default_factory=list)
    #: 開始できなかった理由（ファイル名 -> メッセージ）
    errors: dict[str, str] = Field(default_factory=dict)


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


class ModelSource(BaseModel):
    """1 ファイルの取得元（エージェントに渡す「調べに行けるページ」、AGENT-MODE §3.1）。

    ``model_download_urls`` に登録されたダウンロード URL を、そのまま渡しても
    調べ物には使えないので、可能なら配布ページ（Hugging Face のリポジトリページ /
    Civitai のモデルページ）へ変換したものを ``page_url`` に入れる。変換できな
    かったときは空で、エージェントにはダウンロード URL だけが見える。
    """

    filename: str
    #: 'lora' = LoRA レジストリの行 / 'model' = ワークフローのモデルスロット
    kind: Literal["lora", "model"] = "model"
    #: 表示名（LoRA の display_name。モデルファイルでは空）
    label: str = ""
    #: 何に使われているか（LoRA なら「画像用（family krea2）」、モデルなら
    #: 「<workflow_id>: <ノード名>」。プロンプトにそのまま出す）
    usage: list[str] = Field(default_factory=list)
    #: 設定に登録されているダウンロード URL
    download_url: str = ""
    #: 使い方を調べに行ける配布ページ（解決できなければ空）
    page_url: str = ""
    #: 'huggingface' / 'civitai' / ''（それ以外・不明）
    host: str = ""


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
    # どの接続先環境に置いてある LoRA か（SPEC §5）。``None`` は「環境を問わず出す」
    # で、接続先を分ける前に登録された行がこれになる。生成フォームには
    # 「現在の接続先のもの + 共通（None）」だけが出る。
    comfy_target: ComfyTarget | None = None
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
    #: 置いてある接続先環境（``None`` = 全環境で出す）
    comfy_target: ComfyTarget | None = None


class LoraUpdate(BaseModel):
    display_name: str | None = None
    lora_name: str | None = None
    trigger_word: str | None = None
    default_strength: float | None = None
    default_audio: str | None = None
    sort_order: int | None = None
    target: LoraTarget | None = None
    family: str | None = None
    comfy_target: ComfyTarget | None = None


# Default of the LTX 2.3 "dev" templates (t2v / i2v / ia2v / id_lora).  An empty
# negative means "keep whatever the selected template ships with" (SPEC §3.1).
DEFAULT_NEGATIVE_PROMPT = "pc game, console game, video game, cartoon, childish, ugly"


class LoraRef(BaseModel):
    """One LoRA selected for a job (snapshot of the registry entry)."""

    lora_name: str
    trigger_word: str = ""
    strength: float = 1.0


class MultiShot(BaseModel):
    """ショット割りの 1 ショット（SPEC §3.1、`WorkflowSpec.multi_shot`）。

    ジョブの params は平坦な値が中心だが、ショットは「文と秒数の組が順番に
    並ぶ」ものなので、JSON 文字列ではなく**型付きのリスト**で持つ（そのまま
    ``multi_prompt`` の要素になる）。
    """

    #: そのショットの本文（1 ショットもモデルの文字数上限に収まること）
    prompt: str = ""
    #: そのショットの尺（秒、**整数**）
    duration: int = 5


class ElementInput(BaseModel):
    """Elements の 1 要素（SPEC §3.1、`WorkflowSpec.elements`）。

    :attr:`images` はローカル素材のパス / URL で、投入時に 1 枚ずつ File Upload
    API に上がって ``element_input_urls`` になる（:func:`app.jobs._kie_uploads`）。
    プロンプト本文からは ``@要素名`` で呼ぶ。
    """

    #: プロンプト中の ``@要素名`` で使う名前
    name: str = ""
    #: 何を固定したいのかの説明（モデルに渡る）
    description: str = ""
    #: 参照画像（枚数の範囲はワークフローの宣言による）
    images: list[str] = Field(default_factory=list)


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

    #: ショット割り（SPEC §3.1）。空でなければ ``video_prompt`` の代わりに
    #: これがそのまま ``multi_prompt`` になり、トップレベルの本文は送られない。
    multi_shots: list[MultiShot] = Field(default_factory=list)

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
    #: Suno: styles to keep out of the track (`negativeTags`). Nothing to do
    #: with the image / video `negative_prompt` — this one is a comma separated
    #: list of *sounds*, and only Suno reads it.
    negative_tags: str = ""
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
    #: outputs that do not fit the columns above, in the order the backend
    #: produced them.  One generation can return several takes (Suno answers
    #: every request with **two songs**); the first one goes in the column for
    #: its stage and the rest live here (SPEC §6).
    extra_outputs: list[str] = Field(default_factory=list)
    error: str | None = None
    #: 外部バックエンド（kie.ai）のジョブが消費したクレジット。ComfyUI のジョブと
    #: 失敗したジョブ（kie は自動返金）では None のまま（SPEC §5.2）。
    credits_consumed: float | None = None

    # NSFW フラグ: nsfw_source は '' = 未判定 / 'auto' / 'manual'
    nsfw: bool = False
    nsfw_source: str = ""

    # convenience for the SPA (derived from the paths above, see jobs._row_to_job)
    image_url: str | None = None
    video_url: str | None = None
    last_frame_url: str | None = None
    audio_output_url: str | None = None
    #: URLs of :attr:`extra_outputs`, in the same order
    extra_output_urls: list[str] = Field(default_factory=list)
    #: このジョブの成果物に**追加で掛けられる kie.ai の操作**（SPEC §5.2）。
    #: :data:`FOLLOWUP_MODES` の部分集合で、履歴の UI はここを見てボタンを
    #: 出す（判定は :func:`app.jobs.job_followups`）。空 = 何もできない。
    followups: list[str] = Field(default_factory=list)


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
    multi_shots: list[dict[str, Any]] | None = None,
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
    # ショット割り（`multi_shots`）を渡したジョブでは本文がショット側にあるので、
    # トップレベルの `video_prompt` は要らない（API にも送らない）。
    if (
        mode in ("full", "i2v")
        and not (video_prompt or "").strip()
        and not (multi_shots or [])
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
    *,
    audio_workflow: str | None = None,
    image_workflow: str | None = None,
) -> str | None:
    """選択式フィールドの指定が使えるか（None == 問題なし、SPEC §3.1）。

    宣言のない名前と、選択肢に無い値を拒否する。見るのは**そのモードで実際に
    走るワークフロー**の宣言で、``audio`` なら音声ワークフロー（Suno の
    `model` / `vocal_gender`）、それ以外は画像ステージと動画ステージのうち走る
    ほう（``full`` は両方）。``selects`` はステージをまたいで 1 つの辞書なので、
    **どちらかのステージが宣言していれば通す**（gpt-image-2 の `size` /
    `quality` は画像ステージ側の宣言、SPEC §5.4）。
    """
    if not selects:
        return None
    if not isinstance(selects, dict):
        return "selects は {\"<名前>\": \"<選んだ値>\"} 形式のオブジェクトで指定してください"
    if mode not in ("full", "i2v", "image_only", "audio"):
        return f"mode '{mode}' は selects を持つステージを走らせません"
    # (ジョブの入力フィールド名, マニフェスト) を実行順に
    stages: list[tuple[str, WorkflowSpec]] = []
    try:
        if mode == "audio":
            stages.append(("audio_workflow", get_audio_spec(audio_workflow)))
        else:
            if mode in ("full", "image_only"):
                stages.append(("image_workflow", get_image_spec(image_workflow)))
            if mode in ("full", "i2v"):
                stages.append(("video_workflow", get_video_spec(video_workflow)))
    except WorkflowSpecError as exc:
        return str(exc)
    for name, value in selects.items():
        select = next(
            (
                found
                for _, spec in stages
                if (found := spec.select(str(name))) is not None
            ),
            None,
        )
        if select is None:
            known = ", ".join(
                f"`{key}`" for _, spec in stages for key in spec.selects
            ) or "なし"
            where = " / ".join(f"{field} `{spec.id}`" for field, spec in stages)
            return f"{where} に選択項目 `{name}` はありません（使えるのは {known}）"
        if str(value) not in select.choices:
            return (
                f"`{name}` に {value!r} は使えません（使えるのは "
                + ", ".join(f"{choice!r}" for choice in select.choices)
                + "）"
            )
    return select_requires_problem(stages, selects)


def select_requires_problem(
    stages: list[tuple[str, WorkflowSpec]], selects: dict[str, Any]
) -> str | None:
    """選択式どうしの相関（:attr:`app.workflows.WorkflowSpec.select_requires`）。

    「その項目は相手がこの値のときしか効かない」という宣言を投入前に見る。
    **既定のままなら何も言わない**（指定していないのだから無視されても困らない）。
    Suno の ``duration`` は ``model`` が ``V5_5`` のときしか効かず、他のモデルでは
    **API が黙って無視する**ので、気づかないまま違う長さの曲を待つより 422 で
    断るほうが親切、という判断。
    """
    for _, spec in stages:
        for name, (other, needed) in spec.select_requires.items():
            select = spec.select(name)
            if select is None:
                continue
            value = str(selects.get(name) or "").strip()
            if not value or value == select.fallback:
                continue  # 指定していない（＝既定のまま）ものは相関を見ない
            partner = spec.select(other)
            chosen = str(selects.get(other) or "").strip() or (
                partner.fallback if partner is not None else ""
            )
            if chosen == needed:
                continue
            other_label = partner.label if partner is not None else other
            return (
                f"`{name}`（{select.label}）は{other_label}（`{other}`）が"
                f" {needed!r} のときだけ効きます（今は {chosen!r}）。"
                f"`{other}` を {needed!r} にするか、`{name}` を"
                f" {select.fallback!r} に戻してください"
            )
    return None


#: プロンプト中の Elements 参照（``@要素名``）。名前に使えるのは英数字・
#: アンダースコア・ハイフンと（``\w`` が拾う）日本語などの文字。
ELEMENT_REFERENCE = re.compile(r"@([\w-]+)")


def element_references(text: str | None) -> list[str]:
    """プロンプト本文が呼んでいる ``@要素名``（出てきた順、重複そのまま）。"""
    return ELEMENT_REFERENCE.findall(text or "")


def prompt_chars(text: str | None, reference_chars: int = 0) -> int:
    """API がそのプロンプトを何文字と数えるか（SPEC §3.1）。

    Elements を持つモデル（Kling）では **``@要素名`` 1 回が
    :attr:`app.workflows.ElementsSpec.reference_chars` 文字**として上限を消費する
    ので、見た目の長さのままでは 500 文字の判定が合わない。
    ``reference_chars == 0``（Elements 非対応）なら単純な文字数。
    """
    if not text:
        return 0
    if reference_chars <= 0:
        return len(text)
    return len(text) + sum(
        reference_chars - len(match.group(0))
        for match in ELEMENT_REFERENCE.finditer(text)
    )


def _length_problem(spec: WorkflowSpec, where: str, text: str | None) -> str | None:
    """1 本のプロンプトが上限に収まるか（``@要素名`` の補正込み）。"""
    limit = spec.max_prompt_chars
    if not limit or not text:
        return None
    cost = spec.elements.reference_chars if spec.elements else 0
    counted = prompt_chars(text, cost)
    if counted <= limit:
        return None
    note = ""
    if cost and counted != len(text):
        note = f"、`@要素名` 1 つを {cost} 文字として数えます"
    return (
        f"video workflow '{spec.id}' は {where} を {limit} 文字までしか"
        f"受け取れません（今は {counted} 文字{note}）"
    )


def prompt_length_problem(
    mode: str, video_workflow: str | None, video_prompt: str | None
) -> str | None:
    """``video_prompt`` がそのモデルの長さの上限に収まるか（None == 問題なし）。

    外部 API には**プロンプトの文字数制限**があるものがあり（Kling 3.0 は 500
    文字）、超えたリクエストは 422 で弾かれる。走らせてから失敗させると
    クレジットこそ減らないが待ち時間が無駄になるので、投入前にここで落とす。
    上限を宣言していないワークフロー（``max_prompt_chars == 0``）は素通し。

    Elements を持つモデルでは ``@要素名`` の消費文字数を補正して数える
    （:func:`prompt_chars`）。
    """
    if mode not in ("full", "i2v") or not video_prompt:
        return None
    try:
        spec = get_video_spec(video_workflow)
    except WorkflowSpecError as exc:
        return str(exc)
    return _length_problem(spec, "video_prompt", video_prompt)


def multi_shots_of(params: Any) -> list[dict[str, Any]]:
    """``multi_shots`` を ``[{"prompt", "duration"}]`` に正規化する。

    ``params`` は :class:`JobCreate` でもジョブの ``params`` 辞書でもよい
    （前者は :class:`MultiShot` のリスト、後者は同じ形の素の辞書で入っている）。
    """
    raw = params.get("multi_shots") if isinstance(params, dict) else getattr(
        params, "multi_shots", None
    )
    if not isinstance(raw, (list, tuple)):
        return []
    shots: list[dict[str, Any]] = []
    for item in raw:
        if isinstance(item, MultiShot):
            shots.append(item.model_dump())
        elif isinstance(item, dict):
            shots.append(dict(item))
        else:
            shots.append({"prompt": str(item), "duration": 0})
    return shots


def multi_shot_problem(
    mode: str, video_workflow: str | None, shots: list[dict[str, Any]]
) -> str | None:
    """ショット割りの指定が使えるか（None == 問題なし、SPEC §3.1）。

    宣言のないワークフローに渡す・件数超過・1 ショットの尺が範囲外・1 ショットの
    本文が長すぎる、のいずれも API 側では 422 になるので、投入前にここで落とす。
    ``multi_shots`` があるときトップレベルの ``video_prompt`` は送られない
    （:func:`app.kie.task_values`）ので、空でも「必須項目が無い」とは言わない
    （:func:`missing_job_fields`）。
    """
    if not shots:
        return None
    if mode not in ("full", "i2v"):
        return f"mode '{mode}' は動画ステージを走らせないので、`multi_shots` は使えません"
    try:
        spec = get_video_spec(video_workflow)
    except WorkflowSpecError as exc:
        return str(exc)
    declared = spec.multi_shot
    if declared is None:
        return (
            f"video workflow '{spec.id}' はマルチショット（`multi_shots`）に"
            "対応していません"
        )
    if len(shots) > declared.max_shots:
        return (
            f"video workflow '{spec.id}' のマルチショットは"
            f" {declared.max_shots} ショットまでです（今は {len(shots)} ショット）"
        )
    for index, shot in enumerate(shots, start=1):
        text = str(shot.get("prompt") or "").strip()
        if not text:
            return f"`multi_shots` の {index} ショット目に prompt がありません"
        length = _length_problem(spec, f"multi_shots[{index - 1}].prompt", text)
        if length:
            return length
        raw = shot.get("duration")
        try:
            seconds = int(raw)
        except (TypeError, ValueError):
            return (
                f"`multi_shots` の {index} ショット目の duration は整数の秒数です"
                f"（今は {raw!r}）"
            )
        if not declared.min_duration <= seconds <= declared.max_duration:
            return (
                f"`multi_shots` の {index} ショット目の duration は"
                f" {declared.min_duration}〜{declared.max_duration} 秒です"
                f"（今は {seconds} 秒）"
            )
    return None


def elements_of(params: Any) -> list[dict[str, Any]]:
    """``kling_elements`` を ``[{"name", "description", "images"}]`` に正規化する。"""
    raw = params.get("kling_elements") if isinstance(params, dict) else getattr(
        params, "kling_elements", None
    )
    if not isinstance(raw, (list, tuple)):
        return []
    elements: list[dict[str, Any]] = []
    for item in raw:
        if isinstance(item, ElementInput):
            elements.append(item.model_dump())
        elif isinstance(item, dict):
            elements.append(dict(item))
    return elements


def elements_problem(
    mode: str,
    video_workflow: str | None,
    elements: list[dict[str, Any]],
    *,
    video_prompt: str | None = None,
    shots: list[dict[str, Any]] | None = None,
) -> str | None:
    """Elements の指定とプロンプト中の ``@要素名`` が噛み合うか（SPEC §3.1）。

    - 宣言のないワークフローに渡す / 要素数・画像枚数が範囲外 / 名前が空・重複 /
      画像の拡張子が違う → 投入前に落とす（API 側の 422 と同じ理由）
    - **プロンプトが呼んでいる ``@要素名`` が宣言されていない**ときも落とす。
      未宣言の ``@`` はモデルに文字として渡り、しかも 37 文字を消費してしまう
      ので、黙って通すより気づかせるほうがよい。逆に**呼ばれていない要素**は
      素材を先に用意しただけかもしれないので何も言わない
    """
    prompts = [text for text in [video_prompt] if text]
    prompts += [str(shot.get("prompt") or "") for shot in (shots or [])]
    if not elements and not any(element_references(text) for text in prompts):
        return None
    if mode not in ("full", "i2v"):
        return (
            f"mode '{mode}' は動画ステージを走らせないので、`kling_elements` は"
            "使えません"
        )
    try:
        spec = get_video_spec(video_workflow)
    except WorkflowSpecError as exc:
        return str(exc)
    declared = spec.elements
    if declared is None:
        if not elements:
            return None  # Elements を持たないモデルの `@` はただの文字
        return (
            f"video workflow '{spec.id}' は Elements（`kling_elements`）に"
            "対応していません"
        )
    if len(elements) > declared.max_elements:
        return (
            f"video workflow '{spec.id}' の Elements は"
            f" {declared.max_elements} 要素までです（今は {len(elements)} 要素）"
        )
    allowed = MULTI_INPUT_EXTS["reference_images"]
    names: list[str] = []
    for index, element in enumerate(elements, start=1):
        name = str(element.get("name") or "").strip()
        if not name:
            return f"`kling_elements` の {index} 個目に name がありません"
        if element_references(f"@{name}") != [name]:
            return (
                f"要素名 '{name}' はプロンプト中で `@{name}` として書けません"
                "（英数字・アンダースコア・ハイフン・日本語のみ、空白は不可）"
            )
        if name in names:
            return f"要素名 '{name}' が重複しています（`@要素名` で区別できません）"
        names.append(name)
        images = [
            str(path).strip()
            for path in (element.get("images") or [])
            if str(path).strip()
        ]
        if not declared.min_images <= len(images) <= declared.max_images:
            return (
                f"要素 '{name}' の参照画像は"
                f" {declared.min_images}〜{declared.max_images} 枚です"
                f"（今は {len(images)} 枚）"
            )
        for path in images:
            suffix = path[path.rfind("."):].lower() if "." in path else ""
            if suffix not in allowed:
                return (
                    f"要素 '{name}' の参照画像に使えない拡張子です: {path}"
                    f"（{', '.join(sorted(allowed))} のいずれか）"
                )
    for text in prompts:
        for reference in element_references(text):
            if reference not in names:
                known = "・".join(f"@{name}" for name in names) or "なし"
                return (
                    f"プロンプトの `@{reference}` に対応する要素が"
                    f" `kling_elements` にありません（宣言済み: {known}）"
                )
    return None


def reference_materials(params: Any) -> dict[str, list[str]]:
    """``{論理名: パスのリスト}``（空のものは落とす）。

    ``params`` は :class:`JobCreate` でもジョブの ``params`` 辞書でもよい
    （どちらも :data:`app.workflows.MULTI_INPUT_FIELDS` の名前で持っている）。
    """
    def value_of(key: str) -> Any:
        if isinstance(params, dict):
            return params.get(key)
        return getattr(params, key, None)

    picked: dict[str, list[str]] = {}
    for name, field_name in MULTI_INPUT_FIELDS.items():
        raw = value_of(field_name)
        if not isinstance(raw, (list, tuple)):
            continue
        paths = [str(item).strip() for item in raw if str(item).strip()]
        if paths:
            picked[name] = paths
    return picked


def reference_problem(
    mode: str,
    video_workflow: str | None,
    references: dict[str, list[str]],
    *,
    source_image: str | None = None,
    end_image: str | None = None,
    selects: Any = None,
) -> str | None:
    """マルチモーダル参照が使える組み合わせか（None == 問題なし、SPEC §3.1）。

    Seedance 2 の参照モード（``reference_image_urls`` ほか）は、API 側で
    **先頭フレーム i2v（``first_frame_url`` / ``last_frame_url``）と相互排他**。
    走らせてから 422 を食らうと待ち時間が無駄なので、投入前にここで落とす。
    ``full`` は画像ステージが開始フレームを作る = 先頭フレームモードなので、
    参照素材とは組み合わせられない。

    件数の上限（:attr:`app.workflows.WorkflowSpec.multi_inputs`）と拡張子だけ
    ここで見る。サイズ・解像度・尺の細かい制約は外部 API の判断に任せ、失敗
    メッセージをそのまま見せる。

    参照モードでしか作れない設定がある場合
    （:attr:`~app.workflows.WorkflowSpec.reference_selects`、Veo の素材参照生成は
    8 秒固定）は ``selects`` の明示指定だけを見て断る: 未指定ならその選択式の
    既定がそのまま固定値と同じなので、黙って通してよい。
    """
    if not references:
        return None
    names = "・".join(f"`{MULTI_INPUT_FIELDS[name]}`" for name in references)
    if mode not in ("full", "i2v"):
        return (
            f"mode '{mode}' は動画ステージを走らせないので、参照素材"
            f"（{names}）は使えません"
        )
    try:
        spec = get_video_spec(video_workflow)
    except WorkflowSpecError as exc:
        return str(exc)
    for name, paths in references.items():
        limit = spec.multi_inputs.get(name)
        if limit is None:
            return (
                f"video workflow '{spec.id}' は{MULTI_INPUT_LABELS[name]}"
                f"（`{MULTI_INPUT_FIELDS[name]}`）を受け取れません"
            )
        if len(paths) > limit:
            return (
                f"video workflow '{spec.id}' の{MULTI_INPUT_LABELS[name]}は"
                f" {limit} 件までです"
                f"（今は {len(paths)} 件）"
            )
        allowed = MULTI_INPUT_EXTS.get(name, frozenset())
        for path in paths:
            suffix = path[path.rfind("."):].lower() if "." in path else ""
            if suffix not in allowed:
                return (
                    f"{MULTI_INPUT_LABELS[name]}に使えない拡張子です: {path}"
                    f"（{', '.join(sorted(allowed))} のいずれか）"
                )
    chosen = selects if isinstance(selects, dict) else {}
    for name, fixed in spec.reference_selects.items():
        value = str(chosen.get(name) or "").strip()
        if value and value != fixed:
            label = (select.label if (select := spec.select(name)) else name)
            return (
                f"video workflow '{spec.id}' の参照素材モードでは{label}"
                f"（`{name}`）は {fixed!r} 固定です（今は {value!r}）"
            )
    if mode == "full":
        return (
            f"mode 'full' は画像ステージが作った静止画を開始フレームにするので、"
            f"参照素材（{names}）とは同時に使えません"
            "（参照素材だけで作るなら mode を \"i2v\" にし、開始フレームから作るなら"
            "参照素材を外してください）"
        )
    given = [
        INPUT_FIELDS[name]
        for name, value in (("image", source_image), ("end_image", end_image))
        if (value or "").strip()
    ]
    if given:
        return (
            f"video workflow '{spec.id}' では先頭フレーム"
            f"（{', '.join(f'`{name}`' for name in given)}）と参照素材"
            f"（{names}）は同時に指定できません（API 側で排他のモードです）"
        )
    return None


def followup_problem(
    mode: str, video_workflow: str | None, source_task_id: str | None
) -> str | None:
    """追加操作のジョブが成り立つか（None == 問題なし、SPEC §5.2 / issue #26）。

    :data:`FOLLOWUP_MODES` のジョブは新しく生成するのではなく、**kie.ai 側に
    残っている元タスク**に仕事を足す。だから必要なのは「元ジョブの ``taskId``」と
    「そのモデルが追加操作を持っていること」の 2 つだけで、プロンプト・入力
    ファイル・選択式はどれも要らない（:func:`missing_job_fields` も素通しする）。
    """
    if mode not in FOLLOWUP_MODES:
        return None
    if not (source_task_id or "").strip():
        return (
            f"mode '{mode}' には元ジョブの kie.ai タスク ID が要ります"
            "（外部 API に投入した記録が残っているジョブからだけ実行できます）"
        )
    try:
        spec = get_video_spec(video_workflow)
    except WorkflowSpecError as exc:
        return str(exc)
    if spec.backend != "kie" or spec.family != FOLLOWUP_FAMILY:
        return (
            f"video workflow '{spec.id}' に mode '{mode}' の追加操作はありません"
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
    failing the job halfway through.  A workflow that declares no length at all
    (``max_duration == 0``, e.g. Suno, whose API has no length parameter) skips
    the range check: the model decides how long the track is.
    """
    if mode != "audio":
        return None
    try:
        spec = get_audio_spec(audio_workflow)
    except WorkflowSpecError as exc:
        return str(exc)
    if duration is not None and spec.max_duration > 0 and not (
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


def image_lora_problem(
    mode: str, image_workflow: str | None, loras: list[Any]
) -> str | None:
    """Why the selected image workflow cannot take ``loras`` at all (None == fine).

    外部バックエンド（Grok Build CLI など）のワークフローにはグラフが無く、LoRA を
    差し込む場所そのものが無い。指定を黙って捨てるのではなく 422 で断る（動画側の
    :func:`video_lora_problem` と同じ流儀、SPEC §3.4）。
    """
    if not loras or mode not in ("full", "image_only"):
        return None
    try:
        spec = get_image_spec(image_workflow)
    except WorkflowSpecError as exc:
        return str(exc)
    if spec.lora_chain is None:
        return f"image workflow '{spec.id}' does not support LoRAs"
    return None


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
    #: Suno: styles to keep out of the track (`negativeTags`)
    negative_tags: str = ""
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

    # マルチモーダル参照（SPEC §3.1）。1 つのフィールドが**複数ファイル**を持ち、
    # 外部 API には URL の配列で渡る。宣言しているワークフロー（Seedance 2 系）で
    # のみ使え、**先頭フレーム（`source_image` / `end_image`）とは排他**。
    #: 一貫性のよりどころにする参照画像（最大枚数はワークフロー宣言による）
    reference_images: list[str] = Field(default_factory=list)
    #: 動きのお手本にする参照動画
    reference_videos: list[str] = Field(default_factory=list)
    #: ムード・曲調のよりどころにする参照音声
    reference_audios: list[str] = Field(default_factory=list)

    #: ショット割り（SPEC §3.1）。宣言しているワークフロー（Kling 3.0）でのみ
    #: 使え、指定すると ``video_prompt`` の代わりにこれが本文になる。
    multi_shots: list[MultiShot] = Field(default_factory=list)
    #: Elements（``@要素名`` で呼ぶ参照画像の束、SPEC §3.1）
    kling_elements: list[ElementInput] = Field(default_factory=list)

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
            or prompt_length_problem(
                self.mode, self.video_workflow, self.video_prompt
            )
            or reference_problem(
                self.mode,
                self.video_workflow,
                reference_materials(self),
                source_image=self.source_image,
                end_image=self.end_image,
                selects=self.selects,
            )
            or multi_shot_problem(
                self.mode, self.video_workflow, multi_shots_of(self)
            )
            or elements_problem(
                self.mode,
                self.video_workflow,
                elements_of(self),
                video_prompt=self.video_prompt,
                shots=multi_shots_of(self),
            )
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
            multi_shots=multi_shots_of(self),
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


class VeoExtend(BaseModel):
    """``POST /api/jobs/{id}/veo/extend`` の body（SPEC §5.2 / issue #26）。

    元動画の**続き 7 秒**をどう作るかの指示だけを取る。元タスクの ``taskId`` は
    ジョブの ``workflow_json`` から引くので送らない。
    """

    #: 続きの指示（Veo の通常のプロンプトと同じ書き方）
    prompt: str = ""
    #: 再現性のためのシード（kie.ai の仕様どおり 10000〜99999）
    seeds: int | None = Field(default=None, ge=10000, le=99999)
    #: 焼き込む透かしのテキスト（省略 = 入れない）
    watermark: str | None = None


class VeoUpscale(BaseModel):
    """``POST /api/jobs/{id}/veo/1080p`` の body（SPEC §5.2 / issue #26）。"""

    #: 1 タスクが複数本返したときの何本目か（省略 = kie.ai の既定）
    index: int | None = Field(default=None, ge=0)


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
    #: Suno: styles to keep out of the track (`negativeTags`)
    negative_tags: str | None = None
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

#: 素材の出どころ（``LibraryItem.source``）。ジョブの出力 4 種に、アプリ内で
#: 合成したリファレンスシート（'sheet'、SPEC §7.2）を足したもの。from-job で
#: 指定できるのは :data:`LibrarySource` のほうだけ
LibraryOrigin = Literal["image", "last_frame", "video", "audio", "sheet"]

#: 素材の分類（棚の仕切り）。None は「未分類」で、DB では NULL。
#: 後段のキャラクターシート合成で character は大パネル、background / prop は
#: 小パネルに割り当てる（SPEC §7.2）。
LibraryCategory = Literal["character", "background", "prop"]


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
    #: 元ジョブのどの出力か（重複登録の判定に使う。アップロード・旧行は None）。
    #: 合成したリファレンスシートは 'sheet'（元ジョブを持たない）
    source: LibraryOrigin | None = None
    #: 分類タグ（検索・絞り込み用。順序は登録したまま）
    tags: list[str] = Field(default_factory=list)
    #: 素材の分類（None = 未分類。アップロード時に指定しなければ未分類）
    category: LibraryCategory | None = None


class LibraryFromJob(BaseModel):
    """POST /api/library/from-job body（生成物をライブラリに入れる）。"""

    job_id: str
    source: LibrarySource
    #: 表示名（空ならジョブのプロンプトから決める）
    name: str = ""
    tags: list[str] = Field(default_factory=list)
    #: 分類（省略・空・'none' なら未分類。不正な値は 400。:mod:`app.library` で検証）
    category: str | None = None


class LibraryUpdate(BaseModel):
    """PATCH /api/library/{id} body（指定した項目だけ変える）。"""

    name: str | None = None
    nsfw: bool | None = None
    tags: list[str] | None = None
    #: 分類。None（未送信）= 変えない / 'none'（または空文字）= 未分類に戻す /
    #: それ以外は :data:`LibraryCategory` の値（不正なら 400）。他の項目と同じく
    #: 「None = 変更なし」を守るため、未分類は値なしではなく明示の 'none' で送る
    category: str | None = None


class LibrarySheet(BaseModel):
    """POST /api/library/sheet body（素材を 1 枚のリファレンスシートに合成する）。

    ``item_ids`` の**並び順に意味がある**（左上から順に置く。SPEC §7.2）。枚数・
    大きさの検証は :mod:`app.sheets` が行い、外れていれば 400。
    """

    #: 載せる素材の id（すべて kind='image'。並べる順序）
    item_ids: list[str] = Field(default_factory=list)
    #: 表示名（空なら素材の名前から決める）
    name: str = ""
    #: シートの大きさ（省略すると 1280x720。出力動画と同じ縦横比が望ましい）
    width: int | None = None
    height: int | None = None


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
    #: 外部生成バックエンド kie.ai（キー未設定は not_configured、SPEC §5.2）
    kie: HealthStatus = Field(
        default_factory=lambda: HealthStatus(status="not_configured")
    )


class BackendInfo(BaseModel):
    """生成バックエンドの可用性（SPEC §5.2）。

    ``available`` が false のバックエンドのワークフローは選択肢に出ない。
    """

    backend: str
    status: Literal["ok", "not_configured", "error"]
    detail: str = ""
    available: bool = False


class KieCredits(BaseModel):
    """GET /api/kie/credits — 残クレジット照会（SPEC §5.2）。"""

    #: API キーがあるか（false なら kie 系ワークフローは選択肢に出ない）
    configured: bool = False
    #: 残クレジット（1 credit = $0.005）。取得できなければ None
    credits: float | None = None
    #: 照会に失敗した理由（成功時は None）
    error: str | None = None


# --------------------------------------------------------------------------
# agent mode (AGENT-MODE §4 / §5)
# --------------------------------------------------------------------------

AgentStatus = Literal[
    "idle", "planning", "running", "waiting_checkin", "stopped", "done"
]
AgentCheckinMode = Literal["every_job", "milestone", "auto"]
AgentActionName = Literal[
    "plan", "run_task", "continue", "rerun", "inspect", "note", "rename",
    "library", "library_search", "library_sheet", "checkin", "done",
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
    #: 素材の分類（'character' / 'background' / 'prop' / 'none' = 未分類）。
    #: library では登録時の分類、library_search では絞り込み条件。None は
    #: 「指定なし」= 未分類のまま登録する / 分類で絞らない（SPEC §7.2）
    category: str | None = None
    #: 検索結果の読み出し位置（ページング）
    offset: int = 0
    # library_sheet アクション: シートに載せる素材の id（並べる順）と大きさ
    item_ids: list[str] = Field(default_factory=list)
    width: int | None = None
    height: int | None = None
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


class MultiShotOption(BaseModel):
    """ショット割りの上限（`GET /api/options`、SPEC §3.1）。フォームの行数・
    秒数のバリデーションはこの値を見る。"""

    max_shots: int
    min_duration: int
    max_duration: int


class ElementsOption(BaseModel):
    """Elements の上限（`GET /api/options`、SPEC §3.1）。"""

    max_elements: int
    min_images: int
    max_images: int
    #: ``@要素名`` 1 参照が消費する文字数（フォームの残り文字数表示に使う）
    reference_chars: int


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
    #: 複数ファイルで渡せる参照入力（論理名 -> 件数の上限、SPEC §3.1）。宣言の
    #: ないワークフローでは空で、フォームは参照欄そのものを出さない。
    multi_inputs: dict[str, int] = Field(default_factory=dict)
    #: 参照素材を使うときに固定される選択式（名前 -> 値、SPEC §3.1）。Veo の
    #: 素材参照生成は 8 秒固定なので ``{"duration": "8"}``。フォームは参照素材が
    #: 選ばれている間だけこの値を要求する（バックエンドの 422 と同じ理由）。
    reference_selects: dict[str, str] = Field(default_factory=dict)
    #: 選択式どうしの相関（名前 -> `[相手の名前, 相手に必要な値]`、SPEC §3.1）。
    #: Suno の `duration` は `model` が `V5_5` のときだけ効くので
    #: `{"duration": ["model", "V5_5"]}`。フォームは既定以外を選んだときだけ
    #: その場でエラーを出す（バックエンドの 422 と同じ理由）。
    select_requires: dict[str, list[str]] = Field(default_factory=dict)
    #: ショット割りの宣言（対応していないワークフローでは None、SPEC §3.1）
    multi_shot: MultiShotOption | None = None
    #: Elements の宣言（対応していないワークフローでは None、SPEC §3.1）
    elements: ElementsOption | None = None
    #: プロンプトの文字数上限（0 = 上限なし）。フォームの残り文字数表示に使う。
    max_prompt_chars: int = 0
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
    #: 実行エンジン（``comfyui`` / ``kie``、SPEC §5.2）。UI のバッジ用。
    backend: str = "comfyui"
    #: audio workflows: the clip length the model supports, in seconds
    min_duration: float = 0.0
    max_duration: float = 0.0
    default_duration: float = 0.0


class Options(BaseModel):
    """Choices for the generation form (SPEC §9 GET /api/options)."""

    model_config = ConfigDict(protected_namespaces=())

    comfy_connected: bool = False
    comfy_error: str | None = None
    #: いま使っている接続先プロファイルと、その URL（表示用）
    comfy_target: ComfyTarget = "local"
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
    #: 生成バックエンドの可用性（SPEC §5.2）。使えないバックエンドのワークフローは
    #: 上のリストに載らないので、その理由をここで見せる。
    backends: list["BackendInfo"] = Field(default_factory=list)
    negative_presets: dict[str, str] = Field(default_factory=dict)
