import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '../api'
import { SHEET_MAX_ITEMS } from '../form'
import type { LibraryItem, LibraryPage } from '../types'
import SheetBuilderModal from './SheetBuilderModal'

vi.mock('../api', () => ({
  api: {
    listLibrary: vi.fn(),
    uploadToLibrary: vi.fn(),
    updateLibraryItem: vi.fn(),
    deleteLibraryItem: vi.fn(),
    createLibrarySheet: vi.fn(),
  },
}))

const listLibrary = vi.mocked(api.listLibrary)
const createLibrarySheet = vi.mocked(api.createLibrarySheet)

afterEach(cleanup)

function item(id: string, name: string, overrides: Partial<LibraryItem> = {}): LibraryItem {
  return {
    id,
    created_at: '2026-07-30T10:00:00+00:00',
    kind: 'image',
    name,
    path: `/repo/library/image/${id}.png`,
    url: `/library/image/${id}.png`,
    nsfw: false,
    nsfw_source: '',
    source_job_id: null,
    source: null,
    tags: [],
    category: null,
    ...overrides,
  }
}

const HERO = item('l1', 'サクラ', { category: 'character' })
const SWORD = item('l2', '刀')
const ROOM = item('l3', '教室', { category: 'background' })

function pageOf(items: LibraryItem[]): LibraryPage {
  return { items, total: items.length, limit: 50, offset: 0, tags: [] }
}

function show(items: LibraryItem[] = [HERO, SWORD, ROOM]) {
  listLibrary.mockResolvedValue(pageOf(items))
  const onCreated = vi.fn()
  const onClose = vi.fn()
  const onChanged = vi.fn()
  render(
    <SheetBuilderModal
      showNsfw={false}
      width={1280}
      height={720}
      onCreated={onCreated}
      onClose={onClose}
      onChanged={onChanged}
    />,
  )
  return { onCreated, onClose, onChanged }
}

/** 一覧のタイル（表示名で押す）。 */
async function pick(name: string) {
  fireEvent.click(await screen.findByText(name))
}

function createButton(): HTMLButtonElement {
  return screen.getByRole('button', { name: 'この順で作成' }) as HTMLButtonElement
}

describe('SheetBuilderModal', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('画像の素材だけを取りに行く', async () => {
    show()
    await waitFor(() =>
      expect(listLibrary).toHaveBeenCalledWith(
        expect.objectContaining({ kind: 'image' }),
      ),
    )
  })

  it('1 枚では作れず、2 枚選ぶと作れるようになる', async () => {
    show()
    expect(createButton().disabled).toBe(true)

    await pick('サクラ')
    expect(screen.getByText(/1 枚選択/)).toBeTruthy()
    expect(createButton().disabled).toBe(true)

    await pick('刀')
    expect(createButton().disabled).toBe(false)
  })

  it('選んだ順にバッジを振り、もう一度押すと外れる', async () => {
    show()
    await pick('刀')
    await pick('サクラ')
    // 押した順がそのままシートの並び順（1 = 刀、2 = サクラ）
    expect(screen.getByLabelText('「刀」の選択順').textContent).toBe('1')
    expect(screen.getByLabelText('「サクラ」の選択順').textContent).toBe('2')

    await pick('刀')
    expect(screen.queryByLabelText('「刀」の選択順')).toBeNull()
    // 詰めるので、残ったものが 1 番になる
    expect(screen.getByLabelText('「サクラ」の選択順').textContent).toBe('1')
  })

  it('選んだ順と大きさを添えてシートを作り、出来上がりを返す', async () => {
    const sheet = item('l9', 'リファレンスシート（サクラほか1件）', {
      category: 'character',
      source: 'sheet',
      tags: ['reference-sheet'],
    })
    createLibrarySheet.mockResolvedValue(sheet)
    const { onCreated, onChanged } = show()

    await pick('サクラ')
    await pick('教室')
    fireEvent.click(createButton())

    await waitFor(() =>
      expect(createLibrarySheet).toHaveBeenCalledWith(['l1', 'l3'], {
        width: 1280,
        height: 720,
      }),
    )
    await waitFor(() => expect(onCreated).toHaveBeenCalledWith(sheet))
    // ライブラリが増えたのでフォーム側の選択肢も取り直してもらう
    expect(onChanged).toHaveBeenCalled()
  })

  it('作成中はボタンを押せなくする', async () => {
    let finish: (value: LibraryItem) => void = () => {}
    createLibrarySheet.mockReturnValue(
      new Promise<LibraryItem>((resolve) => {
        finish = resolve
      }),
    )
    show()
    await pick('サクラ')
    await pick('刀')
    fireEvent.click(createButton())

    const busy = (await screen.findByRole('button', {
      name: '作成中…',
    })) as HTMLButtonElement
    expect(busy.disabled).toBe(true)
    finish(item('l9', 'シート'))
  })

  it('失敗したらそのまま出して、選択は残す', async () => {
    createLibrarySheet.mockRejectedValue(new Error('library item not found: l3'))
    const { onCreated } = show()
    await pick('サクラ')
    await pick('刀')
    fireEvent.click(createButton())

    expect(await screen.findByText('library item not found: l3')).toBeTruthy()
    expect(onCreated).not.toHaveBeenCalled()
    // やり直せるように選択とボタンはそのまま
    expect(screen.getByText(/2 枚選択/)).toBeTruthy()
    expect(createButton().disabled).toBe(false)
  })

  it('上限を超える選択は断る', async () => {
    const many = Array.from({ length: SHEET_MAX_ITEMS + 1 }, (_, index) =>
      item(`m${index}`, `素材${index}`),
    )
    show(many)
    for (const material of many) await pick(material.name)

    expect(
      screen.getByText(`シートに載せられるのは ${SHEET_MAX_ITEMS} 枚までです`),
    ).toBeTruthy()
    expect(screen.getByText(new RegExp(`${SHEET_MAX_ITEMS} 枚選択`))).toBeTruthy()
    expect(screen.queryByLabelText(`「素材${SHEET_MAX_ITEMS}」の選択順`)).toBeNull()
  })
})
