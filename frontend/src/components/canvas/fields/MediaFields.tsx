import type { StudioProjectDetail, StudioTake } from '../../../types'
import {
  TAKE_STATUS_CLASS,
  TAKE_STATUS_LABEL,
  isStale,
  staleTooltip,
} from '../../studio/studio'

/**
 * 生成物カード（Take）の中身。
 *
 * Take は生成の記録なので直せる項目が無い。採用・却下はスタジオの制作タブで
 * 行うため、ここでは成果物と投入内容を読めるようにするだけにする。
 */
export default function MediaFields({
  take,
  detail,
}: {
  take: StudioTake
  detail: StudioProjectDetail
}) {
  const shot = detail.shots.find((item) => item.id === take.shot_id)

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className={`chip !px-2 !py-0.5 ${TAKE_STATUS_CLASS[take.status]}`}>
          {TAKE_STATUS_LABEL[take.status]}
        </span>
        {shot && (
          <span className="text-slate-400">{shot.title || '無題のカット'}</span>
        )}
        {take.video_workflow && (
          <span className="text-slate-500">{take.video_workflow}</span>
        )}
        {isStale(take) && (
          <span className="text-amber-300" title={staleTooltip(take)}>
            ⚠ 古い
          </span>
        )}
      </div>

      <div className="overflow-hidden rounded-md border border-ink-600 bg-ink-900">
        {take.video_url ? (
          <video src={take.video_url} controls className="max-h-72 w-full object-contain" />
        ) : take.last_frame_url ? (
          <img
            src={take.last_frame_url}
            alt="ラストフレーム"
            className="max-h-72 w-full object-contain"
          />
        ) : (
          <p className="px-3 py-6 text-center text-[11px] text-slate-500">
            まだ成果物がありません
          </p>
        )}
      </div>

      {take.error && (
        <p className="whitespace-pre-wrap break-words text-xs text-red-300">
          {take.error}
        </p>
      )}
      {take.warning && (
        <p className="whitespace-pre-wrap break-words text-xs text-amber-300">
          {take.warning}
        </p>
      )}

      <div>
        <p className="label">投入したプロンプト</p>
        <p className="whitespace-pre-wrap break-words rounded-md border border-ink-600 bg-ink-900 p-2 text-xs text-slate-300">
          {take.prompt || '（記録なし）'}
        </p>
      </div>
      {take.source_prompt && (
        <div>
          <p className="label">英訳する前の原文</p>
          <p className="whitespace-pre-wrap break-words rounded-md border border-ink-600 bg-ink-900 p-2 text-xs text-slate-400">
            {take.source_prompt}
          </p>
        </div>
      )}

      <p className="text-[11px] text-slate-500">
        Take の採用・却下はスタジオの制作タブで行います。
      </p>
    </div>
  )
}
