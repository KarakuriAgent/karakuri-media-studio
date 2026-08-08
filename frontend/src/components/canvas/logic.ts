/**
 * キャンバス画面の純関数（座標変換・カードの並べ方・カードの見せ方）。
 *
 * 描画から切り離してあるのは `studio/studio.ts` と同じ理由で、ここだけを単体で
 * テストできるようにするため。
 *
 * カード 1 枚 = スタジオの 1 エンティティなので、中身は必ず
 * `GET /api/studio/projects/{id}`（StudioProjectDetail）から引く。カードが
 * 持っているのは「どの行か」と「どこに置いてあるか」だけ。
 */
import type {
  CanvasAttachment,
  CanvasCard,
  CanvasCardKind,
  CanvasMessage,
  CanvasModelData,
  CanvasTextData,
  CanvasViewport,
  JobProgress,
  StudioAsset,
  StudioAssetCategory,
  StudioEpisode,
  StudioProjectDetail,
  StudioScene,
  StudioShot,
  StudioTake,
} from '../../types'
import { episodeLabel } from '../studio/studio'

// --------------------------------------------------------------------------
// カード種別
// --------------------------------------------------------------------------

/** カードの並び順（素材 -> 脚本 -> 生成物 -> キャンバス専用）。 */
export const CARD_KINDS: CanvasCardKind[] = [
  'character',
  'location',
  'object',
  'style',
  'reference',
  'scene',
  'shot',
  'media',
  'text',
  'model',
]

export const KIND_LABEL: Record<CanvasCardKind, string> = {
  character: 'キャラ',
  location: '場所',
  object: '小物',
  style: '画風',
  reference: '参照',
  scene: '場',
  shot: 'カット',
  media: '生成物',
  text: 'メモ',
  model: 'モデル設定',
}

export const KIND_ICON: Record<CanvasCardKind, string> = {
  character: '🧑',
  location: '🏞',
  object: '📦',
  style: '🎨',
  reference: '🔖',
  scene: '🎞',
  shot: '🎬',
  media: '🖼',
  text: '📝',
  model: '⚙️',
}

/** カード種別ごとのヘッダー色（ボード上で種別を見分けるため）。 */
export const KIND_STYLE: Record<CanvasCardKind, string> = {
  character: 'border-violet-800 bg-violet-950/60 text-violet-200',
  location: 'border-emerald-800 bg-emerald-950/60 text-emerald-200',
  object: 'border-amber-800 bg-amber-950/60 text-amber-200',
  style: 'border-fuchsia-800 bg-fuchsia-950/60 text-fuchsia-200',
  reference: 'border-slate-600 bg-slate-800/60 text-slate-200',
  scene: 'border-indigo-800 bg-indigo-950/60 text-indigo-200',
  shot: 'border-sky-800 bg-sky-950/60 text-sky-200',
  media: 'border-teal-800 bg-teal-950/60 text-teal-200',
  text: 'border-slate-600 bg-slate-800/60 text-slate-200',
  model: 'border-rose-800 bg-rose-950/60 text-rose-200',
}

/**
 * 素材カードの種別 -> `studio_assets.category`
 * （backend/app/canvas.py の `CARD_CATEGORIES` の写し）。
 */
export const KIND_CATEGORY: Partial<Record<CanvasCardKind, StudioAssetCategory>> = {
  character: 'character',
  location: 'environment',
  object: 'prop',
  style: 'style',
  reference: 'reference',
}

/** 逆引き（素材をカード化するときに、どの種別のカードになるか）。 */
export const CATEGORY_KIND: Record<StudioAssetCategory, CanvasCardKind> = {
  character: 'character',
  environment: 'location',
  prop: 'object',
  style: 'style',
  reference: 'reference',
}

/** エンティティを持たない（キャンバス専用の）カード。 */
export const STANDALONE_KINDS: CanvasCardKind[] = ['text', 'model']

export function isStandalone(kind: CanvasCardKind): boolean {
  return STANDALONE_KINDS.includes(kind)
}

export function isAssetKind(kind: CanvasCardKind): boolean {
  return kind in KIND_CATEGORY
}

/** 新規作成でエンティティも一緒に作れる種別（media は Take なので作れない）。 */
export function canCreateEntity(kind: CanvasCardKind): boolean {
  return kind !== 'media'
}

/**
 * 「＋」メニューに出す種別。
 *
 * 生成結果（media）が無いのは、Take が生まれるとサーバー側の鏡が勝手に
 * カードにするため——キャンバスから作るものではない。
 */
export const ADDABLE_KINDS: CanvasCardKind[] = CARD_KINDS.filter(canCreateEntity)

// --------------------------------------------------------------------------
// タブ（作品共通 + 話ごと）
// --------------------------------------------------------------------------
//
// 盤面は話ごとに分かれる。どのタブに出るかはスタジオの所属から**導く**
// （場 -> その話、カット -> 場の話、生成物 -> カットの話）ので、カード側には
// 持たない。素材と未分類のカットは話に属さないので「作品共通」タブに出る。
// 中身を持つ text / model カードだけが自分の `episode_id` に従う。
// （backend/app/canvas.py の `entity_episodes` / `card_episode` の写し）

/** 作品共通タブの呼び名。 */
export const COMMON_TAB_LABEL = '作品共通'

/** キャンバス上部に並べる 1 タブ。`episodeId` が null なら作品共通。 */
export interface CanvasTab {
  episodeId: string | null
  label: string
}

/** タブの並び（作品共通 -> 話を sort_order 順に）。 */
export function canvasTabs(detail: { episodes: StudioEpisode[] }): CanvasTab[] {
  return [
    { episodeId: null, label: COMMON_TAB_LABEL },
    ...detail.episodes.map((episode, index) => ({
      episodeId: episode.id,
      label: episodeLabel(episode, index),
    })),
  ]
}

/** スタジオの行 id -> その行が属する話（null = 作品共通）。 */
export function entityEpisodes(detail: {
  scenes: StudioScene[]
  shots: StudioShot[]
  takes: StudioTake[]
}): Record<string, string | null> {
  const index: Record<string, string | null> = {}
  const sceneEpisodes: Record<string, string> = {}
  for (const scene of detail.scenes) {
    sceneEpisodes[scene.id] = scene.episode_id
    index[scene.id] = scene.episode_id
  }
  for (const shot of detail.shots) {
    index[shot.id] = shot.scene_id ? (sceneEpisodes[shot.scene_id] ?? null) : null
  }
  for (const take of detail.takes) index[take.id] = index[take.shot_id] ?? null
  return index
}

/** そのカードが出るタブ（null = 作品共通）。 */
export function cardEpisode(
  card: CanvasCard,
  index: Record<string, string | null>,
): string | null {
  if (!card.entity_id) return card.episode_id
  return index[card.entity_id] ?? null
}

/** そのタブに出るカードだけ（並びは元のまま）。 */
export function cardsInTab(
  cards: CanvasCard[],
  index: Record<string, string | null>,
  episodeId: string | null,
): CanvasCard[] {
  return cards.filter((card) => cardEpisode(card, index) === episodeId)
}

/** どの場にも属していないカットのカードか（作品共通タブで「未分類」と出す）。 */
export function isLooseShot(card: CanvasCard, detail: StudioProjectDetail): boolean {
  const shot = shotOf(card, detail)
  return Boolean(shot) && !shot?.scene_id
}

// --------------------------------------------------------------------------
// 座標変換（無限キャンバス）
// --------------------------------------------------------------------------

/** カードの既定の大きさ（backend/app/models.py の CanvasCard の既定値）。 */
export const CARD_W = 320
export const CARD_H = 220

export const MIN_ZOOM = 0.25
export const MAX_ZOOM = 2

export interface Point {
  x: number
  y: number
}

export function clampZoom(zoom: number): number {
  if (!Number.isFinite(zoom)) return 1
  return Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, zoom))
}

/**
 * 画面（コンテナ左上を原点とする px）-> ボード座標。
 *
 * 描画側は `translate(viewport.x, viewport.y) scale(viewport.zoom)` を
 * `transform-origin: 0 0` で掛けるので、その逆変換になる。
 */
export function screenToBoard(point: Point, viewport: CanvasViewport): Point {
  const zoom = viewport.zoom || 1
  return { x: (point.x - viewport.x) / zoom, y: (point.y - viewport.y) / zoom }
}

/** ボード座標 -> 画面（コンテナ左上を原点とする px）。 */
export function boardToScreen(point: Point, viewport: CanvasViewport): Point {
  const zoom = viewport.zoom || 1
  return { x: point.x * zoom + viewport.x, y: point.y * zoom + viewport.y }
}

/** 表示位置をずらす（ドラッグでの流し見）。 */
export function panBy(viewport: CanvasViewport, dx: number, dy: number): CanvasViewport {
  return { ...viewport, x: viewport.x + dx, y: viewport.y + dy }
}

/**
 * `pivot`（画面上の点）を動かさずに拡大率を変える。
 *
 * ホイールでのズームとボタンでのズームで同じ計算を使う。倍率は
 * `MIN_ZOOM`〜`MAX_ZOOM` に丸めるので、端では動かなくなる。
 */
export function zoomAt(
  viewport: CanvasViewport,
  factor: number,
  pivot: Point,
): CanvasViewport {
  const zoom = clampZoom(viewport.zoom * factor)
  // ピボットのボード座標が変わらないように原点をずらす
  const board = screenToBoard(pivot, viewport)
  return { zoom, x: pivot.x - board.x * zoom, y: pivot.y - board.y * zoom }
}

/**
 * カード全体が見える表示位置。
 *
 * サーバーが盤面を自動で埋める（スタジオの鏡）ので、初めて開いたときは
 * どこに何があるか分からない——そこで全部が入るところまで引く。拡大は
 * しない（等倍が上限）ので、カードが少ないときに巨大化しない。
 */
export function fitViewport(
  cards: CanvasCard[],
  size: { width: number; height: number },
  padding = 48,
): CanvasViewport {
  if (cards.length === 0 || size.width <= 0 || size.height <= 0) {
    return { x: 0, y: 0, zoom: 1 }
  }
  const left = Math.min(...cards.map((card) => card.x))
  const top = Math.min(...cards.map((card) => card.y))
  const width = Math.max(1, Math.max(...cards.map((card) => card.x + card.w)) - left)
  const height = Math.max(1, Math.max(...cards.map((card) => card.y + card.h)) - top)
  const zoom = clampZoom(
    Math.min(
      (size.width - padding * 2) / width,
      (size.height - padding * 2) / height,
      1,
    ),
  )
  return {
    zoom,
    x: (size.width - width * zoom) / 2 - left * zoom,
    y: (size.height - height * zoom) / 2 - top * zoom,
  }
}

/** いま見えている範囲の中心（ボード座標）。 */
export function viewCenter(
  size: { width: number; height: number },
  viewport: CanvasViewport,
): Point {
  return screenToBoard({ x: size.width / 2, y: size.height / 2 }, viewport)
}

/**
 * 置きたい場所が既にカードで埋まっていたら、空くまで斜めにずらす。
 *
 * 「＋」から続けて作ったカードが 1 枚に重なって見えないのを避けるためだけの
 * 処理で、厳密な当たり判定はしない（左上が一致するかどうかで見る）。
 */
export function freeSpot(
  cards: CanvasCard[],
  wanted: Point,
  step = 32,
  limit = 40,
): Point {
  const taken = (point: Point) =>
    cards.some(
      (card) => Math.abs(card.x - point.x) < step && Math.abs(card.y - point.y) < step,
    )
  const spot = { ...wanted }
  for (let index = 0; index < limit && taken(spot); index += 1) {
    spot.x += step
    spot.y += step
  }
  return spot
}

/** 整列の並び順（種別 -> 置いた順）。 */
function orderIndex(card: CanvasCard): number {
  const index = CARD_KINDS.indexOf(card.kind)
  return index < 0 ? CARD_KINDS.length : index
}

/**
 * カードを格子状に並べ直す（種別ごとにまとまるよう、種別 -> 作成順で並べる）。
 *
 * 返すのは新しい置き場所だけで、実際に動かすのは呼び出し側
 * （`PUT /api/canvas/cards/{id}/position`）。
 */
export function arrangeCards(
  cards: CanvasCard[],
  options: {
    origin?: Point
    columns?: number
    gapX?: number
    gapY?: number
  } = {},
): { id: string; x: number; y: number }[] {
  const origin = options.origin ?? { x: 0, y: 0 }
  const gapX = options.gapX ?? CARD_W + 32
  const gapY = options.gapY ?? CARD_H + 32
  const columns = Math.max(1, options.columns ?? Math.ceil(Math.sqrt(cards.length || 1)))
  const sorted = [...cards].sort((left, right) => {
    const byKind = orderIndex(left) - orderIndex(right)
    if (byKind !== 0) return byKind
    return left.created_at < right.created_at ? -1 : left.created_at > right.created_at ? 1 : 0
  })
  return sorted.map((card, index) => ({
    id: card.id,
    x: origin.x + (index % columns) * gapX,
    y: origin.y + Math.floor(index / columns) * gapY,
  }))
}

/** 一番手前の重なり順（新しく掴んだカードを最前面に出すときに使う）。 */
export function topZ(cards: CanvasCard[]): number {
  return cards.reduce((top, card) => Math.max(top, card.z), 0)
}

/** 取得済みのカード列に 1 枚ぶんの変更を反映する（作成・更新の応答で確定）。 */
export function upsertCard(cards: CanvasCard[], card: CanvasCard): CanvasCard[] {
  return cards.some((item) => item.id === card.id)
    ? cards.map((item) => (item.id === card.id ? card : item))
    : [...cards, card]
}

export function dropCard(cards: CanvasCard[], cardId: string): CanvasCard[] {
  return cards.filter((card) => card.id !== cardId)
}

// --------------------------------------------------------------------------
// カードの中身（スタジオ側から引く）
// --------------------------------------------------------------------------

/** カードが指しているスタジオの行（見つからなければ null）。 */
export type CardEntity = StudioAsset | StudioScene | StudioShot | StudioTake | null

export function entityOf(card: CanvasCard, detail: StudioProjectDetail): CardEntity {
  if (!card.entity_id) return null
  if (isAssetKind(card.kind)) {
    return detail.assets.find((asset) => asset.id === card.entity_id) ?? null
  }
  if (card.kind === 'scene') {
    return detail.scenes.find((scene) => scene.id === card.entity_id) ?? null
  }
  if (card.kind === 'shot') {
    return detail.shots.find((shot) => shot.id === card.entity_id) ?? null
  }
  if (card.kind === 'media') {
    return detail.takes.find((take) => take.id === card.entity_id) ?? null
  }
  return null
}

export function assetOf(card: CanvasCard, detail: StudioProjectDetail): StudioAsset | null {
  return isAssetKind(card.kind) ? (entityOf(card, detail) as StudioAsset | null) : null
}

export function sceneOf(card: CanvasCard, detail: StudioProjectDetail): StudioScene | null {
  return card.kind === 'scene' ? (entityOf(card, detail) as StudioScene | null) : null
}

export function shotOf(card: CanvasCard, detail: StudioProjectDetail): StudioShot | null {
  return card.kind === 'shot' ? (entityOf(card, detail) as StudioShot | null) : null
}

export function takeOf(card: CanvasCard, detail: StudioProjectDetail): StudioTake | null {
  return card.kind === 'media' ? (entityOf(card, detail) as StudioTake | null) : null
}

/**
 * 参照先が見つからないカードか。
 *
 * DB のトリガーで一緒に消えるはずだが、スタジオ側の削除とキャンバスの取り直しの
 * 間には隙間があるので、その一瞬を「読み込み中ではない」形で見せる。
 */
export function isDangling(card: CanvasCard, detail: StudioProjectDetail): boolean {
  return Boolean(card.entity_id) && entityOf(card, detail) === null
}

function firstLine(text: string): string {
  return (text || '').split('\n')[0].trim()
}

export function textDataOf(card: CanvasCard): CanvasTextData {
  return { ...(defaultDataFor('text') as unknown as CanvasTextData), ...card.data }
}

export function modelDataOf(card: CanvasCard): CanvasModelData {
  const fallback = defaultDataFor('model') as unknown as CanvasModelData
  const data = card.data as unknown as Partial<CanvasModelData>
  return {
    ...fallback,
    ...data,
    params: { ...fallback.params, ...(data.params ?? {}) },
  }
}

/** カードの見出し（スタジオ側の名前 / タイトル）。 */
export function cardTitle(card: CanvasCard, detail: StudioProjectDetail): string {
  if (card.kind === 'text') return firstLine(textDataOf(card).body) || 'メモ'
  if (card.kind === 'model') {
    const data = modelDataOf(card)
    return data.workflow || 'モデル設定'
  }
  const entity = entityOf(card, detail)
  if (!entity) return '（参照先がありません）'
  if (isAssetKind(card.kind)) return `@${(entity as StudioAsset).name}`
  if (card.kind === 'scene') return (entity as StudioScene).title || '無題の場'
  if (card.kind === 'shot') return (entity as StudioShot).title || '無題のカット'
  const take = entity as StudioTake
  const shot = detail.shots.find((item) => item.id === take.shot_id)
  return shot?.title || '生成結果'
}

/** カード 1 枚の要約（ボード上の数行表示）。 */
export function cardSummary(card: CanvasCard, detail: StudioProjectDetail): string {
  if (card.kind === 'text') return textDataOf(card).body
  if (card.kind === 'model') {
    const data = modelDataOf(card)
    const target =
      data.target === 'image' ? '画像' : data.target === 'video' ? '動画' : '音声'
    return `${target} / ${data.workflow || 'ワークフロー未選択'}${
      data.note ? `\n${data.note}` : ''
    }`
  }
  const entity = entityOf(card, detail)
  if (!entity) return ''
  if (isAssetKind(card.kind)) {
    const asset = entity as StudioAsset
    return asset.caption || asset.prompt_caption || ''
  }
  if (card.kind === 'scene') {
    const scene = entity as StudioScene
    return scene.synopsis || scene.time_of_day || ''
  }
  if (card.kind === 'shot') {
    const shot = entity as StudioShot
    return shot.prompt || shot.action || shot.purpose || ''
  }
  const take = entity as StudioTake
  return take.error || take.prompt || ''
}

/** その場に属するカット（scene カードに軽く並べる）。 */
export function shotsInScene(
  detail: StudioProjectDetail,
  sceneId: string,
): StudioShot[] {
  return detail.shots.filter((shot) => shot.scene_id === sceneId)
}

/** media カードに出すプレビュー（Take の成果物。まだ無ければ null）。 */
export function takeMedia(
  take: StudioTake,
): { kind: 'video' | 'image'; url: string } | null {
  if (take.video_url) return { kind: 'video', url: take.video_url }
  if (take.last_frame_url) return { kind: 'image', url: take.last_frame_url }
  return null
}

/** 素材カードに出すプレビュー（ファイルなしの素材は null）。 */
export function assetMedia(
  asset: StudioAsset,
): { kind: 'image' | 'video' | 'audio'; url: string } | null {
  return asset.url ? { kind: asset.kind, url: asset.url } : null
}

// --------------------------------------------------------------------------
// キャンバス専用カードの中身
// --------------------------------------------------------------------------

/** 新規カードの空 `data`（backend/app/models.py のスキーマの既定値と同じ）。 */
export function defaultDataFor(kind: CanvasCardKind): Record<string, unknown> {
  if (kind === 'text') return { body: '' }
  if (kind === 'model') {
    return {
      target: 'image',
      workflow: '',
      params: {
        aspect_ratio: '4:3 (Standard)',
        megapixels: 1.0,
        duration: 10.0,
        fps: 25,
        loras: [],
        video_loras: [],
        negative_prompt: '',
        selects: {},
        model_overrides: {},
      },
      note: '',
    }
  }
  return {}
}

// --------------------------------------------------------------------------
// 素材の拡張項目（`studio_assets.profile`）
// --------------------------------------------------------------------------

export interface ProfileField {
  key: string
  label: string
  /** 'text' = 1 行 / 'area' = 複数行 / 'urls' = 1 行 1 件の URL。 */
  kind: 'text' | 'area' | 'urls'
  hint?: string
}

/**
 * 分類ごとの拡張項目（backend/app/models.py の `ASSET_PROFILE_MODELS` の写し）。
 *
 * ここに無い項目を送ると API が弾くので、フォームはこの表からだけ作る。
 */
export const PROFILE_FIELDS: Record<StudioAssetCategory, ProfileField[]> = {
  character: [
    { key: 'appearance', label: '外見', kind: 'area', hint: '生成プロンプトに使える具体性で' },
    { key: 'personality', label: '性格', kind: 'area' },
    { key: 'voice', label: '声・話し方', kind: 'area', hint: '音声つき生成の手がかり' },
    { key: 'notes', label: 'メモ', kind: 'area' },
  ],
  environment: [
    { key: 'mood', label: '雰囲気', kind: 'area', hint: '時間帯・天候・空気感' },
    { key: 'notes', label: 'メモ', kind: 'area' },
  ],
  prop: [{ key: 'notes', label: 'メモ', kind: 'area' }],
  style: [
    { key: 'palette', label: 'パレット', kind: 'area', hint: '色調・カラーパレットのメモ' },
    { key: 'references', label: '参照画像', kind: 'urls' },
    { key: 'notes', label: 'メモ', kind: 'area' },
  ],
  reference: [{ key: 'notes', label: 'メモ', kind: 'area' }],
}

/** 素材の `profile` をフォームの初期値に均す（知らない項目は落とす）。 */
export function profileForm(asset: StudioAsset): Record<string, string> {
  const profile = (asset.profile ?? {}) as Record<string, unknown>
  const form: Record<string, string> = {}
  for (const field of PROFILE_FIELDS[asset.category]) {
    const value = profile[field.key]
    form[field.key] =
      field.kind === 'urls'
        ? (Array.isArray(value) ? value : []).join('\n')
        : typeof value === 'string'
          ? value
          : ''
  }
  return form
}

/** フォームの値を PATCH に載せる `profile` に戻す（丸ごと置き換わる）。 */
export function profilePayload(
  category: StudioAssetCategory,
  form: Record<string, string>,
): Record<string, unknown> {
  const profile: Record<string, unknown> = {}
  for (const field of PROFILE_FIELDS[category]) {
    const value = form[field.key] ?? ''
    profile[field.key] =
      field.kind === 'urls'
        ? value
            .split('\n')
            .map((line) => line.trim())
            .filter(Boolean)
        : value
  }
  return profile
}

// --------------------------------------------------------------------------
// チャット
// --------------------------------------------------------------------------

/** `@` オートコンプリートの候補に出す最大件数。 */
export const MENTION_LIMIT = 8

/**
 * カーソル位置から後方に `@` を探す（間に空白や別の `@` があれば補完しない）。
 *
 * `@` の直前は行頭か空白であることを求める（メールアドレスのような文字列で
 * 候補が開かないようにするため）。
 */
export function mentionQueryAt(
  text: string,
  caret: number,
): { start: number; query: string } | null {
  const head = text.slice(0, Math.max(0, Math.min(caret, text.length)))
  const start = head.lastIndexOf('@')
  if (start < 0) return null
  const before = start > 0 ? head[start - 1] : ''
  if (before && !/\s/.test(before)) return null
  const query = head.slice(start + 1)
  if (/[\s@]/.test(query)) return null
  return { start, query }
}

/** 補完の確定: `[start, caret)` を `@素材名 ` に置き換える。 */
export function applyMention(
  text: string,
  start: number,
  caret: number,
  name: string,
): { text: string; caret: number } {
  const inserted = `@${name} `
  const next = text.slice(0, start) + inserted + text.slice(caret)
  return { text: next, caret: start + inserted.length }
}

/** 入力中の語で素材を絞る（スタジオの `@素材名` と同じ呼び方）。 */
export function mentionCandidates(
  assets: StudioAsset[],
  query: string,
  limit = MENTION_LIMIT,
): StudioAsset[] {
  const needle = query.trim().toLowerCase()
  return assets
    .filter((asset) => !needle || asset.name.toLowerCase().includes(needle))
    .slice(0, limit)
}

/**
 * 会話に 1 件足す（同じ id は上書き）。
 *
 * 同じ発言が WS と応答の両方から届くので、id で重ならないようにする。
 */
export function appendMessage(
  messages: CanvasMessage[],
  message: CanvasMessage,
): CanvasMessage[] {
  return messages.some((item) => item.id === message.id)
    ? messages.map((item) => (item.id === message.id ? message : item))
    : [...messages, message]
}

/**
 * ジョブ進捗（WS `type: "job"`）の「盤面に効く変化」だけを表す文字列。
 *
 * キャンバスはスタジオの鏡なので、生成が始まった / 終わった（＝ Take が現れた
 * / 完成した）ら盤面を取り直したい。ところが進捗フレームは % ごとに何度も届く
 * ので、そのまま見ると取り直しの連打になる。ここでは **job_id と状態だけ**を
 * 並べた文字列にして、状態が動いたときだけ取り直せるようにする。
 */
export function jobSignature(progress: Record<string, JobProgress>): string {
  return Object.keys(progress)
    .sort()
    .map((jobId) => `${jobId}:${progress[jobId].status}`)
    .join(',')
}

/** 会話の発言に添えられた添付（`data.attachments`）。 */
export function messageAttachments(
  message: CanvasMessage,
): CanvasAttachment[] {
  const list = message.data?.attachments
  if (!Array.isArray(list)) return []
  return list.flatMap((item) => {
    if (!item || typeof item !== 'object') return []
    const attachment = item as Partial<CanvasAttachment>
    if (typeof attachment.path !== 'string') return []
    return [
      {
        path: attachment.path,
        name: attachment.name ?? attachment.path.split('/').pop() ?? '添付',
        abs_path: attachment.abs_path ?? '',
        kind: attachment.kind ?? 'document',
      },
    ]
  })
}

/**
 * 吹き出しに出す本文。
 *
 * 添付つきの発言は、エージェントに渡す一覧が本文の後ろに足されている。画面には
 * ユーザーが書いた文（`data.text`）だけを出す。
 */
export function messageText(message: CanvasMessage): string {
  const text = message.data?.text
  return typeof text === 'string' ? text : message.content
}

/** 実行中の表示（活動が分かればそれを、分からなければ既定の一言）。 */
export function runningLabel(activity: string | null): string {
  return activity ? `実行中: ${activity}` : 'エージェントが作業しています…'
}

export function shortTime(ts: string): string {
  const parsed = new Date(ts)
  return Number.isNaN(parsed.getTime())
    ? ''
    : parsed.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}
