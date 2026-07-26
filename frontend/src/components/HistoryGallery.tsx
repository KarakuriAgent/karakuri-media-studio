import type { Job } from '../types'
import { StatusBadge } from './ui'

export default function HistoryGallery({
  jobs,
  selectedId,
  onSelect,
  onReload,
  loading,
}: {
  jobs: Job[]
  selectedId: string | null
  onSelect: (job: Job) => void
  onReload: () => void
  loading: boolean
}) {
  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-2 px-3 py-2">
        <h2 className="text-xs font-semibold uppercase tracking-wider text-slate-400">
          履歴ギャラリー
        </h2>
        <span className="text-xs text-slate-600">{jobs.length} 件</span>
        <button
          className="btn-ghost ml-auto !py-1 text-xs"
          onClick={onReload}
          disabled={loading}
        >
          {loading ? '読込中…' : '更新'}
        </button>
      </div>
      <div className="flex-1 overflow-y-auto px-3 pb-3">
        {jobs.length === 0 && (
          <p className="py-6 text-center text-xs text-slate-600">
            まだジョブがありません
          </p>
        )}
        <div className="grid grid-cols-[repeat(auto-fill,minmax(120px,1fr))] gap-2">
          {jobs.map((job) => {
            const thumb = job.last_frame_url ?? job.image_url
            const active = job.id === selectedId
            const pending = ['queued', 'running', 'prompting'].includes(job.status)
            return (
              <button
                key={job.id}
                onClick={() => onSelect(job)}
                className={`group relative overflow-hidden rounded-md border text-left ${
                  active ? 'border-accent-500' : 'border-ink-600 hover:border-ink-500'
                }`}
                title={job.video_prompt ?? job.image_prompt ?? job.id}
              >
                <div className="flex h-20 items-center justify-center bg-ink-900">
                  {thumb ? (
                    <img
                      src={thumb}
                      alt={job.id}
                      className="h-full w-full object-cover"
                    />
                  ) : (
                    <span className="text-[10px] text-slate-600">
                      {pending ? '生成中' : 'サムネなし'}
                    </span>
                  )}
                </div>
                <div className="flex items-center gap-1 bg-ink-800 px-1.5 py-1">
                  {job.video_url && <span className="text-[10px]">🎬</span>}
                  <span className="truncate text-[10px] text-slate-500">
                    {job.created_at.replace('T', ' ').replace('+00:00', '')}
                  </span>
                </div>
                {(pending || job.status === 'failed') && (
                  <div className="absolute left-1 top-1">
                    <StatusBadge status={job.status} />
                  </div>
                )}
              </button>
            )
          })}
        </div>
      </div>
    </div>
  )
}
