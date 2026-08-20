import { useState } from 'react'
import { AlertTriangle, Loader2, Trash2 } from 'lucide-react'

import type { TimelineMissingFix, TimelineMissingReport } from '../../types'
import { Modal } from '../ui'
import { Button } from '../ui/button'
import { formatSeconds } from './timeline'

/**
 * メディア欠落の直し方を選ぶダイアログ。
 *
 * 元のテイクの動画が消えたクリップには**同じカットの別テイク**が候補として出る
 * ので、選んで差し替えられる（切り出しは新しいテイクの頭からになる）。候補が
 * 無いものは消すしかないので、「欠落クリップを一括削除」で片づける。
 *
 * 欠落が残っているあいだは書き出しが 400 で断られる（黙って黒＋無音にしない）。
 */
export default function MissingDialog({
  report,
  busy,
  onResolve,
  onClose,
}: {
  report: TimelineMissingReport
  busy: boolean
  onResolve: (fix: TimelineMissingFix) => void
  onClose: () => void
}) {
  const [picked, setPicked] = useState<Record<string, string>>({})

  const replaceable = report.clips.filter((clip) => clip.candidates.length > 0)
  const chosen = Object.keys(picked).length

  return (
    <Modal title="メディア欠落の修復" onClose={onClose}>
      <div className="flex flex-col gap-4 text-xs">
        <p className="flex items-start gap-1.5 rounded border border-red-900/70 bg-red-950/50 px-2 py-1.5 text-red-200">
          <AlertTriangle className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
          <span>
            元のファイルが見つからないクリップが {report.clips.length} 件あります。
            差し替えるか消すまで書き出せません。
          </span>
        </p>

        <ul className="flex flex-col gap-2">
          {report.clips.map((clip) => (
            <li
              key={clip.clip_id}
              className="flex flex-col gap-1.5 rounded border border-border bg-secondary/40 px-2 py-2"
            >
              <p className="truncate font-medium">
                {clip.label || clip.clip_id}
              </p>
              {clip.candidates.length === 0 ? (
                <p className="text-[10px] text-muted-foreground">
                  差し替えられるテイクがありません（削除してください）。
                </p>
              ) : (
                <div className="flex flex-wrap items-center gap-2">
                  <label
                    className="text-[10px] text-muted-foreground"
                    htmlFor={`swap-${clip.clip_id}`}
                  >
                    別のテイクへ
                  </label>
                  <select
                    id={`swap-${clip.clip_id}`}
                    className="h-7 max-w-64 rounded-md border border-border bg-background px-2 text-[11px]"
                    value={picked[clip.clip_id] ?? ''}
                    onChange={(event) =>
                      setPicked((current) => {
                        const next = { ...current }
                        if (event.target.value) {
                          next[clip.clip_id] = event.target.value
                        } else {
                          delete next[clip.clip_id]
                        }
                        return next
                      })
                    }
                  >
                    <option value="">差し替えない</option>
                    {clip.candidates.map((candidate) => (
                      <option key={candidate.take_id} value={candidate.take_id}>
                        {candidate.created_at.slice(0, 16)}
                        {candidate.status ? ` / ${candidate.status}` : ''}
                        {candidate.duration_ms != null
                          ? ` / ${formatSeconds(candidate.duration_ms)}`
                          : ''}
                      </option>
                    ))}
                  </select>
                </div>
              )}
            </li>
          ))}
        </ul>

        <div className="flex flex-wrap items-center justify-end gap-2 border-t border-border pt-3">
          <Button
            type="button"
            size="sm"
            variant="ghost"
            disabled={busy}
            onClick={() => onResolve({ drop_all: true })}
            title="残っている欠落クリップをすべて消す（映像は後ろを詰めます）"
          >
            <Trash2 className="size-4" aria-hidden="true" />
            欠落クリップを一括削除
          </Button>
          <Button type="button" size="sm" variant="ghost" onClick={onClose}>
            やめる
          </Button>
          <Button
            type="button"
            size="sm"
            disabled={busy || chosen === 0 || replaceable.length === 0}
            onClick={() => onResolve({ replace: picked })}
          >
            {busy && (
              <Loader2 className="size-4 animate-spin" aria-hidden="true" />
            )}
            {chosen} 件を差し替え
          </Button>
        </div>
      </div>
    </Modal>
  )
}
