// Mirrors backend/app/models.py

/** `audio` は独立モード: 音声ワークフローを 1 本だけ走らせ、画像→動画の連結
 *  （full）とは一切繋がらない。 */
export type JobMode = 'full' | 'i2v' | 'image_only' | 'audio'
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
  grok_command: string
  grok_model: string
  /** チャット / エージェントが grok CLI を回すときの作業ディレクトリ（空 = 既定）。 */
  grok_workdir: string
  /**
   * Grok Imagine（画像生成・編集、SPEC §5.2）の作業ディレクトリと制限時間。
   * コマンド名は `grok_command` と共有する。
   */
  grok_media_workdir: string
  grok_media_timeout: number
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
   * エージェント / 相談の実行上限（AGENT-MODE §3.4）。どれも **0 = 無制限**で、
   * 既定値は従来どおり（無制限にしたい人だけが 0 を入れる）。
   */
  /**
   * grok CLI に足す追加フラグ（ツール権限）。**空にするとエージェントのツールが
   * 丸ごと無効**になり、システムプロンプトからもツールの節が落ちる。
   */
  agent_grok_args: string[]
  /**
   * エージェントのターンを ACP（`grok agent stdio`）で回すか。ON だと実行中の
   * 活動（思考 / ツール実行）が UI に出る。OFF は従来のワンショット実行。
   */
  agent_use_acp: boolean
  /** grok CLI 1 回あたりの制限時間（秒）。0 = タイムアウトなし。 */
  agent_grok_timeout: number
  /** 自走セッションの「1 回のプラン提案で増やせる新規ジョブ数」。0 = 無制限。 */
  agent_max_plan_tasks: number
  /** スタジオのエージェントが人間の入力なしに回せる連続ターン数。0 = 無制限。 */
  agent_max_turns: number
  /** キャンバスのエージェントが 1 回の発言から回す連続ターン数。0 = 無制限。 */
  canvas_max_turns: number
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
  /** ACE-Step: 歌詞（[Verse] / [Chorus] の構造タグ付き）。空ならインスト。 */
  lyrics?: string
  bpm?: number
  keyscale?: string
  language?: string
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
  grok: HealthStatus
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
  bpm?: number | null
  keyscale?: string | null
  language?: string | null
  negative_tags_draft?: string | null
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

/**
 * PATCH /api/agent/sessions/{id} body（送った項目だけ変わる）。
 *
 * システムプロンプトは作成時に焼き込むので、指示文に載るのは次のターンから。
 * 生成本数の上限判定だけは実行中のループにも即時に効く。
 */
export interface AgentSessionUpdate {
  checkin_mode?: AgentCheckinMode
  /** 生成本数の上限（0 = 無制限）。 */
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

/** リビジョンを作った主体（人の操作か、エージェントの操作か）。 */
export type StudioRevisionActor = 'user' | 'agent'

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
  /** 動画生成の品質（テイク生成のたびにモードと掛け合わせて解決される）。 */
  quality: StudioVideoQuality
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
  /** 動画生成の品質（既定は素の 20 steps = `normal`）。 */
  quality?: StudioVideoQuality
  /** 動画生成の画質（メガピクセル）の作品既定（`null` = ワークフローの既定）。 */
  megapixels?: number | null
  /** 動画生成のアスペクト比の作品既定（`null` = 既定のまま）。 */
  aspect_ratio?: string | null
  /** サンプリング回数の作品既定（`0` = テンプレートの既定のまま）。 */
  steps?: number
  /** この作品から投入するジョブをすべて NSFW 扱いにする（OFF = 非 NSFW 固定）。 */
  nsfw?: boolean
}

/** PATCH /api/studio/projects/{id}（送った項目だけ変わる）。 */
export interface StudioProjectUpdate {
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
  /** 動画生成の品質。 */
  quality?: StudioVideoQuality
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
export interface StudioEpisodeUpdate {
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
export interface StudioSceneUpdate {
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
 * 流れない（エージェントとインスペクタの手がかり）。
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
export interface StudioAssetUpdate {
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
export interface StudioShotUpdate {
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
 * `will_translate`）。組み立てられないカットは 400 ではなく `error` 付きで返る。
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
  /** この本文が実際に英訳されるか。 */
  will_translate: boolean
  /** プロジェクトの設定（引き継ぎを Motion Context で行う = ラテント連続性）。 */
  latent_continuity: boolean
  /** プロジェクトの設定（動画生成の品質）。 */
  quality: StudioVideoQuality
  /**
   * `quality` が実際に効いたか（false = 素へフォールバックした。
   * 理由は `workflow_reason` の末尾に入る）。
   */
  quality_applied: boolean
  /** ラテント連続性で引き継ぐ直前カットの動画（使わないときは null）。 */
  context_video: string | null
  /** 同じく、引き継ぎ元の AV ラテント（ComfyUI 側のパス）。 */
  context_latent: string | null
  /** 組み立てられなかった理由（日本語。空なら問題なし）。 */
  error: string
}

/** GET /api/studio/capabilities: いまの接続先でスタジオの追加機能が使えるか。 */
export interface StudioCapabilities {
  /** ラテント連続性（MiniMaxH3MotionContext 系のカスタムノードが揃っている）。 */
  latent_continuity: boolean
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
}

/** リビジョン 1 件の見出し（GET .../revisions の 1 行）。 */
export interface StudioRevision {
  seq: number
  actor: StudioRevisionActor
  /** 変更内容の短い説明（日本語）。 */
  action: string
  created_at: string
}

/** GET .../revisions/{seq}: そのときのプロジェクト全体つき。 */
export interface StudioRevisionDetail extends StudioRevision {
  snapshot: Record<string, unknown>
}

/** POST /api/studio/demo body。 */
export interface StudioDemoCreate {
  code: string
}

// --------------------------------------------------------------------------
// キャンバス（ドラマスタジオの別ビュー）
// backend/app/models.py のキャンバス系モデルの写し。
//
// カード 1 枚 = スタジオの 1 エンティティで、カードが持つのは「どの行か」
// （kind と entity_id）と「どこに置いてあるか」だけ。中身は studio_* が唯一の
// 正で、text / model のカードだけ中身を `data` に持つ。
//
// キャンバスはスタジオの鏡: カードの無いエンティティには board を読んだ時点で
// サーバーがカードを作るので、「置く / 置かない」という状態は無い。
// --------------------------------------------------------------------------

/**
 * カードの種別。前半 5 つは素材（studio_assets。分類は
 * character / environment / prop / style / reference に対応づく）、
 * scene / shot / media はそれぞれ場・Shot・Take、text / model はキャンバス専用。
 */
export type CanvasCardKind =
  | 'character'
  | 'location'
  | 'object'
  | 'style'
  | 'reference'
  | 'scene'
  | 'shot'
  | 'media'
  | 'text'
  | 'model'

export type CanvasRole = 'user' | 'assistant' | 'event'

/** model カードの生成対象（生成フォームの WorkflowKind と同じ語彙）。 */
export type CanvasModelTarget = 'image' | 'video' | 'audio'

/** text カードの中身（ただの覚え書き）。 */
export interface CanvasTextData {
  body: string
}

/** model カードに書ける生成パラメータ。 */
export interface CanvasModelParams {
  aspect_ratio: string
  megapixels: number
  /** 動画の尺 / 音声の長さ（秒）。 */
  duration: number
  fps: number
  /** null = サーバー設定の既定値に任せる。 */
  loras: LoraRef[]
  video_loras: LoraRef[]
  /** 空 = ジョブの既定値に任せる。 */
  negative_prompt: string
  selects: Record<string, string>
  model_overrides: Record<string, string>
}

/** model カードの中身（「何用の生成設定か」を置いておくカード）。 */
export interface CanvasModelData {
  target: CanvasModelTarget
  /** 既存カタログのワークフロー ID（空 = まだ選んでいない）。 */
  workflow: string
  params: CanvasModelParams
  note: string
}

/** キャンバスの表示位置（プロジェクトごとに 1 つ）。 */
export interface CanvasViewport {
  x: number
  y: number
  zoom: number
}

/** キャンバスに置いた 1 枚。 */
export interface CanvasCard {
  id: string
  project_id: string
  kind: CanvasCardKind
  /** 参照しているスタジオ側の行（text / model は null）。 */
  entity_id: string | null
  /**
   * 置いてあるタブ（null = 作品共通）。**参照カードでは常に null** で、
   * どのタブに出るかはスタジオの所属（場 -> 話）から導く（logic.ts の
   * `cardEpisode`）。使うのは text / model カードだけ。
   */
  episode_id: string | null
  /** キャンバス専用 kind の中身（参照カードでは空）。 */
  data: Record<string, unknown>
  x: number
  y: number
  w: number
  h: number
  z: number
  created_at: string
  updated_at: string
}

/**
 * POST /api/canvas/projects/{id}/cards body。
 *
 * 作るのは**新しいもの**だけ（参照カードは対応するエンティティも一緒に作る）。
 * 既にあるものはキャンバスを開けば自動で並ぶので、置き直す口は無い
 * （media カードは Take が生まれたときにだけできる）。
 */
export interface CanvasCardCreate {
  kind: CanvasCardKind
  /** 新しく作るエンティティの名前（素材）またはタイトル（場 / Shot）。 */
  title?: string
  /** 新しく作る素材の種別。 */
  asset_kind?: StudioAssetKind
  /** shot カードを作るとき、どの場に入れるか（null = 未分類）。 */
  scene_id?: string | null
  /**
   * scene カードならどの話に入れるか（null = 先頭の話）、text / model カード
   * ならどのタブに置くか（null = 作品共通）。
   */
  episode_id?: string | null
  /** text / model の中身。素材カードでは新しい素材の profile として渡る。 */
  data?: Record<string, unknown>
  x?: number
  y?: number
  w?: number
  h?: number
}

/** PATCH /api/canvas/cards/{id} body（`data` は text / model カードのみ）。 */
export interface CanvasCardUpdate {
  data?: Record<string, unknown>
  x?: number
  y?: number
  w?: number
  h?: number
  z?: number
}

/** PUT /api/canvas/cards/{id}/position body（置き場所だけ動かす）。 */
export interface CanvasCardPosition {
  x: number
  y: number
  w?: number
  h?: number
  z?: number
}

/** キャンバスのチャット 1 発言。 */
export interface CanvasMessage {
  id: string
  project_id: string
  ts: string
  role: CanvasRole
  content: string
  /** event の種別（action_result など。会話なら null）。 */
  kind: string | null
  data: Record<string, unknown>
}

/** POST /api/canvas/projects/{id}/messages body。 */
export interface CanvasMessageCreate {
  role?: CanvasRole
  content: string
  kind?: string | null
  data?: Record<string, unknown>
}

/** 添付ファイルの種別（プレビューの出し分けにだけ使う）。 */
export type CanvasAttachmentKind = 'image' | 'video' | 'audio' | 'document'

/**
 * POST /api/canvas/projects/{id}/attachments の応答。
 *
 * 実体はキャンバスの作業ディレクトリの `attachments/` に置かれ、エージェント
 * （grok CLI）はそこを根に動くのでそのまま開ける。ブラウザからは
 * `GET /api/canvas/projects/{id}/attachments/{path}` で読める。
 */
export interface CanvasAttachment {
  /** 元のファイル名（画面に出す名前）。 */
  name: string
  /** workdir 相対パス（`attachments/<file>`）。発言に添えるのはこれ。 */
  path: string
  /** 保存先の絶対パス（エージェントにはこちらを伝える）。 */
  abs_path: string
  kind: CanvasAttachmentKind
}

/**
 * GET /api/canvas/projects/{id}: キャンバス **1 タブ**ぶん。
 *
 * カードの中身はスタジオ側（GET /api/studio/projects/{id}）にあるので、ここに
 * 入るのは置き場所と会話だけ。`cards` と `viewport` は開いているタブのもので、
 * 会話（`messages`）はタブによらず作品に 1 本。
 */
export interface CanvasBoard {
  project_id: string
  /** 開いているタブ（null = 作品共通）。 */
  episode_id: string | null
  viewport: CanvasViewport
  cards: CanvasCard[]
  messages: CanvasMessage[]
}

/**
 * キャンバスのチャットから走らせたエージェントの状態。
 *
 * セッションは持たない（会話は `canvas_messages` が唯一の正）ので、走っているか
 * どうかと実行中の活動だけ。
 */
export interface CanvasAgentState {
  project_id: string
  running: boolean
  /** 実行中の活動（「ツール実行中: …」など）。null = 無し。 */
  activity: string | null
}

/** POST /api/canvas/projects/{id}/agent の応答（保存したユーザー発言つき）。 */
export interface CanvasAgentRun extends CanvasAgentState {
  message: CanvasMessage
}

/** WS /api/ws のキャンバス実行イベント（`type: "canvas"`）。 */
export interface CanvasProgress extends CanvasAgentState {
  type: 'canvas'
  /** 会話に足された 1 件（null = 状態が変わっただけ）。 */
  message: CanvasMessage | null
}
