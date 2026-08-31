/**
 * App が WS のフレームで画面を動かすところ（issue #45 Phase 4）。
 *
 * ここで見るのは受け取り口だけ: 外部 API の `POST /api/v1/ui/navigate` が流す
 * `ui` フレームで、スタジオタブに切り替わって指定の作品・カットが開くこと。
 * フォーム同期そのものは `formSync.test.tsx`、スタジオの中身は
 * `StudioView.test.tsx` が見ている。
 */

import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import App from './App'
import { api } from './api'
import type { StudioProjectDetail } from './types'

vi.mock('./api', async () => {
  const actual = await vi.importActual<typeof import('./api')>('./api')
  return {
    ...actual,
    api: {
      health: vi.fn(),
      options: vi.fn(),
      listJobs: vi.fn(),
      getSettings: vi.fn(),
      getGenerateForm: vi.fn(),
      putGenerateForm: vi.fn(),
      getStudioCapabilities: vi.fn(),
      listStudioProjects: vi.fn(),
      getStudioProject: vi.fn(),
    },
  }
})

vi.mock('./push', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./push')>()),
  ensurePushSubscription: vi.fn(),
}))

const mocked = api as unknown as Record<string, ReturnType<typeof vi.fn>>

/** 開かれた WebSocket の身代わり（テストからフレームを流し込む）。 */
class FakeSocket {
  static last: FakeSocket | null = null
  onopen: (() => void) | null = null
  onclose: (() => void) | null = null
  onerror: (() => void) | null = null
  onmessage: ((event: { data: string }) => void) | null = null

  constructor() {
    FakeSocket.last = this
  }

  close() {}

  /** サーバーから 1 フレーム届いたことにする。 */
  deliver(frame: unknown) {
    this.onmessage?.({ data: JSON.stringify(frame) })
  }
}

function detail(projectId: string): StudioProjectDetail {
  return {
    id: projectId,
    name: '深夜のラーメン屋',
    code: 'KW',
    synopsis: '',
    world_notes: '',
    auto_translate: true,
    latent_continuity: false,
    latent_upscale: true,
    quality: 'normal',
    image_quality: 'normal',
    megapixels: null,
    aspect_ratio: null,
    steps: 0,
    image_megapixels: null,
    image_aspect_ratio: null,
    image_steps: 0,
    nsfw: false,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    episodes: [],
    scenes: [],
    // 先頭の自動選択と見分けたいので 2 カット置く（指されるのは 2 つ目）
    shots: [
      {
        id: 'shot-1',
        project_id: projectId,
        scene_id: null,
        sort_order: 0,
        title: '出会い',
        prompt: 'A cat walks in.',
        dialogue: '',
        english_prompt: '',
        english_source: '',
        translating: false,
        duration_seconds: 5,
        aspect_ratio: '',
        megapixels: null,
        steps: 0,
        workflow: '',
        carry_over: true,
        asset_ids: [],
        created_at: '2026-01-01T00:00:00Z',
        updated_at: '2026-01-01T00:00:00Z',
      },
      {
        id: 'shot-2',
        project_id: projectId,
        scene_id: null,
        sort_order: 1,
        title: '決裂',
        prompt: 'The cat leaves.',
        dialogue: '',
        english_prompt: '',
        english_source: '',
        translating: false,
        duration_seconds: 5,
        aspect_ratio: '',
        megapixels: null,
        steps: 0,
        workflow: '',
        carry_over: true,
        asset_ids: [],
        created_at: '2026-01-01T00:00:00Z',
        updated_at: '2026-01-01T00:00:00Z',
      },
    ],
    assets: [],
    takes: [],
  } as unknown as StudioProjectDetail
}

beforeEach(() => {
  vi.stubGlobal('WebSocket', FakeSocket)
  window.localStorage.clear()
  // 生成タブ側は「読めなかった」で構わない（このテストは WS の受け取り口を見る）
  mocked.health.mockRejectedValue(new Error('offline'))
  mocked.options.mockRejectedValue(new Error('offline'))
  mocked.getSettings.mockRejectedValue(new Error('offline'))
  mocked.listJobs.mockResolvedValue([])
  mocked.getGenerateForm.mockResolvedValue({
    values: {},
    revision: 0,
    updated_by: '',
    updated_at: '',
  })
  mocked.getStudioCapabilities.mockResolvedValue({
    latent_continuity: true,
    error: '',
  })
  mocked.listStudioProjects.mockResolvedValue([])
  mocked.getStudioProject.mockImplementation(async (id: string) => detail(id))
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.clearAllMocks()
})

it('まだ開いていない作品でも、navigate で指定のカットが選ばれる', async () => {
  render(<App />)
  await waitFor(() => expect(FakeSocket.last).not.toBeNull())

  await act(async () => {
    FakeSocket.last?.deliver({
      type: 'ui',
      op: 'navigate',
      view: 'studio',
      project_id: 'project-1',
      shot_id: 'shot-2',
    })
  })

  await waitFor(() =>
    expect(mocked.getStudioProject).toHaveBeenCalledWith('project-1', null),
  )
  // 先頭カットの自動選択に奪われず、指定のカットが選ばれている
  await waitFor(() =>
    expect(
      screen.getByRole('button', { name: '決裂' }).getAttribute('aria-current'),
    ).toBe('true'),
  )
  expect(
    screen.getByRole('button', { name: '出会い' }).getAttribute('aria-current'),
  ).toBeNull()
})

it('navigate で設定タブへ移ることもできる', async () => {
  render(<App />)
  await waitFor(() => expect(FakeSocket.last).not.toBeNull())

  await act(async () => {
    FakeSocket.last?.deliver({
      type: 'ui',
      op: 'navigate',
      view: 'settings',
      project_id: null,
      shot_id: null,
    })
  })

  // 生成タブのフォーム（「生成する」ボタン）は消えている
  await waitFor(() =>
    expect(screen.queryByRole('button', { name: /生成する/ })).toBeNull(),
  )
})
