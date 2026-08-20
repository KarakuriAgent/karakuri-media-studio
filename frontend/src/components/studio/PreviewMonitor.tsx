import { useCallback, useEffect, useRef, useState } from 'react'
import { AlertTriangle, Pause, Play, SkipBack } from 'lucide-react'

import type { TimelineClip } from '../../types'
import { Button } from '../ui/button'
import { clipAt, formatTimecode, totalDuration } from './timeline'

/**
 * タイムラインのプレビュー（プレイヤースイッチング方式）。
 *
 * クリップごとに `<video>` を 1 つ DOM に置き、再生ヘッドの位置にあるものだけを
 * 見せて再生する。境界に来たら次のクリップへ切り替え、その `in_ms` へシークして
 * から再生を始める。音は video 要素のものをそのまま使う（Web Audio は使わない）。
 *
 * **近似であることは画面にも書いてある**: 切り替えの継ぎ目に一瞬の間が出るし、
 * 解像度・fps の正規化も入らない。正確な結果は書き出しで確かめる。
 */
export default function PreviewMonitor({
  clips,
  playheadMs,
  onSeek,
}: {
  clips: TimelineClip[]
  playheadMs: number
  onSeek: (ms: number) => void
}) {
  const [playing, setPlaying] = useState(false)
  const videos = useRef(new Map<string, HTMLVideoElement>())
  const total = totalDuration(clips)
  const current = clipAt(clips, playheadMs)

  /** 再生ヘッドがどのクリップの上にいるか（切り替えの検知に使う）。 */
  const currentId = current?.clip.id ?? null

  const register = useCallback((id: string, element: HTMLVideoElement | null) => {
    if (element) videos.current.set(id, element)
    else videos.current.delete(id)
  }, [])

  // 表示中でないクリップは必ず止める（裏で音が鳴り続けるのを防ぐ）。
  useEffect(() => {
    for (const [id, element] of videos.current) {
      if (id !== currentId && !element.paused) element.pause()
    }
  }, [currentId])

  // 再生ヘッドが動いたら、いま出ているクリップをその位置へ合わせる。再生中の
  // 微少なズレでシークし直すと音が途切れるので、離れているときだけ直す。
  useEffect(() => {
    if (!current) return
    const element = videos.current.get(current.clip.id)
    if (!element) return
    const wanted = (current.clip.in_ms + current.offsetMs) / 1000
    if (Math.abs(element.currentTime - wanted) > 0.25) {
      try {
        element.currentTime = wanted
      } catch {
        /* まだメタデータが来ていない: `loadedmetadata` で入れ直す */
      }
    }
  }, [current])

  // 再生・一時停止。再生中は `timeupdate` ではなく rAF で再生ヘッドを進める
  // （クリップの終わりを取りこぼさない細かさが要る）。
  useEffect(() => {
    if (!playing) {
      for (const element of videos.current.values()) element.pause()
      return
    }
    let frame = 0
    let last = performance.now()
    let cursor = playheadMs

    const tick = (now: number) => {
      const elapsed = now - last
      last = now
      cursor += elapsed
      if (cursor >= total) {
        onSeek(total)
        setPlaying(false)
        return
      }
      onSeek(cursor)
      const at = clipAt(clips, cursor)
      const element = at ? videos.current.get(at.clip.id) : null
      if (element && element.paused) void element.play().catch(() => undefined)
      frame = window.requestAnimationFrame(tick)
    }
    frame = window.requestAnimationFrame(tick)
    return () => window.cancelAnimationFrame(frame)
    // playheadMs はスタート地点としてだけ読む（依存に入れると毎フレーム張り直す）。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [playing, clips, total, onSeek])

  const toggle = () => {
    if (total <= 0) return
    if (!playing && playheadMs >= total) onSeek(0)
    setPlaying((value) => !value)
  }

  return (
    <div className="flex min-h-0 flex-col gap-2">
      <div className="relative aspect-video w-full overflow-hidden rounded-lg border border-border bg-black">
        {clips.map((clip) => {
          const visible = clip.id === currentId
          if (!clip.video_url) {
            return visible ? (
              <div
                key={clip.id}
                className="absolute inset-0 flex flex-col items-center justify-center gap-2 text-center text-xs text-red-300"
              >
                <AlertTriangle className="size-6" aria-hidden="true" />
                <span>メディア欠落: このクリップの動画が見つかりません</span>
              </div>
            ) : null
          }
          return (
            <video
              key={clip.id}
              ref={(element) => register(clip.id, element)}
              src={clip.video_url}
              preload="auto"
              playsInline
              className={`absolute inset-0 size-full object-contain ${
                visible ? '' : 'invisible'
              }`}
              onLoadedMetadata={(event) => {
                // メタデータが来る前に入れたシークは効かないので、来た時点で
                // もう一度その位置へ合わせる。
                if (!visible || !current) return
                event.currentTarget.currentTime =
                  (current.clip.in_ms + current.offsetMs) / 1000
              }}
            />
          )
        })}
        {clips.length === 0 && (
          <p className="absolute inset-0 flex items-center justify-center text-xs text-muted-foreground">
            クリップがありません
          </p>
        )}
      </div>

      <div className="flex items-center gap-2">
        <Button
          type="button"
          size="sm"
          variant="secondary"
          onClick={toggle}
          disabled={clips.length === 0}
        >
          {playing ? (
            <Pause className="size-4" aria-hidden="true" />
          ) : (
            <Play className="size-4" aria-hidden="true" />
          )}
          {playing ? '一時停止' : '再生'}
        </Button>
        <Button
          type="button"
          size="sm"
          variant="ghost"
          onClick={() => {
            setPlaying(false)
            onSeek(0)
          }}
          disabled={clips.length === 0}
        >
          <SkipBack className="size-4" aria-hidden="true" />
          先頭へ
        </Button>
        <span className="ml-auto font-mono text-xs text-muted-foreground">
          {formatTimecode(playheadMs)} / {formatTimecode(total)}
        </span>
      </div>

      <p className="text-[11px] text-muted-foreground">
        プレビューは近似です。正確な結果は書き出しで確認してください。
      </p>
    </div>
  )
}
