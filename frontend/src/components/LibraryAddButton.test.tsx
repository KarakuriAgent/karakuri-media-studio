import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError, api } from '../api'
import type { Job, LibraryItem } from '../types'
import LibraryAddButton, { isInLibrary, librarySourcesOf } from './LibraryAddButton'

vi.mock('../api', async () => {
  const actual = await vi.importActual<typeof import('../api')>('../api')
  return { ...actual, api: { addJobToLibrary: vi.fn() } }
})

const addJobToLibrary = vi.mocked(api.addJobToLibrary)

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

function libraryItem(overrides: Partial<LibraryItem> = {}): LibraryItem {
  return {
    id: 'l1',
    created_at: '2026-07-30T10:00:00+00:00',
    kind: 'image',
    name: '決めポーズ',
    path: '/repo/library/image/pose.png',
    url: '/library/image/pose.png',
    nsfw: false,
    nsfw_source: '',
    source_job_id: 'j1',
    source: 'image',
    tags: [],
    category: null,
    ...overrides,
  }
}

describe('librarySourcesOf', () => {
  it('その job が持っている出力だけを並べる', () => {
    expect(librarySourcesOf(job())).toEqual([
      { source: 'video', label: '動画' },
      { source: 'image', label: '生成画像' },
      { source: 'last_frame', label: 'ラストフレーム' },
    ])
    expect(
      librarySourcesOf(
        job({
          video_url: null,
          image_url: null,
          last_frame_url: null,
          audio_output_url: '/outputs/j1/track.mp3',
        }),
      ),
    ).toEqual([{ source: 'audio', label: '音声' }])
    expect(
      librarySourcesOf(
        job({ video_url: null, image_url: null, last_frame_url: null }),
      ),
    ).toEqual([])
  })
})

describe('isInLibrary', () => {
  it('同じジョブの同じ出力だけを登録済みと見なす', () => {
    const library = [libraryItem()]
    expect(isInLibrary(library, job(), 'image')).toBe(true)
    // 同じジョブでも別の出力は未登録
    expect(isInLibrary(library, job(), 'last_frame')).toBe(false)
    // 別ジョブは無関係
    expect(isInLibrary(library, job({ id: 'j2' }), 'image')).toBe(false)
    // アップロード（source が null）は判定に効かない
    expect(
      isInLibrary([libraryItem({ source: null, source_job_id: null })], job(), 'image'),
    ).toBe(false)
    expect(isInLibrary(undefined, job(), 'image')).toBe(false)
  })
})

describe('LibraryAddButton', () => {
  beforeEach(() => vi.clearAllMocks())

  it('押すと登録し、結果を短く出す', async () => {
    const onAdded = vi.fn()
    addJobToLibrary.mockResolvedValue(libraryItem())
    render(<LibraryAddButton job={job()} source="image" onAdded={onAdded} />)

    const button = screen.getByRole('button', { name: '☆ ライブラリに登録' })
    fireEvent.click(button)
    // カテゴリを触らなければ未分類（'none'）で登録する
    await waitFor(() =>
      expect(addJobToLibrary).toHaveBeenCalledWith('j1', 'image', '', [], 'none'),
    )
    expect(await screen.findByRole('button', { name: '★ 登録しました' })).toBeTruthy()
    expect(onAdded).toHaveBeenCalled()
  })

  it('出力名をラベルに出す', () => {
    render(<LibraryAddButton job={job()} source="last_frame" label="ラストフレーム" />)
    expect(
      screen.getByRole('button', { name: '☆ ライブラリに登録: ラストフレーム' }),
    ).toBeTruthy()
  })

  it('409 は失敗ではなく「登録済みです」として出す', async () => {
    addJobToLibrary.mockRejectedValue(
      new ApiError(409, {
        message: '「決めポーズ」は登録済みです',
        item: libraryItem(),
      }),
    )
    render(<LibraryAddButton job={job()} source="image" />)
    fireEvent.click(screen.getByRole('button', { name: '☆ ライブラリに登録' }))

    const done = await screen.findByRole('button', { name: '★ 登録済みです' })
    // 押し直しても 409 を繰り返さないよう止め、理由は title に出す
    expect(done.hasAttribute('disabled')).toBe(true)
    expect(done.getAttribute('title')).toBe('「決めポーズ」は登録済みです')
    expect(done.className).not.toContain('text-red-300')
  })

  it('それ以外の失敗は赤くして理由を出す', async () => {
    addJobToLibrary.mockRejectedValue(new ApiError(400, 'job has no 動画'))
    render(<LibraryAddButton job={job()} source="video" />)
    fireEvent.click(screen.getByRole('button', { name: '☆ ライブラリに登録' }))

    const failed = await screen.findByRole('button', { name: '登録できません' })
    expect(failed.className).toContain('text-red-300')
    expect(failed.getAttribute('title')).toBe('job has no 動画')
    // 直せる可能性があるので押し直せる
    expect(failed.hasAttribute('disabled')).toBe(false)
  })

  it('選んだカテゴリを付けて登録する', async () => {
    addJobToLibrary.mockResolvedValue(libraryItem({ category: 'character' }))
    render(<LibraryAddButton job={job()} source="image" />)

    const select = screen.getByLabelText('登録するカテゴリ') as HTMLSelectElement
    // 既定は未分類
    expect(select.value).toBe('none')
    fireEvent.change(select, { target: { value: 'character' } })
    fireEvent.click(screen.getByRole('button', { name: '☆ ライブラリに登録' }))

    await waitFor(() =>
      expect(addJobToLibrary).toHaveBeenCalledWith(
        'j1',
        'image',
        '',
        [],
        'character',
      ),
    )
    // 登録し終わったら分類はライブラリ側で変えるものなので、選択欄は引っ込める
    expect(await screen.findByRole('button', { name: '★ 登録しました' })).toBeTruthy()
    expect(screen.queryByLabelText('登録するカテゴリ')).toBeNull()
  })

  it('登録済みと分かっているときは押す前からそう出す', () => {
    render(<LibraryAddButton job={job()} source="image" registered />)
    const button = screen.getByRole('button', { name: '★ 登録済みです' })
    expect(button.hasAttribute('disabled')).toBe(true)
    expect(addJobToLibrary).not.toHaveBeenCalled()
  })
})
