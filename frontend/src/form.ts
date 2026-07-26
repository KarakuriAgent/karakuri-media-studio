import type { JobMode, Lora, LoraRef, PromptTemplate } from './types'

/** Backend default (models.DEFAULT_NEGATIVE_PROMPT). */
export const DEFAULT_NEGATIVE_PROMPT =
  'pc game, console game, video game, cartoon, childish, ugly'

/** docs/prompt-samples.md — the model author's own negative prompt. */
export const AUTHOR_NEGATIVE_PROMPT =
  'blurry, oversaturated, pixelated, low resolution, grainy, distorted, noise, ' +
  'compression artifacts, jpeg artifacts, glitches, watermark, text, logo, ' +
  'signature, copyright, subtitles, distorted sound, saturated sound, loud'

export const NEGATIVE_PRESET_LABELS: Record<string, string> = {
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
  duration: number
  fps: number
  seedLocked: boolean
  seed: number
  promptTemplate: PromptTemplate
}

export const initialForm: FormState = {
  mode: 'full',
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

/** Per-mode disabled fields (SPEC §8). */
export function disabledFields(mode: JobMode) {
  return {
    imagePrompt: mode === 'i2v',
    loras: mode === 'i2v',
    trigger: mode === 'i2v',
    videoPrompt: mode === 'image_only',
    negative: mode === 'image_only',
    audio: mode === 'image_only',
    duration: mode === 'image_only',
    fps: mode === 'image_only',
    startImage: mode !== 'i2v',
  }
}

export const MODE_LABELS: Record<JobMode, string> = {
  full: 'フル生成',
  i2v: '画像から動画',
  image_only: '画像のみ',
}
