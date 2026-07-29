// Mirrors backend/app/models.py

export type JobMode = 'full' | 'i2v' | 'image_only'
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
}

/** One configurable model file of a workflow template (GET /api/models). */
export interface ModelFieldState {
  key: string
  workflow_id: string
  workflow_label: string
  node_id: string
  field: string
  class_type: string
  title: string
  default: string
  value: string
  overridden: boolean
}

/** Which stage a registered LoRA belongs to (mirrors models.LoraTarget). */
export type LoraTarget = 'image' | 'video'

export interface Lora {
  id: number
  display_name: string
  lora_name: string
  trigger_word: string
  default_strength: number
  default_audio: string | null
  sort_order: number
  /** 'image' = Krea 2 の画像ワークフロー / 'video' = LTX 2.3 の動画ワークフロー。 */
  target: LoraTarget
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
  kind: 'image' | 'video'
  notes: string
  requires: WorkflowInput[]
  supports: string[]
  accepts_start_image: boolean
  image_label: string
}

export interface Job {
  id: string
  created_at: string
  mode: JobMode
  status: JobStatus
  user_input: string | null
  image_prompt: string | null
  video_prompt: string | null
  grok_raw: string | null
  params: Record<string, unknown>
  workflow_json: Record<string, unknown>
  comfy_prompt_id: string | null
  image_path: string | null
  video_path: string | null
  last_frame_path: string | null
  source_image: string | null
  audio_path: string | null
  error: string | null
  nsfw: boolean
  /** '' = 未判定 / 'auto' = 自動判定 / 'manual' = 手動指定。 */
  nsfw_source: string
  image_url: string | null
  video_url: string | null
  last_frame_url: string | null
}

export interface JobCreate {
  mode: JobMode
  /** id of the video template to run (see /api/options video_workflows). */
  video_workflow: string
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
  chat_session_id?: string | null
  user_input?: string | null
  /** 明示指定（manual 扱い）。省略すると自動判定に任せる。 */
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
  default_video_workflow: string
  aspect_ratios: string[]
  lora_files: string[]
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
  loras: ChatLoraRef[]
  trigger_text: string
  video_loras?: ChatLoraRef[]
  video_trigger_text?: string
  duration: number
  image_prompt_draft: string
  video_prompt_draft: string
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
  kind: 'plan' | 'note' | 'research' | 'frame' | 'image' | 'video'
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
