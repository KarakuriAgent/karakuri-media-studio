import { useEffect, useRef, useState } from 'react'
import { Clapperboard, Music, Paperclip, Square } from 'lucide-react'

import { api } from '../../api'
import type { CanvasAttachment, CanvasMessage, StudioAsset } from '../../types'
import { Banner } from '../ui'
import {
  ATTACHMENT_ACCEPT,
  AttachmentChip,
  isAllowedAttachment,
  rejectedMessage,
} from '../agent/attachments'
import { EventIcon } from '../agent/common'
import { Button } from '../ui/button'
import MentionInput from './MentionInput'
import {
  messageAttachments,
  messageText,
  runningLabel,
  shortTime,
} from './logic'

export const CHAT_PLACEHOLDER = 'やりたいことを書く（@ で素材を参照）'

interface Props {
  /** 添付のアップロード先・配信元（キャンバスは作品ごとに 1 本の会話）。 */
  projectId: string
  messages: CanvasMessage[]
  /** `@` 候補に出す素材（World Bible）。 */
  assets: StudioAsset[]
  /** 送信中（このブラウザ発）。 */
  busy: boolean
  /** エージェントが走っている（このブラウザ発でなくても立つ）。 */
  running: boolean
  /** 実行中の活動（「ツール実行中: …」など）。 */
  activity: string | null
  /** 本文と添付（workdir 相対パス）。どちらか一方だけでも送れる。 */
  onSend: (content: string, attachments: string[]) => void
  onStop: () => void
}

/** 発言に添えられた添付のサムネイル（クリックで開ける）。 */
function AttachmentThumb({
  projectId,
  attachment,
}: {
  projectId: string
  attachment: CanvasAttachment
}) {
  const url = api.canvasAttachmentUrl(projectId, attachment.path)
  return (
    <a
      href={url}
      target="_blank"
      rel="noreferrer"
      className="flex items-center gap-1.5 rounded-sm border border-border bg-surface-sunken px-1.5 py-1 text-[11px] text-foreground/85 transition-colors hover:text-foreground"
      title={attachment.abs_path || attachment.path}
    >
      {attachment.kind === 'image' ? (
        <img src={url} alt="" className="h-8 w-8 rounded object-cover" />
      ) : attachment.kind === 'video' ? (
        <Clapperboard className="size-3.5 shrink-0" aria-hidden="true" />
      ) : attachment.kind === 'audio' ? (
        <Music className="size-3.5 shrink-0" aria-hidden="true" />
      ) : (
        <Paperclip className="size-3.5 shrink-0" aria-hidden="true" />
      )}
      <span className="max-w-[10rem] truncate">{attachment.name}</span>
    </a>
  )
}

function Bubble({
  message,
  projectId,
}: {
  message: CanvasMessage
  projectId: string
}) {
  const mine = message.role === 'user'
  // 添付つきの発言は本文の後ろに一覧が足してあるので、画面には元の文だけ出す。
  const text = mine ? messageText(message) : message.content
  const attachments = mine ? messageAttachments(message) : []
  return (
    <div className={`flex ${mine ? 'justify-end' : 'justify-start'}`}>
      <div
        className={`max-w-[85%] break-words rounded-lg px-3 py-2 text-sm ${
          mine ? 'bg-primary/20 text-foreground' : 'bg-secondary text-foreground/90'
        }`}
      >
        {text && <p className="whitespace-pre-wrap">{text}</p>}
        {attachments.length > 0 && (
          <div className={`flex flex-wrap gap-1.5 ${text ? 'mt-2' : ''}`}>
            {attachments.map((attachment) => (
              <AttachmentThumb
                key={attachment.path}
                projectId={projectId}
                attachment={attachment}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

/**
 * ツール実行などのシステムイベント 1 行。
 *
 * アイコンはエージェントモードの制作記録と同じ表（`agent/common`）を使う——
 * 同じアクションが同じ絵で出たほうが読み替えなくて済むため。
 */
function EventRow({ message }: { message: CanvasMessage }) {
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

/**
 * キャンバスのチャット。
 *
 * 発言を送ると、スタジオと同じエージェント基盤が「スタジオの操作 + 盤面の
 * 操作」のツールで動く（`POST /api/canvas/projects/{id}/agent`）。応答と
 * ツール実行の結果は WS で 1 件ずつ届き、そのまま履歴に残る。
 *
 * 画像・動画・音声はここから添付できる（アップロード先はキャンバスの作業
 * ディレクトリで、エージェントはそのファイルを直接開いて中身を見る）。
 */
export default function CanvasChat({
  projectId,
  messages,
  assets,
  busy,
  running,
  activity,
  onSend,
  onStop,
}: Props) {
  const [draft, setDraft] = useState('')
  const [attachments, setAttachments] = useState<CanvasAttachment[]>([])
  const [uploading, setUploading] = useState(false)
  const [uploadError, setUploadError] = useState<string | null>(null)
  const [dragging, setDragging] = useState(false)
  const scroller = useRef<HTMLDivElement>(null)
  const filePicker = useRef<HTMLInputElement>(null)

  useEffect(() => {
    scroller.current?.scrollTo({ top: scroller.current.scrollHeight })
  }, [messages.length])

  const disabled = busy || running
  const sendable =
    (draft.trim().length > 0 || attachments.length > 0) && !uploading

  const send = () => {
    if (!sendable || disabled) return
    const content = draft.trim()
    const paths = attachments.map((item) => item.path)
    setDraft('')
    setAttachments([])
    onSend(content, paths)
  }

  /** 選んだファイルは即アップロードし、チップとして入力欄の上に並べる。 */
  const pick = async (picked: FileList | File[] | null) => {
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
        const uploaded = await api.uploadCanvasAttachment(projectId, file)
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
    <div
      className={`flex min-h-0 flex-1 flex-col gap-2 ${
        dragging ? 'rounded-lg outline-dashed outline-2 outline-primary' : ''
      }`}
      onDragOver={(event) => {
        event.preventDefault()
        setDragging(true)
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={(event) => {
        event.preventDefault()
        setDragging(false)
        if (!disabled) void pick(event.dataTransfer.files)
      }}
    >
      <div className="flex shrink-0 items-center gap-2">
        <h2 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
          チャット
        </h2>
        {running && (
          <Button variant="outline" size="sm" className="ml-auto" onClick={onStop}>
            <Square aria-hidden="true" />
            停止
          </Button>
        )}
      </div>

      <div
        ref={scroller}
        className="min-h-0 flex-1 space-y-2 overflow-y-auto rounded-lg border border-border bg-surface-sunken p-2"
      >
        {messages.length === 0 && (
          <p className="text-xs text-muted-foreground">
            まだ会話はありません。作りたいものを書いてみてください（画像・動画・
            音声はドラッグ&ドロップでも添付できます）。
          </p>
        )}
        {messages.map((message) =>
          message.role === 'event' ? (
            <EventRow key={message.id} message={message} />
          ) : (
            <Bubble key={message.id} message={message} projectId={projectId} />
          ),
        )}
        {running && (
          <p className="animate-pulse px-1 text-[11px] text-muted-foreground">
            {runningLabel(activity)}
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
              title={item.abs_path || item.path}
              onRemove={() =>
                setAttachments((current) =>
                  current.filter((other) => other.path !== item.path),
                )
              }
            />
          ))}
          {uploading && (
            <span className="text-[11px] text-muted-foreground">アップロード中…</span>
          )}
        </div>
      )}

      <div className="flex shrink-0 flex-col gap-2">
        <MentionInput
          value={draft}
          assets={assets}
          disabled={disabled}
          placeholder={CHAT_PLACEHOLDER}
          onChange={setDraft}
          onSubmit={send}
        />
        <div className="flex items-center gap-2">
          <input
            ref={filePicker}
            type="file"
            multiple
            hidden
            accept={ATTACHMENT_ACCEPT}
            data-testid="canvas-attachment-input"
            onChange={(event) => void pick(event.target.files)}
          />
          <Button
            variant="outline"
            size="icon-sm"
            disabled={disabled || uploading}
            title="ファイルを添付（エージェントが中身を見ます）"
            aria-label="ファイルを添付"
            onClick={() => filePicker.current?.click()}
          >
            <Paperclip aria-hidden="true" />
          </Button>
          <span className="text-[11px] text-muted-foreground">Ctrl + Enter で送信</span>
          <Button
            size="sm"
            className="ml-auto"
            disabled={disabled || !sendable}
            onClick={send}
          >
            送信
          </Button>
        </div>
      </div>
    </div>
  )
}
