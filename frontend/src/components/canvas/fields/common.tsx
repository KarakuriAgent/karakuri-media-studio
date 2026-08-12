/** カード編集フォームの部品（9 種の fields/* で共有する）。 */
import type { ReactNode } from 'react'

import { Input } from '../../ui/input'
import { Label } from '../../ui/label'
import { Textarea } from '../../ui/textarea'

export function Field({
  label,
  children,
}: {
  label: string
  children: ReactNode
}) {
  return (
    <div>
      <Label className="mb-1">{label}</Label>
      {children}
    </div>
  )
}

export function TextField({
  label,
  value,
  onChange,
  placeholder,
}: {
  label: string
  value: string
  onChange: (value: string) => void
  placeholder?: string
}) {
  return (
    <Field label={label}>
      <Input
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
  placeholder,
}: {
  label: string
  value: string
  onChange: (value: string) => void
  rows?: number
  placeholder?: string
}) {
  return (
    <Field label={label}>
      <Textarea
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
}: {
  label: string
  value: number
  onChange: (value: number) => void
  step?: number
  min?: number
}) {
  return (
    <Field label={label}>
      <Input
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
}: {
  label: string
  value: string[]
  onChange: (value: string[]) => void
}) {
  return (
    <Field label={label}>
      <Textarea
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
