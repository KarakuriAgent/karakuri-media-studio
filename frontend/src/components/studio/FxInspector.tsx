import { useEffect, useState } from 'react'
import { Trash2 } from 'lucide-react'

import type { TimelineFxEvent } from '../../types'
import { Section } from '../ui'
import { Button } from '../ui/button'
import { Checkbox } from '../ui/checkbox'
import {
  FX_COMMON_FIELDS,
  fxDurationMs,
  fxLabel,
  fxMainFields,
  fxRest,
  fxStartMs,
  fxType,
} from './fx'
import { formatSeconds, formatTimecode } from './timeline'

/**
 * 選んでいる演出（FX トラックのイベント）の中身と、そのイベントに効く操作。
 *
 * 出すのは 3 段:
 *
 * - **共通** … `t` / `until` / `duration` / `z` と、出す・出さない（`enabled`）
 * - **主要項目** … その型が持っている `text` / `lines` / `src` / `cx` / `cy` /
 *   `w` / `color`（人がプレビューを見ながら直したくなるもの）
 * - **残り** … JSON のテキスト欄。知らない型・知らない項目もここで触れる
 *
 * 送るのは**変わった項目だけ**（`event` の浅いマージ）。空にした項目は `null`
 * で送られてイベントから消える。新規作成の入り口は持たない（作るのは外部 API）。
 */
export default function FxInspector({
  item,
  busy,
  onPatch,
  onEnabled,
  onDelete,
}: {
  item: TimelineFxEvent | null
  busy: boolean
  /** `event` の項目を書き換える（浅いマージ。`null` はその項目の削除）。 */
  onPatch: (patch: Record<string, unknown>) => void
  onEnabled: (enabled: boolean) => void
  onDelete: () => void
}) {
  if (!item) {
    return (
      <Section title="演出">
        <p className="text-xs text-muted-foreground">
          FX トラックで帯を選ぶと、ここに中身が出ます。演出を作るのは外部 API
          （AI）で、ここでは調整と削除だけができます。
        </p>
      </Section>
    )
  }

  return <FxFields key={item.id} item={item} busy={busy} onPatch={onPatch} onEnabled={onEnabled} onDelete={onDelete} />
}

function FxFields({
  item,
  busy,
  onPatch,
  onEnabled,
  onDelete,
}: {
  item: TimelineFxEvent
  busy: boolean
  onPatch: (patch: Record<string, unknown>) => void
  onEnabled: (enabled: boolean) => void
  onDelete: () => void
}) {
  const rest = fxRest(item)
  const [restText, setRestText] = useState(() => JSON.stringify(rest, null, 2))
  const [restError, setRestError] = useState<string | null>(null)

  // 帯のドラッグやサーバーからの読み直しで中身が変わったら、テキスト欄も追う
  // （編集中の打ち掛けは JSON の文字列が変わらないかぎり触らない）。
  const restJson = JSON.stringify(rest, null, 2)
  useEffect(() => {
    setRestText(restJson)
    setRestError(null)
  }, [restJson])

  const applyRest = () => {
    let parsed: unknown
    try {
      parsed = JSON.parse(restText || '{}')
    } catch (cause) {
      setRestError(cause instanceof Error ? cause.message : String(cause))
      return
    }
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      setRestError('JSON のオブジェクト（{…}）で書いてください')
      return
    }
    setRestError(null)
    const next = parsed as Record<string, unknown>
    const patch: Record<string, unknown> = { ...next }
    // 消えた項目は null で送る（サーバー側がその項目を落とす）。
    for (const name of Object.keys(rest)) {
      if (!(name in next)) patch[name] = null
    }
    onPatch(patch)
  }

  return (
    <Section
      title={`演出: ${fxType(item) || '不明'}`}
      right={
        <Button
          type="button"
          size="sm"
          variant="ghost"
          disabled={busy}
          onClick={onDelete}
          title="この演出を消す"
        >
          <Trash2 className="size-4" aria-hidden="true" />
          削除
        </Button>
      }
    >
      <div className="flex flex-col gap-2">
        <p className="break-words text-xs text-foreground/85">{fxLabel(item)}</p>
        <p className="text-[11px] text-muted-foreground">
          {formatTimecode(fxStartMs(item))} から {formatSeconds(fxDurationMs(item))}
        </p>

        <label className="flex cursor-pointer items-center gap-2 text-[11px]">
          <Checkbox
            checked={item.enabled}
            disabled={busy}
            onCheckedChange={(value) => onEnabled(value === true)}
          />
          <span>プレビューと書き出しに出す</span>
        </label>

        <div className="grid grid-cols-2 gap-2">
          {FX_COMMON_FIELDS.map((field) => (
            <ValueField
              key={field.name}
              label={field.label}
              value={item.event[field.name]}
              kind={field.kind}
              disabled={busy}
              onCommit={(value) => onPatch({ [field.name]: value })}
            />
          ))}
        </div>

        {fxMainFields(item).length > 0 && (
          <div className="flex flex-col gap-2 border-t border-border pt-2">
            {fxMainFields(item).map((field) => (
              <ValueField
                key={field.name}
                label={field.label}
                value={item.event[field.name]}
                kind={field.kind}
                disabled={busy}
                onCommit={(value) => onPatch({ [field.name]: value })}
              />
            ))}
          </div>
        )}

        <label className="flex flex-col gap-1 border-t border-border pt-2 text-[11px]">
          <span className="text-muted-foreground">
            残りの項目（JSON。離すと保存）
          </span>
          <textarea
            className="h-40 rounded-md border border-border bg-background px-2 py-1 font-mono text-[11px]"
            value={restText}
            disabled={busy}
            spellCheck={false}
            onChange={(event) => setRestText(event.target.value)}
            onBlur={applyRest}
          />
        </label>
        {restError && (
          <p className="rounded border border-red-900/70 bg-red-950/50 px-2 py-1 text-[11px] text-red-200">
            {restError}
          </p>
        )}
      </div>
    </Section>
  )
}

/**
 * 項目 1 つ（数値 / 文字列）。空にすると `null`（＝その項目を消す）を送る。
 *
 * `lines` のような配列・オブジェクトの項目は JSON の文字列として編集する。
 */
function ValueField({
  label,
  value,
  kind,
  disabled,
  onCommit,
}: {
  label: string
  value: unknown
  kind: 'number' | 'text'
  disabled: boolean
  onCommit: (value: unknown) => void
}) {
  const structured = value !== null && typeof value === 'object'
  const initial =
    value === undefined || value === null
      ? ''
      : structured
        ? JSON.stringify(value)
        : String(value)
  const [text, setText] = useState(initial)
  const [error, setError] = useState(false)

  useEffect(() => {
    setText(initial)
    setError(false)
  }, [initial])

  const commit = () => {
    const trimmed = text.trim()
    if (trimmed === initial.trim()) return
    if (trimmed === '') {
      onCommit(null)
      return
    }
    if (structured) {
      try {
        onCommit(JSON.parse(trimmed))
        setError(false)
      } catch {
        setError(true)
      }
      return
    }
    if (kind === 'number') {
      const parsed = Number(trimmed)
      if (!Number.isFinite(parsed)) {
        setError(true)
        return
      }
      setError(false)
      onCommit(parsed)
      return
    }
    setError(false)
    onCommit(trimmed)
  }

  return (
    <label className="flex flex-col gap-1 text-[11px]">
      <span className="text-muted-foreground">{label}</span>
      <input
        type="text"
        inputMode={kind === 'number' && !structured ? 'decimal' : undefined}
        className={`h-7 rounded-md border bg-background px-2 text-[11px] ${
          error ? 'border-red-700' : 'border-border'
        }`}
        value={text}
        disabled={disabled}
        aria-label={label}
        spellCheck={false}
        onChange={(event) => setText(event.target.value)}
        onBlur={commit}
        onKeyDown={(event) => {
          if (event.key === 'Enter') event.currentTarget.blur()
        }}
      />
    </label>
  )
}
