import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { CanvasMessage, CanvasNode, CanvasProjectDetail } from '../../types'
import CanvasChat, { CHAT_PLACEHOLDER, THINKING_TEXT } from './CanvasChat'
import { defaultDataFor } from './logic'

afterEach(cleanup)

function card(id: string, title: string): CanvasNode {
  return {
    id,
    project_id: 'p1',
    created_at: '2026-08-04T00:00:00+00:00',
    updated_at: '2026-08-04T00:00:00+00:00',
    kind: 'character',
    title,
    data: defaultDataFor('character') as unknown as Record<string, unknown>,
    x: 0,
    y: 0,
    w: 320,
    h: 220,
    z: 1,
  }
}

function message(overrides: Partial<CanvasMessage> = {}): CanvasMessage {
  return {
    id: 'm1',
    project_id: 'p1',
    ts: '2026-08-04T00:00:00+00:00',
    role: 'user',
    content: 'こんにちは',
    kind: null,
    data: {},
    ...overrides,
  }
}

function project(overrides: Partial<CanvasProjectDetail> = {}): CanvasProjectDetail {
  return {
    id: 'p1',
    created_at: '2026-08-04T00:00:00+00:00',
    updated_at: '2026-08-04T00:00:00+00:00',
    title: 'PV 企画',
    llm: 'grok',
    viewport: { x: 0, y: 0, zoom: 1 },
    nodes: [],
    messages: [],
    thinking: false,
    ...overrides,
  }
}

function show(
  overrides: Partial<CanvasProjectDetail> = {},
  props: { busy?: boolean } = {},
) {
  const onSend = vi.fn<(content: string) => void>()
  const onStop = vi.fn()
  const view = render(
    <CanvasChat
      project={project(overrides)}
      busy={props.busy ?? false}
      onSend={onSend}
      onStop={onStop}
    />,
  )
  return { onSend, onStop, view }
}

describe('CanvasChat', () => {
  it('プレースホルダは「アイデアを説明し、@ で素材を参照」', () => {
    show()
    expect(screen.getByPlaceholderText(CHAT_PLACEHOLDER)).not.toBeNull()
  })

  it('入力して送信すると本文が渡り、入力欄が空になる', () => {
    const { onSend } = show()
    const input = screen.getByPlaceholderText(CHAT_PLACEHOLDER) as HTMLTextAreaElement
    fireEvent.change(input, { target: { value: '  屋上のカットを作って  ' } })
    fireEvent.click(screen.getByText('送信'))
    expect(onSend).toHaveBeenCalledWith('屋上のカットを作って')
    expect(input.value).toBe('')
  })

  it('Ctrl + Enter でも送信できる', () => {
    const { onSend } = show()
    const input = screen.getByPlaceholderText(CHAT_PLACEHOLDER)
    fireEvent.change(input, { target: { value: 'よろしく' } })
    fireEvent.keyDown(input, { key: 'Enter', ctrlKey: true })
    expect(onSend).toHaveBeenCalledWith('よろしく')
  })

  it('空の入力では送信できない', () => {
    const { onSend } = show()
    const button = screen.getByText('送信') as HTMLButtonElement
    expect(button.disabled).toBe(true)
    fireEvent.click(button)
    expect(onSend).not.toHaveBeenCalled()
  })

  it('会話は user / assistant を吹き出し、event を行として出す', () => {
    show({
      messages: [
        message({ id: 'm1', role: 'user', content: 'ヒロインを教えて' }),
        message({
          id: 'm2',
          role: 'event',
          kind: 'read_node',
          content: 'カードを読みました',
        }),
        message({ id: 'm3', role: 'assistant', content: '銀髪のヒロインですね' }),
      ],
    })
    expect(screen.getByText('ヒロインを教えて')).not.toBeNull()
    expect(screen.getByText('カードを読みました')).not.toBeNull()
    expect(screen.getByText('銀髪のヒロインですね')).not.toBeNull()
  })

  it('ユーザー発言は展開後でなく元発言を出す', () => {
    show({
      messages: [
        message({
          role: 'user',
          content: '@ヒロイン の案\n\n[Referenced cards — full contents]\n{...}',
          data: { text: '@ヒロイン の案', mentions: ['n1'] },
        }),
      ],
    })
    expect(screen.getByText('@ヒロイン の案')).not.toBeNull()
    expect(screen.queryByText(/Referenced cards/)).toBeNull()
  })

  it('busy 中は入力と送信を塞ぎ、thinking を出し、停止を押せる', () => {
    const { onStop } = show({}, { busy: true })
    const input = screen.getByPlaceholderText(CHAT_PLACEHOLDER) as HTMLTextAreaElement
    expect(input.disabled).toBe(true)
    expect((screen.getByText('送信') as HTMLButtonElement).disabled).toBe(true)
    expect(screen.getByText(THINKING_TEXT)).not.toBeNull()

    const stop = screen.getByText('⏹ 停止') as HTMLButtonElement
    expect(stop.disabled).toBe(false)
    fireEvent.click(stop)
    expect(onStop).toHaveBeenCalled()
  })

  it('走っていなければ thinking も停止も出さない', () => {
    show()
    expect(screen.queryByText(THINKING_TEXT)).toBeNull()
    expect((screen.getByText('⏹ 停止') as HTMLButtonElement).disabled).toBe(true)
  })

  it('@ を打つとカード候補が出て、選ぶと入力に入る', () => {
    show({ nodes: [card('n1', 'ヒロイン'), card('n2', '夜の屋上')] })
    const input = screen.getByPlaceholderText(CHAT_PLACEHOLDER) as HTMLTextAreaElement
    fireEvent.change(input, { target: { value: '@ヒロ' } })

    const option = screen.getByRole('option', { name: /ヒロイン/ })
    expect(screen.queryByRole('option', { name: /夜の屋上/ })).toBeNull()
    fireEvent.mouseDown(option)
    expect(input.value).toBe('@ヒロイン ')
  })
})
