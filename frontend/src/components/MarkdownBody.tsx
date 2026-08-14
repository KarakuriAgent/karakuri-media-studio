import type { Components } from 'react-markdown'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

import { cn } from '@/lib/utils'

/** http / https / mailto 以外のスキーム（javascript: など）はリンクにしない。 */
function allowedHref(href: string | undefined): string | null {
  if (!href) return null
  const trimmed = href.trim()
  const lower = trimmed.toLowerCase()
  if (
    lower.startsWith('https:') ||
    lower.startsWith('http:') ||
    lower.startsWith('mailto:')
  ) {
    return trimmed
  }
  return null
}

function looksLikeActionJson(body: string): boolean {
  const trimmed = body.trim()
  return trimmed.startsWith('{') && /"action"\s*:/.test(trimmed)
}

/**
 * 末尾の json / 無名フェンスだけを切り出す。途中のコードブロックは残す。
 * Grok は応答の最後に `"action"` 付き JSON を置く。
 */
function splitTrailingAction(text: string): { body: string; action: string | null } {
  const endMatch = text.match(/\n```[ \t]*(?:\r?\n)*$/)
  if (!endMatch || endMatch.index === undefined) return { body: text, action: null }

  const beforeClose = text.slice(0, endMatch.index)
  // 直前改行は必須にしない。Grok は「本文。```json」と繋ぐことがある。
  const openRe = /```(?:json)?[ \t]*\r?\n/gi
  let last: { index: number; header: string } | null = null
  let found: RegExpExecArray | null
  while ((found = openRe.exec(beforeClose)) !== null) {
    last = { index: found.index, header: found[0] }
  }
  if (!last) return { body: text, action: null }

  const fenceBody = beforeClose.slice(last.index + last.header.length)
  if (!looksLikeActionJson(fenceBody)) return { body: text, action: null }

  return {
    body: text.slice(0, last.index).trimEnd(),
    action: fenceBody.trim(),
  }
}

const components: Components = {
  p({ children }) {
    return <p className="mb-1.5 last:mb-0 [overflow-wrap:anywhere]">{children}</p>
  },
  ul({ children }) {
    return (
      <ul className="mb-1.5 list-disc space-y-0.5 pl-4 last:mb-0">{children}</ul>
    )
  },
  ol({ children }) {
    return (
      <ol className="mb-1.5 list-decimal space-y-0.5 pl-4 last:mb-0">{children}</ol>
    )
  },
  li({ children }) {
    return <li className="leading-snug [overflow-wrap:anywhere]">{children}</li>
  },
  strong({ children }) {
    return <strong className="font-semibold">{children}</strong>
  },
  em({ children }) {
    return <em>{children}</em>
  },
  h1({ children }) {
    return (
      <h1 className="mb-1.5 mt-3 text-base font-semibold first:mt-0 [overflow-wrap:anywhere]">
        {children}
      </h1>
    )
  },
  h2({ children }) {
    return (
      <h2 className="mb-1.5 mt-3 text-[15px] font-semibold first:mt-0 [overflow-wrap:anywhere]">
        {children}
      </h2>
    )
  },
  h3({ children }) {
    return (
      <h3 className="mb-1 mt-2.5 font-semibold first:mt-0 [overflow-wrap:anywhere]">
        {children}
      </h3>
    )
  },
  a({ href, children }) {
    const safe = allowedHref(href)
    if (!safe) return <>{children}</>
    return (
      <a
        href={safe}
        target="_blank"
        rel="noreferrer"
        className="text-accent-400 underline [overflow-wrap:anywhere]"
      >
        {children}
      </a>
    )
  },
  code({ className, children }) {
    const fenced = Boolean(className)
    if (fenced) {
      return <code className={cn('font-mono text-xs', className)}>{children}</code>
    }
    return (
      <code className="break-all rounded bg-muted px-1 py-px font-mono text-[0.85em]">
        {children}
      </code>
    )
  },
  pre({ children }) {
    return (
      <pre className="my-2 min-w-0 max-h-64 max-w-full overflow-x-auto rounded-md border border-border bg-surface-sunken p-2 text-xs last:mb-0 [&_code]:bg-transparent [&_code]:p-0">
        {children}
      </pre>
    )
  },
  blockquote({ children }) {
    return (
      <blockquote className="my-2 border-l-2 border-border pl-2 text-muted-foreground last:mb-0 [overflow-wrap:anywhere]">
        {children}
      </blockquote>
    )
  },
  hr() {
    return <hr className="my-2 border-border" />
  },
  table({ children }) {
    return (
      <div className="my-2 min-w-0 w-full max-w-full overflow-x-auto last:mb-0">
        <table className="w-max min-w-full border-collapse text-left text-xs">{children}</table>
      </div>
    )
  },
  thead({ children }) {
    return <thead>{children}</thead>
  },
  tbody({ children }) {
    return <tbody>{children}</tbody>
  },
  tr({ children }) {
    return <tr>{children}</tr>
  },
  th({ children }) {
    return (
      <th className="border border-border bg-secondary px-2 py-1 font-semibold [overflow-wrap:anywhere]">
        {children}
      </th>
    )
  },
  td({ children }) {
    return <td className="border border-border px-2 py-1 [overflow-wrap:anywhere]">{children}</td>
  },
}

export function MarkdownBody({
  text,
  className,
  splitAction = false,
}: {
  text: string
  className?: string
  splitAction?: boolean
}) {
  if (!text.trim()) return null

  const { body, action } = splitAction
    ? splitTrailingAction(text)
    : { body: text, action: null }
  const main = body.trim()

  return (
    <div
      className={cn(
        'min-w-0 w-full max-w-full overflow-x-hidden break-words [overflow-wrap:anywhere]',
        className,
      )}
    >
      {/* rehype-raw は使わない（生 HTML を解釈しない）。 */}
      {main ? (
        <Markdown remarkPlugins={[remarkGfm]} components={components}>
          {body}
        </Markdown>
      ) : null}
      {action != null && (
        <details
          className={cn(
            'rounded-md border border-border bg-surface-sunken',
            main && 'mt-2',
          )}
        >
          <summary className="cursor-pointer select-none px-2 py-1 text-xs text-muted-foreground">
            アクション
          </summary>
          <pre className="min-w-0 max-h-64 max-w-full overflow-x-auto border-t border-border p-2 font-mono text-xs">
            {action}
          </pre>
        </details>
      )}
    </div>
  )
}
