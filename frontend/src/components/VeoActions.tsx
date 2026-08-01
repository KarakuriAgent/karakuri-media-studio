import { useEffect, useState } from 'react'
import type { Job } from '../types'

/**
 * 生成し終わった Veo の動画に掛けられる**追加操作**（SPEC §5.2 / issue #26）。
 *
 * 「続きを生成」（ラストフレームから別のクリップを作る）とは別物で、どちらも
 * kie.ai 側に残っている元タスクに仕事を足す:
 *
 * - **延長**: 元動画に +7 秒を継いだ 1 本が返る（続きの指示を書いて実行）
 * - **1080P を取得**: 720p で作った動画の高解像度版を取りに行く（5 credits、
 *   生成の 1〜3 分後に用意されるので、まだなら待ち直す）
 *
 * どちらを出せるかはバックエンドが決める（`job.followups`）ので、ここは
 * 判定を持たない。実行するとどちらも**新しいジョブ**になる。
 */
export default function VeoActions({
  job,
  busy,
  onExtend,
  onUpscale,
}: {
  job: Job
  busy: boolean
  onExtend: (job: Job, prompt: string) => void
  onUpscale: (job: Job) => void
}) {
  const followups = job.followups ?? []
  const [asking, setAsking] = useState(false)
  const [prompt, setPrompt] = useState('')

  // 別のジョブを選んだら書きかけを捨てる（前のジョブへの指示が残ると危ない）。
  useEffect(() => {
    setAsking(false)
    setPrompt('')
  }, [job.id])

  if (followups.length === 0) return null

  const submit = () => {
    const text = prompt.trim()
    if (!text) return
    setAsking(false)
    setPrompt('')
    onExtend(job, text)
  }

  return (
    <>
      {followups.includes('veo_extend') && (
        <button
          className="btn-ghost !py-1 text-xs"
          disabled={busy}
          title="元の動画に 7 秒を継ぎ足します（新しいジョブになります）"
          onClick={() => setAsking(true)}
        >
          延長（+7 秒）
        </button>
      )}
      {followups.includes('veo_1080p') && (
        <button
          className="btn-ghost !py-1 text-xs"
          disabled={busy}
          title="この動画の 1080P 版を取得します（5 クレジット・数分かかります）"
          onClick={() => onUpscale(job)}
        >
          1080P を取得
        </button>
      )}

      {asking && (
        <div
          className="fixed inset-0 z-40 flex items-center justify-center bg-black/70 p-6"
          onClick={() => setAsking(false)}
        >
          <div
            className="w-full max-w-lg rounded-lg border border-ink-600 bg-ink-800 p-4"
            onClick={(event) => event.stopPropagation()}
          >
            <h2 className="mb-1 text-sm font-semibold text-slate-100">
              動画を 7 秒延長する
            </h2>
            <p className="mb-3 text-xs text-slate-500">
              元の動画の続きに何が起きるかを書いてください（英語推奨）。元動画を
              含んだ 1 本の動画が、新しいジョブとして生成されます。
            </p>
            <textarea
              className="field h-32 w-full"
              autoFocus
              placeholder="She keeps walking toward the edge and the wind picks up."
              value={prompt}
              onChange={(event) => setPrompt(event.target.value)}
            />
            <div className="mt-3 flex justify-end gap-2">
              <button className="btn-ghost text-xs" onClick={() => setAsking(false)}>
                キャンセル
              </button>
              <button
                className="btn-primary text-xs"
                disabled={busy || !prompt.trim()}
                onClick={submit}
              >
                延長を実行
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  )
}
