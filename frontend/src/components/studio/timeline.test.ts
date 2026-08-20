import { describe, expect, it } from 'vitest'

import type {
  TimelineClip,
  TimelineMediaItem,
  TimelineTrack,
  TimelineTransitionKind,
} from '../../types'
import {
  DEFAULT_IMAGE_MS,
  DEFAULT_MEDIA_MS,
  HISTORY_LIMIT,
  LOCAL_ID_PREFIX,
  MIN_CLIP_MS,
  SPEED_MAX,
  SPEED_MIN,
  SUBTITLE_DEFAULT_STYLE,
  ZOOM_MAX,
  ZOOM_MIN,
  applyToTrack,
  canRedo,
  canUndo,
  clampSpeed,
  clampZoom,
  clipAt,
  clipFromMedia,
  clipsOfTrack,
  firstFreeSlot,
  formatSeconds,
  formatTimecode,
  initHistory,
  isSpanless,
  maxTransitionMs,
  moveClip,
  moveClipTo,
  msToPx,
  orderedClips,
  pushHistory,
  pxToMs,
  redo,
  removeClip,
  removeClipFree,
  ripple,
  rulerStepMs,
  sameClips,
  setSpeed,
  setSubtitle,
  setTransition,
  spanOf,
  splitClipAt,
  subtitleStyle,
  subtitleText,
  toClipInputs,
  totalDuration,
  transitionAt,
  trimClip,
  undo,
  videoTrackOf,
  zoomBy,
} from './timeline'

/** テスト用のクリップ 1 つ（並びは `ripple` が決めるので start は仮）。 */
function clip(id: string, overrides: Partial<TimelineClip> = {}): TimelineClip {
  return {
    id,
    track_id: 'V1',
    timeline_id: 'TL',
    start_ms: 0,
    duration_ms: 2000,
    source_kind: 'take',
    source_id: `TAKE-${id}`,
    in_ms: 0,
    out_ms: 2000,
    gain_db: 0,
    fade_in_ms: 0,
    fade_out_ms: 0,
    transition_kind: null,
    transition_ms: 0,
    text_payload: null,
    speed: 1,
    sort_order: 0,
    video_url: `/outputs/${id}/video.mp4`,
    source_duration_ms: 10000,
    missing: false,
    label: `カット ${id}`,
    ...overrides,
  }
}

// --------------------------------------------------------------------------
// ズームと時間の見せ方
// --------------------------------------------------------------------------

describe('ズーム', () => {
  it('範囲の外は端に寄せる', () => {
    expect(clampZoom(5)).toBe(ZOOM_MIN)
    expect(clampZoom(9999)).toBe(ZOOM_MAX)
    expect(clampZoom(60)).toBe(60)
    expect(clampZoom(Number.NaN)).toBeGreaterThan(0)
  })

  it('ホイールの向きで拡大・縮小し、範囲を出ない', () => {
    expect(zoomBy(60, -1)).toBeGreaterThan(60)
    expect(zoomBy(60, 1)).toBeLessThan(60)
    expect(zoomBy(ZOOM_MAX, -1)).toBe(ZOOM_MAX)
    expect(zoomBy(ZOOM_MIN, 1)).toBe(ZOOM_MIN)
  })
})

describe('時間と px の行き来', () => {
  it('1 秒 = zoom px', () => {
    expect(msToPx(1000, 60)).toBe(60)
    expect(pxToMs(60, 60)).toBe(1000)
  })

  it('px は負にならず、zoom が 0 でも壊れない', () => {
    expect(pxToMs(-100, 60)).toBe(0)
    expect(pxToMs(100, 0)).toBe(0)
  })

  it('mm:ss.cs で出す', () => {
    expect(formatTimecode(0)).toBe('00:00.00')
    expect(formatTimecode(3210)).toBe('00:03.21')
    expect(formatTimecode(65000)).toBe('01:05.00')
    expect(formatTimecode(-5)).toBe('00:00.00')
  })

  it('尺は 1 桁の秒で出す', () => {
    expect(formatSeconds(3210)).toBe('3.2 秒')
  })

  it('ルーラーの目盛りはズームが上がるほど細かくなる', () => {
    expect(rulerStepMs(ZOOM_MIN)).toBeGreaterThan(rulerStepMs(ZOOM_MAX))
    // どのズームでも目盛りは 40px 以上あく
    for (const zoom of [ZOOM_MIN, 60, 120, ZOOM_MAX]) {
      expect(msToPx(rulerStepMs(zoom), zoom)).toBeGreaterThanOrEqual(40)
    }
  })
})

// --------------------------------------------------------------------------
// トラックとクリップの取り出し
// --------------------------------------------------------------------------

describe('トラックの取り出し', () => {
  const track = (kind: TimelineTrack['kind'], clips: TimelineClip[]) => ({
    id: kind,
    timeline_id: 'TL',
    kind,
    name: kind,
    sort_order: 0,
    muted: false,
    locked: false,
    clips,
  })

  it('編集するのは映像トラック 1 本', () => {
    const detail = {
      id: 'TL',
      project_id: 'P',
      episode_id: null,
      name: '',
      fps: 24,
      width: 1280,
      height: 720,
      created_at: '',
      updated_at: '',
      duration_ms: 0,
      tracks: [track('audio', []), track('video', [clip('a')])],
    }
    expect(videoTrackOf(detail)?.kind).toBe('video')
    expect(videoTrackOf(null)).toBeNull()
  })

  it('クリップは開始位置の順に並べ直す', () => {
    const t = track('video', [
      clip('b', { start_ms: 2000 }),
      clip('a', { start_ms: 0 }),
    ])
    expect(orderedClips(t).map((item) => item.id)).toEqual(['a', 'b'])
    expect(orderedClips(null)).toEqual([])
  })
})

describe('再生ヘッドの下のクリップ', () => {
  const clips = ripple([clip('a'), clip('b'), clip('c')])

  it('境界はクリップの始まり側に付く', () => {
    expect(clipAt(clips, 0)?.clip.id).toBe('a')
    expect(clipAt(clips, 1999)?.clip.id).toBe('a')
    expect(clipAt(clips, 2000)?.clip.id).toBe('b')
  })

  it('クリップの中での相対位置も返す', () => {
    expect(clipAt(clips, 2500)?.offsetMs).toBe(500)
    expect(clipAt(clips, 2500)?.index).toBe(1)
  })

  it('終わりより後ろはどのクリップでもない', () => {
    expect(clipAt(clips, 6000)).toBeNull()
    expect(clipAt([], 0)).toBeNull()
  })
})

// --------------------------------------------------------------------------
// 編集操作（リップル方式: 常に隙間なく詰まる）
// --------------------------------------------------------------------------

describe('リップル', () => {
  it('配列の順に、先頭から隙間なく詰める', () => {
    const laid = ripple([
      clip('a', { duration_ms: 1000 }),
      clip('b', { duration_ms: 500 }),
      clip('c', { duration_ms: 2000 }),
    ])
    expect(laid.map((item) => item.start_ms)).toEqual([0, 1000, 1500])
  })

  it('合計の尺は詰め方に依らない', () => {
    const clips = [clip('a', { duration_ms: 1000 }), clip('b', { duration_ms: 500 })]
    expect(totalDuration(ripple(clips))).toBe(1500)
  })
})

describe('並べ替え', () => {
  const clips = ripple([clip('a'), clip('b'), clip('c')])

  it('動かした先へ入れて、前後を詰め直す', () => {
    const moved = moveClip(clips, 'c', 0)
    expect(moved.map((item) => item.id)).toEqual(['c', 'a', 'b'])
    expect(moved.map((item) => item.start_ms)).toEqual([0, 2000, 4000])
  })

  it('同じ位置・居ないクリップは元のまま（同じ配列を返す）', () => {
    expect(moveClip(clips, 'a', 0)).toBe(clips)
    expect(moveClip(clips, 'nope', 1)).toBe(clips)
  })

  it('範囲の外を指しても端に寄せるだけ', () => {
    expect(moveClip(clips, 'a', 99).map((item) => item.id)).toEqual(['b', 'c', 'a'])
    expect(moveClip(clips, 'c', -5).map((item) => item.id)).toEqual(['c', 'a', 'b'])
  })
})

describe('削除', () => {
  it('消したあとは後ろが詰まる', () => {
    const clips = ripple([clip('a'), clip('b'), clip('c')])
    const left = removeClip(clips, 'b')
    expect(left.map((item) => item.id)).toEqual(['a', 'c'])
    expect(left.map((item) => item.start_ms)).toEqual([0, 2000])
  })

  it('居ないクリップの削除は何もしない', () => {
    const clips = ripple([clip('a')])
    expect(removeClip(clips, 'nope')).toBe(clips)
  })
})

describe('トリム', () => {
  const clips = ripple([clip('a'), clip('b')])

  it('頭を内側へ動かすと尺が縮み、後ろが詰まる', () => {
    const trimmed = trimClip(clips, 'a', 'in', 500)
    expect(trimmed[0].in_ms).toBe(500)
    expect(trimmed[0].duration_ms).toBe(1500)
    expect(trimmed[1].start_ms).toBe(1500)
  })

  it('尻を外側へ動かすと、ソースの長さまで伸びる', () => {
    const trimmed = trimClip(clips, 'a', 'out', 3000)
    expect(trimmed[0].out_ms).toBe(5000)
    expect(trimmed[0].duration_ms).toBe(5000)
  })

  it('ソースの終わりより先へは伸ばせない', () => {
    const trimmed = trimClip(clips, 'a', 'out', 99999)
    expect(trimmed[0].out_ms).toBe(10000) // source_duration_ms
  })

  it('頭は 0 より手前へ戻れない', () => {
    const trimmed = trimClip(clips, 'a', 'in', -5000)
    expect(trimmed[0].in_ms).toBe(0)
    expect(trimmed[0]).toEqual(clips[0]) // 動かないので元のまま
  })

  it('最小の尺より短くはできない', () => {
    const trimmed = trimClip(clips, 'a', 'in', 99999)
    expect(trimmed[0].duration_ms).toBe(MIN_CLIP_MS)
  })

  it('ソースの長さが分からなければ、いまの終わりが上限になる', () => {
    const unknown = ripple([clip('a', { source_duration_ms: null })])
    expect(trimClip(unknown, 'a', 'out', 5000)[0].out_ms).toBe(2000)
  })

  it('居ないクリップ・動かない操作は元のまま', () => {
    expect(trimClip(clips, 'nope', 'in', 100)).toBe(clips)
    expect(trimClip(clips, 'a', 'in', 0)).toBe(clips)
  })
})

describe('分割', () => {
  const clips = ripple([clip('a'), clip('b')])

  it('再生ヘッドの位置で 2 つに割り、切り出しが繋がったままになる', () => {
    const split = splitClipAt(clips, 'a', 800)
    expect(split).toHaveLength(3)
    const [head, tail] = split
    expect([head.in_ms, head.out_ms]).toEqual([0, 800])
    expect([tail.in_ms, tail.out_ms]).toEqual([800, 2000])
    expect(head.duration_ms + tail.duration_ms).toBe(2000)
    // 割っても全体の尺は変わらず、後ろのクリップの位置も動かない
    expect(totalDuration(split)).toBe(totalDuration(clips))
    expect(split[2].start_ms).toBe(2000)
  })

  it('前半は元の id を引き継ぎ、後半は採番待ちの一時 id を持つ', () => {
    const [head, tail] = splitClipAt(clips, 'a', 1000)
    expect(head.id).toBe('a')
    expect(tail.id.startsWith(LOCAL_ID_PREFIX)).toBe(true)
    expect(tail.source_id).toBe(head.source_id)
  })

  it('端すぎる位置では割らない', () => {
    expect(splitClipAt(clips, 'a', 10)).toBe(clips)
    expect(splitClipAt(clips, 'a', 1995)).toBe(clips)
  })

  it('クリップの外・居ないクリップでは割らない', () => {
    expect(splitClipAt(clips, 'a', 3000)).toBe(clips)
    expect(splitClipAt(clips, 'nope', 500)).toBe(clips)
  })
})

// --------------------------------------------------------------------------
// 保存の body
// --------------------------------------------------------------------------

describe('保存の body', () => {
  it('一時 id は null にして採番させ、解決済みの項目は送らない', () => {
    const split = splitClipAt(ripple([clip('a')]), 'a', 1000)
    const [head, tail] = toClipInputs(split)
    expect(head.id).toBe('a')
    expect(tail.id).toBeNull()
    expect(head).not.toHaveProperty('video_url')
    expect(head).not.toHaveProperty('missing')
    expect(head).not.toHaveProperty('label')
  })

  it('サーバーが必要とする項目は揃っている', () => {
    const [input] = toClipInputs(ripple([clip('a')]))
    expect(input).toMatchObject({
      track_id: 'V1',
      start_ms: 0,
      duration_ms: 2000,
      source_kind: 'take',
      in_ms: 0,
      out_ms: 2000,
    })
    // 等速なので、尺と切り出しの長さは必ず一致する（サーバー側の検証）
    expect(input.duration_ms).toBe(input.out_ms - input.in_ms)
  })
})

describe('同じ並びかの比較', () => {
  const clips = ripple([clip('a'), clip('b')])

  it('中身が同じなら同じ', () => {
    expect(sameClips(clips, ripple([clip('a'), clip('b')]))).toBe(true)
  })

  it('長さ・順番・切り出しが変われば違う', () => {
    expect(sameClips(clips, ripple([clip('a')]))).toBe(false)
    expect(sameClips(clips, ripple([clip('b'), clip('a')]))).toBe(false)
    expect(sameClips(clips, trimClip(clips, 'a', 'in', 500))).toBe(false)
  })
})

// --------------------------------------------------------------------------
// 操作履歴
// --------------------------------------------------------------------------

describe('Undo / Redo', () => {
  it('積んで戻して進める', () => {
    let history = initHistory(['a'])
    expect(canUndo(history)).toBe(false)
    expect(canRedo(history)).toBe(false)

    history = pushHistory(history, ['a', 'b'])
    expect(canUndo(history)).toBe(true)

    history = undo(history)
    expect(history.present).toEqual(['a'])
    expect(canRedo(history)).toBe(true)

    history = redo(history)
    expect(history.present).toEqual(['a', 'b'])
  })

  it('同じ状態は積まない', () => {
    const history = initHistory(['a'])
    expect(pushHistory(history, ['a'], (x, y) => x.join() === y.join())).toBe(history)
  })

  it('戻したあとに新しい操作をすると、やり直しは捨てられる', () => {
    let history = pushHistory(initHistory(['a']), ['a', 'b'])
    history = undo(history)
    history = pushHistory(history, ['a', 'c'])
    expect(canRedo(history)).toBe(false)
    expect(history.present).toEqual(['a', 'c'])
  })

  it('端では何もしない', () => {
    const history = initHistory(['a'])
    expect(undo(history)).toBe(history)
    expect(redo(history)).toBe(history)
  })

  it('手数は上限で打ち切る', () => {
    let history = initHistory(0)
    for (let step = 1; step <= HISTORY_LIMIT + 10; step += 1) {
      history = pushHistory(history, step)
    }
    expect(history.past).toHaveLength(HISTORY_LIMIT)
    expect(history.present).toBe(HISTORY_LIMIT + 10)
  })
})

// --------------------------------------------------------------------------
// 繋ぎ（トランジション）: オーバーラップ方式
// --------------------------------------------------------------------------

/** 繋ぎつきのクリップ（`ripple` に通す前の並び）。 */
function faded(
  id: string,
  kind: TimelineTransitionKind | null,
  ms: number,
  overrides: Partial<TimelineClip> = {},
): TimelineClip {
  return clip(id, { transition_kind: kind, transition_ms: ms, ...overrides })
}

describe('繋ぎ', () => {
  it('重なったぶんだけ後ろが前へ食い込み、全長が縮む', () => {
    const placed = ripple([
      clip('a', { duration_ms: 2000 }),
      faded('b', 'crossfade', 500, { duration_ms: 3000 }),
    ])
    expect(placed.map((item) => item.start_ms)).toEqual([0, 1500])
    // 2000 + 3000 - 500
    expect(totalDuration(placed)).toBe(4500)
    // 一番後ろの終わりも同じ
    expect(spanOf(placed)).toBe(4500)
  })

  it('先頭のクリップの繋ぎは落とす（重なる相手が居ない）', () => {
    const placed = ripple([faded('a', 'crossfade', 500), clip('b')])
    expect(placed[0].transition_kind).toBeNull()
    expect(placed[0].start_ms).toBe(0)
  })

  it('隣り合う短いほうの半分より長い繋ぎは丸める', () => {
    const placed = ripple([
      clip('a', { duration_ms: 2000 }),
      faded('b', 'crossfade', 1500, { duration_ms: 1000 }),
    ])
    expect(placed[1].transition_ms).toBe(500)
    expect(placed[1].start_ms).toBe(1500)
  })

  it('最小より短くなる境界は繋ぎをあきらめてカットに戻す', () => {
    const placed = ripple([
      clip('a', { duration_ms: 2000 }),
      faded('b', 'crossfade', 500, { duration_ms: 300 }),
    ])
    expect(placed[1].transition_kind).toBeNull()
    expect(placed[1].transition_ms).toBe(0)
    expect(placed[1].start_ms).toBe(2000)
  })

  it('境界に置ける長さの上限は短いほうの半分', () => {
    const clips = [clip('a', { duration_ms: 2000 }), clip('b', { duration_ms: 1000 })]
    expect(maxTransitionMs(clips, 1)).toBe(500)
    // 先頭と範囲外には置けない
    expect(maxTransitionMs(clips, 0)).toBe(0)
    expect(maxTransitionMs(clips, 5)).toBe(0)
  })

  it('setTransition で置いたり、null でカットへ戻したりできる', () => {
    const clips = [clip('a'), clip('b')]
    const withFade = setTransition(clips, 1, 'crossfade', 600)
    expect(withFade[1].transition_kind).toBe('crossfade')
    expect(withFade[1].transition_ms).toBe(600)
    expect(withFade[1].start_ms).toBe(1400)

    const cut = setTransition(withFade, 1, null)
    expect(cut[1].transition_kind).toBeNull()
    expect(cut[1].start_ms).toBe(2000)
  })

  it('setTransition は先頭の境界を触らない', () => {
    const clips = [clip('a'), clip('b')]
    expect(setTransition(clips, 0, 'crossfade')).toBe(clips)
  })

  it('transitionAt は重なりの中にいるあいだ進み具合を返す', () => {
    const placed = ripple([
      clip('a', { duration_ms: 2000 }),
      faded('b', 'crossfade', 500, { duration_ms: 2000 }),
    ])
    expect(transitionAt(placed, 1000)).toBeNull()
    const at = transitionAt(placed, 1750)
    expect(at?.from.id).toBe('a')
    expect(at?.to.id).toBe('b')
    expect(at?.progress).toBeCloseTo(0.5)
    expect(transitionAt(placed, 2100)).toBeNull()
  })
})

// --------------------------------------------------------------------------
// リタイム（速度変更）
// --------------------------------------------------------------------------

describe('リタイム', () => {
  it('速度を上げると尺が縮み、切り出しはそのまま', () => {
    const clips = [clip('a', { in_ms: 0, out_ms: 4000, duration_ms: 4000 })]
    const fast = setSpeed(clips, 'a', 2)
    expect(fast[0].speed).toBe(2)
    expect(fast[0].duration_ms).toBe(2000)
    expect([fast[0].in_ms, fast[0].out_ms]).toEqual([0, 4000])
  })

  it('範囲の外は端に寄せる', () => {
    expect(clampSpeed(0)).toBe(1)
    expect(clampSpeed(99)).toBe(SPEED_MAX)
    expect(clampSpeed(0.01)).toBe(SPEED_MIN)
    expect(clampSpeed(Number.NaN)).toBe(1)
  })

  it('速度を変えたクリップのトリムはソースの中で速度ぶん動く', () => {
    const clips = [
      clip('a', { in_ms: 0, out_ms: 4000, duration_ms: 2000, speed: 2 }),
    ]
    // タイムライン上で 500ms 削る = ソースでは 1000ms
    const trimmed = trimClip(clips, 'a', 'out', -500)
    expect(trimmed[0].out_ms).toBe(3000)
    expect(trimmed[0].duration_ms).toBe(1500)
  })

  it('分割はタイムライン上の位置をソースの位置へ直してから割る', () => {
    const clips = [
      clip('a', { in_ms: 0, out_ms: 4000, duration_ms: 2000, speed: 2 }),
    ]
    const split = splitClipAt(clips, 'a', 500)
    expect(split).toHaveLength(2)
    expect(split[0].out_ms).toBe(1000)
    expect(split[0].duration_ms).toBe(500)
    expect(split[1].in_ms).toBe(1000)
    expect(split[1].duration_ms).toBe(1500)
  })
})

// --------------------------------------------------------------------------
// トラックの出し入れと自由配置
// --------------------------------------------------------------------------

describe('トラックごとの操作', () => {
  const mixed = [
    clip('v1', { track_id: 'V1' }),
    clip('v2', { track_id: 'V1' }),
    clip('a1', { track_id: 'A1', start_ms: 4000 }),
  ]

  it('clipsOfTrack はそのトラックだけを開始位置順で返す', () => {
    expect(clipsOfTrack(mixed, 'V1').map((item) => item.id)).toEqual(['v1', 'v2'])
    expect(clipsOfTrack(mixed, 'A1').map((item) => item.id)).toEqual(['a1'])
  })

  it('applyToTrack は他のトラックを触らない', () => {
    const next = applyToTrack(mixed, 'V1', (lane) => removeClip(lane, 'v1'))
    expect(clipsOfTrack(next, 'V1').map((item) => item.id)).toEqual(['v2'])
    expect(clipsOfTrack(next, 'A1')).toHaveLength(1)
  })

  it('自由配置は好きな位置へ動かせるが、重なるところへは置けない', () => {
    const lane = [
      clip('a', { track_id: 'A1', start_ms: 0, duration_ms: 2000 }),
      clip('b', { track_id: 'A1', start_ms: 5000, duration_ms: 2000 }),
    ]
    const moved = moveClipTo(lane, 'b', 3000)
    expect(moved.find((item) => item.id === 'b')?.start_ms).toBe(3000)
    // 前のクリップに掛かる位置は断る（元の配列がそのまま返る）
    expect(moveClipTo(lane, 'b', 1000)).toBe(lane)
  })

  it('firstFreeSlot は塞がっているところを飛ばす', () => {
    const lane = [
      clip('a', { track_id: 'A1', start_ms: 0, duration_ms: 2000 }),
      clip('b', { track_id: 'A1', start_ms: 3000, duration_ms: 1000 }),
    ]
    expect(firstFreeSlot(lane, 500, 0)).toBe(2000)
    expect(firstFreeSlot(lane, 2000, 0)).toBe(4000)
    expect(firstFreeSlot(lane, 500, 4000)).toBe(4000)
  })

  it('自由配置の削除は後ろを詰めない', () => {
    const lane = [
      clip('a', { track_id: 'A1', start_ms: 0 }),
      clip('b', { track_id: 'A1', start_ms: 5000 }),
    ]
    const left = removeClipFree(lane, 'a')
    expect(left).toHaveLength(1)
    expect(left[0].start_ms).toBe(5000)
  })
})

// --------------------------------------------------------------------------
// テロップ
// --------------------------------------------------------------------------

describe('テロップ', () => {
  it('本文と見た目を読み書きできる（欠けていれば既定）', () => {
    const empty = clip('t', { source_kind: 'text', text_payload: null })
    expect(subtitleText(empty)).toBe('')
    expect(subtitleStyle(empty)).toEqual(SUBTITLE_DEFAULT_STYLE)

    const changed = setSubtitle([empty], 't', {
      text: 'こんにちは',
      style: { color: 'yellow' },
    })
    expect(subtitleText(changed[0])).toBe('こんにちは')
    expect(subtitleStyle(changed[0])).toEqual({
      position: 'bottom',
      size: 'M',
      color: 'yellow',
    })
  })

  it('知らない見た目の値は既定へ落とす', () => {
    const odd = clip('t', {
      source_kind: 'text',
      text_payload: { text: 'x', style: { position: 'middle', size: 'XL', color: 'red' } },
    })
    expect(subtitleStyle(odd)).toEqual(SUBTITLE_DEFAULT_STYLE)
  })

  it('テロップは切り出しを持たないので、トリムは尺を直に伸ばす', () => {
    const lane = [
      clip('t', {
        source_kind: 'text',
        in_ms: 0,
        out_ms: 0,
        start_ms: 1000,
        duration_ms: 2000,
        source_duration_ms: null,
      }),
    ]
    const longer = trimClip(lane, 't', 'out', 500)
    expect(longer[0].duration_ms).toBe(2500)
    expect(longer[0].start_ms).toBe(1000)
    // 頭を縮めると開始位置がずれる
    const later = trimClip(lane, 't', 'in', 500)
    expect(later[0].duration_ms).toBe(1500)
    expect(later[0].start_ms).toBe(1500)
  })
})

// --------------------------------------------------------------------------
// 素材ビンからのクリップ生成
// --------------------------------------------------------------------------

describe('素材ビン', () => {
  const media: TimelineMediaItem = {
    source_kind: 'library',
    source_id: 'LIB1',
    media_kind: 'audio',
    name: '静かなピアノ',
    origin: 'ライブラリ',
    url: '/library/audio/x.mp3',
    duration_ms: 30000,
    created_at: '2026-01-01T00:00:00+00:00',
  }

  it('長さが分かっていればその尺で置く', () => {
    const made = clipFromMedia(media, 'A1', 2000)
    expect(made.duration_ms).toBe(30000)
    expect([made.in_ms, made.out_ms]).toEqual([0, 30000])
    expect(made.start_ms).toBe(2000)
    expect(made.id.startsWith(LOCAL_ID_PREFIX)).toBe(true)
  })

  it('長さが分からない素材は既定の尺に落とす', () => {
    const made = clipFromMedia({ ...media, duration_ms: null }, 'A1', 0)
    expect(made.duration_ms).toBe(DEFAULT_MEDIA_MS)
  })

  it('静止画は切り出しを持たず、既定の 3 秒で置く', () => {
    const made = clipFromMedia(
      { ...media, source_kind: 'image', media_kind: 'image', source_id: 'library:X' },
      'V1',
      0,
    )
    expect(made.duration_ms).toBe(DEFAULT_IMAGE_MS)
    expect([made.in_ms, made.out_ms]).toEqual([0, 0])
    expect(isSpanless(made)).toBe(true)
  })
})

// --------------------------------------------------------------------------
// 保存の比較（インスペクタの編集も拾う）
// --------------------------------------------------------------------------

describe('保存の比較', () => {
  it('音量・繋ぎ・速度・テロップの違いも「変わった」とみなす', () => {
    const base = [clip('a')]
    expect(sameClips(base, [clip('a')])).toBe(true)
    expect(sameClips(base, [clip('a', { gain_db: -6 })])).toBe(false)
    expect(sameClips(base, [clip('a', { fade_in_ms: 500 })])).toBe(false)
    expect(sameClips(base, [clip('a', { speed: 2 })])).toBe(false)
    expect(
      sameClips(base, [clip('a', { transition_kind: 'crossfade', transition_ms: 500 })]),
    ).toBe(false)
    expect(sameClips(base, [clip('a', { text_payload: { text: 'x' } })])).toBe(false)
  })

  it('保存の body には速度も載る', () => {
    const [body] = toClipInputs([clip('a', { speed: 1.5 })])
    expect(body.speed).toBe(1.5)
  })
})
