import { useState } from 'react'
import { Clapperboard, Loader2, Star, Wand2 } from 'lucide-react'

import { ApiError, api, formatDetail } from '../../api'
import type { TimelineExportItem } from '../../types'
import { Button } from '@/components/ui/button'
import { exportLength, fxLabel, timestampOf } from './library'

/**
 * タイムラインの書き出し 1 本の詳細（ファイルタブの「書き出し」）。
 *
 * 書き出しは実行の記録なのでここでは何も直せない。できるのは「棚に取っておく」
 * （`POST /api/studio/exports/{id}/save-to-library`。同じものが既に棚にあれば
 * サーバーが 409 を返すので、失敗ではなく「登録済みです」と伝える）、
 * 「編集タブで開く」、「もう一度生成の入力に使う」の 3 つ。
 */
export default function ExportDetail({
  item,
  onUseInGenerate,
  onOpenInStudio,
  onRegistered,
}: {
  item: TimelineExportItem
  /** この mp4 を参照動画の欄に入れて [生成] タブへ移る。 */
  onUseInGenerate?: () => void
  /** スタジオタブのこの作品の編集タブへ移る。 */
  onOpenInStudio?: () => void
  /** ライブラリに登録できたら一覧を取り直してもらう。 */
  onRegistered?: () => void
}) {
  const [state, setState] = useState<'idle' | 'busy' | 'done' | 'exists' | 'failed'>(
    'idle',
  )
  const [error, setError] = useState<string | null>(null)

  // 演出まで焼けていればそちらが「完成品」（下地の mp4 も残っている）。
  const finished = item.fx_status === 'done' ? item.fx_video_url : null
  const url = finished ?? item.output_url ?? ''
  const size = item.width && item.height ? `${item.width}x${item.height}` : ''
  const length = exportLength(item)
  const fx = fxLabel(item)

  const register = async () => {
    setState('busy')
    setError(null)
    try {
      await api.saveExportToLibrary(item.id, item.timeline_name)
      setState('done')
      onRegistered?.()
    } catch (caught) {
      // 409 = 同じ書き出しが既に棚にある。失敗ではないので赤くしない。
      if (caught instanceof ApiError && caught.status === 409) {
        setError(formatDetail(caught.detail))
        setState('exists')
        return
      }
      setError(caught instanceof Error ? caught.message : String(caught))
      setState('failed')
    }
  }

  const text =
    state === 'done'
      ? '登録しました'
      : state === 'busy'
        ? '登録中…'
        : state === 'exists'
          ? '登録済みです'
          : state === 'failed'
            ? '登録できません'
            : 'ライブラリに登録'

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-center overflow-hidden rounded-md border border-border bg-background">
        {url ? (
          <video src={url} controls className="max-h-64 w-full" />
        ) : (
          <p className="p-4 text-[11px] text-muted-foreground">
            書き出したファイルが見つかりません。
          </p>
        )}
      </div>

      <dl className="grid grid-cols-[auto_1fr] gap-x-2 gap-y-1 text-[11px] text-muted-foreground">
        <dt>タイムライン</dt>
        <dd className="text-foreground/85">{item.timeline_name}</dd>
        {item.project_name && (
          <>
            <dt>作品</dt>
            <dd className="text-foreground/85">{item.project_name}</dd>
          </>
        )}
        {size && (
          <>
            <dt>解像度</dt>
            <dd className="tnum text-foreground/85">
              {size}
              {item.fps ? ` / ${item.fps}fps` : ''}
            </dd>
          </>
        )}
        {length && (
          <>
            <dt>尺</dt>
            <dd className="tnum text-foreground/85">{length}</dd>
          </>
        )}
        {fx && (
          <>
            <dt>演出</dt>
            <dd className="text-foreground/85">{fx}</dd>
          </>
        )}
        <dt>書き出し日時</dt>
        <dd className="tnum text-foreground/85">{timestampOf(item.created_at)}</dd>
        <dt>書き出し</dt>
        <dd className="break-all text-foreground/85">{item.id}</dd>
      </dl>

      {item.warnings.length > 0 && (
        <div>
          <p className="mb-1 text-[11px] text-muted-foreground">書き出しの注意</p>
          <ul className="max-h-24 overflow-y-auto rounded-md border border-border bg-surface-sunken p-2 text-[11px] text-amber-300">
            {item.warnings.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
        </div>
      )}

      <div className="flex flex-wrap gap-2">
        <Button
          variant="outline"
          size="sm"
          className={
            state === 'failed'
              ? 'text-red-300'
              : state === 'exists'
                ? 'text-muted-foreground'
                : undefined
          }
          disabled={state === 'busy' || state === 'exists' || state === 'done'}
          title={error ?? 'ライブラリに入れると、素材として選べるようになります'}
          onClick={() => void register()}
        >
          {state === 'busy' ? (
            <Loader2 className="animate-spin" />
          ) : (
            <Star
              className={
                state === 'done' || state === 'exists' ? 'fill-current' : undefined
              }
            />
          )}
          {text}
        </Button>
        {onOpenInStudio && (
          <Button variant="outline" size="sm" onClick={onOpenInStudio}>
            <Clapperboard />
            編集タブで開く
          </Button>
        )}
        {onUseInGenerate && url && (
          <Button variant="outline" size="sm" onClick={onUseInGenerate}>
            <Wand2 />
            生成の入力にする
          </Button>
        )}
      </div>

      <p className="text-[11px] text-muted-foreground">
        書き出したファイルは `outputs/exports/` に残ります（ライブラリに登録すると
        コピーが棚にも入ります）。
      </p>
    </div>
  )
}
