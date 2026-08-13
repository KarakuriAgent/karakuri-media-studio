import { useRef, useState, type CSSProperties } from 'react'
import {
  ChevronLeft,
  ChevronRight,
  Loader2,
  Paperclip,
  Plus,
  RefreshCw,
} from 'lucide-react'

import type {
  AgentCheckinMode,
  AgentSessionCreate,
  AgentSessionSummary,
} from '../../types'
import { NsfwBadge, NsfwToggle } from '../ui'
import { cn } from '@/lib/utils'
import { NativeSelect } from '../NativeSelect'
import { Button } from '../ui/button'
import { Input } from '../ui/input'
import { Label } from '../ui/label'
import { Textarea } from '../ui/textarea'
import {
  ATTACHMENT_ACCEPT,
  AttachmentChip,
  isAllowedAttachment,
  rejectedMessage,
} from './attachments'
import {
  AgentStatusBadge,
  CHECKIN_LABEL,
  CHECKIN_MODES,
  autoLimitLabel,
  shortTime,
} from './common'

interface Props {
  sessions: AgentSessionSummary[]
  activeId: string | null
  loading: boolean
  busy: boolean
  collapsed: boolean
  onToggle: () => void
  onReload: () => void
  onSelect: (id: string) => void
  onDelete: (id: string) => void
  /**
   * 新規セッション開始。``files`` はセッション作成後にアップロードして最初の
   * 発言に添付する（作成前はアップロード先が無いので File のまま渡す）。
   */
  onCreate: (payload: AgentSessionCreate, files: File[]) => void
  onToggleNsfw: (id: string, nsfw: boolean) => void
  /** オンのときだけ NSFW バッジを出す（オフのとき NSFW は渡ってこない）。 */
  showNsfw: boolean
  /** Layout override: desktop column vs. mobile drawer (AGENT-MODE §1). */
  className?: string
  /** 展開時の幅（リサイズ結果）。折りたたみ中は当てない。 */
  style?: CSSProperties
}

export default function SessionList({
  sessions,
  activeId,
  loading,
  busy,
  collapsed,
  onToggle,
  onReload,
  onSelect,
  onDelete,
  onCreate,
  onToggleNsfw,
  showNsfw,
  className = '',
  style,
}: Props) {
  const [creating, setCreating] = useState(false)
  const [goal, setGoal] = useState('')
  /** 表示名（空なら最初の指示から自動で決まる）。 */
  const [title, setTitle] = useState('')
  const [mode, setMode] = useState<AgentCheckinMode>('milestone')
  const [autoLimit, setAutoLimit] = useState(5)
  const [files, setFiles] = useState<File[]>([])
  const [attachError, setAttachError] = useState<string | null>(null)
  const filePicker = useRef<HTMLInputElement>(null)

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
          title="セッション一覧を開く"
          aria-label="セッション一覧を開く"
        >
          <ChevronRight />
        </Button>
        <span className="tnum text-[11px] text-muted-foreground">
          {sessions.length}
        </span>
      </aside>
    )
  }

  const start = () => {
    onCreate(
      {
        // 空なら送らない（バックエンドが最初の指示から自動で名前を付ける）
        title: title.trim(),
        goal: goal.trim(),
        checkin_mode: mode,
        auto_limit: autoLimit,
      },
      files,
    )
    setGoal('')
    setTitle('')
    setFiles([])
    setAttachError(null)
    setCreating(false)
  }

  /** 作成前なので保持だけ（アップロードはセッション作成後に AgentView が行う）。 */
  const pick = (picked: FileList | null) => {
    const chosen = Array.from(picked ?? [])
    if (chosen.length === 0) return
    const rejected = chosen.filter((file) => !isAllowedAttachment(file.name))
    setAttachError(rejected.length ? rejectedMessage(rejected.map((f) => f.name)) : null)
    const allowed = chosen.filter((file) => isAllowedAttachment(file.name))
    if (allowed.length > 0) setFiles((current) => [...current, ...allowed])
    if (filePicker.current) filePicker.current.value = ''
  }

  return (
    <aside
      className={cn(
        'flex w-64 shrink-0 flex-col rounded-lg border border-border bg-card shadow-elevation-1',
        className,
      )}
      style={style}
    >
      <div className="flex items-center gap-2 border-b border-border px-3 py-2">
        <h2 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
          セッション
        </h2>
        <span className="tnum text-xs text-muted-foreground">
          {sessions.length}
        </span>
        <div className="ml-auto flex items-center gap-1">
          <Button
            variant="outline"
            size="icon-xs"
            onClick={onReload}
            disabled={loading}
            title="一覧を更新"
            aria-label="一覧を更新"
          >
            {loading ? <Loader2 className="animate-spin" /> : <RefreshCw />}
          </Button>
          <Button
            variant="outline"
            size="icon-xs"
            onClick={onToggle}
            title="折りたたむ"
            aria-label="折りたたむ"
          >
            <ChevronLeft />
          </Button>
        </div>
      </div>

      <div className="border-b border-border p-2">
        {!creating ? (
          <Button size="sm" className="w-full" onClick={() => setCreating(true)}>
            <Plus />
            新規セッション
          </Button>
        ) : (
          <div className="space-y-2">
            <div className="space-y-1">
              <Label htmlFor="new-session-title">セッション名（任意）</Label>
              <Input
                id="new-session-title"
                className="h-8 text-xs"
                value={title}
                placeholder="空なら最初の指示から決めます"
                onChange={(event) => setTitle(event.target.value)}
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="new-session-goal">最初の指示</Label>
              <Textarea
                id="new-session-goal"
                className="h-20 resize-none text-xs"
                value={goal}
                autoFocus
                placeholder="例: かおりのダンス動画を雰囲気違いで3本"
                onChange={(event) => setGoal(event.target.value)}
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="new-session-checkin">チェックイン</Label>
              <NativeSelect
                id="new-session-checkin"
                value={mode}
                onChange={(event) =>
                  setMode(event.target.value as AgentCheckinMode)
                }
              >
                {CHECKIN_MODES.map((value) => (
                  <option key={value} value={value}>
                    {CHECKIN_LABEL[value]}
                  </option>
                ))}
              </NativeSelect>
            </div>
            {/* 生成本数の上限はチェックインモードに関係なく効く
                （agent_runner.over_limit は auto_limit だけを見る）。 */}
            <div className="space-y-1">
              <Label htmlFor="new-session-auto-limit">上限本数（0 = 無制限）</Label>
              <Input
                id="new-session-auto-limit"
                className="tnum h-8 text-xs"
                type="number"
                min={0}
                value={autoLimit}
                onChange={(event) =>
                  setAutoLimit(Math.max(0, Number(event.target.value) || 0))
                }
              />
              <p className="text-[11px] text-muted-foreground">
                この本数ごとに続けてよいか確認します。0 = 無制限で、確認せず走り切ります。
              </p>
            </div>
            <div>
              <input
                ref={filePicker}
                type="file"
                multiple
                hidden
                accept={ATTACHMENT_ACCEPT}
                data-testid="new-session-attachment-input"
                onChange={(event) => pick(event.target.files)}
              />
              <Button
                variant="outline"
                size="sm"
                className="w-full"
                title="ファイルを添付"
                aria-label="ファイルを添付"
                onClick={() => filePicker.current?.click()}
              >
                <Paperclip />
                ファイルを添付
              </Button>
              {files.length > 0 && (
                <div className="mt-1.5 flex flex-wrap gap-1">
                  {files.map((file) => (
                    <AttachmentChip
                      key={`${file.name}:${file.size}:${file.lastModified}`}
                      label={file.name}
                      onRemove={() =>
                        setFiles((current) => current.filter((other) => other !== file))
                      }
                    />
                  ))}
                </div>
              )}
              {attachError && (
                <p className="mt-1 text-[11px] text-red-400">{attachError}</p>
              )}
            </div>
            <div className="flex gap-2">
              <Button
                size="sm"
                className="flex-1"
                disabled={busy || (!goal.trim() && files.length === 0)}
                onClick={start}
              >
                開始
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={() => {
                  setFiles([])
                  setAttachError(null)
                  setCreating(false)
                }}
              >
                取消
              </Button>
            </div>
          </div>
        )}
      </div>

      <div className="min-h-0 flex-1 space-y-1 overflow-y-auto p-2">
        {sessions.length === 0 && (
          <p className="px-1 py-3 text-center text-xs text-muted-foreground">
            まだセッションがありません
          </p>
        )}
        {sessions.map((session) => (
          <div
            key={session.id}
            className={`group rounded-md border px-2 py-1.5 transition-colors ${
              session.id === activeId
                ? 'border-primary bg-primary/10'
                : 'border-border bg-surface-sunken hover:border-primary/50'
            }`}
          >
            <button
              className="block w-full rounded text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
              onClick={() => onSelect(session.id)}
            >
              <span className="block truncate text-xs text-foreground/90">
                {session.title || '(無題)'}
              </span>
              <span className="mt-1 flex items-center gap-1.5">
                <AgentStatusBadge status={session.status} />
                {showNsfw && session.nsfw && <NsfwBadge />}
                <span className="tnum text-[11px] text-muted-foreground">
                  {shortTime(session.created_at)}
                </span>
              </span>
              <span className="mt-1 block text-[11px] text-muted-foreground-subtle">
                タスク {session.task_count} / 成果物 {session.artifact_count} ／{' '}
                {CHECKIN_LABEL[session.checkin_mode]} ／{' '}
                {autoLimitLabel(session.auto_limit)}
              </span>
            </button>
            <div className="mt-1 flex items-center gap-2">
              <NsfwToggle
                nsfw={session.nsfw}
                disabled={busy}
                onToggle={(nsfw) => onToggleNsfw(session.id, nsfw)}
                className="h-6 px-1.5 text-[11px]"
              />
              <Button
                variant="ghost"
                size="xs"
                className="ml-auto text-[11px] text-muted-foreground opacity-0 transition-opacity hover:text-red-400 group-hover:opacity-100"
                onClick={() => onDelete(session.id)}
              >
                削除
              </Button>
            </div>
          </div>
        ))}
      </div>
    </aside>
  )
}
