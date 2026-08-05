import { useEffect, useRef, useState } from 'react'
import type { CanvasMessage, CanvasProjectDetail } from '../../types'
import MentionInput from './MentionInput'
import { messageText } from './logic'

export const CHAT_PLACEHOLDER = 'アイデアを説明し、@ で素材を参照'
export const THINKING_TEXT = 'キャンバスの相棒が考えています…'

interface Props {
  project: CanvasProjectDetail
  /** 送信中（このブラウザ発）または バックエンドのターンが走っている。 */
  busy: boolean
  onSend: (content: string) => void
  onStop: () => void
}

/** イベント行の見出しアイコン（種別が増えても既定の • に落ちる）。 */
const EVENT_ICON: Record<string, string> = {
  list_nodes: '📋',
  read_node: '📖',
  create_node: '➕',
  update_node: '✏️',
  move_node: '↔️',
  best_practices: '💡',
  job_started: '🎬',
  job_done: '✅',
  job_failed: '❌',
  generate_failed: '❌',
  action_invalid: '⚠',
  stopped: '⏹',
}

function shortTime(ts: string): string {
  const parsed = new Date(ts)
  return Number.isNaN(parsed.getTime())
    ? ''
    : parsed.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

function Bubble({ message }: { message: CanvasMessage }) {
  const mine = message.role === 'user'
  return (
    <div className={`flex ${mine ? 'justify-end' : 'justify-start'}`}>
      <div
        className={`max-w-[85%] whitespace-pre-wrap break-words rounded-lg px-3 py-2 text-sm ${
          mine ? 'bg-accent-500/20 text-slate-100' : 'bg-ink-700 text-slate-200'
        }`}
      >
        {messageText(message)}
      </div>
    </div>
  )
}

function EventRow({ message }: { message: CanvasMessage }) {
  return (
    <div className="flex items-start gap-2 px-1 text-[11px] text-slate-500">
      <span>{EVENT_ICON[message.kind ?? ''] ?? '•'}</span>
      <span className="min-w-0 flex-1 whitespace-pre-wrap break-words">
        {message.content}
      </span>
      <span className="shrink-0 text-slate-700">{shortTime(message.ts)}</span>
    </div>
  )
}

export default function CanvasChat({ project, busy, onSend, onStop }: Props) {
  const [draft, setDraft] = useState('')
  const scroller = useRef<HTMLDivElement>(null)

  useEffect(() => {
    scroller.current?.scrollTo({ top: scroller.current.scrollHeight })
  }, [project.messages.length, busy])

  const send = () => {
    const content = draft.trim()
    if (!content || busy) return
    setDraft('')
    onSend(content)
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-2">
      <div className="flex shrink-0 items-center gap-2">
        <h2 className="text-xs font-semibold uppercase tracking-wider text-slate-400">
          チャット
        </h2>
        <button
          className="btn-ghost ml-auto !py-1 text-xs"
          disabled={!busy}
          title={busy ? '次のターンの手前で中断します' : '実行中ではありません'}
          onClick={onStop}
        >
          ⏹ 停止
        </button>
      </div>

      <div
        ref={scroller}
        className="min-h-0 flex-1 space-y-2 overflow-y-auto rounded-lg border border-ink-700 bg-ink-800/40 p-2"
      >
        {project.messages.length === 0 && !busy && (
          <p className="text-xs text-slate-600">
            まだ会話はありません。作りたいものを書いてみてください。
          </p>
        )}
        {project.messages.map((message) =>
          message.role === 'event' ? (
            <EventRow key={message.id} message={message} />
          ) : (
            <Bubble key={message.id} message={message} />
          ),
        )}
        {busy && <p className="text-xs text-slate-500">{THINKING_TEXT}</p>}
      </div>

      <div className="flex shrink-0 flex-col gap-2">
        <MentionInput
          value={draft}
          nodes={project.nodes}
          disabled={busy}
          placeholder={CHAT_PLACEHOLDER}
          onChange={setDraft}
          onSubmit={send}
        />
        <div className="flex items-center gap-2">
          <span className="text-[11px] text-slate-600">
            Ctrl + Enter で送信
          </span>
          <button
            className="btn-primary ml-auto !py-1 text-xs"
            disabled={busy || draft.trim().length === 0}
            onClick={send}
          >
            送信
          </button>
        </div>
      </div>
    </div>
  )
}
