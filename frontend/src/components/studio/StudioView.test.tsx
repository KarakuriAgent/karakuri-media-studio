import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError, api } from '../../api'
import type {
  StudioEpisode,
  StudioProjectDetail,
  StudioProjectSummary,
  StudioScene,
  StudioShot,
  StudioShotPreview,
  StudioTake,
} from '../../types'
import StudioView from './StudioView'

// スタジオ画面は自前で /api/studio を叩くので、全部ここから返す。
vi.mock('../../api', async () => {
  const actual = await vi.importActual<typeof import('../../api')>('../../api')
  return {
    ...actual,
    api: {
      getStudioCapabilities: vi.fn(),
      listStudioProjects: vi.fn(),
      createStudioProject: vi.fn(),
      createStudioDemoProject: vi.fn(),
      getStudioProject: vi.fn(),
      updateStudioProject: vi.fn(),
      deleteStudioProject: vi.fn(),
      uploadStudioAsset: vi.fn(),
      createStudioAsset: vi.fn(),
      uploadStudioAssetFile: vi.fn(),
      addStudioAssetFile: vi.fn(),
      deleteStudioAssetFile: vi.fn(),
      updateStudioAsset: vi.fn(),
      deleteStudioAsset: vi.fn(),
      createStudioEpisode: vi.fn(),
      reorderStudioEpisodes: vi.fn(),
      updateStudioEpisode: vi.fn(),
      deleteStudioEpisode: vi.fn(),
      createStudioScene: vi.fn(),
      reorderStudioScenes: vi.fn(),
      updateStudioScene: vi.fn(),
      deleteStudioScene: vi.fn(),
      listStudioRevisions: vi.fn(),
      getStudioRevision: vi.fn(),
      restoreStudioRevision: vi.fn(),
      createStudioShot: vi.fn(),
      reorderStudioShots: vi.fn(),
      updateStudioShot: vi.fn(),
      deleteStudioShot: vi.fn(),
      previewStudioShotPrompt: vi.fn(),
      renderStudioShot: vi.fn(),
      selectStudioTake: vi.fn(),
      rejectStudioTake: vi.fn(),
      deleteStudioTake: vi.fn(),
    },
  }
})

afterEach(cleanup)

const mocked = api as unknown as Record<string, ReturnType<typeof vi.fn>>

// 概要タブは開いたときに接続先のケーパビリティを聞きに行く（ラテント連続性の
// トグルの出し分け）。既定は「使える」にして、他のテストの邪魔をしない。
beforeEach(() => {
  mocked.getStudioCapabilities.mockResolvedValue({
    latent_continuity: true,
    error: '',
  })
})

function shot(id: string, overrides: Partial<StudioShot> = {}): StudioShot {
  return {
    id,
    project_id: 'p1',
    scene_id: null,
    sort_order: 0,
    title: id,
    purpose: '',
    action: '',
    dialogue: '',
    soundscape: '',
    bgm: '',
    camera: '',
    duration_seconds: 5,
    prompt: 'a quiet street',
    status: 'draft',
    selected_take_id: null,
    carry_over_end_frame: false,
    nsfw: false,
    aspect_ratio: null,
    megapixels: null,
    seed: null,
    workflow_override: null,
    created_at: '2026-01-01T00:00:00+00:00',
    updated_at: '2026-01-01T00:00:00+00:00',
    ...overrides,
  }
}

function episode(id: string, title: string): StudioEpisode {
  return {
    id,
    project_id: 'p1',
    sort_order: 0,
    title,
    synopsis: '',
    created_at: '2026-01-01T00:00:00+00:00',
  }
}

function scene(id: string, episodeId: string, title: string): StudioScene {
  return {
    id,
    episode_id: episodeId,
    project_id: 'p1',
    sort_order: 0,
    title,
    synopsis: '',
    time_of_day: '',
    created_at: '2026-01-01T00:00:00+00:00',
  }
}

function take(id: string, overrides: Partial<StudioTake> = {}): StudioTake {
  return {
    id,
    shot_id: 'カット1',
    project_id: 'p1',
    job_id: `job-${id}`,
    status: 'candidate',
    created_at: '2026-01-01T00:00:00+00:00',
    job_status: 'done',
    video_workflow: 'minimax_h3_t2v',
    video_path: `/outputs/${id}.mp4`,
    video_url: `/outputs/${id}.mp4`,
    last_frame_path: null,
    last_frame_url: null,
    error: null,
    ...overrides,
  }
}

function detail(overrides: Partial<StudioProjectDetail> = {}): StudioProjectDetail {
  return {
    id: 'p1',
    name: '夜明けの街',
    code: 'EP01',
    synopsis: 'あらすじ',
    world_notes: '',
    auto_translate: true,
  latent_continuity: false,
    created_at: '2026-01-01T00:00:00+00:00',
    updated_at: '2026-01-01T00:00:00+00:00',
    episodes: [],
    scenes: [],
    assets: [
      {
        id: 'a1',
        project_id: 'p1',
        name: 'アキ',
        category: 'character',
        caption: '主人公',
        prompt_caption: 'a young woman',
        kind: 'image',
        path: '/assets/image/aki.png',
        url: '/assets/image/aki.png',
        locked: true,
        sort_order: 0,
        created_at: '2026-01-01T00:00:00+00:00',
      },
    ],
    shots: [shot('カット1'), shot('カット2')],
    takes: [take('t1')],
    ...overrides,
  }
}

/** 左レール（Shot リスト）の中だけを探す。タブや脚本ビューと名前がぶつかるため。 */
function rail() {
  return within(screen.getByRole('complementary'))
}

function summary(current: StudioProjectDetail): StudioProjectSummary {
  return {
    id: current.id,
    name: current.name,
    code: current.code,
    synopsis: current.synopsis,
    world_notes: current.world_notes,
    auto_translate: current.auto_translate,
    latent_continuity: current.latent_continuity,
    created_at: current.created_at,
    updated_at: current.updated_at,
    shot_count: current.shots.length,
    asset_count: current.assets.length,
    take_count: current.takes.length,
    selected_take_count: current.takes.filter((item) => item.status === 'selected')
      .length,
  }
}

function shotPreview(
  overrides: Partial<StudioShotPreview> = {},
): StudioShotPreview {
  return {
    shot_id: 'カット1',
    workflow: 'minimax_h3_t2v',
    workflow_reason: '開始フレームの引き継ぎも、ファイルのある素材の参照もありません',
    prompt: 'a quiet street\nCamera: slow dolly in\nNo text, subtitles, logos or watermarks.',
    references: [],
    start_frame: null,
    auto_translate: true,
    will_translate: false,
    latent_continuity: false,
    context_video: null,
    context_latent: null,
    error: '',
    ...overrides,
  }
}

/** プロジェクトを 1 つ開いた状態まで進める。 */
async function openProject(current = detail()) {
  mocked.listStudioProjects.mockResolvedValue([summary(current)])
  mocked.getStudioProject.mockResolvedValue(current)
  mocked.previewStudioShotPrompt.mockResolvedValue(shotPreview())
  render(<StudioView progress={{}} />)
  fireEvent.click(await screen.findByText(current.name))
  await screen.findByRole('button', { name: '概要' })
}

describe('StudioView', () => {
  it('プロジェクト一覧を出し、選ぶと 3 ペインに切り替わる', async () => {
    await openProject()
    expect(rail().getByRole('heading', { name: '脚本' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'World Bible' })).toBeTruthy()
    // 概要タブのサマリー（カット 2 / 素材 1 / Take 1）
    expect(screen.getByText('カット')).toBeTruthy()
    expect(screen.getByDisplayValue('夜明けの街')).toBeTruthy()
  })

  it('一覧が空なら作成フォームから作る', async () => {
    mocked.listStudioProjects.mockResolvedValue([])
    mocked.createStudioProject.mockResolvedValue({ id: 'p9', name: '新作' })
    mocked.getStudioProject.mockResolvedValue(detail({ id: 'p9', name: '新作' }))
    render(<StudioView progress={{}} />)
    await screen.findByText('まだプロジェクトがありません。下から作ってください。')

    // 作品名が空のままだとバリデーションで止まる
    fireEvent.click(screen.getByRole('button', { name: '作成' }))
    expect(await screen.findByText('作品名は必須です')).toBeTruthy()
    expect(mocked.createStudioProject).not.toHaveBeenCalled()

    fireEvent.change(screen.getByLabelText('作品名'), { target: { value: '新作' } })
    fireEvent.click(screen.getByRole('button', { name: '作成' }))
    await waitFor(() =>
      expect(mocked.createStudioProject).toHaveBeenCalledWith({
        name: '新作',
        code: '',
        synopsis: '',
      }),
    )
  })

  it('カットの並べ替えは全 id を並べて reorder に送る', async () => {
    await openProject()
    mocked.reorderStudioShots.mockResolvedValue([])
    fireEvent.click(rail().getByRole('button', { name: 'カット1を下へ' }))
    await waitFor(() =>
      expect(mocked.reorderStudioShots).toHaveBeenCalledWith('p1', [
        'カット2',
        'カット1',
      ]),
    )
  })

  it('先頭のカットは「上へ」が押せない', async () => {
    await openProject()
    expect(
      rail().getByRole('button', { name: 'カット1を上へ' }).hasAttribute('disabled'),
    ).toBe(true)
  })

  it('脚本タブでカットを保存すると尺が数値で PATCH される', async () => {
    await openProject()
    fireEvent.click(screen.getByRole('button', { name: '脚本' }))
    // 左レールで選んでからフォームが出る
    fireEvent.click(rail().getByRole('button', { name: 'カット1' }))
    fireEvent.change(await screen.findByLabelText('尺（秒）'), {
      target: { value: '8' },
    })
    mocked.updateStudioShot.mockResolvedValue({})
    fireEvent.click(screen.getByRole('button', { name: '保存' }))
    await waitFor(() =>
      expect(mocked.updateStudioShot).toHaveBeenCalledWith(
        'カット1',
        expect.objectContaining({ duration_seconds: 8 }),
      ),
    )
  })

  it('尺が範囲外なら PATCH を投げずにエラーを出す', async () => {
    await openProject()
    fireEvent.click(screen.getByRole('button', { name: '脚本' }))
    fireEvent.click(rail().getByRole('button', { name: 'カット1' }))
    fireEvent.change(await screen.findByLabelText('尺（秒）'), {
      target: { value: '99' },
    })
    fireEvent.click(screen.getByRole('button', { name: '保存' }))
    expect(await screen.findByText('尺は 1〜15 秒です')).toBeTruthy()
    expect(mocked.updateStudioShot).not.toHaveBeenCalled()
  })

  it('World Bible タブに素材と LOCKED バッジが出る', async () => {
    await openProject()
    fireEvent.click(screen.getByRole('button', { name: 'World Bible' }))
    expect(await screen.findByText('@アキ')).toBeTruthy()
    expect(screen.getByText('🔒 LOCKED')).toBeTruthy()
    // インスペクタは選んでから
    fireEvent.click(screen.getByRole('button', { name: /@アキ/ }))
    expect(await screen.findByDisplayValue('主人公')).toBeTruthy()
    expect(screen.getByDisplayValue('a young woman')).toBeTruthy()
  })

  it('素材にリファレンス（声サンプル）を足せる', async () => {
    await openProject()
    fireEvent.click(screen.getByRole('button', { name: 'World Bible' }))
    fireEvent.click(await screen.findByRole('button', { name: /@アキ/ }))
    mocked.addStudioAssetFile.mockResolvedValue({})

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
  })

  it('素材のメインのファイルを差し替えられる', async () => {
    await openProject()
    fireEvent.click(screen.getByRole('button', { name: 'World Bible' }))
    fireEvent.click(await screen.findByRole('button', { name: /@アキ/ }))
    mocked.uploadStudioAssetFile.mockResolvedValue({})

    fireEvent.change(screen.getByLabelText(/メインのファイル/), {
      target: { files: [new File(['DATA'], 'aki.png', { type: 'image/png' })] },
    })

    await waitFor(() =>
      expect(mocked.uploadStudioAssetFile).toHaveBeenCalledWith(
        'a1',
        expect.any(File),
      ),
    )
  })

  it('素材のロックを外すと locked だけを PATCH する', async () => {
    await openProject()
    fireEvent.click(screen.getByRole('button', { name: 'World Bible' }))
    fireEvent.click(await screen.findByRole('button', { name: /@アキ/ }))
    mocked.updateStudioAsset.mockResolvedValue({})
    fireEvent.click(screen.getByRole('button', { name: '🔓 ロック解除' }))
    await waitFor(() =>
      expect(mocked.updateStudioAsset).toHaveBeenCalledWith('a1', { locked: false }),
    )
  })

  it('制作タブで生成を押すと render に投げる', async () => {
    await openProject()
    fireEvent.click(screen.getByRole('button', { name: '制作' }))
    expect(
      screen.getByText('左のレールでカットを選ぶと、ここで生成できます'),
    ).toBeTruthy()

    fireEvent.click(rail().getByRole('button', { name: 'カット1' }))
    mocked.renderStudioShot.mockResolvedValue({})
    fireEvent.click(await screen.findByRole('button', { name: '生成' }))
    await waitFor(() =>
      expect(mocked.renderStudioShot).toHaveBeenCalledWith('カット1'),
    )
  })

  it('生成中は Take レールに進捗が出て、生成ボタンが止まる', async () => {
    const current = detail({
      takes: [take('t1', { status: 'rendering', job_status: 'running' })],
    })
    await openProject(current)
    cleanup()
    render(
      <StudioView
        progress={{
          'job-t1': { type: 'job', job_id: 'job-t1', status: 'running', progress: 0.4 },
        }}
      />,
    )
    fireEvent.click(await screen.findByText('夜明けの街'))
    fireEvent.click(await screen.findByRole('button', { name: '制作' }))
    fireEvent.click(rail().getByRole('button', { name: 'カット1' }))

    expect(await screen.findByRole('button', { name: '生成中…' })).toBeTruthy()
    const takeRail = screen.getByText('Take（1）').closest('section') as HTMLElement
    expect(within(takeRail).getByText('生成中')).toBeTruthy()
    expect(within(takeRail).getByText('40%')).toBeTruthy()
  })

  it('Take を採用すると select を呼び、プロジェクトを取り直す', async () => {
    await openProject()
    fireEvent.click(screen.getByRole('button', { name: '制作' }))
    fireEvent.click(rail().getByRole('button', { name: 'カット1' }))
    mocked.selectStudioTake.mockResolvedValue({})
    const before = mocked.getStudioProject.mock.calls.length
    fireEvent.click(await screen.findByRole('button', { name: '採用' }))
    await waitFor(() => expect(mocked.selectStudioTake).toHaveBeenCalledWith('t1'))
    await waitFor(() =>
      expect(mocked.getStudioProject.mock.calls.length).toBeGreaterThan(before),
    )
  })

  it('未登録の @メンションを警告する', async () => {
    await openProject(
      detail({ shots: [shot('カット1', { prompt: '@アキ と @ユキ が歩く' })] }),
    )
    fireEvent.click(screen.getByRole('button', { name: '制作' }))
    fireEvent.click(rail().getByRole('button', { name: 'カット1' }))
    expect(
      await screen.findByText('World Bible に無い素材を指しています: @ユキ'),
    ).toBeTruthy()
  })

  it('API が失敗したらバナーに出す', async () => {
    mocked.listStudioProjects.mockRejectedValue(new Error('接続できません'))
    render(<StudioView progress={{}} />)
    expect(await screen.findByText('接続できません')).toBeTruthy()
  })
})

describe('StudioView: プロジェクト一覧', () => {
  it('カット数・素材数・採用済みを出す', async () => {
    mocked.listStudioProjects.mockResolvedValue([
      summary(detail({ takes: [take('t1', { status: 'selected' })] })),
    ])
    render(<StudioView progress={{}} />)
    const row = (await screen.findByText('夜明けの街')).closest('button') as HTMLElement
    expect(within(row).getByText('カット 2')).toBeTruthy()
    expect(within(row).getByText('素材 1')).toBeTruthy()
    expect(within(row).getByText('採用済み 1')).toBeTruthy()
  })

  it('デモプロジェクトを作って、そのまま開く', async () => {
    mocked.listStudioProjects.mockResolvedValue([])
    const demo = detail({ id: 'demo1', name: 'さざなみ食堂' })
    mocked.createStudioDemoProject.mockResolvedValue(demo)
    mocked.getStudioProject.mockResolvedValue(demo)
    render(<StudioView progress={{}} />)

    fireEvent.change(await screen.findByLabelText('デモ作品'), {
      target: { value: 'SAZANAMI-02' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'デモプロジェクトを作成' }))
    await waitFor(() =>
      expect(mocked.createStudioDemoProject).toHaveBeenCalledWith('SAZANAMI-02'),
    )
    expect(await screen.findByRole('button', { name: '概要' })).toBeTruthy()
  })

  it('同じデモが既にあれば（409）エラーを出す', async () => {
    mocked.listStudioProjects.mockResolvedValue([])
    mocked.createStudioDemoProject.mockRejectedValue(
      new ApiError(409, 'そのデモ作品は既にあります'),
    )
    render(<StudioView progress={{}} />)
    fireEvent.click(
      await screen.findByRole('button', { name: 'デモプロジェクトを作成' }),
    )
    expect(await screen.findByText('そのデモ作品は既にあります')).toBeTruthy()
  })
})

/** 話 2 本・場 3 つ・未分類 1 カットのプロジェクト。 */
function structured() {
  return detail({
    episodes: [episode('e1', '第一夜'), episode('e2', '第二夜')],
    scenes: [
      scene('sc1', 'e1', '路地'),
      scene('sc2', 'e1', '屋上'),
      scene('sc3', 'e2', '駅前'),
    ],
    shots: [
      shot('カット1', { scene_id: 'sc1' }),
      shot('カット2', { scene_id: null }),
      shot('カット3', { scene_id: 'sc3' }),
    ],
    takes: [],
  })
}

describe('StudioView: 話と場のツリー', () => {
  it('話 -> 場 -> カットを並べ、余ったカットは未分類に置く', async () => {
    await openProject(structured())
    const tree = rail()
    expect(tree.getByRole('heading', { name: '第一夜' })).toBeTruthy()
    expect(tree.getByRole('heading', { name: /路地/ })).toBeTruthy()
    expect(tree.getByRole('heading', { name: '未分類' })).toBeTruthy()
    // 未分類のグループにいるのは scene_id が無いカットだけ
    const unassigned = tree
      .getByRole('heading', { name: '未分類' })
      .closest('section') as HTMLElement
    expect(within(unassigned).getByRole('button', { name: 'カット2' })).toBeTruthy()
    expect(within(unassigned).queryByRole('button', { name: 'カット1' })).toBeNull()
  })

  it('話を追加すると連番のタイトルで作る', async () => {
    await openProject(structured())
    mocked.createStudioEpisode.mockResolvedValue({})
    fireEvent.click(rail().getByRole('button', { name: '＋ 話' }))
    await waitFor(() =>
      expect(mocked.createStudioEpisode).toHaveBeenCalledWith('p1', {
        title: '第 3 話',
      }),
    )
  })

  it('場を追加するとその話の連番で作る', async () => {
    await openProject(structured())
    mocked.createStudioScene.mockResolvedValue({})
    fireEvent.click(rail().getByRole('button', { name: '第一夜に場を追加' }))
    await waitFor(() =>
      expect(mocked.createStudioScene).toHaveBeenCalledWith('e1', { title: '場 3' }),
    )
  })

  it('場の「＋」はその場に属するカットを作って選ぶ', async () => {
    await openProject(structured())
    mocked.createStudioShot.mockResolvedValue(
      shot('sh9', { scene_id: 'sc1', title: 'カット 4' }),
    )
    fireEvent.click(rail().getByRole('button', { name: '路地にカットを追加' }))
    await waitFor(() =>
      expect(mocked.createStudioShot).toHaveBeenCalledWith('p1', {
        title: 'カット 4',
        scene_id: 'sc1',
      }),
    )
  })

  it('改名は prompt で受けて title だけ PATCH する', async () => {
    await openProject(structured())
    const prompt = vi.spyOn(window, 'prompt').mockReturnValue(' 第一夜（改） ')
    mocked.updateStudioEpisode.mockResolvedValue({})
    fireEvent.click(rail().getByRole('button', { name: '第一夜を改名' }))
    await waitFor(() =>
      expect(mocked.updateStudioEpisode).toHaveBeenCalledWith('e1', {
        title: '第一夜（改）',
      }),
    )
    prompt.mockRestore()
  })

  it('prompt をキャンセルしたら何もしない', async () => {
    await openProject(structured())
    const prompt = vi.spyOn(window, 'prompt').mockReturnValue(null)
    fireEvent.click(rail().getByRole('button', { name: '路地を改名' }))
    await waitFor(() => expect(prompt).toHaveBeenCalled())
    expect(mocked.updateStudioScene).not.toHaveBeenCalled()
    prompt.mockRestore()
  })

  it('場の並べ替えはその話の場だけを並べて送る', async () => {
    await openProject(structured())
    mocked.reorderStudioScenes.mockResolvedValue([])
    fireEvent.click(rail().getByRole('button', { name: '路地を下へ' }))
    await waitFor(() =>
      expect(mocked.reorderStudioScenes).toHaveBeenCalledWith('e1', ['sc2', 'sc1']),
    )
  })

  it('話の並べ替えは全 id を並べて送る', async () => {
    await openProject(structured())
    mocked.reorderStudioEpisodes.mockResolvedValue([])
    fireEvent.click(rail().getByRole('button', { name: '第二夜を上へ' }))
    await waitFor(() =>
      expect(mocked.reorderStudioEpisodes).toHaveBeenCalledWith('p1', ['e2', 'e1']),
    )
  })

  it('話の削除は確認してから消す', async () => {
    await openProject(structured())
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
    mocked.deleteStudioEpisode.mockResolvedValue(undefined)
    fireEvent.click(rail().getByRole('button', { name: '第二夜を削除' }))
    await waitFor(() => expect(mocked.deleteStudioEpisode).toHaveBeenCalledWith('e2'))
    confirm.mockRestore()
  })
})

describe('StudioView: 脚本タブの生成設定', () => {
  it('空欄の生成設定は null を明示して PATCH する', async () => {
    await openProject(structured())
    fireEvent.click(screen.getByRole('button', { name: '脚本' }))
    fireEvent.click(rail().getByRole('button', { name: 'カット2' }))
    mocked.updateStudioShot.mockResolvedValue({})
    fireEvent.click(await screen.findByRole('button', { name: '保存' }))
    await waitFor(() =>
      expect(mocked.updateStudioShot).toHaveBeenCalledWith(
        'カット2',
        expect.objectContaining({
          scene_id: null,
          aspect_ratio: null,
          megapixels: null,
          seed: null,
          workflow_override: null,
        }),
      ),
    )
  })

  it('場の割り当てとワークフロー強制指定を保存する', async () => {
    await openProject(structured())
    fireEvent.click(screen.getByRole('button', { name: '脚本' }))
    fireEvent.click(rail().getByRole('button', { name: 'カット2' }))
    fireEvent.change(await screen.findByLabelText('所属する場'), {
      target: { value: 'sc3' },
    })
    fireEvent.change(screen.getByLabelText('ワークフローの強制指定'), {
      target: { value: 'minimax_h3_r2v' },
    })
    fireEvent.change(screen.getByLabelText('シード'), { target: { value: '42' } })
    mocked.updateStudioShot.mockResolvedValue({})
    fireEvent.click(screen.getByRole('button', { name: '保存' }))
    await waitFor(() =>
      expect(mocked.updateStudioShot).toHaveBeenCalledWith(
        'カット2',
        expect.objectContaining({
          scene_id: 'sc3',
          workflow_override: 'minimax_h3_r2v',
          seed: 42,
        }),
      ),
    )
  })

  it('アスペクト比は候補があればセレクトになる', async () => {
    mocked.listStudioProjects.mockResolvedValue([summary(detail())])
    mocked.getStudioProject.mockResolvedValue(detail())
    render(<StudioView progress={{}} aspectRatios={['16:9 (Widescreen)']} />)
    fireEvent.click(await screen.findByText('夜明けの街'))
    fireEvent.click(await screen.findByRole('button', { name: '脚本' }))
    fireEvent.click(rail().getByRole('button', { name: 'カット1' }))
    const select = await screen.findByLabelText('アスペクト比')
    expect(select.tagName).toBe('SELECT')
    expect(
      within(select).getByRole('option', { name: '16:9 (Widescreen)' }),
    ).toBeTruthy()
  })

  it('シードが整数でなければ PATCH を投げない', async () => {
    await openProject()
    fireEvent.click(screen.getByRole('button', { name: '脚本' }))
    fireEvent.click(rail().getByRole('button', { name: 'カット1' }))
    fireEvent.change(await screen.findByLabelText('シード'), {
      target: { value: '1.5' },
    })
    fireEvent.click(screen.getByRole('button', { name: '保存' }))
    expect(
      await screen.findByText('シードは整数で入れてください（空欄で毎回ランダム）'),
    ).toBeTruthy()
    expect(mocked.updateStudioShot).not.toHaveBeenCalled()
  })
})

describe('StudioView: 概要タブと変更履歴', () => {
  it('自動英訳のトグルを保存する', async () => {
    await openProject()
    mocked.updateStudioProject.mockResolvedValue({})
    fireEvent.click(screen.getByLabelText('日本語プロンプトを自動で英訳して投入'))
    fireEvent.click(screen.getByRole('button', { name: '保存' }))
    await waitFor(() =>
      expect(mocked.updateStudioProject).toHaveBeenCalledWith(
        'p1',
        expect.objectContaining({ auto_translate: false }),
      ),
    )
  })

  it('変更履歴を一覧して、その時点に戻す', async () => {
    await openProject()
    mocked.listStudioRevisions.mockResolvedValue([
      {
        seq: 2,
        actor: 'agent',
        action: 'カットを 1 つ足しました',
        created_at: '2026-08-01T12:34:56+00:00',
      },
      {
        seq: 1,
        actor: 'user',
        action: 'プロジェクトを作りました',
        created_at: '2026-07-31T09:00:00+00:00',
      },
    ])
    fireEvent.click(screen.getByRole('button', { name: '変更履歴' }))

    expect(await screen.findByText('カットを 1 つ足しました')).toBeTruthy()
    expect(screen.getByText('エージェント')).toBeTruthy()
    expect(screen.getByText('ユーザー')).toBeTruthy()
    expect(screen.getByText('2026-08-01 12:34')).toBeTruthy()

    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
    mocked.restoreStudioRevision.mockResolvedValue(detail())
    fireEvent.click(screen.getByRole('button', { name: '#2 の時点に戻す' }))
    await waitFor(() =>
      expect(mocked.restoreStudioRevision).toHaveBeenCalledWith('p1', 2),
    )
    confirm.mockRestore()
  })

  it('確認をキャンセルしたら書き戻さない', async () => {
    await openProject()
    mocked.listStudioRevisions.mockResolvedValue([
      { seq: 1, actor: 'user', action: '作成', created_at: '2026-07-31T09:00:00+00:00' },
    ])
    fireEvent.click(screen.getByRole('button', { name: '変更履歴' }))
    await screen.findByText('作成')
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
    fireEvent.click(screen.getByRole('button', { name: '#1 の時点に戻す' }))
    expect(mocked.restoreStudioRevision).not.toHaveBeenCalled()
    confirm.mockRestore()
  })
})

describe('StudioView: stale と自動英訳の見せ方', () => {
  it('古びた Take に警告バッジと理由を出す', async () => {
    await openProject(
      detail({
        takes: [
          take('t1', {
            stale: true,
            stale_reasons: ['脚本が更新されました', '素材『アキ』が更新されました'],
          }),
        ],
      }),
    )
    fireEvent.click(screen.getByRole('button', { name: '制作' }))
    fireEvent.click(rail().getByRole('button', { name: 'カット1' }))
    expect(await screen.findByText('⚠ 要再生成')).toBeTruthy()
    expect(screen.getByText('⚠ 脚本が更新されました')).toBeTruthy()
    expect(screen.getByText('⚠ 素材『アキ』が更新されました')).toBeTruthy()
  })

  it('実投入プロンプトと英訳前の原文を併記する', async () => {
    await openProject(
      detail({
        takes: [
          take('t1', {
            prompt: 'a woman walks down a quiet street',
            source_prompt: '女がひとけのない通りを歩く',
            warning: '英訳に失敗したので原文のまま投入しました',
          }),
        ],
      }),
    )
    fireEvent.click(screen.getByRole('button', { name: '制作' }))
    fireEvent.click(rail().getByRole('button', { name: 'カット1' }))
    expect(await screen.findByText('a woman walks down a quiet street')).toBeTruthy()
    expect(screen.getByText('英訳する前の原文を見る')).toBeTruthy()
    expect(screen.getByText('女がひとけのない通りを歩く')).toBeTruthy()
    expect(
      screen.getAllByText('英訳に失敗したので原文のまま投入しました').length,
    ).toBeGreaterThan(0)
  })
})

describe('StudioView: メタデータのみの素材', () => {
  const metaOnly = () =>
    detail({
      assets: [
        {
          id: 'a2',
          project_id: 'p1',
          name: '記録端末',
          category: 'prop',
          caption: '古い端末',
          prompt_caption: 'an old terminal',
          kind: 'image',
          path: '',
          url: '',
          locked: false,
          sort_order: 0,
          created_at: '2026-01-01T00:00:00+00:00',
        },
      ],
      shots: [shot('カット1', { prompt: '@記録端末 が光る' })],
      takes: [],
    })

  it('World Bible の素材カードに「ファイルなし」バッジを出す', async () => {
    await openProject(metaOnly())
    fireEvent.click(screen.getByRole('button', { name: 'World Bible' }))
    expect(await screen.findByText('ファイルなし')).toBeTruthy()
  })

  it('ファイルなしで素材を追加できる', async () => {
    await openProject(metaOnly())
    fireEvent.click(screen.getByRole('button', { name: 'World Bible' }))
    fireEvent.change(await screen.findByLabelText('素材名（@ で呼ぶ名前）'), {
      target: { value: '停止した警告灯' },
    })
    fireEvent.change(screen.getByLabelText('キャプション（任意）'), {
      target: { value: '橙のランプ' },
    })
    mocked.createStudioAsset.mockResolvedValue({ id: 'a3' })
    fireEvent.click(screen.getByRole('button', { name: 'ファイルなしで追加' }))
    await waitFor(() =>
      expect(mocked.createStudioAsset).toHaveBeenCalledWith('p1', {
        name: '停止した警告灯',
        category: 'character',
        caption: '橙のランプ',
      }),
    )
  })

})

describe('StudioView: 投入プレビュー', () => {
  it('脚本タブに最終プロンプトとワークフローが出る', async () => {
    await openProject()
    fireEvent.click(screen.getByRole('button', { name: '脚本' }))
    fireEvent.click(rail().getByRole('button', { name: 'カット1' }))

    await waitFor(() =>
      expect(mocked.previewStudioShotPrompt).toHaveBeenCalledWith('カット1'),
    )
    const panel = within(await screen.findByRole('group', { name: '投入プレビュー' }))
    expect(panel.getByText('t2v（文章だけから）')).toBeTruthy()
    const body = await panel.findByLabelText('投入される最終プロンプト')
    expect(body.textContent).toContain('Camera: slow dolly in')
    expect(body.textContent).toContain('No text, subtitles, logos or watermarks.')
  })

  it('参照素材と英訳の注記を出す', async () => {
    await openProject()
    mocked.previewStudioShotPrompt.mockResolvedValue(
      shotPreview({
        workflow: 'minimax_h3_r2v',
        prompt: '<Picture 1> が歩いてくる。',
        references: [
          {
            name: 'アキ',
            kind: 'image',
            tag: '<Picture 1>',
            path: '/assets/image/aki.png',
          },
        ],
        will_translate: true,
      }),
    )
    fireEvent.click(screen.getByRole('button', { name: '脚本' }))
    fireEvent.click(rail().getByRole('button', { name: 'カット1' }))

    const panel = within(await screen.findByRole('group', { name: '投入プレビュー' }))
    await waitFor(() => expect(panel.getByText('r2v（参照素材から）')).toBeTruthy())
    expect(panel.getByText('<Picture 1>')).toBeTruthy()
    expect(panel.getByText(/アキ（image \/ aki\.png）/)).toBeTruthy()
    expect(panel.getByText(/投入時に英語へ自動変換されます/)).toBeTruthy()
  })

  it('組み立てられないカットは理由を出す', async () => {
    await openProject()
    mocked.previewStudioShotPrompt.mockResolvedValue(
      shotPreview({
        workflow: null,
        prompt: '',
        error: '解決できない素材メンションです: @Inu',
      }),
    )
    fireEvent.click(screen.getByRole('button', { name: '脚本' }))
    fireEvent.click(rail().getByRole('button', { name: 'カット1' }))

    expect(
      await screen.findByText('解決できない素材メンションです: @Inu'),
    ).toBeTruthy()
    expect(screen.queryByLabelText('投入される最終プロンプト')).toBeNull()
  })
})
