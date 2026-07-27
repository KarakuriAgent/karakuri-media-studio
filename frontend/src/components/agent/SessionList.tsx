import { useState } from 'react'
import type {
  AgentCheckinMode,
  AgentSessionCreate,
  AgentSessionSummary,
} from '../../types'
import { AgentStatusBadge, CHECKIN_LABEL, shortTime } from './common'

interface Props {
  sessions: AgentSessionSummary[]
  activeId: string | null
  loading: boolean
  busy: boolean
  collapsed: boolean
  onToggle: () => void
  onReload: () => void
  onSelect: (id: string) => void
  onDelete: (id: string) => void
  onCreate: (payload: AgentSessionCreate) => void
  /** Layout override: desktop column vs. mobile drawer (AGENT-MODE §1). */
  className?: string
}

const MODES: AgentCheckinMode[] = ['every_job', 'milestone', 'auto']

export default function SessionList({
  sessions,
  activeId,
  loading,
  busy,
  collapsed,
  onToggle,
  onReload,
  onSelect,
  onDelete,
  onCreate,
  className = '',
}: Props) {
  const [creating, setCreating] = useState(false)
  const [goal, setGoal] = useState('')
  const [mode, setMode] = useState<AgentCheckinMode>('milestone')
  const [autoLimit, setAutoLimit] = useState(5)

  if (collapsed) {
    return (
      <aside
        className={`flex w-10 shrink-0 flex-col items-center gap-2 rounded-lg border border-ink-700 bg-ink-800/60 py-2 ${className}`}
      >
        <button className="btn-ghost !px-2 !py-1 text-xs" onClick={onToggle} title="セッション一覧を開く">
          ▶
        </button>
        <span className="text-[10px] text-slate-500">{sessions.length}</span>
      </aside>
    )
  }

  const start = () => {
    onCreate({
      goal: goal.trim(),
      checkin_mode: mode,
      auto_limit: autoLimit,
    })
    setGoal('')
    setCreating(false)
  }

  return (
    <aside
      className={`flex w-64 shrink-0 flex-col rounded-lg border border-ink-700 bg-ink-800/60 ${className}`}
    >
      <div className="flex items-center gap-2 border-b border-ink-700 px-3 py-2">
        <h2 className="text-xs font-semibold uppercase tracking-wider text-slate-400">
          セッション
        </h2>
        <span className="text-xs text-slate-600">{sessions.length}</span>
        <div className="ml-auto flex items-center gap-1">
          <button
            className="btn-ghost !px-2 !py-1 text-xs"
            onClick={onReload}
            disabled={loading}
            title="一覧を更新"
          >
            {loading ? '…' : '⟳'}
          </button>
          <button
            className="btn-ghost !px-2 !py-1 text-xs"
            onClick={onToggle}
            title="折りたたむ"
          >
            ◀
          </button>
        </div>
      </div>

      <div className="border-b border-ink-700 p-2">
        {!creating ? (
          <button
            className="btn-primary w-full !py-1.5 text-xs"
            onClick={() => setCreating(true)}
          >
            ＋ 新規セッション
          </button>
        ) : (
          <div className="space-y-2">
            <div>
              <label className="label">最初の指示</label>
              <textarea
                className="field h-20 resize-none text-xs"
                value={goal}
                autoFocus
                placeholder="例: かおりのダンス動画を雰囲気違いで3本"
                onChange={(event) => setGoal(event.target.value)}
              />
            </div>
            <div>
              <label className="label">チェックイン</label>
              <select
                className="field text-xs"
                value={mode}
                onChange={(event) =>
                  setMode(event.target.value as AgentCheckinMode)
                }
              >
                {MODES.map((value) => (
                  <option key={value} value={value}>
                    {CHECKIN_LABEL[value]}
                  </option>
                ))}
              </select>
            </div>
            {mode === 'auto' && (
              <div>
                <label className="label">上限本数（自走時は必須）</label>
                <input
                  className="field text-xs"
                  type="number"
                  min={1}
                  max={50}
                  value={autoLimit}
                  onChange={(event) =>
                    setAutoLimit(
                      Math.min(50, Math.max(1, Number(event.target.value) || 1)),
                    )
                  }
                />
              </div>
            )}
            <div className="flex gap-2">
              <button
                className="btn-primary flex-1 !py-1.5 text-xs"
                disabled={busy || !goal.trim()}
                onClick={start}
              >
                開始
              </button>
              <button
                className="btn-ghost !py-1.5 text-xs"
                onClick={() => setCreating(false)}
              >
                取消
              </button>
            </div>
          </div>
        )}
      </div>

      <div className="min-h-0 flex-1 space-y-1 overflow-y-auto p-2">
        {sessions.length === 0 && (
          <p className="px-1 py-3 text-center text-xs text-slate-600">
            まだセッションがありません
          </p>
        )}
        {sessions.map((session) => (
          <div
            key={session.id}
            className={`group rounded-md border px-2 py-1.5 transition-colors ${
              session.id === activeId
                ? 'border-accent-500 bg-accent-500/10'
                : 'border-ink-600 bg-ink-800 hover:border-ink-500'
            }`}
          >
            <button
              className="block w-full text-left"
              onClick={() => onSelect(session.id)}
            >
              <span className="block truncate text-xs text-slate-200">
                {session.title || '(無題)'}
              </span>
              <span className="mt-1 flex items-center gap-1.5">
                <AgentStatusBadge status={session.status} />
                <span className="text-[10px] text-slate-500">
                  {shortTime(session.created_at)}
                </span>
              </span>
              <span className="mt-1 block text-[10px] text-slate-600">
                タスク {session.task_count} / 成果物 {session.artifact_count} ／{' '}
                {CHECKIN_LABEL[session.checkin_mode]}
              </span>
            </button>
            <button
              className="mt-1 text-[10px] text-slate-600 opacity-0 transition-opacity hover:text-red-400 group-hover:opacity-100"
              onClick={() => onDelete(session.id)}
            >
              削除
            </button>
          </div>
        ))}
      </div>
    </aside>
  )
}
