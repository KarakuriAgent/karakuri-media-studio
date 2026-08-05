import { useState } from 'react'
import type {
  CanvasCharacterData,
  CanvasMediaData,
  CanvasModelData,
  CanvasNode,
  CanvasObjectData,
  CanvasLocationData,
  CanvasScriptData,
  CanvasStoryboardData,
  CanvasStyleData,
  CanvasTextData,
  Options,
} from '../../types'
import { Banner, Modal } from '../ui'
import CharacterFields from './fields/CharacterFields'
import LocationFields from './fields/LocationFields'
import MediaFields from './fields/MediaFields'
import ModelFields from './fields/ModelFields'
import ObjectFields from './fields/ObjectFields'
import ScriptFields from './fields/ScriptFields'
import StoryboardFields from './fields/StoryboardFields'
import StyleFields from './fields/StyleFields'
import TextFields from './fields/TextFields'
import { KIND_ICON, KIND_LABEL, defaultDataFor } from './logic'

type CardData = Record<string, unknown>

/**
 * カードの編集モーダル（ChatModal と同じ固定オーバーレイ構造）。
 *
 * 保存はタイトルと `data` の**全量**を PATCH する（バックエンドは data を
 * 部分マージしないので、フォームの state をそのまま送る）。
 */
export default function CardEditor({
  node,
  options,
  busy,
  error,
  onClose,
  onSave,
  onDelete,
}: {
  node: CanvasNode
  options: Options | null
  busy: boolean
  error: string | null
  onClose: () => void
  onSave: (patch: { title: string; data: CardData }) => void
  onDelete: () => void
}) {
  const [title, setTitle] = useState(node.title)
  // 古いカードに新しい項目が足りていても壊れないよう、既定値の上に重ねる。
  const [data, setData] = useState<CardData>({
    ...(defaultDataFor(node.kind) as unknown as CardData),
    ...node.data,
  })

  const update = (next: unknown) => setData(next as CardData)

  const fields = () => {
    switch (node.kind) {
      case 'style':
        return (
          <StyleFields data={data as unknown as CanvasStyleData} onChange={update} />
        )
      case 'character':
        return (
          <CharacterFields
            data={data as unknown as CanvasCharacterData}
            onChange={update}
          />
        )
      case 'location':
        return (
          <LocationFields
            data={data as unknown as CanvasLocationData}
            onChange={update}
          />
        )
      case 'object':
        return (
          <ObjectFields data={data as unknown as CanvasObjectData} onChange={update} />
        )
      case 'script':
        return (
          <ScriptFields data={data as unknown as CanvasScriptData} onChange={update} />
        )
      case 'storyboard':
        return (
          <StoryboardFields
            data={data as unknown as CanvasStoryboardData}
            onChange={update}
          />
        )
      case 'media':
        return (
          <MediaFields data={data as unknown as CanvasMediaData} onChange={update} />
        )
      case 'text':
        return <TextFields data={data as unknown as CanvasTextData} onChange={update} />
      case 'model':
        return (
          <ModelFields
            data={data as unknown as CanvasModelData}
            onChange={update}
            options={options}
          />
        )
      default:
        return null
    }
  }

  return (
    <Modal
      title={`${KIND_ICON[node.kind]} ${KIND_LABEL[node.kind]}カードを編集`}
      onClose={onClose}
      wide
    >
      <div className="flex flex-col gap-3">
        {error && <Banner>{error}</Banner>}

        <div>
          <label className="label">カード名</label>
          <input
            className="field"
            aria-label="カード名"
            value={title}
            placeholder="ヒロイン"
            onChange={(event) => setTitle(event.target.value)}
          />
          <p className="mt-1 text-[11px] text-slate-500">
            チャットから <code>@カード名</code> で参照するときの名前
          </p>
        </div>

        {fields()}

        <div className="flex items-center justify-between gap-2 border-t border-ink-600 pt-3">
          <button className="btn-ghost text-xs" onClick={onDelete} disabled={busy}>
            🗑 このカードを削除
          </button>
          <div className="flex gap-2">
            <button className="btn-ghost" onClick={onClose} disabled={busy}>
              キャンセル
            </button>
            <button
              className="btn-primary"
              onClick={() => onSave({ title, data })}
              disabled={busy}
            >
              {busy ? '保存中…' : '保存'}
            </button>
          </div>
        </div>
      </div>
    </Modal>
  )
}
