import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '../api'
import type { LibraryItem, LibraryPage, LibraryQuery } from '../types'
import LibraryPickerModal, { libraryItemsOf } from './LibraryPickerModal'

vi.mock('../api', () => ({
  api: {
    listLibrary: vi.fn(),
    uploadToLibrary: vi.fn(),
    updateLibraryItem: vi.fn(),
    deleteLibraryItem: vi.fn(),
  },
}))

const listLibrary = vi.mocked(api.listLibrary)
const updateLibraryItem = vi.mocked(api.updateLibraryItem)
const deleteLibraryItem = vi.mocked(api.deleteLibraryItem)

afterEach(cleanup)

function item(overrides: Partial<LibraryItem> = {}): LibraryItem {
  return {
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
    tags: ['キャラ'],
    category: null,
    ...overrides,
  }
}

const IMAGE = item()
const CLIP = item({ id: 'l2', kind: 'video', name: '参考モーション', tags: [] })
const TRACK = item({ id: 'l3', kind: 'audio', name: 'BGM', tags: ['音楽'] })
const SPICY = item({ id: 'l4', name: 'ひみつ', nsfw: true, tags: [] })

function pageOf(items: LibraryItem[], extra: Partial<LibraryPage> = {}): LibraryPage {
  return {
    items,
    total: items.length,
    limit: 50,
    offset: 0,
    tags: ['キャラ', '音楽'],
    ...extra,
  }
}

describe('libraryItemsOf', () => {
  const ALL = [IMAGE, CLIP, TRACK, SPICY]

  it('種別で絞る', () => {
    expect(libraryItemsOf(ALL, 'video', true).map((i) => i.id)).toEqual(['l2'])
    expect(libraryItemsOf(ALL, 'audio', true).map((i) => i.id)).toEqual(['l3'])
    expect(libraryItemsOf(ALL, 'image', true).map((i) => i.id)).toEqual(['l1', 'l4'])
  })

  it('NSFW はチェックが入っているときだけ出す', () => {
    expect(libraryItemsOf(ALL, 'image', false).map((i) => i.id)).toEqual(['l1'])
  })

  it('未取得（undefined）でも落ちない', () => {
    expect(libraryItemsOf(undefined, 'image', true)).toEqual([])
  })

  it('サーバーの並び（新しい順）をそのまま保つ', () => {
    const older = item({ id: 'old', name: '古い' })
    expect(libraryItemsOf([IMAGE, older], 'image', false).map((i) => i.id)).toEqual([
      'l1',
      'old',
    ])
  })
})

describe('LibraryPickerModal', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    listLibrary.mockResolvedValue(pageOf([IMAGE, SPICY]))
  })

  function show(overrides: { showNsfw?: boolean; kind?: 'image' | 'video' } = {}) {
    const onSelect = vi.fn()
    const onClose = vi.fn()
    const onChanged = vi.fn()
    render(
      <LibraryPickerModal
        kind={overrides.kind ?? 'image'}
        title="ライブラリから選択: 最後のフレーム"
        showNsfw={overrides.showNsfw ?? false}
        onSelect={onSelect}
        onClose={onClose}
        onChanged={onChanged}
      />,
    )
    return { onSelect, onClose, onChanged }
  }

  /** 直近の listLibrary 呼び出しのクエリ。 */
  function lastQuery(): LibraryQuery {
    const calls = listLibrary.mock.calls
    return calls[calls.length - 1][0] as LibraryQuery
  }

  it('開いた時点で種別を絞って読み込む', async () => {
    show()
    await waitFor(() => expect(listLibrary).toHaveBeenCalled())
    expect(lastQuery()).toMatchObject({ kind: 'image', q: '', offset: 0, limit: 50 })
    expect(await screen.findByText('決めポーズ')).toBeTruthy()
    // NSFW は既定で隠れ、その旨を件数に出す
    expect(screen.getByText(/1 件表示 \/ 全 2 件（NSFW 1 件を非表示）/)).toBeTruthy()
  })

  it('タグをタイルに出す', async () => {
    show()
    expect(await screen.findByText('#キャラ')).toBeTruthy()
  })

  it('検索ボックスの入力をクエリとしてサーバーに送る', async () => {
    show()
    await waitFor(() => expect(listLibrary).toHaveBeenCalled())
    fireEvent.change(screen.getByLabelText('ライブラリを検索'), {
      target: { value: 'ポーズ' },
    })
    await waitFor(() => expect(lastQuery().q).toBe('ポーズ'))
    expect(lastQuery().offset).toBe(0)
  })

  it('タグチップで絞り込み、もう一度押すと解除する', async () => {
    show()
    const chip = await screen.findByRole('button', { name: 'キャラ' })
    fireEvent.click(chip)
    await waitFor(() => expect(lastQuery().tag).toBe('キャラ'))
    fireEvent.click(screen.getByRole('button', { name: 'キャラ' }))
    await waitFor(() => expect(lastQuery().tag).toBeUndefined())
  })

  it('カテゴリのプルダウンで絞り込む（未分類も選べる）', async () => {
    show()
    await waitFor(() => expect(listLibrary).toHaveBeenCalled())
    // 既定は「すべてのカテゴリ」なので分類では絞らない
    expect(lastQuery().category).toBeUndefined()

    const select = screen.getByLabelText('カテゴリで絞り込み')
    fireEvent.change(select, { target: { value: 'character' } })
    await waitFor(() => expect(lastQuery().category).toBe('character'))
    expect(lastQuery().offset).toBe(0)

    fireEvent.change(select, { target: { value: 'none' } })
    await waitFor(() => expect(lastQuery().category).toBe('none'))

    fireEvent.change(select, { target: { value: '' } })
    await waitFor(() => expect(lastQuery().category).toBeUndefined())
  })

  it('タイルのプルダウンでカテゴリを変える（未分類にも戻せる）', async () => {
    listLibrary.mockResolvedValue(pageOf([item({ category: 'prop' })]))
    const { onChanged } = show()
    const select = (await screen.findByLabelText(
      '「決めポーズ」のカテゴリ',
    )) as HTMLSelectElement
    expect(select.value).toBe('prop')

    updateLibraryItem.mockResolvedValue(item({ category: 'character' }))
    fireEvent.change(select, { target: { value: 'character' } })
    await waitFor(() =>
      expect(updateLibraryItem).toHaveBeenCalledWith('l1', {
        category: 'character',
      }),
    )
    expect(onChanged).toHaveBeenCalled()

    // 'none' は「未分類に戻す」の明示指定
    fireEvent.change(select, { target: { value: 'none' } })
    await waitFor(() =>
      expect(updateLibraryItem).toHaveBeenLastCalledWith('l1', {
        category: 'none',
      }),
    )
  })

  it('分類で絞り込みながら足したものは、そのカテゴリで登録する', async () => {
    show()
    await waitFor(() => expect(listLibrary).toHaveBeenCalled())
    fireEvent.change(screen.getByLabelText('カテゴリで絞り込み'), {
      target: { value: 'background' },
    })
    const file = new File(['x'], 'bg.png', { type: 'image/png' })
    const input = document.querySelector('input[type=file]') as HTMLInputElement
    fireEvent.change(input, { target: { files: [file] } })
    await waitFor(() =>
      expect(api.uploadToLibrary).toHaveBeenCalledWith(
        'image',
        file,
        [],
        'background',
      ),
    )
  })

  it('選ぶとその素材を返す', async () => {
    const { onSelect } = show()
    fireEvent.click(await screen.findByText('決めポーズ'))
    expect(onSelect).toHaveBeenCalledWith(IMAGE)
  })

  it('NSFW 表示はモーダル内で切り替えられる', async () => {
    show()
    await screen.findByText('決めポーズ')
    expect(screen.queryByText('ひみつ')).toBeNull()
    fireEvent.click(screen.getByLabelText('🫣 NSFW表示'))
    expect(screen.getByText('ひみつ')).toBeTruthy()
    expect(screen.getByText('2 件表示 / 全 2 件')).toBeTruthy()
  })

  it('続きがあるときだけ「さらに表示」を出し、offset を進めて継ぎ足す', async () => {
    listLibrary.mockResolvedValue(pageOf([IMAGE], { total: 2 }))
    show()
    const more = await screen.findByRole('button', { name: /さらに表示（残り 1 件）/ })

    listLibrary.mockResolvedValue(
      pageOf([item({ id: 'l9', name: '二枚目' })], { total: 2, offset: 1 }),
    )
    fireEvent.click(more)
    await waitFor(() => expect(lastQuery().offset).toBe(1))
    expect(await screen.findByText('二枚目')).toBeTruthy()
    // 1 ページ目も残る（継ぎ足し）
    expect(screen.getByText('決めポーズ')).toBeTruthy()
    expect(screen.queryByRole('button', { name: /さらに表示/ })).toBeNull()
  })

  it('タグを編集すると PATCH して一覧を取り直す', async () => {
    const { onChanged } = show()
    await screen.findByText('決めポーズ')
    vi.spyOn(window, 'prompt').mockReturnValue('背景, 夜景')
    updateLibraryItem.mockResolvedValue(item({ tags: ['背景', '夜景'] }))

    fireEvent.click(screen.getByRole('button', { name: 'タグ' }))
    await waitFor(() =>
      expect(updateLibraryItem).toHaveBeenCalledWith('l1', {
        tags: ['背景', '夜景'],
      }),
    )
    expect(onChanged).toHaveBeenCalled()
  })

  it('タグ編集をキャンセルしたら何もしない', async () => {
    show()
    await screen.findByText('決めポーズ')
    vi.spyOn(window, 'prompt').mockReturnValue(null)
    fireEvent.click(screen.getByRole('button', { name: 'タグ' }))
    expect(updateLibraryItem).not.toHaveBeenCalled()
  })

  it('削除は確認してから消す', async () => {
    show()
    await screen.findByText('決めポーズ')
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    deleteLibraryItem.mockResolvedValue(undefined)
    fireEvent.click(screen.getByRole('button', { name: '削除' }))
    await waitFor(() => expect(deleteLibraryItem).toHaveBeenCalledWith('l1'))
  })

  it('空のときは条件の有無で案内を出し分ける', async () => {
    listLibrary.mockResolvedValue(pageOf([]))
    show({ kind: 'video' })
    expect(await screen.findByText(/ライブラリに動画がありません/)).toBeTruthy()

    fireEvent.change(screen.getByLabelText('ライブラリを検索'), {
      target: { value: 'ghost' },
    })
    expect(await screen.findByText('条件に合う素材がありません。')).toBeTruthy()
  })

  it('読み込みに失敗したらメッセージを出す', async () => {
    listLibrary.mockRejectedValue(new Error('バックエンドに接続できません'))
    show()
    expect(await screen.findByText('バックエンドに接続できません')).toBeTruthy()
  })

  it('Esc と背景クリックで閉じる', async () => {
    const { onClose } = show()
    await screen.findByText('決めポーズ')
    fireEvent.keyDown(window, { key: 'Escape' })
    expect(onClose).toHaveBeenCalledTimes(1)
    fireEvent.click(document.querySelector('.fixed.inset-0') as Element)
    expect(onClose).toHaveBeenCalledTimes(2)
  })
})
