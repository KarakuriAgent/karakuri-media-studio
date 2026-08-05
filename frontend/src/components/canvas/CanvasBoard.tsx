import {
  Background,
  Controls,
  ReactFlow,
  ReactFlowProvider,
  applyNodeChanges,
  useReactFlow,
  type NodeChange,
  type Viewport,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { useCallback, useEffect, useRef, useState } from 'react'
import type {
  CanvasNode,
  CanvasNodeKind,
  CanvasProjectDetail,
  CanvasViewport,
} from '../../types'
import CardNode, { CardActions } from './CardNode'
import {
  CANVAS_KINDS,
  KIND_ICON,
  KIND_LABEL,
  fromNodeChange,
  mergeFlowNodes,
  nextDraggingIds,
  toFlowNode,
  type CardFlowNode,
} from './logic'

// コンポーネントの外で定義する（毎回作り直すと全ノードが再マウントされる）。
const nodeTypes = { card: CardNode }

/** viewport の保存はドラッグのたびに投げず、落ち着いてから 1 回だけ送る。 */
const VIEWPORT_DEBOUNCE = 800

export interface BoardProps {
  project: CanvasProjectDetail
  onCreateNode: (kind: CanvasNodeKind, x: number, y: number) => void
  onMoveNode: (id: string, x: number, y: number) => void
  onResizeNode: (id: string, w: number, h: number) => void
  onEditNode: (node: CanvasNode) => void
  onViewport: (viewport: CanvasViewport) => void
  /** md 未満ではリサイズを無効にする（モバイルは閲覧・移動・編集のみ）。 */
  resizable?: boolean
}

/** 「どこに」「どの種別で」カードを作るかの選択中の状態。 */
interface Picker {
  /** 配置先（フロー座標）。 */
  x: number
  y: number
  /** ポップオーバーの表示位置（コンテナ基準の px。未指定なら右下の FAB の上）。 */
  screen?: { left: number; top: number }
}

function BoardInner({
  project,
  onCreateNode,
  onMoveNode,
  onResizeNode,
  onEditNode,
  onViewport,
  resizable = true,
}: BoardProps) {
  const [nodes, setNodes] = useState<CardFlowNode[]>(() =>
    project.nodes.map(toFlowNode),
  )
  const [picker, setPicker] = useState<Picker | null>(null)
  const container = useRef<HTMLDivElement | null>(null)
  const flow = useReactFlow()

  // 掴んでいるカードの id（再取得で位置を巻き戻さないため）。effect を余計に
  // 走らせたくないので state ではなく ref で持つ。
  const dragging = useRef<ReadonlySet<string>>(new Set())

  // サーバーの状態が変わったら描画中のノードを揃える（楽観更新の確定もここ）。
  // 選択状態とドラッグ中の位置は手元のものを引き継ぐ（mergeFlowNodes 参照）。
  useEffect(() => {
    setNodes((current) => mergeFlowNodes(current, project.nodes, dragging.current))
  }, [project.nodes])

  const handleChanges = useCallback(
    (changes: NodeChange<CardFlowNode>[]) => {
      dragging.current = nextDraggingIds(dragging.current, changes)
      setNodes((current) => applyNodeChanges(changes, current))
      for (const change of changes) {
        const moved = fromNodeChange(change)
        if (moved) onMoveNode(moved.id, moved.x, moved.y)
      }
    },
    [onMoveNode],
  )

  // viewport の保存（debounce）。アンマウント時に残ったタイマーは捨てる。
  const timer = useRef<number | undefined>(undefined)
  useEffect(() => () => window.clearTimeout(timer.current), [])
  const handleMoveEnd = useCallback(
    (_event: unknown, viewport: Viewport) => {
      window.clearTimeout(timer.current)
      timer.current = window.setTimeout(
        () => onViewport(viewport),
        VIEWPORT_DEBOUNCE,
      )
    },
    [onViewport],
  )

  /** 空白のダブルクリック: その場所にカードを作る（ショートカット扱い）。 */
  const handleDoubleClick = (event: React.MouseEvent<HTMLDivElement>) => {
    const target = event.target as HTMLElement
    if (!target.classList.contains('react-flow__pane')) return
    const box = container.current?.getBoundingClientRect()
    const position = flow.screenToFlowPosition({
      x: event.clientX,
      y: event.clientY,
    })
    setPicker({
      x: position.x,
      y: position.y,
      screen: box
        ? { left: event.clientX - box.left, top: event.clientY - box.top }
        : undefined,
    })
  }

  /** 「＋」ボタン: いま見えている範囲の中央に作る（モバイルではこちらが正）。 */
  const openCenterPicker = () => {
    const box = container.current?.getBoundingClientRect()
    const center = box
      ? flow.screenToFlowPosition({
          x: box.left + box.width / 2,
          y: box.top + box.height / 2,
        })
      : { x: 0, y: 0 }
    setPicker({ x: center.x, y: center.y })
  }

  return (
    <div
      ref={container}
      className="relative h-full w-full"
      onDoubleClick={handleDoubleClick}
    >
      <CardActions.Provider
        value={{ onEdit: onEditNode, onResize: onResizeNode, resizable }}
      >
        <ReactFlow
          nodes={nodes}
          edges={[]}
          nodeTypes={nodeTypes}
          onNodesChange={handleChanges}
          onMoveEnd={handleMoveEnd}
          defaultViewport={project.viewport}
          nodesConnectable={false}
          elementsSelectable
          deleteKeyCode={null}
          minZoom={0.2}
          maxZoom={2}
          // 既定の明るい配色のままだと UI 全体のダークテーマから浮く
          colorMode="dark"
        >
          <Background />
          <Controls showInteractive={false} />
        </ReactFlow>
      </CardActions.Provider>

      {/* カード作成の「＋」（デスクトップでも常設、モバイルではこれが正） */}
      <button
        className="btn-primary absolute bottom-9 right-4 z-10 !rounded-full !px-4 !py-3 text-lg shadow-lg"
        title="カードを追加"
        aria-label="カードを追加"
        onClick={openCenterPicker}
      >
        ＋
      </button>

      {picker && (
        <>
          <div
            className="absolute inset-0 z-10"
            onClick={() => setPicker(null)}
            onDoubleClick={(event) => event.stopPropagation()}
          />
          <div
            className="absolute z-20 flex w-44 flex-col gap-0.5 rounded-lg border border-ink-600 bg-ink-800 p-1 shadow-2xl"
            style={
              picker.screen
                ? { left: picker.screen.left, top: picker.screen.top }
                : { right: '1rem', bottom: '6rem' }
            }
          >
            <p className="px-2 py-1 text-[11px] text-slate-500">カードの種類</p>
            {CANVAS_KINDS.map((kind) => (
              <button
                key={kind}
                className="flex items-center gap-2 rounded px-2 py-1 text-left text-xs text-slate-300 hover:bg-ink-700"
                onClick={() => {
                  setPicker(null)
                  onCreateNode(kind, picker.x, picker.y)
                }}
              >
                <span>{KIND_ICON[kind]}</span>
                {KIND_LABEL[kind]}
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  )
}

/**
 * カードを並べる無限キャンバス（React Flow のラッパー）。
 *
 * エッジ（配線）は使わない: カードどうしの関係は「同じキャンバスに置いてある」
 * ことだけで表し、参照はチャットの `@カード名` で行う。
 */
export default function CanvasBoard(props: BoardProps) {
  return (
    <ReactFlowProvider>
      <BoardInner {...props} />
    </ReactFlowProvider>
  )
}
