import { useEffect, useState, type ReactNode } from 'react'
import { EyeOff, X } from 'lucide-react'

import { cn } from '@/lib/utils'
import { Button } from './ui/button'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from './ui/dialog'

/**
 * アプリ共通のモーダル。中身は shadcn/ui（Radix Dialog）なのでフォーカストラップと
 * 開閉アニメーションが付くが、Esc と背景クリックの扱いは従来どおり自前で持つ
 * （Radix 側の dismiss を止めて二重に onClose が飛ばないようにしている）。
 */
export function Modal({
  title,
  onClose,
  children,
  wide,
  closeOnBackdrop,
}: {
  title: string
  onClose: () => void
  children: ReactNode
  wide?: boolean
  /** 背景クリックでも閉じる（入力途中を失いうるモーダルでは付けない）。 */
  closeOnBackdrop?: boolean
}) {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <Dialog open>
      <DialogContent
        hideClose
        aria-describedby={undefined}
        // `card` は見た目の指定に加えて「モーダル本体」を掴む目印としても使われている
        className={`card ${wide ? 'max-w-4xl' : 'max-w-2xl'}`}
        overlayProps={{
          className: 'z-40 p-4',
          onClick: closeOnBackdrop ? onClose : undefined,
        }}
        // Esc と外側クリックは上のハンドラ／オーバーレイ側で処理する
        onEscapeKeyDown={(event) => event.preventDefault()}
        onInteractOutside={(event) => event.preventDefault()}
      >
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <Button variant="ghost" size="icon-xs" onClick={onClose} title="閉じる">
            <X />
            <span className="sr-only">閉じる</span>
          </Button>
        </DialogHeader>
        <div className="flex-1 overflow-y-auto p-4">{children}</div>
      </DialogContent>
    </Dialog>
  )
}

/** 生成フォームの 1 ブロック（見出し + 任意の右肩ボタン）。 */
export function Section({
  title,
  children,
  right,
}: {
  title: string
  children: ReactNode
  right?: ReactNode
}) {
  return (
    <section className="card p-3">
      <div className="mb-2 flex items-center justify-between">
        <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
          {title}
        </h3>
        {right}
      </div>
      {children}
    </section>
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
      className={`flex items-start gap-2 rounded-md border px-3 py-2 text-xs shadow-elevation-1 ${tones[tone]}`}
    >
      <div className="flex-1 whitespace-pre-wrap break-words">{children}</div>
      {onClose && (
        <button
          className="mt-0.5 shrink-0 rounded-sm opacity-70 transition-opacity hover:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
          onClick={onClose}
          title="閉じる"
        >
          <X className="size-3.5" />
          <span className="sr-only">閉じる</span>
        </button>
      )}
    </div>
  )
}

export function FieldError({ message }: { message?: string }) {
  if (!message) return null
  return <p className="mt-1 text-xs text-red-400">{message}</p>
}

/** 状態名（バッジ表記。読み上げ用のラベルでも同じ言い回しを使う）。 */
export const STATUS_LABELS: Record<string, string> = {
  queued: 'キュー',
  prompting: 'プロンプト生成中',
  running: '実行中',
  done: '完了',
  failed: '失敗',
  canceled: 'キャンセル',
}

export function StatusBadge({ status }: { status: string }) {
  const map: Record<string, string> = {
    queued: 'border-border bg-secondary text-muted-foreground',
    prompting: 'border-sky-800 bg-sky-950 text-sky-300',
    running: 'border-amber-800 bg-amber-950 text-amber-300',
    done: 'border-emerald-800 bg-emerald-950 text-emerald-300',
    failed: 'border-destructive/50 bg-destructive/15 text-red-300',
    canceled: 'border-border bg-secondary text-muted-foreground',
  }
  return (
    <span
      className={`chip !px-2 !py-0.5 ${map[status] ?? 'border-border bg-card text-foreground/85'}`}
    >
      {STATUS_LABELS[status] ?? status}
    </span>
  )
}

/** NSFW 印（表示トグルがオンのときだけ現れる）。 */
export function NsfwBadge({ className = '' }: { className?: string }) {
  return (
    <span
      // relative が無いと中の sr-only（絶対配置）の包含ブロックが body になり、
      // ページ全体が「何もない領域」まで縦スクロールできてしまう。
      className={`chip relative !px-1.5 !py-0.5 border-pink-800 bg-pink-950/80 text-pink-300 ${className}`}
      title="NSFW"
    >
      <EyeOff className="size-3" aria-hidden="true" />
      <span className="sr-only">NSFW</span>
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
    <Button
      variant="outline"
      size="sm"
      className={cn(
        nsfw && 'border-pink-700 bg-pink-950/60 text-pink-300 hover:border-pink-600 hover:bg-pink-950 hover:text-pink-200',
        className,
      )}
      disabled={disabled}
      title={nsfw ? 'NSFW 指定を外す' : 'NSFW として印を付ける'}
      onClick={() => onToggle(!nsfw)}
    >
      <EyeOff aria-hidden="true" />
      NSFW
    </Button>
  )
}

export function CopyButton({ text, label = 'コピー' }: { text: string; label?: string }) {
  const [copied, setCopied] = useState(false)
  return (
    <Button
      variant="outline"
      size="sm"
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
    </Button>
  )
}
