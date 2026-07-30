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

export interface Settings {
  comfy_url: string
  comfy_api_key: string
  grok_command: string
  grok_model: string
  grok_workdir: string
  /**
   * {"<workflow_id>/<node_id>.<field>": "file.safetensors"} — only non-default
   * entries.
   */
  model_overrides: Record<string, string>
  /**
   * 同じキー形式の「そのスロットで選べるモデルファイル名」。2 件以上あるスロットは
   * 生成フォームで実行時に選べる（SPEC §3.3）。
   */
  model_choices: Record<string, string[]>
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

/** Logical inputs a video workflow can require (mirrors workflows.InputName). */
export type WorkflowInput = 'image' | 'audio' | 'end_image' | 'video'

/** One selectable workflow template (GET /api/options). */
export interface WorkflowOption {
  id: string
  label: string
  kind: 'image' | 'video' | 'audio'
  /** model family — image LoRAs of another family cannot be used with it. */
  family: string
  notes: string
  requires: WorkflowInput[]
  supports: string[]
  accepts_start_image: boolean
  image_label: string
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
  seed: number | null
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
