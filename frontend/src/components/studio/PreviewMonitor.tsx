import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { AlertTriangle, Pause, Play, SkipBack } from 'lucide-react'

import type { TimelineClip, TimelineTrack } from '../../types'
import { Button } from '../ui/button'
import {
  SUBTITLE_SIZE_RATIO,
  clipAt,
  clipsOfTrack,
  formatTimecode,
  speedOf,
  subtitleStyle,
  subtitleText,
  totalDuration,
  transitionAt,
} from './timeline'

/**
 * タイムラインのプレビュー（プレイヤースイッチング方式）。
 *
 * 映像クリップごとに `<video>` を 1 つ DOM に置き、再生ヘッドの位置にあるものだけを
 * 見せて再生する。境界に来たら次のクリップへ切り替え、その `in_ms` へシークして
 * から再生を始める。**次のクリップは先に `in_ms` へシークして待たせてある**ので、
 * 切り替えのギャップはそのぶん短い。速度を変えたクリップは `playbackRate` で追う。
 *
 * 近似しているもの:
 *
 * - **繋ぎ** … クロスフェード / 黒・白フェードだけ 2 枚重ねで近似する。ワイプや
 *   スライドはカット表示（書き出しで確認する）。
 * - **音声トラック（BGM / SE）** … Web Audio（`AudioBufferSourceNode` +
 *   `GainNode`）で再生ヘッドに合わせて鳴らす。フェードは gain のスケジュール。
 * - **テロップ** … `<div>` の重ね書き（位置・大きさ・色は反映するが、書体と
 *   縁取りは焼き込みと同じにはならない）。
 *
 * **近似であることは画面にも書いてある**。正確な結果は書き出しで確かめる。
 */
export default function PreviewMonitor({
  clips,
  tracks,
  playheadMs,
  onSeek,
}: {
  /** タイムラインの全クリップ（トラックはここから引く）。 */
  clips: TimelineClip[]
  tracks: TimelineTrack[]
  playheadMs: number
  onSeek: (ms: number) => void
}) {
  const [playing, setPlaying] = useState(false)
  const videos = useRef(new Map<string, HTMLVideoElement>())

  const videoTrack = tracks.find((track) => track.kind === 'video') ?? null
  const videoClips = useMemo(
    () => (videoTrack ? clipsOfTrack(clips, videoTrack.id) : []),
    [clips, videoTrack],
  )
  const audioClips = useMemo(
    () =>
      tracks
        .filter((track) => track.kind === 'audio' && !track.muted)
        .flatMap((track) => clipsOfTrack(clips, track.id))
        .filter((clip) => clip.video_url && !clip.missing),
    [clips, tracks],
  )
  const subtitleClips = useMemo(
    () =>
      tracks
        .filter((track) => track.kind === 'subtitle' && !track.muted)
        .flatMap((track) => clipsOfTrack(clips, track.id)),
    [clips, tracks],
  )

  const total = totalDuration(videoClips)
  const current = clipAt(videoClips, playheadMs)
  const blend = transitionAt(videoClips, playheadMs)

  /** 再生ヘッドがどのクリップの上にいるか（切り替えの検知に使う）。 */
  const currentId = current?.clip.id ?? null
  /** 繋ぎで重ねて見せるクリップ（無ければ null）。 */
  const incomingId = blend?.to.id ?? null

  const register = useCallback((id: string, element: HTMLVideoElement | null) => {
    if (element) videos.current.set(id, element)
    else videos.current.delete(id)
  }, [])

  // 表示中でないクリップは必ず止める（裏で音が鳴り続けるのを防ぐ）。
  useEffect(() => {
    for (const [id, element] of videos.current) {
      if (id !== currentId && id !== incomingId && !element.paused) element.pause()
    }
  }, [currentId, incomingId])

  // 再生ヘッドが動いたら、いま出ているクリップをその位置へ合わせる。再生中の
  // 微少なズレでシークし直すと音が途切れるので、離れているときだけ直す。
  useEffect(() => {
    if (!current) return
    const element = videos.current.get(current.clip.id)
    if (!element) return
    element.playbackRate = speedOf(current.clip)
    const wanted =
      (current.clip.in_ms + current.offsetMs * speedOf(current.clip)) / 1000
    if (Math.abs(element.currentTime - wanted) > 0.25) {
      try {
        element.currentTime = wanted
      } catch {
        /* まだメタデータが来ていない: `loadedmetadata` で入れ直す */
      }
    }
  }, [current])

  // 次のクリップを先に `in_ms` へシークして待たせておく（切り替えの間を縮める）。
  useEffect(() => {
    if (!current) return
    const next = videoClips[current.index + 1]
    if (!next) return
    const element = videos.current.get(next.id)
    if (!element || !element.paused) return
    const wanted = next.in_ms / 1000
    if (Math.abs(element.currentTime - wanted) > 0.25) {
      try {
        element.currentTime = wanted
      } catch {
        /* メタデータ待ち。`loadedmetadata` の時点で入れ直す */
      }
    }
  }, [current, videoClips])

  // ------------------------------------------------------------ 音声トラック
  //
  // Web Audio は「押した瞬間の再生ヘッド」を基準にまとめて予約するので、
  // どこから鳴らし始めるかを再生の開始時に 1 回だけ確定させる。
  const [playFrom, setPlayFrom] = useState(0)
  const audio = useAudioTracks(audioClips, playing, playFrom)

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
      const at = clipAt(videoClips, cursor)
      const element = at ? videos.current.get(at.clip.id) : null
      if (element && element.paused) void element.play().catch(() => undefined)
      // 繋ぎの区間では次のクリップも一緒に流す（重ねて見せるため）。
      const overlap = transitionAt(videoClips, cursor)
      const other = overlap ? videos.current.get(overlap.to.id) : null
      if (other && other.paused) void other.play().catch(() => undefined)
      frame = window.requestAnimationFrame(tick)
    }
    frame = window.requestAnimationFrame(tick)
    return () => window.cancelAnimationFrame(frame)
    // playheadMs はスタート地点としてだけ読む（依存に入れると毎フレーム張り直す）。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [playing, videoClips, total, onSeek])

  const toggle = () => {
    if (total <= 0) return
    if (!playing) {
      const from = playheadMs >= total ? 0 : playheadMs
      if (from !== playheadMs) onSeek(from)
      setPlayFrom(from)
    }
    setPlaying((value) => !value)
  }

  return (
    <div className="flex min-h-0 flex-col gap-2">
      <div
        className="relative aspect-video w-full overflow-hidden rounded-lg border border-border bg-black"
        // テロップの文字サイズを「画面の高さに対する比」で決めるための入れ物
        // （焼き込みの ASS と同じ決め方にすると、見え方の比率が揃う）。
        style={{ containerType: 'size' }}
      >
        {videoClips.map((clip) => {
          const visible = clip.id === currentId || clip.id === incomingId
          if (!clip.video_url) {
            return clip.id === currentId ? (
              <div
                key={clip.id}
                className="absolute inset-0 flex flex-col items-center justify-center gap-2 text-center text-xs text-red-300"
              >
                <AlertTriangle className="size-6" aria-hidden="true" />
                <span>メディア欠落: このクリップのファイルが見つかりません</span>
              </div>
            ) : null
          }
          if (clip.source_kind === 'image') {
            return (
              <img
                key={clip.id}
                src={clip.video_url}
                alt=""
                className={`absolute inset-0 size-full object-contain ${
                  visible ? '' : 'invisible'
                }`}
                style={layerStyle(clip.id, currentId, incomingId, blend)}
              />
            )
          }
          return (
            <video
              key={clip.id}
              ref={(element) => register(clip.id, element)}
              src={clip.video_url}
              preload="auto"
              playsInline
              muted={clip.id === incomingId && clip.id !== currentId}
              className={`absolute inset-0 size-full object-contain ${
                visible ? '' : 'invisible'
              }`}
              style={layerStyle(clip.id, currentId, incomingId, blend)}
              onLoadedMetadata={(event) => {
                // メタデータが来る前に入れたシークは効かないので、来た時点で
                // もう一度その位置へ合わせる（先読みの待機ぶんも含む）。
                if (clip.id === currentId && current) {
                  event.currentTarget.currentTime =
                    (current.clip.in_ms + current.offsetMs * speedOf(current.clip)) /
                    1000
                } else {
                  event.currentTarget.currentTime = clip.in_ms / 1000
                }
              }}
            />
          )
        })}

        {/* 黒・白フェードは 1 枚の板を被せて近似する */}
        {blend && fadeColor(blend.to.transition_kind) && (
          <div
            className="pointer-events-none absolute inset-0"
            style={{
              background: fadeColor(blend.to.transition_kind) ?? undefined,
              // 真ん中で一番濃くなる（前半で沈み、後半で戻る）
              opacity: 1 - Math.abs(blend.progress * 2 - 1),
            }}
          />
        )}

        <SubtitleOverlay clips={subtitleClips} playheadMs={playheadMs} />

        {videoClips.length === 0 && (
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
          disabled={videoClips.length === 0}
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
          disabled={videoClips.length === 0}
        >
          <SkipBack className="size-4" aria-hidden="true" />
          先頭へ
        </Button>
        <span className="ml-auto font-mono text-xs text-muted-foreground">
          {formatTimecode(playheadMs)} / {formatTimecode(total)}
        </span>
      </div>

      <p className="text-[11px] text-muted-foreground">
        プレビューは近似です（ワイプ・スライド系の繋ぎはカット表示、テロップの
        書体と縁取りも書き出しとは違います）。正確な結果は書き出しで確認してください。
        {audio.error && ` BGM を読めませんでした: ${audio.error}`}
      </p>
    </div>
  )
}

/** 繋ぎで重ねている 2 枚の見せ方（クロスフェードだけ不透明度を動かす）。 */
function layerStyle(
  id: string,
  currentId: string | null,
  incomingId: string | null,
  blend: { to: TimelineClip; progress: number } | null,
): React.CSSProperties {
  if (!blend || id !== incomingId || id === currentId) return {}
  if (blend.to.transition_kind !== 'crossfade') {
    // カット表示（近似しない繋ぎ）: 後半で入れ替わる。
    return { opacity: blend.progress >= 0.5 ? 1 : 0, zIndex: 1 }
  }
  return { opacity: blend.progress, zIndex: 1 }
}

/** 黒・白フェードで被せる色（それ以外は null）。 */
function fadeColor(kind: string | null): string | null {
  if (kind === 'fadeblack') return '#000'
  if (kind === 'fadewhite') return '#fff'
  return null
}

/**
 * テロップの重ね書き。
 *
 * 文字の大きさは焼き込みと同じ「画面の高さに対する比」で決めるので、
 * プレビューの大きさが変わっても見え方の比率は変わらない。
 */
function SubtitleOverlay({
  clips,
  playheadMs,
}: {
  clips: TimelineClip[]
  playheadMs: number
}) {
  const showing = clips.filter(
    (clip) =>
      playheadMs >= clip.start_ms && playheadMs < clip.start_ms + clip.duration_ms,
  )
  if (showing.length === 0) return null
  return (
    <>
      {showing.map((clip) => {
        const style = subtitleStyle(clip)
        return (
          <div
            key={clip.id}
            className={`pointer-events-none absolute inset-x-[5%] z-[2] text-center ${
              style.position === 'top' ? 'top-[5%]' : 'bottom-[5%]'
            }`}
            style={{
              fontSize: `${SUBTITLE_SIZE_RATIO[style.size] * 100}cqh`,
              color: style.color === 'yellow' ? '#ffff00' : '#ffffff',
              fontWeight: 700,
              lineHeight: 1.25,
              // 焼き込みの黒縁取りの代わり（`paint-order` が効かない環境でも
              // 影が 4 方向にあれば読める）。
              textShadow:
                '1px 1px 2px #000, -1px 1px 2px #000, 1px -1px 2px #000,' +
                ' -1px -1px 2px #000',
            }}
          >
            {subtitleText(clip)
              .split('\n')
              .map((line, index) => (
                <span key={index} className="block">
                  {line}
                </span>
              ))}
          </div>
        )
      })}
    </>
  )
}

/**
 * 音声トラック（BGM / SE）を Web Audio で鳴らす。
 *
 * 再生を押した時点の再生ヘッドを基準に、そこから先に鳴るクリップをまとめて
 * スケジュールする（`AudioBufferSourceNode.start(when, offset, duration)`）。
 * フェードは `GainNode` の `linearRampToValueAtTime` で近似する。止めるときは
 * 全部切って作り直す——数十本の短い音を追いかけるより、掴み直すほうが確実。
 */
function useAudioTracks(
  clips: TimelineClip[],
  playing: boolean,
  /** 再生を押した時点の再生ヘッド（ミリ秒）。 */
  startMs: number,
) {
  const contextRef = useRef<AudioContext | null>(null)
  const buffers = useRef(new Map<string, AudioBuffer>())
  const playingNodes = useRef<AudioBufferSourceNode[]>([])
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const stop = () => {
      for (const node of playingNodes.current) {
        try {
          node.stop()
        } catch {
          /* もう止まっている */
        }
      }
      playingNodes.current = []
    }
    if (!playing || clips.length === 0) {
      stop()
      return
    }

    let cancelled = false
    void (async () => {
      const AudioCtor =
        window.AudioContext ??
        (window as unknown as { webkitAudioContext?: typeof AudioContext })
          .webkitAudioContext
      if (!AudioCtor) return
      const context = contextRef.current ?? new AudioCtor()
      contextRef.current = context
      if (context.state === 'suspended') await context.resume()

      try {
        for (const clip of clips) {
          if (!clip.video_url || buffers.current.has(clip.video_url)) continue
          const response = await fetch(clip.video_url)
          const decoded = await context.decodeAudioData(await response.arrayBuffer())
          buffers.current.set(clip.video_url, decoded)
        }
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : String(cause))
        return
      }
      if (cancelled) return
      setError(null)

      // 押した位置（startMs）がタイムラインの 0 秒として並ぶよう、
      // すべてのクリップをそこからの相対で予約する。
      const base = context.currentTime + 0.05
      for (const clip of clips) {
        const buffer = clip.video_url ? buffers.current.get(clip.video_url) : null
        if (!buffer) continue
        const end = clip.start_ms + clip.duration_ms
        if (end <= startMs) continue // もう鳴り終わっている
        // 途中から入るクリップは、その分だけソースの中も進めて始める。
        const skipMs = Math.max(0, startMs - clip.start_ms)
        const source = context.createBufferSource()
        source.buffer = buffer
        const gain = context.createGain()
        const level = 10 ** (clip.gain_db / 20)
        const when = base + Math.max(0, clip.start_ms - startMs) / 1000
        const full = (clip.out_ms - clip.in_ms) / 1000
        const duration = full - skipMs / 1000
        if (duration <= 0) continue
        // フェードはクリップの頭・尻からの相対なので、途中入りでは削れる。
        const fadeInEnd = clip.fade_in_ms / 1000 - skipMs / 1000
        const fadeOut = Math.min(clip.fade_out_ms / 1000, duration)

        gain.gain.setValueAtTime(fadeInEnd > 0 ? 0.0001 : level, when)
        if (fadeInEnd > 0) gain.gain.linearRampToValueAtTime(level, when + fadeInEnd)
        if (fadeOut > 0) {
          gain.gain.setValueAtTime(level, when + duration - fadeOut)
          gain.gain.linearRampToValueAtTime(0.0001, when + duration)
        }
        source.connect(gain).connect(context.destination)
        source.start(when, clip.in_ms / 1000 + skipMs / 1000, duration)
        playingNodes.current.push(source)
      }
    })()

    return () => {
      cancelled = true
      stop()
    }
  }, [playing, clips, startMs])

  return { error }
}
