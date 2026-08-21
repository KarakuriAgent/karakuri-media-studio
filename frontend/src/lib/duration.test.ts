import { describe, expect, it } from 'vitest'
import { durationMs, formatDuration, formatElapsed, jobDurationLabel } from './duration'

describe('formatDuration', () => {
  it('1 分未満は秒だけ', () => {
    expect(formatDuration(0)).toBe('0秒')
    expect(formatDuration(45_000)).toBe('45秒')
    expect(formatDuration(45_900)).toBe('45秒') // 端数は切り捨て
  })

  it('1 時間未満は分と秒', () => {
    expect(formatDuration(83_000)).toBe('1分23秒')
    expect(formatDuration(120_000)).toBe('2分')
  })

  it('1 時間以上は時間と分（秒は落とす）', () => {
    expect(formatDuration(3_723_000)).toBe('1時間2分')
    expect(formatDuration(3_600_000)).toBe('1時間')
  })

  it('負の値や NaN は空文字', () => {
    expect(formatDuration(-1)).toBe('')
    expect(formatDuration(Number.NaN)).toBe('')
  })
})

describe('formatElapsed', () => {
  it('分:秒 で出す', () => {
    expect(formatElapsed(0)).toBe('0:00')
    expect(formatElapsed(42_000)).toBe('0:42')
    expect(formatElapsed(62_000)).toBe('1:02')
  })

  it('1 時間を超えたら時:分:秒', () => {
    expect(formatElapsed(3_723_000)).toBe('1:02:03')
  })
})

describe('durationMs', () => {
  it('2 点の差を返す', () => {
    expect(durationMs('2026-08-22T10:00:00+00:00', '2026-08-22T10:01:23+00:00')).toBe(83_000)
  })

  it('欠けている・読めない・逆順なら null', () => {
    expect(durationMs(null, '2026-08-22T10:00:00+00:00')).toBeNull()
    expect(durationMs('2026-08-22T10:00:00+00:00', undefined)).toBeNull()
    expect(durationMs('nope', '2026-08-22T10:00:00+00:00')).toBeNull()
    expect(durationMs('2026-08-22T10:01:00+00:00', '2026-08-22T10:00:00+00:00')).toBeNull()
  })
})

describe('jobDurationLabel', () => {
  it('揃っていれば日本語の所要時間', () => {
    expect(
      jobDurationLabel({
        started_at: '2026-08-22T10:00:00+00:00',
        finished_at: '2026-08-22T10:01:23+00:00',
      }),
    ).toBe('1分23秒')
  })

  it('過去ジョブ（NULL）は null', () => {
    expect(jobDurationLabel({ started_at: null, finished_at: null })).toBeNull()
  })
})
