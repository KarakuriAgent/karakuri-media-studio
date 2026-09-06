/**
 * ファイルタブの小道具（SPEC §7.2 / §8）。
 *
 * 表示のための純関数だけを置く（API 呼び出しと状態は :file:`LibraryView.tsx`）。
 */
import type {
  Job,
  LibraryItem,
  LibraryKind,
  LibrarySource,
  StudioAssetItem,
  TimelineExportItem,
} from '../../types'
import { CATEGORY_LABELS } from '../LibraryPickerModal'
import { ASSET_CATEGORY_LABEL } from '../studio/studio'

/** 種別の表示名（プルダウンの並び順もこの通り）。 */
export const KIND_LABELS: Record<LibraryKind, string> = {
  image: '画像',
  video: '動画',
  audio: '音声',
}

/** 1 ページの件数（グリッドなのでモーダルより多めに読む）。 */
export const LIBRARY_PAGE_SIZE = 48

/** 一覧・詳細に出す日時（UTC のまま「分」まで）。 */
export function libraryTimestamp(item: LibraryItem): string {
  return item.created_at.replace('T', ' ').replace('+00:00', '').slice(0, 16)
}

/**
 * 表示に使う URL。
 *
 * 構図リファレンスは焼き直しても**同じパスに上書き**されるので、そのままだと
 * ブラウザのキャッシュと React の差分の両方が古い動画を出し続ける。版
 * （`blocking_version`）を問い合わせに足して、焼き直すたびに別の URL にする。
 */
export function mediaUrl(item: {
  url: string
  blocking_version?: number
}): string {
  if (!item.url || !item.blocking_version) return item.url
  return `${item.url}${item.url.includes('?') ? '&' : '?'}v=${item.blocking_version}`
}


// --------------------------------------------------------------------------
// 出どころ（ライブラリ / 生成履歴 / プロジェクト素材 / 書き出し）
// --------------------------------------------------------------------------

/** 一覧に出す出どころ（`all` は 4 つを混ぜて日時の新しい順）。 */
export type LibrarySourceTab = 'all' | 'library' | 'jobs' | 'assets' | 'exports'

/** 出どころ切り替えのラベル（並び順もこの通り）。 */
export const SOURCE_LABELS: Record<LibrarySourceTab, string> = {
  all: 'すべて',
  library: 'ライブラリ',
  jobs: '生成履歴',
  assets: 'プロジェクト素材',
  exports: '書き出し',
}

/** タイルの左下に出す出どころの印（ライブラリだけは印を出さない）。 */
export const ORIGIN_BADGES: Record<LibraryEntry['origin'], string> = {
  library: '',
  job: '生成履歴',
  asset: 'プロジェクト素材',
  export: '書き出し',
}

/** ジョブの成果物 1 つ 1 つのラベル（タイルの 2 行目に出す）。 */
export const OUTPUT_LABELS: Record<LibrarySource, string> = {
  image: '画像',
  last_frame: '最終フレーム',
  video: '動画',
  audio: '音声',
}

/**
 * 一覧に並ぶ 1 タイル。
 *
 * 出どころが 4 つあるので、タイルとして必要なぶん（見た目と並び替えに使う値）
 * だけをここに写し、詳細パネルは元の値（`item` / `job` / `asset`）から作る。
 * ジョブは**成果物 1 ファイル = 1 タイル**に展開するので、1 件が複数の
 * エントリになりうる。
 */
export interface LibraryEntry {
  /** 一覧の中で一意な key（出どころをまたいでもぶつからない）。 */
  key: string
  origin: 'library' | 'job' | 'asset' | 'export'
  kind: LibraryKind
  /** タイルの 1 行目（素材名 / プロンプトの先頭 / 素材名）。 */
  name: string
  /** タイルの 2 行目（種別・分類・作品名など、出どころごとの補足）。 */
  note: string
  /** プレビューの URL（メタデータのみの素材では空）。 */
  url: string
  /** 並べ替えに使う日時（ISO 文字列。新しい順）。 */
  createdAt: string
  nsfw: boolean
  item?: LibraryItem
  job?: Job
  /** ジョブのどの成果物か（`origin === 'job'` のときだけ）。 */
  output?: LibrarySource
  asset?: StudioAssetItem
  export?: TimelineExportItem
}

/** プロンプトの先頭（タイルの 1 行目に出すぶんだけ）。 */
export function promptHead(job: Job, length = 40): string {
  const text = (
    job.video_prompt ||
    job.image_prompt ||
    job.audio_prompt ||
    job.user_input ||
    ''
  )
    .replace(/\s+/g, ' ')
    .trim()
  if (!text) return `${job.mode} ジョブ`
  return text.length > length ? `${text.slice(0, length)}…` : text
}

/**
 * Take 由来のジョブの出どころ（`作品名 / カット題名`）。
 *
 * スタジオを通していないジョブでは空文字（一覧 `GET /api/jobs` だけが
 * `project_name` / `shot_title` を返す）。
 */
export function takeLabel(job: Job): string {
  return [job.project_name, job.shot_title].filter(Boolean).join(' / ')
}

/** 一覧・詳細に出す日時（UTC のまま「分」まで）。 */
export function timestampOf(value: string): string {
  return value.replace('T', ' ').replace('+00:00', '').slice(0, 16)
}

/** ライブラリ項目 1 件をタイルにする。 */
export function libraryEntry(item: LibraryItem): LibraryEntry {
  return {
    key: `library:${item.id}`,
    origin: 'library',
    kind: item.kind,
    name: item.name,
    note:
      KIND_LABELS[item.kind] +
      (item.category ? ` / ${CATEGORY_LABELS[item.category]}` : ''),
    url: mediaUrl(item),
    createdAt: item.created_at,
    nsfw: item.nsfw,
    item,
  }
}

/**
 * ジョブ 1 件を**成果物ごと**のタイルに展開する。
 *
 * 画像・最終フレーム・動画・音声（`POST /api/library/from-job` が受ける 4 つ）
 * だけを出す。`extra_output_urls` はライブラリに登録する経路が無いので、ここでも
 * 並べない。
 */
export function jobEntries(job: Job): LibraryEntry[] {
  const outputs: { source: LibrarySource; url: string | null; kind: LibraryKind }[] = [
    { source: 'image', url: job.image_url, kind: 'image' },
    { source: 'last_frame', url: job.last_frame_url, kind: 'image' },
    { source: 'video', url: job.video_url, kind: 'video' },
    { source: 'audio', url: job.audio_output_url, kind: 'audio' },
  ]
  return outputs
    .filter((output) => Boolean(output.url))
    .map((output) => ({
      key: `job:${job.id}:${output.source}`,
      origin: 'job' as const,
      kind: output.kind,
      name: promptHead(job),
      note: [
        OUTPUT_LABELS[output.source],
        takeLabel(job),
        timestampOf(job.created_at),
      ]
        .filter(Boolean)
        .join(' / '),
      url: output.url ?? '',
      createdAt: job.created_at,
      nsfw: job.nsfw,
      job,
      output: output.source,
    }))
}

/** プロジェクト素材 1 件をタイルにする（NSFW は作品の指定に従う）。 */
export function assetEntry(asset: StudioAssetItem): LibraryEntry {
  return {
    key: `asset:${asset.id}`,
    origin: 'asset',
    kind: asset.kind,
    name: asset.name,
    note: `${asset.project_name} / ${ASSET_CATEGORY_LABEL[asset.category] ?? asset.category}`,
    url: asset.url,
    createdAt: asset.created_at,
    nsfw: asset.project_nsfw,
    asset,
  }
}

/** 書き出した mp4 の尺（`1:23` 形式。まだ測れていなければ空）。 */
export function exportLength(item: TimelineExportItem): string {
  if (!item.duration_ms) return ''
  const total = Math.round(item.duration_ms / 1000)
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, '0')}`
}

/** 書き出しの規格（`1280x720` / 尺 / 演出つき）をタイルの 2 行目にまとめる。 */
export function exportNote(item: TimelineExportItem): string {
  const size = item.width && item.height ? `${item.width}x${item.height}` : ''
  return ['書き出し', item.project_name, size, exportLength(item), fxLabel(item)]
    .filter(Boolean)
    .join(' / ')
}

/** 演出（FX トラック）まで載った書き出しか（載っていなければ空文字）。 */
export function fxLabel(item: TimelineExportItem): string {
  if (!item.fx_job_id) return ''
  return item.fx_status === 'done' ? 'FX' : `FX（${item.fx_status ?? '待ち'}）`
}

/**
 * 書き出し 1 本をタイルにする（1 ファイル = 1 タイル）。
 *
 * 演出まで焼けていればそちらを見せる（人が「完成品」と思うのはそちら）。
 * NSFW は持ち主の作品の指定に従う（プロジェクト素材と同じ）。
 */
export function exportEntry(item: TimelineExportItem): LibraryEntry {
  return {
    key: `export:${item.id}`,
    origin: 'export',
    kind: 'video',
    name: item.timeline_name || '（名前のないタイムライン）',
    note: exportNote(item),
    url: (item.fx_status === 'done' ? item.fx_video_url : null) ?? item.output_url ?? '',
    createdAt: item.created_at,
    nsfw: item.project_nsfw,
    export: item,
  }
}

/** 出どころをまたいで日時の新しい順に混ぜる（`すべて` の並び）。 */
export function mergeEntries(...lists: LibraryEntry[][]): LibraryEntry[] {
  return lists
    .flat()
    .sort((left, right) => right.createdAt.localeCompare(left.createdAt))
}

/** 実際に出すタイル（`showNsfw` がオフなら NSFW を落とす）。 */
export function visibleEntries(
  entries: LibraryEntry[],
  showNsfw: boolean,
): LibraryEntry[] {
  return showNsfw ? entries : entries.filter((entry) => !entry.nsfw)
}

/**
 * 生成履歴のタイルに**種別**の絞り込みを効かせる。
 *
 * 検索語・種別・NSFW はサーバー側（`GET /api/jobs` の `q` / `kind` / `nsfw`）で
 * 絞るようになったが、`kind` はジョブ単位の絞り込みなので、1 ジョブを成果物
 * ごとに展開したタイルからは「その種別でない成果物」を落とす必要がある
 * （画像も動画も出したジョブを `kind=video` で引いたときの画像タイルなど）。
 */
export function filterJobEntries(
  entries: LibraryEntry[],
  { kind }: { kind: LibraryKind | '' },
): LibraryEntry[] {
  if (!kind) return entries
  return entries.filter((entry) => entry.kind === kind)
}
