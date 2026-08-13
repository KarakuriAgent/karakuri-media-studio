import { useEffect, useRef, useState, type CSSProperties } from 'react'
import { Clapperboard, ChevronLeft, ChevronRight, X } from 'lucide-react'

import { api } from '../../api'
import type { AgentArtifact } from '../../types'
import { cn } from '@/lib/utils'
import { Badge } from '../ui/badge'
import { Button } from '../ui/button'
import { Progress } from '../ui/progress'
import ArtifactViewer from './ArtifactViewer'
import FrameGrid from './FrameGrid'
import { ARTIFACT_LABEL, ArtifactIcon, shortTime } from './common'
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
  /** 見出しのトグルの意味（狭幅のオーバーレイは折りたたみではなく「閉じる」）。 */
  toggleIcon?: 'collapse' | 'close'
  /** 展開時の幅（リサイズ結果）。折りたたみ中は当てない。 */
  style?: CSSProperties
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
  toggleIcon = 'collapse',
  style,
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
        className={cn(
          'flex w-10 shrink-0 flex-col items-center gap-2 rounded-lg border border-border bg-card py-2 shadow-elevation-1',
          className,
        )}
      >
        <Button
          variant="outline"
          size="icon-xs"
          onClick={onToggle}
          title="成果物パネルを開く"
          aria-label="成果物パネルを開く"
        >
          <ChevronLeft />
        </Button>
        <span className="tnum text-[11px] text-muted-foreground">
          {artifacts.length}
        </span>
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
      className={cn(
        'flex w-72 shrink-0 flex-col rounded-lg border border-border bg-card shadow-elevation-1',
        className,
      )}
      style={style}
    >
      <div className="flex items-center gap-2 border-b border-border px-3 py-2">
        <h2 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
          成果物
        </h2>
        <span className="tnum text-xs text-muted-foreground">
          {artifacts.length}
        </span>
        <Button
          variant="outline"
          size="icon-xs"
          className="ml-auto"
          onClick={onToggle}
          title={toggleIcon === 'collapse' ? '折りたたむ' : '閉じる'}
          aria-label={toggleIcon === 'collapse' ? '折りたたむ' : '閉じる'}
        >
          {toggleIcon === 'collapse' ? <ChevronRight /> : <X />}
        </Button>
      </div>

      <div className="min-h-0 flex-1 space-y-1.5 overflow-y-auto p-2">
        {artifacts.length === 0 && pending.length === 0 && (
          <p className="px-1 py-3 text-center text-xs text-muted-foreground">
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
              className="flex w-full items-center gap-2 rounded-md border border-border bg-surface-sunken p-2 text-left transition-colors hover:border-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
              onClick={() =>
                isFrames ? setOpenFrames(card.key) : setOpenIndex(card.index)
              }
              title={title}
            >
              <span className="flex size-8 shrink-0 items-center justify-center rounded bg-background text-muted-foreground">
                <ArtifactIcon kind={kind} className="size-4" />
              </span>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-xs text-foreground/90">
                  {title}
                </span>
                <span className="mt-0.5 flex items-center gap-1.5">
                  <Badge
                    variant="outline"
                    className="bg-background px-1.5 py-0 text-[11px] font-normal text-muted-foreground"
                  >
                    {chip}
                  </Badge>
                  <span className="tnum text-[11px] text-muted-foreground-subtle">
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
            className="rounded-md border border-border bg-surface-sunken p-1.5"
          >
            <p className="flex items-center gap-1.5 truncate text-xs text-foreground/85">
              <Clapperboard className="size-3.5 shrink-0" />
              {task.label}
            </p>
            <div className="mt-1 flex items-center gap-2">
              <Progress className="flex-1" value={task.percent} />
              <span className="tnum text-[11px] text-muted-foreground">
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
