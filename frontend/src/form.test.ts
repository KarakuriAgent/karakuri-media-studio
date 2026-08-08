import { describe, expect, it } from 'vitest'
import {
  AUTHOR_NEGATIVE_PROMPT,
  DEFAULT_MEGAPIXELS,
  DEFAULT_NEGATIVE_PROMPT,
  MODE_LABELS,
  audioJobPayload,
  audioSupports,
  clampToWorkflow,
  formStateFromParams,
  hiddenFields,
  durationRange,
  imageWorkflowNeedsSource,
  initialForm,
  isAudioJob,
  jobModelOverrides,
  jobSelects,
  jobSteps,
  jobWorkflowIds,
  lorasForTarget,
  matchesLoraQuery,
  megapixelsFor,
  modelSlotsForJob,
  elementsLimits,
  multiShotLimits,
  needsReferenceSheet,
  newShot,
  promptChars,
  referenceFields,
  sheetSize,
  toggleReference,
  validateForm,
  workflowFamilies,
  workflowFamilyLabel,
  workflowModeLabel,
  workflowsForMode,
  type FormState,
} from './form'
import type {
  KlingElement,
  Lora,
  ModelSlot,
  Options,
  WorkflowOption,
  WorkflowSelect,
} from './types'

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
    selects: [],
    prompt_required: true,
    accepts_video_loras: true,
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
// Veo（kie.ai）: 画像も最後のフレームも必須ではないが、渡せる
const VEO = workflow({
  id: 'veo3_1_fast',
  requires: [],
  accepts_start_image: true,
  supports: ['prompt', 'image', 'end_image'],
  backend: 'kie',
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

describe('workflowFamilies', () => {
  const KLING = workflow({
    id: 'kling3_video',
    label: 'Kling 3.0（音声つき・外部 API）',
    mode_label: '標準（音声つき）',
    family_label: 'Kling 3.0（外部 API）',
    family: 'kling',
  })
  const KLING_MULTI = workflow({
    id: 'kling3_multishot',
    mode_label: 'マルチショット（音声つき）',
    family_label: 'Kling 3.0（外部 API）',
    family: 'kling',
  })

  it('ファミリーごとにまとめ、並びは一覧の初出順', () => {
    const groups = workflowFamilies([T2V, KLING, I2V, KLING_MULTI])
    expect(groups.map((group) => group.family)).toEqual(['ltx2.3', 'kling'])
    expect(groups[0].workflows.map((item) => item.id)).toEqual([
      'ltx2_3_t2v',
      'i2v',
    ])
    expect(groups[1].label).toBe('Kling 3.0（外部 API）')
  })

  it('モードのラベルはモデル名を含まない mode_label を使う', () => {
    expect(workflowModeLabel(KLING)).toBe('標準（音声つき）')
    // 宣言の無い（古いレスポンスの）ものは label のまま
    expect(workflowModeLabel(T2V)).toBe('W')
  })

  it('モデルのラベルは family_label、無ければ日本語名に落とす', () => {
    expect(workflowFamilyLabel(KLING)).toBe('Kling 3.0（外部 API）')
    expect(workflowFamilyLabel(T2V)).toBe('LTX 2.3')
    expect(workflowFamilyLabel(workflow({ family: 'unknown' }))).toBe('unknown')
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

  it('offers the optional image inputs of a workflow that only accepts them', () => {
    // Veo は画像なしでも生成できるが、渡せる以上フォームには出す
    expect(hiddenFields('i2v', VEO).startImage).toBe(false)
    expect(hiddenFields('i2v', VEO).endImage).toBe(false)
    // 尺・fps は API 側の選択式なので、汎用の数値欄は出さない
    expect(hiddenFields('i2v', VEO).duration).toBe(true)
    expect(hiddenFields('i2v', VEO).fps).toBe(true)
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

  describe('matchesLoraQuery', () => {
    const row: Lora = {
      ...lora(1, 'image'),
      display_name: 'サクラ',
      lora_name: 'Sakura_v1.safetensors',
      trigger_word: 'sakuraStyle',
    }

    it('keeps everything when the query is blank', () => {
      expect(matchesLoraQuery(row, '')).toBe(true)
      expect(matchesLoraQuery(row, '   ')).toBe(true)
    })

    it('matches display name, file name and trigger word case-insensitively', () => {
      expect(matchesLoraQuery(row, 'サクラ')).toBe(true)
      expect(matchesLoraQuery(row, ' sakura_v1 ')).toBe(true)
      expect(matchesLoraQuery(row, 'SAKURASTYLE')).toBe(true)
      expect(matchesLoraQuery(row, '見つからない名前')).toBe(false)
    })
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
  selects: [],
  prompt_required: true,
  accepts_video_loras: true,
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

  it('hides the image LoRA chain for a workflow that cannot take one', () => {
    // Grok Imagine は ComfyUI のグラフを持たない（= lora_chain が無い）
    const GROK = workflow({
      id: 'grok_imagine_t2i',
      kind: 'image',
      family: 'grok-imagine',
      backend: 'grok_cli',
      supports: ['aspect_ratio', 'prompt'],
      accepts_video_loras: false,
    })
    expect(hiddenFields('image_only', ID_LORA, GROK).loras).toBe(true)
    expect(hiddenFields('image_only', ID_LORA, GROK).trigger).toBe(true)
    expect(hiddenFields('full', I2V, GROK).loras).toBe(true)
    // LoRA チェーンを持つ ComfyUI のワークフローはこれまでどおり出す
    expect(hiddenFields('image_only', ID_LORA, KREA2).loras).toBe(false)
    // 選択肢がまだ来ていない（ワークフロー未指定）ときも出す
    expect(hiddenFields('image_only', ID_LORA).loras).toBe(false)
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

// ------------------------------------------- マルチモーダル参照（SPEC §3.1）

// Seedance 2 の参照版（kie.ai）: 参照素材専用で、開始フレームは受け取らない。
const SEEDANCE = workflow({
  id: 'seedance2_ref',
  requires: [],
  accepts_start_image: false,
  supports: [
    'prompt',
    'reference_images',
    'reference_videos',
    'reference_audios',
  ],
  multi_inputs: {
    reference_images: 9,
    reference_videos: 3,
    reference_audios: 3,
  },
  backend: 'kie',
})

describe('マルチモーダル参照', () => {
  it('宣言しているワークフローの欄だけを、宣言順で出す', () => {
    expect(referenceFields(SEEDANCE).map((item) => item.name)).toEqual([
      'reference_images',
      'reference_videos',
      'reference_audios',
    ])
    expect(referenceFields(SEEDANCE)[0].limit).toBe(9)
    expect(referenceFields(SEEDANCE)[1].field).toBe('referenceVideos')
    // 宣言のないワークフローには欄そのものが無い
    expect(referenceFields(VEO)).toEqual([])
    expect(referenceFields(null)).toEqual([])
  })

  it('参照専用ワークフローの i2v でだけ欄を出す', () => {
    expect(hiddenFields('i2v', SEEDANCE).references).toBe(false)
    // full は画像ステージが開始フレームを作るので参照ワークフローでは使えない
    expect(hiddenFields('full', SEEDANCE).references).toBe(true)
    expect(hiddenFields('i2v', VEO).references).toBe(true)
    // 参照専用ワークフローには開始 / 最終フレームの欄そのものが無い
    expect(hiddenFields('i2v', SEEDANCE).startImage).toBe(true)
    expect(hiddenFields('i2v', SEEDANCE).endImage).toBe(true)
  })

  it('選んだ順に積み上げ、もう一度選ぶと外す', () => {
    expect(toggleReference([], 'a')).toEqual(['a'])
    expect(toggleReference(['a'], 'b')).toEqual(['a', 'b'])
    expect(toggleReference(['a', 'b'], 'a')).toEqual(['b'])
  })

  it('参照素材だけのジョブは通る（開始フレームの欄は出ない）', () => {
    const form: FormState = {
      ...initialForm,
      mode: 'i2v',
      referenceImages: ['/library/image/a.png'],
    }
    expect(validateForm(form, null, null, SEEDANCE)).toEqual({})
    // 欄が出ないので送られない = 排他の検証そのものが要らなくなった
    expect(hiddenFields('i2v', SEEDANCE).startImage).toBe(true)
  })

  it('full モードでの参照指定と件数超過を断る', () => {
    const form: FormState = {
      ...initialForm,
      mode: 'full',
      imagePrompt: 'a cat',
      referenceImages: ['/library/image/a.png'],
    }
    expect(validateForm(form, KREA2, null, SEEDANCE).references).toContain(
      '動画生成',
    )

    const tooMany: FormState = {
      ...initialForm,
      mode: 'i2v',
      referenceVideos: ['a', 'b', 'c', 'd'].map((n) => `/library/video/${n}.mp4`),
    }
    expect(
      validateForm(tooMany, null, null, SEEDANCE).reference_videos,
    ).toContain('3 件までです')
  })

  it('参照専用ワークフローの固定値は選択肢そのものが 1 つ（Veo は 8 秒固定）', () => {
    // Veo 3.1 Fast の素材参照生成: 参照画像 3 枚まで・尺は 8 秒しか作れない
    const veoRef = workflow({
      id: 'veo3_1_fast_ref',
      accepts_start_image: false,
      supports: ['prompt', 'reference_images'],
      multi_inputs: { reference_images: 3 },
      selects: [
        {
          name: 'duration',
          label: '尺（秒）',
          choices: ['8'],
          default: '8',
          auto: false,
          hint: '',
        },
      ],
      backend: 'kie',
    })
    const form: FormState = {
      ...initialForm,
      mode: 'i2v',
      referenceImages: ['/library/image/a.png'],
    }
    expect(validateForm(form, null, null, veoRef)).toEqual({})
    // 選べる尺が 1 つしかないので、他の尺を選ぶ手立てそのものが無い
    expect(veoRef.selects[0].choices).toEqual(['8'])
  })

  it('過去ジョブの params から参照素材を復元する', () => {
    const { patch } = formStateFromParams(
      {
        reference_images: ['/library/image/a.png', '/library/image/b.png'],
        reference_videos: [],
        // 文字列以外が混ざった行は復元しない
        reference_audios: 'nope',
      },
      null,
    )
    expect(patch.referenceImages).toEqual([
      '/library/image/a.png',
      '/library/image/b.png',
    ])
    expect(patch.referenceVideos).toEqual([])
    expect(patch.referenceAudios).toBeUndefined()
  })
})

// ------------------------- ショット割り / Elements（SPEC §3.1、Kling 3.0）

// Kling 3.0（kie.ai）: 500 文字の上限つきで Elements を持つ 1 カット版。
const KLING = workflow({
  id: 'kling3_video',
  requires: [],
  accepts_start_image: true,
  supports: ['prompt', 'image', 'end_image'],
  max_prompt_chars: 500,
  elements: {
    max_elements: 3,
    min_images: 2,
    max_images: 4,
    reference_chars: 37,
  },
  backend: 'kie',
})

// ショット割り専用版: 本文はショット側にあるので `video_prompt` を持たない。
const KLING_SHOTS = workflow({
  ...KLING,
  id: 'kling3_multishot',
  prompt_required: false,
  multi_shot: { max_shots: 5, min_duration: 1, max_duration: 12 },
})

const IMAGES = ['/library/image/a.png', '/library/image/b.png']

function klingForm(overrides: Partial<FormState> = {}): FormState {
  return { ...initialForm, mode: 'i2v', videoWorkflow: KLING.id, ...overrides }
}

function shotForm(overrides: Partial<FormState> = {}): FormState {
  return {
    ...initialForm,
    mode: 'i2v',
    videoWorkflow: KLING_SHOTS.id,
    ...overrides,
  }
}

describe('マルチショット', () => {
  it('宣言のあるワークフローで動画ステージが走るときだけ欄を出す', () => {
    expect(multiShotLimits(KLING_SHOTS)?.max_shots).toBe(5)
    // 1 カット版にはショット割りの宣言そのものが無い
    expect(multiShotLimits(KLING)).toBeNull()
    expect(multiShotLimits(VEO)).toBeNull()
    expect(multiShotLimits(null)).toBeNull()

    expect(hiddenFields('i2v', KLING_SHOTS).multiShots).toBe(false)
    expect(hiddenFields('full', KLING_SHOTS).multiShots).toBe(false)
    // 動画ステージを走らせない mode にはショットの概念が無い
    expect(hiddenFields('image_only', KLING_SHOTS).multiShots).toBe(true)
    expect(hiddenFields('i2v', KLING).multiShots).toBe(true)
    expect(hiddenFields('i2v', VEO).multiShots).toBe(true)
    // ショット割り版では本文がショット側なので、プロンプト欄は出さない
    expect(hiddenFields('i2v', KLING_SHOTS).videoPrompt).toBe(true)
    expect(hiddenFields('i2v', KLING).videoPrompt).toBe(false)
  })

  it('ショット割り専用のワークフローではショットが必須', () => {
    expect(validateForm(shotForm(), null, null, KLING_SHOTS).multi_shots)
      .toContain('ショット割り専用')
    expect(
      validateForm(
        shotForm({ multiShots: [{ prompt: 'She turns.', duration: 5 }] }),
        null,
        null,
        KLING_SHOTS,
      ),
    ).toEqual({})
  })

  it('追加するショットの秒数は宣言の範囲に収まる', () => {
    expect(newShot({ max_shots: 5, min_duration: 1, max_duration: 12 })).toEqual({
      prompt: '',
      duration: 5,
    })
    // 範囲が 5 秒を含まないときは端に寄せる
    expect(
      newShot({ max_shots: 5, min_duration: 1, max_duration: 3 }).duration,
    ).toBe(3)
    expect(
      newShot({ max_shots: 5, min_duration: 8, max_duration: 12 }).duration,
    ).toBe(8)
  })

  it('件数・秒数・1 ショットの長さを送る前に断る', () => {
    const shot = { prompt: 'She turns.', duration: 5 }
    expect(validateForm(shotForm({ multiShots: [shot] }), null, null, KLING)).toEqual(
      {},
    )
    // ちょうど 5 ショットまでは通る
    expect(
      validateForm(
        shotForm({ multiShots: Array(5).fill(shot) }),
        null,
        null,
        KLING_SHOTS,
      ),
    ).toEqual({})

    expect(
      validateForm(
        shotForm({ multiShots: Array(6).fill(shot) }),
        null,
        null,
        KLING_SHOTS,
      ).multi_shots,
    ).toContain('5 個までです')
    expect(
      validateForm(
        shotForm({ multiShots: [{ ...shot, duration: 13 }] }),
        null,
        null,
        KLING_SHOTS,
      )['multi_shots.0'],
    ).toContain('1〜12 秒')
    expect(
      validateForm(
        shotForm({ multiShots: [{ ...shot, prompt: '  ' }] }),
        null,
        null,
        KLING_SHOTS,
      )['multi_shots.0'],
    ).toContain('プロンプトを入力')
    expect(
      validateForm(
        shotForm({ multiShots: [{ ...shot, prompt: 'a'.repeat(501) }] }),
        null,
        null,
        KLING_SHOTS,
      )['multi_shots.0'],
    ).toContain('500 文字までです')
  })

  it('過去ジョブの params から復元する', () => {
    const { patch } = formStateFromParams(
      {
        multi_shots: [
          { prompt: 'Shot 1', duration: 4 },
          // 形の違う行は落とす
          { duration: 5 },
        ],
      },
      null,
    )
    expect(patch.multiShots).toEqual([{ prompt: 'Shot 1', duration: 4 }])
    expect(formStateFromParams({ multi_shots: 'nope' }, null).patch.multiShots)
      .toBeUndefined()
  })
})

describe('Elements', () => {
  it('`@要素名` は 37 文字として数える', () => {
    expect(promptChars('@kaori walks.', 37)).toBe(37 + ' walks.'.length)
    // Elements を持たないモデルでは補正しない
    expect(promptChars('@kaori walks.', 0)).toBe('@kaori walks.'.length)
    expect(promptChars('', 37)).toBe(0)

    // 見た目 470 文字でも `@kaori`（6 -> 37）の分で 501 文字になり弾かれる
    const declared = [{ name: 'kaori', description: '', images: IMAGES }]
    const text = '@kaori ' + 'a'.repeat(463)
    expect(text.length).toBe(470)
    expect(
      validateForm(
        klingForm({ videoPrompt: text, klingElements: declared }),
        null,
        null,
        KLING,
      ).video_prompt,
    ).toContain('501 文字')
    expect(
      validateForm(
        klingForm({ videoPrompt: text.slice(0, -1), klingElements: declared }),
        null,
        null,
        KLING,
      ),
    ).toEqual({})
  })

  it('宣言していない `@名前` を送る前に断る', () => {
    const element = { name: 'kaori', description: '', images: IMAGES }
    expect(elementsLimits(KLING)?.max_elements).toBe(3)
    expect(elementsLimits(VEO)).toBeNull()

    expect(
      validateForm(
        klingForm({ klingElements: [element], videoPrompt: '@kaori waits.' }),
        null,
        null,
        KLING,
      ),
    ).toEqual({})
    expect(
      validateForm(
        klingForm({ klingElements: [element], videoPrompt: '@akira waits.' }),
        null,
        null,
        KLING,
      ).kling_elements,
    ).toContain('`@akira`')
    // ショットの本文の `@名前` も同じように見る
    expect(
      validateForm(
        shotForm({
          klingElements: [element],
          multiShots: [{ prompt: '@akira waits.', duration: 4 }],
        }),
        null,
        null,
        KLING_SHOTS,
      ).kling_elements,
    ).toContain('`@akira`')
    // 宣言したが参照していないのは、素材を先に用意しただけなので通す
    expect(
      validateForm(
        klingForm({ klingElements: [element], videoPrompt: 'She waits.' }),
        null,
        null,
        KLING,
      ),
    ).toEqual({})
  })

  it('要素の形（名前・枚数・重複）も送る前に見る', () => {
    const check = (element: Partial<KlingElement>) =>
      validateForm(
        klingForm({
          klingElements: [
            { name: 'kaori', description: '', images: IMAGES, ...element },
          ],
        }),
        null,
        null,
        KLING,
      )['kling_elements.0']

    expect(check({})).toBeUndefined()
    expect(check({ name: '  ' })).toContain('要素名を入力')
    expect(check({ name: 'kaori san' })).toContain('書けません')
    expect(check({ images: IMAGES.slice(0, 1) })).toContain('2〜4 枚')
    expect(check({ images: [...IMAGES, ...IMAGES, '/library/image/e.png'] }))
      .toContain('2〜4 枚')

    const two = [
      { name: 'kaori', description: '', images: IMAGES },
      { name: 'kaori', description: '', images: IMAGES },
    ]
    expect(
      validateForm(klingForm({ klingElements: two }), null, null, KLING)[
        'kling_elements.1'
      ],
    ).toContain('重複しています')
    expect(
      validateForm(
        klingForm({ klingElements: [...two, ...two] }),
        null,
        null,
        KLING,
      ).kling_elements,
    ).toContain('3 要素までです')
  })

  it('過去ジョブの params から復元する', () => {
    const { patch } = formStateFromParams(
      {
        kling_elements: [
          { name: 'kaori', description: 'grey coat', images: IMAGES },
          // 名前の無い行は落とす
          { images: IMAGES },
        ],
      },
      null,
    )
    expect(patch.klingElements).toEqual([
      { name: 'kaori', description: 'grey coat', images: IMAGES },
    ])
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

/** Suno は kie.ai 経由の音声ワークフロー（選択式つき・尺の宣言なし）。 */
const SUNO = workflow({
  id: 'suno_v5',
  label: 'Suno V5',
  kind: 'audio',
  family: 'suno',
  backend: 'kie',
  supports: ['prompt', 'lyrics', 'negative_tags'],
  min_duration: 0,
  max_duration: 0,
  default_duration: 0,
  selects: [
    {
      name: 'model',
      label: 'モデル',
      choices: ['V5', 'V5_5', 'V4_5PLUS'],
      default: 'V5',
      auto: false,
      hint: '',
    },
    {
      name: 'duration',
      label: '尺（秒）',
      choices: ['auto', '30', '60', '120', '180', '240', '300', '360'],
      default: 'auto',
      auto: false,
      hint: '',
    },
    {
      name: 'vocal_gender',
      label: 'ボーカルの性別',
      choices: ['auto', 'm', 'f'],
      default: 'auto',
      auto: false,
      hint: '',
    },
  ],
  // 尺は V5_5 のときだけ効く（他のモデルでは API が黙って無視する）
  select_requires: { duration: ['model', 'V5_5'] },
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

describe('megapixelsFor', () => {
  it('uses the workflow default when it declares one (MiniMax H3 = 0.4MP)', () => {
    const h3 = workflow({ id: 'minimax_h3_i2v_turbo', default_megapixels: 0.4 })
    expect(megapixelsFor(h3)).toBe(0.4)
  })

  it('falls back to the global default without a declaration', () => {
    expect(megapixelsFor(T2V)).toBe(DEFAULT_MEGAPIXELS)
    expect(megapixelsFor(workflow({ default_megapixels: 0 }))).toBe(
      DEFAULT_MEGAPIXELS,
    )
    expect(megapixelsFor(null)).toBe(DEFAULT_MEGAPIXELS)
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

  it('sends the style, the lyrics, the negative tags and the selects (Suno)', () => {
    const payload = audioJobPayload(
      audioForm({
        audioWorkflow: SUNO.id,
        lyrics: '[Verse 1]\nhello',
        negativeTags: 'screaming, distorted guitar',
        bpm: 92,
        keyscale: 'F# minor',
        selects: { model: 'V5_5', vocal_gender: 'f' },
      }),
      SUNO,
    )
    expect(payload).toMatchObject({
      audio_workflow: SUNO.id,
      audio_prompt: 'a warm lofi loop',
      lyrics: '[Verse 1]\nhello',
      negative_tags: 'screaming, distorted guitar',
      selects: { model: 'V5_5', vocal_gender: 'f' },
    })
    // Suno が読まないつまみは送らない（ACE-Step 専用）
    expect(payload).not.toHaveProperty('bpm')
    expect(payload).not.toHaveProperty('keyscale')
    expect(payload).not.toHaveProperty('language')
  })

  it('尺は V5_5 のときだけ指定できる（他のモデルでは黙って無視される）', () => {
    const form = audioForm({
      audioWorkflow: SUNO.id,
      selects: { model: 'V5', duration: '120' },
    })
    expect(validateForm(form, null, SUNO).duration).toContain(
      'モデルが V5_5 のときだけ効きます',
    )

    // V5_5 なら通る
    expect(
      validateForm(
        { ...form, selects: { model: 'V5_5', duration: '120' } }, null, SUNO,
      ),
    ).toEqual({})
    // 既定（auto / 未指定）のままならモデルは何でもよい
    expect(
      validateForm({ ...form, selects: { model: 'V5' } }, null, SUNO),
    ).toEqual({})
    expect(
      validateForm(
        { ...form, selects: { model: 'V4_5PLUS', duration: 'auto' } }, null, SUNO,
      ),
    ).toEqual({})
    // モデル未指定なら既定（V5）で判定する
    expect(
      validateForm({ ...form, selects: { duration: '60' } }, null, SUNO).duration,
    ).toContain('V5_5')
  })

  it('leaves the negative tags and the selects out for ACE-Step', () => {
    const payload = audioJobPayload(
      audioForm({
        audioWorkflow: ACE.id,
        negativeTags: 'screaming',
        selects: { model: 'V5_5' },
        audioDuration: 120,
      }),
      ACE,
    )
    expect(payload).not.toHaveProperty('negative_tags')
    expect(payload).not.toHaveProperty('selects')
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

  it('carries the picked audio model, and only when it differs', () => {
    const audioSlot: ModelSlot = {
      key: `${ACE.id}/1.ckpt_name`,
      workflow_id: ACE.id,
      workflow_label: 'ACE-Step',
      kind: 'audio',
      node_id: '1',
      field: 'ckpt_name',
      class_type: 'CheckpointLoaderSimple',
      label: 'Load Checkpoint',
      default: 'ace.safetensors',
      choices: ['ace.safetensors', 'ace-alt.safetensors'],
    }
    const picked = audioJobPayload(
      audioForm({
        audioWorkflow: ACE.id,
        modelOverrides: { [audioSlot.key]: 'ace-alt.safetensors' },
      }),
      ACE,
      [audioSlot],
    )
    expect(picked.model_overrides).toEqual({ [audioSlot.key]: 'ace-alt.safetensors' })
    // 既定値のままなら送らない
    expect(
      audioJobPayload(audioForm({ audioWorkflow: ACE.id }), ACE, [audioSlot]),
    ).not.toHaveProperty('model_overrides')
  })

  it('passes an explicit seed only when it is locked', () => {
    expect(audioJobPayload(audioForm({ seedLocked: true, seed: 7 }), ACE).seed).toBe(7)
    expect(
      audioJobPayload(audioForm({ seedLocked: false, seed: 7 }), ACE).seed,
    ).toBeNull()
  })
})

// ------------------------------------------- 実行時のモデル切り替え（SPEC §3.3）

function slot(overrides: Partial<ModelSlot> = {}): ModelSlot {
  return {
    key: 'krea2_turbo/30:10.unet_name',
    workflow_id: 'krea2_turbo',
    workflow_label: 'Krea 2',
    kind: 'image',
    node_id: '30:10',
    field: 'unet_name',
    class_type: 'UNETLoader',
    label: 'Load Diffusion Model',
    default: 'base.safetensors',
    choices: ['base.safetensors', 'alt.safetensors'],
    ...overrides,
  }
}

const IMAGE_SLOT = slot()
const VIDEO_SLOT = slot({
  key: 'id_lora/340:317.ckpt_name',
  workflow_id: 'id_lora',
  kind: 'video',
  node_id: '340:317',
  field: 'ckpt_name',
  default: 'ltx.safetensors',
  choices: ['ltx.safetensors', 'ltx-alt.safetensors'],
})
const SLOTS = [IMAGE_SLOT, VIDEO_SLOT]

function modelForm(overrides: Partial<FormState> = {}): FormState {
  return {
    ...initialForm,
    imageWorkflow: 'krea2_turbo',
    videoWorkflow: 'id_lora',
    ...overrides,
  }
}

describe('jobWorkflowIds', () => {
  it('lists the workflows the mode actually runs', () => {
    expect(jobWorkflowIds(modelForm({ mode: 'full' }))).toEqual([
      'krea2_turbo',
      'id_lora',
    ])
    expect(jobWorkflowIds(modelForm({ mode: 'image_only' }))).toEqual(['krea2_turbo'])
    expect(jobWorkflowIds(modelForm({ mode: 'i2v' }))).toEqual(['id_lora'])
    expect(jobWorkflowIds(modelForm({ mode: 'audio' }))).toEqual([
      initialForm.audioWorkflow,
    ])
  })
})

describe('modelSlotsForJob', () => {
  it('keeps only the slots of the given workflows', () => {
    expect(modelSlotsForJob(SLOTS, ['id_lora'])).toEqual([VIDEO_SLOT])
    expect(modelSlotsForJob(SLOTS, [])).toEqual([])
    expect(modelSlotsForJob(undefined, ['krea2_turbo'])).toEqual([])
  })
})

describe('jobModelOverrides', () => {
  it('sends the picked model only when it differs from the default', () => {
    const form = modelForm({
      mode: 'full',
      modelOverrides: {
        [IMAGE_SLOT.key]: 'alt.safetensors',
        [VIDEO_SLOT.key]: VIDEO_SLOT.default,
      },
    })
    expect(jobModelOverrides(form, SLOTS, jobWorkflowIds(form))).toEqual({
      [IMAGE_SLOT.key]: 'alt.safetensors',
    })
  })

  it('drops the slots of a workflow this mode does not run', () => {
    const form = modelForm({
      mode: 'image_only',
      modelOverrides: {
        [IMAGE_SLOT.key]: 'alt.safetensors',
        [VIDEO_SLOT.key]: 'ltx-alt.safetensors',
      },
    })
    expect(jobModelOverrides(form, SLOTS, jobWorkflowIds(form))).toEqual({
      [IMAGE_SLOT.key]: 'alt.safetensors',
    })
  })

  it('ignores a value that is no longer a candidate', () => {
    const form = modelForm({
      mode: 'image_only',
      modelOverrides: { [IMAGE_SLOT.key]: 'removed.safetensors' },
    })
    expect(jobModelOverrides(form, SLOTS, jobWorkflowIds(form))).toEqual({})
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

// -------------------------------------------- 選択式フィールド（SPEC §3.1）

function select(overrides: Partial<WorkflowSelect> = {}): WorkflowSelect {
  return {
    name: 'dance_style',
    label: '踊りの種類',
    choices: ['K-Pop 韩舞', 'Street Dance 街舞'],
    default: 'K-Pop 韩舞',
    auto: false,
    hint: '',
    ...overrides,
  }
}

const DANCE = select()
const LENGTH = select({
  name: 'duration',
  label: '尺（秒）',
  choices: ['5', '10', '15'],
  default: '15',
  auto: true,
})
const WAN = workflow({
  id: 'wan_dancer',
  requires: ['image', 'audio'],
  accepts_start_image: true,
  prompt_required: false,
  accepts_video_loras: false,
  selects: [DANCE, LENGTH],
})

describe('jobSelects', () => {
  it('宣言された項目の選んだ値だけを送る', () => {
    const form = {
      ...initialForm,
      selects: { dance_style: 'Street Dance 街舞', duration: '10' },
    }
    expect(jobSelects(form, WAN)).toEqual({
      dance_style: 'Street Dance 街舞',
      duration: '10',
    })
  })

  it('未指定（空文字）は送らない — 既定値や自動決定に任せる', () => {
    const form = { ...initialForm, selects: { dance_style: '', duration: '' } }
    expect(jobSelects(form, WAN)).toEqual({})
  })

  it('ワークフローが宣言していない項目と選択肢外の値は落とす', () => {
    const form = {
      ...initialForm,
      selects: { dance_style: 'Tango', motion_amplitude: 'high 高' },
    }
    expect(jobSelects(form, WAN)).toEqual({})
    // 選択式を持たないワークフローには何も送らない
    expect(
      jobSelects({ ...initialForm, selects: { dance_style: 'K-Pop 韩舞' } }, ID_LORA),
    ).toEqual({})
    expect(jobSelects({ ...initialForm, selects: {} }, null)).toEqual({})
  })

  it('画像ステージの選択項目も同じ辞書に混ぜて送る（SPEC §5.4）', () => {
    // gpt-image-2 は画像ワークフロー側で大きさ・品質を宣言する
    const GPT_IMAGE = workflow({
      id: 'gpt_image2',
      kind: 'image',
      family: 'gpt-image',
      accepts_video_loras: false,
      selects: [
        select({ name: 'size', label: '大きさ', choices: ['1024x1024', '1536x1024'], default: '1024x1024' }),
        select({ name: 'quality', label: '品質', choices: ['low', 'medium', 'high'], default: 'medium' }),
      ],
    })
    const form = {
      ...initialForm,
      selects: { size: '1536x1024', duration: '10' },
    }

    // full モードは 2 段とも走るので、両方の宣言が効く
    expect(jobSelects(form, WAN, GPT_IMAGE)).toEqual({
      duration: '10',
      size: '1536x1024',
    })
    // 画像だけのモードでは動画側を渡さない（持ち越しの値を送らない）
    expect(jobSelects(form, null, GPT_IMAGE)).toEqual({ size: '1536x1024' })
  })
})

describe('hiddenFields と LoRA チェーン', () => {
  it('LoRA チェーンを持たないワークフローでは動画 LoRA 欄を出さない', () => {
    const hidden = hiddenFields('i2v', WAN)
    expect(hidden.videoLoras).toBe(true)
    expect(hidden.videoTrigger).toBe(true)
    // 従来のワークフローは今までどおり出す
    expect(hiddenFields('i2v', ID_LORA).videoLoras).toBe(false)
  })

  it('選択肢が未取得（/api/options 前）でも欄は消さない', () => {
    expect(hiddenFields('i2v', null).videoLoras).toBe(false)
  })
})

// ------------------------------------------- 過去ジョブからのフォーム復元
// job.params（POST /api/jobs の内容そのもの）→ FormState。

function registered(overrides: Partial<Lora> = {}): Lora {
  return {
    id: 1,
    display_name: 'サクラ',
    lora_name: 'sakura.safetensors',
    trigger_word: 'sakura',
    default_strength: 1,
    default_audio: null,
    sort_order: 0,
    target: 'image',
    family: 'krea2',
    sample_images: [],
    ...overrides,
  }
}

const SAKURA = registered()
const SLOWMO = registered({
  id: 2,
  display_name: 'スローモ',
  lora_name: 'slowmo.safetensors',
  trigger_word: 'slowmo',
  target: 'video',
  default_strength: 0.8,
})

function restoreOptions(overrides: Partial<Options> = {}): Options {
  return {
    comfy_connected: true,
    comfy_error: null,
    comfy_target: 'local',
    comfy_url: '',
    image_workflows: [KREA2, ANIMA, QWEN],
    video_workflows: [T2V, I2V, ID_LORA, WAN],
    audio_workflows: [ACE, SA3],
    default_video_workflow: ID_LORA.id,
    default_image_workflow: KREA2.id,
    default_audio_workflow: ACE.id,
    audio_categories: [],
    keyscales: [],
    languages: [],
    aspect_ratios: ['4:3 (Standard)', '16:9 (Wide)'],
    lora_files: [],
    model_slots: [],
    model_files: {},
    loras: [SAKURA, SLOWMO],
    audio_assets: [],
    image_assets: [],
    video_assets: [],
    library: [],
    negative_presets: {
      current: DEFAULT_NEGATIVE_PROMPT,
      author: AUTHOR_NEGATIVE_PROMPT,
    },
    ...overrides,
  }
}

const OPTIONS = restoreOptions()

/** 復元は初期値の上に重ねて使うので、テストも完成した FormState で見る。 */
function restored(
  params: Record<string, unknown>,
  options: Options | null = OPTIONS,
): FormState {
  return { ...initialForm, ...formStateFromParams(params, options).patch }
}

describe('formStateFromParams — 画像＋動画ジョブ', () => {
  const params = {
    mode: 'full',
    image_workflow: KREA2.id,
    video_workflow: ID_LORA.id,
    aspect_ratio: '16:9 (Wide)',
    megapixels: 2,
    image_prompt: 'a cat',
    video_prompt: 'the cat walks',
    negative_prompt: AUTHOR_NEGATIVE_PROMPT,
    loras: [],
    trigger_text: '',
    video_loras: [],
    video_trigger_text: '',
    duration: 8,
    fps: 30,
    audio_path: '/assets/audio/a.wav',
    source_image: '',
    end_image: '',
    reference_video: '',
    selects: { dance_style: 'Street Dance 街舞' },
    model_overrides: { 'krea2_turbo/30:10.unet_name': 'alt.safetensors' },
    seed: 4242,
    image_seed: 4242,
    video_seeds: [4242, 4242],
    audio_seed: 4242,
  }

  it('そのジョブを再現できるフォーム状態に戻す', () => {
    expect(restored(params)).toMatchObject({
      mode: 'full',
      imageWorkflow: KREA2.id,
      videoWorkflow: ID_LORA.id,
      aspectRatio: '16:9 (Wide)',
      megapixels: 2,
      imagePrompt: 'a cat',
      videoPrompt: 'the cat walks',
      negativePrompt: AUTHOR_NEGATIVE_PROMPT,
      duration: 8,
      fps: 30,
      audioPath: '/assets/audio/a.wav',
      selects: { dance_style: 'Street Dance 街舞' },
      modelOverrides: { 'krea2_turbo/30:10.unet_name': 'alt.safetensors' },
    })
  })

  it('動画ジョブの尺は動画側のつまみに入る（音声側は初期値のまま）', () => {
    const form = restored(params)
    expect(form.duration).toBe(8)
    expect(form.audioDuration).toBe(initialForm.audioDuration)
  })

  it('params に無い項目は初期値のまま', () => {
    const form = restored({ mode: 'full', image_prompt: 'a cat' })
    expect(form.videoPrompt).toBe(initialForm.videoPrompt)
    expect(form.fps).toBe(initialForm.fps)
    expect(form.seedLocked).toBe(false)
    expect(form.seed).toBe(initialForm.seed)
  })

  it('選択肢から消えたワークフロー / アスペクト比は復元しない', () => {
    const form = restored({
      mode: 'full',
      image_workflow: 'retired_workflow',
      aspect_ratio: '1:1 (Square)',
    })
    expect(form.imageWorkflow).toBe(initialForm.imageWorkflow)
    expect(form.aspectRatio).toBe(initialForm.aspectRatio)
  })

  it('選択肢が未取得なら記録どおりに戻す', () => {
    const form = restored({ image_workflow: 'krea2_turbo', aspect_ratio: '1:1' }, null)
    expect(form.imageWorkflow).toBe('krea2_turbo')
    expect(form.aspectRatio).toBe('1:1')
  })
})

describe('formStateFromParams — 動画のみ / 音声ジョブ', () => {
  it('i2v の入力素材を戻す', () => {
    const form = restored({
      mode: 'i2v',
      video_workflow: I2V.id,
      video_prompt: 'pan right',
      source_image: '/assets/image/start.png',
      end_image: '/assets/image/end.png',
      reference_video: '/assets/video/ref.mp4',
      duration: 5,
      fps: 24,
    })
    expect(form).toMatchObject({
      mode: 'i2v',
      videoWorkflow: I2V.id,
      sourceImage: '/assets/image/start.png',
      endImage: '/assets/image/end.png',
      referenceVideo: '/assets/video/ref.mp4',
      duration: 5,
      fps: 24,
    })
  })

  it('記録されている絶対パスは配信 URL に直して戻す', () => {
    // params に入るのは解決済みの絶対パス。そのまま戻すとプレビューの
    // <img src> がファイルシステムのパスを取りに行ってしまう。
    const form = restored({
      mode: 'i2v',
      video_workflow: I2V.id,
      source_image: '/home/kohei/workspace/video-studio/library/image/kaori_015.png',
      end_image: '/home/kohei/workspace/video-studio/assets/image/end.png',
      reference_video: '/home/kohei/workspace/video-studio/outputs/j1/vid.mp4',
      audio_path: '/home/kohei/workspace/video-studio/library/audio/bgm.wav',
      reference_images: ['/mnt/other/video-studio/library/image/a.png'],
      kling_elements: [
        {
          name: 'cat',
          description: '',
          images: ['/mnt/other/video-studio/library/image/b.png'],
        },
      ],
    })
    expect(form).toMatchObject({
      sourceImage: '/library/image/kaori_015.png',
      endImage: '/assets/image/end.png',
      referenceVideo: '/outputs/j1/vid.mp4',
      audioPath: '/library/audio/bgm.wav',
      referenceImages: ['/library/image/a.png'],
    })
    expect(form.klingElements[0].images).toEqual(['/library/image/b.png'])
  })

  it('すでに URL のもの・置き場に当たらないものはそのまま', () => {
    const form = restored({
      mode: 'i2v',
      video_workflow: I2V.id,
      source_image: '/library/image/a.png',
      end_image: 'image/legacy.png',
    })
    expect(form).toMatchObject({
      sourceImage: '/library/image/a.png',
      endImage: 'image/legacy.png',
    })
  })

  it('音声ジョブの尺は audioDuration に入る（動画側は初期値のまま）', () => {
    const form = restored({
      mode: 'audio',
      audio_workflow: ACE.id,
      audio_prompt: 'a warm lofi loop',
      lyrics: '[Verse 1]\nhello',
      duration: 180,
      bpm: 92,
      keyscale: 'F# minor',
      language: 'ja',
      audio_category: 'SFX',
      reprompt: true,
      audio_seed: 7,
      seed: 7,
    })
    expect(form).toMatchObject({
      mode: 'audio',
      audioWorkflow: ACE.id,
      audioPrompt: 'a warm lofi loop',
      lyrics: '[Verse 1]\nhello',
      audioDuration: 180,
      bpm: 92,
      keyscale: 'F# minor',
      language: 'ja',
      audioCategory: 'SFX',
      reprompt: true,
      seed: 7,
      seedLocked: true,
    })
    expect(form.duration).toBe(initialForm.duration)
  })
})

describe('formStateFromParams — LoRA の再水和', () => {
  it('登録簿から id と表示名を引き当て、強度はジョブの値を使う', () => {
    const { patch, missingLoras } = formStateFromParams(
      {
        mode: 'full',
        loras: [
          { lora_name: SAKURA.lora_name, trigger_word: 'sakura', strength: 0.65 },
        ],
        video_loras: [
          { lora_name: SLOWMO.lora_name, trigger_word: 'slowmo', strength: 1 },
        ],
      },
      OPTIONS,
    )
    expect(missingLoras).toEqual([])
    expect(patch.loras).toEqual([
      {
        id: SAKURA.id,
        display_name: SAKURA.display_name,
        lora_name: SAKURA.lora_name,
        trigger_word: 'sakura',
        strength: 0.65,
      },
    ])
    expect(patch.videoLoras).toEqual([
      {
        id: SLOWMO.id,
        display_name: SLOWMO.display_name,
        lora_name: SLOWMO.lora_name,
        trigger_word: 'slowmo',
        strength: 1,
      },
    ])
  })

  it('登録簿に無い LoRA は落として名前を返す', () => {
    const { patch, missingLoras } = formStateFromParams(
      {
        mode: 'full',
        loras: [
          { lora_name: 'gone.safetensors', trigger_word: 'gone', strength: 1 },
          { lora_name: SAKURA.lora_name, trigger_word: 'sakura', strength: 1 },
        ],
        video_loras: [
          { lora_name: 'also-gone.safetensors', trigger_word: '', strength: 1 },
        ],
      },
      OPTIONS,
    )
    expect(missingLoras).toEqual(['gone.safetensors', 'also-gone.safetensors'])
    expect(patch.loras?.map((l) => l.lora_name)).toEqual([SAKURA.lora_name])
    expect(patch.videoLoras).toEqual([])
  })

  it('自動生成と同じトリガー語なら dirty にしない', () => {
    const form = restored({
      mode: 'full',
      loras: [{ lora_name: SAKURA.lora_name, trigger_word: 'sakura', strength: 1 }],
      trigger_text: 'sakura',
      video_loras: [],
      video_trigger_text: '',
    })
    expect(form.triggerText).toBe('sakura')
    expect(form.triggerDirty).toBe(false)
    expect(form.videoTriggerDirty).toBe(false)
  })

  it('手で書き換えたトリガー語は dirty にして LoRA 操作で消させない', () => {
    const form = restored({
      mode: 'full',
      loras: [{ lora_name: SAKURA.lora_name, trigger_word: 'sakura', strength: 1 }],
      trigger_text: 'sakura, best quality',
      video_loras: [{ lora_name: SLOWMO.lora_name, trigger_word: 'slowmo', strength: 1 }],
      video_trigger_text: '',
    })
    expect(form.triggerDirty).toBe(true)
    expect(form.videoTriggerText).toBe('')
    expect(form.videoTriggerDirty).toBe(true)
  })
})

describe('formStateFromParams — ネガティブのプリセット判定', () => {
  it('プリセットと一致すればその名前を選ぶ', () => {
    expect(restored({ negative_prompt: DEFAULT_NEGATIVE_PROMPT }).negativePreset).toBe(
      'current',
    )
    expect(restored({ negative_prompt: AUTHOR_NEGATIVE_PROMPT }).negativePreset).toBe(
      'author',
    )
  })

  it('サーバーが返すプリセット（template 等）も見る', () => {
    const options = restoreOptions({
      negative_presets: { template: 'worst quality', current: DEFAULT_NEGATIVE_PROMPT },
    })
    expect(restored({ negative_prompt: 'worst quality' }, options).negativePreset).toBe(
      'template',
    )
  })

  it('どれとも違えば custom', () => {
    const form = restored({ negative_prompt: 'blurry, extra fingers' })
    expect(form.negativePreset).toBe('custom')
    expect(form.negativePrompt).toBe('blurry, extra fingers')
  })
})

describe('formStateFromParams — シード', () => {
  it('記録されたシードを固定状態で戻す（再実行と違い同じ絵を狙う）', () => {
    const form = restored({ mode: 'full', seed: 12345 })
    expect(form.seed).toBe(12345)
    expect(form.seedLocked).toBe(true)
  })

  it('seed が無ければ段階ごとの記録から拾う', () => {
    expect(restored({ mode: 'full', video_seeds: [99, 99] })).toMatchObject({
      seed: 99,
      seedLocked: true,
    })
    expect(restored({ mode: 'audio', audio_seed: 5 })).toMatchObject({
      seed: 5,
      seedLocked: true,
    })
  })

  it('シードの記録が無ければ固定しない', () => {
    const form = restored({ mode: 'full' })
    expect(form.seedLocked).toBe(false)
  })
})

describe('formStateFromParams — 壊れた params', () => {
  it('型の合わない値は無視して初期値のままにする', () => {
    const form = restored({
      mode: 'nonsense',
      megapixels: 'big',
      fps: null,
      selects: ['not', 'a', 'map'],
      model_overrides: { ok: 'a.safetensors', bad: 3 },
      loras: 'nope',
      seed: 'x',
    })
    expect(form.mode).toBe(initialForm.mode)
    expect(form.megapixels).toBe(initialForm.megapixels)
    expect(form.fps).toBe(initialForm.fps)
    expect(form.selects).toEqual({})
    expect(form.modelOverrides).toEqual({ ok: 'a.safetensors' })
    expect(form.loras).toEqual([])
    expect(form.seedLocked).toBe(false)
  })
})


describe('リファレンスシート（SPEC §7.2）', () => {
  it('シートを入力に取る動画ワークフローだけを見分ける', () => {
    expect(needsReferenceSheet(workflow({ id: 'ltx2_3_ic_lora_image' }))).toBe(true)
    expect(needsReferenceSheet(T2V)).toBe(false)
    expect(needsReferenceSheet(null)).toBe(false)
    expect(needsReferenceSheet(undefined)).toBe(false)
  })

  it('アスペクト比のプリセットから、長辺 1280px のシートの大きさを決める', () => {
    expect(sheetSize('16:9 (Widescreen)')).toEqual({ width: 1280, height: 720 })
    expect(sheetSize('9:16 (Portrait Widescreen)')).toEqual({
      width: 720,
      height: 1280,
    })
    expect(sheetSize('1:1 (Square)')).toEqual({ width: 1280, height: 1280 })
    // 8 の倍数に丸める（4:3 → 1280x960）
    expect(sheetSize('4:3 (Standard)')).toEqual({ width: 1280, height: 960 })
  })

  it('読めないアスペクト比は既定の 1280x720 にする', () => {
    expect(sheetSize('')).toEqual({ width: 1280, height: 720 })
    expect(sheetSize('わからない')).toEqual({ width: 1280, height: 720 })
    expect(sheetSize('0:0')).toEqual({ width: 1280, height: 720 })
  })
})

// -------------------------------------------------------- ステップ数（§3.1）

const STEPS_VIDEO = workflow({
  id: 'minimax_h3_i2v_turbo',
  requires: ['image'],
  accepts_start_image: true,
  supports: ['prompt', 'width', 'height', 'duration', 'fps', 'steps'],
})
const STEPS_IMAGE = workflow({
  id: 'krea2_turbo_steps',
  kind: 'image',
  family: 'krea2',
  supports: ['aspect_ratio', 'megapixels', 'prompt', 'seed', 'steps'],
})
const STEPS_AUDIO = workflow({
  ...SA3,
  supports: [...SA3.supports, 'steps'],
})

describe('steps', () => {
  it('宣言しているワークフローのときだけ欄を出す', () => {
    expect(hiddenFields('i2v', STEPS_VIDEO).steps).toBe(false)
    expect(hiddenFields('i2v', I2V).steps).toBe(true)
    // full は画像・動画のどちらかが宣言していれば出す
    expect(hiddenFields('full', I2V, STEPS_IMAGE).steps).toBe(false)
    expect(hiddenFields('image_only', I2V, STEPS_IMAGE).steps).toBe(false)
    expect(hiddenFields('image_only', I2V, KREA2).steps).toBe(true)
    // 動画側だけの宣言は、画像だけ走る mode では出さない
    expect(hiddenFields('image_only', STEPS_VIDEO, KREA2).steps).toBe(true)
  })

  it('走るワークフローが宣言しているときだけ送る（未指定の 0 は送らない）', () => {
    const form = { ...initialForm, steps: 12 }
    expect(jobSteps(form, STEPS_VIDEO)).toBe(12)
    expect(jobSteps(form, null, STEPS_IMAGE)).toBe(12)
    expect(jobSteps(form, I2V, KREA2)).toBe(0)
    expect(jobSteps({ ...initialForm, steps: 0 }, STEPS_VIDEO)).toBe(0)
  })

  it('音声ジョブでも宣言しているワークフローのときだけ送る', () => {
    const form = audioForm({ audioWorkflow: STEPS_AUDIO.id, steps: 24 })
    expect(audioJobPayload(form, STEPS_AUDIO)).toMatchObject({ steps: 24 })
    expect(audioJobPayload(form, SA3)).not.toHaveProperty('steps')
    expect(
      audioJobPayload({ ...form, steps: 0 }, STEPS_AUDIO),
    ).not.toHaveProperty('steps')
  })

  it('過去ジョブの params から復元する', () => {
    expect(formStateFromParams({ steps: 16 }, null).patch.steps).toBe(16)
    // 古いジョブには無いので、無ければ触らない（initialForm の 0 のまま）
    expect(formStateFromParams({}, null).patch).not.toHaveProperty('steps')
  })
})
