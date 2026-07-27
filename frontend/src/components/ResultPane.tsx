import { useEffect, useMemo, useState } from 'react'
import type { Job, JobProgress } from '../types'
import { Banner, CopyButton, NsfwBadge, NsfwToggle, StatusBadge } from './ui'

interface Props {
  job: Job | null
  progress: JobProgress | undefined
  onRerun: (job: Job) => void
  onContinue: (job: Job) => void
  onDelete: (job: Job) => void
  onOpenDetail: (job: Job) => void
  onToggleNsfw: (job: Job, nsfw: boolean) => void
  busy: boolean
  queue: Job[]
  /** NSFW 表示トグル（オンのときだけ 🫣 バッジを出す）。 */
  showNsfw: boolean
}

type MediaKind = 'video' | 'image'

interface MediaItem {
  key: string
  kind: MediaKind
  label: string
  url: string
  /** Still frame used for the thumbnail tab (videos have none). */
  thumb: string | null
}

const ACTIVE_STATUSES = ['queued', 'prompting', 'running']

function mediaOf(job: Job): MediaItem[] {
  const items: MediaItem[] = []
  if (job.video_url) {
    items.push({
      key: 'video',
      kind: 'video',
      label: '動画',
      url: job.video_url,
      thumb: job.last_frame_url ?? job.image_url,
    })
  }
  if (job.image_url) {
    items.push({
      key: 'image',
      kind: 'image',
      label: '生成画像',
      url: job.image_url,
      thumb: job.image_url,
    })
  }
  if (job.last_frame_url) {
    items.push({
      key: 'last_frame',
      kind: 'image',
      label: 'ラストフレーム',
      url: job.last_frame_url,
      thumb: job.last_frame_url,
    })
  }
  return items
}

function fileNameOf(url: string): string {
  const clean = url.split('?')[0]
  return clean.slice(clean.lastIndexOf('/') + 1) || 'download'
}

function formatTime(iso: string): string {
  return iso.replace('T', ' ').replace('+00:00', '').slice(0, 19)
}

export default function ResultPane({
  job,
  progress,
  onRerun,
  onContinue,
  onDelete,
  onOpenDetail,
  onToggleNsfw,
  busy,
  queue,
  showNsfw,
}: Props) {
  const media = useMemo(() => (job ? mediaOf(job) : []), [job])
  const [selectedKey, setSelectedKey] = useState<string | null>(null)
  const [lightbox, setLightbox] = useState<string | null>(null)

  // Job (or its outputs) changed: fall back to the best available media.
  useEffect(() => {
    setSelectedKey((current) =>
      current && media.some((item) => item.key === current)
        ? current
        : (media[0]?.key ?? null),
    )
  }, [media])

  // New job selected: reset to its best media (video first) and close the lightbox.
  useEffect(() => {
    setSelectedKey(null)
    setLightbox(null)
  }, [job?.id])

  useEffect(() => {
    if (!lightbox) return
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setLightbox(null)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [lightbox])

  const current = media.find((item) => item.key === selectedKey) ?? media[0] ?? null
  const running = job != null && ACTIVE_STATUSES.includes(job.status)
  const percent = Math.round(Math.min(1, Math.max(0, progress?.progress ?? 0)) * 100)

  return (
    <div className="flex h-full min-h-0 flex-col gap-2">
      {/* ---------------------------------------------------------- viewer */}
      <div className="relative flex min-h-0 flex-1 items-center justify-center overflow-hidden rounded-lg border border-ink-600 bg-black">
        {!job && (
          <div className="flex flex-col items-center gap-2 px-6 text-center">
            <span className="text-4xl opacity-40">🎬</span>
            <p className="text-sm text-slate-500">結果はここに表示されます</p>
            <p className="text-xs text-slate-600">左のフォームから実行してください</p>
          </div>
        )}

        {job && !current && !running && (
          <p className="px-6 text-center text-sm text-slate-600">
            {job.status === 'failed' ? '成果物はありません' : 'メディアがありません'}
          </p>
        )}

        {job && current?.kind === 'video' && (
          <video
            key={current.url}
            src={current.url}
            controls
            className="max-h-full max-w-full"
          />
        )}
        {job && current?.kind === 'image' && (
          <img
            key={current.url}
            src={current.url}
            alt={current.label}
            className="max-h-full max-w-full cursor-zoom-in object-contain"
            onClick={() => setLightbox(current.url)}
          />
        )}

        {/* queue chip */}
        {queue.length > 0 && (
          <div className="absolute right-2 top-2 flex items-center gap-1.5 rounded-full border border-ink-500 bg-ink-800/90 px-2.5 py-1 text-xs backdrop-blur">
            <span className="h-2 w-2 animate-pulse rounded-full bg-accent-500" />
            <span className="text-slate-300">キュー {queue.length}件</span>
            {queue[0] && <StatusBadge status={queue[0].status} />}
          </div>
        )}

        {/* progress overlay */}
        {running && (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 bg-black/70 backdrop-blur-sm">
            <div className="h-10 w-10 animate-spin rounded-full border-2 border-ink-500 border-t-accent-500" />
            <p className="text-4xl font-semibold tabular-nums text-slate-100">
              {percent}%
            </p>
            <div className="h-1.5 w-64 max-w-[70%] overflow-hidden rounded-full bg-ink-600">
              <div
                className="h-full bg-accent-500 transition-all"
                style={{ width: `${percent}%` }}
              />
            </div>
            <StatusBadge status={job.status} />
            {(progress?.node || progress?.message) && (
              <p className="max-w-[80%] truncate text-xs text-slate-400">
                {progress?.node ? `ノード ${progress.node}` : ''}
                {progress?.node && progress?.message ? ' — ' : ''}
                {progress?.message ?? ''}
              </p>
            )}
          </div>
        )}

        {/* failure banner */}
        {job?.error && (
          <div className="absolute inset-x-3 bottom-3">
            <Banner>{job.error}</Banner>
          </div>
        )}
      </div>

      {/* ------------------------------------------------------ media tabs */}
      {media.length > 1 && (
        <div className="flex shrink-0 gap-2 overflow-x-auto">
          {media.map((item) => (
            <button
              key={item.key}
              onClick={() => setSelectedKey(item.key)}
              title={item.label}
              className={`flex shrink-0 items-center gap-2 rounded-md border p-1 pr-2 text-xs transition-colors ${
                item.key === current?.key
                  ? 'border-accent-500 bg-accent-500/10 text-accent-400'
                  : 'border-ink-600 bg-ink-800 text-slate-400 hover:border-ink-500'
              }`}
            >
              <span className="flex h-10 w-16 items-center justify-center overflow-hidden rounded bg-ink-900">
                {item.thumb ? (
                  <img src={item.thumb} alt={item.label} className="h-full w-full object-cover" />
                ) : (
                  <span className="text-sm">🎬</span>
                )}
              </span>
              {item.kind === 'video' && <span>🎬</span>}
              {item.label}
            </button>
          ))}
        </div>
      )}

      {/* --------------------------------------------------------- toolbar */}
      {job && (
        <div className="flex shrink-0 flex-wrap items-center gap-2 rounded-lg border border-ink-600 bg-ink-800 px-3 py-2">
          <StatusBadge status={job.status} />
          <span className="font-mono text-xs text-slate-500" title={job.id}>
            {job.id.slice(0, 8)}
          </span>
          <span className="text-xs text-slate-500">{formatTime(job.created_at)}</span>
          <span className="text-xs text-slate-600">{job.mode}</span>
          {showNsfw && job.nsfw && <NsfwBadge />}

          <div className="ml-auto flex flex-wrap items-center gap-2">
            {current && (
              <a
                className="btn-ghost !py-1 text-xs"
                href={current.url}
                download={fileNameOf(current.url)}
              >
                ダウンロード
              </a>
            )}
            <button
              className="btn-ghost !py-1 text-xs"
              disabled={busy}
              onClick={() => onRerun(job)}
            >
              再実行（シード再抽選）
            </button>
            <button
              className="btn-ghost !py-1 text-xs"
              disabled={busy || !job.last_frame_url}
              title={job.last_frame_url ? '' : 'ラストフレームがありません'}
              onClick={() => onContinue(job)}
            >
              続きを生成
            </button>
            <NsfwToggle
              nsfw={job.nsfw}
              disabled={busy}
              onToggle={(nsfw) => onToggleNsfw(job, nsfw)}
            />
            <button className="btn-ghost !py-1 text-xs" onClick={() => onOpenDetail(job)}>
              詳細
            </button>
            <button
              className="btn-danger !py-1 text-xs"
              disabled={busy}
              onClick={() => onDelete(job)}
            >
              削除
            </button>
          </div>
        </div>
      )}

      {/* --------------------------------------------------------- prompts */}
      {job && (job.image_prompt || job.video_prompt || job.user_input) && (
        <details className="shrink-0 rounded-lg border border-ink-600 bg-ink-800 px-3 py-2">
          <summary className="cursor-pointer text-xs text-slate-400">プロンプト</summary>
          <div className="mt-2 max-h-48 space-y-2 overflow-y-auto">
            {job.image_prompt && (
              <PromptBlock label="画像プロンプト" text={job.image_prompt} />
            )}
            {job.video_prompt && (
              <PromptBlock label="動画プロンプト" text={job.video_prompt} />
            )}
            {job.user_input && <PromptBlock label="最初の指示" text={job.user_input} />}
          </div>
        </details>
      )}

      {/* -------------------------------------------------------- lightbox */}
      {lightbox && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/90 p-6"
          onClick={() => setLightbox(null)}
        >
          <img
            src={lightbox}
            alt="拡大表示"
            className="max-h-full max-w-full object-contain"
            onClick={(event) => event.stopPropagation()}
          />
          <button
            className="btn-ghost absolute right-4 top-4 !px-2 !py-1"
            onClick={() => setLightbox(null)}
            title="閉じる (Esc)"
          >
            ✕
          </button>
        </div>
      )}
    </div>
  )
}

export function PromptBlock({ label, text }: { label: string; text: string }) {
  return (
    <div className="rounded border border-ink-600 bg-ink-900 p-2">
      <div className="mb-1 flex items-center justify-between">
        <span className="text-xs text-slate-400">{label}</span>
        <CopyButton text={text} />
      </div>
      <p className="whitespace-pre-wrap break-words text-xs text-slate-300">{text}</p>
    </div>
  )
}
