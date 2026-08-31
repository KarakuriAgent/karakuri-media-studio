// Mirrors backend/app/models.py

/** `audio` は独立モード: 音声ワークフローを 1 本だけ走らせ、画像→動画の連結
 *  （full）とは一切繋がらない。`remotion` も同じく独立で、構築済み Remotion
 *  プロジェクトに mp4 を書かせるだけ（生成フォームからは選べず、外部 API か
 *  ら投入されて履歴に出る）。 */
export type JobMode = 'full' | 'i2v' | 'image_only' | 'audio' | 'remotion'
export type JobStatus =
  | 'queued'
  | 'prompting'
  | 'running'
  | 'done'
  | 'failed'
  | 'canceled'

/**
 * ComfyUI の接続先プロファイル（SPEC §5）。設定には 3 つ分の接続情報を持ち、
 * `Settings.comfy_target` が「今どれを使うか」を決める。
 */
export type ComfyTarget = 'local' | 'runpod' | 'comfy_cloud'

/**
 * LLM を回すコーディング CLI（SPEC §4.1）。チャット・スタジオ会話・
 * 英訳・自動タグがこの選択に従う。
 * Grok Imagine（画像生成）だけは常に grok。
 */
export type LlmCli = 'grok' | 'claude' | 'codex' | 'cursor'

export interface Settings {
  /** 現在の接続先（生成フォームのプルダウンがこれだけを書き換える）。 */
  comfy_target: ComfyTarget
  /** ローカル（同じマシン / LAN）の ComfyUI。API キーは使わない。 */
  local_comfy_url: string
  /** RunPod の Pod 上の ComfyUI（Cloudflare Tunnel の固定ホスト名）。 */
  runpod_comfy_url: string
  /** Pod の ComfyUI を認証付きで公開している場合のキー（不要なら空）。 */
  runpod_comfy_api_key: string
  /** ComfyCloud の API キー（URL は `https://cloud.comfy.org` 固定）。 */
  comfy_cloud_api_key: string
  /** どの CLI で LLM を回すか（既定 'grok'）。 */
  agent_cli: LlmCli
  /** CLI ごとのコマンド上書き（空 = 既定。`<cli>_oneshot` も置ける）。 */
  agent_cli_commands: Record<string, string>
  /** CLI ごとのモデル上書き（空 = CLI の既定）。grok は `grok_model`。 */
  agent_cli_models: Record<string, string>
  grok_command: string
  grok_model: string
  /** チャット・英訳が grok CLI を回すときの作業ディレクトリ（空 = 既定）。 */
  grok_workdir: string
  /**
   * Grok Imagine（画像生成・編集、SPEC §5.2）の作業ディレクトリと制限時間。
   * コマンド名は `grok_command` と共有する。
   */
  grok_media_workdir: string
  grok_media_timeout: number
  /**
   * 構築済み Remotion プロジェクト（Node のリポジトリ）のパス。
   * 空 = Remotion 連携を無効（一覧も投入も 400）。
   */
  remotion_project_dir: string
  /**
   * 接続先ごとのモデル指定（SPEC §3.3 / §5）。
   * `{"local": {"<workflow_id>/<node_id>.<field>": "file.safetensors"}, …}` で、
   * テンプレート既定と違うものだけが入る。設定ページは `GET/PUT /api/models`
   * （`?target=`）を使うので、ここを直接触るのは移行の確認用。
   */
  model_overrides: Partial<Record<ComfyTarget, Record<string, string>>>
  /**
   * 同じキー形式の「そのスロットで選べるモデルファイル名」を接続先ごとに持つ。
   * 2 件以上あるスロットは生成フォームで実行時に選べる（SPEC §3.3）。
   */
  model_choices: Partial<Record<ComfyTarget, Record<string, string[]>>>
  /**
   * gated リポジトリ用の Hugging Face トークン（不足モデルのダウンロード、SPEC §3.3）。
   * 保存先の models ディレクトリは設定ではなく環境変数 `COMFY_MODELS_DIR` が決める。
   */
  hf_token: string
  civitai_api_key: string
  /** `{"<ファイル名>": "<ダウンロード URL>"}`（キーはファイル名なので行を跨いで共有）。 */
  model_download_urls: Record<string, string>
  /**
   * ComfyUI が RunPod の Pod にある構成での自動起動（SPEC §5.1）。接続先が
   * `runpod` で、かつ有効なとき、ジョブ投入の直前に `runpod_comfy_url` の疎通を
   * 確かめ、落ちていれば Pod を作って待つ。
   */
  runpod_enabled: boolean
  runpod_api_key: string
  runpod_template_id: string
  /** RunPod の gpuTypeId（例: `NVIDIA RTX PRO 6000 Blackwell Workstation Edition`）。 */
  runpod_gpu_type: string
  /** /workspace にマウントする Network Volume の ID。 */
  runpod_network_volume_id: string
  /**
   * 外部公開 API（`/api/v1`。docs/EXTERNAL-API.md）の共有キー。
   * 空 = 外部 API を丸ごと無効（404）。キーを入れることが有効化そのもの。
   */
  external_api_key: string
  /** 外部 API から積める未完了 Take の上限（0 = 無制限）。 */
  external_max_pending_takes: number
  /**
   * grok CLI に足す追加フラグ（ツール権限）。相談チャットや英訳の呼び出しに
   * そのまま渡る。**空にすると CLI のツールが丸ごと無効**になる。
   */
  agent_grok_args: string[]
  /**
   * LLM のターンを ACP（`grok agent stdio`）で回すか。ON だと実行中の
   * 活動（思考 / ツール実行）が UI に出る。OFF は従来のワンショット実行。
   */
  agent_use_acp: boolean
  /** grok CLI 1 回あたりの制限時間（秒）。0 = タイムアウトなし。 */
  agent_grok_timeout: number
  /**
   * 高速化トグルの既定値（SPEC §3.1）。宣言のある動画ワークフロー（`supports`
   * にそれぞれの名前があるもの）だけが読む。生成フォームのトグルがここを
   * 書き換えるので、次に開いたときも同じ状態で始まる。
   */
}

/**
 * GET /api/models/dir-status: models ディレクトリに書けるか（SPEC §3.3）。
 *
 * `configured` は環境変数 `COMFY_MODELS_DIR` が設定されているか。false なら
 * 機能ごと無効で、UI はダウンロード関連を一切出さない。
 */
export interface ModelsDirStatus {
  configured: boolean
  exists: boolean
  writable: boolean
  path: string
}

/** WS /api/ws のモデルダウンロード進捗（`total` は不明なら null）。 */
export interface ModelDownloadProgress {
  type: 'model_download'
  filename: string
  status: 'downloading' | 'done' | 'error'
  received: number
  total: number | null
  error: string | null
}

/** GET /api/models/downloads の 1 件（進捗＋保存先）。 */
export interface ModelDownload extends ModelDownloadProgress {
  subfolder: string
  url: string
  path: string
}

/** POST /api/models/download-all のレスポンス（不足モデルの一括取得）。 */
export interface ModelDownloadAllResult {
  /** 開始したダウンロード（進捗は WS の `model_download` で届く）。 */
  started: ModelDownload[]
  /** 未検出だが取得元 URL が無くて開始できなかったファイル名。 */
  missing_urls: string[]
  /** 開始できなかった理由（ファイル名 -> メッセージ）。 */
  errors: Record<string, string>
}

/** One configurable model file of a workflow template (GET /api/models). */
export interface ModelFieldState {
  key: string
  workflow_id: string
  workflow_label: string
  /** which stage the owning workflow belongs to (settings page grouping). */
  kind: 'image' | 'video' | 'audio'
  /** モデルファミリーとその表示名（設定ページの動画はこちらでまとめる）。 */
  family: string
  family_label: string
  node_id: string
  field: string
  class_type: string
  title: string
  default: string
  /** ダウンロードの既定の置き場所（models ディレクトリ相対。未知のローダーは空）。 */
  subfolder: string
  value: string
  overridden: boolean
  /** そのスロットで選べるモデルファイル名（設定ページで登録した候補）。 */
  choices: string[]
}

/**
 * 実行時に切り替えられるモデル 1 スロット（GET /api/options の model_slots）。
 *
 * `choices` は `default` を先頭に含む。候補が 2 件以上あるスロットだけが返る。
 */
export interface ModelSlot {
  key: string
  workflow_id: string
  workflow_label: string
  kind: 'image' | 'video' | 'audio'
  node_id: string
  field: string
  class_type: string
  label: string
  default: string
  choices: string[]
}

/** Which stage a registered LoRA belongs to (mirrors models.LoraTarget). */
export type LoraTarget = 'image' | 'video'

/** Image model family a LoRA / image workflow belongs to (workflows.py). */
export type ImageFamily = 'krea2' | 'anima' | 'z-image' | 'qwen-image'

export interface Lora {
  id: number
  display_name: string
  lora_name: string
  trigger_word: string
  default_strength: number
  default_audio: string | null
  sort_order: number
  /** 'image' = 画像ワークフロー / 'video' = 動画ワークフロー。 */
  target: LoraTarget
  /**
   * 学習元の画像モデルファミリー。同じ family の画像ワークフローでのみ使える。
   * target='video' の行では無視される。
   */
  family: string
  /** サンプル画像の URL（/assets/lora_samples/<id>/<file>）。専用APIで管理。 */
  sample_images: string[]
  /**
   * 置いてある接続先環境（SPEC §5）。`null` は「環境を問わず出す」で、接続先を
   * 分ける前に登録された行がこれになる。
   */
  comfy_target?: ComfyTarget | null
}

export type LoraPayload = Omit<Lora, 'id' | 'sample_images'>

export interface LoraRef {
  lora_name: string
  trigger_word: string
  strength: number
}

export interface Asset {
  name: string
  kind: 'audio' | 'image' | 'video'
  path: string
  url: string
  size: number
}

// ------------------------------------------------------------------ library
// SPEC §7.2 — 履歴とは別に取っておく素材の棚（backend/app/library.py）。

/** ライブラリに入れられる素材の種別。 */
export type LibraryKind = 'image' | 'video' | 'audio'

/** ジョブのどの出力を登録するか（ResultPane のタブと同じ区分）。 */
export type LibrarySource = 'image' | 'last_frame' | 'video' | 'audio'

/**
 * 素材の出どころ（`LibraryItem.source`）。
 *
 * ジョブの出力 4 種に加えて、アプリ内で合成したリファレンスシート（`'sheet'`、
 * SPEC §7.2）を取る。from-job で指定できるのは `LibrarySource` のほうだけ。
 */
export type LibraryOrigin = LibrarySource | 'sheet'

/** 素材の分類（棚の仕切り。1 件に 1 つだけ。null = 未分類）。 */
export type LibraryCategory = 'character' | 'background' | 'prop'

/**
 * カテゴリとして送れる値（SPEC §7.2）。
 *
 * API は「指定なし」（絞り込まない / 変更しない）と「未分類そのもの」を区別する
 * ので、未分類は値なしではなく `'none'` で送る（DB では NULL）。
 */
export type LibraryCategoryValue = LibraryCategory | 'none'

export interface LibraryItem {
  id: string
  created_at: string
  kind: LibraryKind
  /** 表示名（あとから変更できる）。 */
  name: string
  /** ファイルの絶対パス（ジョブの入力にはこれか `url` を指定できる）。 */
  path: string
  /** `/library/<kind>/<file>`（静的配信 URL）。 */
  url: string
  nsfw: boolean
  /** '' = 未判定 / 'auto' = 元ジョブから継承 / 'manual' = 手動指定。 */
  nsfw_source: string
  /** 生成物から登録した場合の元ジョブ id。 */
  source_job_id: string | null
  /**
   * 元ジョブのどの出力か（重複登録の判定に使う。アップロード・旧行は null）。
   * 合成したリファレンスシートは `'sheet'`（元ジョブを持たない）。
   */
  source: LibraryOrigin | null
  /** 分類タグ（検索・絞り込み用）。 */
  tags: string[]
  /** 素材の分類（null = 未分類。カラムを足す前の行も null）。 */
  category: LibraryCategory | null
}

/** GET /api/library のレスポンス（絞り込み結果の 1 ページ）。 */
export interface LibraryPage {
  items: LibraryItem[]
  /** 絞り込み条件に合う総件数（このページの件数ではない）。 */
  total: number
  limit: number
  offset: number
  /** ライブラリに登録されている全タグ（絞り込みの補完用）。 */
  tags: string[]
}

/** POST /api/library/sheet の body（リファレンスシートの合成、SPEC §7.2）。 */
export interface LibrarySheetRequest {
  /** 載せる素材の id（すべて image。**並び順に意味がある**）。 */
  item_ids: string[]
  /** 表示名（省略すると素材の名前から決まる）。 */
  name?: string
  /** シートの大きさ（省略すると 1280x720）。 */
  width?: number
  height?: number
}

/** GET /api/library のクエリ。 */
export interface LibraryQuery {
  kind?: LibraryKind
  /** 分類（省略 = 全件 / `'none'` = 未分類のみ）。 */
  category?: LibraryCategoryValue
  /** 名前・タグへの部分一致。 */
  q?: string
  /** タグの完全一致。 */
  tag?: string
  limit?: number
  offset?: number
}

/** Logical inputs a video workflow can require (mirrors workflows.InputName). */
export type WorkflowInput = 'image' | 'audio' | 'end_image' | 'video'

/**
 * 複数ファイルで渡す参照入力（mirrors workflows.MULTI_INPUT_FIELDS）。
 * 論理名とジョブのフィールド名は同じ。
 */
export type ReferenceInput =
  | 'reference_images'
  | 'reference_videos'
  | 'reference_audios'

/**
 * ショット割りの 1 ショット（mirrors models.MultiShot、SPEC §3.1）。
 * 平坦な値ではなく**構造化されたリスト**でジョブに載る。
 */
export interface MultiShot {
  prompt: string
  /** そのショットの尺（秒、整数）。 */
  duration: number
}

/** Elements の 1 要素（mirrors models.ElementInput、SPEC §3.1）。 */
export interface KlingElement {
  /** プロンプト中で `@要素名` として呼ぶ名前。 */
  name: string
  description: string
  /** 参照画像（枚数の範囲はワークフローの宣言による）。 */
  images: string[]
}

/** ショット割りの上限（mirrors models.MultiShotOption）。 */
export interface MultiShotLimits {
  max_shots: number
  min_duration: number
  max_duration: number
}

/** Elements の上限（mirrors models.ElementsOption）。 */
export interface ElementsLimits {
  max_elements: number
  min_images: number
  max_images: number
  /** `@要素名` 1 参照が消費する文字数。 */
  reference_chars: number
}

/**
 * ワークフローが宣言する選択式フィールド（GET /api/options）。
 *
 * 自由記述ではなく決まった選択肢で挙動が決まるワークフロー（踊りの種類など、
 * 踊りの種類・動きの大きさ・尺）用。宣言のないワークフローでは空配列。
 */
export interface WorkflowSelect {
  /** ジョブの `selects` のキー。 */
  name: string
  label: string
  choices: string[]
  /** 未指定のときに使われる値。 */
  default: string
  /** true なら「自動」を選べる（未指定なら入力から決まる）。 */
  auto: boolean
  hint: string
  /**
   * **表示だけ**の日本語ラベル（`選ぶ値 -> 画面に出す文字列`、SPEC §3.1）。
   *
   * 送る値は `choices` の生のまま。`decode_recommended` のようなノード由来の
   * enum を読める日本語に置き換えるためのもので、宣言の無い値（や宣言そのものが
   * 無い選択式）は生の値をそのまま出す。
   */
  choice_labels?: Record<string, string>
}

/** One selectable workflow template (GET /api/options). */
export interface WorkflowOption {
  id: string
  label: string
  /**
   * 生成フォームの「モデル → モード」2 段プルダウンの **2 段目**（モード）の
   * 表示名。1 段目に `family_label` が出るので、モデル名は入っていない。
   * 古いレスポンスには無いので、無ければ `label` を使う。
   */
  mode_label?: string
  /** 1 段目（モデル）の表示名。供給元の注記（外部 API など）つき。 */
  family_label?: string
  kind: 'image' | 'video' | 'audio'
  /** model family — image LoRAs of another family cannot be used with it. */
  family: string
  notes: string
  requires: WorkflowInput[]
  /**
   * 複数ファイルで渡せる参照入力（論理名 -> 件数の上限、SPEC §3.1）。
   * **参照専用のワークフロー**（`minimax_h3_r2v`）だけが宣言し、
   * そちらは開始フレームを受け取らない（`accepts_start_image` が false）。
   */
  multi_inputs?: Partial<Record<ReferenceInput, number>>
  /**
   * 選択式どうしの相関（名前 -> `[相手の名前, 相手に必要な値]`、SPEC §3.1）。
   * 例えば `duration` が `model` の値によってしか効かないとき、他のモデルでは
   * API が黙って無視するので、既定以外を選んだら送る前にエラーにする。
   */
  select_requires?: Record<string, [string, string] | string[]>
  /**
   * ショット割り / Elements の宣言（SPEC §3.1）。対応していないワークフロー
   * では null で、フォームはそのセクションを出さない。古いレスポンスには無い。
   */
  multi_shot?: MultiShotLimits | null
  elements?: ElementsLimits | null
  /** プロンプトの文字数上限（0 / 未定義 = 上限なし）。 */
  max_prompt_chars?: number
  supports: string[]
  accepts_start_image: boolean
  image_label: string
  /** 選択式フィールド（無いワークフローでは空）。 */
  selects: WorkflowSelect[]
  /** `video_prompt` が必須か（false なら任意）。 */
  prompt_required: boolean
  /** 動画用 LoRA を挿せるか（テンプレートに LoRA チェーンがあるか）。 */
  accepts_video_loras: boolean
  /** 実行エンジン（今は `comfyui` のみ、SPEC §5.2）。省略時は `comfyui`。 */
  backend?: string
  /** 音声ワークフローがサポートする長さ（秒）。それ以外では 0。 */
  min_duration: number
  max_duration: number
  default_duration: number
  /**
   * そのモデルが想定している解像度（メガピクセル）。0（と古いレスポンスの
   * 未定義）= 宣言なしで、フォームのグローバル既定（`DEFAULT_MEGAPIXELS`）の
   * まま（SPEC §3.1）。
   */
  default_megapixels?: number
}

export interface Job {
  id: string
  created_at: string
  mode: JobMode
  status: JobStatus
  user_input: string | null
  image_prompt: string | null
  video_prompt: string | null
  /** mode 'audio' のジョブが何を作ろうとしたか。 */
  audio_prompt: string | null
  grok_raw: string | null
  params: Record<string, unknown>
  workflow_json: Record<string, unknown>
  comfy_prompt_id: string | null
  image_path: string | null
  video_path: string | null
  last_frame_path: string | null
  source_image: string | null
  /** 動画ジョブに渡した*入力*のリファレンス音声。 */
  audio_path: string | null
  /** mode 'audio' のジョブが生成した音声ファイル（*出力*）。 */
  audio_output_path: string | null
  error: string | null
  /** 実行を開始した時刻。列を足す前の履歴には無いので任意（null）。 */
  started_at?: string | null
  /** 終端（done/failed/canceled）に入った時刻。所要時間はこの差で出す。 */
  finished_at?: string | null
  nsfw: boolean
  /** '' = 未判定 / 'auto' = 自動判定 / 'manual' = 手動指定。 */
  nsfw_source: string
  image_url: string | null
  video_url: string | null
  last_frame_url: string | null
  audio_output_url: string | null
  /**
   * 主成果物の列に収まらない出力（1 リクエストで複数返るモデルの 2 つめ
   * 以降がここに入る）。古いレスポンスには無いので任意。
   */
  extra_outputs?: string[]
  /** `extra_outputs` の URL（同じ並び）。 */
  extra_output_urls?: string[]
}

export interface JobCreate {
  mode: JobMode
  /** id of the video template to run (see /api/options video_workflows). */
  video_workflow: string
  /** id of the image template to run (see /api/options image_workflows). */
  image_workflow: string
  image_prompt: string
  video_prompt: string
  negative_prompt: string
  aspect_ratio: string
  megapixels: number
  loras: LoraRef[]
  trigger_text: string
  video_loras: LoraRef[]
  video_trigger_text: string
  duration: number
  fps: number
  /**
   * サンプリング回数（SPEC §3.1）。`steps` を宣言しているワークフローでだけ効く。
   * 省略 / 0 は「未指定」= テンプレートの既定値のまま。
   */
  steps?: number
  /**
   * 高速化トグル（SPEC §3.1）。宣言のある動画ワークフローでだけ効く。
   * 省略 / null なら設定（`GET /api/settings`）の既定値に従う。
   */
  audio_path: string | null
  source_image: string | null
  end_image: string | null
  reference_video: string | null
  /**
   * マルチモーダル参照（SPEC §3.1）。宣言しているワークフロー（MiniMax H3 r2v）
   * でだけ使え、開始フレーム（`source_image` / `end_image`）とは排他。
   */
  reference_images?: string[]
  reference_videos?: string[]
  reference_audios?: string[]
  /**
   * ショット割りと Elements（SPEC §3.1）。宣言しているワークフロー
   * でだけ使える。`multi_shots` があるときは `video_prompt` は
   * 送られない（本文はショット側にある）。
   */
  multi_shots?: MultiShot[]
  kling_elements?: KlingElement[]
  seed: number | null
  /**
   * ワークフローが宣言する選択式フィールドの値（論理名 -> 選んだ文字列）。
   * 省略した項目は既定値、`auto` の項目は入力から自動で決まる。
   */
  selects?: Record<string, string>
  /**
   * このジョブだけで使うモデルファイル名（キーは /api/options の model_slots の
   * `key`、値はそのスロットの `choices` にあるもの）。空なら設定の既定値。
   */
  model_overrides?: Record<string, string>
  chat_session_id?: string | null
  user_input?: string | null
  /** 明示指定（manual 扱い）。省略すると自動判定に任せる。 */
  nsfw?: boolean | null
}

/**
 * POST /api/jobs の body（`mode: 'audio'` の単独ジョブ）。
 *
 * 音声は画像→動画の連結とは無関係なので、JobCreate とは別の形。画像・動画側の
 * フィールドを送るとバックエンドに拒否される。
 */
export interface AudioJobCreate {
  mode: 'audio'
  /** id of the audio template to run (see /api/options audio_workflows). */
  audio_workflow: string
  audio_prompt: string
  /** 生成する長さ（秒）。ワークフローごとに上下限がある。 */
  duration: number
  seed: number | null
  /**
   * サンプリング回数（SPEC §3.1）。宣言しているワークフローでだけ効く。
   * 省略 / 0 は「未指定」= テンプレートの既定値のまま。
   */
  steps?: number
  /** 歌詞（[Verse] / [Chorus] のセクションタグ付き）。空ならインスト。 */
  lyrics?: string
  /** 曲に入れたくない要素（宣言しているモデルのみ）。 */
  negative_tags?: string
  /** ワークフローが宣言する選択式フィールド。 */
  selects?: Record<string, string>
  /** Stable Audio: Music / Instrument / SFX / One-shot。 */
  audio_category?: string
  /** Stable Audio: 内蔵 LLM でプロンプトを展開してから流すか。 */
  reprompt?: boolean
  /** このジョブだけで使うモデルファイル名（音声ワークフローのスロットのみ）。 */
  model_overrides?: Record<string, string>
  chat_session_id?: string | null
  user_input?: string | null
  nsfw?: boolean | null
}

/**
 * POST /api/jobs/{id}/continue の body（すべて任意の上書き）。
 *
 * 送らなかった項目は元ジョブの値をそのまま引き継ぐので、何も入れずに投げれば
 * 従来どおりの「そのまま続き」になる。
 */
export interface JobContinue {
  video_workflow?: string
  video_prompt?: string
  negative_prompt?: string
  aspect_ratio?: string
  megapixels?: number
  duration?: number
  fps?: number
  audio_path?: string
  end_image?: string
  reference_video?: string
  seed?: number
  model_overrides?: Record<string, string>
}

/** WS /api/ws のライブラリ更新（自動タグ生成の反映など）。 */
export interface LibraryProgress {
  type: 'library'
  item_id: string
  kind: LibraryKind
  name: string
  tags: string[]
}

export interface JobProgress {
  type: 'job'
  job_id: string
  status: JobStatus
  node?: string | null
  progress?: number | null
  message?: string | null
  /** NSFW フラグが確定したときだけ入る。 */
  nsfw?: boolean | null
}

export interface HealthStatus {
  status: 'ok' | 'not_configured' | 'not_implemented' | 'error'
  detail: string | null
}

export interface Health {
  app: 'ok'
  comfyui: HealthStatus
  /** 選ばれている CLI の状態（キー名は歴史的に grok のまま）。 */
  grok: HealthStatus
  /** いま選ばれている CLI とその表示名。 */
  cli?: LlmCli
  cli_label?: string
}

export interface PushVapidPublicKey {
  public_key: string
}

export interface PushSubscriptionPayload {
  endpoint: string
  keys: { p256dh: string; auth: string }
}

export interface Options {
  comfy_connected: boolean
  comfy_error: string | null
  /** いま使っている接続先プロファイルと、その URL（表示用）。 */
  comfy_target: ComfyTarget
  comfy_url: string
  image_workflows: WorkflowOption[]
  video_workflows: WorkflowOption[]
  audio_workflows: WorkflowOption[]
  default_video_workflow: string
  default_image_workflow: string
  default_audio_workflow: string
  /** Stable Audio の COMBO 選択肢（音声フォーム用）。 */
  audio_categories: string[]
  aspect_ratios: string[]
  lora_files: string[]
  /** 実行時に切り替えられるモデルスロット（候補が 2 件以上あるものだけ）。 */
  model_slots: ModelSlot[]
  /** ComfyUI が持つモデルファイル一覧。キーは `"<class_type>.<field>"`。 */
  model_files: Record<string, string[]>
  loras: Lora[]
  audio_assets: Asset[]
  image_assets: Asset[]
  video_assets: Asset[]
  /** ライブラリの全件（新しい順）。NSFW のフィルタは表示側で行う。 */
  library: LibraryItem[]
  negative_presets: Record<string, string>
}

export type PromptTemplate = 'natural' | 'tagged'

export interface ChatMessage {
  role: 'system' | 'user' | 'assistant'
  content: string
  ts: string
}

export interface ChatSession {
  id: string
  created_at: string
  job_id: string | null
  messages: ChatMessage[]
  /** 続き用の grok セッション id（空 = まだ開いていない）。 */
  grok_session_id?: string
  /** このチャットの作業ディレクトリ（grok の cwd）。 */
  grok_cwd?: string
}

/** 相談チャットの実行状態（POST /api/chat/sessions/{id}/stop の応答）。 */
export interface ChatState {
  session_id: string
  /** Grok のターンが走っているか。 */
  running: boolean
  /** 実行中の活動テキスト（「思考中」など。null = 無し）。 */
  activity: string | null
}

/** WS /api/ws の相談チャットのイベント（`type: "chat"`）。 */
export interface ChatProgress extends ChatState {
  type: 'chat'
}

export interface PromptResult {
  image_prompt: string | null
  video_prompt: string | null
  /** mode 'audio' のセッションが返す音の説明。 */
  audio_prompt: string | null
  /** セクションタグ付きの歌詞。 */
  lyrics: string | null
  /** 曲に入れたくない要素（宣言しているモデルのみ）。 */
  negative_tags: string | null
  notes: string | null
}

export interface ChatReply {
  role: 'assistant'
  content: string
  result: PromptResult | null
}

/** LoRA as the chat API takes it: the job snapshot plus the human name. */
export interface ChatLoraRef extends LoraRef {
  display_name: string
}

export interface ChatSessionCreate {
  mode: JobMode
  /** selected video template: its characteristics steer the video prompt. */
  video_workflow: string
  /** selected image template: its model family steers the image prompt. */
  image_workflow?: string
  /** selected audio template: its guide steers the audio prompt (mode 'audio'). */
  audio_workflow?: string
  loras: ChatLoraRef[]
  trigger_text: string
  video_loras?: ChatLoraRef[]
  video_trigger_text?: string
  /** 動画のクリップ長 / 音声モードでは音の長さ（どちらも秒）。 */
  duration: number
  image_prompt_draft: string
  video_prompt_draft: string
  audio_prompt_draft?: string
  lyrics_draft?: string
  prompt_template: PromptTemplate
  start_image_path?: string | null
  /** 最後のフレーム（欄が出ているときだけ送る）。 */
  end_image_path?: string | null
  /** r2v 系で実際に選んでいる参照素材（欄が出ているときだけ送る）。 */
  reference_images?: string[]
  reference_videos?: string[]
  reference_audios?: string[]
  /** 解像度欄が出ているときのフォームの現在値。 */
  aspect_ratio?: string | null
  megapixels?: number | null
  /** ネガティブプロンプト欄が出ているときの現在値。 */
  negative_prompt?: string | null
  /** 音声モードのフォームの現在値（選択中のモデルが読むものだけ）。 */
  audio_category?: string | null
  negative_tags_draft?: string | null
}

// --------------------------------------------------------------------------
// ドラマスタジオ（プロジェクト -> 脚本 -> Shot ごとの生成 -> Take の採用）
// backend/app/models.py 末尾の Studio 系モデルの写し。
// --------------------------------------------------------------------------

/** World Bible の素材の区分（キャラクター / 場所・背景 / 小道具 / その他の参照）。 */
export type StudioAssetCategory =
  | 'character'
  | 'environment'
  | 'prop'
  | 'style'
  | 'reference'

/** 素材の実体の種別（そのまま assets/<kind>/ の置き場になる）。 */
export type StudioAssetKind = 'image' | 'video' | 'audio'

/** Shot の進み具合（'draft' = 執筆中 / 'ready' = 生成してよい / 'done' = 採用済み）。 */
export type StudioShotStatus = 'draft' | 'ready' | 'done'

/**
 * Take の状態。'rendering' / 'candidate' / 'failed' はジョブから導出した値で、
 * DB に残るのは人が決めた 'selected' / 'rejected' だけ。
 */
export type StudioTakeStatus =
  | 'rendering'
  | 'candidate'
  | 'selected'
  | 'rejected'
  | 'failed'

/** Shot が使う動画ワークフローの強制指定（null = 素材と引き継ぎから自動で決める）。 */
export type StudioWorkflowOverride =
  | 'minimax_h3_t2v'
  | 'minimax_h3_i2v'
  | 'minimax_h3_r2v'

/**
 * リビジョンを作った主体。`user` = UI からの操作、`external` = 外部 API
 * （`/api/v1`）、`chat` = 内蔵チャット。`agent` は外部 API を `external` へ
 * 分ける前に書かれた過去行のためだけに残っている。
 */
export type StudioRevisionActor = 'user' | 'agent' | 'external' | 'chat'

/** リビジョンの差分に出るエンティティの種別。 */
export type StudioRevisionEntity =
  | 'project'
  | 'episode'
  | 'scene'
  | 'shot'
  | 'asset'
  | 'asset_file'
  | 'timeline'
  | 'timeline_track'
  | 'timeline_clip'
  | 'take'

/**
 * 動画生成の品質（プロジェクト単位の設定）。論理モード（t2v / i2v / r2v）とは
 * 直交していて、モードが決まったあとに「モード × 品質 -> バリアント」で解決される。
 *
 * - `normal`: 素の MiniMax H3（20 steps）。どの接続先でも動く。
 * - `opt`: 20 steps のまま、量子化と高速化パッチだけを焼き込んだ最適化版。
 * - `turbo`: 4 steps の蒸留 LoRA 版（いちばん速いが粗い）。
 *
 * `opt` / `turbo` は i2v / r2v にしかバリアントが無く、カスタムノード頼みなので、
 * 条件が揃わなければ素へフォールバックする（理由は `workflow_reason`）。
 */
export type StudioVideoQuality = 'normal' | 'opt' | 'turbo'

/**
 * 画像生成の品質（プロジェクト単位の設定）。動画の `StudioVideoQuality` と同じ
 * 3 段だが**独立したつまみ**で、素材の静止画を MiniMax H3 Image
 * （`minimax_h3_t2i` / `_i2i` / `_r2i` の素 / `_opt` / `_turbo`）で焼くときに
 * だけ効く。動画を turbo で回していても素材の絵は素で焼ける。
 */
export type StudioImageQuality = 'normal' | 'opt' | 'turbo'

export interface StudioProject {
  id: string
  name: string
  /** 作品コード（任意。付けた場合だけ重複を拒む）。 */
  code: string
  synopsis: string
  /** World Bible の覚え書き（作品全体の設定）。 */
  world_notes: string
  /** 日本語のプロンプトを Grok で英訳してから投入する（MiniMax H3 は英語前提）。 */
  auto_translate: boolean
  /**
   * 引き継ぎ（`carry_over_end_frame`）を Motion Context で行う（ラテント連続性）。
   * OFF なら直前カットのラストフレーム 1 枚を開始フレームにする従来の i2v、
   * ON なら直前カットの動画と AV ラテントを渡す `minimax_h3_r2v_context`。
   */
  latent_continuity: boolean
  /**
   * ラテントアップスケール（作品既定）。ON = 1 パス目を 0.2MP で回してから
   * `MinimaxH3LatentUpscaler3D` で指定解像度に拡大する 2 パス。テイク生成の
   * たびにジョブの `selects.latent_upscale` へ解決される（1 回ぶんの上書きは
   * `StudioRenderRequest`）。入れられない接続先では OFF に落ちる。
   */
  latent_upscale: boolean
  /** 動画生成の品質（テイク生成のたびにモードと掛け合わせて解決される）。 */
  quality: StudioVideoQuality
  /**
   * 画像生成の品質（動画の `quality` とは独立）。素材の静止画を MiniMax H3
   * Image で焼くときの版（素 / `_opt` / `_turbo`）を決める。
   */
  image_quality: StudioImageQuality
  /**
   * 動画生成の画質（メガピクセル）の作品既定。`null` = 指定しない＝ワークフローの
   * 既定のまま。Shot 個別の `megapixels` があればそちらが勝つ。
   */
  megapixels: number | null
  /**
   * 動画生成のアスペクト比の作品既定（`'16:9 (Widescreen)'` 等）。`null` = 既定の
   * まま。Shot 個別の `aspect_ratio` があればそちらが勝つ。
   */
  aspect_ratio: string | null
  /**
   * サンプリング回数の作品既定。`0` = 未指定＝**テンプレートの既定のまま**
   * （品質 turbo なら 4、normal / opt なら 20）。
   */
  steps: number
  /**
   * 素材画像の画質（メガピクセル）の作品既定。`null` = 指定しない＝テンプレートの
   * 既定のまま（MiniMax H3 Image は約 0.98MP）。動画の `megapixels` とは独立で、
   * 静止画に動画用の値は流用しない。
   */
  image_megapixels: number | null
  /** 素材画像のアスペクト比の作品既定（`null` = 既定のまま）。 */
  image_aspect_ratio: string | null
  /**
   * 素材画像のサンプリング回数の作品既定。`0` = 未指定＝テンプレートの既定の
   * まま。上限は動画側と同じ。
   */
  image_steps: number
  /**
   * この作品から投入するジョブをすべて NSFW 扱いにする。OFF なら**非 NSFW で
   * 固定**（投入時に明示するので、Grok の自動判定は走らない）。
   */
  nsfw: boolean
  created_at: string
  updated_at: string
}

/** GET /api/studio/projects の 1 行（一覧に出す件数つき）。 */
export interface StudioProjectSummary extends StudioProject {
  shot_count: number
  asset_count: number
  take_count: number
  /** 採用済みの Take の数（= 仕上がった Shot の数）。 */
  selected_take_count: number
}

export interface StudioProjectCreate {
  name: string
  code?: string
  synopsis?: string
  world_notes?: string
  auto_translate?: boolean
  /**
   * 引き継ぎ（`carry_over_end_frame`）を Motion Context で行う（ラテント連続性）。
   * OFF なら直前カットのラストフレーム 1 枚を開始フレームにする従来の i2v、
   * ON なら直前カットの動画と AV ラテントを渡す `minimax_h3_r2v_context`。
   */
  latent_continuity?: boolean
  /** ラテントアップスケール（既定 ON = 0.2MP の 1 パス目 → 指定解像度へ拡大）。 */
  latent_upscale?: boolean
  /** 動画生成の品質（既定は素の 20 steps = `normal`）。 */
  quality?: StudioVideoQuality
  /** 画像生成の品質（素材の静止画にだけ効く。既定 `normal`）。 */
  image_quality?: StudioImageQuality
  /** 素材画像の画質（メガピクセル）の作品既定（`null` = テンプレートの既定）。 */
  image_megapixels?: number | null
  /** 素材画像のアスペクト比の作品既定（`null` = 既定のまま）。 */
  image_aspect_ratio?: string | null
  /** 素材画像のサンプリング回数の作品既定（`0` = テンプレートの既定のまま）。 */
  image_steps?: number
  /** 動画生成の画質（メガピクセル）の作品既定（`null` = ワークフローの既定）。 */
  megapixels?: number | null
  /** 動画生成のアスペクト比の作品既定（`null` = 既定のまま）。 */
  aspect_ratio?: string | null
  /** サンプリング回数の作品既定（`0` = テンプレートの既定のまま）。 */
  steps?: number
  /** この作品から投入するジョブをすべて NSFW 扱いにする（OFF = 非 NSFW 固定）。 */
  nsfw?: boolean
}

/**
 * PATCH 系の body に共通の「書き換える項目ではない」欄。
 *
 * `base_revision` は読んだときの `revision_seq`。送ると、それ以降に**同じ
 * エンティティへの変更**があったときだけ 409 になる（別のカットを直しただけ
 * なら通る）。送らなければ今までどおり無条件に書き込む。
 */
export interface StudioUpdateBase {
  base_revision?: number
}

/** PATCH /api/studio/projects/{id}（送った項目だけ変わる）。 */
export interface StudioProjectUpdate extends StudioUpdateBase {
  name?: string
  code?: string
  synopsis?: string
  world_notes?: string
  auto_translate?: boolean
  /**
   * 引き継ぎ（`carry_over_end_frame`）を Motion Context で行う（ラテント連続性）。
   * OFF なら直前カットのラストフレーム 1 枚を開始フレームにする従来の i2v、
   * ON なら直前カットの動画と AV ラテントを渡す `minimax_h3_r2v_context`。
   */
  latent_continuity?: boolean
  /** ラテントアップスケール（ON = 0.2MP の 1 パス目 → 指定解像度へ拡大）。 */
  latent_upscale?: boolean
  /** 動画生成の品質。 */
  quality?: StudioVideoQuality
  /** 画像生成の品質（素材の静止画にだけ効く）。 */
  image_quality?: StudioImageQuality
  /** 素材画像の画質（メガピクセル）の作品既定（`null` を送ると既定へ戻る）。 */
  image_megapixels?: number | null
  /** 素材画像のアスペクト比の作品既定（`null` を送ると既定へ戻る）。 */
  image_aspect_ratio?: string | null
  /** 素材画像のサンプリング回数の作品既定（`0` = テンプレートの既定のまま）。 */
  image_steps?: number
  /** 動画生成の画質（メガピクセル）の作品既定（`null` を送ると既定へ戻る）。 */
  megapixels?: number | null
  /** 動画生成のアスペクト比の作品既定（`null` を送ると既定へ戻る）。 */
  aspect_ratio?: string | null
  /** サンプリング回数の作品既定（`0` を送るとテンプレートの既定へ戻る）。 */
  steps?: number
  /** この作品から投入するジョブをすべて NSFW 扱いにする（OFF = 非 NSFW 固定）。 */
  nsfw?: boolean
}

/** 話（エピソード）。場（StudioScene）の入れ物。 */
export interface StudioEpisode {
  id: string
  project_id: string
  sort_order: number
  title: string
  synopsis: string
  created_at: string
}

export interface StudioEpisodeCreate {
  title?: string
  synopsis?: string
  /** 並び順（省略すると末尾に足す）。 */
  sort_order?: number | null
}

/** PATCH /api/studio/episodes/{id}（送った項目だけ変わる）。 */
export interface StudioEpisodeUpdate extends StudioUpdateBase {
  title?: string
  synopsis?: string
  sort_order?: number
}

/** 場（シーン）。Shot はここに属する（属さない Shot は未分類）。 */
export interface StudioScene {
  id: string
  episode_id: string
  project_id: string
  sort_order: number
  title: string
  synopsis: string
  /** 「夜明け前」「閉店後」などの時間帯メモ。 */
  time_of_day: string
  created_at: string
}

export interface StudioSceneCreate {
  title?: string
  synopsis?: string
  time_of_day?: string
  sort_order?: number | null
}

/** PATCH /api/studio/scenes/{id}（送った項目だけ変わる）。 */
export interface StudioSceneUpdate extends StudioUpdateBase {
  title?: string
  synopsis?: string
  time_of_day?: string
  sort_order?: number
  /** 引っ越し先の話（同じ作品の話だけ。並び順は移動先の末尾になる）。 */
  episode_id?: string
}

export interface StudioAsset {
  id: string
  project_id: string
  /** `@名前` で呼ぶ識別名（プロジェクト内で一意）。 */
  name: string
  category: StudioAssetCategory
  /** 人間向けの説明（日本語可）。 */
  caption: string
  /** 生成プロンプトに埋め込む説明（英語推奨）。 */
  prompt_caption: string
  /** 分類ごとの拡張項目（キャラの外見・声、画風のパレットなど）。生成には効かない。 */
  profile?: Record<string, unknown>
  /** メインのファイルに足したリファレンス（声サンプル・動画・追加画像）。 */
  files?: StudioAssetFile[]
  kind: StudioAssetKind
  /** ファイルの絶対パス。**空 = メタデータのみの素材**（プロンプトへ説明文として展開される）。 */
  path: string
  /** `/assets/<kind>/<file>`（静的配信 URL）。メタデータのみの素材では空。 */
  url: string
  /** 差し替え禁止の印（UI で鍵を出すだけ。生成には影響しない）。 */
  locked: boolean
  sort_order: number
  created_at: string
  /** 最後に書き換えた時刻（一度も直していなければ created_at）。 */
  updated_at?: string
  /** プロンプトに効く項目を最後に書き換えた時刻（Take の stale 判定に使う）。 */
  prompt_updated_at?: string
}

/**
 * 素材にぶら下がる追加リファレンス（studio_asset_files）。
 *
 * メインのファイル（`StudioAsset.path`）とは別に、キャラの声サンプルや動きの
 * 参照動画・別アングルの画像を何本でも足せる。今の生成ワークフローには自動では
 * 流れない（インスペクタの手がかり）。
 */
export interface StudioAssetFile {
  id: string
  asset_id: string
  project_id: string
  /** image = 追加画像 / voice = 声サンプル / video = 動画リファレンス。 */
  role: StudioAssetFileRole
  /** ファイルの絶対パス。 */
  path: string
  /** `/assets/<kind>/<file>`（静的配信 URL）。 */
  url: string
  caption: string
  sort_order: number
  created_at: string
}

export type StudioAssetFileRole = 'image' | 'voice' | 'video'

/** POST /api/studio/projects/{id}/assets（ファイルなしのメタデータのみの素材）。 */
export interface StudioAssetCreate {
  name: string
  kind?: StudioAssetKind
  category?: StudioAssetCategory
  caption?: string
  prompt_caption?: string
  locked?: boolean
}

/** PATCH /api/studio/assets/{id}（送った項目だけ変わる）。 */
export interface StudioAssetUpdate extends StudioUpdateBase {
  name?: string
  category?: StudioAssetCategory
  caption?: string
  prompt_caption?: string
  /** 分類ごとの拡張項目。**送ったものが丸ごと今の値になる**（項目単位ではない）。 */
  profile?: Record<string, unknown>
  locked?: boolean
  sort_order?: number
}

export interface StudioShot {
  id: string
  project_id: string
  /** 所属する場（null = まだどの場にも入れていない = 未分類）。 */
  scene_id: string | null
  sort_order: number
  title: string
  /** 物語上の目的（このカットで何が進むのか）。 */
  purpose: string
  action: string
  /** 台詞（投入時に MiniMax H3 の `<d>[Language] …</d>` へ組み込まれる）。 */
  dialogue: string
  /** 効果音・環境音。 */
  soundscape: string
  bgm: string
  camera: string
  /** 尺（MiniMax H3 は 1〜15 秒）。 */
  duration_seconds: number
  /** 生成プロンプトの本文（`@素材名` メンション可）。 */
  prompt: string
  status: StudioShotStatus
  selected_take_id: string | null
  /** 直前の Shot の採用 Take のラストフレームを開始フレームに使う。 */
  carry_over_end_frame: boolean
  // --- Shot ごとの生成設定（null = 生成フォームの既定値のまま） ---------------
  /** 画面比（`"16:9 (Widescreen)"` などのプリセット名か `"W:H"`）。 */
  aspect_ratio: string | null
  /** 解像度の目安（画面比と合わせて幅×高さが決まる）。 */
  megapixels: number | null
  /** 乱数の種（null = 毎回ランダム）。 */
  seed: number | null
  /** ワークフローの強制指定（null = t2v / i2v / r2v を自動で決める）。 */
  workflow_override: StudioWorkflowOverride | null
  /** 訳した（または人が直した）英語。公式フィールド込みの完成文。 */
  english_prompt?: string
  /** その英語の元になった組み立て済み日本語。 */
  english_source?: string
  /** 英訳の進行（`''` / `translating` / `failed`）。 */
  english_status?: string
  /** 英訳失敗の理由（日本語。成功時・未実施は空）。 */
  english_error?: string
  created_at: string
  updated_at: string
  /** プロンプトに効く項目を最後に書き換えた時刻（Take の stale 判定に使う）。 */
  prompt_updated_at?: string
}

export interface StudioShotCreate {
  title?: string
  purpose?: string
  action?: string
  dialogue?: string
  soundscape?: string
  bgm?: string
  camera?: string
  duration_seconds?: number
  prompt?: string
  status?: StudioShotStatus
  carry_over_end_frame?: boolean
  scene_id?: string | null
  aspect_ratio?: string | null
  megapixels?: number | null
  seed?: number | null
  workflow_override?: StudioWorkflowOverride | null
  /** 並び順（省略すると末尾に足す）。 */
  sort_order?: number | null
}

/**
 * PATCH /api/studio/shots/{id}（送った項目だけ変わる）。
 *
 * `scene_id` / 生成設定は **null を明示すると外れる**（送らなければ今の値のまま）。
 */
export interface StudioShotUpdate extends StudioUpdateBase {
  title?: string
  purpose?: string
  action?: string
  dialogue?: string
  soundscape?: string
  bgm?: string
  camera?: string
  duration_seconds?: number
  prompt?: string
  status?: StudioShotStatus
  carry_over_end_frame?: boolean
  sort_order?: number
  scene_id?: string | null
  selected_take_id?: string | null
  aspect_ratio?: string | null
  megapixels?: number | null
  seed?: number | null
  workflow_override?: StudioWorkflowOverride | null
  /** 英語キャッシュ。空文字または null で消す。 */
  english_prompt?: string | null
}

/**
 * POST /api/studio/shots/{id}/render のボディ（**すべて任意**）。
 *
 * そのテイク 1 回の生成にだけ効き、カットもプロジェクトも書き換えない。送らな
 * かった項目は今までどおりの解決に落ちる:
 *
 * - `megapixels` / `aspect_ratio`: ここ → カット → プロジェクト → 既定
 * - `duration`: ここ → カットの `duration_seconds`
 * - `steps`: ここ → プロジェクトの `steps` → テンプレートの既定
 * - `seed`: ここ → カットの `seed` → 毎回ランダム
 * - `latent_upscale`: ここ → プロジェクトの `latent_upscale`
 */
export interface StudioRenderRequest {
  megapixels?: number
  aspect_ratio?: string
  /** 尺（秒）。 */
  duration?: number
  /** サンプリング回数（`0` = テンプレートの既定のまま、を明示する）。 */
  steps?: number
  /** 乱数の種（省略 = カットの設定、それも無ければ毎回ランダム）。 */
  seed?: number
  /** ラテントアップスケール（省略 = プロジェクトの `latent_upscale`）。 */
  latent_upscale?: boolean
}

export interface StudioTake {
  id: string
  shot_id: string
  project_id: string
  job_id: string
  status: StudioTakeStatus
  created_at: string
  /** 元ジョブの状態。ジョブが消えていれば null。 */
  job_status: JobStatus | null
  /** 実際に走ったワークフロー（minimax_h3_t2v / _i2v / _r2v / _r2v_context）。 */
  video_workflow: string | null
  video_path: string | null
  video_url: string | null
  last_frame_path: string | null
  last_frame_url: string | null
  /**
   * ラテント連続性で保存した AV ラテント（ComfyUI 側のパス）。次のカットが
   * ここから続きを作る。使わなかった Take は null。
   */
  latent_path?: string | null
  /**
   * 同じく、`latent_upscale` = on のときに保存した 2 パス目（最終解像度）の
   * AV ラテント。2 段引き継ぎで次のカットが読む（off だった Take は null）。
   */
  latent_hires_path?: string | null
  error: string | null
  /** 元ジョブの NSFW フラグ（ジョブが消えていれば null）。 */
  nsfw?: boolean | null
  /** その判定の出どころ（'' = 未判定 / 'auto' / 'manual'）。 */
  nsfw_source?: string
  /** 実際に投入した本文（英訳したときは訳したあとのもの）。 */
  prompt?: string
  /** 英訳する前の原文（英訳していなければ空）。 */
  source_prompt?: string
  /** 投入はできたが伝えたいこと（過去 Take の英訳失敗フォールバックなど）。 */
  warning?: string
  /** この Take を作ったあとに脚本や素材が変わった。 */
  stale?: boolean
  /** stale と判断した理由（日本語。stale でなければ空）。 */
  stale_reasons?: string[]
}

/** 投入プレビューに出る参照素材 1 件（r2v のときだけ入る）。 */
export interface StudioPromptReference {
  name: string
  kind: StudioAssetKind
  /** 本文でこの素材を指すタグ（`<Picture 1>` など）。 */
  tag: string
  /** 添付されるファイル（assets/ からの相対パス）。 */
  path: string
}

/**
 * GET /api/studio/shots/{id}/prompt-preview: 投入される最終形。
 *
 * 生成と同じ組み立てを通した結果で、Grok の英訳だけは走らせない（入るかどうかは
 * `will_translate`。使える英語キャッシュがあれば false）。組み立てられないカットは
 * 400 ではなく `error` 付きで返る。
 */
export interface StudioShotPreview {
  shot_id: string
  /** 投入されるワークフロー（組み立てられなければ強制指定か null）。 */
  workflow: string | null
  /** そのワークフローになる理由（日本語）。 */
  workflow_reason: string
  /** 実際に投入される本文（公式フィールドと除外文まで込み）。 */
  prompt: string
  references: StudioPromptReference[]
  /** 開始フレームに使われるファイル（i2v のときだけ）。 */
  start_frame: string | null
  /** プロジェクトの設定（日本語まじりなら投入時に英訳する）。 */
  auto_translate: boolean
  /** 使える英語キャッシュが無く、auto_translate かつ日本語を含むときだけ true。 */
  will_translate: boolean
  /** 保存済みの英語（古くても出す）。 */
  english_prompt: string
  /** 英語はあるが脚本の組み立てが変わっている。 */
  english_stale: boolean
  /** 英訳の進行（`''` / `translating` / `failed`）。 */
  english_status?: string
  /** 英訳失敗の理由（日本語。成功時・未実施は空）。 */
  english_error?: string
  /** プロジェクトの設定（引き継ぎを Motion Context で行う = ラテント連続性）。 */
  latent_continuity: boolean
  /** プロジェクトの設定（動画生成の品質）。 */
  quality: StudioVideoQuality
  /**
   * `quality` が実際に効いたか（false = 素へフォールバックした。
   * 理由は `workflow_reason` の末尾に入る）。
   */
  quality_applied: boolean
  /** 実際に投入される `selects.latent_upscale`（接続先とワークフローで解決済み）。 */
  latent_upscale: boolean
  /** ラテント連続性で引き継ぐ直前カットの動画（使わないときは null）。 */
  context_video: string | null
  /** 同じく、引き継ぎ元の AV ラテント（ComfyUI 側のパス）。 */
  context_latent: string | null
  /** 同じく、引き継ぎ元の 2 パス目（最終解像度）の AV ラテント（2 段引き継ぎ）。 */
  context_latent_hires?: string | null
  /** 組み立てられなかった理由（日本語。空なら問題なし）。 */
  error: string
  /**
   * 組み立てはできたが**投入だけができない**理由（日本語。空なら投入できる）。
   * いまは連続カットで前 Shot の採用 Take がまだ無いとき（英訳はできる）。
   */
  render_blocker?: string
}

/** GET /api/studio/capabilities: いまの接続先でスタジオの追加機能が使えるか。 */
export interface StudioCapabilities {
  /** ラテント連続性（MiniMaxH3MotionContext 系のカスタムノードが揃っている）。 */
  latent_continuity: boolean
  /** ラテントアップスケール（MinimaxH3LatentUpscaler3D を入れられる接続先か）。 */
  latent_upscale: boolean
  /** 確かめられなかった理由（日本語。空なら判定できている）。 */
  error: string
}

/** GET /api/studio/projects/{id}: 画面 1 枚を組み立てるのに要るもの一式。 */
export interface StudioProjectDetail extends StudioProject {
  assets: StudioAsset[]
  episodes: StudioEpisode[]
  /** プロジェクトの全シーン（話ごとではなく 1 本の配列。`episode_id` で束ねる）。 */
  scenes: StudioScene[]
  shots: StudioShot[]
  /** プロジェクトの全 Take（Shot ごとに古い順）。 */
  takes: StudioTake[]
  /**
   * いまのリビジョン連番（0 = まだ履歴が無い）。PATCH の `base_revision` に
   * そのまま渡すと「読んだあとに他所が触っていたら 409」にできる。
   */
  revision_seq: number
}

/** リビジョン 1 件の見出し（GET .../revisions の 1 行）。 */
export interface StudioRevision {
  seq: number
  actor: StudioRevisionActor
  /** 変更内容の短い説明（日本語）。 */
  action: string
  /**
   * 触ったエンティティ（1 件だけを触る操作のときだけ入る）。並べ替えや一括
   * 作成のように複数へ跨る操作と、この列を足す前の行は空。
   */
  entity_kind: string
  entity_id: string
  created_at: string
}

/** GET .../revisions/{seq}: そのときのプロジェクト全体つき。 */
export interface StudioRevisionDetail extends StudioRevision {
  snapshot: Record<string, unknown>
}

/** 1 項目ぶんの差分（`StudioRevisionEntityDiff` の中身）。 */
export interface StudioRevisionFieldDiff {
  field: string
  /** 直前のリビジョンでの値（`op` が `create` なら入らない）。 */
  before: unknown
  /** このリビジョンでの値（`op` が `delete` なら入らない）。 */
  after: unknown
}

/** 1 エンティティぶんの差分（作成・削除は `fields` が空）。 */
export interface StudioRevisionEntityDiff {
  entity: StudioRevisionEntity
  id: string
  /** 見出し（title / name 相当。持たないエンティティは id）。 */
  name: string
  op: 'create' | 'update' | 'delete'
  fields: StudioRevisionFieldDiff[]
}

/** GET .../revisions/{seq}/diff: **直前のリビジョンとの**差分。 */
export interface StudioRevisionDiff extends StudioRevision {
  changes: StudioRevisionEntityDiff[]
}

/**
 * POST .../revisions/{seq}/restore body（すべて任意）。
 *
 * 何も送らなければプロジェクト丸ごとの復元。`entity` と `id` を送るとその 1 件
 * だけを戻し、`fields` まで送るとその項目だけを戻す。
 */
export interface StudioRevisionRestore {
  entity?: StudioRevisionEntity
  id?: string
  fields?: string[]
}

/** POST /api/studio/demo body。 */
export interface StudioDemoCreate {
  code: string
}

// --------------------------------------------------------------------------
// 編集タブ（タイムライン -> トラック -> クリップ -> 書き出し）
// backend/app/models.py の Timeline 系モデルの写し。
//
// 焼き上がった Take を並べ直して 1 本の動画にするための EDL。クリップはソースを
// 参照するだけで、元が消えても並びは残り `missing` で伝わる。
// --------------------------------------------------------------------------

/** トラックの種別（`video` の V1 / `audio` の A1… / `subtitle` の T1）。 */
export type TimelineTrackKind = 'video' | 'audio' | 'subtitle'

/**
 * クリップのソース。
 *
 * `take` は制作タブのテイク、`library` / `job` / `asset_file` は素材ビンから
 * 足したもの、`image` は静止画（`source_id` は `library:<id>` のように出どころ
 * の印つき）、`text` はテロップ、`gap` は隙間。
 */
export type TimelineClipSource =
  | 'take'
  | 'asset_file'
  | 'library'
  | 'job'
  | 'image'
  | 'text'
  | 'gap'

/** 繋ぎの種別（ffmpeg の `xfade` にマップされる）。 */
export type TimelineTransitionKind =
  | 'crossfade'
  | 'fadeblack'
  | 'fadewhite'
  | 'wipeleft'
  | 'wiperight'
  | 'slideleft'
  | 'slideright'
  | 'circleopen'
  | 'pixelize'

/** 書き出しのプリセット（`timeline` = タイムラインの規格そのまま）。 */
export type TimelineExportPreset = 'timeline' | '1080p' | 'vertical' | '720p'

/** 縦横比が変わるときの収め方。 */
export type TimelineExportFit = 'pad' | 'crop'

/** 素材ビンに出るものの種別。 */
export type TimelineMediaKind = 'video' | 'audio' | 'image'

/** 書き出し 1 回の状態。 */
export type TimelineExportStatus = 'queued' | 'running' | 'done' | 'failed'

/** 1 本のタイムライン（書き出しの規格を持つ EDL の入れ物）。 */
export interface StudioTimeline {
  id: string
  project_id: string
  /** どの話を組んだものか（null = 作品まるごと）。 */
  episode_id: string | null
  name: string
  /** 書き出しの規格。クリップはここへ揃えて連結される。 */
  fps: number
  width: number
  height: number
  created_at: string
  updated_at: string
}

/**
 * POST /api/studio/projects/{id}/timelines body。
 *
 * `episode_id` を送ると自動配置つきの初期化になる（その話の採用 Take を V1 へ
 * 隙間なく並べる）。
 */
export interface StudioTimelineCreate {
  episode_id?: string | null
  name?: string
  fps?: number
  width?: number
  height?: number
}

/** PATCH /api/studio/timelines/{id} body（指定した項目だけ変える）。 */
export interface StudioTimelineUpdate {
  name?: string
  fps?: number
  width?: number
  height?: number
}

/** トラックに置かれたクリップ 1 つ（ソース解決済み）。 */
export interface TimelineClip {
  id: string
  track_id: string
  timeline_id: string
  /** タイムライン上の開始位置（ミリ秒）。 */
  start_ms: number
  /** 尺（ミリ秒）。`(out_ms - in_ms) / speed` と一致する。 */
  duration_ms: number
  source_kind: TimelineClipSource
  source_id: string | null
  /** ソースの中の切り出し位置（ミリ秒）。 */
  in_ms: number
  out_ms: number
  gain_db: number
  fade_in_ms: number
  fade_out_ms: number
  /**
   * **前の**クリップとの繋ぎ（null = カット）。オーバーラップ方式なので、
   * 繋ぎが付くとこのクリップはその分だけ前へ食い込む。
   */
  transition_kind: string | null
  transition_ms: number
  /** `text` クリップの中身（`{ text, style }`）。 */
  text_payload: Record<string, unknown> | null
  /** 再生速度（1.0 = 等速。映像クリップだけ 1 以外を取れる）。 */
  speed: number
  sort_order: number
  // --- サーバーが読み取りのたびに解決するぶん -------------------------------
  /** 再生できる URL（`/outputs/…` / `/library/…` / `/assets/…`）。 */
  video_url: string | null
  /** ソースそのものの長さ（ミリ秒）。分からなければ null。 */
  source_duration_ms: number | null
  /** ソースの実ファイルが無い（元の Take が消えた / 失敗した）。 */
  missing: boolean
  /** 画面に出す見出し（「第 1 話 / 場 1 / #2 カット名」）。 */
  label: string
}

/** トラック 1 本（クリップ込み）。 */
export interface TimelineTrack {
  id: string
  timeline_id: string
  kind: TimelineTrackKind
  name: string
  sort_order: number
  muted: boolean
  locked: boolean
  clips: TimelineClip[]
}

/** GET /api/studio/timelines/{id}: トラックとクリップ込みのフル EDL。 */
export interface StudioTimelineDetail extends StudioTimeline {
  tracks: TimelineTrack[]
  /** 一番後ろのクリップの終わり（ミリ秒）。 */
  duration_ms: number
}

/**
 * PUT /api/studio/timelines/{id}/clips の 1 件。
 *
 * `id` は送れば引き継がれ、省けば新しく振られる（分割した直後のクリップなど）。
 */
export interface TimelineClipInput {
  id?: string | null
  track_id: string
  start_ms: number
  duration_ms: number
  source_kind: TimelineClipSource
  source_id?: string | null
  in_ms: number
  out_ms: number
  gain_db?: number
  fade_in_ms?: number
  fade_out_ms?: number
  transition_kind?: string | null
  transition_ms?: number
  text_payload?: Record<string, unknown> | null
  speed?: number
}

/** 書き出し 1 回（`outputs/exports/{id}/final.mp4`）。 */
export interface TimelineExport {
  id: string
  timeline_id: string
  status: TimelineExportStatus
  /** 0.0〜1.0。 */
  progress: number
  params: Record<string, unknown>
  output_path: string | null
  /** `/outputs/…` の配信 URL（まだ無ければ null）。 */
  output_url: string | null
  error: string | null
  created_at: string
  finished_at: string | null
}

/** POST /api/studio/timelines/{id}/export body（すべて任意の上書き）。 */
export interface TimelineExportRequest {
  width?: number
  height?: number
  fps?: number
  /** 解像度のプリセット（`width` / `height` を直接送ればそちらが勝つ）。 */
  preset?: TimelineExportPreset
  /** 縦横比が変わるときの収め方。 */
  fit?: TimelineExportFit
  /** ラウドネス正規化（-14 LUFS / TP -1.5 dB）を掛けるか。 */
  loudnorm?: boolean
}

// --- トラックの出し入れ ------------------------------------------------------

/** POST /api/studio/timelines/{id}/tracks body。 */
export interface TimelineTrackCreate {
  /** `video` は V1 が正なので足せない（400）。 */
  kind?: TimelineTrackKind
  /** 省略すると種別ごとの連番（`A2` / `T1`）。 */
  name?: string
}

/** PATCH /api/studio/timelines/{id}/tracks/{track_id} body。 */
export interface TimelineTrackUpdate {
  name?: string
  muted?: boolean
  locked?: boolean
}

// --- 素材ビン ----------------------------------------------------------------

/** タイムラインへ置ける素材 1 件。 */
export interface TimelineMediaItem {
  /** クリップにしたときの `source_kind`。 */
  source_kind: TimelineClipSource
  /** その種別の中での id（`source_id` にそのまま入る）。 */
  source_id: string
  media_kind: TimelineMediaKind
  name: string
  /** 出どころの説明（「ライブラリ」「素材」など）。 */
  origin: string
  url: string | null
  /** 素材そのものの長さ（ミリ秒。静止画と読めなかったものは null）。 */
  duration_ms: number | null
  created_at: string
}

/** GET /api/studio/projects/{id}/media: 素材ビンの 1 ページ。 */
export interface TimelineMediaPage {
  items: TimelineMediaItem[]
  total: number
  limit: number
  offset: number
}

// --- 台詞からのテロップ生成 --------------------------------------------------

/** POST /api/studio/timelines/{id}/generate-subtitles body。 */
export interface TimelineSubtitleRequest {
  /** 書き込む字幕トラック（省略すると T1。無ければ作られる）。 */
  track_id?: string | null
}

// --- 脚本との差分 ------------------------------------------------------------

/** 増えたカット（採用テイクの動画がある）。 */
export interface TimelineSyncAdded {
  shot_id: string
  take_id: string
  label: string
  duration_ms: number
}

/** クリップが古いテイクを指している。 */
export interface TimelineSyncRetaken {
  clip_id: string
  shot_id: string
  old_take_id: string
  new_take_id: string
  label: string
  duration_ms: number | null
}

/** 元のカットが消えた（または採用が外れた）クリップ。 */
export interface TimelineSyncRemoved {
  clip_id: string
  label: string
  reason: string
}

/** GET /api/studio/timelines/{id}/sync-preview: 反映できる差分。 */
export interface TimelineSyncPreview {
  added: TimelineSyncAdded[]
  retaken: TimelineSyncRetaken[]
  removed: TimelineSyncRemoved[]
}

/** POST /api/studio/timelines/{id}/sync body（選んだ項目だけ反映）。 */
export interface TimelineSyncRequest {
  add_shot_ids?: string[]
  retake_clip_ids?: string[]
  remove_clip_ids?: string[]
}

// --- メディア欠落のリカバリ --------------------------------------------------

/** 欠落クリップに充てられる同じカットの別テイク。 */
export interface TimelineMissingCandidate {
  take_id: string
  status: string
  created_at: string
  duration_ms: number | null
}

/** 実ファイルが見つからないクリップ 1 つと、その直し方。 */
export interface TimelineMissingClip {
  clip_id: string
  label: string
  source_kind: TimelineClipSource
  source_id: string | null
  candidates: TimelineMissingCandidate[]
}

/** GET /api/studio/timelines/{id}/missing: 欠落クリップの一覧。 */
export interface TimelineMissingReport {
  clips: TimelineMissingClip[]
}

/** POST /api/studio/timelines/{id}/missing/resolve body。 */
export interface TimelineMissingFix {
  /** `{ クリップ id: 差し替え先の take id }`。 */
  replace?: Record<string, string>
  drop_clip_ids?: string[]
  /** 残っている欠落クリップを全部消す。 */
  drop_all?: boolean
}

/** WS /api/ws の書き出し進捗（`type: "timeline_export"`）。 */
export interface TimelineExportProgress {
  type: 'timeline_export'
  export_id: string
  timeline_id: string
  status: TimelineExportStatus
  progress: number
  /** 完了したときだけ入る配信 URL。 */
  output_url: string | null
  error: string | null
}

// ------------------------------------------------ 画面のリアルタイム化（WS）

/**
 * スタジオの更新（`type: "studio"`）。外部エージェントが API から脚本や素材を
 * 書き換えたときに届く。正は DB なので、載っているのは「どの作品の何が動いたか」
 * だけ（受け取った画面はその作品を取り直す）。
 */
export interface StudioEvent {
  type: 'studio'
  project_id: string
  entity:
    | 'project'
    | 'episode'
    | 'scene'
    | 'shot'
    | 'asset'
    | 'asset_file'
    | 'take'
    | 'timeline'
  id: string
  op: 'create' | 'update' | 'delete'
}

/** 生成フォームの下書き（`GET/PUT /api/ui/generate-form`）。 */
export interface UiFormState {
  values: Record<string, unknown>
  /** 保存のたびに 1 つ上がる連番（0 = まだ一度も保存されていない）。 */
  revision: number
  /** 最後に書いた側（`ui` = ブラウザ / `external` = 外部 API）。 */
  updated_by: string
  updated_at: string
}

/** 下書きが変わったことの通知（`type: "form"`）。値そのものが載る。 */
export interface UiFormProgress {
  type: 'form'
  revision: number
  updated_by: string
  values: Record<string, unknown>
}

/** 外部からの画面移動の指示（`type: "ui"`、`op: "navigate"`）。 */
export interface UiNavigateEvent {
  type: 'ui'
  op: 'navigate'
  view: 'main' | 'studio' | 'settings'
  project_id: string | null
  shot_id: string | null
}
