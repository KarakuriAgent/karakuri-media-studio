import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { TimelineClip, TimelineTrack } from '../../types'
import ClipInspector from './ClipInspector'

afterEach(cleanup)

const CLIP: TimelineClip = {
  id: 'c1',
  track_id: 'v1',
  timeline_id: 't1',
  start_ms: 0,
  duration_ms: 2000,
  source_kind: 'library',
  source_id: 'lib1',
  in_ms: 0,
  out_ms: 2000,
  gain_db: 0,
  fade_in_ms: 0,
  fade_out_ms: 0,
  transition_kind: null,
  transition_ms: 0,
  text_payload: null,
  speed: 1,
  effects: null,
  sort_order: 0,
  video_url: '/library/video/a.mp4',
  source_duration_ms: 4000,
  missing: false,
  label: 'カット 1',
}

const TRACK: TimelineTrack = {
  id: 'v1',
  timeline_id: 't1',
  kind: 'video',
  name: 'V1',
  sort_order: 0,
  muted: false,
  locked: false,
  clips: [CLIP],
}

function renderInspector(onEffects = vi.fn()) {
  render(
    <ClipInspector
      clip={CLIP}
      track={TRACK}
      index={0}
      canSplit={false}
      onSplit={() => {}}
      onDelete={() => {}}
      onSpeed={() => {}}
      onPatch={() => {}}
      onSubtitle={() => {}}
      onEffects={onEffects}
    />,
  )
  return onEffects
}

describe('ClipInspector の加工', () => {
  it('シャープの強さと半径を加工に入れる', () => {
    const onEffects = renderInspector()
    fireEvent.change(screen.getByLabelText('シャープ（0〜3）'), {
      target: { value: '1.2' },
    })
    expect(onEffects).toHaveBeenLastCalledWith({
      sharpen: { amount: 1.2, radius: 2 },
    })
    fireEvent.change(screen.getByLabelText('シャープの半径（px）'), {
      target: { value: '9' },
    })
    // 範囲（1〜5）の外は端に寄せる
    expect(onEffects).toHaveBeenLastCalledWith({
      sharpen: { amount: 0, radius: 5 },
    })
  })
})
