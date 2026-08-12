import { Check, ClipboardList } from 'lucide-react'

import type { AgentPlan, AgentTask, JobProgress } from '../../types'
import { Badge } from '../ui/badge'
import { Button } from '../ui/button'
import { Progress } from '../ui/progress'
import { TaskIcon } from './common'

interface Props {
  plan: AgentPlan
  /** Sticky compact rendering while the loop runs (AGENT-MODE §1). */
  compact: boolean
  busy: boolean
  progress: Record<string, JobProgress>
  onApprove: () => void
  onRequestChanges: () => void
}

function text(job: Record<string, unknown>, key: string): string {
  const value = job[key]
  return typeof value === 'string' ? value : ''
}

/** "full ・ 9:16 ・ 5s ・ LoRA kaori(0.8)" — the settings that matter at a glance. */
function summarize(job: Record<string, unknown>): string {
  const parts: string[] = []
  if (text(job, 'mode')) parts.push(text(job, 'mode'))
  if (text(job, 'aspect_ratio')) parts.push(text(job, 'aspect_ratio'))
  if (typeof job.duration === 'number') parts.push(`${job.duration}s`)
  if (typeof job.fps === 'number') parts.push(`${job.fps}fps`)
  const loras = Array.isArray(job.loras) ? job.loras : []
  for (const entry of loras) {
    if (entry && typeof entry === 'object') {
      const lora = entry as { lora_name?: string; strength?: number }
      parts.push(`LoRA ${lora.lora_name ?? '?'}(${lora.strength ?? '?'})`)
    }
  }
  return parts.join(' ・ ')
}

function TaskRow({
  task,
  compact,
  progress,
}: {
  task: AgentTask
  compact: boolean
  progress: JobProgress | undefined
}) {
  const percent = Math.round(
    Math.min(1, Math.max(0, progress?.progress ?? 0)) * 100,
  )
  const running = task.status === 'running'

  if (compact) {
    return (
      <span
        className="flex items-center gap-1 rounded-md border border-border bg-surface-sunken px-2 py-0.5 text-[11px] text-foreground/85"
        title={task.label}
      >
        <TaskIcon status={task.status} className="size-3" />
        <span className="max-w-[10rem] truncate">{task.label}</span>
        {running && <span className="tnum text-amber-300">{percent}%</span>}
      </span>
    )
  }

  return (
    <div className="rounded-md border border-border bg-surface-sunken p-2">
      <div className="flex items-start gap-2">
        <TaskIcon status={task.status} className="mt-0.5" />
        <div className="min-w-0 flex-1">
          <p className="truncate text-xs text-foreground/90">{task.label}</p>
          <p className="mt-0.5 truncate text-[11px] text-muted-foreground">
            {summarize(task.job)}
          </p>
          {task.error && (
            <p className="mt-0.5 text-[11px] text-red-400">{task.error}</p>
          )}
        </div>
      </div>

      {running && (
        <div className="mt-1.5 flex items-center gap-2">
          <Progress className="flex-1" value={percent} />
          <span className="tnum text-[11px] text-muted-foreground">{percent}%</span>
        </div>
      )}

      {(text(task.job, 'image_prompt') || text(task.job, 'video_prompt')) && (
        <details className="mt-1.5">
          <summary className="cursor-pointer text-[11px] text-muted-foreground">
            プロンプト
          </summary>
          <div className="mt-1 space-y-1">
            {text(task.job, 'image_prompt') && (
              <p className="whitespace-pre-wrap break-words text-[11px] text-muted-foreground">
                <span className="text-muted-foreground-subtle">画像: </span>
                {text(task.job, 'image_prompt')}
              </p>
            )}
            {text(task.job, 'video_prompt') && (
              <p className="whitespace-pre-wrap break-words text-[11px] text-muted-foreground">
                <span className="text-muted-foreground-subtle">動画: </span>
                {text(task.job, 'video_prompt')}
              </p>
            )}
          </div>
        </details>
      )}
    </div>
  )
}

export default function PlanCard({
  plan,
  compact,
  busy,
  progress,
  onApprove,
  onRequestChanges,
}: Props) {
  if (plan.tasks.length === 0) return null

  return (
    <div className={`card border-primary/50 ${compact ? 'px-3 py-2' : 'p-3'}`}>
      <div className="flex items-center gap-2">
        <h3 className="flex items-center gap-1.5 text-xs font-semibold text-accent-400">
          <ClipboardList className="size-3.5" />
          プラン v{plan.version}
        </h3>
        <span className="tnum text-[11px] text-muted-foreground">
          {plan.tasks.length} 件
        </span>
        {plan.approved ? (
          <Badge variant="success" className="px-2 py-0.5 font-normal">
            承認済み
          </Badge>
        ) : (
          <Badge variant="warning" className="px-2 py-0.5 font-normal">
            未承認
          </Badge>
        )}
      </div>

      {!compact && plan.notes && (
        <p className="mt-1.5 whitespace-pre-wrap text-xs text-muted-foreground">
          {plan.notes}
        </p>
      )}

      <div
        className={
          compact
            ? 'mt-1.5 flex flex-wrap items-center gap-1.5'
            : 'mt-2 space-y-1.5'
        }
      >
        {plan.tasks.map((task) => (
          <TaskRow
            key={task.id || task.label}
            task={task}
            compact={compact}
            progress={task.job_id ? progress[task.job_id] : undefined}
          />
        ))}
      </div>

      {!plan.approved && !compact && (
        <div className="mt-2 flex gap-2">
          <Button size="sm" disabled={busy} onClick={onApprove}>
            <Check />
            承認して開始
          </Button>
          <Button variant="outline" size="sm" onClick={onRequestChanges}>
            修正を指示
          </Button>
        </div>
      )}
    </div>
  )
}
