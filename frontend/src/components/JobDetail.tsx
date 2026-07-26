import type { Job } from '../types'
import { PromptBlock } from './ResultPane'
import { Banner, CopyButton, StatusBadge } from './ui'

export default function JobDetail({
  job,
  onClose,
  onRerun,
  onContinue,
  onDelete,
  busy,
  error,
}: {
  job: Job
  onClose: () => void
  onRerun: (job: Job) => void
  onContinue: (job: Job) => void
  onDelete: (job: Job) => void
  busy: boolean
  error: string | null
}) {
  const params = job.params ?? {}
  const entries = Object.entries(params).filter(
    ([key]) => !['image_prompt', 'video_prompt'].includes(key),
  )

  return (
    <div className="fixed inset-0 z-30 flex justify-end bg-black/60" onClick={onClose}>
      <aside
        className="flex h-full w-full max-w-md flex-col border-l border-ink-600 bg-ink-800"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="flex items-center gap-2 border-b border-ink-600 px-4 py-3">
          <StatusBadge status={job.status} />
          <span className="truncate font-mono text-xs text-slate-500">{job.id}</span>
          <button className="btn-ghost ml-auto !px-2 !py-1" onClick={onClose}>
            ✕
          </button>
        </div>

        <div className="flex-1 space-y-3 overflow-y-auto p-4">
          {error && <Banner>{error}</Banner>}
          {job.error && <Banner>{job.error}</Banner>}

          {job.video_url && (
            <video src={job.video_url} controls className="w-full rounded border border-ink-600" />
          )}
          {job.image_url && (
            <img
              src={job.image_url}
              alt="生成画像"
              className="w-full rounded border border-ink-600"
            />
          )}
          {job.last_frame_url && (
            <div>
              <p className="label">ラストフレーム</p>
              <img
                src={job.last_frame_url}
                alt="ラストフレーム"
                className="w-full rounded border border-ink-600"
              />
            </div>
          )}

          {job.image_prompt && (
            <PromptBlock label="画像プロンプト" text={job.image_prompt} />
          )}
          {job.video_prompt && (
            <PromptBlock label="動画プロンプト" text={job.video_prompt} />
          )}
          {job.user_input && (
            <PromptBlock label="最初の指示" text={job.user_input} />
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
        </div>

        <div className="flex flex-wrap gap-2 border-t border-ink-600 p-3">
          <button
            className="btn-ghost text-xs"
            disabled={busy}
            onClick={() => onRerun(job)}
          >
            再実行（シード再抽選）
          </button>
          <button
            className="btn-ghost text-xs"
            disabled={busy || !job.last_frame_url}
            title={job.last_frame_url ? '' : 'ラストフレームがありません'}
            onClick={() => onContinue(job)}
          >
            続きを生成
          </button>
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
