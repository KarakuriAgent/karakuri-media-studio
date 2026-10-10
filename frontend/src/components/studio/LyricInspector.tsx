import { useEffect, useMemo, useRef, useState } from 'react'
import { Dices, Trash2 } from 'lucide-react'

import { Section } from '../ui'
import { Button } from '../ui/button'
import { Checkbox } from '../ui/checkbox'
import {
  LYRIC_CENTER_DIRS,
  LYRIC_FX_SLIDERS,
  LYRIC_KOMA_OPTIONS,
  LYRIC_OFFSET_RANGE,
  LYRIC_OVERRIDE_GROUPS,
  LYRIC_PLACE_PRESETS,
  LYRIC_SCALE_RANGE,
  LYRIC_SETS,
  LYRIC_SWITCHES,
  type Lyric,
  type LyricPlace,
  lyricFlag,
  lyricFxValue,
  lyricOverride,
  lyricPlace,
  lyricRest,
  lyricSeed,
  lyricText,
  rollSeed,
  setLyricFx,
  setLyricOverride,
  setLyricPlace,
  withLyricRest,
} from './lyric'

/**
 * 歌詞モーション（FX トラックに相乗りする JIZURA の層）のプロパティパネル。
 *
 * `FxInspector` と同じ流儀で、出すのは 3 段:
 *
 * - **全体** … 出す・出さない / スタイル / 雰囲気 / シード / 追加分・和風 /
 *   部品セット（文字PV系・キネティック・ホラー）/ 統一感・文字整列 /
 *   中央を空ける（縦長のときの向き）/ 演出の強さ（スライダー）/ 歌詞テキスト
 *   （LRC 記法のまま）
 * - **行ごとの上書き** … レイアウト・入り・保持・抜け・文字の処理・背景・カメラの
 *   指名（既定は「自動」）と装飾の複数選択、1 カットに固定、その行だけのシード、
 *   位置 X / Y・拡大率（手元パッチの `offset` / `scale`）とそのプリセット
 * - **残り** … JSON のテキスト欄（カット単位の指名 `cutTech` などは行の上書きの
 *   中にあり、ここでは消さずに持ち回る）
 *
 * 選択肢の名前は**実行時に JIZURA の登録一覧から取る**（`J.order(group)` と
 * `J.registry(group)[key].name`）。エンジンは 2MB 級なので動的 import で、この
 * パネルを開くまで読み込まない。`extra: false` のときは追加分を、OFF の部品
 * セットの部品を薄く出す（JIZURA の UI と同じ流儀。選べはする——手で指名する
 * ぶんには効く）。
 *
 * 作るのは外部 API（AI）で、ここでできるのは調整と削除だけ（SPEC §7.3）。
 * select は即時、テキスト欄はデバウンスして保存する。
 */
export default function LyricInspector({
  lyric,
  enabled,
  busy,
  onChange,
  onEnabled,
  onDelete,
}: {
  lyric: Lyric
  enabled: boolean
  busy: boolean
  /** 書き換えた `lyric` を丸ごと保存する（`PUT …/fx/lyric`）。 */
  onChange: (next: Lyric) => void
  onEnabled: (enabled: boolean) => void
  onDelete: () => void
}) {
  const engine = useJizura()
  const lines = useMemo(
    () => (engine ? engine.parseLyrics(lyricText(lyric)).lines : []),
    [engine, lyricText(lyric)],
  )
  const extra = lyric.extra === true
  const sets: Record<string, boolean> = Object.fromEntries(
    LYRIC_SETS.map((set) => [set.name, lyricFlag(lyric, set.name, set.fallback)]),
  )
  const centerFree = lyricFlag(lyric, 'centerFree', false)

  return (
    <Section
      title="歌詞モーション"
      right={
        <Button
          type="button"
          size="sm"
          variant="ghost"
          disabled={busy}
          onClick={onDelete}
          title="歌詞モーションを消す"
        >
          <Trash2 className="size-4" aria-hidden="true" />
          削除
        </Button>
      }
    >
      <div className="flex flex-col gap-2">
        <label className="flex cursor-pointer items-center gap-2 text-[11px]">
          <Checkbox
            checked={enabled}
            disabled={busy}
            onCheckedChange={(value) => onEnabled(value === true)}
          />
          <span>プレビューと書き出しに出す</span>
        </label>

        {/* ---------------------------------------------------------- 全体 */}
        <div className="grid grid-cols-2 gap-2 border-t border-border pt-2">
          <Picker
            label="スタイル"
            value={typeof lyric.style === 'string' ? lyric.style : ''}
            autoLabel="任せる"
            options={engine?.styles ?? []}
            extra={extra}
            sets={sets}
            disabled={busy || !engine}
            onChange={(value) => onChange({ ...lyric, style: value })}
          />
          <Picker
            label="雰囲気"
            value={typeof lyric.mood === 'string' ? lyric.mood : ''}
            autoLabel="なし"
            options={engine?.moods ?? []}
            extra
            sets={sets}
            disabled={busy || !engine}
            onChange={(value) =>
              onChange({ ...lyric, mood: value === '' ? null : value })
            }
          />
        </div>

        <div className="flex items-end gap-2">
          <label className="flex min-w-0 flex-1 flex-col gap-1 text-[11px]">
            <span className="text-muted-foreground">シード</span>
            <input
              type="number"
              className="h-7 rounded-md border border-border bg-background px-2 text-[11px]"
              value={lyricSeed(lyric)}
              disabled={busy}
              onChange={(event) => {
                const parsed = Number(event.target.value)
                if (Number.isFinite(parsed)) {
                  onChange({ ...lyric, seed: Math.round(parsed) })
                }
              }}
            />
          </label>
          <Button
            type="button"
            size="sm"
            variant="secondary"
            disabled={busy}
            title="シードを振り直す（絵の選び方が変わる）"
            onClick={() => onChange({ ...lyric, seed: rollSeed() })}
          >
            <Dices className="size-4" aria-hidden="true" />
            振り直す
          </Button>
        </div>

        <div className="flex flex-wrap gap-3">
          <label className="flex cursor-pointer items-center gap-2 text-[11px]">
            <Checkbox
              checked={extra}
              disabled={busy}
              onCheckedChange={(value) =>
                onChange({ ...lyric, extra: value === true })
              }
            />
            <span title="初版より後に足された部品もランダムに選ぶ">追加分</span>
          </label>
          <label className="flex cursor-pointer items-center gap-2 text-[11px]">
            <Checkbox
              checked={lyric.wa !== false}
              disabled={busy}
              onCheckedChange={(value) =>
                onChange({ ...lyric, wa: value === true })
              }
            />
            <span title="和風モチーフ（提灯・障子・家紋…）をランダムに選ぶ">
              和風
            </span>
          </label>
        </div>

        {/* ------------------------------------ 部品セット・統一感・文字整列 */}
        <div className="flex flex-col gap-1">
          <span className="text-[11px] text-muted-foreground">
            部品セット（OFF ならランダムには選ばない）
          </span>
          <div className="flex flex-wrap gap-3">
            {LYRIC_SETS.map((set) => (
              <label
                key={set.name}
                className="flex cursor-pointer items-center gap-2 text-[11px]"
              >
                <Checkbox
                  checked={sets[set.name]}
                  disabled={busy}
                  onCheckedChange={(value) =>
                    onChange({ ...lyric, [set.name]: value === true })
                  }
                />
                <span>{set.label}</span>
              </label>
            ))}
          </div>
        </div>
        <div className="flex flex-wrap gap-3">
          {LYRIC_SWITCHES.map((item) => (
            <label
              key={item.name}
              className="flex cursor-pointer items-center gap-2 text-[11px]"
            >
              <Checkbox
                checked={lyricFlag(lyric, item.name, false)}
                disabled={busy}
                onCheckedChange={(value) =>
                  onChange({ ...lyric, [item.name]: value === true })
                }
              />
              <span title={item.title}>{item.label}</span>
            </label>
          ))}
        </div>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <label className="flex cursor-pointer items-center gap-2 text-[11px]">
            <Checkbox
              checked={centerFree}
              disabled={busy}
              onCheckedChange={(value) =>
                onChange({ ...lyric, centerFree: value === true })
              }
            />
            <span title="歌詞を左右（縦長なら上下 / 左右）の帯に分けて、中央をキャラクターのために空ける">
              中央を空ける
            </span>
          </label>
          <label className="flex items-center gap-1 text-[11px]">
            <span className="text-muted-foreground">縦長のときの向き</span>
            <select
              className="h-7 rounded-md border border-border bg-background px-1 text-[11px]"
              aria-label="縦長のときの向き"
              value={lyric.centerDir === 'lr' ? 'lr' : 'tb'}
              disabled={busy || !centerFree}
              onChange={(event) =>
                onChange({ ...lyric, centerDir: event.target.value })
              }
            >
              {LYRIC_CENTER_DIRS.map((dir) => (
                <option key={dir.value} value={dir.value}>
                  {dir.label}
                </option>
              ))}
            </select>
          </label>
        </div>

        {/* ------------------------------------------------ 演出の強さ（fx） */}
        <div className="flex flex-col gap-1 border-t border-border pt-2">
          <span className="text-[11px] text-muted-foreground">演出の強さ</span>
          {LYRIC_FX_SLIDERS.map((slider) => (
            <FxSlider
              key={slider.name}
              label={slider.label}
              value={lyricFxValue(lyric, slider.name)}
              disabled={busy}
              onChange={(value) => onChange(setLyricFx(lyric, slider.name, value))}
            />
          ))}
          <div className="mt-1 flex items-center justify-between gap-2">
            <label className="flex cursor-pointer items-center gap-2 text-[11px]">
              <Checkbox
                checked={lyricFxValue(lyric, 'flash') === true}
                disabled={busy}
                onCheckedChange={(value) =>
                  onChange(setLyricFx(lyric, 'flash', value === true))
                }
              />
              <span>決めのフラッシュ</span>
            </label>
            <label className="flex items-center gap-1 text-[11px]">
              <span className="text-muted-foreground">作画</span>
              <select
                className="h-7 rounded-md border border-border bg-background px-1 text-[11px]"
                aria-label="作画枚数"
                value={String(lyricFxValue(lyric, 'koma') ?? '')}
                disabled={busy}
                onChange={(event) =>
                  onChange(
                    setLyricFx(
                      lyric,
                      'koma',
                      event.target.value === ''
                        ? undefined
                        : Number(event.target.value),
                    ),
                  )
                }
              >
                <option value="">任せる</option>
                {LYRIC_KOMA_OPTIONS.map((option) => (
                  <option key={option.value} value={String(option.value)}>
                    {option.label}
                  </option>
                ))}
              </select>
            </label>
          </div>
        </div>

        {/* -------------------------------------------------------- 歌詞 */}
        <DebouncedText
          label="歌詞（LRC 記法のまま）"
          value={lyricText(lyric)}
          rows={6}
          disabled={busy}
          onCommit={(value) => onChange({ ...lyric, lyrics: value })}
        />

        {/* ------------------------------------------------ 行ごとの上書き */}
        <div className="flex flex-col gap-2 border-t border-border pt-2">
          <span className="text-[11px] text-muted-foreground">
            行ごとの上書き（指名しなければ自動）
          </span>
          {!engine && (
            <p className="text-[11px] text-muted-foreground">
              部品の一覧を読み込んでいます…
            </p>
          )}
          {engine && lines.length === 0 && (
            <p className="text-[11px] text-muted-foreground">
              歌詞がまだありません。
            </p>
          )}
          {engine &&
            lines.map((line, index) => (
              <LineOverride
                key={index}
                index={index}
                text={line.interlude ? '（間奏）' : line.text}
                engine={engine}
                extra={extra}
                sets={sets}
                lyric={lyric}
                disabled={busy}
                onChange={onChange}
              />
            ))}
        </div>

        {/* -------------------------------------------------------- 残り */}
        <RestJson lyric={lyric} disabled={busy} onChange={onChange} />
      </div>
    </Section>
  )
}

// ---------------------------------------------------------------------------
// 行 1 つぶん
// ---------------------------------------------------------------------------

function LineOverride({
  index,
  text,
  engine,
  extra,
  sets,
  lyric,
  disabled,
  onChange,
}: {
  index: number
  text: string
  engine: Engine
  extra: boolean
  sets: Record<string, boolean>
  lyric: Lyric
  disabled: boolean
  onChange: (next: Lyric) => void
}) {
  const override = lyricOverride(lyric, index)
  const decor = Array.isArray(override.decor)
    ? (override.decor as unknown[]).filter(
        (item): item is string => typeof item === 'string',
      )
    : null
  const touched = Object.keys(override).length > 0

  return (
    <div
      className={`flex flex-col gap-1 rounded-md border px-2 py-1.5 ${
        touched ? 'border-accent-500/50 bg-accent-500/5' : 'border-border'
      }`}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="min-w-0 truncate text-[11px] font-medium">
          {index + 1}. {text}
        </span>
        <Button
          type="button"
          size="icon-xs"
          variant="ghost"
          disabled={disabled}
          title="この行だけ振り直す"
          onClick={() =>
            onChange(setLyricOverride(lyric, index, { seed: rollSeed() }))
          }
        >
          <Dices className="size-3.5" aria-hidden="true" />
          <span className="sr-only">この行だけ振り直す</span>
        </Button>
      </div>

      <div className="grid grid-cols-2 gap-1">
        {LYRIC_OVERRIDE_GROUPS.map((group) => (
          <Picker
            key={group.name}
            label={group.label}
            value={
              typeof override[group.name] === 'string'
                ? (override[group.name] as string)
                : ''
            }
            autoLabel="自動"
            options={engine.groups[group.name] ?? []}
            extra={extra}
            sets={sets}
            disabled={disabled}
            onChange={(value) =>
              onChange(
                setLyricOverride(lyric, index, {
                  [group.name]: value === '' ? undefined : value,
                }),
              )
            }
          />
        ))}
      </div>

      <label className="flex flex-col gap-1 text-[11px]">
        <span className="text-muted-foreground">装飾（複数選べます）</span>
        <select
          multiple
          size={3}
          className="rounded-md border border-border bg-background px-1 py-1 text-[11px]"
          aria-label={`${index + 1} 行目の装飾`}
          value={decor ?? []}
          disabled={disabled}
          onChange={(event) => {
            const picked = Array.from(event.target.selectedOptions).map(
              (option) => option.value,
            )
            onChange(
              setLyricOverride(lyric, index, {
                decor: picked.length > 0 ? picked : undefined,
              }),
            )
          }}
        >
          {(engine.groups.decor ?? []).map((option) => {
            const note = optionNote(option, extra, sets)
            return (
              <option
                key={option.key}
                value={option.key}
                className={note ? 'opacity-50' : undefined}
              >
                {option.name}
                {note}
              </option>
            )
          })}
        </select>
      </label>

      <label className="flex cursor-pointer items-center gap-2 text-[11px]">
        <Checkbox
          checked={override.single === true}
          disabled={disabled}
          onCheckedChange={(value) =>
            onChange(
              setLyricOverride(lyric, index, {
                single: value === true ? true : undefined,
              }),
            )
          }
        />
        <span>1 カットに収める</span>
      </label>

      <LinePlace
        index={index}
        place={lyricPlace(lyric, index)}
        disabled={disabled}
        onChange={(patch) => onChange(setLyricPlace(lyric, index, patch))}
      />
    </div>
  )
}

/**
 * 行の位置 X / Y（画面比。中央を空けるときは帯に対する比）と拡大率。
 * 数値欄は打ち終わり（0.8 秒）・Enter・フォーカスが外れたときに保存し、
 * プリセットは即時に書いた軸だけを変える。
 */
function LinePlace({
  index,
  place,
  disabled,
  onChange,
}: {
  index: number
  place: LyricPlace
  disabled: boolean
  /** null で位置と拡大率を消す（リセット）。 */
  onChange: (patch: Partial<LyricPlace> | null) => void
}) {
  const fields = [
    { name: 'x', label: '位置 X', step: 0.05, ...LYRIC_OFFSET_RANGE },
    { name: 'y', label: '位置 Y', step: 0.05, ...LYRIC_OFFSET_RANGE },
    { name: 'scale', label: '拡大率', step: 0.05, ...LYRIC_SCALE_RANGE },
  ] as const
  return (
    <div className="flex flex-col gap-1">
      <div className="grid grid-cols-3 gap-1">
        {fields.map((field) => (
          <label key={field.name} className="flex min-w-0 flex-col gap-1 text-[11px]">
            <span className="truncate text-muted-foreground">{field.label}</span>
            <DebouncedNumber
              label={`${index + 1} 行目の${field.label}`}
              value={place[field.name]}
              min={field.min}
              max={field.max}
              step={field.step}
              disabled={disabled}
              onCommit={(value) => onChange({ [field.name]: value })}
            />
          </label>
        ))}
      </div>
      <div className="flex flex-wrap gap-1">
        {LYRIC_PLACE_PRESETS.map((preset) => (
          <Button
            key={preset.name}
            type="button"
            size="sm"
            variant="ghost"
            className="h-6 px-2 text-[11px]"
            disabled={disabled}
            aria-label={`${index + 1} 行目を${preset.label}`}
            onClick={() => onChange(preset.place)}
          >
            {preset.label}
          </Button>
        ))}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// 部品
// ---------------------------------------------------------------------------

/** 部品セットの表示名（`LYRIC_SETS` から）。 */
const SET_LABELS: Record<string, string> = Object.fromEntries(
  LYRIC_SETS.map((set) => [set.name, set.label]),
)

/**
 * 選択肢に添える注記（ランダムには選ばれない部品の印）。
 * `extra: false` なら追加分、OFF の部品セットならそのセットの名前。
 */
function optionNote(
  option: Option,
  extra: boolean,
  sets: Record<string, boolean>,
): string {
  if (option.set && sets[option.set] === false) {
    return `（${SET_LABELS[option.set] ?? option.set}・OFF）`
  }
  if (!extra && option.extra) return '（追加分）'
  return ''
}

/** 選択肢 1 つ（ランダムには選ばれない部品は薄く出す）。 */
function Picker({
  label,
  value,
  autoLabel,
  options,
  extra,
  sets,
  disabled,
  onChange,
}: {
  label: string
  value: string
  /** 何も指名していないときの選択肢の名前（「自動」「任せる」「なし」）。 */
  autoLabel: string
  options: Option[]
  extra: boolean
  /** 部品セットの ON / OFF（OFF のセットの部品に印を付ける）。 */
  sets: Record<string, boolean>
  disabled: boolean
  onChange: (value: string) => void
}) {
  return (
    <label className="flex min-w-0 flex-col gap-1 text-[11px]">
      <span className="truncate text-muted-foreground">{label}</span>
      <select
        className="h-7 min-w-0 rounded-md border border-border bg-background px-1 text-[11px]"
        aria-label={label}
        value={value}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value)}
      >
        <option value="">{autoLabel}</option>
        {options.map((option) => {
          const note = optionNote(option, extra, sets)
          return (
            <option
              key={option.key}
              value={option.key}
              className={note ? 'opacity-50' : undefined}
            >
              {option.name}
              {note}
            </option>
          )
        })}
      </select>
    </label>
  )
}

/** 0〜1 のスライダー（空なら「任せる」）。 */
function FxSlider({
  label,
  value,
  disabled,
  onChange,
}: {
  label: string
  value: unknown
  disabled: boolean
  onChange: (value: number | undefined) => void
}) {
  const current = typeof value === 'number' && Number.isFinite(value) ? value : null
  return (
    <div className="flex items-center gap-2 text-[11px]">
      <span className="w-14 shrink-0 text-muted-foreground">{label}</span>
      <input
        type="range"
        className="min-w-0 flex-1"
        aria-label={label}
        min={0}
        max={1}
        step={0.05}
        value={current ?? 0.5}
        disabled={disabled}
        onChange={(event) => onChange(Number(event.target.value))}
      />
      <span className="w-8 shrink-0 text-right tabular-nums text-muted-foreground">
        {current === null ? '—' : current.toFixed(2)}
      </span>
    </div>
  )
}

/** テキスト欄（打ち終わり 1.5 秒で保存。外れたときも保存）。 */
function DebouncedText({
  label,
  value,
  rows,
  disabled,
  onCommit,
}: {
  label: string
  value: string
  rows: number
  disabled: boolean
  onCommit: (value: string) => void
}) {
  const [text, setText] = useState(value)
  const timer = useRef<number | null>(null)

  // サーバーからの読み直しで中身が変わったら追う（打ち掛けは触らない）。
  useEffect(() => {
    setText(value)
  }, [value])

  const schedule = (next: string) => {
    setText(next)
    if (timer.current !== null) window.clearTimeout(timer.current)
    timer.current = window.setTimeout(() => {
      timer.current = null
      if (next !== value) onCommit(next)
    }, 1500)
  }

  const flush = () => {
    if (timer.current !== null) {
      window.clearTimeout(timer.current)
      timer.current = null
    }
    if (text !== value) onCommit(text)
  }

  useEffect(
    () => () => {
      if (timer.current !== null) window.clearTimeout(timer.current)
    },
    [],
  )

  return (
    <label className="flex flex-col gap-1 text-[11px]">
      <span className="text-muted-foreground">{label}</span>
      <textarea
        className="rounded-md border border-border bg-background px-2 py-1 font-mono text-[11px]"
        rows={rows}
        aria-label={label}
        value={text}
        disabled={disabled}
        spellCheck={false}
        onChange={(event) => schedule(event.target.value)}
        onBlur={flush}
      />
    </label>
  )
}

/**
 * 数値欄（打ち終わり 0.8 秒・Enter・外れたときに保存）。保存中はパネルごと
 * 触れなくなるので、1 文字ごとには保存しない（`0.25` を打つ途中で止まらないように）。
 * 数として読めない中身は保存せず、外れたときに元の値へ戻す。
 */
function DebouncedNumber({
  label,
  value,
  min,
  max,
  step,
  disabled,
  onCommit,
}: {
  label: string
  value: number
  min: number
  max: number
  step: number
  disabled: boolean
  onCommit: (value: number) => void
}) {
  const [text, setText] = useState(String(value))
  const timer = useRef<number | null>(null)

  // サーバーからの読み直しで値が変わったら追う（打ち掛けは触らない）。
  useEffect(() => {
    setText(String(value))
  }, [value])

  useEffect(
    () => () => {
      if (timer.current !== null) window.clearTimeout(timer.current)
    },
    [],
  )

  const commit = (raw: string, revert: boolean) => {
    if (timer.current !== null) {
      window.clearTimeout(timer.current)
      timer.current = null
    }
    const parsed = Number(raw)
    if (raw.trim() === '' || !Number.isFinite(parsed)) {
      if (revert) setText(String(value))
      return
    }
    if (parsed !== value) onCommit(parsed)
  }

  return (
    <input
      type="number"
      className="h-7 min-w-0 rounded-md border border-border bg-background px-1 text-[11px]"
      aria-label={label}
      min={min}
      max={max}
      step={step}
      value={text}
      disabled={disabled}
      onChange={(event) => {
        const next = event.target.value
        setText(next)
        if (timer.current !== null) window.clearTimeout(timer.current)
        timer.current = window.setTimeout(() => commit(next, false), 800)
      }}
      onKeyDown={(event) => {
        if (event.key === 'Enter') commit(text, true)
      }}
      onBlur={() => commit(text, true)}
    />
  )
}

/** 個別の項目に出ない残り（`FxInspector` の「残りは JSON」と同じ）。 */
function RestJson({
  lyric,
  disabled,
  onChange,
}: {
  lyric: Lyric
  disabled: boolean
  onChange: (next: Lyric) => void
}) {
  const rest = lyricRest(lyric)
  const json = JSON.stringify(rest, null, 2)
  const [text, setText] = useState(json)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setText(json)
    setError(null)
  }, [json])

  const apply = () => {
    let parsed: unknown
    try {
      parsed = JSON.parse(text || '{}')
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
      return
    }
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      setError('JSON のオブジェクト（{…}）で書いてください')
      return
    }
    setError(null)
    if (JSON.stringify(parsed) === json) return
    onChange(withLyricRest(lyric, parsed as Record<string, unknown>))
  }

  return (
    <>
      <label className="flex flex-col gap-1 border-t border-border pt-2 text-[11px]">
        <span className="text-muted-foreground">
          残りの項目（JSON。離すと保存）
        </span>
        <textarea
          className="h-28 rounded-md border border-border bg-background px-2 py-1 font-mono text-[11px]"
          aria-label="残りの項目（JSON）"
          value={text}
          disabled={disabled}
          spellCheck={false}
          onChange={(event) => setText(event.target.value)}
          onBlur={apply}
        />
      </label>
      {error && (
        <p className="rounded border border-red-900/70 bg-red-950/50 px-2 py-1 text-[11px] text-red-200">
          {error}
        </p>
      )}
    </>
  )
}

// ---------------------------------------------------------------------------
// JIZURA の登録一覧（選択肢の名前とタグの出どころ）
// ---------------------------------------------------------------------------

interface Option {
  key: string
  name: string
  /** 初版より後に足された部品（`extra: false` のときは薄く出す）。 */
  extra: boolean
  /** 属する部品セット（`typo` / `kinetic` / `horror`。OFF のときは薄く出す）。 */
  set?: string | null
}

interface Engine {
  groups: Record<string, Option[]>
  styles: Option[]
  moods: Option[]
  parseLyrics: (raw: string) => { lines: { text: string; interlude?: boolean }[] }
}

/**
 * JIZURA のエンジンを**動的に**読んで、選択肢の一覧に均す。
 *
 * バンドルは 2MB 級なので、このパネルを開くまで読み込まない（`@fx/lib/jizura`
 * は同梱 Remotion プロジェクトの入口で、`vite.config.ts` の `@fx` エイリアス
 * で解決される）。読めなければ select は空のまま——歌詞の書き換えや削除は
 * それでもできる。
 */
function useJizura(): Engine | null {
  const [engine, setEngine] = useState<Engine | null>(null)

  useEffect(() => {
    let alive = true
    void (async () => {
      try {
        const { loadJizura } = await import('@fx/lib/jizura')
        const J = loadJizura()
        if (!alive) return
        const options = (
          group: 'layout' | 'enter' | 'hold' | 'exit' | 'decor' | 'treat' | 'bg' | 'cam',
        ): Option[] =>
          J.order(group).map((key) => ({
            key,
            name: J.registry(group)[key]?.name ?? key,
            extra: J.isExtra(group, key),
            set: J.setOf?.(group, key) ?? null,
          }))
        setEngine({
          groups: {
            layout: options('layout'),
            enter: options('enter'),
            hold: options('hold'),
            exit: options('exit'),
            decor: options('decor'),
            treat: options('treat'),
            bg: options('bg'),
            cam: options('cam'),
          },
          styles: J.STYLE_ORDER.map((key) => ({
            key,
            name: J.STYLES[key]?.name ?? key,
            extra: J.isExtra('style', key),
            set: J.setOf?.('style', key) ?? null,
          })),
          // 雰囲気には印を付けない（ホラーを指名すればホラーの部品セットも ON になる）
          moods: Object.entries(J.MOODS).map(([key, mood]) => ({
            key,
            name: mood.name,
            extra: false,
          })),
          parseLyrics: (raw) => J.parseLyrics(raw),
        })
      } catch {
        // 読めなくても歌詞の書き換えと削除はできる（選択肢が出ないだけ）
      }
    })()
    return () => {
      alive = false
    }
  }, [])

  return engine
}
