import type { AgentPlan, AgentTask, JobProgress } from '../../types'
import { TASK_ICON } from './common'

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
        className="flex items-center gap-1 rounded border border-ink-600 bg-ink-900 px-2 py-0.5 text-[11px] text-slate-300"
        title={task.label}
      >
        <span>{TASK_ICON[task.status]}</span>
        <span className="max-w-[10rem] truncate">{task.label}</span>
        {running && <span className="tabular-nums text-amber-300">{percent}%</span>}
      </span>
    )
  }

  return (
    <div className="rounded border border-ink-600 bg-ink-900 p-2">
      <div className="flex items-start gap-2">
        <span className="text-sm leading-5">{TASK_ICON[task.status]}</span>
        <div className="min-w-0 flex-1">
          <p className="truncate text-xs text-slate-200">{task.label}</p>
          <p className="mt-0.5 truncate text-[11px] text-slate-500">
            {summarize(task.job)}
          </p>
          {task.error && (
            <p className="mt-0.5 text-[11px] text-red-400">{task.error}</p>
          )}
        </div>
      </div>

      {running && (
        <div className="mt-1.5 flex items-center gap-2">
          <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-ink-600">
            <div
              className="h-full bg-accent-500 transition-all"
              style={{ width: `${percent}%` }}
            />
          </div>
          <span className="text-[11px] tabular-nums text-slate-400">{percent}%</span>
        </div>
      )}

      {(text(task.job, 'image_prompt') || text(task.job, 'video_prompt')) && (
        <details className="mt-1.5">
          <summary className="cursor-pointer text-[11px] text-slate-500">
            プロンプト
          </summary>
          <div className="mt-1 space-y-1">
            {text(task.job, 'image_prompt') && (
              <p className="whitespace-pre-wrap break-words text-[11px] text-slate-400">
                <span className="text-slate-500">画像: </span>
                {text(task.job, 'image_prompt')}
              </p>
            )}
            {text(task.job, 'video_prompt') && (
              <p className="whitespace-pre-wrap break-words text-[11px] text-slate-400">
                <span className="text-slate-500">動画: </span>
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
    <div
      className={`card border-accent-600/60 ${compact ? 'px-3 py-2' : 'p-3'}`}
    >
      <div className="flex items-center gap-2">
        <h3 className="text-xs font-semibold text-accent-400">
          📋 プラン v{plan.version}
        </h3>
        <span className="text-[11px] text-slate-500">{plan.tasks.length} 件</span>
        {plan.approved ? (
          <span className="chip !px-2 !py-0.5 border-emerald-800 bg-emerald-950 text-emerald-300">
            承認済み
          </span>
        ) : (
          <span className="chip !px-2 !py-0.5 border-amber-800 bg-amber-950 text-amber-300">
            未承認
          </span>
        )}
      </div>

      {!compact && plan.notes && (
        <p className="mt-1.5 whitespace-pre-wrap text-xs text-slate-400">
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
          <button
            className="btn-primary !py-1.5 text-xs"
            disabled={busy}
            onClick={onApprove}
          >
            ✔ 承認して開始
          </button>
          <button className="btn-ghost !py-1.5 text-xs" onClick={onRequestChanges}>
            修正を指示
          </button>
        </div>
      )}
    </div>
  )
}
