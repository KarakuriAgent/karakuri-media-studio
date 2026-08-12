import { EyeOff, RefreshCw, Settings as SettingsIcon } from 'lucide-react'

import type { Health, HealthStatus } from '../types'
import { Button } from './ui/button'
import { Switch } from './ui/switch'
import { Tabs, TabsList, TabsTrigger } from './ui/tabs'

/** 接続状態のドット付きピル（ヘッダー右寄りの並び）。 */
function StatusPill({
  label,
  tone,
  detail,
  title,
}: {
  label: string
  tone: 'ok' | 'warn' | 'error' | 'unknown'
  detail: string
  title?: string
}) {
  const dot = {
    ok: 'bg-emerald-400',
    warn: 'bg-amber-400',
    error: 'bg-red-500',
    unknown: 'bg-muted-foreground/40',
  }[tone]
  return (
    <span
      className="flex items-center gap-1.5 rounded-md border border-border bg-card/70 px-2 py-1 text-xs shadow-elevation-1"
      title={title ?? ''}
    >
      <span className={`size-1.5 rounded-full ${dot}`} />
      <span className="text-foreground/85">{label}</span>
      <span className="text-muted-foreground">{detail}</span>
    </span>
  )
}

function Indicator({ name, status }: { name: string; status?: HealthStatus }) {
  const tone =
    status === undefined
      ? 'unknown'
      : status.status === 'ok'
        ? 'ok'
        : status.status === 'not_configured'
          ? 'warn'
          : 'error'
  const detail =
    status === undefined
      ? '未確認'
      : status.status === 'ok'
        ? '接続済み'
        : status.status === 'not_configured'
          ? '未設定'
          : 'エラー'
  return <StatusPill label={name} tone={tone} detail={detail} title={status?.detail ?? ''} />
}

/** [生成 | エージェント | スタジオ | 設定] tab toggle (AGENT-MODE §1 header). */
function ViewTabs({
  view,
  onView,
}: {
  view: 'main' | 'agent' | 'studio' | 'settings'
  onView: (view: 'main' | 'agent' | 'studio' | 'settings') => void
}) {
  const tabs: { value: 'main' | 'agent' | 'studio' | 'settings'; label: string }[] = [
    { value: 'main', label: '生成' },
    { value: 'agent', label: 'エージェント' },
    { value: 'studio', label: 'スタジオ' },
    { value: 'settings', label: '設定' },
  ]
  return (
    <Tabs
      value={view}
      onValueChange={(value) => onView(value as 'main' | 'agent' | 'studio' | 'settings')}
    >
      <TabsList>
        {tabs.map((tab) => (
          <TabsTrigger key={tab.value} value={tab.value}>
            {tab.label}
          </TabsTrigger>
        ))}
      </TabsList>
    </Tabs>
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
  view: 'main' | 'agent' | 'studio' | 'settings'
  onView: (view: 'main' | 'agent' | 'studio' | 'settings') => void
  showNsfw: boolean
  onShowNsfw: (show: boolean) => void
}) {
  return (
    <header className="sticky top-0 z-30 flex flex-wrap items-center gap-x-3 gap-y-2 border-b border-border bg-background/80 px-4 py-2.5 shadow-elevation-1 backdrop-blur-md supports-[backdrop-filter]:bg-background/60">
      <h1 className="text-sm font-semibold tracking-wide text-foreground">
        Karakuri Media Studio
      </h1>
      <ViewTabs view={view} onView={onView} />
      <div className="ml-2 flex flex-wrap items-center gap-2">
        <Indicator name="ComfyUI" status={health?.comfyui} />
        <Indicator name="Grok" status={health?.grok} />
        <StatusPill
          label="進捗WS"
          tone={wsConnected ? 'ok' : 'unknown'}
          detail={wsConnected ? '接続済み' : '未接続'}
          title="進捗配信 WebSocket"
        />
      </div>
      <div className="ml-auto flex items-center gap-2">
        <div
          className={`flex select-none items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs transition-colors ${
            showNsfw
              ? 'border-primary/60 bg-primary/15 text-accent-400'
              : 'border-border bg-card text-muted-foreground'
          }`}
          title="オフのあいだは NSFW の作品を一覧から隠します"
        >
          <Switch id="header-show-nsfw" checked={showNsfw} onCheckedChange={onShowNsfw} />
          <label
            htmlFor="header-show-nsfw"
            className="flex cursor-pointer items-center gap-1.5 text-inherit"
          >
            <EyeOff className="size-3.5" />
            NSFW表示
          </label>
        </div>
        <Button variant="outline" size="sm" onClick={onRefresh} disabled={checking}>
          <RefreshCw className={checking ? 'animate-spin' : undefined} />
          {checking ? '確認中…' : '接続状態を更新'}
        </Button>
        <Button variant="outline" size="sm" onClick={onOpenSettings}>
          <SettingsIcon />
          設定
        </Button>
      </div>
    </header>
  )
}
