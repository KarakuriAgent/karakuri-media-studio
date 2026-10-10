import { describe, expect, it } from 'vitest'

import {
  LYRIC_SETS,
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

describe('歌詞モーションの読み取り', () => {
  it('知らない形の値は既定へ落ちる（AI が書いた途中の props でも画面は止まらない）', () => {
    expect(lyricText({})).toBe('')
    expect(lyricText({ lyrics: 12 })).toBe('')
    expect(lyricSeed({})).toBe(0)
    expect(lyricSeed({ seed: 'x' })).toBe(0)
    expect(lyricFxValue({}, 'motion')).toBeUndefined()
    expect(lyricFxValue({ fx: 'x' }, 'motion')).toBeUndefined()
    expect(lyricOverride({ overrides: 'x' }, 0)).toEqual({})
    expect(lyricOverride({ overrides: { '0': 'x' } }, 0)).toEqual({})
  })

  it('シードの振り直しは 0 以上の整数', () => {
    for (let i = 0; i < 20; i += 1) {
      const seed = rollSeed()
      expect(Number.isInteger(seed)).toBe(true)
      expect(seed).toBeGreaterThanOrEqual(0)
    }
  })
})

describe('演出の強さ（fx）', () => {
  it('書いた項目だけを上書きし、undefined でその項目を消す', () => {
    const one = setLyricFx({ lyrics: 'あ' }, 'motion', 0.7)
    expect(one).toEqual({ lyrics: 'あ', fx: { motion: 0.7 } })

    const two = setLyricFx(one, 'glitch', 0.2)
    expect(two.fx).toEqual({ motion: 0.7, glitch: 0.2 })

    expect(setLyricFx(two, 'motion', undefined).fx).toEqual({ glitch: 0.2 })
  })
})

describe('行ごとの上書き', () => {
  const base = { lyrics: 'あ\nい', overrides: { '1': { layout: 'huge' } } }

  it('行番号（0 始まり）のキーへ浅くマージする', () => {
    const next = setLyricOverride(base, 1, { enter: 'slice' })
    expect(next.overrides).toEqual({ '1': { layout: 'huge', enter: 'slice' } })
    // 元は触らない
    expect(base.overrides['1']).toEqual({ layout: 'huge' })
  })

  it('undefined を送ると指名が外れ、空になった行は落ちる', () => {
    const next = setLyricOverride(base, 1, { layout: undefined })
    expect(next.overrides).toEqual({})
  })

  it('指名の無い行にも足せる', () => {
    const next = setLyricOverride(base, 0, { decor: ['seal'], single: true })
    expect(next.overrides).toEqual({
      '0': { decor: ['seal'], single: true },
      '1': { layout: 'huge' },
    })
  })
})

describe('全体の真偽値（部品セット・統一感・中央を空ける）', () => {
  it('書いていなければ既定（部品セットは本家の J.SETS と同じ）', () => {
    const defaults = Object.fromEntries(
      LYRIC_SETS.map((set) => [set.name, lyricFlag({}, set.name, set.fallback)]),
    )
    expect(defaults).toEqual({ typo: true, kinetic: true, horror: false })
    expect(lyricFlag({}, 'centerFree', false)).toBe(false)
    expect(lyricFlag({ centerFree: 'yes' }, 'centerFree', false)).toBe(false)
    expect(lyricFlag({ kinetic: false }, 'kinetic', true)).toBe(false)
  })
})

describe('行の位置ずらし（offset / scale）', () => {
  const base = { lyrics: 'あ\nい', overrides: { '1': { layout: 'huge' } } }

  it('書いていなければ 0, 0, 1。壊れた値も既定へ落ちる', () => {
    expect(lyricPlace(base, 0)).toEqual({ x: 0, y: 0, scale: 1 })
    expect(
      lyricPlace({ overrides: { '0': { offset: { x: 'a', y: 0.2 }, scale: null } } }, 0),
    ).toEqual({ x: 0, y: 0.2, scale: 1 })
  })

  it('書いた軸だけを変え、ほかの指名は残す', () => {
    const one = setLyricPlace(base, 1, { x: 0.25 })
    expect(one.overrides).toEqual({ '1': { layout: 'huge', offset: { x: 0.25, y: 0 } } })
    const two = setLyricPlace(one, 1, { y: -0.5, scale: 0.6 })
    expect(two.overrides).toEqual({
      '1': { layout: 'huge', offset: { x: 0.25, y: -0.5 }, scale: 0.6 },
    })
  })

  it('範囲（±1 / 0.3〜2）に収め、既定に戻ったものは消す', () => {
    const wide = setLyricPlace(base, 0, { x: 3, y: -9, scale: 10 })
    expect(wide.overrides).toMatchObject({ '0': { offset: { x: 1, y: -1 }, scale: 2 } })
    const back = setLyricPlace(wide, 0, { x: 0, y: 0, scale: 1 })
    expect(back.overrides).toEqual({ '1': { layout: 'huge' } })
  })

  it('null（リセット）で位置と拡大率を両方消す', () => {
    const placed = setLyricPlace(base, 1, { x: 0.1, scale: 0.5 })
    expect(setLyricPlace(placed, 1, null).overrides).toEqual({ '1': { layout: 'huge' } })
  })

  it('カット単位の指定（cutOffsets / cutTech）は触らずに持ち回る', () => {
    const lyric = {
      overrides: { '0': { cutOffsets: { '1': { x: 0.2 } }, cutTech: { '0': { enter: 'pop' } } } },
    }
    expect(setLyricPlace(lyric, 0, { y: 0.1 }).overrides).toEqual({
      '0': {
        cutOffsets: { '1': { x: 0.2 } },
        cutTech: { '0': { enter: 'pop' } },
        offset: { x: 0, y: 0.1 },
      },
    })
  })
})

describe('残りの項目（JSON の生編集欄）', () => {
  const lyric = {
    lyrics: 'あ',
    style: 'noir',
    seed: 1,
    fx: { motion: 0.7 },
    overrides: {},
    lang: 'ja',
    colors: { bg: '#000' },
  }

  it('個別に出している項目は残りに出さない', () => {
    expect(lyricRest(lyric)).toEqual({ lang: 'ja', colors: { bg: '#000' } })
    expect(
      lyricRest({
        ...lyric,
        typo: false,
        kinetic: true,
        horror: true,
        unify: true,
        typeset: false,
        centerFree: true,
        centerDir: 'lr',
      }),
    ).toEqual({ lang: 'ja', colors: { bg: '#000' } })
  })

  it('書き戻しても個別の項目は保たれ、消した残りは落ちる', () => {
    const next = withLyricRest(lyric, { lang: 'en' })
    expect(next).toEqual({
      lyrics: 'あ',
      style: 'noir',
      seed: 1,
      fx: { motion: 0.7 },
      overrides: {},
      lang: 'en',
    })
  })
})
