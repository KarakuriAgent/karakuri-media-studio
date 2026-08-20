import { Download, Film, Library, Loader2 } from 'lucide-react'

import type { TimelineExport } from '../../types'
import { Section } from '../ui'
import { Badge } from '../ui/badge'
import { Button } from '../ui/button'
import { Progress } from '../ui/progress'
import { EXPORT_STATUS_CLASS, EXPORT_STATUS_LABEL } from './timeline'

/**
 * 書き出しのボタンと履歴。
 *
 * 押すと 202 で受け付けられ、進捗は WS の `timeline_export` フレームで届く
 * （取りこぼしても親が履歴を取り直す）。終わったら最新の 1 本をその場で
 * 再生でき、ダウンロードとライブラリ登録もここから。
 */
export default function ExportPanel({
  exports,
  running,
  busy,
  savingId,
  canExport,
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
  onExport: () => void
  onSaveToLibrary: (exportId: string) => void
}) {
  const latest = exports[0] ?? null
  const finished = exports.find((item) => item.status === 'done') ?? null

  return (
    <Section
      title="書き出し"
      right={
        <Button
          type="button"
          size="sm"
          onClick={onExport}
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

        {latest?.status === 'failed' && (
          <p className="rounded border border-red-900/70 bg-red-950/50 px-2 py-1.5 text-xs text-red-200">
            書き出しに失敗しました: {latest.error ?? '理由が分かりません'}
          </p>
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
                  {item.finished_at ?? item.created_at}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </Section>
  )
}
