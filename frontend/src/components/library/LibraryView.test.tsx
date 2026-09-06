import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError, api } from '../../api'
import type {
  Job,
  JobQuery,
  LibraryItem,
  LibraryPage,
  LibraryQuery,
  StudioAssetItem,
  StudioAssetPage,
  StudioProjectSummary,
  TimelineExportItem,
  TimelineExportPage,
} from '../../types'
import LibraryView from './LibraryView'
import { initialBlockingScene } from './blocking'
import { jobEntries, mediaUrl, mergeEntries, visibleEntries } from './library'

vi.mock('../../api', async () => {
  const actual = await vi.importActual<typeof import('../../api')>('../../api')
  return {
    ...actual,
    api: {
      listLibrary: vi.fn(),
      listJobPage: vi.fn(),
      listStudioAssets: vi.fn(),
      listStudioExports: vi.fn(),
      saveExportToLibrary: vi.fn(),
      listStudioProjects: vi.fn(),
      uploadAnyToLibrary: vi.fn(),
      updateLibraryItem: vi.fn(),
      deleteLibraryItem: vi.fn(),
      addJobToLibrary: vi.fn(),
    },
  }
})

const listLibrary = vi.mocked(api.listLibrary)
const listJobPage = vi.mocked(api.listJobPage)
const listStudioAssets = vi.mocked(api.listStudioAssets)
const listStudioExports = vi.mocked(api.listStudioExports)
const saveExportToLibrary = vi.mocked(api.saveExportToLibrary)
const listStudioProjects = vi.mocked(api.listStudioProjects)
const updateLibraryItem = vi.mocked(api.updateLibraryItem)
const deleteLibraryItem = vi.mocked(api.deleteLibraryItem)
const addJobToLibrary = vi.mocked(api.addJobToLibrary)

afterEach(cleanup)

/**
 * 無限スクロールの番兵を見張る `IntersectionObserver` の代役。
 *
 * jsdom には `IntersectionObserver` が無いので、交差の通知を手で起こせる
 * ものに差し替える（`observers` に作られたぶんが溜まる）。
 */
const observers: MockObserver[] = []

class MockObserver {
  readonly targets: Element[] = []

  constructor(
    private readonly callback: (records: { target: Element; isIntersecting: boolean }[]) => void,
    readonly options?: IntersectionObserverInit,
  ) {
    observers.push(this)
  }

  observe(target: Element) {
    this.targets.push(target)
  }

  unobserve() {}

  disconnect() {
    this.targets.length = 0
  }

  takeRecords() {
    return []
  }

  /** 見張っている要素が視界に入った（出た）ことにする。 */
  trigger(isIntersecting: boolean) {
    this.callback(this.targets.map((target) => ({ target, isIntersecting })))
  }
}

function item(overrides: Partial<LibraryItem> = {}): LibraryItem {
  return {
    id: 'l1',
    created_at: '2026-07-30T10:00:00+00:00',
    kind: 'image',
    name: '決めポーズ',
    path: '/repo/library/image/pose.png',
    url: '/library/image/pose.png',
    nsfw: false,
    nsfw_source: '',
    source_job_id: null,
    source: null,
    tags: ['キャラ'],
    category: null,
    ...overrides,
  }
}

function job(overrides: Partial<Job> = {}): Job {
  return {
    id: 'j1',
    created_at: '2026-07-31T10:00:00+00:00',
    mode: 'full',
    status: 'done',
    user_input: null,
    image_prompt: null,
    video_prompt: '踊る猫のクリップ',
    audio_prompt: null,
    grok_raw: null,
    params: { video_workflow: 'minimax_h3_i2v' },
    workflow_json: {},
    comfy_prompt_id: null,
    image_path: null,
    video_path: null,
    last_frame_path: null,
    source_image: null,
    audio_path: null,
    audio_output_path: null,
    error: null,
    nsfw: false,
    nsfw_source: '',
    image_url: '/outputs/j1/still.png',
    video_url: '/outputs/j1/clip.mp4',
    last_frame_url: '/outputs/j1/last.png',
    audio_output_url: null,
    project_id: null,
    project_name: null,
    shot_id: null,
    shot_title: null,
    ...overrides,
  }
}

/** 作品セレクトに並ぶ作品（要るのは id と name だけ）。 */
function project(id: string, name: string): StudioProjectSummary {
  return {
    id,
    name,
    code: '',
    synopsis: '',
    world_notes: '',
    nsfw: false,
    created_at: '2026-07-01T00:00:00+00:00',
    updated_at: '2026-07-01T00:00:00+00:00',
    shot_count: 0,
    asset_count: 0,
    take_count: 0,
    selected_take_count: 0,
  } as StudioProjectSummary
}

/** ジョブ一覧の 1 ページ（`total` は X-Total-Count 相当）。 */
function jobPageOf(items: Job[], total = items.length) {
  return { items, total }
}

function asset(overrides: Partial<StudioAssetItem> = {}): StudioAssetItem {
  return {
    id: 'a1',
    project_id: 'p1',
    project_name: '深夜のラーメン屋',
    project_nsfw: false,
    name: 'タカシ',
    category: 'character',
    caption: '常連客',
    prompt_caption: '',
    kind: 'image',
    path: '/repo/assets/image/takashi.png',
    url: '/assets/image/takashi.png',
    locked: false,
    sort_order: 0,
    created_at: '2026-07-29T10:00:00+00:00',
    library_update_available: false,
    library_blocking: false,
    ...overrides,
  }
}

/** 書き出し 1 本（焼き上がったものだけが一覧に来る）。 */
function exported(overrides: Partial<TimelineExportItem> = {}): TimelineExportItem {
  return {
    id: 'x1',
    timeline_id: 't1',
    timeline_name: '本編 v2',
    project_id: 'p1',
    project_name: '深夜のラーメン屋',
    project_nsfw: false,
    status: 'done',
    progress: 1,
    params: {},
    output_path: '/repo/outputs/exports/x1/final.mp4',
    output_url: '/outputs/exports/x1/final.mp4',
    error: null,
    fps: 24,
    width: 1280,
    height: 720,
    frames: 1440,
    duration_ms: 60000,
    warnings: [],
    fx_job_id: null,
    fx_status: null,
    fx_video_url: null,
    created_at: '2026-07-28T10:00:00+00:00',
    finished_at: '2026-07-28T10:01:00+00:00',
    ...overrides,
  }
}

function exportPageOf(
  items: TimelineExportItem[],
  extra: Partial<TimelineExportPage> = {},
): TimelineExportPage {
  return { items, total: items.length, limit: 48, offset: 0, ...extra }
}

/**
 * 48 件ぴったりの 1 ページ（[さらに読み込む] の offset を 48 にするため）。
 * ジョブは成果物 1 つだけにして、タイル数が膨らまないようにする。
 */
const FULL_ITEMS = Array.from({ length: 48 }, (_, index) =>
  item({ id: `f${index}`, name: `素材 ${index}` }),
)
const FULL_JOBS = Array.from({ length: 48 }, (_, index) =>
  job({ id: `fj${index}`, image_url: null, last_frame_url: null }),
)
const FULL_ASSETS = Array.from({ length: 48 }, (_, index) =>
  asset({ id: `fa${index}`, name: `素材A ${index}` }),
)
const FULL_EXPORTS = Array.from({ length: 48 }, (_, index) =>
  exported({ id: `fx${index}` }),
)

/** 4 系統とも「48 件読めたが総数はもっとある」状態にする。 */
function fillFirstPages(total = 600) {
  listLibrary.mockResolvedValue(pageOf(FULL_ITEMS, { total }))
  listJobPage.mockResolvedValue(jobPageOf(FULL_JOBS, total))
  listStudioAssets.mockResolvedValue(assetPageOf(FULL_ASSETS, { total }))
  listStudioExports.mockResolvedValue(exportPageOf(FULL_EXPORTS, { total }))
}

/**
 * 4 系統とも「次の 1 ページで読み切る」状態にする。
 *
 * 無限スクロールは読み残しがあるかぎり自動で続きを読むので、テストでは
 * 2 ページ目で打ち止めにして走り続けないようにする。
 */
function finishSecondPages(more: LibraryItem) {
  listLibrary.mockResolvedValue(pageOf([more], { total: FULL_ITEMS.length + 1 }))
  listJobPage.mockResolvedValue(
    jobPageOf([job({ id: 'fj-more', image_url: null, last_frame_url: null })], FULL_JOBS.length + 1),
  )
  listStudioAssets.mockResolvedValue(
    assetPageOf([asset({ id: 'fa-more', name: '素材A 追加' })], {
      total: FULL_ASSETS.length + 1,
    }),
  )
  listStudioExports.mockResolvedValue(
    exportPageOf([exported({ id: 'fx-more' })], { total: FULL_EXPORTS.length + 1 }),
  )
}

const IMAGE = item()
const CLIP = item({ id: 'l2', kind: 'video', name: '参考モーション', tags: [] })
const SPICY = item({ id: 'l4', name: 'ひみつ', nsfw: true, tags: [] })

function pageOf(items: LibraryItem[], extra: Partial<LibraryPage> = {}): LibraryPage {
  return {
    items,
    total: items.length,
    limit: 48,
    offset: 0,
    tags: ['キャラ'],
    ...extra,
  }
}

function assetPageOf(
  items: StudioAssetItem[],
  extra: Partial<StudioAssetPage> = {},
): StudioAssetPage {
  return { items, total: items.length, limit: 48, offset: 0, ...extra }
}

describe('visibleEntries', () => {
  it('NSFW 表示がオフのあいだは NSFW を落とす', () => {
    const entries = mergeEntries(jobEntries(job({ nsfw: true })), jobEntries(job({ id: 'j2' })))
    expect(visibleEntries(entries, false).every((entry) => !entry.nsfw)).toBe(true)
    expect(visibleEntries(entries, true).length).toBe(entries.length)
  })
})

describe('jobEntries', () => {
  it('ジョブは成果物 1 ファイル = 1 タイルに展開する', () => {
    expect(jobEntries(job()).map((entry) => entry.output)).toEqual([
      'image',
      'last_frame',
      'video',
    ])
    // 出力の無いジョブはタイルにならない
    expect(
      jobEntries(job({ image_url: null, last_frame_url: null, video_url: null })),
    ).toEqual([])
  })
})

describe('mediaUrl', () => {
  it('構図リファレンスは版を問い合わせに足す（焼き直しで同じパスに上書きされるため）', () => {
    expect(mediaUrl(item())).toBe('/library/image/pose.png')
    expect(mediaUrl(item({ blocking_version: 3 }))).toBe('/library/image/pose.png?v=3')
    expect(mediaUrl(item({ url: '/library/a.mp4?x=1', blocking_version: 2 }))).toBe(
      '/library/a.mp4?x=1&v=2',
    )
  })
})

describe('LibraryView', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    observers.length = 0
    vi.stubGlobal('IntersectionObserver', MockObserver)
    listLibrary.mockResolvedValue(pageOf([IMAGE, CLIP, SPICY], { total: 3 }))
    listJobPage.mockResolvedValue(jobPageOf([job()]))
    listStudioAssets.mockResolvedValue(assetPageOf([asset()]))
    listStudioExports.mockResolvedValue(exportPageOf([exported()]))
    listStudioProjects.mockResolvedValue([
      project('p1', '深夜のラーメン屋'),
      project('p2', 'かおりプロジェクト'),
    ])
  })

  afterEach(() => vi.unstubAllGlobals())

  /**
   * 一覧の末尾までスクロールしたことにする（番兵が視界に入る）。
   *
   * いったん「見えていない」を通してから「見えた」を送るので、すでに末尾に
   * 着いている状態からでも改めて交差の通知を起こせる。
   */
  async function reachEnd() {
    await screen.findByTestId('library-sentinel')
    await act(async () => {
      observers.forEach((observer) => observer.trigger(false))
    })
    await act(async () => {
      observers.forEach((observer) => observer.trigger(true))
    })
  }

  function show(overrides: Partial<Parameters<typeof LibraryView>[0]> = {}) {
    const onChanged = vi.fn()
    const onUseInGenerate = vi.fn()
    const onUseMedia = vi.fn()
    const onOpenJob = vi.fn()
    const onOpenStudioAsset = vi.fn()
    const onOpenStudioTimeline = vi.fn()
    render(
      <LibraryView
        showNsfw={false}
        onChanged={onChanged}
        onUseInGenerate={onUseInGenerate}
        onUseMedia={onUseMedia}
        onOpenJob={onOpenJob}
        onOpenStudioAsset={onOpenStudioAsset}
        onOpenStudioTimeline={onOpenStudioTimeline}
        {...overrides}
      />,
    )
    return {
      onChanged,
      onUseInGenerate,
      onUseMedia,
      onOpenJob,
      onOpenStudioAsset,
      onOpenStudioTimeline,
    }
  }

  /** 直近の listLibrary 呼び出しのクエリ。 */
  function lastQuery(): LibraryQuery {
    const calls = listLibrary.mock.calls
    return calls[calls.length - 1][0] as LibraryQuery
  }

  /** 直近の listJobPage 呼び出しのクエリ。 */
  function lastJobQuery(): JobQuery {
    const calls = listJobPage.mock.calls
    return calls[calls.length - 1][0] as JobQuery
  }

  /** 出どころを切り替える。 */
  function pick(label: string) {
    fireEvent.click(screen.getByRole('tab', { name: label }))
  }

  /** 一覧のタイルを名前で選んで詳細を開く。 */
  async function open(name: string) {
    fireEvent.click(await screen.findByText(name))
  }

  it('既定の「すべて」は 4 つの出どころを混ぜて並べる', async () => {
    show()
    await waitFor(() => expect(listLibrary).toHaveBeenCalled())
    expect(listJobPage).toHaveBeenCalled()
    expect(listStudioAssets).toHaveBeenCalled()
    expect(listStudioExports).toHaveBeenCalled()
    expect(lastQuery()).toMatchObject({ q: '', offset: 0, limit: 48 })
    expect(lastQuery().kind).toBeUndefined()
    // ライブラリ・生成履歴・プロジェクト素材がそれぞれ出る
    expect(await screen.findByText('決めポーズ')).toBeTruthy()
    expect(screen.getAllByText('踊る猫のクリップ').length).toBe(3)
    expect(screen.getByText('タカシ')).toBeTruthy()
    expect(screen.getByText('本編 v2')).toBeTruthy()
    // NSFW は既定で隠れ、その旨を件数に添える
    expect(screen.queryByText('ひみつ')).toBeNull()
    expect(screen.getByText(/NSFW 1 件を非表示/)).toBeTruthy()
  })

  it('出どころを切り替えると呼ぶ API が変わる', async () => {
    show()
    await waitFor(() => expect(listLibrary).toHaveBeenCalled())

    vi.clearAllMocks()
    listLibrary.mockResolvedValue(pageOf([IMAGE]))
    pick('ライブラリ')
    await waitFor(() => expect(listLibrary).toHaveBeenCalled())
    expect(listJobPage).not.toHaveBeenCalled()
    expect(listStudioAssets).not.toHaveBeenCalled()

    vi.clearAllMocks()
    listJobPage.mockResolvedValue(jobPageOf([job()]))
    pick('生成履歴')
    await waitFor(() => expect(listJobPage).toHaveBeenCalled())
    expect(listLibrary).not.toHaveBeenCalled()
    expect(listStudioAssets).not.toHaveBeenCalled()

    vi.clearAllMocks()
    listStudioAssets.mockResolvedValue(assetPageOf([asset()]))
    pick('プロジェクト素材')
    await waitFor(() => expect(listStudioAssets).toHaveBeenCalled())
    expect(listLibrary).not.toHaveBeenCalled()
    expect(listJobPage).not.toHaveBeenCalled()
    expect(listStudioExports).not.toHaveBeenCalled()

    vi.clearAllMocks()
    listStudioExports.mockResolvedValue(exportPageOf([exported()]))
    pick('書き出し')
    await waitFor(() => expect(listStudioExports).toHaveBeenCalled())
    expect(listLibrary).not.toHaveBeenCalled()
    expect(listJobPage).not.toHaveBeenCalled()
    expect(listStudioAssets).not.toHaveBeenCalled()
  })

  it('[生成履歴から登録] は出どころを生成履歴に切り替える', async () => {
    show()
    await waitFor(() => expect(listJobPage).toHaveBeenCalled())
    vi.clearAllMocks()
    listJobPage.mockResolvedValue(jobPageOf([job()]))
    fireEvent.click(screen.getByRole('button', { name: '生成履歴から登録' }))
    await waitFor(() => expect(listJobPage).toHaveBeenCalled())
    expect(listLibrary).not.toHaveBeenCalled()
  })

  it('種別・分類・検索・タグの絞り込みがクエリに乗る', async () => {
    show()
    await waitFor(() => expect(listLibrary).toHaveBeenCalled())

    fireEvent.change(screen.getByLabelText('種別で絞り込み'), {
      target: { value: 'video' },
    })
    await waitFor(() => expect(lastQuery().kind).toBe('video'))
    // 種別は横断の素材一覧にも渡る
    await waitFor(() =>
      expect(listStudioAssets).toHaveBeenCalledWith(
        expect.objectContaining({ kind: 'video' }),
      ),
    )

    fireEvent.change(screen.getByLabelText('カテゴリで絞り込み'), {
      target: { value: 'none' },
    })
    await waitFor(() => expect(lastQuery().category).toBe('none'))

    fireEvent.change(screen.getByLabelText('ファイルを検索'), {
      target: { value: 'ポーズ' },
    })
    await waitFor(() => expect(lastQuery().q).toBe('ポーズ'))

    fireEvent.click(screen.getByRole('button', { name: 'キャラ' }))
    await waitFor(() => expect(lastQuery().tag).toBe('キャラ'))
    expect(lastQuery().offset).toBe(0)
  })

  it('タグは既定で 1 行に畳み、[すべて表示] で折り返して開ける', async () => {
    listLibrary.mockResolvedValue(
      pageOf([IMAGE], { tags: ['キャラ', '背景', '夜景'] }),
    )
    show()
    const chip = await screen.findByRole('button', { name: 'キャラ' })
    const row = chip.parentElement as HTMLElement
    // 件数つきの見出しと、1 行 + 横スクロール
    expect(screen.getByText('タグ 3:')).toBeTruthy()
    expect(row.className).toContain('overflow-x-auto')
    expect(row.className).not.toContain('flex-wrap')

    fireEvent.click(screen.getByRole('button', { name: 'すべて表示' }))
    expect(row.className).toContain('flex-wrap')
    expect(row.className).not.toContain('overflow-x-auto')

    // もう一度押すと 1 行に戻る
    fireEvent.click(screen.getByRole('button', { name: '1 行に戻す' }))
    expect(row.className).toContain('overflow-x-auto')
  })

  it('選んだタグは先頭に出て、× で解除できる', async () => {
    listLibrary.mockResolvedValue(
      pageOf([IMAGE], { tags: ['キャラ', '背景', '夜景'] }),
    )
    show()
    fireEvent.click(await screen.findByRole('button', { name: '夜景' }))
    await waitFor(() => expect(lastQuery().tag).toBe('夜景'))

    // 1 行に畳んでいても見えるよう、選択中のタグは先頭に来る
    const clear = screen.getByRole('button', {
      name: 'タグ「夜景」の絞り込みを解除',
    })
    const row = clear.parentElement as HTMLElement
    expect(row.firstElementChild).toBe(clear)

    fireEvent.click(clear)
    await waitFor(() => expect(lastQuery().tag).toBeUndefined())
  })

  it('分類の絞り込みはライブラリを含まない出どころでは無効になる', async () => {
    show()
    await waitFor(() => expect(listLibrary).toHaveBeenCalled())
    expect(screen.getByLabelText('カテゴリで絞り込み')).toHaveProperty(
      'disabled',
      false,
    )
    pick('生成履歴')
    await waitFor(() =>
      expect(screen.getByLabelText('カテゴリで絞り込み')).toHaveProperty(
        'disabled',
        true,
      ),
    )
  })

  it('ライブラリだけを見ているときは offset を進めて読む', async () => {
    listLibrary.mockResolvedValue(pageOf([IMAGE], { total: 60 }))
    show()
    pick('ライブラリ')
    // 読み込み中はページ送りを無効にしているので、1 ページ目が出るまで待つ
    await screen.findByText('決めポーズ')
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /次へ/ })).toHaveProperty(
        'disabled',
        false,
      ),
    )
    expect(screen.getByText(/1–1 件 \/ 全 60 件/)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: /次へ/ }))
    await waitFor(() => expect(lastQuery().offset).toBe(48))
  })

  it('混ざっているときは末尾までスクロールすると offset を進めて末尾に足す', async () => {
    fillFirstPages()
    show()
    await screen.findByText('素材 0')

    finishSecondPages(item({ id: 'l9', name: '追加のポーズ' }))
    await reachEnd()

    // 4 系統とも offset = 読み込み済み件数 / limit = 48 で続きを読む
    await waitFor(() =>
      expect(lastQuery()).toMatchObject({ offset: 48, limit: 48 }),
    )
    expect(listJobPage).toHaveBeenLastCalledWith(
      expect.objectContaining({ offset: 48, limit: 48 }),
    )
    expect(listStudioAssets).toHaveBeenLastCalledWith(
      expect.objectContaining({ offset: 48, limit: 48 }),
    )
    expect(listStudioExports).toHaveBeenLastCalledWith(
      expect.objectContaining({ offset: 48, limit: 48 }),
    )
    // 読み直しではなく末尾への連結（1 ページ目のタイルも残る）
    expect(await screen.findByText('追加のポーズ')).toBeTruthy()
    expect(screen.getByText('素材 0')).toBeTruthy()
  })

  it('読み残しが無いときは番兵を置かず、続きも読まない', async () => {
    // 既定のモックは 4 系統とも 1 ページで全件（読み残しが無い）
    show()
    await screen.findByText('決めポーズ')
    expect(screen.queryByTestId('library-sentinel')).toBeNull()

    // 番兵が無いので見張りもしていない = 続きは読まれない
    expect(observers.length).toBe(0)
    await waitFor(() => expect(listLibrary).toHaveBeenCalledTimes(1))
    expect(lastQuery().offset).toBe(0)
  })

  it('読み込み中に番兵がまた見えても重ねて読まない', async () => {
    fillFirstPages()
    show()
    await screen.findByText('素材 0')

    // 続きの応答を止めたまま、もう一度末尾に着いたことにする
    // （ライブラリ以外は 2 ページ目で読み切らせて、読み続けさせない）
    finishSecondPages(item({ id: 'l9', name: '追加のポーズ' }))
    let release: (page: LibraryPage) => void = () => {}
    listLibrary.mockReturnValueOnce(
      new Promise<LibraryPage>((resolve) => {
        release = resolve
      }),
    )
    await reachEnd()
    await waitFor(() => expect(lastQuery().offset).toBe(48))
    expect(screen.getByText('読み込み中…')).toBeTruthy()

    const calls = listLibrary.mock.calls.length
    await reachEnd()
    expect(listLibrary.mock.calls.length).toBe(calls)

    // 読み終わったら（読み切ったので）番兵ごと消える
    await act(async () => {
      release(pageOf([item({ id: 'l9', name: '追加のポーズ' })], { total: 49 }))
    })
    await waitFor(() => expect(screen.queryByTestId('library-sentinel')).toBeNull())
  })

  it('番兵が見えているあいだ読み続けても limit は 48 のまま', async () => {
    // 全 192 件 = 48 件 × 4 ページ。末尾に着いたら読み切るまで自動で続く。
    fillFirstPages(192)
    show()
    await screen.findByText('素材 0')

    // 応答も毎回 48 件なので、offset は 48 → 96 → 144 と進む
    await reachEnd()
    for (const expected of [48, 96, 144]) {
      await waitFor(() =>
        expect(
          listLibrary.mock.calls.some(
            ([query]) => (query as LibraryQuery).offset === expected,
          ),
        ).toBe(true),
      )
    }
    // 読み切ったら番兵ごと消える
    await waitFor(() => expect(screen.queryByTestId('library-sentinel')).toBeNull())
    // サーバーの上限（ライブラリ 200 / ジョブ 500）を超える limit は投げない
    expect(
      listLibrary.mock.calls.every(([query]) => (query as LibraryQuery).limit === 48),
    ).toBe(true)
    expect(
      listJobPage.mock.calls.every(([query]) => (query as JobQuery).limit === 48),
    ).toBe(true)
  })

  it('続きの読み込みは読み切った出どころには投げない', async () => {
    // ライブラリだけ読み残しがあり、他は 1 ページで全件
    listLibrary.mockResolvedValue(pageOf([IMAGE], { total: 60 }))
    show()
    await screen.findByText('決めポーズ')
    vi.clearAllMocks()
    // 続きの 1 件で読み切る（読み残しがあると自動で読み続けてしまう）
    listLibrary.mockResolvedValue(pageOf([item({ id: 'l9', name: '追加のポーズ' })], { total: 2 }))

    await reachEnd()
    await waitFor(() => expect(listLibrary).toHaveBeenCalledTimes(1))
    expect(listJobPage).not.toHaveBeenCalled()
    expect(listStudioAssets).not.toHaveBeenCalled()
    expect(listStudioExports).not.toHaveBeenCalled()
  })

  it('絞り込みを変えると offset 0 から読み直す', async () => {
    fillFirstPages()
    show()
    await screen.findByText('素材 0')
    finishSecondPages(item({ id: 'l9', name: '追加のポーズ' }))
    await reachEnd()
    await waitFor(() => expect(lastQuery().offset).toBe(48))

    // 絞り込み後は 1 ページで読み切る（続きを自動で読ませない）
    listLibrary.mockResolvedValue(pageOf([IMAGE], { total: 1 }))
    fireEvent.change(screen.getByLabelText('ファイルを検索'), {
      target: { value: 'ポーズ' },
    })
    await waitFor(() => expect(lastQuery().q).toBe('ポーズ'))
    expect(lastQuery().offset).toBe(0)
    expect(lastJobQuery().offset).toBe(0)
  })

  it('追記中に絞り込みが変わったら、古い応答で上書きしない', async () => {
    fillFirstPages()
    show()
    await screen.findByText('素材 0')

    // 続きの応答をわざと遅らせ、そのあいだに検索を変える
    let release: (page: LibraryPage) => void = () => {}
    listLibrary.mockReturnValueOnce(
      new Promise<LibraryPage>((resolve) => {
        release = resolve
      }),
    )
    await reachEnd()
    await waitFor(() => expect(lastQuery().offset).toBe(48))

    listLibrary.mockResolvedValue(pageOf([CLIP], { total: 1 }))
    fireEvent.change(screen.getByLabelText('ファイルを検索'), {
      target: { value: '参考' },
    })
    await waitFor(() => expect(lastQuery().q).toBe('参考'))
    await screen.findByText('参考モーション')

    // 追い越された古い応答は捨てる（絞り込み後の一覧に混ざらない）
    release(pageOf([item({ id: 'l9', name: '追い越された古い素材' })], { total: 600 }))
    await waitFor(() => expect(screen.getByText('参考モーション')).toBeTruthy())
    expect(screen.queryByText('追い越された古い素材')).toBeNull()
  })

  it('続きが空で返ったら打ち止めにする（同じ offset を読み続けない）', async () => {
    fillFirstPages()
    show()
    await screen.findByText('素材 0')

    // サーバーの件数（全 600 件）と実際の行がずれ、続きが 0 件で返ってくる
    listLibrary.mockResolvedValue(pageOf([], { total: 600 }))
    listJobPage.mockResolvedValue(jobPageOf([], 600))
    listStudioAssets.mockResolvedValue(assetPageOf([], { total: 600 }))
    listStudioExports.mockResolvedValue(exportPageOf([], { total: 600 }))
    await reachEnd()

    // total を読み込み済みの件数に切り詰めるので、番兵ごと消えて止まる
    await waitFor(() => expect(screen.queryByTestId('library-sentinel')).toBeNull())
    // 読み込み済みのタイルはそのまま残る
    expect(screen.getByText('素材 0')).toBeTruthy()
    const calls = listLibrary.mock.calls.length
    await act(async () => {})
    expect(listLibrary.mock.calls.length).toBe(calls)
  })

  it('続きの読み込みが失敗したら自動で読み直さず、番兵が出直したら 1 回だけ試す', async () => {
    fillFirstPages()
    show()
    await screen.findByText('素材 0')
    vi.clearAllMocks()

    listLibrary.mockRejectedValue(new Error('ネットワークに繋がりません'))
    await reachEnd()
    expect(await screen.findByText('ネットワークに繋がりません')).toBeTruthy()
    // 読み残しはあるままなので番兵は出ているが、自動では読み直さない
    expect(screen.queryByTestId('library-sentinel')).toBeTruthy()
    await act(async () => {})
    expect(listLibrary).toHaveBeenCalledTimes(1)

    // スクロールし直して番兵がまた見えたら、1 回だけ再試行する
    await reachEnd()
    await waitFor(() => expect(listLibrary).toHaveBeenCalledTimes(2))
    await act(async () => {})
    expect(listLibrary).toHaveBeenCalledTimes(2)

    // 同じ offset で 2 回続けて失敗したので、以後は番兵が出直しても投げない
    await reachEnd()
    await act(async () => {})
    expect(listLibrary).toHaveBeenCalledTimes(2)

    // 絞り込みを変えれば歯止めは外れ、また読みにいく
    listLibrary.mockResolvedValue(pageOf([IMAGE], { total: 1 }))
    fireEvent.change(screen.getByLabelText('ファイルを検索'), {
      target: { value: 'ポーズ' },
    })
    await waitFor(() => expect(lastQuery().q).toBe('ポーズ'))
  })

  it('詳細の編集を保存すると PATCH して一覧を取り直す', async () => {
    const { onChanged } = show()
    await open('決めポーズ')

    const name = screen.getByLabelText('表示名') as HTMLInputElement
    fireEvent.change(name, { target: { value: '決めポーズ（改）' } })
    fireEvent.change(screen.getByLabelText('タグ（カンマ区切り）'), {
      target: { value: '背景, 夜景' },
    })
    fireEvent.change(screen.getByLabelText('分類'), {
      target: { value: 'character' },
    })
    updateLibraryItem.mockResolvedValue(item({ name: '決めポーズ（改）' }))

    fireEvent.click(screen.getByRole('button', { name: '保存' }))
    await waitFor(() =>
      expect(updateLibraryItem).toHaveBeenCalledWith('l1', {
        name: '決めポーズ（改）',
        tags: ['背景', '夜景'],
        category: 'character',
        nsfw: false,
      }),
    )
    expect(onChanged).toHaveBeenCalled()
  })

  it('何も変えていないあいだ [保存] は押せない', async () => {
    show()
    await open('決めポーズ')
    expect(screen.getByRole('button', { name: '保存' })).toHaveProperty(
      'disabled',
      true,
    )
  })

  it('削除は確認してから消す（キャンセルなら何もしない）', async () => {
    show()
    await open('決めポーズ')

    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
    fireEvent.click(screen.getByRole('button', { name: '削除' }))
    expect(deleteLibraryItem).not.toHaveBeenCalled()

    confirm.mockReturnValue(true)
    deleteLibraryItem.mockResolvedValue(undefined)
    fireEvent.click(screen.getByRole('button', { name: '削除' }))
    await waitFor(() => expect(deleteLibraryItem).toHaveBeenCalledWith('l1'))
  })

  it('[生成の入力にする] は選んだ素材を返す', async () => {
    const { onUseInGenerate } = show()
    await open('参考モーション')
    fireEvent.click(screen.getByRole('button', { name: '生成の入力にする' }))
    expect(onUseInGenerate).toHaveBeenCalledWith(CLIP)
  })

  // ------------------------------------------------------------ 生成履歴

  it('生成履歴の成果物は種類ごとのタイルになり、詳細にジョブの情報が出る', async () => {
    show()
    pick('生成履歴')
    await waitFor(() => expect(listJobPage).toHaveBeenCalled())
    // 画像 / 最終フレーム / 動画の 3 タイル
    expect(await screen.findAllByText('踊る猫のクリップ')).toHaveLength(3)
    expect(screen.getByText(/^画像 \//)).toBeTruthy()
    expect(screen.getByText(/^最終フレーム \//)).toBeTruthy()
    expect(screen.getByText(/^動画 \//)).toBeTruthy()

    fireEvent.click(screen.getByText(/^動画 \//))
    expect(screen.getByText('minimax_h3_i2v')).toBeTruthy()
    expect(screen.getByText('画像＋動画')).toBeTruthy()
  })

  it('[ライブラリに登録] は from-job をその成果物の source で呼ぶ', async () => {
    const { onChanged } = show()
    pick('生成履歴')
    await screen.findAllByText('踊る猫のクリップ')
    fireEvent.click(screen.getByText(/^最終フレーム \//))

    addJobToLibrary.mockResolvedValue(item({ id: 'l9', source: 'last_frame' }))
    fireEvent.click(screen.getByRole('button', { name: /ライブラリに登録/ }))
    await waitFor(() =>
      expect(addJobToLibrary).toHaveBeenCalledWith('j1', 'last_frame', '', [], 'none'),
    )
    await waitFor(() => expect(onChanged).toHaveBeenCalled())
  })

  it('同じ成果物が既に棚にある（409）ときは「登録済みです」にする', async () => {
    show()
    pick('生成履歴')
    await screen.findAllByText('踊る猫のクリップ')
    fireEvent.click(screen.getByText(/^動画 \//))

    addJobToLibrary.mockRejectedValue(new ApiError(409, 'already in the library'))
    fireEvent.click(screen.getByRole('button', { name: /ライブラリに登録/ }))
    expect(await screen.findByText('登録済みです')).toBeTruthy()
  })

  it('[ジョブを開く] と [生成の入力にする] は元のジョブと URL を返す', async () => {
    const { onOpenJob, onUseMedia } = show()
    pick('生成履歴')
    await screen.findAllByText('踊る猫のクリップ')
    fireEvent.click(screen.getByText(/^動画 \//))

    fireEvent.click(screen.getByRole('button', { name: 'ジョブを開く' }))
    expect(onOpenJob).toHaveBeenCalledWith(expect.objectContaining({ id: 'j1' }))

    fireEvent.click(screen.getByRole('button', { name: '生成の入力にする' }))
    expect(onUseMedia).toHaveBeenCalledWith(
      expect.objectContaining({ kind: 'video', url: '/outputs/j1/clip.mp4' }),
    )
  })

  it('生成履歴の検索・種別・NSFW はサーバー側のクエリに乗る', async () => {
    show()
    await waitFor(() => expect(listJobPage).toHaveBeenCalled())
    // NSFW 表示がオフのあいだは NSFW のジョブを読まない
    expect(lastJobQuery()).toMatchObject({ nsfw: false })

    fireEvent.change(screen.getByLabelText('ファイルを検索'), {
      target: { value: 'かおり' },
    })
    await waitFor(() => expect(lastJobQuery().q).toBe('かおり'))

    fireEvent.change(screen.getByLabelText('種別で絞り込み'), {
      target: { value: 'video' },
    })
    await waitFor(() => expect(lastJobQuery().kind).toBe('video'))

    fireEvent.click(screen.getByLabelText('NSFW表示'))
    await waitFor(() => expect(lastJobQuery().nsfw).toBeUndefined())
  })

  it('作品セレクトで project_id を送る（ライブラリだけのときは無効）', async () => {
    show()
    await waitFor(() => expect(listStudioProjects).toHaveBeenCalled())
    const select = await screen.findByLabelText('作品で絞り込み')
    expect(screen.getByRole('option', { name: 'かおりプロジェクト' })).toBeTruthy()

    fireEvent.change(select, { target: { value: 'p2' } })
    await waitFor(() => expect(lastJobQuery().project_id).toBe('p2'))
    // プロジェクト素材側は既存の project_id クエリを使う
    await waitFor(() =>
      expect(listStudioAssets).toHaveBeenCalledWith(
        expect.objectContaining({ project_id: 'p2' }),
      ),
    )

    pick('ライブラリ')
    await waitFor(() =>
      expect(screen.getByLabelText('作品で絞り込み')).toHaveProperty(
        'disabled',
        true,
      ),
    )
  })

  it('Take 由来のジョブはタイルと詳細に作品名・カット題名を出す', async () => {
    listJobPage.mockResolvedValue(
      jobPageOf([
        job({
          project_id: 'p2',
          project_name: 'かおりプロジェクト',
          shot_id: 's1',
          shot_title: '屋上の逢瀬',
        }),
      ]),
    )
    const onOpenStudioShot = vi.fn()
    show({ onOpenStudioShot })
    pick('生成履歴')
    await screen.findAllByText('踊る猫のクリップ')
    // タイルの 2 行目に「成果物 / 作品 / カット / 日時」
    expect(
      screen.getAllByText(/かおりプロジェクト \/ 屋上の逢瀬/).length,
    ).toBeGreaterThan(0)

    fireEvent.click(screen.getByText(/^動画 \//))
    expect(screen.getByText('屋上の逢瀬')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'スタジオで開く' }))
    expect(onOpenStudioShot).toHaveBeenCalledWith(
      expect.objectContaining({ id: 'j1', shot_id: 's1' }),
    )
  })

  // -------------------------------------------------- プロジェクト素材

  it('プロジェクト素材は作品名つきで並び、詳細は読み取りだけ', async () => {
    const { onOpenStudioAsset } = show()
    pick('プロジェクト素材')
    await waitFor(() => expect(listStudioAssets).toHaveBeenCalled())
    await open('タカシ')
    expect(screen.getAllByText('深夜のラーメン屋').length).toBeGreaterThan(0)
    // 「キャラクター」は分類の絞り込みにも並ぶので、詳細のぶんを数に含めて見る
    expect(screen.getAllByText('キャラクター').length).toBeGreaterThan(1)
    expect(screen.getByText('常連客')).toBeTruthy()
    // 編集はスタジオ側なので、この画面に [保存] は無い
    expect(screen.queryByRole('button', { name: '保存' })).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: 'スタジオで開く' }))
    expect(onOpenStudioAsset).toHaveBeenCalledWith(
      expect.objectContaining({ id: 'a1', project_id: 'p1' }),
    )
  })

  it('素材の追加リファレンス（声・動画・画像）を役割つきで並べる', async () => {
    listStudioAssets.mockResolvedValue(
      assetPageOf([
        asset({
          files: [
            {
              id: 'f1',
              asset_id: 'a1',
              project_id: 'p1',
              role: 'voice',
              path: '/repo/assets/audio/koe.wav',
              url: '/assets/audio/koe.wav',
              caption: '怒っているときの声',
              sort_order: 0,
              created_at: '2026-07-29T11:00:00+00:00',
            },
            {
              id: 'f2',
              asset_id: 'a1',
              project_id: 'p1',
              role: 'video',
              path: '/repo/assets/video/aruki.mp4',
              url: '/assets/video/aruki.mp4',
              caption: '',
              sort_order: 1,
              created_at: '2026-07-29T12:00:00+00:00',
            },
          ],
        }),
      ]),
    )
    show()
    pick('プロジェクト素材')
    await open('タカシ')
    expect(screen.getByText('追加リファレンス 2 件')).toBeTruthy()
    expect(screen.getByText('声サンプル')).toBeTruthy()
    expect(screen.getByText('動画リファレンス')).toBeTruthy()
    expect(screen.getByText('怒っているときの声')).toBeTruthy()
  })

  it('ライブラリ由来の素材は取り込んだ版と [更新あり] を出す', async () => {
    listStudioAssets.mockResolvedValue(
      assetPageOf([
        asset({
          source_library_id: 'l1',
          source_library_version: 2,
          library_update_available: true,
        }),
      ]),
    )
    show()
    pick('プロジェクト素材')
    await open('タカシ')
    expect(screen.getByText('ライブラリ（版 2）')).toBeTruthy()
    expect(screen.getByText('更新あり')).toBeTruthy()
  })

  // -------------------------------------------------------------- 書き出し

  it('書き出しは 1 本 = 1 タイルで、詳細に規格と作品が出る', async () => {
    show()
    pick('書き出し')
    await waitFor(() => expect(listStudioExports).toHaveBeenCalled())
    expect(await screen.findByText('本編 v2')).toBeTruthy()
    // タイルの 2 行目に「書き出し / 作品 / 解像度 / 尺」
    expect(screen.getByText(/書き出し \/ 深夜のラーメン屋 \/ 1280x720 \/ 1:00/)).toBeTruthy()

    fireEvent.click(screen.getByText('本編 v2'))
    expect(screen.getByText('1280x720 / 24fps')).toBeTruthy()
    expect(screen.getAllByText('深夜のラーメン屋').length).toBeGreaterThan(0)
    // 書き出しは記録なので、この画面で直せるものは無い
    expect(screen.queryByRole('button', { name: '保存' })).toBeNull()
  })

  it('[ライブラリに登録] は save-to-library を呼び、409 は「登録済みです」', async () => {
    const { onChanged } = show()
    pick('書き出し')
    fireEvent.click(await screen.findByText('本編 v2'))

    saveExportToLibrary.mockResolvedValue(item({ id: 'l9', kind: 'video' }))
    fireEvent.click(screen.getByRole('button', { name: /ライブラリに登録/ }))
    await waitFor(() =>
      expect(saveExportToLibrary).toHaveBeenCalledWith('x1', '本編 v2'),
    )
    expect(await screen.findByText('登録しました')).toBeTruthy()
    await waitFor(() => expect(onChanged).toHaveBeenCalled())

    cleanup()
    show()
    pick('書き出し')
    fireEvent.click(await screen.findByText('本編 v2'))
    saveExportToLibrary.mockRejectedValue(new ApiError(409, 'already in the library'))
    fireEvent.click(screen.getByRole('button', { name: /ライブラリに登録/ }))
    expect(await screen.findByText('登録済みです')).toBeTruthy()
  })

  it('[編集タブで開く] と [生成の入力にする] は作品と mp4 を返す', async () => {
    const { onOpenStudioTimeline, onUseMedia } = show()
    pick('書き出し')
    fireEvent.click(await screen.findByText('本編 v2'))

    fireEvent.click(screen.getByRole('button', { name: '編集タブで開く' }))
    expect(onOpenStudioTimeline).toHaveBeenCalledWith(
      expect.objectContaining({ id: 'x1', project_id: 'p1' }),
    )

    fireEvent.click(screen.getByRole('button', { name: '生成の入力にする' }))
    expect(onUseMedia).toHaveBeenCalledWith(
      expect.objectContaining({
        kind: 'video',
        url: '/outputs/exports/x1/final.mp4',
      }),
    )
  })

  it('演出まで焼けていれば FX の mp4 を見せる', async () => {
    listStudioExports.mockResolvedValue(
      exportPageOf([
        exported({
          fx_job_id: 'j9',
          fx_status: 'done',
          fx_video_url: '/outputs/j9/fx.mp4',
        }),
      ]),
    )
    const { onUseMedia } = show()
    pick('書き出し')
    fireEvent.click(await screen.findByText('本編 v2'))
    expect(screen.getByText('FX')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: '生成の入力にする' }))
    expect(onUseMedia).toHaveBeenCalledWith(
      expect.objectContaining({ url: '/outputs/j9/fx.mp4' }),
    )
  })

  it('書き出しの検索と作品はサーバー側のクエリに乗り、種別が動画以外なら読まない', async () => {
    show()
    await waitFor(() => expect(listStudioExports).toHaveBeenCalled())

    fireEvent.change(screen.getByLabelText('ファイルを検索'), {
      target: { value: '本編' },
    })
    await waitFor(() =>
      expect(listStudioExports).toHaveBeenLastCalledWith(
        expect.objectContaining({ q: '本編' }),
      ),
    )
    fireEvent.change(screen.getByLabelText('作品で絞り込み'), {
      target: { value: 'p2' },
    })
    await waitFor(() =>
      expect(listStudioExports).toHaveBeenLastCalledWith(
        expect.objectContaining({ project_id: 'p2' }),
      ),
    )

    // 書き出しは必ず mp4 なので、画像に絞っているあいだは投げない
    vi.clearAllMocks()
    fireEvent.change(screen.getByLabelText('種別で絞り込み'), {
      target: { value: 'image' },
    })
    await waitFor(() => expect(listLibrary).toHaveBeenCalled())
    expect(listStudioExports).not.toHaveBeenCalled()
  })

  // ------------------------------------------------------------ その他

  it('アップロードは種別を指定せずに投げる（絞り込み中の分類が付く）', async () => {
    show()
    await waitFor(() => expect(listLibrary).toHaveBeenCalled())
    fireEvent.change(screen.getByLabelText('カテゴリで絞り込み'), {
      target: { value: 'background' },
    })
    const file = new File(['x'], 'bg.png', { type: 'image/png' })
    const input = document.querySelector('input[type=file]') as HTMLInputElement
    fireEvent.change(input, { target: { files: [file] } })
    await waitFor(() =>
      expect(api.uploadAnyToLibrary).toHaveBeenCalledWith(file, [], 'background'),
    )
  })

  it('NSFW 表示はこの画面で切り替えられる', async () => {
    show()
    await screen.findByText('決めポーズ')
    fireEvent.click(screen.getByLabelText('NSFW表示'))
    expect(screen.getByText('ひみつ')).toBeTruthy()
  })

  it('ヘッダーの NSFW トグルが変わるとこの画面も追従する', async () => {
    const { rerender } = render(<LibraryView showNsfw={false} />)
    await screen.findByText('決めポーズ')
    expect(screen.queryByText('ひみつ')).toBeNull()

    rerender(<LibraryView showNsfw={true} />)
    expect(await screen.findByText('ひみつ')).toBeTruthy()

    // 画面内のトグルでの上書きは引き続きできる
    fireEvent.click(screen.getByLabelText('NSFW表示'))
    await waitFor(() => expect(screen.queryByText('ひみつ')).toBeNull())
  })

  it('空のときは出どころと条件で案内を出し分ける', async () => {
    listLibrary.mockResolvedValue(pageOf([]))
    listJobPage.mockResolvedValue(jobPageOf([]))
    listStudioAssets.mockResolvedValue(assetPageOf([]))
    listStudioExports.mockResolvedValue(exportPageOf([]))
    show()
    expect(await screen.findByText('ファイルがまだありません。')).toBeTruthy()

    pick('ライブラリ')
    expect(await screen.findByText('ライブラリはまだ空です。')).toBeTruthy()

    pick('生成履歴')
    expect(await screen.findByText('まだ何も生成していません。')).toBeTruthy()

    pick('プロジェクト素材')
    expect(
      await screen.findByText('World Bible の素材がまだありません。'),
    ).toBeTruthy()

    pick('書き出し')
    expect(await screen.findByText('まだ書き出していません。')).toBeTruthy()

    fireEvent.change(screen.getByLabelText('ファイルを検索'), {
      target: { value: 'ghost' },
    })
    expect(await screen.findByText('条件に合うファイルがありません。')).toBeTruthy()
  })

  it('読み込みに失敗したらメッセージを出す', async () => {
    listLibrary.mockRejectedValue(new Error('バックエンドに接続できません'))
    show()
    expect(await screen.findByText('バックエンドに接続できません')).toBeTruthy()
  })

  it('上部バーに構図リファレンスを作る導線がある', async () => {
    show()
    await waitFor(() => expect(listLibrary).toHaveBeenCalled())
    expect(screen.getByRole('button', { name: '構図リファレンスを作る' })).toBeTruthy()
  })

  it('構図リファレンスが載っていれば版つきのバッジと編集の導線を出す', async () => {
    listLibrary.mockResolvedValue(
      pageOf([item({ blocking: initialBlockingScene(), blocking_version: 2 })]),
    )
    show()
    await open('決めポーズ')
    expect(screen.getByText('構図リファレンス（版 2）')).toBeTruthy()
    expect(screen.getByRole('button', { name: '構図を編集' })).toBeTruthy()
  })

  it('焼き直した構図リファレンスは版つきの URL で出す（古い動画を出さない）', async () => {
    listLibrary.mockResolvedValue(
      pageOf([
        item({
          kind: 'video',
          name: '屋上の対峙',
          url: '/library/video/blocking.mp4',
          blocking: initialBlockingScene(),
          blocking_version: 3,
        }),
      ]),
    )
    show()
    // 他の出どころの動画が混ざらないように、ライブラリだけにする。
    pick('ライブラリ')
    await open('屋上の対峙')
    const videos = Array.from(document.querySelectorAll('video'))
    expect(videos.length).toBeGreaterThan(0)
    // タイルも詳細のプレビューも版つき（?v=3）
    videos.forEach((video) =>
      expect(video.getAttribute('src')).toBe('/library/video/blocking.mp4?v=3'),
    )
  })

  it('reloadKey が変わると読み直す（WS の library 通知）', async () => {
    const { rerender } = render(<LibraryView showNsfw={false} reloadKey={1} />)
    await waitFor(() => expect(listLibrary).toHaveBeenCalledTimes(1))
    rerender(<LibraryView showNsfw={false} reloadKey={2} />)
    await waitFor(() => expect(listLibrary).toHaveBeenCalledTimes(2))
  })

  it('jobsReloadKey が変わると読み直す（ジョブ一覧の更新）', async () => {
    const { rerender } = render(<LibraryView showNsfw={false} jobsReloadKey={1} />)
    await waitFor(() => expect(listJobPage).toHaveBeenCalledTimes(1))
    rerender(<LibraryView showNsfw={false} jobsReloadKey={2} />)
    await waitFor(() => expect(listJobPage).toHaveBeenCalledTimes(2))
  })
})
