import { EyeOff, HelpCircle, RefreshCw, Settings as SettingsIcon } from 'lucide-react'

import type { Health, HealthStatus } from '../types'
import { Button } from './ui/button'
import { Popover, PopoverContent, PopoverTrigger } from './ui/popover'
import { Switch } from './ui/switch'
import { Tabs, TabsList, TabsTrigger } from './ui/tabs'

export type View = 'main' | 'agent' | 'studio' | 'settings'

/**
 * 画面切替の行き先（設定は歯車ボタン側なのでここには出さない）。
 *
 * sm 以上のヘッダータブと、sm 未満の下部タブバー（BottomNav）で共有する。
 */
export const VIEW_TABS: { value: Exclude<View, 'settings'>; label: string }[] = [
  { value: 'main', label: '生成' },
  { value: 'agent', label: 'エージェント' },
  { value: 'studio', label: 'スタジオ' },
]

type Tone = 'ok' | 'warn' | 'error' | 'unknown'

const DOT: Record<Tone, string> = {
  ok: 'bg-emerald-400',
  warn: 'bg-amber-400',
  error: 'bg-red-500',
  unknown: 'bg-muted-foreground/40',
}

const NSFW_HELP = 'オフのあいだは NSFW の作品を一覧から隠します。'

/** 接続 1 件ぶんの見え方（ドットの色・状態テキスト・補足）。 */
type Connection = {
  name: string
  tone: Tone
  detail: string
  note?: string
}

function connection(name: string, status: HealthStatus | undefined): Connection {
  if (status === undefined) return { name, tone: 'unknown', detail: '未確認' }
  if (status.status === 'ok')
    return { name, tone: 'ok', detail: '接続済み', note: status.detail ?? undefined }
  if (status.status === 'not_configured')
    return { name, tone: 'warn', detail: '未設定', note: status.detail ?? undefined }
  return { name, tone: 'error', detail: 'エラー', note: status.detail ?? undefined }
}

/** いちばん悪い状態（エラー > 未設定 > 未確認 > 正常）をヘッダーの見た目に使う。 */
function worstTone(items: Connection[]): Tone {
  if (items.some((item) => item.tone === 'error')) return 'error'
  if (items.some((item) => item.tone === 'warn')) return 'warn'
  if (items.some((item) => item.tone === 'unknown')) return 'unknown'
  return 'ok'
}

const SUMMARY: Record<Tone, string> = {
  ok: '接続正常',
  warn: '未設定あり',
  error: '接続エラー',
  unknown: '未確認',
}

const TRIGGER_TONE: Record<Tone, string> = {
  ok: 'border-emerald-500/40',
  warn: 'border-amber-500/60 bg-amber-950/30 text-amber-200',
  error: 'border-red-500/70 bg-red-950/40 text-red-200',
  unknown: 'border-border',
}

/**
 * 接続状態（ComfyUI / 選択中の CLI / 進捗WS）。
 *
 * ヘッダーでは色付きドットだけに縮退させ、名前・状態テキスト・更新ボタンは
 * クリックで開く Popover の中に可視テキストで置く（title 頼みにしない）。
 */
function ConnectionStatus({
  health,
  checking,
  onRefresh,
  wsConnected,
}: {
  health: Health | null
  checking: boolean
  onRefresh: () => void
  wsConnected: boolean
}) {
  const items: Connection[] = [
    connection('ComfyUI', health?.comfyui),
    connection(health?.cli_label || 'Grok', health?.grok),
    {
      name: '進捗WS',
      tone: wsConnected ? 'ok' : 'unknown',
      detail: wsConnected ? '接続済み' : '未接続',
      note: '進捗配信 WebSocket',
    },
  ]
  const tone = worstTone(items)

  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button
          variant="outline"
          size="sm"
          className={`gap-1 px-2 ${TRIGGER_TONE[tone]}`}
          aria-label={`接続状態: ${SUMMARY[tone]}`}
        >
          {items.map((item) => (
            <span
              key={item.name}
              className={`size-1.5 rounded-full ${DOT[item.tone]}`}
              aria-hidden
            />
          ))}
          <span className="ml-0.5 hidden sm:inline">{SUMMARY[tone]}</span>
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-64 space-y-3">
        <p className="text-xs font-medium text-foreground">接続状態</p>
        <ul className="space-y-2">
          {items.map((item) => (
            <li key={item.name} className="flex items-start gap-2 text-xs">
              <span
                className={`mt-1 size-1.5 shrink-0 rounded-full ${DOT[item.tone]}`}
                aria-hidden
              />
              <span className="min-w-0 flex-1">
                <span className="flex items-baseline justify-between gap-2">
                  <span className="text-foreground/85">{item.name}</span>
                  <span className="text-muted-foreground">{item.detail}</span>
                </span>
                {item.note && (
                  <span className="mt-0.5 block break-words text-[11px] text-muted-foreground">
                    {item.note}
                  </span>
                )}
              </span>
            </li>
          ))}
        </ul>
        <Button
          variant="outline"
          size="sm"
          className="w-full"
          onClick={onRefresh}
          disabled={checking}
        >
          <RefreshCw className={checking ? 'animate-spin' : undefined} />
          {checking ? '確認中…' : '接続状態を更新'}
        </Button>
      </PopoverContent>
    </Popover>
  )
}

/** NSFW 表示トグル（説明は aria-describedby と Popover の両方で読める）。 */
function NsfwToggle({
  showNsfw,
  onShowNsfw,
}: {
  showNsfw: boolean
  onShowNsfw: (show: boolean) => void
}) {
  return (
    <div
      className={`flex select-none items-center gap-1.5 rounded-full border px-2 py-1 text-xs transition-colors ${
        showNsfw
          ? 'border-primary/60 bg-primary/15 text-accent-400'
          : 'border-border bg-card text-muted-foreground'
      }`}
    >
      <Switch
        id="header-show-nsfw"
        checked={showNsfw}
        onCheckedChange={onShowNsfw}
        aria-describedby="header-show-nsfw-help"
      />
      <label
        htmlFor="header-show-nsfw"
        className="flex cursor-pointer items-center gap-1.5 text-inherit"
      >
        <EyeOff className="size-3.5" />
        <span className="hidden sm:inline">NSFW表示</span>
      </label>
      {/* 支援技術には常に読める説明。目で見る用は下の Popover。 */}
      <span id="header-show-nsfw-help" className="sr-only">
        {NSFW_HELP}
      </span>
      <Popover>
        <PopoverTrigger asChild>
          <Button
            variant="ghost"
            size="icon-xs"
            className="text-inherit opacity-70 hover:opacity-100"
            aria-label="NSFW表示の説明"
          >
            <HelpCircle />
          </Button>
        </PopoverTrigger>
        <PopoverContent align="end" className="w-56 text-xs text-muted-foreground">
          {NSFW_HELP}
        </PopoverContent>
      </Popover>
    </div>
  )
}

/**
 * [生成 | エージェント | スタジオ] tab toggle (AGENT-MODE §1 header).
 *
 * 設定は右端の歯車ボタンへ一本化したのでタブには出さない（`view === 'settings'`
 * のあいだはどのタブも選択状態にならない）。
 */
function ViewTabs({ view, onView }: { view: View; onView: (view: View) => void }) {
  return (
    <Tabs value={view} onValueChange={(value) => onView(value as View)}>
      <TabsList>
        {VIEW_TABS.map((tab) => (
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
  view: View
  onView: (view: View) => void
  showNsfw: boolean
  onShowNsfw: (show: boolean) => void
}) {
  return (
    <header className="sticky top-0 z-30 flex items-center gap-x-2 gap-y-2 border-b border-border bg-background/80 px-3 py-2 shadow-elevation-1 backdrop-blur-md supports-[backdrop-filter]:bg-background/60 sm:px-4">
      {/* 狭幅ではタイトルを短縮して、操作系を 1 行に収める。 */}
      {/* 狭幅では文字を viewport 幅なりに縮めてフルタイトルのまま収める。 */}
      <h1 className="flex min-w-0 shrink items-center gap-2 text-[clamp(0.72rem,3.2vw,1rem)] font-semibold tracking-wide text-foreground sm:shrink-0 sm:text-base">
        <img
          src="/icon.svg"
          alt=""
          width={28}
          height={28}
          className="size-7 shrink-0 rounded-md"
          aria-hidden
        />
        <span className="truncate">Karakuri Media Studio</span>
      </h1>

      {/* 画面切替は sm 以上だけ。sm 未満は下部タブバー（BottomNav）に出す。 */}
      <div className="hidden min-w-0 sm:block">
        <ViewTabs view={view} onView={onView} />
      </div>

      <div className="ml-auto flex shrink-0 items-center gap-2">
        <ConnectionStatus
          health={health}
          checking={checking}
          onRefresh={onRefresh}
          wsConnected={wsConnected}
        />
        <NsfwToggle showNsfw={showNsfw} onShowNsfw={onShowNsfw} />
        <Button
          variant={view === 'settings' ? 'default' : 'outline'}
          size="icon-sm"
          onClick={onOpenSettings}
          aria-label="設定"
          aria-current={view === 'settings' ? 'page' : undefined}
        >
          <SettingsIcon />
        </Button>
      </div>
    </header>
  )
}
