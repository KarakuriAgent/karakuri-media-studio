import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { Job } from '../types'
import ResultPane from './ResultPane'

afterEach(cleanup)

function job(overrides: Partial<Job> = {}): Job {
  return {
    id: 'j1',
    created_at: '2026-07-30T10:00:00+00:00',
    mode: 'full',
    status: 'done',
    user_input: null,
    image_prompt: null,
    video_prompt: 'a dance clip',
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
    ...overrides,
  }
}

function show(current: Job | null, extras: { busy?: boolean } = {}) {
  const onCancel = vi.fn<(target: Job) => void>()
  render(
    <ResultPane
      job={current}
      progress={undefined}
      onRerun={() => {}}
      onRestoreParams={() => {}}
      onContinue={() => {}}
      onDelete={() => {}}
      onCancel={onCancel}
      onOpenDetail={() => {}}
      onToggleNsfw={() => {}}
      busy={extras.busy ?? false}
      queue={[]}
      showNsfw
    />,
  )
  return { onCancel }
}

describe('ResultPane の停止', () => {
  it.each(['queued', 'prompting', 'running'] as const)(
    '%s のとき停止ボタンが出る',
    (status) => {
      show(job({ status, video_url: null, image_url: null, last_frame_url: null }))
      expect(screen.queryByRole('button', { name: '停止' })).not.toBeNull()
    },
  )

  it('完了済みでは停止ボタンを出さない', () => {
    show(job())
    expect(screen.queryByRole('button', { name: '停止' })).toBeNull()
  })

  it('停止を押すと onCancel に今のジョブを渡す', () => {
    const current = job({ status: 'running' })
    const { onCancel } = show(current)
    fireEvent.click(screen.getByRole('button', { name: '停止' }))
    expect(onCancel).toHaveBeenCalledTimes(1)
    expect(onCancel).toHaveBeenCalledWith(current)
  })

  it('busy 中は停止できない', () => {
    show(job({ status: 'running' }), { busy: true })
    expect((screen.getByRole('button', { name: '停止' }) as HTMLButtonElement).disabled).toBe(
      true,
    )
  })
})

describe('ResultPane の所要時間', () => {
  it('完了したジョブは生成にかかった時間を出す', () => {
    show(
      job({
        started_at: '2026-07-30T10:00:05+00:00',
        finished_at: '2026-07-30T10:01:28+00:00',
      }),
    )
    expect(screen.queryByText('生成 1分23秒')).not.toBeNull()
  })

  it('実行中は開始からの経過を出す', () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-07-30T10:00:42+00:00'))
    try {
      show(
        job({
          status: 'running',
          started_at: '2026-07-30T10:00:00+00:00',
          video_url: null,
          image_url: null,
          last_frame_url: null,
        }),
      )
      expect(screen.queryByText('経過 0:42')).not.toBeNull()
    } finally {
      vi.useRealTimers()
    }
  })

  it('started_at を持たない過去のジョブでは何も出さない', () => {
    show(job({ started_at: null, finished_at: null }))
    expect(screen.queryByText(/生成 /)).toBeNull()
    expect(screen.queryByText(/経過 /)).toBeNull()
  })
})
