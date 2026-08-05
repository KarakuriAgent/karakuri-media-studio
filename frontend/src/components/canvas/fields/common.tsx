/** カード編集フォームの部品（9 種の fields/* で共有する）。 */
import type { ReactNode } from 'react'

export function Field({
  label,
  hint,
  children,
}: {
  label: string
  hint?: string
  children: ReactNode
}) {
  return (
    <div>
      <label className="label">{label}</label>
      {children}
      {hint && <p className="mt-1 text-[11px] text-slate-500">{hint}</p>}
    </div>
  )
}

export function TextField({
  label,
  value,
  onChange,
  hint,
  placeholder,
}: {
  label: string
  value: string
  onChange: (value: string) => void
  hint?: string
  placeholder?: string
}) {
  return (
    <Field label={label} hint={hint}>
      <input
        className="field"
        aria-label={label}
        value={value}
        placeholder={placeholder}
        onChange={(event) => onChange(event.target.value)}
      />
    </Field>
  )
}

export function AreaField({
  label,
  value,
  onChange,
  rows = 3,
  hint,
  placeholder,
}: {
  label: string
  value: string
  onChange: (value: string) => void
  rows?: number
  hint?: string
  placeholder?: string
}) {
  return (
    <Field label={label} hint={hint}>
      <textarea
        className="field"
        aria-label={label}
        rows={rows}
        value={value}
        placeholder={placeholder}
        onChange={(event) => onChange(event.target.value)}
      />
    </Field>
  )
}

export function NumberField({
  label,
  value,
  onChange,
  step,
  min,
  hint,
}: {
  label: string
  value: number
  onChange: (value: number) => void
  step?: number
  min?: number
  hint?: string
}) {
  return (
    <Field label={label} hint={hint}>
      <input
        className="field"
        aria-label={label}
        type="number"
        step={step}
        min={min}
        value={value}
        onChange={(event) => onChange(Number(event.target.value))}
      />
    </Field>
  )
}

/** URL の並び（1 行 1 件のテキストエリアで編集する）。 */
export function UrlListField({
  label,
  value,
  onChange,
  hint = '1 行に 1 件（/library/… や /outputs/… の URL）',
}: {
  label: string
  value: string[]
  onChange: (value: string[]) => void
  hint?: string
}) {
  return (
    <Field label={label} hint={hint}>
      <textarea
        className="field"
        aria-label={label}
        rows={2}
        value={value.join('\n')}
        onChange={(event) =>
          onChange(
            event.target.value
              .split('\n')
              .map((line) => line.trim())
              .filter(Boolean),
          )
        }
      />
    </Field>
  )
}
