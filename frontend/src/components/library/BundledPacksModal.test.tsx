import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { api } from '../../api'
import type { AssetPackImport, BundledAssetPack } from '../../types'
import BundledPacksModal from './BundledPacksModal'

vi.mock('../../api', async () => {
  const actual = await vi.importActual<typeof import('../../api')>('../../api')
  return {
    ...actual,
    api: {
      listBundledPacks: vi.fn(),
      installBundledPack: vi.fn(),
      getPackImport: vi.fn(),
    },
  }
})

const listBundledPacks = vi.mocked(api.listBundledPacks)
const installBundledPack = vi.mocked(api.installBundledPack)
const getPackImport = vi.mocked(api.getPackImport)

afterEach(cleanup)

function pack(over: Partial<BundledAssetPack> = {}): BundledAssetPack {
  return {
    id: 'shorts-sfx-komori',
    aliases: ['old-sfx'],
    name: '効果音（テスト）',
    description: '説明',
    categories: ['効果音'],
    kinds: { audio: 3 },
    total: 3,
    installed: 1,
    sources: [
      {
        id: 'site-a',
        name: 'サイト A',
        url: 'https://a.example.com/',
        terms_url: 'https://a.example.com/terms',
        terms_summary: '商用可。再配布は禁止',
        checked_at: '2026-11-10',
        credit_required: false,
        credit_text: '',
        content_id: '',
        notes: '',
        acquire: 'auto',
        interval_seconds: 4,
        total: 3,
        installed: 1,
      },
    ],
    file: 'asset-packs/shorts-sfx-komori.yaml',
    notice: '入れる前に規約のページを確かめてください',
    ...over,
  }
}

function result(over: Partial<AssetPackImport> = {}): AssetPackImport {
  return {
    id: 'imp1',
    status: 'done',
    pack: 'shorts-sfx-komori',
    dry_run: false,
    total: 3,
    processed: 3,
    imported: [],
    skipped: [],
    changed: [],
    missing: [],
    blocked: [],
    failed: [],
    unaccepted_sources: [],
    inbox_dir: '',
    error: null,
    created_at: '2026-11-10T00:00:00+00:00',
    finished_at: null,
    ...over,
  }
}

const entry = (file: string, reason = '') => ({
  file,
  source: 'site-a',
  member: '',
  library_id: null,
  name: file,
  url: '',
  reason,
})

describe('BundledPacksModal', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    listBundledPacks.mockResolvedValue([pack()])
  })

  it('一覧に件数・規約の要点・確かめた日・規約のページを出す', async () => {
    render(<BundledPacksModal onClose={() => {}} />)
    expect(await screen.findByText('効果音（テスト）')).toBeTruthy()
    expect(screen.getByTestId('installed-shorts-sfx-komori').textContent).toContain('1 / 3')
    expect(screen.getByText('商用可。再配布は禁止')).toBeTruthy()
    expect(screen.getByText(/2026-11-10/)).toBeTruthy()
    const link = screen.getByText('規約のページ').closest('a')
    expect(link?.getAttribute('href')).toBe('https://a.example.com/terms')
    expect(screen.getByText('入れる前に規約のページを確かめてください')).toBeTruthy()
  })

  it('同意のチェックが無ければ入れられない', async () => {
    render(<BundledPacksModal onClose={() => {}} />)
    const button = (await screen.findByRole('button', { name: /^入れる/ })) as HTMLButtonElement
    expect(button.disabled).toBe(true)
    fireEvent.click(button)
    expect(installBundledPack).not.toHaveBeenCalled()
  })

  it('同意した入手元で入れ、進み具合を追って結果を出す', async () => {
    installBundledPack.mockResolvedValue(result({ status: 'queued', processed: 0 }))
    getPackImport.mockResolvedValue(
      result({
        imported: [entry('a.mp3')],
        skipped: [entry('b.mp3', '登録済み')],
        failed: [entry('c.mp3', 'HTML が返りました')],
      }),
    )
    const onInstalled = vi.fn()
    render(<BundledPacksModal onClose={() => {}} onInstalled={onInstalled} />)
    fireEvent.click(await screen.findByRole('checkbox'))
    const button = screen.getByRole('button', { name: /^入れる/ }) as HTMLButtonElement
    expect(button.textContent).toContain('未入手 2 件')
    fireEvent.click(button)
    await waitFor(() =>
      expect(installBundledPack).toHaveBeenCalledWith('shorts-sfx-komori', {
        accept_sources: ['site-a'],
        dry_run: false,
      }),
    )
    await waitFor(() => expect(screen.getByText('完了')).toBeTruthy(), { timeout: 3000 })
    expect(screen.getByText('取り込んだ 1')).toBeTruthy()
    expect(screen.getByText('飛ばした 1')).toBeTruthy()
    expect(screen.getByText(/c\.mp3: HTML が返りました/)).toBeTruthy()
    expect(onInstalled).toHaveBeenCalled()
    expect(listBundledPacks).toHaveBeenCalledTimes(2)
  })

  it('予定だけ見る（dry run）はその場の結果を出す', async () => {
    installBundledPack.mockResolvedValue(
      result({ dry_run: true, imported: [entry('a.mp3'), entry('c.mp3')] }),
    )
    render(<BundledPacksModal onClose={() => {}} />)
    fireEvent.click(await screen.findByRole('checkbox'))
    fireEvent.click(screen.getByText('予定だけ見る'))
    expect(await screen.findByText('取り込む予定 2')).toBeTruthy()
    expect(installBundledPack).toHaveBeenCalledWith('shorts-sfx-komori', {
      accept_sources: ['site-a'],
      dry_run: true,
    })
    expect(getPackImport).not.toHaveBeenCalled()
  })
})
