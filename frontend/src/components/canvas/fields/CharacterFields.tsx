import type { CanvasCharacterData } from '../../../types'
import { AreaField, UrlListField } from './common'

export default function CharacterFields({
  data,
  onChange,
}: {
  data: CanvasCharacterData
  onChange: (data: CanvasCharacterData) => void
}) {
  const patch = (changes: Partial<CanvasCharacterData>) =>
    onChange({ ...data, ...changes })
  return (
    <div className="flex flex-col gap-3">
      <AreaField
        label="ひとこと紹介"
        value={data.description}
        onChange={(description) => patch({ description })}
        rows={2}
      />
      <AreaField
        label="外見"
        value={data.appearance}
        onChange={(appearance) => patch({ appearance })}
        hint="生成プロンプトにそのまま使える具体性で（英語推奨）"
        placeholder="silver hair, blue eyes, navy sailor uniform"
      />
      <AreaField
        label="性格"
        value={data.personality}
        onChange={(personality) => patch({ personality })}
        rows={2}
      />
      <AreaField
        label="声・話し方"
        value={data.voice}
        onChange={(voice) => patch({ voice })}
        rows={2}
        hint="音声生成の指示に使う"
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
