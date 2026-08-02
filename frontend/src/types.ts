// Mirrors backend/app/models.py

/** `audio` は独立モード: 音声ワークフローを 1 本だけ走らせ、画像→動画の連結
 *  （full）とは一切繋がらない。
 *
 *  `veo_extend` / `veo_1080p` は**フォームから選ぶモードではない**: 生成済みの
 *  Veo ジョブに履歴から掛ける追加操作で、新しいジョブ 1 本として履歴に並ぶ
 *  （SPEC §5.2）。生成フォームのモード選択には出さない。 */
export type JobMode =
  | 'full'
  | 'i2v'
  | 'image_only'
  | 'audio'
  | 'veo_extend'
  | 'veo_1080p'

/** 生成済みジョブに追加で掛けられる操作（`Job.followups` の要素）。 */
export type JobFollowup = 'veo_extend' | 'veo_1080p'
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
  /**
   * kie.ai（外部生成バックエンド、SPEC §5.2）の API キー。空のときだけ環境変数
   * `KIE_API_KEY` に落ちる。キーが有効だと確認できたときだけ kie 系ワークフローが
   * 選択肢に出る。
   */
  kie_api_key: string
  grok_command: string
  grok_model: string
  grok_workdir: string
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
  /** 'image' = 画像ワークフロー / 'video' = LTX 2.3 の動画ワークフロー。 */
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
 * 自由記述ではなく決まった選択肢で挙動が決まるワークフロー（wan_dancer の
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
}

/** One selectable workflow template (GET /api/options). */
export interface WorkflowOption {
  id: string
  label: string
  kind: 'image' | 'video' | 'audio'
  /** model family — image LoRAs of another family cannot be used with it. */
  family: string
  notes: string
  requires: WorkflowInput[]
  /**
   * 複数ファイルで渡せる参照入力（論理名 -> 件数の上限、SPEC §3.1）。
   * **参照専用のワークフロー**（`*_ref` / `veo3_1_fast_ref`）だけが宣言し、
   * そちらは開始フレームを受け取らない（`accepts_start_image` が false）。
   */
  multi_inputs?: Partial<Record<ReferenceInput, number>>
  /**
   * 選択式どうしの相関（名前 -> `[相手の名前, 相手に必要な値]`、SPEC §3.1）。
   * Suno の `duration` は `model` が `V5_5` のときだけ効き、他のモデルでは
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
  /** 実行エンジン（`comfyui` / `kie`、SPEC §5.2）。省略時は `comfyui`。 */
  backend?: string
  /** 音声ワークフローがサポートする長さ（秒）。それ以外では 0。 */
  min_duration: number
  max_duration: number
  default_duration: number
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
  nsfw: boolean
  /** '' = 未判定 / 'auto' = 自動判定 / 'manual' = 手動指定。 */
  nsfw_source: string
  image_url: string | null
  video_url: string | null
  last_frame_url: string | null
  audio_output_url: string | null
  /**
   * 主成果物の列に収まらない出力（Suno は 1 リクエストで 2 曲返るので 2 曲目
   * 以降がここに入る）。古いレスポンスには無いので任意。
   */
  extra_outputs?: string[]
  /** `extra_outputs` の URL（同じ並び）。 */
  extra_output_urls?: string[]
  /**
   * このジョブの成果物に追加で掛けられる kie.ai の操作（SPEC §5.2）。
   * 履歴の「延長」「1080P を取得」はここを見て出す。古いレスポンスには無い。
   */
  followups?: JobFollowup[]
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
  audio_path: string | null
  source_image: string | null
  end_image: string | null
  reference_video: string | null
  /**
   * マルチモーダル参照（SPEC §3.1）。宣言しているワークフロー（Seedance 2 系）
   * でだけ使え、開始フレーム（`source_image` / `end_image`）とは排他。
   */
  reference_images?: string[]
  reference_videos?: string[]
  reference_audios?: string[]
  /**
   * ショット割りと Elements（SPEC §3.1）。宣言しているワークフロー
   * （Kling 3.0）でだけ使える。`multi_shots` があるときは `video_prompt` は
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
  /** ACE-Step: 歌詞（[Verse] / [Chorus] の構造タグ付き）。空ならインスト。 */
  lyrics?: string
  bpm?: number
  keyscale?: string
  language?: string
  /** Suno: 曲に入れたくない要素（`negativeTags`）。 */
  negative_tags?: string
  /** ワークフローが宣言する選択式フィールド（Suno のモデル・ボーカル性別）。 */
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
  grok: HealthStatus
  /** 外部生成バックエンド kie.ai（未設定は not_configured、SPEC §5.2）。 */
  kie: HealthStatus
}

/** 生成バックエンドの可用性（`GET /api/options` の `backends`、SPEC §5.2）。 */
export interface BackendInfo {
  backend: string
  status: 'ok' | 'not_configured' | 'error'
  detail: string
  /** false のバックエンドのワークフローは一覧に載らない。 */
  available: boolean
}

/** GET /api/kie/credits — kie.ai の残クレジット（1 credit = $0.005）。 */
export interface KieCredits {
  configured: boolean
  credits: number | null
  error: string | null
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
  /** 生成バックエンドの可用性（使えないものはワークフロー一覧に載らない）。 */
  backends?: BackendInfo[]
  default_video_workflow: string
  default_image_workflow: string
  default_audio_workflow: string
  /** ACE-Step / Stable Audio の COMBO 選択肢（音声フォーム用）。 */
  audio_categories: string[]
  keyscales: string[]
  languages: string[]
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
}

export interface PromptResult {
  image_prompt: string | null
  video_prompt: string | null
  /** mode 'audio' のセッションが返す音の説明。 */
  audio_prompt: string | null
  /** ACE-Step: 構造タグ付きの歌詞。 */
  lyrics: string | null
  bpm: number | null
  keyscale: string | null
  language: string | null
  /** Suno: 曲に入れたくない要素（`negativeTags`）。 */
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
}

// ---------------------------------------------------------------- agent mode
// AGENT-MODE §4 / §5 — mirrors the Agent* models of backend/app/models.py.

export type AgentStatus =
  | 'idle'
  | 'planning'
  | 'running'
  | 'waiting_checkin'
  | 'stopped'
  | 'done'
export type AgentCheckinMode = 'every_job' | 'milestone' | 'auto'
export type AgentActionName =
  | 'plan'
  | 'run_task'
  | 'continue'
  | 'rerun'
  | 'inspect'
  | 'note'
  | 'checkin'
  | 'done'
export type AgentTaskStatus = 'pending' | 'running' | 'done' | 'failed' | 'skipped'

/** POST /api/agent/sessions/{id}/attachments のレスポンス。 */
export interface AgentAttachment {
  name: string
  /** workdir 相対パス（`attachments/<name>`）。 */
  path: string
}

export interface AgentMessage {
  role: 'system' | 'user' | 'assistant' | 'event' | 'checkin'
  content: string
  ts: string
  /** event / checkin の種別（job_started, inspect_result, checkin …）。 */
  kind: string | null
  data: Record<string, unknown>
}

export interface AgentTask {
  id: string
  label: string
  /** Validated JobCreate snapshot. */
  job: Record<string, unknown>
  status: AgentTaskStatus
  job_id: string | null
  error: string | null
  retries: number
}

export interface AgentPlan {
  version: number
  notes: string
  approved: boolean
  tasks: AgentTask[]
}

export interface AgentArtifact {
  kind: 'plan' | 'note' | 'research' | 'frame' | 'image' | 'video' | 'audio'
  title: string
  ts: string
  /** workdir 相対のファイル名（外部成果物は空）。 */
  name: string
  url: string | null
  job_id: string | null
  text: string | null
}

export interface AgentSession {
  id: string
  created_at: string
  title: string
  status: AgentStatus
  checkin_mode: AgentCheckinMode
  auto_limit: number
  messages: AgentMessage[]
  plan: AgentPlan
  artifacts: AgentArtifact[]
  nsfw: boolean
  /** '' = 未判定 / 'auto' / 'manual'。 */
  nsfw_source: string
  /** Grok ターンの実行中フラグ（バックエンドのインメモリ状態）。 */
  thinking: boolean
  /** 実行中の活動（「思考中」「ツール実行中: …」。未実行なら null）。 */
  activity: string | null
}

export interface AgentSessionSummary {
  id: string
  created_at: string
  title: string
  status: AgentStatus
  checkin_mode: AgentCheckinMode
  auto_limit: number
  message_count: number
  task_count: number
  artifact_count: number
  nsfw: boolean
  nsfw_source: string
}

export interface AgentSessionCreate {
  title?: string
  goal?: string
  checkin_mode?: AgentCheckinMode
  auto_limit?: number
}

export interface AgentApprove {
  approved?: boolean
  note?: string
}

export interface AgentCheckinReply {
  content?: string
  choice?: string | null
}

export interface AgentAction {
  action: AgentActionName
  notes: string
  summary: string
  question: string
  options: string[]
  tasks: AgentTask[]
  task_id: string | null
  job_id: string | null
  interval: number
  title: string
  filename: string | null
  content: string
  /** note アクションの成果物種別（リサーチまとめは research）。 */
  kind: 'note' | 'research'
  overrides: Record<string, unknown>
  /** プラン外 continue / rerun がユーザー承認済みか。 */
  approved: boolean
}

export interface AgentReply {
  content: string
  action: AgentAction | null
  session: AgentSession
}

export interface AgentProgress {
  type: 'agent'
  session_id: string
  status: AgentStatus
  task_id: string | null
  task_status: AgentTaskStatus | null
  job_id: string | null
  artifact: AgentArtifact | null
  message: string | null
  /** Grok ターンが走っているか（null = この通知では変化なし）。 */
  thinking: boolean | null
  /** 実行中の活動テキスト（null = 変化なし / ターン終了）。 */
  activity: string | null
}
