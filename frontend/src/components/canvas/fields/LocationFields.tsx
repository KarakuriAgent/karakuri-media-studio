import type { CanvasLocationData } from '../../../types'
import { AreaField, TextField, UrlListField } from './common'

export default function LocationFields({
  data,
  onChange,
}: {
  data: CanvasLocationData
  onChange: (data: CanvasLocationData) => void
}) {
  const patch = (changes: Partial<CanvasLocationData>) =>
    onChange({ ...data, ...changes })
  return (
    <div className="flex flex-col gap-3">
      <AreaField
        label="場所の説明"
        value={data.description}
        onChange={(description) => patch({ description })}
      />
      <TextField
        label="時間帯・天候・雰囲気"
        value={data.mood}
        onChange={(mood) => patch({ mood })}
        placeholder="夕暮れ、小雨、人けのない"
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
