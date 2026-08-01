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
    },
  }
})

const getSettings = vi.mocked(api.getSettings)
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
    kie_api_key: '',
    grok_command: 'grok',
    grok_model: 'grok-4.5',
    grok_workdir: '/repo/runtime/grok-workdir',
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
  }
}

function modelRow(): ModelFieldState {
  return {
    key: 'krea2_turbo/30:10.unet_name',
    workflow_id: 'krea2_turbo',
    workflow_label: 'Krea 2 Turbo',
    kind: 'image',
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

/** モデルタブを開いて、1 件だけあるワークフローの折りたたみも開く。 */
async function openModelsTab(options: Options = {} as Options) {
  await openSettings(options)
  screen.getByRole('button', { name: 'モデル' }).click()
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
    screen.getByRole('button', { name: 'モデル' }).click()
    await waitFor(() => screen.getByLabelText('対象の接続先'))
    const select = screen.getByLabelText('対象の接続先') as HTMLSelectElement
    expect(select.value).toBe('runpod')
    // 現在の接続先が分かるように印を付ける
    expect(
      [...select.options].find((option) => option.value === 'runpod')?.text,
    ).toContain('現在の接続先')
  })

  it('環境を切り替えるとその環境の一覧を読み直す', async () => {
    await openSettings()
    screen.getByRole('button', { name: 'モデル' }).click()
    await waitFor(() => screen.getByLabelText('対象の接続先'))
    listModels.mockClear()
    listLoras.mockClear()

    fireEvent.change(screen.getByLabelText('対象の接続先'), {
      target: { value: 'local' },
    })

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
    screen.getByRole('button', { name: 'LoRA 管理' }).click()
    await waitFor(() => screen.getByText('かおり'))

    const [displayName] = screen.getAllByRole('textbox') as HTMLInputElement[]
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

    fireEvent.change(screen.getByLabelText('対象の接続先'), {
      target: { value: 'local' },
    })

    await waitFor(() =>
      expect(
        screen.queryByTitle('ComfyUI のファイル一覧に見つかりません'),
      ).toBeNull(),
    )
  })

  it('ComfyCloud を選ぶとダウンロード関連は出さない', async () => {
    await openModelsTab()
    fireEvent.change(screen.getByLabelText('対象の接続先'), {
      target: { value: 'comfy_cloud' },
    })

    await waitFor(() =>
      expect(screen.queryByRole('button', { name: '全DL' })).toBeNull(),
    )
    expect(screen.getByText(/Comfy Cloud 側の管理/)).toBeTruthy()
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

    const select = screen.getByDisplayValue('ローカル') as HTMLSelectElement
    expect(
      [...select.options].map((option) => [option.value, option.text]),
    ).toEqual([
      ['comfy_cloud', 'ComfyCloud'],
      ['runpod', 'RunPod'],
      ['local', 'ローカル'],
    ])
    // ComfyCloud はエンドポイント固定なので APIキーだけ（URL 欄は出さない）
    expect(screen.getByText('ComfyCloud APIキー')).toBeTruthy()
    expect(screen.getByText(/https:\/\/cloud\.comfy\.org 固定/)).toBeTruthy()
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

    fireEvent.change(screen.getByDisplayValue('ローカル'), {
      target: { value: 'comfy_cloud' },
    })
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

  it('自動起動を有効にすると RunPod の起動設定が出る', async () => {
    await openSettings()

    screen.getByRole('checkbox').click()

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
    expect(screen.getByRole('button', { name: /取得元 URL ✓/ })).toBeTruthy()
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
    const toggle = screen.getByRole('button', { name: /取得元 URL ✓/ })
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
    expect(screen.queryByRole('button', { name: /取得元 URL ✓/ })).toBeNull()
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
    screen.getByRole('button', { name: 'LoRA 管理' }).click()
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

    // フォーム先頭のテキスト入力が「表示名」（label は htmlFor を持たないので順で引く）
    const [displayName] = screen.getAllByRole('textbox') as HTMLInputElement[]
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
