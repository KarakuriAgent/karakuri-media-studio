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

export default function Header({
  health,
  checking,
  onRefresh,
  onOpenSettings,
  wsConnected,
}: {
  health: Health | null
  checking: boolean
  onRefresh: () => void
  onOpenSettings: () => void
  wsConnected: boolean
}) {
  return (
    <header className="flex items-center gap-3 border-b border-ink-700 bg-ink-800/80 px-4 py-2.5 backdrop-blur">
      <h1 className="text-sm font-semibold tracking-wide text-slate-100">
        Video Studio
      </h1>
      <div className="ml-2 flex flex-wrap items-center gap-2">
        <Indicator name="ComfyUI" status={health?.comfyui} />
        <Indicator name="Grok" status={health?.grok} />
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
