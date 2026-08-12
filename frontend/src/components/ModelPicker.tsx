import { modelSlotsForJob, type FormState } from '../form'
import type { ModelSlot } from '../types'
import { Label } from '@/components/ui/label'
import { NativeSelect } from './NativeSelect'

/**
 * 実行時のモデル切り替えセレクト（SPEC §3.3）。
 *
 * 設定ページの「モデル」タブで候補を 2 件以上登録したスロットだけが
 * `/api/options` の `model_slots` に出るので、何も登録していないワークフローでは
 * 何も描画しない（従来どおり設定の既定値で走る）。
 */
export default function ModelPicker({
  slots,
  workflowId,
  form,
  patch,
}: {
  slots: ModelSlot[] | undefined
  workflowId: string
  form: FormState
  patch: (patch: Partial<FormState>) => void
}) {
  const usable = modelSlotsForJob(slots, [workflowId])
  if (usable.length === 0) return null
  return (
    <div className="mt-2 flex flex-col gap-2">
      {usable.map((slot) => {
        const title = `使用モデル: ${slot.label || `${slot.node_id}.${slot.field}`}`
        return (
          <div key={slot.key}>
            <Label className="mb-1">{title}</Label>
            <NativeSelect
              aria-label={title}
              value={form.modelOverrides[slot.key] ?? slot.default}
              onChange={(event) =>
                patch({
                  modelOverrides: {
                    ...form.modelOverrides,
                    [slot.key]: event.target.value,
                  },
                })
              }
            >
              {slot.choices.map((name) => (
                <option key={name} value={name}>
                  {name === slot.default ? `${name}（既定）` : name}
                </option>
              ))}
            </NativeSelect>
          </div>
        )
      })}
    </div>
  )
}
