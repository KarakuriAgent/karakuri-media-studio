import { describe, expect, it } from 'vitest'

import {
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
