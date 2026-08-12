import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from '../../api'
import AgentView from './AgentView'

// AgentView は開いた時点でセッション一覧を取りに行くので、そこだけ差し替える。
vi.mock('../../api', async () => {
  const actual = await vi.importActual<typeof import('../../api')>('../../api')
  return {
    ...actual,
    api: { listAgentSessions: vi.fn(), getAgentSession: vi.fn() },
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
