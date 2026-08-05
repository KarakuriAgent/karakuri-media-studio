import type { CanvasMediaData } from '../../../types'
import { AreaField, Field, TextField } from './common'

const MEDIA_TYPES: { value: CanvasMediaData['media_type']; label: string }[] = [
  { value: 'image', label: '画像' },
  { value: 'video', label: '動画' },
  { value: 'audio', label: '音声' },
]

export default function MediaFields({
  data,
  onChange,
}: {
  data: CanvasMediaData
  onChange: (data: CanvasMediaData) => void
}) {
  const patch = (changes: Partial<CanvasMediaData>) => onChange({ ...data, ...changes })
  return (
    <div className="flex flex-col gap-3">
      <Field label="種別">
        <select
          className="field"
          aria-label="種別"
          value={data.media_type}
          onChange={(event) =>
            patch({ media_type: event.target.value as CanvasMediaData['media_type'] })
          }
        >
          {MEDIA_TYPES.map((item) => (
            <option key={item.value} value={item.value}>
              {item.label}
            </option>
          ))}
        </select>
      </Field>
      <TextField
        label="URL"
        value={data.url}
        onChange={(url) => patch({ url })}
        hint="/outputs/… /library/… /assets/… のいずれか"
      />
      <TextField
        label="キャプション"
        value={data.caption}
        onChange={(caption) => patch({ caption })}
      />
      <AreaField
        label="生成プロンプト"
        value={data.prompt}
        onChange={(prompt) => patch({ prompt })}
        hint="作り直すときの手がかり（生成結果カードには自動で入る）"
      />
      {data.job_id && (
        <p className="text-[11px] text-slate-500">元ジョブ: {data.job_id}</p>
      )}
    </div>
  )
}
