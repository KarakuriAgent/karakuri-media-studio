import { act, cleanup, render } from '@testing-library/react'
import React from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'

// FxOverlay の video イベント（remotion/src/fx/Video.tsx）を、Remotion の描画の
// 仕組みを差し替えて描く。書き出しで「Fetching … from server was called but not
// cleared」で落ちていたのは、尺を読み終えたところで OffthreadVideo を <Loop> で
// 包み直していたため（外された OffthreadVideo の待ちが残った）。ここでは
// OffthreadVideo が 1 度だけ置かれ、待ち（delayRender）がすべて解かれることを確かめる。

const state = vi.hoisted(() => ({
  mounts: 0,
  unmounts: 0,
  open: new Set<number>(),
  next: 1,
  resolveMeta: null as null | ((value: { durationInSeconds: number }) => void),
}))

vi.mock('remotion', () => {
  const OffthreadVideo = () => {
    React.useEffect(() => {
      state.mounts += 1
      return () => {
        state.unmounts += 1
      }
    }, [])
    return null
  }
  return {
    OffthreadVideo,
    Loop: ({ children }: { children: React.ReactNode }) => <>{children}</>,
    delayRender: () => {
      const handle = state.next++
      state.open.add(handle)
      return handle
    },
    continueRender: (handle: number) => {
      state.open.delete(handle)
    },
    interpolate: () => 1,
    staticFile: (path: string) => path,
    useCurrentFrame: () => 0,
    useVideoConfig: () => ({ fps: 30, durationInFrames: 90, width: 1080, height: 1920 }),
  }
})

vi.mock('@remotion/media-utils', () => ({
  getVideoMetadata: () =>
    new Promise((resolve) => {
      state.resolveMeta = resolve
    }),
}))

vi.mock('@fx/lib/fx', () => ({
  anchorCenter: () => ({ cx: 0.5, cy: 0.5 }),
  motionTransform: () => ({ transform: '', scale: 1 }),
  toFrames: (seconds: number, fps: number) => Math.round(seconds * fps),
  useFxCtx: () => ({
    width: 1080,
    height: 1920,
    fs: (value: number) => value,
    color: (value: string) => value,
  }),
}))

afterEach(() => {
  cleanup()
  state.mounts = 0
  state.unmounts = 0
  state.open.clear()
  state.resolveMeta = null
})

const EVENT = {
  type: 'video',
  t: 0,
  src: 'http://127.0.0.1:8000/library/video/mark.webm',
  anchor: 'center',
  margin: 0.05,
  w: 0.3,
  maxH: 0.5,
  fit: 'contain',
  rot: 0,
  opacity: 1,
  motion: 'none',
  fade: 0,
  in: 0,
  playbackRate: 1,
  loop: true,
  transparent: true,
} as const

/** remotion/ 側の @types/react と型が食い違うので、描ける形に寄せて読む。 */
async function loadVideoEvent() {
  const module = await import('@fx/fx/Video')
  return module.FxVideoEvent as unknown as React.FC<{ ev: never; seed: number }>
}

describe('FxOverlay の video イベント', () => {
  it('尺を読み終えてから動画を 1 度だけ置き、待ちを残さない', async () => {
    const FxVideoEvent = await loadVideoEvent()
    render(<FxVideoEvent ev={EVENT as never} seed={1} />)
    // 尺を読んでいるあいだは動画を置かない（あとで包み直さないため）
    expect(state.mounts).toBe(0)
    expect(state.open.size).toBe(1)

    await act(async () => {
      state.resolveMeta?.({ durationInSeconds: 3.2 })
      await new Promise((resolve) => setTimeout(resolve, 0))
    })
    expect(state.mounts).toBe(1)
    expect(state.unmounts).toBe(0)
    expect(state.open.size).toBe(0)
  })

  it('尺が分かっている（loopSeconds）なら待たずに置く', async () => {
    const FxVideoEvent = await loadVideoEvent()
    render(<FxVideoEvent ev={{ ...EVENT, loopSeconds: 1.5 } as never} seed={1} />)
    expect(state.mounts).toBe(1)
    expect(state.open.size).toBe(0)
  })

  it('尺を読み終える前に外されても待ちを残さない', async () => {
    const FxVideoEvent = await loadVideoEvent()
    const view = render(<FxVideoEvent ev={EVENT as never} seed={1} />)
    expect(state.open.size).toBe(1)
    view.unmount()
    expect(state.open.size).toBe(0)
  })
})
