/**
 * FX トラック（タイムラインに載せる演出）の見せ方と書き換えの純関数（SPEC §7.3）。
 *
 * 演出そのものの正本は Remotion 側（`remotion/src/schema.ts` の zod）で、ここは
 * **タイムラインに帯として並べるため**の薄い読み取りだけを持つ:
 *
 * - 帯の位置と長さ（`t` 〜 `until` / `duration` / 型ごとの既定尺）
 * - 型ごとの色と見出し
 * - プロパティパネルが編集する「主要項目」と、それ以外を JSON で見せる分け方
 *
 * イベントの中身（`Record<string, unknown>`）はそのまま持ち回り、知らない型・
 * 知らない項目も落とさない（AI が書いた新しい演出をこちらが知らなくても、
 * 人が秒を直したり消したりはできる）。
 */

import type { TimelineFxEvent } from '../../types'

/**
 * `until` も `duration` も無いときに帯へ充てる尺（秒）。
 *
 * 本当の既定尺は Remotion 側（`lib/fx.ts` の `defaultEventSeconds`）が
 * イベントの中身から決めるので、ここは**帯の幅の見当**にすぎない。
 */
export const FX_DEFAULT_SECONDS: Record<string, number> = {
  card: 1.5,
  invertShake: 0.6,
  imageSlam: 1.5,
  terminalText: 2.0,
  screen: 1.0,
  glitchCut: 0.3,
  collapse: 1.2,
  crtOff: 0.5,
  sprite: 2.0,
  stickerStack: 2.0,
  credits: 3.0,
  lyric: 2.0,
  endCard: 3.0,
  beatMarker: 1.0,
  shape: 1.0,
}

/** 型ごとの帯の色（クリップの帯と喧嘩しない範囲で見分けが付くように）。 */
export const FX_TONE: Record<string, string> = {
  lyric: 'border-fuchsia-800 bg-fuchsia-950/70 text-fuchsia-100',
  credits: 'border-violet-800 bg-violet-950/70 text-violet-100',
  terminalText: 'border-lime-800 bg-lime-950/70 text-lime-100',
  card: 'border-amber-800 bg-amber-950/70 text-amber-100',
  endCard: 'border-amber-700 bg-amber-900/70 text-amber-100',
  sprite: 'border-cyan-800 bg-cyan-950/70 text-cyan-100',
  stickerStack: 'border-teal-800 bg-teal-950/70 text-teal-100',
  imageSlam: 'border-orange-800 bg-orange-950/70 text-orange-100',
  shape: 'border-indigo-800 bg-indigo-950/70 text-indigo-100',
  screen: 'border-slate-700 bg-slate-900/80 text-slate-100',
  glitchCut: 'border-rose-800 bg-rose-950/70 text-rose-100',
  invertShake: 'border-rose-900 bg-rose-950/60 text-rose-100',
  collapse: 'border-stone-700 bg-stone-900/70 text-stone-100',
  crtOff: 'border-zinc-700 bg-zinc-900/70 text-zinc-100',
  beatMarker: 'border-emerald-800 bg-emerald-950/70 text-emerald-100',
}

/** 知らない型はここへ落ちる。 */
export const FX_TONE_FALLBACK = 'border-border bg-secondary text-foreground/90'

const num = (value: unknown): number | null =>
  typeof value === 'number' && Number.isFinite(value) ? value : null

const str = (value: unknown): string | null =>
  typeof value === 'string' ? value : null

/** イベントの型（`type`）。書かれていなければ空文字。 */
export function fxType(item: TimelineFxEvent): string {
  return str(item.event.type) ?? ''
}

/** 帯の左端（ミリ秒）。 */
export function fxStartMs(item: TimelineFxEvent): number {
  return Math.max(0, Math.round((num(item.event.t) ?? 0) * 1000))
}

/** 帯の尺（ミリ秒）。`until` -> `duration` -> 型ごとの既定尺の順で決まる。 */
export function fxDurationMs(item: TimelineFxEvent): number {
  const t = num(item.event.t) ?? 0
  const until = num(item.event.until)
  if (until !== null) return Math.max(0, Math.round((until - t) * 1000))
  const duration = num(item.event.duration)
  if (duration !== null) return Math.max(0, Math.round(duration * 1000))
  return Math.round((FX_DEFAULT_SECONDS[fxType(item)] ?? 1.0) * 1000)
}

/** 帯の右端（ミリ秒）。 */
export function fxEndMs(item: TimelineFxEvent): number {
  return fxStartMs(item) + fxDurationMs(item)
}

/** 演出の終わり（ミリ秒）。プレビューの尺を決めるのに使う。 */
export function fxSpanMs(events: TimelineFxEvent[]): number {
  return events.reduce((max, item) => Math.max(max, fxEndMs(item)), 0)
}

/** 帯とプロパティパネルの見出し（型 + 中身から拾える短い文字列）。 */
export function fxLabel(item: TimelineFxEvent): string {
  const kind = fxType(item) || 'イベント'
  const text =
    str(item.event.text) ??
    firstLineText(item.event.lines) ??
    basename(str(item.event.src)) ??
    ''
  return text ? `${kind}: ${text}` : kind
}

function firstLineText(lines: unknown): string | null {
  if (!Array.isArray(lines) || lines.length === 0) return null
  const first = lines[0]
  if (typeof first === 'string') return first
  if (first && typeof first === 'object') {
    return str((first as Record<string, unknown>).text)
  }
  return null
}

function basename(src: string | null): string | null {
  if (!src) return null
  const tail = src.split('?')[0].split('/').pop() ?? ''
  return tail || src
}

/** プロパティパネルが 1 つずつ出す項目。 */
export interface FxField {
  name: string
  label: string
  kind: 'number' | 'text'
}

/** すべての型に共通の項目（`enabled` はチェックボックスで別に出す）。 */
export const FX_COMMON_FIELDS: FxField[] = [
  { name: 't', label: '開始（秒）', kind: 'number' },
  { name: 'until', label: '終わり（秒）', kind: 'number' },
  { name: 'duration', label: '尺（秒）', kind: 'number' },
  { name: 'z', label: '重なり（z）', kind: 'number' },
]

/**
 * 型ごとの主要項目。ここに出さないものは JSON のテキスト欄で触る。
 *
 * 「人がプレビューを見ながら直したくなるもの」だけを選ぶ（文言・位置・大きさ・
 * 素材・色）。`lines` のような配列は JSON のまま出す。
 */
export const FX_MAIN_FIELDS: FxField[] = [
  { name: 'text', label: '文言', kind: 'text' },
  { name: 'lines', label: '行（JSON 配列）', kind: 'text' },
  { name: 'src', label: '素材（src）', kind: 'text' },
  { name: 'cx', label: '中心 X（0〜1）', kind: 'number' },
  { name: 'cy', label: '中心 Y（0〜1）', kind: 'number' },
  { name: 'w', label: '幅（画面比）', kind: 'number' },
  { name: 'color', label: '色', kind: 'text' },
]

/** そのイベントで実際に出す主要項目（値を持っているものだけ）。 */
export function fxMainFields(item: TimelineFxEvent): FxField[] {
  return FX_MAIN_FIELDS.filter((field) => field.name in item.event)
}

/** 主要項目にも共通項目にも出ない残り（JSON のテキスト欄で触る）。 */
export function fxRest(item: TimelineFxEvent): Record<string, unknown> {
  const shown = new Set([
    'type',
    ...FX_COMMON_FIELDS.map((field) => field.name),
    ...fxMainFields(item).map((field) => field.name),
  ])
  return Object.fromEntries(
    Object.entries(item.event).filter(([name]) => !shown.has(name)),
  )
}

/**
 * 「今の event」を「こうしたい event」に変える PATCH body（浅いマージ）。
 *
 * 消えた項目は `null` にして送る（サーバー側はそれをその項目の削除として読む）。
 */
export function fxPatch(
  current: Record<string, unknown>,
  next: Record<string, unknown>,
): Record<string, unknown> {
  const patch: Record<string, unknown> = {}
  for (const [name, value] of Object.entries(next)) {
    if (JSON.stringify(current[name]) !== JSON.stringify(value)) {
      patch[name] = value
    }
  }
  for (const name of Object.keys(current)) {
    if (!(name in next)) patch[name] = null
  }
  return patch
}

/** 帯を `startMs` へ動かす（`until` があれば尺を保ったまま一緒に動かす）。 */
export function fxMovedTo(
  item: TimelineFxEvent,
  startMs: number,
): Record<string, unknown> {
  const t = Math.max(0, startMs) / 1000
  const patch: Record<string, unknown> = { t: round3(t) }
  const until = num(item.event.until)
  if (until !== null) {
    patch.until = round3(t + (until - (num(item.event.t) ?? 0)))
  }
  return patch
}

/** 帯の右端を `endMs` にする（`until` を書く。最短 0.1 秒）。 */
export function fxResizedTo(
  item: TimelineFxEvent,
  endMs: number,
): Record<string, unknown> {
  const t = num(item.event.t) ?? 0
  return { until: round3(Math.max(t + 0.1, endMs / 1000)) }
}

/** 秒はミリ秒までで足りる（Remotion 側もフレームへ丸める）。 */
export function round3(value: number): number {
  return Math.round(value * 1000) / 1000
}

/** 手元のイベント 1 件を書き換えた配列（サーバーの応答を待たずに見せる）。 */
export function fxApplyLocal(
  events: TimelineFxEvent[],
  id: string,
  patch: Record<string, unknown>,
  enabled?: boolean,
): TimelineFxEvent[] {
  return events.map((item) => {
    if (item.id !== id) return item
    const event = { ...item.event }
    for (const [name, value] of Object.entries(patch)) {
      if (value === null) delete event[name]
      else event[name] = value
    }
    return { ...item, event, enabled: enabled ?? item.enabled }
  })
}
