/**
 * 歌詞モーション（FX トラックに相乗りする JIZURA の層）の読み書きの純関数。
 *
 * 中身の正本は Remotion 側の zod（`remotion/src/schema.ts` の
 * `lyricOverlaySchema`）で、ここは**画面が触る項目を出し入れする**だけ:
 *
 * - 全体（スタイル・雰囲気・テーマ・シード・部品セット・統一感・文字整列・中央を空ける・
 *   スライダー・飾りの数字 / 時刻を隠す・歌詞テキスト）
 * - 行ごとの上書き（`overrides["0"]` … 部品の指名と位置ずらし `offset` / `scale`）
 *
 * カット単位の指名（`cutTech` / `cutLayouts` / `cutOffsets`）は画面には出さないが、
 * 行の上書きを浅くマージするので、AI が書いたものは消さずに持ち回る。
 *
 * 知らない項目はそのまま持ち回る（AI が書いた新しい指定をこちらが知らなくても、
 * 人がスタイルを変えたり行を指名したりはできる）。
 */

/** 歌詞モーション（`TimelineFx.lyric`）。中身は Remotion の zod が正。 */
export type Lyric = Record<string, unknown>

/** 行ごとの上書きで select として出すグループ（JIZURA の対応範囲）。 */
export const LYRIC_OVERRIDE_GROUPS = [
  { name: 'layout', label: 'レイアウト' },
  { name: 'enter', label: '入り' },
  { name: 'hold', label: '保持' },
  { name: 'exit', label: '抜け' },
  { name: 'treat', label: '文字の処理' },
  { name: 'bg', label: '背景' },
  { name: 'cam', label: 'カメラ' },
] as const

/** 演出の強さ（`fx`）のスライダー。 */
export const LYRIC_FX_SLIDERS = [
  { name: 'motion', label: '動き' },
  { name: 'glitch', label: 'グリッチ' },
  { name: 'chroma', label: '色ズレ' },
  { name: 'decor', label: '装飾' },
  { name: 'density', label: '密度' },
  { name: 'texture', label: '質感' },
] as const

/** 作画枚数（`fx.koma`。24fps 基準で、0 は毎フレーム）。 */
export const LYRIC_KOMA_OPTIONS = [
  { value: 12, label: '2 コマ打ち（12）' },
  { value: 8, label: '3 コマ打ち（8）' },
  { value: 24, label: '1 コマ打ち（24）' },
  { value: 0, label: '毎フレーム（0）' },
] as const

/**
 * 部品セット（本家 v0.9 の `typo` / `kinetic` / `horror`）。`fallback` は書いて
 * いないときの値で、本家の `J.SETS` の既定と同じ（文字PV系・キネティックは ON）。
 */
export const LYRIC_SETS = [
  { name: 'typo', label: '文字PV系', fallback: true },
  { name: 'kinetic', label: 'キネティック', fallback: true },
  { name: 'horror', label: 'ホラー', fallback: false },
] as const

/** 全体の ON / OFF（既定はどちらも OFF）。 */
export const LYRIC_SWITCHES = [
  {
    name: 'unify',
    label: '統一感',
    title: 'パートごとに部品を揃え、繰り返す行を同じ絵で出し、カットの間をモーフでつなぐ',
  },
  {
    name: 'typeset',
    label: '文字整列',
    title: 'カナの詰め・助詞を小さく・0.2 秒先出し・装飾と演出を控えめに',
  },
] as const

/**
 * 飾りの数字・時刻を隠す（本家 v0.10 の `fx.hideNo` / `fx.hideTime`。既定はどちらも
 * OFF）。そのカットの歌詞に含まれる文字は歌詞なので消えない。
 */
export const LYRIC_DECO_HIDES = [
  {
    name: 'hideNo',
    label: '数字を隠す',
    title: '飾りの数字（No.01 / #03 / 128 など）を出さない。歌詞の中の数字は消さない',
  },
  {
    name: 'hideTime',
    label: '時刻を隠す',
    title: '飾りの時刻（00:12.34 / REC 1:05 など）を出さない。歌詞の中の時刻は消さない',
  },
] as const

/** 中央を空けるときの縦長画面での向き（`centerDir`）。 */
export const LYRIC_CENTER_DIRS = [
  { value: 'tb', label: '上下' },
  { value: 'lr', label: '左右' },
] as const

/** 行の位置ずらし（x / y は画面比、scale は拡大率）。 */
export interface LyricPlace {
  x: number
  y: number
  scale: number
}

/** 位置ずらしの範囲（Remotion の zod と同じ）。 */
export const LYRIC_OFFSET_RANGE = { min: -1, max: 1 } as const
export const LYRIC_SCALE_RANGE = { min: 0.3, max: 2 } as const

/**
 * 位置のプリセット。書いた軸だけを変える（左寄せ + 上 で左上になる）。
 * `place: null` は位置と拡大率を両方消す（リセット）。
 */
export const LYRIC_PLACE_PRESETS: ReadonlyArray<{
  name: string
  label: string
  place: Partial<LyricPlace> | null
}> = [
  { name: 'left', label: '左寄せ', place: { x: -0.25 } },
  { name: 'right', label: '右寄せ', place: { x: 0.25 } },
  { name: 'top', label: '上', place: { y: -0.25 } },
  { name: 'bottom', label: '下', place: { y: 0.25 } },
  { name: 'reset', label: 'リセット', place: null },
]

const isObject = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === 'object' && !Array.isArray(value)

/** `lyric.fx` の 1 項目（無ければ `undefined`）。 */
export function lyricFxValue(lyric: Lyric, name: string): unknown {
  const fx = lyric.fx
  return isObject(fx) ? fx[name] : undefined
}

/** `lyric.fx` の 1 項目を書き換えた新しい `lyric`（`undefined` でその項目を消す）。 */
export function setLyricFx(lyric: Lyric, name: string, value: unknown): Lyric {
  const fx: Record<string, unknown> = isObject(lyric.fx) ? { ...lyric.fx } : {}
  if (value === undefined) delete fx[name]
  else fx[name] = value
  return { ...lyric, fx }
}

/** `overrides` 全体（無ければ空）。 */
export function lyricOverrides(lyric: Lyric): Record<string, Record<string, unknown>> {
  const overrides = lyric.overrides
  if (!isObject(overrides)) return {}
  const out: Record<string, Record<string, unknown>> = {}
  for (const [key, value] of Object.entries(overrides)) {
    if (isObject(value)) out[key] = value
  }
  return out
}

/** `index` 行目（0 始まり）の上書き（無ければ空）。 */
export function lyricOverride(lyric: Lyric, index: number): Record<string, unknown> {
  return lyricOverrides(lyric)[String(index)] ?? {}
}

/**
 * `index` 行目の上書きを**浅くマージ**した新しい `lyric`。
 *
 * 値に `undefined` を渡すとその指名を外し（＝「自動」に戻す）、指名が 1 つも
 * 無くなった行は `overrides` から丸ごと落とす（空の `{}` を残さない）。
 */
export function setLyricOverride(
  lyric: Lyric,
  index: number,
  patch: Record<string, unknown>,
): Lyric {
  const overrides = lyricOverrides(lyric)
  const key = String(index)
  const merged: Record<string, unknown> = { ...(overrides[key] ?? {}) }
  for (const [name, value] of Object.entries(patch)) {
    if (value === undefined) delete merged[name]
    else merged[name] = value
  }
  const next = { ...overrides }
  if (Object.keys(merged).length === 0) delete next[key]
  else next[key] = merged
  return { ...lyric, overrides: next }
}

/** 全体の真偽値（書いていなければ `fallback`）。 */
export function lyricFlag(lyric: Lyric, name: string, fallback: boolean): boolean {
  const value = lyric[name]
  return typeof value === 'boolean' ? value : fallback
}

const finiteOr = (value: unknown, fallback: number): number =>
  typeof value === 'number' && Number.isFinite(value) ? value : fallback

const clamp = (value: number, min: number, max: number): number =>
  Math.min(max, Math.max(min, value))

/** `index` 行目の位置ずらし（書いていなければ 0, 0, 1）。 */
export function lyricPlace(lyric: Lyric, index: number): LyricPlace {
  const override = lyricOverride(lyric, index)
  const offset = isObject(override.offset) ? override.offset : {}
  return {
    x: finiteOr(offset.x, 0),
    y: finiteOr(offset.y, 0),
    scale: finiteOr(override.scale, 1),
  }
}

/**
 * `index` 行目の位置ずらしを書き換えた新しい `lyric`。
 *
 * `patch` に書いた軸だけを変え、範囲（x / y は ±1、scale は 0.3〜2）に収める。
 * 位置が 0, 0 に戻れば `offset` を、拡大率が 1 に戻れば `scale` を消す。
 * `patch` が null なら両方消す（リセット）。ほかの指名はそのまま。
 */
export function setLyricPlace(
  lyric: Lyric,
  index: number,
  patch: Partial<LyricPlace> | null,
): Lyric {
  if (patch === null) {
    return setLyricOverride(lyric, index, { offset: undefined, scale: undefined })
  }
  const current = lyricPlace(lyric, index)
  const round = (value: number) => Math.round(value * 1000) / 1000
  const x = round(clamp(finiteOr(patch.x, current.x), LYRIC_OFFSET_RANGE.min, LYRIC_OFFSET_RANGE.max))
  const y = round(clamp(finiteOr(patch.y, current.y), LYRIC_OFFSET_RANGE.min, LYRIC_OFFSET_RANGE.max))
  const scale = round(
    clamp(finiteOr(patch.scale, current.scale), LYRIC_SCALE_RANGE.min, LYRIC_SCALE_RANGE.max),
  )
  return setLyricOverride(lyric, index, {
    offset: x === 0 && y === 0 ? undefined : { x, y },
    scale: scale === 1 ? undefined : scale,
  })
}

/** シードの振り直し（0 〜 2^31-1 の整数）。 */
export function rollSeed(): number {
  return Math.floor(Math.random() * 0x7fffffff)
}

/** `lyric.seed`（無ければ 0）。 */
export function lyricSeed(lyric: Lyric): number {
  return typeof lyric.seed === 'number' && Number.isFinite(lyric.seed)
    ? lyric.seed
    : 0
}

/** `lyric.theme`（おまかせのテーマ。無ければ空文字 = テーマなし）。 */
export function lyricTheme(lyric: Lyric): string {
  return typeof lyric.theme === 'string' ? lyric.theme : ''
}

/** `lyric.lyrics`（無ければ空文字）。 */
export function lyricText(lyric: Lyric): string {
  return typeof lyric.lyrics === 'string' ? lyric.lyrics : ''
}

/**
 * 画面が個別に出す項目（残りは JSON の生編集欄へ落とす）。
 *
 * `FxInspector` の「残りは JSON」と同じ流儀。行ごとの上書き（`overrides`）は
 * 専用の一覧で触るので、ここでも「出している」扱いにする。
 */
export const LYRIC_SHOWN_KEYS = [
  'lyrics',
  'style',
  'mood',
  'theme',
  'seed',
  'extra',
  'wa',
  'typo',
  'kinetic',
  'horror',
  'unify',
  'typeset',
  'centerFree',
  'centerDir',
  'fx',
  'overrides',
]

/** 個別の項目に出ない残り（JSON のテキスト欄で触る）。 */
export function lyricRest(lyric: Lyric): Record<string, unknown> {
  return Object.fromEntries(
    Object.entries(lyric).filter(([name]) => !LYRIC_SHOWN_KEYS.includes(name)),
  )
}

/** JSON の生編集欄の中身を `lyric` へ戻す（出している項目は触らない）。 */
export function withLyricRest(lyric: Lyric, rest: Record<string, unknown>): Lyric {
  const kept = Object.fromEntries(
    Object.entries(lyric).filter(([name]) => LYRIC_SHOWN_KEYS.includes(name)),
  )
  return { ...kept, ...rest }
}
