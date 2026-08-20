/**
 * 編集タブの純関数まわり（クリップの並べ替え・トリム・分割・時間の見せ方・
 * 操作履歴）。
 *
 * 描画から切り離してあるのは `studio.ts` と同じ理由で、ここだけを単体で
 * テストできるようにするため。
 *
 * フェーズ 1 の並べ方は**リップル方式**: V1 の 1 本だけを扱い、クリップは常に
 * 先頭から隙間なく詰める（自由配置と重なりは無い）。だから配列の順番がそのまま
 * タイムライン上の順番で、`start_ms` はそこから導ける値になる。
 */
import type {
  StudioTimelineDetail,
  TimelineClip,
  TimelineClipInput,
  TimelineTrack,
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

/** フェーズ 1 が編集する 1 本（一番上の映像トラック）。無ければ null。 */
export function videoTrackOf(
  timeline: StudioTimelineDetail | null,
): TimelineTrack | null {
  if (!timeline) return null
  return timeline.tracks.find((track) => track.kind === 'video') ?? null
}

/** タイムラインの並び（開始位置順）。 */
export function orderedClips(track: TimelineTrack | null): TimelineClip[] {
  if (!track) return []
  return [...track.clips].sort((a, b) => a.start_ms - b.start_ms)
}

/** クリップ全部を足した尺。 */
export function totalDuration(clips: TimelineClip[]): number {
  return clips.reduce((total, clip) => total + Math.max(0, clip.duration_ms), 0)
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
// 編集操作（どれも新しい配列を返す純関数）
// --------------------------------------------------------------------------

/** 配列の順番のまま、先頭から隙間なく詰め直す（リップル）。 */
export function ripple(clips: TimelineClip[]): TimelineClip[] {
  let cursor = 0
  return clips.map((clip) => {
    const placed = { ...clip, start_ms: cursor }
    cursor += Math.max(0, clip.duration_ms)
    return placed
  })
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
  const limit = clip.source_duration_ms ?? clip.out_ms

  let { in_ms: inMs, out_ms: outMs } = clip
  if (edge === 'in') {
    inMs = Math.round(clip.in_ms + deltaMs)
    inMs = Math.max(0, Math.min(outMs - MIN_CLIP_MS, inMs))
  } else {
    outMs = Math.round(clip.out_ms + deltaMs)
    outMs = Math.min(limit, Math.max(inMs + MIN_CLIP_MS, outMs))
  }
  if (inMs === clip.in_ms && outMs === clip.out_ms) return clips

  const next = [...clips]
  next[index] = { ...clip, in_ms: inMs, out_ms: outMs, duration_ms: outMs - inMs }
  return ripple(next)
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

  const cut = clip.in_ms + offset
  const head: TimelineClip = {
    ...clip,
    out_ms: cut,
    duration_ms: cut - clip.in_ms,
  }
  const tail: TimelineClip = {
    ...clip,
    id: localClipId(),
    in_ms: cut,
    duration_ms: clip.out_ms - cut,
  }
  const next = [...clips]
  next.splice(index, 1, head, tail)
  return ripple(next)
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
  }))
}

/** 2 つの並びが同じ中身か（自動保存を空振りさせないための比較）。 */
export function sameClips(a: TimelineClip[], b: TimelineClip[]): boolean {
  if (a.length !== b.length) return false
  return a.every((clip, index) => {
    const other = b[index]
    return (
      clip.id === other.id &&
      clip.start_ms === other.start_ms &&
      clip.duration_ms === other.duration_ms &&
      clip.in_ms === other.in_ms &&
      clip.out_ms === other.out_ms &&
      clip.source_id === other.source_id
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

export const EXPORT_STATUS_CLASS: Record<string, string> = {
  queued: 'border-border bg-secondary text-muted-foreground',
  running: 'border-sky-800 bg-sky-950 text-sky-300',
  done: 'border-emerald-800 bg-emerald-950 text-emerald-300',
  failed: 'border-red-800 bg-red-950 text-red-300',
}
