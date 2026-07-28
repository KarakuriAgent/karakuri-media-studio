import type {
  JobMode,
  Lora,
  LoraRef,
  PromptTemplate,
  WorkflowOption,
} from './types'

/** Fallback while /api/options has not answered yet (backend default). */
export const DEFAULT_VIDEO_WORKFLOW = 'ltx2_3_id_lora'

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
  imagePrompt: string
  videoPrompt: string
  negativePreset: string
  negativePrompt: string
  aspectRatio: string
  megapixels: number
  loras: SelectedLora[]
  triggerText: string
  triggerDirty: boolean
  audioPath: string
  sourceImage: string
  endImage: string
  referenceVideo: string
  duration: number
  fps: number
  seedLocked: boolean
  seed: number
  promptTemplate: PromptTemplate
}

export const initialForm: FormState = {
  mode: 'full',
  videoWorkflow: DEFAULT_VIDEO_WORKFLOW,
  imagePrompt: '',
  videoPrompt: '',
  negativePreset: 'current',
  negativePrompt: DEFAULT_NEGATIVE_PROMPT,
  aspectRatio: '4:3 (Standard)',
  megapixels: 1,
  loras: [],
  triggerText: '',
  triggerDirty: false,
  audioPath: '',
  sourceImage: '',
  endImage: '',
  referenceVideo: '',
  duration: 10,
  fps: 25,
  seedLocked: false,
  seed: 0,
  promptTemplate: 'natural',
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

/** Which fields the selected mode + video workflow does not use (SPEC §8).
 *
 * The video side is driven by the workflow manifest: a workflow that declares no
 * audio input greys the audio picker out, one that needs a closing frame or a
 * reference clip shows the matching picker, and t2v needs no start frame at all.
 */
export function disabledFields(mode: JobMode, workflow?: WorkflowOption | null) {
  const video = mode !== 'image_only'
  const requires = (name: string) =>
    video && (workflow?.requires ?? []).includes(name as never)
  const supports = (name: string) =>
    video && (workflow?.supports ?? []).includes(name)
  return {
    imagePrompt: mode === 'i2v',
    loras: mode === 'i2v',
    trigger: mode === 'i2v',
    videoPrompt: !video,
    negative: !video,
    audio: !requires('audio'),
    duration: !supports('duration'),
    fps: !supports('fps'),
    // in full mode the start frame comes from the image stage
    startImage: !(mode === 'i2v' && requires('image')),
    endImage: !requires('end_image'),
    referenceVideo: !requires('video'),
  }
}

export const MODE_LABELS: Record<JobMode, string> = {
  full: 'フル生成',
  i2v: '動画生成',
  image_only: '画像のみ',
}

export const MODE_HINTS: Record<JobMode, string> = {
  full: '画像を生成 → その画像を開始フレームに動画を生成（2 段実行）',
  i2v: '選択した動画ワークフローを単発実行',
  image_only: '画像のみ生成',
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
