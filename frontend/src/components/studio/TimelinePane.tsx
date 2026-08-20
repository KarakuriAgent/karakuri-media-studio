import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  AlertTriangle,
  GripVertical,
  Image as ImageIcon,
  Music,
  Plus,
  Trash2,
  Type,
  Volume2,
  VolumeX,
} from 'lucide-react'

import type { TimelineClip, TimelineTrack, TimelineTransitionKind } from '../../types'
import { Button } from '../ui/button'
import { Popover, PopoverContent, PopoverTrigger } from '../ui/popover'
import {
  TRANSITION_DEFAULT_MS,
  TRANSITION_LABEL,
  TRANSITION_MIN_MS,
  TRANSITION_OPTIONS,
  clipAt,
  clipsOfTrack,
  formatSeconds,
  formatTimecode,
  maxTransitionMs,
  msToPx,
  overlapOf,
  pxToMs,
  rulerStepMs,
  spanOf,
  speedOf,
  subtitleText,
  zoomBy,
} from './timeline'

/** クリップの端をつかめる幅（px）。細すぎると掴めず、太すぎると本体が押せない。 */
const HANDLE_PX = 8

/** タイムラインの右端に足す余白（ミリ秒）。最後のクリップの先も少し見せる。 */
const TAIL_PADDING_MS = 3000

/** ドラッグとみなす最小の移動量（px）。これ未満はクリックとして扱う。 */
const DRAG_THRESHOLD_PX = 4

/** トラック 1 段の高さ（px）。映像だけ厚く見せる。 */
const LANE_HEIGHT = { video: 80, audio: 56, subtitle: 48 } as const

/** 左のトラック見出しの幅（px）。 */
const HEADER_PX = 84

type DragKind = 'move' | 'trim-in' | 'trim-out'

interface DragState {
  kind: DragKind
  clipId: string
  trackId: string
  /** そのトラックが映像（リップル）か自由配置か。 */
  ripple: boolean
  startX: number
  /** つかんだ時点のクリップの並び順 / 開始位置。 */
  index: number
  startMs: number
  moved: boolean
}

/**
 * タイムライン（時間軸ルーラー + 再生ヘッド + トラックごとのクリップの帯）。
 *
 * トラックによって並べ方が違う:
 *
 * - **V1（映像）** … 本体を掴んで左右で**並べ替え**（順序だけが変わり、前後は
 *   詰め直される）。クリップの境界に**繋ぎ（トランジション）**を置ける
 *   ——オーバーラップ方式なので、置くとその分だけ全長が縮む。
 * - **A1…（音声）/ T1（字幕）** … 本体を掴んで**自由配置**（重なるところへは
 *   置けない）。
 *
 * どのトラックでも端を掴めば**トリム**、ルーラーや空きを押せば**スクラブ**。
 * ズームは Ctrl + ホイール（1 秒あたり 20〜200px）。実際の書き換えは親が持つ
 * 純関数（`timeline.ts`）で行い、ここは「どの操作が起きたか」を上げるだけ。
 */
export default function TimelinePane({
  tracks,
  clips,
  videoTrackId,
  selectedId,
  playheadMs,
  zoom,
  onZoom,
  onSelect,
  onSeek,
  onMove,
  onMoveTo,
  onTrim,
  onSetTransition,
  onAddAudioTrack,
  onToggleMute,
  onDeleteTrack,
  onAddSubtitle,
}: {
  tracks: TimelineTrack[]
  clips: TimelineClip[]
  videoTrackId: string | null
  selectedId: string | null
  playheadMs: number
  zoom: number
  onZoom: (zoom: number) => void
  onSelect: (id: string | null) => void
  onSeek: (ms: number) => void
  /** V1 の `id` のクリップを `to` 番目へ動かす。 */
  onMove: (id: string, to: number) => void
  /** 自由配置のクリップを `startMs` へ動かす。 */
  onMoveTo: (id: string, startMs: number) => void
  /** `id` のクリップの端を `deltaMs` だけ動かす。 */
  onTrim: (id: string, edge: 'in' | 'out', deltaMs: number) => void
  /** V1 の `index` 番目の境界に繋ぎを置く（`kind` が null でカットに戻す）。 */
  onSetTransition: (
    index: number,
    kind: TimelineTransitionKind | null,
    ms: number,
  ) => void
  onAddAudioTrack: () => void
  onToggleMute: (trackId: string, muted: boolean) => void
  onDeleteTrack: (trackId: string) => void
  /** 再生ヘッドの位置にテロップを 1 枚足す。 */
  onAddSubtitle: () => void
}) {
  const laneRef = useRef<HTMLDivElement | null>(null)
  const [drag, setDrag] = useState<DragState | null>(null)
  const [scrubbing, setScrubbing] = useState(false)

  const videoClips = useMemo(
    () => (videoTrackId ? clipsOfTrack(clips, videoTrackId) : []),
    [clips, videoTrackId],
  )
  const total = useMemo(() => spanOf(clips), [clips])
  const spanMs = total + TAIL_PADDING_MS
  const width = msToPx(spanMs, zoom)

  const step = rulerStepMs(zoom)
  const ticks = useMemo(() => {
    const marks: number[] = []
    for (let ms = 0; ms <= spanMs; ms += step) marks.push(ms)
    return marks
  }, [spanMs, step])

  /** マウスの位置（ページ座標）を、レーンの中のミリ秒に直す。 */
  const msAtClientX = useCallback(
    (clientX: number) => {
      const lane = laneRef.current
      if (!lane) return 0
      const box = lane.getBoundingClientRect()
      return Math.min(spanMs, pxToMs(clientX - box.left + lane.scrollLeft, zoom))
    },
    [spanMs, zoom],
  )

  // ------------------------------------------------------------ スクラブ
  useEffect(() => {
    if (!scrubbing) return
    const move = (event: MouseEvent) => onSeek(msAtClientX(event.clientX))
    const up = () => setScrubbing(false)
    window.addEventListener('mousemove', move)
    window.addEventListener('mouseup', up)
    return () => {
      window.removeEventListener('mousemove', move)
      window.removeEventListener('mouseup', up)
    }
  }, [scrubbing, msAtClientX, onSeek])

  // ------------------------------------------------- クリップのドラッグ操作
  //
  // つかんでいるあいだの追従は window に張る（ポインタがクリップの外へ出ても
  // 続けたい）。映像トラックの move は「離した位置がどのクリップの上か」で
  // 入れ替え先を決め、自由配置の move はつかんだ位置からの差分をそのまま
  // 開始位置に足す。trim はどちらも横の移動量をミリ秒に直して端へ渡す。
  useEffect(() => {
    if (!drag) return
    const move = (event: MouseEvent) => {
      const deltaPx = event.clientX - drag.startX
      if (!drag.moved && Math.abs(deltaPx) < DRAG_THRESHOLD_PX) return
      const deltaMs = Math.round((deltaPx / zoom) * 1000)

      if (drag.kind === 'move') {
        if (drag.ripple) {
          // 入れ替えは離した時点で 1 回だけ（途中で並べ替えると掴み直しになる）
          if (!drag.moved) setDrag({ ...drag, moved: true })
          return
        }
        onMoveTo(drag.clipId, Math.max(0, drag.startMs + deltaMs))
        setDrag({ ...drag, moved: true })
        return
      }
      onTrim(drag.clipId, drag.kind === 'trim-in' ? 'in' : 'out', deltaMs)
      // トリムは 1 回ごとに確定させるので、次の基準を今の位置へ進める。
      setDrag({ ...drag, startX: event.clientX, moved: true })
    }
    const up = (event: MouseEvent) => {
      if (drag.kind === 'move' && drag.ripple && drag.moved) {
        const dropped = clipAt(videoClips, msAtClientX(event.clientX))
        const to = dropped ? dropped.index : videoClips.length - 1
        if (to !== drag.index) onMove(drag.clipId, to)
      }
      setDrag(null)
    }
    window.addEventListener('mousemove', move)
    window.addEventListener('mouseup', up)
    return () => {
      window.removeEventListener('mousemove', move)
      window.removeEventListener('mouseup', up)
    }
  }, [drag, videoClips, zoom, msAtClientX, onMove, onMoveTo, onTrim])

  const startDrag = (
    event: React.MouseEvent,
    kind: DragKind,
    clip: TimelineClip,
    index: number,
    ripple: boolean,
  ) => {
    event.preventDefault()
    event.stopPropagation()
    onSelect(clip.id)
    setDrag({
      kind,
      clipId: clip.id,
      trackId: clip.track_id,
      ripple,
      startX: event.clientX,
      index,
      startMs: clip.start_ms,
      moved: false,
    })
  }

  // ------------------------------------------------------------------ ズーム
  const onWheel = (event: React.WheelEvent) => {
    if (!event.ctrlKey && !event.metaKey) return
    event.preventDefault()
    onZoom(zoomBy(zoom, event.deltaY))
  }

  const hasSubtitleTrack = tracks.some((track) => track.kind === 'subtitle')

  return (
    <div className="flex min-h-0 flex-col rounded-lg border border-border bg-card">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-3 py-1.5">
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <span>{videoClips.length} クリップ</span>
          <span>合計 {formatSeconds(total)}</span>
          <Button
            type="button"
            size="sm"
            variant="ghost"
            onClick={onAddAudioTrack}
            title="音声トラックを 1 本足す"
          >
            <Plus className="size-4" aria-hidden="true" />
            音声トラック
          </Button>
          {hasSubtitleTrack && (
            <Button
              type="button"
              size="sm"
              variant="ghost"
              onClick={onAddSubtitle}
              title="再生ヘッドの位置にテロップを 1 枚足す"
            >
              <Type className="size-4" aria-hidden="true" />
              テロップ
            </Button>
          )}
        </div>
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <span className="font-mono text-foreground">{formatTimecode(playheadMs)}</span>
          <span className="hidden sm:inline">Ctrl + ホイールで拡大縮小</span>
        </div>
      </div>

      <div className="flex min-h-0">
        {/* 左のトラック見出し（横スクロールしても残る） */}
        <div
          className="shrink-0 border-r border-border bg-secondary/30"
          style={{ width: HEADER_PX }}
        >
          <div className="h-6 border-b border-border" />
          {tracks.map((track) => (
            <div
              key={track.id}
              className="flex flex-col justify-center gap-1 border-b border-border px-2"
              style={{ height: LANE_HEIGHT[track.kind] }}
            >
              <span className="truncate text-[11px] font-semibold">
                {track.name || track.kind}
              </span>
              {track.kind !== 'video' && (
                <span className="flex items-center gap-0.5">
                  <Button
                    type="button"
                    size="icon-xs"
                    variant="ghost"
                    title={track.muted ? 'ミュートを解除' : 'ミュート'}
                    onClick={() => onToggleMute(track.id, !track.muted)}
                  >
                    {track.muted ? (
                      <VolumeX className="size-3.5 text-amber-400" aria-hidden="true" />
                    ) : (
                      <Volume2 className="size-3.5" aria-hidden="true" />
                    )}
                    <span className="sr-only">
                      {track.muted ? 'ミュートを解除' : 'ミュート'}
                    </span>
                  </Button>
                  <Button
                    type="button"
                    size="icon-xs"
                    variant="ghost"
                    title="このトラックを消す"
                    onClick={() => onDeleteTrack(track.id)}
                  >
                    <Trash2 className="size-3.5" aria-hidden="true" />
                    <span className="sr-only">このトラックを消す</span>
                  </Button>
                </span>
              )}
            </div>
          ))}
        </div>

        <div
          ref={laneRef}
          className="min-h-0 flex-1 overflow-x-auto overflow-y-hidden"
          onWheel={onWheel}
        >
          <div className="relative select-none" style={{ width: Math.max(width, 320) }}>
            {/* 時間軸ルーラー（押した位置へ再生ヘッドが飛び、そのままドラッグで送れる） */}
            <div
              className="relative h-6 cursor-col-resize border-b border-border bg-secondary/40"
              role="slider"
              aria-label="再生位置"
              aria-valuemin={0}
              aria-valuemax={Math.round(spanMs)}
              aria-valuenow={Math.round(playheadMs)}
              tabIndex={0}
              onMouseDown={(event) => {
                onSeek(msAtClientX(event.clientX))
                setScrubbing(true)
              }}
            >
              {ticks.map((ms) => (
                <div
                  key={ms}
                  className="absolute top-0 h-full border-l border-border/70 pl-1 text-[10px] leading-6 text-muted-foreground"
                  style={{ left: msToPx(ms, zoom) }}
                >
                  {formatTimecode(ms)}
                </div>
              ))}
            </div>

            {/* トラックごとのクリップの帯 */}
            {tracks.map((track) => {
              const laneClips = clipsOfTrack(clips, track.id)
              const isVideo = track.kind === 'video'
              return (
                <div
                  key={track.id}
                  className={`relative border-b border-border bg-background/40 ${
                    track.muted ? 'opacity-50' : ''
                  }`}
                  style={{ height: LANE_HEIGHT[track.kind] }}
                  onMouseDown={() => onSelect(null)}
                >
                  {laneClips.map((clip, index) => (
                    <ClipRect
                      key={clip.id}
                      clip={clip}
                      index={index}
                      kind={track.kind}
                      zoom={zoom}
                      height={LANE_HEIGHT[track.kind] - 8}
                      selected={clip.id === selectedId}
                      dragging={drag?.clipId === clip.id && drag.moved}
                      onBody={(event) =>
                        startDrag(event, 'move', clip, index, isVideo)
                      }
                      onIn={(event) =>
                        startDrag(event, 'trim-in', clip, index, isVideo)
                      }
                      onOut={(event) =>
                        startDrag(event, 'trim-out', clip, index, isVideo)
                      }
                    />
                  ))}

                  {/* 繋ぎのつまみ（映像トラックの境界だけ） */}
                  {isVideo &&
                    laneClips.map((clip, index) =>
                      index === 0 ? null : (
                        <TransitionMark
                          key={`edge-${clip.id}`}
                          clip={clip}
                          index={index}
                          zoom={zoom}
                          limit={maxTransitionMs(laneClips, index)}
                          onSet={onSetTransition}
                        />
                      ),
                    )}

                  {laneClips.length === 0 && (
                    <p className="px-3 py-4 text-[11px] text-muted-foreground">
                      {isVideo
                        ? 'クリップがありません。素材ビンから足すか、話を選んでタイムラインを作り直してください。'
                        : track.kind === 'audio'
                          ? '素材ビンの「音声」から BGM や SE を足せます。'
                          : '「テロップを生成」か「テロップ」ボタンで足せます。'}
                    </p>
                  )}
                </div>
              )
            })}

            {/* 再生ヘッド（ルーラーとすべてのトラックを貫く 1 本） */}
            <div
              className="pointer-events-none absolute top-0 z-10 h-full w-px bg-accent-400"
              style={{ left: msToPx(playheadMs, zoom) }}
            >
              <div className="-ml-1 size-2 rounded-full bg-accent-400" />
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

/**
 * クリップ 1 つの矩形。
 *
 * 左右 {@link HANDLE_PX} px はトリムのつまみで、真ん中が本体（映像なら並べ替え、
 * 音声・字幕なら自由配置）のつかみどころ。メディア欠落（`missing`）は赤系で
 * 出し、そのまま書き出せないことをツールチップで伝える。
 */
function ClipRect({
  clip,
  index,
  kind,
  zoom,
  height,
  selected,
  dragging,
  onBody,
  onIn,
  onOut,
}: {
  clip: TimelineClip
  index: number
  kind: TimelineTrack['kind']
  zoom: number
  height: number
  selected: boolean
  dragging: boolean
  onBody: (event: React.MouseEvent) => void
  onIn: (event: React.MouseEvent) => void
  onOut: (event: React.MouseEvent) => void
}) {
  const left = msToPx(clip.start_ms, zoom)
  const width = Math.max(msToPx(clip.duration_ms, zoom), 12)
  const speed = speedOf(clip)
  const tone = clip.missing
    ? 'border-red-700 bg-red-950/70 text-red-200'
    : selected
      ? 'border-accent-400 bg-accent-500/25 text-foreground'
      : kind === 'audio'
        ? 'border-emerald-900 bg-emerald-950/60 text-emerald-100'
        : kind === 'subtitle'
          ? 'border-sky-900 bg-sky-950/60 text-sky-100'
          : 'border-border bg-secondary text-foreground/90'

  const title = clip.missing
    ? 'メディア欠落: 元のファイルが見つかりません（差し替えるか削除するまで書き出せません）'
    : `${clip.label || 'クリップ'} / ${formatSeconds(clip.duration_ms)}` +
      (speed !== 1 ? ` / ${speed}x` : '')

  return (
    <div
      className={`absolute top-1 flex items-stretch overflow-hidden rounded border ${tone} ${
        dragging ? 'opacity-60' : ''
      }`}
      style={{ left, width, height }}
      title={title}
      onMouseDown={onBody}
    >
      <button
        type="button"
        className="shrink-0 cursor-w-resize bg-foreground/10 hover:bg-accent-400/50"
        style={{ width: HANDLE_PX }}
        aria-label={`クリップ ${index + 1} の始まりをトリム`}
        onMouseDown={onIn}
      />
      <div className="min-w-0 flex-1 cursor-grab px-1.5 py-1">
        <div className="flex items-center gap-1 text-[11px] font-medium">
          <ClipIcon clip={clip} kind={kind} />
          <span className="truncate">
            {clip.missing
              ? 'メディア欠落'
              : kind === 'subtitle'
                ? subtitleText(clip) || 'テロップ'
                : clip.label || `クリップ ${index + 1}`}
          </span>
        </div>
        <p className="truncate text-[10px] text-muted-foreground">
          {formatSeconds(clip.duration_ms)}
          {speed !== 1 && ` / ${speed}x`}
          {kind === 'audio' && clip.gain_db !== 0 && ` / ${clip.gain_db}dB`}
        </p>
      </div>
      <button
        type="button"
        className="shrink-0 cursor-e-resize bg-foreground/10 hover:bg-accent-400/50"
        style={{ width: HANDLE_PX }}
        aria-label={`クリップ ${index + 1} の終わりをトリム`}
        onMouseDown={onOut}
      />
    </div>
  )
}

function ClipIcon({
  clip,
  kind,
}: {
  clip: TimelineClip
  kind: TimelineTrack['kind']
}) {
  if (clip.missing) {
    return <AlertTriangle className="size-3 shrink-0" aria-hidden="true" />
  }
  if (kind === 'audio') return <Music className="size-3 shrink-0" aria-hidden="true" />
  if (kind === 'subtitle') return <Type className="size-3 shrink-0" aria-hidden="true" />
  if (clip.source_kind === 'image') {
    return <ImageIcon className="size-3 shrink-0" aria-hidden="true" />
  }
  return (
    <GripVertical
      className="size-3 shrink-0 text-muted-foreground"
      aria-hidden="true"
    />
  )
}

/**
 * クリップの境界に出る繋ぎのつまみ（押すと種別と長さを選べる）。
 *
 * 繋ぎが置いてあるところは印が濃く、重なっている区間には帯が乗る。境界が
 * 短すぎて（両側のどちらかが 400ms 未満）繋ぎを置けないときは、選択肢を
 * 出さずに理由だけ出す。
 */
function TransitionMark({
  clip,
  index,
  zoom,
  limit,
  onSet,
}: {
  clip: TimelineClip
  index: number
  zoom: number
  /** この境界に置ける繋ぎの最大の長さ（ミリ秒）。 */
  limit: number
  onSet: (
    index: number,
    kind: TimelineTransitionKind | null,
    ms: number,
  ) => void
}) {
  const overlap = overlapOf(clip)
  const active = overlap > 0
  const left = msToPx(clip.start_ms, zoom)
  const [length, setLength] = useState(overlap || TRANSITION_DEFAULT_MS)

  return (
    <Popover>
      <PopoverTrigger asChild>
        <button
          type="button"
          className={`absolute top-0 z-[5] h-4 -translate-x-1/2 rounded-b px-1 text-[9px] leading-4 ${
            active
              ? 'bg-accent-500 text-background'
              : 'bg-border/80 text-muted-foreground hover:bg-accent-500/60'
          }`}
          style={{ left }}
          title={
            active
              ? `繋ぎ: ${TRANSITION_LABEL[clip.transition_kind ?? ''] ?? clip.transition_kind} / ${overlap}ms`
              : 'ここに繋ぎ（トランジション）を置く'
          }
          onMouseDown={(event) => event.stopPropagation()}
        >
          {active ? '⇄' : '＋'}
        </button>
      </PopoverTrigger>
      <PopoverContent align="center" className="flex flex-col gap-2 text-xs">
        <p className="font-semibold">クリップの繋ぎ</p>
        {limit < TRANSITION_MIN_MS ? (
          <p className="text-muted-foreground">
            この境界は短すぎて繋ぎを置けません（前後どちらかを
            {` ${TRANSITION_MIN_MS * 2}ms `}
            以上にしてください）。
          </p>
        ) : (
          <>
            <label className="flex flex-col gap-1">
              <span className="text-muted-foreground">種別</span>
              <select
                className="h-8 rounded-md border border-border bg-background px-2"
                value={clip.transition_kind ?? ''}
                onChange={(event) =>
                  onSet(
                    index,
                    (event.target.value || null) as TimelineTransitionKind | null,
                    length,
                  )
                }
              >
                <option value="">カット（繋ぎなし）</option>
                {TRANSITION_OPTIONS.map((option) => (
                  <option key={option.kind} value={option.kind}>
                    {option.label}
                    {option.previewable ? '' : '（書き出しで確認）'}
                  </option>
                ))}
              </select>
            </label>
            <label className="flex flex-col gap-1">
              <span className="text-muted-foreground">
                長さ {(length / 1000).toFixed(1)} 秒（最大{' '}
                {(limit / 1000).toFixed(1)} 秒）
              </span>
              <input
                type="range"
                min={TRANSITION_MIN_MS}
                max={limit}
                step={100}
                value={Math.min(length, limit)}
                onChange={(event) => setLength(Number(event.target.value))}
                onMouseUp={() => {
                  if (clip.transition_kind) {
                    onSet(index, clip.transition_kind as TimelineTransitionKind, length)
                  }
                }}
              />
            </label>
            <p className="text-[10px] text-muted-foreground">
              前後のクリップがこの長さだけ重なり、タイムラインはその分だけ縮みます。
            </p>
          </>
        )}
      </PopoverContent>
    </Popover>
  )
}
