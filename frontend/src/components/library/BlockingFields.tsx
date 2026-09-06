import { useEffect, useState } from 'react'

import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'

/**
 * 数値 1 つの入力欄（構図リファレンスの座標・角度・尺）。
 *
 * 打ちかけ（`-` や `1.` だけの状態）で値を書き戻すと入力が壊れるので、文字列は
 * 手元に持ち、**数として読めたときだけ** `onChange` を呼ぶ。外から値が変わった
 * （平面図をドラッグした）ときは、同じ数を指していない限り取り直す。
 *
 * `commitOnBlur` を付けると、確定（blur か Enter）まで `onChange` を呼ばない。
 * 打ちかけの値でも他を壊してしまう欄（尺: 縮めるとキーフレームが落ちるので、
 * 「8」を消して「10」と打つ途中の 1 で消えてしまう）で使う。
 */
export function NumberField({
  label,
  value,
  onChange,
  step = 0.1,
  min,
  max,
  disabled,
  hideLabel,
  commitOnBlur,
  className,
}: {
  label: string
  value: number
  onChange: (value: number) => void
  step?: number
  min?: number
  max?: number
  disabled?: boolean
  /** 見出しを出さない（表の中で使うとき。読み上げ用には aria-label が残る） */
  hideLabel?: boolean
  /** 打っている間は伝えず、blur か Enter で確定する。 */
  commitOnBlur?: boolean
  className?: string
}) {
  const [text, setText] = useState(String(value))

  useEffect(() => {
    setText((current) => (Number(current) === value ? current : String(value)))
  }, [value])

  /** 打ち終わりの確定（読めない値は元へ戻す）。 */
  const commit = (raw: string) => {
    const parsed = Number(raw)
    if (raw === '' || !Number.isFinite(parsed)) {
      setText(String(value))
      return
    }
    if (parsed !== value) onChange(parsed)
  }

  return (
    <div className={className}>
      {!hideLabel && (
        <Label className="mb-1 text-[11px] text-muted-foreground">{label}</Label>
      )}
      <Input
        type="number"
        className="h-7 px-1.5 text-xs"
        aria-label={label}
        value={text}
        step={step}
        min={min}
        max={max}
        disabled={disabled}
        onChange={(event) => {
          setText(event.target.value)
          if (commitOnBlur) return
          const parsed = Number(event.target.value)
          if (event.target.value !== '' && Number.isFinite(parsed)) onChange(parsed)
        }}
        onBlur={(event) => {
          if (commitOnBlur) commit(event.target.value)
        }}
        onKeyDown={(event) => {
          if (commitOnBlur && event.key === 'Enter') commit(event.currentTarget.value)
        }}
      />
    </div>
  )
}
