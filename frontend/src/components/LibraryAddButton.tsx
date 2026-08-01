import { useState } from 'react'
import { ApiError, api, formatDetail } from '../api'
import type {
  Job,
  LibraryCategory,
  LibraryCategoryValue,
  LibraryItem,
  LibrarySource,
} from '../types'
import { CATEGORY_LABELS, UNCATEGORIZED } from './LibraryPickerModal'

/** その出力が既にライブラリにあるか（`/api/options` の library から判定）。 */
export function isInLibrary(
  library: LibraryItem[] | undefined,
  job: Job,
  source: LibrarySource,
): boolean {
  return (library ?? []).some(
    (item) => item.source_job_id === job.id && item.source === source,
  )
}

/** ジョブが持っている出力と、その日本語ラベル（新しい順に並べる用）。 */
export function librarySourcesOf(job: Job): { source: LibrarySource; label: string }[] {
  const found: { source: LibrarySource; label: string }[] = []
  if (job.audio_output_url) found.push({ source: 'audio', label: '音声' })
  if (job.video_url) found.push({ source: 'video', label: '動画' })
  if (job.image_url) found.push({ source: 'image', label: '生成画像' })
  if (job.last_frame_url) found.push({ source: 'last_frame', label: 'ラストフレーム' })
  return found
}

/**
 * ジョブの出力をライブラリに取っておくボタン（SPEC §7.2）。
 *
 * 履歴を消すと生成物も消えるが、ライブラリに入れたものは残り、あとで入力素材と
 * して選べる。押したことが分かるように結果を短く出す。同じ出力が既に棚にあると
 * バックエンドが 409 を返すので、それは失敗ではなく「登録済みです」と伝える。
 */
export default function LibraryAddButton({
  job,
  source,
  label,
  registered = false,
  onAdded,
}: {
  job: Job
  source: LibrarySource
  /** ボタンに出す出力名（「生成画像」など。省略時は種別を書かない） */
  label?: string
  /** 既にライブラリにあると分かっている（押す前から「登録済み」を出す） */
  registered?: boolean
  onAdded?: () => void
}) {
  const [state, setState] = useState<
    'idle' | 'busy' | 'done' | 'exists' | 'failed'
  >('idle')
  const [error, setError] = useState<string | null>(null)
  // 登録と同時に付ける分類。既定は未分類（あとからライブラリで変えられる）。
  const [category, setCategory] = useState<LibraryCategoryValue>(UNCATEGORIZED)

  const add = async () => {
    setState('busy')
    setError(null)
    try {
      await api.addJobToLibrary(job.id, source, '', [], category)
      setState('done')
      onAdded?.()
      window.setTimeout(() => setState('idle'), 2000)
    } catch (caught) {
      // 409 = 同じ出力が既に棚にある。失敗ではないので赤くしない。
      if (caught instanceof ApiError && caught.status === 409) {
        setError(formatDetail(caught.detail))
        setState('exists')
        return
      }
      setError(caught instanceof Error ? caught.message : String(caught))
      setState('failed')
    }
  }

  // 押す前から分かっている登録済みも、409 を受けたのと同じ見た目にする。
  const shown = state === 'idle' && registered ? 'exists' : state
  const text =
    shown === 'done'
      ? '★ 登録しました'
      : shown === 'busy'
        ? '登録中…'
        : shown === 'exists'
          ? '★ 登録済みです'
          : shown === 'failed'
            ? '登録できません'
            : `☆ ライブラリに登録${label ? `: ${label}` : ''}`

  return (
    <span className="inline-flex items-center gap-1">
      <button
        className={`btn-ghost !py-1 text-xs ${
          shown === 'failed' ? '!text-red-300' : ''
        } ${shown === 'exists' ? '!text-slate-500' : ''}`}
        disabled={shown === 'busy' || shown === 'exists'}
        title={error ?? 'ライブラリに入れると、履歴を消しても素材として残ります'}
        onClick={() => void add()}
      >
        {text}
      </button>
      {/* 登録済み・登録直後は分類を変える先がライブラリ側なので出さない */}
      {(shown === 'idle' || shown === 'failed') && (
        <select
          className="field w-auto !py-1 text-xs"
          aria-label="登録するカテゴリ"
          value={category}
          onChange={(event) =>
            setCategory(event.target.value as LibraryCategoryValue)
          }
        >
          <option value={UNCATEGORIZED}>未分類</option>
          {(Object.keys(CATEGORY_LABELS) as LibraryCategory[]).map((value) => (
            <option key={value} value={value}>
              {CATEGORY_LABELS[value]}
            </option>
          ))}
        </select>
      )}
    </span>
  )
}
