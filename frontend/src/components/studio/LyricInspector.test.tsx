import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import LyricInspector from './LyricInspector'

// JIZURA のエンジン（1MB 級）は読まず、選択肢だけを差し替える。
// 本物を読むかどうかは `@fx/lib/jizura` の動的 import 1 か所に閉じているので、
// ここを差し替えればパネルの中身はそのまま試せる。
vi.mock('@fx/lib/jizura', () => {
  const groups: Record<string, Record<string, { name: string; extra?: boolean }>> = {
    layout: { center: { name: '中央' }, ema: { name: '絵馬', extra: true } },
    enter: { slice: { name: 'スライス' } },
    hold: { still: { name: '静止' } },
    exit: { blur: { name: 'ブラー' } },
    decor: { seal: { name: '落款' }, dots: { name: 'ドット' } },
    treat: { none: { name: 'なし' } },
    bg: { none: { name: '無地' } },
    cam: { push: { name: 'ゆっくり寄る' } },
  }
  return {
    loadJizura: () => ({
      order: (group: string) => Object.keys(groups[group] ?? {}),
      registry: (group: string) => groups[group] ?? {},
      isExtra: (group: string, key: string) =>
        group === 'style' ? false : Boolean(groups[group]?.[key]?.extra),
      STYLE_ORDER: ['noir', 'paper'],
      STYLES: { noir: { name: 'ノワール' }, paper: { name: '紙' } },
      MOODS: { calm: { name: 'しっとり' } },
      parseLyrics: (raw: string) => ({
        lines: String(raw)
          .split('\n')
          .filter((line) => line.trim())
          .map((line) => ({ text: line.trim() })),
      }),
    }),
  }
})

afterEach(cleanup)

const LYRIC = {
  lyrics: '夜明けの色を\nほどけた声が',
  style: 'noir',
  seed: 7,
  extra: false,
  fx: { motion: 0.7 },
  overrides: {},
}

function show(overrides: Record<string, unknown> = {}) {
  const onChange = vi.fn()
  const onEnabled = vi.fn()
  const onDelete = vi.fn()
  render(
    <LyricInspector
      lyric={{ ...LYRIC, ...overrides }}
      enabled
      busy={false}
      onChange={onChange}
      onEnabled={onEnabled}
      onDelete={onDelete}
    />,
  )
  return { onChange, onEnabled, onDelete }
}

describe('LyricInspector', () => {
  it('歌詞の行を並べ、行ごとの指名を PUT へ渡す（select は即時）', async () => {
    const { onChange } = show()
    await screen.findByText('1. 夜明けの色を')
    expect(screen.getByText('2. ほどけた声が')).toBeTruthy()

    const layouts = await waitFor(() => {
      const found = screen.getAllByLabelText('レイアウト')
      expect(found.length).toBe(2)
      return found
    })
    fireEvent.change(layouts[1], { target: { value: 'center' } })
    expect(onChange).toHaveBeenCalledTimes(1)
    expect(onChange.mock.calls[0][0].overrides).toEqual({ '1': { layout: 'center' } })
  })

  it('extra: false のときは追加分の部品に印を付ける', async () => {
    show()
    const layout = (await screen.findAllByLabelText('レイアウト'))[0]
    const labels = Array.from(layout.querySelectorAll('option')).map(
      (option) => option.textContent,
    )
    expect(labels).toContain('絵馬（追加分）')
    expect(labels).toContain('中央')
  })

  it('extra: true なら追加分の印は出ない', async () => {
    show({ extra: true })
    const layout = (await screen.findAllByLabelText('レイアウト'))[0]
    const labels = Array.from(layout.querySelectorAll('option')).map(
      (option) => option.textContent,
    )
    expect(labels).toContain('絵馬')
  })

  it('スタイル・雰囲気・シードの振り直しはそのまま lyric に載る', async () => {
    const { onChange } = show()
    fireEvent.change(await screen.findByLabelText('スタイル'), {
      target: { value: 'paper' },
    })
    expect(onChange.mock.calls[0][0].style).toBe('paper')

    fireEvent.change(screen.getByLabelText('雰囲気'), { target: { value: 'calm' } })
    expect(onChange.mock.calls[1][0].mood).toBe('calm')

    fireEvent.click(screen.getByTitle('シードを振り直す（絵の選び方が変わる）'))
    expect(onChange.mock.calls[2][0].seed).not.toBe(7)
  })

  it('演出の強さのスライダーは fx の中だけを書き換える', async () => {
    const { onChange } = show()
    fireEvent.change(await screen.findByLabelText('グリッチ'), {
      target: { value: '0.4' },
    })
    expect(onChange.mock.calls[0][0].fx).toEqual({ motion: 0.7, glitch: 0.4 })
  })

  it('歌詞のテキストはデバウンスしてから保存する', async () => {
    vi.useFakeTimers()
    try {
      const { onChange } = show()
      const area = screen.getByLabelText('歌詞（LRC 記法のまま）')
      fireEvent.change(area, { target: { value: 'あたらしい歌詞' } })
      expect(onChange).not.toHaveBeenCalled()
      vi.advanceTimersByTime(1600)
      expect(onChange).toHaveBeenCalledTimes(1)
      expect(onChange.mock.calls[0][0].lyrics).toBe('あたらしい歌詞')
    } finally {
      vi.useRealTimers()
    }
  })

  it('出す・出さないと削除はそのまま親へ渡す', async () => {
    const { onEnabled, onDelete } = show()
    fireEvent.click(await screen.findByText('プレビューと書き出しに出す'))
    expect(onEnabled).toHaveBeenCalledWith(false)
    fireEvent.click(screen.getByTitle('歌詞モーションを消す'))
    expect(onDelete).toHaveBeenCalled()
  })
})
