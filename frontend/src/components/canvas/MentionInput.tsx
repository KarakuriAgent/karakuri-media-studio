import { useEffect, useRef, useState } from 'react'
import type { CanvasNode } from '../../types'
import { KIND_ICON, applyMention, mentionCandidates, mentionQueryAt } from './logic'

interface Props {
  value: string
  /** @ 候補に出すカード（タイトル付きのものだけ使われる）。 */
  nodes: CanvasNode[]
  disabled: boolean
  placeholder: string
  onChange: (value: string) => void
  /** Ctrl/⌘ + Enter。候補を開いているあいだは発火しない。 */
  onSubmit: () => void
}

/**
 * `@` オートコンプリート付きの textarea。
 *
 * 参照の展開はサーバー側（router の expand_mentions）が行うので、送るのは
 * `@タイトル` を含んだ素のテキストのまま。
 */
export default function MentionInput({
  value,
  nodes,
  disabled,
  placeholder,
  onChange,
  onSubmit,
}: Props) {
  const input = useRef<HTMLTextAreaElement>(null)
  const [caret, setCaret] = useState(0)
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(0)

  const hit = open ? mentionQueryAt(value, caret) : null
  const candidates = hit ? mentionCandidates(nodes, hit.query) : []
  const showing = candidates.length > 0

  useEffect(() => {
    setActive(0)
  }, [hit?.query])

  const sync = (element: HTMLTextAreaElement) => {
    setCaret(element.selectionStart ?? element.value.length)
    setOpen(true)
  }

  const choose = (title: string) => {
    if (!hit) return
    const next = applyMention(value, hit.start, caret, title)
    onChange(next.text)
    setOpen(false)
    // 確定後は挿入した直後にカーソルを戻す（続けて書けるように）。
    requestAnimationFrame(() => {
      const element = input.current
      if (!element) return
      element.focus()
      element.setSelectionRange(next.caret, next.caret)
      setCaret(next.caret)
    })
  }

  return (
    <div className="relative">
      {showing && (
        <ul
          className="absolute bottom-full left-0 z-20 mb-1 max-h-48 w-full overflow-y-auto rounded-md border border-ink-600 bg-ink-800 py-1 shadow-lg"
          role="listbox"
          aria-label="カード候補"
        >
          {candidates.map((node, index) => (
            <li key={node.id}>
              <button
                type="button"
                role="option"
                aria-selected={index === active}
                className={`flex w-full items-center gap-2 px-2 py-1 text-left text-xs ${
                  index === active
                    ? 'bg-accent-500/20 text-slate-100'
                    : 'text-slate-300 hover:bg-ink-700'
                }`}
                // blur でリストが閉じる前に確定させる
                onMouseDown={(event) => {
                  event.preventDefault()
                  choose(node.title)
                }}
              >
                <span>{KIND_ICON[node.kind]}</span>
                <span className="min-w-0 flex-1 truncate">{node.title}</span>
              </button>
            </li>
          ))}
        </ul>
      )}

      <textarea
        ref={input}
        className="field h-16 w-full resize-none"
        value={value}
        placeholder={placeholder}
        disabled={disabled}
        onChange={(event) => {
          onChange(event.target.value)
          sync(event.target)
        }}
        onClick={(event) => sync(event.currentTarget)}
        onKeyUp={(event) => {
          if (['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key))
            sync(event.currentTarget)
        }}
        onBlur={() => setOpen(false)}
        onKeyDown={(event) => {
          if (showing) {
            if (event.key === 'ArrowDown') {
              event.preventDefault()
              setActive((current) => (current + 1) % candidates.length)
              return
            }
            if (event.key === 'ArrowUp') {
              event.preventDefault()
              setActive(
                (current) => (current - 1 + candidates.length) % candidates.length,
              )
              return
            }
            if (event.key === 'Enter' || event.key === 'Tab') {
              event.preventDefault()
              choose(candidates[active].title)
              return
            }
            if (event.key === 'Escape') {
              event.preventDefault()
              setOpen(false)
              return
            }
          }
          if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
            event.preventDefault()
            onSubmit()
          }
        }}
      />
    </div>
  )
}
