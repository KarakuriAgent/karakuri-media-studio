import re
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .workflows import (
    AUDIO_CATEGORIES,
    DEFAULT_AUDIO_WORKFLOW,
    DEFAULT_FAMILY,
    DEFAULT_IMAGE_WORKFLOW,
    DEFAULT_MEGAPIXELS,
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
    input_label,
)

#: ``audio`` is a stand-alone mode: it runs one audio graph and is never
#: chained with the image / video stages (which is why every ``mode in (...)``
#: test below simply does not list it).
JobMode = Literal["full", "i2v", "image_only", "audio"]
JobStatus = Literal["queued", "prompting", "running", "done", "failed", "canceled"]

#: ComfyUI の接続先プロファイル（SPEC §5）。設定には 3 つ分の接続情報を持ち、
#: ``Settings.comfy_target`` が「今どれを使うか」を決める。生成フォームの
#: プルダウンはこの値だけを書き換える。
ComfyTarget = Literal["local", "runpod", "comfy_cloud"]
#: LLM を回すコーディング CLI（SPEC §4.1。app/llm_cli.py のアダプタと対応）
LlmCli = Literal["grok", "claude", "codex", "cursor"]

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
    # LLM を回すコーディング CLI（SPEC §4.1）。チャット・エージェント・スタジオ
    # 会話・キャンバス・英訳・自動タグ・ヘルスチェックがこの選択に従う。
    # **Grok Imagine（画像生成）だけは常に grok**（内蔵ツールに乗っているため）。
    agent_cli: LlmCli = "grok"
    #: CLI ごとのコマンド上書き（``{cli: コマンド}``。空 = アダプタの既定）。
    #: 値には引数を書いてよく、``"<cli>_oneshot"`` でワンショット側だけも指定できる。
    agent_cli_commands: dict[str, str] = Field(default_factory=dict)
    #: CLI ごとのモデル上書き（空 = CLI の既定に任せる）。grok は ``grok_model``。
    agent_cli_models: dict[str, str] = Field(default_factory=dict)
    grok_command: str = "grok"
    grok_model: str = "grok-4.5"
    grok_workdir: str = ""
    # Grok Imagine（画像生成・編集、SPEC §5.2）。コマンド名は上の `grok_command`
    # と共有し、作業ディレクトリと制限時間だけ専用に持つ。
    grok_media_workdir: str = ""
    grok_media_timeout: float = 300.0
    # Agent mode (AGENT-MODE §3.4): extra CLI flags (tool permissions) and the
    # longer timeout research / inspection turns need. `--permission-mode auto`
    # is confirmed on grok 0.2.112 to enable file read/write (incl. viewing
    # images) and web search in headless `-p` runs.
    agent_grok_args: list[str] = Field(
        default_factory=lambda: ["--permission-mode", "auto"]
    )
    # 実行上限はどれも **0 = 無制限**（既定値は従来どおりなので、無制限にしたい
    # 人だけが 0 を入れる）。
    #: grok CLI 1 回あたりの制限時間（秒）。0 = タイムアウトなし
    agent_grok_timeout: float = Field(default=300.0, ge=0)
    #: 自走セッションの「1 回のプラン提案で増やせる新規ジョブ数」。0 = 無制限
    agent_max_plan_tasks: int = Field(default=5, ge=0)
    #: スタジオのエージェントが人間の入力なしに回せる連続ターン数。0 = 無制限
    agent_max_turns: int = Field(default=20, ge=0)
    #: キャンバスのエージェントが 1 回の発言から回す連続ターン数。0 = 無制限
    canvas_max_turns: int = Field(default=8, ge=0)
    # エージェントのターンを ACP (`grok agent stdio`) で回すか。ACP だと実行中の
    # 活動（思考 / ツール実行）を UI に出せる。False なら従来のワンショット実行。
    agent_use_acp: bool = True
    # inspect の音声解析に文字起こし (STT) を足すか（AGENT-MODE §3.3）。推論は
    # このプロセスに抱えず、**OpenAI 互換の外部エンドポイント**へ投げる
    # （speaches / whisper.cpp server / OpenAI API など）。接続先が要るので既定は無効。
    agent_stt_enabled: bool = False
    #: 文字起こしサーバーのベース URL（例 `http://localhost:8000/v1`、
    #: `https://api.openai.com/v1`）。空なら STT はスキップする。Docker から
    #: ホストのサーバーを指すときは `host.docker.internal` を使う
    agent_stt_base_url: str = ""
    #: 使うモデル名（空 = サーバーの既定に任せる。OpenAI なら `whisper-1`）
    agent_stt_model: str = ""
    #: 文字起こしサーバーの API キー（ローカルサーバーなら空でよい）
    agent_stt_api_key: str = ""
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
    # 外部公開 API（docs/EXTERNAL-API.md）。既定は空 = `/api/v1` を丸ごと無効に
    # する（キーの設定が「有効化」そのもの）。
    #: `X-API-Key` と定数時間比較する共有キー（空 = 外部 API は 404）
    external_api_key: str = ""
    #: 外部 API から積める未完了 Take の上限（0 = 無制限）。バグったブリッジの
    #: 無限投入が GPU キューと課金を食い潰すのを防ぐ安全弁で、UI からの操作
    #: （内部 API）には掛からない
    external_max_pending_takes: int = 20

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
    agent_cli: LlmCli | None = None
    agent_cli_commands: dict[str, str] | None = None
    agent_cli_models: dict[str, str] | None = None
    grok_command: str | None = None
    grok_model: str | None = None
    grok_workdir: str | None = None
    grok_media_workdir: str | None = None
    grok_media_timeout: float | None = None
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
    agent_grok_timeout: float | None = Field(default=None, ge=0)
    agent_max_plan_tasks: int | None = Field(default=None, ge=0)
    agent_max_turns: int | None = Field(default=None, ge=0)
    canvas_max_turns: int | None = Field(default=None, ge=0)
    agent_use_acp: bool | None = None
    agent_stt_enabled: bool | None = None
    agent_stt_base_url: str | None = None
    agent_stt_model: str | None = None
    agent_stt_api_key: str | None = None
    external_api_key: str | None = None
    external_max_pending_takes: int | None = None


class ModelField(BaseModel):
    """One model-file input of one workflow template (SPEC §3.3)."""

    key: str  # f"{workflow_id}/{node_id}.{field}"
    workflow_id: str = ""
    workflow_label: str = ""
    #: which stage the owning workflow belongs to (settings page grouping)
    kind: Literal["image", "video", "audio"] = "image"
    #: モデルファミリー（``WorkflowSpec.family``）とその表示名。動画は 1 モデル =
    #: 複数ワークフローなので、設定ページはワークフローではなくこちらでまとめる。
    family: str = ""
    family_label: str = ""
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
#: 'video' は動画ワークフローに注入される。
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
    # target='video' の行では無視される（動画 LoRA はファミリーを持たない）。
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


# フォームの既定のネガティブ。空の negative は「選んだテンプレートの値をその
# まま使う」の意味（SPEC §3.1）。
DEFAULT_NEGATIVE_PROMPT = "pc game, console game, video game, cartoon, childish, ugly"

#: サンプリング回数（`steps`）の上限（SPEC §3.1）。0 は「未指定」= テンプレートの
#: 既定値のままで、それより大きい値だけがワークフローに注入される。実用上ここまで
#: 回すことはまず無いが、桁を間違えた指定で GPU を何時間も占有させないための蓋。
MAX_STEPS = 150


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
    参照画像のパスの並びで持つ。
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
    megapixels: float = DEFAULT_MEGAPIXELS
    # 参照画像（開始フレーム）の実寸 (w, h)。分かっている場合、動画側の幅・高さは
    # `aspect_ratio` プリセットではなくこの比から計算される（SPEC §3.1）。
    start_image_size: tuple[int, int] | None = None

    # 画像ワークフロー（Krea 2）に挿す LoRA
    loras: list[LoraRef] = Field(default_factory=list)
    trigger_text: str = ""  # already-concatenated / user-edited trigger words
    # 動画ワークフローに挿す LoRA。`video_trigger_text` が空なら
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
    #: サンプリング回数（SPEC §3.1）。``0`` = 未指定で、宣言のあるワークフロー
    #: でもテンプレートの既定値をそのまま使う（注入しない）。
    steps: int = 0

    # --- audio job knobs (mode 'audio' only, see workflow.build_audio_workflow)
    audio_prompt: str = ""
    #: the words to sing, with [Verse] / [Chorus] structure tags.
    #: Empty == instrumental.
    lyrics: str = ""
    #: styles to keep out of the track. Nothing to do
    #: with the image / video `negative_prompt` — this one is a comma separated
    #: list of *sounds*, and only models that declare it read it.
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
    #: 参照素材（複数、SPEC §3.1）。渡した順がそのまま ``ref_image_0`` /
    #: ``ref_video_0`` / ``ref_audio_0`` … の順で、プロンプトの ``<Picture 1>`` /
    #: ``<Video 1>`` / ``<Audio 1>`` … と対応する。参照動画のサウンドトラックは
    #: 動画から取り出して同じ番号に繋ぐので、別のリストは持たない。宣言している
    #: ワークフロー（:attr:`app.workflows.WorkflowSpec.ref_media`）だけが読む。
    reference_image_names: list[str] = Field(default_factory=list)
    reference_video_names: list[str] = Field(default_factory=list)
    reference_audio_names: list[str] = Field(default_factory=list)

    #: 直前カットの AV ラテントのパス（ラテント連続性、SPEC §3.1）。ComfyUI 側の
    #: ファイルなのでアップロードは通さず、文字列をそのままグラフに書く。
    context_latent_path: str = ""

    filename_prefix: str | None = None  # explicit override

    @property
    def video_filename_prefix(self) -> str:
        return self.filename_prefix or f"video/{self.job_id}"

    @property
    def latent_filename_prefix(self) -> str:
        """このジョブが保存する AV ラテントの ComfyUI 側での置き場所。

        ジョブごとに別の名前にして、再実行や別カットの保存とぶつからないように
        する（``clip_index`` は 0 のままなので実行ごとに連番が付く）。
        """
        return f"h3_context/{self.job_id}"

    @property
    def image_filename_prefix(self) -> str:
        return self.filename_prefix or f"images/{self.job_id}"

    @property
    def audio_filename_prefix(self) -> str:
        return self.filename_prefix or f"audio/{self.job_id}"


class Job(BaseModel):
    id: str
    created_at: str
    #: :data:`JobMode` の値。**過去に作られたジョブは今は無いモード**
    #: （廃止した外部バックエンドの追加操作など）を持っていることがあるので、
    #: 読み取り用のこのモデルだけは Literal で縛らない: 履歴の一覧・詳細が
    #: 古い行 1 つで丸ごと 500 になるのを防ぐ（投入側の :class:`JobCreate` は
    #: :data:`JobMode` のまま）。
    mode: str
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
    #: produced them.  The first one goes in the column for its stage and the
    #: rest live here (SPEC §6).
    extra_outputs: list[str] = Field(default_factory=list)
    error: str | None = None
    #: 外部バックエンドのジョブが消費したクレジット（過去の履歴のためだけに
    #: 残している列。ComfyUI のジョブでは常に None）。
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
    t2v needs no start frame while i2v needs one.  In ``full`` mode the
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
    # プロンプトを選択肢から組み立てるワークフローと、本文が
    # ショット側にあるワークフローでは video_prompt は
    # 任意（`prompt_required=False`）。前者は書かれた場合だけテンプレートに
    # 注入され、後者は書かれていたら `multi_shot_problem` が断る（SPEC §3.1）。
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
    *,
    audio_workflow: str | None = None,
    image_workflow: str | None = None,
) -> str | None:
    """選択式フィールドの指定が使えるか（None == 問題なし、SPEC §3.1）。

    宣言のない名前と、選択肢に無い値を拒否する。見るのは**そのモードで実際に
    走るワークフロー**の宣言で、``audio`` なら音声ワークフロー（その
    `model` / `vocal_gender`）、それ以外は画像ステージと動画ステージのうち走る
    ほう（``full`` は両方）。``selects`` はステージをまたいで 1 つの辞書なので、
    **どちらかのステージが宣言していれば通す**（画像側の `size` /
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
    たとえば ``duration`` が ``model`` の値でしか効かないとき、他の値では
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

    Elements を持つモデルでは **``@要素名`` 1 回が
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

    モデルによっては**プロンプトの文字数制限**があり（たとえば 500
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
    mode: str,
    video_workflow: str | None,
    shots: list[dict[str, Any]],
    *,
    video_prompt: str | None = None,
) -> str | None:
    """ショット割りの指定が使えるか（None == 問題なし、SPEC §3.1）。

    ショット割りは**専用のワークフロー**の機能なので、
    見るのは 2 方向:

    - 宣言のないワークフローに ``multi_shots`` を渡した → 断る
    - 宣言のあるワークフローで ``multi_shots`` が空 → 断る（そのワークフローは
      ショット割りでしか動かない）。逆にトップレベルの ``video_prompt`` は
      モデルに送られないので、書かれていたら
      「本文はショット側に書く」と教える

    件数超過・1 ショットの尺が範囲外・1 ショットの本文が長すぎる、のいずれも
    API 側では 422 になるので、投入前にここで落とす。
    """
    if mode not in ("full", "i2v"):
        if not shots:
            return None
        return f"mode '{mode}' は動画ステージを走らせないので、`multi_shots` は使えません"
    try:
        spec = get_video_spec(video_workflow)
    except WorkflowSpecError as exc:
        return str(exc)
    declared = spec.multi_shot
    if declared is None:
        if not shots:
            return None
        return (
            f"video workflow '{spec.id}' はマルチショット（`multi_shots`）に"
            "対応していません"
            "（ショット割り専用のワークフローを選んでください）"
        )
    if not shots:
        return (
            f"video workflow '{spec.id}' はショット割り専用です"
            "（`multi_shots` に 1 ショット以上を指定してください。1 カットで"
            "作るなら 1 カット版のワークフローを選んでください）"
        )
    if (video_prompt or "").strip():
        return (
            f"video workflow '{spec.id}' では本文はショット側に書きます"
            "（`video_prompt` は空のままにして、`multi_shots` の各 prompt に"
            "書いてください。トップレベルの prompt は API に送られません）"
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


#: 参照素材を受け取りうるステージ 1 つ ``(ジョブのフィールド名, ステージの日本語名,
#: マニフェスト)``。参照素材は画像ステージ（MiniMax H3 Image r2i）と動画ステージ
#: （MiniMax H3 r2v）の**どちらの**入力にもなりうるので、検証はステージの一覧に
#: 対して回す（``full`` では両方が並ぶ）。
_ReferenceStage = tuple[str, str, "WorkflowSpec"]


def _reference_stages(
    mode: str, video_workflow: str | None, image_workflow: str | None
) -> list[_ReferenceStage]:
    """``mode`` で実際に走る、参照素材を受け取りうるステージ（実行順）。

    ワークフロー id が壊れていれば :class:`WorkflowSpecError` を投げるので、
    呼び出し側がメッセージにする。
    """
    stages: list[_ReferenceStage] = []
    if mode in ("full", "image_only"):
        stages.append(
            ("image workflow", "画像ステージ", get_image_spec(image_workflow))
        )
    if mode in ("full", "i2v"):
        stages.append(
            ("video workflow", "動画ステージ", get_video_spec(video_workflow))
        )
    return stages


def _missing_reference_problem(
    mode: str, video_workflow: str | None, image_workflow: str | None = None
) -> str | None:
    """参照素材が 1 つも無いときに、それを必須にしているワークフローかを見る。

    宣言は :attr:`app.workflows.RefMediaFan.min_refs`（0 なら不問）で、**種類を
    問わない合計**なので画像・動画・音声のどれで満たしてもよい。ここは「参照が
    空のとき」だけを通るので、ワークフロー id が壊れている場合は
    :func:`video_workflow_problem` / :func:`image_workflow_problem` に任せて
    黙って通す。
    """
    try:
        stages = _reference_stages(mode, video_workflow, image_workflow)
    except WorkflowSpecError:
        return None
    for kind, _label, spec in stages:
        fan = spec.ref_media
        if fan is None or fan.min_refs < 1:
            continue
        names = "・".join(
            f"`{MULTI_INPUT_FIELDS[name]}`（{MULTI_INPUT_LABELS[name]}）"
            for name in fan.names()
        )
        return (
            f"{kind} '{spec.id}' には参照素材 {names} のいずれかが"
            f" {fan.min_refs} 件以上必要です"
        )
    return None


def reference_problem(
    mode: str,
    video_workflow: str | None,
    references: dict[str, list[str]],
    *,
    image_workflow: str | None = None,
) -> str | None:
    """マルチモーダル参照が使える組み合わせか（None == 問題なし、SPEC §3.1）。

    参照素材は**画像ステージと動画ステージのどちらの入力にもなる**（MiniMax H3
    Image r2i の参照画像 / MiniMax H3 r2v の参照画像・動画・音声）ので、その
    mode で走るステージのうち**どれか 1 つでも宣言していれば通す**。参照モードは
    API 側で先頭フレーム i2v と相互排他だが、それは**ワークフローを分けて**
    表現してある（参照専用のワークフローだけが ``multi_inputs`` を宣言し、開始
    フレームの受け取り口を持たない）。だからここで見るのは「宣言のないワーク
    フローに渡していないか」「件数の上限」「拡張子」の 3 つだけで、開始フレーム
    との組み合わせは :func:`start_image_problem` が別に断る。

    例外は**下限**で、ComfyUI 側で参照素材をグラフに展開するワークフロー
    （MiniMax H3 r2v / r2i、:class:`app.workflows.RefMediaFan`）だけが「種類を
    問わず合計 1 件以上」を要求する。参照が 1 件も無いと ref2va のウェイトで素の
    生成をするだけになり、ワークフローを選んだ意味が無くなるため。

    サイズ・解像度・尺の細かい制約は外部 API の判断に任せ、失敗メッセージを
    そのまま見せる。
    """
    if not references:
        return _missing_reference_problem(mode, video_workflow, image_workflow)
    names = "・".join(f"`{MULTI_INPUT_FIELDS[name]}`" for name in references)
    try:
        stages = _reference_stages(mode, video_workflow, image_workflow)
    except WorkflowSpecError as exc:
        return str(exc)
    if not stages:
        return (
            f"mode '{mode}' は参照素材を受け取るステージを走らせないので、"
            f"参照素材（{names}）は使えません"
        )
    for name, paths in references.items():
        accepting = [
            (kind, spec) for kind, _label, spec in stages if name in spec.multi_inputs
        ]
        if not accepting:
            where = " / ".join(f"{kind} '{spec.id}'" for kind, _l, spec in stages)
            return (
                f"{where} は{MULTI_INPUT_LABELS[name]}"
                f"（`{MULTI_INPUT_FIELDS[name]}`）を受け取れません"
            )
        for kind, spec in accepting:
            limit = spec.multi_inputs[name]
            if len(paths) > limit:
                return (
                    f"{kind} '{spec.id}' の{MULTI_INPUT_LABELS[name]}は"
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
    return None


def context_latent_problem(
    mode: str,
    video_workflow: str | None,
    context_latent_path: str | None,
) -> str | None:
    """ラテント連続性の入力が揃っているか（None == 問題なし、SPEC §2.2）。

    連続カットのワークフロー（``minimax_h3_r2v_context``）は、直前カットの
    AV ラテントが無いと ComfyUI 側でファイルを探しに行って落ちる。宣言のない
    ワークフローに渡していないかと合わせて、投入前にここで断る。
    """
    supplied = (context_latent_path or "").strip()
    try:
        spec = get_video_spec(video_workflow)
    except WorkflowSpecError as exc:
        return str(exc) if supplied else None
    if not spec.supports("context_latent"):
        if supplied and mode in ("full", "i2v"):
            return (
                f"video workflow '{spec.id}' は引き継ぎ元の AV ラテント"
                "（`context_latent_path`）を受け取れません"
            )
        return None
    if mode in ("full", "i2v") and not supplied:
        return (
            f"video workflow '{spec.id}' は連続カット専用なので、引き継ぎ元の"
            "AV ラテント（`context_latent_path`）が要ります"
            "（前のカットを生成して採用すると付きます）"
        )
    return None


def start_image_problem(
    mode: str,
    video_workflow: str | None,
    *,
    source_image: str | None = None,
    end_image: str | None = None,
) -> str | None:
    """開始 / 最終フレームを受け取らないワークフローに渡していないか（§3.1）。

    参照専用のワークフロー（``minimax_h3_r2v``）は、モデル側で
    参照モードと先頭フレーム i2v が排他なので**開始フレームの受け取り口そのものを
    持たない**。黙って捨てると「渡したのに効かない」になるので、投入前に断る。

    ``mode: "full"`` は画像ステージが開始フレームを作るモードで、こちらは
    :func:`video_workflow_problem` が ``accepts_start_image`` を見て断っている
    （``source_image`` は画像ワークフロー側の入力なのでここでは見ない）。
    """
    if mode != "i2v":
        return None
    try:
        spec = get_video_spec(video_workflow)
    except WorkflowSpecError as exc:
        return str(exc)
    for name, value in (("image", source_image), ("end_image", end_image)):
        if not (value or "").strip() or spec.supports(name):
            continue
        return (
            f"video workflow '{spec.id}' は{input_label(spec, name)}"
            f"（`{INPUT_FIELDS[name]}`）を受け取りません"
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
) -> str | None:
    """Why this audio job cannot run (None == fine).

    Only ``mode: "audio"`` is checked: every other mode ignores the audio
    fields entirely, so an unknown ``audio_workflow`` there is harmless.

    ``audio_category`` is a COMBO widget: ComfyUI rejects the whole prompt when
    a value is outside its declared set, so it is caught here (422) instead of
    failing the job halfway through.  A workflow that declares no length at all
    (``max_duration == 0``, e.g. a model whose API has no length parameter) skips
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
    #: the words to sing ([Verse] / [Chorus] …). Empty == instrumental.
    lyrics: str = ""
    #: styles to keep out of the track
    negative_tags: str = ""
    #: Stable Audio: Music / Instrument / SFX / One-shot
    audio_category: str = AUDIO_CATEGORIES[0]
    #: Stable Audio: expand the prompt with the graph's own local LLM first
    reprompt: bool = False

    aspect_ratio: str = "4:3 (Standard)"
    megapixels: float = DEFAULT_MEGAPIXELS

    # 画像ワークフロー用 LoRA（target='image' で登録したもの）
    loras: list[LoraRef] = Field(default_factory=list)
    trigger_text: str = ""
    # 動画ワークフロー用 LoRA（target='video' で登録したもの）
    video_loras: list[LoraRef] = Field(default_factory=list)
    video_trigger_text: str = ""

    duration: float = 10.0
    fps: int = 25
    #: サンプリング回数（SPEC §3.1）。`steps` を宣言しているワークフローだけが
    #: 読み、`0`（既定）は「未指定」= テンプレートの既定値のまま。
    steps: int = Field(default=0, ge=0, le=MAX_STEPS)

    # absolute path inside assets/ or the "/assets/..." URL returned by the
    # asset upload endpoints.
    audio_path: str | None = None
    source_image: str | None = None
    end_image: str | None = None
    reference_video: str | None = None

    #: ラテント連続性（``minimax_h3_r2v_context``）で読み込む、直前カットの
    #: AV ラテントのパス。**ComfyUI 側のファイルパス**であって `assets/` の中の
    #: ファイルではないので、投入前の解決も上げ直しもしない（宣言のある
    #: ワークフローだけが読む、SPEC §3.1）。
    context_latent_path: str | None = None

    # マルチモーダル参照（SPEC §3.1）。1 つのフィールドが**複数ファイル**を持ち、
    # 宣言しているワークフロー（MiniMax H3 r2v）で
    # のみ使え、**先頭フレーム（`source_image` / `end_image`）とは排他**。
    #: 一貫性のよりどころにする参照画像（最大枚数はワークフロー宣言による）
    reference_images: list[str] = Field(default_factory=list)
    #: 動きのお手本にする参照動画
    reference_videos: list[str] = Field(default_factory=list)
    #: ムード・曲調のよりどころにする参照音声
    reference_audios: list[str] = Field(default_factory=list)

    #: ショット割り（SPEC §3.1）。宣言しているワークフローでのみ
    #: 使え、指定すると ``video_prompt`` の代わりにこれが本文になる。
    multi_shots: list[MultiShot] = Field(default_factory=list)
    #: Elements（``@要素名`` で呼ぶ参照画像の束、SPEC §3.1）
    kling_elements: list[ElementInput] = Field(default_factory=list)

    seed: int | None = None  # None -> random (recorded in params)

    # 選択式フィールドの値（`GET /api/options` の workflow の `selects` にある
    # 論理名 -> 選んだ文字列）。省略した項目はワークフローの既定値、`auto` を
    # 宣言している項目（尺など）は入力から自動で決まる（SPEC §3.1）。
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
                image_workflow=self.image_workflow,
            )
            or start_image_problem(
                self.mode,
                self.video_workflow,
                source_image=self.source_image,
                end_image=self.end_image,
            )
            or multi_shot_problem(
                self.mode,
                self.video_workflow,
                multi_shots_of(self),
                video_prompt=self.video_prompt,
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
    # extra inputs of the workflow the continuation switches to (a workflow may
    # want a closing frame or a reference clip); omitted means "keep whatever
    # the source job used".
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
    #: 続き用の grok セッション id（空 = まだ開いていない / 使えなかった）
    grok_session_id: str = ""
    #: このチャットの作業ディレクトリ（入力画像のコピー先 = grok の cwd）
    grok_cwd: str = ""


PromptTemplate = Literal["natural", "tagged"]


class ChatLoraRef(LoraRef):
    """A selected LoRA as the chat sees it: the trigger word plus the human name.

    ``display_name`` lets the system prompt map e.g. 「サクラ」 -> ``sakura`` so Grok can
    resolve the Japanese name the user actually types.  Job params keep the
    plain :class:`LoraRef` snapshot.
    """

    display_name: str = ""


class ChatReference(BaseModel):
    """チャットに渡す参照素材 1 件（ライブラリで解決済み、SPEC §4.3）。

    生成タブのチャットは参照ファイルそのものを Grok に見せない（ワークフローが
    実際に読むのはジョブ実行時）。代わりに**何が添付されているか**だけを伝える
    ためのメタデータで、``name`` が空なら「ライブラリに無いファイル」の意味。
    """

    #: 素材の種別（``image`` / ``video`` / ``audio``）
    kind: str = "image"
    #: 実ファイル名（ライブラリに無い素材でも、これだけは必ず入る）
    filename: str = ""
    #: ライブラリ上の表示名（未登録なら空）
    name: str = ""
    #: ライブラリの分類（``character`` / ``background`` / ``prop``、未分類は空）
    category: str = ""
    tags: list[str] = Field(default_factory=list)
    nsfw: bool = False


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
    #: 最後のフレーム（欄が出ているときだけ送られる）。中身は見せず、「指定が
    #: あるかどうか」と名前だけを CONTEXT に出す。
    end_image_path: str | None = None
    #: r2v 系で実際に添付されている参照素材（フォームで選んだ順。ライブラリの
    #: パス / URL）。突き合わせて名前・分類・タグを CONTEXT の対応表にする。
    reference_images: list[str] = Field(default_factory=list)
    reference_videos: list[str] = Field(default_factory=list)
    reference_audios: list[str] = Field(default_factory=list)
    #: 解像度欄が出ているときのフォームの現在値（構図の前提になる）
    aspect_ratio: str | None = None
    megapixels: float | None = None
    #: ネガティブプロンプト欄が出ているときの現在値。ネガティブを持たない
    #: ワークフローでは本文の除外文に畳み込ませる。
    negative_prompt: str | None = None
    # --- 音声モードのフォームの現在値 ---------------------------------------
    audio_category: str | None = None
    negative_tags_draft: str | None = None


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
    #: the words to sing, with [Verse] / [Chorus] structure tags
    lyrics: str | None = None
    #: styles to keep out of the track
    negative_tags: str | None = None
    notes: str | None = None


class ChatReply(BaseModel):
    """POST /api/chat/sessions/{id}/messages response."""

    role: Literal["assistant"] = "assistant"
    content: str
    result: PromptResult | None = None


class ChatState(BaseModel):
    """相談チャットの実行状態（POST …/stop の応答）。"""

    session_id: str
    #: Grok のターンが走っているか
    running: bool
    #: 実行中の活動テキスト（「思考中」など。None = 無し）
    activity: str | None = None


class ChatProgress(ChatState):
    """WS /api/ws で流す相談チャットのイベント（``type: "chat"``）。"""

    type: Literal["chat"] = "chat"


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
    #: 選ばれている CLI の状態（歴史的に ``grok`` という名前のまま）
    grok: HealthStatus
    #: いま選ばれている CLI（設定 ``agent_cli``）とその表示名
    cli: LlmCli = "grok"
    cli_label: str = "Grok"


class PushKeys(BaseModel):
    p256dh: str
    auth: str


class PushSubscriptionIn(BaseModel):
    endpoint: str
    keys: PushKeys


class PushEndpointIn(BaseModel):
    endpoint: str


class PushVapidPublicKey(BaseModel):
    public_key: str


# --------------------------------------------------------------------------
# agent mode (AGENT-MODE §4 / §5)
# --------------------------------------------------------------------------

AgentStatus = Literal[
    "idle", "planning", "running", "waiting_checkin", "stopped", "done"
]
AgentCheckinMode = Literal["every_job", "milestone", "auto"]
AgentActionName = Literal[
    "plan", "run_task", "continue", "rerun", "inspect", "note", "rename",
    "library", "library_search", "library_sheet", "agent_search_sessions",
    "agent_read_session", "get_prompt_examples",
    "checkin", "done",
    # ドラマスタジオ（:mod:`app.studio`）の操作
    "studio_list_projects", "studio_get_project", "studio_create_project",
    "studio_update_project", "studio_upsert_episode", "studio_upsert_scene",
    "studio_upsert_shot", "studio_delete_shot", "studio_upsert_asset",
    "studio_register_asset_from_job", "studio_render_shot", "studio_get_takes",
    "studio_translate_shot",
    "studio_select_take", "studio_reject_take",
    # キャンバス（:mod:`app.canvas`）の盤面操作。スタジオのツール一式に足す形で
    # 使い、キャンバスのチャットからの実行でだけプロンプトに載る
    "canvas_list_cards", "canvas_search_sessions", "canvas_read_session",
    "canvas_place_card",
    "canvas_move_card", "canvas_update_card",
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
    # Grok セッションの続き用キャッシュ（API には出さない）
    grok_session_id: str = Field(default="", exclude=True)
    grok_cwd: str = Field(default="", exclude=True)
    snapshot_key: str = Field(default="", exclude=True)


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
    #: 直近の非 system メッセージ冒頭（一覧の絞り込み用）
    preview: str = ""


class AgentSessionCreate(BaseModel):
    """POST /api/agent/sessions body (AGENT-MODE §5.1)."""

    title: str = ""
    goal: str = ""
    checkin_mode: AgentCheckinMode = "milestone"
    #: 自走セッションが続けて生成する本数（0 = 無制限。チェックインしない）
    auto_limit: int = Field(default=5, ge=0)


class AgentSessionUpdate(BaseModel):
    """PATCH /api/agent/sessions/{id} body（指定した項目だけ変える）。

    システムプロンプトはセッション作成時に焼き込む方式なので、ここで変えた値が
    Grok の読む文面に載るのは**次のターンから**。一方、上限に達したかどうかの
    判定（:func:`app.agent_runner.over_limit`）は毎回ここの値を読むので、実行中
    でも即時に効く。
    """

    checkin_mode: AgentCheckinMode | None = None
    #: 生成本数の上限（0 = 無制限）
    auto_limit: int | None = Field(default=None, ge=0)


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
    #: get_prompt_examples アクション: MiniMax H3 実例の絞り込み
    #: （:mod:`app.h3_examples` の id / モード / カテゴリ / 件数）
    example_id: str | None = None
    example_mode: str | None = None
    example_category: str | None = None
    example_limit: int = 0
    #: agent_read_session の対象（他セッション。自分自身は読めない）
    session_id: str | None = None
    # library_sheet アクション: シートに載せる素材の id（並べる順）と大きさ
    item_ids: list[str] = Field(default_factory=list)
    width: int | None = None
    height: int | None = None
    overrides: dict[str, Any] = Field(default_factory=dict)
    #: studio_* アクションのパラメータ。対象の id（``project_id`` / ``id`` /
    #: ``shot_id`` / ``take_id`` / ``job_id`` / ``source``）と、検証済みの本文
    #: ``body``（Studio*Create / Studio*Update のフィールドだけ）が入る
    studio: dict[str, Any] = Field(default_factory=dict)
    #: canvas_* アクションのパラメータ。対象の id（``project_id`` /
    #: ``card_id``）と、検証済みの本文 ``body``（CanvasCard* のフィールドだけ）
    canvas: dict[str, Any] = Field(default_factory=dict)
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
    #: True なら「自動」を選べる（未指定で入力から決まる。尺など）
    auto: bool = False
    hint: str = ""
    #: **表示だけ**の日本語ラベル（``選ぶ値 -> 画面に出す文字列``、SPEC §3.1）。
    #: 送る値は `choices` の生のままで、フォームの `<option>` の文字だけを差し替える。
    #: 宣言の無い値はフロントが生の値をそのまま出す（宣言は任意）。
    choice_labels: dict[str, str] = Field(default_factory=dict)


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
    #: 生成フォームの 2 段プルダウンの 2 段目（モード）の表示名。1 段目に
    #: `family_label` が出るので、こちらにモデル名は入らない（SPEC §3.1）。
    mode_label: str = ""
    #: 生成フォームの 1 段目（モデル）の表示名（供給元の注記つき）
    family_label: str = ""
    #: model family — image LoRAs of another family cannot be used with it
    family: str = DEFAULT_FAMILY
    notes: str = ""
    #: logical inputs the workflow needs: image / audio / end_image / video
    requires: list[str] = Field(default_factory=list)
    #: 複数ファイルで渡せる参照入力（論理名 -> 件数の上限、SPEC §3.1）。宣言の
    #: ないワークフローでは空で、フォームは参照欄そのものを出さない。
    multi_inputs: dict[str, int] = Field(default_factory=dict)
    #: 選択式どうしの相関（名前 -> `[相手の名前, 相手に必要な値]`、SPEC §3.1）。
    #: `duration` が `model` の値でしか効かないときのように
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
    #: 実行エンジン（今は ``comfyui`` のみ、SPEC §5.2）。UI のバッジ用。
    backend: str = "comfyui"
    #: audio workflows: the clip length the model supports, in seconds
    min_duration: float = 0.0
    max_duration: float = 0.0
    default_duration: float = 0.0
    #: そのモデルが想定している解像度（メガピクセル、0.0 = 宣言なし）。宣言が
    #: あるワークフローを選ぶと、フォームの「メガピクセル」がこの値になる
    #: （SPEC §3.1）。無ければフォームのグローバル既定（``DEFAULT_MEGAPIXELS``）のまま。
    default_megapixels: float = 0.0


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
    #: the Stable Audio COMBO choices, for the 音声 form
    audio_categories: list[str] = Field(default_factory=lambda: list(AUDIO_CATEGORIES))
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


# --------------------------------------------------------------------------
# ドラマスタジオ（プロジェクト -> 脚本 -> Shot ごとの生成 -> Take の採用）
# --------------------------------------------------------------------------

#: World Bible の素材の区分（キャラクター / 場所・背景 / 小道具 / その他の参照）
#: ``style`` は画風・トーンの取り決め（キャンバスの style カードの参照先）。
#: ファイル実体を持たないメタデータのみの素材として使うことが多い。
StudioAssetCategory = Literal[
    "character", "environment", "prop", "style", "reference"
]

#: 素材の実体の種別（そのまま assets/<kind>/ の置き場になる）
StudioAssetKind = Literal["image", "video", "audio"]

#: 素材に足す追加リファレンスの役割（:data:`studio_asset_files`）。メインの
#: ファイル（``studio_assets.path``）とは別に、キャラなら声サンプル（voice）・
#: 動作の参照動画（video）・別アングルの画像（image）を何本でもぶら下げられる。
StudioAssetFileRole = Literal["image", "voice", "video"]

#: リファレンスの役割 -> 実体の種別（``assets/<kind>/`` の置き場と静的配信 URL）
ASSET_FILE_ROLE_KINDS: dict[str, str] = {
    "image": "image",
    "voice": "audio",
    "video": "video",
}

#: Shot の進み具合（'draft' = 執筆中 / 'ready' = 生成してよい / 'done' = 採用済み）
StudioShotStatus = Literal["draft", "ready", "done"]

#: Take の状態。'rendering' / 'failed' はジョブから導出した値で、DB に残るのは
#: 人が決めた 'selected' / 'rejected' だけ（:mod:`app.studio`）。
StudioTakeStatus = Literal["rendering", "candidate", "selected", "rejected", "failed"]

#: Shot が使う動画ワークフローの強制指定（None = 素材と引き継ぎから自動で決める）
StudioWorkflowOverride = Literal[
    "minimax_h3_t2v", "minimax_h3_i2v", "minimax_h3_r2v"
]

#: 動画生成の品質（プロジェクト単位の設定）。論理モード（t2v / i2v / r2v）とは
#: 直交していて、モードが決まったあとに「モード × 品質 -> バリアント id」で
#: 解決される（:func:`app.studio._quality_workflow`）:
#:
#: - ``normal``: 素の MiniMax H3（20 steps）。どの接続先でも動く。
#: - ``opt``: 素と同じ 20 steps のまま、量子化ウェイトと高速化パッチだけを
#:   焼き込んだ最適化版（品質は素相当で実行が速い）。
#: - ``turbo``: 4 steps の蒸留 LoRA 版（いちばん速いが粗い）。
#:
#: ``opt`` / ``turbo`` は i2v / r2v にしかバリアントが無く、カスタムノード頼み
#: なので、条件が揃わなければ素へフォールバックする。
StudioVideoQuality = Literal["normal", "opt", "turbo"]

#: リビジョンを作った主体（人の操作か、エージェントの操作か）
StudioRevisionActor = Literal["user", "agent"]


class StudioProject(BaseModel):
    """1 本の作品。"""

    id: str
    name: str
    #: 作品コード（任意。付けた場合だけ重複を拒む）
    code: str = ""
    synopsis: str = ""
    #: World Bible の覚え書き（作品全体の設定）
    world_notes: str = ""
    #: 日本語のプロンプトを Grok で英語に直してから投入する（MiniMax H3 は英語前提）
    auto_translate: bool = True
    #: 引き継ぎ（`carry_over_end_frame`）を Motion Context で行う（ラテント連続性）。
    #: OFF なら直前カットのラストフレーム 1 枚を開始フレームにする従来の i2v、
    #: ON なら直前カットの動画と AV ラテントを渡す ``minimax_h3_r2v_context``。
    #: カスタムノードが要るので、入っていない接続先では投入時に断られる。
    latent_continuity: bool = False
    #: 動画生成の品質（:data:`StudioVideoQuality`）。テイク生成のたびに、決まった
    #: 論理モードと掛け合わせてワークフローのバリアントへ解決される。
    quality: StudioVideoQuality = "normal"
    #: 動画生成の画質＝メガピクセル（作品単位の既定）。``None`` = 指定しない
    #: ＝ワークフロー宣言の ``default_megapixels`` / グローバル既定のまま。
    #: Shot 個別の ``megapixels`` があればそちらが勝つ。
    megapixels: float | None = None
    #: 動画生成のアスペクト比（作品単位の既定。``"16:9 (Widescreen)"`` 等）。
    #: ``None`` = 指定しない＝既定のまま。Shot 個別の指定があればそちらが勝つ。
    aspect_ratio: str | None = None
    #: サンプリング回数（作品単位の既定、:data:`MAX_STEPS` まで）。``0`` = 未指定
    #: ＝**テンプレートの既定のまま**（品質 turbo なら 4、normal / opt なら 20）。
    #: ``steps`` を宣言しているワークフローだけが読む（SPEC §3.1）。
    steps: int = 0
    #: この作品から投入するジョブをすべて NSFW 扱いにする。OFF なら**非 NSFW で
    #: 固定**（投入時に明示するので、Grok の自動判定は走らない）。
    nsfw: bool = False
    created_at: str
    updated_at: str


class StudioProjectSummary(StudioProject):
    """GET /api/studio/projects の 1 行（一覧に出す件数つき）。"""

    shot_count: int = 0
    asset_count: int = 0
    take_count: int = 0
    #: 採用済みの Take の数（= 仕上がった Shot の数）
    selected_take_count: int = 0


class StudioProjectCreate(BaseModel):
    """POST /api/studio/projects body。"""

    name: str
    code: str = ""
    synopsis: str = ""
    world_notes: str = ""
    auto_translate: bool = True
    #: 引き継ぎ（`carry_over_end_frame`）を Motion Context で行う（ラテント連続性）。
    #: OFF なら直前カットのラストフレーム 1 枚を開始フレームにする従来の i2v、
    #: ON なら直前カットの動画と AV ラテントを渡す ``minimax_h3_r2v_context``。
    #: カスタムノードが要るので、入っていない接続先では投入時に断られる。
    latent_continuity: bool = False
    #: 動画生成の品質（:data:`StudioVideoQuality`。既定は素の 20 steps）
    quality: StudioVideoQuality = "normal"
    #: 動画生成の画質＝メガピクセル（``None`` = ワークフローの既定のまま）
    megapixels: float | None = None
    #: 動画生成のアスペクト比（``None`` = 既定のまま）
    aspect_ratio: str | None = None
    #: サンプリング回数（``0`` = 未指定＝テンプレートの既定のまま）
    steps: int = 0
    #: この作品から投入するジョブをすべて NSFW 扱いにする（OFF = 非 NSFW 固定）
    nsfw: bool = False


class StudioProjectUpdate(BaseModel):
    """PATCH /api/studio/projects/{id} body（指定した項目だけ変える）。

    ``megapixels`` / ``aspect_ratio`` は **null を明示すると外れる**（送らな
    ければ今の値のまま）。区別は ``model_fields_set`` で行う（Shot 側の
    :class:`StudioShotUpdate` と同じ約束）。
    """

    name: str | None = None
    code: str | None = None
    synopsis: str | None = None
    world_notes: str | None = None
    auto_translate: bool | None = None
    #: 引き継ぎ（`carry_over_end_frame`）を Motion Context で行う（ラテント連続性）。
    #: OFF なら直前カットのラストフレーム 1 枚を開始フレームにする従来の i2v、
    #: ON なら直前カットの動画と AV ラテントを渡す ``minimax_h3_r2v_context``。
    #: カスタムノードが要るので、入っていない接続先では投入時に断られる。
    latent_continuity: bool | None = None
    #: 動画生成の品質（:data:`StudioVideoQuality`）
    quality: StudioVideoQuality | None = None
    #: 動画生成の画質＝メガピクセル（null を送ると既定へ戻る）
    megapixels: float | None = None
    #: 動画生成のアスペクト比（null を送ると既定へ戻る）
    aspect_ratio: str | None = None
    #: サンプリング回数（``0`` を送ると「テンプレートの既定のまま」へ戻る）。
    #: ``megapixels`` などと違って NULL を持たない列なので、未指定を表すのは
    #: null ではなく **0** のほう（送らなければ今の値のまま）。
    steps: int | None = None
    #: この作品から投入するジョブをすべて NSFW 扱いにする（OFF = 非 NSFW 固定）
    nsfw: bool | None = None

    #: null を明示できる項目（送られたときだけ NULL 書き込みを許す）
    NULLABLE: ClassVar[tuple[str, ...]] = ("megapixels", "aspect_ratio")

    def changes(self) -> dict[str, object]:
        """書き換える項目だけを取り出す（未指定は入らない）。"""
        return {
            name: value
            for name, value in self.model_dump().items()
            if value is not None or (
                name in self.NULLABLE and name in self.model_fields_set
            )
        }


class StudioEpisode(BaseModel):
    """話（エピソード）。場（:class:`StudioScene`）の入れ物。"""

    id: str
    project_id: str
    sort_order: int = 0
    title: str = ""
    synopsis: str = ""
    created_at: str


class StudioEpisodeCreate(BaseModel):
    """POST /api/studio/projects/{id}/episodes body。"""

    title: str = ""
    synopsis: str = ""
    #: 並び順（省略すると末尾に足す）
    sort_order: int | None = None


class StudioEpisodeUpdate(BaseModel):
    """PATCH /api/studio/episodes/{id} body（指定した項目だけ変える）。"""

    title: str | None = None
    synopsis: str | None = None
    sort_order: int | None = None


class StudioScene(BaseModel):
    """場（シーン）。Shot はここに属する（属さない Shot は未分類）。"""

    id: str
    episode_id: str
    project_id: str
    sort_order: int = 0
    title: str = ""
    synopsis: str = ""
    #: 「夜明け前」「閉店後」などの時間帯メモ
    time_of_day: str = ""
    created_at: str


class StudioSceneCreate(BaseModel):
    """POST /api/studio/episodes/{id}/scenes body。"""

    title: str = ""
    synopsis: str = ""
    time_of_day: str = ""
    sort_order: int | None = None


class StudioSceneUpdate(BaseModel):
    """PATCH /api/studio/scenes/{id} body（指定した項目だけ変える）。"""

    title: str | None = None
    synopsis: str | None = None
    time_of_day: str | None = None
    sort_order: int | None = None
    #: 引っ越し先の話（同じ作品の話だけ。並び順は移動先の末尾になる）
    episode_id: str | None = None


class StudioReorder(BaseModel):
    """並べ替えの body。``ids`` の並び順がそのまま ``sort_order`` になる
    （その親の子を全件、過不足なく並べたものを送る）。"""

    ids: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------
# 素材の拡張項目（`studio_assets.profile`）
# --------------------------------------------------------------------------
#
# 「名前・説明・ファイル」に収まらない設定を分類ごとに持つ。生成には直接
# 効かせず（プロンプトに入るのは今までどおり `prompt_caption`）、キャンバスの
# カードや脚本を書くときの覚え書きとして使う。

class StudioCharacterProfile(BaseModel):
    """``category='character'`` の拡張項目。"""

    model_config = ConfigDict(extra="forbid")
    #: 外見（生成プロンプトに使える具体性で）
    appearance: str = ""
    personality: str = ""
    #: 声・話し方（音声つき生成の手がかり）
    voice: str = ""
    notes: str = ""


class StudioEnvironmentProfile(BaseModel):
    """``category='environment'`` の拡張項目。"""

    model_config = ConfigDict(extra="forbid")
    #: 時間帯・天候・雰囲気
    mood: str = ""
    notes: str = ""


class StudioPropProfile(BaseModel):
    """``category='prop'`` の拡張項目。"""

    model_config = ConfigDict(extra="forbid")
    notes: str = ""


class StudioStyleProfile(BaseModel):
    """``category='style'`` の拡張項目。"""

    model_config = ConfigDict(extra="forbid")
    #: 色調・カラーパレットのメモ
    palette: str = ""
    #: 参照画像の URL（``/library/…`` / ``/assets/…``）
    references: list[str] = Field(default_factory=list)
    notes: str = ""


class StudioReferenceProfile(BaseModel):
    """``category='reference'`` の拡張項目。"""

    model_config = ConfigDict(extra="forbid")
    notes: str = ""


#: 素材の分類 -> `profile` の検証モデル
ASSET_PROFILE_MODELS: dict[str, type[BaseModel]] = {
    "character": StudioCharacterProfile,
    "environment": StudioEnvironmentProfile,
    "prop": StudioPropProfile,
    "style": StudioStyleProfile,
    "reference": StudioReferenceProfile,
}


def validate_asset_profile(
    category: str, profile: dict[str, Any], *, strict: bool = True
) -> dict[str, Any]:
    """分類のスキーマで ``profile`` を検証して正規化する。

    ``strict=False`` では**その分類に無い項目を黙って捨てる**。分類を付け替えた
    ときに、前の分類でだけ意味があった項目を持ち越さないための逃げ道で、人が
    送った値を検証するとき（``strict=True``）は知らない項目を弾く。
    """
    model = ASSET_PROFILE_MODELS.get(category, StudioReferenceProfile)
    if not strict:
        known = set(model.model_fields)
        profile = {key: value for key, value in profile.items() if key in known}
    return model(**profile).model_dump()


class StudioAssetFile(BaseModel):
    """素材にぶら下がる追加リファレンス 1 件（:data:`studio_asset_files`）。

    メインのファイル（``studio_assets.path``）を置き換えるものではなく、
    「この声で喋る」「この動きを参照する」を素材に足していくためのもの。
    """

    id: str
    asset_id: str
    project_id: str
    #: image = 追加画像 / voice = 声サンプル / video = 動画リファレンス
    role: StudioAssetFileRole = "image"
    #: ファイルの絶対パス（``assets/<kind>/`` の下）
    path: str = ""
    #: ``/assets/<kind>/<file>``（静的配信 URL）
    url: str = ""
    #: 人間向けの短い説明（「怒っているときの声」など）
    caption: str = ""
    sort_order: int = 0
    created_at: str


class StudioAssetFileCreate(BaseModel):
    """POST /api/studio/assets/{id}/files の multipart 以外の項目。"""

    role: StudioAssetFileRole = "image"
    caption: str = ""


class StudioAsset(BaseModel):
    """World Bible の素材 1 件（プロンプトからは ``@name`` で呼ぶ）。"""

    id: str
    project_id: str
    #: `@名前` で呼ぶ識別名（プロジェクト内で一意）
    name: str
    category: StudioAssetCategory = "reference"
    #: 人間向けの説明（日本語可）
    caption: str = ""
    #: 生成プロンプトに埋め込む説明（英語推奨）。参照として添付できないモードで
    #: メンションを置き換えるのに使う
    prompt_caption: str = ""
    kind: StudioAssetKind
    #: ファイルの絶対パス。**空 = メタデータのみの素材**（名前とキャプションだけ）で、
    #: 参照には添付できないぶん、プロンプトでは説明文に展開される
    path: str = ""
    #: ``/assets/<kind>/<file>``（静的配信 URL）。メタデータのみの素材では空
    url: str = ""
    #: 分類ごとの拡張項目（:data:`ASSET_PROFILE_MODELS`）。生成には効かない
    profile: dict[str, Any] = Field(default_factory=dict)
    #: メインのファイルに足したリファレンス（声サンプル・動画・追加画像）。
    #: 今の生成ワークフローには流し込まないが、エージェントには渡している
    files: list[StudioAssetFile] = Field(default_factory=list)
    #: 差し替え禁止の印（UI で鍵を出すだけ。生成には影響しない）
    locked: bool = False
    sort_order: int = 0
    created_at: str
    #: 最後に書き換えた時刻（一度も直していなければ ``created_at``）
    updated_at: str = ""
    #: プロンプトに効く項目（名前・キャプション・ファイル）を最後に書き換えた
    #: 時刻。Take の stale 判定に使う
    prompt_updated_at: str = ""


class StudioAssetCreate(BaseModel):
    """POST /api/studio/projects/{id}/assets を JSON で送るときの body。

    ファイルを持たない**メタデータのみの素材**を作る（ファイルつきで登録する
    ときは同じ URL に multipart で投げる）。
    """

    name: str
    kind: StudioAssetKind = "image"
    category: StudioAssetCategory = "reference"
    caption: str = ""
    prompt_caption: str = ""
    #: 分類ごとの拡張項目（:data:`ASSET_PROFILE_MODELS` で検証する）
    profile: dict[str, Any] = Field(default_factory=dict)
    #: 既にあるファイルの絶対パス（省略 = メタデータのみ）。実体は
    #: ``assets/<kind>/`` へ複製されるので、チャットの添付や生成結果を
    #: そのまま素材にできる
    path: str = ""
    locked: bool = False
    sort_order: int | None = None


class StudioAssetUpdate(BaseModel):
    """PATCH /api/studio/assets/{id} body（指定した項目だけ変える）。"""

    name: str | None = None
    category: StudioAssetCategory | None = None
    caption: str | None = None
    prompt_caption: str | None = None
    #: 分類ごとの拡張項目。**送ったものが丸ごと今の値になる**（項目単位の
    #: 差分更新ではない）
    profile: dict[str, Any] | None = None
    #: メインのファイルの差し替え（既にあるファイルの絶対パス。実体は
    #: ``assets/<kind>/`` へ複製される）。ブラウザからのアップロードは
    #: ``POST /api/studio/assets/{id}/file``
    path: str | None = None
    kind: StudioAssetKind | None = None
    locked: bool | None = None
    sort_order: int | None = None


class StudioShot(BaseModel):
    """脚本の 1 カット。"""

    id: str
    project_id: str
    #: 所属する場（None = まだどの場にも入れていない）
    scene_id: str | None = None
    #: **場の中での**並び順（未分類なら「作品の未分類グループ」の中での順番）。
    #: 画面に出る順は 話 -> 場 -> この値 の階層で決まり、未分類は作品の末尾
    sort_order: int = 0
    title: str = ""
    #: 物語上の目的（このカットで何が進むのか）
    purpose: str = ""
    action: str = ""
    #: 台詞（投入時に MiniMax H3 の `<d>[Language] …</d>` へ組み込む）
    dialogue: str = ""
    #: 効果音・環境音
    soundscape: str = ""
    bgm: str = ""
    camera: str = ""
    #: 尺（MiniMax H3 は 1〜15 秒）
    duration_seconds: float = 5.0
    #: 生成プロンプトの本文（`@素材名` メンション可）
    prompt: str = ""
    status: StudioShotStatus = "draft"
    selected_take_id: str | None = None
    #: 直前の Shot の採用 Take のラストフレームを開始フレームに使う
    carry_over_end_frame: bool = False
    # --- Shot ごとの生成設定（None = JobCreate の既定値のまま） -------------
    #: 画面比（``"16:9 (Widescreen)"`` などのプリセット名か ``"W:H"``）
    aspect_ratio: str | None = None
    #: 解像度の目安（画面比と合わせて幅×高さが決まる）
    megapixels: float | None = None
    #: 乱数の種（None = 毎回ランダム）
    seed: int | None = None
    #: ワークフローの強制指定（None = t2v / i2v / r2v を自動で決める）
    workflow_override: StudioWorkflowOverride | None = None
    #: 訳した（または人が直した）英語。公式フィールド込みの完成文
    english_prompt: str = ""
    #: その英語の元になった組み立て済み日本語（``preview.prompt`` と同じもの）
    english_source: str = ""
    #: 英訳の進行（``''`` / ``translating`` / ``failed``）
    english_status: str = ""
    #: 英訳失敗の理由（日本語。成功時・未実施は空）
    english_error: str = ""
    created_at: str
    updated_at: str
    #: プロンプトに効く項目を最後に書き換えた時刻（Take の stale 判定に使う）
    prompt_updated_at: str = ""


class StudioShotCreate(BaseModel):
    """POST /api/studio/projects/{id}/shots body。"""

    title: str = ""
    purpose: str = ""
    action: str = ""
    dialogue: str = ""
    soundscape: str = ""
    bgm: str = ""
    camera: str = ""
    duration_seconds: float = 5.0
    prompt: str = ""
    status: StudioShotStatus = "draft"
    carry_over_end_frame: bool = False
    scene_id: str | None = None
    aspect_ratio: str | None = None
    megapixels: float | None = None
    seed: int | None = None
    workflow_override: StudioWorkflowOverride | None = None
    #: 場の中での並び順（省略すると入る場の末尾に足す。未分類なら未分類の末尾）
    sort_order: int | None = None


class StudioShotUpdate(BaseModel):
    """PATCH /api/studio/shots/{id} body（指定した項目だけ変える）。

    ``scene_id`` / ``selected_take_id`` / 生成設定は **null を明示すると外れる**
    （送らなければ今の値のまま）。区別は ``model_fields_set`` で行う。
    """

    title: str | None = None
    purpose: str | None = None
    action: str | None = None
    dialogue: str | None = None
    soundscape: str | None = None
    bgm: str | None = None
    camera: str | None = None
    duration_seconds: float | None = None
    prompt: str | None = None
    status: StudioShotStatus | None = None
    carry_over_end_frame: bool | None = None
    sort_order: int | None = None
    scene_id: str | None = None
    selected_take_id: str | None = None
    aspect_ratio: str | None = None
    megapixels: float | None = None
    seed: int | None = None
    workflow_override: StudioWorkflowOverride | None = None
    #: 英語キャッシュ。空文字または null 明示で消す（``english_source`` は書けない）
    english_prompt: str | None = None

    #: null を明示できる項目（送られたときだけ NULL 書き込みを許す）
    NULLABLE: ClassVar[tuple[str, ...]] = (
        "scene_id",
        "selected_take_id",
        "aspect_ratio",
        "megapixels",
        "seed",
        "workflow_override",
        "english_prompt",
    )

    def changes(self) -> dict[str, object]:
        """書き換える項目だけを取り出す（未指定は入らない）。"""
        return {
            name: value
            for name, value in self.model_dump().items()
            if value is not None or (
                name in self.NULLABLE and name in self.model_fields_set
            )
        }


class StudioShotReorder(BaseModel):
    """POST /api/studio/projects/{id}/shots/reorder body。

    ``shot_ids`` の**並び順がそのまま** ``sort_order`` になる。並び順は場の中の
    ものなので、**1 つの場**（または未分類グループ）の Shot を全件、過不足なく
    並べたものを送る。作品の Shot 全件も受け取れる（場ごとに切り分けて書き
    戻す。場をまたいだ移動は :class:`StudioShotUpdate` の ``scene_id``）。
    """

    shot_ids: list[str] = Field(default_factory=list)


class StudioRenderRequest(BaseModel):
    """POST /api/studio/shots/{id}/render body（**すべて任意**）。

    その 1 回の投入にだけ効く上書きで、Shot もプロジェクトも書き換えない
    （何を使ったかは Take の元ジョブの ``params`` に残る）。送らなかった項目は
    今までどおりの解決に落ちる:

    - ``megapixels`` / ``aspect_ratio``: ここ → Shot → プロジェクト → 既定
    - ``duration``: ここ → Shot の ``duration_seconds``
    - ``steps``: ここ → プロジェクトの ``steps`` → テンプレートの既定
    - ``seed``: ここ → Shot の ``seed`` → 毎回ランダム

    ``steps`` は **0 も指定**（＝「テンプレートの既定のまま」を明示する）で、
    プロジェクトの設定より優先される。範囲の検査は
    :func:`app.studio.render_shot` が行い、外れていれば 400。
    """

    megapixels: float | None = None
    aspect_ratio: str | None = None
    #: 尺（秒）。未指定なら Shot の ``duration_seconds``
    duration: float | None = None
    #: サンプリング回数（``0`` = テンプレートの既定のまま）
    steps: int | None = None
    #: 乱数の種（未指定 = Shot の設定、それも無ければ毎回ランダム）
    seed: int | None = None


class StudioTake(BaseModel):
    """Shot 1 回ぶんの生成。実行状態と成果物はジョブ側から引いてくる。"""

    id: str
    shot_id: str
    project_id: str
    job_id: str
    status: StudioTakeStatus = "rendering"
    created_at: str
    #: 元ジョブの状態（queued / running / done / failed …）。ジョブが消えていれば None
    job_status: JobStatus | None = None
    #: 実際に走ったワークフロー（minimax_h3_t2v / _i2v / _r2v / _r2v_context）
    video_workflow: str | None = None
    video_path: str | None = None
    video_url: str | None = None
    last_frame_path: str | None = None
    last_frame_url: str | None = None
    #: ラテント連続性で保存した AV ラテント（ComfyUI 側のパス）。次のカットが
    #: ここから続きを作る。使わなかった Take は None。
    latent_path: str | None = None
    error: str | None = None
    #: 元ジョブの NSFW フラグ（ジョブが消えていれば None）
    nsfw: bool | None = None
    #: その判定の出どころ（'' = 未判定 / 'auto' / 'manual'）
    nsfw_source: str = ""
    #: 実際に投入した本文（英訳したときは訳したあとのもの）
    prompt: str = ""
    #: 英訳する前の原文（英訳していなければ空）
    source_prompt: str = ""
    #: 投入はできたが伝えたいこと（過去 Take の英訳失敗フォールバックなど）
    warning: str = ""
    #: この Take を作ったあとに脚本や素材が変わった（保存はせず読み取りで導出）
    stale: bool = False
    #: stale と判断した理由（日本語。stale が False なら空）
    stale_reasons: list[str] = Field(default_factory=list)


class StudioPromptReference(BaseModel):
    """投入プレビューに出す参照素材 1 件（r2v のときだけ入る）。"""

    name: str
    kind: StudioAssetKind = "image"
    #: 本文でこの素材を指すタグ（``<Picture 1>`` / ``<Video 1>`` / ``<Audio 1>``）
    tag: str = ""
    #: 添付されるファイル（``assets/`` からの相対パス）
    path: str = ""


class StudioShotPreview(BaseModel):
    """GET /api/studio/shots/{id}/prompt-preview: **投入される最終形**。

    生成（:func:`app.studio.render_shot`）と同じ組み立てを通した結果で、Grok の
    英訳だけは走らせない（遅く、課金枠を食うため）。英訳が入るかどうかは
    ``will_translate`` で伝える（使える ``english_prompt`` があれば False）。
    組み立てられない Shot はエラーではなく ``error`` に理由を入れて 200 で返す
    （プレビューで気づけるように）。
    """

    shot_id: str
    #: 投入されるワークフロー（組み立てられなかったときは強制指定か None）
    workflow: str | None = None
    #: そのワークフローになる理由（日本語）
    workflow_reason: str = ""
    #: 実際に投入される本文（公式フィールドと除外文まで込み）
    prompt: str = ""
    #: 参照として添付される素材（r2v のときだけ）
    references: list[StudioPromptReference] = Field(default_factory=list)
    #: 開始フレームに使われるファイル（i2v のときだけ）
    start_frame: str | None = None
    #: プロジェクトの設定（日本語まじりなら投入時に英訳する）
    auto_translate: bool = False
    #: 使える英語キャッシュが無く、``auto_translate`` かつ日本語を含むときだけ True
    will_translate: bool = False
    #: 保存済みの英語（古くても出す）
    english_prompt: str = ""
    #: 英語はあるが ``english_source`` が今の組み立てと一致しない
    english_stale: bool = False
    #: 英訳の進行（``''`` / ``translating`` / ``failed``）
    english_status: str = ""
    #: 英訳失敗の理由（日本語。成功時・未実施は空）
    english_error: str = ""
    #: プロジェクトの設定（引き継ぎを Motion Context で行う = ラテント連続性）
    latent_continuity: bool = False
    #: プロジェクトの設定（動画生成の品質）
    quality: StudioVideoQuality = "normal"
    #: ``quality`` が実際に効いたか（False = 素へフォールバックした。理由は
    #: ``workflow_reason`` の末尾に入る）
    quality_applied: bool = False
    #: ラテント連続性で引き継ぐ直前カットの動画（使わないときは None）
    context_video: str | None = None
    #: 同じく、引き継ぎ元の AV ラテント（ComfyUI 側のパス）
    context_latent: str | None = None
    #: 組み立てられなかった理由（日本語。空なら問題なし）
    error: str = ""


class StudioCapabilities(BaseModel):
    """GET /api/studio/capabilities: いまの接続先でスタジオの追加機能が使えるか。

    画面はこれを見てトグルを出し分ける（使えない接続先でオンにさせない）。
    """

    #: ラテント連続性（``MiniMaxH3MotionContext`` 系のカスタムノードが揃っている）
    latent_continuity: bool = False
    #: 確かめられなかった理由（日本語。空なら判定できている）
    error: str = ""


class StudioProjectDetail(StudioProject):
    """GET /api/studio/projects/{id}: 画面 1 枚を組み立てるのに要るもの一式。

    クエリの ``episode_id`` を付けると ``scenes`` / ``shots`` / ``takes`` は
    **その話のぶんだけ**になる（``assets`` と ``episodes`` は常に全件）。
    """

    assets: list[StudioAsset] = Field(default_factory=list)
    episodes: list[StudioEpisode] = Field(default_factory=list)
    #: プロジェクトの全シーン（話ごとではなく 1 本の配列。``episode_id`` で束ねる）
    scenes: list[StudioScene] = Field(default_factory=list)
    #: 話 -> 場 -> カットの順（未分類の Shot は末尾にまとまる）
    shots: list[StudioShot] = Field(default_factory=list)
    #: プロジェクトの全 Take（新しい順ではなく Shot ごとに古い順）
    takes: list[StudioTake] = Field(default_factory=list)


class StudioRevision(BaseModel):
    """リビジョン 1 件の見出し（GET .../revisions の 1 行）。"""

    seq: int
    actor: StudioRevisionActor = "user"
    #: 変更内容の短い説明（日本語）
    action: str = ""
    created_at: str


class StudioRevisionDetail(StudioRevision):
    """GET .../revisions/{seq}: 中身（そのときのプロジェクト全体）つき。"""

    #: ``{"project": {...}, "episodes": [...], "scenes": [...],
    #: "shots": [...], "assets": [...]}``。Take と実行状態は入らない
    snapshot: dict[str, Any] = Field(default_factory=dict)


class StudioDemoCreate(BaseModel):
    """POST /api/studio/demo body。"""

    #: 作りたいデモの作品コード（:mod:`app.studio_demo` の ``DEMO_PROJECTS``）
    code: str


#: ジョブのどの出力を素材にするか（:data:`app.library.SOURCES` のキー）
StudioJobSource = Literal["image", "last_frame", "video", "audio"]


class StudioAssetFromJob(StudioAssetCreate):
    """POST /api/v1/projects/{id}/assets/from-job body。

    生成済みのジョブの出力を World Bible の素材にする。``kind`` と ``path`` は
    ``source`` が選んだ出力から決まるので、書いても無視される。
    """

    #: 出力を取ってくるジョブ
    job_id: str
    #: そのジョブのどの出力か
    source: StudioJobSource = "image"


# --------------------------------------------------------------------------
# 編集タブ（タイムライン -> トラック -> クリップ -> 書き出し）
# --------------------------------------------------------------------------
#
# 焼き上がった Take を並べ直して 1 本の動画にするための EDL。生成（Shot ->
# Take）とは別の面で、タイムラインはソースの行を**参照するだけ**（元が消えても
# 並びは残り、読み取りで「メディア欠落」= ``missing`` として見せる）。

#: トラックの種別（``video`` の V1 / ``audio`` の A1… / ``subtitle`` の T1）
TimelineTrackKind = Literal["video", "audio", "subtitle"]

#: クリップのソース。``take`` は制作タブのテイク、``library`` / ``job`` /
#: ``asset_file`` は素材ビンから足したもの、``image`` は静止画、``text`` は
#: テロップ、``gap`` は隙間（ソースを持たない）
TimelineClipSource = Literal[
    "take", "asset_file", "library", "job", "image", "text", "gap"
]

#: 繋ぎの種別（ffmpeg の ``xfade`` にマップする。:data:`app.timeline_export.TRANSITIONS`）
TimelineTransitionKind = Literal[
    "crossfade",
    "fadeblack",
    "fadewhite",
    "wipeleft",
    "wiperight",
    "slideleft",
    "slideright",
    "circleopen",
    "pixelize",
]

#: 書き出しのプリセット（``timeline`` = タイムラインの規格そのまま）
TimelineExportPreset = Literal["timeline", "1080p", "vertical", "720p"]

#: 縦横比が変わるときの収め方（黒帯 / 中央を切り出す）
TimelineExportFit = Literal["pad", "crop"]

#: 素材ビンに出るものの種別
TimelineMediaKind = Literal["video", "audio", "image"]

#: 書き出し 1 回の状態（ジョブと違って外部バックエンドは無いので 4 つだけ）
TimelineExportStatus = Literal["queued", "running", "done", "failed"]


class StudioTimeline(BaseModel):
    """1 本のタイムライン（書き出しの規格を持つ EDL の入れ物）。"""

    id: str
    project_id: str
    #: どの話を組んだものか（``None`` = 作品まるごと）
    episode_id: str | None = None
    name: str = ""
    #: 書き出しの規格。クリップはここへ揃えて連結される
    fps: float = 24.0
    width: int = 1280
    height: int = 720
    created_at: str
    updated_at: str


class StudioTimelineCreate(BaseModel):
    """POST /api/studio/projects/{id}/timelines body。

    ``episode_id`` を送ると**自動配置つきの初期化**になる: その話のカットを
    場 -> カット順に走査し、採用 Take の動画があるものを V1 へ隙間なく並べる。
    """

    episode_id: str | None = None
    #: 省略すると話の見出し（または作品名）から決まる
    name: str = ""
    fps: float | None = None
    width: int | None = None
    height: int | None = None


class StudioTimelineUpdate(BaseModel):
    """PATCH /api/studio/timelines/{id} body（指定した項目だけ変える）。"""

    name: str | None = None
    fps: float | None = None
    width: int | None = None
    height: int | None = None


class TimelineClip(BaseModel):
    """トラックに置かれたクリップ 1 つ（ソース解決済み）。"""

    id: str
    track_id: str
    timeline_id: str
    #: タイムライン上の開始位置（ミリ秒）
    start_ms: int = 0
    #: 尺（ミリ秒）。``(out_ms - in_ms) / speed`` と一致する
    duration_ms: int = 0
    source_kind: TimelineClipSource = "take"
    #: 上の種別の中での id（``gap`` / ``text`` は None）
    source_id: str | None = None
    #: ソースの中の切り出し位置（ミリ秒）
    in_ms: int = 0
    out_ms: int = 0
    gain_db: float = 0.0
    fade_in_ms: int = 0
    fade_out_ms: int = 0
    #: **前の**クリップとの繋ぎ（``None`` = カット）。オーバーラップ方式なので、
    #: 繋ぎが付くとこのクリップはその分だけ前へ食い込む
    transition_kind: str | None = None
    transition_ms: int = 0
    #: ``text`` クリップの中身（他の種別では None）。``{"text": …, "style": {…}}``
    text_payload: dict[str, Any] | None = None
    #: 再生速度（1.0 = 等速。映像クリップだけ 1 以外を取れる）
    speed: float = 1.0
    sort_order: int = 0
    # --- ここから下は読み取りのたびに解決する（DB には持たない） -------------
    #: 再生できる URL（``/outputs/…`` / ``/library/…`` / ``/assets/…``）。
    #: 音声・静止画のクリップもここに入る。解決できなければ None
    video_url: str | None = None
    #: ソースそのものの長さ（ミリ秒）。分からなければ None
    source_duration_ms: int | None = None
    #: ソースの実ファイルが無い（元の Take やジョブが消えた / 失敗した）
    missing: bool = False
    #: 画面に出す見出し（Take なら「第 1 話 / 場 1 / #2」のようなカットの位置）
    label: str = ""


class TimelineTrack(BaseModel):
    """トラック 1 本（クリップ込み）。"""

    id: str
    timeline_id: str
    kind: TimelineTrackKind = "video"
    name: str = ""
    sort_order: int = 0
    muted: bool = False
    locked: bool = False
    clips: list[TimelineClip] = Field(default_factory=list)


class StudioTimelineDetail(StudioTimeline):
    """GET /api/studio/timelines/{id}: トラックとクリップ込みのフル EDL。"""

    tracks: list[TimelineTrack] = Field(default_factory=list)
    #: 一番後ろのクリップの終わり（ミリ秒）
    duration_ms: int = 0


class TimelineClipInput(BaseModel):
    """PUT /api/studio/timelines/{id}/clips の 1 件。

    ``id`` は送れば引き継ぎ、省略すれば新しく振る（画面側で分割した直後の
    クリップなど）。解決済みの項目（``video_url`` 等）は送っても無視される。
    """

    id: str | None = None
    track_id: str
    start_ms: int = 0
    duration_ms: int = 0
    source_kind: TimelineClipSource = "take"
    source_id: str | None = None
    in_ms: int = 0
    out_ms: int = 0
    gain_db: float = 0.0
    fade_in_ms: int = 0
    fade_out_ms: int = 0
    transition_kind: str | None = None
    transition_ms: int = 0
    text_payload: dict[str, Any] | None = None
    speed: float = 1.0


class TimelineClipsUpdate(BaseModel):
    """PUT /api/studio/timelines/{id}/clips body（クリップ全置換）。"""

    clips: list[TimelineClipInput] = Field(default_factory=list)


class TimelineExport(BaseModel):
    """書き出し 1 回（``outputs/exports/{id}/final.mp4``）。"""

    id: str
    timeline_id: str
    status: TimelineExportStatus = "queued"
    #: 0.0〜1.0（ffmpeg の ``-progress`` から出す目安）
    progress: float = 0.0
    params: dict[str, Any] = Field(default_factory=dict)
    output_path: str | None = None
    #: ``/outputs/…`` の配信 URL（まだ無ければ None）
    output_url: str | None = None
    error: str | None = None
    created_at: str
    finished_at: str | None = None


class TimelineExportRequest(BaseModel):
    """POST /api/studio/timelines/{id}/export body（すべて任意の上書き）。"""

    #: 送らなければタイムラインの規格のまま焼く
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    #: 解像度のプリセット（``width`` / ``height`` を直接送ればそちらが勝つ）
    preset: TimelineExportPreset = "timeline"
    #: 縦横比が変わるときの収め方
    fit: TimelineExportFit = "pad"
    #: ラウドネス正規化（-14 LUFS / TP -1.5 dB）を掛けるか
    loudnorm: bool = True


class TimelineExportSave(BaseModel):
    """POST /api/studio/exports/{id}/save-to-library body。"""

    #: ライブラリでの表示名（省略するとタイムライン名から決まる）
    name: str = ""


# --------------------------------------------------------------------------
# トラックの出し入れ（フェーズ 2: 音声 A1… と字幕 T1）
# --------------------------------------------------------------------------

class TimelineTrackCreate(BaseModel):
    """POST /api/studio/timelines/{id}/tracks body。"""

    #: ``video`` は V1 が正なので足せない（400）
    kind: TimelineTrackKind = "audio"
    #: 省略すると種別ごとの連番（``A2`` / ``T1``）
    name: str = ""


class TimelineTrackUpdate(BaseModel):
    """PATCH /api/studio/timelines/{id}/tracks/{track_id} body。"""

    name: str | None = None
    muted: bool | None = None
    locked: bool | None = None


# --------------------------------------------------------------------------
# 素材ビン（タイムラインへ足せるもの）
# --------------------------------------------------------------------------

class TimelineMediaItem(BaseModel):
    """素材ビンの 1 件（タイムラインに置ける素材）。"""

    #: クリップにしたときの ``source_kind``
    source_kind: TimelineClipSource
    #: その種別の中での id（``source_id`` にそのまま入る）
    source_id: str
    #: 映像 / 音声 / 静止画のどれか（置けるトラックが決まる）
    media_kind: TimelineMediaKind
    name: str = ""
    #: 出どころの説明（「ライブラリ」「素材 / 声」など）
    origin: str = ""
    #: 配信 URL（試聴・サムネイル用）
    url: str | None = None
    #: 素材そのものの長さ（ミリ秒。静止画と読めなかったものは None）
    duration_ms: int | None = None
    created_at: str = ""


class TimelineMediaPage(BaseModel):
    """GET /api/studio/projects/{id}/media: 素材ビンの 1 ページ。"""

    items: list[TimelineMediaItem] = Field(default_factory=list)
    total: int = 0
    limit: int = 50
    offset: int = 0


# --------------------------------------------------------------------------
# 台詞からのテロップ生成
# --------------------------------------------------------------------------

class TimelineSubtitleRequest(BaseModel):
    """POST /api/studio/timelines/{id}/generate-subtitles body。

    V1 のクリップの元カット（Take -> Shot）の台詞を、そのクリップの区間へ
    割り付ける。字幕トラックの既存クリップは**置き換える**（画面側で確認する）。
    """

    #: 書き込む字幕トラック（省略すると T1。無ければ作る）
    track_id: str | None = None


# --------------------------------------------------------------------------
# 脚本との差分（作ったあとに脚本が動いた分）
# --------------------------------------------------------------------------

class TimelineSyncAdded(BaseModel):
    """タイムラインを作ったあとに増えたカット（採用テイクの動画がある）。"""

    shot_id: str
    take_id: str
    label: str = ""
    duration_ms: int = 0


class TimelineSyncRetaken(BaseModel):
    """クリップが古いテイクを指している（カットの採用が変わった）。"""

    clip_id: str
    shot_id: str
    old_take_id: str
    new_take_id: str
    label: str = ""
    #: 新しいテイクの長さ（切り出しはここへ丸める）
    duration_ms: int | None = None


class TimelineSyncRemoved(BaseModel):
    """元のカットが消えた（または採用が外れた）クリップ。"""

    clip_id: str
    label: str = ""
    reason: str = ""


class TimelineSyncPreview(BaseModel):
    """GET /api/studio/timelines/{id}/sync-preview: 反映できる差分。"""

    added: list[TimelineSyncAdded] = Field(default_factory=list)
    retaken: list[TimelineSyncRetaken] = Field(default_factory=list)
    removed: list[TimelineSyncRemoved] = Field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not (self.added or self.retaken or self.removed)


class TimelineSyncRequest(BaseModel):
    """POST /api/studio/timelines/{id}/sync body（選んだ項目だけ反映）。"""

    #: V1 の末尾へ足すカット（``TimelineSyncAdded.shot_id``）
    add_shot_ids: list[str] = Field(default_factory=list)
    #: 新しいテイクへ差し替えるクリップ（``TimelineSyncRetaken.clip_id``）
    retake_clip_ids: list[str] = Field(default_factory=list)
    #: 消して詰めるクリップ（``TimelineSyncRemoved.clip_id``）
    remove_clip_ids: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------
# メディア欠落のリカバリ
# --------------------------------------------------------------------------

class TimelineMissingCandidate(BaseModel):
    """欠落クリップに充てられる同じカットの別テイク。"""

    take_id: str
    status: str = ""
    created_at: str = ""
    duration_ms: int | None = None


class TimelineMissingClip(BaseModel):
    """実ファイルが見つからないクリップ 1 つと、その直し方。"""

    clip_id: str
    label: str = ""
    source_kind: TimelineClipSource = "take"
    source_id: str | None = None
    #: 同じカットの、動画が実在する別テイク（新しい順）
    candidates: list[TimelineMissingCandidate] = Field(default_factory=list)


class TimelineMissingReport(BaseModel):
    """GET /api/studio/timelines/{id}/missing: 欠落クリップの一覧。"""

    clips: list[TimelineMissingClip] = Field(default_factory=list)


class TimelineMissingFix(BaseModel):
    """POST /api/studio/timelines/{id}/missing/resolve body。"""

    #: ``{クリップ id: 差し替え先の take id}``
    replace: dict[str, str] = Field(default_factory=dict)
    #: 消してしまうクリップ（映像トラックなら後ろを詰める）
    drop_clip_ids: list[str] = Field(default_factory=list)
    #: 残っている欠落クリップを全部消す（``drop_clip_ids`` と併用できる）
    drop_all: bool = False


class TimelineExportProgress(BaseModel):
    """WS /api/ws で流す書き出しの進捗（``type: "timeline_export"``）。"""

    type: Literal["timeline_export"] = "timeline_export"
    export_id: str
    timeline_id: str
    status: TimelineExportStatus
    progress: float = 0.0
    #: 完了したときだけ入る配信 URL
    output_url: str | None = None
    error: str | None = None


# --------------------------------------------------------------------------
# 一括投入（外部 API の POST /api/v1/stories。docs/EXTERNAL-API.md §2）
# --------------------------------------------------------------------------
#
# 話 1 本ぶんの脚本（話 -> 場 -> Shot）を 1 リクエストで納品するための形。
# 個別 CRUD の組み合わせでも同じことはできるが、こちらは 1 トランザクションで
# 「全部作れたか、全く作らなかったか」の二択にする。

class StoryScene(StudioSceneCreate):
    """一括投入の中の 1 場（``shots`` を入れ子で持つ）。"""

    #: この場に並べるカット（項目は :class:`StudioShotCreate` と同じ。
    #: ``scene_id`` は入れ子の位置で決まるので書いても無視される）
    shots: list[StudioShotCreate] = Field(default_factory=list)


class StoryCreate(BaseModel):
    """POST /api/v1/stories body。

    対象のプロジェクトは ``project_id`` か ``project_code``（作品コード）の
    どちらかで指定する（両方あれば ``project_id`` が優先）。
    """

    project_id: str = ""
    project_code: str = ""
    episode: StudioEpisodeCreate = Field(default_factory=StudioEpisodeCreate)
    scenes: list[StoryScene] = Field(default_factory=list)
    #: true なら作成をコミットしたあと、1 カットずつ生成を投入する
    render: bool = False


class StoryShotResult(BaseModel):
    """作られたカット 1 件（``render`` したなら投入の成否つき）。"""

    id: str
    title: str = ""
    #: 投入できた Take（``render`` が false / 投入に失敗したときは None）
    take_id: str | None = None
    job_id: str | None = None
    #: 投入できなかった理由（日本語。空なら問題なし）
    error: str = ""


class StorySceneResult(BaseModel):
    """作られた場 1 件。"""

    id: str
    title: str = ""
    shots: list[StoryShotResult] = Field(default_factory=list)


class StoryResult(BaseModel):
    """POST /api/v1/stories のレスポンス（201）。"""

    project_id: str
    episode_id: str
    scenes: list[StorySceneResult] = Field(default_factory=list)
    #: 作った順の Shot の id（場をまたいだ通し）
    shot_ids: list[str] = Field(default_factory=list)
    #: 投入できた Take の id（``render`` が false なら空）
    take_ids: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------
# 脚本ドラフト作成ガイド（外部 API の GET /api/v1/prompt-guide）
# --------------------------------------------------------------------------
#
# 外部のエージェントが上の一括投入に渡す脚本を書くための手引きを、API で配る。
# 本文は :func:`app.drafting_guide.build_drafting_guide` が既存の定数から
# 組み立てる（静的なコピーは持たない）。

class DraftingGuideLimits(BaseModel):
    """ガイド本文と同じ数値を機械可読で渡す（本文を読み解かずに検証できる）。"""

    #: カットの尺として API が受け付ける範囲（``app.studio.SHOT_DURATION_MIN/MAX``）
    shot_duration_min_seconds: float
    shot_duration_max_seconds: float
    #: 実用上おすすめの尺（``"4-15"`` のような範囲の表記）
    shot_duration_recommended: str
    #: 1 カットに添付できる参照素材の件数（``app.workflows.MINIMAX_H3_REFERENCE_*``）
    reference_images_max: int
    reference_videos_max: int
    reference_audios_max: int


class DraftingGuide(BaseModel):
    """GET /api/v1/prompt-guide のレスポンス。"""

    #: 中身を変えたら上がる日付ベースの版（受け取り側のキャッシュ判定用）
    guide_version: str
    #: LLM のプロンプトへそのまま貼れる日本語 Markdown
    markdown: str
    limits: DraftingGuideLimits


class PromptExample(BaseModel):
    """GET /api/v1/prompt-examples が返す実例 1 件（:mod:`app.h3_examples`）。"""

    #: ``H3-E4`` のような安定した id
    id: str
    #: ``t2v`` / ``i2v`` / ``fl2v`` / ``l2v`` / ``r2v`` / ``edit``
    mode: str
    #: 題材のタグ（``cinematic`` / ``dialogue`` / ``ui-text`` …）
    categories: list[str]
    #: 一行説明（英語）
    summary: str
    #: ``canonical``（公式形式の完成例）/ ``inspiration``（rewrite 前の生入力）
    tier: str
    #: 出典
    source: str
    #: 使いどころの補足（無ければ空文字）
    note: str = ""
    #: 例の本文。索引だけを返すとき（絞り込み無し）は ``null``
    body: str | None = None


class PromptExamples(BaseModel):
    """GET /api/v1/prompt-examples のレスポンス。"""

    #: ガイド本文と同じ版（:data:`app.drafting_guide.GUIDE_VERSION`）
    guide_version: str
    #: 実際に例が存在するモード / カテゴリ（絞り込みに使える値）
    modes: list[str]
    categories: list[str]
    #: 返した件数
    total: int
    examples: list[PromptExample]


# --------------------------------------------------------------------------
# キャンバス（スタジオの別ビュー）
# --------------------------------------------------------------------------
#
# 1 枚のカード = スタジオの 1 エンティティ。カードが持つのは「どの行か」と
# 「どこに置いてあるか」だけで、中身は studio_* が唯一の正。対応する
# エンティティが無い text / model のカードだけ、中身を `data` に持つ。

#: カードの種別。前半 5 つは素材（``studio_assets``。分類は
#: :data:`app.canvas.CARD_CATEGORIES` で対応づける）、``scene`` / ``shot`` /
#: ``media`` はそれぞれ場・Shot・Take、``text`` / ``model`` はキャンバス専用。
CanvasCardKind = Literal[
    "character", "location", "object", "style", "reference",
    "scene", "shot", "media", "text", "model",
]
CanvasRole = Literal["user", "assistant", "event"]

#: model カードの生成対象（既存 WorkflowKind と同じ語彙）
CanvasModelTarget = Literal["image", "video", "audio"]


class CanvasTextData(BaseModel):
    """text カードの中身（ただの覚え書き）。"""

    model_config = ConfigDict(extra="forbid")
    body: str = ""


class CanvasModelParams(BaseModel):
    """model カードに書ける生成パラメータ。"""

    model_config = ConfigDict(extra="forbid")
    aspect_ratio: str = "4:3 (Standard)"
    megapixels: float = 1.0
    #: 動画の尺 / 音声の長さ（秒）
    duration: float = 10.0
    fps: int = 25
    loras: list[LoraRef] = Field(default_factory=list)
    video_loras: list[LoraRef] = Field(default_factory=list)
    #: 空 = JobCreate の既定値に任せる
    negative_prompt: str = ""
    selects: dict[str, str] = Field(default_factory=dict)
    model_overrides: dict[str, str] = Field(default_factory=dict)


class CanvasModelData(BaseModel):
    """model カードの中身（「何用の生成設定か」を置いておくカード）。"""

    model_config = ConfigDict(extra="forbid")
    target: CanvasModelTarget = "image"
    #: 既存カタログのワークフロー ID（空 = まだ選んでいない）
    workflow: str = ""
    params: CanvasModelParams = Field(default_factory=CanvasModelParams)
    note: str = ""


#: キャンバス専用の kind -> `data` の検証モデル。ここに無い kind は
#: スタジオ側の行が中身を持つので、`data` は常に空。
CARD_DATA_MODELS: dict[str, type[BaseModel]] = {
    "text": CanvasTextData,
    "model": CanvasModelData,
}


def validate_card_data(kind: str, data: dict[str, Any]) -> dict[str, Any]:
    """kind のスキーマで ``data`` を検証して正規化する。

    参照カード（スタジオのエンティティを指すもの）は中身を持たないので、何を
    送られても空の dict になる。
    """
    model = CARD_DATA_MODELS.get(kind)
    return model(**data).model_dump() if model else {}


class CanvasViewport(BaseModel):
    """キャンバスの表示位置（タブ = 話ごとに 1 つ）。"""

    x: float = 0.0
    y: float = 0.0
    zoom: float = Field(default=1.0, gt=0)


class CanvasCard(BaseModel):
    """キャンバスに置いた 1 枚。"""

    id: str
    project_id: str
    kind: CanvasCardKind
    #: 参照しているスタジオ側の行（text / model は None）
    entity_id: str | None = None
    #: 置いてあるタブ（None = 作品共通）。**参照カードでは常に None** で、
    #: そのカードがどのタブに出るかはスタジオの所属（場 -> 話）から導く
    #: （:func:`app.canvas.card_episode`）。使うのは text / model カードだけ。
    episode_id: str | None = None
    #: キャンバス専用 kind の中身（参照カードでは空）
    data: dict[str, Any] = Field(default_factory=dict)
    x: float = 0.0
    y: float = 0.0
    w: float = 320.0
    h: float = 220.0
    z: int = 0
    created_at: str
    updated_at: str


class CanvasCardCreate(BaseModel):
    """POST /api/canvas/projects/{id}/cards body。

    作るのは**新しいもの**だけ: 参照カードは対応するエンティティも一緒に作り、
    text / model はキャンバス専用のカードを作る。既にあるエンティティは
    :func:`app.canvas._mirror` が自動で並べるので、ここでは指定しない
    （media カードは Take が生まれたときにだけできる）。
    エンティティの中身はスタジオの API で編集する。
    """

    kind: CanvasCardKind
    #: 新しく作るエンティティの名前（素材）またはタイトル（場 / Shot）。
    #: 素材で省くと ``character_1`` のような名前が自動で付く
    title: str = ""
    #: 新しく作る素材の種別（image / video / audio）
    asset_kind: StudioAssetKind = "image"
    #: shot カードを作るとき、どの場に入れるか（None = 未分類）
    scene_id: str | None = None
    #: scene カードならどの話に入れるか（None = 先頭の話。無ければ作る）、
    #: text / model カードならどのタブに置くか（None = 作品共通）。
    #: 素材 / shot カードでは見ない（出るタブはスタジオの所属で決まる）
    episode_id: str | None = None
    #: text / model カードの中身。素材カードでは新しい素材の `profile`
    #: （:data:`ASSET_PROFILE_MODELS`）として渡る。それ以外の kind では無視
    data: dict[str, Any] = Field(default_factory=dict)
    x: float = 0.0
    y: float = 0.0
    w: float = 320.0
    h: float = 220.0


class CanvasCardUpdate(BaseModel):
    """PATCH /api/canvas/cards/{id} body（指定した項目だけ変える）。"""

    data: dict[str, Any] | None = None
    x: float | None = None
    y: float | None = None
    w: float | None = None
    h: float | None = None
    z: int | None = None


class CanvasCardPosition(BaseModel):
    """PUT /api/canvas/cards/{id}/position body（置き場所だけ動かす）。"""

    x: float
    y: float
    w: float | None = None
    h: float | None = None
    z: int | None = None


class CanvasMessage(BaseModel):
    """キャンバスのチャット 1 発言。"""

    id: str
    project_id: str
    session_id: str = ""
    ts: str
    role: CanvasRole
    content: str
    #: event の種別（``action_result`` など。会話なら None）
    kind: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)


class CanvasMessageCreate(BaseModel):
    """POST /api/canvas/projects/{id}/messages body。"""

    role: CanvasRole = "user"
    content: str
    kind: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)


#: 添付ファイルの種別（プレビューの出し分けにだけ使う）
CanvasAttachmentKind = Literal["image", "video", "audio", "document"]


class CanvasAttachment(BaseModel):
    """POST /api/canvas/projects/{id}/attachments のレスポンス。

    置き場所はキャンバスの作業ディレクトリの ``attachments/`` で、エージェント
    （grok CLI）はそこを作業根として動くので、``path`` をそのまま開ける
    （エージェントモードの添付と同じ流儀。:mod:`app.agent_store`）。
    """

    #: 元のファイル名（画面に出す名前）
    name: str
    #: workdir 相対パス（``attachments/<file>``）
    path: str
    #: 保存先の絶対パス（プロンプトにはこちらを書く）
    abs_path: str = ""
    kind: CanvasAttachmentKind = "document"


class CanvasAgentStart(CanvasMessageCreate):
    """POST /api/canvas/projects/{id}/agent body。

    発言に「いま開いているタブ」を添える（エージェントはそのタブの盤面を見て
    考え、置いた text / model カードもそのタブに載る）。
    """

    #: 開いているタブ（None / 'common' = 作品共通）
    episode_id: str | None = None
    #: 会話セッション（省略時は最新。無ければ作る）
    session_id: str | None = None
    #: 添付ファイルの workdir 相対パス（``attachments/<file>``）。本文が空でも
    #: 添付だけで送れる
    attachments: list[str] = Field(default_factory=list)


class CanvasBoard(BaseModel):
    """GET /api/canvas/projects/{id}: キャンバス **1 タブ**ぶん。

    カードの中身はスタジオ側（``GET /api/studio/projects/{id}``）にあるので、
    ここに入るのは置き場所と会話だけ。``cards`` と ``viewport`` は開いている
    タブのもので、会話（``messages``）は指定した（または最新の）セッション。
    """

    project_id: str
    #: 開いているタブ（None = 作品共通）
    episode_id: str | None = None
    #: 会話セッション（省略時は最新）
    session_id: str | None = None
    viewport: CanvasViewport = Field(default_factory=CanvasViewport)
    cards: list[CanvasCard] = Field(default_factory=list)
    messages: list[CanvasMessage] = Field(default_factory=list)


class CanvasChatSession(BaseModel):
    """キャンバスの会話セッション 1 本。"""

    id: str
    project_id: str
    title: str = ""
    created_at: str
    updated_at: str
    preview: str = ""
    grok_session_id: str = Field(default="", exclude=True)
    grok_cwd: str = Field(default="", exclude=True)
    snapshot_key: str = Field(default="", exclude=True)


class CanvasSessionCreate(BaseModel):
    title: str = ""


class CanvasSessionUpdate(BaseModel):
    title: str | None = None


class CanvasSessionSearchHit(BaseModel):
    session_id: str
    title: str = ""
    snippet: str = ""
    ts: str = ""


class CanvasAgentState(BaseModel):
    """キャンバスのチャットから走らせたエージェントの状態。"""

    project_id: str
    running: bool = False
    #: 実行中の活動テキスト（「ツール実行中: …」など）。無ければ None
    activity: str | None = None
    session_id: str | None = None


class CanvasAgentRun(CanvasAgentState):
    """POST /api/canvas/projects/{id}/agent の応答（保存した発言つき）。"""

    message: CanvasMessage


class CanvasProgress(BaseModel):
    """WS /api/ws で流すキャンバスの実行イベント（``type: "canvas"``）。"""

    type: Literal["canvas"] = "canvas"
    project_id: str
    running: bool
    activity: str | None = None
    session_id: str | None = None
    #: 会話に足された 1 件（発言・エージェントの応答・ツール実行イベント）
    message: CanvasMessage | None = None
