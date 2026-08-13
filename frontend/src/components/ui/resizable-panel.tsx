import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type PointerEvent as ReactPointerEvent,
} from 'react'

import { cn } from '@/lib/utils'

/** 矢印キー 1 打ぶんの変化量（px）。 */
export const RESIZE_STEP = 16

/** lg（1024px）以上か。未満ではリサイズを止め、切り替え表示／ドロワーに寄せる。 */
export function useIsWide(): boolean {
  const [wide, setWide] = useState(true)
  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return
    const query = window.matchMedia('(min-width: 1024px)')
    const apply = () => setWide(query.matches)
    apply()
    query.addEventListener('change', apply)
    return () => query.removeEventListener('change', apply)
  }, [])
  return wide
}

function clampSize(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, Math.round(value)))
}

function storedSize(key: string, fallback: number, min: number, max: number): number {
  try {
    const raw = window.localStorage.getItem(key)
    const value = raw == null ? Number.NaN : Number(raw)
    return Number.isFinite(value) ? clampSize(value, min, max) : fallback
  } catch {
    return fallback
  }
}

export interface ResizablePanel {
  /** いまの幅（axis='x'）または高さ（axis='y'）。単位は px。 */
  size: number
  dragging: boolean
  axis: 'x' | 'y'
  min: number
  max: number
  handleProps: {
    onPointerDown: (event: ReactPointerEvent<HTMLDivElement>) => void
    onPointerMove: (event: ReactPointerEvent<HTMLDivElement>) => void
    onPointerUp: (event: ReactPointerEvent<HTMLDivElement>) => void
    onPointerCancel: (event: ReactPointerEvent<HTMLDivElement>) => void
    onDoubleClick: () => void
    onKeyDown: (event: ReactKeyboardEvent<HTMLDivElement>) => void
  }
}

export interface ResizablePanelOptions {
  /**
   * ハンドルが対象の右／下ではなく左／上の縁にあるとき true。
   *
   * `axis='x'` の右カラム（ハンドルはパネルの左縁）は左へ引くと広がるので、
   * ポインタの移動量と矢印キーの向きを反転させる。`axis='y'` のハンドルは
   * 既定で上端に置く前提なので最初から反転している。
   */
  inverted?: boolean
}

/**
 * ドラッグでサイズを変えられるパネル（サイドバーの幅・履歴の高さ）。
 *
 * 値は localStorage に持つので次に開いたときも同じ大きさで出る。ハンドルの
 * ダブルクリックで既定値へ戻し、矢印キーでも ±{@link RESIZE_STEP}px 動かせる。
 */
export function useResizablePanel(
  storageKey: string,
  { initial, min, max }: { initial: number; min: number; max: number },
  axis: 'x' | 'y',
  { inverted = false }: ResizablePanelOptions = {},
): ResizablePanel {
  const [size, setSize] = useState(() => storedSize(storageKey, initial, min, max))
  const [dragging, setDragging] = useState(false)
  // ドラッグ開始時のポインタ位置とサイズ（移動量を足し込むための起点）。
  const origin = useRef<{ position: number; size: number } | null>(null)
  // 上端ハンドル（axis='y'）と右カラムの左縁ハンドルは、向きが逆。
  const flipped = axis === 'y' ? !inverted : inverted

  // ドラッグ中は 1 フレームごとに size が変わるので、落ち着いた値だけ書き込む。
  useEffect(() => {
    const timer = window.setTimeout(() => {
      try {
        window.localStorage.setItem(storageKey, String(size))
      } catch {
        /* 保存できない環境ではこのセッションのあいだだけ効く */
      }
    }, 200)
    return () => window.clearTimeout(timer)
  }, [size, storageKey])

  // ドラッグ中は文字が選択されないようにする（ハンドルを掴んだまま動かすため）。
  useEffect(() => {
    if (!dragging) return
    document.body.classList.add('select-none')
    return () => document.body.classList.remove('select-none')
  }, [dragging])

  const nudge = useCallback(
    (delta: number) => setSize((previous) => clampSize(previous + delta, min, max)),
    [max, min],
  )

  const handleProps = {
    onPointerDown: (event: ReactPointerEvent<HTMLDivElement>) => {
      if (event.button !== 0) return
      event.preventDefault()
      origin.current = {
        position: axis === 'x' ? event.clientX : event.clientY,
        size,
      }
      event.currentTarget.setPointerCapture(event.pointerId)
      setDragging(true)
    },
    onPointerMove: (event: ReactPointerEvent<HTMLDivElement>) => {
      const start = origin.current
      if (!start) return
      const position = axis === 'x' ? event.clientX : event.clientY
      const delta = flipped ? start.position - position : position - start.position
      setSize(clampSize(start.size + delta, min, max))
    },
    onPointerUp: (event: ReactPointerEvent<HTMLDivElement>) => {
      origin.current = null
      setDragging(false)
      if (event.currentTarget.hasPointerCapture(event.pointerId))
        event.currentTarget.releasePointerCapture(event.pointerId)
    },
    onPointerCancel: () => {
      origin.current = null
      setDragging(false)
    },
    onDoubleClick: () => setSize(clampSize(initial, min, max)),
    onKeyDown: (event: ReactKeyboardEvent<HTMLDivElement>) => {
      const [grow, shrink] =
        axis === 'x'
          ? flipped
            ? (['ArrowLeft', 'ArrowRight'] as const)
            : (['ArrowRight', 'ArrowLeft'] as const)
          : flipped
            ? (['ArrowUp', 'ArrowDown'] as const)
            : (['ArrowDown', 'ArrowUp'] as const)
      if (event.key === grow) nudge(RESIZE_STEP)
      else if (event.key === shrink) nudge(-RESIZE_STEP)
      else if (event.key === 'Home') setSize(min)
      else if (event.key === 'End') setSize(max)
      else return
      event.preventDefault()
    },
  }

  return { size, dragging, axis, min, max, handleProps }
}

/**
 * リサイズ用のつまみ（lg 以上でのみ出す）。
 *
 * ドラッグ中は全面のオーバーレイを重ね、iframe / video がポインタを奪って
 * 追従が途切れるのを防ぐ。
 */
export function ResizeHandle({
  panel,
  label,
  className,
}: {
  panel: ResizablePanel
  label: string
  className?: string
}) {
  const vertical = panel.axis === 'x'
  const bar = panel.dragging
    ? 'bg-primary'
    : 'bg-transparent group-hover:bg-primary group-focus-visible:bg-primary'
  return (
    <>
      <div
        role="separator"
        aria-orientation={vertical ? 'vertical' : 'horizontal'}
        aria-label={label}
        aria-valuenow={panel.size}
        aria-valuemin={panel.min}
        aria-valuemax={panel.max}
        tabIndex={0}
        title={`${label}（ドラッグで変更 / ダブルクリックで既定に戻す）`}
        className={cn(
          'group relative hidden shrink-0 touch-none focus-visible:outline-none lg:block',
          vertical ? 'w-1.5 cursor-col-resize' : 'h-1.5 cursor-row-resize',
          className,
        )}
        {...panel.handleProps}
      >
        <span
          aria-hidden
          className={cn(
            'pointer-events-none absolute transition-colors',
            bar,
            vertical
              ? 'inset-y-0 left-1/2 w-[3px] -translate-x-1/2'
              : 'inset-x-0 top-1/2 h-[3px] -translate-y-1/2',
          )}
        />
      </div>
      {panel.dragging && (
        <div
          className={cn(
            'fixed inset-0 z-50',
            vertical ? 'cursor-col-resize' : 'cursor-row-resize',
          )}
        />
      )}
    </>
  )
}
