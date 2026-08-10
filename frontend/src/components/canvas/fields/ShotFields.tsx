import { useState } from 'react'
import type {
  StudioProjectDetail,
  StudioShot,
  StudioShotStatus,
  StudioShotUpdate,
} from '../../../types'
import { FieldError } from '../../ui'
import {
  SHOT_STATUS_LABEL,
  sceneOptions,
  shotFormFromShot,
  shotUpdateFromForm,
  validateShotForm,
  type ShotFormState,
} from '../../studio/studio'
import PromptPreview from '../../studio/PromptPreview'
import { AreaField, Field, TextField } from './common'

const STATUSES: StudioShotStatus[] = ['draft', 'ready', 'done']

/**
 * カットカードの編集フォーム（保存先は `PATCH /api/studio/shots/{id}`）。
 *
 * フォームの持ち方・検証・PATCH の組み立ては脚本タブと同じもの
 * （`studio/studio.ts`）を使う。生成設定（アスペクト比・シードなど）まで出すと
 * カードには重いので、ここは脚本まわりの項目だけに絞る。
 */
export default function ShotFields({
  shot,
  detail,
  busy,
  onSave,
}: {
  shot: StudioShot
  detail: StudioProjectDetail
  busy: boolean
  onSave: (patch: StudioShotUpdate) => void
}) {
  const [form, setForm] = useState<ShotFormState>(() => shotFormFromShot(shot))
  const [errors, setErrors] = useState<Record<string, string>>({})
  const scenes = sceneOptions(detail)

  const patch = (changes: Partial<ShotFormState>) =>
    setForm((current) => ({ ...current, ...changes }))

  const save = () => {
    const found = validateShotForm(form)
    setErrors(found)
    if (Object.keys(found).length > 0) return
    onSave(shotUpdateFromForm(form))
  }

  return (
    <div className="flex flex-col gap-3">
      <TextField
        label="カットのタイトル"
        value={form.title}
        onChange={(title) => patch({ title })}
      />

      <div className="grid gap-2 sm:grid-cols-2">
        <Field label="状態">
          <select
            className="field"
            aria-label="状態"
            value={form.status}
            onChange={(event) =>
              patch({ status: event.target.value as StudioShotStatus })
            }
          >
            {STATUSES.map((status) => (
              <option key={status} value={status}>
                {SHOT_STATUS_LABEL[status]}
              </option>
            ))}
          </select>
        </Field>
        <Field label="所属する場">
          <select
            className="field"
            aria-label="所属する場"
            value={form.scene_id}
            onChange={(event) => patch({ scene_id: event.target.value })}
          >
            <option value="">未分類</option>
            {scenes.map((scene) => (
              <option key={scene.id} value={scene.id}>
                {scene.label}
              </option>
            ))}
          </select>
        </Field>
      </div>

      <AreaField
        label="プロンプト"
        value={form.prompt}
        rows={4}
        onChange={(prompt) => patch({ prompt })}
      />
      <FieldError message={errors.prompt} />

      <AreaField
        label="アクション"
        value={form.action}
        rows={2}
        onChange={(action) => patch({ action })}
      />
      <AreaField
        label="台詞"
        value={form.dialogue}
        rows={2}
        onChange={(dialogue) => patch({ dialogue })}
      />
      <div className="grid gap-2 sm:grid-cols-2">
        <TextField
          label="効果音・環境音"
          value={form.soundscape}
          onChange={(soundscape) => patch({ soundscape })}
        />
        <TextField
          label="BGM"
          value={form.bgm}
          onChange={(bgm) => patch({ bgm })}
        />
      </div>
      <div className="grid gap-2 sm:grid-cols-2">
        <TextField
          label="カメラ"
          value={form.camera}
          onChange={(camera) => patch({ camera })}
        />
        <div>
          <TextField
            label="尺（秒）"
            value={form.duration_seconds}
            onChange={(duration_seconds) => patch({ duration_seconds })}
          />
          <FieldError message={errors.duration_seconds} />
        </div>
      </div>
      <AreaField
        label="目的"
        value={form.purpose}
        rows={2}
        onChange={(purpose) => patch({ purpose })}
      />

      <button className="btn-primary self-start" disabled={busy} onClick={save}>
        {busy ? '保存中…' : '保存'}
      </button>

      <PromptPreview shot={shot} />
    </div>
  )
}
