import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from '../api'
import { DEFAULT_MEGAPIXELS, initialForm, type FormState } from '../form'
import type {
  ComfyTarget,
  Job,
  LibraryItem,
  LibraryQuery,
  Lora,
  Options,
} from '../types'
import GenerateForm from './GenerateForm'

// ライブラリのモーダルは自分で GET /api/library を叩くので、素材はここから返す。
vi.mock('../api', () => ({
  api: {
    listLibrary: vi.fn(),
    uploadToLibrary: vi.fn(),
    updateLibraryItem: vi.fn(),
    deleteLibraryItem: vi.fn(),
    createLibrarySheet: vi.fn(),
    uploadImage: vi.fn(),
    uploadVideo: vi.fn(),
    uploadAudio: vi.fn(),
  },
}))

afterEach(cleanup)

function lora(
  id: number,
  name: string,
  target: Lora['target'],
  family = 'krea2',
): Lora {
  return {
    id,
    display_name: name,
    lora_name: `${name}.safetensors`,
    trigger_word: name,
    default_strength: 1,
    default_audio: null,
    sort_order: 0,
    target,
    family,
    sample_images: [],
  }
}

const OPTIONS: Options = {
  comfy_connected: true,
  comfy_error: null,
  comfy_target: 'local',
  comfy_url: '',
  image_workflows: [],
  video_workflows: [],
  audio_workflows: [],
  default_video_workflow: 'minimax_h3_i2v',
  default_image_workflow: 'krea2_turbo',
  default_audio_workflow: 'ace_step1_5_xl_sft',
  audio_categories: [],
  keyscales: [],
  languages: [],
  aspect_ratios: [],
  lora_files: [],
  library: [],
  model_slots: [],
  model_files: {},
  loras: [lora(1, 'サクラ', 'image'), lora(2, 'スローモ', 'video')],
  audio_assets: [],
  image_assets: [],
  video_assets: [],
  negative_presets: {},
}

function show(
  form: Partial<FormState> = {},
  comfyTarget: ComfyTarget | null = 'local',
) {
  const patch = vi.fn()
  const onComfyTarget = vi.fn()
  render(
    <GenerateForm
      form={{ ...initialForm, ...form }}
      patch={patch}
      options={OPTIONS}
      optionsError={null}
      onReloadOptions={() => {}}
      onOpenChat={() => {}}
      onSubmit={() => {}}
      submitting={false}
      fieldErrors={{}}
      comfyTarget={comfyTarget}
      onComfyTarget={onComfyTarget}
      jobs={[]}
      showNsfw={false}
    />,
  )
  return { patch, onComfyTarget }
}

/** The <section> whose heading is `title`. */
function section(title: string): HTMLElement {
  const heading = screen.getByText(title)
  const element = heading.closest('section')
  if (!element) throw new Error(`no section for ${title}`)
  return element
}

/** Open the LoRA candidate modal belonging to the requested stage. */
function openLoraPicker(title: string): void {
  fireEvent.click(
    within(section(title)).getByRole('button', { name: 'LoRAを選ぶ' }),
  )
  expect(screen.getByText('LoRAを選択')).toBeTruthy()
}

function closeLoraPicker(): void {
  fireEvent.click(screen.getByRole('button', { name: '選択を完了' }))
}

describe('GenerateForm の接続先プルダウン', () => {
  it('3 つの接続先を出し、現在の選択を反映する', () => {
    show({}, 'runpod')
    const select = screen.getByLabelText('接続先') as HTMLSelectElement

    expect(
      [...select.options].map((option) => [option.value, option.text]),
    ).toEqual([
      ['comfy_cloud', 'ComfyCloud'],
      ['runpod', 'RunPod'],
      ['local', 'ローカル'],
    ])
    expect(select.value).toBe('runpod')
  })

  it('選び直すと保存を頼む（サーバー側の設定に載る）', () => {
    const { onComfyTarget } = show()
    const select = screen.getByLabelText('接続先') as HTMLSelectElement

    fireEvent.change(select, { target: { value: 'comfy_cloud' } })

    expect(onComfyTarget).toHaveBeenCalledWith('comfy_cloud')
  })

  it('設定を読み込むまでは触れない', () => {
    show({}, null)

    expect((screen.getByLabelText('接続先') as HTMLSelectElement).disabled).toBe(
      true,
    )
  })
})

describe('GenerateForm の LoRA セクション', () => {
  it('画像用と動画用をそれぞれの対象で絞り込む', () => {
    show()
    openLoraPicker('LoRA（画像）')
    expect(screen.getByRole('button', { name: 'サクラ' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'スローモ' })).toBeNull()
    closeLoraPicker()

    openLoraPicker('LoRA（動画）')
    expect(screen.getByRole('button', { name: 'スローモ' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'サクラ' })).toBeNull()
  })

  it('候補は常時並べず、モーダル内で検索できる', () => {
    show()
    expect(
      within(section('LoRA（画像）')).queryByRole('button', { name: 'サクラ' }),
    ).toBeNull()

    openLoraPicker('LoRA（画像）')
    const search = screen.getByPlaceholderText('LoRAを検索')
    fireEvent.change(search, { target: { value: '見つからない名前' } })
    expect(screen.queryByRole('button', { name: 'サクラ' })).toBeNull()
    expect(screen.getByText('条件に一致するLoRAがありません')).toBeTruthy()

    fireEvent.change(search, { target: { value: 'サクラ' } })
    expect(screen.getByRole('button', { name: 'サクラ' })).toBeTruthy()
  })

  it('モーダルを開き直すと検索語はリセットされる', () => {
    show()
    openLoraPicker('LoRA（画像）')
    fireEvent.change(screen.getByPlaceholderText('LoRAを検索'), {
      target: { value: '見つからない名前' },
    })
    expect(screen.getByText('条件に一致するLoRAがありません')).toBeTruthy()
    closeLoraPicker()

    openLoraPicker('LoRA（画像）')
    const search = screen.getByPlaceholderText('LoRAを検索') as HTMLInputElement
    expect(search.value).toBe('')
    expect(screen.getByRole('button', { name: 'サクラ' })).toBeTruthy()
    expect(screen.getByText('表示 1 / 全 1')).toBeTruthy()
  })

  it('対象の登録が無ければその旨を出す', () => {
    render(
      <GenerateForm
        form={initialForm}
        patch={vi.fn()}
        options={{ ...OPTIONS, loras: [] }}
        optionsError={null}
        onReloadOptions={() => {}}
        onOpenChat={() => {}}
        onSubmit={() => {}}
        submitting={false}
        fieldErrors={{}}
        comfyTarget="local"
        onComfyTarget={() => {}}
        jobs={[]}
        showNsfw={false}
      />,
    )
    expect(screen.getAllByText(/画像用の登録済み LoRA がありません/).length).toBe(1)
    expect(screen.getAllByText(/動画用の登録済み LoRA がありません/).length).toBe(1)
  })

  it('動画生成モードでは画像 LoRA のセクションごと消す', () => {
    show({ mode: 'i2v' })
    expect(screen.queryByText('LoRA（画像）')).toBeNull()
    openLoraPicker('LoRA（動画）')
    expect(screen.getByRole('button', { name: 'スローモ' })).toBeTruthy()
  })

  it('画像のみモードでは動画 LoRA のセクションごと消す', () => {
    show({ mode: 'image_only' })
    expect(screen.queryByText('LoRA（動画）')).toBeNull()
    openLoraPicker('LoRA（画像）')
    expect(screen.getByRole('button', { name: 'サクラ' })).toBeTruthy()
  })

  it('動画 LoRA を選ぶと動画側のトリガーだけが連結される', () => {
    const { patch } = show()
    openLoraPicker('LoRA（動画）')
    const video = screen.getByRole('button', { name: 'スローモ' })
    video.click()
    expect(patch).toHaveBeenCalledWith(
      expect.objectContaining({ videoTriggerText: 'スローモ' }),
    )
    expect(patch.mock.calls[0][0]).not.toHaveProperty('triggerText')
  })
})

// --------------------------------------------------------- 画像ワークフロー

const IMAGE_WORKFLOWS: Options['image_workflows'] = [
  {
    id: 'krea2_turbo',
    label: 'Krea 2 turbo',
    kind: 'image',
    family: 'krea2',
    notes: '',
    requires: [],
    supports: ['aspect_ratio', 'megapixels', 'prompt', 'seed'],
    accepts_start_image: false,
    image_label: '開始フレーム',
    selects: [],
    prompt_required: true,
    accepts_video_loras: true,
    min_duration: 0,
    max_duration: 0,
    default_duration: 0,
  },
  {
    id: 'qwen_image_edit_2511',
    label: 'Qwen-Image Edit 2511',
    kind: 'image',
    family: 'qwen-image',
    notes: '',
    requires: ['image'],
    supports: ['image', 'prompt', 'seed'],
    accepts_start_image: false,
    image_label: '編集元画像',
    selects: [],
    prompt_required: true,
    accepts_video_loras: true,
    min_duration: 0,
    max_duration: 0,
    default_duration: 0,
  },
  // ComfyUI を使わない外部バックエンド（Grok Build CLI、SPEC §5.2）
  {
    id: 'grok_imagine_t2i',
    label: 'Grok Imagine 画像生成（サブスク CLI）',
    mode_label: 'テキスト→画像',
    family_label: 'Grok Imagine（サブスク CLI）',
    kind: 'image',
    family: 'grok-imagine',
    backend: 'grok_cli',
    notes: '',
    requires: [],
    supports: ['aspect_ratio', 'prompt'],
    accepts_start_image: false,
    image_label: '開始フレーム',
    selects: [],
    prompt_required: true,
    accepts_video_loras: false,
    min_duration: 0,
    max_duration: 0,
    default_duration: 0,
  },
  {
    id: 'grok_imagine_edit',
    label: 'Grok Imagine 画像編集（サブスク CLI）',
    mode_label: '画像編集',
    family_label: 'Grok Imagine（サブスク CLI）',
    kind: 'image',
    family: 'grok-imagine',
    backend: 'grok_cli',
    notes: '',
    requires: ['image'],
    supports: ['image', 'prompt'],
    accepts_start_image: false,
    image_label: '編集元画像',
    selects: [],
    prompt_required: true,
    accepts_video_loras: false,
    min_duration: 0,
    max_duration: 0,
    default_duration: 0,
  },
]

function showImages(form: Partial<FormState> = {}) {
  const patch = vi.fn()
  render(
    <GenerateForm
      form={{ ...initialForm, ...form }}
      patch={patch}
      options={{
        ...OPTIONS,
        image_workflows: IMAGE_WORKFLOWS,
        loras: [
          lora(1, 'サクラ', 'image', 'krea2'),
          lora(3, 'ハナ', 'image', 'qwen-image'),
          lora(2, 'スローモ', 'video'),
        ],
      }}
      optionsError={null}
      onReloadOptions={() => {}}
      onOpenChat={() => {}}
      onSubmit={() => {}}
      submitting={false}
      fieldErrors={{}}
      comfyTarget="local"
      onComfyTarget={() => {}}
      jobs={[]}
      showNsfw={false}
    />,
  )
  return { patch }
}

describe('GenerateForm の画像ワークフロー', () => {
  it('画像ステージがあるモードでのみセレクトを出す', () => {
    showImages()
    expect(section('画像ワークフロー')).toBeTruthy()
    cleanup()
    showImages({ mode: 'i2v' })
    expect(screen.queryByText('画像ワークフロー')).toBeNull()
  })

  it('選択中ワークフローのファミリーの画像 LoRA だけを出す', () => {
    showImages()
    openLoraPicker('LoRA（画像）')
    expect(screen.getByRole('button', { name: 'サクラ' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'ハナ' })).toBeNull()

    cleanup()
    showImages({ imageWorkflow: 'qwen_image_edit_2511' })
    openLoraPicker('LoRA（画像）')
    expect(screen.getByRole('button', { name: 'ハナ' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'サクラ' })).toBeNull()
  })

  it('編集系を選ぶと参照画像ピッカーが必須表示になる', () => {
    showImages({ imageWorkflow: 'qwen_image_edit_2511', mode: 'image_only' })
    expect(section('編集元画像')).toBeTruthy()
    expect(
      screen.getAllByText(/入力画像を編集するワークフロー/).length,
    ).toBeGreaterThan(0)
  })

  it('テキスト生成系では参照画像ピッカーを出さない', () => {
    showImages({ mode: 'image_only' })
    expect(screen.queryByText('編集元画像')).toBeNull()
    expect(screen.queryByText('開始フレーム')).toBeNull()
  })

  it('Grok Imagine もモデルとして選べる（ComfyUI 非依存、SPEC §5.2）', () => {
    showImages({ mode: 'image_only' })
    const models = screen.getByLabelText('画像モデル') as HTMLSelectElement
    expect([...models.options].map((item) => item.textContent)).toContain(
      'Grok Imagine（サブスク CLI）',
    )
  })

  it('Grok Imagine の編集モードは編集元画像を必須表示にする', () => {
    showImages({ imageWorkflow: 'grok_imagine_edit', mode: 'image_only' })
    expect(section('編集元画像')).toBeTruthy()

    cleanup()
    // テキスト→画像のほうは入力画像を取らない
    showImages({ imageWorkflow: 'grok_imagine_t2i', mode: 'image_only' })
    expect(screen.queryByText('編集元画像')).toBeNull()
  })

  it('LoRA を挿せない画像ワークフローでは LoRA の欄を出さない', () => {
    // Grok Imagine は ComfyUI のグラフを持たない（= lora_chain が無い）ので、
    // 出しても「設定 → LoRA 管理で追加」という誤った案内になるだけ。
    showImages({ imageWorkflow: 'grok_imagine_t2i', mode: 'image_only' })
    expect(screen.queryByText('LoRA（画像）')).toBeNull()

    cleanup()
    // ComfyUI のワークフローではこれまでどおり出る
    showImages({ mode: 'image_only' })
    expect(section('LoRA（画像）')).toBeTruthy()
  })

  it('Grok Imagine の編集モードでは解像度の欄を出さない', () => {
    showImages({ imageWorkflow: 'grok_imagine_edit', mode: 'image_only' })
    expect(screen.queryByText('メガピクセル')).toBeNull()
  })
})

// ------------------------------------------------- ワークフローの 2 段プルダウン
// 1 段目でモデル（ファミリー）、2 段目でそのモデルのモードを選ぶ。フォームが持つ
// のはワークフロー id だけなので、id を入れれば両方のセレクトが揃う。

const VIDEO_WORKFLOWS: Options['video_workflows'] = [
  {
    id: 'wan22_t2v',
    label: 'テキスト→動画 (t2v)',
    mode_label: 'テキスト→動画 (t2v)',
    family_label: 'Wan 2.2',
    kind: 'video',
    family: 'wan',
    notes: '',
    requires: [],
    supports: ['prompt', 'duration', 'fps'],
    accepts_start_image: false,
    image_label: '開始フレーム',
    selects: [],
    prompt_required: true,
    accepts_video_loras: true,
    min_duration: 0,
    max_duration: 0,
    default_duration: 0,
  },
  {
    id: 'minimax_h3_t2v',
    default_megapixels: 0.4,
    label: 'テキスト→動画・音声つき (MiniMax H3 t2v)',
    mode_label: 'テキスト→動画・音声つき (t2v)',
    family_label: 'MiniMax H3',
    kind: 'video',
    family: 'minimax-h3',
    notes: '',
    requires: [],
    supports: ['prompt', 'duration', 'fps'],
    accepts_start_image: false,
    image_label: '開始フレーム',
    selects: [],
    prompt_required: true,
    accepts_video_loras: false,
    min_duration: 0,
    max_duration: 0,
    default_duration: 0,
  },
  {
    id: 'minimax_h3_i2v',
    default_megapixels: 0.4,
    label: '画像→動画・音声つき (MiniMax H3 i2v)',
    mode_label: '画像→動画・音声つき (i2v)',
    family_label: 'MiniMax H3',
    kind: 'video',
    family: 'minimax-h3',
    notes: '',
    requires: ['image'],
    supports: ['prompt', 'image', 'duration', 'fps', 'steps'],
    accepts_start_image: true,
    image_label: '開始フレーム',
    selects: [],
    prompt_required: true,
    accepts_video_loras: false,
    min_duration: 0,
    max_duration: 0,
    default_duration: 0,
  },
]

function showVideos(form: Partial<FormState> = {}) {
  const patch = vi.fn()
  render(
    <GenerateForm
      form={{ ...initialForm, ...form }}
      patch={patch}
      options={{ ...OPTIONS, video_workflows: VIDEO_WORKFLOWS }}
      optionsError={null}
      onReloadOptions={() => {}}
      onOpenChat={() => {}}
      onSubmit={() => {}}
      submitting={false}
      fieldErrors={{}}
      comfyTarget="local"
      onComfyTarget={() => {}}
      jobs={[]}
      showNsfw={false}
    />,
  )
  return { patch }
}

describe('GenerateForm のワークフロー選択（モデル → モード）', () => {
  it('モデルは家族ごとに 1 つ、モードは選択中モデルのぶんだけ出す', () => {
    showVideos({ mode: 'i2v', videoWorkflow: 'minimax_h3_t2v' })
    const models = screen.getByLabelText('動画モデル') as HTMLSelectElement
    expect([...models.options].map((item) => item.textContent)).toEqual([
      'Wan 2.2',
      'MiniMax H3',
    ])
    const modes = screen.getByLabelText('動画モード') as HTMLSelectElement
    expect([...modes.options].map((item) => item.textContent)).toEqual([
      'テキスト→動画・音声つき (t2v)',
      '画像→動画・音声つき (i2v)',
    ])
  })

  it('ワークフロー id を入れると両方のセレクトが揃う', () => {
    showVideos({ mode: 'i2v', videoWorkflow: 'minimax_h3_i2v' })
    expect((screen.getByLabelText('動画モデル') as HTMLSelectElement).value).toBe(
      'minimax-h3',
    )
    expect((screen.getByLabelText('動画モード') as HTMLSelectElement).value).toBe(
      'minimax_h3_i2v',
    )
  })

  it('モデルを変えるとそのモデルの先頭モードへ切り替える', () => {
    const { patch } = showVideos({ mode: 'i2v', videoWorkflow: 'wan22_t2v' })
    fireEvent.change(screen.getByLabelText('動画モデル'), {
      target: { value: 'minimax-h3' },
    })
    expect(patch).toHaveBeenCalledWith({ videoWorkflow: 'minimax_h3_t2v' })
  })

  it('モードを変えるとそのワークフロー id を patch する', () => {
    const { patch } = showVideos({ mode: 'i2v', videoWorkflow: 'minimax_h3_t2v' })
    fireEvent.change(screen.getByLabelText('動画モード'), {
      target: { value: 'minimax_h3_i2v' },
    })
    expect(patch).toHaveBeenCalledWith({ videoWorkflow: 'minimax_h3_i2v' })
  })

  it('モデルが想定する解像度をメガピクセルの既定にする（SPEC §3.1）', () => {
    // MiniMax H3 は 0.4MP 前提（1.0MP のままだと VRAM が足りない）
    const { patch } = showVideos({
      mode: 'i2v',
      videoWorkflow: 'minimax_h3_i2v',
      megapixels: 1,
    })
    expect(patch).toHaveBeenCalledWith({ megapixels: 0.4 })
  })

  it('宣言の無いモデルではグローバル既定に戻す', () => {
    const { patch } = showVideos({
      mode: 'i2v',
      videoWorkflow: 'wan22_t2v',
      megapixels: 1,
    })
    expect(patch).toHaveBeenCalledWith({ megapixels: DEFAULT_MEGAPIXELS })
  })

  it('すでにそのモデルの既定ならフォームを触らない', () => {
    const { patch } = showVideos({
      mode: 'i2v',
      videoWorkflow: 'minimax_h3_i2v',
      megapixels: 0.4,
    })
    expect(patch).not.toHaveBeenCalledWith({ megapixels: expect.anything() })
  })

  it('動画ステージが走らないモードでは触らない', () => {
    const { patch } = showVideos({
      mode: 'image_only',
      videoWorkflow: 'minimax_h3_i2v',
      megapixels: 1,
    })
    expect(patch).not.toHaveBeenCalledWith({ megapixels: expect.anything() })
  })

  it('画像＋動画では開始フレームを取れるモードだけが並ぶ', () => {
    showVideos({ mode: 'full', videoWorkflow: 'minimax_h3_i2v' })
    const models = screen.getByLabelText('動画モデル') as HTMLSelectElement
    // t2v しか無い Wan はモデルごと消える
    expect([...models.options].map((item) => item.value)).toEqual(['minimax-h3'])
    const modes = screen.getByLabelText('動画モード') as HTMLSelectElement
    expect([...modes.options].map((item) => item.value)).toEqual([
      'minimax_h3_i2v',
    ])
    expect(modes.disabled).toBe(true)
  })
})

// --------------------------------------------------------------- 音声モード
// 音声はモードタブの一つ。選ぶと画像・動画のセクションは丸ごと消え、選択中の
// 音声ワークフローが読むつまみだけが出る。

const AUDIO_WORKFLOWS: Options['audio_workflows'] = [
  {
    id: 'ace_step1_5_xl_sft',
    label: 'ACE-Step 1.5 XL',
    kind: 'audio',
    family: 'ace-step',
    notes: 'acestep_v1.5_xl_sft',
    requires: [],
    supports: ['prompt', 'lyrics', 'duration', 'bpm', 'keyscale', 'language', 'seed'],
    accepts_start_image: false,
    image_label: '開始フレーム',
    selects: [],
    prompt_required: true,
    accepts_video_loras: true,
    min_duration: 10,
    max_duration: 600,
    default_duration: 120,
  },
  {
    id: 'stable_audio_3_medium_base',
    label: 'Stable Audio 3 Medium',
    kind: 'audio',
    family: 'stable-audio',
    notes: '',
    requires: [],
    supports: [
      'prompt',
      'duration',
      'audio_category',
      'reprompt',
      'seed',
      'steps',
    ],
    accepts_start_image: false,
    image_label: '開始フレーム',
    selects: [],
    prompt_required: true,
    accepts_video_loras: true,
    min_duration: 1,
    max_duration: 380,
    default_duration: 60,
  },
]

function showAudio(form: Partial<FormState> = {}, options: Options | null = null) {
  const patch = vi.fn()
  const onOpenChat = vi.fn()
  render(
    <GenerateForm
      form={{ ...initialForm, mode: 'audio', ...form }}
      patch={patch}
      options={
        options ?? {
          ...OPTIONS,
          audio_workflows: AUDIO_WORKFLOWS,
          audio_categories: ['Music', 'Instrument', 'SFX', 'One-shot'],
          keyscales: ['C major', 'F# minor'],
          languages: ['ja', 'en', 'unknown'],
        }
      }
      optionsError={null}
      onReloadOptions={() => {}}
      onOpenChat={onOpenChat}
      onSubmit={() => {}}
      submitting={false}
      fieldErrors={{}}
      comfyTarget="local"
      onComfyTarget={() => {}}
      jobs={[]}
      showNsfw={false}
    />,
  )
  return { patch, onOpenChat }
}

describe('GenerateForm の音声モード', () => {
  it('モードタブに「音声」が並ぶ', () => {
    show()
    expect(screen.getByText('音声')).toBeTruthy()
    // 連結モードの表示名はユーザー確定の「画像＋動画」
    expect(screen.getByText('画像＋動画')).toBeTruthy()
  })

  it('画像・動画のセクションを丸ごと隠す', () => {
    showAudio()
    expect(screen.queryByText('動画ワークフロー')).toBeNull()
    expect(screen.queryByText('画像ワークフロー')).toBeNull()
    expect(screen.queryByText('LoRA（画像）')).toBeNull()
    expect(screen.queryByText('LoRA（動画）')).toBeNull()
    expect(screen.queryByText('解像度')).toBeNull()
    expect(screen.queryByText('リファレンス音声')).toBeNull()
    expect(screen.queryByText('動画ネガティブ')).toBeNull()
    expect(screen.queryByText('画像プロンプト')).toBeNull()
    expect(screen.queryByText('動画プロンプト')).toBeNull()
  })

  it('ACE-Step では歌詞・BPM・キー・言語が出て、カテゴリは出ない', () => {
    showAudio({ audioWorkflow: 'ace_step1_5_xl_sft' })
    expect(screen.getByLabelText('歌詞')).toBeTruthy()
    expect(screen.getByLabelText('BPM')).toBeTruthy()
    expect(screen.getByLabelText('キー / スケール')).toBeTruthy()
    expect(screen.getByLabelText('歌詞の言語')).toBeTruthy()
    expect(screen.queryByLabelText('カテゴリ')).toBeNull()
    expect(screen.queryByText(/内蔵 LLM でプロンプトを展開/)).toBeNull()
  })

  it('Stable Audio ではカテゴリとリプロンプトが出て、歌詞は出ない', () => {
    showAudio({ audioWorkflow: 'stable_audio_3_medium_base', audioDuration: 60 })
    expect(screen.getByLabelText('カテゴリ')).toBeTruthy()
    expect(screen.getByText(/内蔵 LLM でプロンプトを展開/)).toBeTruthy()
    expect(screen.queryByLabelText('歌詞')).toBeNull()
    expect(screen.queryByLabelText('BPM')).toBeNull()
    expect(screen.queryByLabelText('キー / スケール')).toBeNull()
  })

  it('秒数の入力にモデルの上下限が入る', () => {
    showAudio({ audioWorkflow: 'stable_audio_3_medium_base', audioDuration: 60 })
    const duration = screen.getByLabelText(/長さ（秒）/) as HTMLInputElement
    expect(duration.min).toBe('1')
    expect(duration.max).toBe('380')
  })

  it('モデルを選び直すと、そのモデルの先頭モードへ patch される', () => {
    const { patch } = showAudio({ audioWorkflow: 'ace_step1_5_xl_sft' })
    fireEvent.change(screen.getByLabelText('音声モデル'), {
      target: { value: 'stable-audio' },
    })
    expect(patch).toHaveBeenCalledWith({ audioWorkflow: 'stable_audio_3_medium_base' })
  })

  it('モデルが 1 モードだけのときはモードのセレクトを無効にする', () => {
    showAudio({ audioWorkflow: 'ace_step1_5_xl_sft' })
    const mode = screen.getByLabelText('音声モード') as HTMLSelectElement
    expect(mode.disabled).toBe(true)
    expect(mode.value).toBe('ace_step1_5_xl_sft')
  })

  it('範囲外の秒数のままワークフローを切り替えると既定へ寄せる', () => {
    const { patch } = showAudio({
      audioWorkflow: 'stable_audio_3_medium_base',
      audioDuration: 600,
    })
    expect(patch).toHaveBeenCalledWith({ audioDuration: 60 })
  })

  it('Grokで生成 から音声モードでもチャットを開ける', () => {
    const { onOpenChat } = showAudio()
    fireEvent.click(screen.getByText('Grokで生成'))
    expect(onOpenChat).toHaveBeenCalled()
  })

  it('選択肢が来る前はワークフロー id を手入力できる', () => {
    showAudio({}, { ...OPTIONS, audio_workflows: [] })
    const input = screen.getByLabelText('音声ワークフロー') as HTMLInputElement
    expect(input.tagName).toBe('INPUT')
  })
})

// --------------------------------------------------- 使わない項目は描画しない
// 無効化（グレーアウト）ではなくセクション/フィールドごと消す。値は FormState に
// 残るので、使うモードに戻せば入力はそのまま復元される。

describe('GenerateForm は使わない項目を出さない', () => {
  it('動画生成モードでは画像側のセクションを消す', () => {
    show({ mode: 'i2v' })
    expect(screen.queryByText('画像ワークフロー')).toBeNull()
    expect(screen.queryByText('LoRA（画像）')).toBeNull()
    expect(screen.queryByText('画像プロンプト')).toBeNull()
    // 動画側は今までどおり出る
    expect(screen.getByText('動画ワークフロー')).toBeTruthy()
    expect(screen.getByText('動画プロンプト')).toBeTruthy()
  })

  it('画像のみモードでは動画側のセクションを消す', () => {
    show({ mode: 'image_only' })
    expect(screen.queryByText('動画ワークフロー')).toBeNull()
    expect(screen.queryByText('LoRA（動画）')).toBeNull()
    expect(screen.queryByText('動画プロンプト')).toBeNull()
    expect(screen.queryByText('動画ネガティブ')).toBeNull()
    expect(screen.queryByText('リファレンス音声')).toBeNull()
    // 秒数 / fps は動画ステージのものなので消える。seed は残るのでセクションは残す
    expect(screen.queryByText('秒数（上限なし）')).toBeNull()
    expect(screen.queryByText('fps')).toBeNull()
    expect(screen.getByText('出力設定')).toBeTruthy()
    expect(screen.getByText('seed 固定')).toBeTruthy()
  })

  it('音声入力を取らない動画ワークフローではリファレンス音声を出さない', () => {
    const options = {
      ...OPTIONS,
      video_workflows: [
        {
          id: 'wan22_t2v',
          label: 'テキスト→動画',
          kind: 'video' as const,
          family: 'wan',
          notes: '',
          requires: [],
          supports: ['prompt', 'negative', 'duration', 'fps'],
          accepts_start_image: false,
          image_label: '開始フレーム',
          selects: [],
          prompt_required: true,
          accepts_video_loras: true,
          min_duration: 0,
          max_duration: 0,
          default_duration: 0,
        },
      ],
    }
    render(
      <GenerateForm
        form={{ ...initialForm, mode: 'i2v', videoWorkflow: 'wan22_t2v' }}
        patch={vi.fn()}
        options={options}
        optionsError={null}
        onReloadOptions={() => {}}
        onOpenChat={() => {}}
        onSubmit={() => {}}
        submitting={false}
        fieldErrors={{}}
        comfyTarget="local"
        onComfyTarget={() => {}}
        jobs={[]}
        showNsfw={false}
      />,
    )
    expect(screen.queryByText('リファレンス音声')).toBeNull()
    // 開始フレームを取らないワークフローなのでピッカーも出ない
    expect(screen.queryByText('開始フレーム')).toBeNull()
  })

  it('編集系ワークフロー + 画像のみでは解像度セクションを消す', () => {
    showImages({ mode: 'image_only', imageWorkflow: 'qwen_image_edit_2511' })
    expect(screen.queryByText('解像度')).toBeNull()
    // 画像＋動画なら動画側に効くので残る
    cleanup()
    showImages({ mode: 'full', imageWorkflow: 'qwen_image_edit_2511' })
    expect(screen.getByText('解像度')).toBeTruthy()
  })

  it('消えた項目の値は保持され、モードを戻すと復元される', () => {
    // image_only では動画プロンプトが消えるが、値は FormState に残ったまま
    const form = { videoPrompt: 'a clip', imagePrompt: 'a still' }
    show({ ...form, mode: 'image_only' })
    expect(screen.queryByDisplayValue('a clip')).toBeNull()
    cleanup()
    show({ ...form, mode: 'full' })
    expect(screen.getByDisplayValue('a clip')).toBeTruthy()
    expect(screen.getByDisplayValue('a still')).toBeTruthy()
  })

  it('残っている入力はグレーアウトされていない', () => {
    show({ mode: 'i2v' })
    const video = screen.getByPlaceholderText(/1 段落 4〜8 文/) as HTMLTextAreaElement
    expect(video.disabled).toBe(false)
  })
})

describe('GenerateForm の使用モデル選択（SPEC §3.3）', () => {
  const IMAGE_SLOT = {
    key: 'krea2_turbo/30:10.unet_name',
    workflow_id: 'krea2_turbo',
    workflow_label: 'Krea 2',
    kind: 'image' as const,
    node_id: '30:10',
    field: 'unet_name',
    class_type: 'UNETLoader',
    label: 'Load Diffusion Model',
    default: 'base.safetensors',
    choices: ['base.safetensors', 'alt.safetensors'],
  }

  function showWithSlots(form: Partial<FormState> = {}) {
    const patch = vi.fn()
    render(
      <GenerateForm
        form={{ ...initialForm, imageWorkflow: 'krea2_turbo', ...form }}
        patch={patch}
        options={{ ...OPTIONS, model_slots: [IMAGE_SLOT] }}
        optionsError={null}
        onReloadOptions={() => {}}
        onOpenChat={() => {}}
        onSubmit={() => {}}
        submitting={false}
        fieldErrors={{}}
        comfyTarget="local"
        onComfyTarget={() => {}}
        jobs={[]}
        showNsfw={false}
      />,
    )
    return { patch }
  }

  it('候補のあるスロットだけセレクトを出し、選択を patch する', () => {
    const { patch } = showWithSlots()
    const select = screen.getByLabelText(
      '使用モデル: Load Diffusion Model',
    ) as HTMLSelectElement
    expect([...select.options].map((option) => option.value)).toEqual([
      'base.safetensors',
      'alt.safetensors',
    ])
    fireEvent.change(select, { target: { value: 'alt.safetensors' } })
    expect(patch).toHaveBeenCalledWith({
      modelOverrides: { 'krea2_turbo/30:10.unet_name': 'alt.safetensors' },
    })
  })

  it('候補が無ければ何も出さない', () => {
    show()
    expect(screen.queryByText(/使用モデル:/)).toBeNull()
  })

  it('画像ステージを走らせないモードでは画像側のセレクトを出さない', () => {
    showWithSlots({ mode: 'i2v' })
    expect(screen.queryByText(/使用モデル:/)).toBeNull()
  })
})

describe('GenerateForm の「履歴から選択」', () => {
  /** flf2v 相当: 開始フレーム・最後のフレーム・参照動画・音声をすべて要求する。 */
  const EVERY_INPUT: Options['video_workflows'] = [
    {
      id: 'all_inputs',
      label: 'すべての入力',
      kind: 'video',
      family: 'wan',
      notes: '',
      requires: ['image', 'end_image', 'video', 'audio'],
      supports: ['prompt', 'negative', 'duration', 'fps'],
      accepts_start_image: true,
      image_label: '最初のフレーム',
      selects: [],
      prompt_required: true,
      accepts_video_loras: true,
      min_duration: 0,
      max_duration: 0,
      default_duration: 0,
    },
  ]

  const HISTORY_JOB: Job = {
    id: 'j1',
    created_at: '2026-07-30T10:00:00+00:00',
    mode: 'full',
    status: 'done',
    user_input: null,
    image_prompt: 'a still',
    video_prompt: 'a clip',
    audio_prompt: null,
    grok_raw: null,
    params: {},
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
  }

  function showInputs() {
    render(
      <GenerateForm
        form={{ ...initialForm, mode: 'i2v', videoWorkflow: 'all_inputs' }}
        patch={vi.fn()}
        options={{ ...OPTIONS, video_workflows: EVERY_INPUT }}
        optionsError={null}
        onReloadOptions={() => {}}
        onOpenChat={() => {}}
        onSubmit={() => {}}
        submitting={false}
        fieldErrors={{}}
        comfyTarget="local"
        onComfyTarget={() => {}}
        jobs={[HISTORY_JOB]}
        showNsfw={false}
      />,
    )
  }

  it('画像・動画・音声の入力すべてにボタンを出す', () => {
    showInputs()
    for (const title of [
      '最初のフレーム',
      '最後のフレーム',
      '参照動画（モーション転写）',
      'リファレンス音声',
    ]) {
      expect(
        within(section(title)).getByRole('button', { name: '履歴から選択' }),
      ).toBeTruthy()
    }
  })

  it('最後のフレームのボタンから画像候補のモーダルが開く', () => {
    showInputs()
    fireEvent.click(
      within(section('最後のフレーム')).getByRole('button', { name: '履歴から選択' }),
    )
    expect(screen.getByText('履歴から選択: 最後のフレーム')).toBeTruthy()
    // 画像入力なので生成画像とラストフレームの 2 件（動画は出ない）
    expect(screen.getByText('2 件')).toBeTruthy()
    expect(screen.getByText('ラストフレーム')).toBeTruthy()
  })

  it('参照動画のボタンからは動画候補だけが並ぶ', () => {
    showInputs()
    fireEvent.click(
      within(section('参照動画（モーション転写）')).getByRole('button', {
        name: '履歴から選択',
      }),
    )
    // モードタブにも「動画」があるので、バッジの確認はモーダル内にスコープする
    const modal = screen
      .getByText('履歴から選択: 参照動画')
      .closest('.card') as HTMLElement
    expect(within(modal).getByText('1 件')).toBeTruthy()
    expect(within(modal).getByText('動画')).toBeTruthy()
  })

  it('リファレンス音声のボタンからは音声候補だけが並ぶ', () => {
    showInputs()
    fireEvent.click(
      within(section('リファレンス音声')).getByRole('button', { name: '履歴から選択' }),
    )
    expect(screen.getByText('履歴から選択: リファレンス音声')).toBeTruthy()
    expect(screen.getByText(/履歴に使える音声がまだありません/)).toBeTruthy()
  })

  it('インラインのサムネイル帯は出さない（モーダルに置き換えた）', () => {
    showInputs()
    expect(screen.queryByText('履歴のラストフレームから選択')).toBeNull()
  })
})

describe('GenerateForm の「ライブラリから選択」', () => {
  const EVERY_INPUT: Options['video_workflows'] = [
    {
      id: 'all_inputs',
      label: 'すべての入力',
      kind: 'video',
      family: 'wan',
      notes: '',
      requires: ['image', 'end_image', 'video', 'audio'],
      supports: ['prompt', 'negative', 'duration', 'fps'],
      accepts_start_image: true,
      image_label: '最初のフレーム',
      selects: [],
      prompt_required: true,
      accepts_video_loras: true,
      min_duration: 0,
      max_duration: 0,
      default_duration: 0,
    },
  ]

  const LIB: LibraryItem[] = [
    {
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
      tags: [],
      category: null,
    },
    {
      id: 'l2',
      created_at: '2026-07-30T10:00:00+00:00',
      kind: 'audio',
      name: 'テーマ曲',
      path: '/repo/library/audio/bgm.mp3',
      url: '/library/audio/bgm.mp3',
      nsfw: false,
      nsfw_source: '',
      source_job_id: null,
      source: null,
      tags: [],
      category: null,
    },
  ]

  function showInputs(form: Partial<FormState> = {}) {
    // モーダルは kind で絞って取りに来るので、その通りに返す
    vi.mocked(api.listLibrary).mockImplementation(async (query: LibraryQuery = {}) => {
      const items = LIB.filter((item) => item.kind === query.kind)
      return { items, total: items.length, limit: 50, offset: 0, tags: [] }
    })
    const patch = vi.fn()
    render(
      <GenerateForm
        form={{ ...initialForm, mode: 'i2v', videoWorkflow: 'all_inputs', ...form }}
        patch={patch}
        options={{ ...OPTIONS, video_workflows: EVERY_INPUT, library: LIB }}
        optionsError={null}
        onReloadOptions={() => {}}
        onOpenChat={() => {}}
        onSubmit={() => {}}
        submitting={false}
        fieldErrors={{}}
        comfyTarget="local"
        onComfyTarget={() => {}}
        jobs={[]}
        showNsfw={false}
      />,
    )
    return { patch }
  }

  it('画像・動画・音声の入力すべてにボタンを出す', () => {
    showInputs()
    for (const title of [
      '最初のフレーム',
      '最後のフレーム',
      '参照動画（モーション転写）',
      'リファレンス音声',
    ]) {
      expect(
        within(section(title)).getByRole('button', { name: 'ライブラリから選択' }),
      ).toBeTruthy()
    }
  })

  it('選ぶとコピーせずライブラリの URL をそのままフォームに入れる', async () => {
    const { patch } = showInputs()
    fireEvent.click(
      within(section('最後のフレーム')).getByRole('button', {
        name: 'ライブラリから選択',
      }),
    )
    expect(screen.getByText('ライブラリから選択: 最後のフレーム')).toBeTruthy()
    fireEvent.click(await screen.findByText('決めポーズ'))
    expect(patch).toHaveBeenCalledWith({ endImage: '/library/image/pose.png' })
  })

  it('音声欄では音声の素材だけを取りに行く', async () => {
    showInputs()
    fireEvent.click(
      within(section('リファレンス音声')).getByRole('button', {
        name: 'ライブラリから選択',
      }),
    )
    await waitFor(() =>
      expect(api.listLibrary).toHaveBeenCalledWith(
        expect.objectContaining({ kind: 'audio' }),
      ),
    )
    expect(await screen.findByText('テーマ曲')).toBeTruthy()
    expect(screen.queryByText('決めポーズ')).toBeNull()
  })

  it('リファレンス音声はアセットのプルダウンを出さず、選択中を名前で見せる', () => {
    // 旧 UI（assets/audio のセレクト）は廃止した
    const { patch } = showInputs({ audioPath: '/library/audio/bgm.mp3' })
    expect(within(section('リファレンス音声')).queryByRole('combobox')).toBeNull()
    expect(within(section('リファレンス音声')).getByText('テーマ曲')).toBeTruthy()
    fireEvent.click(
      within(section('リファレンス音声')).getByRole('button', { name: 'クリア' }),
    )
    expect(patch).toHaveBeenCalledWith({ audioPath: '' })
  })

  it('LoRA 由来の assets URL もそのまま表示できる（後方互換）', () => {
    showInputs({ audioPath: '/assets/audio/legacy.mp3' })
    // ライブラリにもアセット一覧にも無ければ、値そのものを出す
    expect(
      within(section('リファレンス音声')).getByText('/assets/audio/legacy.mp3'),
    ).toBeTruthy()
  })
})

describe('GenerateForm の選択式フィールド（SPEC §3.1）', () => {
  const WAN: Options['video_workflows'][number] = {
    id: 'wan_dancer',
    label: '画像+音声→ダンス動画 (Wan Dancer)',
    kind: 'video',
    family: 'wan',
    notes: '',
    requires: ['image', 'audio'],
    supports: ['prompt', 'negative', 'width', 'height'],
    accepts_start_image: true,
    image_label: '開始フレーム',
    selects: [
      {
        name: 'dance_style',
        label: '踊りの種類',
        choices: ['K-Pop 韩舞', 'Street Dance 街舞'],
        default: 'K-Pop 韩舞',
        auto: false,
        hint: 'プロンプトの <dance style> に入る踊りの種類。',
      },
      {
        name: 'duration',
        label: '尺（秒）',
        choices: ['5', '10', '15'],
        default: '15',
        auto: true,
        hint: '省略すると音声の長さに合わせて決める。',
      },
    ],
    prompt_required: false,
    accepts_video_loras: false,
    min_duration: 0,
    max_duration: 0,
    default_duration: 0,
  }

  function showWan(form: Partial<FormState> = {}) {
    const patch = vi.fn()
    render(
      <GenerateForm
        form={{ ...initialForm, mode: 'i2v', videoWorkflow: 'wan_dancer', ...form }}
        patch={patch}
        options={{ ...OPTIONS, video_workflows: [WAN] }}
        optionsError={null}
        onReloadOptions={() => {}}
        onOpenChat={() => {}}
        onSubmit={() => {}}
        submitting={false}
        fieldErrors={{}}
        comfyTarget="local"
        onComfyTarget={() => {}}
        jobs={[]}
        showNsfw={false}
      />,
    )
    return { patch }
  }

  it('宣言された選択肢をセレクトとして出し、選択を patch する', () => {
    const { patch } = showWan()
    const dance = screen.getByLabelText('踊りの種類') as HTMLSelectElement
    expect([...dance.options].map((option) => option.value)).toEqual([
      '',
      'K-Pop 韩舞',
      'Street Dance 街舞',
    ])
    // 未指定は「既定（…）」と出す
    expect(dance.options[0].textContent).toContain('既定（K-Pop 韩舞）')
    fireEvent.change(dance, { target: { value: 'Street Dance 街舞' } })
    expect(patch).toHaveBeenCalledWith({
      selects: { dance_style: 'Street Dance 街舞' },
    })
  })

  it('自動決定できる項目には「自動」を既定オプションとして置く', () => {
    showWan()
    const length = screen.getByLabelText('尺（秒）') as HTMLSelectElement
    expect(length.value).toBe('')
    expect(length.options[0].textContent).toContain('自動')
  })

  it('選択式を持たないワークフローでは何も出さない', () => {
    show()
    expect(screen.queryByLabelText('踊りの種類')).toBeNull()
  })

  it('LoRA チェーンが無いので動画 LoRA のセクションを出さない', () => {
    showWan()
    expect(screen.queryByText('LoRA（動画）')).toBeNull()
  })

  it('動画プロンプトを任意と示す', () => {
    showWan()
    expect(screen.getByText('（任意）')).toBeTruthy()
    expect(
      screen.getByPlaceholderText(/選択項目からプロンプトが組み立てられます/),
    ).toBeTruthy()
  })

  it('画像ワークフローの選択式も出す（gpt-image-2 の大きさ・品質、SPEC §5.4）', () => {
    const GPT_IMAGE: Options['image_workflows'][number] = {
      id: 'gpt_image2',
      label: 'gpt-image-2（Codex CLI）',
      kind: 'image',
      family: 'gpt-image',
      notes: '',
      requires: [],
      supports: ['prompt'],
      accepts_start_image: false,
      image_label: '開始フレーム',
      selects: [
        {
          name: 'size',
          label: '大きさ',
          choices: ['1024x1024', '1536x1024', '1024x1536'],
          default: '1024x1024',
          auto: false,
          hint: '',
        },
        {
          name: 'quality',
          label: '品質',
          choices: ['low', 'medium', 'high'],
          default: 'medium',
          auto: false,
          hint: '',
        },
      ],
      prompt_required: true,
      accepts_video_loras: false,
      min_duration: 0,
      max_duration: 0,
      default_duration: 0,
    }
    const patch = vi.fn()
    render(
      <GenerateForm
        form={{ ...initialForm, mode: 'image_only', imageWorkflow: 'gpt_image2' }}
        patch={patch}
        options={{ ...OPTIONS, image_workflows: [GPT_IMAGE] }}
        optionsError={null}
        onReloadOptions={() => {}}
        onOpenChat={() => {}}
        onSubmit={() => {}}
        submitting={false}
        fieldErrors={{}}
        comfyTarget="local"
        onComfyTarget={() => {}}
        jobs={[]}
        showNsfw={false}
      />,
    )

    const size = screen.getByLabelText('大きさ') as HTMLSelectElement
    expect([...size.options].map((option) => option.value)).toEqual([
      '',
      '1024x1024',
      '1536x1024',
      '1024x1536',
    ])
    fireEvent.change(size, { target: { value: '1536x1024' } })
    expect(patch).toHaveBeenCalledWith({ selects: { size: '1536x1024' } })
    expect(screen.getByLabelText('品質')).toBeTruthy()
  })
})

// ------------------------------------------- マルチモーダル参照（SPEC §3.1 / §8）
// Seedance 2 の参照モード: 1 欄が複数ファイルを持ち、開始フレームとは排他。

describe('GenerateForm のマルチモーダル参照', () => {
  const SEEDANCE: Options['video_workflows'][number] = {
    id: 'seedance2_ref',
    label: 'Seedance 2.0（素材参照）',
    kind: 'video',
    family: 'seedance',
    notes: '',
    requires: [],
    supports: [
      'prompt',
      'reference_images',
      'reference_videos',
      'reference_audios',
    ],
    multi_inputs: {
      reference_images: 9,
      reference_videos: 3,
      reference_audios: 3,
    },
    accepts_start_image: false,
    image_label: '開始フレーム（任意）',
    selects: [],
    prompt_required: true,
    accepts_video_loras: false,
    backend: 'kie',
    min_duration: 0,
    max_duration: 0,
    default_duration: 0,
  }

  const VEO: Options['video_workflows'][number] = {
    ...SEEDANCE,
    id: 'veo3_1_fast',
    label: 'Veo 3.1 Fast',
    family: 'veo',
    supports: ['prompt', 'image', 'end_image'],
    multi_inputs: {},
    accepts_start_image: true,
  }

  const REFERENCES: LibraryItem[] = ['l1', 'l2'].map((id, index) => ({
    id,
    created_at: '2026-08-01T10:00:00+00:00',
    kind: 'image' as const,
    name: `参照${index + 1}`,
    path: `/repo/library/image/ref${index}.png`,
    url: `/library/image/ref${index}.png`,
    nsfw: false,
    nsfw_source: '',
    source_job_id: null,
    source: null,
    tags: [],
    category: null,
  }))

  const TITLE = 'マルチモーダル参照（素材参照ワークフロー）'

  function showReferenceForm(form: Partial<FormState> = {}) {
    vi.mocked(api.listLibrary).mockResolvedValue({
      items: REFERENCES,
      total: REFERENCES.length,
      limit: 50,
      offset: 0,
      tags: [],
    })
    const patch = vi.fn()
    render(
      <GenerateForm
        form={{
          ...initialForm,
          mode: 'i2v',
          videoWorkflow: 'seedance2_ref',
          ...form,
        }}
        patch={patch}
        options={{ ...OPTIONS, video_workflows: [SEEDANCE, VEO] }}
        optionsError={null}
        onReloadOptions={() => {}}
        onOpenChat={() => {}}
        onSubmit={() => {}}
        submitting={false}
        fieldErrors={{}}
        comfyTarget="local"
        onComfyTarget={() => {}}
        jobs={[]}
        showNsfw={false}
      />,
    )
    return { patch }
  }

  it('宣言しているワークフローのときだけ、宣言した欄と上限を出す', () => {
    showReferenceForm()
    const panel = section(TITLE)
    expect(within(panel).getByText('参照画像')).toBeTruthy()
    expect(within(panel).getByText('0 / 9 件')).toBeTruthy()
    // 参照動画・参照音声は 3 件まで
    expect(within(panel).getAllByText('0 / 3 件')).toHaveLength(2)
    expect(within(panel).getByText('参照音声')).toBeTruthy()

    cleanup()
    showReferenceForm({ videoWorkflow: 'veo3_1_fast' })
    expect(screen.queryByText(TITLE)).toBeNull()
  })

  it('画像＋動画モードでは参照欄そのものを出さない', () => {
    showReferenceForm({ mode: 'full' })
    expect(screen.queryByText(TITLE)).toBeNull()
  })

  it('選んだ素材が順番つきで積み上がり、外せる', () => {
    const { patch } = showReferenceForm({
      referenceImages: ['/library/image/ref0.png'],
    })
    const panel = section(TITLE)
    expect(within(panel).getByText('1 / 9 件')).toBeTruthy()
    expect(within(panel).getByText('/library/image/ref0.png')).toBeTruthy()

    fireEvent.click(within(panel).getAllByRole('button', { name: '外す' })[0])
    expect(patch).toHaveBeenCalledWith({ referenceImages: [] })
  })

  it('ライブラリからは複数選べ、選ぶたびに欄へ足していく', async () => {
    const { patch } = showReferenceForm()
    fireEvent.click(
      within(section(TITLE)).getAllByRole('button', {
        name: 'ライブラリから選択',
      })[0],
    )
    expect(screen.getByText('ライブラリから選択: 参照画像')).toBeTruthy()

    fireEvent.click(await screen.findByText('参照1'))
    expect(patch).toHaveBeenCalledWith({
      referenceImages: ['/library/image/ref0.png'],
    })
    // 1 件選んでもモーダルは開いたまま（続けて選べる）
    expect(screen.getByText('ライブラリから選択: 参照画像')).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: '選択を終える' }))
    await waitFor(() =>
      expect(screen.queryByText('ライブラリから選択: 参照画像')).toBeNull(),
    )
  })
})

// --------------------------- マルチショット / Elements（SPEC §3.1 / §8、Kling）
// 行が増えて縦に伸びるので、どちらも既定は折りたたみ。

describe('GenerateForm のマルチショットと Elements', () => {
  const KLING: Options['video_workflows'][number] = {
    id: 'kling3_video',
    label: 'Kling 3.0',
    kind: 'video',
    family: 'kling',
    notes: '',
    requires: [],
    supports: ['prompt', 'image', 'end_image'],
    max_prompt_chars: 500,
    multi_shot: null,
    elements: {
      max_elements: 3,
      min_images: 2,
      max_images: 4,
      reference_chars: 37,
    },
    accepts_start_image: true,
    image_label: '開始フレーム（任意）',
    selects: [],
    prompt_required: true,
    accepts_video_loras: false,
    backend: 'kie',
    min_duration: 0,
    max_duration: 0,
    default_duration: 0,
  }

  // ショット割り専用版: 本文はショット側にあるのでプロンプト欄が出ない
  const KLING_SHOTS: Options['video_workflows'][number] = {
    ...KLING,
    id: 'kling3_multishot',
    label: 'Kling 3.0 マルチショット',
    prompt_required: false,
    multi_shot: { max_shots: 5, min_duration: 1, max_duration: 12 },
  }

  const PLAIN: Options['video_workflows'][number] = {
    ...KLING,
    id: 'veo3_1_fast',
    label: 'Veo 3.1 Fast',
    family: 'veo',
    max_prompt_chars: 0,
    multi_shot: null,
    elements: null,
  }

  const ELEMENT_IMAGES: LibraryItem[] = ['e1', 'e2'].map((id, index) => ({
    id,
    created_at: '2026-08-01T10:00:00+00:00',
    kind: 'image' as const,
    name: `要素${index + 1}`,
    path: `/repo/library/image/el${index}.png`,
    url: `/library/image/el${index}.png`,
    nsfw: false,
    nsfw_source: '',
    source_job_id: null,
    source: null,
    tags: [],
    category: null,
  }))

  const SHOTS = 'マルチショット'
  const ELEMENTS = 'Elements（@要素名 でのキャラ固定）'

  function showKlingForm(form: Partial<FormState> = {}) {
    vi.mocked(api.listLibrary).mockResolvedValue({
      items: ELEMENT_IMAGES,
      total: ELEMENT_IMAGES.length,
      limit: 50,
      offset: 0,
      tags: [],
    })
    const patch = vi.fn()
    render(
      <GenerateForm
        form={{
          ...initialForm,
          mode: 'i2v',
          videoWorkflow: 'kling3_multishot',
          ...form,
        }}
        patch={patch}
        options={{ ...OPTIONS, video_workflows: [KLING, KLING_SHOTS, PLAIN] }}
        optionsError={null}
        onReloadOptions={() => {}}
        onOpenChat={() => {}}
        onSubmit={() => {}}
        submitting={false}
        fieldErrors={{}}
        comfyTarget="local"
        onComfyTarget={() => {}}
        jobs={[]}
        showNsfw={false}
      />,
    )
    return { patch }
  }

  it('宣言しているワークフローのときだけ、畳んだ状態で出す', () => {
    showKlingForm()
    expect(screen.getByText('開く（0 / 5 ショット）')).toBeTruthy()
    expect(screen.getByText('開く（0 / 3 要素）')).toBeTruthy()
    // 畳んでいるあいだは中身を描かない
    expect(screen.queryByRole('button', { name: 'ショットを追加' })).toBeNull()

    cleanup()
    // 1 カット版は Elements だけ（ショット割りは別ワークフローの機能）
    showKlingForm({ videoWorkflow: 'kling3_video' })
    expect(screen.queryByText(SHOTS)).toBeNull()
    expect(screen.getByText('開く（0 / 3 要素）')).toBeTruthy()

    cleanup()
    showKlingForm({ videoWorkflow: 'veo3_1_fast' })
    expect(screen.queryByText(SHOTS)).toBeNull()
    expect(screen.queryByText(ELEMENTS)).toBeNull()
  })

  it('ショットを追加・編集・削除できる', () => {
    const { patch } = showKlingForm({
      multiShots: [{ prompt: 'Shot 1', duration: 4 }],
    })
    fireEvent.click(screen.getByText('開く（1 / 5 ショット）'))

    fireEvent.click(screen.getByRole('button', { name: 'ショットを追加' }))
    expect(patch).toHaveBeenCalledWith({
      multiShots: [
        { prompt: 'Shot 1', duration: 4 },
        { prompt: '', duration: 5 },
      ],
    })

    fireEvent.change(screen.getByLabelText('Shot 1 の秒数'), {
      target: { value: '9' },
    })
    expect(patch).toHaveBeenCalledWith({
      multiShots: [{ prompt: 'Shot 1', duration: 9 }],
    })

    fireEvent.click(within(section(SHOTS)).getByRole('button', { name: '削除' }))
    expect(patch).toHaveBeenCalledWith({ multiShots: [] })
  })

  it('上限に達したら追加できない', () => {
    showKlingForm({
      multiShots: Array(5).fill({ prompt: 'x', duration: 4 }),
    })
    fireEvent.click(screen.getByText('開く（5 / 5 ショット）'))
    expect(
      screen.getByRole('button', { name: 'ショットを追加' }),
    ).toHaveProperty('disabled', true)
  })

  it('`@要素名` を 37 文字として残り文字数を出す', () => {
    // プロンプト欄が出るのは 1 カット版（ショット割り版は本文がショット側）
    showKlingForm({ videoWorkflow: 'kling3_video', videoPrompt: '@kaori walks.' })
    // 500 - (37 + " walks.".length)
    expect(screen.getByText('残り 456 文字')).toBeTruthy()
  })

  it('要素の参照画像はライブラリから複数選べる', async () => {
    const { patch } = showKlingForm({
      klingElements: [{ name: 'kaori', description: '', images: [] }],
    })
    fireEvent.click(screen.getByText('開く（1 / 3 要素）'))
    expect(within(section(ELEMENTS)).getByText('0 / 4 枚')).toBeTruthy()

    fireEvent.click(
      within(section(ELEMENTS)).getByRole('button', {
        name: 'ライブラリから選択',
      }),
    )
    expect(screen.getByText('ライブラリから選択: 要素 1 の参照画像')).toBeTruthy()

    fireEvent.click(await screen.findByText('要素1'))
    expect(patch).toHaveBeenCalledWith({
      klingElements: [
        { name: 'kaori', description: '', images: ['/library/image/el0.png'] },
      ],
    })
    // 続けて選べるようモーダルは開いたまま
    expect(screen.getByText('ライブラリから選択: 要素 1 の参照画像')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: '選択を終える' }))
    await waitFor(() =>
      expect(
        screen.queryByText('ライブラリから選択: 要素 1 の参照画像'),
      ).toBeNull(),
    )
  })

  it('要素を追加・削除できる', () => {
    const { patch } = showKlingForm()
    fireEvent.click(screen.getByText('開く（0 / 3 要素）'))
    fireEvent.click(screen.getByRole('button', { name: '要素を追加' }))
    expect(patch).toHaveBeenCalledWith({
      klingElements: [{ name: '', description: '', images: [] }],
    })

    cleanup()
    const second = showKlingForm({
      klingElements: [
        { name: 'kaori', description: '', images: ['/library/image/el0.png'] },
      ],
    })
    fireEvent.click(screen.getByText('開く（1 / 3 要素）'))
    fireEvent.click(within(section(ELEMENTS)).getByRole('button', { name: '外す' }))
    expect(second.patch).toHaveBeenCalledWith({
      klingElements: [{ name: 'kaori', description: '', images: [] }],
    })
  })
})


describe('ステップ数（SPEC §3.1）', () => {
  it('宣言しているワークフローのときだけ欄を出す', () => {
    showVideos({ mode: 'i2v', videoWorkflow: 'minimax_h3_i2v' })
    expect(screen.getByLabelText('ステップ数')).toBeTruthy()
    cleanup()
    showVideos({ mode: 'i2v', videoWorkflow: 'wan22_t2v' })
    expect(screen.queryByLabelText('ステップ数')).toBeNull()
  })

  it('未指定（0）は空欄で見せ、入力すると patch する', () => {
    const { patch } = showVideos({ mode: 'i2v', videoWorkflow: 'minimax_h3_i2v' })
    const steps = screen.getByLabelText('ステップ数') as HTMLInputElement
    expect(steps.value).toBe('')
    expect(steps.placeholder).toBe('未指定＝既定')
    fireEvent.change(steps, { target: { value: '12' } })
    expect(patch).toHaveBeenCalledWith({ steps: 12 })
  })

  it('音声ワークフローでも宣言があれば出す', () => {
    showAudio({ audioWorkflow: 'stable_audio_3_medium_base', steps: 24 })
    expect((screen.getByLabelText('ステップ数') as HTMLInputElement).value).toBe('24')
    cleanup()
    showAudio({ audioWorkflow: 'ace_step1_5_xl_sft' })
    expect(screen.queryByLabelText('ステップ数')).toBeNull()
  })
})
