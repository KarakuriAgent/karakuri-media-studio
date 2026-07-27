import { useEffect, useState, type ReactNode } from 'react'

export function Modal({
  title,
  onClose,
  children,
  wide,
}: {
  title: string
  onClose: () => void
  children: ReactNode
  wide?: boolean
}) {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/70 p-4">
      <div
        className={`card flex max-h-[90vh] w-full flex-col overflow-hidden shadow-2xl ${
          wide ? 'max-w-4xl' : 'max-w-2xl'
        }`}
      >
        <div className="flex items-center justify-between border-b border-ink-600 px-4 py-3">
          <h2 className="text-sm font-semibold text-slate-100">{title}</h2>
          <button className="btn-ghost !px-2 !py-1" onClick={onClose} title="閉じる">
            ✕
          </button>
        </div>
        <div className="flex-1 overflow-y-auto p-4">{children}</div>
      </div>
    </div>
  )
}

export function Banner({
  tone = 'error',
  children,
  onClose,
}: {
  tone?: 'error' | 'warn' | 'info'
  children: ReactNode
  onClose?: () => void
}) {
  const tones = {
    error: 'border-red-900/70 bg-red-950/50 text-red-200',
    warn: 'border-amber-900/70 bg-amber-950/40 text-amber-200',
    info: 'border-sky-900/70 bg-sky-950/40 text-sky-200',
  }
  return (
    <div
      className={`flex items-start gap-2 rounded-md border px-3 py-2 text-xs ${tones[tone]}`}
    >
      <div className="flex-1 whitespace-pre-wrap break-words">{children}</div>
      {onClose && (
        <button className="shrink-0 opacity-70 hover:opacity-100" onClick={onClose}>
          ✕
        </button>
      )}
    </div>
  )
}

export function FieldError({ message }: { message?: string }) {
  if (!message) return null
  return <p className="mt-1 text-xs text-red-400">{message}</p>
}

export function StatusBadge({ status }: { status: string }) {
  const map: Record<string, string> = {
    queued: 'border-slate-600 bg-slate-800 text-slate-300',
    prompting: 'border-sky-800 bg-sky-950 text-sky-300',
    running: 'border-amber-800 bg-amber-950 text-amber-300',
    done: 'border-emerald-800 bg-emerald-950 text-emerald-300',
    failed: 'border-red-800 bg-red-950 text-red-300',
    canceled: 'border-slate-600 bg-slate-800 text-slate-400',
  }
  const label: Record<string, string> = {
    queued: 'キュー',
    prompting: 'プロンプト生成中',
    running: '実行中',
    done: '完了',
    failed: '失敗',
    canceled: 'キャンセル',
  }
  return (
    <span
      className={`chip !px-2 !py-0.5 ${map[status] ?? 'border-ink-500 bg-ink-700 text-slate-300'}`}
    >
      {label[status] ?? status}
    </span>
  )
}

/** NSFW 印（表示トグルがオンのときだけ現れる）。 */
export function NsfwBadge({ className = '' }: { className?: string }) {
  return (
    <span
      className={`chip !px-1.5 !py-0.5 border-pink-800 bg-pink-950/80 text-pink-300 ${className}`}
      title="NSFW"
    >
      🫣
    </span>
  )
}

/** NSFW フラグの手動トグル（押すと即時反映）。 */
export function NsfwToggle({
  nsfw,
  onToggle,
  disabled,
  className = '',
}: {
  nsfw: boolean
  onToggle: (nsfw: boolean) => void
  disabled?: boolean
  className?: string
}) {
  return (
    <button
      className={`btn-ghost !py-1 text-xs ${
        nsfw ? '!border-pink-700 !bg-pink-950/60 !text-pink-300' : ''
      } ${className}`}
      disabled={disabled}
      title={nsfw ? 'NSFW 指定を外す' : 'NSFW として印を付ける'}
      onClick={() => onToggle(!nsfw)}
    >
      🫣 NSFW
    </button>
  )
}

export function CopyButton({ text, label = 'コピー' }: { text: string; label?: string }) {
  const [copied, setCopied] = useState(false)
  return (
    <button
      className="btn-ghost !px-2 !py-1 text-xs"
      onClick={() => {
        void navigator.clipboard
          ?.writeText(text)
          .then(() => {
            setCopied(true)
            window.setTimeout(() => setCopied(false), 1200)
          })
          .catch(() => setCopied(false))
      }}
    >
      {copied ? 'コピーしました' : label}
    </button>
  )
}
