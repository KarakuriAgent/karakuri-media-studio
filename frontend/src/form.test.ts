import { describe, expect, it } from 'vitest'
import {
  MODE_LABELS,
  audioJobPayload,
  audioSupports,
  clampToWorkflow,
  hiddenFields,
  durationRange,
  imageWorkflowNeedsSource,
  initialForm,
  isAudioJob,
  lorasForTarget,
  validateForm,
  workflowsForMode,
  type FormState,
} from './form'
import type { Lora, WorkflowOption } from './types'

function workflow(overrides: Partial<WorkflowOption> = {}): WorkflowOption {
  return {
    id: 'w',
    label: 'W',
    kind: 'video',
    family: 'ltx2.3',
    notes: '',
    requires: [],
    supports: ['prompt', 'negative', 'width', 'height', 'duration', 'fps'],
    accepts_start_image: false,
    image_label: '開始フレーム',
    min_duration: 0,
    max_duration: 0,
    default_duration: 0,
    ...overrides,
  }
}

const T2V = workflow({ id: 'ltx2_3_t2v' })
const I2V = workflow({ id: 'i2v', requires: ['image'], accepts_start_image: true })
const ID_LORA = workflow({
  id: 'id_lora',
  requires: ['image', 'audio'],
  accepts_start_image: true,
})
const FLF2V = workflow({
  id: 'flf2v',
  requires: ['image', 'end_image'],
  accepts_start_image: true,
})
const MOTION = workflow({
  id: 'motion',
  requires: ['image', 'video'],
  accepts_start_image: true,
  supports: ['prompt', 'negative', 'width', 'height', 'duration', 'fps'],
})

describe('workflowsForMode', () => {
  const all = [T2V, I2V, ID_LORA, FLF2V, MOTION]

  it('only offers start-frame capable workflows for full generation', () => {
    expect(workflowsForMode('full', all).map((w) => w.id)).toEqual([
      'i2v',
      'id_lora',
      'flf2v',
      'motion',
    ])
  })

  it('offers everything for a standalone video job', () => {
    expect(workflowsForMode('i2v', all)).toHaveLength(5)
  })
})

describe('hiddenFields', () => {
  it('greys out the image side for a standalone video job', () => {
    const hidden = hiddenFields('i2v', ID_LORA)
    expect(hidden.imagePrompt).toBe(true)
    expect(hidden.loras).toBe(true)
    expect(hidden.trigger).toBe(true)
    expect(hidden.videoPrompt).toBe(false)
    // the video LoRA chain lives in the LTX graph, which does run here
    expect(hidden.videoLoras).toBe(false)
    expect(hidden.videoTrigger).toBe(false)
  })

  it('keeps the video LoRAs available in every mode that renders video', () => {
    for (const mode of ['full', 'i2v'] as const) {
      expect(hiddenFields(mode, ID_LORA).videoLoras).toBe(false)
    }
    expect(hiddenFields('image_only', ID_LORA).videoLoras).toBe(true)
    expect(hiddenFields('image_only', ID_LORA).videoTrigger).toBe(true)
  })

  it('greys out the video side for an image-only job', () => {
    const hidden = hiddenFields('image_only', ID_LORA)
    expect(hidden.videoPrompt).toBe(true)
    expect(hidden.negative).toBe(true)
    expect(hidden.audio).toBe(true)
    expect(hidden.duration).toBe(true)
    expect(hidden.fps).toBe(true)
    expect(hidden.startImage).toBe(true)
    expect(hidden.endImage).toBe(true)
    expect(hidden.referenceVideo).toBe(true)
  })

  it('enables audio only for workflows that take it', () => {
    expect(hiddenFields('i2v', ID_LORA).audio).toBe(false)
    expect(hiddenFields('i2v', I2V).audio).toBe(true)
    expect(hiddenFields('full', ID_LORA).audio).toBe(false)
  })

  it('asks for a start frame only when the workflow needs one', () => {
    expect(hiddenFields('i2v', I2V).startImage).toBe(false)
    expect(hiddenFields('i2v', T2V).startImage).toBe(true)
    // full generation produces the start frame itself
    expect(hiddenFields('full', I2V).startImage).toBe(true)
  })

  it('shows the extra inputs each workflow declares', () => {
    expect(hiddenFields('i2v', FLF2V).endImage).toBe(false)
    expect(hiddenFields('full', FLF2V).endImage).toBe(false)
    expect(hiddenFields('i2v', I2V).endImage).toBe(true)
    expect(hiddenFields('i2v', MOTION).referenceVideo).toBe(false)
    expect(hiddenFields('i2v', FLF2V).referenceVideo).toBe(true)
  })

  it('treats a not-yet-loaded workflow as offering nothing', () => {
    const hidden = hiddenFields('i2v', null)
    expect(hidden.audio).toBe(true)
    expect(hidden.startImage).toBe(true)
    expect(hidden.duration).toBe(true)
  })
})

describe('lorasForTarget', () => {
  const lora = (id: number, target?: Lora['target'], family = 'krea2'): Lora => ({
    id,
    display_name: `L${id}`,
    lora_name: `l${id}.safetensors`,
    trigger_word: `t${id}`,
    default_strength: 1,
    default_audio: null,
    sort_order: 0,
    target: target as Lora['target'],
    family,
    sample_images: [],
  })

  const all = [lora(1, 'image'), lora(2, 'video'), lora(3, 'image')]

  it('splits the registry by stage', () => {
    expect(lorasForTarget(all, 'image').map((l) => l.id)).toEqual([1, 3])
    expect(lorasForTarget(all, 'video').map((l) => l.id)).toEqual([2])
  })

  it('treats a target-less entry as an image LoRA', () => {
    const legacy = [lora(9, undefined)]
    expect(lorasForTarget(legacy, 'image')).toHaveLength(1)
    expect(lorasForTarget(legacy, 'video')).toHaveLength(0)
  })
})

// ---------------------------------------------------------------- image side

const KREA2 = workflow({
  id: 'krea2_turbo',
  label: 'Krea 2 turbo',
  kind: 'image',
  family: 'krea2',
  supports: ['aspect_ratio', 'megapixels', 'prompt', 'seed'],
})
const ANIMA = workflow({
  id: 'anima',
  label: 'Anima',
  kind: 'image',
  family: 'anima',
  supports: ['aspect_ratio', 'megapixels', 'prompt', 'seed'],
})
const QWEN = workflow({
  id: 'qwen_image_edit_2511',
  label: 'Qwen-Image Edit 2511',
  kind: 'image',
  family: 'qwen-image',
  requires: ['image'],
  supports: ['image', 'prompt', 'seed'],
  image_label: '編集元画像',
})

describe('image LoRA families', () => {
  const imageLora = (id: number, family: string): Lora => ({
    id,
    display_name: `L${id}`,
    lora_name: `l${id}.safetensors`,
    trigger_word: `t${id}`,
    default_strength: 1,
    default_audio: null,
    sort_order: 0,
    target: 'image',
    family,
    sample_images: [],
  })

  const all = [
    imageLora(1, 'krea2'),
    imageLora(2, 'anima'),
    { ...imageLora(3, 'krea2'), target: 'video' as const },
  ]

  it('only offers the family of the selected image workflow', () => {
    expect(lorasForTarget(all, 'image', 'krea2').map((l) => l.id)).toEqual([1])
    expect(lorasForTarget(all, 'image', 'anima').map((l) => l.id)).toEqual([2])
    expect(lorasForTarget(all, 'image', 'z-image')).toHaveLength(0)
  })

  it('ignores the family for video LoRAs', () => {
    expect(lorasForTarget(all, 'video', 'anima').map((l) => l.id)).toEqual([3])
  })

  it('falls back to every image LoRA when no family is known yet', () => {
    expect(lorasForTarget(all, 'image').map((l) => l.id)).toEqual([1, 2])
  })

  it('treats a family-less legacy row as krea2', () => {
    const legacy = [{ ...imageLora(9, 'krea2'), family: undefined as never }]
    expect(lorasForTarget(legacy, 'image', 'krea2')).toHaveLength(1)
    expect(lorasForTarget(legacy, 'image', 'anima')).toHaveLength(0)
  })
})

describe('editing image workflows', () => {
  it('knows which image workflows edit a given picture', () => {
    expect(imageWorkflowNeedsSource(QWEN)).toBe(true)
    expect(imageWorkflowNeedsSource(KREA2)).toBe(false)
    expect(imageWorkflowNeedsSource(ANIMA)).toBe(false)
    expect(imageWorkflowNeedsSource(null)).toBe(false)
  })

  it('asks for a reference image in every mode that runs the image stage', () => {
    expect(hiddenFields('image_only', ID_LORA, QWEN).startImage).toBe(false)
    // full mode: the video start frame is generated, but qwen still needs input
    expect(hiddenFields('full', I2V, QWEN).startImage).toBe(false)
    expect(hiddenFields('full', I2V, KREA2).startImage).toBe(true)
    // i2v runs no image stage at all
    expect(hiddenFields('i2v', T2V, QWEN).startImage).toBe(true)
  })

  it('drops the resolution controls when nothing else uses them', () => {
    expect(hiddenFields('image_only', ID_LORA, QWEN).resolution).toBe(true)
    // in full mode the video stage still needs the aspect ratio
    expect(hiddenFields('full', I2V, QWEN).resolution).toBe(false)
    expect(hiddenFields('image_only', ID_LORA, KREA2).resolution).toBe(false)
  })

  it('rejects a submit without the picture the editing workflow needs', () => {
    const form = { ...initialForm, mode: 'image_only' as const, sourceImage: '' }
    expect(validateForm(form, QWEN).source_image).toContain('参照画像')
    expect(validateForm({ ...form, sourceImage: '/assets/image/a.png' }, QWEN))
      .toEqual({})
    expect(validateForm(form, KREA2)).toEqual({})
    // no image stage -> nothing to validate
    expect(validateForm({ ...form, mode: 'i2v' }, QWEN)).toEqual({})
  })
})

// ------------------------------------------------------------- audio (mode)
// 音声はモードタブの一つだが、走るのは音声ワークフロー 1 本きりの独立ジョブ。

const ACE = workflow({
  id: 'ace_step1_5_xl_sft',
  label: 'ACE-Step 1.5 XL',
  kind: 'audio',
  family: 'ace-step',
  supports: ['prompt', 'lyrics', 'duration', 'bpm', 'keyscale', 'language', 'seed'],
  min_duration: 10,
  max_duration: 600,
  default_duration: 120,
})

const SA3 = workflow({
  id: 'stable_audio_3_medium_base',
  label: 'Stable Audio 3 Medium',
  kind: 'audio',
  family: 'stable-audio',
  supports: ['prompt', 'duration', 'audio_category', 'reprompt', 'seed'],
  min_duration: 1,
  max_duration: 380,
  default_duration: 60,
})

function audioForm(overrides: Partial<FormState> = {}): FormState {
  return {
    ...initialForm,
    mode: 'audio',
    audioPrompt: 'a warm lofi loop',
    ...overrides,
  }
}

describe('audioSupports / durationRange', () => {
  it('reads the knobs straight off the workflow manifest', () => {
    expect(audioSupports(ACE, 'lyrics')).toBe(true)
    expect(audioSupports(SA3, 'lyrics')).toBe(false)
    expect(audioSupports(SA3, 'audio_category')).toBe(true)
    expect(audioSupports(ACE, 'audio_category')).toBe(false)
  })

  it('treats a missing workflow as unconstrained', () => {
    expect(durationRange(null)).toBeNull()
    expect(durationRange(ACE)).toEqual({ min: 10, max: 600 })
  })
})

describe('clampToWorkflow', () => {
  it('keeps a duration that is already in range', () => {
    expect(clampToWorkflow(audioForm({ audioDuration: 90 }), ACE)).toEqual({})
  })

  it('falls back to the new model default when switching out of range', () => {
    expect(clampToWorkflow(audioForm({ audioDuration: 600 }), SA3)).toEqual({
      audioDuration: 60,
    })
    expect(clampToWorkflow(audioForm({ audioDuration: 2 }), ACE)).toEqual({
      audioDuration: 120,
    })
  })

  it('does nothing while the workflow list is unknown', () => {
    expect(clampToWorkflow(audioForm({ audioDuration: 3 }), null)).toEqual({})
  })
})

describe('hiddenFields — audio mode', () => {
  it('turns every image / video knob off', () => {
    const hidden = hiddenFields('audio', ID_LORA, KREA2)
    expect(hidden.imagePrompt).toBe(true)
    expect(hidden.videoPrompt).toBe(true)
    expect(hidden.loras).toBe(true)
    expect(hidden.videoLoras).toBe(true)
    expect(hidden.negative).toBe(true)
    expect(hidden.audio).toBe(true)
    expect(hidden.startImage).toBe(true)
    expect(hidden.endImage).toBe(true)
    expect(hidden.referenceVideo).toBe(true)
    expect(hidden.resolution).toBe(true)
    expect(hidden.duration).toBe(true)
    expect(hidden.fps).toBe(true)
  })
})

describe('validateForm — audio mode', () => {
  it('accepts a filled-in form', () => {
    expect(validateForm(audioForm({ audioDuration: 120 }), null, ACE)).toEqual({})
  })

  it('requires a prompt', () => {
    expect(
      validateForm(audioForm({ audioPrompt: '   ' }), null, ACE),
    ).toHaveProperty('audio_prompt')
  })

  it('reports a duration outside the model range', () => {
    const errors = validateForm(audioForm({ audioDuration: 900 }), null, ACE)
    expect(errors.duration).toContain('10')
    expect(errors.duration).toContain('600')
    expect(
      validateForm(audioForm({ audioDuration: 900 }), null, SA3).duration,
    ).toContain('380')
  })

  it('only checks the bpm of a workflow that has one', () => {
    expect(
      validateForm(audioForm({ bpm: 900, audioDuration: 120 }), null, ACE),
    ).toHaveProperty('bpm')
    expect(
      validateForm(audioForm({ bpm: 900, audioDuration: 60 }), null, SA3),
    ).toEqual({})
  })

  it('does not run the image-workflow check in audio mode', () => {
    // qwen-image が選ばれたままでも、音声ジョブは参照画像を要求しない
    expect(
      validateForm(audioForm({ audioDuration: 120, sourceImage: '' }), QWEN, ACE),
    ).toEqual({})
  })
})

describe('audioJobPayload', () => {
  it('sends only the fields the selected workflow uses (ACE-Step)', () => {
    const payload = audioJobPayload(
      audioForm({
        audioWorkflow: ACE.id,
        lyrics: '[Verse 1]\nhello',
        audioDuration: 120,
        bpm: 92,
        keyscale: 'F# minor',
        language: 'ja',
        audioCategory: 'SFX',
        reprompt: true,
      }),
      ACE,
    )
    expect(payload).toMatchObject({
      mode: 'audio',
      audio_workflow: ACE.id,
      audio_prompt: 'a warm lofi loop',
      lyrics: '[Verse 1]\nhello',
      duration: 120,
      bpm: 92,
      keyscale: 'F# minor',
      language: 'ja',
      seed: null,
    })
    expect(payload).not.toHaveProperty('audio_category')
    expect(payload).not.toHaveProperty('reprompt')
  })

  it('sends only the fields the selected workflow uses (Stable Audio)', () => {
    const payload = audioJobPayload(
      audioForm({
        audioWorkflow: SA3.id,
        audioDuration: 30,
        audioCategory: 'SFX',
        reprompt: true,
      }),
      SA3,
    )
    expect(payload).toMatchObject({
      audio_workflow: SA3.id,
      audio_category: 'SFX',
      reprompt: true,
      duration: 30,
    })
    expect(payload).not.toHaveProperty('lyrics')
    expect(payload).not.toHaveProperty('bpm')
    expect(payload).not.toHaveProperty('keyscale')
  })

  it('never carries image / video fields', () => {
    const payload = audioJobPayload(audioForm({ audioWorkflow: ACE.id }), ACE)
    for (const key of [
      'image_prompt',
      'video_prompt',
      'image_workflow',
      'video_workflow',
      'loras',
      'video_loras',
      'source_image',
      'aspect_ratio',
      'fps',
    ]) {
      expect(payload).not.toHaveProperty(key)
    }
  })

  it('uses the audio length, never the video one', () => {
    const payload = audioJobPayload(
      audioForm({ duration: 10, audioDuration: 180 }),
      ACE,
    )
    expect(payload.duration).toBe(180)
  })

  it('passes an explicit seed only when it is locked', () => {
    expect(audioJobPayload(audioForm({ seedLocked: true, seed: 7 }), ACE).seed).toBe(7)
    expect(
      audioJobPayload(audioForm({ seedLocked: false, seed: 7 }), ACE).seed,
    ).toBeNull()
  })
})

describe('isAudioJob', () => {
  it('separates audio jobs from the image / video ones', () => {
    expect(isAudioJob({ mode: 'audio' })).toBe(true)
    expect(isAudioJob({ mode: 'full' })).toBe(false)
    expect(isAudioJob({ mode: 'i2v' })).toBe(false)
    expect(isAudioJob({ mode: 'image_only' })).toBe(false)
  })
})

describe('MODE_LABELS', () => {
  it('names the chained mode 画像＋動画 and lists audio as a mode', () => {
    expect(MODE_LABELS.full).toBe('画像＋動画')
    expect(MODE_LABELS.audio).toBe('音声')
  })
})
