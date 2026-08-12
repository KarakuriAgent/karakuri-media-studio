import { useEffect, useRef } from 'react'
import { Film, Loader2, Music, RefreshCw } from 'lucide-react'
import type { Job } from '../types'
import { Button } from '@/components/ui/button'
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
  /**
   * NSFW 表示トグル。オフのあいだも、このセッションで投げたジョブは渡ってくる
   * ので、そのサムネイルはぼかして出す（オンにするとぼかしが外れる）。
   */
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
        <h2 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
          履歴
        </h2>
        <span className="tnum text-xs text-muted-foreground/70">{jobs.length} 件</span>
        <span className="text-[11px] text-muted-foreground/50">← / → で切替</span>
        <Button
          variant="outline"
          size="xs"
          className="ml-auto"
          onClick={onReload}
          disabled={loading}
        >
          {loading ? <Loader2 className="animate-spin" /> : <RefreshCw />}
          {loading ? '読込中…' : '更新'}
        </Button>
      </div>

      <div className="flex flex-1 items-center gap-2 overflow-x-auto overflow-y-hidden px-3 pb-2">
        {jobs.length === 0 && (
          <p className="w-full text-center text-xs text-muted-foreground">
            まだジョブがありません
          </p>
        )}
        {jobs.map((job) => {
          const thumb = job.last_frame_url ?? job.image_url
          const active = job.id === selectedId
          const pending = PENDING.includes(job.status)
          const failed = job.status === 'failed'
          // 表示トグルがオフのまま渡ってきた NSFW（このセッションで投げたもの）。
          const blurred = !showNsfw && job.nsfw
          return (
            <button
              key={job.id}
              ref={(element) => {
                items.current[job.id] = element
              }}
              onClick={() => onSelect(job)}
              title={job.video_prompt ?? job.image_prompt ?? job.id}
              className={`relative h-24 w-32 shrink-0 overflow-hidden rounded-md border bg-surface-sunken transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50 ${
                active
                  ? 'border-primary ring-2 ring-primary/60'
                  : failed
                    ? 'border-red-800/70 hover:border-red-700'
                    : 'border-border'
              }`}
            >
              {thumb ? (
                <img
                  src={thumb}
                  alt={job.id}
                  className={`size-full object-cover ${blurred ? 'blur-lg' : ''}`}
                />
              ) : (
                <span className="flex size-full items-center justify-center text-[10px] text-muted-foreground/70">
                  {pending ? (
                    <Loader2 className="size-5 animate-spin text-primary" />
                  ) : job.audio_output_url ? (
                    // 音声ジョブには映像が無いので、サムネイルの代わりに音符を出す
                    <Music className="size-6 opacity-60" />
                  ) : (
                    'サムネなし'
                  )}
                </span>
              )}

              {blurred && thumb && (
                <span className="pointer-events-none absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 rounded-full border border-pink-800 bg-black/70 px-2 py-0.5 text-[10px] font-semibold tracking-widest text-pink-300">
                  NSFW
                </span>
              )}

              <span className="absolute right-1 top-1 flex items-center gap-1">
                {job.nsfw && <NsfwBadge />}
                {job.video_url && (
                  <span className="rounded-sm bg-black/60 p-0.5">
                    <Film className="size-3 text-foreground" />
                  </span>
                )}
                {job.audio_output_url && (
                  <span className="tnum flex items-center gap-0.5 rounded-sm bg-black/60 px-1 py-0.5 text-[10px] text-foreground">
                    <Music className="size-3" />
                    {/* 1 回の生成で複数返るモデルは本数を出す */}
                    {(job.extra_output_urls?.length ?? 0) > 0
                      ? `×${(job.extra_output_urls?.length ?? 0) + 1}`
                      : ''}
                  </span>
                )}
              </span>
              {(pending || failed) && (
                <span className="absolute left-1 top-1">
                  <StatusBadge status={job.status} />
                </span>
              )}
              <span className="tnum absolute inset-x-0 bottom-0 truncate bg-black/60 px-1 py-0.5 text-[10px] text-foreground/85">
                {job.created_at.replace('T', ' ').replace('+00:00', '').slice(5, 16)}
              </span>
            </button>
          )
        })}
      </div>
    </div>
  )
}
