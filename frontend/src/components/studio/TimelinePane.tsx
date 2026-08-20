import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { AlertTriangle, GripVertical } from 'lucide-react'

import type { TimelineClip } from '../../types'
import {
  clipAt,
  formatSeconds,
  formatTimecode,
  msToPx,
  pxToMs,
  rulerStepMs,
  totalDuration,
  zoomBy,
} from './timeline'

/** クリップの端をつかめる幅（px）。細すぎると掴めず、太すぎると本体が押せない。 */
const HANDLE_PX = 8

/** タイムラインの右端に足す余白（ミリ秒）。最後のクリップの先も少し見せる。 */
const TAIL_PADDING_MS = 3000

/** ドラッグとみなす最小の移動量（px）。これ未満はクリックとして扱う。 */
const DRAG_THRESHOLD_PX = 4

type DragKind = 'move' | 'trim-in' | 'trim-out'

interface DragState {
  kind: DragKind
  clipId: string
  startX: number
  /** つかんだ時点のクリップの並び順（move のときの入れ替え先の計算に使う）。 */
  index: number
  moved: boolean
}

/**
 * V1 のタイムライン（時間軸ルーラー + 再生ヘッド + クリップの帯）。
 *
 * フェーズ 1 の編集は 3 つだけ:
 *
 * - **並べ替え**: クリップ本体を掴んで左右へ。順序だけが変わり、前後は
 *   隙間なく詰め直される（リップル方式。自由配置はしない）。
 * - **トリム**: クリップの端を掴んで内側 / 外側へ。`source_duration_ms` の
 *   範囲に収まる。
 * - **スクラブ**: ルーラーや空きをクリック / ドラッグで再生ヘッドを動かす。
 *
 * ズームは Ctrl + ホイール（1 秒あたり 20〜200px）。実際の書き換えは親が持つ
 * 純関数（`timeline.ts`）で行い、ここは「どの操作が起きたか」を上げるだけ。
 */
export default function TimelinePane({
  clips,
  selectedId,
  playheadMs,
  zoom,
  onZoom,
  onSelect,
  onSeek,
  onMove,
  onTrim,
}: {
  clips: TimelineClip[]
  selectedId: string | null
  playheadMs: number
  zoom: number
  onZoom: (zoom: number) => void
  onSelect: (id: string | null) => void
  onSeek: (ms: number) => void
  /** `id` のクリップを `to` 番目へ動かす。 */
  onMove: (id: string, to: number) => void
  /** `id` のクリップの端を `deltaMs` だけ動かす。 */
  onTrim: (id: string, edge: 'in' | 'out', deltaMs: number) => void
}) {
  const laneRef = useRef<HTMLDivElement | null>(null)
  const [drag, setDrag] = useState<DragState | null>(null)
  const [scrubbing, setScrubbing] = useState(false)

  const total = useMemo(() => totalDuration(clips), [clips])
  const spanMs = total + TAIL_PADDING_MS
  const width = msToPx(spanMs, zoom)

  const step = rulerStepMs(zoom)
  const ticks = useMemo(() => {
    const marks: number[] = []
    for (let ms = 0; ms <= spanMs; ms += step) marks.push(ms)
    return marks
  }, [spanMs, step])

  /** マウスの位置（ページ座標）を、レーンの中のミリ秒に直す。 */
  const msAtClientX = useCallback(
    (clientX: number) => {
      const lane = laneRef.current
      if (!lane) return 0
      const box = lane.getBoundingClientRect()
      return Math.min(spanMs, pxToMs(clientX - box.left + lane.scrollLeft, zoom))
    },
    [spanMs, zoom],
  )

  // ------------------------------------------------------------ スクラブ
  useEffect(() => {
    if (!scrubbing) return
    const move = (event: MouseEvent) => onSeek(msAtClientX(event.clientX))
    const up = () => setScrubbing(false)
    window.addEventListener('mousemove', move)
    window.addEventListener('mouseup', up)
    return () => {
      window.removeEventListener('mousemove', move)
      window.removeEventListener('mouseup', up)
    }
  }, [scrubbing, msAtClientX, onSeek])

  // ------------------------------------------------- クリップのドラッグ操作
  //
  // つかんでいるあいだの追従は window に張る（ポインタがクリップの外へ出ても
  // 続けたい）。move は「離した位置がどのクリップの上か」で入れ替え先を決め、
  // trim は横の移動量をそのままミリ秒に直して端へ渡す。
  useEffect(() => {
    if (!drag) return
    const move = (event: MouseEvent) => {
      const deltaPx = event.clientX - drag.startX
      if (!drag.moved && Math.abs(deltaPx) < DRAG_THRESHOLD_PX) return
      if (!drag.moved) setDrag({ ...drag, moved: true })

      if (drag.kind === 'move') return // 入れ替えは離した時点で 1 回だけ
      const deltaMs = Math.round((deltaPx / zoom) * 1000)
      onTrim(drag.clipId, drag.kind === 'trim-in' ? 'in' : 'out', deltaMs)
      // トリムは 1 回ごとに確定させるので、次の基準を今の位置へ進める。
      setDrag({ ...drag, startX: event.clientX, moved: true })
    }
    const up = (event: MouseEvent) => {
      if (drag.kind === 'move' && drag.moved) {
        const dropped = clipAt(clips, msAtClientX(event.clientX))
        const to = dropped ? dropped.index : clips.length - 1
        if (to !== drag.index) onMove(drag.clipId, to)
      }
      setDrag(null)
    }
    window.addEventListener('mousemove', move)
    window.addEventListener('mouseup', up)
    return () => {
      window.removeEventListener('mousemove', move)
      window.removeEventListener('mouseup', up)
    }
  }, [drag, clips, zoom, msAtClientX, onMove, onTrim])

  const startDrag = (
    event: React.MouseEvent,
    kind: DragKind,
    clip: TimelineClip,
    index: number,
  ) => {
    event.preventDefault()
    event.stopPropagation()
    onSelect(clip.id)
    setDrag({ kind, clipId: clip.id, startX: event.clientX, index, moved: false })
  }

  // ------------------------------------------------------------------ ズーム
  const onWheel = (event: React.WheelEvent) => {
    if (!event.ctrlKey && !event.metaKey) return
    event.preventDefault()
    onZoom(zoomBy(zoom, event.deltaY))
  }

  return (
    <div className="flex min-h-0 flex-col rounded-lg border border-border bg-card">
      <div className="flex items-center justify-between gap-2 border-b border-border px-3 py-1.5">
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <span className="font-semibold text-foreground">V1</span>
          <span>{clips.length} クリップ</span>
          <span>合計 {formatSeconds(total)}</span>
        </div>
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <span className="font-mono text-foreground">{formatTimecode(playheadMs)}</span>
          <span className="hidden sm:inline">Ctrl + ホイールで拡大縮小</span>
        </div>
      </div>

      <div
        ref={laneRef}
        className="min-h-0 flex-1 overflow-x-auto overflow-y-hidden"
        onWheel={onWheel}
      >
        <div className="relative select-none" style={{ width: Math.max(width, 320) }}>
          {/* 時間軸ルーラー（押した位置へ再生ヘッドが飛び、そのままドラッグで送れる） */}
          <div
            className="relative h-6 cursor-col-resize border-b border-border bg-secondary/40"
            role="slider"
            aria-label="再生位置"
            aria-valuemin={0}
            aria-valuemax={Math.round(spanMs)}
            aria-valuenow={Math.round(playheadMs)}
            tabIndex={0}
            onMouseDown={(event) => {
              onSeek(msAtClientX(event.clientX))
              setScrubbing(true)
            }}
          >
            {ticks.map((ms) => (
              <div
                key={ms}
                className="absolute top-0 h-full border-l border-border/70 pl-1 text-[10px] leading-6 text-muted-foreground"
                style={{ left: msToPx(ms, zoom) }}
              >
                {formatTimecode(ms)}
              </div>
            ))}
          </div>

          {/* クリップの帯 */}
          <div
            className="relative h-20 bg-background/40"
            onMouseDown={() => onSelect(null)}
          >
            {clips.map((clip, index) => (
              <ClipRect
                key={clip.id}
                clip={clip}
                index={index}
                zoom={zoom}
                selected={clip.id === selectedId}
                dragging={drag?.clipId === clip.id && drag.moved}
                onBody={(event) => startDrag(event, 'move', clip, index)}
                onIn={(event) => startDrag(event, 'trim-in', clip, index)}
                onOut={(event) => startDrag(event, 'trim-out', clip, index)}
              />
            ))}
            {clips.length === 0 && (
              <p className="px-3 py-6 text-xs text-muted-foreground">
                クリップがありません。話を選んでタイムラインを作り直すか、制作タブで
                テイクを採用してください。
              </p>
            )}
          </div>

          {/* 再生ヘッド（ルーラーとクリップの帯を貫く 1 本） */}
          <div
            className="pointer-events-none absolute top-0 z-10 h-full w-px bg-accent-400"
            style={{ left: msToPx(playheadMs, zoom) }}
          >
            <div className="-ml-1 size-2 rounded-full bg-accent-400" />
          </div>
        </div>
      </div>
    </div>
  )
}

/**
 * クリップ 1 つの矩形。
 *
 * 左右 {@link HANDLE_PX} px はトリムのつまみで、真ん中が並べ替えのつかみどころ。
 * メディア欠落（`missing`）は赤系で出し、そのまま書き出すと黒＋無音になることを
 * ツールチップで伝える。
 */
function ClipRect({
  clip,
  index,
  zoom,
  selected,
  dragging,
  onBody,
  onIn,
  onOut,
}: {
  clip: TimelineClip
  index: number
  zoom: number
  selected: boolean
  dragging: boolean
  onBody: (event: React.MouseEvent) => void
  onIn: (event: React.MouseEvent) => void
  onOut: (event: React.MouseEvent) => void
}) {
  const left = msToPx(clip.start_ms, zoom)
  const width = Math.max(msToPx(clip.duration_ms, zoom), 12)
  const tone = clip.missing
    ? 'border-red-700 bg-red-950/70 text-red-200'
    : selected
      ? 'border-accent-400 bg-accent-500/25 text-foreground'
      : 'border-border bg-secondary text-foreground/90'

  return (
    <div
      className={`absolute top-2 flex h-16 items-stretch overflow-hidden rounded border ${tone} ${
        dragging ? 'opacity-60' : ''
      }`}
      style={{ left, width }}
      title={
        clip.missing
          ? 'メディア欠落: 元のテイクの動画が見つかりません（書き出すと黒＋無音になります）'
          : `${clip.label || 'クリップ'} / ${formatSeconds(clip.duration_ms)}`
      }
      onMouseDown={onBody}
    >
      <button
        type="button"
        className="shrink-0 cursor-w-resize bg-foreground/10 hover:bg-accent-400/50"
        style={{ width: HANDLE_PX }}
        aria-label={`クリップ ${index + 1} の始まりをトリム`}
        onMouseDown={onIn}
      />
      <div className="min-w-0 flex-1 cursor-grab px-1.5 py-1">
        <div className="flex items-center gap-1 text-[11px] font-medium">
          {clip.missing ? (
            <AlertTriangle className="size-3 shrink-0" aria-hidden="true" />
          ) : (
            <GripVertical
              className="size-3 shrink-0 text-muted-foreground"
              aria-hidden="true"
            />
          )}
          <span className="truncate">
            {clip.missing ? 'メディア欠落' : clip.label || `クリップ ${index + 1}`}
          </span>
        </div>
        <p className="truncate text-[10px] text-muted-foreground">
          {formatSeconds(clip.duration_ms)}
        </p>
      </div>
      <button
        type="button"
        className="shrink-0 cursor-e-resize bg-foreground/10 hover:bg-accent-400/50"
        style={{ width: HANDLE_PX }}
        aria-label={`クリップ ${index + 1} の終わりをトリム`}
        onMouseDown={onOut}
      />
    </div>
  )
}
