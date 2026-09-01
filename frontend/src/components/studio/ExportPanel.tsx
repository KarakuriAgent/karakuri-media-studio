import { useState } from 'react'
import { Download, Film, Library, Loader2 } from 'lucide-react'

import type {
  TimelineExport,
  TimelineExportFit,
  TimelineExportPreset,
  TimelineExportRequest,
} from '../../types'
import { Section } from '../ui'
import { Badge } from '../ui/badge'
import { Button } from '../ui/button'
import { Checkbox } from '../ui/checkbox'
import { Progress } from '../ui/progress'
import {
  EXPORT_FITS,
  EXPORT_PRESETS,
  EXPORT_STATUS_CLASS,
  EXPORT_STATUS_LABEL,
} from './timeline'

/**
 * 書き出しの設定・ボタン・履歴。
 *
 * 押すと 202 で受け付けられ、進捗は WS の `timeline_export` フレームで届く
 * （取りこぼしても親が履歴を取り直す）。終わったら最新の 1 本をその場で
 * 再生でき、ダウンロードとライブラリ登録もここから。
 *
 * 設定は 3 つ:
 *
 * - **解像度プリセット** … 既定はタイムラインの規格そのまま。fps は変えない。
 * - **収め方** … 縦横比が変わるときにレターボックス（黒帯）か中央クロップか。
 *   プリセットが「タイムライン規格」のときは効かないので出さない。
 * - **ラウドネス正規化** … 既定 ON（-14 LUFS / TP -1.5 dB）。
 */
export default function ExportPanel({
  exports,
  running,
  busy,
  savingId,
  canExport,
  canExportFx,
  onExport,
  onSaveToLibrary,
}: {
  /** 新しい順の履歴。 */
  exports: TimelineExport[]
  /** いま走っている書き出し（無ければ null）。 */
  running: TimelineExport | null
  busy: boolean
  /** ライブラリへ保存中の書き出し id（ボタンを二度押しさせない）。 */
  savingId: string | null
  canExport: boolean
  /**
   * 演出付き（FX トラックを載せた）書き出しを選べるか。Remotion 連携が ON で、
   * 演出が 1 件以上あるときだけ。
   */
  canExportFx?: boolean
  onExport: (body: TimelineExportRequest) => void
  onSaveToLibrary: (exportId: string) => void
}) {
  const [preset, setPreset] = useState<TimelineExportPreset>('timeline')
  const [fit, setFit] = useState<TimelineExportFit>('pad')
  const [loudnorm, setLoudnorm] = useState(true)
  const [fx, setFx] = useState(true)
  const latest = exports[0] ?? null
  const finished = exports.find((item) => item.status === 'done') ?? null
  /** 演出まで載った mp4 のうち、いちばん新しいもの。 */
  const finishedFx =
    exports.find((item) => item.fx_status === 'done' && item.fx_video_url) ?? null
  const withFx = Boolean(canExportFx && fx)

  return (
    <Section
      title="書き出し"
      right={
        <Button
          type="button"
          size="sm"
          onClick={() => onExport({ preset, fit, loudnorm, fx: withFx })}
          disabled={busy || !canExport || running !== null}
          title={
            canExport
              ? '今のタイムラインを 1 本の mp4 に焼く'
              : 'クリップが 1 つも無いので書き出せません'
          }
        >
          {running ? (
            <Loader2 className="size-4 animate-spin" aria-hidden="true" />
          ) : (
            <Film className="size-4" aria-hidden="true" />
          )}
          書き出す
        </Button>
      }
    >
      <div className="flex flex-col gap-3">
        <div className="flex flex-col gap-2 rounded border border-border bg-secondary/30 px-2 py-2 text-[11px]">
          <label className="flex flex-col gap-1">
            <span className="text-muted-foreground">解像度</span>
            <select
              className="h-7 rounded-md border border-border bg-background px-2 text-[11px]"
              value={preset}
              onChange={(event) =>
                setPreset(event.target.value as TimelineExportPreset)
              }
            >
              {EXPORT_PRESETS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>
          {preset !== 'timeline' && (
            <label className="flex flex-col gap-1">
              <span className="text-muted-foreground">縦横比が変わるとき</span>
              <select
                className="h-7 rounded-md border border-border bg-background px-2 text-[11px]"
                value={fit}
                onChange={(event) =>
                  setFit(event.target.value as TimelineExportFit)
                }
              >
                {EXPORT_FITS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </label>
          )}
          <label className="flex cursor-pointer items-center gap-2">
            <Checkbox
              checked={loudnorm}
              onCheckedChange={(value) => setLoudnorm(value === true)}
            />
            <span>ラウドネス正規化（-14 LUFS / TP -1.5 dB）</span>
          </label>
          {canExportFx && (
            <label className="flex cursor-pointer items-center gap-2">
              <Checkbox
                checked={fx}
                onCheckedChange={(value) => setFx(value === true)}
              />
              <span>FX トラックの演出も載せる（Remotion。数分かかります）</span>
            </label>
          )}
          <p className="text-[10px] text-muted-foreground">
            fps はタイムラインの値のままです。
          </p>
        </div>

        {running && (
          <div className="flex flex-col gap-1">
            <div className="flex items-center justify-between text-xs text-muted-foreground">
              <span>{EXPORT_STATUS_LABEL[running.status] ?? running.status}</span>
              <span className="font-mono">
                {Math.round((running.progress ?? 0) * 100)}%
              </span>
            </div>
            <Progress value={Math.round((running.progress ?? 0) * 100)} />
          </div>
        )}

        {latest?.fx_status === 'queued' || latest?.fx_status === 'running' ? (
          <p className="rounded border border-border bg-secondary/40 px-2 py-1.5 text-xs text-muted-foreground">
            演出を載せています（Remotion のレンダリング。数分かかります）…
          </p>
        ) : null}

        {latest?.fx_status === 'failed' && (
          <p className="rounded border border-amber-900/70 bg-amber-950/40 px-2 py-1.5 text-xs text-amber-200">
            演出を載せられませんでした（mp4 そのものは書き出せています。
            履歴の Remotion ジョブに理由が出ます）。
          </p>
        )}

        {finishedFx?.fx_video_url && (
          <div className="flex flex-col gap-2">
            <p className="text-[11px] text-muted-foreground">演出付き</p>
            <video
              src={finishedFx.fx_video_url}
              controls
              preload="metadata"
              className="w-full rounded border border-border bg-black"
            />
            <Button asChild size="sm" variant="secondary">
              <a href={finishedFx.fx_video_url} download="final-fx.mp4">
                <Download className="size-4" aria-hidden="true" />
                ダウンロード（演出付き）
              </a>
            </Button>
          </div>
        )}

        {latest?.status === 'failed' && (
          <p className="rounded border border-red-900/70 bg-red-950/50 px-2 py-1.5 text-xs text-red-200">
            書き出しに失敗しました: {latest.error ?? '理由が分かりません'}
          </p>
        )}

        {finished && finished.warnings.length > 0 && (
          <ul className="rounded border border-amber-900/70 bg-amber-950/40 px-2 py-1.5 text-xs text-amber-200">
            {finished.warnings.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
        )}

        {finished?.output_url && (
          <div className="flex flex-col gap-2">
            <video
              src={finished.output_url}
              controls
              preload="metadata"
              className="w-full rounded border border-border bg-black"
            />
            <div className="flex flex-wrap gap-2">
              <Button asChild size="sm" variant="secondary">
                <a href={finished.output_url} download="final.mp4">
                  <Download className="size-4" aria-hidden="true" />
                  ダウンロード
                </a>
              </Button>
              <Button
                type="button"
                size="sm"
                variant="ghost"
                disabled={savingId === finished.id}
                onClick={() => onSaveToLibrary(finished.id)}
              >
                {savingId === finished.id ? (
                  <Loader2 className="size-4 animate-spin" aria-hidden="true" />
                ) : (
                  <Library className="size-4" aria-hidden="true" />
                )}
                ライブラリへ保存
              </Button>
            </div>
          </div>
        )}

        {exports.length === 0 ? (
          <p className="text-xs text-muted-foreground">まだ書き出していません。</p>
        ) : (
          <ul className="flex flex-col gap-1">
            {exports.slice(0, 5).map((item) => (
              <li
                key={item.id}
                className="flex items-center justify-between gap-2 text-[11px] text-muted-foreground"
              >
                <Badge
                  className={
                    EXPORT_STATUS_CLASS[item.status] ??
                    'border-border bg-secondary text-muted-foreground'
                  }
                >
                  {EXPORT_STATUS_LABEL[item.status] ?? item.status}
                </Badge>
                <span className="truncate font-mono">
                  {item.fx_job_id ? '演出付き / ' : ''}
                  {item.frames == null
                    ? (item.finished_at ?? item.created_at)
                    : `${item.width}x${item.height} ${item.fps}fps ${item.frames}f`}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </Section>
  )
}
