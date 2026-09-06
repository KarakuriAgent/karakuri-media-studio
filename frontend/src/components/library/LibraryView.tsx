import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Boxes,
  ChevronLeft,
  ChevronRight,
  EyeOff,
  Film,
  History,
  ImageIcon,
  Loader2,
  Music,
  RefreshCw,
  Upload,
  X,
} from 'lucide-react'

import { api } from '../../api'
import type {
  Job,
  LibraryCategory,
  LibraryCategoryValue,
  LibraryItem,
  LibraryKind,
  StudioAssetItem,
  StudioProjectSummary,
  TimelineExportItem,
} from '../../types'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import { NativeSelect } from '../NativeSelect'
import { CATEGORY_LABELS, UNCATEGORIZED } from '../LibraryPickerModal'
import { Banner, Modal, NsfwBadge } from '../ui'
import { useIsWide } from '../ui/resizable-panel'
import BlockingBuilderModal from './BlockingBuilderModal'
import ExportDetail from './ExportDetail'
import JobOutputDetail from './JobOutputDetail'
import LibraryDetail, { type LibraryPatch } from './LibraryDetail'
import ProjectAssetDetail from './ProjectAssetDetail'
import {
  KIND_LABELS,
  LIBRARY_PAGE_SIZE,
  ORIGIN_BADGES,
  SOURCE_LABELS,
  assetEntry,
  exportEntry,
  filterJobEntries,
  jobEntries,
  libraryEntry,
  mergeEntries,
  timestampOf,
  visibleEntries,
  type LibrarySourceTab,
} from './library'

/** 検索ボックスを打つたびに投げないための待ち時間（ms。モーダルと同じ）。 */
const DEBOUNCE_MS = 200

/**
 * ファイルタブ（SPEC §7.2 / §8）。
 *
 * 選択モーダル（:file:`LibraryPickerModal.tsx`）が「入力に使う 1 件を選ぶ場所」
 * なのに対し、こちらは**手元のファイルを全部眺めて手入れする**場所。ライブラリは
 * その出どころの 1 つで、上のタブで切り替えて、
 *
 * - **ライブラリ**（`GET /api/library`）= 取っておくと決めた素材。編集できる
 * - **生成履歴**（`GET /api/jobs`）= 生成した成果物。**1 ファイル = 1 タイル**に
 *   展開して並べ、そのままライブラリに登録できる
 * - **プロジェクト素材**（`GET /api/studio/assets`）= 全作品の World Bible。
 *   読み取りだけ（編集はスタジオ側）
 * - **書き出し**（`GET /api/studio/exports`）= 編集タブで焼いた mp4。
 *   1 本 = 1 タイルで、ライブラリに登録したり編集タブへ戻ったりできる
 *
 * を混ぜて（`すべて`）日時の新しい順に見せる。種別・検索は 4 つとも効き、
 * 分類・タグはライブラリ項目にしか無いのでライブラリを含むときだけ効く。
 */
export default function LibraryView({
  showNsfw,
  reloadKey,
  jobsReloadKey,
  onChanged,
  onUseInGenerate,
  onUseMedia,
  onOpenJob,
  onOpenStudioAsset,
  onOpenStudioShot,
  onOpenStudioTimeline,
}: {
  /** ヘッダーの NSFW 表示トグル（この画面のトグルはこれに追従する） */
  showNsfw: boolean
  /** 値が変わると一覧を読み直す（WS の `type: "library"` 通知で使う） */
  reloadKey?: number
  /** 値が変わると生成履歴を読み直す（ジョブ一覧が動いたとき） */
  jobsReloadKey?: number
  /** 追加・更新・削除の後に、フォーム側の選択肢も取り直してもらう */
  onChanged?: () => void
  /** ライブラリの素材を生成フォームの入力欄に入れて [生成] タブへ移る */
  onUseInGenerate?: (item: LibraryItem) => void
  /** 履歴の成果物を生成フォームの入力欄に入れて [生成] タブへ移る */
  onUseMedia?: (media: { kind: LibraryKind; url: string; name: string }) => void
  /** そのジョブを [生成] タブで開く */
  onOpenJob?: (job: Job) => void
  /** その素材の作品をスタジオタブの World Bible で開く */
  onOpenStudioAsset?: (asset: StudioAssetItem) => void
  /** Take 由来のジョブの**カット**をスタジオタブで開く */
  onOpenStudioShot?: (job: Job) => void
  /** その書き出しの作品をスタジオタブの**編集**タブで開く */
  onOpenStudioTimeline?: (item: TimelineExportItem) => void
}) {
  const [source, setSource] = useState<LibrarySourceTab>('all')
  const [nsfw, setNsfw] = useState(showNsfw)
  const [query, setQuery] = useState('')
  // '' = すべて（その軸では絞らない）。分類の 'none' は未分類だけ。
  const [kind, setKind] = useState<LibraryKind | ''>('')
  const [category, setCategory] = useState<LibraryCategoryValue | ''>('')
  // 作品の絞り込み（'' = すべての作品）。生成履歴は Take 経由、プロジェクト
  // 素材は持ち主の作品で絞る。
  const [projectId, setProjectId] = useState('')
  const [tag, setTag] = useState<string | null>(null)
  // タグのチップ列を折り返して全部見せるか（既定は 1 行 + 横スクロール）。
  const [tagsExpanded, setTagsExpanded] = useState(false)
  // ライブラリだけを見ているときのページ送り（[前へ] / [次へ]）。
  const [offset, setOffset] = useState(0)
  const [items, setItems] = useState<LibraryItem[]>([])
  const [jobs, setJobs] = useState<Job[]>([])
  const [jobTotal, setJobTotal] = useState(0)
  const [assets, setAssets] = useState<StudioAssetItem[]>([])
  const [exports, setExports] = useState<TimelineExportItem[]>([])
  const [projects, setProjects] = useState<StudioProjectSummary[]>([])
  const [knownTags, setKnownTags] = useState<string[]>([])
  const [total, setTotal] = useState(0)
  const [assetTotal, setAssetTotal] = useState(0)
  const [exportTotal, setExportTotal] = useState(0)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [selectedKey, setSelectedKey] = useState<string | null>(null)
  const [dropping, setDropping] = useState(false)
  // 構図リファレンス（ブロッキング、SPEC §7.2）のモーダル。
  // `{item: null}` は新規、項目つきはその項目の焼き直し。
  const [builder, setBuilder] = useState<{ item: LibraryItem | null } | null>(null)
  const fileInput = useRef<HTMLInputElement>(null)
  const isWide = useIsWide()
  // 無限スクロール（一覧の末尾に置いた番兵と、それを見張るスクロール枠）。
  const scrollRef = useRef<HTMLDivElement>(null)
  const [sentinel, setSentinel] = useState<HTMLDivElement | null>(null)
  // 番兵が（先読みぶんを含めて）視界に入っているか。
  const [nearEnd, setNearEnd] = useState(false)

  // ヘッダーのグローバルトグルに追従する（この画面のトグルでの上書きは可）。
  useEffect(() => setNsfw(showNsfw), [showNsfw])

  // 作品セレクトの選択肢（数は多くないので 1 回だけ読む）。
  useEffect(() => {
    void (async () => {
      try {
        setProjects(await api.listStudioProjects())
      } catch {
        setProjects([]) // 作品が読めなくても一覧そのものは使える
      }
    })()
  }, [])

  const wantLibrary = source === 'all' || source === 'library'
  const wantJobs = source === 'all' || source === 'jobs'
  const wantAssets = source === 'all' || source === 'assets'
  // 書き出しは必ず mp4 なので、種別を画像・音声に絞っているあいだは読まない。
  const wantExports =
    (source === 'all' || source === 'exports') && (kind === '' || kind === 'video')
  // ライブラリだけのときは今までどおり 1 ページずつ（[前へ] / [次へ]）、
  // 混ざるときは出どころごとに同じ件数を読んで、末尾までスクロールしたら足す。
  const paged = source === 'library'

  /**
   * 追い越し対策の連番。
   *
   * 読み込みを始めるたびに 1 つ進め、応答が返ったときに自分の番号が最新で
   * なければ捨てる（追記中に絞り込みが変わったとき、古い応答で新しい一覧を
   * 上書きしないため）。
   */
  const loadSeq = useRef(0)

  /**
   * 続きの読み込みが失敗した回数（同じ深さで数える）。
   *
   * `key` は「どの出どころをどこまで読んだか」＝次に投げる offset の組。同じ
   * 条件で 2 回続けて失敗したら、絞り込みが変わって `load()` が通るまで自動では
   * 読まない（番兵が見えたままサーバーへ投げ続けないための歯止め）。
   */
  const loadMoreFails = useRef({ key: '', count: 0 })

  /**
   * 出どころごとに **1 ページ（48 件）** だけ読む。
   *
   * `limit` は常に `LIBRARY_PAGE_SIZE` で固定し、続きは `offset` で送る。
   * サーバーの `limit` 上限（ライブラリ・素材・書き出しは 200、ジョブは 500）に
   * 当たらないのはこのため。読まない出どころは `null` を返す。
   */
  const fetchPages = useCallback(
    async (
      offsets: { library: number; jobs: number; assets: number; exports: number },
      want: { library: boolean; jobs: boolean; assets: boolean; exports: boolean },
    ) => {
      const [library, jobs, assets, exports] = await Promise.all([
        want.library
          ? api.listLibrary({
              kind: kind || undefined,
              category: category || undefined,
              q: query.trim(),
              tag: tag ?? undefined,
              limit: LIBRARY_PAGE_SIZE,
              offset: offsets.library,
            })
          : null,
        // 履歴はサーバー側で絞る（検索・種別・作品・NSFW）。件数は総数が
        // `X-Total-Count` で返るので、続きを読むかどうかの判断に使う。
        want.jobs
          ? api.listJobPage({
              q: query.trim() || undefined,
              kind: kind || undefined,
              project_id: projectId || undefined,
              // オンなら全部、オフなら NSFW を読まない（画面側でも落とすが、
              // 読み込む 48 件を伏せたもので埋めないため）。
              nsfw: nsfw ? undefined : false,
              limit: LIBRARY_PAGE_SIZE,
              offset: offsets.jobs,
            })
          : null,
        want.assets
          ? api.listStudioAssets({
              kind: kind || undefined,
              q: query.trim(),
              project_id: projectId || undefined,
              limit: LIBRARY_PAGE_SIZE,
              offset: offsets.assets,
            })
          : null,
        // 書き出しは焼き上がった mp4 だけが返る（1 本 = 1 ファイル）。
        want.exports
          ? api.listStudioExports({
              q: query.trim(),
              project_id: projectId || undefined,
              limit: LIBRARY_PAGE_SIZE,
              offset: offsets.exports,
            })
          : null,
      ])
      return { library, jobs, assets, exports }
    },
    [kind, category, query, tag, projectId, nsfw],
  )

  /**
   * 最初から読み直す（offset 0）。
   *
   * 絞り込みを変えたとき・`reloadKey` / `jobsReloadKey` が動いたとき・
   * 追加や削除のあとに通る道。スクロールで伸ばしたぶんは 1 ページに戻る。
   */
  const load = useCallback(async () => {
    const seq = ++loadSeq.current
    // 絞り込みやページが変わって読み直すので、続きの読み込みの歯止めも外す。
    loadMoreFails.current = { key: '', count: 0 }
    setBusy(true)
    setError(null)
    try {
      const page = await fetchPages(
        // ライブラリ単独のページ送りだけは今の offset から読む。
        { library: paged ? offset : 0, jobs: 0, assets: 0, exports: 0 },
        {
          library: wantLibrary,
          jobs: wantJobs,
          assets: wantAssets,
          exports: wantExports,
        },
      )
      if (seq !== loadSeq.current) return // 追い越された応答は捨てる
      setItems(page.library?.items ?? [])
      setTotal(page.library?.total ?? 0)
      setKnownTags(page.library?.tags ?? [])
      setJobs(page.jobs?.items ?? [])
      setJobTotal(page.jobs?.total ?? 0)
      setAssets(page.assets?.items ?? [])
      setAssetTotal(page.assets?.total ?? 0)
      setExports(page.exports?.items ?? [])
      setExportTotal(page.exports?.total ?? 0)
    } catch (caught) {
      if (seq !== loadSeq.current) return
      setError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      if (seq === loadSeq.current) setBusy(false)
    }
  }, [fetchPages, paged, offset, wantLibrary, wantJobs, wantAssets, wantExports])

  /**
   * 続きを読む: **まだ読み残している出どころだけ** 48 件ずつ足す。
   *
   * `offset` は読み込み済みの件数そのもの。total まで読み切った出どころは
   * 投げない（全部読み切っていれば番兵そのものが出ない）。
   */
  const loadMore = useCallback(async () => {
    const want = {
      library: wantLibrary && items.length < total,
      jobs: wantJobs && jobs.length < jobTotal,
      assets: wantAssets && assets.length < assetTotal,
      exports: wantExports && exports.length < exportTotal,
    }
    if (!want.library && !want.jobs && !want.assets && !want.exports) return
    // 同じ深さ（＝同じ offset の組）で 2 回続けて失敗していたら、もう投げない。
    const key = `${items.length}/${jobs.length}/${assets.length}/${exports.length}`
    if (loadMoreFails.current.key === key && loadMoreFails.current.count >= 2) return
    const seq = ++loadSeq.current
    setBusy(true)
    setError(null)
    try {
      const page = await fetchPages(
        {
          library: items.length,
          jobs: jobs.length,
          assets: assets.length,
          exports: exports.length,
        },
        want,
      )
      if (seq !== loadSeq.current) return // 絞り込みが変わっていたら捨てる
      loadMoreFails.current = { key: '', count: 0 }
      if (page.library) {
        const more = page.library
        // 読み残しがあるはずなのに空で返った（サーバーの件数と実際の行がずれた）
        // ときは、total を読み込み済みの件数まで切り詰めて打ち止めにする。
        // そうしないと `canLoadMore` が true のまま同じ offset を読み続ける。
        if (more.items.length === 0) setTotal(Math.min(more.total, items.length))
        else {
          setItems((prev) => [...prev, ...more.items])
          setTotal(more.total)
        }
        setKnownTags(more.tags)
      }
      if (page.jobs) {
        const more = page.jobs
        if (more.items.length === 0) setJobTotal(Math.min(more.total, jobs.length))
        else {
          setJobs((prev) => [...prev, ...more.items])
          setJobTotal(more.total)
        }
      }
      if (page.assets) {
        const more = page.assets
        if (more.items.length === 0) setAssetTotal(Math.min(more.total, assets.length))
        else {
          setAssets((prev) => [...prev, ...more.items])
          setAssetTotal(more.total)
        }
      }
      if (page.exports) {
        const more = page.exports
        if (more.items.length === 0) setExportTotal(Math.min(more.total, exports.length))
        else {
          setExports((prev) => [...prev, ...more.items])
          setExportTotal(more.total)
        }
      }
    } catch (caught) {
      if (seq !== loadSeq.current) return
      setError(caught instanceof Error ? caught.message : String(caught))
      // 失敗したまま番兵が見えていると `busy` が下りるたびに読み直してしまうので、
      // 見えていない扱いに戻して自動の再試行を止める（ユーザーがスクロールし直して
      // 番兵がまた視界に入れば 1 回だけ試す）。
      loadMoreFails.current =
        loadMoreFails.current.key === key
          ? { key, count: loadMoreFails.current.count + 1 }
          : { key, count: 1 }
      setNearEnd(false)
    } finally {
      if (seq === loadSeq.current) setBusy(false)
    }
  }, [
    fetchPages,
    wantLibrary,
    wantJobs,
    wantAssets,
    wantExports,
    items,
    total,
    jobs,
    jobTotal,
    assets,
    assetTotal,
    exports,
    exportTotal,
  ])

  // 番兵の監視から呼ぶための最新版（`loadMore` は毎描画で作り直されるので、
  // effect の依存に入れると読み込むたびに監視を張り直すことになる）。
  const loadMoreRef = useRef(loadMore)
  useEffect(() => {
    loadMoreRef.current = loadMore
  }, [loadMore])

  // 絞り込み・ページが変わったら読み直す（入力中は少し待ってから投げる）。
  // `reloadKey`（ライブラリの WS 通知）と `jobsReloadKey`（ジョブ一覧の更新）
  // でも同じ経路で読み直す。
  useEffect(() => {
    const timer = window.setTimeout(() => void load(), DEBOUNCE_MS)
    return () => window.clearTimeout(timer)
  }, [load, reloadKey, jobsReloadKey])

  /** 絞り込みを変えたら 1 ページ目に戻す（今の offset に残ると空に見える）。 */
  const refine = (change: () => void) => {
    change()
    setOffset(0)
  }

  /** 追加・更新・削除のあと: 一覧を取り直し、フォーム側にも知らせる。 */
  const mutate = async (action: () => Promise<unknown>) => {
    setBusy(true)
    setError(null)
    try {
      await action()
      onChanged?.()
      await load()
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
      setBusy(false)
    }
  }

  /** 手元のファイルを追加する（種別はサーバーが拡張子 / MIME から決める）。 */
  const uploadFiles = (files: File[]) => {
    if (files.length === 0) return
    void mutate(async () => {
      for (const file of files) {
        // 絞り込み中の分類をそのまま付ける（「背景」を見ながら足したものは背景）。
        await api.uploadAnyToLibrary(file, [], category || UNCATEGORIZED)
      }
    })
  }

  const save = (item: LibraryItem, patch: LibraryPatch) =>
    void mutate(() => api.updateLibraryItem(item.id, patch))

  const remove = (item: LibraryItem) => {
    if (!window.confirm(`「${item.name}」をライブラリから削除しますか？`)) return
    setSelectedKey(null)
    void mutate(() => api.deleteLibraryItem(item.id))
  }

  /**
   * チップ列に並べるタグ（選んでいるものを常に先頭に出す）。
   *
   * 1 行に畳んでいるあいだも選択中のタグが見えるようにするための並べ替え。
   * 絞り込みの結果そのタグが `knownTags` から消えても、解除できるように残す。
   */
  const orderedTags = useMemo(() => {
    if (!tag) return knownTags
    return [tag, ...knownTags.filter((name) => name !== tag)]
  }, [knownTags, tag])

  const entries = useMemo(
    () =>
      mergeEntries(
        items.map(libraryEntry),
        filterJobEntries(jobs.flatMap(jobEntries), { kind }),
        assets.map(assetEntry),
        exports.map(exportEntry),
      ),
    [items, jobs, assets, exports, kind],
  )
  const visible = useMemo(() => visibleEntries(entries, nsfw), [entries, nsfw])
  const hiddenByNsfw = entries.length - visible.length
  // 伏せた素材を選んだままにしない（トグルを戻せばまた開ける）。
  const selected = visible.find((entry) => entry.key === selectedKey) ?? null
  const filtered = Boolean(query.trim() || tag || category || kind || projectId)
  const first = total === 0 ? 0 : offset + 1
  const last = offset + items.length
  // 読み残しのある出どころ（出どころごとに読み込んだ深さが違いうるので、
  // 「すべて」でも何がどこまで読めているかを注記に出す）。
  const remaining = [
    wantLibrary && total > items.length && `ライブラリは全 ${total} 件のうち ${items.length} 件`,
    wantJobs && jobTotal > jobs.length && `生成履歴は全 ${jobTotal} 件のうち ${jobs.length} 件`,
    wantAssets &&
      assetTotal > assets.length &&
      `プロジェクト素材は全 ${assetTotal} 件のうち ${assets.length} 件`,
    wantExports &&
      exportTotal > exports.length &&
      `書き出しは全 ${exportTotal} 件のうち ${exports.length} 件`,
  ].filter((note): note is string => typeof note === 'string')
  // まだ読めるものが残っているか（どれかの出どころで読み残しがあるなら）。
  const canLoadMore = !paged && remaining.length > 0

  /**
   * 一覧の末尾に置いた番兵を見張る（無限スクロール、SPEC §8）。
   *
   * スクロールしているのは window ではなくグリッドを包む `overflow-y-auto` の
   * 枠なので、そこを `root` にする。`rootMargin` を 400px 取って、末尾に着く
   * 前に次の 48 件を読み始める。`IntersectionObserver` の無い環境（テストの
   * jsdom など）では何もしない。
   */
  useEffect(() => {
    if (!sentinel || typeof IntersectionObserver === 'undefined') return
    const observer = new IntersectionObserver(
      (records) => setNearEnd(records.some((record) => record.isIntersecting)),
      { root: scrollRef.current, rootMargin: '400px 0px' },
    )
    observer.observe(sentinel)
    return () => {
      observer.disconnect()
      // 番兵が消えた（＝読み切った / 絞り込みが変わった）ら見えていない扱いに
      // 戻す。次に番兵が出たときは改めて交差の通知を待つ。
      setNearEnd(false)
    }
  }, [sentinel])

  /**
   * 番兵が見えているあいだ、読めるものが無くなるまで 48 件ずつ足していく。
   *
   * 依存は `nearEnd` / `canLoadMore` / `busy` の 3 つだけなので、1 回の可視化に
   * つき 1 回しか投げない。読み終わって `busy` が false に戻ったときにもう一度
   * 通り、まだ番兵が見えていれば続けて次のページを読む（48 件が画面に収まって
   * しまうときに 2 ページ目が自動で続くのはこのため）。
   *
   * 読めるものが減らないまま回り続けないよう、`loadMore` の側で
   * 「空で返ったら total を切り詰める」「失敗したら `nearEnd` を false に戻し、
   * 同じ深さで 2 回続けて失敗したらもう投げない」という歯止めを掛けている。
   */
  useEffect(() => {
    if (!nearEnd || !canLoadMore || busy) return
    void loadMoreRef.current()
  }, [nearEnd, canLoadMore, busy])

  const detail = selected && (
    <>
      {selected.origin === 'library' && selected.item && (
        <LibraryDetail
          item={selected.item}
          busy={busy}
          onSave={(patch) => save(selected.item as LibraryItem, patch)}
          onDelete={() => remove(selected.item as LibraryItem)}
          onUseInGenerate={
            onUseInGenerate
              ? () => onUseInGenerate(selected.item as LibraryItem)
              : undefined
          }
          onEditBlocking={
            selected.item.blocking
              ? () => setBuilder({ item: selected.item as LibraryItem })
              : undefined
          }
        />
      )}
      {selected.origin === 'job' && selected.job && selected.output && (
        <JobOutputDetail
          job={selected.job}
          output={selected.output}
          url={selected.url}
          kind={selected.kind}
          onUseInGenerate={
            onUseMedia
              ? () =>
                  onUseMedia({
                    kind: selected.kind,
                    url: selected.url,
                    name: selected.name,
                  })
              : undefined
          }
          onOpenJob={
            onOpenJob ? () => onOpenJob(selected.job as Job) : undefined
          }
          onOpenInStudio={
            onOpenStudioShot && selected.job.project_id
              ? () => onOpenStudioShot(selected.job as Job)
              : undefined
          }
          onRegistered={() => {
            onChanged?.()
            void load()
          }}
        />
      )}
      {selected.origin === 'export' && selected.export && (
        <ExportDetail
          item={selected.export}
          onUseInGenerate={
            onUseMedia && selected.url
              ? () =>
                  onUseMedia({
                    kind: 'video',
                    url: selected.url,
                    name: selected.name,
                  })
              : undefined
          }
          onOpenInStudio={
            onOpenStudioTimeline && selected.export.project_id
              ? () => onOpenStudioTimeline(selected.export as TimelineExportItem)
              : undefined
          }
          onRegistered={() => {
            onChanged?.()
            void load()
          }}
        />
      )}
      {selected.origin === 'asset' && selected.asset && (
        <ProjectAssetDetail
          asset={selected.asset}
          onOpenInStudio={
            onOpenStudioAsset
              ? () => onOpenStudioAsset(selected.asset as StudioAssetItem)
              : undefined
          }
        />
      )}
    </>
  )

  return (
    <main className="flex min-h-0 flex-1 flex-col overflow-hidden">
      {/* 出どころの切り替え（すべて / ライブラリ / 生成履歴 / プロジェクト素材 / 書き出し）。 */}
      {/* 狭幅でも 1 行に収め、はみ出すぶんは横スクロールで見せる。 */}
      <div
        className="flex items-center gap-1 overflow-x-auto scrollbar-none border-b border-border px-3 py-2"
        role="tablist"
        aria-label="出どころ"
      >
        {(Object.keys(SOURCE_LABELS) as LibrarySourceTab[]).map((value) => (
          <button
            key={value}
            type="button"
            role="tab"
            aria-selected={source === value}
            className={`chip !py-1 shrink-0 text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50 ${
              source === value
                ? 'border-primary bg-primary/20 text-foreground'
                : 'border-border bg-card text-foreground/85 hover:bg-secondary'
            }`}
            onClick={() =>
              refine(() => {
                setSource(value)
                setSelectedKey(null)
              })
            }
          >
            {SOURCE_LABELS[value]}
          </button>
        ))}
      </div>

      {/*
        狭幅（sm 未満）では検索欄を単独の行で全幅にし、種別・カテゴリと操作ボタンは
        次の 1 行に収めて（収まらないぶんは横スクロール）折り返さない。sm 以上では
        `sm:contents` で子を外側の flex 行へ流し込み、今までどおりの 1 行に戻す。
      */}
      <div className="flex flex-col gap-2 border-b border-border px-3 py-2 sm:flex-row sm:flex-wrap sm:items-center">
        <Input
          className="w-full sm:max-w-[16rem] sm:flex-1"
          type="search"
          aria-label="ファイルを検索"
          placeholder="名前・タグで検索"
          value={query}
          onChange={(event) => refine(() => setQuery(event.target.value))}
        />
        <div className="flex items-center gap-2 overflow-x-auto scrollbar-none sm:contents">
          <div className="shrink-0">
            <NativeSelect
              className="w-auto"
              aria-label="種別で絞り込み"
              value={kind}
              onChange={(event) =>
                refine(() => setKind(event.target.value as LibraryKind | ''))
              }
            >
              <option value="">すべての種別</option>
              {(Object.keys(KIND_LABELS) as LibraryKind[]).map((value) => (
                <option key={value} value={value}>
                  {KIND_LABELS[value]}
                </option>
              ))}
            </NativeSelect>
          </div>
          {/* 分類はライブラリ項目にしか無いので、含まない出どころでは無効にする。 */}
          <div className="shrink-0">
            <NativeSelect
              className="w-auto"
              aria-label="カテゴリで絞り込み"
              value={category}
              disabled={!wantLibrary}
              title={
                wantLibrary ? undefined : '分類はライブラリの素材にだけ付きます'
              }
              onChange={(event) =>
                refine(() =>
                  setCategory(event.target.value as LibraryCategoryValue | ''),
                )
              }
            >
              <option value="">すべてのカテゴリ</option>
              {(Object.keys(CATEGORY_LABELS) as LibraryCategory[]).map((value) => (
                <option key={value} value={value}>
                  {CATEGORY_LABELS[value]}
                </option>
              ))}
              <option value={UNCATEGORIZED}>未分類</option>
            </NativeSelect>
          </div>
          {/* 作品は生成履歴（Take 経由）・プロジェクト素材・書き出しに効く。 */}
          <div className="shrink-0">
            <NativeSelect
              className="w-auto"
              aria-label="作品で絞り込み"
              value={projectId}
              disabled={source === 'library'}
              title={
                source === 'library'
                  ? '作品はスタジオの素材・生成履歴・書き出しにだけ付きます'
                  : undefined
              }
              onChange={(event) => refine(() => setProjectId(event.target.value))}
            >
              <option value="">すべての作品</option>
              {projects.map((project) => (
                <option key={project.id} value={project.id}>
                  {project.name}
                </option>
              ))}
            </NativeSelect>
          </div>

          <input
            ref={fileInput}
            type="file"
            multiple
            className="hidden"
            onChange={(event) => {
              const files = Array.from(event.target.files ?? [])
              event.target.value = ''
              uploadFiles(files)
            }}
          />
          {/*
            狭幅ではラベルを畳んでアイコンだけにする（読み上げとツールチップには
            `aria-label` / `title` でラベルを残す）。
          */}
          <Button
            variant="outline"
            size="sm"
            className="shrink-0"
            disabled={busy}
            aria-label={busy ? '処理中…' : 'アップロード'}
            title={busy ? '処理中…' : 'アップロード'}
            onClick={() => fileInput.current?.click()}
          >
            {busy ? <Loader2 className="animate-spin" /> : <Upload />}
            <span className="hidden sm:inline">
              {busy ? '処理中…' : 'アップロード'}
            </span>
          </Button>
          <Button
            variant="outline"
            size="sm"
            className="shrink-0"
            disabled={busy}
            aria-label="構図リファレンスを作る"
            title="構図リファレンスを作る"
            onClick={() => setBuilder({ item: null })}
          >
            <Boxes />
            <span className="hidden sm:inline">構図リファレンスを作る</span>
          </Button>
          <Button
            variant="outline"
            size="sm"
            className="shrink-0"
            aria-label="生成履歴から登録"
            title="生成履歴から登録"
            onClick={() =>
              refine(() => {
                setSource('jobs')
                setSelectedKey(null)
              })
            }
          >
            <History />
            <span className="hidden sm:inline">生成履歴から登録</span>
          </Button>
          <Button
            variant="outline"
            size="sm"
            className="shrink-0"
            disabled={busy}
            aria-label="更新"
            title="更新"
            onClick={() => void load()}
          >
            <RefreshCw className={busy ? 'animate-spin' : undefined} />
            <span className="hidden sm:inline">更新</span>
          </Button>

          <div
            className={`chip ml-auto shrink-0 select-none !py-1 ${
              nsfw
                ? 'border-primary bg-primary/15 text-foreground'
                : 'border-border bg-surface-sunken text-muted-foreground'
            }`}
            title="オフのあいだは NSFW の素材を一覧から隠します（この画面かぎりの設定）"
          >
            <Switch
              id="library-view-nsfw"
              aria-label="NSFW表示"
              checked={nsfw}
              onCheckedChange={setNsfw}
            />
            <Label
              htmlFor="library-view-nsfw"
              className="flex cursor-pointer items-center gap-1 text-xs"
            >
              <EyeOff className="size-3" aria-hidden="true" />
              <span className="hidden sm:inline">NSFW表示</span>
            </Label>
          </div>
        </div>
      </div>

      {/*
        タグは 80 個を超えることがあるので、既定は 1 行に収めて横スクロール。
        [すべて表示] で従来の折り返し表示に開き、もう一度押すと 1 行に戻る。
        選んでいるタグは常に先頭に出し、× で解除できる。
      */}
      {wantLibrary && orderedTags.length > 0 && (
        <div className="flex items-start gap-1 border-b border-border px-3 py-1.5">
          <span className="shrink-0 py-0.5 text-[11px] text-muted-foreground">
            {orderedTags.length > 1 ? `タグ ${orderedTags.length}:` : 'タグ:'}
          </span>
          <div
            className={`min-w-0 flex-1 items-center gap-1 ${
              tagsExpanded
                ? 'flex flex-wrap'
                : 'flex overflow-x-auto scrollbar-none'
            }`}
          >
            {orderedTags.map((name) => {
              const active = tag === name
              return (
                <button
                  key={name}
                  type="button"
                  aria-pressed={active}
                  aria-label={active ? `タグ「${name}」の絞り込みを解除` : undefined}
                  className={`chip !py-0.5 shrink-0 text-[11px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50 ${
                    active
                      ? 'border-primary bg-primary/20 text-foreground'
                      : 'border-border bg-card text-foreground/85 hover:bg-secondary'
                  }`}
                  onClick={() => refine(() => setTag(active ? null : name))}
                >
                  {name}
                  {active && <X className="size-3" aria-hidden="true" />}
                </button>
              )
            })}
          </div>
          <button
            type="button"
            aria-expanded={tagsExpanded}
            className="chip !py-0.5 shrink-0 border-border bg-card text-[11px] text-foreground/85 hover:bg-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
            onClick={() => setTagsExpanded(!tagsExpanded)}
          >
            {tagsExpanded ? '1 行に戻す' : 'すべて表示'}
          </button>
        </div>
      )}

      <div className="flex min-h-0 flex-1 flex-col lg:flex-row">
        <div
          ref={scrollRef}
          className={`min-h-0 flex-1 overflow-y-auto p-3 ${
            dropping ? 'bg-primary/10 outline-dashed outline-2 outline-primary' : ''
          }`}
          onDragOver={(event) => {
            event.preventDefault()
            setDropping(true)
          }}
          onDragLeave={() => setDropping(false)}
          onDrop={(event) => {
            event.preventDefault()
            setDropping(false)
            uploadFiles(Array.from(event.dataTransfer.files ?? []))
          }}
        >
          <div className="mb-2 flex flex-wrap items-center gap-2">
            <p className="tnum text-xs text-muted-foreground">
              {paged
                ? total === 0
                  ? '0 件'
                  : `${first}–${last} 件 / 全 ${total} 件`
                : `${visible.length} 件`}
              {!paged && remaining.length > 0 && `（${remaining.join('、')}）`}
              {hiddenByNsfw > 0 && `（NSFW ${hiddenByNsfw} 件を非表示）`}
            </p>
            {/* 混ざっているときは無限スクロールなので、ページ送りは
                ライブラリ単独のときだけ出す。 */}
            {paged && (
              <div className="ml-auto flex items-center gap-1">
                <Button
                  variant="outline"
                  size="xs"
                  disabled={busy || offset === 0}
                  onClick={() => setOffset(Math.max(0, offset - LIBRARY_PAGE_SIZE))}
                >
                  <ChevronLeft />
                  前へ
                </Button>
                <Button
                  variant="outline"
                  size="xs"
                  disabled={busy || last >= total}
                  onClick={() => setOffset(offset + LIBRARY_PAGE_SIZE)}
                >
                  次へ
                  <ChevronRight />
                </Button>
              </div>
            )}
          </div>

          {error && <Banner onClose={() => setError(null)}>{error}</Banner>}

          {visible.length === 0 ? (
            <div className="flex flex-col items-center gap-1 py-10 text-center">
              {busy ? (
                <Loader2 className="size-6 animate-spin text-primary" aria-hidden />
              ) : (
                <ImageIcon className="size-6 text-muted-foreground" aria-hidden />
              )}
              <p className="text-xs text-muted-foreground">
                {busy
                  ? '読み込み中…'
                  : filtered
                    ? '条件に合うファイルがありません。'
                    : source === 'jobs'
                      ? 'まだ何も生成していません。'
                      : source === 'assets'
                        ? 'World Bible の素材がまだありません。'
                        : source === 'exports'
                          ? 'まだ書き出していません。'
                          : source === 'library'
                            ? 'ライブラリはまだ空です。'
                            : 'ファイルがまだありません。'}
              </p>
              {!busy && !filtered && wantLibrary && (
                <p className="text-xs text-muted-foreground">
                  ファイルをここにドロップするか、生成履歴の成果物を
                  [ライブラリに登録] で足せます。
                </p>
              )}
            </div>
          ) : (
            <ul className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5">
              {visible.map((entry) => (
                <li key={entry.key}>
                  <button
                    type="button"
                    aria-current={entry.key === selected?.key ? 'true' : undefined}
                    title={`${entry.name}\n${entry.note}`}
                    className={`w-full overflow-hidden rounded-md border bg-surface-sunken text-left shadow-elevation-1 transition-colors hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50 ${
                      entry.key === selected?.key ? 'border-primary' : 'border-border'
                    }`}
                    onClick={() => setSelectedKey(entry.key)}
                  >
                    <div className="relative flex h-28 items-center justify-center bg-background">
                      {!entry.url ? (
                        <ImageIcon className="size-7 opacity-60" />
                      ) : entry.kind === 'image' ? (
                        <img src={entry.url} alt="" className="size-full object-cover" />
                      ) : entry.kind === 'video' ? (
                        // 音を出さずに 1 コマ目だけ見せる（読み込みは metadata まで）。
                        <video
                          src={entry.url}
                          muted
                          playsInline
                          preload="metadata"
                          className="size-full object-cover"
                        />
                      ) : (
                        <Music className="size-7 opacity-60" />
                      )}
                      {entry.kind === 'video' && entry.url && (
                        <span className="absolute left-1 top-1 rounded-sm bg-black/60 p-0.5">
                          <Film className="size-3 text-foreground" aria-hidden />
                        </span>
                      )}
                      {/* 出どころが混ざるので、ライブラリ以外はタイルにも印を出す。 */}
                      {ORIGIN_BADGES[entry.origin] && (
                        <span className="absolute bottom-1 left-1 rounded-sm bg-black/60 px-1 text-[10px] text-foreground">
                          {ORIGIN_BADGES[entry.origin]}
                        </span>
                      )}
                      {entry.nsfw && (
                        <span className="absolute right-1 top-1">
                          <NsfwBadge />
                        </span>
                      )}
                    </div>
                    <p className="truncate px-1.5 pt-1.5 text-[11px] text-foreground/85">
                      {entry.name}
                    </p>
                    <p className="truncate px-1.5 text-[11px] text-muted-foreground">
                      {entry.note}
                    </p>
                    {entry.item && entry.item.tags.length > 0 && (
                      <p className="truncate px-1.5 text-[11px] text-primary">
                        {entry.item.tags.map((name) => `#${name}`).join(' ')}
                      </p>
                    )}
                    <p className="tnum px-1.5 pb-1.5 text-[11px] text-muted-foreground">
                      {timestampOf(entry.createdAt)}
                    </p>
                  </button>
                </li>
              ))}
            </ul>
          )}

          {/*
            無限スクロールの番兵。読み残しがあるあいだだけ置き、視界に入ったら
            次の 48 件を読む（読み込み中は小さな印を出す）。
          */}
          {canLoadMore && (
            <div
              ref={setSentinel}
              data-testid="library-sentinel"
              className="flex items-center justify-center gap-1.5 py-4 text-xs text-muted-foreground"
              role="status"
            >
              {busy && (
                <>
                  <Loader2 className="size-4 animate-spin" aria-hidden />
                  読み込み中…
                </>
              )}
            </div>
          )}
        </div>

        {/* 広い画面では右に置き、狭い画面ではモーダルとして重ねる。 */}
        {isWide && selected && (
          <aside className="w-80 shrink-0 overflow-y-auto border-l border-border bg-surface-sunken/50 p-3">
            <div className="mb-2 flex items-center justify-between">
              <h2 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                ファイルの詳細
              </h2>
              <Button variant="ghost" size="xs" onClick={() => setSelectedKey(null)}>
                閉じる
              </Button>
            </div>
            {detail}
          </aside>
        )}
      </div>

      {/* 狭幅の詳細はモーダルなので、構図モーダルと重ならないよう引っ込める。 */}
      {!isWide && selected && !builder && (
        <Modal title={selected.name} onClose={() => setSelectedKey(null)} closeOnBackdrop>
          {detail}
        </Modal>
      )}

      {builder && (
        <BlockingBuilderModal
          item={builder.item}
          onSaved={() => {
            onChanged?.()
            void load()
          }}
          onClose={() => setBuilder(null)}
        />
      )}
    </main>
  )
}
