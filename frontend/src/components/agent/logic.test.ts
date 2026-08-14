import { describe, expect, it } from 'vitest'
import type { AgentArtifact, AgentProgress } from '../../types'
import { message, session } from './fixtures'
import {
  currentActivity,
  downloadName,
  frameGroupTitle,
  groupArtifacts,
  inputState,
  isCheckinAnswered,
  isThinking,
  openCheckinIndex,
  shouldReplaceSession,
} from './logic'

function artifact(
  kind: AgentArtifact['kind'],
  title: string,
  extra: Partial<AgentArtifact> = {},
): AgentArtifact {
  return {
    kind,
    title,
    ts: '2026-07-27T00:00:00',
    name: '',
    url: null,
    job_id: null,
    text: null,
    ...extra,
  }
}

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
    activity: null,
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

describe('currentActivity', () => {
  it('WS フレームの activity を最優先で出す', () => {
    expect(
      currentActivity({
        session: session({ status: 'running', thinking: true }),
        frame: frame({ thinking: true, activity: 'ツール実行中: run_terminal_command' }),
      }),
    ).toBe('ツール実行中: run_terminal_command')
  })

  it('フレームが無ければセッションの activity（ポーリング）で補う', () => {
    expect(
      currentActivity({ session: session({ status: 'running', activity: '思考中' }) }),
    ).toBe('思考中')
  })

  it('別セッションのフレームは無視する', () => {
    expect(
      currentActivity({
        session: session({ status: 'running', activity: '思考中' }),
        frame: frame({ session_id: 'other', activity: 'ツール実行中: ls' }),
      }),
    ).toBe('思考中')
  })

  it('ターン終了のフレーム（thinking=false）では活動を出さない', () => {
    expect(
      currentActivity({ session: session({ status: 'done' }), frame: frame({ thinking: false }) }),
    ).toBeNull()
  })

  it('何も無ければ null', () => {
    expect(currentActivity({ session: session(), frame: frame() })).toBeNull()
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
    expect(state.placeholder).toBe('実行中は「停止」で中断できます')
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

describe('groupArtifacts', () => {
  it('フレームは job ごとに 1 枚のカードへまとめる', () => {
    const cards = groupArtifacts([
      artifact('image', '① 明るいスタジオ 生成画像'),
      artifact('frame', '① 明るいスタジオ フレーム検分 1', {
        job_id: 'job-1',
        name: 'inspect_job-1/frame_001.png',
      }),
      artifact('frame', '① 明るいスタジオ フレーム検分 2', {
        job_id: 'job-1',
        name: 'inspect_job-1/frame_002.png',
      }),
      artifact('video', '① 明るいスタジオ 動画'),
    ])
    expect(cards.map((card) => card.type)).toEqual(['single', 'frames', 'single'])
    const group = cards[1]
    if (group.type !== 'frames') throw new Error('frames card expected')
    expect(group.jobId).toBe('job-1')
    expect(group.frames.map((entry) => entry.index)).toEqual([1, 2])
    expect(group.title).toBe('① 明るいスタジオ フレーム検分 (2枚)')
  })

  it('別 job のフレームは別カードになる', () => {
    const cards = groupArtifacts([
      artifact('frame', 'A フレーム検分 1', { job_id: 'job-1' }),
      artifact('frame', 'B フレーム検分 1', { job_id: 'job-2' }),
      artifact('frame', 'A フレーム検分 2', { job_id: 'job-1' }),
    ])
    expect(cards).toHaveLength(2)
    expect(cards[0].type === 'frames' && cards[0].frames).toHaveLength(2)
  })

  it('job_id のないフレームは 1 件 1 カードのまま', () => {
    const cards = groupArtifacts([artifact('frame', '検分 1'), artifact('frame', '検分 2')])
    expect(cards.map((card) => card.type)).toEqual(['single', 'single'])
  })

  it('成果物が無ければカードも無い', () => {
    expect(groupArtifacts([])).toEqual([])
  })
})

describe('frameGroupTitle', () => {
  it('末尾の連番を落として枚数を添える', () => {
    expect(frameGroupTitle([artifact('frame', '夕暮れ屋上ダンス フレーム検分 3')])).toBe(
      '夕暮れ屋上ダンス フレーム検分 (1枚)',
    )
  })

  it('旧データのファイル名付きタイトルも畳める', () => {
    expect(
      frameGroupTitle([
        artifact('frame', 'job-1 検分 frame_001.png'),
        artifact('frame', 'job-1 検分 frame_002.png'),
      ]),
    ).toBe('job-1 検分 (2枚)')
  })

  it('タイトルが無ければ既定の見出しを使う', () => {
    expect(frameGroupTitle([artifact('frame', '')])).toBe('フレーム検分 (1枚)')
  })
})

describe('downloadName', () => {
  it('タイトルから作り、拡張子は URL のものを維持する', () => {
    expect(downloadName('夕暮れ屋上ダンス・引きカメラ', '/outputs/abc123.mp4')).toBe(
      '夕暮れ屋上ダンス・引きカメラ.mp4',
    )
  })

  it('ファイル名に使えない文字と空白を落とす', () => {
    expect(downloadName('a/b:c d', '/outputs/x.png')).toBe('abc_d.png')
  })

  it('タイトルが無ければ URL 側のファイル名にする', () => {
    expect(downloadName('', '/outputs/abc123.mp4')).toBe('abc123.mp4')
    expect(downloadName('', null)).toBe('download')
  })
})
