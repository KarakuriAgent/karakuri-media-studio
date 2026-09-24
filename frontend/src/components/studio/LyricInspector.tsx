import { useEffect, useMemo, useRef, useState } from 'react'
import { Dices, Trash2 } from 'lucide-react'

import { Section } from '../ui'
import { Button } from '../ui/button'
import { Checkbox } from '../ui/checkbox'
import {
  LYRIC_FX_SLIDERS,
  LYRIC_KOMA_OPTIONS,
  LYRIC_OVERRIDE_GROUPS,
  type Lyric,
  lyricFxValue,
  lyricOverride,
  lyricRest,
  lyricSeed,
  lyricText,
  rollSeed,
  setLyricFx,
  setLyricOverride,
  withLyricRest,
} from './lyric'

/**
 * 歌詞モーション（FX トラックに相乗りする JIZURA の層）のプロパティパネル。
 *
 * `FxInspector` と同じ流儀で、出すのは 3 段:
 *
 * - **全体** … 出す・出さない / スタイル / 雰囲気 / シード / 追加分・和風 /
 *   演出の強さ（スライダー）/ 歌詞テキスト（LRC 記法のまま）
 * - **行ごとの上書き** … JIZURA の対応範囲と同じく**行単位のみ**。レイアウト・
 *   入り・保持・抜け・文字の処理・背景・カメラの指名（既定は「自動」）と装飾の
 *   複数選択、1 カットに固定、その行だけのシード
 * - **残り** … JSON のテキスト欄
 *
 * 選択肢の名前は**実行時に JIZURA の登録一覧から取る**（`J.order(group)` と
 * `J.registry(group)[key].name`）。エンジンは 1MB 級なので動的 import で、この
 * パネルを開くまで読み込まない。`extra: false` のときは追加分を薄く出す
 * （JIZURA の UI と同じ流儀。選べはする——手で指名するぶんには効く）。
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
            disabled={busy || !engine}
            onChange={(value) => onChange({ ...lyric, style: value })}
          />
          <Picker
            label="雰囲気"
            value={typeof lyric.mood === 'string' ? lyric.mood : ''}
            autoLabel="なし"
            options={engine?.moods ?? []}
            extra
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
                text={line.text}
                engine={engine}
                extra={extra}
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
  lyric,
  disabled,
  onChange,
}: {
  index: number
  text: string
  engine: Engine
  extra: boolean
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
          {(engine.groups.decor ?? []).map((option) => (
            <option
              key={option.key}
              value={option.key}
              className={!extra && option.extra ? 'opacity-50' : undefined}
            >
              {option.name}
              {!extra && option.extra ? '（追加分）' : ''}
            </option>
          ))}
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
    </div>
  )
}

// ---------------------------------------------------------------------------
// 部品
// ---------------------------------------------------------------------------

/** 選択肢 1 つ（`extra: false` のときは追加分を薄く出す）。 */
function Picker({
  label,
  value,
  autoLabel,
  options,
  extra,
  disabled,
  onChange,
}: {
  label: string
  value: string
  /** 何も指名していないときの選択肢の名前（「自動」「任せる」「なし」）。 */
  autoLabel: string
  options: Option[]
  extra: boolean
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
        {options.map((option) => (
          <option
            key={option.key}
            value={option.key}
            className={!extra && option.extra ? 'opacity-50' : undefined}
          >
            {option.name}
            {!extra && option.extra ? '（追加分）' : ''}
          </option>
        ))}
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
}

interface Engine {
  groups: Record<string, Option[]>
  styles: Option[]
  moods: Option[]
  parseLyrics: (raw: string) => { lines: { text: string }[] }
}

/**
 * JIZURA のエンジンを**動的に**読んで、選択肢の一覧に均す。
 *
 * バンドルは 1MB 級なので、このパネルを開くまで読み込まない（`@fx/lib/jizura`
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
          })),
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
