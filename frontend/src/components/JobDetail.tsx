import type { Job, LibraryItem } from '../types'
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
        className="flex h-full w-full max-w-md flex-col border-l border-ink-600 bg-ink-800"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="flex items-center gap-2 border-b border-ink-600 px-4 py-3">
          <h2 className="text-sm font-semibold text-slate-100">パラメータ詳細</h2>
          <StatusBadge status={job.status} />
          {showNsfw && job.nsfw && <NsfwBadge />}
          <span className="truncate font-mono text-xs text-slate-500">{job.id}</span>
          <button className="btn-ghost ml-auto !px-2 !py-1" onClick={onClose}>
            ✕
          </button>
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
            <details className="rounded border border-ink-600 bg-ink-900 p-2">
              <summary className="cursor-pointer text-xs text-slate-400">
                Grok 生出力
              </summary>
              <div className="mt-2">
                <PromptBlock label="grok_raw" text={job.grok_raw} />
              </div>
            </details>
          )}

          <div className="rounded border border-ink-600 bg-ink-900 p-2">
            <div className="mb-1 flex items-center justify-between">
              <span className="text-xs text-slate-400">パラメータ</span>
              <CopyButton text={JSON.stringify(params, null, 2)} label="JSONをコピー" />
            </div>
            <dl className="space-y-1 text-xs">
              <div className="flex gap-2">
                <dt className="w-28 shrink-0 text-slate-500">mode</dt>
                <dd className="break-all text-slate-300">{job.mode}</dd>
              </div>
              <div className="flex gap-2">
                <dt className="w-28 shrink-0 text-slate-500">created_at</dt>
                <dd className="break-all text-slate-300">{job.created_at}</dd>
              </div>
              {entries.map(([key, value]) => (
                <div key={key} className="flex gap-2">
                  <dt className="w-28 shrink-0 text-slate-500">{key}</dt>
                  <dd className="break-all text-slate-300">
                    {typeof value === 'object' && value !== null
                      ? JSON.stringify(value)
                      : String(value)}
                  </dd>
                </div>
              ))}
            </dl>
          </div>

          {librarySources.length > 0 && (
            <div className="rounded border border-ink-600 bg-ink-900 p-2">
              <p className="mb-1 text-xs text-slate-400">ライブラリ</p>
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

        <div className="flex flex-wrap gap-2 border-t border-ink-600 p-3">
          <button
            className="btn-ghost text-xs"
            disabled={busy}
            onClick={() => onRerun(job, true)}
          >
            再実行（シード再抽選）
          </button>
          <button
            className="btn-ghost text-xs"
            disabled={busy}
            title="元ジョブと同じシードのまま流し直します"
            onClick={() => onRerun(job, false)}
          >
            再実行（同じシード）
          </button>
          <button
            className="btn-ghost text-xs"
            title="このジョブの設定をフォームに書き戻します"
            onClick={() => onRestoreParams(job)}
          >
            パラメータを復元
          </button>
          <button
            className="btn-ghost text-xs"
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
          <button
            className="btn-danger ml-auto text-xs"
            disabled={busy}
            onClick={() => onDelete(job)}
          >
            削除
          </button>
        </div>
      </aside>
    </div>
  )
}
