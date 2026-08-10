import { workflowSelects, type FormState } from '../form'
import type { WorkflowOption } from '../types'
import { FieldError } from './ui'

/** `auto` を宣言した項目に出る「おまかせ」の選択肢（値は未指定 = 空文字）。 */
export const AUTO_LABEL = '自動（入力に合わせる）'

/**
 * ワークフローが宣言した選択式フィールドのセレクト（SPEC §3.1）。
 *
 * 自由記述ではなく決まった選択肢で挙動が決まるワークフロー（踊りの
 * 種類・動きの大きさ・尺）用。宣言のないワークフローでは何も描画しないので、
 * 既存のワークフローの見た目は変わらない。
 */
export default function WorkflowSelects({
  workflow,
  form,
  patch,
  errors,
}: {
  workflow: WorkflowOption | null | undefined
  form: FormState
  patch: (patch: Partial<FormState>) => void
  /** 選択式ごとのエラー（相関の宣言に反したとき。SPEC §3.1）。 */
  errors?: Record<string, string>
}) {
  const selects = workflowSelects(workflow)
  if (selects.length === 0) return null
  return (
    <div className="mt-2 flex flex-col gap-2">
      {selects.map((select) => {
        // 未指定は「自動」（auto のとき）か、ワークフローの既定値。
        const value = form.selects[select.name] ?? ''
        return (
          <div key={select.name}>
            <label className="label">{select.label}</label>
            <select
              className="field"
              aria-label={select.label}
              value={value}
              onChange={(event) =>
                patch({
                  selects: { ...form.selects, [select.name]: event.target.value },
                })
              }
            >
              <option value="">
                {select.auto ? AUTO_LABEL : `既定（${select.default}）`}
              </option>
              {select.choices.map((choice) => (
                <option key={choice} value={choice}>
                  {choice}
                </option>
              ))}
            </select>
            <FieldError message={errors?.[select.name]} />
          </div>
        )
      })}
    </div>
  )
}
