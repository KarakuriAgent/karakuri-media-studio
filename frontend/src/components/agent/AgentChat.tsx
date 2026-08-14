import { useEffect, useRef, useState } from 'react'
import {
  AlertTriangle,
  Loader2,
  Menu,
  Paperclip,
  Settings,
  ShieldCheck,
  Square,
} from 'lucide-react'

import { api } from '../../api'
import type {
  AgentAttachment,
  AgentCheckinMode,
  AgentMessage,
  AgentSession,
  AgentSessionUpdate,
  JobProgress,
} from '../../types'
import { MarkdownBody } from '../MarkdownBody'
import { Banner, NsfwBadge, NsfwToggle } from '../ui'
import { NativeSelect } from '../NativeSelect'
import { Button } from '../ui/button'
import { Input } from '../ui/input'
import { Label } from '../ui/label'
import { Textarea } from '../ui/textarea'
import PlanCard from './PlanCard'
import {
  ATTACHMENT_ACCEPT,
  AttachmentChip,
  isAllowedAttachment,
  rejectedMessage,
} from './attachments'
import {
  AGENT_ACTIVE,
  AgentStatusBadge,
  CHECKIN_LABEL,
  CHECKIN_MODES,
  EventIcon,
  autoLimitLabel,
  shortTime,
} from './common'
import { inputState, isCheckinAnswered, openCheckinIndex } from './logic'

interface Props {
  session: AgentSession
  progress: Record<string, JobProgress>
  busy: boolean
  error: string | null
  onDismissError: () => void
  /** 本文と添付（workdir 相対パス）。どちらか一方だけでも送信できる。 */
  onSend: (content: string, attachments: string[]) => void
  onApprove: () => void
  onCheckin: (answer: string) => void
  onStop: () => void
  /** チェックインモード / 生成本数の上限の変更（送った項目だけ変わる）。 */
  onUpdateSession: (patch: AgentSessionUpdate) => void
  /** 狭幅のみ: セッション一覧ドロワーを開く。 */
  onOpenSessions: () => void
  /** 狭幅のみ: 成果物パネル（全画面オーバーレイ）を開く。 */
  onOpenArtifacts: () => void
  artifactCount: number
  /** 未読の新着成果物がある（狭幅ではボタンにバッジを出すだけ）。 */
  artifactBadge: boolean
  onToggleNsfw: (nsfw: boolean) => void
  /** NSFW 表示トグル（オンのときだけ NSFW バッジを出す）。 */
  showNsfw: boolean
  /**
   * Grok ターンが走っている（このブラウザ発の呼び出し + バックエンドのループ）。
   * `busy` と違い、承認後やジョブ完了後の自動ターンでも立つ。
   */
  thinking: boolean
  /** 実行中の活動（「ツール実行中: …」など）。無ければ null。 */
  activity?: string | null
}

function optionsOf(message: AgentMessage): string[] {
  const options = message.data?.options
  return Array.isArray(options) ? options.filter((o): o is string => typeof o === 'string') : []
}

const IMAGE_RE = /\.(png|jpe?g|webp|bmp|gif)$/i

/** 添付の workdir 相対パス（バックエンドが data に残したもの）。 */
function attachmentsOf(message: AgentMessage): string[] {
  const list = message.data?.attachments
  return Array.isArray(list) ? list.filter((p): p is string => typeof p === 'string') : []
}

/**
 * 吹き出しに出す本文。添付付きの発言は content に定型文が足されているので、
 * data.text に控えてあるユーザー本文のほうを使う。
 */
function bubbleText(message: AgentMessage): string {
  const text = message.data?.text
  return typeof text === 'string' ? text : message.content
}

function baseName(path: string): string {
  return path.split('/').pop() || path
}

function Bubble({ message, sessionId }: { message: AgentMessage; sessionId: string }) {
  const mine = message.role === 'user'
  const attachments = attachmentsOf(message)
  const text = mine ? bubbleText(message) : message.content
  return (
    <div className={`flex ${mine ? 'justify-end' : 'justify-start'}`}>
      <div
        className={`min-w-0 max-w-[80%] rounded-lg px-3 py-2 text-sm ${
          mine ? 'bg-primary/20 text-foreground' : 'bg-card text-foreground/90'
        }`}
      >
        {text &&
          (mine ? (
            <p className="whitespace-pre-wrap break-words [overflow-wrap:anywhere]">{text}</p>
          ) : (
            <MarkdownBody text={text} splitAction />
          ))}
        {attachments.length > 0 && (
          <div className={`flex flex-wrap gap-1.5 ${text ? 'mt-2' : ''}`}>
            {attachments.map((path) => {
              const url = api.agentArtifactUrl(sessionId, path)
              return (
                <a
                  key={path}
                  href={url}
                  target="_blank"
                  rel="noreferrer"
                  className="flex items-center gap-1.5 rounded-md border border-border bg-secondary px-1.5 py-1 text-[11px] text-foreground/85 transition-colors hover:text-foreground"
                  title={path}
                >
                  {IMAGE_RE.test(path) ? (
                    <img src={url} alt="" className="h-8 w-8 rounded object-cover" />
                  ) : (
                    <Paperclip className="size-3" />
                  )}
                  <span className="max-w-[12rem] truncate">{baseName(path)}</span>
                </a>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}

function EventRow({ message }: { message: AgentMessage }) {
  return (
    <div className="flex items-start gap-2 px-1 text-[11px] text-muted-foreground">
      <EventIcon kind={message.kind} />
      <span className="min-w-0 flex-1 whitespace-pre-wrap break-words">
        {message.content}
      </span>
      <span className="tnum shrink-0 text-muted-foreground-subtle">
        {shortTime(message.ts)}
      </span>
    </div>
  )
}

function CheckinBubble({
  message,
  open,
  answered,
  busy,
  onAnswer,
}: {
  message: AgentMessage
  open: boolean
  /** 実際に回答されたか（未回答のまま終わったチェックインと区別する）。 */
  answered: boolean
  busy: boolean
  onAnswer: (answer: string) => void
}) {
  const [free, setFree] = useState('')
  const options = optionsOf(message)

  return (
    <div className="flex justify-start">
      <div
        className={`min-w-0 max-w-[85%] rounded-lg border px-3 py-2 text-sm ${
          open
            ? 'border-violet-700/70 bg-violet-950/40 text-violet-100'
            : 'border-border bg-surface-sunken text-muted-foreground'
        }`}
      >
        <div className="mb-2 flex items-start gap-1.5">
          {message.kind === 'approval' ? (
            <>
              <ShieldCheck className="mt-0.5 size-3.5 shrink-0" />
              <span className="shrink-0">承認</span>
            </>
          ) : (
            <AlertTriangle className="mt-0.5 size-3.5 shrink-0" />
          )}
          <MarkdownBody text={message.content} className="min-w-0 flex-1" />
        </div>
        <div className="flex flex-wrap gap-1.5">
          {options.map((option) => (
            <Button
              key={option}
              variant="outline"
              size="sm"
              disabled={!open || busy}
              onClick={() => onAnswer(option)}
            >
              {option}
            </Button>
          ))}
        </div>
        {open && (
          <div className="mt-2 flex gap-1.5">
            <Input
              className="h-8 text-xs"
              value={free}
              placeholder="自由に回答"
              disabled={busy}
              onChange={(event) => setFree(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' && free.trim()) {
                  event.preventDefault()
                  onAnswer(free.trim())
                  setFree('')
                }
              }}
            />
            <Button
              size="sm"
              disabled={busy || !free.trim()}
              onClick={() => {
                onAnswer(free.trim())
                setFree('')
              }}
            >
              返答
            </Button>
          </div>
        )}
        {!open && (
          <p className="mt-1 text-[11px] text-muted-foreground">
            {answered ? '応答済み' : '未応答のまま終了しました'}
          </p>
        )}
      </div>
    </div>
  )
}

/**
 * セッションの進め方（チェックイン / 生成本数の上限）を後から変える小窓。
 *
 * 上限はどのチェックインモードでも効くので、モードに関係なく出す。指示文への
 * 反映は次のターンからなので、その旨をここに書いておく。
 */
function SessionSettings({
  session,
  busy,
  onSave,
}: {
  session: AgentSession
  busy: boolean
  onSave: (patch: AgentSessionUpdate) => void
}) {
  const [open, setOpen] = useState(false)
  const [mode, setMode] = useState<AgentCheckinMode>(session.checkin_mode)
  const [limit, setLimit] = useState(session.auto_limit)

  /** 開くたびに現在値へ揃える（閉じている間に変わっていることがある）。 */
  const toggle = () => {
    if (!open) {
      setMode(session.checkin_mode)
      setLimit(session.auto_limit)
    }
    setOpen((value) => !value)
  }

  return (
    <span className="relative">
      <Button
        variant="outline"
        size="icon-sm"
        aria-label="セッション設定を変更"
        aria-expanded={open}
        title="チェックインと生成本数の上限を変える"
        onClick={toggle}
      >
        <Settings />
      </Button>
      {open && (
        <div className="absolute right-0 top-full z-20 mt-1 w-64 space-y-2 rounded-md border border-border bg-popover p-2 shadow-elevation-3">
          <div className="space-y-1">
            <Label htmlFor="session-checkin-mode">チェックイン</Label>
            <NativeSelect
              id="session-checkin-mode"
              className="h-8 text-xs"
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
          <div className="space-y-1">
            <Label htmlFor="session-auto-limit">上限本数（0 = 無制限）</Label>
            <Input
              id="session-auto-limit"
              className="tnum h-8 text-xs"
              type="number"
              min={0}
              value={limit}
              onChange={(event) =>
                setLimit(Math.max(0, Number(event.target.value) || 0))
              }
            />
          </div>
          <p className="text-[11px] text-muted-foreground">
            上限の判定はすぐ効きます。エージェントへの指示文に載るのは次のターンからです。
          </p>
          <div className="flex gap-2">
            <Button
              size="sm"
              className="flex-1"
              disabled={busy}
              onClick={() => {
                onSave({ checkin_mode: mode, auto_limit: limit })
                setOpen(false)
              }}
            >
              保存
            </Button>
            <Button variant="outline" size="sm" onClick={() => setOpen(false)}>
              取消
            </Button>
          </div>
        </div>
      )}
    </span>
  )
}

export default function AgentChat({
  session,
  progress,
  busy,
  error,
  onDismissError,
  onSend,
  onApprove,
  onCheckin,
  onStop,
  onUpdateSession,
  onOpenSessions,
  onOpenArtifacts,
  artifactCount,
  artifactBadge,
  onToggleNsfw,
  showNsfw,
  thinking,
  activity = null,
}: Props) {
  const [draft, setDraft] = useState('')
  const [attachments, setAttachments] = useState<AgentAttachment[]>([])
  const [uploading, setUploading] = useState(false)
  const [uploadError, setUploadError] = useState<string | null>(null)
  const scroller = useRef<HTMLDivElement>(null)
  const input = useRef<HTMLTextAreaElement>(null)
  const filePicker = useRef<HTMLInputElement>(null)

  useEffect(() => {
    scroller.current?.scrollTo({ top: scroller.current.scrollHeight })
  }, [session.messages.length, thinking])

  const running = session.status === 'running'
  const openIndex = openCheckinIndex(session)
  const stoppable = AGENT_ACTIVE.includes(session.status)
  const { disabled: inputDisabled, placeholder } = inputState(session, thinking)

  const sendable = (draft.trim().length > 0 || attachments.length > 0) && !uploading

  const send = () => {
    if (!sendable || inputDisabled) return
    const content = draft.trim()
    const paths = attachments.map((item) => item.path)
    setDraft('')
    setAttachments([])
    onSend(content, paths)
  }

  /** 選んだファイルは即アップロードし、チップとして入力欄の上に並べる。 */
  const pick = async (picked: FileList | null) => {
    const files = Array.from(picked ?? [])
    if (files.length === 0) return
    const rejected = files.filter((file) => !isAllowedAttachment(file.name))
    setUploadError(rejected.length ? rejectedMessage(rejected.map((f) => f.name)) : null)
    const allowed = files.filter((file) => isAllowedAttachment(file.name))
    if (allowed.length === 0) {
      if (filePicker.current) filePicker.current.value = ''
      return
    }
    setUploading(true)
    try {
      for (const file of allowed) {
        const uploaded = await api.uploadAgentAttachment(session.id, file)
        setAttachments((current) => [...current, uploaded])
      }
    } catch (caught) {
      setUploadError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      setUploading(false)
      if (filePicker.current) filePicker.current.value = ''
    }
  }

  return (
    <section className="flex min-h-0 min-w-0 flex-1 flex-col gap-2">
      <div className="flex shrink-0 flex-wrap items-center gap-2 rounded-lg border border-border bg-card px-3 py-2 shadow-elevation-1">
        <Button
          variant="outline"
          size="icon-sm"
          className="lg:hidden"
          onClick={onOpenSessions}
          title="セッション一覧"
          aria-label="セッション一覧"
        >
          <Menu />
        </Button>
        <span className="min-w-0 flex-1 truncate text-xs text-foreground/90 lg:flex-none">
          {session.title || '(無題)'}
        </span>
        <AgentStatusBadge status={session.status} />
        {showNsfw && session.nsfw && <NsfwBadge />}
        {/* 生成本数の上限はどのチェックインモードでも効く（0 なら「無制限」）。 */}
        <span className="text-[11px] text-muted-foreground">
          {CHECKIN_LABEL[session.checkin_mode]} / {autoLimitLabel(session.auto_limit)}
        </span>
        <div className="ml-auto flex items-center gap-1.5">
          <SessionSettings
            session={session}
            busy={busy}
            onSave={onUpdateSession}
          />
          <NsfwToggle nsfw={session.nsfw} disabled={busy} onToggle={onToggleNsfw} />
          <Button
            variant="outline"
            size="sm"
            className="relative lg:hidden"
            onClick={onOpenArtifacts}
            title="成果物パネルを開く"
          >
            成果物 ({artifactCount})
            {artifactBadge && (
              <span className="absolute -right-0.5 -top-0.5 size-2 rounded-full bg-accent-400" />
            )}
          </Button>
          <Button
            variant="outline"
            size="sm"
            disabled={!stoppable}
            title={
              stoppable ? '実行中の生成も停止します' : '実行中ではありません'
            }
            onClick={onStop}
          >
            <Square />
            停止
          </Button>
        </div>
      </div>

      {error && <Banner onClose={onDismissError}>{error}</Banner>}

      <div
        ref={scroller}
        className="min-h-0 min-w-0 flex-1 space-y-2 overflow-x-hidden overflow-y-auto rounded-lg border border-border bg-surface-sunken p-3"
      >
        {running && session.plan.tasks.length > 0 && (
          <div className="sticky -top-3 z-10 -mx-1 bg-surface-sunken/95 py-1 backdrop-blur">
            <PlanCard
              plan={session.plan}
              compact
              busy={busy}
              progress={progress}
              onApprove={onApprove}
              onRequestChanges={() => input.current?.focus()}
            />
          </div>
        )}

        {session.messages.map((message, index) => {
          if (message.role === 'system') return null
          if (message.role === 'event')
            return <EventRow key={index} message={message} />
          if (message.role === 'checkin')
            return (
              <CheckinBubble
                key={index}
                message={message}
                open={index === openIndex}
                answered={isCheckinAnswered(session, index)}
                busy={busy}
                onAnswer={onCheckin}
              />
            )
          return <Bubble key={index} message={message} sessionId={session.id} />
        })}

        {!running && session.plan.tasks.length > 0 && (
          <PlanCard
            plan={session.plan}
            compact={false}
            busy={busy}
            progress={progress}
            onApprove={onApprove}
            onRequestChanges={() => input.current?.focus()}
          />
        )}

        {thinking && (
          <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <Loader2 className="size-3.5 animate-spin" />
            {activity ? `Grok が作業しています… ${activity}` : 'Grok が考えています…'}
          </p>
        )}
      </div>

      {uploadError && (
        <Banner onClose={() => setUploadError(null)}>{uploadError}</Banner>
      )}

      {(attachments.length > 0 || uploading) && (
        <div className="flex shrink-0 flex-wrap items-center gap-1.5">
          {attachments.map((item) => (
            <AttachmentChip
              key={item.path}
              label={item.name}
              title={item.path}
              onRemove={() =>
                setAttachments((current) =>
                  current.filter((other) => other.path !== item.path),
                )
              }
            />
          ))}
          {uploading && (
            <span className="flex items-center gap-1 text-[11px] text-muted-foreground">
              <Loader2 className="size-3 animate-spin" />
              アップロード中…
            </span>
          )}
        </div>
      )}

      <div className="flex shrink-0 gap-2">
        <input
          ref={filePicker}
          type="file"
          multiple
          hidden
          accept={ATTACHMENT_ACCEPT}
          data-testid="agent-attachment-input"
          onChange={(event) => void pick(event.target.files)}
        />
        <Button
          variant="outline"
          size="icon"
          disabled={inputDisabled || uploading}
          title="ファイルを添付"
          aria-label="ファイルを添付"
          onClick={() => filePicker.current?.click()}
        >
          <Paperclip />
        </Button>
        <Textarea
          ref={input}
          className="h-16 flex-1 resize-none"
          value={draft}
          placeholder={placeholder}
          disabled={inputDisabled}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
              event.preventDefault()
              send()
            }
          }}
        />
        <Button disabled={inputDisabled || !sendable} onClick={send}>
          送信
        </Button>
      </div>
    </section>
  )
}
