import type { CanvasScriptData, CanvasScriptScene } from '../../../types'
import { AreaField, TextField } from './common'

export default function ScriptFields({
  data,
  onChange,
}: {
  data: CanvasScriptData
  onChange: (data: CanvasScriptData) => void
}) {
  const patch = (changes: Partial<CanvasScriptData>) => onChange({ ...data, ...changes })

  const patchScene = (index: number, changes: Partial<CanvasScriptScene>) => {
    const scenes = data.scenes.map((scene, i) =>
      i === index ? { ...scene, ...changes } : scene,
    )
    patch({ scenes })
  }

  const addScene = () =>
    patch({
      scenes: [
        ...data.scenes,
        { no: data.scenes.length + 1, heading: '', body: '' },
      ],
    })

  const removeScene = (index: number) =>
    patch({ scenes: data.scenes.filter((_, i) => i !== index) })

  return (
    <div className="flex flex-col gap-3">
      <AreaField
        label="あらすじ"
        value={data.synopsis}
        onChange={(synopsis) => patch({ synopsis })}
      />

      <div className="flex flex-col gap-2">
        <div className="flex items-center justify-between">
          <span className="label !mb-0">シーン（{data.scenes.length}）</span>
          <button className="btn-ghost !py-1 text-xs" onClick={addScene}>
            ＋ シーンを追加
          </button>
        </div>
        {data.scenes.map((scene, index) => (
          <div
            key={index}
            className="flex flex-col gap-2 rounded-md border border-ink-600 bg-ink-800/60 p-2"
          >
            <div className="flex items-center gap-2">
              <input
                className="field !w-16"
                aria-label={`シーン ${index + 1} の番号`}
                type="number"
                value={scene.no}
                onChange={(event) =>
                  patchScene(index, { no: Number(event.target.value) })
                }
              />
              <input
                className="field flex-1"
                aria-label={`シーン ${index + 1} の見出し`}
                value={scene.heading}
                placeholder="屋上・夕"
                onChange={(event) => patchScene(index, { heading: event.target.value })}
              />
              <button
                className="btn-ghost !px-2 !py-1 text-xs"
                title="このシーンを削除"
                onClick={() => removeScene(index)}
              >
                ✕
              </button>
            </div>
            <textarea
              className="field"
              aria-label={`シーン ${index + 1} の本文`}
              rows={3}
              value={scene.body}
              placeholder="ト書き・セリフ"
              onChange={(event) => patchScene(index, { body: event.target.value })}
            />
          </div>
        ))}
        {data.scenes.length === 0 && (
          <p className="text-xs text-slate-500">まだシーンがありません</p>
        )}
      </div>

      <TextField label="メモ" value={data.notes} onChange={(notes) => patch({ notes })} />
    </div>
  )
}
