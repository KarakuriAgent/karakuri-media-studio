/**
 * 画面切り替えのタブ（ヘッダーと下部タブバーは同じ `VIEW_TABS` を見ている）。
 *
 * 3 つめのタブは「ライブラリ（取っておく棚）」ではなく**手元のファイル全部**を
 * 眺める場所なので、名前は [ファイル]（`View` の内部値は `library` のまま）。
 */
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import BottomNav from './BottomNav'
import { VIEW_TABS } from './Header'

afterEach(cleanup)

describe('VIEW_TABS', () => {
  it('3 つめは [ファイル]（内部値は library のまま）', () => {
    expect(VIEW_TABS.map((tab) => tab.label)).toEqual([
      '生成',
      'スタジオ',
      'ファイル',
    ])
    expect(VIEW_TABS[2].value).toBe('library')
  })
})

describe('BottomNav', () => {
  it('同じ行き先を出し、押すと切り替わる', () => {
    const onView = vi.fn()
    render(<BottomNav view="library" onView={onView} />)
    const files = screen.getByRole('button', { name: 'ファイル' })
    expect(files.getAttribute('aria-current')).toBe('page')

    fireEvent.click(screen.getByRole('button', { name: 'スタジオ' }))
    expect(onView).toHaveBeenCalledWith('studio')
  })
})
