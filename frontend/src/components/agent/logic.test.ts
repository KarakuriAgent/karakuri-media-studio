import { describe, expect, it } from 'vitest'
import type { AgentProgress } from '../../types'
import { message, session } from './fixtures'
import {
  inputState,
  isCheckinAnswered,
  isThinking,
  openCheckinIndex,
  shouldReplaceSession,
} from './logic'

function frame(overrides: Partial<AgentProgress> = {}): AgentProgress {
  return {
    type: 'agent',
    session_id: 'session-1',
    status: 'running',
    task_id: null,
    task_status: null,
    job_id: null,
    artifact: null,
    message: null,
    thinking: null,
    ...overrides,
  }
}

describe('openCheckinIndex', () => {
  const checkin = message('checkin', '進めますか？', { kind: 'checkin' })

  it('チェックイン待ちなら末尾のチェックインを開く', () => {
    const state = session({
      status: 'waiting_checkin',
      messages: [message('user', 'お願い'), checkin],
    })
    expect(openCheckinIndex(state)).toBe(1)
  })

  it('応答済みマークが付いていれば開かない', () => {
    const state = session({
      status: 'waiting_checkin',
      messages: [
        message('checkin', '進めますか？', { kind: 'checkin', data: { resolved: true } }),
      ],
    })
    expect(openCheckinIndex(state)).toBe(-1)
  })

  it('チェックイン待ち以外では開かない', () => {
    expect(openCheckinIndex(session({ status: 'running', messages: [checkin] }))).toBe(-1)
  })
})

describe('isCheckinAnswered', () => {
  it('resolved が立っていれば応答済み', () => {
    const state = session({
      messages: [message('checkin', 'q', { data: { resolved: true } })],
    })
    expect(isCheckinAnswered(state, 0)).toBe(true)
  })

  it('後続のユーザー発言でも応答済みとみなす（古いセッション）', () => {
    const state = session({
      messages: [message('checkin', 'q'), message('user', 'そのまま')],
    })
    expect(isCheckinAnswered(state, 0)).toBe(true)
  })

  it('停止などで回答されなかったチェックインは未応答', () => {
    const state = session({
      messages: [message('checkin', 'q'), message('event', '停止しました', { kind: 'stopped' })],
    })
    expect(isCheckinAnswered(state, 0)).toBe(false)
  })
})

describe('isThinking', () => {
  it('このブラウザ発の呼び出し中は busy で出す', () => {
    expect(isThinking({ busy: true, session: session() })).toBe(true)
  })

  it('バックエンドのループが回すターンは session.thinking で出す', () => {
    expect(
      isThinking({ busy: false, session: session({ status: 'running', thinking: true }) }),
    ).toBe(true)
  })

  it('WS フレームの thinking でも出す', () => {
    expect(
      isThinking({
        busy: false,
        session: session({ status: 'running' }),
        frame: frame({ thinking: true }),
      }),
    ).toBe(true)
  })

  it('別セッションのフレームは無視する', () => {
    expect(
      isThinking({
        busy: false,
        session: session({ status: 'running' }),
        frame: frame({ session_id: 'other', thinking: true }),
      }),
    ).toBe(false)
  })

  it('終了済みセッションに残ったフレームでは出しっぱなしにしない', () => {
    expect(
      isThinking({
        busy: false,
        session: session({ status: 'done' }),
        frame: frame({ status: 'done', thinking: true }),
      }),
    ).toBe(false)
  })

  it('何も走っていなければ出さない', () => {
    expect(isThinking({ busy: false, session: session(), frame: frame() })).toBe(false)
  })
})

describe('inputState', () => {
  it('実行中は入力を止めて理由を出す', () => {
    const state = inputState(session({ status: 'running' }), false)
    expect(state.disabled).toBe(true)
    expect(state.placeholder).toContain('停止')
  })

  it('ターン実行中（thinking）も入力を止める', () => {
    expect(inputState(session({ status: 'idle' }), true).disabled).toBe(true)
  })

  it('チェックイン待ちは入力できる（自由回答として送る）', () => {
    const state = inputState(session({ status: 'waiting_checkin' }), false)
    expect(state.disabled).toBe(false)
    expect(state.placeholder).toContain('チェックイン')
  })

  it('待機中は通常の指示入力', () => {
    const state = inputState(session({ status: 'idle' }), false)
    expect(state.disabled).toBe(false)
    expect(state.placeholder).toContain('指示')
  })
})

describe('shouldReplaceSession', () => {
  const current = session({
    messages: [message('user', 'a'), message('assistant', 'b')],
  })

  it('未取得や別セッションは常に差し替える', () => {
    expect(shouldReplaceSession(null, current)).toBe(true)
    expect(shouldReplaceSession(session({ id: 'other' }), current)).toBe(true)
  })

  it('記録が増えていれば差し替える', () => {
    const next = session({ messages: [...current.messages, message('event', 'c')] })
    expect(shouldReplaceSession(current, next)).toBe(true)
  })

  it('記録が減る古いレスポンスは捨てる', () => {
    expect(shouldReplaceSession(current, session({ messages: [message('user', 'a')] }))).toBe(
      false,
    )
  })

  it('成果物が減る古いレスポンスも捨てる', () => {
    const withArtifact = session({
      messages: current.messages,
      artifacts: [
        { kind: 'note', title: 'メモ', ts: '2026-07-27T00:00:00', name: '', url: null, job_id: null, text: 'x' },
      ],
    })
    expect(shouldReplaceSession(withArtifact, session({ messages: current.messages }))).toBe(
      false,
    )
  })
})
