import { useState } from 'react'
import { Loader2 } from 'lucide-react'
import { api } from '../api'
import { SHEET_MAX_ITEMS, SHEET_MIN_ITEMS } from '../form'
import type { LibraryItem } from '../types'
import { Button } from '@/components/ui/button'
import LibraryPickerModal from './LibraryPickerModal'
import { Banner } from './ui'

/**
 * ライブラリの画像から IC-LoRA 用リファレンスシートを作るモーダル（SPEC §7.2）。
 *
 * 中身は :file:`LibraryPickerModal.tsx` の複数選択モードそのままで、ここは
 * 「選択順を持つ」「`POST /api/library/sheet` を呼ぶ」ぶんだけを足した薄い
 * ラッパー。**タイルを押した順**がシートの並び順になり（左上から詰める）、
 * `character` の素材だけ大きいパネルになる（並べ方の規則はバックエンドの
 * `app/sheets.py`）。
 *
 * 出来上がったシートはライブラリにも残るので、次からは [ライブラリから選択] で
 * そのまま選び直せる。
 */
export default function SheetBuilderModal({
  showNsfw,
  width,
  height,
  reloadKey,
  onCreated,
  onClose,
  onChanged,
}: {
  /** ヘッダーの NSFW 表示トグル（一覧の初期値） */
  showNsfw: boolean
  /** 合成するシートの大きさ（出力動画と同じ縦横比にしておく） */
  width: number
  height: number
  /** 値が変わると一覧を読み直す */
  reloadKey?: number
  /** 出来上がったシート（そのまま画像欄に入れる） */
  onCreated: (item: LibraryItem) => void
  onClose: () => void
  /** ライブラリが変わったことをフォーム側にも知らせる */
  onChanged: () => void
}) {
  // 選んだ順にそのまま並べるので、Set ではなく配列で持つ。
  const [picked, setPicked] = useState<LibraryItem[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  /** タイルを押すたびに選択を出し入れする（上限を超える追加は断る）。 */
  const toggle = (item: LibraryItem) => {
    setError(null)
    setPicked((current) => {
      if (current.some((chosen) => chosen.id === item.id)) {
        return current.filter((chosen) => chosen.id !== item.id)
      }
      if (current.length >= SHEET_MAX_ITEMS) {
        setError(`シートに載せられるのは ${SHEET_MAX_ITEMS} 枚までです`)
        return current
      }
      return [...current, item]
    })
  }

  const create = async () => {
    setBusy(true)
    setError(null)
    try {
      const sheet = await api.createLibrarySheet(
        picked.map((item) => item.id),
        { width, height },
      )
      onChanged()
      onCreated(sheet)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
      setBusy(false)
    }
  }

  const enough = picked.length >= SHEET_MIN_ITEMS

  return (
    <LibraryPickerModal
      kind="image"
      title="ライブラリから作成: リファレンスシート"
      showNsfw={showNsfw}
      reloadKey={reloadKey}
      selectedIds={picked.map((item) => item.id)}
      onSelect={toggle}
      onClose={onClose}
      onChanged={onChanged}
      footer={
        <div className="mt-3 flex flex-col gap-2">
          {error && <Banner onClose={() => setError(null)}>{error}</Banner>}
          <div className="flex items-center gap-2">
            <span className="text-xs text-muted-foreground">
              {picked.length} 枚選択
              {picked.length > 0 && `: ${picked.map((item) => item.name).join(' → ')}`}
            </span>
            <Button
              size="sm"
              className="ml-auto"
              disabled={!enough || busy}
              onClick={() => void create()}
            >
              {busy && <Loader2 className="animate-spin" />}
              {busy ? '作成中…' : 'この順で作成'}
            </Button>
          </div>
        </div>
      }
    />
  )
}
