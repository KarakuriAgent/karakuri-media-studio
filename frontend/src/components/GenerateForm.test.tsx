import { cleanup, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { initialForm, type FormState } from '../form'
import type { Lora, Options } from '../types'
import GenerateForm from './GenerateForm'

afterEach(cleanup)

function lora(id: number, name: string, target: Lora['target']): Lora {
  return {
    id,
    display_name: name,
    lora_name: `${name}.safetensors`,
    trigger_word: name,
    default_strength: 1,
    default_audio: null,
    sort_order: 0,
    target,
    sample_images: [],
  }
}

const OPTIONS: Options = {
  comfy_connected: true,
  comfy_error: null,
  comfy_url: '',
  image_workflows: [],
  video_workflows: [],
  default_video_workflow: 'ltx2_3_id_lora',
  aspect_ratios: [],
  lora_files: [],
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
      />,
    )
    expect(screen.getAllByText(/画像用の登録済み LoRA がありません/).length).toBe(1)
    expect(screen.getAllByText(/動画用の登録済み LoRA がありません/).length).toBe(1)
  })

  it('動画生成モードでは画像 LoRA だけを無効化する', () => {
    show({ mode: 'i2v' })
    const image = within(section('LoRA（画像）')).getByRole('button', { name: 'サクラ' })
    const video = within(section('LoRA（動画）')).getByRole('button', { name: 'スローモ' })
    expect((image as HTMLButtonElement).disabled).toBe(true)
    expect((video as HTMLButtonElement).disabled).toBe(false)
  })

  it('画像のみモードでは動画 LoRA を無効化する', () => {
    show({ mode: 'image_only' })
    const image = within(section('LoRA（画像）')).getByRole('button', { name: 'サクラ' })
    const video = within(section('LoRA（動画）')).getByRole('button', { name: 'スローモ' })
    expect((image as HTMLButtonElement).disabled).toBe(false)
    expect((video as HTMLButtonElement).disabled).toBe(true)
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
