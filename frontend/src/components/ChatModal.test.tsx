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

const OPTIONS = { audio_workflows: [ACE, SA3] } as unknown as Options

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
