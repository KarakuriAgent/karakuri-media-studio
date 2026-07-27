import type {
  AgentApprove,
  AgentCheckinReply,
  AgentReply,
  AgentSession,
  AgentSessionCreate,
  AgentSessionSummary,
  Asset,
  ChatReply,
  ChatSession,
  ChatSessionCreate,
  Health,
  Job,
  JobCreate,
  Lora,
  LoraPayload,
  ModelFieldState,
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

/** FastAPI returns either `{detail: "..."}` or `{detail: [{loc, msg}, ...]}`. */
export function formatDetail(detail: unknown): string {
  if (typeof detail === 'string') return detail
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
  audio_path: 'リファレンス音声',
  source_image: '開始フレーム',
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

function upload<T>(path: string, file: File): Promise<T> {
  const data = new FormData()
  data.append('file', file)
  return request<T>(path, { method: 'POST', body: data })
}

export const api = {
  health: () => request<Health>('/api/health'),
  options: () => request<Options>('/api/options'),

  getSettings: () => request<Settings>('/api/settings'),
  putSettings: (patch: Partial<Settings>) =>
    json<Settings>('PUT', '/api/settings', patch),

  listModels: () => request<ModelFieldState[]>('/api/models'),
  putModels: (overrides: Record<string, string>) =>
    json<ModelFieldState[]>('PUT', '/api/models', { overrides }),

  listLoras: () => request<Lora[]>('/api/loras'),
  createLora: (payload: LoraPayload) => json<Lora>('POST', '/api/loras', payload),
  updateLora: (id: number, payload: Partial<LoraPayload>) =>
    json<Lora>('PUT', `/api/loras/${id}`, payload),
  deleteLora: (id: number) => json<void>('DELETE', `/api/loras/${id}`),

  listAudio: () => request<Asset[]>('/api/assets/audio'),
  listImages: () => request<Asset[]>('/api/assets/image'),
  uploadAudio: (file: File) => upload<Asset>('/api/assets/audio', file),
  uploadImage: (file: File) => upload<Asset>('/api/assets/image', file),

  listJobs: (limit = 60) => request<Job[]>(`/api/jobs?limit=${limit}`),
  getJob: (id: string) => request<Job>(`/api/jobs/${id}`),
  createJob: (payload: JobCreate) => json<Job>('POST', '/api/jobs', payload),
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
  sendAgentMessage: (id: string, content: string) =>
    json<AgentReply>('POST', `/api/agent/sessions/${id}/messages`, { content }),
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
