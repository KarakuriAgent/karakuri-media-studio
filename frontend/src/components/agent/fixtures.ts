/** テスト用のセッション組み立てヘルパー（AgentSession の既定値を埋める）。 */
import type { AgentMessage, AgentSession } from '../../types'

export function message(
  role: AgentMessage['role'],
  content: string,
  extra: Partial<AgentMessage> = {},
): AgentMessage {
  return {
    role,
    content,
    ts: '2026-07-27T00:00:00',
    kind: null,
    data: {},
    ...extra,
  }
}

export function session(overrides: Partial<AgentSession> = {}): AgentSession {
  return {
    id: 'session-1',
    created_at: '2026-07-27T00:00:00',
    title: 'テストセッション',
    status: 'idle',
    checkin_mode: 'milestone',
    auto_limit: 5,
    messages: [message('system', 'system prompt')],
    plan: { version: 0, notes: '', approved: false, tasks: [] },
    artifacts: [],
    nsfw: false,
    nsfw_source: '',
    thinking: false,
    activity: null,
    ...overrides,
  }
}
