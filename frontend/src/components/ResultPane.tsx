import type { Job, JobProgress } from '../types'
import { Banner, CopyButton, StatusBadge } from './ui'

interface Props {
  job: Job | null
  progress: JobProgress | undefined
  onContinue: (job: Job) => void
  busy: boolean
  queue: Job[]
}

export default function ResultPane({ job, progress, onContinue, busy, queue }: Props) {
  const running =
    job != null && (job.status === 'queued' || job.status === 'running' || job.status === 'prompting')
  const percent = Math.round(Math.min(1, Math.max(0, progress?.progress ?? 0)) * 100)

  return (
    <div className="flex flex-col gap-3">
      {queue.length > 0 && (
        <div className="card p-3 text-xs">
          <p className="mb-1 font-semibold text-slate-400">ジョブキュー ({queue.length})</p>
          <ul className="space-y-1">
            {queue.map((item) => (
              <li key={item.id} className="flex items-center gap-2">
                <StatusBadge status={item.status} />
                <span className="truncate text-slate-400">{item.id}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {!job && (
        <div className="card p-6 text-center text-sm text-slate-500">
          結果はここに表示されます。左のフォームから実行してください。
        </div>
      )}

      {job && (
        <div className="card p-3">
          <div className="mb-2 flex items-center gap-2">
            <StatusBadge status={job.status} />
            <span className="truncate font-mono text-xs text-slate-500">{job.id}</span>
            <span className="ml-auto text-xs text-slate-500">{job.mode}</span>
          </div>

          {running && (
            <div className="mb-3">
              <div className="h-2 w-full overflow-hidden rounded-full bg-ink-600">
                <div
                  className="h-full bg-accent-500 transition-all"
                  style={{ width: `${percent}%` }}
                />
              </div>
              <p className="mt-1 text-xs text-slate-400">
                {percent}%
                {progress?.node ? ` — ノード ${progress.node}` : ''}
                {progress?.message ? ` — ${progress.message}` : ''}
              </p>
            </div>
          )}

          {job.error && <Banner>{job.error}</Banner>}

          <div className="flex flex-col gap-3">
            {job.image_url && (
              <div>
                <p className="label">生成画像</p>
                <img
                  src={job.image_url}
                  alt="生成画像"
                  className="w-full rounded border border-ink-600 object-contain"
                />
              </div>
            )}
            {job.video_url && (
              <div>
                <p className="label">動画</p>
                <video
                  src={job.video_url}
                  controls
                  className="w-full rounded border border-ink-600"
                />
              </div>
            )}
            {job.last_frame_url && (
              <div>
                <p className="label">ラストフレーム</p>
                <img
                  src={job.last_frame_url}
                  alt="ラストフレーム"
                  className="w-full rounded border border-ink-600 object-contain"
                />
                <button
                  className="btn-ghost mt-2 w-full text-xs"
                  disabled={busy}
                  onClick={() => onContinue(job)}
                >
                  この画像で続きを生成
                </button>
              </div>
            )}
          </div>

          <div className="mt-3 space-y-2">
            {job.image_prompt && (
              <PromptBlock label="画像プロンプト" text={job.image_prompt} />
            )}
            {job.video_prompt && (
              <PromptBlock label="動画プロンプト" text={job.video_prompt} />
            )}
          </div>
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
