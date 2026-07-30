import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '../api'
import type { ModelFieldState, ModelsDirStatus, Options, Settings } from '../types'
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
    },
  }
})

const getSettings = vi.mocked(api.getSettings)
const listModels = vi.mocked(api.listModels)
const listLoras = vi.mocked(api.listLoras)
const modelsDirStatus = vi.mocked(api.modelsDirStatus)
const listModelDownloads = vi.mocked(api.listModelDownloads)

afterEach(cleanup)

/** 進捗購読の WebSocket は jsdom では張らせず、何もしない偽物に差し替える。 */
class FakeSocket {
  onmessage: ((event: MessageEvent) => void) | null = null
  close() {}
}

function settings(): Settings {
  return {
    comfy_url: 'http://127.0.0.1:8188',
    comfy_api_key: '',
    grok_command: 'grok',
    grok_model: 'grok-4.5',
    grok_workdir: '/repo/runtime/grok-workdir',
    model_overrides: {},
    model_choices: {},
    hf_token: '',
    civitai_api_key: '',
    model_download_urls: {},
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

function dirStatus(overrides: Partial<ModelsDirStatus> = {}): ModelsDirStatus {
  return { configured: false, exists: false, writable: false, path: '', ...overrides }
}

/** 設定ページを描画し、dir-status を読み終えるまで待つ。 */
async function openSettings() {
  render(
    <SettingsPage options={{} as Options} onBack={() => {}} onChanged={() => {}} />,
  )
  await waitFor(() => expect(modelsDirStatus).toHaveBeenCalled())
  await waitFor(() => screen.getByText('ComfyUI URL'))
}

/** モデルタブを開いて、1 件だけあるワークフローの折りたたみも開く。 */
async function openModelsTab() {
  await openSettings()
  screen.getByRole('button', { name: 'モデル' }).click()
  await waitFor(() => screen.getByText(/Krea 2 Turbo/))
  screen.getByText(/Krea 2 Turbo/).click()
  await waitFor(() => screen.getByText('ノード'))
}

describe('SettingsPage: 不足モデルのダウンロード UI（COMFY_MODELS_DIR ゲート）', () => {
  beforeEach(() => {
    vi.stubGlobal('WebSocket', FakeSocket)
    getSettings.mockResolvedValue(settings())
    listModels.mockResolvedValue([modelRow()])
    listLoras.mockResolvedValue([])
    listModelDownloads.mockResolvedValue([])
  })

  it('環境変数が無ければ接続タブのダウンロード関連ブロックを出さない', async () => {
    modelsDirStatus.mockResolvedValue(dirStatus())
    await openSettings()

    expect(screen.queryByText('モデル自動ダウンロード')).toBeNull()
    expect(screen.queryByText(/Hugging Face トークン/)).toBeNull()
    expect(screen.queryByText(/Civitai/)).toBeNull()
    expect(screen.queryByText(/COMFY_MODELS_DIR が設定されていません/)).toBeNull()
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

  it('環境変数が無ければモデルタブの DL 列を出さず、警告も出さない', async () => {
    modelsDirStatus.mockResolvedValue(dirStatus())
    await openModelsTab()

    expect(screen.queryByText('ダウンロード（不足時）')).toBeNull()
    expect(screen.queryByRole('button', { name: 'DL' })).toBeNull()
    expect(screen.queryByText(/COMFY_MODELS_DIR が設定されていません/)).toBeNull()
    // 代わりに設定方法の案内だけを控えめに出す
    expect(screen.getByText(/自動ダウンロードを使う場合は/)).toBeTruthy()
  })

  it('環境変数があればモデルタブに DL 列を出す（使えない状態なら理由つきで無効）', async () => {
    modelsDirStatus.mockResolvedValue(
      dirStatus({ configured: true, path: '/comfy/models' }),
    )
    await openModelsTab()

    expect(screen.getByText('ダウンロード（不足時）')).toBeTruthy()
    const button = screen.getByRole('button', { name: 'DL' })
    expect(button.hasAttribute('disabled')).toBe(true)
    expect(button.getAttribute('title')).toMatch(/パスが見つかりません/)
    expect(screen.getByText(/パスが見つかりません/)).toBeTruthy()
  })
})
