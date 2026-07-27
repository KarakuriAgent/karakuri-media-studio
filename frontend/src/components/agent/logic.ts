/**
 * エージェント画面の判定ロジック（純関数だけ。UI から切り離してテストする）。
 */
import type { AgentProgress, AgentSession } from '../../types'
import { AGENT_ACTIVE } from './common'

/** 応答待ちのチェックイン（末尾の未応答チェックイン）。無ければ -1。 */
export function openCheckinIndex(session: AgentSession): number {
  if (session.status !== 'waiting_checkin') return -1
  for (let index = session.messages.length - 1; index >= 0; index -= 1) {
    const message = session.messages[index]
    if (message.role !== 'checkin') continue
    return message.data?.resolved === true ? -1 : index
  }
  return -1
}

/**
 * そのチェックインに実際に回答したか。
 *
 * バックエンドは応答時に `data.resolved` を立てる（古いセッションには無いので、
 * 後続にユーザー発言があるかどうかで補う）。
 */
export function isCheckinAnswered(session: AgentSession, index: number): boolean {
  const message = session.messages[index]
  if (!message) return false
  if (message.data?.resolved === true) return true
  return session.messages
    .slice(index + 1)
    .some((later) => later.role === 'user')
}

/**
 * 「Grok が考えています…」を出すか。
 *
 * `busy` はこのブラウザ発の API 呼び出し中しか立たないので、バックエンドのループが
 * 回すターン（承認直後・ジョブ完了後の自動ターン・チェックイン応答後）は
 * セッションの `thinking`（ポーリング）と WS フレームで拾う。
 */
export function isThinking({
  busy,
  session,
  frame,
}: {
  busy: boolean
  session: AgentSession
  frame?: AgentProgress | null
}): boolean {
  if (busy || session.thinking) return true
  if (!frame || frame.session_id !== session.id || frame.thinking !== true) return false
  // 終了済みセッションに取り残されたフレームで出しっぱなしにしない
  return AGENT_ACTIVE.includes(session.status) || session.status === 'planning'
}

/** メイン入力欄の状態（ループ実行中は送信が 409 になるので触らせない）。 */
export function inputState(
  session: AgentSession,
  thinking: boolean,
): { disabled: boolean; placeholder: string } {
  if (session.status === 'running' || thinking) {
    return {
      disabled: true,
      placeholder: '実行中は完了を待つか ⏹停止 してください',
    }
  }
  if (session.status === 'waiting_checkin') {
    return {
      disabled: false,
      placeholder: 'チェックインに回答（Ctrl+Enter で送信）',
    }
  }
  return { disabled: false, placeholder: '指示を入力（Ctrl+Enter で送信）' }
}

/**
 * 取得済みセッションを差し替えてよいか（連打時のレース対策）。
 *
 * 古いレスポンスが新しい状態を上書きしないように、記録が減る差し替えは捨てる。
 */
export function shouldReplaceSession(
  current: AgentSession | null,
  next: AgentSession,
): boolean {
  if (!current || current.id !== next.id) return true
  if (next.messages.length < current.messages.length) return false
  if (next.artifacts.length < current.artifacts.length) return false
  return true
}
