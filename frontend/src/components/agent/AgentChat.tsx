import { useEffect, useRef, useState } from 'react'
import type { AgentMessage, AgentSession, JobProgress } from '../../types'
import { Banner, NsfwBadge, NsfwToggle } from '../ui'
import PlanCard from './PlanCard'
import { AGENT_ACTIVE, AgentStatusBadge, CHECKIN_LABEL, eventIcon, shortTime } from './common'

interface Props {
  session: AgentSession
  progress: Record<string, JobProgress>
  busy: boolean
  error: string | null
  onDismissError: () => void
  onSend: (content: string) => void
  onApprove: () => void
  onCheckin: (answer: string) => void
  onStop: () => void
  /** 狭幅のみ: セッション一覧ドロワーを開く。 */
  onOpenSessions: () => void
  /** 狭幅のみ: 成果物パネル（全画面オーバーレイ）を開く。 */
  onOpenArtifacts: () => void
  artifactCount: number
  /** 未読の新着成果物がある（狭幅ではボタンにバッジを出すだけ）。 */
  artifactBadge: boolean
  onToggleNsfw: (nsfw: boolean) => void
  /** NSFW 表示トグル（オンのときだけ 🔞 バッジを出す）。 */
  showNsfw: boolean
}

/** The checkin that is still open: the last one, while the loop waits for it. */
function openCheckinIndex(session: AgentSession): number {
  if (session.status !== 'waiting_checkin') return -1
  for (let index = session.messages.length - 1; index >= 0; index -= 1) {
    if (session.messages[index].role === 'checkin') return index
  }
  return -1
}

function optionsOf(message: AgentMessage): string[] {
  const options = message.data?.options
  return Array.isArray(options) ? options.filter((o): o is string => typeof o === 'string') : []
}

function Bubble({ message }: { message: AgentMessage }) {
  const mine = message.role === 'user'
  return (
    <div className={`flex ${mine ? 'justify-end' : 'justify-start'}`}>
      <div
        className={`max-w-[80%] whitespace-pre-wrap rounded-lg px-3 py-2 text-sm ${
          mine ? 'bg-accent-500/20 text-slate-100' : 'bg-ink-700 text-slate-200'
        }`}
      >
        {message.content}
      </div>
    </div>
  )
}

function EventRow({ message }: { message: AgentMessage }) {
  return (
    <div className="flex items-start gap-2 px-1 text-[11px] text-slate-500">
      <span>{eventIcon(message.kind)}</span>
      <span className="min-w-0 flex-1 whitespace-pre-wrap break-words">
        {message.content}
      </span>
      <span className="shrink-0 text-slate-700">{shortTime(message.ts)}</span>
    </div>
  )
}

function CheckinBubble({
  message,
  open,
  busy,
  onAnswer,
}: {
  message: AgentMessage
  open: boolean
  busy: boolean
  onAnswer: (answer: string) => void
}) {
  const [free, setFree] = useState('')
  const options = optionsOf(message)

  return (
    <div className="flex justify-start">
      <div
        className={`max-w-[85%] rounded-lg border px-3 py-2 text-sm ${
          open
            ? 'border-violet-700/70 bg-violet-950/40 text-violet-100'
            : 'border-ink-600 bg-ink-800 text-slate-400'
        }`}
      >
        <p className="mb-2 whitespace-pre-wrap">
          {message.kind === 'approval' ? '🛡 承認' : '⚠'} {message.content}
        </p>
        <div className="flex flex-wrap gap-1.5">
          {options.map((option) => (
            <button
              key={option}
              className="btn-ghost !py-1 text-xs"
              disabled={!open || busy}
              onClick={() => onAnswer(option)}
            >
              {option}
            </button>
          ))}
        </div>
        {open && (
          <div className="mt-2 flex gap-1.5">
            <input
              className="field !py-1 text-xs"
              value={free}
              placeholder="自由に回答"
              disabled={busy}
              onChange={(event) => setFree(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' && free.trim()) {
                  event.preventDefault()
                  onAnswer(free.trim())
                  setFree('')
                }
              }}
            />
            <button
              className="btn-primary !py-1 text-xs"
              disabled={busy || !free.trim()}
              onClick={() => {
                onAnswer(free.trim())
                setFree('')
              }}
            >
              返答
            </button>
          </div>
        )}
        {!open && <p className="mt-1 text-[11px] text-slate-600">応答済み</p>}
      </div>
    </div>
  )
}

export default function AgentChat({
  session,
  progress,
  busy,
  error,
  onDismissError,
  onSend,
  onApprove,
  onCheckin,
  onStop,
  onOpenSessions,
  onOpenArtifacts,
  artifactCount,
  artifactBadge,
  onToggleNsfw,
  showNsfw,
}: Props) {
  const [draft, setDraft] = useState('')
  const scroller = useRef<HTMLDivElement>(null)
  const input = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    scroller.current?.scrollTo({ top: scroller.current.scrollHeight })
  }, [session.messages.length, busy])

  const running = session.status === 'running'
  const openIndex = openCheckinIndex(session)
  const stoppable = AGENT_ACTIVE.includes(session.status)

  const send = () => {
    const content = draft.trim()
    if (!content || busy) return
    setDraft('')
    onSend(content)
  }

  return (
    <section className="flex min-h-0 min-w-0 flex-1 flex-col gap-2">
      <div className="flex shrink-0 flex-wrap items-center gap-2 rounded-lg border border-ink-600 bg-ink-800 px-3 py-2">
        <button
          className="btn-ghost !px-2 !py-1 text-xs lg:hidden"
          onClick={onOpenSessions}
          title="セッション一覧"
        >
          ☰
        </button>
        <span className="min-w-0 flex-1 truncate text-xs text-slate-200 lg:flex-none">
          {session.title || '(無題)'}
        </span>
        <AgentStatusBadge status={session.status} />
        {showNsfw && session.nsfw && <NsfwBadge />}
        <span className="text-[11px] text-slate-500">
          {CHECKIN_LABEL[session.checkin_mode]}
          {session.checkin_mode === 'auto' ? ` / 上限 ${session.auto_limit} 本` : ''}
        </span>
        <div className="ml-auto flex items-center gap-1.5">
          <NsfwToggle nsfw={session.nsfw} disabled={busy} onToggle={onToggleNsfw} />
          <button
            className="btn-ghost relative !py-1 text-xs lg:hidden"
            onClick={onOpenArtifacts}
            title="成果物パネルを開く"
          >
            成果物 ({artifactCount})
            {artifactBadge && (
              <span className="absolute -right-0.5 -top-0.5 h-2 w-2 rounded-full bg-accent-400" />
            )}
          </button>
          <button
            className="btn-ghost !py-1 text-xs"
            disabled={!stoppable}
            title={
              stoppable ? '実行中のジョブは完了を待って中断します' : '実行中ではありません'
            }
            onClick={onStop}
          >
            ⏹ 停止
          </button>
        </div>
      </div>

      {error && <Banner onClose={onDismissError}>{error}</Banner>}

      <div
        ref={scroller}
        className="min-h-0 flex-1 space-y-2 overflow-y-auto rounded-lg border border-ink-600 bg-ink-900 p-3"
      >
        {running && session.plan.tasks.length > 0 && (
          <div className="sticky -top-3 z-10 -mx-1 bg-ink-900/95 py-1 backdrop-blur">
            <PlanCard
              plan={session.plan}
              compact
              busy={busy}
              progress={progress}
              onApprove={onApprove}
              onRequestChanges={() => input.current?.focus()}
            />
          </div>
        )}

        {session.messages.map((message, index) => {
          if (message.role === 'system') return null
          if (message.role === 'event')
            return <EventRow key={index} message={message} />
          if (message.role === 'checkin')
            return (
              <CheckinBubble
                key={index}
                message={message}
                open={index === openIndex}
                busy={busy}
                onAnswer={onCheckin}
              />
            )
          return <Bubble key={index} message={message} />
        })}

        {!running && session.plan.tasks.length > 0 && (
          <PlanCard
            plan={session.plan}
            compact={false}
            busy={busy}
            progress={progress}
            onApprove={onApprove}
            onRequestChanges={() => input.current?.focus()}
          />
        )}

        {busy && <p className="text-xs text-slate-500">Grok が考えています…</p>}
      </div>

      <div className="flex shrink-0 gap-2">
        <textarea
          ref={input}
          className="field h-16 flex-1 resize-none"
          value={draft}
          placeholder="指示を入力（Ctrl+Enter で送信）"
          disabled={busy}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
              event.preventDefault()
              send()
            }
          }}
        />
        <button
          className="btn-primary"
          disabled={busy || !draft.trim()}
          onClick={send}
        >
          送信
        </button>
      </div>
    </section>
  )
}
