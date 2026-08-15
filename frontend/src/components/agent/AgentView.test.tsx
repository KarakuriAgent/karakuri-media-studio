import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from '../../api'
import type { AgentProgress } from '../../types'
import AgentView from './AgentView'
import { message, session } from './fixtures'

// AgentView は開いた時点でセッション一覧を取りに行くので、そこだけ差し替える。
vi.mock('../../api', async () => {
  const actual = await vi.importActual<typeof import('../../api')>('../../api')
  return {
    ...actual,
    api: {
      listAgentSessions: vi.fn(),
      getAgentSession: vi.fn(),
      sendAgentMessage: vi.fn(),
    },
  }
})

const mocked = api as unknown as Record<string, ReturnType<typeof vi.fn>>

afterEach(cleanup)

describe('AgentView の接続先プルダウン', () => {
  it('生成タブと同じセレクタを出し、選び直すと保存を頼む', async () => {
    mocked.listAgentSessions.mockResolvedValue([])
    const onComfyTarget = vi.fn()
    render(
      <AgentView
        event={null}
        progress={{}}
        showNsfw={false}
        comfyTarget="runpod"
        onComfyTarget={onComfyTarget}
      />,
    )

    const select = (await screen.findByLabelText('接続先')) as HTMLSelectElement
    expect(select.value).toBe('runpod')
    expect(
      [...select.options].map((option) => option.value),
    ).toEqual(['comfy_cloud', 'runpod', 'local'])

    fireEvent.change(select, { target: { value: 'local' } })
    expect(onComfyTarget).toHaveBeenCalledWith('local')
  })

  it('接続先を渡さない画面には出さない', async () => {
    mocked.listAgentSessions.mockResolvedValue([])
    render(<AgentView event={null} progress={{}} showNsfw={false} />)
    await waitFor(() => expect(mocked.listAgentSessions).toHaveBeenCalled())
    expect(screen.queryByLabelText('接続先')).toBeNull()
  })
})


describe('AgentView の即受付（202）', () => {
  it('送信は受付だけ確認し、返事はセッションの更新で受け取る', async () => {
    // 開いたままのセッションを復元させる（一覧を経由せずチャットを出す）
    window.sessionStorage.setItem('agent-open-session', 'session-1')
    const idle = session()
    const asked = session({
      status: 'running',
      thinking: true,
      messages: [...idle.messages, message('user', '3本つくって')],
    })
    const answered = session({
      status: 'idle',
      messages: [...asked.messages, message('assistant', 'どんな雰囲気にしますか？')],
    })
    mocked.listAgentSessions.mockResolvedValue([
      {
        id: 'session-1',
        created_at: idle.created_at,
        title: idle.title,
        status: 'idle',
        checkin_mode: 'milestone',
        auto_limit: 5,
        message_count: 1,
        artifact_count: 0,
        nsfw: false,
      },
    ])
    mocked.getAgentSession.mockResolvedValue(idle)
    // バックエンドは受付だけ返す（content は空、session は実行中）
    mocked.sendAgentMessage.mockResolvedValue({
      content: '',
      action: null,
      session: asked,
    })

    const view = render(<AgentView event={null} progress={{}} showNsfw={false} />)
    const input = await screen.findByPlaceholderText('指示を入力（Ctrl+Enter で送信）')
    fireEvent.change(input, { target: { value: '3本つくって' } })
    fireEvent.click(screen.getByRole('button', { name: '送信' }))

    await waitFor(() =>
      expect(mocked.sendAgentMessage).toHaveBeenCalledWith('session-1', '3本つくって', []),
    )
    // ターンの完了を待たずに「考えています…」を出す（受付の session が実行中）
    expect(await screen.findByText('Grok が考えています…')).toBeTruthy()
    expect(screen.getByText('3本つくって')).toBeTruthy()

    // 返事は WS フレーム（→ セッション取り直し）で届く
    mocked.getAgentSession.mockResolvedValue(answered)
    const frame: AgentProgress = {
      type: 'agent',
      session_id: 'session-1',
      status: 'idle',
      task_id: null,
      task_status: null,
      job_id: null,
      artifact: null,
      message: null,
      thinking: false,
      activity: null,
    }
    view.rerender(
      <AgentView event={frame} progress={{}} showNsfw={false} />,
    )
    expect(await screen.findByText('どんな雰囲気にしますか？')).toBeTruthy()
    await waitFor(() =>
      expect(screen.queryByText('Grok が考えています…')).toBeNull(),
    )
  })
})
