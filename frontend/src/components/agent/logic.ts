/**
 * エージェント画面の判定ロジック（純関数だけ。UI から切り離してテストする）。
 */
import type { AgentArtifact, AgentProgress, AgentSession } from '../../types'
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

/**
 * 実行中の活動テキスト（「ツール実行中: run_terminal_command」など）。
 *
 * `isThinking` と同じ流儀で、WS フレーム（最新）とセッションの `activity`
 * （ポーリングの取りこぼし補完）を統合する。無ければ null。
 */
export function currentActivity({
  session,
  frame,
}: {
  session: AgentSession
  frame?: AgentProgress | null
}): string | null {
  if (frame && frame.session_id === session.id && frame.thinking !== false) {
    if (frame.activity) return frame.activity
  }
  return session.activity || null
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

// ------------------------------------------------------------- 成果物カード

/** 成果物 1 件（元配列の位置を持つ）。 */
export interface ArtifactEntry {
  index: number
  artifact: AgentArtifact
}

/** 成果物パネルのカード 1 枚（フレーム検分だけ job ごとにまとまる）。 */
export type ArtifactCard =
  | { type: 'single'; key: string; index: number; artifact: AgentArtifact }
  | {
      type: 'frames'
      key: string
      jobId: string
      title: string
      ts: string
      frames: ArtifactEntry[]
    }

/**
 * フレームまとめカードのタイトル。
 *
 * バックエンドは 1 枚ずつ「<label> フレーム検分 <n>」で登録するので、
 * 末尾の連番 / ファイル名を落として共通部分だけ使う（旧データも壊さない）。
 */
export function frameGroupTitle(frames: AgentArtifact[]): string {
  const first = frames[0]
  const base = (first?.title || '')
    .replace(/\s*(\d+|frame_\d+\.png|last_frame\.png)$/i, '')
    .trim()
  const label = base || 'フレーム検分'
  return `${label} (${frames.length}枚)`
}

/**
 * 成果物をカードへ畳む。
 *
 * kind='frame' は job_id ごとに 1 枚のカードへまとめ（最初のフレームの位置に置く）、
 * job_id を持たないフレームと他の種別はそのまま 1 件 1 カード。
 */
export function groupArtifacts(artifacts: AgentArtifact[]): ArtifactCard[] {
  const cards: ArtifactCard[] = []
  const byJob = new Map<string, ArtifactCard & { type: 'frames' }>()

  artifacts.forEach((artifact, index) => {
    if (artifact.kind === 'frame' && artifact.job_id) {
      const existing = byJob.get(artifact.job_id)
      if (existing) {
        existing.frames.push({ index, artifact })
        existing.title = frameGroupTitle(existing.frames.map((f) => f.artifact))
        return
      }
      const card: ArtifactCard & { type: 'frames' } = {
        type: 'frames',
        key: `frames-${artifact.job_id}`,
        jobId: artifact.job_id,
        title: frameGroupTitle([artifact]),
        ts: artifact.ts,
        frames: [{ index, artifact }],
      }
      byJob.set(artifact.job_id, card)
      cards.push(card)
      return
    }
    cards.push({
      type: 'single',
      key: `${artifact.ts}-${index}`,
      index,
      artifact,
    })
  })

  return cards
}

/**
 * ダウンロード時のファイル名（成果物タイトルから作り、拡張子は URL のものを維持）。
 */
export function downloadName(title: string, url: string | null): string {
  const path = (url ?? '').split('?')[0]
  const original = path.slice(path.lastIndexOf('/') + 1)
  const dot = original.lastIndexOf('.')
  const extension = dot > 0 ? original.slice(dot) : ''
  const base = (title || '')
    // ファイル名に使えない文字を落とし、空白は _ に寄せる（日本語はそのまま）
    .replace(/[\\/:*?"<>|]/g, '')
    .trim()
    .replace(/\s+/g, '_')
    .replace(/^[._]+|[._]+$/g, '')
    .slice(0, 80)
  if (!base) return original || 'download'
  return base.toLowerCase().endsWith(extension.toLowerCase())
    ? base
    : base + extension
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
