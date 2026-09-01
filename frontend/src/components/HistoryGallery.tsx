import { useEffect, useRef } from 'react'
import { FileJson, Film, ImageIcon, Loader2, Music, RefreshCw } from 'lucide-react'
import type { Job } from '../types'
import { Button } from '@/components/ui/button'
import { jobDurationLabel } from '@/lib/duration'
import { NsfwBadge, STATUS_LABELS, StatusBadge } from './ui'

const PENDING = ['queued', 'running', 'prompting']

/** サムネイルのボタンに付ける説明（UUID ではなく日時・状態・プロンプトで示す）。 */
function labelOf(job: Job): string {
  const time = job.created_at.replace('T', ' ').replace('+00:00', '').slice(0, 16)
  const status = STATUS_LABELS[job.status] ?? job.status
  const prompt = (job.video_prompt ?? job.image_prompt ?? '').trim()
  const head = prompt ? `${prompt.slice(0, 40)}${prompt.length > 40 ? '…' : ''}` : ''
  return [time, status, head].filter(Boolean).join(' / ')
}

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
        <span className="tnum text-xs text-muted-foreground">{jobs.length} 件</span>
        <span className="text-xs text-muted-foreground">← / → で切替</span>
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
          <div className="flex w-full flex-col items-center gap-1 py-2 text-center">
            <ImageIcon className="size-6 text-muted-foreground" aria-hidden="true" />
            <p className="text-xs text-muted-foreground">まだジョブがありません</p>
            <p className="text-xs text-muted-foreground">
              左のフォームから実行すると、ここに履歴が並びます
            </p>
          </div>
        )}
        {jobs.map((job) => {
          const thumb = job.last_frame_url ?? job.image_url
          const active = job.id === selectedId
          const pending = PENDING.includes(job.status)
          const failed = job.status === 'failed'
          // 完了したジョブだけ、控えめに所要時間を添える。
          const duration = pending ? null : jobDurationLabel(job)
          // 表示トグルがオフのまま渡ってきた NSFW（このセッションで投げたもの）。
          const blurred = !showNsfw && job.nsfw
          return (
            <button
              key={job.id}
              ref={(element) => {
                items.current[job.id] = element
              }}
              onClick={() => onSelect(job)}
              aria-label={labelOf(job)}
              aria-current={active ? 'true' : undefined}
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
                  alt=""
                  className={`size-full object-cover ${blurred ? 'blur-lg' : ''}`}
                />
              ) : (
                <span className="flex size-full items-center justify-center text-[11px] text-muted-foreground">
                  {pending ? (
                    <Loader2 className="size-5 animate-spin text-primary" />
                  ) : job.audio_output_url ? (
                    // 音声ジョブには映像が無いので、サムネイルの代わりに音符を出す
                    <Music className="size-6 opacity-60" />
                  ) : job.analysis_url ? (
                    // 音源解析は JSON しか作らないので、その印を出す（SPEC §5.2）
                    <FileJson className="size-6 opacity-60" />
                  ) : (
                    'サムネなし'
                  )}
                </span>
              )}

              {blurred && thumb && (
                <span className="pointer-events-none absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 rounded-full border border-pink-800 bg-black/70 px-2 py-0.5 text-[11px] font-semibold tracking-widest text-pink-300">
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
                  <span className="tnum flex items-center gap-0.5 rounded-sm bg-black/60 px-1 py-0.5 text-[11px] text-foreground">
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
              <span className="tnum absolute inset-x-0 bottom-0 flex items-center gap-1 truncate bg-black/60 px-1 py-0.5 text-[11px] text-foreground/85">
                <span className="truncate">
                  {job.created_at.replace('T', ' ').replace('+00:00', '').slice(5, 16)}
                </span>
                {/* 生成にかかった時間（started_at を持たない過去ジョブでは出ない） */}
                {duration && (
                  <span className="shrink-0 text-foreground/60">{duration}</span>
                )}
              </span>
            </button>
          )
        })}
      </div>
    </div>
  )
}
