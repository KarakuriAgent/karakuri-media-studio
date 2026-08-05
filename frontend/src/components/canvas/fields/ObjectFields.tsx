import type { CanvasObjectData } from '../../../types'
import { AreaField, UrlListField } from './common'

export default function ObjectFields({
  data,
  onChange,
}: {
  data: CanvasObjectData
  onChange: (data: CanvasObjectData) => void
}) {
  const patch = (changes: Partial<CanvasObjectData>) => onChange({ ...data, ...changes })
  return (
    <div className="flex flex-col gap-3">
      <AreaField
        label="小道具の説明"
        value={data.description}
        onChange={(description) => patch({ description })}
      />
      <UrlListField
        label="参照画像"
        value={data.images}
        onChange={(images) => patch({ images })}
      />
      <AreaField label="メモ" value={data.notes} onChange={(notes) => patch({ notes })} />
    </div>
  )
}
