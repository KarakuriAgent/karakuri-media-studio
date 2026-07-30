import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { Job } from '../types'
import HistoryPickerModal, {
  assetExtension,
  historyCandidates,
  jobText,
} from './HistoryPickerModal'

afterEach(cleanup)

function job(overrides: Partial<Job> = {}): Job {
  return {
    id: 'j1',
    created_at: '2026-07-30T10:00:00+00:00',
    mode: 'full',
    status: 'done',
    user_input: null,
    image_prompt: null,
    video_prompt: null,
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
    video_url: null,
    last_frame_url: null,
    audio_output_url: null,
    ...overrides,
  }
}

const FULL = job({
  id: 'full1',
  video_prompt: 'a dance clip',
  image_url: '/outputs/full1/still.png',
  video_url: '/outputs/full1/clip.mp4',
  last_frame_url: '/outputs/full1/last.png',
})
const IMAGE_ONLY = job({
  id: 'img1',
  mode: 'image_only',
  image_prompt: 'a portrait',
  image_url: '/outputs/img1/still.png',
})
const AUDIO = job({
  id: 'snd1',
  mode: 'audio',
  audio_prompt: 'a lofi loop',
  audio_output_url: '/outputs/snd1/track.mp3',
})
const NSFW_IMAGE = job({
  id: 'nsfw1',
  mode: 'image_only',
  image_prompt: 'spicy',
  image_url: '/outputs/nsfw1/still.png',
  nsfw: true,
})

describe('historyCandidates', () => {
  it('画像入力には生成画像とラストフレームの両方を出す', () => {
    const picked = historyCandidates([FULL, IMAGE_ONLY], 'image', false)
    expect(picked.map((item) => [item.job.id, item.source, item.label])).toEqual([
      ['full1', 'image', '生成画像'],
      ['full1', 'last_frame', 'ラストフレーム'],
      ['img1', 'image', '生成画像'],
    ])
    // key はジョブ id だけでは足りない（1 ジョブから 2 件出る）
    expect(new Set(picked.map((item) => item.id)).size).toBe(3)
    expect(picked[1].url).toBe('/outputs/full1/last.png')
  })

  it('動画入力は動画を持つジョブだけ（サムネはラストフレームで代用）', () => {
    const picked = historyCandidates([FULL, IMAGE_ONLY, AUDIO], 'video', false)
    expect(picked.map((item) => item.job.id)).toEqual(['full1'])
    expect(picked[0].url).toBe('/outputs/full1/clip.mp4')
    expect(picked[0].thumb).toBe('/outputs/full1/last.png')
  })

  it('音声入力は音声ジョブの出力だけ（サムネなし）', () => {
    const picked = historyCandidates([FULL, IMAGE_ONLY, AUDIO], 'audio', false)
    expect(picked.map((item) => item.job.id)).toEqual(['snd1'])
    expect(picked[0].url).toBe('/outputs/snd1/track.mp3')
    expect(picked[0].thumb).toBeNull()
  })

  it('完了していないジョブは出さない', () => {
    const running = job({ id: 'r1', status: 'running', image_url: '/outputs/r1/x.png' })
    const failed = job({ id: 'f1', status: 'failed', image_url: '/outputs/f1/x.png' })
    expect(historyCandidates([running, failed], 'image', true)).toEqual([])
  })

  it('NSFW はチェックが入っているときだけ出す', () => {
    expect(
      historyCandidates([IMAGE_ONLY, NSFW_IMAGE], 'image', false).map((i) => i.job.id),
    ).toEqual(['img1'])
    expect(
      historyCandidates([IMAGE_ONLY, NSFW_IMAGE], 'image', true).map((i) => i.job.id),
    ).toEqual(['img1', 'nsfw1'])
  })

  it('query でジョブの文言を絞り込む', () => {
    const picked = historyCandidates([FULL, IMAGE_ONLY], 'image', false, 'dance')
    // FULL は video_prompt が "a dance clip"
    expect(picked.map((item) => item.job.id)).toEqual(['full1', 'full1'])
    // 大文字小文字は無視する
    expect(
      historyCandidates([FULL, IMAGE_ONLY], 'image', false, 'PORTRAIT').map(
        (item) => item.job.id,
      ),
    ).toEqual(['img1'])
    // 空白だけの query は絞り込まない
    expect(historyCandidates([FULL, IMAGE_ONLY], 'image', false, '  ')).toHaveLength(3)
    expect(historyCandidates([FULL, IMAGE_ONLY], 'image', false, 'ghost')).toEqual([])
  })

  it('NSFW フィルタと検索は両方かかる', () => {
    const spicy = job({
      id: 'nsfw2',
      image_prompt: 'a spicy dance',
      image_url: '/outputs/nsfw2/x.png',
      nsfw: true,
    })
    expect(
      historyCandidates([FULL, spicy], 'image', false, 'dance').map((i) => i.job.id),
    ).toEqual(['full1', 'full1'])
    expect(
      historyCandidates([FULL, spicy], 'image', true, 'spicy').map((i) => i.job.id),
    ).toEqual(['nsfw2'])
  })

  it('渡された順（API の新しい順）を保つ', () => {
    const older = job({ id: 'old', image_url: '/outputs/old/x.png' })
    expect(
      historyCandidates([IMAGE_ONLY, older], 'image', false).map((i) => i.job.id),
    ).toEqual(['img1', 'old'])
  })
})

describe('jobText', () => {
  it('動画 → 画像 → 音声 → 最初の指示 → id の順に拾う', () => {
    expect(jobText(FULL)).toBe('a dance clip')
    expect(jobText(IMAGE_ONLY)).toBe('a portrait')
    expect(jobText(AUDIO)).toBe('a lofi loop')
    expect(jobText(job({ id: 'bare' }))).toBe('bare')
  })
})

describe('assetExtension', () => {
  it('URL の拡張子を使う（アセットに許されるものだけ）', () => {
    expect(assetExtension('/outputs/a/still.PNG', 'image')).toBe('.png')
    expect(assetExtension('/outputs/a/clip.webm', 'video')).toBe('.webm')
    expect(assetExtension('/outputs/a/track.flac', 'audio')).toBe('.flac')
    expect(assetExtension('/outputs/a/x.png?v=2#f', 'image')).toBe('.png')
  })

  it('許されない・分からない拡張子は種別の既定にする', () => {
    expect(assetExtension('/outputs/a/still.gif', 'image')).toBe('.png')
    expect(assetExtension('/outputs/a/clip', 'video')).toBe('.mp4')
    expect(assetExtension('', 'audio')).toBe('.mp3')
  })
})

describe('HistoryPickerModal', () => {
  function show(overrides: { showNsfw?: boolean } = {}) {
    const onSelect = vi.fn()
    const onClose = vi.fn()
    render(
      <HistoryPickerModal
        kind="image"
        title="履歴から選択: 最後のフレーム"
        jobs={[IMAGE_ONLY, NSFW_IMAGE]}
        showNsfw={overrides.showNsfw ?? false}
        onSelect={onSelect}
        onClose={onClose}
      />,
    )
    return { onSelect, onClose }
  }

  it('候補をタイルで出し、選ぶとその候補を返す', () => {
    const { onSelect } = show()
    expect(screen.getByText('履歴から選択: 最後のフレーム')).toBeTruthy()
    expect(screen.getByText('1 件')).toBeTruthy()
    screen.getByTitle('a portrait').click()
    expect(onSelect).toHaveBeenCalledWith(
      expect.objectContaining({ url: '/outputs/img1/still.png', source: 'image' }),
    )
  })

  it('検索ボックスでプロンプトから絞り込む', () => {
    show({ showNsfw: true })
    expect(screen.getByText('2 件')).toBeTruthy()
    fireEvent.change(screen.getByLabelText('履歴を検索'), {
      target: { value: 'spicy' },
    })
    expect(screen.getByText('1 件')).toBeTruthy()
    expect(screen.queryByTitle('a portrait')).toBeNull()
    fireEvent.change(screen.getByLabelText('履歴を検索'), {
      target: { value: 'ghost' },
    })
    expect(screen.getByText('検索に一致する生成物がありません。')).toBeTruthy()
  })

  it('NSFW 表示はモーダル内で切り替えられる', () => {
    show()
    expect(screen.queryByTitle('spicy')).toBeNull()
    fireEvent.click(screen.getByLabelText('🫣 NSFW表示'))
    expect(screen.getByTitle('spicy')).toBeTruthy()
    expect(screen.getByText('2 件')).toBeTruthy()
  })

  it('グローバル設定がオンなら最初から NSFW を出す', () => {
    show({ showNsfw: true })
    expect(screen.getByTitle('spicy')).toBeTruthy()
  })

  it('候補が無ければ種別ごとの案内を出す', () => {
    render(
      <HistoryPickerModal
        kind="audio"
        title="履歴から選択: リファレンス音声"
        jobs={[IMAGE_ONLY]}
        showNsfw={false}
        onSelect={vi.fn()}
        onClose={vi.fn()}
      />,
    )
    expect(screen.getByText(/履歴に使える音声がまだありません/)).toBeTruthy()
  })

  it('Esc と背景クリックで閉じる', () => {
    const { onClose } = show()
    fireEvent.keyDown(window, { key: 'Escape' })
    expect(onClose).toHaveBeenCalledTimes(1)
    // 背景（オーバーレイ）クリック。パネル内のクリックでは閉じない
    fireEvent.click(screen.getByText('1 件'))
    expect(onClose).toHaveBeenCalledTimes(1)
    fireEvent.click(document.querySelector('.fixed.inset-0') as Element)
    expect(onClose).toHaveBeenCalledTimes(2)
  })
})
