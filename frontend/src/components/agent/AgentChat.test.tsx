import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { AgentSession } from '../../types'
import AgentChat from './AgentChat'
import { message, session } from './fixtures'

afterEach(cleanup)

function show(overrides: Partial<AgentSession> = {}, props: { busy?: boolean; thinking?: boolean } = {}) {
  const onSend = vi.fn()
  const onCheckin = vi.fn()
  render(
    <AgentChat
      session={session(overrides)}
      progress={{}}
      busy={props.busy ?? false}
      thinking={props.thinking ?? props.busy ?? false}
      error={null}
      onDismissError={() => {}}
      onSend={onSend}
      onApprove={() => {}}
      onCheckin={onCheckin}
      onStop={() => {}}
      onOpenSessions={() => {}}
      onOpenArtifacts={() => {}}
      artifactCount={0}
      artifactBadge={false}
      onToggleNsfw={() => {}}
      showNsfw={false}
    />,
  )
  return { onSend, onCheckin }
}

const THINKING = 'Grok が考えています…'

describe('AgentChat のインジケーター', () => {
  it('このブラウザ発の呼び出し中（busy）は出る', () => {
    show({}, { busy: true })
    expect(screen.queryByText(THINKING)).not.toBeNull()
  })

  it('バックエンドのループが回すターン（thinking）でも出る', () => {
    show({ status: 'running', thinking: true }, { busy: false, thinking: true })
    expect(screen.queryByText(THINKING)).not.toBeNull()
  })

  it('何も走っていなければ出ない', () => {
    show()
    expect(screen.queryByText(THINKING)).toBeNull()
  })
})

describe('AgentChat の入力欄', () => {
  it('実行中は入力できず理由を出す（409 で無反応に見えるのを防ぐ）', () => {
    show({ status: 'running' })
    const input = screen.getByRole('textbox') as HTMLTextAreaElement
    expect(input.disabled).toBe(true)
    expect(input.placeholder).toBe('実行中は完了を待つか ⏹停止 してください')
    expect((screen.getByRole('button', { name: '送信' }) as HTMLButtonElement).disabled).toBe(
      true,
    )
  })

  it('チェックイン待ちのあいだは入力できる', () => {
    show({
      status: 'waiting_checkin',
      messages: [message('checkin', '進めますか？', { kind: 'checkin' })],
    })
    const input = screen.getByPlaceholderText(
      'チェックインに回答（Ctrl+Enter で送信）',
    ) as HTMLTextAreaElement
    expect(input.disabled).toBe(false)
  })
})

describe('AgentChat のチェックイン吹き出し', () => {
  const options = { kind: 'checkin', data: { options: ['進める', '止める'] } }

  it('応答待ちのあいだは選択肢が押せる', () => {
    show({ status: 'waiting_checkin', messages: [message('checkin', 'q', options)] })
    expect((screen.getByRole('button', { name: '進める' }) as HTMLButtonElement).disabled).toBe(
      false,
    )
    expect(screen.queryByText('応答済み')).toBeNull()
  })

  it('応答済みになると選択肢は押せず「応答済み」を出す', () => {
    show({
      status: 'running',
      messages: [
        message('checkin', 'q', { ...options, data: { ...options.data, resolved: true } }),
      ],
    })
    expect((screen.getByRole('button', { name: '進める' }) as HTMLButtonElement).disabled).toBe(
      true,
    )
    expect(screen.queryByText('応答済み')).not.toBeNull()
  })

  it('回答されずに終わったチェックインは未応答と表示する', () => {
    show({ status: 'stopped', messages: [message('checkin', 'q', options)] })
    expect(screen.queryByText('未応答のまま終了しました')).not.toBeNull()
  })

  it('ターン実行中（thinking）は応答ボタンを押せない', () => {
    show(
      { status: 'waiting_checkin', messages: [message('checkin', 'q', options)] },
      { busy: true },
    )
    expect((screen.getByRole('button', { name: '進める' }) as HTMLButtonElement).disabled).toBe(
      true,
    )
  })
})
