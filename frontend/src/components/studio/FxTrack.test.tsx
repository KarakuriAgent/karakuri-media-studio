import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { api } from '../../api'
import type {
  StudioTimelineDetail,
  TimelineFx,
  TimelineFxEvent,
} from '../../types'
import EditView from './EditView'

// 編集タブが叩く API は全部ここから返す（StudioView.test.tsx と同じ流儀）。
vi.mock('../../api', async () => {
  const actual = await vi.importActual<typeof import('../../api')>('../../api')
  return {
    ...actual,
    api: {
      getSettings: vi.fn(),
      listStudioTimelines: vi.fn(),
      getStudioTimeline: vi.fn(),
      listStudioTimelineExports: vi.fn(),
      getStudioTimelineSyncPreview: vi.fn(),
      listStudioTimelineMedia: vi.fn(),
      listStudioRevisions: vi.fn(),
      getStudioTimelineFx: vi.fn(),
      updateStudioTimelineFxEvent: vi.fn(),
      deleteStudioTimelineFxEvent: vi.fn(),
      putStudioTimelineFxLyric: vi.fn(),
      replaceStudioTimelineClips: vi.fn(),
    },
  }
})

// プレビューに重ねる Player は本物の Remotion を読み込むので、ここでは出さない
// （FX トラックの帯とプロパティパネルだけを見るテスト）。
vi.mock('./FxPreviewOverlay', () => ({ default: () => null }))

afterEach(cleanup)

const mocked = api as unknown as Record<string, ReturnType<typeof vi.fn>>

const TIMELINE: StudioTimelineDetail = {
  id: 'TL1',
  project_id: 'P1',
  episode_id: null,
  name: 'BAN E2E',
  fps: 24,
  width: 1280,
  height: 720,
  gap_fill: 'clone',
  planned_end_seconds: null,
  created_at: '2026-01-01T00:00:00+00:00',
  updated_at: '2026-01-01T00:00:00+00:00',
  duration_ms: 60000,
  tracks: [
    {
      id: 'V1',
      timeline_id: 'TL1',
      kind: 'video',
      name: 'V1',
      sort_order: 0,
      muted: false,
      locked: false,
      clips: [],
    },
  ],
}

function fxEvent(
  id: string,
  event: Record<string, unknown>,
  enabled = true,
): TimelineFxEvent {
  return { id, enabled, event }
}

const FX: TimelineFx = {
  timeline_id: 'TL1',
  theme: { palette: ['#dc1428'] },
  seed: 1,
  ambient: null,
  backgroundColor: '#000000',
  lyric: null,
  lyric_enabled: true,
  events: [
    fxEvent('E1', {
      type: 'lyric',
      t: 45.96,
      until: 47.5,
      text: '撃ち抜け',
      cx: 0.5,
    }),
    fxEvent('E2', { type: 'sprite', t: 10, until: 16.2, src: 'logo.png' }, false),
  ],
}

beforeEach(() => {
  mocked.getSettings.mockResolvedValue({ remotion_enabled: true })
  mocked.listStudioTimelines.mockResolvedValue([
    { ...TIMELINE, tracks: undefined },
  ])
  mocked.getStudioTimeline.mockResolvedValue(TIMELINE)
  mocked.listStudioTimelineExports.mockResolvedValue([])
  mocked.getStudioTimelineSyncPreview.mockResolvedValue({
    added: [],
    retaken: [],
    removed: [],
  })
  mocked.listStudioTimelineMedia.mockResolvedValue({
    items: [],
    total: 0,
    limit: 50,
    offset: 0,
  })
  mocked.listStudioRevisions.mockResolvedValue([{ seq: 12 }])
  mocked.getStudioTimelineFx.mockResolvedValue(FX)
})

function open() {
  return render(<EditView projectId="P1" episodes={[]} />)
}

it('FX トラックに演出の帯が並ぶ（外したものは薄く）', async () => {
  open()
  const lyric = await screen.findByTitle(/lyric: 撃ち抜け/)
  expect(lyric).toBeTruthy()
  // 外してある（enabled: false）イベントも見えているが薄い
  const sprite = screen.getByTitle(/sprite: logo\.png.*外してあります/)
  expect(sprite.className).toContain('opacity-40')
  expect(screen.getByText('2 件')).toBeTruthy()
})

it('Remotion 連携が OFF なら FX トラックそのものが出ない', async () => {
  mocked.getSettings.mockResolvedValue({ remotion_enabled: false })
  open()
  await screen.findByText('BAN E2E')
  await waitFor(() => expect(mocked.getStudioTimeline).toHaveBeenCalled())
  expect(screen.queryByTitle(/lyric: 撃ち抜け/)).toBeNull()
  expect(mocked.getStudioTimelineFx).not.toHaveBeenCalled()
})

it('帯を選ぶとプロパティが出て、直すと PATCH が飛ぶ', async () => {
  mocked.updateStudioTimelineFxEvent.mockResolvedValue(FX)
  open()
  fireEvent.mouseDown(await screen.findByTitle(/lyric: 撃ち抜け/))

  const start = (await screen.findByLabelText('開始（秒）')) as HTMLInputElement
  expect(start.value).toBe('45.96')
  fireEvent.change(start, { target: { value: '46.5' } })
  fireEvent.blur(start)

  await waitFor(() =>
    expect(mocked.updateStudioTimelineFxEvent).toHaveBeenCalledWith('TL1', 'E1', {
      event: { t: 46.5 },
      base_revision: 12,
    }),
  )

  // 主要項目（文言）も同じ入り口から直せる
  const text = screen.getByLabelText('文言') as HTMLInputElement
  fireEvent.change(text, { target: { value: '撃ち抜けろ' } })
  fireEvent.blur(text)
  await waitFor(() =>
    expect(mocked.updateStudioTimelineFxEvent).toHaveBeenLastCalledWith(
      'TL1',
      'E1',
      { event: { text: '撃ち抜けろ' }, base_revision: 12 },
    ),
  )
})

it('空にした項目は null で送られる（その項目が消える）', async () => {
  mocked.updateStudioTimelineFxEvent.mockResolvedValue(FX)
  open()
  fireEvent.mouseDown(await screen.findByTitle(/lyric: 撃ち抜け/))
  const until = (await screen.findByLabelText('終わり（秒）')) as HTMLInputElement
  fireEvent.change(until, { target: { value: '' } })
  fireEvent.blur(until)
  await waitFor(() =>
    expect(mocked.updateStudioTimelineFxEvent).toHaveBeenCalledWith('TL1', 'E1', {
      event: { until: null },
      base_revision: 12,
    }),
  )
})

it('チェックを外すと enabled: false で送られる', async () => {
  mocked.updateStudioTimelineFxEvent.mockResolvedValue(FX)
  open()
  fireEvent.mouseDown(await screen.findByTitle(/lyric: 撃ち抜け/))
  fireEvent.click(await screen.findByLabelText('プレビューと書き出しに出す'))
  await waitFor(() =>
    expect(mocked.updateStudioTimelineFxEvent).toHaveBeenCalledWith('TL1', 'E1', {
      enabled: false,
      base_revision: 12,
    }),
  )
})

it('削除ボタンでその演出だけ消える', async () => {
  mocked.deleteStudioTimelineFxEvent.mockResolvedValue({
    ...FX,
    events: [FX.events[1]],
  })
  open()
  fireEvent.mouseDown(await screen.findByTitle(/lyric: 撃ち抜け/))
  fireEvent.click(await screen.findByRole('button', { name: '削除' }))

  await waitFor(() =>
    expect(mocked.deleteStudioTimelineFxEvent).toHaveBeenCalledWith(
      'TL1',
      'E1',
      12,
    ),
  )
  await waitFor(() =>
    expect(screen.queryByTitle(/lyric: 撃ち抜け/)).toBeNull(),
  )
})

// --------------------------------------------------------------------------
// 歌詞モーション（FX トラックに相乗りする JIZURA の層）
// --------------------------------------------------------------------------

const LYRIC_MOTION = {
  lyrics: '夜明けの色を\nほどけた声が',
  style: 'noir',
  seed: 7,
  extra: false,
  fx: { motion: 0.7 },
  overrides: {},
}

it('lyric があれば FX トラックの先頭に「歌詞モーション」の帯が 1 本出る', async () => {
  mocked.getStudioTimelineFx.mockResolvedValue({ ...FX, lyric: LYRIC_MOTION })
  open()
  expect(await screen.findByText('歌詞モーション')).toBeTruthy()
  expect(screen.getByText('2 件 + 歌詞')).toBeTruthy()
})

it('外してある歌詞モーションは薄く出る', async () => {
  mocked.getStudioTimelineFx.mockResolvedValue({
    ...FX,
    lyric: LYRIC_MOTION,
    lyric_enabled: false,
  })
  open()
  const band = await screen.findByTitle(/歌詞モーション（タイムライン全長）（外してあります）/)
  expect(band.className).toContain('opacity-40')
})

it('帯を押すと LyricInspector が出て、直すと fx/lyric へ PUT する', async () => {
  const fx = { ...FX, lyric: LYRIC_MOTION }
  mocked.getStudioTimelineFx.mockResolvedValue(fx)
  mocked.putStudioTimelineFxLyric.mockResolvedValue(fx)
  open()
  fireEvent.mouseDown(await screen.findByText('歌詞モーション'))

  // 部品の一覧（JIZURA のバンドル）は動的 import なので、ここでは読み込みを
  // 待たずに触れるものだけを試す。選択肢そのものは LyricInspector.test.tsx で。
  fireEvent.click(
    await screen.findByTitle('シードを振り直す（絵の選び方が変わる）'),
  )
  await waitFor(() => expect(mocked.putStudioTimelineFxLyric).toHaveBeenCalled())
  const [timelineId, body] = mocked.putStudioTimelineFxLyric.mock.calls[0]
  expect(timelineId).toBe('TL1')
  expect(body.base_revision).toBe(12)
  expect(body.lyric.seed).not.toBe(7)
  expect(body.lyric.lyrics).toBe(LYRIC_MOTION.lyrics)
})

it('削除ボタンは lyric: null を送る（帯も消える）', async () => {
  mocked.getStudioTimelineFx.mockResolvedValue({ ...FX, lyric: LYRIC_MOTION })
  mocked.putStudioTimelineFxLyric.mockResolvedValue({ ...FX, lyric: null })
  open()
  fireEvent.mouseDown(await screen.findByText('歌詞モーション'))
  fireEvent.click(await screen.findByTitle('歌詞モーションを消す'))
  await waitFor(() =>
    expect(mocked.putStudioTimelineFxLyric).toHaveBeenCalledWith('TL1', {
      lyric: null,
      base_revision: 12,
    }),
  )
  await waitFor(() => expect(screen.queryByText('歌詞モーション')).toBeNull())
})

it('Remotion 連携が OFF なら歌詞モーションの帯も出ない', async () => {
  mocked.getSettings.mockResolvedValue({ remotion_enabled: false })
  mocked.getStudioTimelineFx.mockResolvedValue({ ...FX, lyric: LYRIC_MOTION })
  open()
  await screen.findByText('BAN E2E')
  await waitFor(() => expect(mocked.getStudioTimeline).toHaveBeenCalled())
  expect(screen.queryByText('歌詞モーション')).toBeNull()
})
