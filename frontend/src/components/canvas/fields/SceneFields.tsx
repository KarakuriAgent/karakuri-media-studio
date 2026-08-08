import { useState } from 'react'
import type {
  StudioProjectDetail,
  StudioScene,
  StudioSceneUpdate,
} from '../../../types'
import {
  SHOT_STATUS_CLASS,
  SHOT_STATUS_LABEL,
  episodeLabel,
} from '../../studio/studio'
import { shotsInScene } from '../logic'
import { AreaField, Field, TextField } from './common'

/**
 * 場カードの編集フォーム（保存先は `PATCH /api/studio/scenes/{id}`）。
 *
 * 場に属するカットは読むだけで並べる。カットの中身を直すのはカット側の
 * カード（またはスタジオの脚本タブ）の仕事。
 *
 * 所属する話はここで変えられる（キャンバスのタブ = 話なので、変えると次の
 * 読み込みでこの場とそのカットが移動先のタブに現れる）。
 */
export default function SceneFields({
  scene,
  detail,
  busy,
  onSave,
}: {
  scene: StudioScene
  detail: StudioProjectDetail
  busy: boolean
  onSave: (patch: StudioSceneUpdate) => void
}) {
  const [title, setTitle] = useState(scene.title)
  const [synopsis, setSynopsis] = useState(scene.synopsis)
  const [timeOfDay, setTimeOfDay] = useState(scene.time_of_day)
  const [episodeId, setEpisodeId] = useState(scene.episode_id)
  const shots = shotsInScene(detail, scene.id)

  return (
    <div className="flex flex-col gap-3">
      <TextField label="場のタイトル" value={title} onChange={setTitle} />
      <Field
        label="所属する話"
        hint="変えると、この場とそのカットが移動先の話のタブに移ります"
      >
        <select
          className="field"
          aria-label="所属する話"
          value={episodeId}
          onChange={(event) => setEpisodeId(event.target.value)}
        >
          {detail.episodes.map((episode, index) => (
            <option key={episode.id} value={episode.id}>
              {episodeLabel(episode, index)}
            </option>
          ))}
        </select>
      </Field>
      <AreaField label="あらすじ" value={synopsis} rows={3} onChange={setSynopsis} />
      <TextField
        label="時間帯"
        value={timeOfDay}
        onChange={setTimeOfDay}
        hint="「夜明け前」「閉店後」などのメモ"
      />

      <div className="rounded-md border border-ink-600 bg-ink-900 p-2">
        <p className="mb-1 text-[11px] text-slate-500">
          この場のカット（{shots.length} 件）
        </p>
        {shots.length === 0 ? (
          <p className="py-2 text-center text-xs text-slate-600">まだありません</p>
        ) : (
          <ul className="flex flex-col gap-1">
            {shots.map((shot, index) => (
              <li key={shot.id} className="flex items-center gap-2 text-xs">
                <span className="w-6 shrink-0 text-slate-600">{index + 1}</span>
                <span className="min-w-0 flex-1 truncate text-slate-300">
                  {shot.title || '無題のカット'}
                </span>
                <span className={`chip !px-1.5 !py-0 ${SHOT_STATUS_CLASS[shot.status]}`}>
                  {SHOT_STATUS_LABEL[shot.status]}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>

      <button
        className="btn-primary self-start"
        disabled={busy}
        onClick={() =>
          onSave({
            title,
            synopsis,
            time_of_day: timeOfDay,
            ...(episodeId !== scene.episode_id ? { episode_id: episodeId } : {}),
          })
        }
      >
        {busy ? '保存中…' : '保存'}
      </button>
    </div>
  )
}
