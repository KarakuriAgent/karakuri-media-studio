import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError, api, formatDetail } from '../../api'
import type {
  CanvasNode,
  CanvasNodeKind,
  CanvasProgress,
  CanvasProject,
  CanvasProjectDetail,
  CanvasViewport,
  Options,
} from '../../types'
import { Banner } from '../ui'
import CanvasBoard from './CanvasBoard'
import CanvasChat from './CanvasChat'
import CardEditor from './CardEditor'
import ProjectPicker from './ProjectPicker'
import {
  KIND_LABEL,
  defaultDataFor,
  nextGenerating,
  shouldReplaceProject,
} from './logic'

interface Props {
  /** Latest `type: "canvas"` WS frame. */
  event: CanvasProgress | null
}

/** md 以上か（未満ではリサイズを無効にし、チャットをシートに切り替える）。 */
function useIsDesktop(): boolean {
  const [desktop, setDesktop] = useState(true)
  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return
    const query = window.matchMedia('(min-width: 768px)')
    const apply = () => setDesktop(query.matches)
    apply()
    query.addEventListener('change', apply)
    return () => query.removeEventListener('change', apply)
  }, [])
  return desktop
}

export default function CanvasView({ event }: Props) {
  const [projects, setProjects] = useState<CanvasProject[]>([])
  const [projectId, setProjectId] = useState<string | null>(null)
  const [project, setProject] = useState<CanvasProjectDetail | null>(null)
  const [options, setOptions] = useState<Options | null>(null)
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [editing, setEditing] = useState<CanvasNode | null>(null)
  // 狭幅 (<md): ボードとチャットをトグルで切り替える
  const [pane, setPane] = useState<'board' | 'chat'>('board')
  // 狭幅のチャットシートを全画面まで引き上げているか
  const [sheetFull, setSheetFull] = useState(false)
  // 送信中（このブラウザ発）。バックエンドのターンは project.thinking で見る。
  const [sending, setSending] = useState(false)
  // バックグラウンドの生成（job_started 〜 job_done/job_failed）が走っているか。
  const [generating, setGenerating] = useState(false)
  const desktop = useIsDesktop()

  /** 開いているプロジェクト（レース判定用に ref でも持つ）。 */
  const wanted = useRef<string | null>(null)
  /** 最後に投げた取得の世代（古いレスポンスを捨てるための単調カウンタ）。 */
  const generation = useRef(0)

  const fail = useCallback((caught: unknown) => {
    setError(
      caught instanceof ApiError ? formatDetail(caught.detail) : String(caught),
    )
  }, [])

  const loadProjects = useCallback(async () => {
    setLoading(true)
    try {
      setProjects(await api.listCanvasProjects())
    } catch (caught) {
      fail(caught)
    } finally {
      setLoading(false)
    }
  }, [fail])

  /** Silent re-sync used by the WS handler and the polling safety net. */
  const syncProject = useCallback(
    async (id: string, report = false) => {
      const mine = (generation.current += 1)
      try {
        const next = await api.getCanvasProject(id)
        // 追い抜かれた取得・別キャンバスへの切り替え後の到着は捨てる
        if (mine !== generation.current || wanted.current !== id) return
        setProject((current) =>
          shouldReplaceProject(current, next) ? next : current,
        )
      } catch (caught) {
        if (report) fail(caught)
        /* transient: the poller will try again */
      }
    },
    [fail],
  )

  const selectProject = useCallback((id: string | null) => {
    wanted.current = id
    generation.current += 1
    setGenerating(false)
    setProjectId(id)
  }, [])

  useEffect(() => {
    void loadProjects()
    // model カードのワークフロー・LoRA 選択に使う（生成フォームと同じ選択肢）。
    api.options().then(setOptions).catch(() => setOptions(null))
  }, [loadProjects])

  useEffect(() => {
    wanted.current = projectId
    if (!projectId) {
      setProject(null)
      return
    }
    setError(null)
    void syncProject(projectId, true)
  }, [projectId, syncProject])

  // WS: 開いているキャンバスの canvas フレームで取り直す。
  const eventRef = useRef<CanvasProgress | null>(null)
  useEffect(() => {
    if (!event || event === eventRef.current) return
    eventRef.current = event
    if (event.project_id !== projectId) return
    setGenerating((current) => nextGenerating(current, event))
    void syncProject(event.project_id)
  }, [event, projectId, syncProject])

  /** エージェントのターンが回っている（送信中 + バックエンドの thinking）。 */
  const agentBusy = sending || Boolean(project?.thinking)

  // 安全網: ターンか生成が走っているあいだだけポーリングする（WS の取りこぼし
  // 対策）。generate はターンが終わったあともバックグラウンドで続くので、
  // job_started 〜 job_done のあいだも回す。
  useEffect(() => {
    if (!projectId || (!agentBusy && !generating)) return
    const id = window.setInterval(() => void syncProject(projectId), 5000)
    return () => window.clearInterval(id)
  }, [agentBusy, generating, projectId, syncProject])

  // ---------------------------------------------------------------- カード操作

  /** 取得済みのプロジェクトに 1 枚ぶんの変更を反映する（PATCH の応答で確定）。 */
  const mergeNode = useCallback((node: CanvasNode) => {
    setProject((current) => {
      if (!current || current.id !== node.project_id) return current
      const exists = current.nodes.some((item) => item.id === node.id)
      return {
        ...current,
        nodes: exists
          ? current.nodes.map((item) => (item.id === node.id ? node : item))
          : [...current.nodes, node],
      }
    })
  }, [])

  const dropNode = useCallback((nodeId: string) => {
    setProject((current) =>
      current
        ? { ...current, nodes: current.nodes.filter((item) => item.id !== nodeId) }
        : current,
    )
  }, [])

  const createNode = async (kind: CanvasNodeKind, x: number, y: number) => {
    if (!projectId) return
    setBusy(true)
    setError(null)
    try {
      const node = await api.createCanvasNode(projectId, {
        kind,
        title: KIND_LABEL[kind],
        data: defaultDataFor(kind) as unknown as Record<string, unknown>,
        x,
        y,
      })
      mergeNode(node)
      // 作った直後は中身が空なので、そのまま編集モーダルを開く。
      setEditing(node)
    } catch (caught) {
      fail(caught)
    } finally {
      setBusy(false)
    }
  }

  /** 移動・リサイズは楽観更新 + PATCH（WS を待たない）。 */
  const patchNode = async (
    nodeId: string,
    patch: Parameters<typeof api.updateCanvasNode>[2],
    optimistic?: Partial<CanvasNode>,
  ) => {
    if (!projectId) return
    if (optimistic) {
      setProject((current) =>
        current
          ? {
              ...current,
              nodes: current.nodes.map((item) =>
                item.id === nodeId ? { ...item, ...optimistic } : item,
              ),
            }
          : current,
      )
    }
    try {
      mergeNode(await api.updateCanvasNode(projectId, nodeId, patch))
    } catch (caught) {
      fail(caught)
      if (projectId) await syncProject(projectId)
    }
  }

  const saveNode = async (patch: { title: string; data: Record<string, unknown> }) => {
    if (!projectId || !editing) return
    setBusy(true)
    setError(null)
    try {
      mergeNode(await api.updateCanvasNode(projectId, editing.id, patch))
      setEditing(null)
    } catch (caught) {
      fail(caught)
    } finally {
      setBusy(false)
    }
  }

  const removeNode = async (nodeId: string) => {
    if (!projectId) return
    if (!window.confirm('このカードを削除しますか？')) return
    setBusy(true)
    try {
      await api.deleteCanvasNode(projectId, nodeId)
      dropNode(nodeId)
      setEditing(null)
    } catch (caught) {
      fail(caught)
    } finally {
      setBusy(false)
    }
  }

  // ------------------------------------------------------------ キャンバス操作

  const createProject = async (title: string) => {
    setBusy(true)
    setError(null)
    try {
      const created = await api.createCanvasProject(title)
      await loadProjects()
      selectProject(created.id)
    } catch (caught) {
      fail(caught)
    } finally {
      setBusy(false)
    }
  }

  const renameProject = async (id: string, title: string) => {
    try {
      await api.updateCanvasProject(id, { title })
      setProject((current) => (current?.id === id ? { ...current, title } : current))
      await loadProjects()
    } catch (caught) {
      fail(caught)
    }
  }

  const removeProject = async (id: string) => {
    if (!window.confirm('このキャンバスを削除しますか？（カードと会話ごと消えます）'))
      return
    setBusy(true)
    try {
      await api.deleteCanvasProject(id)
      if (id === projectId) selectProject(null)
      await loadProjects()
    } catch (caught) {
      fail(caught)
    } finally {
      setBusy(false)
    }
  }

  // ------------------------------------------------------------------ チャット

  const sendMessage = async (content: string) => {
    if (!projectId) return
    setSending(true)
    setError(null)
    try {
      const reply = await api.sendCanvasMessage(projectId, content)
      // 応答にはターン後のカード・会話がまるごと入っている。
      if (wanted.current === projectId) {
        generation.current += 1
        setProject(reply.project)
      }
    } catch (caught) {
      fail(caught)
      await syncProject(projectId)
    } finally {
      setSending(false)
    }
  }

  const stopAgent = async () => {
    if (!projectId) return
    try {
      await api.stopCanvasAgent(projectId)
    } catch (caught) {
      fail(caught)
    }
  }

  const saveViewport = useCallback(
    (viewport: CanvasViewport) => {
      if (!wanted.current) return
      void api
        .updateCanvasProject(wanted.current, { viewport })
        .catch(() => {
          /* 表示位置の保存に失敗しても作業は続けられる */
        })
    },
    [],
  )

  // ---------------------------------------------------------------- レイアウト

  const boardVisible = desktop || pane === 'board'
  const chatVisible = desktop || pane === 'chat'

  return (
    <main className="flex min-h-0 flex-1 flex-col gap-2 overflow-hidden p-3">
      <div className="flex flex-wrap items-center gap-2">
        <ProjectPicker
          projects={projects}
          activeId={projectId}
          loading={loading}
          busy={busy}
          onSelect={selectProject}
          onCreate={(title) => void createProject(title)}
          onRename={(id, title) => void renameProject(id, title)}
          onDelete={(id) => void removeProject(id)}
          onReload={() => void loadProjects()}
        />
        {/* 狭幅: ボード / チャットの切り替え */}
        {project && (
          <div className="ml-auto flex rounded-md border border-ink-600 bg-ink-800 p-0.5 md:hidden">
            {(['board', 'chat'] as const).map((value) => (
              <button
                key={value}
                className={`rounded px-2.5 py-1 text-xs transition-colors ${
                  pane === value
                    ? 'bg-accent-500 text-white'
                    : 'text-slate-400 hover:bg-ink-700'
                }`}
                onClick={() => setPane(value)}
              >
                {value === 'board' ? 'ボード' : 'チャット'}
              </button>
            ))}
          </div>
        )}
      </div>

      {error && <Banner onClose={() => setError(null)}>{error}</Banner>}

      {project ? (
        <div className="flex min-h-0 flex-1 gap-3">
          <section
            className={`min-h-0 min-w-0 flex-1 overflow-hidden rounded-lg border border-ink-600 bg-ink-900 ${
              boardVisible ? 'flex' : 'hidden'
            }`}
          >
            <CanvasBoard
              key={project.id}
              project={project}
              resizable={desktop}
              onCreateNode={(kind, x, y) => void createNode(kind, x, y)}
              onMoveNode={(id, x, y) => void patchNode(id, { x, y }, { x, y })}
              onResizeNode={(id, w, h) => void patchNode(id, { w, h }, { w, h })}
              onEditNode={setEditing}
              onViewport={saveViewport}
            />
          </section>

          {/* チャット: md 以上は右ドック、未満は下からのシート（全画面まで可） */}
          {chatVisible && (
            <aside
              className={
                desktop
                  ? 'flex min-h-0 w-80 shrink-0 flex-col gap-2 rounded-lg border border-ink-600 bg-ink-900 p-3'
                  : `fixed inset-x-0 bottom-0 z-30 flex flex-col gap-2 border-t border-ink-600 bg-ink-900 p-3 ${
                      sheetFull ? 'top-0' : 'top-1/4'
                    }`
              }
            >
              {!desktop && (
                <div className="flex shrink-0 items-center gap-2">
                  <button
                    className="btn-ghost !py-1 text-xs"
                    onClick={() => setSheetFull((current) => !current)}
                  >
                    {sheetFull ? '↓ 縮める' : '↑ 全画面'}
                  </button>
                  <button
                    className="btn-ghost ml-auto !py-1 text-xs"
                    onClick={() => setPane('board')}
                  >
                    ボードへ戻る
                  </button>
                </div>
              )}
              <CanvasChat
                project={project}
                busy={agentBusy}
                onSend={(content) => void sendMessage(content)}
                onStop={() => void stopAgent()}
              />
            </aside>
          )}
        </div>
      ) : (
        <section className="flex min-h-0 flex-1 flex-col items-center justify-center gap-2 rounded-lg border border-ink-600 bg-ink-900 p-4 text-center">
          <span className="text-4xl opacity-40">🗂</span>
          <p className="text-sm text-slate-500">
            動画制作の素材と文脈をカードで並べるキャンバスです
          </p>
          <p className="text-xs text-slate-600">
            上の「＋ 新規キャンバス」から始めてください
          </p>
        </section>
      )}

      {editing && (
        <CardEditor
          key={editing.id}
          node={editing}
          options={options}
          busy={busy}
          error={error}
          onClose={() => setEditing(null)}
          onSave={(patch) => void saveNode(patch)}
          onDelete={() => void removeNode(editing.id)}
        />
      )}
    </main>
  )
}
