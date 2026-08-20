import { AlertTriangle, Scissors, Trash2 } from 'lucide-react'

import type { TimelineClip } from '../../types'
import { Section } from '../ui'
import { Button } from '../ui/button'
import { formatSeconds, formatTimecode } from './timeline'

/**
 * 選んでいるクリップの中身と、そのクリップに効く操作（分割・削除）。
 *
 * フェーズ 1 は数値を直接いじらせない（トリムはタイムライン上のドラッグ、
 * 並べ替えも同じ）。ここは「いま何が選ばれていて、どこを使っているか」を
 * 読むための面に留める。
 */
export default function ClipInspector({
  clip,
  index,
  canSplit,
  onSplit,
  onDelete,
}: {
  clip: TimelineClip | null
  /** タイムライン上の位置（0 起点。見出しの番号に使う）。 */
  index: number
  /** 再生ヘッドがこのクリップの中にあり、両側に十分な長さが残るか。 */
  canSplit: boolean
  onSplit: () => void
  onDelete: () => void
}) {
  if (!clip) {
    return (
      <Section title="クリップ">
        <p className="text-xs text-muted-foreground">
          タイムラインでクリップを選ぶと、ここに中身が出ます。
        </p>
      </Section>
    )
  }

  const source = clip.source_duration_ms

  return (
    <Section title={`クリップ ${index + 1}`}>
      <div className="flex flex-col gap-2">
        {clip.missing && (
          <p className="flex items-start gap-1.5 rounded border border-red-900/70 bg-red-950/50 px-2 py-1.5 text-xs text-red-200">
            <AlertTriangle className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
            <span>
              メディア欠落: 元のテイクの動画が見つかりません。このまま書き出すと、
              この区間は黒＋無音になります。
            </span>
          </p>
        )}

        <p className="break-words text-xs text-foreground/85">
          {clip.label || '（見出しなし）'}
        </p>

        <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-[11px]">
          <Row label="位置" value={formatTimecode(clip.start_ms)} />
          <Row label="尺" value={formatSeconds(clip.duration_ms)} />
          <Row
            label="切り出し"
            value={`${formatTimecode(clip.in_ms)} 〜 ${formatTimecode(clip.out_ms)}`}
          />
          <Row
            label="ソースの長さ"
            value={source == null ? '不明' : formatSeconds(source)}
          />
        </dl>

        <div className="flex flex-wrap gap-2 pt-1">
          <Button
            type="button"
            size="sm"
            variant="secondary"
            onClick={onSplit}
            disabled={!canSplit}
            title={
              canSplit
                ? '再生ヘッドの位置で 2 つに割る'
                : '再生ヘッドをこのクリップの中（端から少し内側）へ動かしてください'
            }
          >
            <Scissors className="size-4" aria-hidden="true" />
            分割
          </Button>
          <Button type="button" size="sm" variant="ghost" onClick={onDelete}>
            <Trash2 className="size-4" aria-hidden="true" />
            削除
          </Button>
        </div>
        <p className="text-[11px] text-muted-foreground">
          端をドラッグでトリム、本体をドラッグで並べ替え。Delete で削除、
          Ctrl+Z / Ctrl+Shift+Z でやり直し。
        </p>
      </div>
    </Section>
  )
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <>
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="font-mono text-foreground/90">{value}</dd>
    </>
  )
}
