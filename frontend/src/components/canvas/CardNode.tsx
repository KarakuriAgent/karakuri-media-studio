import { NodeResizer, type NodeProps } from '@xyflow/react'
import { createContext, useContext } from 'react'
import type { CanvasMediaData, CanvasModelData, CanvasNode } from '../../types'
import {
  KIND_ICON,
  KIND_LABEL,
  KIND_STYLE,
  cardSummary,
  type CardFlowNode,
} from './logic'

/**
 * カードからボードへの操作（React Flow の `data` は純粋な写像に保ちたいので、
 * ハンドラはコンテキストで配る）。
 */
export interface CardActionsValue {
  onEdit: (node: CanvasNode) => void
  onResize: (id: string, w: number, h: number) => void
  /** md 未満ではリサイズさせない（閲覧・移動・編集のみ）。 */
  resizable: boolean
}

export const CardActions = createContext<CardActionsValue>({
  onEdit: () => {},
  onResize: () => {},
  resizable: true,
})

function MediaPreview({ data }: { data: CanvasMediaData }) {
  if (!data.url) {
    return <p className="text-xs text-slate-500">URL 未設定</p>
  }
  if (data.media_type === 'video') {
    return (
      <video
        className="max-h-full w-full rounded object-contain"
        src={data.url}
        controls
        preload="metadata"
      />
    )
  }
  if (data.media_type === 'audio') {
    return <audio className="w-full" src={data.url} controls preload="metadata" />
  }
  return (
    <img
      className="max-h-full w-full rounded object-contain"
      src={data.url}
      alt={data.caption || '素材'}
    />
  )
}

/** ボード上のカード 1 枚（ダブルクリックで編集モーダルを開く）。 */
export default function CardNode({ data, selected }: NodeProps<CardFlowNode>) {
  const { onEdit, onResize, resizable } = useContext(CardActions)
  const node = data.node
  const summary = cardSummary(node)

  return (
    <>
      {resizable && (
        <NodeResizer
          isVisible={selected}
          minWidth={200}
          minHeight={120}
          onResizeEnd={(_event, params) =>
            onResize(node.id, params.width, params.height)
          }
        />
      )}
      <div
        className={`flex h-full w-full cursor-grab flex-col overflow-hidden rounded-lg border bg-ink-900 text-left shadow-lg ${
          selected ? 'border-accent-500' : 'border-ink-600'
        }`}
        onDoubleClick={() => onEdit(node)}
        title="ダブルクリックで編集"
      >
        <div
          className={`flex items-center gap-1.5 border-b px-2 py-1 text-[11px] ${KIND_STYLE[node.kind]}`}
        >
          <span>{KIND_ICON[node.kind]}</span>
          <span className="shrink-0 opacity-80">{KIND_LABEL[node.kind]}</span>
          <span className="truncate font-semibold text-slate-100">{node.title}</span>
          <button
            className="ml-auto shrink-0 opacity-60 hover:opacity-100"
            title="編集"
            onClick={(event) => {
              event.stopPropagation()
              onEdit(node)
            }}
          >
            ✎
          </button>
        </div>

        <div className="min-h-0 flex-1 overflow-hidden p-2 text-xs text-slate-300">
          {node.kind === 'media' ? (
            <MediaPreview data={node.data as unknown as CanvasMediaData} />
          ) : node.kind === 'model' ? (
            <ModelBadges data={node.data as unknown as CanvasModelData} />
          ) : (
            <p className="whitespace-pre-wrap break-words line-clamp-6">
              {summary || <span className="text-slate-600">（空のカード）</span>}
            </p>
          )}
        </div>
      </div>
    </>
  )
}

function ModelBadges({ data }: { data: CanvasModelData }) {
  const target =
    data.target === 'image' ? '画像' : data.target === 'video' ? '動画' : '音声'
  return (
    <div className="flex flex-wrap gap-1">
      <span className="chip !px-2 !py-0.5">{target}</span>
      <span className="chip !px-2 !py-0.5">{data.workflow || 'ワークフロー未選択'}</span>
      {data.note && <span className="w-full text-slate-400">{data.note}</span>}
    </div>
  )
}
