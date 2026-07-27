import { useEffect, useRef, useState } from 'react'
import { api } from '../../api'
import type { AgentArtifact } from '../../types'
import ArtifactViewer from './ArtifactViewer'
import FrameGrid from './FrameGrid'
import { ARTIFACT_ICON, ARTIFACT_LABEL, shortTime } from './common'
import { groupArtifacts } from './logic'

interface PendingTask {
  id: string
  label: string
  percent: number
}

interface Props {
  sessionId: string
  artifacts: AgentArtifact[]
  pending: PendingTask[]
  collapsed: boolean
  onToggle: () => void
  /** 新着があればパネル自体は開く（ビューアは開かない）。 */
  onExpand: () => void
  /** Layout override: desktop column vs. mobile full-screen overlay (§1). */
  className?: string
  /** Icon of the header toggle (mobile overlay closes instead of collapsing). */
  toggleIcon?: string
}

/** Backend fills `url` for outputs; workdir files are served by name. */
function urlOf(sessionId: string, artifact: AgentArtifact): string | null {
  if (artifact.url) return artifact.url
  return artifact.name ? api.agentArtifactUrl(sessionId, artifact.name) : null
}

/**
 * 成果物パネル（AGENT-MODE §1）。
 *
 * NSFW が混ざるアプリなので、カードはサムネイルを持たない「リンクカード」
 * （アイコン + タイトル + 種別チップ + 時刻）。中身はタップして初めて見える。
 * 新着でビューアを勝手に開くことはしない（パネルの展開とバッジだけで知らせる）。
 */
export default function ArtifactPanel({
  sessionId,
  artifacts,
  pending,
  collapsed,
  onToggle,
  onExpand,
  className = '',
  toggleIcon = '▶',
}: Props) {
  /** 単体ビューアで開いている成果物（artifacts 配列の位置）。 */
  const [openIndex, setOpenIndex] = useState<number | null>(null)
  /** フレーム検分のまとめカードで開いているグループ。 */
  const [openFrames, setOpenFrames] = useState<string | null>(null)
  const seen = useRef<{ sessionId: string; count: number }>({ sessionId, count: 0 })
  const bottom = useRef<HTMLDivElement>(null)

  // 新着（WS）ではパネルを開いて末尾までスクロールするだけに留める。
  useEffect(() => {
    const previous = seen.current
    const fresh = previous.sessionId !== sessionId
    seen.current = { sessionId, count: artifacts.length }
    if (fresh) {
      setOpenIndex(null)
      setOpenFrames(null)
    }
    if (fresh || artifacts.length <= previous.count) return
    onExpand()
    bottom.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
  }, [artifacts, sessionId, onExpand])

  if (collapsed) {
    return (
      <aside
        className={`flex w-10 shrink-0 flex-col items-center gap-2 rounded-lg border border-ink-700 bg-ink-800/60 py-2 ${className}`}
      >
        <button
          className="btn-ghost !px-2 !py-1 text-xs"
          onClick={onToggle}
          title="成果物パネルを開く"
        >
          ◀
        </button>
        <span className="text-[10px] text-slate-500">{artifacts.length}</span>
      </aside>
    )
  }

  const cards = groupArtifacts(artifacts)
  const open = openIndex != null ? artifacts[openIndex] : null
  const frames = cards.find(
    (card) => card.type === 'frames' && card.key === openFrames,
  )

  return (
    <aside
      className={`flex w-72 shrink-0 flex-col rounded-lg border border-ink-700 bg-ink-800/60 ${className}`}
    >
      <div className="flex items-center gap-2 border-b border-ink-700 px-3 py-2">
        <h2 className="text-xs font-semibold uppercase tracking-wider text-slate-400">
          成果物
        </h2>
        <span className="text-xs text-slate-600">{artifacts.length}</span>
        <button
          className="btn-ghost ml-auto !px-2 !py-1 text-xs"
          onClick={onToggle}
          title={toggleIcon === '▶' ? '折りたたむ' : '閉じる'}
        >
          {toggleIcon}
        </button>
      </div>

      <div className="min-h-0 flex-1 space-y-1.5 overflow-y-auto p-2">
        {artifacts.length === 0 && pending.length === 0 && (
          <p className="px-1 py-3 text-center text-xs text-slate-600">
            まだ成果物がありません
          </p>
        )}

        {cards.map((card) => {
          const isFrames = card.type === 'frames'
          const kind = isFrames ? 'frame' : card.artifact.kind
          const title = isFrames
            ? card.title
            : card.artifact.title || card.artifact.name
          const chip = isFrames
            ? `${ARTIFACT_LABEL.frame} ${card.frames.length}`
            : ARTIFACT_LABEL[kind]
          return (
            <button
              key={card.key}
              className="flex w-full items-center gap-2 rounded-md border border-ink-600 bg-ink-800 p-2 text-left transition-colors hover:border-accent-500"
              onClick={() =>
                isFrames ? setOpenFrames(card.key) : setOpenIndex(card.index)
              }
              title={title}
            >
              <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded bg-ink-900 text-sm">
                {ARTIFACT_ICON[kind]}
              </span>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-xs text-slate-200">
                  {title}
                </span>
                <span className="mt-0.5 flex items-center gap-1.5">
                  <span className="chip !border-ink-600 !bg-ink-900 !px-1.5 !py-0 text-[10px] text-slate-400">
                    {chip}
                  </span>
                  <span className="text-[10px] text-slate-600">
                    {shortTime(isFrames ? card.ts : card.artifact.ts)}
                  </span>
                </span>
              </span>
            </button>
          )
        })}

        {pending.map((task) => (
          <div
            key={task.id}
            className="rounded-md border border-ink-600 bg-ink-800 p-1.5"
          >
            <p className="truncate text-xs text-slate-300">🎬 {task.label}</p>
            <div className="mt-1 flex items-center gap-2">
              <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-ink-600">
                <div
                  className="h-full bg-accent-500 transition-all"
                  style={{ width: `${task.percent}%` }}
                />
              </div>
              <span className="text-[10px] tabular-nums text-slate-400">
                {task.percent}%
              </span>
            </div>
          </div>
        ))}
        <div ref={bottom} />
      </div>

      {open && (
        <ArtifactViewer
          artifact={open}
          url={urlOf(sessionId, open)}
          onClose={() => setOpenIndex(null)}
        />
      )}

      {frames?.type === 'frames' && (
        <FrameGrid
          title={frames.title}
          frames={frames.frames.map((entry) => entry.artifact)}
          urlOf={(artifact) => urlOf(sessionId, artifact)}
          onClose={() => setOpenFrames(null)}
        />
      )}
    </aside>
  )
}
