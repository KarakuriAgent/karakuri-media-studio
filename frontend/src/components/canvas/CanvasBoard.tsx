import { useCallback, useEffect, useRef, useState } from 'react'
import { LayoutGrid, Maximize, Minus, Plus } from 'lucide-react'

import type {
  CanvasCard,
  CanvasCardKind,
  CanvasViewport,
  StudioProjectDetail,
} from '../../types'
import { Button } from '../ui/button'
import CardNode from './CardNode'
import {
  ADDABLE_KINDS,
  KIND_ICON,
  KIND_LABEL,
  MAX_ZOOM,
  MIN_ZOOM,
  fitViewport,
  panBy,
  screenToBoard,
  viewCenter,
  zoomAt,
  type Point,
} from './logic'

/** 表示位置の保存はドラッグのたびに投げず、落ち着いてから 1 回だけ送る。 */
const VIEWPORT_DEBOUNCE = 800

/** ホイール 1 ノッチぶんの倍率。 */
const WHEEL_STEP = 1.0015

/** 「＋」で開いている種別メニュー（どこに置くかを持つ）。 */
interface Picker {
  /** 配置先（ボード座標）。 */
  point: Point
  /** メニューの表示位置（コンテナ基準の px。未指定なら右下の FAB の上）。 */
  screen?: { left: number; top: number }
}

/** 掴んでいるカード（掴んだ時点のカード座標とポインタ位置）。 */
interface Drag {
  cardId: string
  pointerId: number
  origin: Point
  start: Point
}

export interface BoardProps {
  cards: CanvasCard[]
  detail: StudioProjectDetail
  /** 開いているタブ（null = 作品共通）。空のときの案内文だけに使う。 */
  tab?: string | null
  viewport: CanvasViewport
  selectedId: string | null
  onSelect: (cardId: string | null) => void
  onEdit: (card: CanvasCard) => void
  /** ドラッグの確定（動かしているあいだは呼ばない）。 */
  onMove: (cardId: string, x: number, y: number) => void
  /** 「＋」やダブルクリックからのカード追加（置き場所はボード座標）。 */
  onAdd: (kind: CanvasCardKind, point: Point) => void
  /** 場カードの「＋カット」（その場に属するカットを 1 手で足す）。 */
  onAddShot?: (sceneId: string) => void
  /** 保存中（場カードの「＋カット」を止める）。 */
  busy?: boolean
  onViewport: (viewport: CanvasViewport) => void
  /** カードを格子状に並べ直す。 */
  onArrange: () => void
  /**
   * 「全体が見えるところまで寄せる」の要求。
   *
   * 数が増えるたびに、いま出ているカードに合わせて表示位置を計算し直す
   * （どこに何があるかは開いてみるまで分からないため、初回に親が 1 回上げる）。
   */
  fitRequest?: number
}

/**
 * カードを並べる無限キャンバス。
 *
 * 表示は `translate(x, y) scale(zoom)` 1 枚で作り、カードはボード座標の絶対
 * 配置で置く。ここが持つのは「見え方」と「掴んでいるもの」だけで、保存は
 * すべて呼び出し側（`CanvasView`）が行う。
 *
 * カードどうしを線で結ぶ機能は持たない: 関係は「同じキャンバスに置いてある」
 * ことと、スタジオ側の所属（場とカット）で表す。
 */
export default function CanvasBoard({
  cards,
  detail,
  tab = null,
  viewport,
  selectedId,
  onSelect,
  onEdit,
  onMove,
  onAdd,
  onAddShot,
  busy = false,
  onViewport,
  onArrange,
  fitRequest = 0,
}: BoardProps) {
  const container = useRef<HTMLDivElement | null>(null)
  // 手元の表示位置（サーバーへの保存は落ち着いてから）。
  const [view, setView] = useState<CanvasViewport>(viewport)
  const [picker, setPicker] = useState<Picker | null>(null)
  // 掴んでいるカード（動かしているあいだの位置は手元にだけ持つ）。
  const [drag, setDrag] = useState<Drag | null>(null)
  const [dragged, setDragged] = useState<Point | null>(null)
  // 背景を掴んでの流し見（ポインタ id と直前の位置）。
  const pan = useRef<{ pointerId: number; last: Point } | null>(null)

  // 別のキャンバスを開いたときはサーバーの表示位置に合わせる。
  useEffect(() => setView(viewport), [viewport])

  // 表示位置の保存（debounce）。アンマウント時に残ったタイマーは捨てる。
  const timer = useRef<number | undefined>(undefined)
  useEffect(() => () => window.clearTimeout(timer.current), [])
  const saveView = useCallback(
    (next: CanvasViewport) => {
      setView(next)
      window.clearTimeout(timer.current)
      timer.current = window.setTimeout(() => onViewport(next), VIEWPORT_DEBOUNCE)
    },
    [onViewport],
  )

  /** いま出ているカード全部が入るところまで引く。 */
  const fit = useCallback(() => {
    const box = container.current?.getBoundingClientRect()
    saveView(fitViewport(cards, box ?? { width: 0, height: 0 }))
  }, [cards, saveView])

  // 要求のたびに最新のカードで計算し直す（貼り直しは避ける）。
  const fitRef = useRef(fit)
  fitRef.current = fit
  useEffect(() => {
    if (fitRequest > 0) fitRef.current()
  }, [fitRequest])

  /** イベントの座標をコンテナ左上基準の px に直す。 */
  const localPoint = (event: { clientX: number; clientY: number }): Point => {
    const box = container.current?.getBoundingClientRect()
    return box
      ? { x: event.clientX - box.left, y: event.clientY - box.top }
      : { x: event.clientX, y: event.clientY }
  }

  // ------------------------------------------------------------ ズーム / 流し見

  // ホイールでのズーム。React の onWheel は passive で貼られて preventDefault が
  // 効かない（ページごとスクロールしてしまう）ので、自前で非 passive に貼る。
  // 最新の表示位置はハンドラを貼り直さずに読めるよう ref から取る。
  const viewRef = useRef(view)
  viewRef.current = view
  useEffect(() => {
    const node = container.current
    if (!node) return
    const onWheel = (event: WheelEvent) => {
      event.preventDefault()
      const box = node.getBoundingClientRect()
      saveView(
        zoomAt(viewRef.current, Math.pow(WHEEL_STEP, -event.deltaY), {
          x: event.clientX - box.left,
          y: event.clientY - box.top,
        }),
      )
    }
    node.addEventListener('wheel', onWheel, { passive: false })
    return () => node.removeEventListener('wheel', onWheel)
  }, [saveView])

  const zoomBy = (factor: number) => {
    const box = container.current?.getBoundingClientRect()
    const pivot = box ? { x: box.width / 2, y: box.height / 2 } : { x: 0, y: 0 }
    saveView(zoomAt(view, factor, pivot))
  }

  const handleBackgroundDown = (event: React.PointerEvent) => {
    if (event.button !== 0 && event.button !== 1) return
    onSelect(null)
    pan.current = { pointerId: event.pointerId, last: { x: event.clientX, y: event.clientY } }
    event.currentTarget.setPointerCapture(event.pointerId)
  }

  const handlePointerMove = (event: React.PointerEvent) => {
    if (drag && drag.pointerId === event.pointerId) {
      const moved = screenToBoard(localPoint(event), view)
      setDragged({
        x: drag.origin.x + (moved.x - drag.start.x),
        y: drag.origin.y + (moved.y - drag.start.y),
      })
      return
    }
    const panning = pan.current
    if (!panning || panning.pointerId !== event.pointerId) return
    const next = panBy(
      view,
      event.clientX - panning.last.x,
      event.clientY - panning.last.y,
    )
    panning.last = { x: event.clientX, y: event.clientY }
    saveView(next)
  }

  const handlePointerUp = (event: React.PointerEvent) => {
    if (drag && drag.pointerId === event.pointerId) {
      if (dragged) onMove(drag.cardId, dragged.x, dragged.y)
      setDrag(null)
      setDragged(null)
    }
    if (pan.current?.pointerId === event.pointerId) pan.current = null
  }

  const startDrag = (card: CanvasCard, event: React.PointerEvent) => {
    event.stopPropagation()
    if (event.button !== 0) return
    onSelect(card.id)
    setDrag({
      cardId: card.id,
      pointerId: event.pointerId,
      origin: { x: card.x, y: card.y },
      start: screenToBoard(localPoint(event), view),
    })
    setDragged(null)
    // ドラッグ中もイベントを取りこぼさないよう、面（この要素）で捕まえ続ける
    container.current?.setPointerCapture(event.pointerId)
  }

  // ------------------------------------------------------------------ 追加

  /** 空白のダブルクリック: その場所にカードを作る（ショートカット扱い）。 */
  const handleBackgroundDoubleClick = (event: React.MouseEvent) => {
    const local = localPoint(event)
    setPicker({ point: screenToBoard(local, view), screen: { left: local.x, top: local.y } })
  }

  /** 「＋」ボタン: いま見えている範囲の中央に作る。 */
  const openCenterPicker = () => {
    const box = container.current?.getBoundingClientRect()
    const size = box ?? { width: 0, height: 0 }
    setPicker({ point: viewCenter(size, view) })
  }

  return (
    <div
      ref={container}
      className="relative h-full w-full touch-none overflow-hidden bg-background"
      onPointerMove={handlePointerMove}
      onPointerUp={handlePointerUp}
      onPointerCancel={handlePointerUp}
    >
      {/* 背景（掴むと流し見、ダブルクリックでカード追加） */}
      <div
        className="absolute inset-0 cursor-grab"
        style={{
          backgroundImage:
            'radial-gradient(circle, rgb(51 65 85 / 0.7) 1px, transparent 1px)',
          backgroundSize: `${24 * view.zoom}px ${24 * view.zoom}px`,
          backgroundPosition: `${view.x}px ${view.y}px`,
        }}
        onPointerDown={handleBackgroundDown}
        onDoubleClick={handleBackgroundDoubleClick}
        aria-label="キャンバスの背景"
      />

      <div
        className="absolute left-0 top-0 origin-top-left"
        style={{ transform: `translate(${view.x}px, ${view.y}px) scale(${view.zoom})` }}
      >
        {cards.map((card) => {
          const moving = drag?.cardId === card.id && dragged
          const placed = moving ? { ...card, x: dragged.x, y: dragged.y } : card
          return (
            <CardNode
              key={card.id}
              card={placed}
              detail={detail}
              selected={card.id === selectedId}
              onSelect={() => onSelect(card.id)}
              onEdit={() => onEdit(card)}
              onDragStart={(event) => startDrag(card, event)}
              busy={busy}
              onAddShot={onAddShot}
            />
          )
        })}
      </div>

      {/* 表示のコントロール（拡大・縮小・原点に戻す・整列） */}
      <div className="absolute bottom-4 left-4 z-10 flex items-center gap-1 rounded-md border border-border bg-card/90 p-1 shadow-elevation-1">
        <Button
          variant="outline"
          size="icon-sm"
          onClick={() => zoomBy(1 / 1.2)}
          disabled={view.zoom <= MIN_ZOOM}
          aria-label="縮小"
        >
          <Minus aria-hidden="true" />
        </Button>
        <span className="tnum w-12 text-center text-[11px] text-muted-foreground">
          {Math.round(view.zoom * 100)}%
        </span>
        <Button
          variant="outline"
          size="icon-sm"
          onClick={() => zoomBy(1.2)}
          disabled={view.zoom >= MAX_ZOOM}
          aria-label="拡大"
        >
          <Plus aria-hidden="true" />
        </Button>
        <Button
          variant="outline"
          size="icon-sm"
          onClick={fit}
          title="全体が見えるように寄せる"
          aria-label="全体を見る"
        >
          <Maximize aria-hidden="true" />
        </Button>
        <Button
          variant="outline"
          size="sm"
          onClick={onArrange}
          title="カードを格子状に並べ直す"
        >
          <LayoutGrid aria-hidden="true" />
          整列
        </Button>
      </div>

      {/* スタジオにも中身が無いとき（鏡なので、盤面が空 = そのタブが空）。 */}
      {cards.length === 0 && (
        <div className="pointer-events-none absolute inset-0 z-10 flex items-center justify-center p-6">
          <div className="max-w-sm rounded-lg border border-border bg-card/90 p-4 text-center shadow-elevation-1">
            <p className="text-sm text-foreground/85">
              {tab
                ? 'この話にはまだ中身がありません'
                : 'この作品にはまだ中身がありません'}
            </p>
          </div>
        </div>
      )}

      {/* カード作成の「＋」 */}
      <Button
        size="icon"
        className="absolute bottom-4 right-4 z-10 rounded-full shadow-elevation-3 [&_svg]:size-5"
        title="カードを追加"
        aria-label="カードを追加"
        onClick={openCenterPicker}
      >
        <Plus aria-hidden="true" />
      </Button>

      {picker && (
        <>
          <div
            className="absolute inset-0 z-10"
            onClick={() => setPicker(null)}
            onDoubleClick={(event) => event.stopPropagation()}
          />
          <div
            className="absolute z-20 flex w-44 flex-col gap-0.5 rounded-lg border border-border bg-popover p-1 shadow-elevation-3"
            style={
              picker.screen
                ? { left: picker.screen.left, top: picker.screen.top }
                : { right: '1rem', bottom: '5rem' }
            }
          >
            <p className="px-2 py-1 text-[11px] text-muted-foreground">カードの種類</p>
            {ADDABLE_KINDS.map((kind) => {
              const Icon = KIND_ICON[kind]
              return (
                <button
                  key={kind}
                  className="flex items-center gap-2 rounded-sm px-2 py-1 text-left text-xs text-foreground/85 transition-colors hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
                  onClick={() => {
                    setPicker(null)
                    onAdd(kind, picker.point)
                  }}
                >
                  <Icon className="size-3.5 shrink-0" aria-hidden="true" />
                  {KIND_LABEL[kind]}
                </button>
              )
            })}
          </div>
        </>
      )}
    </div>
  )
}
