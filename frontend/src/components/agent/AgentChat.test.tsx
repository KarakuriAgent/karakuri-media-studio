import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from '../../api'
import type { AgentSession } from '../../types'
import AgentChat from './AgentChat'
import { message, session } from './fixtures'

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

function show(overrides: Partial<AgentSession> = {}, props: { busy?: boolean; thinking?: boolean; activity?: string | null } = {}) {
  const onSend = vi.fn<(content: string, attachments: string[]) => void>()
  const onCheckin = vi.fn()
  const onUpdateSession = vi.fn()
  render(
    <AgentChat
      session={session(overrides)}
      progress={{}}
      busy={props.busy ?? false}
      thinking={props.thinking ?? props.busy ?? false}
      activity={props.activity ?? null}
      error={null}
      onDismissError={() => {}}
      onSend={onSend}
      onApprove={() => {}}
      onCheckin={onCheckin}
      onStop={() => {}}
      onUpdateSession={onUpdateSession}
      onOpenSessions={() => {}}
      onOpenArtifacts={() => {}}
      artifactCount={0}
      artifactBadge={false}
      onToggleNsfw={() => {}}
      showNsfw={false}
    />,
  )
  return { onSend, onCheckin, onUpdateSession }
}

const THINKING = 'Grok が考えています…'

describe('AgentChat のアシスタント吹き出し', () => {
  it('assistant の Markdown は太字とリストとして描画する', () => {
    show({ messages: [message('assistant', '**方針**\n- 画像')] })
    const strong = screen.getByText('方針')
    expect(strong.tagName).toBe('STRONG')
    expect(screen.getByText('画像').closest('li')).not.toBeNull()
    expect(screen.queryByText('**方針**')).toBeNull()
    let bubble: HTMLElement | null = strong.parentElement
    while (bubble && !bubble.className.includes('max-w-[80%]')) {
      bubble = bubble.parentElement
    }
    expect(bubble?.className).toContain('min-w-0')
    expect(bubble?.className).toContain('max-w-[80%]')
  })
})

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

  it('ACP の活動が届いていればその内容を出す', () => {
    show({ status: 'running', thinking: true }, {
      thinking: true,
      activity: 'ツール実行中: run_terminal_command',
    })
    expect(screen.queryByText(THINKING)).toBeNull()
    expect(
      screen.queryByText('Grok が作業しています… ツール実行中: run_terminal_command'),
    ).not.toBeNull()
  })

  it('活動が無ければ従来の文言に戻す', () => {
    show({ status: 'running', thinking: true }, { thinking: true, activity: null })
    expect(screen.queryByText(THINKING)).not.toBeNull()
  })
})

describe('AgentChat の入力欄', () => {
  it('実行中は入力できず理由を出す（409 で無反応に見えるのを防ぐ）', () => {
    show({ status: 'running' })
    const input = screen.getByRole('textbox') as HTMLTextAreaElement
    expect(input.disabled).toBe(true)
    expect(input.placeholder).toBe('実行中は「停止」で中断できます')
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

describe('AgentChat のファイル添付', () => {
  function attach(name = 'photo.png') {
    const upload = vi
      .spyOn(api, 'uploadAgentAttachment')
      .mockResolvedValue({ name, path: `attachments/${name}` })
    const picker = screen.getByTestId('agent-attachment-input') as HTMLInputElement
    fireEvent.change(picker, {
      target: { files: [new File(['x'], name, { type: 'image/png' })] },
    })
    return upload
  }

  it('選んだファイルは即アップロードしてチップに出る', async () => {
    show()
    attach()
    expect(await screen.findByText('photo.png')).not.toBeNull()
  })

  it('添付だけでも送信でき、パスが onSend に渡る', async () => {
    const { onSend } = show()
    attach()
    await screen.findByText('photo.png')

    const button = screen.getByRole('button', { name: '送信' }) as HTMLButtonElement
    expect(button.disabled).toBe(false)
    fireEvent.click(button)
    expect(onSend).toHaveBeenCalledWith('', ['attachments/photo.png'])
    // 送信後はチップを片付ける
    await waitFor(() => expect(screen.queryByText('photo.png')).toBeNull())
  })

  it('本文と一緒に送ると両方渡る', async () => {
    const { onSend } = show()
    attach()
    await screen.findByText('photo.png')

    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'これ見て' } })
    fireEvent.click(screen.getByRole('button', { name: '送信' }))
    expect(onSend).toHaveBeenCalledWith('これ見て', ['attachments/photo.png'])
  })

  it('チップの ✕ で添付を外せる', async () => {
    show()
    attach()
    fireEvent.click(await screen.findByRole('button', { name: 'photo.png を外す' }))
    expect(screen.queryByText('photo.png')).toBeNull()
    expect((screen.getByRole('button', { name: '送信' }) as HTMLButtonElement).disabled).toBe(
      true,
    )
  })

  it('許可外の拡張子はアップロードせずエラーを出す', () => {
    show()
    const upload = vi.spyOn(api, 'uploadAgentAttachment')
    fireEvent.change(screen.getByTestId('agent-attachment-input'), {
      target: { files: [new File(['x'], 'evil.exe')] },
    })
    expect(upload).not.toHaveBeenCalled()
    expect(screen.queryByText('この形式は添付できません: evil.exe')).not.toBeNull()
  })

  it('入力できない状態では添付ボタンも押せない', () => {
    show({ status: 'running' })
    expect(
      (screen.getByRole('button', { name: 'ファイルを添付' }) as HTMLButtonElement)
        .disabled,
    ).toBe(true)
  })

  it('user メッセージの添付はバブルにファイル名を出す', () => {
    show({
      messages: [
        message('user', '本文\n\n[Attached files]\n- attachments/photo.png', {
          data: { attachments: ['attachments/photo.png'], text: '本文' },
        }),
      ],
    })
    expect(screen.queryByText('本文')).not.toBeNull()
    // 定型文つきの content ではなくユーザー本文だけを出す
    expect(screen.queryByText(/Attached files/)).toBeNull()
    expect(screen.queryByText('photo.png')).not.toBeNull()
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

describe('AgentChat のセッション設定（上限・チェックイン）', () => {
  it('上限バッジはどのチェックインモードでも出る（0 は無制限）', () => {
    show({ checkin_mode: 'milestone', auto_limit: 3 })
    expect(screen.queryByText('節目のみ / 上限 3 本')).not.toBeNull()

    cleanup()
    show({ checkin_mode: 'auto', auto_limit: 0 })
    expect(screen.queryByText('完了まで自走 / 上限 無制限')).not.toBeNull()
  })

  it('⚙ から上限とチェックインを変えて保存できる', () => {
    const { onUpdateSession } = show({ checkin_mode: 'milestone', auto_limit: 5 })
    fireEvent.click(screen.getByRole('button', { name: 'セッション設定を変更' }))

    fireEvent.change(screen.getByLabelText('チェックイン'), {
      target: { value: 'auto' },
    })
    fireEvent.change(screen.getByLabelText('上限本数（0 = 無制限）'), {
      target: { value: '0' },
    })
    fireEvent.click(screen.getByRole('button', { name: '保存' }))

    expect(onUpdateSession).toHaveBeenCalledWith({
      checkin_mode: 'auto',
      auto_limit: 0,
    })
    // 保存すると小窓は閉じる
    expect(screen.queryByLabelText('上限本数（0 = 無制限）')).toBeNull()
  })
})
