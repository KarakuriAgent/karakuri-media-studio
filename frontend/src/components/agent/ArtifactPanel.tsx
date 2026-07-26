import { useEffect, useRef, useState } from 'react'
import { api } from '../../api'
import type { AgentArtifact } from '../../types'
import ArtifactViewer from './ArtifactViewer'
import { ARTIFACT_ICON, shortTime } from './common'

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
  /** Panel must be visible for auto-open to make sense. */
  onExpand: () => void
}

/** Backend fills `url` for outputs; workdir files are served by name. */
function urlOf(sessionId: string, artifact: AgentArtifact): string | null {
  if (artifact.url) return artifact.url
  return artifact.name ? api.agentArtifactUrl(sessionId, artifact.name) : null
}

export default function ArtifactPanel({
  sessionId,
  artifacts,
  pending,
  collapsed,
  onToggle,
  onExpand,
}: Props) {
  const [openIndex, setOpenIndex] = useState<number | null>(null)
  const seen = useRef<{ sessionId: string; count: number }>({ sessionId, count: 0 })
  const bottom = useRef<HTMLDivElement>(null)

  // New artifact arrived (WS): reveal the panel and open media in the viewer.
  useEffect(() => {
    const previous = seen.current
    const fresh = previous.sessionId !== sessionId
    seen.current = { sessionId, count: artifacts.length }
    if (fresh || artifacts.length <= previous.count) {
      if (fresh) setOpenIndex(null)
      return
    }
    onExpand()
    bottom.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
    const latest = artifacts[artifacts.length - 1]
    if (latest.kind === 'image' || latest.kind === 'video') {
      setOpenIndex(artifacts.length - 1)
    }
  }, [artifacts, sessionId, onExpand])

  if (collapsed) {
    return (
      <aside className="flex w-10 shrink-0 flex-col items-center gap-2 rounded-lg border border-ink-700 bg-ink-800/60 py-2">
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

  const open = openIndex != null ? artifacts[openIndex] : null

  return (
    <aside className="flex w-72 shrink-0 flex-col rounded-lg border border-ink-700 bg-ink-800/60">
      <div className="flex items-center gap-2 border-b border-ink-700 px-3 py-2">
        <h2 className="text-xs font-semibold uppercase tracking-wider text-slate-400">
          成果物
        </h2>
        <span className="text-xs text-slate-600">{artifacts.length}</span>
        <button
          className="btn-ghost ml-auto !px-2 !py-1 text-xs"
          onClick={onToggle}
          title="折りたたむ"
        >
          ▶
        </button>
      </div>

      <div className="min-h-0 flex-1 space-y-1.5 overflow-y-auto p-2">
        {artifacts.length === 0 && pending.length === 0 && (
          <p className="px-1 py-3 text-center text-xs text-slate-600">
            まだ成果物がありません
          </p>
        )}

        {artifacts.map((artifact, index) => {
          const url = urlOf(sessionId, artifact)
          const thumb =
            artifact.kind === 'image' || artifact.kind === 'frame' ? url : null
          return (
            <button
              key={`${artifact.ts}-${index}`}
              className="flex w-full items-center gap-2 rounded-md border border-ink-600 bg-ink-800 p-1.5 text-left transition-colors hover:border-ink-500"
              onClick={() => setOpenIndex(index)}
              title={artifact.title || artifact.name}
            >
              <span className="flex h-10 w-14 shrink-0 items-center justify-center overflow-hidden rounded bg-ink-900 text-sm">
                {thumb ? (
                  <img
                    src={thumb}
                    alt={artifact.title}
                    className="h-full w-full object-cover"
                  />
                ) : (
                  ARTIFACT_ICON[artifact.kind]
                )}
              </span>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-xs text-slate-200">
                  {ARTIFACT_ICON[artifact.kind]} {artifact.title || artifact.name}
                </span>
                <span className="block text-[10px] text-slate-600">
                  {shortTime(artifact.ts)}
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
    </aside>
  )
}
