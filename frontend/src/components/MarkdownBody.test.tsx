import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { MarkdownBody } from './MarkdownBody'

afterEach(cleanup)

const ACTION_REPLY = '方針です。\n\n```json\n{"action":"done"}\n```\n'

describe('MarkdownBody', () => {
  it('太字を strong にする', () => {
    render(<MarkdownBody text="**太字**" />)
    expect(screen.getByText('太字').tagName).toBe('STRONG')
    expect(screen.queryByText('**太字**')).toBeNull()
  })

  it('箇条書きを list item にする', () => {
    render(<MarkdownBody text={'- 項目'} />)
    expect(screen.getByText('項目').closest('li')).not.toBeNull()
  })

  it('GFM テーブルを table にする', () => {
    render(<MarkdownBody text={'| a | b |\n| --- | --- |\n| 1 | 2 |'} />)
    const table = screen.getByRole('table')
    expect(table).toBeTruthy()
    expect(screen.getByText('a').closest('th')).not.toBeNull()
    expect(table.parentElement?.className).toContain('overflow-x-auto')
  })

  it('ルートは min-w-0 / max-w-full で幅を確定する', () => {
    const { container } = render(<MarkdownBody text="ただの文章" />)
    const root = container.firstElementChild
    expect(root?.className).toContain('min-w-0')
    expect(root?.className).toContain('max-w-full')
  })

  it('折り返せない長いパスも DOM に出す', () => {
    const path = 'frames_extracted/frame_0001.png'
    render(<MarkdownBody text={`参照: \`${path}\``} />)
    expect(screen.getByText(path)).toBeTruthy()
  })

  it('https リンクは target=_blank の a になる', () => {
    render(<MarkdownBody text={'[x](https://example.com)'} />)
    const link = screen.getByRole('link', { name: 'x' })
    expect(link.getAttribute('href')).toBe('https://example.com')
    expect(link.getAttribute('target')).toBe('_blank')
    expect(link.getAttribute('rel')).toBe('noreferrer')
  })

  it('javascript: リンクは a[href] にしない', () => {
    const { container } = render(<MarkdownBody text={'[x](javascript:alert(1))'} />)
    expect(container.querySelector('a[href^="javascript:"]')).toBeNull()
    expect(screen.getByText('x')).toBeTruthy()
  })

  it('splitAction で末尾の action JSON を折りたたむ', () => {
    const { container } = render(<MarkdownBody text={ACTION_REPLY} splitAction />)
    expect(screen.getByText('アクション').tagName).toBe('SUMMARY')
    expect(screen.getByText('{"action":"done"}')).toBeTruthy()
    expect(container.querySelector('code.language-json')).toBeNull()
    expect(screen.getByText('方針です。')).toBeTruthy()
  })

  it('splitAction は本文とフェンスの間に改行が無くても切る', () => {
    render(<MarkdownBody text={'承認後に生成します。```json\n{"action":"plan"}\n```'} splitAction />)
    expect(screen.getByText('承認後に生成します。')).toBeTruthy()
    expect(screen.getByText('アクション').tagName).toBe('SUMMARY')
    expect(screen.getByText('{"action":"plan"}')).toBeTruthy()
  })

  it('splitAction なしでは JSON を本文のコードフェンスとして残す', () => {
    const { container } = render(<MarkdownBody text={ACTION_REPLY} />)
    expect(screen.queryByText('アクション')).toBeNull()
    expect(container.querySelector('pre')).not.toBeNull()
    expect(screen.getByText('{"action":"done"}')).toBeTruthy()
  })

  it('Markdown の無い平文もそのまま出す', () => {
    render(<MarkdownBody text="ただの文章" />)
    expect(screen.getByText('ただの文章')).toBeTruthy()
  })
})
