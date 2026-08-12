import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from '../../api'
import type {
  CanvasBoard,
  CanvasCard,
  CanvasMessage,
  CanvasProgress,
  JobProgress,
  StudioAsset,
  StudioEpisode,
  StudioProjectDetail,
  StudioShot,
} from '../../types'
import CanvasView from './CanvasView'

// キャンバス画面は /api/canvas と /api/studio を自前で叩くので、全部ここから返す。
vi.mock('../../api', async () => {
  const actual = await vi.importActual<typeof import('../../api')>('../../api')
  return {
    ...actual,
    api: {
      options: vi.fn(),
      getCanvasBoard: vi.fn(),
      setCanvasViewport: vi.fn(),
      createCanvasCard: vi.fn(),
      updateCanvasCard: vi.fn(),
      moveCanvasCard: vi.fn(),
      deleteCanvasCard: vi.fn(),
      createCanvasMessage: vi.fn(),
      createStudioEpisode: vi.fn(),
      runCanvasAgent: vi.fn(),
      getCanvasAgentState: vi.fn(),
      stopCanvasAgent: vi.fn(),
      updateStudioAsset: vi.fn(),
      updateStudioScene: vi.fn(),
      createStudioShot: vi.fn(),
      updateStudioShot: vi.fn(),
      previewStudioShotPrompt: vi.fn(),
      uploadCanvasAttachment: vi.fn(),
      canvasAttachmentUrl: (projectId: string, path: string) =>
        `/api/canvas/projects/${projectId}/attachments/${path}`,
      uploadStudioAssetFile: vi.fn(),
      addStudioAssetFile: vi.fn(),
      deleteStudioAssetFile: vi.fn(),
    },
  }
})

afterEach(cleanup)

const mocked = api as unknown as Record<string, ReturnType<typeof vi.fn>>

function asset(id: string, overrides: Partial<StudioAsset> = {}): StudioAsset {
  return {
    id,
    project_id: 'p1',
    name: 'アキ',
    category: 'character',
    caption: '主人公',
    prompt_caption: '',
    kind: 'image',
    path: '',
    url: '',
    locked: false,
    sort_order: 0,
    created_at: '2026-01-01T00:00:00Z',
    ...overrides,
  }
}

function shot(id: string, overrides: Partial<StudioShot> = {}): StudioShot {
  return {
    id,
    project_id: 'p1',
    scene_id: null,
    sort_order: 0,
    title: 'カット1',
    purpose: '',
    action: '',
    dialogue: '',
    soundscape: '',
    bgm: '',
    camera: '',
    duration_seconds: 5,
    prompt: '路地を歩く',
    status: 'draft',
    selected_take_id: null,
    carry_over_end_frame: false,
    nsfw: false,
    aspect_ratio: null,
    megapixels: null,
    seed: null,
    workflow_override: null,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    ...overrides,
  }
}

function card(id: string, overrides: Partial<CanvasCard> = {}): CanvasCard {
  return {
    id,
    project_id: 'p1',
    kind: 'character',
    entity_id: 'a1',
    episode_id: null,
    data: {},
    x: 0,
    y: 0,
    w: 320,
    h: 220,
    z: 0,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    ...overrides,
  }
}

const detail: StudioProjectDetail = {
  id: 'p1',
  name: '夜明けの街',
  code: '',
  synopsis: '',
  world_notes: '',
  auto_translate: true,
  latent_continuity: false,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
  assets: [asset('a1')],
  episodes: [],
  scenes: [],
  shots: [shot('sh1')],
  takes: [],
}

function board(overrides: Partial<CanvasBoard> = {}): CanvasBoard {
  return {
    project_id: 'p1',
    episode_id: null,
    viewport: { x: 0, y: 0, zoom: 1 },
    cards: [card('c1')],
    messages: [],
    ...overrides,
  }
}

function message(overrides: Partial<CanvasMessage> = {}): CanvasMessage {
  return {
    id: 'm1',
    project_id: 'p1',
    ts: '2026-01-01T00:00:00Z',
    role: 'user',
    content: '@アキ の立ち絵がほしい',
    kind: null,
    data: {},
    ...overrides,
  }
}

/** WS の 1 フレーム（`type: "canvas"`）。 */
function frame(overrides: Partial<CanvasProgress> = {}): CanvasProgress {
  return {
    type: 'canvas',
    project_id: 'p1',
    running: true,
    activity: null,
    message: null,
    ...overrides,
  }
}

function setup(data: CanvasBoard = board(), project: StudioProjectDetail = detail) {
  mocked.options.mockResolvedValue({})
  mocked.setCanvasViewport.mockResolvedValue(data.viewport)
  mocked.getCanvasBoard.mockResolvedValue(data)
  mocked.getCanvasAgentState.mockResolvedValue({
    project_id: 'p1',
    running: false,
    activity: null,
  })
  mocked.previewStudioShotPrompt.mockResolvedValue({
    shot_id: 'sh1',
    workflow: 'minimax_h3_r2v',
    workflow_reason: 'プロンプトがファイルのある素材を呼んでいます（参照として添付）',
    prompt: '<Picture 1> が歩いてくる。\nNo text, subtitles, logos or watermarks.',
    references: [
      {
        name: 'アキ',
        kind: 'image',
        tag: '<Picture 1>',
        path: '/assets/image/aki.png',
      },
    ],
    start_frame: null,
    auto_translate: true,
  latent_continuity: false,
    will_translate: true,
    error: '',
  })
  const reload = vi.fn().mockResolvedValue(undefined)
  const view = render(<CanvasView detail={project} onReloadStudio={reload} />)
  const emit = (event: CanvasProgress) =>
    view.rerender(
      <CanvasView detail={project} event={event} onReloadStudio={reload} />,
    )
  /** WS のジョブ進捗（App が集めているもの）を差し替える。 */
  const progress = (frames: Record<string, JobProgress>) =>
    view.rerender(
      <CanvasView detail={project} progress={frames} onReloadStudio={reload} />,
    )
  return { reload, emit, progress }
}

function jobFrame(jobId: string, status: JobProgress['status']): JobProgress {
  return {
    type: 'job',
    job_id: jobId,
    status,
    node: null,
    progress: null,
    message: null,
    nsfw: null,
  }
}

/** 話が 2 つある作品（タブの試験に使う）。 */
function episode(id: string, title = ''): StudioEpisode {
  return {
    id,
    project_id: 'p1',
    sort_order: 0,
    title,
    synopsis: '',
    created_at: '2026-01-01T00:00:00Z',
  }
}

const serial: StudioProjectDetail = {
  ...detail,
  episodes: [episode('e1', '第1話'), episode('e2')],
  scenes: [
    {
      id: 'sc1',
      episode_id: 'e1',
      project_id: 'p1',
      sort_order: 0,
      title: '路地',
      synopsis: '',
      time_of_day: '',
      created_at: '2026-01-01T00:00:00Z',
    },
  ],
  shots: [shot('sh1'), shot('sh2', { scene_id: 'sc1', title: 'カット2' })],
}

describe('CanvasView のタブ', () => {
  afterEach(() => window.sessionStorage.clear())

  /** 「前に第1話を開いていた」状態から始める。 */
  const reopenOnEpisode = () =>
    window.sessionStorage.setItem('canvas-tab:p1', 'e1')

  it('作品共通と話ごとのタブを出し、切り替えるとその盤面を取り直す', async () => {
    setup(board(), serial)
    await screen.findByText('@アキ')
    expect(screen.getByRole('tab', { name: '作品共通' })).toBeTruthy()
    expect(screen.getByRole('tab', { name: '第1話' })).toBeTruthy()
    // 無題の話は通し番号で呼ぶ
    expect(screen.getByRole('tab', { name: '第 2 話' })).toBeTruthy()

    mocked.getCanvasBoard.mockResolvedValue(
      board({
        episode_id: 'e1',
        cards: [card('c2', { kind: 'shot', entity_id: 'sh2' })],
      }),
    )
    fireEvent.click(screen.getByRole('tab', { name: '第1話' }))

    await waitFor(() =>
      expect(mocked.getCanvasBoard).toHaveBeenCalledWith('p1', 'e1'),
    )
    expect(await screen.findByText('カット2')).toBeTruthy()
    // 作品共通のカード（素材）は出ない
    expect(screen.queryByText('@アキ')).toBeNull()
  })

  it('他のタブのカードが混ざっていても、開いているタブのぶんだけ出す', async () => {
    // 話を付け替えた直後など、取り直すまでの一瞬に混ざることがある。
    reopenOnEpisode()
    setup(
      board({
        episode_id: 'e1',
        cards: [
          card('c1'), // 素材 = 作品共通
          card('c2', { kind: 'shot', entity_id: 'sh2' }), // 第1話の場のカット
          card('c3', { kind: 'shot', entity_id: 'sh1' }), // 未分類 = 作品共通
        ],
      }),
      serial,
    )
    expect(await screen.findByText('カット2')).toBeTruthy()
    expect(screen.queryByText('@アキ')).toBeNull()
    expect(screen.queryByText('カット1')).toBeNull()
  })

  it('メモは「いま開いているタブ」に置かれる', async () => {
    reopenOnEpisode()
    setup(board({ episode_id: 'e1', cards: [] }), serial)
    await screen.findByRole('tab', { name: '第1話' })
    fireEvent.click(screen.getByRole('button', { name: 'カードを追加' }))
    fireEvent.click(screen.getByRole('button', { name: /メモ/ }))
    mocked.createCanvasCard.mockResolvedValue(
      card('c9', { kind: 'text', entity_id: null, episode_id: 'e1' }),
    )
    fireEvent.click(screen.getByRole('button', { name: '作る' }))

    await waitFor(() => expect(mocked.createCanvasCard).toHaveBeenCalled())
    const [, payload] = mocked.createCanvasCard.mock.calls[0]
    expect(payload.episode_id).toBe('e1')
  })

  it('「＋」で話を足して、そのタブを開く', async () => {
    const { reload } = setup(board(), serial)
    await screen.findByRole('tab', { name: '作品共通' })
    mocked.createStudioEpisode.mockResolvedValue(episode('e3'))
    mocked.getCanvasBoard.mockResolvedValue(board({ episode_id: 'e3', cards: [] }))

    fireEvent.click(screen.getByRole('button', { name: '話を追加' }))

    await waitFor(() => expect(mocked.createStudioEpisode).toHaveBeenCalledWith('p1'))
    await waitFor(() => expect(reload).toHaveBeenCalled())
    await waitFor(() =>
      expect(mocked.getCanvasBoard).toHaveBeenCalledWith('p1', 'e3'),
    )
  })

  it('エージェントの実行には開いているタブを渡す', async () => {
    reopenOnEpisode()
    setup(board({ episode_id: 'e1', cards: [] }), serial)
    await screen.findByRole('tab', { name: '第1話' })

    mocked.runCanvasAgent.mockResolvedValue({
      project_id: 'p1',
      running: true,
      activity: null,
      message: message(),
    })
    fireEvent.change(screen.getByPlaceholderText(/やりたいことを書く/), {
      target: { value: 'この話のカットを足して' },
    })
    fireEvent.click(screen.getByRole('button', { name: '送信' }))

    await waitFor(() =>
      expect(mocked.runCanvasAgent).toHaveBeenCalledWith(
        'p1',
        'この話のカットを足して',
        'e1',
        [],
      ),
    )
  })

  it('入り直したときは前に開いていたタブから始める', async () => {
    setup(board(), serial)
    await screen.findByRole('tab', { name: '第1話' })
    mocked.getCanvasBoard.mockResolvedValue(board({ episode_id: 'e1', cards: [] }))
    fireEvent.click(screen.getByRole('tab', { name: '第1話' }))
    await waitFor(() =>
      expect(mocked.getCanvasBoard).toHaveBeenCalledWith('p1', 'e1'),
    )
    cleanup()

    mocked.getCanvasBoard.mockClear()
    setup(board({ episode_id: 'e1', cards: [] }), serial)
    await waitFor(() =>
      expect(mocked.getCanvasBoard).toHaveBeenCalledWith('p1', 'e1'),
    )
  })
})

describe('CanvasView', () => {
  it('カードの中身はスタジオの詳細から出す', async () => {
    setup()
    expect(await screen.findByText('@アキ')).toBeTruthy()
    expect(screen.getByText('主人公')).toBeTruthy()
    expect(mocked.getCanvasBoard).toHaveBeenCalledWith('p1', null)
  })

  it('初めて開いたときは全体が見える位置に寄せる', async () => {
    // 表示位置が既定のまま = まだ一度も動かしていないので、鏡が並べたものに合わせる。
    const box = vi
      .spyOn(HTMLElement.prototype, 'getBoundingClientRect')
      .mockReturnValue({ width: 800, height: 600, top: 0, left: 0 } as DOMRect)
    setup(
      board({
        cards: [
          card('c1', { x: 0, y: 0 }),
          card('c2', { kind: 'shot', entity_id: 'sh1', x: 4000, y: 3000 }),
        ],
      }),
    )
    await screen.findByText('@アキ')
    await waitFor(() => expect(mocked.setCanvasViewport).toHaveBeenCalled(), {
      timeout: 3000,
    })
    const [, viewport] = mocked.setCanvasViewport.mock.calls[0]
    expect(viewport.zoom).toBeLessThan(1)
    box.mockRestore()
  })

  it('動かしたことのある表示位置は勝手に変えない', async () => {
    setup(board({ viewport: { x: -120, y: 40, zoom: 1.5 } }))
    await screen.findByText('@アキ')
    await waitFor(() => expect(mocked.getCanvasBoard).toHaveBeenCalled())
    await new Promise((resolve) => setTimeout(resolve, 1200))
    expect(mocked.setCanvasViewport).not.toHaveBeenCalled()
  })

  it('スタジオの中身は開いた時点でカードになっている', async () => {
    // 盤面はサーバー側の鏡なので、フロントからは「並べる」操作を投げない。
    setup(board({ cards: [card('c1'), card('c2', { kind: 'shot', entity_id: 'sh1' })] }))
    expect(await screen.findByText('@アキ')).toBeTruthy()
    expect(screen.getByText('カット1')).toBeTruthy()
    expect(mocked.createCanvasCard).not.toHaveBeenCalled()
  })

  it('本当に空のときだけ案内を出す', async () => {
    setup(board({ cards: [] }))
    expect(await screen.findByText(/まだ中身がありません/)).toBeTruthy()
    cleanup()

    setup()
    await screen.findByText('@アキ')
    expect(screen.queryByText(/まだ中身がありません/)).toBeNull()
  })

  it('「＋」から作れるのは新しいものだけ（生成物は出ない）', async () => {
    setup()
    await screen.findByText('@アキ')
    fireEvent.click(screen.getByRole('button', { name: 'カードを追加' }))
    expect(screen.queryByRole('button', { name: /生成物/ })).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: /カット/ }))
    expect(screen.queryByRole('button', { name: 'スタジオにあるものから' })).toBeNull()
    fireEvent.change(screen.getByLabelText('タイトル'), {
      target: { value: '路地を歩く' },
    })
    mocked.createCanvasCard.mockResolvedValue(
      card('c2', { kind: 'shot', entity_id: 'sh2' }),
    )
    fireEvent.click(screen.getByRole('button', { name: '作る' }))

    await waitFor(() => expect(mocked.createCanvasCard).toHaveBeenCalled())
    const [projectId, payload] = mocked.createCanvasCard.mock.calls[0]
    expect(projectId).toBe('p1')
    expect(payload.kind).toBe('shot')
    expect(payload.title).toBe('路地を歩く')
    expect(payload.entity_id).toBeUndefined()
  })

  it('参照カードはエンティティごとしか消せない', async () => {
    setup()
    await screen.findByText('@アキ')
    fireEvent.click(screen.getByRole('button', { name: '@アキ を編集' }))
    expect(screen.queryByRole('button', { name: /カードだけ外す/ })).toBeNull()
    expect(screen.getByRole('button', { name: /ごと削除/ })).toBeTruthy()
  })

  it('エンティティごと削除は確認を取ってからスタジオも取り直す', async () => {
    const { reload } = setup()
    await screen.findByText('@アキ')
    mocked.deleteCanvasCard.mockResolvedValue(undefined)
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)

    fireEvent.click(screen.getByRole('button', { name: '@アキ を編集' }))
    fireEvent.click(screen.getByRole('button', { name: /ごと削除/ }))
    expect(confirm).toHaveBeenCalled()
    expect(mocked.deleteCanvasCard).not.toHaveBeenCalled()

    confirm.mockReturnValue(true)
    fireEvent.click(screen.getByRole('button', { name: /ごと削除/ }))
    await waitFor(() => expect(mocked.deleteCanvasCard).toHaveBeenCalledWith('c1', true))
    await waitFor(() => expect(reload).toHaveBeenCalled())
  })

  it('素材カードの編集はスタジオの素材 API に保存する', async () => {
    const { reload } = setup()
    await screen.findByText('@アキ')
    mocked.updateStudioAsset.mockResolvedValue(asset('a1'))

    fireEvent.click(screen.getByRole('button', { name: '@アキ を編集' }))
    fireEvent.change(screen.getByLabelText('外見'), {
      target: { value: '赤い上着' },
    })
    fireEvent.click(screen.getByRole('button', { name: '保存' }))

    await waitFor(() => expect(mocked.updateStudioAsset).toHaveBeenCalled())
    const [assetId, patch] = mocked.updateStudioAsset.mock.calls[0]
    expect(assetId).toBe('a1')
    expect(patch.profile).toEqual({
      appearance: '赤い上着',
      personality: '',
      voice: '',
      notes: '',
    })
    await waitFor(() => expect(reload).toHaveBeenCalled())
  })

  it('チャットの発言はエージェントを走らせる', async () => {
    setup()
    await screen.findByText('@アキ')

    mocked.runCanvasAgent.mockResolvedValue({
      project_id: 'p1',
      running: true,
      activity: null,
      message: message(),
    })
    fireEvent.change(screen.getByPlaceholderText(/やりたいことを書く/), {
      target: { value: '@アキ の立ち絵がほしい' },
    })
    fireEvent.click(screen.getByRole('button', { name: '送信' }))

    await waitFor(() =>
      expect(mocked.runCanvasAgent).toHaveBeenCalledWith(
        'p1',
        '@アキ の立ち絵がほしい',
        null,
        [],
      ),
    )
    // 実行中は送信できず、止める口が出る
    expect(await screen.findByText('@アキ の立ち絵がほしい')).toBeTruthy()
    expect(await screen.findByRole('button', { name: /停止/ })).toBeTruthy()
    expect(
      (screen.getByRole('button', { name: '送信' }) as HTMLButtonElement).disabled,
    ).toBe(true)
  })

  it('実行中の応答とツール実行は WS のフレームから履歴に積む', async () => {
    const { emit } = setup()
    await screen.findByText('@アキ')

    emit(
      frame({
        activity: 'ツール実行中: canvas_place_card',
        message: message({ id: 'm2', role: 'assistant', content: '置きますね。' }),
      }),
    )
    expect(await screen.findByText('置きますね。')).toBeTruthy()
    expect(screen.getByText(/canvas_place_card/)).toBeTruthy()

    emit(
      frame({
        message: message({
          id: 'm3',
          role: 'event',
          kind: 'canvas_card_placed',
          content: 'カード `c9` [character] を (0, 0) に置きました。',
        }),
      }),
    )
    expect(await screen.findByText(/カード `c9`/)).toBeTruthy()
  })

  it('実行が終わったら盤面もスタジオも取り直す', async () => {
    const { emit, reload } = setup()
    await screen.findByText('@アキ')
    mocked.getCanvasBoard.mockClear()

    emit(frame({ running: false }))

    await waitFor(() => expect(mocked.getCanvasBoard).toHaveBeenCalledWith('p1', null))
    await waitFor(() => expect(reload).toHaveBeenCalled())
    expect(screen.queryByRole('button', { name: /停止/ })).toBeNull()
  })

  it('他のプロジェクトのフレームは無視する', async () => {
    const { emit } = setup()
    await screen.findByText('@アキ')

    emit(
      frame({
        project_id: 'other',
        message: message({ id: 'm9', role: 'assistant', content: 'よその返事' }),
      }),
    )
    await waitFor(() => expect(screen.queryByText('よその返事')).toBeNull())
  })

  it('⏹ で実行を止める', async () => {
    const { emit } = setup()
    await screen.findByText('@アキ')
    emit(frame({ running: true }))

    mocked.stopCanvasAgent.mockResolvedValue({
      project_id: 'p1',
      running: false,
      activity: null,
    })
    fireEvent.click(await screen.findByRole('button', { name: /停止/ }))

    await waitFor(() => expect(mocked.stopCanvasAgent).toHaveBeenCalledWith('p1'))
  })

  it('生成ジョブの状態が動いたら盤面とスタジオを取り直す', async () => {
    const { reload, progress } = setup()
    await screen.findByText('@アキ')
    const boards = mocked.getCanvasBoard.mock.calls.length
    reload.mockClear()

    progress({ j1: jobFrame('j1', 'running') })
    // デバウンスぶん待ってから 1 回だけ取り直す（進捗の連打では走らせない）
    await waitFor(() =>
      expect(mocked.getCanvasBoard.mock.calls.length).toBeGreaterThan(boards),
    )
    expect(reload).toHaveBeenCalled()
  })
})

describe('CanvasView の添付', () => {
  const file = () => new File(['DATA'], 'koe.wav', { type: 'audio/wav' })

  it('添付したファイルは発言と一緒にエージェントへ渡す', async () => {
    setup()
    await screen.findByText('@アキ')
    mocked.uploadCanvasAttachment.mockResolvedValue({
      name: 'koe.wav',
      path: 'attachments/koe_1.wav',
      abs_path: '/tmp/canvas/attachments/koe_1.wav',
      kind: 'audio',
    })
    mocked.runCanvasAgent.mockResolvedValue({
      project_id: 'p1',
      running: true,
      activity: null,
      message: message(),
    })

    fireEvent.change(screen.getByTestId('canvas-attachment-input'), {
      target: { files: [file()] },
    })
    await waitFor(() =>
      expect(mocked.uploadCanvasAttachment).toHaveBeenCalledWith('p1', expect.any(File)),
    )
    // 送信前はチップとして見えている
    expect(await screen.findByTitle('/tmp/canvas/attachments/koe_1.wav')).toBeTruthy()

    fireEvent.change(screen.getByPlaceholderText(/やりたいことを書く/), {
      target: { value: 'この声で' },
    })
    fireEvent.click(screen.getByRole('button', { name: '送信' }))

    await waitFor(() =>
      expect(mocked.runCanvasAgent).toHaveBeenCalledWith('p1', 'この声で', null, [
        'attachments/koe_1.wav',
      ]),
    )
  })

  it('本文が空でも添付だけで送れる', async () => {
    setup()
    await screen.findByText('@アキ')
    mocked.uploadCanvasAttachment.mockResolvedValue({
      name: 'koe.wav',
      path: 'attachments/koe_2.wav',
      abs_path: '/tmp/canvas/attachments/koe_2.wav',
      kind: 'audio',
    })
    mocked.runCanvasAgent.mockResolvedValue({
      project_id: 'p1',
      running: true,
      activity: null,
      message: message(),
    })

    fireEvent.change(screen.getByTestId('canvas-attachment-input'), {
      target: { files: [file()] },
    })
    await waitFor(() =>
      expect(
        (screen.getByRole('button', { name: '送信' }) as HTMLButtonElement).disabled,
      ).toBe(false),
    )
    fireEvent.click(screen.getByRole('button', { name: '送信' }))
    await waitFor(() =>
      expect(mocked.runCanvasAgent).toHaveBeenCalledWith('p1', '', null, [
        'attachments/koe_2.wav',
      ]),
    )
  })

  it('履歴の添付はサムネイルとして出る', async () => {
    setup(
      board({
        messages: [
          message({
            content: 'この声で\n\n[Attached files]',
            data: {
              text: 'この声で',
              attachments: [
                {
                  path: 'attachments/koe_1.wav',
                  name: 'koe.wav',
                  abs_path: '/tmp/canvas/attachments/koe_1.wav',
                  kind: 'audio',
                },
              ],
            },
          }),
        ],
      }),
    )
    // 本文は元の文だけ（エージェント向けの一覧は出さない）
    expect(await screen.findByText('この声で')).toBeTruthy()
    expect(screen.queryByText(/Attached files/)).toBeNull()
    expect(screen.getByText('koe.wav')).toBeTruthy()
  })
})

describe('CanvasView の素材ファイル', () => {
  it('カード編集からメインのファイルを差し替えられる', async () => {
    const { reload } = setup()
    await screen.findByText('@アキ')
    mocked.uploadStudioAssetFile.mockResolvedValue(asset('a1'))

    fireEvent.click(screen.getByRole('button', { name: '@アキ を編集' }))
    fireEvent.change(screen.getByLabelText(/メインのファイル/), {
      target: { files: [new File(['DATA'], 'aki.png', { type: 'image/png' })] },
    })

    await waitFor(() =>
      expect(mocked.uploadStudioAssetFile).toHaveBeenCalledWith(
        'a1',
        expect.any(File),
      ),
    )
    await waitFor(() => expect(reload).toHaveBeenCalled())
  })

  it('カード編集から声のリファレンスを足せる', async () => {
    const { reload } = setup()
    await screen.findByText('@アキ')
    mocked.addStudioAssetFile.mockResolvedValue({})

    fireEvent.click(screen.getByRole('button', { name: '@アキ を編集' }))
    fireEvent.change(screen.getByLabelText('メモ（任意）'), {
      target: { value: '落ち着いた声' },
    })
    fireEvent.change(screen.getByLabelText('リファレンスを追加'), {
      target: { files: [new File(['DATA'], 'koe.wav', { type: 'audio/wav' })] },
    })

    await waitFor(() =>
      expect(mocked.addStudioAssetFile).toHaveBeenCalledWith(
        'a1',
        expect.any(File),
        { role: 'voice', caption: '落ち着いた声' },
      ),
    )
    await waitFor(() => expect(reload).toHaveBeenCalled())
  })
})

describe('CanvasView: 場カードからのカット追加', () => {
  afterEach(() => window.sessionStorage.clear())

  it('「＋カット」はその場に属するカットを作り、盤面とスタジオを取り直す', async () => {
    window.sessionStorage.setItem('canvas-tab:p1', 'e1')
    const { reload } = setup(
      board({
        episode_id: 'e1',
        cards: [card('c5', { kind: 'scene', entity_id: 'sc1', episode_id: 'e1' })],
      }),
      serial,
    )
    expect(await screen.findByText('カット 1 件')).toBeTruthy()
    mocked.createStudioShot.mockResolvedValue(shot('sh9', { scene_id: 'sc1' }))
    mocked.getCanvasBoard.mockClear()

    fireEvent.click(screen.getByRole('button', { name: '路地 にカットを追加' }))

    await waitFor(() =>
      expect(mocked.createStudioShot).toHaveBeenCalledWith('p1', {
        title: 'カット 3',
        scene_id: 'sc1',
      }),
    )
    // 鏡がカード化するので、盤面とスタジオを取り直せば出てくる
    await waitFor(() => expect(mocked.getCanvasBoard).toHaveBeenCalledWith('p1', 'e1'))
    await waitFor(() => expect(reload).toHaveBeenCalled())
  })

  it('カットカードでない場所には「＋カット」を出さない', async () => {
    setup()
    await screen.findByText('@アキ')
    expect(screen.queryByRole('button', { name: /にカットを追加/ })).toBeNull()
  })
})

describe('CanvasView: カットカードの編集', () => {
  it('効果音・BGM を含めて PATCH する', async () => {
    const { reload } = setup(
      board({ cards: [card('c2', { kind: 'shot', entity_id: 'sh1' })] }),
    )
    await screen.findByText('カット1')
    mocked.updateStudioShot.mockResolvedValue(shot('sh1'))

    fireEvent.click(screen.getByRole('button', { name: 'カット1 を編集' }))
    fireEvent.change(screen.getByLabelText('効果音・環境音'), {
      target: { value: '雨音、遠くの電車' },
    })
    fireEvent.change(screen.getByLabelText('BGM'), {
      target: { value: 'しずかなピアノ' },
    })
    fireEvent.click(screen.getByRole('button', { name: '保存' }))

    await waitFor(() => expect(mocked.updateStudioShot).toHaveBeenCalled())
    const [shotId, patch] = mocked.updateStudioShot.mock.calls[0]
    expect(shotId).toBe('sh1')
    expect(patch.soundscape).toBe('雨音、遠くの電車')
    expect(patch.bgm).toBe('しずかなピアノ')
    await waitFor(() => expect(reload).toHaveBeenCalled())
  })

  it('カットカードの編集フォームに投入プレビューが出る', async () => {
    setup(board({ cards: [card('c2', { kind: 'shot', entity_id: 'sh1' })] }))
    await screen.findByText('カット1')

    fireEvent.click(screen.getByRole('button', { name: 'カット1 を編集' }))
    await waitFor(() =>
      expect(mocked.previewStudioShotPrompt).toHaveBeenCalledWith('sh1'),
    )
    expect(await screen.findByText('r2v（参照素材から）')).toBeTruthy()
    const body = await screen.findByLabelText('投入される最終プロンプト')
    expect(body.textContent).toContain('<Picture 1> が歩いてくる。')
    expect(screen.getByText(/アキ（image \/ aki\.png）/)).toBeTruthy()
    expect(screen.getByText(/投入時に英語へ自動変換されます/)).toBeTruthy()
  })

  it('保存済みの効果音・BGM は開いた時点でフォームに入っている', async () => {
    setup(board({ cards: [card('c2', { kind: 'shot', entity_id: 'sh2' })] }), {
      ...detail,
      shots: [shot('sh2', { soundscape: '波の音', bgm: 'アコギ' })],
    })
    await screen.findByText('カット1')

    fireEvent.click(screen.getByRole('button', { name: 'カット1 を編集' }))
    expect((screen.getByLabelText('効果音・環境音') as HTMLInputElement).value).toBe(
      '波の音',
    )
    expect((screen.getByLabelText('BGM') as HTMLInputElement).value).toBe('アコギ')
  })
})
