import type {
  AgentArtifact,
  AgentCheckinMode,
  AgentStatus,
  AgentTaskStatus,
} from '../../types'

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

/** プルダウンに出す順（新規作成と後からの変更で同じ並びにする）。 */
export const CHECKIN_MODES: AgentCheckinMode[] = ['every_job', 'milestone', 'auto']

/** 生成本数の上限の表示（0 = 無制限。どのモードでも効く）。 */
export function autoLimitLabel(limit: number): string {
  return limit > 0 ? `上限 ${limit} 本` : '上限 無制限'
}

export const ARTIFACT_ICON: Record<AgentArtifact['kind'], string> = {
  plan: '📋',
  audio: '🎵',
  research: '🔍',
  note: '📝',
  image: '🖼',
  video: '🎬',
  frame: '🎞',
}

/** 種別チップの見出し（リンクカードは中身を見せないので種別だけ出す）。 */
export const ARTIFACT_LABEL: Record<AgentArtifact['kind'], string> = {
  plan: 'プラン',
  audio: '音声',
  research: 'リサーチ',
  note: 'メモ',
  image: '画像',
  video: '動画',
  frame: 'フレーム',
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
  if (kind === 'approval_required') return '🛡'
  if (kind === 'action_skipped') return '⏭'
  if (kind === 'note_saved') return '📝'
  if (kind === 'artifact_renamed') return '🏷'
  if (kind === 'inspect_result') return '🎞'
  if (kind === 'stopped') return '⏹'
  if (kind === 'limit_reached') return '🚦'
  return '⚙'
}

export function formatTime(iso: string): string {
  return iso.replace('T', ' ').replace('+00:00', '').slice(0, 19)
}

export function shortTime(iso: string): string {
  return iso.replace('T', ' ').replace('+00:00', '').slice(5, 16)
}
