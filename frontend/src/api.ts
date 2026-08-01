import type {
  AgentApprove,
  AgentAttachment,
  AgentCheckinReply,
  AgentReply,
  AgentSession,
  AgentSessionCreate,
  AgentSessionSummary,
  Asset,
  AudioJobCreate,
  BackendInfo,
  ChatReply,
  ChatSession,
  ChatSessionCreate,
  ComfyTarget,
  Health,
  KieCredits,
  Job,
  JobCreate,
  LibraryCategoryValue,
  LibraryItem,
  LibraryKind,
  LibraryPage,
  LibraryQuery,
  LibrarySheetRequest,
  LibrarySource,
  Lora,
  LoraPayload,
  ModelDownload,
  ModelDownloadAllResult,
  ModelFieldState,
  ModelsDirStatus,
  Options,
  Settings,
} from './types'

export class ApiError extends Error {
  status: number
  detail: unknown

  constructor(status: number, detail: unknown) {
    super(formatDetail(detail) || `HTTP ${status}`)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
  }
}

/**
 * FastAPI returns `{detail: "..."}`, `{detail: [{loc, msg}, ...]}` or, for the
 * errors that carry data（ライブラリの二重登録など）, `{detail: {message, …}}`.
 */
export function formatDetail(detail: unknown): string {
  if (typeof detail === 'string') return detail
  if (
    detail &&
    typeof detail === 'object' &&
    !Array.isArray(detail) &&
    typeof (detail as { message?: unknown }).message === 'string'
  ) {
    return (detail as { message: string }).message
  }
  if (Array.isArray(detail)) {
    return detail
      .map((d) => {
        if (d && typeof d === 'object') {
          const item = d as { loc?: unknown[]; msg?: string }
          const loc = Array.isArray(item.loc)
            ? item.loc.filter((p) => p !== 'body').join('.')
            : ''
          return loc ? `${loc}: ${item.msg ?? ''}` : (item.msg ?? '')
        }
        return String(d)
      })
      .filter(Boolean)
      .join(' / ')
  }
  if (detail == null) return ''
  return typeof detail === 'object' ? JSON.stringify(detail) : String(detail)
}

const FIELD_LABELS: Record<string, string> = {
  image_prompt: '画像プロンプト',
  video_prompt: '動画プロンプト',
  audio_prompt: '音声プロンプト',
  audio_path: 'リファレンス音声',
  source_image: '開始フレーム',
  end_image: '最後のフレーム',
  reference_video: '参照動画',
}

/** Pull `mode 'x' requires: a, b` out of a 422 body into per-field messages. */
export function fieldErrorsFromError(error: unknown): Record<string, string> {
  if (!(error instanceof ApiError)) return {}
  const text = formatDetail(error.detail)
  const match = /requires:\s*(.+)$/.exec(text)
  if (!match) return {}
  const fields: Record<string, string> = {}
  for (const raw of match[1].split(',')) {
    const key = raw.trim()
    if (key in FIELD_LABELS) fields[key] = `${FIELD_LABELS[key]}は必須です`
  }
  return fields
}

async function request<T>(
  path: string,
  init?: RequestInit & { raw?: BodyInit },
): Promise<T> {
  let response: Response
  try {
    response = await fetch(path, init)
  } catch (cause) {
    throw new ApiError(0, `バックエンドに接続できません (${String(cause)})`)
  }
  if (!response.ok) {
    let detail: unknown = response.statusText
    const text = await response.text().catch(() => '')
    if (text) {
      try {
        const parsed = JSON.parse(text) as { detail?: unknown }
        detail = parsed?.detail ?? text
      } catch {
        detail = text
      }
    }
    throw new ApiError(response.status, detail)
  }
  if (response.status === 204) return undefined as T
  const text = await response.text()
  return (text ? JSON.parse(text) : undefined) as T
}

function json<T>(method: string, path: string, body?: unknown): Promise<T> {
  return request<T>(path, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
}

function upload<T>(
  path: string,
  file: File,
  fields: Record<string, string> = {},
): Promise<T> {
  const data = new FormData()
  data.append('file', file)
  for (const [key, value] of Object.entries(fields)) data.append(key, value)
  return request<T>(path, { method: 'POST', body: data })
}

export const api = {
  health: () => request<Health>('/api/health'),

  /** kie.ai の残クレジット（SPEC §5.2）。 */
  kieCredits: () => request<KieCredits>('/api/kie/credits'),

  /** kie.ai のキーを確かめ直す（選択肢に出すかどうかが決まる）。 */
  kieCheck: () => request<KieCredits>('/api/kie/check', { method: 'POST' }),

  /** Grok Build CLI（サブスク枠）を実際に 1 ターン回して確かめる（SPEC §5.2）。 */
  grokCheck: () => request<BackendInfo>('/api/grok/check', { method: 'POST' }),
  options: () => request<Options>('/api/options'),

  getSettings: () => request<Settings>('/api/settings'),
  putSettings: (patch: Partial<Settings>) =>
    json<Settings>('PUT', '/api/settings', patch),

  // モデル指定と LoRA 登録は接続先ごと（SPEC §5）。`target` を省略すると
  // サーバーが現在の接続先を使う。設定ページは編集中の環境を明示的に渡す。
  listModels: (target?: ComfyTarget) =>
    request<ModelFieldState[]>(`/api/models${target ? `?target=${target}` : ''}`),
  /** `choices` を省略すると保存済みの候補リストはそのまま残る。 */
  putModels: (
    overrides: Record<string, string>,
    choices?: Record<string, string[]>,
    target?: ComfyTarget,
  ) =>
    json<ModelFieldState[]>('PUT', '/api/models', { overrides, choices, target }),

  // 不足モデルのダウンロード（SPEC §3.3）。進捗は WS の `model_download` で届く。
  // 落とし先は `target`: ローカルはこのアプリが、RunPod は Pod の API が落とす。
  modelsDirStatus: () => request<ModelsDirStatus>('/api/models/dir-status'),
  listModelDownloads: (target?: ComfyTarget) =>
    request<ModelDownload[]>(
      `/api/models/downloads${target ? `?target=${target}` : ''}`,
    ),
  downloadModel: (
    filename: string,
    url: string,
    subfolder: string,
    target?: ComfyTarget,
  ) =>
    json<ModelDownload>('POST', '/api/models/download', {
      filename,
      url,
      subfolder,
      target,
    }),
  /** 未検出かつ取得元 URL 登録済みのモデルをまとめて落とす。 */
  downloadAllModels: (target?: ComfyTarget) =>
    json<ModelDownloadAllResult>('POST', '/api/models/download-all', { target }),

  listLoras: (target?: ComfyTarget) =>
    request<Lora[]>(`/api/loras${target ? `?target=${target}` : ''}`),
  createLora: (payload: LoraPayload) => json<Lora>('POST', '/api/loras', payload),
  updateLora: (id: number, payload: Partial<LoraPayload>) =>
    json<Lora>('PUT', `/api/loras/${id}`, payload),
  deleteLora: (id: number) => json<void>('DELETE', `/api/loras/${id}`),
  uploadLoraSample: (id: number, file: File) =>
    upload<Lora>(`/api/loras/${id}/samples`, file),
  deleteLoraSample: (id: number, name: string) =>
    json<Lora>('DELETE', `/api/loras/${id}/samples/${encodeURIComponent(name)}`),

  // ライブラリ（SPEC §7.2）。一覧は /api/options にも入っているので、フォームは
  // そちらを使い、ここは登録・更新・削除のために使う。
  listLibrary: (query: LibraryQuery = {}) => {
    const params = new URLSearchParams()
    for (const [key, value] of Object.entries(query)) {
      if (value !== undefined && value !== '') params.set(key, String(value))
    }
    const search = params.toString()
    return request<LibraryPage>(`/api/library${search ? `?${search}` : ''}`)
  },
  /** `category` を省くと未分類で登録される。 */
  uploadToLibrary: (
    kind: LibraryKind,
    file: File,
    tags: string[] = [],
    category: LibraryCategoryValue = 'none',
  ) =>
    upload<LibraryItem>(`/api/library/${kind}`, file, {
      tags: tags.join(','),
      category,
    }),
  /** ジョブの出力をライブラリに取っておく（NSFW は元ジョブを引き継ぐ）。 */
  addJobToLibrary: (
    jobId: string,
    source: LibrarySource,
    name = '',
    tags: string[] = [],
    category: LibraryCategoryValue = 'none',
  ) =>
    json<LibraryItem>('POST', '/api/library/from-job', {
      job_id: jobId,
      source,
      name,
      tags,
      category,
    }),
  /**
   * 選んだ画像素材を 1 枚のリファレンスシートに合成して登録する（SPEC §7.2）。
   *
   * `itemIds` の**並び順に意味がある**（左上から置く）。返るのは出来上がった
   * シートの LibraryItem で、そのまま画像欄（`source_image`）に指定できる。
   */
  createLibrarySheet: (
    itemIds: string[],
    options: Omit<LibrarySheetRequest, 'item_ids'> = {},
  ) =>
    json<LibraryItem>('POST', '/api/library/sheet', {
      item_ids: itemIds,
      ...options,
    }),
  /** 送った項目だけ変える。`category: 'none'` は未分類に戻す指定（SPEC §7.2）。 */
  updateLibraryItem: (
    id: string,
    patch: {
      name?: string
      nsfw?: boolean
      tags?: string[]
      category?: LibraryCategoryValue
    },
  ) => json<LibraryItem>('PATCH', `/api/library/${id}`, patch),
  deleteLibraryItem: (id: string) => json<void>('DELETE', `/api/library/${id}`),

  listAudio: () => request<Asset[]>('/api/assets/audio'),
  listImages: () => request<Asset[]>('/api/assets/image'),
  listVideos: () => request<Asset[]>('/api/assets/video'),
  uploadAudio: (file: File) => upload<Asset>('/api/assets/audio', file),
  uploadImage: (file: File) => upload<Asset>('/api/assets/image', file),
  uploadVideo: (file: File) => upload<Asset>('/api/assets/video', file),

  listJobs: (limit = 60) => request<Job[]>(`/api/jobs?limit=${limit}`),
  getJob: (id: string) => request<Job>(`/api/jobs/${id}`),
  createJob: (payload: JobCreate | AudioJobCreate) =>
    json<Job>('POST', '/api/jobs', payload),
  rerunJob: (id: string) =>
    json<Job>('POST', `/api/jobs/${id}/rerun`, { randomize_seed: true }),
  continueJob: (id: string, body: Record<string, unknown> = {}) =>
    json<Job>('POST', `/api/jobs/${id}/continue`, body),
  deleteJob: (id: string) => json<void>('DELETE', `/api/jobs/${id}`),
  /** NSFW フラグの手動トグル（manual として保存される）。 */
  setJobNsfw: (id: string, nsfw: boolean) =>
    json<Job>('POST', `/api/jobs/${id}/nsfw`, { nsfw }),

  createChatSession: (payload: ChatSessionCreate) =>
    json<ChatSession>('POST', '/api/chat/sessions', payload),
  getChatSession: (id: string) => request<ChatSession>(`/api/chat/sessions/${id}`),
  sendChatMessage: (id: string, content: string) =>
    json<ChatReply>('POST', `/api/chat/sessions/${id}/messages`, { content }),

  // agent mode (AGENT-MODE §5.1)
  createAgentSession: (payload: AgentSessionCreate) =>
    json<AgentSession>('POST', '/api/agent/sessions', payload),
  listAgentSessions: (limit = 50) =>
    request<AgentSessionSummary[]>(`/api/agent/sessions?limit=${limit}`),
  getAgentSession: (id: string) =>
    request<AgentSession>(`/api/agent/sessions/${id}`),
  deleteAgentSession: (id: string) =>
    json<void>('DELETE', `/api/agent/sessions/${id}`),
  sendAgentMessage: (id: string, content: string, attachments: string[] = []) =>
    json<AgentReply>('POST', `/api/agent/sessions/${id}/messages`, {
      content,
      attachments,
    }),
  /** 添付ファイルを workdir の attachments/ に置き、相対パスを受け取る。 */
  uploadAgentAttachment: (id: string, file: File) =>
    upload<AgentAttachment>(`/api/agent/sessions/${id}/attachments`, file),
  approveAgentPlan: (id: string, body: AgentApprove = {}) =>
    json<AgentReply>('POST', `/api/agent/sessions/${id}/approve`, body),
  replyAgentCheckin: (id: string, body: AgentCheckinReply) =>
    json<AgentReply>('POST', `/api/agent/sessions/${id}/checkin`, body),
  setAgentSessionNsfw: (id: string, nsfw: boolean) =>
    json<AgentSession>('POST', `/api/agent/sessions/${id}/nsfw`, { nsfw }),
  stopAgentSession: (id: string) =>
    json<AgentSession>('POST', `/api/agent/sessions/${id}/stop`),
  agentArtifactUrl: (id: string, name: string) =>
    `/api/agent/sessions/${id}/artifacts/${name.split('/').map(encodeURIComponent).join('/')}`,
}

export function wsUrl(path = '/api/ws'): string {
  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${protocol}//${location.host}${path}`
}
