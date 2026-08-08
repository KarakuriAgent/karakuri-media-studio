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
      className="flex flex-wrap items-center gap-0.5 rounded-md border border-ink-600 bg-ink-800 p-0.5"
      role="tablist"
      aria-label="キャンバスのタブ"
    >
      {tabs.map((tab) => (
        <button
          key={tab.episodeId ?? 'common'}
          role="tab"
          className={`rounded px-2.5 py-1 text-xs transition-colors ${
            current === tab.episodeId
              ? 'bg-accent-500 text-white'
              : 'text-slate-400 hover:bg-ink-700'
          }`}
          aria-selected={current === tab.episodeId}
          onClick={() => onSelect(tab.episodeId)}
        >
          {tab.label}
        </button>
      ))}
      <button
        className="rounded px-2 py-1 text-xs text-slate-400 transition-colors hover:bg-ink-700"
        title="話を追加"
        aria-label="話を追加"
        disabled={busy}
        onClick={onAddEpisode}
      >
        ＋
      </button>
    </div>
  )
}
