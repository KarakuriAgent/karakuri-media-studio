import type { Health, HealthStatus } from '../types'

function Indicator({ name, status }: { name: string; status?: HealthStatus }) {
  const color =
    status?.status === 'ok'
      ? 'bg-emerald-400'
      : status === undefined
        ? 'bg-slate-600'
        : status.status === 'not_configured'
          ? 'bg-amber-400'
          : 'bg-red-500'
  const text =
    status === undefined
      ? '未確認'
      : status.status === 'ok'
        ? '接続済み'
        : status.status === 'not_configured'
          ? '未設定'
          : 'エラー'
  return (
    <span
      className="flex items-center gap-1.5 rounded-md border border-ink-600 bg-ink-800 px-2 py-1 text-xs"
      title={status?.detail ?? ''}
    >
      <span className={`h-2 w-2 rounded-full ${color}`} />
      <span className="text-slate-300">{name}</span>
      <span className="text-slate-500">{text}</span>
    </span>
  )
}

/** [生成 | エージェント | キャンバス] tab toggle (AGENT-MODE §1 header). */
function ViewTabs({
  view,
  onView,
}: {
  view: 'main' | 'agent' | 'canvas' | 'settings'
  onView: (view: 'main' | 'agent' | 'canvas') => void
}) {
  const tabs: { value: 'main' | 'agent' | 'canvas'; label: string }[] = [
    { value: 'main', label: '生成' },
    { value: 'agent', label: 'エージェント' },
    { value: 'canvas', label: 'キャンバス' },
  ]
  return (
    <div className="flex rounded-md border border-ink-600 bg-ink-800 p-0.5">
      {tabs.map((tab) => (
        <button
          key={tab.value}
          className={`rounded px-2.5 py-1 text-xs transition-colors ${
            view === tab.value
              ? 'bg-accent-500 text-white'
              : 'text-slate-400 hover:bg-ink-700'
          }`}
          onClick={() => onView(tab.value)}
        >
          {tab.label}
        </button>
      ))}
    </div>
  )
}

export default function Header({
  health,
  checking,
  onRefresh,
  onOpenSettings,
  wsConnected,
  view,
  onView,
  showNsfw,
  onShowNsfw,
}: {
  health: Health | null
  checking: boolean
  onRefresh: () => void
  onOpenSettings: () => void
  wsConnected: boolean
  view: 'main' | 'agent' | 'canvas' | 'settings'
  onView: (view: 'main' | 'agent' | 'canvas') => void
  showNsfw: boolean
  onShowNsfw: (show: boolean) => void
}) {
  return (
    <header className="flex flex-wrap items-center gap-x-3 gap-y-2 border-b border-ink-700 bg-ink-800/80 px-4 py-2.5 backdrop-blur">
      <h1 className="text-sm font-semibold tracking-wide text-slate-100">
        Karakuri Media Studio
      </h1>
      <ViewTabs view={view} onView={onView} />
      <div className="ml-2 flex flex-wrap items-center gap-2">
        <Indicator name="ComfyUI" status={health?.comfyui} />
        <Indicator name="Grok" status={health?.grok} />
        {/* kie.ai は使う人だけの機能なので、キーを入れている環境だけ出す
            （未設定のままの環境に「未設定」の橙ランプを常設しない、SPEC §5.2）。 */}
        {health?.kie && health.kie.status !== 'not_configured' && (
          <Indicator name="kie.ai" status={health.kie} />
        )}
        <span
          className="flex items-center gap-1.5 rounded-md border border-ink-600 bg-ink-800 px-2 py-1 text-xs"
          title="進捗配信 WebSocket"
        >
          <span
            className={`h-2 w-2 rounded-full ${wsConnected ? 'bg-emerald-400' : 'bg-slate-600'}`}
          />
          <span className="text-slate-300">進捗WS</span>
        </span>
      </div>
      <div className="ml-auto flex items-center gap-2">
        <label
          className={`chip cursor-pointer select-none !py-1 ${
            showNsfw
              ? 'border-accent-500 bg-accent-500/15 text-accent-400'
              : 'border-ink-600 bg-ink-800 text-slate-400 hover:border-ink-500'
          }`}
          title="オフのあいだは NSFW の作品を一覧から隠します"
        >
          <input
            type="checkbox"
            className="h-3 w-3 accent-accent-500"
            checked={showNsfw}
            onChange={(event) => onShowNsfw(event.target.checked)}
          />
          🫣 NSFW表示
        </label>
        <button className="btn-ghost" onClick={onRefresh} disabled={checking}>
          {checking ? '確認中…' : '接続状態を更新'}
        </button>
        <button className="btn-ghost" onClick={onOpenSettings}>
          設定
        </button>
      </div>
    </header>
  )
}
