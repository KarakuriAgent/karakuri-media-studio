import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError, api, formatDetail } from '../../api'
import type {
  AgentProgress,
  AgentSession,
  AgentSessionCreate,
  AgentSessionSummary,
  JobProgress,
} from '../../types'
import AgentChat from './AgentChat'
import ArtifactPanel from './ArtifactPanel'
import SessionList from './SessionList'
import { AGENT_ACTIVE } from './common'

interface Props {
  /** Latest `type: "agent"` WS frame (AGENT-MODE §5.1). */
  event: AgentProgress | null
  /** Job progress map shared with the generate view (`type: "job"` frames). */
  progress: Record<string, JobProgress>
}

export default function AgentView({ event, progress }: Props) {
  const [sessions, setSessions] = useState<AgentSessionSummary[]>([])
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [session, setSession] = useState<AgentSession | null>(null)
  const [loadingSessions, setLoadingSessions] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [leftCollapsed, setLeftCollapsed] = useState(false)
  const [rightCollapsed, setRightCollapsed] = useState(false)

  const fail = useCallback((caught: unknown) => {
    setError(
      caught instanceof ApiError ? formatDetail(caught.detail) : String(caught),
    )
  }, [])

  const loadSessions = useCallback(async () => {
    setLoadingSessions(true)
    try {
      setSessions(await api.listAgentSessions())
    } catch (caught) {
      fail(caught)
    } finally {
      setLoadingSessions(false)
    }
  }, [fail])

  /** Silent re-sync used by the WS handler and the polling safety net. */
  const syncSession = useCallback(async (id: string) => {
    try {
      setSession(await api.getAgentSession(id))
    } catch {
      /* transient: the poller will try again */
    }
  }, [])

  useEffect(() => {
    void loadSessions()
  }, [loadSessions])

  useEffect(() => {
    if (!sessionId) {
      setSession(null)
      return
    }
    setError(null)
    void api.getAgentSession(sessionId).then(setSession).catch(fail)
  }, [sessionId, fail])

  // WS: agent frames for the open session refresh it (and the list metadata).
  const eventRef = useRef<AgentProgress | null>(null)
  useEffect(() => {
    if (!event || event === eventRef.current) return
    eventRef.current = event
    if (event.session_id !== sessionId) return
    void syncSession(event.session_id)
    if (!AGENT_ACTIVE.includes(event.status)) void loadSessions()
  }, [event, sessionId, syncSession, loadSessions])

  // Safety net while the backend loop runs (mirrors App.tsx's job poller).
  useEffect(() => {
    const live =
      AGENT_ACTIVE.includes(session?.status ?? 'idle') ||
      // approved plan: the loop may not have flipped to "running" yet
      (session?.status === 'planning' && session.plan.approved)
    if (!session || !live) return
    const id = window.setInterval(() => void syncSession(session.id), 5000)
    return () => window.clearInterval(id)
  }, [session, syncSession])

  // ---------------------------------------------------------------- actions

  const create = async (payload: AgentSessionCreate) => {
    const goal = (payload.goal ?? '').trim()
    setBusy(true)
    setError(null)
    try {
      // The goal is sent as the first message so it triggers the opening turn.
      const created = await api.createAgentSession({
        title: goal.slice(0, 40),
        checkin_mode: payload.checkin_mode,
        auto_limit: payload.auto_limit,
      })
      setSessionId(created.id)
      setSession(created)
      await loadSessions()
      if (goal) {
        const reply = await api.sendAgentMessage(created.id, goal)
        setSession(reply.session)
      }
    } catch (caught) {
      fail(caught)
    } finally {
      setBusy(false)
      void loadSessions()
    }
  }

  const run = async (action: () => Promise<AgentSession>) => {
    setBusy(true)
    setError(null)
    try {
      setSession(await action())
    } catch (caught) {
      fail(caught)
      if (sessionId) await syncSession(sessionId)
    } finally {
      setBusy(false)
      void loadSessions()
    }
  }

  const send = (content: string) => {
    if (!sessionId) return
    void run(async () => (await api.sendAgentMessage(sessionId, content)).session)
  }

  const approve = () => {
    if (!sessionId) return
    void run(async () => (await api.approveAgentPlan(sessionId, {})).session)
  }

  const checkin = (answer: string) => {
    if (!sessionId) return
    void run(
      async () => (await api.replyAgentCheckin(sessionId, { content: answer })).session,
    )
  }

  const stop = () => {
    if (!sessionId) return
    void run(() => api.stopAgentSession(sessionId))
  }

  const remove = async (id: string) => {
    if (!window.confirm('このセッションを削除しますか？（作業ディレクトリごと消えます）'))
      return
    setBusy(true)
    try {
      await api.deleteAgentSession(id)
      if (id === sessionId) setSessionId(null)
      await loadSessions()
    } catch (caught) {
      fail(caught)
    } finally {
      setBusy(false)
    }
  }

  const expandArtifacts = useCallback(() => setRightCollapsed(false), [])

  const pending = (session?.plan.tasks ?? [])
    .filter((task) => task.status === 'running')
    .map((task) => ({
      id: task.id || task.label,
      label: task.label,
      percent: Math.round(
        Math.min(
          1,
          Math.max(0, (task.job_id ? progress[task.job_id]?.progress : 0) ?? 0),
        ) * 100,
      ),
    }))

  return (
    <main className="flex min-h-0 flex-1 gap-3 overflow-hidden p-3">
      <SessionList
        sessions={sessions}
        activeId={sessionId}
        loading={loadingSessions}
        busy={busy}
        collapsed={leftCollapsed}
        onToggle={() => setLeftCollapsed((value) => !value)}
        onReload={() => void loadSessions()}
        onSelect={setSessionId}
        onDelete={(id) => void remove(id)}
        onCreate={(payload) => void create(payload)}
      />

      {session ? (
        <AgentChat
          session={session}
          progress={progress}
          busy={busy}
          error={error}
          onDismissError={() => setError(null)}
          onSend={send}
          onApprove={approve}
          onCheckin={checkin}
          onStop={stop}
        />
      ) : (
        <section className="flex min-h-0 min-w-0 flex-1 flex-col items-center justify-center gap-2 rounded-lg border border-ink-600 bg-ink-900 text-center">
          <span className="text-4xl opacity-40">🤖</span>
          <p className="text-sm text-slate-500">
            エージェントに任せる作業をセッションとして始めます
          </p>
          <p className="text-xs text-slate-600">
            左の「＋ 新規セッション」から最初の指示を入力してください
          </p>
        </section>
      )}

      {session && (
        <ArtifactPanel
          sessionId={session.id}
          artifacts={session.artifacts}
          pending={pending}
          collapsed={rightCollapsed}
          onToggle={() => setRightCollapsed((value) => !value)}
          onExpand={expandArtifacts}
        />
      )}
    </main>
  )
}
