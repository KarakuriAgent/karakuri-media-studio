import { describe, expect, it } from 'vitest'

import type { TimelineFxEvent } from '../../types'
import { fxMainFields, fxRest } from './fx'

const item = (event: Record<string, unknown>): TimelineFxEvent =>
  ({ id: 'e1', enabled: true, event }) as TimelineFxEvent

describe('fxMainFields', () => {
  it('sprite と video には、書いていなくても cover と coverMargin を出す', () => {
    for (const type of ['sprite', 'video']) {
      const names = fxMainFields(item({ type, t: 0, src: 'a.png' })).map((f) => f.name)
      expect(names).toContain('cover')
      expect(names).toContain('coverMargin')
      expect(names).not.toContain('fitStart')
    }
  })

  it('imageSlam には fitStart を出し、cover は出さない', () => {
    const fields = fxMainFields(item({ type: 'imageSlam', t: 0, src: 'a.png' }))
    const fit = fields.find((f) => f.name === 'fitStart')
    expect(fit?.kind).toBe('boolean')
    expect(fields.map((f) => f.name)).not.toContain('cover')
  })

  it('ほかの型は、値を持っている項目だけ（前のまま）', () => {
    const names = fxMainFields(item({ type: 'card', t: 0, text: 'BAN' })).map((f) => f.name)
    expect(names).toEqual(['text'])
  })

  it('主要項目に出した cover は、残りの JSON に出さない', () => {
    const rest = fxRest(item({ type: 'sprite', t: 0, src: 'a.png', cover: true, motion: 'none' }))
    expect(rest).toEqual({ motion: 'none' })
  })
})
