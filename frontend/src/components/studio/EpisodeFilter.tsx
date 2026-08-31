import type { StudioEpisode } from '../../types'
import { episodeLabel } from './studio'

/** 「すべて」を指す値（話 id と混ざらないように文字列で持つ）。 */
export const ALL_EPISODES = 'all'

/**
 * 脚本・制作タブの上に出す話の絞り込み（`[すべて][第1話][第2話]…`）。
 *
 * 押すと親が `GET /api/studio/projects/{id}?episode_id=…` で取り直すので、選択を
 * 上げるだけ。話が 1 つも無ければ何も出さない（絞る先が無いため）。
 */
export default function EpisodeFilter({
  episodes,
  value,
  busy,
  onChange,
}: {
  episodes: StudioEpisode[]
  /** 選んでいる話（`ALL_EPISODES` = 絞り込みなし）。 */
  value: string
  busy: boolean
  onChange: (value: string) => void
}) {
  if (episodes.length === 0) return null
  const tabs = [
    { id: ALL_EPISODES, label: 'すべて' },
    ...episodes.map((episode, index) => ({
      id: episode.id,
      label: episodeLabel(episode, index),
    })),
  ]
  return (
    <div
      className="flex flex-wrap items-center gap-0.5 rounded-md border border-border bg-card p-0.5"
      role="tablist"
      aria-label="話で絞り込む"
    >
      {tabs.map((tab) => (
        <button
          key={tab.id}
          role="tab"
          className={`rounded-sm px-2.5 py-1 text-xs transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50 ${
            value === tab.id
              ? 'bg-primary text-primary-foreground'
              : 'text-muted-foreground hover:bg-secondary hover:text-foreground'
          }`}
          aria-selected={value === tab.id}
          disabled={busy}
          onClick={() => onChange(tab.id)}
        >
          {tab.label}
        </button>
      ))}
    </div>
  )
}
