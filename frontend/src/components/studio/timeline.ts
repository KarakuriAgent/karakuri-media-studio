/**
 * 編集タブの純関数まわり（クリップの並べ替え・トリム・分割・時間の見せ方・
 * 操作履歴）。
 *
 * 描画から切り離してあるのは `studio.ts` と同じ理由で、ここだけを単体で
 * テストできるようにするため。
 *
 * トラックごとに並べ方が違う:
 *
 * - **V1（映像）** … **リップル方式**。配列の順番がそのままタイムライン上の
 *   順番で、`start_ms` はそこから導ける値になる。ただし繋ぎ（トランジション）を
 *   持つクリップは前へ `transition_ms` だけ食い込み、全長はその分だけ縮む
 *   （**オーバーラップ方式**）。
 * - **A1…（音声）** … 自由配置。隙間も空けられるが、同じトラックの中で重なる
 *   ことはできない。
 * - **T1（字幕）** … 音声と同じ自由配置。中身は `text_payload`。
 */
import type {
  StudioTimelineDetail,
  TimelineClip,
  TimelineClipInput,
  TimelineExportFit,
  TimelineExportPreset,
  TimelineMediaItem,
  TimelineTrack,
  TimelineTransitionKind,
} from '../../types'

// --------------------------------------------------------------------------
// ズームと時間の見せ方
// --------------------------------------------------------------------------

/** タイムラインのズーム（1 秒あたりの px）。 */
export const ZOOM_MIN = 20
export const ZOOM_MAX = 200
export const ZOOM_DEFAULT = 60

export function clampZoom(value: number): number {
  if (!Number.isFinite(value)) return ZOOM_DEFAULT
  return Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, value))
}

/** ホイール 1 刻みぶんのズーム（掛け算なので、拡大と縮小が対称になる）。 */
export function zoomBy(value: number, delta: number): number {
  return clampZoom(value * (delta < 0 ? 1.15 : 1 / 1.15))
}

/** ミリ秒 -> px（`zoom` は 1 秒あたりの px）。 */
export function msToPx(ms: number, zoom: number): number {
  return (ms / 1000) * zoom
}

/** px -> ミリ秒（負にはしない）。 */
export function pxToMs(px: number, zoom: number): number {
  if (zoom <= 0) return 0
  return Math.max(0, Math.round((px / zoom) * 1000))
}

/** `mm:ss.cs`（ルーラーと再生ヘッドの表示）。 */
export function formatTimecode(ms: number): string {
  const safe = Math.max(0, Math.round(ms))
  const minutes = Math.floor(safe / 60000)
  const seconds = Math.floor((safe % 60000) / 1000)
  const centis = Math.floor((safe % 1000) / 10)
  const pad = (value: number) => String(value).padStart(2, '0')
  return `${pad(minutes)}:${pad(seconds)}.${pad(centis)}`
}

/** 「3.2 秒」（クリップの尺の表示）。 */
export function formatSeconds(ms: number): string {
  return `${(Math.max(0, ms) / 1000).toFixed(1)} 秒`
}

/**
 * ルーラーの目盛りの間隔（ミリ秒）。
 *
 * ズームに応じて 0.5 / 1 / 2 / 5 / 10 / 30 / 60 秒から選ぶ。目盛りが 40px
 * より詰まらない、いちばん細かいものを採る。
 */
const TICK_STEPS_MS = [500, 1000, 2000, 5000, 10000, 30000, 60000]

export function rulerStepMs(zoom: number): number {
  for (const step of TICK_STEPS_MS) {
    if (msToPx(step, zoom) >= 40) return step
  }
  return TICK_STEPS_MS[TICK_STEPS_MS.length - 1]
}

// --------------------------------------------------------------------------
// トラックとクリップの取り出し
// --------------------------------------------------------------------------

/** 並べ替えの正本になる 1 本（一番上の映像トラック）。無ければ null。 */
export function videoTrackOf(
  timeline: StudioTimelineDetail | null,
): TimelineTrack | null {
  if (!timeline) return null
  return timeline.tracks.find((track) => track.kind === 'video') ?? null
}

/** 音声トラック（A1…）を並び順で。 */
export function audioTracksOf(
  timeline: StudioTimelineDetail | null,
): TimelineTrack[] {
  if (!timeline) return []
  return timeline.tracks.filter((track) => track.kind === 'audio')
}

/** 字幕トラック（T1）。無ければ null。 */
export function subtitleTrackOf(
  timeline: StudioTimelineDetail | null,
): TimelineTrack | null {
  if (!timeline) return null
  return timeline.tracks.find((track) => track.kind === 'subtitle') ?? null
}

/** タイムラインの全クリップ（トラックの並び順 -> 開始位置順）。 */
export function allClipsOf(timeline: StudioTimelineDetail | null): TimelineClip[] {
  if (!timeline) return []
  return timeline.tracks.flatMap((track) => orderedClips(track))
}

/** そのトラックに載っているクリップだけ（開始位置順）。 */
export function clipsOfTrack(clips: TimelineClip[], trackId: string): TimelineClip[] {
  return clips
    .filter((clip) => clip.track_id === trackId)
    .sort((a, b) => a.start_ms - b.start_ms)
}

/**
 * そのトラックのクリップだけを `next` に差し替える（他のトラックはそのまま）。
 *
 * 編集操作は 1 トラックずつ純関数で行い、結果をここで全体へ戻す。
 */
export function withTrackClips(
  clips: TimelineClip[],
  trackId: string,
  next: TimelineClip[],
): TimelineClip[] {
  return [...clips.filter((clip) => clip.track_id !== trackId), ...next]
}

/**
 * `trackId` のクリップに `change` を当てて、全体の配列を返す。
 *
 * 画面の操作（並べ替え・トリム・分割…）はどれも「1 トラックの中の話」なので、
 * 呼び出し側はトラックを気にせずに済む。
 */
export function applyToTrack(
  clips: TimelineClip[],
  trackId: string,
  change: (current: TimelineClip[]) => TimelineClip[],
): TimelineClip[] {
  const before = clipsOfTrack(clips, trackId)
  const after = change(before)
  if (after === before) return clips
  return withTrackClips(clips, trackId, after)
}

/** タイムラインの並び（開始位置順）。 */
export function orderedClips(track: TimelineTrack | null): TimelineClip[] {
  if (!track) return []
  return [...track.clips].sort((a, b) => a.start_ms - b.start_ms)
}

/**
 * クリップの並びの全長（繋ぎで重なったぶんは引く）。
 *
 * 配列は「タイムライン上の順番」で渡す（`ripple` を通した後の並び）。
 */
export function totalDuration(clips: TimelineClip[]): number {
  return clips.reduce(
    (total, clip, index) =>
      total + Math.max(0, clip.duration_ms) - (index > 0 ? overlapOf(clip) : 0),
    0,
  )
}

/** トラックをまたいだ全長（一番後ろのクリップの終わり）。 */
export function spanOf(clips: TimelineClip[]): number {
  return clips.reduce(
    (end, clip) => Math.max(end, clip.start_ms + Math.max(0, clip.duration_ms)),
    0,
  )
}

/**
 * `ms` の位置にあるクリップ（と、そのクリップの中での相対位置）。
 *
 * 境界はクリップの**始まり側**に付ける（`start_ms` ちょうどはそのクリップ）。
 * どのクリップにも当たらなければ null。
 */
export function clipAt(
  clips: TimelineClip[],
  ms: number,
): { clip: TimelineClip; index: number; offsetMs: number } | null {
  for (const [index, clip] of clips.entries()) {
    if (ms >= clip.start_ms && ms < clip.start_ms + clip.duration_ms) {
      return { clip, index, offsetMs: ms - clip.start_ms }
    }
  }
  return null
}

// --------------------------------------------------------------------------
// 繋ぎ（トランジション）
// --------------------------------------------------------------------------
//
// セマンティクスは**オーバーラップ方式**: 繋ぎは「その境界で前後が重なる」もので、
// クリップの `transition_kind` / `transition_ms` は**前のクリップとの境界**を指す。
// 重なった分だけタイムラインの全長は縮む。

/** 繋ぎの長さの範囲（ミリ秒。サーバーの検証と同じ値）。 */
export const TRANSITION_MIN_MS = 200
export const TRANSITION_MAX_MS = 2000

/** 既定の繋ぎの長さ。 */
export const TRANSITION_DEFAULT_MS = 500

/** 画面に出す繋ぎの選択肢（`app.timeline_export.TRANSITIONS` のキーと対）。 */
export const TRANSITION_OPTIONS: {
  kind: TimelineTransitionKind
  label: string
  /** プレビューで近似できるか（できないものはカット表示になる）。 */
  previewable: boolean
}[] = [
  { kind: 'crossfade', label: 'クロスフェード', previewable: true },
  { kind: 'fadeblack', label: '黒フェード', previewable: true },
  { kind: 'fadewhite', label: '白フェード', previewable: true },
  { kind: 'wipeleft', label: 'ワイプ（左へ）', previewable: false },
  { kind: 'wiperight', label: 'ワイプ（右へ）', previewable: false },
  { kind: 'slideleft', label: 'スライド（左へ）', previewable: false },
  { kind: 'slideright', label: 'スライド（右へ）', previewable: false },
  { kind: 'circleopen', label: '円で開く', previewable: false },
  { kind: 'pixelize', label: 'モザイク', previewable: false },
]

export const TRANSITION_LABEL: Record<string, string> = Object.fromEntries(
  TRANSITION_OPTIONS.map((option) => [option.kind, option.label]),
)

/** そのクリップが前のクリップと重なる長さ（繋ぎが無ければ 0）。 */
export function overlapOf(clip: TimelineClip): number {
  if (!clip.transition_kind || clip.transition_ms <= 0) return 0
  return clip.transition_ms
}

/**
 * `index` 番目の境界に置ける繋ぎの上限（隣り合う 2 つの短いほうの 1/2）。
 *
 * 先頭（`index <= 0`）には置けないので 0。
 */
export function maxTransitionMs(clips: TimelineClip[], index: number): number {
  if (index <= 0 || index >= clips.length) return 0
  const shortest = Math.min(clips[index - 1].duration_ms, clips[index].duration_ms)
  return Math.min(TRANSITION_MAX_MS, Math.floor(shortest / 2))
}

/** 境界に繋ぎを置く（`kind` が null ならカットに戻す）。 */
export function setTransition(
  clips: TimelineClip[],
  index: number,
  kind: TimelineTransitionKind | null,
  ms: number = TRANSITION_DEFAULT_MS,
): TimelineClip[] {
  if (index <= 0 || index >= clips.length) return clips
  const limit = maxTransitionMs(clips, index)
  const length = kind === null ? 0 : Math.min(Math.max(ms, TRANSITION_MIN_MS), limit)
  // 短すぎて置けない境界（両側が 400ms 未満）は触らない。
  if (kind !== null && length < TRANSITION_MIN_MS) return clips
  const next = [...clips]
  next[index] = {
    ...next[index],
    transition_kind: kind === null ? null : kind,
    transition_ms: length,
  }
  return ripple(next)
}

/**
 * `ms` の位置がどれかの繋ぎの中なら、その前後のクリップと進み具合（0〜1）。
 *
 * プレビューでクロスフェードを近似するのに使う（外なら null）。
 */
export function transitionAt(
  clips: TimelineClip[],
  ms: number,
): { from: TimelineClip; to: TimelineClip; progress: number } | null {
  for (const [index, clip] of clips.entries()) {
    const overlap = index > 0 ? overlapOf(clip) : 0
    if (overlap <= 0) continue
    if (ms >= clip.start_ms && ms < clip.start_ms + overlap) {
      return {
        from: clips[index - 1],
        to: clip,
        progress: Math.min(1, Math.max(0, (ms - clip.start_ms) / overlap)),
      }
    }
  }
  return null
}

// --------------------------------------------------------------------------
// 編集操作（どれも新しい配列を返す純関数）
// --------------------------------------------------------------------------

/**
 * 配列の順番のまま、先頭から隙間なく詰め直す（リップル）。
 *
 * 繋ぎ（トランジション）を持つクリップは前へ `transition_ms` だけ食い込む
 * （オーバーラップ方式）。長すぎる繋ぎは隣り合う 2 つの短いほうの 1/2 へ丸め、
 * それでも最小を割るならカットに落とす。先頭のクリップの繋ぎは落とす
 * （重なる相手が居ない）。サーバー側の `app.timeline.relayout` と同じ規則。
 */
export function ripple(clips: TimelineClip[]): TimelineClip[] {
  const placed: TimelineClip[] = []
  let cursor = 0
  for (const [index, clip] of clips.entries()) {
    let overlap = index === 0 ? 0 : overlapOf(clip)
    if (overlap > 0) {
      const shortest = Math.min(placed[index - 1].duration_ms, clip.duration_ms)
      overlap = Math.min(overlap, Math.floor(shortest / 2), TRANSITION_MAX_MS)
      if (overlap < TRANSITION_MIN_MS) overlap = 0
    }
    placed.push({
      ...clip,
      transition_kind: overlap > 0 ? clip.transition_kind : null,
      transition_ms: overlap,
      start_ms: Math.max(0, cursor - overlap),
    })
    cursor = placed[index].start_ms + Math.max(0, clip.duration_ms)
  }
  return placed
}

/**
 * `id` のクリップを `to` 番目へ動かす（前後は詰め直す）。
 *
 * 動かせない（居ない / 位置が同じ）ときは元の配列をそのまま返す。
 */
export function moveClip(
  clips: TimelineClip[],
  id: string,
  to: number,
): TimelineClip[] {
  const from = clips.findIndex((clip) => clip.id === id)
  if (from < 0) return clips
  const target = Math.min(clips.length - 1, Math.max(0, to))
  if (target === from) return clips
  const next = [...clips]
  const [moved] = next.splice(from, 1)
  next.splice(target, 0, moved)
  return ripple(next)
}

/** `id` のクリップを消す（後ろを詰める）。 */
export function removeClip(clips: TimelineClip[], id: string): TimelineClip[] {
  const next = clips.filter((clip) => clip.id !== id)
  return next.length === clips.length ? clips : ripple(next)
}

/** クリップに残せる最小の尺（これ以下にはトリムできない）。 */
export const MIN_CLIP_MS = 100

/**
 * クリップの頭（`edge: 'in'`）または尻（`'out'`）を `deltaMs` だけ動かす。
 *
 * `source_duration_ms` が分かっていればその範囲に収め、分からなければ今の
 * `out_ms` を上限として扱う（ソースを読めていないクリップを伸ばして、書き出しで
 * こけるのを避ける）。動かせないときは元の配列をそのまま返す。
 */
export function trimClip(
  clips: TimelineClip[],
  id: string,
  edge: 'in' | 'out',
  deltaMs: number,
): TimelineClip[] {
  const index = clips.findIndex((clip) => clip.id === id)
  if (index < 0) return clips
  const clip = clips[index]

  // 切り出し位置を持たないもの（テロップ・静止画・隙間）は尺を直に伸ばす。
  if (isSpanless(clip)) {
    const duration = Math.max(
      MIN_CLIP_MS,
      Math.round(clip.duration_ms + (edge === 'in' ? -deltaMs : deltaMs)),
    )
    const start =
      edge === 'in'
        ? Math.max(0, clip.start_ms + (clip.duration_ms - duration))
        : clip.start_ms
    if (duration === clip.duration_ms) return clips
    const next = [...clips]
    next[index] = { ...clip, duration_ms: duration, start_ms: start }
    return next
  }

  const limit = clip.source_duration_ms ?? clip.out_ms
  const speed = speedOf(clip)
  // つまみの移動はタイムライン上の量なので、ソースの中では速度ぶん伸びる。
  const sourceDelta = Math.round(deltaMs * speed)

  let { in_ms: inMs, out_ms: outMs } = clip
  if (edge === 'in') {
    inMs = Math.round(clip.in_ms + sourceDelta)
    inMs = Math.max(0, Math.min(outMs - MIN_CLIP_MS, inMs))
  } else {
    outMs = Math.round(clip.out_ms + sourceDelta)
    outMs = Math.min(limit, Math.max(inMs + MIN_CLIP_MS, outMs))
  }
  if (inMs === clip.in_ms && outMs === clip.out_ms) return clips

  const next = [...clips]
  next[index] = {
    ...clip,
    in_ms: inMs,
    out_ms: outMs,
    duration_ms: Math.round((outMs - inMs) / speed),
  }
  return ripple(next)
}

/** 切り出し位置を持たないクリップ（尺がそのまま長さ）。 */
export function isSpanless(clip: TimelineClip): boolean {
  return (
    clip.source_kind === 'text' ||
    clip.source_kind === 'image' ||
    clip.source_kind === 'gap'
  )
}

/** そのクリップの再生速度（未設定・壊れた値は 1.0）。 */
export function speedOf(clip: TimelineClip): number {
  const speed = Number(clip.speed)
  return Number.isFinite(speed) && speed > 0 ? speed : 1
}

/** 画面で作ったばかりで、まだサーバー側の id を持たないクリップの印。 */
export const LOCAL_ID_PREFIX = 'new:'

let localCounter = 0

/** 画面の中だけで通じる一時 id（保存のときに null へ落として採番させる）。 */
export function localClipId(): string {
  localCounter += 1
  return `${LOCAL_ID_PREFIX}${localCounter}-${Math.random().toString(36).slice(2, 8)}`
}

/**
 * `id` のクリップを、タイムライン上の `ms`（= 再生ヘッドの位置）で 2 つに割る。
 *
 * 割れない（クリップの外・端すぎて片方が短くなりすぎる）ときは元の配列をその
 * まま返す。前半は元の id を引き継ぎ、後半は一時 id を持つ（サーバーが採番する）。
 */
export function splitClipAt(
  clips: TimelineClip[],
  id: string,
  ms: number,
): TimelineClip[] {
  const index = clips.findIndex((clip) => clip.id === id)
  if (index < 0) return clips
  const clip = clips[index]
  const offset = Math.round(ms - clip.start_ms)
  if (offset < MIN_CLIP_MS || clip.duration_ms - offset < MIN_CLIP_MS) return clips

  const next = [...clips]
  if (isSpanless(clip)) {
    // 尺だけを割る（テロップ・静止画）。後ろは繋ぎを持たない。
    next.splice(
      index,
      1,
      { ...clip, duration_ms: offset },
      {
        ...clip,
        id: localClipId(),
        start_ms: clip.start_ms + offset,
        duration_ms: clip.duration_ms - offset,
        transition_kind: null,
        transition_ms: 0,
      },
    )
    return next
  }

  const speed = speedOf(clip)
  const cut = clip.in_ms + Math.round(offset * speed)
  const head: TimelineClip = {
    ...clip,
    out_ms: cut,
    duration_ms: Math.round((cut - clip.in_ms) / speed),
  }
  const tail: TimelineClip = {
    ...clip,
    id: localClipId(),
    in_ms: cut,
    duration_ms: Math.round((clip.out_ms - cut) / speed),
    // 割った境目はカット（前半の頭の繋ぎだけが残る）。
    transition_kind: null,
    transition_ms: 0,
  }
  next.splice(index, 1, head, tail)
  return ripple(next)
}

// --------------------------------------------------------------------------
// クリップの中身をいじる（インスペクタから）
// --------------------------------------------------------------------------

/** リタイムの範囲（サーバーの検証と同じ値）。 */
export const SPEED_MIN = 0.25
export const SPEED_MAX = 4

/** インスペクタに出す速度のプリセット。 */
export const SPEED_PRESETS = [0.5, 1, 1.5, 2]

export function clampSpeed(value: number): number {
  if (!Number.isFinite(value) || value <= 0) return 1
  return Math.min(SPEED_MAX, Math.max(SPEED_MIN, Math.round(value * 100) / 100))
}

/**
 * クリップの速度を変える（尺は切り出しの長さから決め直す）。
 *
 * 切り出しはそのままで、タイムライン上の長さだけが変わる（2 倍速なら半分）。
 * 映像トラック向けなので、変えたあとは後ろを詰め直す。
 */
export function setSpeed(
  clips: TimelineClip[],
  id: string,
  speed: number,
): TimelineClip[] {
  const index = clips.findIndex((clip) => clip.id === id)
  if (index < 0) return clips
  const clip = clips[index]
  if (isSpanless(clip)) return clips
  const wanted = clampSpeed(speed)
  const duration = Math.max(
    MIN_CLIP_MS,
    Math.round((clip.out_ms - clip.in_ms) / wanted),
  )
  if (wanted === speedOf(clip) && duration === clip.duration_ms) return clips
  const next = [...clips]
  next[index] = { ...clip, speed: wanted, duration_ms: duration }
  return ripple(next)
}

/** 音量・フェードなど、並びに影響しない項目を差し替える。 */
export function patchClip(
  clips: TimelineClip[],
  id: string,
  patch: Partial<TimelineClip>,
): TimelineClip[] {
  const index = clips.findIndex((clip) => clip.id === id)
  if (index < 0) return clips
  const next = [...clips]
  next[index] = { ...next[index], ...patch }
  return next
}

// --------------------------------------------------------------------------
// テロップ（字幕クリップの中身）
// --------------------------------------------------------------------------

/** テロップの見た目（`text_payload.style`。サーバーの既定と対）。 */
export interface SubtitleStyle {
  position: 'bottom' | 'top'
  size: 'S' | 'M' | 'L'
  color: 'white' | 'yellow'
}

export const SUBTITLE_DEFAULT_STYLE: SubtitleStyle = {
  position: 'bottom',
  size: 'M',
  color: 'white',
}

export const SUBTITLE_POSITION_LABEL: Record<SubtitleStyle['position'], string> = {
  bottom: '下',
  top: '上',
}

export const SUBTITLE_SIZE_LABEL: Record<SubtitleStyle['size'], string> = {
  S: '小',
  M: '中',
  L: '大',
}

export const SUBTITLE_COLOR_LABEL: Record<SubtitleStyle['color'], string> = {
  white: '白',
  yellow: '黄',
}

/** 画面の高さに対する文字サイズの比（`app.timeline_subtitles.SIZES` と同じ）。 */
export const SUBTITLE_SIZE_RATIO: Record<SubtitleStyle['size'], number> = {
  S: 0.045,
  M: 0.06,
  L: 0.08,
}

/** `text_payload` の本文（無ければ空文字）。 */
export function subtitleText(clip: TimelineClip): string {
  const payload = clip.text_payload as { text?: unknown } | null
  return typeof payload?.text === 'string' ? payload.text : ''
}

/** `text_payload.style` を既知の値だけに直す（欠けていれば既定）。 */
export function subtitleStyle(clip: TimelineClip): SubtitleStyle {
  const payload = clip.text_payload as { style?: Record<string, unknown> } | null
  const style = payload?.style ?? {}
  const position = style.position === 'top' ? 'top' : 'bottom'
  const size =
    style.size === 'S' || style.size === 'L'
      ? (style.size as SubtitleStyle['size'])
      : 'M'
  const color = style.color === 'yellow' ? 'yellow' : 'white'
  return { position, size, color }
}

/** テロップの本文・見た目を差し替える。 */
export function setSubtitle(
  clips: TimelineClip[],
  id: string,
  patch: { text?: string; style?: Partial<SubtitleStyle> },
): TimelineClip[] {
  const clip = clips.find((item) => item.id === id)
  if (!clip) return clips
  return patchClip(clips, id, {
    text_payload: {
      text: patch.text ?? subtitleText(clip),
      style: { ...subtitleStyle(clip), ...(patch.style ?? {}) },
    },
  })
}

// --------------------------------------------------------------------------
// 自由配置のトラック（音声 A1… と字幕 T1）
// --------------------------------------------------------------------------
//
// 映像と違って隙間も空けられる（BGM を途中から鳴らす）。ただし同じトラックの
// 中で重ねることはできない（ffmpeg 側で 1 本ずつ切り出すため）。

/** 自由配置のクリップを `startMs` へ動かす（重なるところへは置けない）。 */
export function moveClipTo(
  clips: TimelineClip[],
  id: string,
  startMs: number,
): TimelineClip[] {
  const index = clips.findIndex((clip) => clip.id === id)
  if (index < 0) return clips
  const clip = clips[index]
  const wanted = Math.max(0, Math.round(startMs))
  if (wanted === clip.start_ms) return clips
  const others = clips.filter((item) => item.id !== id)
  if (overlapsAny(others, wanted, clip.duration_ms)) return clips
  const next = [...clips]
  next[index] = { ...clip, start_ms: wanted }
  return next.sort((a, b) => a.start_ms - b.start_ms)
}

/** その区間に別のクリップが掛かっているか。 */
export function overlapsAny(
  clips: TimelineClip[],
  startMs: number,
  durationMs: number,
): boolean {
  const end = startMs + durationMs
  return clips.some(
    (clip) => startMs < clip.start_ms + clip.duration_ms && end > clip.start_ms,
  )
}

/** そのトラックで `durationMs` を置ける、`fromMs` 以降で一番早い位置。 */
export function firstFreeSlot(
  clips: TimelineClip[],
  durationMs: number,
  fromMs = 0,
): number {
  let cursor = Math.max(0, Math.round(fromMs))
  for (const clip of [...clips].sort((a, b) => a.start_ms - b.start_ms)) {
    const end = clip.start_ms + clip.duration_ms
    if (end <= cursor) continue
    if (clip.start_ms >= cursor + durationMs) break
    cursor = end
  }
  return cursor
}

/** 自由配置のクリップを 1 つ消す（後ろは詰めない）。 */
export function removeClipFree(
  clips: TimelineClip[],
  id: string,
): TimelineClip[] {
  const next = clips.filter((clip) => clip.id !== id)
  return next.length === clips.length ? clips : next
}

/** 素材ビンの 1 件をクリップにするときの既定の尺（ミリ秒）。 */
export const DEFAULT_IMAGE_MS = 3000
export const DEFAULT_MEDIA_MS = 5000

/** テロップを 1 枚足すときの既定の尺。 */
export const DEFAULT_SUBTITLE_MS = 2000

/**
 * 素材ビンの 1 件から、置けるクリップ 1 つを作る。
 *
 * `startMs` は置き場所（映像トラックはあとで `ripple` が決め直す）。長さが
 * 分からない素材は既定の尺に落とす（置いてからトリムできる）。
 */
export function clipFromMedia(
  item: TimelineMediaItem,
  trackId: string,
  startMs: number,
): TimelineClip {
  const isImage = item.source_kind === 'image'
  const duration = isImage
    ? DEFAULT_IMAGE_MS
    : Math.max(MIN_CLIP_MS, item.duration_ms ?? DEFAULT_MEDIA_MS)
  return {
    id: localClipId(),
    track_id: trackId,
    timeline_id: '',
    start_ms: Math.max(0, Math.round(startMs)),
    duration_ms: duration,
    source_kind: item.source_kind,
    source_id: item.source_id,
    in_ms: 0,
    out_ms: isImage ? 0 : duration,
    gain_db: 0,
    fade_in_ms: 0,
    fade_out_ms: 0,
    transition_kind: null,
    transition_ms: 0,
    text_payload: null,
    speed: 1,
    sort_order: 0,
    video_url: item.url,
    source_duration_ms: item.duration_ms,
    missing: false,
    label: item.name,
  }
}

/** テロップを 1 枚作る（字幕トラックへ置く）。 */
export function newSubtitleClip(
  trackId: string,
  startMs: number,
  text = 'テロップ',
): TimelineClip {
  return {
    id: localClipId(),
    track_id: trackId,
    timeline_id: '',
    start_ms: Math.max(0, Math.round(startMs)),
    duration_ms: DEFAULT_SUBTITLE_MS,
    source_kind: 'text',
    source_id: null,
    in_ms: 0,
    out_ms: 0,
    gain_db: 0,
    fade_in_ms: 0,
    fade_out_ms: 0,
    transition_kind: null,
    transition_ms: 0,
    text_payload: { text, style: { ...SUBTITLE_DEFAULT_STYLE } },
    speed: 1,
    sort_order: 0,
    video_url: null,
    source_duration_ms: null,
    missing: false,
    label: text,
  }
}

/**
 * 保存の body（`PUT /clips`）にする。
 *
 * 一時 id は `null` にして、サーバーに採番させる。解決済みの項目
 * （`video_url` など）は送らない。
 */
export function toClipInputs(clips: TimelineClip[]): TimelineClipInput[] {
  return clips.map((clip) => ({
    id: clip.id.startsWith(LOCAL_ID_PREFIX) ? null : clip.id,
    track_id: clip.track_id,
    start_ms: clip.start_ms,
    duration_ms: clip.duration_ms,
    source_kind: clip.source_kind,
    source_id: clip.source_id,
    in_ms: clip.in_ms,
    out_ms: clip.out_ms,
    gain_db: clip.gain_db,
    fade_in_ms: clip.fade_in_ms,
    fade_out_ms: clip.fade_out_ms,
    transition_kind: clip.transition_kind,
    transition_ms: clip.transition_ms,
    text_payload: clip.text_payload,
    speed: clip.speed,
  }))
}

/**
 * 2 つの並びが同じ中身か（自動保存を空振りさせないための比較）。
 *
 * 見るのは**保存に載る項目だけ**（解決済みの `video_url` などは無視）。
 * 音量や繋ぎのように並びを変えない項目もここに入っていないと、インスペクタで
 * いじっても保存が走らない。
 */
export function sameClips(a: TimelineClip[], b: TimelineClip[]): boolean {
  if (a.length !== b.length) return false
  return a.every((clip, index) => {
    const other = b[index]
    return (
      clip.id === other.id &&
      clip.track_id === other.track_id &&
      clip.start_ms === other.start_ms &&
      clip.duration_ms === other.duration_ms &&
      clip.in_ms === other.in_ms &&
      clip.out_ms === other.out_ms &&
      clip.source_kind === other.source_kind &&
      clip.source_id === other.source_id &&
      clip.gain_db === other.gain_db &&
      clip.fade_in_ms === other.fade_in_ms &&
      clip.fade_out_ms === other.fade_out_ms &&
      clip.transition_kind === other.transition_kind &&
      clip.transition_ms === other.transition_ms &&
      speedOf(clip) === speedOf(other) &&
      JSON.stringify(clip.text_payload ?? null) ===
        JSON.stringify(other.text_payload ?? null)
    )
  })
}

// --------------------------------------------------------------------------
// 操作履歴（Undo / Redo）
// --------------------------------------------------------------------------
//
// サーバーには「今の並び」しか無いので、やり直しは画面の中だけで持つ。
// 素直な 2 本のスタック（過去 / 未来）で、新しい操作をすると未来は捨てる。

export interface History<T> {
  past: T[]
  present: T
  future: T[]
}

/** 履歴に残せる手数（増やしすぎても使わないので、ほどほどに切る）。 */
export const HISTORY_LIMIT = 50

export function initHistory<T>(present: T): History<T> {
  return { past: [], present, future: [] }
}

/** 新しい状態を積む（未来は捨てる）。同じものなら何もしない。 */
export function pushHistory<T>(
  history: History<T>,
  next: T,
  isSame: (a: T, b: T) => boolean = Object.is,
): History<T> {
  if (isSame(history.present, next)) return history
  const past = [...history.past, history.present].slice(-HISTORY_LIMIT)
  return { past, present: next, future: [] }
}

export function canUndo<T>(history: History<T>): boolean {
  return history.past.length > 0
}

export function canRedo<T>(history: History<T>): boolean {
  return history.future.length > 0
}

export function undo<T>(history: History<T>): History<T> {
  if (!canUndo(history)) return history
  const past = [...history.past]
  const present = past.pop() as T
  return { past, present, future: [history.present, ...history.future] }
}

export function redo<T>(history: History<T>): History<T> {
  if (!canRedo(history)) return history
  const [present, ...future] = history.future
  return { past: [...history.past, history.present], present, future }
}

/** 読み直しなどで履歴ごと差し替える（やり直しの足場もそこで切る）。 */
export function resetHistory<T>(present: T): History<T> {
  return initHistory(present)
}

// --------------------------------------------------------------------------
// 保存状態の見せ方
// --------------------------------------------------------------------------

/** 自動保存の状態（インジケータに出す）。 */
export type SaveState = 'saved' | 'pending' | 'saving' | 'failed'

export const SAVE_STATE_LABEL: Record<SaveState, string> = {
  saved: '保存済み',
  pending: '未保存の変更',
  saving: '保存中…',
  failed: '保存に失敗',
}

export const SAVE_STATE_CLASS: Record<SaveState, string> = {
  saved: 'border-emerald-800 bg-emerald-950 text-emerald-300',
  pending: 'border-amber-800 bg-amber-950 text-amber-300',
  saving: 'border-sky-800 bg-sky-950 text-sky-300',
  failed: 'border-red-800 bg-red-950 text-red-300',
}

/** 自動保存までの待ち時間（ミリ秒）。連続した操作を 1 回にまとめる。 */
export const AUTOSAVE_DELAY_MS = 1200

/** 書き出しの状態ラベル。 */
export const EXPORT_STATUS_LABEL: Record<string, string> = {
  queued: '待機中',
  running: '書き出し中',
  done: '完了',
  failed: '失敗',
}

/** 書き出しの解像度プリセット（`app.timeline_export.PRESETS` と対）。 */
export const EXPORT_PRESETS: {
  value: TimelineExportPreset
  label: string
}[] = [
  { value: 'timeline', label: 'タイムライン規格' },
  { value: '1080p', label: '1080p (1920x1080)' },
  { value: 'vertical', label: '縦 9:16 (1080x1920)' },
  { value: '720p', label: '720p (1280x720)' },
]

/** 縦横比が変わるときの収め方。 */
export const EXPORT_FITS: { value: TimelineExportFit; label: string }[] = [
  { value: 'pad', label: 'レターボックス（黒帯）' },
  { value: 'crop', label: '中央クロップ' },
]

export const EXPORT_STATUS_CLASS: Record<string, string> = {
  queued: 'border-border bg-secondary text-muted-foreground',
  running: 'border-sky-800 bg-sky-950 text-sky-300',
  done: 'border-emerald-800 bg-emerald-950 text-emerald-300',
  failed: 'border-red-800 bg-red-950 text-red-300',
}
