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
import { currentActivity, isThinking, shouldReplaceSession } from './logic'

interface Props {
  /** Latest `type: "agent"` WS frame (AGENT-MODE §5.1). */
  event: AgentProgress | null
  /** Job progress map shared with the generate view (`type: "job"` frames). */
  progress: Record<string, JobProgress>
  /** オフのあいだは NSFW セッションを一覧から隠す。 */
  showNsfw: boolean
}

export default function AgentView({ event, progress, showNsfw }: Props) {
  const [sessions, setSessions] = useState<AgentSessionSummary[]>([])
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [session, setSession] = useState<AgentSession | null>(null)
  const [loadingSessions, setLoadingSessions] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [leftCollapsed, setLeftCollapsed] = useState(false)
  const [rightCollapsed, setRightCollapsed] = useState(false)
  // 狭幅 (<lg) 用: 一覧はドロワー、成果物は全画面オーバーレイ（AGENT-MODE §1）
  const [sessionsOpen, setSessionsOpen] = useState(false)
  const [artifactsOpen, setArtifactsOpen] = useState(false)
  const [artifactBadge, setArtifactBadge] = useState(false)

  /** 開いているセッション（レース判定用に ref でも持つ）。 */
  const wanted = useRef<string | null>(null)
  /** 最後に投げた取得の世代（古いレスポンスを捨てるための単調カウンタ）。 */
  const generation = useRef(0)

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

  /**
   * 受け取ったセッションを反映する（連打時に古いレスポンスで巻き戻さない）。
   * POST の応答は最新なので、進行中の GET はここで無効化する。
   */
  const applySession = useCallback((next: AgentSession) => {
    generation.current += 1
    if (wanted.current !== next.id) return
    setSession((current) => (shouldReplaceSession(current, next) ? next : current))
  }, [])

  /** Silent re-sync used by the WS handler and the polling safety net. */
  const syncSession = useCallback(
    async (id: string, report = false) => {
      const mine = (generation.current += 1)
      try {
        const next = await api.getAgentSession(id)
        // 追い抜かれた取得・別セッションへの切り替え後の到着は捨てる
        if (mine !== generation.current || wanted.current !== id) return
        setSession((current) =>
          shouldReplaceSession(current, next) ? next : current,
        )
      } catch (caught) {
        if (report) fail(caught)
        /* transient: the poller will try again */
      }
    },
    [fail],
  )

  /** セッションの切り替え（ref も同時に動かしてレース判定を狂わせない）。 */
  const selectSession = useCallback((id: string | null) => {
    wanted.current = id
    generation.current += 1
    setSessionId(id)
  }, [])

  useEffect(() => {
    void loadSessions()
  }, [loadSessions])

  useEffect(() => {
    wanted.current = sessionId
    if (!sessionId) {
      setSession(null)
      return
    }
    setError(null)
    void syncSession(sessionId, true)
  }, [sessionId, syncSession])

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
    setSessionsOpen(false)
    try {
      // `goal` is burnt into the system prompt (SESSION CONTEXT); the same text
      // is then posted as the first message to trigger the opening Grok turn
      // (the backend does not record it twice).
      const created = await api.createAgentSession({
        title: goal.slice(0, 40),
        goal,
        checkin_mode: payload.checkin_mode,
        auto_limit: payload.auto_limit,
      })
      selectSession(created.id)
      applySession(created)
      await loadSessions()
      if (goal) {
        const reply = await api.sendAgentMessage(created.id, goal)
        applySession(reply.session)
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
      applySession(await action())
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
      if (id === sessionId) selectSession(null)
      await loadSessions()
    } catch (caught) {
      fail(caught)
    } finally {
      setBusy(false)
    }
  }

  const expandArtifacts = useCallback(() => setRightCollapsed(false), [])

  // 狭幅では新着成果物で勝手に開かず、ボタンのバッジで知らせるだけ（暴発防止）。
  const seenArtifacts = useRef<{ id: string | null; count: number }>({
    id: null,
    count: 0,
  })
  useEffect(() => {
    const previous = seenArtifacts.current
    const count = session?.artifacts.length ?? 0
    const id = session?.id ?? null
    seenArtifacts.current = { id, count }
    if (id !== previous.id) {
      setArtifactBadge(false)
      return
    }
    if (count > previous.count && !artifactsOpen) setArtifactBadge(true)
  }, [session, artifactsOpen])

  const openArtifacts = () => {
    setArtifactBadge(false)
    setArtifactsOpen(true)
  }

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

  /** NSFW フラグの手動トグル（開いているセッションにも即時反映する）。 */
  const toggleNsfw = async (id: string, nsfw: boolean) => {
    try {
      const updated = await api.setAgentSessionNsfw(id, nsfw)
      setSession((current) =>
        current?.id === id
          ? { ...current, nsfw: updated.nsfw, nsfw_source: updated.nsfw_source }
          : current,
      )
      await loadSessions()
    } catch (caught) {
      fail(caught)
    }
  }

  // 「Grok が考えています…」: busy（このブラウザ発）だけでなく、バックエンドの
  // ループが回すターンも session.thinking / WS フレームで拾う。
  const thinking = session
    ? isThinking({ busy, session, frame: event })
    : busy
  // 実行中の活動（ACP から届く「思考中」「ツール実行中: …」）。
  const activity = session ? currentActivity({ session, frame: event }) : null

  // 一覧からは NSFW を外す（開いているセッションは作業中なので表示を続ける）。
  const visibleSessions = showNsfw
    ? sessions
    : sessions.filter((item) => !item.nsfw)

  const sessionListProps = {
    sessions: visibleSessions,
    activeId: sessionId,
    showNsfw,
    onToggleNsfw: (id: string, nsfw: boolean) => void toggleNsfw(id, nsfw),
    loading: loadingSessions,
    busy,
    onReload: () => void loadSessions(),
    onDelete: (id: string) => void remove(id),
    onCreate: (payload: AgentSessionCreate) => void create(payload),
  }

  return (
    <main className="flex min-h-0 flex-1 gap-3 overflow-hidden p-3">
      {/* デスクトップ: 3 カラムの左列（狭幅ではドロワーに退避する） */}
      <SessionList
        {...sessionListProps}
        className="hidden lg:flex"
        collapsed={leftCollapsed}
        onToggle={() => setLeftCollapsed((value) => !value)}
        onSelect={selectSession}
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
          onOpenSessions={() => setSessionsOpen(true)}
          onOpenArtifacts={openArtifacts}
          artifactCount={session.artifacts.length}
          artifactBadge={artifactBadge}
          onToggleNsfw={(nsfw) => void toggleNsfw(session.id, nsfw)}
          showNsfw={showNsfw}
          thinking={thinking}
          activity={activity}
        />
      ) : (
        <section className="flex min-h-0 min-w-0 flex-1 flex-col items-center justify-center gap-2 rounded-lg border border-ink-600 bg-ink-900 p-4 text-center">
          <span className="text-4xl opacity-40">🤖</span>
          <p className="text-sm text-slate-500">
            エージェントに任せる作業をセッションとして始めます
          </p>
          <p className="hidden text-xs text-slate-600 lg:block">
            左の「＋ 新規セッション」から最初の指示を入力してください
          </p>
          <button
            className="btn-primary mt-1 text-xs lg:hidden"
            onClick={() => setSessionsOpen(true)}
          >
            セッション一覧を開く
          </button>
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
          className="hidden lg:flex"
        />
      )}

      {/* 狭幅: セッション一覧は左ドロワー（背景タップで閉じる） */}
      {sessionsOpen && (
        <div className="fixed inset-0 z-40 flex lg:hidden">
          <div
            className="absolute inset-0 bg-black/70"
            onClick={() => setSessionsOpen(false)}
          />
          <SessionList
            {...sessionListProps}
            className="relative z-10 h-full !w-72 max-w-[85%] rounded-none border-y-0 border-l-0"
            collapsed={false}
            onToggle={() => setSessionsOpen(false)}
            onSelect={(id) => {
              selectSession(id)
              setSessionsOpen(false)
            }}
          />
        </div>
      )}

      {/* 狭幅: 成果物パネルは全画面オーバーレイ（AGENT-MODE §1） */}
      {session && artifactsOpen && (
        <div className="fixed inset-0 z-40 flex bg-ink-900 p-2 lg:hidden">
          <ArtifactPanel
            sessionId={session.id}
            artifacts={session.artifacts}
            pending={pending}
            collapsed={false}
            onToggle={() => setArtifactsOpen(false)}
            onExpand={() => setArtifactsOpen(true)}
            className="!w-full"
            toggleIcon="✕"
          />
        </div>
      )}
    </main>
  )
}
