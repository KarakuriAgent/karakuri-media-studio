/**
 * ジョブの所要時間（started_at 〜 finished_at）を表示用に整える小物。
 *
 * サーバは秒精度の ISO8601（`_now()`）で時刻を書くので、ここでも秒より
 * 細かい単位は扱わない。
 */
import type { JobStatus } from '../types'

/** これ以上は動かない状態（backend の jobs._TERMINAL_STATUSES と同じ）。 */
const FINAL_STATUSES: JobStatus[] = ['done', 'failed', 'canceled']

/** ジョブが終端に入っているか。 */
export function isJobFinished(status: JobStatus): boolean {
  return FINAL_STATUSES.includes(status)
}


/** ISO8601 の 2 点間のミリ秒。どちらかが欠けている／読めなければ null。 */
export function durationMs(
  start: string | null | undefined,
  end: string | null | undefined,
): number | null {
  if (!start || !end) return null
  const from = Date.parse(start)
  const to = Date.parse(end)
  if (Number.isNaN(from) || Number.isNaN(to)) return null
  const ms = to - from
  return ms < 0 ? null : ms
}

/**
 * ミリ秒を「45秒」「1分23秒」「1時間2分」のような日本語にする。
 *
 * 上位 2 単位までしか出さない（1 時間を超えたら秒は落とす）。0 の単位は
 * 省くので「2分」「1時間」のようにもなる。
 */
export function formatDuration(ms: number): string {
  if (!Number.isFinite(ms) || ms < 0) return ''
  const total = Math.floor(ms / 1000)
  const hours = Math.floor(total / 3600)
  const minutes = Math.floor((total % 3600) / 60)
  const seconds = total % 60
  if (hours > 0) return minutes > 0 ? `${hours}時間${minutes}分` : `${hours}時間`
  if (minutes > 0) return seconds > 0 ? `${minutes}分${seconds}秒` : `${minutes}分`
  return `${seconds}秒`
}

/** 実行中の経過表示用のタイムコード（`0:42` / `1:02:03`）。 */
export function formatElapsed(ms: number): string {
  if (!Number.isFinite(ms) || ms < 0) return '0:00'
  const total = Math.floor(ms / 1000)
  const hours = Math.floor(total / 3600)
  const minutes = Math.floor((total % 3600) / 60)
  const seconds = total % 60
  const ss = String(seconds).padStart(2, '0')
  if (hours > 0) return `${hours}:${String(minutes).padStart(2, '0')}:${ss}`
  return `${minutes}:${ss}`
}

/**
 * ジョブの所要時間の文字列。started_at / finished_at が揃っていなければ null
 * （＝何も出さない）。
 */
export function jobDurationLabel(job: {
  started_at?: string | null
  finished_at?: string | null
}): string | null {
  const ms = durationMs(job.started_at, job.finished_at)
  return ms === null ? null : formatDuration(ms)
}
