import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { AgentSessionCreate } from '../../types'
import SessionList from './SessionList'

afterEach(cleanup)

function show() {
  const onCreate = vi.fn<(payload: AgentSessionCreate, files: File[]) => void>()
  render(
    <SessionList
      sessions={[]}
      activeId={null}
      loading={false}
      busy={false}
      collapsed={false}
      onToggle={() => {}}
      onReload={() => {}}
      onSelect={() => {}}
      onDelete={() => {}}
      onCreate={onCreate}
      onToggleNsfw={() => {}}
      showNsfw={false}
    />,
  )
  // 新規セッションフォームを開く
  fireEvent.click(screen.getByRole('button', { name: '＋ 新規セッション' }))
  return { onCreate }
}

function attach(...names: string[]) {
  const picker = screen.getByTestId('new-session-attachment-input')
  fireEvent.change(picker, {
    target: { files: names.map((name) => new File(['x'], name)) },
  })
}

describe('SessionList の新規セッションフォーム', () => {
  it('選んだファイルはチップに出る（アップロードはまだしない）', () => {
    show()
    attach('photo.png')
    expect(screen.queryByText('📎 photo.png')).not.toBeNull()
  })

  it('✕ でチップを外せる', () => {
    show()
    attach('photo.png')
    fireEvent.click(screen.getByRole('button', { name: 'photo.png を外す' }))
    expect(screen.queryByText('📎 photo.png')).toBeNull()
  })

  it('許可外の拡張子は弾いてエラーを出す', () => {
    show()
    attach('evil.exe', 'ok.png')
    expect(screen.queryByText('この形式は添付できません: evil.exe')).not.toBeNull()
    expect(screen.queryByText('📎 evil.exe')).toBeNull()
    expect(screen.queryByText('📎 ok.png')).not.toBeNull()
  })

  it('開始で onCreate に File が渡り、チップは片付く', () => {
    const { onCreate } = show()
    attach('photo.png')
    fireEvent.change(screen.getByPlaceholderText(/かおりのダンス動画/), {
      target: { value: 'この写真で作って' },
    })
    fireEvent.click(screen.getByRole('button', { name: '開始' }))

    expect(onCreate).toHaveBeenCalledTimes(1)
    const [payload, files] = onCreate.mock.calls[0]
    expect(payload.goal).toBe('この写真で作って')
    expect(files.map((file) => file.name)).toEqual(['photo.png'])
  })

  it('上限本数はチェックインモードに関係なく出る（0 = 無制限）', () => {
    const { onCreate } = show()
    const limit = screen.getByLabelText('上限本数（0 = 無制限）')
    // 既定は「節目のみ」。自走に切り替えなくても欄は出ている。
    expect((screen.getByLabelText('チェックイン') as HTMLSelectElement).value).toBe(
      'milestone',
    )
    expect(limit).not.toBeNull()
    expect(
      screen.queryByText(/0 = 無制限で、確認せず走り切ります/),
    ).not.toBeNull()

    fireEvent.change(limit, { target: { value: '0' } })
    fireEvent.change(screen.getByPlaceholderText(/かおりのダンス動画/), {
      target: { value: '3本' },
    })
    fireEvent.click(screen.getByRole('button', { name: '開始' }))
    expect(onCreate.mock.calls[0][0].auto_limit).toBe(0)
    expect(onCreate.mock.calls[0][0].checkin_mode).toBe('milestone')
  })

  it('セッション名は任意（入れれば payload に載る）', () => {
    const { onCreate } = show()
    fireEvent.change(screen.getByPlaceholderText(/かおりのダンス動画/), {
      target: { value: '3本' },
    })
    fireEvent.click(screen.getByRole('button', { name: '開始' }))
    expect(onCreate.mock.calls[0][0].title).toBe('')

    cleanup()
    const again = show()
    fireEvent.change(screen.getByLabelText('セッション名（任意）'), {
      target: { value: '夏の企画' },
    })
    fireEvent.change(screen.getByPlaceholderText(/かおりのダンス動画/), {
      target: { value: '3本' },
    })
    fireEvent.click(screen.getByRole('button', { name: '開始' }))
    expect(again.onCreate.mock.calls[0][0].title).toBe('夏の企画')
  })

  it('添付だけでも開始できる（指示が空でもボタンが押せる）', () => {
    const { onCreate } = show()
    const button = screen.getByRole('button', { name: '開始' }) as HTMLButtonElement
    expect(button.disabled).toBe(true)
    attach('notes.txt')
    expect(button.disabled).toBe(false)

    fireEvent.click(button)
    const [payload, files] = onCreate.mock.calls[0]
    expect(payload.goal).toBe('')
    expect(files).toHaveLength(1)
  })
})
