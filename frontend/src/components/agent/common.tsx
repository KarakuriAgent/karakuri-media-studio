import {
  Check,
  CheckCircle2,
  CircleDashed,
  Clapperboard,
  ClipboardList,
  FileText,
  Film,
  Hourglass,
  Image as ImageIcon,
  Music,
  Search,
  Settings,
  ShieldCheck,
  SkipForward,
  Square,
  Tag,
  TrafficCone,
  XCircle,
  type LucideIcon,
} from 'lucide-react'

import type {
  AgentArtifact,
  AgentCheckinMode,
  AgentStatus,
  AgentTaskStatus,
} from '../../types'
import { cn } from '@/lib/utils'

/** Statuses where the backend loop is (or may be) working (AGENT-MODE §5.3). */
export const AGENT_ACTIVE: AgentStatus[] = ['running', 'waiting_checkin']

const STATUS_STYLE: Record<AgentStatus, string> = {
  idle: 'border-border bg-secondary text-foreground/85',
  planning: 'border-sky-800 bg-sky-950 text-sky-300',
  running: 'border-amber-800 bg-amber-950 text-amber-300',
  waiting_checkin: 'border-violet-800 bg-violet-950 text-violet-300',
  stopped: 'border-border bg-secondary text-muted-foreground',
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

export const ARTIFACT_ICON: Record<AgentArtifact['kind'], LucideIcon> = {
  plan: ClipboardList,
  audio: Music,
  research: Search,
  note: FileText,
  image: ImageIcon,
  video: Clapperboard,
  frame: Film,
}

/** 成果物の種別アイコン（見出し・カード・ビューアで同じものを使う）。 */
export function ArtifactIcon({
  kind,
  className,
}: {
  kind: AgentArtifact['kind']
  className?: string
}) {
  const Icon = ARTIFACT_ICON[kind]
  return <Icon className={cn('size-3.5 shrink-0', className)} />
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

const TASK_ICON: Record<AgentTaskStatus, LucideIcon> = {
  pending: CircleDashed,
  running: Hourglass,
  done: CheckCircle2,
  failed: XCircle,
  skipped: SkipForward,
}

const TASK_ICON_STYLE: Record<AgentTaskStatus, string> = {
  pending: 'text-muted-foreground',
  running: 'text-amber-300',
  done: 'text-emerald-400',
  failed: 'text-red-400',
  skipped: 'text-muted-foreground',
}

/** プランの 1 タスクの状態アイコン。 */
export function TaskIcon({
  status,
  className,
}: {
  status: AgentTaskStatus
  className?: string
}) {
  const Icon = TASK_ICON[status]
  return (
    <Icon className={cn('size-3.5 shrink-0', TASK_ICON_STYLE[status], className)} />
  )
}

/** Work-log icon per system event kind (AGENT-MODE §4). */
function eventIconOf(kind: string | null): LucideIcon {
  if (!kind) return Settings
  if (kind.endsWith('_failed') || kind === 'action_invalid') return XCircle
  if (kind === 'job_done' || kind === 'done') return Check
  if (kind === 'plan_proposed') return ClipboardList
  if (kind === 'approval_required') return ShieldCheck
  if (kind === 'action_skipped') return SkipForward
  if (kind === 'note_saved') return FileText
  if (kind === 'artifact_renamed') return Tag
  if (kind === 'inspect_result') return Film
  if (kind === 'stopped') return Square
  if (kind === 'limit_reached') return TrafficCone
  return Settings
}

/** 作業ログ 1 行の頭のアイコン。 */
export function EventIcon({ kind }: { kind: string | null }) {
  const Icon = eventIconOf(kind)
  return <Icon className="mt-px size-3 shrink-0" />
}


export function formatTime(iso: string): string {
  return iso.replace('T', ' ').replace('+00:00', '').slice(0, 19)
}

export function shortTime(iso: string): string {
  return iso.replace('T', ' ').replace('+00:00', '').slice(5, 16)
}
