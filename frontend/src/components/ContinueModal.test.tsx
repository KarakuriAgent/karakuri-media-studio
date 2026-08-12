import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { Job, Options } from '../types'
import ContinueModal, { continuePayload } from './ContinueModal'

afterEach(cleanup)

const OPTIONS = {
  aspect_ratios: ['4:3 (Standard)', '16:9 (Widescreen)'],
  video_workflows: [],
  model_slots: [],
} as unknown as Options

function job(): Job {
  return {
    id: 'j1',
    params: {
      video_workflow: 'minimax_h3_i2v',
      video_prompt: 'she dances',
      duration: 5,
      aspect_ratio: '4:3 (Standard)',
    },
  } as unknown as Job
}

function show() {
  const onSubmit = vi.fn()
  render(
    <ContinueModal
      job={job()}
      options={OPTIONS}
      busy={false}
      error={null}
      onClose={() => {}}
      onSubmit={onSubmit}
    />,
  )
  return { onSubmit }
}

describe('continuePayload', () => {
  it('空欄は送らない（＝元ジョブを引き継ぐ）', () => {
    expect(continuePayload({}, {})).toEqual({})
    expect(continuePayload({ video_prompt: '  ', duration: '' }, {})).toEqual({})
  })

  it('埋めた項目だけ送る', () => {
    expect(
      continuePayload({ video_prompt: ' 続き ', duration: '3', fps: 'x' }, {}),
    ).toEqual({ video_prompt: '続き', duration: 3 })
  })

  it('モデル指定は 1 つでも選ばれたときだけ載せる', () => {
    expect(continuePayload({}, {})).toEqual({})
    expect(continuePayload({}, { 'w/1.unet': 'a.safetensors' })).toEqual({
      model_overrides: { 'w/1.unet': 'a.safetensors' },
    })
  })
})

describe('ContinueModal', () => {
  it('［そのまま続き生成］は空ボディで投げる', () => {
    const { onSubmit } = show()
    fireEvent.click(screen.getByRole('button', { name: 'そのまま続き生成' }))
    expect(onSubmit).toHaveBeenCalledWith({})
  })

  it('埋めた項目だけが上書きとして送られる', () => {
    const { onSubmit } = show()
    fireEvent.change(screen.getByLabelText('動画プロンプト'), {
      target: { value: 'she walks away' },
    })
    fireEvent.change(screen.getByLabelText('長さ（秒）'), {
      target: { value: '4' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'この設定で続き生成' }))
    expect(onSubmit).toHaveBeenCalledWith({
      video_prompt: 'she walks away',
      duration: 4,
    })
  })

  it('引き継ぐ値はプレースホルダで見える', () => {
    show()
    expect(
      (screen.getByLabelText('動画プロンプト') as HTMLTextAreaElement).placeholder,
    ).toBe('元ジョブを引き継ぐ（she dances）')
    expect(
      (screen.getByLabelText('fps') as HTMLInputElement).placeholder,
    ).toBe('元ジョブを引き継ぐ')
  })
})
