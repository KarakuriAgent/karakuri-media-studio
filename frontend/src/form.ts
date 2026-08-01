import type {
  AudioJobCreate,
  ComfyTarget,
  ImageFamily,
  JobMode,
  Lora,
  LoraRef,
  LoraTarget,
  ModelSlot,
  Options,
  PromptTemplate,
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
