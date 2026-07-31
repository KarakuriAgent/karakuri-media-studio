import type {
  AudioJobCreate,
  ComfyTarget,
  ImageFamily,
  JobMode,
  Lora,
  LoraRef,
  LoraTarget,
  ModelSlot,
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
  /** ACE-Step の歌詞。空ならインストゥルメンタル。 */
  lyrics: string
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
 */
export function jobSelects(
  form: FormState,
  workflow?: WorkflowOption | null,
): Record<string, string> {
  const picked: Record<string, string> = {}
  for (const select of workflowSelects(workflow)) {
    const value = form.selects[select.name]
    if (value && select.choices.includes(value)) picked[select.name] = value
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
  if (audioSupports(workflow, 'lyrics')) payload.lyrics = form.lyrics
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
    startImage: !((mode === 'i2v' && requires('image')) || imageNeedsSource),
    endImage: !requires('end_image'),
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
