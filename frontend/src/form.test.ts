import { describe, expect, it } from 'vitest'
import { disabledFields, workflowsForMode } from './form'
import type { WorkflowOption } from './types'

function workflow(overrides: Partial<WorkflowOption> = {}): WorkflowOption {
  return {
    id: 'w',
    label: 'W',
    kind: 'video',
    notes: '',
    requires: [],
    supports: ['prompt', 'negative', 'width', 'height', 'duration', 'fps'],
    accepts_start_image: false,
    image_label: '開始フレーム',
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

describe('disabledFields', () => {
  it('greys out the image side for a standalone video job', () => {
    const disabled = disabledFields('i2v', ID_LORA)
    expect(disabled.imagePrompt).toBe(true)
    expect(disabled.loras).toBe(true)
    expect(disabled.trigger).toBe(true)
    expect(disabled.videoPrompt).toBe(false)
  })

  it('greys out the video side for an image-only job', () => {
    const disabled = disabledFields('image_only', ID_LORA)
    expect(disabled.videoPrompt).toBe(true)
    expect(disabled.negative).toBe(true)
    expect(disabled.audio).toBe(true)
    expect(disabled.duration).toBe(true)
    expect(disabled.fps).toBe(true)
    expect(disabled.startImage).toBe(true)
    expect(disabled.endImage).toBe(true)
    expect(disabled.referenceVideo).toBe(true)
  })

  it('enables audio only for workflows that take it', () => {
    expect(disabledFields('i2v', ID_LORA).audio).toBe(false)
    expect(disabledFields('i2v', I2V).audio).toBe(true)
    expect(disabledFields('full', ID_LORA).audio).toBe(false)
  })

  it('asks for a start frame only when the workflow needs one', () => {
    expect(disabledFields('i2v', I2V).startImage).toBe(false)
    expect(disabledFields('i2v', T2V).startImage).toBe(true)
    // full generation produces the start frame itself
    expect(disabledFields('full', I2V).startImage).toBe(true)
  })

  it('shows the extra inputs each workflow declares', () => {
    expect(disabledFields('i2v', FLF2V).endImage).toBe(false)
    expect(disabledFields('full', FLF2V).endImage).toBe(false)
    expect(disabledFields('i2v', I2V).endImage).toBe(true)
    expect(disabledFields('i2v', MOTION).referenceVideo).toBe(false)
    expect(disabledFields('i2v', FLF2V).referenceVideo).toBe(true)
  })

  it('treats a not-yet-loaded workflow as offering nothing', () => {
    const disabled = disabledFields('i2v', null)
    expect(disabled.audio).toBe(true)
    expect(disabled.startImage).toBe(true)
    expect(disabled.duration).toBe(true)
  })
})
