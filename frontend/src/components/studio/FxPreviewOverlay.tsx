import { useEffect, useMemo, useRef, useState } from 'react'

import type { TimelineFx } from '../../types'

/**
 * プレビューの上に重ねる演出（`FxOverlay` を透明背景で描く）。
 *
 * 描くのは**同梱の Remotion プロジェクトそのもの**（`remotion/src/FxOverlay.tsx`
 * を Vite の `@fx` エイリアスで共有する）。演出の実装を SPA 側へ写さないので、
 * ここで見えるものと書き出しの中身は同じコードから出てくる。
 *
 * - `base` は渡さない（下地は既存のプレビュー映像で、こちらは透明の板）
 * - `audio` も渡さない（音は既存のプレビューが鳴らしている）
 * - `events` は `enabled` のものだけ。Remotion の zod を通らないイベントは
 *   落として数だけ知らせる（AI が書いた途中の props でも画面は止まらない）
 * - 再生位置は親（既存のプレビュー）が正で、`seekTo` で追いかける
 *
 * `@remotion/player` は**この画面を開いたときだけ**動的 import する。Remotion
 * 連携が OFF のときは親がこのコンポーネント自体を出さないので、コードも落ちない。
 */
export default function FxPreviewOverlay({
  fx,
  fps,
  width,
  height,
  durationMs,
  playheadMs,
  playing,
  onDropped,
}: {
  fx: TimelineFx
  fps: number
  width: number
  height: number
  /** タイムラインの尺（ミリ秒）。Player の `durationInFrames` の元。 */
  durationMs: number
  playheadMs: number
  playing: boolean
  /** zod を通らずに落としたイベントの数（0 なら何も出さない）。 */
  onDropped?: (count: number) => void
}) {
  const [loaded, setLoaded] = useState<FxPlayerModule | null>(null)
  const [error, setError] = useState<string | null>(null)
  const playerRef = useRef<FxPlayerRef | null>(null)

  useEffect(() => {
    let alive = true
    void (async () => {
      try {
        // 条件分岐の中の import()。Remotion 連携が OFF なら親がここまで来ない。
        const [player, overlay, schema] = await Promise.all([
          import('@remotion/player'),
          import('@fx/FxOverlay'),
          import('@fx/schema'),
        ])
        if (!alive) return
        setLoaded({
          Player: player.Player as FxPlayerComponent,
          FxOverlay: overlay.FxOverlay as FxOverlayComponent,
          parseProps: (raw) => schema.fxOverlaySchema.parse(raw),
          parseEvent: (raw) => schema.fxEventSchema.safeParse(raw).success,
        })
      } catch (cause) {
        if (alive) setError(cause instanceof Error ? cause.message : String(cause))
      }
    })()
    return () => {
      alive = false
    }
  }, [])

  const prepared = useMemo(() => {
    if (!loaded) return { props: null, dropped: 0, error: null as string | null }
    const events = fx.events
      .filter((item) => item.enabled)
      .map((item) => item.event)
    const kept = events.filter((event) => loaded.parseEvent(event))
    try {
      return {
        props: loaded.parseProps({
          fps,
          width,
          height,
          // 下地は既存のプレビュー映像。ここは透明の板として重ねるだけ。
          backgroundColor: 'transparent',
          ...(fx.theme ? { theme: fx.theme } : {}),
          ...(fx.ambient ? { ambient: fx.ambient } : {}),
          ...(fx.seed == null ? {} : { seed: fx.seed }),
          events: kept,
        }),
        dropped: events.length - kept.length,
        error: null,
      }
    } catch (cause) {
      return {
        props: null,
        dropped: events.length - kept.length,
        error: cause instanceof Error ? cause.message : String(cause),
      }
    }
  }, [loaded, fx, fps, width, height])
  const props = prepared.props

  // 落としたイベントの数は**描画のあと**に知らせる（描画中に親の state を
  // 触ると React が警告する）。
  useEffect(() => {
    onDropped?.(prepared.dropped)
    // onDropped は毎描画で変わりうるので依存に入れない。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [prepared.dropped])

  const durationInFrames = Math.max(1, Math.round((durationMs / 1000) * fps))

  // 再生位置は既存のプレビューが正。フレームに直して追いかける。
  useEffect(() => {
    if (!playerRef.current) return
    const frame = Math.min(
      durationInFrames - 1,
      Math.max(0, Math.round((playheadMs / 1000) * fps)),
    )
    playerRef.current.seekTo(frame)
  }, [playheadMs, fps, durationInFrames, props])

  // 再生 / 停止も既存のプレビューに合わせる（音は鳴らさない）。
  useEffect(() => {
    const player = playerRef.current
    if (!player) return
    if (playing) player.play()
    else player.pause()
  }, [playing, props])

  const failure = error ?? prepared.error
  if (failure) {
    return (
      <p className="pointer-events-none absolute inset-x-0 bottom-0 z-[3] bg-red-950/70 px-2 py-1 text-[10px] text-red-200">
        演出を描けませんでした: {failure}
      </p>
    )
  }
  if (!loaded || !props) return null
  const { Player, FxOverlay } = loaded
  return (
    <div className="pointer-events-none absolute inset-0 z-[3]">
      <Player
        ref={playerRef}
        component={FxOverlay}
        inputProps={props}
        durationInFrames={durationInFrames}
        fps={fps}
        compositionWidth={width}
        compositionHeight={height}
        controls={false}
        clickToPlay={false}
        spaceKeyToPlayOrPause={false}
        doubleClickToFullscreen={false}
        initiallyMuted
        acknowledgeRemotionLicense
        style={{ width: '100%', height: '100%', backgroundColor: 'transparent' }}
      />
    </div>
  )
}

// 動的 import したものを取り回すための最小の型（`@remotion/player` と
// `@fx/FxOverlay` をここでしか触らないので、外へは出さない）。
type FxPlayerRef = { seekTo: (frame: number) => void; play: () => void; pause: () => void }
type FxOverlayComponent = React.ComponentType<Record<string, unknown>>
type FxPlayerComponent = React.ComponentType<
  {
    component: FxOverlayComponent
    inputProps: Record<string, unknown>
    durationInFrames: number
    fps: number
    compositionWidth: number
    compositionHeight: number
    controls?: boolean
    clickToPlay?: boolean
    spaceKeyToPlayOrPause?: boolean
    doubleClickToFullscreen?: boolean
    initiallyMuted?: boolean
    acknowledgeRemotionLicense?: boolean
    style?: React.CSSProperties
  } & { ref?: React.Ref<FxPlayerRef> }
>
interface FxPlayerModule {
  Player: FxPlayerComponent
  FxOverlay: FxOverlayComponent
  parseProps: (raw: Record<string, unknown>) => Record<string, unknown>
  parseEvent: (raw: unknown) => boolean
}
