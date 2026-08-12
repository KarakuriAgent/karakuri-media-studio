import { useEffect, useMemo, useState } from 'react'
import {
  Clapperboard,
  Download,
  Film,
  Loader2,
  Music,
  RotateCcw,
  Trash2,
  Undo2,
  X,
} from 'lucide-react'
import type { Job, JobProgress, LibraryItem, LibrarySource } from '../types'
import { Button } from '@/components/ui/button'
import { Progress } from '@/components/ui/progress'
import LibraryAddButton, { isInLibrary } from './LibraryAddButton'
import { Banner, CopyButton, NsfwBadge, NsfwToggle, StatusBadge } from './ui'

interface Props {
  job: Job | null
  progress: JobProgress | undefined
  /** `randomizeSeed` を false にすると元ジョブと同じシードで流し直す。 */
  onRerun: (job: Job, randomizeSeed?: boolean) => void
  /** ジョブの生成パラメータを左のフォームへ書き戻す。 */
  onRestoreParams: (job: Job) => void
  onContinue: (job: Job) => void
  onDelete: (job: Job) => void
  onOpenDetail: (job: Job) => void
  onToggleNsfw: (job: Job, nsfw: boolean) => void
  busy: boolean
  queue: Job[]
  /**
   * NSFW 表示トグル。オフのあいだも、このセッションで投げたジョブは渡ってくる
   * ので、その映像はぼかして出す（オンにするとぼかしが外れる）。
   */
  showNsfw: boolean
  /** ライブラリに登録したあと、選択肢を取り直してもらう。 */
  onLibraryChanged?: () => void
  /** 登録済みかどうかの判定に使う（`/api/options` の library）。 */
  library?: LibraryItem[]
}

type MediaKind = 'video' | 'image' | 'audio'

interface MediaItem {
  key: string
  kind: MediaKind
  label: string
  url: string
  /** Still frame used for the thumbnail tab (videos have none). */
  thumb: string | null
}

const ACTIVE_STATUSES = ['queued', 'prompting', 'running']

/** ライブラリに登録できる出力（`extra_*` は列を持たないので登録できない）。 */
const LIBRARY_SOURCES: LibrarySource[] = ['image', 'last_frame', 'video', 'audio']

function mediaOf(job: Job): MediaItem[] {
  const items: MediaItem[] = []
  // 音声ジョブの成果物（サムネイルは存在しないので音符アイコンが出る）
  if (job.audio_output_url) {
    items.push({
      key: 'audio',
      kind: 'audio',
      label: '音声',
      url: job.audio_output_url,
      thumb: null,
    })
  }
  // 1 回の生成で複数返ったぶん。列に入るのは
  // 1 つめだけなので、残りはここでタブとして並べる（SPEC §6）。
  for (const [index, url] of (job.extra_output_urls ?? []).entries()) {
    items.push({
      key: `extra_${index}`,
      kind: job.audio_output_url ? 'audio' : 'video',
      label: job.audio_output_url ? `音声 ${index + 2}` : `動画 ${index + 2}`,
      url,
      thumb: null,
    })
  }
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
  onRestoreParams,
  onContinue,
  onDelete,
  onOpenDetail,
  onToggleNsfw,
  busy,
  queue,
  showNsfw,
  onLibraryChanged,
  library,
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
  // 表示トグルがオフのまま届いた NSFW（このセッションで投げたジョブ）はぼかす。
  // トグルをオンにすればそのまま見える。
  const blurred = !showNsfw && Boolean(job?.nsfw)
  const blur = blurred ? 'blur-lg' : ''

  return (
    <div className="flex flex-col gap-2 lg:h-full lg:min-h-0">
      {/* ---------------------------------------------------------- viewer
          狭幅では親の高さに押し込まれず、最低 40vh を確保して内容なりに伸びる。
          lg 以上は従来どおり親の残り高さを埋める。 */}
      <div className="relative flex min-h-[40vh] flex-1 items-center justify-center overflow-hidden rounded-lg border border-border bg-black shadow-elevation-1 lg:min-h-0">
        {!job && (
          <div className="flex flex-col items-center gap-2 px-6 text-center">
            <Clapperboard className="size-10 text-muted-foreground/40" />
            <p className="text-sm text-muted-foreground">結果はここに表示されます</p>
            <p className="text-xs text-muted-foreground/70">
              左のフォームから実行してください
            </p>
          </div>
        )}

        {job && !current && !running && (
          <p className="px-6 text-center text-sm text-muted-foreground">
            {job.status === 'failed' ? '成果物はありません' : 'メディアがありません'}
          </p>
        )}

        {/* 狭幅はビューアが auto 高さなので max-h-full が効かない。vh で上限を置く。 */}
        {job && current?.kind === 'video' && (
          <video
            key={current.url}
            src={current.url}
            controls
            className={`max-h-[60vh] max-w-full lg:max-h-full ${blur}`}
          />
        )}
        {job && current?.kind === 'audio' && (
          <div className="flex w-full max-w-xl flex-col items-center gap-4 px-6">
            <Music className="size-12 text-muted-foreground/60" />
            <audio key={current.url} src={current.url} controls className="w-full" />
          </div>
        )}
        {job && current?.kind === 'image' && (
          <img
            key={current.url}
            src={current.url}
            alt={current.label}
            className={`max-h-[60vh] max-w-full cursor-zoom-in object-contain lg:max-h-full ${blur}`}
            onClick={() => setLightbox(current.url)}
          />
        )}

        {/* ぼかしていることが分かるように、その旨を重ねる（SPEC §7.1）。 */}
        {blurred && current && !running && (
          <span className="pointer-events-none absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 rounded-full border border-pink-800 bg-black/70 px-3 py-1 text-xs font-semibold tracking-widest text-pink-300">
            NSFW
          </span>
        )}

        {/* queue chip */}
        {queue.length > 0 && (
          <div className="absolute right-2 top-2 flex items-center gap-1.5 rounded-full border border-border bg-card/90 px-2.5 py-1 text-xs shadow-elevation-2 backdrop-blur">
            <span className="size-2 animate-pulse rounded-full bg-primary" />
            <span className="tnum text-foreground/85">キュー {queue.length}件</span>
            {queue[0] && <StatusBadge status={queue[0].status} />}
          </div>
        )}

        {/* progress overlay */}
        {running && (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 bg-black/70 backdrop-blur-sm">
            <Loader2 className="size-10 animate-spin text-primary" />
            <p className="tnum text-4xl font-semibold text-foreground">{percent}%</p>
            <Progress value={percent} className="w-64 max-w-[70%]" />
            <StatusBadge status={job.status} />
            {(progress?.node || progress?.message) && (
              <p className="max-w-[80%] truncate text-xs text-muted-foreground">
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
              className={`flex shrink-0 items-center gap-2 rounded-md border p-1 pr-2 text-xs transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50 ${
                item.key === current?.key
                  ? 'border-primary bg-primary/10 text-foreground'
                  : 'border-border bg-card text-muted-foreground hover:text-foreground'
              }`}
            >
              <span className="flex h-10 w-16 items-center justify-center overflow-hidden rounded-sm bg-background">
                {item.thumb ? (
                  <img
                    src={item.thumb}
                    alt={item.label}
                    className={`size-full object-cover ${blur}`}
                  />
                ) : item.kind === 'audio' ? (
                  <Music className="size-4 opacity-60" />
                ) : (
                  <Film className="size-4 opacity-60" />
                )}
              </span>
              {item.kind === 'video' && <Film className="size-3.5" />}
              {item.kind === 'audio' && <Music className="size-3.5" />}
              {item.label}
            </button>
          ))}
        </div>
      )}

      {/* --------------------------------------------------------- toolbar */}
      {job && (
        <div className="flex shrink-0 flex-wrap items-center gap-2 rounded-lg border border-border bg-card px-3 py-2 shadow-elevation-1">
          <StatusBadge status={job.status} />
          <span className="font-mono text-xs text-muted-foreground" title={job.id}>
            {job.id.slice(0, 8)}
          </span>
          <span className="tnum text-xs text-muted-foreground">
            {formatTime(job.created_at)}
          </span>
          <span className="text-xs text-muted-foreground/70">{job.mode}</span>
          {job.nsfw && <NsfwBadge />}

          <div className="ml-auto flex flex-wrap items-center gap-2">
            {current && (
              <Button asChild variant="outline" size="sm">
                <a href={current.url} download={fileNameOf(current.url)}>
                  <Download />
                  ダウンロード
                </a>
              </Button>
            )}
            {/* 表示中の成果物をライブラリへ（SPEC §7.2）。タブの key が
                そのまま登録元の区分になる。 */}
            {current && LIBRARY_SOURCES.includes(current.key as LibrarySource) && (
              <LibraryAddButton
                key={current.key}
                job={job}
                source={current.key as LibrarySource}
                registered={isInLibrary(library, job, current.key as LibrarySource)}
                onAdded={onLibraryChanged}
              />
            )}
            <Button
              variant="outline"
              size="sm"
              disabled={busy}
              onClick={() => onRerun(job, true)}
            >
              <RotateCcw />
              再実行（シード再抽選）
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={busy}
              title="元ジョブと同じシードのまま流し直します"
              onClick={() => onRerun(job, false)}
            >
              <RotateCcw />
              再実行（同じシード）
            </Button>
            <Button
              variant="outline"
              size="sm"
              title="このジョブの設定をフォームに書き戻します"
              onClick={() => onRestoreParams(job)}
            >
              <Undo2 />
              パラメータを復元
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={busy || !job.last_frame_url}
              title={job.last_frame_url ? '' : 'ラストフレームがありません'}
              onClick={() => onContinue(job)}
            >
              続きを生成
            </Button>
            <NsfwToggle
              nsfw={job.nsfw}
              disabled={busy}
              onToggle={(nsfw) => onToggleNsfw(job, nsfw)}
            />
            <Button variant="outline" size="sm" onClick={() => onOpenDetail(job)}>
              詳細
            </Button>
            <Button
              variant="destructive"
              size="sm"
              disabled={busy}
              onClick={() => onDelete(job)}
            >
              <Trash2 />
              削除
            </Button>
          </div>
        </div>
      )}

      {/* --------------------------------------------------------- prompts */}
      {job &&
        (job.image_prompt || job.video_prompt || job.audio_prompt || job.user_input) && (
        <details className="shrink-0 rounded-lg border border-border bg-card px-3 py-2 shadow-elevation-1">
          <summary className="cursor-pointer text-xs text-muted-foreground">
            プロンプト
          </summary>
          <div className="mt-2 max-h-48 space-y-2 overflow-y-auto">
            {job.image_prompt && (
              <PromptBlock label="画像プロンプト" text={job.image_prompt} />
            )}
            {job.video_prompt && (
              <PromptBlock label="動画プロンプト" text={job.video_prompt} />
            )}
            {job.audio_prompt && (
              <PromptBlock label="音声プロンプト" text={job.audio_prompt} />
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
            className={`max-h-full max-w-full object-contain ${blur}`}
            onClick={(event) => event.stopPropagation()}
          />
          <Button
            variant="outline"
            size="icon-sm"
            className="absolute right-4 top-4"
            onClick={() => setLightbox(null)}
            title="閉じる (Esc)"
          >
            <X />
            <span className="sr-only">閉じる</span>
          </Button>
        </div>
      )}
    </div>
  )
}

export function PromptBlock({ label, text }: { label: string; text: string }) {
  return (
    <div className="rounded-md border border-border bg-surface-sunken p-2">
      <div className="mb-1 flex items-center justify-between">
        <span className="text-xs text-muted-foreground">{label}</span>
        <CopyButton text={text} />
      </div>
      <p className="whitespace-pre-wrap break-words text-xs text-foreground/85">{text}</p>
    </div>
  )
}
