import { useEffect, useState } from 'react'
import type { AgentArtifact } from '../../types'
import ArtifactViewer from './ArtifactViewer'
import { ARTIFACT_ICON, shortTime } from './common'

interface Props {
  /** まとめカードの見出し（例: 「① 明るいスタジオ フレーム検分 (5枚)」）。 */
  title: string
  frames: AgentArtifact[]
  urlOf: (artifact: AgentArtifact) => string | null
  onClose: () => void
}

/**
 * フレーム検分の全画面グリッド（AGENT-MODE §1）。
 *
 * 成果物パネルのカードはサムネイルを出さないので、中身が見えるのはここから。
 * 1 枚タップで単体の全画面表示へ進み、Esc / 戻るで 1 階層ずつ戻る。
 */
export default function FrameGrid({ title, frames, urlOf, onClose }: Props) {
  const [selected, setSelected] = useState<number | null>(null)

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      if (selected != null) setSelected(null)
      else onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [selected, onClose])

  const open = selected != null ? frames[selected] : null

  return (
    <div className="fixed inset-0 z-50 flex flex-col bg-ink-900/98">
      <div className="flex items-center gap-2 border-b border-ink-700 px-3 py-2">
        <h2 className="min-w-0 flex-1 truncate text-sm text-slate-200">
          {ARTIFACT_ICON.frame} {title}
        </h2>
        <button
          className="btn-ghost !px-2 !py-1 text-xs"
          onClick={onClose}
          title="閉じる (Esc)"
        >
          ✕
        </button>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto p-2">
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4">
          {frames.map((frame, index) => {
            const url = urlOf(frame)
            return (
              <button
                key={`${frame.ts}-${frame.name || index}`}
                className="overflow-hidden rounded-md border border-ink-600 bg-ink-800 text-left transition-colors hover:border-accent-500"
                onClick={() => setSelected(index)}
                title={frame.title || frame.name}
              >
                {url && (
                  <img
                    src={url}
                    alt={frame.title || frame.name}
                    className="aspect-square w-full object-cover"
                    loading="lazy"
                  />
                )}
                <span className="block truncate px-1.5 py-1 text-[10px] text-slate-400">
                  {index + 1}. {shortTime(frame.ts)}
                </span>
              </button>
            )
          })}
        </div>
      </div>

      {open && (
        <ArtifactViewer
          artifact={open}
          url={urlOf(open)}
          onClose={() => setSelected(null)}
        />
      )}
    </div>
  )
}
