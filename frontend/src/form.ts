import type {
  AudioJobCreate,
  ComfyTarget,
  ElementsLimits,
  ImageFamily,
  JobMode,
  KlingElement,
  LibraryKind,
  MultiShot,
  MultiShotLimits,
  Lora,
  LoraRef,
  LoraTarget,
  ModelSlot,
  Options,
  PromptTemplate,
  ReferenceInput,
  WorkflowOption,
  WorkflowSelect,
} from './types'

/** Fallback while /api/options has not answered yet (backend default). */
export const DEFAULT_VIDEO_WORKFLOW = 'ltx2_3_id_lora'
export const DEFAULT_IMAGE_WORKFLOW = 'krea2_turbo'
export const DEFAULT_AUDIO_WORKFLOW = 'ace_step1_5_xl_sft'

/**
 * ComfyUI の接続先プロファイル（SPEC §5）。設定画面のサブセクションと生成
 * フォーム上部のプルダウンで共通に使う並び順・表示名。
 */
export const COMFY_TARGETS: ComfyTarget[] = ['comfy_cloud', 'runpod', 'local']

export const COMFY_TARGET_LABELS: Record<ComfyTarget, string> = {
  comfy_cloud: 'ComfyCloud',
  runpod: 'RunPod',
  local: 'ローカル',
}

/** バックエンド workflows.AUDIO_CATEGORIES（/api/options が来るまでの控え）。 */
export const AUDIO_CATEGORIES = ['Music', 'Instrument', 'SFX', 'One-shot']

export const CATEGORY_LABELS: Record<string, string> = {
  Music: '音楽',
  Instrument: '楽器 / ステム',
  SFX: '効果音・環境音',
  'One-shot': 'ワンショット',
}

/** よく使う言語だけ読みやすく（残りは ISO コードのまま並ぶ）。 */
export const LANGUAGE_LABELS: Record<string, string> = {
  ja: '日本語 (ja)',
  en: '英語 (en)',
  zh: '中国語 (zh)',
  ko: '韓国語 (ko)',
  unknown: '自動 / インスト (unknown)',
}

/** Backend workflows.DEFAULT_FAMILY — the family a LoRA gets when unset. */
export const DEFAULT_FAMILY = 'krea2'

/** 日本語ラベル（mirrors workflows.FAMILY_LABELS）。 */
export const FAMILY_LABELS: Record<string, string> = {
  krea2: 'Krea 2',
  anima: 'Anima',
  'z-image': 'Z-Image',
  'qwen-image': 'Qwen-Image Edit',
  'ltx2.3': 'LTX 2.3',
}

/** Families an image LoRA can be registered for (mirrors image_families()). */
export const IMAGE_FAMILIES: ImageFamily[] = [
  'krea2',
  'anima',
  'z-image',
  'qwen-image',
]

/** Backend default (models.DEFAULT_NEGATIVE_PROMPT). */
export const DEFAULT_NEGATIVE_PROMPT =
  'pc game, console game, video game, cartoon, childish, ugly'

/** docs/prompt-samples.md — the model author's own negative prompt. */
export const AUTHOR_NEGATIVE_PROMPT =
  'blurry, oversaturated, pixelated, low resolution, grainy, distorted, noise, ' +
  'compression artifacts, jpeg artifacts, glitches, watermark, text, logo, ' +
  'signature, copyright, subtitles, distorted sound, saturated sound, loud'

export const NEGATIVE_PRESET_LABELS: Record<string, string> = {
  template: 'ワークフロー既定',
  current: '現行値（既定）',
  author: 'モデル作者版',
  custom: 'カスタム',
}

export interface SelectedLora extends LoraRef {
  id: number
  display_name: string
}

export interface FormState {
  mode: JobMode
  /** id of the selected video workflow template. */
  videoWorkflow: string
  /** id of the selected image workflow template. */
  imageWorkflow: string
  imagePrompt: string
  videoPrompt: string
  negativePreset: string
  negativePrompt: string
  aspectRatio: string
  megapixels: number
  /** LoRAs of the image stage — registered with target 'image'. */
  loras: SelectedLora[]
  triggerText: string
  triggerDirty: boolean
  /** LoRAs of the video (LTX 2.3) stage — registered with target 'video'. */
  videoLoras: SelectedLora[]
  videoTriggerText: string
  videoTriggerDirty: boolean
  audioPath: string
  sourceImage: string
  endImage: string
  referenceVideo: string
  /**
   * マルチモーダル参照（SPEC §3.1）。選んだ順がそのまま外部 API に渡る配列の
   * 順序になる。開始フレームとは排他なので、両方に値があるとフォームが弾く。
   */
  referenceImages: string[]
  referenceVideos: string[]
  referenceAudios: string[]
  /**
   * ショット割り（SPEC §3.1）。1 行でも入っていれば `videoPrompt` の代わりに
   * これが本文になり、トップレベルのプロンプトは送られない。
   */
  multiShots: MultiShot[]
  /** Elements（`@要素名` で呼ぶ参照画像の束、SPEC §3.1）。 */
  klingElements: KlingElement[]
  duration: number
  fps: number
  seedLocked: boolean
  seed: number
  promptTemplate: PromptTemplate
  /**
   * ワークフローが宣言する選択式フィールドの値（論理名 -> 選んだ文字列）。
   * 空文字は「未指定」= 既定値 / 自動。ワークフローを行き来しても選択が消えない
   * よう、全ワークフローぶんを 1 つのマップに持つ（送信時に絞る）。
   */
  selects: Record<string, string>
  /**
   * 実行時に切り替えたモデル（キーは /api/options の model_slots の `key`）。
   * 全ワークフローぶんを 1 つに持ち、送信時に走らせるワークフローのぶんだけ絞る
   * （モードを行き来しても選択が消えない）。
   */
  modelOverrides: Record<string, string>

  // --- mode 'audio' -------------------------------------------------------
  // 音声はモードの一つだが、画像・動画とは連結されない独立ジョブ。モードを行き来
  // しても入力が消えないよう、動画側の duration とは別のフィールドに持つ。
  /** id of the selected audio workflow template. */
  audioWorkflow: string
  audioPrompt: string
  /** ACE-Step / Suno の歌詞。空ならインストゥルメンタル。 */
  lyrics: string
  /** Suno: 曲に入れたくない要素（英語のカンマ区切り）。 */
  negativeTags: string
  /** 音声の長さ（秒）。ワークフローごとに上下限が違う。 */
  audioDuration: number
  bpm: number
  keyscale: string
  language: string
  /** Stable Audio: Music / Instrument / SFX / One-shot。 */
  audioCategory: string
  /** Stable Audio: 内蔵 LLM でプロンプトを展開してから流すか。 */
  reprompt: boolean
}

export const initialForm: FormState = {
  mode: 'full',
  videoWorkflow: DEFAULT_VIDEO_WORKFLOW,
  imageWorkflow: DEFAULT_IMAGE_WORKFLOW,
  imagePrompt: '',
  videoPrompt: '',
  negativePreset: 'current',
  negativePrompt: DEFAULT_NEGATIVE_PROMPT,
  aspectRatio: '4:3 (Standard)',
  megapixels: 1,
  loras: [],
  triggerText: '',
  triggerDirty: false,
  videoLoras: [],
  videoTriggerText: '',
  videoTriggerDirty: false,
  audioPath: '',
  sourceImage: '',
  endImage: '',
  referenceVideo: '',
  referenceImages: [],
  referenceVideos: [],
  referenceAudios: [],
  multiShots: [],
  klingElements: [],
  duration: 10,
  fps: 25,
  seedLocked: false,
  seed: 0,
  promptTemplate: 'natural',
  selects: {},
  modelOverrides: {},
  audioWorkflow: DEFAULT_AUDIO_WORKFLOW,
  audioPrompt: '',
  lyrics: '',
  negativeTags: '',
  audioDuration: 120,
  bpm: 120,
  keyscale: 'C major',
  language: 'ja',
  audioCategory: 'Music',
  reprompt: false,
}

export function toSelected(lora: Lora): SelectedLora {
  return {
    id: lora.id,
    display_name: lora.display_name,
    lora_name: lora.lora_name,
    trigger_word: lora.trigger_word,
    strength: lora.default_strength ?? 1,
  }
}

export function joinTriggers(loras: SelectedLora[]): string {
  return loras
    .map((lora) => lora.trigger_word.trim())
    .filter(Boolean)
    .join(', ')
}

/** Registered LoRAs usable by one stage (SPEC §3.4).
 *
 * Image LoRAs are additionally scoped by model family: one trained for Krea 2
 * cannot be used with Anima or Z-Image (the backend rejects such a job), so the
 * picker only offers the family of the selected image workflow. Video LoRAs have
 * no family — LTX 2.3 is the only video model.
 */
export function lorasForTarget(
  loras: Lora[],
  target: LoraTarget,
  family?: string | null,
): Lora[] {
  return loras.filter((lora) => {
    if ((lora.target ?? 'image') !== target) return false
    if (target !== 'image' || !family) return true
    return (lora.family ?? DEFAULT_FAMILY) === family
  })
}

// ----------------------------------------------- 選択式フィールド（SPEC §3.1）
// 自由記述ではなく決まった選択肢で挙動が決まるワークフロー（wan_dancer の踊りの
// 種類・動きの大きさ・尺）向け。フォームは workflow.selects をそのまま select と
// して描き、送信時はそのワークフローが宣言している名前だけを送る。

/** 選択中のワークフローが宣言している選択式フィールド。 */
export function workflowSelects(
  workflow?: WorkflowOption | null,
): WorkflowSelect[] {
  return workflow?.selects ?? []
}

/**
 * フォームの選択 → ジョブの `selects`。
 *
 * 未指定（空文字）は送らない: `auto` の項目はバックエンドが入力から決め、それ以外
 * はワークフローの既定値になる。選択肢から外れた値も送らない（ワークフローを
 * 切り替えた直後の持ち越しで 422 にしない）。
 *
 * `selects` はステージをまたいで 1 つの辞書なので、**そのジョブで実際に走る
 * ワークフロー**をすべて渡す（`full` なら画像と動画の両方。gpt-image-2 の
 * `size` / `quality` は画像ステージ側の宣言、SPEC §5.4）。走らないステージの
 * ワークフローを渡すと、持ち越した値を送って 422 になる。
 */
export function jobSelects(
  form: FormState,
  ...workflows: (WorkflowOption | null | undefined)[]
): Record<string, string> {
  const picked: Record<string, string> = {}
  for (const workflow of workflows) {
    for (const select of workflowSelects(workflow)) {
      const value = form.selects[select.name]
      if (value && select.choices.includes(value)) picked[select.name] = value
    }
  }
  return picked
}

// ------------------------------------------------- 実行時のモデル切り替え（§3.3）
// 設定ページで候補を 2 件以上登録したスロットだけが /api/options の `model_slots`
// に出る。フォームはそのうち「選択中のワークフローのもの」だけをセレクトで見せ、
// 既定値と違う選択だけをジョブの `model_overrides` として送る。

/** そのモードで実際に走らせるワークフロー ID（モデル選択のスコープ、SPEC §2）。 */
export function jobWorkflowIds(form: FormState): string[] {
  if (form.mode === 'audio') return [form.audioWorkflow]
  const ids: string[] = []
  if (form.mode !== 'i2v') ids.push(form.imageWorkflow)
  if (form.mode !== 'image_only') ids.push(form.videoWorkflow)
  return ids
}

/** 指定ワークフローに属する、実行時に選べるモデルスロット。 */
export function modelSlotsForJob(
  slots: ModelSlot[] | undefined,
  workflowIds: string[],
): ModelSlot[] {
  return (slots ?? []).filter((slot) => workflowIds.includes(slot.workflow_id))
}

/** フォームの選択 → ジョブの `model_overrides`（既定値のままのものは送らない）。 */
export function jobModelOverrides(
  form: FormState,
  slots: ModelSlot[] | undefined,
  workflowIds: string[],
): Record<string, string> {
  const picked: Record<string, string> = {}
  for (const slot of modelSlotsForJob(slots, workflowIds)) {
    const value = form.modelOverrides[slot.key]
    if (value && value !== slot.default && slot.choices.includes(value)) {
      picked[slot.key] = value
    }
  }
  return picked
}

/** True when the image workflow edits a given picture instead of generating one. */
export function imageWorkflowNeedsSource(
  workflow?: WorkflowOption | null,
): boolean {
  return (workflow?.requires ?? []).includes('image' as never)
}

// --------------------------------------------- マルチモーダル参照（SPEC §3.1）
// 1 つの欄が**複数ファイル**を持つ参照入力（Seedance 2 系の参照画像・参照動画・
// 参照音声）。どの欄をいくつまで出すかはワークフローの `multi_inputs` が決め、
// **開始フレーム（`source_image` / `end_image`）とは排他**。

/** 参照入力 1 つ分の見せ方（論理名 = ジョブのフィールド名）。 */
export interface ReferenceField {
  /** ジョブのフィールド名（= バックエンドの論理名）。 */
  name: ReferenceInput
  /** FormState 側の持ち場。 */
  field: 'referenceImages' | 'referenceVideos' | 'referenceAudios'
  /** 選択モーダル・アップロードの種別。 */
  kind: LibraryKind
  label: string
  hint: string
  /** そのワークフローが受け取れる件数。 */
  limit: number
}

/** 宣言の順序（フォームに出す順番でもある）。 */
const REFERENCE_FIELDS: Omit<ReferenceField, 'limit'>[] = [
  {
    name: 'reference_images',
    field: 'referenceImages',
    kind: 'image',
    label: '参照画像',
    hint: '見た目の一貫性のよりどころ（同じ顔・同じ衣装・同じ小物）。',
  },
  {
    name: 'reference_videos',
    field: 'referenceVideos',
    kind: 'video',
    label: '参照動画',
    hint: '動きのお手本（リズム・カメラの振る舞い）。1 本 2〜15 秒、合計 15 秒まで。',
  },
  {
    name: 'reference_audios',
    field: 'referenceAudios',
    kind: 'audio',
    label: '参照音声',
    hint: 'ムード・曲調のよりどころ。1 本 2〜15 秒、合計 15 秒まで。',
  },
]

/** 選択中のワークフローが受け取る参照入力（宣言が無ければ空）。 */
export function referenceFields(
  workflow?: WorkflowOption | null,
): ReferenceField[] {
  const declared = workflow?.multi_inputs ?? {}
  return REFERENCE_FIELDS.flatMap((item) => {
    const limit = declared[item.name]
    return limit ? [{ ...item, limit }] : []
  })
}

/** いまフォームに入っている参照素材の合計件数。 */
export function referenceCount(form: FormState): number {
  return (
    form.referenceImages.length +
    form.referenceVideos.length +
    form.referenceAudios.length
  )
}

/** 参照素材の出し入れ（すでに入っていれば外す。並び順 = 選んだ順）。 */
export function toggleReference(current: string[], url: string): string[] {
  return current.includes(url)
    ? current.filter((item) => item !== url)
    : [...current, url]
}

// ------------------------------- ショット割り / Elements（SPEC §3.1、Kling 3.0）
// 平坦な値ではない**構造化パラメータ**。どちらもワークフローの宣言
// （`multi_shot` / `elements`）がある場合だけフォームに欄が出る。

/** 選択中のワークフローのショット割りの上限（宣言が無ければ null）。 */
export function multiShotLimits(
  workflow?: WorkflowOption | null,
): MultiShotLimits | null {
  return workflow?.multi_shot ?? null
}

/** 選択中のワークフローの Elements の上限（宣言が無ければ null）。 */
export function elementsLimits(
  workflow?: WorkflowOption | null,
): ElementsLimits | null {
  return workflow?.elements ?? null
}

/**
 * プロンプト中の `@要素名`（mirrors models.ELEMENT_REFERENCE）。
 * Python の `\w` に合わせて、英数字・アンダースコア・ハイフンのほか日本語も拾う。
 */
const ELEMENT_REFERENCE = /@([\p{L}\p{M}\p{N}_-]+)/gu

/** 本文が呼んでいる `@要素名`（出てきた順、重複そのまま）。 */
export function elementReferences(text: string): string[] {
  return [...text.matchAll(ELEMENT_REFERENCE)].map((match) => match[1])
}

/**
 * API がその本文を何文字と数えるか（SPEC §3.1）。
 *
 * Elements を持つモデルでは **`@要素名` 1 回が `referenceChars` 文字**として
 * 上限を消費するので、見た目の長さのままでは 500 文字の判定が合わない。
 */
export function promptChars(text: string, referenceChars = 0): number {
  if (!text) return 0
  if (referenceChars <= 0) return [...text].length
  let counted = [...text].length
  for (const match of text.matchAll(ELEMENT_REFERENCE)) {
    counted += referenceChars - [...match[0]].length
  }
  return counted
}

/** 新しいショット 1 行（尺は宣言の範囲に収まる無難な既定）。 */
export function newShot(limits: MultiShotLimits): MultiShot {
  return {
    prompt: '',
    duration: Math.min(Math.max(5, limits.min_duration), limits.max_duration),
  }
}

/** 新しい要素 1 つ。 */
export function newElement(): KlingElement {
  return { name: '', description: '', images: [] }
}

// -------------------------------------------------- リファレンスシート（§7.2）

/** リファレンスシート 1 枚を参照入力に取る動画ワークフロー（IC-LoRA）。 */
export const REFERENCE_SHEET_WORKFLOW = 'ltx2_3_ic_lora_image'

/** シートの長辺（px）。バックエンドの既定 1280x720 と同じ大きさに揃える。 */
export const SHEET_LONG_EDGE = 1280

/** シートに載せられる枚数（バックエンドの `sheets.MAX_ITEMS` に合わせる）。 */
export const SHEET_MIN_ITEMS = 2
export const SHEET_MAX_ITEMS = 8

/** 画像欄がリファレンスシートか（この動画ワークフローのときだけ合成できる）。 */
export function needsReferenceSheet(
  workflow?: WorkflowOption | null,
): boolean {
  return workflow?.id === REFERENCE_SHEET_WORKFLOW
}

/**
 * アスペクト比プリセットから合成するシートの大きさを決める。
 *
 * シートは出力動画と同じ縦横比が望ましい（ワークフローの `ResizeAndPadImage` が
 * 黒でパディングするので、比が合っていれば余白が出ない）。プリセットは
 * `"16:9 (Widescreen)"` の形なので先頭の `W:H` だけ読み、長辺を
 * :data:`SHEET_LONG_EDGE` にして 8 の倍数に丸める。読めない値のときは既定の
 * 1280x720（メガピクセルは見ない: シートは参照用で、動画の解像度とは別物）。
 */
export function sheetSize(aspectRatio: string): { width: number; height: number } {
  const match = /^\s*(\d+)\s*:\s*(\d+)/.exec(aspectRatio || '')
  const ratioWidth = Number(match?.[1] ?? 16)
  const ratioHeight = Number(match?.[2] ?? 9)
  if (!ratioWidth || !ratioHeight) return { width: SHEET_LONG_EDGE, height: 720 }
  const scale = SHEET_LONG_EDGE / Math.max(ratioWidth, ratioHeight)
  const round8 = (value: number) => Math.max(8, Math.round((value * scale) / 8) * 8)
  return { width: round8(ratioWidth), height: round8(ratioHeight) }
}

// --------------------------------------------------------------- audio mode
// 音声はモードタブの一つだが、走るワークフローは 1 本きりで画像・動画とは連結
// しない。どのつまみを出すかはワークフローのマニフェスト（`supports`）が決める。

/** ワークフローが露出しているつまみか（バックエンドの inject キーと同じ名前）。 */
export function audioSupports(
  workflow: WorkflowOption | null | undefined,
  name: string,
): boolean {
  return (workflow?.supports ?? []).includes(name)
}

/** 秒数の許容範囲（/api/options が無いあいだは制限なし扱い）。 */
export function durationRange(
  workflow: WorkflowOption | null | undefined,
): { min: number; max: number } | null {
  if (!workflow || workflow.max_duration <= 0) return null
  return { min: workflow.min_duration, max: workflow.max_duration }
}

/** 音声ワークフローを切り替えたときに追随させる値（秒数を範囲内へ寄せる）。 */
export function clampToWorkflow(
  form: FormState,
  workflow: WorkflowOption | null | undefined,
): Partial<FormState> {
  const range = durationRange(workflow)
  if (!range) return {}
  if (form.audioDuration >= range.min && form.audioDuration <= range.max) return {}
  return { audioDuration: workflow?.default_duration || range.min }
}

/**
 * フォーム → POST /api/jobs の body（`mode: 'audio'`）。
 *
 * そのワークフローが使わないフィールドは送らない（バックエンドは既定値で埋める
 * ので害はないが、ジョブの params に嘘の設定が残るのを避ける）。画像・動画側の
 * フィールドは一切入れない（入れるとバックエンドに拒否される）。
 */
export function audioJobPayload(
  form: FormState,
  workflow?: WorkflowOption | null,
  modelSlots?: ModelSlot[],
): AudioJobCreate {
  const payload: AudioJobCreate = {
    mode: 'audio',
    audio_workflow: form.audioWorkflow,
    audio_prompt: form.audioPrompt,
    duration: form.audioDuration,
    seed: form.seedLocked ? form.seed : null,
  }
  const models = jobModelOverrides(form, modelSlots, [form.audioWorkflow])
  if (Object.keys(models).length > 0) payload.model_overrides = models
  // 選択式フィールド（Suno のモデル・ボーカル性別。§3.1）
  const selects = jobSelects(form, workflow)
  if (Object.keys(selects).length > 0) payload.selects = selects
  if (audioSupports(workflow, 'lyrics')) payload.lyrics = form.lyrics
  if (audioSupports(workflow, 'negative_tags')) {
    payload.negative_tags = form.negativeTags
  }
  if (audioSupports(workflow, 'bpm')) payload.bpm = form.bpm
  if (audioSupports(workflow, 'keyscale')) payload.keyscale = form.keyscale
  if (audioSupports(workflow, 'language')) payload.language = form.language
  if (audioSupports(workflow, 'audio_category')) {
    payload.audio_category = form.audioCategory
  }
  if (audioSupports(workflow, 'reprompt')) payload.reprompt = form.reprompt
  return payload
}

/** 音声ジョブか（履歴・結果表示の出し分け用）。 */
export function isAudioJob(job: { mode: string }): boolean {
  return job.mode === 'audio'
}

// ------------------------------------------------- 過去ジョブからのフォーム復元
// ジョブの `params`（POST /api/jobs の内容そのもの）を FormState へ戻す。「再実行」
// がサーバー側で同じ params を投げ直すのに対し、こちらは*フォームに書き戻して*
// 手直ししてから流し直すための入口。params に無いキーには触らないので、呼び出し側
// は `{ ...initialForm, ...patch }` として当てれば「そのジョブそのまま」になる。

/** :func:`formStateFromParams` の結果（当てる差分と、引き当てられなかった LoRA）。 */
export interface RestoredForm {
  patch: Partial<FormState>
  /** 登録簿に無くなっていて復元できなかった LoRA のファイル名。 */
  missingLoras: string[]
}

const JOB_MODES: JobMode[] = ['full', 'i2v', 'image_only', 'audio']

function asString(value: unknown): string | undefined {
  return typeof value === 'string' ? value : undefined
}

function asNumber(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isFinite(value) ? value : undefined
}

function asBoolean(value: unknown): boolean | undefined {
  return typeof value === 'boolean' ? value : undefined
}

/** 文字列だけの配列を取り出す（それ以外が来たら復元しない）。 */
function asStringList(value: unknown): string[] | undefined {
  if (!Array.isArray(value)) return
  return value.filter((item): item is string => typeof item === 'string')
}

/** 過去ジョブの params からショット割りを復元する（形が違えば復元しない）。 */
function asMultiShots(value: unknown): MultiShot[] | undefined {
  if (!Array.isArray(value)) return
  return value.flatMap((item) => {
    if (typeof item !== 'object' || item === null) return []
    const shot = item as Record<string, unknown>
    if (typeof shot.prompt !== 'string') return []
    const duration = Number(shot.duration)
    return [
      {
        prompt: shot.prompt,
        duration: Number.isFinite(duration) ? duration : 5,
      },
    ]
  })
}

/** 過去ジョブの params から Elements を復元する。 */
function asElements(value: unknown): KlingElement[] | undefined {
  if (!Array.isArray(value)) return
  return value.flatMap((item) => {
    if (typeof item !== 'object' || item === null) return []
    const element = item as Record<string, unknown>
    if (typeof element.name !== 'string') return []
    return [
      {
        name: element.name,
        description:
          typeof element.description === 'string' ? element.description : '',
        images: asStringList(element.images) ?? [],
      },
    ]
  })
}

/** `{名前: 文字列}` だけを取り出す（数値や null が混ざった行は落とす）。 */
function asStringMap(value: unknown): Record<string, string> | undefined {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return
  const picked: Record<string, string> = {}
  for (const [key, item] of Object.entries(value as Record<string, unknown>)) {
    if (typeof item === 'string') picked[key] = item
  }
  return picked
}

/**
 * 選択肢に残っている ID だけを通す。
 *
 * 消えたワークフロー / アスペクト比をそのまま入れるとセレクトが空表示になり、送信
 * しても 422 になるだけなので、その場合は復元せず初期値のままにする（選択肢が
 * まだ来ていない = 空配列なら、判断できないので素通しする）。
 */
function knownId(id: string | undefined, choices: string[] | undefined): string | undefined {
  if (id === undefined) return
  if (!choices || choices.length === 0) return id
  return choices.includes(id) ? id : undefined
}

/**
 * params の LoRA 参照（`{lora_name, trigger_word, strength}`）を登録簿と突き合わせて
 * :type:`SelectedLora` に戻す。強度とトリガー語はジョブに記録された値を優先する
 * （登録簿の既定値をあとから変えていても、そのジョブの再現を壊さない）。
 */
function restoreLoras(
  raw: unknown,
  registry: Lora[],
  target: LoraTarget,
  missing: string[],
): SelectedLora[] | undefined {
  if (!Array.isArray(raw)) return
  const restored: SelectedLora[] = []
  for (const item of raw) {
    if (typeof item !== 'object' || item === null) continue
    const ref = item as Record<string, unknown>
    const name = asString(ref.lora_name)
    if (!name) continue
    const known =
      registry.find(
        (lora) => lora.lora_name === name && (lora.target ?? 'image') === target,
      ) ?? registry.find((lora) => lora.lora_name === name)
    if (!known) {
      missing.push(name)
      continue
    }
    restored.push({
      id: known.id,
      display_name: known.display_name,
      lora_name: name,
      trigger_word: asString(ref.trigger_word) ?? known.trigger_word,
      strength: asNumber(ref.strength) ?? known.default_strength ?? 1,
    })
  }
  return restored
}

/** 復元したネガティブがどのプリセットと一致するか（どれとも違えば 'custom'）。 */
function negativePresetOf(prompt: string, options: Options | null): string {
  const presets = options?.negative_presets ?? {
    current: DEFAULT_NEGATIVE_PROMPT,
    author: AUTHOR_NEGATIVE_PROMPT,
  }
  const hit = Object.entries(presets).find(([, value]) => value === prompt)
  return hit ? hit[0] : 'custom'
}

/**
 * ジョブの `params` → フォームに当てる差分。
 *
 * `params` に無いキーは差分にも入らない（呼び出し側で `initialForm` に重ねる前提）。
 * 復元できなかった LoRA は差分から外し、名前だけ `missingLoras` で返す。
 */
export function formStateFromParams(
  params: Record<string, unknown>,
  options: Options | null,
): RestoredForm {
  const changes: Partial<FormState> = {}
  const missingLoras: string[] = []

  const mode = JOB_MODES.find((item) => item === params.mode)
  if (mode) changes.mode = mode
  const effectiveMode = mode ?? initialForm.mode

  // --- ワークフロー / 解像度 ------------------------------------------------
  const ids = (workflows?: WorkflowOption[]) => workflows?.map((item) => item.id)
  const imageWorkflow = knownId(
    asString(params.image_workflow),
    ids(options?.image_workflows),
  )
  if (imageWorkflow) changes.imageWorkflow = imageWorkflow
  const videoWorkflow = knownId(
    asString(params.video_workflow),
    ids(options?.video_workflows),
  )
  if (videoWorkflow) changes.videoWorkflow = videoWorkflow
  const audioWorkflow = knownId(
    asString(params.audio_workflow),
    ids(options?.audio_workflows),
  )
  if (audioWorkflow) changes.audioWorkflow = audioWorkflow
  const aspectRatio = knownId(asString(params.aspect_ratio), options?.aspect_ratios)
  if (aspectRatio) changes.aspectRatio = aspectRatio
  const megapixels = asNumber(params.megapixels)
  if (megapixels !== undefined) changes.megapixels = megapixels

  // --- プロンプト ----------------------------------------------------------
  const imagePrompt = asString(params.image_prompt)
  if (imagePrompt !== undefined) changes.imagePrompt = imagePrompt
  const videoPrompt = asString(params.video_prompt)
  if (videoPrompt !== undefined) changes.videoPrompt = videoPrompt
  const negativePrompt = asString(params.negative_prompt)
  if (negativePrompt !== undefined) {
    changes.negativePrompt = negativePrompt
    changes.negativePreset = negativePresetOf(negativePrompt, options)
  }

  // --- LoRA チェーンとトリガー語 -------------------------------------------
  // トリガー語は LoRA から自動生成されるが、ジョブに残っている文面が自動生成と
  // 違うなら手で直したもの。dirty を立てて、あとで LoRA を触っても消させない。
  const registry = options?.loras ?? []
  const loras = restoreLoras(params.loras, registry, 'image', missingLoras)
  if (loras) changes.loras = loras
  const triggerText = asString(params.trigger_text)
  if (triggerText !== undefined) {
    changes.triggerText = triggerText
    changes.triggerDirty = triggerText !== joinTriggers(loras ?? initialForm.loras)
  }
  const videoLoras = restoreLoras(params.video_loras, registry, 'video', missingLoras)
  if (videoLoras) changes.videoLoras = videoLoras
  const videoTriggerText = asString(params.video_trigger_text)
  if (videoTriggerText !== undefined) {
    changes.videoTriggerText = videoTriggerText
    changes.videoTriggerDirty =
      videoTriggerText !== joinTriggers(videoLoras ?? initialForm.videoLoras)
  }

  // --- 入力素材 ------------------------------------------------------------
  // 音声ジョブの params には画像・動画側の入力は入らないが、入っていても mode が
  // 使わないだけなので、あるものはそのまま戻す。
  const audioPath = asString(params.audio_path)
  if (audioPath !== undefined) changes.audioPath = audioPath
  const sourceImage = asString(params.source_image)
  if (sourceImage !== undefined) changes.sourceImage = sourceImage
  const endImage = asString(params.end_image)
  if (endImage !== undefined) changes.endImage = endImage
  const referenceVideo = asString(params.reference_video)
  if (referenceVideo !== undefined) changes.referenceVideo = referenceVideo
  // マルチモーダル参照（複数ファイル、SPEC §3.1）。古いジョブの params には無い。
  for (const item of REFERENCE_FIELDS) {
    const paths = asStringList(params[item.name])
    if (paths) changes[item.field] = paths
  }
  // ショット割り / Elements（構造化パラメータ、SPEC §3.1）。古いジョブには無い。
  const shots = asMultiShots(params.multi_shots)
  if (shots) changes.multiShots = shots
  const elements = asElements(params.kling_elements)
  if (elements) changes.klingElements = elements

  // --- 尺 ------------------------------------------------------------------
  // params の `duration` は 1 つきりだが、フォームは動画と音声で別のつまみを持つ。
  const duration = asNumber(params.duration)
  if (duration !== undefined) {
    if (effectiveMode === 'audio') changes.audioDuration = duration
    else changes.duration = duration
  }
  const fps = asNumber(params.fps)
  if (fps !== undefined) changes.fps = fps

  // --- 音声固有 ------------------------------------------------------------
  const audioPrompt = asString(params.audio_prompt)
  if (audioPrompt !== undefined) changes.audioPrompt = audioPrompt
  const lyrics = asString(params.lyrics)
  if (lyrics !== undefined) changes.lyrics = lyrics
  const negativeTags = asString(params.negative_tags)
  if (negativeTags !== undefined) changes.negativeTags = negativeTags
  const bpm = asNumber(params.bpm)
  if (bpm !== undefined) changes.bpm = bpm
  const keyscale = asString(params.keyscale)
  if (keyscale !== undefined) changes.keyscale = keyscale
  const language = asString(params.language)
  if (language !== undefined) changes.language = language
  const audioCategory = asString(params.audio_category)
  if (audioCategory !== undefined) changes.audioCategory = audioCategory
  const reprompt = asBoolean(params.reprompt)
  if (reprompt !== undefined) changes.reprompt = reprompt

  // --- 選択式 / モデル指定 --------------------------------------------------
  const selects = asStringMap(params.selects)
  if (selects) changes.selects = selects
  const modelOverrides = asStringMap(params.model_overrides)
  if (modelOverrides) changes.modelOverrides = modelOverrides

  // --- シード --------------------------------------------------------------
  // 「再実行（シード再抽選）」と違い、復元は同じ絵が出る状態を戻すのが趣旨なので
  // 固定にする。ランダム実行だったジョブも実際に使われた値が記録されている。
  const videoSeeds = Array.isArray(params.video_seeds) ? params.video_seeds : []
  const seed =
    asNumber(params.seed) ??
    asNumber(params.image_seed) ??
    asNumber(params.audio_seed) ??
    asNumber(videoSeeds[0])
  if (seed !== undefined) {
    changes.seed = seed
    changes.seedLocked = true
  }

  return { patch: changes, missingLoras }
}

/** Which fields the selected mode + workflows do not use (SPEC §8).
 *
 * A field listed here is **not rendered at all** — the form only ever shows the
 * knobs the current mode + workflows actually read, so nothing greyed-out is
 * left to puzzle over. The values stay in :type:`FormState` regardless, so
 * switching back to a mode that uses a field restores what was typed.
 *
 * Both sides are driven by the workflow manifests: a video workflow that
 * declares no audio input hides the audio picker, one that needs a closing
 * frame or a reference clip shows the matching picker, t2v needs no start frame
 * at all — and an *editing* image workflow (qwen-image) needs an input picture
 * of its own while ignoring the aspect ratio / megapixel target.
 *
 * `audio` は独立ジョブなので、画像・動画のつまみは丸ごと落ちる。
 */
export function hiddenFields(
  mode: JobMode,
  workflow?: WorkflowOption | null,
  imageWorkflow?: WorkflowOption | null,
) {
  const video = mode !== 'image_only' && mode !== 'audio'
  const image = mode !== 'i2v' && mode !== 'audio'
  const requires = (name: string) =>
    video && (workflow?.requires ?? []).includes(name as never)
  const supports = (name: string) =>
    video && (workflow?.supports ?? []).includes(name)
  // 必須ではないが受け取れる入力（Veo の開始フレーム・最後のフレームは任意）も
  // 欄は出す — 出さないと渡す手立てが無くなる。
  const accepts = (name: string) => requires(name) || supports(name)
  const imageNeedsSource = image && imageWorkflowNeedsSource(imageWorkflow)
  return {
    imagePrompt: !image,
    // the image LoRA chain only exists in the image workflow, the video one
    // only in the LTX graph — so each follows its own stage
    loras: !image,
    trigger: !image,
    // LoRA チェーンを持たないワークフロー（Wan 系）には挿せないので出さない
    videoLoras: !video || !(workflow?.accepts_video_loras ?? true),
    videoTrigger: !video || !(workflow?.accepts_video_loras ?? true),
    videoPrompt: !video,
    negative: !video,
    audio: !requires('audio'),
    duration: !supports('duration'),
    fps: !supports('fps'),
    // in full mode the video's start frame comes from the image stage, but an
    // editing image workflow still needs its own input picture in every mode
    // that runs the image stage
    startImage: !((mode === 'i2v' && accepts('image')) || imageNeedsSource),
    endImage: !accepts('end_image'),
    referenceVideo: !requires('video'),
    // マルチモーダル参照は「開始フレームの代わり」なので、開始フレームを渡せる
    // mode（= i2v）でだけ意味がある。full は画像ステージが開始フレームを作る。
    references: !(mode === 'i2v' && referenceFields(workflow).length > 0),
    // ショット割り / Elements は動画ステージのパラメータなので、それが走る
    // mode（full / i2v）で、宣言のあるワークフローのときだけ出す。
    multiShots: !(video && multiShotLimits(workflow) !== null),
    elements: !(video && elementsLimits(workflow) !== null),
    // an editing workflow derives the size from its input picture; with no video
    // stage to size, the aspect ratio / megapixels then do nothing at all
    resolution: mode === 'audio' || (mode === 'image_only' && imageNeedsSource),
  }
}

/** Field errors the form can catch before POSTing (SPEC §8). */
export function validateForm(
  form: FormState,
  imageWorkflow?: WorkflowOption | null,
  audioWorkflow?: WorkflowOption | null,
  videoWorkflow?: WorkflowOption | null,
): Record<string, string> {
  const errors: Record<string, string> = {}
  if (form.mode === 'audio') {
    if (!form.audioPrompt.trim()) {
      errors.audio_prompt = '音声プロンプトを入力してください。'
    }
    const range = durationRange(audioWorkflow)
    if (
      range &&
      (form.audioDuration < range.min || form.audioDuration > range.max)
    ) {
      errors.duration =
        `${audioWorkflow?.label ?? 'このワークフロー'}の長さは` +
        ` ${range.min}〜${range.max} 秒です。`
    }
    if (audioSupports(audioWorkflow, 'bpm') && (form.bpm < 10 || form.bpm > 300)) {
      errors.bpm = 'BPM は 10〜300 で指定してください。'
    }
    return errors
  }
  if (
    form.mode !== 'i2v' &&
    imageWorkflowNeedsSource(imageWorkflow) &&
    !form.sourceImage
  ) {
    errors.source_image =
      `${imageWorkflow?.label ?? '選択中の画像ワークフロー'}は入力画像を編集する` +
      'ワークフローです。参照画像を選択してください。'
  }
  // マルチモーダル参照（SPEC §3.1）: 先頭フレームと排他で、件数にも上限がある。
  // どちらもバックエンドが 422 で断るので、送る前に同じ理由をその場で見せる。
  const references = referenceFields(videoWorkflow)
  const used = references.filter((item) => form[item.field].length > 0)
  if (used.length > 0) {
    if (form.mode !== 'i2v') {
      errors.references =
        '参照素材は「動画生成」モードでだけ使えます（画像＋動画は生成した静止画が' +
        '開始フレームになるため、参照素材とは併用できません）。'
    } else if (form.sourceImage || form.endImage) {
      errors.references =
        '開始フレーム / 最後のフレームと参照素材は同時に指定できません' +
        '（外部 API 側で排他のモードです）。どちらかを外してください。'
    }
    for (const item of used) {
      if (form[item.field].length > item.limit) {
        errors[item.name] = `${item.label}は ${item.limit} 件までです（今は ${
          form[item.field].length
        } 件）。`
      }
    }
  }
  // ショット割り / Elements（SPEC §3.1）。バックエンドが 422 で断るのと同じ
  // 理由を、送る前にその場で見せる。
  const runsVideo = form.mode === 'full' || form.mode === 'i2v'
  const shots = runsVideo ? form.multiShots : []
  const elements = runsVideo ? form.klingElements : []
  const shotLimits = multiShotLimits(videoWorkflow)
  const elementLimits = elementsLimits(videoWorkflow)
  const cost = elementLimits?.reference_chars ?? 0
  const maxChars = videoWorkflow?.max_prompt_chars ?? 0

  if (shots.length > 0 && shotLimits) {
    if (shots.length > shotLimits.max_shots) {
      errors.multi_shots = `ショットは ${shotLimits.max_shots} 個までです（今は ${shots.length} 個）。`
    }
    shots.forEach((shot, index) => {
      const where = `multi_shots.${index}`
      if (!shot.prompt.trim()) {
        errors[where] = `${index + 1} ショット目のプロンプトを入力してください。`
      } else if (maxChars && promptChars(shot.prompt, cost) > maxChars) {
        errors[where] =
          `${index + 1} ショット目は ${maxChars} 文字までです（今は ${promptChars(
            shot.prompt,
            cost,
          )} 文字）。`
      } else if (
        !Number.isInteger(shot.duration) ||
        shot.duration < shotLimits.min_duration ||
        shot.duration > shotLimits.max_duration
      ) {
        errors[where] =
          `${index + 1} ショット目の秒数は ${shotLimits.min_duration}〜${shotLimits.max_duration} 秒の整数です。`
      }
    })
  }
  if (elementLimits) {
    if (elements.length > elementLimits.max_elements) {
      errors.kling_elements = `Elements は ${elementLimits.max_elements} 要素までです（今は ${elements.length} 要素）。`
    }
    const names: string[] = []
    elements.forEach((element, index) => {
      const where = `kling_elements.${index}`
      const name = element.name.trim()
      if (!name) {
        errors[where] = `${index + 1} 個目の要素名を入力してください。`
      } else if (elementReferences(`@${name}`).join('') !== name) {
        errors[where] =
          `要素名「${name}」は \`@${name}\` として書けません（英数字・アンダースコア・ハイフン・日本語のみ、空白は不可）。`
      } else if (names.includes(name)) {
        errors[where] = `要素名「${name}」が重複しています。`
      } else if (
        element.images.length < elementLimits.min_images ||
        element.images.length > elementLimits.max_images
      ) {
        errors[where] =
          `要素「${name}」の参照画像は ${elementLimits.min_images}〜${elementLimits.max_images} 枚です（今は ${element.images.length} 枚）。`
      }
      if (name) names.push(name)
    })
    // 宣言していない `@名前` は文字として渡り、しかも文字数だけ消費してしまう
    const bodies = shots.length > 0 ? shots.map((shot) => shot.prompt) : []
    if (runsVideo) bodies.push(form.videoPrompt)
    for (const reference of bodies.flatMap(elementReferences)) {
      if (!names.includes(reference)) {
        errors.kling_elements =
          `プロンプトの \`@${reference}\` に対応する要素がありません（Elements に追加するか、\`@\` を外してください）。`
        break
      }
    }
  }
  // ショット割りを使っているときは本文がショット側にあるので、トップレベルの
  // プロンプトの長さだけをここで見る（ショット側は上のループで見ている）。
  if (
    runsVideo &&
    maxChars &&
    form.videoPrompt &&
    promptChars(form.videoPrompt, cost) > maxChars
  ) {
    errors.video_prompt = `動画プロンプトは ${maxChars} 文字までです（今は ${promptChars(
      form.videoPrompt,
      cost,
    )} 文字${cost ? '、`@要素名` 1 つは ' + cost + ' 文字' : ''}）。`
  }
  return errors
}

export const MODE_LABELS: Record<JobMode, string> = {
  full: '画像＋動画',
  i2v: '動画生成',
  image_only: '画像のみ',
  audio: '音声',
}

export const MODE_HINTS: Record<JobMode, string> = {
  full: '画像を生成 → その画像を開始フレームに動画を生成（2 段実行）',
  i2v: '選択した動画ワークフローを単発実行',
  image_only: '画像のみ生成',
  audio: '音声のみ生成（画像・動画とは連結しない単独実行）',
}

/** Workflows that can be used in `mode` (full needs a start-frame input). */
export function workflowsForMode(
  mode: JobMode,
  workflows: WorkflowOption[],
): WorkflowOption[] {
  return mode === 'full'
    ? workflows.filter((workflow) => workflow.accepts_start_image)
    : workflows
}
