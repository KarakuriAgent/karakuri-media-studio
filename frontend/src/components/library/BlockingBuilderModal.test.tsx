import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { api } from '../../api'
import type { BlockingResult, BlockingScene, LibraryItem } from '../../types'
import BlockingBuilderModal from './BlockingBuilderModal'
import { FALLBACK_CAPABILITIES, initialBlockingScene } from './blocking'

vi.mock('../../api', () => ({
  api: {
    getStudioCapabilities: vi.fn(),
    blockingPreview: vi.fn(),
    blockingLocationMap: vi.fn(),
    createLibraryBlocking: vi.fn(),
    rerenderLibraryBlocking: vi.fn(),
  },
}))

const getStudioCapabilities = vi.mocked(api.getStudioCapabilities)
const blockingPreview = vi.mocked(api.blockingPreview)
const blockingLocationMap = vi.mocked(api.blockingLocationMap)
const createLibraryBlocking = vi.mocked(api.createLibraryBlocking)
const rerenderLibraryBlocking = vi.mocked(api.rerenderLibraryBlocking)

/** 平面図の座標計算は要素の実寸を見るので、jsdom では 480x480 に固定する。 */
const RECT = {
  x: 0,
  y: 0,
  top: 0,
  left: 0,
  right: 480,
  bottom: 480,
  width: 480,
  height: 480,
  toJSON: () => ({}),
} as DOMRect

function item(overrides: Partial<LibraryItem> = {}): LibraryItem {
  return {
    id: 'l9',
    created_at: '2026-07-30T10:00:00+00:00',
    kind: 'video',
    name: '屋上の対峙',
    path: '/repo/library/video/blocking.mp4',
    url: '/library/video/blocking.mp4',
    nsfw: false,
    nsfw_source: '',
    source_job_id: null,
    source: null,
    tags: ['blocking'],
    category: null,
    blocking: initialBlockingScene(),
    blocking_version: 1,
    ...overrides,
  }
}

function result(scene: BlockingScene): BlockingResult {
  return {
    item: item({ blocking: scene, blocking_version: 2 }),
    location_map: 'LOCATION MAP: …',
    reference_note: '<Video 1> (camera path and blocking only): weak_reference - …',
    width: 768,
    height: 448,
    fps: 24,
    frames: 96,
    duration: 4,
  }
}

/** ドラッグ（jsdom に PointerEvent が無いので MouseEvent で代用する）。 */
function drag(element: Element, clientX: number, clientY: number) {
  fireEvent(element, new MouseEvent('pointerdown', { bubbles: true }))
  fireEvent(window, new MouseEvent('pointermove', { clientX, clientY, bubbles: true }))
  fireEvent(window, new MouseEvent('pointerup', { bubbles: true }))
}

afterEach(cleanup)

describe('BlockingBuilderModal', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.spyOn(Element.prototype, 'getBoundingClientRect').mockReturnValue(RECT)
    getStudioCapabilities.mockResolvedValue({
      latent_continuity: true,
      latent_upscale: true,
      error: '',
      blocking: FALLBACK_CAPABILITIES,
    })
    blockingPreview.mockResolvedValue('blob:preview')
    blockingLocationMap.mockResolvedValue({
      location_map: 'LOCATION MAP: person 1 at 50%, 60%.',
      reference_note: '<Video 1> …',
      positions: [],
    })
    createLibraryBlocking.mockImplementation(async (payload) => result(payload.scene))
    rerenderLibraryBlocking.mockImplementation(async (_id, scene) => result(scene))
  })

  function show(overrides: Partial<Parameters<typeof BlockingBuilderModal>[0]> = {}) {
    const onSaved = vi.fn()
    const onClose = vi.fn()
    render(<BlockingBuilderModal onSaved={onSaved} onClose={onClose} {...overrides} />)
    return { onSaved, onClose }
  }

  /** 最後にプレビューへ渡ったシーン。 */
  function lastScene(): BlockingScene {
    return blockingPreview.mock.calls[blockingPreview.mock.calls.length - 1][0]
  }

  it('開いた時点の初期シーンでプレビューと location_map を取りに行く', async () => {
    show()
    await waitFor(() => expect(blockingPreview).toHaveBeenCalled())
    await waitFor(() => expect(blockingLocationMap).toHaveBeenCalled())
    const scene = lastScene()
    expect(scene.aspect_ratio).toBe('16:9')
    expect(scene.duration).toBe(4)
    expect(scene.objects).toHaveLength(1)
    expect(scene.objects[0].shape).toBe('figure')
    expect(scene.camera.keyframes[0].position).toEqual([0, 1.6, 6])
    expect(
      await screen.findByText('LOCATION MAP: person 1 at 50%, 60%.'),
    ).toBeTruthy()
  })

  it('オブジェクトを足すとシーンが増える', async () => {
    show()
    await waitFor(() => expect(blockingPreview).toHaveBeenCalled())
    fireEvent.click(screen.getAllByRole('button', { name: '追加' })[0])
    await waitFor(() => expect(lastScene().objects).toHaveLength(2))
    expect(lastScene().objects[1].id).toBe('person_2')
    expect(screen.getByLabelText('オブジェクトの id')).toHaveProperty(
      'value',
      'person_2',
    )
  })

  it('平面図をドラッグするとキーフレームの位置が変わる', async () => {
    show()
    await waitFor(() => expect(blockingPreview).toHaveBeenCalled())
    // 1m = 28px、中心が原点なので clientX 300 は x = (300 - 240) / 28 ≒ 2.14m。
    drag(screen.getByRole('button', { name: 'person 1 を動かす' }), 300, 240)
    await waitFor(() =>
      expect(lastScene().objects[0].keyframes[0].position).toEqual([2.14, 0, 0]),
    )
    expect(screen.getByLabelText('キーフレーム 1 の X（m）')).toHaveProperty(
      'value',
      '2.14',
    )
  })

  it('尺を打ち直している途中でキーフレームを落とさない（8 を消して 10 と打つ）', async () => {
    show()
    await waitFor(() => expect(blockingPreview).toHaveBeenCalled())
    const duration = screen.getByLabelText('尺（秒。0.5〜10）')
    // まず 8 秒にして、カメラのキーフレームを t=1 / t=2 に足す。
    fireEvent.change(duration, { target: { value: '8' } })
    fireEvent.blur(duration)
    await waitFor(() => expect(lastScene().duration).toBe(8))
    const add = screen.getByRole('button', { name: 'カメラのキーフレームを追加' })
    fireEvent.click(add)
    await waitFor(() => expect(lastScene().camera.keyframes).toHaveLength(2))
    fireEvent.click(add)
    await waitFor(() => expect(lastScene().camera.keyframes).toHaveLength(3))

    // 「8」を消して「10」と打つ（途中の 1 で t=2 が落ちてはいけない）。
    fireEvent.change(duration, { target: { value: '' } })
    fireEvent.change(duration, { target: { value: '1' } })
    expect(lastScene().duration).toBe(8)
    expect(lastScene().camera.keyframes).toHaveLength(3)
    fireEvent.change(duration, { target: { value: '10' } })
    fireEvent.blur(duration)
    await waitFor(() => expect(lastScene().duration).toBe(10))
    expect(lastScene().camera.keyframes.map((keyframe) => keyframe.t)).toEqual([0, 1, 2])
  })

  it('オブジェクトの id を変えるとカメラの追従先も付け替わる', async () => {
    show()
    await waitFor(() => expect(blockingPreview).toHaveBeenCalled())
    fireEvent.change(screen.getByLabelText('カメラの動き'), {
      target: { value: 'follow' },
    })
    await waitFor(() => expect(lastScene().camera.move?.type).toBe('follow'))
    fireEvent.change(screen.getByLabelText('注視の対象'), {
      target: { value: 'person_1' },
    })
    await waitFor(() => expect(lastScene().camera.move?.target).toBe('person_1'))
    fireEvent.change(screen.getByLabelText('オブジェクトの id'), {
      target: { value: 'hero' },
    })
    await waitFor(() => expect(lastScene().objects[0].id).toBe('hero'))
    // 追っていた相手の id が変わったら target も新しい id を指す（残ると 400）。
    expect(lastScene().camera.move?.target).toBe('hero')
  })

  it('保存すると新規登録の API へシーンと名前とタグを送る', async () => {
    const { onSaved } = show()
    await waitFor(() => expect(blockingPreview).toHaveBeenCalled())
    fireEvent.change(
      screen.getByLabelText('名前（空ならオブジェクトの並びから決まります）'),
      { target: { value: '屋上の対峙' } },
    )
    fireEvent.change(screen.getByLabelText('タグ（カンマ区切り）'), {
      target: { value: '屋上, 対峙' },
    })
    fireEvent.click(screen.getByRole('button', { name: '保存' }))
    await waitFor(() => expect(createLibraryBlocking).toHaveBeenCalledTimes(1))
    expect(createLibraryBlocking.mock.calls[0][0]).toMatchObject({
      name: '屋上の対峙',
      tags: ['屋上', '対峙'],
      scene: { aspect_ratio: '16:9', duration: 4 },
    })
    expect(rerenderLibraryBlocking).not.toHaveBeenCalled()
    await waitFor(() => expect(onSaved).toHaveBeenCalledTimes(1))
    // 焼き上がった文章はコピーできる形で残す。
    expect(await screen.findByText('LOCATION MAP: …')).toBeTruthy()
  })

  it('編集モードでは同じ項目を焼き直す（名前とタグは出さない）', async () => {
    const target = item()
    show({ item: target })
    await waitFor(() => expect(blockingPreview).toHaveBeenCalled())
    expect(screen.queryByLabelText('タグ（カンマ区切り）')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: '焼き直して保存' }))
    await waitFor(() => expect(rerenderLibraryBlocking).toHaveBeenCalledTimes(1))
    expect(rerenderLibraryBlocking.mock.calls[0][0]).toBe('l9')
    expect(rerenderLibraryBlocking.mock.calls[0][1]).toMatchObject({
      duration: 4,
      objects: [{ id: 'person_1' }],
    })
    expect(createLibraryBlocking).not.toHaveBeenCalled()
  })

  it('検証エラーはモーダルの中に出す', async () => {
    blockingPreview.mockRejectedValue(new Error('duration は 0.5〜10 秒で指定してください'))
    show()
    expect(
      await screen.findByText('duration は 0.5〜10 秒で指定してください'),
    ).toBeTruthy()
  })
})
