import type { CanvasStoryboardCut, CanvasStoryboardData } from '../../../types'
import { AreaField } from './common'

const EMPTY_CUT: CanvasStoryboardCut = {
  no: 0,
  scene: '',
  description: '',
  camera: '',
  audio: '',
  duration: null,
  prompt: '',
  image: '',
}

export default function StoryboardFields({
  data,
  onChange,
}: {
  data: CanvasStoryboardData
  onChange: (data: CanvasStoryboardData) => void
}) {
  const patch = (changes: Partial<CanvasStoryboardData>) =>
    onChange({ ...data, ...changes })

  const patchCut = (index: number, changes: Partial<CanvasStoryboardCut>) =>
    patch({
      cuts: data.cuts.map((cut, i) => (i === index ? { ...cut, ...changes } : cut)),
    })

  const addCut = () =>
    patch({ cuts: [...data.cuts, { ...EMPTY_CUT, no: data.cuts.length + 1 }] })

  const removeCut = (index: number) =>
    patch({ cuts: data.cuts.filter((_, i) => i !== index) })

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <span className="label !mb-0">カット（{data.cuts.length}）</span>
        <button className="btn-ghost !py-1 text-xs" onClick={addCut}>
          ＋ カットを追加
        </button>
      </div>

      {data.cuts.map((cut, index) => (
        <div
          key={index}
          className="flex flex-col gap-2 rounded-md border border-ink-600 bg-ink-800/60 p-2"
        >
          <div className="flex items-center gap-2">
            <input
              className="field !w-16"
              aria-label={`カット ${index + 1} の番号`}
              type="number"
              value={cut.no}
              onChange={(event) => patchCut(index, { no: Number(event.target.value) })}
            />
            <input
              className="field flex-1"
              aria-label={`カット ${index + 1} のシーン`}
              value={cut.scene}
              placeholder="シーン（屋上・夕）"
              onChange={(event) => patchCut(index, { scene: event.target.value })}
            />
            <input
              className="field !w-24"
              aria-label={`カット ${index + 1} の尺`}
              type="number"
              step={0.5}
              value={cut.duration ?? ''}
              placeholder="秒"
              onChange={(event) =>
                patchCut(index, {
                  duration: event.target.value === '' ? null : Number(event.target.value),
                })
              }
            />
            <button
              className="btn-ghost !px-2 !py-1 text-xs"
              title="このカットを削除"
              onClick={() => removeCut(index)}
            >
              ✕
            </button>
          </div>
          <textarea
            className="field"
            aria-label={`カット ${index + 1} の内容`}
            rows={2}
            value={cut.description}
            placeholder="何が映るか"
            onChange={(event) => patchCut(index, { description: event.target.value })}
          />
          <div className="grid gap-2 sm:grid-cols-2">
            <input
              className="field"
              aria-label={`カット ${index + 1} のカメラ`}
              value={cut.camera}
              placeholder="カメラ（寄り / パン…）"
              onChange={(event) => patchCut(index, { camera: event.target.value })}
            />
            <input
              className="field"
              aria-label={`カット ${index + 1} の音`}
              value={cut.audio}
              placeholder="音（環境音・セリフ）"
              onChange={(event) => patchCut(index, { audio: event.target.value })}
            />
          </div>
          <textarea
            className="field"
            aria-label={`カット ${index + 1} の生成プロンプト`}
            rows={2}
            value={cut.prompt}
            placeholder="生成プロンプト（英語）"
            onChange={(event) => patchCut(index, { prompt: event.target.value })}
          />
          <input
            className="field"
            aria-label={`カット ${index + 1} のラフ画像`}
            value={cut.image}
            placeholder="ラフ画像の URL（/library/… ）"
            onChange={(event) => patchCut(index, { image: event.target.value })}
          />
        </div>
      ))}
      {data.cuts.length === 0 && (
        <p className="text-xs text-slate-500">まだカットがありません</p>
      )}

      <AreaField label="メモ" value={data.notes} onChange={(notes) => patch({ notes })} />
    </div>
  )
}
