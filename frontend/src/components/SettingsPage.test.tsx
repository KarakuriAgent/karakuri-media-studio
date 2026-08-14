import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '../api'
import type {
  Lora,
  ModelFieldState,
  ModelsDirStatus,
  Options,
  Settings,
} from '../types'
import SettingsPage from './SettingsPage'

vi.mock('../api', async () => {
  const actual = await vi.importActual<typeof import('../api')>('../api')
  return {
    ...actual,
    api: {
      getSettings: vi.fn(),
      listModels: vi.fn(),
      listLoras: vi.fn(),
      modelsDirStatus: vi.fn(),
      listModelDownloads: vi.fn(),
      putSettings: vi.fn(),
      putModels: vi.fn(),
      downloadModel: vi.fn(),
      downloadAllModels: vi.fn(),
      createLora: vi.fn(),
      updateLora: vi.fn(),
      checkGrok: vi.fn(),
    },
  }
})

const getSettings = vi.mocked(api.getSettings)
const checkGrok = vi.mocked(api.checkGrok)
const listModels = vi.mocked(api.listModels)
const listLoras = vi.mocked(api.listLoras)
const modelsDirStatus = vi.mocked(api.modelsDirStatus)
const listModelDownloads = vi.mocked(api.listModelDownloads)
const putSettings = vi.mocked(api.putSettings)
const putModels = vi.mocked(api.putModels)
const downloadModel = vi.mocked(api.downloadModel)
const downloadAllModels = vi.mocked(api.downloadAllModels)
const createLora = vi.mocked(api.createLora)
const updateLora = vi.mocked(api.updateLora)

afterEach(cleanup)

/** 進捗購読の WebSocket は jsdom では張らせず、何もしない偽物に差し替える。 */
class FakeSocket {
  onmessage: ((event: MessageEvent) => void) | null = null
  close() {}
}

function settings(): Settings {
  return {
    comfy_target: 'local',
    local_comfy_url: 'http://127.0.0.1:8188',
    runpod_comfy_url: '',
    runpod_comfy_api_key: '',
    comfy_cloud_api_key: '',
    grok_command: 'grok',
    grok_model: 'grok-4.5',
    grok_workdir: '/repo/runtime/grok-workdir',
    grok_media_workdir: '/repo/runtime/grok-media-workdir',
    grok_media_timeout: 300,
    model_overrides: {},
    model_choices: {},
    hf_token: '',
    civitai_api_key: '',
    model_download_urls: {},
    runpod_enabled: false,
    runpod_api_key: '',
    runpod_template_id: '',
    runpod_gpu_type: 'NVIDIA RTX A6000',
    runpod_network_volume_id: '',
    external_api_key: '',
    external_max_pending_takes: 20,
    agent_grok_args: ['--permission-mode', 'auto'],
    agent_use_acp: true,
    agent_grok_timeout: 300,
    agent_max_plan_tasks: 5,
    agent_max_turns: 20,
    canvas_max_turns: 8,
  }
}

function modelRow(): ModelFieldState {
  return {
    key: 'krea2_turbo/30:10.unet_name',
    workflow_id: 'krea2_turbo',
    workflow_label: 'Krea 2 Turbo',
    kind: 'image',
    family: 'krea2',
    family_label: 'Krea 2',
    node_id: '30:10',
    field: 'unet_name',
    class_type: 'UNETLoader',
    title: 'Load Diffusion Model',
    default: 'krea2_turbo_fp8_scaled.safetensors',
    subfolder: 'diffusion_models',
    value: 'krea2_turbo_fp8_scaled.safetensors',
    overridden: false,
    choices: [],
  }
}

function loraRow(): Lora {
  return {
    id: 1,
    display_name: 'かおり',
    lora_name: 'kaori-krea2.safetensors',
    trigger_word: 'kaori',
    default_strength: 1,
    default_audio: null,
    sort_order: 0,
    target: 'image',
    family: 'krea2',
    sample_images: [],
  }
}

function dirStatus(overrides: Partial<ModelsDirStatus> = {}): ModelsDirStatus {
  return { configured: false, exists: false, writable: false, path: '', ...overrides }
}

/** ComfyUI のファイル一覧に無い = その行が「未検出」になる options。 */
function missingOptions(): Options {
  return {
    model_files: { 'UNETLoader.unet_name': ['other.safetensors'] },
  } as unknown as Options
}

/** 設定ページを描画し、dir-status を読み終えるまで待つ。 */
async function openSettings(options: Options = {} as Options) {
  render(<SettingsPage options={options} onBack={() => {}} onChanged={() => {}} />)
  await waitFor(() => expect(modelsDirStatus).toHaveBeenCalled())
  await waitFor(() => screen.getByText('ComfyUI 接続先'))
}

it('接続タブの先頭に通知カードを出す', async () => {
  getSettings.mockResolvedValue(settings())
  listModels.mockResolvedValue([])
  listLoras.mockResolvedValue([])
  modelsDirStatus.mockResolvedValue(dirStatus())
  listModelDownloads.mockResolvedValue([])
  await openSettings()
  expect(screen.getByText('プッシュ通知')).toBeTruthy()
  expect(screen.getByText(/いまの許可状態/)).toBeTruthy()
  expect(screen.getByText('非対応')).toBeTruthy()
})

/** 設定タブを切り替える（shadcn Tabs = Radix なので mousedown が切り替えの契機）。 */
function openTab(name: string) {
  fireEvent.mouseDown(screen.getByRole('tab', { name }))
}

/** shadcn Select（Radix）のプルダウンを開く。 */
function openSelect(label: string): HTMLElement {
  const trigger = screen.getByLabelText(label)
  fireEvent.keyDown(trigger, { key: 'ArrowDown' })
  return trigger
}

/** shadcn Select から選択肢を選ぶ。 */
async function chooseOption(label: string, option: string | RegExp) {
  openSelect(label)
  await waitFor(() => screen.getByRole('option', { name: option }))
  fireEvent.click(screen.getByRole('option', { name: option }))
  await waitFor(() => expect(screen.queryByRole('listbox')).toBeNull())
}

/** 開いた Select の選択肢のラベル一覧（読み終えたら閉じる）。 */
async function optionLabels(label: string): Promise<string[]> {
  const trigger = openSelect(label)
  await waitFor(() => screen.getByRole('listbox'))
  const labels = screen.getAllByRole('option').map((item) => item.textContent ?? '')
  fireEvent.keyDown(trigger, { key: 'Escape' })
  await waitFor(() => expect(screen.queryByRole('listbox')).toBeNull())
  return labels
}

/** モデルタブを開いて、1 件だけあるワークフローの折りたたみも開く。 */
async function openModelsTab(options: Options = {} as Options) {
  await openSettings(options)
  openTab('モデル')
  await waitFor(() => screen.getByText(/Krea 2 Turbo/))
  screen.getByText(/Krea 2 Turbo/).click()
  await waitFor(() => screen.getByText('ノード'))
}

describe('SettingsPage: 環境ごとのモデル / LoRA（SPEC §5）', () => {
  beforeEach(() => {
    vi.stubGlobal('WebSocket', FakeSocket)
    getSettings.mockResolvedValue({ ...settings(), comfy_target: 'runpod' })
    listModels.mockResolvedValue([modelRow()])
    listLoras.mockResolvedValue([loraRow()])
    listModelDownloads.mockResolvedValue([])
    modelsDirStatus.mockResolvedValue(dirStatus())
    listModels.mockClear()
    listLoras.mockClear()
    putModels.mockReset()
    createLora.mockReset()
    downloadModel.mockReset()
    downloadAllModels.mockReset()
  })

  it('初期値は現在の接続先で、その環境のモデル / LoRA を読む', async () => {
    await openSettings()

    expect(listModels).toHaveBeenCalledWith('runpod')
    expect(listLoras).toHaveBeenCalledWith('runpod')
    expect(listModelDownloads).toHaveBeenCalledWith('runpod')
    openTab('モデル')
    await waitFor(() => screen.getByLabelText('対象の接続先'))
    expect(screen.getByLabelText('対象の接続先').textContent).toContain('RunPod')
    // 現在の接続先が分かるように印を付ける
    expect(await optionLabels('対象の接続先')).toContain('RunPod（現在の接続先）')
  })

  it('環境を切り替えるとその環境の一覧を読み直す', async () => {
    await openSettings()
    openTab('モデル')
    await waitFor(() => screen.getByLabelText('対象の接続先'))
    listModels.mockClear()
    listLoras.mockClear()

    await chooseOption('対象の接続先', 'ローカル')

    await waitFor(() => expect(listModels).toHaveBeenCalledWith('local'))
    expect(listLoras).toHaveBeenCalledWith('local')
  })

  it('モデルの保存は選んだ環境あてに送る', async () => {
    putModels.mockResolvedValue([modelRow()])
    await openModelsTab()

    const input = screen.getAllByDisplayValue(
      'krea2_turbo_fp8_scaled.safetensors',
    )[0]
    fireEvent.change(input, { target: { value: 'mine.safetensors' } })
    screen.getByRole('button', { name: '保存' }).click()

    await waitFor(() => expect(putModels).toHaveBeenCalled())
    expect(putModels.mock.calls[0][2]).toBe('runpod')
  })

  it('LoRA の新規登録は選んだ環境に紐づける', async () => {
    await openSettings()
    openTab('LoRA 管理')
    await waitFor(() => screen.getByText('かおり'))

    const displayName = screen.getByLabelText('表示名') as HTMLInputElement
    fireEvent.change(displayName, { target: { value: 'みずき' } })
    fireEvent.change(screen.getByPlaceholderText('例: my_lora.safetensors'), {
      target: { value: 'mizuki.safetensors' },
    })
    screen.getByRole('button', { name: '追加' }).click()

    await waitFor(() => expect(createLora).toHaveBeenCalled())
    expect(createLora.mock.calls[0][0]).toMatchObject({
      lora_name: 'mizuki.safetensors',
      comfy_target: 'runpod',
    })
  })

  it('[DL] は選んだ環境に落とす（RunPod なら Pod へ）', async () => {
    putSettings.mockResolvedValue(settings())
    await openModelsTab(missingOptions())

    fireEvent.change(screen.getByPlaceholderText(/ダウンロード URL/), {
      target: { value: 'https://example.com/a.safetensors' },
    })
    screen.getByRole('button', { name: 'DL' }).click()

    await waitFor(() => expect(downloadModel).toHaveBeenCalled())
    expect(downloadModel.mock.calls[0]).toEqual([
      'krea2_turbo_fp8_scaled.safetensors',
      'https://example.com/a.safetensors',
      'diffusion_models',
      'runpod',
    ])
  })

  it('LoRA 管理一覧は名前・トリガー検索と対象・ファミリーで絞り込める', async () => {
    listLoras.mockResolvedValue([
      loraRow(),
      {
        ...loraRow(),
        id: 2,
        display_name: 'スローモ',
        lora_name: 'slowmo-video.safetensors',
        trigger_word: 'slowmotion',
        target: 'video',
        family: 'video',
      },
      {
        ...loraRow(),
        id: 3,
        display_name: 'セリア',
        lora_name: 'seria_anima_character_v1_comfy.safetensors',
        trigger_word: 'seriaanti',
        target: 'image',
        family: 'anima',
      },
    ])
    await openSettings()
    openTab('LoRA 管理')
    await waitFor(() => screen.getByText('セリア'))

    const list = screen.getByTestId('lora-management-list')
    expect(list.textContent).toContain('かおり')
    expect(list.textContent).toContain('スローモ')
    expect(list.textContent).toContain('セリア')
    expect(screen.getByText('表示 3 / 全 3')).toBeTruthy()

    fireEvent.change(screen.getByLabelText('LoRAを検索'), {
      target: { value: 'seriaanti' },
    })
    await waitFor(() => expect(list.textContent).toContain('セリア'))
    expect(list.textContent).not.toContain('かおり')
    expect(list.textContent).not.toContain('スローモ')
    expect(screen.getByText('表示 1 / 全 3')).toBeTruthy()

    fireEvent.change(screen.getByLabelText('LoRAを検索'), { target: { value: '' } })
    await chooseOption('LoRAの対象', '動画用')
    await waitFor(() => expect(list.textContent).toContain('スローモ'))
    expect(list.textContent).not.toContain('かおり')
    expect(list.textContent).not.toContain('セリア')

    await chooseOption('LoRAの対象', 'すべて')
    await chooseOption('LoRAのファミリー', 'Anima')
    await waitFor(() => expect(list.textContent).toContain('セリア'))
    expect(list.textContent).not.toContain('かおり')
    expect(list.textContent).not.toContain('スローモ')
  })

  it('対象=動画用のときファミリー絞り込みは無効になり、動画 LoRA が消えない', async () => {
    listLoras.mockResolvedValue([
      loraRow(),
      {
        ...loraRow(),
        id: 2,
        display_name: 'スローモ',
        lora_name: 'slowmo-video.safetensors',
        trigger_word: 'slowmotion',
        target: 'video',
        family: 'video',
      },
      {
        ...loraRow(),
        id: 3,
        display_name: 'セリア',
        lora_name: 'seria_anima_character_v1_comfy.safetensors',
        trigger_word: 'seriaanti',
        target: 'image',
        family: 'anima',
      },
    ])
    await openSettings()
    openTab('LoRA 管理')
    await waitFor(() => screen.getByText('セリア'))

    const list = screen.getByTestId('lora-management-list')
    // 先にファミリーを指定してから対象を動画用へ切り替える（残ったファミリー
    // 条件で 0 件にならないこと）
    await chooseOption('LoRAのファミリー', 'Anima')
    await waitFor(() => expect(list.textContent).not.toContain('かおり'))

    await chooseOption('LoRAの対象', '動画用')
    await waitFor(() => expect(list.textContent).toContain('スローモ'))
    expect(list.textContent).not.toContain('かおり')
    expect(list.textContent).not.toContain('セリア')
    expect(screen.getByText('表示 1 / 全 3')).toBeTruthy()
    expect(
      (screen.getByLabelText('LoRAのファミリー') as HTMLButtonElement).disabled,
    ).toBe(true)

    // 対象を戻せばファミリー条件が効き、絞り込みも再び使える
    await chooseOption('LoRAの対象', 'すべて')
    await waitFor(() => expect(list.textContent).toContain('セリア'))
    expect(list.textContent).not.toContain('スローモ')
    expect(
      (screen.getByLabelText('LoRAのファミリー') as HTMLButtonElement).disabled,
    ).toBe(false)
  })

  it('[全DL] は選んだ環境の不足モデルを一括で開始する', async () => {
    downloadAllModels.mockResolvedValue({
      started: [],
      missing_urls: ['other.safetensors'],
      errors: {},
    })
    await openModelsTab(missingOptions())

    screen.getByRole('button', { name: '全DL' }).click()

    await waitFor(() => expect(downloadAllModels).toHaveBeenCalledWith('runpod'))
    await waitFor(() => screen.getByText(/取得元 URL が未登録/))
  })

  it('繋いでいない環境を編集しているあいだは「未検出」を判定しない', async () => {
    // options のファイル一覧は「いま繋いでいる ComfyUI」のものなので、別環境の
    // 在庫は分からない（勝手に未検出と決めつけない）
    await openModelsTab(missingOptions())
    expect(screen.getByTitle('ComfyUI のファイル一覧に見つかりません')).toBeTruthy()

    await chooseOption('対象の接続先', 'ローカル')

    await waitFor(() =>
      expect(
        screen.queryByTitle('ComfyUI のファイル一覧に見つかりません'),
      ).toBeNull(),
    )
  })

  it('ComfyCloud を選ぶとダウンロード関連は出さない', async () => {
    await openModelsTab()
    await chooseOption('対象の接続先', 'ComfyCloud')

    await waitFor(() =>
      expect(screen.queryByRole('button', { name: '全DL' })).toBeNull(),
    )
  })
})

describe('SettingsPage: ComfyUI 接続先（3 プロファイル）', () => {
  beforeEach(() => {
    vi.stubGlobal('WebSocket', FakeSocket)
    getSettings.mockResolvedValue(settings())
    listModels.mockResolvedValue([modelRow()])
    listLoras.mockResolvedValue([])
    listModelDownloads.mockResolvedValue([])
    modelsDirStatus.mockResolvedValue(dirStatus())
    putSettings.mockReset()
  })

  it('接続先の選択と、プロファイルごとの接続情報を出す', async () => {
    await openSettings()

    expect(screen.getByLabelText('接続先').textContent).toContain('ローカル')
    expect(await optionLabels('接続先')).toEqual(['ComfyCloud', 'RunPod', 'ローカル'])
    // ComfyCloud はエンドポイント固定なので APIキーだけ（URL 欄は出さない）
    expect(screen.getByText('ComfyCloud APIキー')).toBeTruthy()
    expect(screen.getByText('RunPod ComfyUI URL')).toBeTruthy()
    expect(screen.getByText('RunPod ComfyUI APIキー（任意）')).toBeTruthy()
    expect(screen.getByText('ローカル ComfyUI URL')).toBeTruthy()
    // 自動起動の詳細はチェックを入れるまで畳んでおく
    expect(screen.queryByText('テンプレート ID')).toBeNull()
  })

  it('接続先とプロファイルを編集して保存すると設定に載る', async () => {
    putSettings.mockResolvedValue({
      ...settings(),
      comfy_target: 'comfy_cloud',
      comfy_cloud_api_key: 'k',
    })
    await openSettings()

    await chooseOption('接続先', 'ComfyCloud')
    fireEvent.change(screen.getByPlaceholderText('https://<Cloudflare Tunnel のホスト名>'), {
      target: { value: 'https://pod.example.com' },
    })
    screen.getByRole('button', { name: '保存' }).click()

    await waitFor(() => expect(putSettings).toHaveBeenCalled())
    const sent = putSettings.mock.calls[0][0]
    expect(sent.comfy_target).toBe('comfy_cloud')
    expect(sent.runpod_comfy_url).toBe('https://pod.example.com')
    // 他のプロファイルも一緒に保存される（切り替えてすぐ使えるように）
    expect(sent.local_comfy_url).toBe('http://127.0.0.1:8188')
    expect(sent.comfy_cloud_api_key).toBe('')
  })

  it('Grok の workdir / 追加フラグ / ACP を編集して保存できる', async () => {
    putSettings.mockResolvedValue(settings())
    await openSettings()

    fireEvent.change(screen.getByLabelText('grok の作業ディレクトリ（空 = 既定）'), {
      target: { value: '/tmp/chat' },
    })
    fireEvent.change(
      screen.getByLabelText('Grok Imagine の作業ディレクトリ（空 = 既定）'),
      { target: { value: '/tmp/media' } },
    )
    fireEvent.change(screen.getByLabelText('grok CLI の追加フラグ（空白区切り）'), {
      target: { value: '--permission-mode  ask ' },
    })
    fireEvent.click(screen.getByRole('switch', { name: 'ACP でターンを回す' }))
    screen.getByRole('button', { name: '保存' }).click()

    await waitFor(() => expect(putSettings).toHaveBeenCalled())
    const sent = putSettings.mock.calls[0][0]
    expect(sent.grok_workdir).toBe('/tmp/chat')
    expect(sent.grok_media_workdir).toBe('/tmp/media')
    // 空白区切りの入力欄はフラグの配列に戻る
    expect(sent.agent_grok_args).toEqual(['--permission-mode', 'ask'])
    expect(sent.agent_use_acp).toBe(false)
  })

  it('追加フラグを空にするとツール無効の警告を出したまま空配列で保存する', async () => {
    putSettings.mockResolvedValue(settings())
    await openSettings()

    fireEvent.change(screen.getByLabelText('grok CLI の追加フラグ（空白区切り）'), {
      target: { value: '   ' },
    })
    expect(
      screen.getByText('空にするとエージェントのツールが無効になります'),
    ).toBeTruthy()
    screen.getByRole('button', { name: '保存' }).click()

    await waitFor(() => expect(putSettings).toHaveBeenCalled())
    expect(putSettings.mock.calls[0][0].agent_grok_args).toEqual([])
  })

  it('自動起動を有効にすると RunPod の起動設定が出る', async () => {
    await openSettings()

    fireEvent.click(
      screen.getByRole('switch', { name: 'RunPod の Pod を自動起動する' }),
    )

    await waitFor(() => screen.getByText('テンプレート ID'))
    expect(screen.getByText('RunPod APIキー')).toBeTruthy()
    expect(screen.getByText('GPU 種別（gpuTypeId）')).toBeTruthy()
  })
})

describe('SettingsPage: 不足モデルのダウンロード UI（COMFY_MODELS_DIR ゲート）', () => {
  beforeEach(() => {
    vi.stubGlobal('WebSocket', FakeSocket)
    getSettings.mockResolvedValue(settings())
    listModels.mockResolvedValue([modelRow()])
    listLoras.mockResolvedValue([])
    listModelDownloads.mockResolvedValue([])
  })

  it('環境変数が無くてもトークン欄と保存先の状態を出す（機能は隠さない）', async () => {
    // RunPod へ落とすときにもトークンは要るので、COMFY_MODELS_DIR の有無で
    // ブロックごと隠したりはしない（押したときに理由を出す方式）。
    modelsDirStatus.mockResolvedValue(dirStatus())
    await openSettings()

    expect(screen.getByText('モデル自動ダウンロード')).toBeTruthy()
    expect(screen.getByText(/Hugging Face トークン/)).toBeTruthy()
    expect(screen.getByText('Civitai APIキー（任意）')).toBeTruthy()
    expect(screen.getByText(/COMFY_MODELS_DIR が設定されていません/)).toBeTruthy()
  })

  it('環境変数があれば保存先（読み取り専用）とトークン欄を出す', async () => {
    modelsDirStatus.mockResolvedValue(
      dirStatus({ configured: true, exists: true, writable: true, path: '/comfy/models' }),
    )
    await openSettings()

    expect(screen.getByText('モデル自動ダウンロード')).toBeTruthy()
    const path = screen.getByDisplayValue('/comfy/models') as HTMLInputElement
    expect(path.readOnly).toBe(true)
    expect(screen.getByText(/Hugging Face トークン/)).toBeTruthy()
    expect(screen.getByText('Civitai APIキー（任意）')).toBeTruthy()
    expect(screen.getByText(/書き込み可/)).toBeTruthy()
  })

  it('環境変数が無くても [DL] と [全DL] は出す（理由は警告で添える）', async () => {
    modelsDirStatus.mockResolvedValue(dirStatus())
    await openModelsTab(missingOptions())

    expect(screen.getByText('取得元 URL / ダウンロード')).toBeTruthy()
    expect(screen.getByPlaceholderText(/ダウンロード URL/)).toBeTruthy()
    // 押せば 400 の理由が返るので、ボタンは隠さない
    expect(screen.getByRole('button', { name: 'DL' })).toBeTruthy()
    expect(screen.getByRole('button', { name: '全DL' })).toBeTruthy()
    expect(screen.getByText(/COMFY_MODELS_DIR が設定されていません/)).toBeTruthy()
  })

  it('ディレクトリが使えなくても [DL] は出し、理由を警告に出す', async () => {
    modelsDirStatus.mockResolvedValue(
      dirStatus({ configured: true, path: '/comfy/models' }),
    )
    await openModelsTab(missingOptions())

    expect(screen.getByText('取得元 URL / ダウンロード')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'DL' })).toBeTruthy()
    expect(screen.getByText(/パスが見つかりません/)).toBeTruthy()
  })

  it('未検出の行は URL 欄と [DL] をそのまま出す（開閉ボタンは挟まない）', async () => {
    modelsDirStatus.mockResolvedValue(
      dirStatus({ configured: true, exists: true, writable: true, path: '/comfy/models' }),
    )
    await openModelsTab(missingOptions())

    // 行に付く「未検出」バッジ（タブ上部の説明文にも同じ語が出るので title で引く）
    expect(screen.getByTitle('ComfyUI のファイル一覧に見つかりません')).toBeTruthy()
    expect(screen.queryByRole('button', { name: /取得元 URL/ })).toBeNull()
    expect(
      screen.getByPlaceholderText(/ダウンロード URL/) as HTMLInputElement,
    ).toBeTruthy()
    expect(screen.getByRole('button', { name: 'DL' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'URL保存' })).toBeNull()
  })
})

describe('SettingsPage: 検出済みモデルの取得元 URL 登録', () => {
  beforeEach(() => {
    vi.stubGlobal('WebSocket', FakeSocket)
    getSettings.mockResolvedValue(settings())
    listModels.mockResolvedValue([modelRow()])
    listLoras.mockResolvedValue([])
    listModelDownloads.mockResolvedValue([])
    putSettings.mockReset()
    modelsDirStatus.mockResolvedValue(
      dirStatus({ configured: true, exists: true, writable: true, path: '/comfy/models' }),
    )
  })

  /** 検出済みの行の [取得元 URL] を押して URL 欄を開く。 */
  async function openUrlEditor() {
    await openModelsTab()
    const toggle = screen.getByRole('button', { name: /取得元 URL/ })
    expect(screen.queryByPlaceholderText(/ダウンロード URL/)).toBeNull()
    toggle.click()
    await waitFor(() => screen.getByPlaceholderText(/ダウンロード URL/))
    return screen.getByPlaceholderText(/ダウンロード URL/) as HTMLInputElement
  }

  it('検出済みの行は既定で URL 欄を畳んでおり、開くと [URL保存] が出る', async () => {
    await openModelsTab()

    expect(screen.queryByTitle('ComfyUI のファイル一覧に見つかりません')).toBeNull()
    expect(screen.queryByRole('button', { name: 'DL' })).toBeNull()
    expect(screen.getByRole('button', { name: /取得元 URL/ })).toBeTruthy()
    expect(screen.queryByPlaceholderText(/ダウンロード URL/)).toBeNull()

    screen.getByRole('button', { name: /取得元 URL/ }).click()
    await waitFor(() => screen.getByPlaceholderText(/ダウンロード URL/))
    expect(screen.getByRole('button', { name: 'URL保存' })).toBeTruthy()
    // 検出済みでも、開いたなら落とし直せる（[DL] も並ぶ）
    expect(screen.getByRole('button', { name: 'DL' })).toBeTruthy()
  })

  it('URL を入れて保存すると model_download_urls に載る', async () => {
    putSettings.mockResolvedValue({
      ...settings(),
      model_download_urls: {
        'krea2_turbo_fp8_scaled.safetensors': 'https://example.com/a.safetensors',
      },
    })
    const input = await openUrlEditor()

    fireEvent.change(input, {
      target: { value: 'https://example.com/a.safetensors' },
    })
    screen.getByRole('button', { name: 'URL保存' }).click()

    await waitFor(() => expect(putSettings).toHaveBeenCalled())
    expect(putSettings.mock.calls[0][0]).toEqual({
      model_download_urls: {
        'krea2_turbo_fp8_scaled.safetensors': 'https://example.com/a.safetensors',
      },
    })
    await waitFor(() => screen.getByText(/取得元 URL を保存しました/))
    // 登録済みは開閉ボタンの表示で分かる
    expect(screen.getByRole('button', { name: /取得元 URL 登録済み/ })).toBeTruthy()
  })

  it('空欄で保存すると登録が解除される（キーが消える）', async () => {
    getSettings.mockResolvedValue({
      ...settings(),
      model_download_urls: {
        'krea2_turbo_fp8_scaled.safetensors': 'https://example.com/a.safetensors',
        'other.safetensors': 'https://example.com/b.safetensors',
      },
    })
    putSettings.mockResolvedValue({
      ...settings(),
      model_download_urls: {
        'other.safetensors': 'https://example.com/b.safetensors',
      },
    })
    await openModelsTab()
    // 登録済みなので印が付いている
    const toggle = screen.getByRole('button', { name: /取得元 URL 登録済み/ })
    toggle.click()
    const input = (await waitFor(() =>
      screen.getByPlaceholderText(/ダウンロード URL/),
    )) as HTMLInputElement
    expect(input.value).toBe('https://example.com/a.safetensors')

    fireEvent.change(input, { target: { value: '' } })
    screen.getByRole('button', { name: 'URL保存' }).click()

    await waitFor(() => expect(putSettings).toHaveBeenCalled())
    expect(putSettings.mock.calls[0][0]).toEqual({
      model_download_urls: { 'other.safetensors': 'https://example.com/b.safetensors' },
    })
    await waitFor(() => screen.getByText(/取得元 URL を解除しました/))
    expect(screen.queryByRole('button', { name: /取得元 URL 登録済み/ })).toBeNull()
  })

  it('COMFY_MODELS_DIR 未設定でも取得元 URL は登録できる', async () => {
    // Comfy Cloud 接続などでローカルの models ディレクトリが無い環境
    modelsDirStatus.mockResolvedValue(dirStatus())
    putSettings.mockResolvedValue({
      ...settings(),
      model_download_urls: {
        'krea2_turbo_fp8_scaled.safetensors': 'https://example.com/a.safetensors',
      },
    })
    const input = await openUrlEditor()

    fireEvent.change(input, {
      target: { value: 'https://example.com/a.safetensors' },
    })
    const save = screen.getByRole('button', { name: 'URL保存' })
    expect(save.hasAttribute('disabled')).toBe(false)
    save.click()

    await waitFor(() => expect(putSettings).toHaveBeenCalled())
    expect(putSettings.mock.calls[0][0]).toEqual({
      model_download_urls: {
        'krea2_turbo_fp8_scaled.safetensors': 'https://example.com/a.safetensors',
      },
    })
  })

  it('変更が無いあいだ [URL保存] は押せない', async () => {
    await openUrlEditor()

    expect(
      screen.getByRole('button', { name: 'URL保存' }).hasAttribute('disabled'),
    ).toBe(true)
  })
})

describe('SettingsPage: LoRA フォームの取得元 URL', () => {
  const URL_A = 'https://example.com/kaori.safetensors'

  beforeEach(() => {
    vi.stubGlobal('WebSocket', FakeSocket)
    getSettings.mockResolvedValue(settings())
    listModels.mockResolvedValue([modelRow()])
    listLoras.mockResolvedValue([loraRow()])
    listModelDownloads.mockResolvedValue([])
    putSettings.mockReset()
    createLora.mockReset()
    updateLora.mockReset()
    modelsDirStatus.mockResolvedValue(dirStatus())
  })

  /** LoRA タブを開く（1 件だけ登録がある状態）。 */
  async function openLorasTab() {
    await openSettings()
    openTab('LoRA 管理')
    await waitFor(() => screen.getByText('かおり'))
  }

  /** 一覧の [編集] を押してフォームに読み込む。 */
  async function startEditing() {
    await openLorasTab()
    screen.getByRole('button', { name: '編集' }).click()
    await waitFor(() => screen.getByText(/LoRA を編集/))
    return screen.getByPlaceholderText(/ダウンロード URL/) as HTMLInputElement
  }

  it('URL 欄はフォームの一部で、行ごとの開閉ボタンは無い', async () => {
    await openLorasTab()

    expect(screen.getByText('取得元 URL（任意）')).toBeTruthy()
    expect(screen.getByPlaceholderText(/ダウンロード URL/)).toBeTruthy()
    expect(screen.queryByRole('button', { name: /取得元 URL/ })).toBeNull()
    expect(screen.queryByRole('button', { name: 'URL保存' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'DL' })).toBeNull()
  })

  it('追加時に URL を入れると LoRA 作成と一緒に model_download_urls へ載る', async () => {
    putSettings.mockResolvedValue({
      ...settings(),
      model_download_urls: { 'mizuki.safetensors': URL_A },
    })
    await openLorasTab()

    const displayName = screen.getByLabelText('表示名') as HTMLInputElement
    fireEvent.change(displayName, { target: { value: 'みずき' } })
    fireEvent.change(screen.getByPlaceholderText('例: my_lora.safetensors'), {
      target: { value: 'mizuki.safetensors' },
    })
    fireEvent.change(screen.getByPlaceholderText(/ダウンロード URL/), {
      target: { value: URL_A },
    })
    screen.getByRole('button', { name: '追加' }).click()

    await waitFor(() => expect(createLora).toHaveBeenCalled())
    await waitFor(() => expect(putSettings).toHaveBeenCalled())
    expect(putSettings.mock.calls[0][0]).toEqual({
      model_download_urls: { 'mizuki.safetensors': URL_A },
    })
  })

  it('編集フォームには登録済み URL がプリフィルされ、一覧には印が出る', async () => {
    getSettings.mockResolvedValue({
      ...settings(),
      model_download_urls: { 'kaori-krea2.safetensors': URL_A },
    })
    const input = await startEditing()

    expect(input.value).toBe(URL_A)
    expect(screen.getByTitle(`取得元 URL: ${URL_A}`)).toBeTruthy()
  })

  it('URL を空欄にして更新すると登録が解除される（キーが消える）', async () => {
    getSettings.mockResolvedValue({
      ...settings(),
      model_download_urls: {
        'kaori-krea2.safetensors': URL_A,
        'other.safetensors': 'https://example.com/b.safetensors',
      },
    })
    putSettings.mockResolvedValue({
      ...settings(),
      model_download_urls: { 'other.safetensors': 'https://example.com/b.safetensors' },
    })
    const input = await startEditing()

    fireEvent.change(input, { target: { value: '' } })
    screen.getByRole('button', { name: '更新' }).click()

    await waitFor(() => expect(updateLora).toHaveBeenCalled())
    await waitFor(() => expect(putSettings).toHaveBeenCalled())
    expect(putSettings.mock.calls[0][0]).toEqual({
      model_download_urls: { 'other.safetensors': 'https://example.com/b.safetensors' },
    })
  })

  it('ファイル名を変えて更新すると旧キーから新キーへ移る', async () => {
    getSettings.mockResolvedValue({
      ...settings(),
      model_download_urls: { 'kaori-krea2.safetensors': URL_A },
    })
    putSettings.mockResolvedValue({
      ...settings(),
      model_download_urls: { 'kaori-v2.safetensors': URL_A },
    })
    await startEditing()

    fireEvent.change(screen.getByPlaceholderText('例: my_lora.safetensors'), {
      target: { value: 'kaori-v2.safetensors' },
    })
    screen.getByRole('button', { name: '更新' }).click()

    await waitFor(() => expect(putSettings).toHaveBeenCalled())
    expect(putSettings.mock.calls[0][0]).toEqual({
      model_download_urls: { 'kaori-v2.safetensors': URL_A },
    })
  })

  it('URL に変更が無ければ設定は PUT しない', async () => {
    getSettings.mockResolvedValue({
      ...settings(),
      model_download_urls: { 'kaori-krea2.safetensors': URL_A },
    })
    await startEditing()

    screen.getByRole('button', { name: '更新' }).click()

    await waitFor(() => expect(updateLora).toHaveBeenCalled())
    expect(putSettings).not.toHaveBeenCalled()
  })
})

describe('SettingsPage: LoRA スロットの候補（SPEC §3.3）', () => {
  beforeEach(() => {
    vi.stubGlobal('WebSocket', FakeSocket)
    getSettings.mockResolvedValue(settings())
    listLoras.mockResolvedValue([loraRow()])
    listModelDownloads.mockResolvedValue([])
    modelsDirStatus.mockResolvedValue(dirStatus())
  })

  it('MiniMax H3 turbo の LoRA スロットは lora_files を候補に使う', async () => {
    // カスタムノードを入れていない環境では object_info に出てこないので、
    // model_files にはこの class_type の一覧が無い。
    listModels.mockResolvedValue([
      {
        ...modelRow(),
        key: 'minimax_h3_i2v_turbo/150.lora_name',
        workflow_id: 'minimax_h3_i2v_turbo',
        workflow_label: 'MiniMax H3 i2v Turbo',
        kind: 'video',
        family: 'minimax-h3',
        family_label: 'MiniMax H3',
        node_id: '150',
        field: 'lora_name',
        class_type: 'MiniMaxH3TurboLoRA',
        title: 'MiniMax H3 Turbo LoRA',
        default: 'minimax_h3_turbo_4step_ema_ckpt850.safetensors',
        value: 'minimax_h3_turbo_4step_ema_ckpt850.safetensors',
        subfolder: 'loras',
      },
    ])
    await openSettings({
      model_files: {},
      lora_files: ['minimax_h3_turbo_4step_ema_ckpt850.safetensors', 'other.safetensors'],
    } as unknown as Options)
    openTab('モデル')
    await waitFor(() => screen.getByText(/MiniMax H3/))

    const list = document.getElementById('model-files-MiniMaxH3TurboLoRA.lora_name')
    expect(list).toBeTruthy()
    expect([...(list as HTMLDataListElement).options].map((o) => o.value)).toEqual([
      'minimax_h3_turbo_4step_ema_ckpt850.safetensors',
      'other.safetensors',
    ])
  })
})


describe('SettingsPage: 動画はモデル名 → ワークフローの 2 階層で出す', () => {
  /** MiniMax H3 の 3 ワークフロー分の行（unet だけ turbo が別ファイル）。 */
  function videoRow(
    workflow: string,
    label: string,
    node: string,
    field: string,
    classType: string,
    value: string,
  ): ModelFieldState {
    return {
      ...modelRow(),
      key: `${workflow}/${node}.${field}`,
      workflow_id: workflow,
      workflow_label: label,
      kind: 'video',
      family: 'minimax-h3',
      family_label: 'MiniMax H3',
      node_id: node,
      field,
      class_type: classType,
      title: classType,
      default: value,
      value,
      subfolder: 'diffusion_models',
    }
  }

  const T2V = 'テキスト→動画・音声つき (MiniMax H3 t2v)'
  const I2V = '画像→動画・音声つき (MiniMax H3 i2v)'
  const I2V_TURBO = '画像→動画・音声つき (MiniMax H3 i2v Turbo)'

  const videoRows = (): ModelFieldState[] => [
    videoRow('minimax_h3_t2v', T2V, '10', 'vae_name', 'VAELoader', 'video_vae.safetensors'),
    videoRow('minimax_h3_t2v', T2V, '11', 'unet_name', 'UNETLoader', 'fl2va.safetensors'),
    videoRow('minimax_h3_i2v', I2V, '10', 'vae_name', 'VAELoader', 'video_vae.safetensors'),
    videoRow('minimax_h3_i2v', I2V, '11', 'unet_name', 'UNETLoader', 'fl2va.safetensors'),
    videoRow(
      'minimax_h3_i2v_turbo',
      I2V_TURBO,
      '11',
      'unet_name',
      'UNETLoader',
      'fl2va_w4a8.safetensors',
    ),
  ]

  beforeEach(() => {
    vi.stubGlobal('WebSocket', FakeSocket)
    getSettings.mockResolvedValue(settings())
    listModels.mockResolvedValue(videoRows())
    listLoras.mockResolvedValue([])
    listModelDownloads.mockResolvedValue([])
    modelsDirStatus.mockResolvedValue(dirStatus())
    putModels.mockReset()
  })

  /** モデルタブを開く（グループは畳まれたまま）。 */
  async function openModels() {
    await openSettings({ model_files: {} } as unknown as Options)
    openTab('モデル')
    await waitFor(() => screen.getByText('MiniMax H3'))
  }

  it('モデル名を見出しにして、その下にワークフローのグループを並べる', async () => {
    await openModels()

    // 親はモデル名 1 つ、子は 3 ワークフロー分（見出しのモデル名は繰り返さない）
    expect(screen.getByText('MiniMax H3')).toBeTruthy()
    expect(screen.getByText('テキスト→動画・音声つき (t2v)')).toBeTruthy()
    expect(screen.getByText('画像→動画・音声つき (i2v)')).toBeTruthy()
    expect(screen.getByText('画像→動画・音声つき (i2v Turbo)')).toBeTruthy()
    expect(screen.queryByText(T2V)).toBeNull()
    // グループごとの項目数（t2v / i2v は 2 項目、turbo は 1 項目）
    expect(screen.getAllByText('2 項目')).toHaveLength(2)
    expect(screen.getByText('1 項目')).toBeTruthy()
  })

  it('ワークフローのグループは個別に開き、行はそのワークフローの分だけ出る', async () => {
    await openModels()

    screen.getByText('テキスト→動画・音声つき (t2v)').click()
    await waitFor(() => screen.getByText('ノード'))

    // 開いたのは t2v だけなので、同じファイルでも行は 1 本ずつ
    expect(screen.getAllByDisplayValue('video_vae.safetensors')).toHaveLength(1)
    expect(screen.getAllByDisplayValue('fl2va.safetensors')).toHaveLength(1)
    expect(screen.queryByDisplayValue('fl2va_w4a8.safetensors')).toBeNull()
    // ノード欄は従来どおり「ノード ID.欄名 / class_type」
    expect(screen.getByText('10.vae_name / VAELoader')).toBeTruthy()
  })

  it('編集はそのワークフローのキーだけ書き換える', async () => {
    putModels.mockResolvedValue(videoRows())
    await openModels()

    screen.getByText('テキスト→動画・音声つき (t2v)').click()
    await waitFor(() => screen.getByText('ノード'))
    fireEvent.change(screen.getByDisplayValue('video_vae.safetensors'), {
      target: { value: 'mine_vae.safetensors' },
    })
    screen.getByRole('button', { name: '保存' }).click()

    await waitFor(() => expect(putModels).toHaveBeenCalled())
    const overrides = putModels.mock.calls[0][0] as Record<string, string>
    expect(overrides['minimax_h3_t2v/10.vae_name']).toBe('mine_vae.safetensors')
    // 他のワークフローの同じ欄は巻き添えにしない
    expect(overrides['minimax_h3_i2v/10.vae_name']).toBe('video_vae.safetensors')
  })

  it('ワークフローが 1 本だけのモデルは見出しを出さず従来どおり', async () => {
    listModels.mockResolvedValue([modelRow()])
    await openSettings({ model_files: {} } as unknown as Options)
    openTab('モデル')

    // グループの見出しはワークフローの完全なラベル（「Krea 2」だけの行は無い）
    await waitFor(() => screen.getByText('Krea 2 Turbo'))
    expect(screen.queryByText('Krea 2')).toBeNull()
    expect(screen.queryByText('Turbo')).toBeNull()
  })
})

describe('SettingsPage: Grok Build CLI（Grok Imagine のバックエンド、SPEC §5.2）', () => {
  beforeEach(() => {
    vi.stubGlobal('WebSocket', FakeSocket)
    getSettings.mockResolvedValue(settings())
    listModels.mockResolvedValue([])
    listLoras.mockResolvedValue([])
    listModelDownloads.mockResolvedValue([])
    modelsDirStatus.mockResolvedValue(dirStatus())
    checkGrok.mockReset()
    putSettings.mockReset()
  })

  it('接続確認は CLI を 1 ターン回して結果を出す', async () => {
    checkGrok.mockResolvedValue({ status: 'ok', detail: 'grok / 認証済み' })
    await openSettings()

    fireEvent.click(screen.getByRole('button', { name: 'grok CLI の接続確認' }))

    await waitFor(() => expect(checkGrok).toHaveBeenCalledTimes(1))
    await waitFor(() => screen.getByText('grok / 認証済み'))
  })

  it('未サインインなら理由をそのまま出す', async () => {
    checkGrok.mockResolvedValue({
      status: 'not_configured',
      detail: 'grok CLI が未認証です',
    })
    await openSettings()

    fireEvent.click(screen.getByRole('button', { name: 'grok CLI の接続確認' }))

    await waitFor(() => screen.getByText('grok CLI が未認証です'))
  })

  it('Grok Imagine の制限時間は設定として保存される', async () => {
    putSettings.mockResolvedValue(settings())
    await openSettings()

    fireEvent.change(screen.getByLabelText('Grok Imagine の制限時間（秒）'), {
      target: { value: '600' },
    })
    fireEvent.click(screen.getAllByRole('button', { name: '保存' })[0])

    await waitFor(() => expect(putSettings).toHaveBeenCalled())
    expect(putSettings.mock.calls[0][0]).toMatchObject({ grok_media_timeout: 600 })
  })

  it('実行上限は 0（無制限）を含めて保存される', async () => {
    putSettings.mockResolvedValue(settings())
    await openSettings()

    fireEvent.change(
      screen.getByLabelText('エージェントの連続ターン上限（0 = 無制限）'),
      { target: { value: '0' } },
    )
    fireEvent.change(
      screen.getByLabelText('キャンバスの連続ターン上限（0 = 無制限）'),
      { target: { value: '0' } },
    )
    fireEvent.change(
      screen.getByLabelText('1 プラン提案の新規ジョブ上限（自走時・0 = 無制限）'),
      { target: { value: '0' } },
    )
    fireEvent.change(
      screen.getByLabelText('grok の制限時間（秒・0 = タイムアウトなし）'),
      { target: { value: '900' } },
    )
    fireEvent.click(screen.getAllByRole('button', { name: '保存' })[0])

    await waitFor(() => expect(putSettings).toHaveBeenCalled())
    expect(putSettings.mock.calls[0][0]).toMatchObject({
      agent_max_turns: 0,
      canvas_max_turns: 0,
      agent_max_plan_tasks: 0,
      agent_grok_timeout: 900,
    })
  })
})
