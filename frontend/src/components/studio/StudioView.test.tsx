import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError, api } from '../../api'
import type {
  ComfyTarget,
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
      translateStudioShotPrompt: vi.fn(),
      renderStudioShot: vi.fn(),
      selectStudioTake: vi.fn(),
      rejectStudioTake: vi.fn(),
      cancelStudioTake: vi.fn(),
      deleteStudioTake: vi.fn(),
    },
  }
})

afterEach(cleanup)

const mocked = api as unknown as Record<string, ReturnType<typeof vi.fn>>

// 概要タブは開いたときに接続先のケーパビリティを聞きに行く（ラテント連続性の
// トグルの出し分け）。既定は「使える」にして、他のテストの邪魔をしない。
beforeEach(() => {
  // 話の絞り込みは作品ごとに localStorage に残るので、テスト間で持ち越さない。
  window.localStorage.clear()
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
    latent_upscale: true,
    quality: 'normal',
    image_quality: 'normal',
    image_megapixels: null,
    image_aspect_ratio: null,
    image_steps: 0,
    megapixels: null,
    aspect_ratio: null,
    steps: 0,
    nsfw: false,
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

/**
 * 中央のタブを切り替える。Radix の Tabs は mousedown で選択が動くので、
 * click ではなくこちらで押す。
 */
function clickTab(name: string) {
  fireEvent.mouseDown(screen.getByRole('tab', { name }))
}

/** タブが出るのを待ってから押す（プロジェクトを開いた直後用）。 */
async function openTab(name: string) {
  fireEvent.mouseDown(await screen.findByRole('tab', { name }))
}

/** 左レール（Shot リスト）の中だけを探す。タブや脚本ビューと名前がぶつかるため。 */
function rail() {
  return within(screen.getByRole('complementary'))
}

/** 脚本タブの左カラム（話・場・番号が左レールと同じ名前で並ぶため）。 */
function script() {
  return within(screen.getByRole('region', { name: '脚本ツリー' }))
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
    latent_upscale: current.latent_upscale,
    quality: current.quality,
    image_quality: current.image_quality,
    image_megapixels: current.image_megapixels,
    image_aspect_ratio: current.image_aspect_ratio,
    image_steps: current.image_steps,
    megapixels: current.megapixels,
    aspect_ratio: current.aspect_ratio,
    steps: current.steps,
    nsfw: current.nsfw,
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
    english_prompt: '',
    english_stale: false,
    english_status: '',
    english_error: '',
    latent_continuity: false,
    quality: 'normal',
    quality_applied: false,
    latent_upscale: true,
    context_video: null,
    context_latent: null,
    error: '',
    ...overrides,
  }
}

/** プロジェクトを 1 つ開いた状態まで進める。 */
async function openProject(
  current = detail(),
  props: {
    aspectRatios?: string[]
    comfyTarget?: ComfyTarget | null
    onComfyTarget?: (target: ComfyTarget) => void
  } = {},
) {
  mocked.listStudioProjects.mockResolvedValue([summary(current)])
  mocked.getStudioProject.mockResolvedValue(current)
  mocked.previewStudioShotPrompt.mockResolvedValue(shotPreview())
  render(<StudioView progress={{}} {...props} />)
  fireEvent.click(await screen.findByText(current.name))
  await screen.findByRole('tab', { name: '概要' })
}

describe('StudioView', () => {
  it('プロジェクト一覧を出し、選ぶと 3 ペインに切り替わる', async () => {
    await openProject()
    expect(rail().getByRole('heading', { name: '脚本' })).toBeTruthy()
    expect(screen.getByRole('tab', { name: 'World Bible' })).toBeTruthy()
    // 概要タブのサマリー（カット 2 / 素材 1 / Take 1）
    expect(screen.getByText('カット')).toBeTruthy()
    expect(screen.getByDisplayValue('夜明けの街')).toBeTruthy()
  })

  it('一覧が空なら作成フォームから作る', async () => {
    mocked.listStudioProjects.mockResolvedValue([])
    mocked.createStudioProject.mockResolvedValue({ id: 'p9', name: '新作' })
    mocked.getStudioProject.mockResolvedValue(detail({ id: 'p9', name: '新作' }))
    render(<StudioView progress={{}} />)
    await screen.findByText(/まだプロジェクトがありません/)
    // 空状態の導線（＋新しいプロジェクト）から作成フォームへ入れる
    fireEvent.click(screen.getByRole('button', { name: '新しいプロジェクト' }))
    expect(document.activeElement).toBe(screen.getByLabelText('作品名'))

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
        // 折りたたみの初期設定は既定値のまま送られる
        world_notes: '',
        auto_translate: true,
        latent_continuity: false,
        nsfw: false,
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
    clickTab('脚本')
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
    clickTab('脚本')
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
    clickTab('World Bible')
    expect(await screen.findByText('@アキ')).toBeTruthy()
    expect(screen.getByText('LOCKED')).toBeTruthy()
    // インスペクタは選んでから
    fireEvent.click(screen.getByRole('button', { name: /@アキ/ }))
    expect(await screen.findByDisplayValue('主人公')).toBeTruthy()
    expect(screen.getByDisplayValue('a young woman')).toBeTruthy()
  })

  it('素材にリファレンス（声サンプル）を足せる', async () => {
    await openProject()
    clickTab('World Bible')
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
    clickTab('World Bible')
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
    clickTab('World Bible')
    fireEvent.click(await screen.findByRole('button', { name: /@アキ/ }))
    mocked.updateStudioAsset.mockResolvedValue({})
    fireEvent.click(screen.getByRole('button', { name: 'ロック解除' }))
    await waitFor(() =>
      expect(mocked.updateStudioAsset).toHaveBeenCalledWith('a1', { locked: false }),
    )
  })

  it('制作タブで生成を押すとダイアログが開き、既定のまま render に投げる', async () => {
    await openProject()
    clickTab('制作')
    // 未選択で行き止まりにならないよう、先頭のカットが選ばれている。
    expect(screen.getByText('カット1 のプロンプト')).toBeTruthy()

    fireEvent.click(rail().getByRole('button', { name: 'カット1' }))
    mocked.renderStudioShot.mockResolvedValue({})
    fireEvent.click(await screen.findByRole('button', { name: '生成' }))

    // 初期値はいまの解決結果（尺 = カット、ステップ数 = 作品の設定 = おまかせ）
    expect(
      (screen.getByLabelText('尺（秒）') as HTMLInputElement).value,
    ).toBe('5')
    expect(
      (screen.getByLabelText('ステップ数') as HTMLInputElement).value,
    ).toBe('')
    // 触らずに押せば従来どおりの投入
    fireEvent.click(screen.getByRole('button', { name: 'この設定で生成' }))
    await waitFor(() =>
      expect(mocked.renderStudioShot).toHaveBeenCalledWith('カット1', {
        duration: 5,
        steps: 0,
      }),
    )
  })

  it('生成ダイアログで変えた値だけがそのテイクに効く', async () => {
    await openProject(
      detail({ megapixels: 1.0, aspect_ratio: '16:9 (Widescreen)', steps: 12 }),
      { aspectRatios: ['16:9 (Widescreen)'] },
    )
    clickTab('制作')
    fireEvent.click(rail().getByRole('button', { name: 'カット1' }))
    mocked.renderStudioShot.mockResolvedValue({})
    fireEvent.click(await screen.findByRole('button', { name: '生成' }))

    // 作品の設定がプレフィルされている
    expect(
      (screen.getByLabelText('解像度（メガピクセル）') as HTMLInputElement).value,
    ).toBe('1')
    expect(
      (screen.getByLabelText('ステップ数') as HTMLInputElement).value,
    ).toBe('12')

    fireEvent.change(screen.getByLabelText('尺（秒）'), {
      target: { value: '8' },
    })
    fireEvent.change(screen.getByLabelText('ステップ数'), {
      target: { value: '30' },
    })
    fireEvent.click(
      screen.getByLabelText('シードを固定する（オフ = ランダム）'),
    )
    fireEvent.change(screen.getByLabelText('シード'), {
      target: { value: '4242' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'この設定で生成' }))

    await waitFor(() =>
      expect(mocked.renderStudioShot).toHaveBeenCalledWith('カット1', {
        duration: 8,
        steps: 30,
        megapixels: 1,
        aspect_ratio: '16:9 (Widescreen)',
        seed: 4242,
      }),
    )
    // プロジェクトもカットも書き換えない
    expect(mocked.updateStudioProject).not.toHaveBeenCalled()
    expect(mocked.updateStudioShot).not.toHaveBeenCalled()
  })

  it('生成ダイアログのラテントアップスケールは作品設定から変えたときだけ送る', async () => {
    await openProject()
    clickTab('制作')
    fireEvent.click(rail().getByRole('button', { name: 'カット1' }))
    mocked.renderStudioShot.mockResolvedValue({})
    fireEvent.click(await screen.findByRole('button', { name: '生成' }))

    // 初期値は作品設定（既定の ON）
    const toggle = screen.getByLabelText(
      'ラテントアップスケール（オフ = 指定解像度で 1 パス）',
    ) as HTMLInputElement
    expect(toggle.getAttribute('data-state')).toBe('checked')

    fireEvent.click(toggle)
    fireEvent.click(screen.getByRole('button', { name: 'この設定で生成' }))
    await waitFor(() =>
      expect(mocked.renderStudioShot).toHaveBeenCalledWith('カット1', {
        duration: 5,
        steps: 0,
        latent_upscale: false,
      }),
    )
  })

  it('作品設定のラテントアップスケールを切ると PATCH で保存される', async () => {
    await openProject()
    mocked.updateStudioProject.mockResolvedValue({})
    // 広い画面のバーではラベルは「拡大」（狭い画面のシートでは全文）
    fireEvent.click(screen.getByLabelText('拡大'))
    await waitFor(() =>
      expect(mocked.updateStudioProject).toHaveBeenCalledWith('p1', {
        latent_upscale: false,
      }),
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
    await openTab('制作')
    fireEvent.click(rail().getByRole('button', { name: 'カット1' }))

    expect(await screen.findByRole('button', { name: '生成中…' })).toBeTruthy()
    const takeRail = screen.getByText('Take（1）').closest('section') as HTMLElement
    expect(within(takeRail).getByText('生成中')).toBeTruthy()
    expect(within(takeRail).getByText('40%')).toBeTruthy()
  })

  it('progress.message が英訳作成中なら制作タブに出す', async () => {
    const current = detail({
      takes: [take('t1', { status: 'rendering', job_status: 'running' })],
    })
    await openProject(current)
    cleanup()
    render(
      <StudioView
        progress={{
          'job-t1': {
            type: 'job',
            job_id: 'job-t1',
            status: 'running',
            progress: 0,
            message: '英訳作成中',
          },
        }}
      />,
    )
    fireEvent.click(await screen.findByText('夜明けの街'))
    await openTab('制作')
    fireEvent.click(rail().getByRole('button', { name: 'カット1' }))

    expect(await screen.findAllByText('英訳作成中')).not.toHaveLength(0)
  })

  it('生成中の Take に停止が出て、押すと cancel する', async () => {
    const current = detail({
      takes: [take('t1', { status: 'rendering', job_status: 'running' })],
    })
    await openProject(current)
    clickTab('制作')
    fireEvent.click(rail().getByRole('button', { name: 'カット1' }))
    mocked.cancelStudioTake.mockResolvedValue({})
    const takeRail = screen.getByText('Take（1）').closest('section') as HTMLElement
    fireEvent.click(within(takeRail).getByRole('button', { name: '停止' }))
    await waitFor(() => expect(mocked.cancelStudioTake).toHaveBeenCalledWith('t1'))
  })

  it('Take を採用すると select を呼び、プロジェクトを取り直す', async () => {
    await openProject()
    clickTab('制作')
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
    clickTab('制作')
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
    expect(await screen.findByRole('tab', { name: '概要' })).toBeTruthy()
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

describe('StudioView: NSFW プロジェクトの出し分け', () => {
  const nsfwProject = () => detail({ id: 'p2', name: '深夜の楽屋', nsfw: true })

  it('NSFW表示がオフなら NSFW プロジェクトは一覧に出ない', async () => {
    mocked.listStudioProjects.mockResolvedValue([
      summary(detail()),
      summary(nsfwProject()),
    ])
    render(<StudioView progress={{}} showNsfw={false} />)
    expect(await screen.findByText('夜明けの街')).toBeTruthy()
    expect(screen.queryByText('深夜の楽屋')).toBeNull()
  })

  it('NSFW表示がオンなら NSFW プロジェクトも一覧に出る', async () => {
    mocked.listStudioProjects.mockResolvedValue([
      summary(detail()),
      summary(nsfwProject()),
    ])
    render(<StudioView progress={{}} showNsfw />)
    expect(await screen.findByText('深夜の楽屋')).toBeTruthy()
  })

  it('開いている NSFW プロジェクトは表示をオフに戻すと一覧へ戻る', async () => {
    const current = nsfwProject()
    mocked.listStudioProjects.mockResolvedValue([summary(current)])
    mocked.getStudioProject.mockResolvedValue(current)
    const view = render(<StudioView progress={{}} showNsfw />)
    fireEvent.click(await screen.findByText(current.name))
    await screen.findByRole('tab', { name: '概要' })

    view.rerender(<StudioView progress={{}} showNsfw={false} />)
    await waitFor(() =>
      expect(screen.queryByRole('tab', { name: '概要' })).toBeNull(),
    )
    expect(screen.queryByText(current.name)).toBeNull()
  })

  it('表示オフのまま概要タブで NSFW を ON にしても画面は閉じない', async () => {
    const current = detail()
    mocked.listStudioProjects.mockResolvedValue([summary(current)])
    mocked.getStudioProject.mockResolvedValue(current)
    render(<StudioView progress={{}} showNsfw={false} />)
    fireEvent.click(await screen.findByText(current.name))
    await screen.findByRole('tab', { name: '概要' })

    fireEvent.click(screen.getByLabelText('NSFW プロジェクト'))
    // 編集中に画面が消えないこと（一覧へ戻ったときに見えなくなる方針）
    expect(screen.getByRole('tab', { name: '概要' })).toBeTruthy()
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

describe('StudioView: カットの既定選択', () => {
  it('開いた時点で 1 話目の最初のカットを選ぶ', async () => {
    await openProject(structured())
    clickTab('制作')
    // 未分類（カット2）ではなく、第一夜 / 路地 の先頭（カット1）
    expect(screen.getByText('カット1 のプロンプト')).toBeTruthy()
  })

  it('カットが 0 件なら 0 件向けの案内を出す', async () => {
    await openProject(detail({ shots: [], takes: [] }))
    clickTab('制作')
    expect(
      screen.getByText('カットがありません。脚本タブでカットを追加してください'),
    ).toBeTruthy()
  })

  it('選んでいたカットを消すと先頭のカットへ戻る', async () => {
    const current = structured()
    await openProject(current)
    fireEvent.click(rail().getByRole('button', { name: 'カット3' }))
    clickTab('制作')
    expect(screen.getByText('カット3 のプロンプト')).toBeTruthy()

    vi.spyOn(window, 'confirm').mockReturnValue(true)
    mocked.deleteStudioShot.mockResolvedValue({})
    mocked.getStudioProject.mockResolvedValue({
      ...current,
      shots: current.shots.filter((item) => item.id !== 'カット3'),
    })
    clickTab('脚本')
    fireEvent.click(screen.getByRole('button', { name: 'このカットを削除' }))
    await waitFor(() => expect(mocked.deleteStudioShot).toHaveBeenCalledWith('カット3'))
    clickTab('制作')
    expect(await screen.findByText('カット1 のプロンプト')).toBeTruthy()
  })
})

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
    fireEvent.click(rail().getByRole('button', { name: '話を追加' }))
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

  it('話のあらすじを prompt で書き換える', async () => {
    await openProject(structured())
    const prompt = vi.spyOn(window, 'prompt').mockReturnValue(' 夜の追跡劇 ')
    mocked.updateStudioEpisode.mockResolvedValue({})
    fireEvent.click(rail().getByRole('button', { name: '第一夜のあらすじを編集' }))
    await waitFor(() =>
      expect(mocked.updateStudioEpisode).toHaveBeenCalledWith('e1', {
        synopsis: '夜の追跡劇',
      }),
    )
    prompt.mockRestore()
  })

  it('場のあらすじと時間帯も prompt で書き換える', async () => {
    await openProject(structured())
    const prompt = vi.spyOn(window, 'prompt').mockReturnValue('夕方')
    mocked.updateStudioScene.mockResolvedValue({})
    fireEvent.click(rail().getByRole('button', { name: '路地の時間帯を編集' }))
    await waitFor(() =>
      expect(mocked.updateStudioScene).toHaveBeenCalledWith('sc1', {
        time_of_day: '夕方',
      }),
    )

    prompt.mockReturnValue('逃げ込む')
    fireEvent.click(rail().getByRole('button', { name: '路地のあらすじを編集' }))
    await waitFor(() =>
      expect(mocked.updateStudioScene).toHaveBeenLastCalledWith('sc1', {
        synopsis: '逃げ込む',
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
    clickTab('脚本')
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
    clickTab('脚本')
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
    await openTab('脚本')
    fireEvent.click(rail().getByRole('button', { name: 'カット1' }))
    const select = await screen.findByLabelText('アスペクト比')
    expect(select.tagName).toBe('SELECT')
    expect(
      within(select).getByRole('option', { name: '16:9 (Widescreen)' }),
    ).toBeTruthy()
  })

  it('シードが整数でなければ PATCH を投げない', async () => {
    await openProject()
    clickTab('脚本')
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

  it('カットに NSFW のチェックボックスは無い（プロジェクト単位に移した）', async () => {
    await openProject()
    clickTab('脚本')
    fireEvent.click(rail().getByRole('button', { name: 'カット1' }))
    await screen.findByLabelText('尺（秒）')
    expect(screen.queryByLabelText(/NSFW/)).toBeNull()
  })
})

describe('StudioView の接続先プルダウン', () => {
  it('生成タブと同じセレクタを出し、選び直すと保存を頼む', async () => {
    const onComfyTarget = vi.fn()
    const current = detail()
    mocked.listStudioProjects.mockResolvedValue([summary(current)])
    mocked.getStudioProject.mockResolvedValue(current)
    mocked.previewStudioShotPrompt.mockResolvedValue(shotPreview())
    render(
      <StudioView
        progress={{}}
        comfyTarget="runpod"
        onComfyTarget={onComfyTarget}
      />,
    )
    // プロジェクト一覧の時点から出す
    const select = (await screen.findByLabelText('接続先')) as HTMLSelectElement
    expect(select.value).toBe('runpod')

    fireEvent.change(select, { target: { value: 'comfy_cloud' } })
    expect(onComfyTarget).toHaveBeenCalledWith('comfy_cloud')

    // プロジェクトを開いてもヘッダーに残る
    fireEvent.click(screen.getByText(current.name))
    await screen.findByRole('tab', { name: '概要' })
    expect(screen.getByLabelText('接続先')).toBeTruthy()
  })

  it('接続先が変わったらケーパビリティを聞き直す', async () => {
    const current = detail()
    mocked.listStudioProjects.mockResolvedValue([summary(current)])
    mocked.getStudioProject.mockResolvedValue(current)
    mocked.previewStudioShotPrompt.mockResolvedValue(shotPreview())
    const view = render(
      <StudioView progress={{}} comfyTarget="local" onComfyTarget={vi.fn()} />,
    )
    fireEvent.click(await screen.findByText(current.name))
    await screen.findByRole('tab', { name: '概要' })
    await waitFor(() => expect(mocked.getStudioCapabilities).toHaveBeenCalledTimes(1))

    view.rerender(
      <StudioView
        progress={{}}
        comfyTarget="comfy_cloud"
        onComfyTarget={vi.fn()}
      />,
    )
    await waitFor(() => expect(mocked.getStudioCapabilities).toHaveBeenCalledTimes(2))
  })
})

describe('StudioView の動画品質セレクタ', () => {
  it('ヘッダーに常時出て、選び直すとプロジェクト設定として保存される', async () => {
    const current = detail()
    await openProject(current)
    const select = screen.getByLabelText('動画品質') as HTMLSelectElement
    expect(select.value).toBe('normal')
    expect(
      Array.from(select.options).map((option) => option.value),
    ).toEqual(['normal', 'opt', 'turbo'])

    mocked.updateStudioProject.mockResolvedValue({})
    fireEvent.change(select, { target: { value: 'turbo' } })
    await waitFor(() =>
      expect(mocked.updateStudioProject).toHaveBeenCalledWith('p1', {
        quality: 'turbo',
      }),
    )
  })

  it('保存済みの品質をそのまま映す', async () => {
    await openProject(detail({ quality: 'opt' }))
    expect((screen.getByLabelText('動画品質') as HTMLSelectElement).value).toBe('opt')
  })

  it('ラテント連続性が ON でも品質はそのまま効く（注記を出さない）', async () => {
    await openProject(detail({ latent_continuity: true, quality: 'turbo' }))
    expect(screen.queryByText(/連続性が有効なため/)).toBeNull()
    expect((screen.getByLabelText('動画品質') as HTMLSelectElement).value).toBe('turbo')
  })
})

describe('StudioView の画像品質セレクタ', () => {
  it('動画品質の隣に出て、選び直すとプロジェクト設定として保存される', async () => {
    await openProject(detail())
    const select = screen.getByLabelText('画像品質') as HTMLSelectElement
    expect(select.value).toBe('normal')
    expect(Array.from(select.options).map((option) => option.value)).toEqual([
      'normal',
      'opt',
      'turbo',
    ])

    mocked.updateStudioProject.mockResolvedValue({})
    fireEvent.change(select, { target: { value: 'opt' } })
    await waitFor(() =>
      expect(mocked.updateStudioProject).toHaveBeenCalledWith('p1', {
        image_quality: 'opt',
      }),
    )
  })

  it('保存済みの画像品質をそのまま映し、動画品質とは独立している', async () => {
    await openProject(detail({ quality: 'turbo', image_quality: 'normal' }))
    expect((screen.getByLabelText('動画品質') as HTMLSelectElement).value).toBe(
      'turbo',
    )
    expect((screen.getByLabelText('画像品質') as HTMLSelectElement).value).toBe(
      'normal',
    )
  })
})

describe('StudioView の動画の画質設定（アスペクト比 / メガピクセル / steps）', () => {
  const ratios = ['4:3 (Standard)', '16:9 (Widescreen)']

  it('アスペクト比は生成フォームと同じ候補で、選ぶとプロジェクトへ保存される', async () => {
    await openProject(detail(), { aspectRatios: ratios })
    const select = screen.getByLabelText('動画 比率') as HTMLSelectElement
    expect(select.value).toBe('')
    expect(Array.from(select.options).map((option) => option.value)).toEqual([
      '',
      ...ratios,
    ])

    mocked.updateStudioProject.mockResolvedValue({})
    fireEvent.change(select, { target: { value: '16:9 (Widescreen)' } })
    await waitFor(() =>
      expect(mocked.updateStudioProject).toHaveBeenCalledWith('p1', {
        aspect_ratio: '16:9 (Widescreen)',
      }),
    )
  })

  it('アスペクト比を「既定のまま」に戻すと null を送る', async () => {
    await openProject(detail({ aspect_ratio: '16:9 (Widescreen)' }), {
      aspectRatios: ratios,
    })
    expect((screen.getByLabelText('動画 比率') as HTMLSelectElement).value).toBe(
      '16:9 (Widescreen)',
    )

    mocked.updateStudioProject.mockResolvedValue({})
    fireEvent.change(screen.getByLabelText('動画 比率'), { target: { value: '' } })
    await waitFor(() =>
      expect(mocked.updateStudioProject).toHaveBeenCalledWith('p1', {
        aspect_ratio: null,
      }),
    )
  })

  it('メガピクセルは確定（フォーカスを外す）まで保存しない', async () => {
    await openProject()
    const input = screen.getByLabelText('動画 MP') as HTMLInputElement
    expect(input.value).toBe('')

    mocked.updateStudioProject.mockResolvedValue({})
    fireEvent.change(input, { target: { value: '1' } })
    expect(mocked.updateStudioProject).not.toHaveBeenCalled()

    fireEvent.blur(input)
    await waitFor(() =>
      expect(mocked.updateStudioProject).toHaveBeenCalledWith('p1', {
        megapixels: 1,
      }),
    )
  })

  it('保存済みのメガピクセルをそのまま映し、空欄にすると既定へ戻す', async () => {
    await openProject(detail({ megapixels: 0.7 }))
    const input = screen.getByLabelText('動画 MP') as HTMLInputElement
    expect(input.value).toBe('0.7')

    mocked.updateStudioProject.mockResolvedValue({})
    fireEvent.change(input, { target: { value: '' } })
    fireEvent.blur(input)
    await waitFor(() =>
      expect(mocked.updateStudioProject).toHaveBeenCalledWith('p1', {
        megapixels: null,
      }),
    )
  })

  it('0 以下のメガピクセルは捨てて保存済みの値に戻す', async () => {
    await openProject(detail({ megapixels: 0.7 }))
    const input = screen.getByLabelText('動画 MP') as HTMLInputElement
    fireEvent.change(input, { target: { value: '0' } })
    fireEvent.blur(input)
    expect(mocked.updateStudioProject).not.toHaveBeenCalled()
    expect(input.value).toBe('0.7')
  })

  it('ステップ数は確定まで保存せず、空欄は 0（おまかせ）として送る', async () => {
    await openProject(detail({ steps: 20 }))
    const input = screen.getByLabelText('動画 steps') as HTMLInputElement
    expect(input.value).toBe('20')

    mocked.updateStudioProject.mockResolvedValue({})
    fireEvent.change(input, { target: { value: '' } })
    expect(mocked.updateStudioProject).not.toHaveBeenCalled()

    fireEvent.blur(input)
    await waitFor(() =>
      expect(mocked.updateStudioProject).toHaveBeenCalledWith('p1', { steps: 0 }),
    )
  })

  it('未設定のステップ数は空欄で見せ、入れた値を保存する', async () => {
    await openProject()
    const input = screen.getByLabelText('動画 steps') as HTMLInputElement
    expect(input.value).toBe('')

    mocked.updateStudioProject.mockResolvedValue({})
    fireEvent.change(input, { target: { value: '30' } })
    fireEvent.blur(input)
    await waitFor(() =>
      expect(mocked.updateStudioProject).toHaveBeenCalledWith('p1', { steps: 30 }),
    )
  })

  it('範囲外のステップ数は捨てて保存済みの値に戻す', async () => {
    await openProject(detail({ steps: 20 }))
    const input = screen.getByLabelText('動画 steps') as HTMLInputElement
    for (const value of ['-1', '151', '1.5']) {
      fireEvent.change(input, { target: { value } })
      fireEvent.blur(input)
      expect(mocked.updateStudioProject).not.toHaveBeenCalled()
      expect(input.value).toBe('20')
    }
  })
})

describe('StudioView の素材画像の画質設定（image_* の 3 項目）', () => {
  const ratios = ['4:3 (Standard)', '16:9 (Widescreen)']

  it('比率は動画側と同じ候補で、選ぶと image_aspect_ratio として保存される', async () => {
    await openProject(detail(), { aspectRatios: ratios })
    const select = screen.getByLabelText('画像 比率') as HTMLSelectElement
    expect(select.value).toBe('')
    expect(Array.from(select.options).map((option) => option.value)).toEqual([
      '',
      ...ratios,
    ])

    mocked.updateStudioProject.mockResolvedValue({})
    fireEvent.change(select, { target: { value: '16:9 (Widescreen)' } })
    await waitFor(() =>
      expect(mocked.updateStudioProject).toHaveBeenCalledWith('p1', {
        image_aspect_ratio: '16:9 (Widescreen)',
      }),
    )
  })

  it('比率を「既定のまま」に戻すと null を送る', async () => {
    await openProject(detail({ image_aspect_ratio: '16:9 (Widescreen)' }), {
      aspectRatios: ratios,
    })
    mocked.updateStudioProject.mockResolvedValue({})
    fireEvent.change(screen.getByLabelText('画像 比率'), {
      target: { value: '' },
    })
    await waitFor(() =>
      expect(mocked.updateStudioProject).toHaveBeenCalledWith('p1', {
        image_aspect_ratio: null,
      }),
    )
  })

  it('MP は確定まで保存せず、空欄にすると既定へ戻す', async () => {
    await openProject(detail({ image_megapixels: 0.7 }))
    const input = screen.getByLabelText('画像 MP') as HTMLInputElement
    expect(input.value).toBe('0.7')

    mocked.updateStudioProject.mockResolvedValue({})
    fireEvent.change(input, { target: { value: '1.2' } })
    expect(mocked.updateStudioProject).not.toHaveBeenCalled()
    fireEvent.blur(input)
    await waitFor(() =>
      expect(mocked.updateStudioProject).toHaveBeenCalledWith('p1', {
        image_megapixels: 1.2,
      }),
    )

    mocked.updateStudioProject.mockClear()
    fireEvent.change(input, { target: { value: '' } })
    fireEvent.blur(input)
    await waitFor(() =>
      expect(mocked.updateStudioProject).toHaveBeenCalledWith('p1', {
        image_megapixels: null,
      }),
    )
  })

  it('0 以下の MP は捨てて保存済みの値に戻す', async () => {
    await openProject(detail({ image_megapixels: 0.7 }))
    const input = screen.getByLabelText('画像 MP') as HTMLInputElement
    fireEvent.change(input, { target: { value: '0' } })
    fireEvent.blur(input)
    expect(mocked.updateStudioProject).not.toHaveBeenCalled()
    expect(input.value).toBe('0.7')
  })

  it('steps は空欄を 0（おまかせ）として送り、範囲外は捨てる', async () => {
    await openProject(detail({ image_steps: 20 }))
    const input = screen.getByLabelText('画像 steps') as HTMLInputElement
    expect(input.value).toBe('20')

    for (const value of ['-1', '151', '1.5']) {
      fireEvent.change(input, { target: { value } })
      fireEvent.blur(input)
      expect(mocked.updateStudioProject).not.toHaveBeenCalled()
      expect(input.value).toBe('20')
    }

    mocked.updateStudioProject.mockResolvedValue({})
    fireEvent.change(input, { target: { value: '' } })
    fireEvent.blur(input)
    await waitFor(() =>
      expect(mocked.updateStudioProject).toHaveBeenCalledWith('p1', {
        image_steps: 0,
      }),
    )
  })

  it('動画側の設定とは独立に持つ', async () => {
    await openProject(
      detail({
        aspect_ratio: '16:9 (Widescreen)',
        megapixels: 0.4,
        steps: 4,
        image_aspect_ratio: null,
        image_megapixels: null,
        image_steps: 0,
      }),
      { aspectRatios: ratios },
    )
    expect((screen.getByLabelText('動画 MP') as HTMLInputElement).value).toBe('0.4')
    expect((screen.getByLabelText('動画 steps') as HTMLInputElement).value).toBe('4')
    expect((screen.getByLabelText('画像 MP') as HTMLInputElement).value).toBe('')
    expect((screen.getByLabelText('画像 steps') as HTMLInputElement).value).toBe('')
    expect(
      (screen.getByLabelText('画像 比率') as HTMLSelectElement).value,
    ).toBe('')
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

  it('NSFW プロジェクトのトグルを保存する', async () => {
    await openProject()
    mocked.updateStudioProject.mockResolvedValue({})
    fireEvent.click(screen.getByLabelText('NSFW プロジェクト'))
    fireEvent.click(screen.getByRole('button', { name: '保存' }))
    await waitFor(() =>
      expect(mocked.updateStudioProject).toHaveBeenCalledWith(
        'p1',
        expect.objectContaining({ nsfw: true }),
      ),
    )
  })

  it('NSFW プロジェクトの説明は ON / OFF で入れ替わる', async () => {
    await openProject()
    expect(
      screen.getByText(
        'このプロジェクトから投入するジョブは非 NSFW で固定されます（自動判定は走りません）。',
      ),
    ).toBeTruthy()
    fireEvent.click(screen.getByLabelText('NSFW プロジェクト'))
    expect(
      screen.getByText(
        'このプロジェクトから投入するジョブはすべて NSFW 扱いになります。',
      ),
    ).toBeTruthy()
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
    clickTab('制作')
    fireEvent.click(rail().getByRole('button', { name: 'カット1' }))
    expect(await screen.findByText('要再生成')).toBeTruthy()
    expect(screen.getByText('脚本が更新されました')).toBeTruthy()
    expect(screen.getByText('素材『アキ』が更新されました')).toBeTruthy()
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
    clickTab('制作')
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
    clickTab('World Bible')
    expect(await screen.findByText('ファイルなし')).toBeTruthy()
  })

  it('ファイルなしで素材を追加できる', async () => {
    await openProject(metaOnly())
    clickTab('World Bible')
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
    clickTab('脚本')
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
    clickTab('脚本')
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
    clickTab('脚本')
    fireEvent.click(rail().getByRole('button', { name: 'カット1' }))

    expect(
      await screen.findByText('解決できない素材メンションです: @Inu'),
    ).toBeTruthy()
    expect(screen.queryByLabelText('投入される最終プロンプト')).toBeNull()
  })

  it('英訳するを押すと選んでいるカットを訳し、プレビューを取り直す', async () => {
    await openProject()
    clickTab('脚本')
    fireEvent.click(rail().getByRole('button', { name: 'カット1' }))
    const panel = within(await screen.findByRole('group', { name: '投入プレビュー' }))
    await waitFor(() =>
      expect(mocked.previewStudioShotPrompt).toHaveBeenCalledWith('カット1'),
    )

    mocked.translateStudioShotPrompt.mockResolvedValue(shot('カット1'))
    mocked.previewStudioShotPrompt.mockResolvedValue(
      shotPreview({
        english_prompt: 'A quiet street in English.',
        english_stale: false,
        will_translate: false,
      }),
    )
    fireEvent.click(panel.getByRole('button', { name: '英訳する' }))
    await waitFor(() =>
      expect(mocked.translateStudioShotPrompt).toHaveBeenCalledWith('カット1'),
    )
    await waitFor(() =>
      expect(mocked.previewStudioShotPrompt.mock.calls.length).toBeGreaterThan(1),
    )
  })

  it('preview が英訳中ならボタンを止める', async () => {
    await openProject()
    mocked.previewStudioShotPrompt.mockResolvedValue(
      shotPreview({ english_status: 'translating' }),
    )
    clickTab('脚本')
    fireEvent.click(rail().getByRole('button', { name: 'カット1' }))

    const panel = within(await screen.findByRole('group', { name: '投入プレビュー' }))
    const button = await panel.findByRole('button', { name: '英訳中…' })
    expect(button).toHaveProperty('disabled', true)
  })

  it('英訳失敗なら Banner に理由を出す', async () => {
    await openProject()
    mocked.previewStudioShotPrompt.mockResolvedValue(
      shotPreview({
        english_status: 'failed',
        english_error: '英語プロンプトへの変換ができないので保存しませんでした（grok CLI が見つかりません）',
      }),
    )
    clickTab('脚本')
    fireEvent.click(rail().getByRole('button', { name: 'カット1' }))

    expect(
      await screen.findByText(
        '英語プロンプトへの変換ができないので保存しませんでした（grok CLI が見つかりません）',
      ),
    ).toBeTruthy()
  })

  it('使える英語キャッシュがあれば投入時変換の注記を出さない', async () => {
    await openProject()
    mocked.previewStudioShotPrompt.mockResolvedValue(
      shotPreview({
        english_prompt: 'A quiet street in English.',
        english_stale: false,
        will_translate: false,
      }),
    )
    clickTab('脚本')
    fireEvent.click(rail().getByRole('button', { name: 'カット1' }))

    const panel = within(await screen.findByRole('group', { name: '投入プレビュー' }))
    expect(await panel.findByText(/この英語を投入します/)).toBeTruthy()
    expect(panel.queryByText(/投入時に英語へ自動変換されます/)).toBeNull()
  })

  it('古い英語キャッシュには使いませんという注記を出す', async () => {
    await openProject()
    mocked.previewStudioShotPrompt.mockResolvedValue(
      shotPreview({
        english_prompt: 'old English.',
        english_stale: true,
        will_translate: true,
      }),
    )
    clickTab('脚本')
    fireEvent.click(rail().getByRole('button', { name: 'カット1' }))

    const panel = within(await screen.findByRole('group', { name: '投入プレビュー' }))
    expect(await panel.findByText(/この英語は使いません/)).toBeTruthy()
  })

  it('組み立てに失敗しても保存済みの英語は消せる', async () => {
    await openProject()
    mocked.previewStudioShotPrompt.mockResolvedValue(
      shotPreview({
        workflow: null,
        prompt: '',
        error: '解決できない素材メンションです: @Inu',
        english_prompt: 'A quiet street in English.',
        english_stale: true,
        will_translate: false,
      }),
    )
    mocked.updateStudioShot.mockResolvedValue(shot('カット1'))
    clickTab('脚本')
    fireEvent.click(rail().getByRole('button', { name: 'カット1' }))

    const panel = within(await screen.findByRole('group', { name: '投入プレビュー' }))
    expect(await panel.findByText(/この英語は使いません/)).toBeTruthy()
    fireEvent.click(panel.getByRole('button', { name: '英語を消す' }))
    await waitFor(() =>
      expect(mocked.updateStudioShot).toHaveBeenCalledWith('カット1', {
        english_prompt: '',
      }),
    )
  })

  it('英語を消すと english_prompt を空で PATCH する', async () => {
    await openProject()
    mocked.previewStudioShotPrompt.mockResolvedValue(
      shotPreview({
        english_prompt: 'A quiet street in English.',
        english_stale: false,
        will_translate: false,
      }),
    )
    mocked.updateStudioShot.mockResolvedValue(shot('カット1'))
    clickTab('脚本')
    fireEvent.click(rail().getByRole('button', { name: 'カット1' }))

    const panel = within(await screen.findByRole('group', { name: '投入プレビュー' }))
    fireEvent.click(await panel.findByRole('button', { name: '英語を消す' }))
    await waitFor(() =>
      expect(mocked.updateStudioShot).toHaveBeenCalledWith('カット1', {
        english_prompt: '',
      }),
    )
  })
})

describe('StudioView の狭い画面ヘッダー', () => {
  const originalMatchMedia = window.matchMedia

  beforeEach(() => {
    window.matchMedia = ((query: string) =>
      ({
        matches: query === '(min-width: 1024px)' ? false : true,
        media: query,
        onchange: null,
        addListener: () => {},
        removeListener: () => {},
        addEventListener: () => {},
        removeEventListener: () => {},
        dispatchEvent: () => false,
      })) as typeof window.matchMedia
  })

  afterEach(() => {
    window.matchMedia = originalMatchMedia
  })

  it('プロジェクトを開くと要約チップだけで、品質などはシートを開くまで無い', async () => {
    await openProject(
      detail({
        quality: 'turbo',
        aspect_ratio: '16:9 (Widescreen)',
        megapixels: 1,
        steps: 20,
      }),
    )
    const chip = await screen.findByRole('button', { name: /生成設定/ })
    expect(chip.textContent).toContain('Turbo')
    expect(chip.textContent).toContain('16:9')
    expect(chip.textContent).toContain('1MP')
    expect(chip.textContent).toContain('20step')
    expect(screen.queryByLabelText('動画品質')).toBeNull()
    expect(screen.queryByLabelText('動画 比率')).toBeNull()
    expect(screen.queryByLabelText('動画 MP')).toBeNull()
    expect(screen.queryByLabelText('動画 steps')).toBeNull()
    expect(screen.queryByLabelText('画像 比率')).toBeNull()
    expect(screen.queryByLabelText('画像 MP')).toBeNull()
    expect(screen.queryByLabelText('画像 steps')).toBeNull()
  })

  it('チップを押すとシートが開き、品質の変更がプロジェクトへ保存される', async () => {
    await openProject(detail({ quality: 'normal' }))
    fireEvent.click(await screen.findByRole('button', { name: /生成設定/ }))
    const select = await screen.findByLabelText('動画品質') as HTMLSelectElement
    expect(select.value).toBe('normal')

    mocked.updateStudioProject.mockResolvedValue({})
    fireEvent.change(select, { target: { value: 'turbo' } })
    await waitFor(() =>
      expect(mocked.updateStudioProject).toHaveBeenCalledWith('p1', {
        quality: 'turbo',
      }),
    )
  })

  it('接続先を渡しているとシート内のセレクトを操作できる', async () => {
    const onComfyTarget = vi.fn()
    await openProject(detail(), {
      comfyTarget: 'runpod',
      onComfyTarget,
    })
    const chip = await screen.findByRole('button', { name: /生成設定/ })
    expect(chip.textContent).toContain('RunPod')
    expect(screen.queryByLabelText('接続先')).toBeNull()

    fireEvent.click(chip)
    const select = (await screen.findByLabelText('接続先')) as HTMLSelectElement
    expect(select.value).toBe('runpod')
    fireEvent.change(select, { target: { value: 'local' } })
    expect(onComfyTarget).toHaveBeenCalledWith('local')
  })
})

/** 場を 2 つ持ち、それぞれに 2 カット入っている作品（場内の並べ替え用）。 */
function twoScenes() {
  return detail({
    episodes: [episode('e1', '第一夜'), episode('e2', '第二夜')],
    scenes: [scene('sc1', 'e1', '路地'), scene('sc2', 'e1', '屋上')],
    // サーバーは 話 -> 場 -> カットの階層順で返す
    shots: [
      shot('カットA', { scene_id: 'sc1' }),
      shot('カットB', { scene_id: 'sc1' }),
      shot('カットC', { scene_id: 'sc2' }),
      shot('カットD', { scene_id: 'sc2' }),
    ],
    takes: [],
  })
}

describe('StudioView: 場の中でのカットの並べ替え', () => {
  it('reorder にはその場の Shot 全件だけを送る', async () => {
    await openProject(twoScenes())
    mocked.reorderStudioShots.mockResolvedValue([])
    fireEvent.click(rail().getByRole('button', { name: 'カットCを下へ' }))
    await waitFor(() =>
      expect(mocked.reorderStudioShots).toHaveBeenCalledWith('p1', [
        'カットD',
        'カットC',
      ]),
    )
  })

  it('端の判定は場の中で行う（作品全体の位置では見ない）', async () => {
    await openProject(twoScenes())
    // カットB は作品全体では 2 番目だが、路地の中では末尾
    expect(
      rail().getByRole('button', { name: 'カットBを下へ' }).hasAttribute('disabled'),
    ).toBe(true)
    // カットC は作品全体では 3 番目だが、屋上の中では先頭
    expect(
      rail().getByRole('button', { name: 'カットCを上へ' }).hasAttribute('disabled'),
    ).toBe(true)
    expect(
      rail().getByRole('button', { name: 'カットCを下へ' }).hasAttribute('disabled'),
    ).toBe(false)
  })
})

describe('StudioView: 話の絞り込み', () => {
  it('話タブを押すと episode_id 付きで取り直す', async () => {
    await openProject(structured())
    clickTab('脚本')
    fireEvent.click(await screen.findByRole('tab', { name: '第二夜' }))
    await waitFor(() =>
      expect(mocked.getStudioProject).toHaveBeenLastCalledWith('p1', 'e2'),
    )

    fireEvent.click(screen.getByRole('tab', { name: 'すべて' }))
    await waitFor(() =>
      expect(mocked.getStudioProject).toHaveBeenLastCalledWith('p1', null),
    )
  })

  it('選んだ話は作品ごとに覚えていて、開き直すとそこから始まる', async () => {
    await openProject(structured())
    clickTab('脚本')
    fireEvent.click(await screen.findByRole('tab', { name: '第二夜' }))
    await waitFor(() =>
      expect(mocked.getStudioProject).toHaveBeenLastCalledWith('p1', 'e2'),
    )

    cleanup()
    mocked.getStudioProject.mockClear()
    await openProject(structured())
    await waitFor(() =>
      expect(mocked.getStudioProject).toHaveBeenLastCalledWith('p1', 'e2'),
    )
  })

  it('操作のあとの取り直しも選んでいる話を保つ', async () => {
    await openProject(structured())
    clickTab('脚本')
    fireEvent.click(await screen.findByRole('tab', { name: '第一夜' }))
    await waitFor(() =>
      expect(mocked.getStudioProject).toHaveBeenLastCalledWith('p1', 'e1'),
    )

    mocked.updateStudioShot.mockResolvedValue({})
    mocked.getStudioProject.mockClear()
    fireEvent.click(await screen.findByRole('button', { name: '保存' }))
    await waitFor(() =>
      expect(mocked.getStudioProject).toHaveBeenLastCalledWith('p1', 'e1'),
    )
  })

  it('覚えていた話が消えていたら、エラーを出さずに作品まるごとへ戻す', async () => {
    window.localStorage.setItem('studio-episode-filter:p1', 'gone')
    const current = structured()
    mocked.listStudioProjects.mockResolvedValue([summary(current)])
    mocked.getStudioProject.mockImplementation((_id: string, episodeId: string | null) =>
      episodeId
        ? Promise.reject(new ApiError(404, 'episode not found'))
        : Promise.resolve(current),
    )
    render(<StudioView progress={{}} />)
    fireEvent.click(await screen.findByText(current.name))
    await screen.findByRole('tab', { name: '概要' })
    await waitFor(() =>
      expect(mocked.getStudioProject).toHaveBeenLastCalledWith('p1', null),
    )
    expect(screen.queryByText('episode not found')).toBeNull()
  })

  it('概要と World Bible では話タブを出さない', async () => {
    await openProject(structured())
    expect(screen.queryByRole('tab', { name: 'すべて' })).toBeNull()
    clickTab('World Bible')
    expect(screen.queryByRole('tab', { name: 'すべて' })).toBeNull()
    clickTab('制作')
    expect(screen.getByRole('tab', { name: 'すべて' })).toBeTruthy()
  })

  it('話を選んでいるあいだは左レールの「未分類」を隠す', async () => {
    await openProject(structured())
    clickTab('脚本')
    expect(rail().getByRole('heading', { name: '未分類' })).toBeTruthy()
    fireEvent.click(screen.getByRole('tab', { name: '第一夜' }))
    await waitFor(() =>
      expect(mocked.getStudioProject).toHaveBeenLastCalledWith('p1', 'e1'),
    )
    expect(rail().queryByRole('heading', { name: '未分類' })).toBeNull()
    // 選んでいない話（第二夜）もレールから消える
    expect(rail().queryByRole('heading', { name: '第二夜' })).toBeNull()
  })

  it('話を選んでいるあいだは、未分類に入る「カットを追加」を押させない', async () => {
    await openProject(structured())
    clickTab('脚本')
    expect(
      rail().getByRole('button', { name: 'カットを追加' }).hasAttribute('disabled'),
    ).toBe(false)
    fireEvent.click(screen.getByRole('tab', { name: '第一夜' }))
    await waitFor(() =>
      expect(mocked.getStudioProject).toHaveBeenLastCalledWith('p1', 'e1'),
    )
    expect(
      rail().getByRole('button', { name: 'カットを追加' }).hasAttribute('disabled'),
    ).toBe(true)
    // 場の「＋」からは足せる
    expect(
      rail().getByRole('button', { name: '路地にカットを追加' }).hasAttribute('disabled'),
    ).toBe(false)
  })

  it('話が 1 つも無ければ話タブは出さない', async () => {
    await openProject()
    clickTab('脚本')
    expect(screen.queryByRole('tab', { name: 'すべて' })).toBeNull()
  })
})

describe('StudioView: 脚本タブのツリーと検索', () => {
  it('話と場の見出しを付けて、カット番号は場の中で振る', async () => {
    await openProject(twoScenes())
    clickTab('脚本')
    // 路地（カットA・カットB）と 屋上（カットC・カットD）が、それぞれ #1 #2
    expect(script().getAllByText('#1').length).toBe(2)
    expect(script().getAllByText('#2').length).toBe(2)
    expect(script().getByRole('heading', { name: '第一夜' })).toBeTruthy()
    expect(script().getByRole('heading', { name: '路地' })).toBeTruthy()
    expect(script().getByRole('heading', { name: '屋上' })).toBeTruthy()
  })

  it('話の見出しを押すと畳める', async () => {
    await openProject(twoScenes())
    clickTab('脚本')
    fireEvent.click(script().getByRole('button', { name: /第一夜/, expanded: true }))
    expect(script().queryByText('#1')).toBeNull()
    fireEvent.click(script().getByRole('button', { name: /第一夜/, expanded: false }))
    expect(script().getAllByText('#1').length).toBe(2)
  })

  it('検索で当たったカットだけ残し、空にすると戻る', async () => {
    const current = twoScenes()
    current.shots[0] = { ...current.shots[0], dialogue: '雨が降ってきた' }
    await openProject(current)
    clickTab('脚本')
    const box = screen.getByLabelText('カットを検索')

    fireEvent.change(box, { target: { value: '雨が降って' } })
    expect(script().getByText('1 / 4 カット')).toBeTruthy()
    expect(script().getAllByText('#1').length).toBe(1)
    // 当たった場（路地）の見出しは残り、当たらなかった場（屋上）は落ちる
    expect(script().getByRole('heading', { name: '路地' })).toBeTruthy()
    expect(script().queryByRole('heading', { name: '屋上' })).toBeNull()

    fireEvent.change(box, { target: { value: 'どこにも無い語' } })
    expect(
      script().getByText('「どこにも無い語」に当たるカットはありません'),
    ).toBeTruthy()

    fireEvent.change(box, { target: { value: '' } })
    expect(script().getAllByText('#1').length).toBe(2)
  })
})

describe('StudioView: 制作タブのタイムライン', () => {
  /** 下段のタイムラインの中だけを探す（左レールと名前がぶつかるため）。 */
  const timeline = () =>
    within(screen.getByRole('group', { name: 'タイムライン' }))

  it('「すべて」では話の区切りを差し、番号は場の中で振る', async () => {
    await openProject(structured())
    clickTab('制作')
    expect(timeline().getByText('第一夜')).toBeTruthy()
    expect(timeline().getByText('第二夜')).toBeTruthy()
    // 場に属さないカットは末尾に「未分類」として固まる
    expect(timeline().getByText('未分類')).toBeTruthy()
    // 場ごとに 1 から数える（第一夜 / 路地 の 1 本目と 第二夜 / 駅前 の 1 本目）
    expect(timeline().getAllByText('#1 カット1').length).toBe(1)
    expect(timeline().getAllByText('#1 カット3').length).toBe(1)
  })

  it('話を選んでいるあいだは区切りを出さない', async () => {
    await openProject(structured())
    clickTab('制作')
    fireEvent.click(screen.getByRole('tab', { name: '第一夜' }))
    await waitFor(() =>
      expect(mocked.getStudioProject).toHaveBeenLastCalledWith('p1', 'e1'),
    )
    expect(timeline().queryByText('第一夜')).toBeNull()
    expect(timeline().queryByText('未分類')).toBeNull()
  })
})

describe('StudioView: 追い越した取り直しの後始末', () => {
  /** 好きな順番で解決できる `getStudioProject` のモック。 */
  function deferred() {
    const waiting = new Map<string, (value: StudioProjectDetail) => void>()
    const answers = new Map<string, StudioProjectDetail>()
    mocked.getStudioProject.mockImplementation(
      (id: string, episodeId: string | null = null) =>
        new Promise<StudioProjectDetail>((resolve) =>
          waiting.set(`${id}/${episodeId ?? 'all'}`, resolve),
        ),
    )
    return {
      /** 応答を用意する（`resolve` で好きなタイミングに返せる）。 */
      answer(key: string, value: StudioProjectDetail) {
        answers.set(key, value)
      },
      /** 用意した応答を返す（キューに積まれるまで待つ）。 */
      async resolve(key: string) {
        await waitFor(() => expect(waiting.has(key)).toBe(true))
        waiting.get(key)!(answers.get(key)!)
      },
    }
  }

  it('話を続けて押しても、遅れて届いた古い話の detail は捨てる', async () => {
    const all = structured()
    const only = (episodeId: string, title: string, sceneId: string) =>
      detail({
        episodes: all.episodes,
        scenes: all.scenes.filter((item) => item.episode_id === episodeId),
        shots: [shot(title, { scene_id: sceneId })],
        takes: [],
      })

    const fetches = deferred()
    fetches.answer('p1/all', all)
    fetches.answer('p1/e1', only('e1', '第一夜のカット', 'sc1'))
    fetches.answer('p1/e2', only('e2', '第二夜のカット', 'sc3'))

    mocked.listStudioProjects.mockResolvedValue([summary(all)])
    render(<StudioView progress={{}} />)
    fireEvent.click(await screen.findByText(all.name))
    await fetches.resolve('p1/all')
    await screen.findByRole('tab', { name: '概要' })

    clickTab('脚本')
    // 連打（どちらの取得もまだ返っていない）
    fireEvent.click(screen.getByRole('tab', { name: '第一夜' }))
    fireEvent.click(screen.getByRole('tab', { name: '第二夜' }))

    // 後から押した第二夜が先に返り、第一夜が遅れて後着する
    await fetches.resolve('p1/e2')
    await waitFor(() =>
      expect(rail().getByRole('button', { name: '第二夜のカット' })).toBeTruthy(),
    )
    await fetches.resolve('p1/e1')

    // 最後に届いたのは第一夜だが、画面は選んでいる第二夜のまま
    await waitFor(() =>
      expect(
        screen.getByRole('tab', { name: '第二夜' }).getAttribute('aria-selected'),
      ).toBe('true'),
    )
    expect(rail().queryByRole('button', { name: '第一夜のカット' })).toBeNull()
    expect(rail().getByRole('button', { name: '第二夜のカット' })).toBeTruthy()
  })

  it('作品を続けて開いても、遅れて届いた前の作品の detail は捨てる', async () => {
    const first = detail({ id: 'p1', name: '作品A' })
    const second = detail({ id: 'p2', name: '作品B' })

    const fetches = deferred()
    fetches.answer('p1/all', first)
    fetches.answer('p2/all', second)

    mocked.listStudioProjects.mockResolvedValue([summary(first), summary(second)])
    render(<StudioView progress={{}} />)
    // どちらもまだ返らないので一覧に留まったまま、続けて別の作品を開ける
    fireEvent.click(await screen.findByText('作品A'))
    fireEvent.click(await screen.findByText('作品B'))

    await fetches.resolve('p2/all')
    await screen.findByRole('tab', { name: '概要' })
    await fetches.resolve('p1/all')

    await waitFor(() => expect(screen.getByDisplayValue('作品B')).toBeTruthy())
    expect(screen.queryByDisplayValue('作品A')).toBeNull()
  })

  it('一覧へ戻ったあとに届いた detail では開き直さない', async () => {
    const current = structured()
    const fetches = deferred()
    fetches.answer('p1/all', current)
    fetches.answer('p1/e1', current)

    mocked.listStudioProjects.mockResolvedValue([summary(current)])
    render(<StudioView progress={{}} />)
    fireEvent.click(await screen.findByText(current.name))
    await fetches.resolve('p1/all')
    await screen.findByRole('tab', { name: '概要' })

    // 第一夜の取得が飛んだまま一覧へ戻る
    clickTab('脚本')
    fireEvent.click(screen.getByRole('tab', { name: '第一夜' }))
    fireEvent.click(screen.getByRole('button', { name: 'プロジェクト一覧' }))
    await waitFor(() => expect(screen.queryByRole('tab', { name: '概要' })).toBeNull())

    // 戻ったあとに届いても、閉じたはずの作品を開き直さない
    await fetches.resolve('p1/e1')
    await waitFor(() => expect(mocked.getStudioProject).toHaveBeenCalled())
    expect(screen.queryByRole('tab', { name: '概要' })).toBeNull()
  })
})
