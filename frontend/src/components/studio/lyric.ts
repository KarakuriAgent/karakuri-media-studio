/**
 * 歌詞モーション（FX トラックに相乗りする JIZURA の層）の読み書きの純関数。
 *
 * 中身の正本は Remotion 側の zod（`remotion/src/schema.ts` の
 * `lyricOverlaySchema`）で、ここは**画面が触る項目を出し入れする**だけ:
 *
 * - 全体（スタイル・雰囲気・シード・スライダー・歌詞テキスト）
 * - 行ごとの上書き（`overrides["0"]` … JIZURA の対応範囲と同じく**行単位のみ**）
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
  'seed',
  'extra',
  'wa',
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
