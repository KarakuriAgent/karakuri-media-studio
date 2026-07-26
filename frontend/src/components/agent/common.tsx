import type { AgentArtifact, AgentStatus, AgentTaskStatus } from '../../types'

/** Statuses where the backend loop is (or may be) working (AGENT-MODE §5.3). */
export const AGENT_ACTIVE: AgentStatus[] = ['running', 'waiting_checkin']

const STATUS_STYLE: Record<AgentStatus, string> = {
  idle: 'border-slate-600 bg-slate-800 text-slate-300',
  planning: 'border-sky-800 bg-sky-950 text-sky-300',
  running: 'border-amber-800 bg-amber-950 text-amber-300',
  waiting_checkin: 'border-violet-800 bg-violet-950 text-violet-300',
  stopped: 'border-slate-600 bg-slate-800 text-slate-400',
  done: 'border-emerald-800 bg-emerald-950 text-emerald-300',
}

const STATUS_LABEL: Record<AgentStatus, string> = {
  idle: '待機',
  planning: 'プラン提示',
  running: '実行中',
  waiting_checkin: 'チェックイン待ち',
  stopped: '停止',
  done: '完了',
}

export function AgentStatusBadge({ status }: { status: AgentStatus }) {
  return (
    <span className={`chip !px-2 !py-0.5 ${STATUS_STYLE[status]}`}>
      {STATUS_LABEL[status] ?? status}
    </span>
  )
}

export const CHECKIN_LABEL: Record<string, string> = {
  every_job: '毎ジョブ確認',
  milestone: '節目のみ',
  auto: '完了まで自走',
}

export const ARTIFACT_ICON: Record<AgentArtifact['kind'], string> = {
  plan: '📋',
  research: '🔍',
  note: '📝',
  image: '🖼',
  video: '🎬',
  frame: '🎞',
}

export const TASK_ICON: Record<AgentTaskStatus, string> = {
  pending: '☐',
  running: '⏳',
  done: '✅',
  failed: '❌',
  skipped: '⏭',
}

/** Work-log icon per system event kind (AGENT-MODE §4). */
export function eventIcon(kind: string | null): string {
  if (!kind) return '⚙'
  if (kind.endsWith('_failed') || kind === 'action_invalid') return '❌'
  if (kind === 'job_done' || kind === 'done') return '✅'
  if (kind === 'plan_proposed') return '📋'
  if (kind === 'note_saved') return '📝'
  if (kind === 'inspect_result') return '🎞'
  if (kind === 'stopped') return '⏹'
  return '⚙'
}

export function formatTime(iso: string): string {
  return iso.replace('T', ' ').replace('+00:00', '').slice(0, 19)
}

export function shortTime(iso: string): string {
  return iso.replace('T', ' ').replace('+00:00', '').slice(5, 16)
}
