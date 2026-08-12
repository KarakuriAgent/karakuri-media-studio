import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '../api'
import { initialForm, type FormState } from '../form'
import type { ChatSession, Options, PromptResult, WorkflowOption } from '../types'
import ChatModal from './ChatModal'

afterEach(cleanup)

function workflow(overrides: Partial<WorkflowOption> = {}): WorkflowOption {
  return {
    id: 'w',
    label: 'W',
    kind: 'audio',
    family: 'ace-step',
    notes: '',
    requires: [],
    supports: [],
    accepts_start_image: false,
    image_label: '開始フレーム',
    selects: [],
    prompt_required: true,
    accepts_video_loras: true,
    min_duration: 0,
    max_duration: 0,
    default_duration: 0,
    ...overrides,
  }
}

const ACE = workflow({
  id: 'ace_step1_5_xl_sft',
  label: 'ACE-Step 1.5 XL',
  supports: ['prompt', 'lyrics', 'duration', 'bpm', 'keyscale', 'language', 'seed'],
  min_duration: 10,
  max_duration: 600,
  default_duration: 120,
})

const SA3 = workflow({
  id: 'stable_audio_3_medium_base',
  label: 'Stable Audio 3 Medium',
  family: 'stable-audio',
  supports: ['prompt', 'duration', 'audio_category', 'reprompt', 'seed'],
  min_duration: 1,
  max_duration: 380,
  default_duration: 60,
})

/** 参照素材だけを受け取るワークフロー（開始フレームは取らない）。 */
const R2V = workflow({
  id: 'minimax_h3_r2v',
  label: 'MiniMax H3 r2v',
  kind: 'video',
  family: 'minimax-h3',
  supports: ['duration', 'prompt'],
  multi_inputs: { reference_images: 9, reference_videos: 3, reference_audios: 3 },
})

/** 開始フレームと最後のフレームを取る動画ワークフロー。 */
const I2V = workflow({
  id: 'minimax_h3_i2v',
  label: 'MiniMax H3 i2v',
  kind: 'video',
  family: 'minimax-h3',
  requires: ['image'],
  supports: ['duration', 'prompt', 'image', 'end_image'],
  accepts_start_image: true,
})

/** 入力画像を編集する画像ワークフロー（LoRA チェーンは持たない）。 */
const EDIT = workflow({
  id: 'qwen_image_edit_2511',
  label: 'Qwen Image Edit',
  kind: 'image',
  family: 'qwen-image',
  requires: ['image'],
  supports: ['prompt', 'image'],
  accepts_video_loras: false,
})

const KREA2 = workflow({
  id: 'krea2_turbo',
  label: 'Krea 2 Turbo',
  kind: 'image',
  family: 'krea2',
  supports: ['prompt'],
})

const OPTIONS = {
  audio_workflows: [ACE, SA3],
  video_workflows: [R2V, I2V],
  image_workflows: [KREA2, EDIT],
} as unknown as Options

const SESSION: ChatSession = {
  id: 'session-1',
  created_at: '2026-01-01T00:00:00Z',
  job_id: null,
  messages: [{ role: 'system', content: 'system prompt', ts: 't' }],
}

function result(overrides: Partial<PromptResult> = {}): PromptResult {
  return {
    image_prompt: null,
    video_prompt: null,
    audio_prompt: null,
    lyrics: null,
    negative_tags: null,
    bpm: null,
    keyscale: null,
    language: null,
    notes: null,
    ...overrides,
  }
}

const AUDIO_RESULT = result({
  audio_prompt: 'dreamy city-pop ballad, female vocal',
  lyrics: '[Verse 1]\n最終列車が街を抜ける',
  bpm: 92,
  keyscale: 'F# minor',
  language: 'ja',
  notes: 'しっとりめに',
})

let created: unknown[] = []

beforeEach(() => {
  created = []
  vi.spyOn(api, 'createChatSession').mockImplementation(async (payload) => {
    created.push(payload)
    return SESSION
  })
})

afterEach(() => vi.restoreAllMocks())

async function open(form: Partial<FormState> = {}) {
  const patch = vi.fn()
  const onClose = vi.fn()
  render(
    <ChatModal
      form={{ ...initialForm, ...form }}
      patch={patch}
      options={OPTIONS}
      onClose={onClose}
      onSessionId={() => {}}
    />,
  )
  await waitFor(() => expect(created.length).toBe(1))
  return { patch, onClose }
}

/** 送信ボタンを押して 1 往復させ、返ってきた result のプレビューを出す。 */
async function reply(promptResult: PromptResult) {
  vi.spyOn(api, 'sendChatMessage').mockResolvedValue({
    role: 'assistant',
    content: 'できました',
    result: promptResult,
  })
  fireEvent.change(screen.getByPlaceholderText(/メッセージを入力/), {
    target: { value: 'lo-fi な曲' },
  })
  fireEvent.click(screen.getByText('送信'))
  await waitFor(() => expect(screen.getByText('フォームに反映')).toBeTruthy())
}

describe('ChatModal — 音声モード', () => {
  it('選択中の音声ワークフローと下書きをセッションに渡す', async () => {
    await open({
      mode: 'audio',
      audioWorkflow: SA3.id,
      audioPrompt: '雨の音',
      lyrics: '[Verse 1]',
      audioDuration: 45,
      duration: 10,
    })
    expect(created[0]).toMatchObject({
      mode: 'audio',
      audio_workflow: SA3.id,
      audio_prompt_draft: '雨の音',
      lyrics_draft: '[Verse 1]',
      // 音声モードでは音の長さを渡す（動画のクリップ長ではない）
      duration: 45,
    })
  })

  it('動画モードでは今までどおり動画のクリップ長を渡す', async () => {
    await open({ mode: 'full', duration: 10, audioDuration: 120 })
    expect(created[0]).toMatchObject({ mode: 'full', duration: 10 })
  })

  it('プロンプトテンプレートの切替は音声モードでは出さない', async () => {
    await open({ mode: 'audio' })
    expect(screen.queryByText('プロンプトテンプレート')).toBeNull()
  })

  it('ACE-Step では歌詞と提案値までフォームに反映する', async () => {
    const { patch, onClose } = await open({
      mode: 'audio',
      audioWorkflow: ACE.id,
    })
    await reply(AUDIO_RESULT)
    expect(screen.getByText('音声プロンプト')).toBeTruthy()
    expect(screen.getByText('歌詞')).toBeTruthy()

    fireEvent.click(screen.getByText('フォームに反映'))
    expect(patch).toHaveBeenCalledWith({
      audioPrompt: 'dreamy city-pop ballad, female vocal',
      lyrics: '[Verse 1]\n最終列車が街を抜ける',
      bpm: 92,
      keyscale: 'F# minor',
      language: 'ja',
    })
    expect(onClose).toHaveBeenCalled()
  })

  it('Stable Audio では読めない項目（歌詞・BPM・キー）を反映しない', async () => {
    const { patch } = await open({ mode: 'audio', audioWorkflow: SA3.id })
    await reply(AUDIO_RESULT)
    expect(screen.queryByText('歌詞')).toBeNull()

    fireEvent.click(screen.getByText('フォームに反映'))
    expect(patch).toHaveBeenCalledWith({
      audioPrompt: 'dreamy city-pop ballad, female vocal',
    })
  })

  it('画像・動画モードの反映は今までどおり', async () => {
    const { patch } = await open({ mode: 'full' })
    await reply(result({ image_prompt: 'a still', video_prompt: 'a clip' }))
    fireEvent.click(screen.getByText('フォームに反映'))
    expect(patch).toHaveBeenCalledWith({
      imagePrompt: 'a still',
      videoPrompt: 'a clip',
    })
  })
})

describe('ChatModal — フォームの現在値の受け渡し', () => {
  it('欄が出ている参照素材と解像度・除外指定を渡す', async () => {
    await open({
      mode: 'i2v',
      videoWorkflow: R2V.id,
      referenceImages: ['/library/image/a.png'],
      referenceVideos: ['/library/video/b.mp4'],
      referenceAudios: ['/library/audio/c.wav'],
      aspectRatio: '9:16 (Portrait Widescreen)',
      megapixels: 0.4,
      negativePrompt: 'blurry',
    })
    expect(created[0]).toMatchObject({
      reference_images: ['/library/image/a.png'],
      reference_videos: ['/library/video/b.mp4'],
      reference_audios: ['/library/audio/c.wav'],
      aspect_ratio: '9:16 (Portrait Widescreen)',
      megapixels: 0.4,
      negative_prompt: 'blurry',
    })
  })

  it('参照欄の出ないワークフローでは残っている参照素材を送らない', async () => {
    await open({
      mode: 'i2v',
      videoWorkflow: I2V.id,
      referenceImages: ['/library/image/a.png'],
    })
    expect(created[0]).toMatchObject({ reference_images: [] })
  })

  it('開始フレームは欄が出ているときだけ送る', async () => {
    await open({
      mode: 'i2v',
      videoWorkflow: R2V.id,
      sourceImage: '/assets/image/start.png',
      endImage: '/assets/image/end.png',
    })
    // r2v は開始フレームも最後のフレームも取らない
    expect(created[0]).toMatchObject({
      start_image_path: null,
      end_image_path: null,
    })
  })

  it('開始フレームと最後のフレームを取るワークフローでは両方送る', async () => {
    await open({
      mode: 'i2v',
      videoWorkflow: I2V.id,
      sourceImage: '/assets/image/start.png',
      endImage: '/assets/image/end.png',
    })
    expect(created[0]).toMatchObject({
      start_image_path: '/assets/image/start.png',
      end_image_path: '/assets/image/end.png',
    })
  })

  it('編集系の画像ワークフローでは image_only でも編集元画像を送る', async () => {
    await open({
      mode: 'image_only',
      imageWorkflow: EDIT.id,
      sourceImage: '/assets/image/start.png',
    })
    expect(created[0]).toMatchObject({
      start_image_path: '/assets/image/start.png',
      // 編集系は入力画像から大きさが決まるので解像度欄は出ない
      aspect_ratio: null,
      megapixels: null,
    })
  })

  it('LoRA を挿せないワークフローには LoRA もトリガーも送らない', async () => {
    const lora = {
      id: 1,
      lora_name: 'x.safetensors',
      trigger_word: 'kaori',
      strength: 1,
      display_name: 'かおり',
    }
    await open({
      mode: 'image_only',
      imageWorkflow: EDIT.id,
      sourceImage: '/assets/image/start.png',
      loras: [lora],
      triggerText: 'kaori',
    })
    expect(created[0]).toMatchObject({ loras: [], trigger_text: '' })
  })

  it('動画 LoRA は動画ステージが走るときだけ送る', async () => {
    const lora = {
      id: 2,
      lora_name: 'motion.safetensors',
      trigger_word: 'slowmo',
      strength: 1,
      display_name: 'スローモ',
    }
    await open({
      mode: 'image_only',
      imageWorkflow: KREA2.id,
      videoLoras: [lora],
      videoTriggerText: 'slowmo',
    })
    expect(created[0]).toMatchObject({ video_loras: [], video_trigger_text: '' })
  })

  it('プロンプトテンプレートの切替は image_only では出さない', async () => {
    await open({ mode: 'image_only', imageWorkflow: KREA2.id })
    expect(screen.queryByText('プロンプトテンプレート')).toBeNull()
  })

  it('動画モードではプロンプトテンプレートの切替を出す', async () => {
    await open({ mode: 'full', videoWorkflow: I2V.id, imageWorkflow: KREA2.id })
    expect(screen.getByText('プロンプトテンプレート')).toBeTruthy()
  })
})
