import type { CanvasStyleData } from '../../../types'
import { AreaField, TextField, UrlListField } from './common'

export default function StyleFields({
  data,
  onChange,
}: {
  data: CanvasStyleData
  onChange: (data: CanvasStyleData) => void
}) {
  const patch = (changes: Partial<CanvasStyleData>) => onChange({ ...data, ...changes })
  return (
    <div className="flex flex-col gap-3">
      <AreaField
        label="画風・トーン"
        value={data.description}
        onChange={(description) => patch({ description })}
        placeholder="水彩タッチ、柔らかい光、フィルムグレイン…"
      />
      <TextField
        label="カラーパレット"
        value={data.palette}
        onChange={(palette) => patch({ palette })}
        placeholder="くすんだ青緑 + 夕焼けのオレンジ"
      />
      <UrlListField
        label="参照画像"
        value={data.references}
        onChange={(references) => patch({ references })}
      />
      <AreaField label="メモ" value={data.notes} onChange={(notes) => patch({ notes })} />
    </div>
  )
}
