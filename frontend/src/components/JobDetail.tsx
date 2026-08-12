import { RotateCcw, Trash2, Undo2, X } from 'lucide-react'
import type { Job, LibraryItem } from '../types'
import { Button } from '@/components/ui/button'
import LibraryAddButton, { isInLibrary, librarySourcesOf } from './LibraryAddButton'
import { PromptBlock } from './ResultPane'
import { Banner, CopyButton, NsfwBadge, NsfwToggle, StatusBadge } from './ui'

export default function JobDetail({
  job,
  onClose,
  onRerun,
  onRestoreParams,
  onContinue,
  onDelete,
  onToggleNsfw,
  busy,
  error,
  showNsfw,
  onLibraryChanged,
  library,
}: {
  job: Job
  onClose: () => void
  /** `randomizeSeed` を false にすると元ジョブと同じシードで流し直す。 */
  onRerun: (job: Job, randomizeSeed?: boolean) => void
  /** ジョブの生成パラメータを生成フォームへ書き戻す。 */
  onRestoreParams: (job: Job) => void
  onContinue: (job: Job) => void
  onDelete: (job: Job) => void
  onToggleNsfw: (job: Job, nsfw: boolean) => void
  busy: boolean
  error: string | null
  showNsfw: boolean
  /** ライブラリに登録したあと、選択肢を取り直してもらう。 */
  onLibraryChanged?: () => void
  /** 登録済みかどうかの判定に使う（`/api/options` の library）。 */
  library?: LibraryItem[]
}) {
  const params = job.params ?? {}
  // 出力ごとに「取っておく」ボタンを出す（SPEC §7.2）
  const librarySources = librarySourcesOf(job)
  const entries = Object.entries(params).filter(
    ([key]) => !['image_prompt', 'video_prompt', 'audio_prompt'].includes(key),
  )

  return (
    <div className="fixed inset-0 z-30 flex justify-end bg-black/60" onClick={onClose}>
      <aside
        className="flex h-full w-full max-w-md flex-col border-l border-border bg-card shadow-elevation-3"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="flex items-center gap-2 border-b border-border px-4 py-3">
          <h2 className="text-sm font-semibold text-foreground">パラメータ詳細</h2>
          <StatusBadge status={job.status} />
          {showNsfw && job.nsfw && <NsfwBadge />}
          <span className="truncate font-mono text-xs text-muted-foreground">
            {job.id}
          </span>
          <Button
            variant="ghost"
            size="icon-sm"
            className="ml-auto"
            title="閉じる"
            onClick={onClose}
          >
            <X />
            <span className="sr-only">閉じる</span>
          </Button>
        </div>

        <div className="flex-1 space-y-3 overflow-y-auto p-4">
          {error && <Banner>{error}</Banner>}
          {job.error && <Banner>{job.error}</Banner>}

          {job.image_prompt && (
            <PromptBlock label="画像プロンプト" text={job.image_prompt} />
          )}
          {job.video_prompt && (
            <PromptBlock label="動画プロンプト" text={job.video_prompt} />
          )}
          {job.audio_prompt && (
            <PromptBlock label="音声プロンプト" text={job.audio_prompt} />
          )}
          {job.user_input && (
            <PromptBlock label="最初の指示" text={job.user_input} />
          )}
          {job.grok_raw && (
            <details className="rounded-md border border-border bg-surface-sunken p-2">
              <summary className="cursor-pointer text-xs text-muted-foreground">
                Grok 生出力
              </summary>
              <div className="mt-2">
                <PromptBlock label="grok_raw" text={job.grok_raw} />
              </div>
            </details>
          )}

          <div className="rounded-md border border-border bg-surface-sunken p-2">
            <div className="mb-1 flex items-center justify-between">
              <span className="text-xs text-muted-foreground">パラメータ</span>
              <CopyButton text={JSON.stringify(params, null, 2)} label="JSONをコピー" />
            </div>
            <dl className="space-y-1 text-xs">
              <div className="flex gap-2">
                <dt className="w-28 shrink-0 text-muted-foreground">mode</dt>
                <dd className="break-all text-foreground/85">{job.mode}</dd>
              </div>
              <div className="flex gap-2">
                <dt className="w-28 shrink-0 text-muted-foreground">created_at</dt>
                <dd className="break-all text-foreground/85">{job.created_at}</dd>
              </div>
              {entries.map(([key, value]) => (
                <div key={key} className="flex gap-2">
                  <dt className="w-28 shrink-0 text-muted-foreground">{key}</dt>
                  <dd className="break-all text-foreground/85">
                    {typeof value === 'object' && value !== null
                      ? JSON.stringify(value)
                      : String(value)}
                  </dd>
                </div>
              ))}
            </dl>
          </div>

          {librarySources.length > 0 && (
            <div className="rounded-md border border-border bg-surface-sunken p-2">
              <p className="mb-1 text-xs text-muted-foreground">ライブラリ</p>
              <div className="flex flex-wrap gap-2">
                {librarySources.map(({ source, label }) => (
                  <LibraryAddButton
                    key={source}
                    job={job}
                    source={source}
                    label={label}
                    registered={isInLibrary(library, job, source)}
                    onAdded={onLibraryChanged}
                  />
                ))}
              </div>
            </div>
          )}
        </div>

        <div className="flex flex-wrap gap-2 border-t border-border p-3">
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
          <Button
            variant="destructive"
            size="sm"
            className="ml-auto"
            disabled={busy}
            onClick={() => onDelete(job)}
          >
            <Trash2 />
            削除
          </Button>
        </div>
      </aside>
    </div>
  )
}
