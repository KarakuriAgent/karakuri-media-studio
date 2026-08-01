import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { Job } from '../types'
import VeoActions from './VeoActions'

afterEach(cleanup)

function job(overrides: Partial<Job> = {}): Job {
  return {
    id: 'j1',
    created_at: '2026-08-02T10:00:00+00:00',
    mode: 'i2v',
    status: 'done',
    user_input: null,
    image_prompt: null,
    video_prompt: 'a rooftop shot',
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
    image_url: null,
    video_url: '/outputs/j1/video.mp4',
    last_frame_url: '/outputs/j1/last.png',
    audio_output_url: null,
    ...overrides,
  }
}

describe('VeoActions', () => {
  it('バックエンドが出した followups のぶんだけボタンを出す', () => {
    const { rerender } = render(
      <VeoActions job={job()} busy={false} onExtend={vi.fn()} onUpscale={vi.fn()} />,
    )
    // followups が無ければ何も出ない（ComfyUI のジョブなど）
    expect(screen.queryByText(/延長/)).toBeNull()
    expect(screen.queryByText('1080P を取得')).toBeNull()

    rerender(
      <VeoActions
        job={job({ followups: ['veo_extend'] })}
        busy={false}
        onExtend={vi.fn()}
        onUpscale={vi.fn()}
      />,
    )
    expect(screen.getByText('延長（+7 秒）')).toBeTruthy()
    // 1080p で生成済みのジョブには 1080P 取得を出さない
    expect(screen.queryByText('1080P を取得')).toBeNull()
  })

  it('1080P 取得はそのまま実行する（入力は要らない）', () => {
    const onUpscale = vi.fn()
    render(
      <VeoActions
        job={job({ followups: ['veo_extend', 'veo_1080p'] })}
        busy={false}
        onExtend={vi.fn()}
        onUpscale={onUpscale}
      />,
    )
    fireEvent.click(screen.getByText('1080P を取得'))
    expect(onUpscale).toHaveBeenCalledTimes(1)
    expect(onUpscale.mock.calls[0][0].id).toBe('j1')
  })

  it('延長は続きの指示を入れてから実行する', () => {
    const onExtend = vi.fn()
    render(
      <VeoActions
        job={job({ followups: ['veo_extend'] })}
        busy={false}
        onExtend={onExtend}
        onUpscale={vi.fn()}
      />,
    )
    fireEvent.click(screen.getByText('延長（+7 秒）'))
    const run = screen.getByText('延長を実行') as HTMLButtonElement
    // 空のままでは実行できない
    expect(run.disabled).toBe(true)

    fireEvent.change(screen.getByRole('textbox'), {
      target: { value: '  She keeps walking.  ' },
    })
    fireEvent.click(screen.getByText('延長を実行'))

    expect(onExtend).toHaveBeenCalledTimes(1)
    expect(onExtend.mock.calls[0][1]).toBe('She keeps walking.')
    // 実行したら閉じる
    expect(screen.queryByText('延長を実行')).toBeNull()
  })

  it('キャンセルすると実行しない', () => {
    const onExtend = vi.fn()
    render(
      <VeoActions
        job={job({ followups: ['veo_extend'] })}
        busy={false}
        onExtend={onExtend}
        onUpscale={vi.fn()}
      />,
    )
    fireEvent.click(screen.getByText('延長（+7 秒）'))
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'more' } })
    fireEvent.click(screen.getByText('キャンセル'))
    expect(onExtend).not.toHaveBeenCalled()
    expect(screen.queryByRole('textbox')).toBeNull()
  })

  it('実行中はボタンを押せない', () => {
    render(
      <VeoActions
        job={job({ followups: ['veo_extend', 'veo_1080p'] })}
        busy
        onExtend={vi.fn()}
        onUpscale={vi.fn()}
      />,
    )
    expect((screen.getByText('延長（+7 秒）') as HTMLButtonElement).disabled).toBe(true)
    expect((screen.getByText('1080P を取得') as HTMLButtonElement).disabled).toBe(true)
  })
})
