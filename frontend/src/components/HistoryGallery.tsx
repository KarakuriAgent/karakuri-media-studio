import { useEffect, useRef } from 'react'
import type { Job } from '../types'
import { NsfwBadge, StatusBadge } from './ui'

const PENDING = ['queued', 'running', 'prompting']

/** Arrow-key navigation must not steal keystrokes from the form. */
function isTyping(target: EventTarget | null): boolean {
  const element = target as HTMLElement | null
  if (!element) return false
  const tag = element.tagName
  return (
    tag === 'INPUT' ||
    tag === 'TEXTAREA' ||
    tag === 'SELECT' ||
    element.isContentEditable
  )
}

export default function HistoryGallery({
  jobs,
  selectedId,
  onSelect,
  onReload,
  loading,
  showNsfw,
}: {
  jobs: Job[]
  selectedId: string | null
  onSelect: (job: Job) => void
  onReload: () => void
  loading: boolean
  /** オンのときだけ 🔞 バッジを出す（オフのとき NSFW は渡ってこない）。 */
  showNsfw: boolean
}) {
  const items = useRef<Record<string, HTMLButtonElement | null>>({})

  useEffect(() => {
    if (!selectedId) return
    items.current[selectedId]?.scrollIntoView({
      behavior: 'smooth',
      block: 'nearest',
      inline: 'center',
    })
  }, [selectedId])

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return
      if (isTyping(event.target)) return
      if (jobs.length === 0) return
      const index = jobs.findIndex((job) => job.id === selectedId)
      const next =
        index < 0
          ? 0
          : Math.min(jobs.length - 1, Math.max(0, index + (event.key === 'ArrowRight' ? 1 : -1)))
      if (next === index) return
      event.preventDefault()
      onSelect(jobs[next])
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [jobs, selectedId, onSelect])

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-2 px-3 pt-1.5">
        <h2 className="text-xs font-semibold uppercase tracking-wider text-slate-400">
          履歴
        </h2>
        <span className="text-xs text-slate-600">{jobs.length} 件</span>
        <span className="text-[11px] text-slate-700">← / → で切替</span>
        <button
          className="btn-ghost ml-auto !py-1 text-xs"
          onClick={onReload}
          disabled={loading}
        >
          {loading ? '読込中…' : '更新'}
        </button>
      </div>

      <div className="flex flex-1 items-center gap-2 overflow-x-auto overflow-y-hidden px-3 pb-2">
        {jobs.length === 0 && (
          <p className="w-full text-center text-xs text-slate-600">
            まだジョブがありません
          </p>
        )}
        {jobs.map((job) => {
          const thumb = job.last_frame_url ?? job.image_url
          const active = job.id === selectedId
          const pending = PENDING.includes(job.status)
          const failed = job.status === 'failed'
          return (
            <button
              key={job.id}
              ref={(element) => {
                items.current[job.id] = element
              }}
              onClick={() => onSelect(job)}
              title={job.video_prompt ?? job.image_prompt ?? job.id}
              className={`relative h-24 w-32 shrink-0 overflow-hidden rounded-md border bg-ink-900 transition-colors ${
                active
                  ? 'border-accent-500 ring-2 ring-accent-500/60'
                  : failed
                    ? 'border-red-800/70 hover:border-red-700'
                    : 'border-ink-600 hover:border-ink-500'
              }`}
            >
              {thumb ? (
                <img src={thumb} alt={job.id} className="h-full w-full object-cover" />
              ) : (
                <span className="flex h-full w-full items-center justify-center text-[10px] text-slate-600">
                  {pending ? (
                    <span className="h-5 w-5 animate-spin rounded-full border-2 border-ink-500 border-t-accent-500" />
                  ) : (
                    'サムネなし'
                  )}
                </span>
              )}

              <span className="absolute right-1 top-1 flex items-center gap-1">
                {showNsfw && job.nsfw && <NsfwBadge />}
                {job.video_url && (
                  <span className="rounded bg-black/60 px-1 text-[10px]">🎬</span>
                )}
              </span>
              {(pending || failed) && (
                <span className="absolute left-1 top-1">
                  <StatusBadge status={job.status} />
                </span>
              )}
              <span className="absolute inset-x-0 bottom-0 truncate bg-black/60 px-1 py-0.5 text-[10px] text-slate-400">
                {job.created_at.replace('T', ' ').replace('+00:00', '').slice(5, 16)}
              </span>
            </button>
          )
        })}
      </div>
    </div>
  )
}
