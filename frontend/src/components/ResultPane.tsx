import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Clapperboard,
  Download,
  EyeOff,
  Film,
  Loader2,
  MoreHorizontal,
  Music,
  RotateCcw,
  Square,
  Star,
  Trash2,
  Undo2,
  X,
} from 'lucide-react'
import type { Job, JobProgress, LibraryItem, LibrarySource } from '../types'
import { Button } from '@/components/ui/button'
import { Progress } from '@/components/ui/progress'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { formatElapsed, isJobFinished, jobDurationLabel } from '@/lib/duration'
import LibraryAddButton, { isInLibrary } from './LibraryAddButton'
import { Banner, CopyButton, NsfwBadge, StatusBadge } from './ui'

interface Props {
  job: Job | null
  progress: JobProgress | undefined
  /** `randomizeSeed` を false にすると元ジョブと同じシードで流し直す。 */
  onRerun: (job: Job, randomizeSeed?: boolean) => void
  /** ジョブの生成パラメータを左のフォームへ書き戻す。 */
  onRestoreParams: (job: Job) => void
  onContinue: (job: Job) => void
  onDelete: (job: Job) => void
  onCancel: (job: Job) => void
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

/**
 * 生成にかかった時間。終わったジョブは確定値（「生成 1分23秒」）、走っている
 * あいだは 1 秒ごとに伸びる経過（「経過 0:42」）を出す。started_at を持たない
 * 過去のジョブでは何も出さない。
 */
function JobDuration({ job }: { job: Job }) {
  const running = !isJobFinished(job.status)
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!running || !job.started_at) return
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [running, job.started_at])

  if (running) {
    if (!job.started_at) return null
    const started = Date.parse(job.started_at)
    if (Number.isNaN(started)) return null
    return (
      <span className="tnum text-xs text-muted-foreground">
        経過 {formatElapsed(Math.max(0, now - started))}
      </span>
    )
  }
  const label = jobDurationLabel(job)
  if (!label) return null
  return <span className="tnum text-xs text-muted-foreground">生成 {label}</span>
}

export default function ResultPane({
  job,
  progress,
  onRerun,
  onRestoreParams,
  onContinue,
  onDelete,
  onCancel,
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
  // 「…」メニューから開くライブラリ登録欄（ツールバーの 2 段目に出す）。
  const [libraryOpen, setLibraryOpen] = useState(false)
  // ライトボックスを閉じたら、開いたトリガー（画像ボタン）へフォーカスを戻す。
  const zoomTriggerRef = useRef<HTMLButtonElement | null>(null)
  const lightboxCloseRef = useRef<HTMLButtonElement | null>(null)

  const closeLightbox = useCallback(() => {
    setLightbox(null)
    const trigger = zoomTriggerRef.current
    if (trigger?.isConnected) trigger.focus()
  }, [])

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
    setLibraryOpen(false)
  }, [job?.id])

  useEffect(() => {
    if (!lightbox) return
    // 開いたら閉じるボタンへフォーカスを移す（キーボードで閉じられるように）。
    lightboxCloseRef.current?.focus()
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') closeLightbox()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [lightbox, closeLightbox])

  const current = media.find((item) => item.key === selectedKey) ?? media[0] ?? null
  const running = job != null && ACTIVE_STATUSES.includes(job.status)
  const percent = Math.round(Math.min(1, Math.max(0, progress?.progress ?? 0)) * 100)
  // 表示トグルがオフのまま届いた NSFW（このセッションで投げたジョブ）はぼかす。
  // トグルをオンにすればそのまま見える。
  const blurred = !showNsfw && Boolean(job?.nsfw)
  const blur = blurred ? 'blur-lg' : ''
  // 表示中の成果物がライブラリに登録できるか（`extra_*` は列を持たないので不可）。
  const libraryEligible = Boolean(
    current && LIBRARY_SOURCES.includes(current.key as LibrarySource),
  )
  const libraryRegistered = Boolean(
    job && libraryEligible && isInLibrary(library, job, current!.key as LibrarySource),
  )

  return (
    <div className="flex flex-col gap-2 lg:h-full lg:min-h-0">
      {/* ---------------------------------------------------------- viewer
          狭幅では親の高さに押し込まれず、最低 40vh を確保して内容なりに伸びる。
          lg 以上は従来どおり親の残り高さを埋める。 */}
      <div className="relative flex min-h-[40vh] flex-1 items-center justify-center overflow-hidden rounded-lg border border-border bg-black shadow-elevation-1 lg:min-h-0">
        {!job && (
          <div className="flex flex-col items-center gap-2 px-6 text-center">
            <Clapperboard className="size-10 text-muted-foreground-subtle" />
            <p className="text-sm text-muted-foreground">結果はここに表示されます</p>
            <p className="text-xs text-muted-foreground">
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
            <Music className="size-12 text-muted-foreground-subtle" />
            <audio key={current.url} src={current.url} controls className="w-full" />
          </div>
        )}
        {job && current?.kind === 'image' && (
          <button
            key={current.url}
            type="button"
            ref={zoomTriggerRef}
            aria-label={`${current.label}を拡大表示`}
            className="flex max-h-full max-w-full cursor-zoom-in items-center justify-center focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
            onClick={() => setLightbox(current.url)}
          >
            <img
              src={current.url}
              alt=""
              className={`max-h-[60vh] max-w-full object-contain lg:max-h-full ${blur}`}
            />
          </button>
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
        {running && job && (
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
            <Button
              variant="outline"
              size="sm"
              disabled={busy}
              title="このジョブを停止します"
              onClick={() => onCancel(job)}
            >
              <Square />
              停止
            </Button>
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
          <JobDuration job={job} />
          <span className="text-xs text-muted-foreground">{job.mode}</span>
          {job.nsfw && <NsfwBadge />}

          {/* 主要 3 つ（ダウンロード / 続きを生成 / 再実行）と詳細だけを前面に置き、
              残りは「…」メニューへ畳む。狭幅でも 1〜2 行に収まる。 */}
          <div className="ml-auto flex flex-wrap items-center gap-2">
            {current && (
              <Button asChild size="sm">
                <a href={current.url} download={fileNameOf(current.url)}>
                  <Download />
                  ダウンロード
                </a>
              </Button>
            )}
            <Button
              variant="outline"
              size="sm"
              disabled={busy || !job.last_frame_url}
              title={job.last_frame_url ? '' : 'ラストフレームがありません'}
              onClick={() => onContinue(job)}
            >
              続きを生成
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={busy}
              title="シードを引き直して流し直します"
              onClick={() => onRerun(job, true)}
            >
              <RotateCcw />
              再実行
            </Button>
            <Button variant="outline" size="sm" onClick={() => onOpenDetail(job)}>
              詳細
            </Button>

            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="outline" size="icon-sm" aria-label="その他の操作">
                  <MoreHorizontal />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="w-64">
                <DropdownMenuItem
                  disabled={busy}
                  onSelect={() => onRerun(job, false)}
                >
                  <RotateCcw />
                  <MenuLabel
                    label="同じシードで再実行"
                    hint={
                      busy
                        ? '生成中は実行できません'
                        : '元ジョブと同じシードのまま流し直します'
                    }
                  />
                </DropdownMenuItem>
                <DropdownMenuItem onSelect={() => onRestoreParams(job)}>
                  <Undo2 />
                  <MenuLabel
                    label="パラメータを復元"
                    hint="このジョブの設定をフォームに書き戻します"
                  />
                </DropdownMenuItem>
                {/* 表示中の成果物をライブラリへ（SPEC §7.2）。タブの key が
                    そのまま登録元の区分になる。 */}
                <DropdownMenuItem
                  disabled={!libraryEligible || libraryRegistered}
                  onSelect={() => setLibraryOpen(true)}
                >
                  <Star className={libraryRegistered ? 'fill-current' : undefined} />
                  <MenuLabel
                    label="ライブラリに追加"
                    hint={
                      libraryRegistered
                        ? '登録済みです'
                        : !libraryEligible
                          ? 'この出力はライブラリに登録できません'
                          : '履歴を消しても素材として残ります'
                    }
                  />
                </DropdownMenuItem>
                <DropdownMenuItem
                  disabled={busy}
                  onSelect={() => onToggleNsfw(job, !job.nsfw)}
                >
                  <EyeOff />
                  <MenuLabel
                    label={job.nsfw ? 'NSFW 指定を外す' : 'NSFW として印を付ける'}
                    hint={busy ? '生成中は変更できません' : undefined}
                  />
                </DropdownMenuItem>
                <DropdownMenuSeparator />
                <DropdownMenuItem
                  disabled={busy}
                  className="text-red-300 focus:bg-destructive/20 focus:text-red-200"
                  onSelect={() => onDelete(job)}
                >
                  <Trash2 />
                  <MenuLabel
                    label="削除"
                    hint={busy ? '生成中は削除できません' : '生成物ごと消えます'}
                  />
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>

          {/* 「ライブラリに追加」を選んだときだけ、ツールバーの 2 段目に登録欄を出す
              （名前・タグ・分類はここで指定する）。 */}
          {libraryOpen && current && libraryEligible && (
            <div className="flex w-full flex-wrap items-center gap-2 border-t border-border pt-2">
              <LibraryAddButton
                key={current.key}
                job={job}
                source={current.key as LibrarySource}
                registered={libraryRegistered}
                onAdded={onLibraryChanged}
              />
              <Button
                variant="ghost"
                size="sm"
                className="ml-auto"
                onClick={() => setLibraryOpen(false)}
              >
                閉じる
              </Button>
            </div>
          )}
        </div>
      )}

      {/* --------------------------------------------------------- prompts
          ビューア・タブ・ツールバーと同じ重さの箱が続かないよう、ここは境界を弱める。 */}
      {job &&
        (job.image_prompt || job.video_prompt || job.audio_prompt || job.user_input) && (
        <details className="shrink-0 rounded-lg bg-secondary/30 px-3 py-2">
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
          role="dialog"
          aria-modal="true"
          aria-label="拡大表示"
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/90 p-6"
          onClick={closeLightbox}
        >
          <img
            src={lightbox}
            alt="拡大表示"
            className={`max-h-full max-w-full object-contain ${blur}`}
            onClick={(event) => event.stopPropagation()}
          />
          <Button
            ref={lightboxCloseRef}
            variant="outline"
            size="icon-sm"
            className="absolute right-4 top-4"
            onClick={closeLightbox}
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

/**
 * メニュー項目のラベルと補足。押せない項目はその理由をここに出す
 * （title 属性だとキーボード・タッチでは読めないため）。
 */
function MenuLabel({ label, hint }: { label: string; hint?: string }) {
  return (
    <span className="flex flex-col items-start gap-0.5">
      <span>{label}</span>
      {hint && <span className="text-xs text-muted-foreground">{hint}</span>}
    </span>
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
