/**
 * ドラマスタジオ画面の純関数まわり（並べ替え・状態の見せ方・フォームの検証・
 * `@素材名` メンションの切り出し）。
 *
 * 描画から切り離してあるのは `src/form.ts` と同じ理由で、ここだけを単体で
 * テストできるようにするため。
 */
import { COMFY_TARGET_LABELS } from '../../form'
import type {
  ComfyTarget,
  StudioAsset,
  StudioAssetCategory,
  StudioAssetKind,
  StudioEpisode,
  StudioProjectDetail,
  StudioRenderRequest,
  StudioRevisionActor,
  StudioScene,
  StudioShot,
  StudioShotStatus,
  StudioShotUpdate,
  StudioTake,
  StudioTakeStatus,
  StudioVideoQuality,
  StudioWorkflowOverride,
} from '../../types'

// --------------------------------------------------------------------------
// ラベルと見た目
// --------------------------------------------------------------------------

export const TAKE_STATUS_LABEL: Record<StudioTakeStatus, string> = {
  rendering: '生成中',
  candidate: '候補',
  selected: '採用',
  rejected: '却下',
  failed: '失敗',
}

export function takeActivityLabel(
  take: StudioTake,
  progress?: { message?: string | null } | null,
): string {
  if (take.status === 'rendering' && progress?.message) return progress.message
  return TAKE_STATUS_LABEL[take.status]
}

export const TAKE_STATUS_CLASS: Record<StudioTakeStatus, string> = {
  rendering: 'border-amber-800 bg-amber-950 text-amber-300',
  candidate: 'border-sky-800 bg-sky-950 text-sky-300',
  selected: 'border-emerald-800 bg-emerald-950 text-emerald-300',
  rejected: 'border-border bg-secondary text-muted-foreground',
  failed: 'border-red-800 bg-red-950 text-red-300',
}

export const SHOT_STATUS_LABEL: Record<StudioShotStatus, string> = {
  draft: '執筆中',
  ready: '生成待ち',
  done: '採用済み',
}

export const SHOT_STATUS_CLASS: Record<StudioShotStatus, string> = {
  draft: 'border-border bg-secondary text-muted-foreground',
  ready: 'border-sky-800 bg-sky-950 text-sky-300',
  done: 'border-emerald-800 bg-emerald-950 text-emerald-300',
}

export const ASSET_CATEGORY_LABEL: Record<StudioAssetCategory, string> = {
  character: 'キャラクター',
  environment: '環境',
  prop: '小物',
  style: '画風',
  reference: '参照',
}

export const ASSET_CATEGORY_CLASS: Record<StudioAssetCategory, string> = {
  character: 'border-violet-800 bg-violet-950 text-violet-300',
  environment: 'border-emerald-800 bg-emerald-950 text-emerald-300',
  prop: 'border-amber-800 bg-amber-950 text-amber-300',
  style: 'border-fuchsia-800 bg-fuchsia-950 text-fuchsia-300',
  reference: 'border-border bg-secondary text-muted-foreground',
}

export const ASSET_CATEGORIES: StudioAssetCategory[] = [
  'character',
  'environment',
  'prop',
  'style',
  'reference',
]

export const ASSET_KIND_LABEL: Record<StudioAssetKind, string> = {
  image: '画像',
  video: '動画',
  audio: '音声',
}

/** ワークフローの強制指定（空文字 = 自動）。 */
export const WORKFLOW_OVERRIDES: StudioWorkflowOverride[] = [
  'minimax_h3_t2v',
  'minimax_h3_i2v',
  'minimax_h3_r2v',
]

export const WORKFLOW_OVERRIDE_LABEL: Record<StudioWorkflowOverride, string> = {
  minimax_h3_t2v: 't2v（文章だけから）',
  minimax_h3_i2v: 'i2v（開始フレームから）',
  minimax_h3_r2v: 'r2v（参照素材から）',
}

/** 動画生成の品質（プロジェクト設定。速い順ではなく「素からの寄り道」の順）。 */
export const VIDEO_QUALITIES: StudioVideoQuality[] = ['normal', 'opt', 'turbo']

export const VIDEO_QUALITY_LABEL: Record<StudioVideoQuality, string> = {
  normal: '通常',
  opt: 'Opt',
  turbo: 'Turbo',
}

export const VIDEO_QUALITY_HINT: Record<StudioVideoQuality, string> = {
  normal: '通常 = 素の MiniMax H3（20 steps）。どの接続先でも動く標準の品質。',
  opt: 'Opt = 20 steps のまま量子化と高速化だけを入れた版（品質は通常のまま速い）。',
  turbo: 'Turbo = 4 steps の蒸留 LoRA 版（いちばん速いが粗い）。',
}

/** 狭い画面のヘッダーに出す、プロジェクト生成設定の 1 行要約。 */
export function formatProjectSettingsSummary(input: {
  target?: ComfyTarget | null
  quality: StudioVideoQuality
  aspectRatio: string | null
  megapixels: number | null
  steps: number
}): string {
  const parts: string[] = []
  if (input.target) parts.push(COMFY_TARGET_LABELS[input.target])
  parts.push(VIDEO_QUALITY_LABEL[input.quality])
  const aspect = input.aspectRatio?.trim() ?? ''
  if (!aspect) {
    parts.push('既定')
  } else {
    const cut = aspect.indexOf(' (')
    parts.push(cut === -1 ? aspect : aspect.slice(0, cut))
  }
  parts.push(input.megapixels === null ? '既定' : `${input.megapixels}MP`)
  parts.push(input.steps > 0 ? `${input.steps}step` : 'おまかせ')
  return parts.join(' · ')
}

export const REVISION_ACTOR_LABEL: Record<StudioRevisionActor, string> = {
  user: 'ユーザー',
  agent: 'エージェント',
}

export const REVISION_ACTOR_CLASS: Record<StudioRevisionActor, string> = {
  user: 'border-sky-800 bg-sky-950 text-sky-300',
  agent: 'border-violet-800 bg-violet-950 text-violet-300',
}

/** 作れるデモ作品（backend/app/studio_demo.py の DEMO_PROJECTS の写し）。 */
export const DEMO_PROJECTS: { code: string; name: string }[] = [
  { code: 'YOAKE-01', name: '夜明けの鋼' },
  { code: 'SAZANAMI-02', name: 'さざなみ食堂' },
  { code: 'ORBIT-03', name: '軌道上の手紙' },
]

/**
 * 実体のファイルを持つ素材か（`path` も `url` も空 = メタデータのみ）。
 *
 * メタデータのみの素材は参照に添付されず、`@名前` は投入時にプロンプトの
 * 説明文へ展開される。
 */
export function assetHasFile(asset: StudioAsset): boolean {
  return Boolean(asset.path || asset.url)
}

/** 拡張子から素材の種別を当てる（アップロード時の既定値）。 */
export function assetKindFromFile(filename: string): StudioAssetKind {
  const extension = filename.toLowerCase().split('.').pop() ?? ''
  if (['mp4', 'mov', 'webm', 'mkv', 'avi', 'm4v'].includes(extension)) return 'video'
  if (['mp3', 'wav', 'flac', 'ogg', 'm4a', 'aac'].includes(extension)) return 'audio'
  return 'image'
}

/** `@名前` として書ける識別名の既定値（ファイル名の主部）。 */
export function assetNameFromFile(filename: string): string {
  const base = filename.replace(/\.[^.]+$/, '')
  return base || filename
}

// --------------------------------------------------------------------------
// Shot の並べ替え
// --------------------------------------------------------------------------

/**
 * `id` の Shot と**同じ場**に居る Shot だけを取り出す（未分類どうしも 1 group）。
 *
 * サーバーが返す並び（話 -> 場 -> カット）をそのまま保つので、返り値の並びが
 * その場の正しい順番になる。
 */
export function shotsInSameScene(shots: StudioShot[], id: string): StudioShot[] {
  const target = shots.find((shot) => shot.id === id)
  if (!target) return []
  const sceneId = target.scene_id ?? null
  return shots.filter((shot) => (shot.scene_id ?? null) === sceneId)
}

/**
 * `id` の Shot を `delta`（-1 = 上へ / +1 = 下へ）だけ動かした、**その場の
 * Shot 全件**の id 配列。
 *
 * `POST /shots/reorder` は「1 つの場（または未分類グループ）の Shot 全件」を
 * 受け取る API なので、作品全体ではなく場の中だけを並べて返す。場をまたぐ移動は
 * `PATCH /shots/{id}` の `scene_id` の仕事。
 *
 * 場の端で動かせないとき・そもそも居ないときは null（呼ぶ側は何もしない）。
 */
export function moveShot(
  shots: StudioShot[],
  id: string,
  delta: number,
): string[] | null {
  return moveId(
    shotsInSameScene(shots, id).map((shot) => shot.id),
    id,
    delta,
  )
}

/**
 * `id` を `delta`（-1 = 上へ / +1 = 下へ）だけ動かした id 配列。
 *
 * 端で動かせないとき・そもそも居ないときは null（呼ぶ側は何もしない）。話・場・
 * Shot の並べ替えはどれも「全件を並べた id 配列を送る」ので共通で使える。
 */
export function moveId(ids: string[], id: string, delta: number): string[] | null {
  const index = ids.indexOf(id)
  if (index < 0) return null
  const next = index + delta
  if (next < 0 || next >= ids.length) return null
  const reordered = [...ids]
  const [moved] = reordered.splice(index, 1)
  reordered.splice(next, 0, moved)
  return reordered
}

// --------------------------------------------------------------------------
// 話（Episode）-> 場（Scene）-> Shot のツリー
// --------------------------------------------------------------------------

/**
 * ツリーの葉。`index` は**その場の中での番号**（0 起点）。
 *
 * 作品全体の通し番号にしないのは、サーバー側の `sort_order` が場ごとに 0 から
 * 振られていて作品全体では一意にならないため。番号だけでは指せないので、UI では
 * 「第◯話 / 場」の見出しと組にして出す。
 */
export interface ShotNode {
  shot: StudioShot
  index: number
}

export interface SceneNode {
  scene: StudioScene
  shots: ShotNode[]
}

export interface EpisodeNode {
  episode: StudioEpisode
  scenes: SceneNode[]
  /** 配下の Shot の総数（畳んだときの見出しに出す）。 */
  shotCount: number
}

export interface ShotTree {
  episodes: EpisodeNode[]
  /** どの場にも属していない Shot（「未分類」グループ）。 */
  unassigned: ShotNode[]
}

/**
 * 話 -> 場 -> Shot に束ねる（サーバーが返す並びをそのまま使う）。
 *
 * `scene_id` が null のものと、消えた場を指しているものは「未分類」に落とす
 * （場を消すと Shot は未分類に戻る、というサーバー側の振る舞いに合わせる）。
 */
export function buildShotTree(detail: {
  episodes: StudioEpisode[]
  scenes: StudioScene[]
  shots: StudioShot[]
}): ShotTree {
  const byScene = new Map<string, ShotNode[]>()
  for (const scene of detail.scenes) byScene.set(scene.id, [])
  const unassigned: ShotNode[] = []
  for (const shot of detail.shots) {
    const bucket = shot.scene_id ? byScene.get(shot.scene_id) : undefined
    // 番号はグループの中で 0 から振り直す（場スコープの `#n`）。
    if (bucket) bucket.push({ shot, index: bucket.length })
    else unassigned.push({ shot, index: unassigned.length })
  }

  const episodes = detail.episodes.map((episode) => {
    const scenes = detail.scenes
      .filter((scene) => scene.episode_id === episode.id)
      .map((scene) => ({ scene, shots: byScene.get(scene.id) ?? [] }))
    return {
      episode,
      scenes,
      shotCount: scenes.reduce((total, node) => total + node.shots.length, 0),
    }
  })
  return { episodes, unassigned }
}

/**
 * 既定で選んでおくカット = 「1 話目の最初のカット」。
 *
 * レールに出る順（話 -> 場 -> Shot）で最初に見つかったものを返し、どの話にも
 * カットが無ければ未分類の先頭に落とす。カットが 1 つも無ければ null。
 */
export function firstShotId(tree: ShotTree): string | null {
  for (const episode of tree.episodes) {
    for (const scene of episode.scenes) {
      if (scene.shots.length > 0) return scene.shots[0].shot.id
    }
  }
  return tree.unassigned[0]?.shot.id ?? null
}

/** ツリーに載っている Shot の総数（話に属さない未分類も数える）。 */
export function countShots(tree: ShotTree): number {
  return (
    tree.unassigned.length +
    tree.episodes.reduce((total, node) => total + node.shotCount, 0)
  )
}

/** 検索の対象にするカットのテキスト（脚本として読める項目だけ）。 */
function shotHaystack(shot: StudioShot): string {
  return [
    shot.title,
    shot.purpose,
    shot.action,
    shot.dialogue,
    shot.soundscape,
    shot.bgm,
    shot.camera,
    shot.prompt,
  ].join('\n')
}

/** そのカットが検索語に当たるか（大文字小文字は無視。空の語は全部に当たる）。 */
export function shotMatches(shot: StudioShot, query: string): boolean {
  const needle = query.trim().toLowerCase()
  if (!needle) return true
  return shotHaystack(shot).toLowerCase().includes(needle)
}

/**
 * 検索語に当たるカットだけを残したツリー。
 *
 * 当たったカットの居場所が分かるよう**話と場の見出しは残す**が、1 件も残らない
 * 場と話は畳んで落とす（空の見出しばかりの一覧にしないため）。番号はグループの
 * 中の位置を指したままにしておく（絞り込みで番号が動くと、レールや制作タブと
 * 食い違って見えるため）。空文字なら素通し。
 */
export function filterShotTree(tree: ShotTree, query: string): ShotTree {
  if (!query.trim()) return tree
  const hit = (node: ShotNode) => shotMatches(node.shot, query)
  const episodes = tree.episodes
    .map((node) => {
      const scenes = node.scenes
        .map((sceneNode) => ({ ...sceneNode, shots: sceneNode.shots.filter(hit) }))
        .filter((sceneNode) => sceneNode.shots.length > 0)
      return {
        ...node,
        scenes,
        shotCount: scenes.reduce((total, scene) => total + scene.shots.length, 0),
      }
    })
    .filter((node) => node.shotCount > 0)
  return { episodes, unassigned: tree.unassigned.filter(hit) }
}

/** 場のセレクトに出す選択肢（`話 / 場` のラベル）。 */
export function sceneOptions(detail: {
  episodes: StudioEpisode[]
  scenes: StudioScene[]
}): { id: string; label: string }[] {
  return detail.episodes.flatMap((episode, episodeIndex) =>
    detail.scenes
      .filter((scene) => scene.episode_id === episode.id)
      .map((scene, sceneIndex) => ({
        id: scene.id,
        label: `${episodeLabel(episode, episodeIndex)} / ${sceneLabel(scene, sceneIndex)}`,
      })),
  )
}

/** 話の見出し（無題なら通し番号で呼ぶ）。 */
export function episodeLabel(episode: StudioEpisode, index: number): string {
  return episode.title || `第 ${index + 1} 話`
}

/** 場の見出し（無題なら通し番号で呼ぶ）。 */
export function sceneLabel(scene: StudioScene, index: number): string {
  return scene.title || `場 ${index + 1}`
}

// --------------------------------------------------------------------------
// Take の束ね方
// --------------------------------------------------------------------------

/** Shot id -> その Shot の Take（サーバーの並び = 古い順のまま）。 */
export function takesByShot(takes: StudioTake[]): Record<string, StudioTake[]> {
  const grouped: Record<string, StudioTake[]> = {}
  for (const take of takes) {
    ;(grouped[take.shot_id] ??= []).push(take)
  }
  return grouped
}

/**
 * その Shot の採用 Take。`selected_take_id` を優先し、指しているものが消えて
 * いれば状態が 'selected' の Take に落とす（無ければ null）。
 */
export function selectedTakeOf(
  shot: StudioShot,
  takes: StudioTake[],
): StudioTake | null {
  const mine = takes.filter((take) => take.shot_id === shot.id)
  const pointed = mine.find((take) => take.id === shot.selected_take_id)
  return pointed ?? mine.find((take) => take.status === 'selected') ?? null
}

/** 概要タブに出すサマリー。 */
export function projectSummary(detail: StudioProjectDetail): {
  shots: number
  assets: number
  takes: number
  selectedTakes: number
  totalSeconds: number
} {
  return {
    shots: detail.shots.length,
    assets: detail.assets.length,
    takes: detail.takes.length,
    selectedTakes: detail.takes.filter((take) => take.status === 'selected').length,
    totalSeconds: detail.shots.reduce(
      (total, shot) => total + (shot.duration_seconds || 0),
      0,
    ),
  }
}

/** まだジョブが動いている Take の job_id（WS の進捗と突き合わせる用）。 */
export function renderingJobIds(takes: StudioTake[]): string[] {
  return takes.filter((take) => take.status === 'rendering').map((take) => take.job_id)
}

/**
 * その Take が古びているか（作ったあとに脚本や素材が変わった）。
 *
 * 判定はサーバー側（`stale`）が持っているが、理由が付いていて `stale` が
 * 落ちている取りこぼしも古びている扱いにする。
 */
export function isStale(take: StudioTake): boolean {
  return Boolean(take.stale) || (take.stale_reasons?.length ?? 0) > 0
}

/** stale バッジのツールチップ（理由を並べる。古びていなければ空）。 */
export function staleTooltip(take: StudioTake): string {
  if (!isStale(take)) return ''
  const reasons = take.stale_reasons ?? []
  return reasons.length > 0
    ? `作り直しをおすすめします: ${reasons.join(' / ')}`
    : '作り直しをおすすめします'
}

// --------------------------------------------------------------------------
// フォームの検証
// --------------------------------------------------------------------------

/** MiniMax H3 の尺の範囲（backend/app/models.py の注記に合わせる）。 */
export const SHOT_DURATION_MIN = 1
export const SHOT_DURATION_MAX = 15

export interface ProjectFormState {
  name: string
  code: string
  synopsis: string
  world_notes: string
  auto_translate: boolean
  /** 引き継ぎを Motion Context で行う（ラテント連続性）。 */
  latent_continuity: boolean
  /** この作品から投入するジョブをすべて NSFW 扱いにする（OFF = 非 NSFW 固定）。 */
  nsfw: boolean
}

export function validateProjectForm(form: {
  name: string
}): Record<string, string> {
  const errors: Record<string, string> = {}
  if (!form.name.trim()) errors.name = '作品名は必須です'
  return errors
}

export interface ShotFormState {
  title: string
  purpose: string
  action: string
  dialogue: string
  soundscape: string
  bgm: string
  camera: string
  duration_seconds: string
  prompt: string
  status: StudioShotStatus
  carry_over_end_frame: boolean
  // --- 空文字 = 未設定（PATCH では null を明示して解除する） -----------------
  /** 所属する場（空 = 未分類）。 */
  scene_id: string
  aspect_ratio: string
  megapixels: string
  seed: string
  /** ワークフローの強制指定（空 = 自動）。 */
  workflow_override: string
}

export function shotFormFromShot(shot: StudioShot): ShotFormState {
  return {
    title: shot.title,
    purpose: shot.purpose,
    action: shot.action,
    dialogue: shot.dialogue,
    soundscape: shot.soundscape,
    bgm: shot.bgm,
    camera: shot.camera,
    duration_seconds: String(shot.duration_seconds),
    prompt: shot.prompt,
    status: shot.status,
    carry_over_end_frame: shot.carry_over_end_frame,
    scene_id: shot.scene_id ?? '',
    aspect_ratio: shot.aspect_ratio ?? '',
    megapixels: shot.megapixels == null ? '' : String(shot.megapixels),
    seed: shot.seed == null ? '' : String(shot.seed),
    workflow_override: shot.workflow_override ?? '',
  }
}

export function validateShotForm(form: ShotFormState): Record<string, string> {
  const errors: Record<string, string> = {}
  const duration = Number(form.duration_seconds)
  if (form.duration_seconds.trim() === '' || Number.isNaN(duration)) {
    errors.duration_seconds = '尺は数値で入れてください'
  } else if (duration < SHOT_DURATION_MIN || duration > SHOT_DURATION_MAX) {
    errors.duration_seconds = `尺は ${SHOT_DURATION_MIN}〜${SHOT_DURATION_MAX} 秒です`
  }
  if (!form.prompt.trim() && !form.action.trim()) {
    errors.prompt = 'プロンプトかアクションのどちらかは書いてください'
  }
  const megapixels = Number(form.megapixels)
  if (form.megapixels.trim() !== '' && (Number.isNaN(megapixels) || megapixels <= 0)) {
    errors.megapixels = '解像度は正の数で入れてください（空欄で既定値）'
  }
  const seed = Number(form.seed)
  if (form.seed.trim() !== '' && !Number.isInteger(seed)) {
    errors.seed = 'シードは整数で入れてください（空欄で毎回ランダム）'
  }
  return errors
}

/**
 * 検証を通ったフォームを PATCH の body にする。
 *
 * 生成設定と場の割り当ては**空欄なら null を明示して外す**（サーバー側は
 * 「null 明示で解除・未送信で据え置き」なので、常に送って画面と揃える）。
 */
export function shotUpdateFromForm(form: ShotFormState): StudioShotUpdate {
  return {
    title: form.title,
    purpose: form.purpose,
    action: form.action,
    dialogue: form.dialogue,
    soundscape: form.soundscape,
    bgm: form.bgm,
    camera: form.camera,
    duration_seconds: Number(form.duration_seconds),
    prompt: form.prompt,
    status: form.status,
    carry_over_end_frame: form.carry_over_end_frame,
    scene_id: form.scene_id || null,
    aspect_ratio: form.aspect_ratio.trim() || null,
    megapixels: form.megapixels.trim() === '' ? null : Number(form.megapixels),
    seed: form.seed.trim() === '' ? null : Number(form.seed),
    workflow_override:
      (form.workflow_override as StudioWorkflowOverride) || null,
  }
}

// --------------------------------------------------------------------------
// テイク 1 回ぶんの生成設定（生成ボタンのダイアログ）
// --------------------------------------------------------------------------
//
// ここで決めた値は **その 1 回の投入にだけ**効き、カットもプロジェクトも
// 書き換えない。既定値はいまの解決結果（カット → プロジェクト）をそのまま
// 映すので、触らずに「生成」を押せば今までどおりの投入になる。

/** サンプリング回数の上限（backend/app/models.py の `MAX_STEPS`）。 */
export const MAX_STEPS = 150

export interface RenderFormState {
  /** 空 = 既定に従う（カット → プロジェクト → ワークフローの既定）。 */
  megapixels: string
  /** 空 = 既定に従う。 */
  aspect_ratio: string
  /** 尺（秒）。空にはできない（既定はカットの尺）。 */
  duration: string
  /** 空 = おまかせ（テンプレートの既定のまま）。 */
  steps: string
  /** シードを固定するか（false = 毎回ランダム）。 */
  fixed_seed: boolean
  seed: string
}

/** プロジェクト設定のうち、このダイアログが既定値に使うぶん。 */
export interface RenderDefaults {
  megapixels: number | null
  aspect_ratio: string | null
  steps: number
}

/**
 * ダイアログの初期値。解像度は **カット個別 → プロジェクト**、尺はカット、
 * ステップ数はプロジェクト、シードはカットに設定があればその固定値。
 */
export function renderFormFromShot(
  shot: StudioShot,
  project: RenderDefaults,
): RenderFormState {
  const megapixels = shot.megapixels ?? project.megapixels
  const aspectRatio = shot.aspect_ratio ?? project.aspect_ratio
  return {
    megapixels: megapixels == null ? '' : String(megapixels),
    aspect_ratio: aspectRatio ?? '',
    duration: String(shot.duration_seconds),
    steps: project.steps > 0 ? String(project.steps) : '',
    fixed_seed: shot.seed != null,
    seed: shot.seed == null ? '' : String(shot.seed),
  }
}

export function validateRenderForm(form: RenderFormState): Record<string, string> {
  const errors: Record<string, string> = {}
  const duration = Number(form.duration)
  if (form.duration.trim() === '' || Number.isNaN(duration)) {
    errors.duration = '尺は数値で入れてください'
  } else if (duration < SHOT_DURATION_MIN || duration > SHOT_DURATION_MAX) {
    errors.duration = `尺は ${SHOT_DURATION_MIN}〜${SHOT_DURATION_MAX} 秒です`
  }
  const megapixels = Number(form.megapixels)
  if (form.megapixels.trim() !== '' && (Number.isNaN(megapixels) || megapixels <= 0)) {
    errors.megapixels = '解像度は正の数で入れてください（空欄で既定値）'
  }
  const steps = Number(form.steps)
  if (form.steps.trim() !== '') {
    if (!Number.isInteger(steps) || steps < 0 || steps > MAX_STEPS) {
      errors.steps = `ステップ数は 0〜${MAX_STEPS} の整数です（空欄でおまかせ）`
    }
  }
  const seed = Number(form.seed)
  if (form.fixed_seed && (form.seed.trim() === '' || !Number.isInteger(seed))) {
    errors.seed = 'シードは整数で入れてください'
  }
  return errors
}

/**
 * 検証を通ったフォームを render のボディにする。
 *
 * 空欄の項目は**送らない**（サーバー側の従来どおりの解決に落とす）。ただし
 * ステップ数だけは空欄でも `0` を送る: 0 は「テンプレートの既定のまま」を
 * **明示する**値で、プロジェクトの設定より優先されるため。
 */
export function renderRequestFromForm(
  form: RenderFormState,
): StudioRenderRequest {
  const body: StudioRenderRequest = {
    duration: Number(form.duration),
    steps: form.steps.trim() === '' ? 0 : Number(form.steps),
  }
  if (form.megapixels.trim() !== '') body.megapixels = Number(form.megapixels)
  if (form.aspect_ratio.trim() !== '') body.aspect_ratio = form.aspect_ratio.trim()
  if (form.fixed_seed && form.seed.trim() !== '') body.seed = Number(form.seed)
  return body
}

// --------------------------------------------------------------------------
// `@素材名` メンション
// --------------------------------------------------------------------------

/** 切り出したプロンプトの一片。`asset` があればメンション。 */
export interface PromptSegment {
  text: string
  /** 解決できたメンションの素材（素の文字列なら null）。 */
  asset: StudioAsset | null
  /** `@` で始まるが登録済みの素材に当たらない（生成時に 400 になる）。 */
  unresolved: boolean
}

const WORD_CHAR = /[A-Za-z0-9_]/

/**
 * プロンプトを「素の文字列」と「`@素材名` メンション」に切り分ける。
 *
 * 突き合わせ方は backend/app/studio.py の `resolve_mentions` と同じで、長い名前
 * から見て前方一致の取り違え（`@Aki` が `@Akira` を食う）を避け、`@{名前}` の
 * 波括弧つきも受ける。解決できないメンションは `unresolved` で返して、UI で
 * 赤く出せるようにする（生成前に気づける）。
 */
export function splitMentions(
  prompt: string,
  assets: StudioAsset[],
): PromptSegment[] {
  const byName = new Map(assets.map((asset) => [asset.name, asset]))
  const names = [...byName.keys()].sort((a, b) => b.length - a.length)
  const segments: PromptSegment[] = []
  const pushText = (text: string) => {
    if (!text) return
    const last = segments[segments.length - 1]
    if (last && last.asset === null && !last.unresolved) last.text += text
    else segments.push({ text, asset: null, unresolved: false })
  }

  let cursor = 0
  for (;;) {
    const at = prompt.indexOf('@', cursor)
    if (at < 0) {
      pushText(prompt.slice(cursor))
      break
    }
    pushText(prompt.slice(cursor, at))
    const found = matchMention(prompt, at, names)
    if (found) {
      const asset = byName.get(found.name) ?? null
      segments.push({ text: prompt.slice(at, found.end), asset, unresolved: false })
      cursor = found.end
      continue
    }
    const end = unresolvedEnd(prompt, at)
    segments.push({ text: prompt.slice(at, end), asset: null, unresolved: true })
    cursor = end
  }
  return segments
}

function matchMention(
  prompt: string,
  at: number,
  names: string[],
): { name: string; end: number } | null {
  if (prompt.startsWith('@{', at)) {
    const close = prompt.indexOf('}', at + 2)
    if (close < 0) return null
    const name = prompt.slice(at + 2, close)
    return names.includes(name) ? { name, end: close + 1 } : null
  }
  for (const name of names) {
    const end = at + 1 + name.length
    if (!prompt.startsWith(name, at + 1)) continue
    const following = prompt.slice(end, end + 1)
    // 名前の途中で切れている（`@Aki` が `@Akira` を食わない）
    if (following && WORD_CHAR.test(name[name.length - 1]) && WORD_CHAR.test(following)) {
      continue
    }
    return { name, end }
  }
  return null
}

/** 解決できなかったメンションの見出し（区切り文字の手前まで）。 */
function unresolvedEnd(prompt: string, at: number): number {
  const match = /^[^\s@、。,.!?！？]+/.exec(prompt.slice(at + 1))
  return at + 1 + (match ? match[0].length : 0)
}

/** プロンプト中の未解決メンション（生成前の警告に使う）。 */
export function unresolvedMentions(prompt: string, assets: StudioAsset[]): string[] {
  return splitMentions(prompt, assets)
    .filter((segment) => segment.unresolved)
    .map((segment) => segment.text)
}
