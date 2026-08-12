import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../api'
import type {
  LibraryCategory,
  LibraryCategoryValue,
  LibraryItem,
  LibraryKind,
} from '../types'
import { Banner, Modal, NsfwBadge, NsfwToggle } from './ui'

/** カテゴリの表示名（プルダウンの並び順もこの通り。SPEC §7.2）。 */
export const CATEGORY_LABELS: Record<LibraryCategory, string> = {
  character: 'キャラクター',
  background: '背景',
  prop: '小物',
}

/** 「未分類」を表す明示値（サーバーでは NULL。「指定なし」と区別する）。 */
export const UNCATEGORIZED = 'none'

/**
 * 表示する素材（`showNsfw` がオフなら NSFW を隠す）。
 *
 * 種別と検索条件はバックエンドで絞ってあるので、ここで落とすのは NSFW だけ
 * （モーダル内のチェックボックスはサーバーに送らない、この画面かぎりの状態）。
 * `kind` は取り違え防止の保険として併せて見る。
 */
export function libraryItemsOf(
  items: LibraryItem[] | undefined,
  kind: LibraryKind,
  showNsfw: boolean,
): LibraryItem[] {
  return (items ?? []).filter(
    (item) => item.kind === kind && (showNsfw || !item.nsfw),
  )
}

/** 1 回の読み込み件数（「さらに表示」で継ぎ足す）。 */
export const PAGE_SIZE = 50

/** 検索ボックスを打つたびに投げないための待ち時間（ms）。 */
const DEBOUNCE_MS = 200

const EMPTY_HINT: Record<LibraryKind, string> = {
  image: 'ライブラリに画像がありません。アップロードするか、履歴の生成物を登録してください。',
  video: 'ライブラリに動画がありません。アップロードするか、履歴の生成物を登録してください。',
  audio: 'ライブラリに音声がありません。アップロードするか、履歴の生成物を登録してください。',
}

const ACCEPT: Record<LibraryKind, string> = {
  image: 'image/*',
  video: 'video/*',
  audio: 'audio/*',
}

function timestamp(item: LibraryItem): string {
  return item.created_at.replace('T', ' ').replace('+00:00', '').slice(0, 16)
}

/**
 * ライブラリから入力リソースを選ぶモーダル（SPEC §7.2）。
 *
 * 見た目と NSFW の扱いは :file:`HistoryPickerModal.tsx` に合わせてある: 初期値は
 * ヘッダーのグローバルトグルに従い、ここでの切り替えは保存しない。履歴と違って
 * ライブラリは編集できる棚なので、この画面から追加・改名・タグ付け・削除もできる。
 *
 * 一覧は `GET /api/library` から取る（名前・タグでの絞り込みとページングをサーバー
 * 側で行うため）。件数が増えても 1 ページ 50 件ずつしか読まない。
 */
export default function LibraryPickerModal({
  kind,
  title,
  showNsfw,
  onSelect,
  onClose,
  onChanged,
  reloadKey,
  selectedIds,
  footer,
}: {
  kind: LibraryKind
  title: string
  /** ヘッダーの NSFW 表示トグル（初期値としてだけ使う） */
  showNsfw: boolean
  onSelect: (item: LibraryItem) => void
  onClose: () => void
  /** 追加・削除の後に、フォーム側の選択肢も取り直してもらう */
  onChanged: () => void
  /** 値が変わると一覧を読み直す（自動タグ生成の WS 通知で使う） */
  reloadKey?: number
  /**
   * 複数選択モード（リファレンスシートの合成、SPEC §7.2）: 選択中の id を
   * **選んだ順**で渡すと、タイルに順番のバッジが出る。`onSelect` は 1 件を
   * 選ぶたびに呼ばれるので、選択の出し入れとモーダルを閉じるかどうかは
   * 呼び出し側が決める（渡さなければ従来どおりの単発選択）。
   */
  selectedIds?: string[]
  /** 一覧の下に足す操作列（複数選択モードの [この順で作成] など） */
  footer?: React.ReactNode
}) {
  const [nsfw, setNsfw] = useState(showNsfw)
  const [query, setQuery] = useState('')
  // '' = すべて（分類で絞らない）。'none' は未分類だけ。
  const [category, setCategory] = useState<LibraryCategoryValue | ''>('')
  const [tag, setTag] = useState<string | null>(null)
  const [items, setItems] = useState<LibraryItem[]>([])
  const [knownTags, setKnownTags] = useState<string[]>([])
  const [total, setTotal] = useState(0)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const input = useRef<HTMLInputElement>(null)

  /** `offset` から 1 ページ読む（0 なら置き換え、それ以外は継ぎ足し）。 */
  const load = useCallback(
    async (offset: number) => {
      setBusy(true)
      setError(null)
      try {
        const page = await api.listLibrary({
          kind,
          category: category || undefined,
          q: query.trim(),
          tag: tag ?? undefined,
          limit: PAGE_SIZE,
          offset,
        })
        setItems((previous) =>
          offset === 0 ? page.items : [...previous, ...page.items],
        )
        setTotal(page.total)
        setKnownTags(page.tags)
      } catch (caught) {
        setError(caught instanceof Error ? caught.message : String(caught))
      } finally {
        setBusy(false)
      }
    },
    [kind, category, query, tag],
  )

  // 検索条件が変わったら先頭から読み直す（入力中は少し待ってから投げる）。
  // `reloadKey` が変わったとき（背景でタグが付いたとき）も同じ経路で読み直す。
  useEffect(() => {
    const timer = window.setTimeout(() => void load(0), DEBOUNCE_MS)
    return () => window.clearTimeout(timer)
  }, [load, reloadKey])

  /** 追加・削除・編集のあと: 一覧を先頭から取り直し、フォーム側にも知らせる。 */
  const mutate = async (action: () => Promise<unknown>) => {
    setBusy(true)
    setError(null)
    try {
      await action()
      onChanged()
      await load(0)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
      setBusy(false)
    }
  }

  const remove = (item: LibraryItem) => {
    if (!window.confirm(`「${item.name}」をライブラリから削除しますか？`)) return
    void mutate(() => api.deleteLibraryItem(item.id))
  }

  const rename = (item: LibraryItem) => {
    const name = window.prompt('新しい表示名', item.name)
    if (name == null || !name.trim() || name === item.name) return
    void mutate(() => api.updateLibraryItem(item.id, { name }))
  }

  /** NSFW フラグの手動トグル（manual として保存され、自動判定に上書きされない）。 */
  const setItemNsfw = (item: LibraryItem, nsfw: boolean) => {
    void mutate(() => api.updateLibraryItem(item.id, { nsfw }))
  }

  /** タイルのプルダウンで分類を変える（'none' は未分類に戻す指定）。 */
  const setItemCategory = (item: LibraryItem, value: LibraryCategoryValue) => {
    void mutate(() => api.updateLibraryItem(item.id, { category: value }))
  }

  const editTags = (item: LibraryItem) => {
    const answer = window.prompt(
      'タグ（カンマ区切り。空にすると全部外します）',
      item.tags.join(', '),
    )
    if (answer == null) return
    const tags = answer
      .split(',')
      .map((value) => value.trim())
      .filter(Boolean)
    void mutate(() => api.updateLibraryItem(item.id, { tags }))
  }

  /** 複数選択モードでの選択順（1 始まり。選んでいなければ 0）。 */
  const orderOf = (item: LibraryItem) =>
    selectedIds ? selectedIds.indexOf(item.id) + 1 : 0

  const visible = libraryItemsOf(items, kind, nsfw)
  const hiddenByNsfw = items.length - visible.length
  const more = items.length < total

  return (
    <Modal title={title} onClose={onClose} closeOnBackdrop wide>
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <input
          className="field max-w-[16rem] flex-1"
          type="search"
          aria-label="ライブラリを検索"
          placeholder="名前・タグで検索"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
        <select
          className="field w-auto"
          aria-label="カテゴリで絞り込み"
          value={category}
          onChange={(event) =>
            setCategory(event.target.value as LibraryCategoryValue | '')
          }
        >
          <option value="">すべてのカテゴリ</option>
          {(Object.keys(CATEGORY_LABELS) as LibraryCategory[]).map((value) => (
            <option key={value} value={value}>
              {CATEGORY_LABELS[value]}
            </option>
          ))}
          <option value={UNCATEGORIZED}>未分類</option>
        </select>
        <input
          ref={input}
          type="file"
          accept={ACCEPT[kind]}
          className="hidden"
          onChange={(event) => {
            const file = event.target.files?.[0]
            event.target.value = ''
            // 絞り込み中のカテゴリをそのまま付ける（「キャラクター」を見ながら
            // 足したものはキャラクター。すべて / 未分類のときは未分類）。
            if (file) {
              void mutate(() =>
                api.uploadToLibrary(kind, file, [], category || UNCATEGORIZED),
              )
            }
          }}
        />
        <button
          className="btn-ghost text-xs"
          disabled={busy}
          onClick={() => input.current?.click()}
        >
          {busy ? '処理中…' : 'アップロードして追加'}
        </button>
        <label
          className={`chip ml-auto cursor-pointer select-none !py-1 ${
            nsfw
              ? 'border-accent-500 bg-accent-500/15 text-accent-400'
              : 'border-ink-600 bg-ink-800 text-slate-400 hover:border-ink-500'
          }`}
          title="オフのあいだは NSFW の素材を一覧から隠します（この画面かぎりの設定）"
        >
          <input
            type="checkbox"
            className="h-3 w-3 accent-accent-500"
            checked={nsfw}
            onChange={(event) => setNsfw(event.target.checked)}
          />
          🫣 NSFW表示
        </label>
      </div>

      {knownTags.length > 0 && (
        <div className="mb-2 flex flex-wrap items-center gap-1">
          <span className="text-[11px] text-slate-500">タグ:</span>
          {knownTags.map((name) => (
            <button
              key={name}
              className={`chip !py-0.5 text-[11px] ${
                tag === name
                  ? 'border-accent-500 bg-accent-500/20 text-accent-400'
                  : 'border-ink-500 bg-ink-700 text-slate-300 hover:bg-ink-600'
              }`}
              onClick={() => setTag(tag === name ? null : name)}
            >
              {name}
            </button>
          ))}
        </div>
      )}

      <p className="mb-2 text-xs text-slate-500">
        {visible.length} 件表示 / 全 {total} 件
        {hiddenByNsfw > 0 && `（NSFW ${hiddenByNsfw} 件を非表示）`}
      </p>

      {error && <Banner onClose={() => setError(null)}>{error}</Banner>}

      {visible.length === 0 ? (
        <p className="py-6 text-center text-xs text-slate-500">
          {busy
            ? '読み込み中…'
            : query.trim() || tag || category
              ? '条件に合う素材がありません。'
              : EMPTY_HINT[kind]}
        </p>
      ) : (
        <div className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4">
          {visible.map((item) => (
            <div
              key={item.id}
              className={`overflow-hidden rounded-md border bg-ink-900 ${
                orderOf(item) > 0 ? 'border-accent-500' : 'border-ink-600'
              }`}
            >
              <button
                className="block w-full text-left transition-colors hover:opacity-80"
                title={`${item.name}\n${item.path}`}
                disabled={busy}
                onClick={() => onSelect(item)}
              >
                <div className="relative flex h-24 items-center justify-center bg-ink-950">
                  {item.kind === 'image' ? (
                    <img src={item.url} alt="" className="h-full w-full object-cover" />
                  ) : (
                    <span className="text-2xl opacity-60">
                      {item.kind === 'audio' ? '🎵' : '🎬'}
                    </span>
                  )}
                  {orderOf(item) > 0 && (
                    <span
                      className="absolute left-1 top-1 flex h-5 w-5 items-center justify-center rounded-full bg-accent-500 text-[11px] font-medium text-white"
                      aria-label={`「${item.name}」の選択順`}
                    >
                      {orderOf(item)}
                    </span>
                  )}
                  {nsfw && item.nsfw && (
                    <span className="absolute right-1 top-1">
                      <NsfwBadge />
                    </span>
                  )}
                </div>
                <p className="truncate px-1.5 pt-1.5 text-[11px] text-slate-300">
                  {item.name}
                </p>
                {item.tags.length > 0 && (
                  <p className="truncate px-1.5 text-[10px] text-accent-400">
                    {item.tags.map((name) => `#${name}`).join(' ')}
                  </p>
                )}
                <p className="px-1.5 text-[10px] text-slate-600">{timestamp(item)}</p>
              </button>
              <div className="px-1 pb-1">
                <select
                  className="field mb-1 w-full !px-1.5 !py-0.5 !text-[10px]"
                  aria-label={`「${item.name}」のカテゴリ`}
                  value={item.category ?? UNCATEGORIZED}
                  disabled={busy}
                  onChange={(event) =>
                    setItemCategory(
                      item,
                      event.target.value as LibraryCategoryValue,
                    )
                  }
                >
                  <option value={UNCATEGORIZED}>未分類</option>
                  {(Object.keys(CATEGORY_LABELS) as LibraryCategory[]).map(
                    (value) => (
                      <option key={value} value={value}>
                        {CATEGORY_LABELS[value]}
                      </option>
                    ),
                  )}
                </select>
              </div>
              <div className="flex gap-1 px-1 pb-1">
                <button
                  className="btn-ghost !px-1.5 !py-0.5 text-[10px]"
                  disabled={busy}
                  onClick={() => rename(item)}
                >
                  名前
                </button>
                {/* NSFW の手動トグル（ジョブ側と同じ見た目・同じ manual 扱い）。 */}
                <NsfwToggle
                  nsfw={item.nsfw}
                  disabled={busy}
                  onToggle={(value) => setItemNsfw(item, value)}
                  className="!px-1.5 !py-0.5 !text-[10px]"
                />
                <button
                  className="btn-ghost !px-1.5 !py-0.5 text-[10px]"
                  disabled={busy}
                  onClick={() => editTags(item)}
                >
                  タグ
                </button>
                <button
                  className="btn-ghost !px-1.5 !py-0.5 text-[10px] text-red-300"
                  disabled={busy}
                  onClick={() => remove(item)}
                >
                  削除
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {footer}

      {more && (
        <button
          className="btn-ghost mt-3 w-full text-xs"
          disabled={busy}
          onClick={() => void load(items.length)}
        >
          {busy ? '読み込み中…' : `さらに表示（残り ${total - items.length} 件）`}
        </button>
      )}
    </Modal>
  )
}
