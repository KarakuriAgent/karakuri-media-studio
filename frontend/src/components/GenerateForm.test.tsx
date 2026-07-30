import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from '../api'
import { initialForm, type FormState } from '../form'
import type { Job, LibraryItem, LibraryQuery, Lora, Options } from '../types'
import GenerateForm from './GenerateForm'

// ライブラリのモーダルは自分で GET /api/library を叩くので、素材はここから返す。
vi.mock('../api', () => ({
  api: {
    listLibrary: vi.fn(),
    uploadToLibrary: vi.fn(),
    updateLibraryItem: vi.fn(),
    deleteLibraryItem: vi.fn(),
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
  comfy_url: '',
  image_workflows: [],
  video_workflows: [],
  audio_workflows: [],
  default_video_workflow: 'ltx2_3_id_lora',
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

function show(form: Partial<FormState> = {}) {
  const patch = vi.fn()
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
      jobs={[]}
      showNsfw={false}
    />,
  )
  return { patch }
}

/** The <section> whose heading is `title`. */
function section(title: string): HTMLElement {
  const heading = screen.getByText(title)
  const element = heading.closest('section')
  if (!element) throw new Error(`no section for ${title}`)
  return element
}

describe('GenerateForm の LoRA セクション', () => {
  it('画像用と動画用をそれぞれの対象で絞り込む', () => {
    show()
    expect(within(section('LoRA（画像）')).getByText('サクラ')).toBeTruthy()
    expect(within(section('LoRA（画像）')).queryByText('スローモ')).toBeNull()

    expect(within(section('LoRA（動画）')).getByText('スローモ')).toBeTruthy()
    expect(within(section('LoRA（動画）')).queryByText('サクラ')).toBeNull()
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
    expect(
      within(section('LoRA（動画）')).getByRole('button', { name: 'スローモ' }),
    ).toBeTruthy()
  })

  it('画像のみモードでは動画 LoRA のセクションごと消す', () => {
    show({ mode: 'image_only' })
    expect(screen.queryByText('LoRA（動画）')).toBeNull()
    expect(
      within(section('LoRA（画像）')).getByRole('button', { name: 'サクラ' }),
    ).toBeTruthy()
  })

  it('動画 LoRA を選ぶと動画側のトリガーだけが連結される', () => {
    const { patch } = show()
    const video = within(section('LoRA（動画）')).getByRole('button', { name: 'スローモ' })
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
    expect(within(section('LoRA（画像）')).getByText('サクラ')).toBeTruthy()
    expect(within(section('LoRA（画像）')).queryByText('ハナ')).toBeNull()

    cleanup()
    showImages({ imageWorkflow: 'qwen_image_edit_2511' })
    expect(within(section('LoRA（画像）')).getByText('ハナ')).toBeTruthy()
    expect(within(section('LoRA（画像）')).queryByText('サクラ')).toBeNull()
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
    supports: ['prompt', 'duration', 'audio_category', 'reprompt', 'seed'],
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

  it('ワークフローを選び直すと patch される', () => {
    const { patch } = showAudio({ audioWorkflow: 'ace_step1_5_xl_sft' })
    fireEvent.change(screen.getByLabelText('音声ワークフロー'), {
      target: { value: 'stable_audio_3_medium_base' },
    })
    expect(patch).toHaveBeenCalledWith({ audioWorkflow: 'stable_audio_3_medium_base' })
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
          id: 'ltx2_3_t2v',
          label: 'テキスト→動画',
          kind: 'video' as const,
          family: 'ltx2.3',
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
        form={{ ...initialForm, mode: 'i2v', videoWorkflow: 'ltx2_3_t2v' }}
        patch={vi.fn()}
        options={options}
        optionsError={null}
        onReloadOptions={() => {}}
        onOpenChat={() => {}}
        onSubmit={() => {}}
        submitting={false}
        fieldErrors={{}}
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
      family: 'ltx2.3',
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
    expect(screen.getByText('履歴から選択: 参照動画')).toBeTruthy()
    expect(screen.getByText('1 件')).toBeTruthy()
    expect(screen.getByText('動画')).toBeTruthy()
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
      family: 'ltx2.3',
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
    expect(screen.getByText(/省略すると音声の長さに合わせて決める/)).toBeTruthy()
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
})
