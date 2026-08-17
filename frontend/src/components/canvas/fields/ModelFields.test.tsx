import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { CanvasModelData, ModelSlot, Options } from '../../../types'
import ModelFields from './ModelFields'

afterEach(cleanup)

const SLOT: ModelSlot = {
  key: 'krea2_turbo/30:10.unet_name',
  workflow_id: 'krea2_turbo',
  workflow_label: 'Krea 2 Turbo',
  kind: 'image',
  node_id: '30:10',
  field: 'unet_name',
  class_type: 'UNETLoader',
  label: 'UNet',
  default: 'krea2.safetensors',
  choices: ['krea2.safetensors', 'krea2-alt.safetensors'],
}

const OPTIONS = {
  image_workflows: [
    { id: 'krea2_turbo', label: 'Krea 2 Turbo', family: 'krea2' },
  ],
  video_workflows: [],
  audio_workflows: [],
  aspect_ratios: ['4:3 (Standard)'],
  loras: [],
  model_slots: [SLOT],
} as unknown as Options

function data(overrides: Partial<CanvasModelData> = {}): CanvasModelData {
  return {
    target: 'image',
    workflow: 'krea2_turbo',
    note: '',
    params: {
      aspect_ratio: '4:3 (Standard)',
      megapixels: 1,
      duration: 10,
      fps: 25,
      loras: [],
      video_loras: [],
      negative_prompt: '',
      selects: {},
      model_overrides: {},
    },
    ...overrides,
  }
}

function show(overrides: Partial<CanvasModelData> = {}) {
  const onChange = vi.fn()
  render(
    <ModelFields data={data(overrides)} onChange={onChange} options={OPTIONS} />,
  )
  return { onChange }
}

describe('ModelFields の使用モデル切り替え（model_overrides）', () => {
  it('候補のあるスロットをセレクトで出す（既定には印を付ける）', () => {
    show()
    const select = screen.getByLabelText('使用モデル: UNet') as HTMLSelectElement
    expect([...select.options].map((option) => option.text)).toEqual([
      'krea2.safetensors（既定）',
      'krea2-alt.safetensors',
    ])
    expect(select.value).toBe('krea2.safetensors')
  })

  it('既定以外を選ぶと params.model_overrides に入る', () => {
    const { onChange } = show()
    fireEvent.change(screen.getByLabelText('使用モデル: UNet'), {
      target: { value: 'krea2-alt.safetensors' },
    })
    expect(onChange.mock.calls[0][0].params.model_overrides).toEqual({
      'krea2_turbo/30:10.unet_name': 'krea2-alt.safetensors',
    })
  })

  it('既定に戻すとキーごと落とす（差分だけ保存する）', () => {
    const { onChange } = show({
      params: {
        ...data().params,
        model_overrides: { 'krea2_turbo/30:10.unet_name': 'krea2-alt.safetensors' },
      },
    })
    fireEvent.change(screen.getByLabelText('使用モデル: UNet'), {
      target: { value: 'krea2.safetensors' },
    })
    expect(onChange.mock.calls[0][0].params.model_overrides).toEqual({})
  })

  it('そのワークフローにスロットが無ければ何も出さない', () => {
    show({ workflow: 'other' })
    expect(screen.queryByLabelText('使用モデル: UNet')).toBeNull()
  })
})

describe('ModelFields の選択式フィールド（表示ラベル、SPEC §3.1）', () => {
  const WITH_SELECTS = {
    ...OPTIONS,
    image_workflows: [
      {
        id: 'krea2_turbo',
        label: 'Krea 2 Turbo',
        family: 'krea2',
        selects: [
          {
            name: 'quality_profile',
            label: 'フレーム枚数（品質）',
            choices: [
              'recommended | 5 frames',
              'high quality | 13 frames',
              'maximum quality | 20 frames (slow)',
            ],
            default: 'recommended | 5 frames',
            auto: false,
            hint: '',
            choice_labels: {
              'recommended | 5 frames': '標準（5 フレーム）',
              'high quality | 13 frames': '最高品質（13 フレーム）',
            },
          },
        ],
      },
    ],
  } as unknown as Options

  function showSelects() {
    const onChange = vi.fn()
    render(
      <ModelFields data={data()} onChange={onChange} options={WITH_SELECTS} />,
    )
    return { onChange }
  }

  it('生成フォームと同じく、表示だけ日本語にして送る値は生の enum のまま', () => {
    const { onChange } = showSelects()
    const select = screen.getByLabelText(
      'フレーム枚数（品質）',
    ) as HTMLSelectElement
    // 見えているのは日本語（「既定（…）」もラベル側）。宣言の無い値は生のまま。
    expect([...select.options].map((option) => option.text)).toEqual([
      '既定（標準（5 フレーム））',
      '標準（5 フレーム）',
      '最高品質（13 フレーム）',
      'maximum quality | 20 frames (slow)',
    ])
    // 送る値は enum のまま
    expect([...select.options].map((option) => option.value)).toEqual([
      '',
      'recommended | 5 frames',
      'high quality | 13 frames',
      'maximum quality | 20 frames (slow)',
    ])

    fireEvent.change(select, { target: { value: 'high quality | 13 frames' } })
    expect(onChange.mock.calls[0][0].params.selects).toEqual({
      quality_profile: 'high quality | 13 frames',
    })
  })
})
