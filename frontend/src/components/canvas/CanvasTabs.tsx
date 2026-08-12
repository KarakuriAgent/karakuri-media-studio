import { Plus } from 'lucide-react'

import { Button } from '../ui/button'
import type { CanvasTab } from './logic'

/**
 * 盤面のタブ（`[作品共通] [第1話] … [＋]`）。
 *
 * 見た目はスタジオ表示のタブに合わせる。話そのものはスタジオのデータなので、
 * 「＋」で作るのも `POST /api/studio/projects/{id}/episodes`（親が投げる）。
 */
export default function CanvasTabs({
  tabs,
  current,
  busy,
  onSelect,
  onAddEpisode,
}: {
  tabs: CanvasTab[]
  /** 開いているタブ（null = 作品共通）。 */
  current: string | null
  busy: boolean
  onSelect: (episodeId: string | null) => void
  /** 「＋」: 話を 1 つ足して、そのタブを開く。 */
  onAddEpisode: () => void
}) {
  return (
    <div
      className="flex flex-wrap items-center gap-0.5 rounded-md border border-border bg-card p-0.5"
      role="tablist"
      aria-label="キャンバスのタブ"
    >
      {tabs.map((tab) => (
        <button
          key={tab.episodeId ?? 'common'}
          role="tab"
          className={`rounded-sm px-2.5 py-1 text-xs transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50 ${
            current === tab.episodeId
              ? 'bg-primary text-primary-foreground'
              : 'text-muted-foreground hover:bg-secondary hover:text-foreground'
          }`}
          aria-selected={current === tab.episodeId}
          onClick={() => onSelect(tab.episodeId)}
        >
          {tab.label}
        </button>
      ))}
      <Button
        variant="ghost"
        size="icon-xs"
        title="話を追加"
        aria-label="話を追加"
        disabled={busy}
        onClick={onAddEpisode}
      >
        <Plus aria-hidden="true" />
      </Button>
    </div>
  )
}
