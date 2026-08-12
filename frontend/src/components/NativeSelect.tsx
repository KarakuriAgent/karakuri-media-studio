import * as React from 'react'
import { ChevronDown } from 'lucide-react'

import { cn } from '@/lib/utils'

/**
 * 素の `<select>` を shadcn の SelectTrigger と同じ見た目にしたもの。
 *
 * Radix の Select と違って `<option>` をそのまま持てるので、選択肢が多い欄
 * （モデル名・アセットのパスなど）でもブラウザ本来の絞り込みが効く。OS 既定の
 * 矢印は `appearance-none` で消し、lucide の ChevronDown を重ねて出す。
 * ドロップダウン自体の配色は `color-scheme: dark` でダークに寄せる。
 */
const NativeSelect = React.forwardRef<
  HTMLSelectElement,
  React.SelectHTMLAttributes<HTMLSelectElement>
>(({ className, children, ...props }, ref) => (
  // 幅は `<select>` 側が決める（ブロック内では w-full、flex 行の中では中身なり）。
  <div className="relative">
    <select
      ref={ref}
      className={cn(
        'flex h-9 w-full appearance-none rounded-md border border-input bg-surface-sunken py-2 pl-3 pr-8',
        'text-sm text-foreground shadow-sm transition-colors [color-scheme:dark]',
        'focus-visible:border-ring focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40',
        'disabled:cursor-not-allowed disabled:opacity-40',
        className,
      )}
      {...props}
    >
      {children}
    </select>
    <ChevronDown
      aria-hidden="true"
      className="pointer-events-none absolute right-2.5 top-1/2 size-4 -translate-y-1/2 opacity-60"
    />
  </div>
))
NativeSelect.displayName = 'NativeSelect'

export { NativeSelect }
