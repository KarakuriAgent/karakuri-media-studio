import { useCallback, useEffect, useState } from 'react'
import { Loader2, RefreshCw } from 'lucide-react'

import { ApiError, api, formatDetail } from '../../api'
import type { StudioShot, StudioShotPreview } from '../../types'
import { Banner, CopyButton } from '../ui'
import { Badge } from '../ui/badge'
import { Button } from '../ui/button'

/** ワークフロー ID -> 画面に出す名前（`WORKFLOW_OVERRIDE_LABEL` の短い版）。 */
const WORKFLOW_LABEL: Record<string, string> = {
  minimax_h3_t2v: 't2v（文章だけから）',
  minimax_h3_i2v: 'i2v（開始フレームから）',
  minimax_h3_r2v: 'r2v（参照素材から）',
  minimax_h3_r2v_context: 'r2v + 連続カット（直前カットの続き）',
  // ラテント連続性が ON のときの通常カット（仕上がりは素の版と同じで、
  // 次のカットに渡す AV ラテントを残すぶんだけ違う）
  minimax_h3_t2v_save: 't2v（文章だけから・ラテント保存）',
  minimax_h3_i2v_save: 'i2v（開始フレームから・ラテント保存）',
  minimax_h3_r2v_save: 'r2v（参照素材から・ラテント保存）',
}

/** 添付ファイルのパスは長いので、ファイル名だけ見せる。 */
function fileName(path: string): string {
  return path.split('/').pop() || path
}

/**
 * 投入プレビュー: このカットを今生成したら**実際に何が投入されるか**。
 *
 * 隠れた合成（Camera: / Audio: 行、除外文、`@素材名` の展開、ワークフローの
 * 自動選択）を全部ここで見せる。組み立てはサーバー側の生成経路そのものなので、
 * ここに出るものと投入されるものは食い違わない。
 *
 * 取り直すのは保存のあと（`shot.updated_at` が動いたとき）と「再取得」ボタン。
 * 読み取り専用で、生成は起こさない。
 */
export default function PromptPreview({ shot }: { shot: StudioShot }) {
  const [preview, setPreview] = useState<StudioShotPreview | null>(null)
  const [loading, setLoading] = useState(false)
  const [failure, setFailure] = useState<string | null>(null)

  const shotId = shot.id
  const updatedAt = shot.updated_at

  const load = useCallback(async () => {
    setLoading(true)
    try {
      setPreview(await api.previewStudioShotPrompt(shotId))
      setFailure(null)
    } catch (error) {
      setFailure(
        error instanceof ApiError
          ? formatDetail(error.detail)
          : error instanceof Error
            ? error.message
            : String(error),
      )
    } finally {
      setLoading(false)
    }
  }, [shotId])

  useEffect(() => {
    void load()
    // 保存でサーバー側の値が動いたら取り直す。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [shotId, updatedAt])

  return (
    <div
      className="space-y-2 rounded-md border border-border bg-surface-sunken p-2"
      role="group"
      aria-label="投入プレビュー"
    >
      <div className="flex items-center gap-2">
        <h4 className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
          投入プレビュー
        </h4>
        {preview?.workflow && (
          <Badge variant="outline" className="bg-card px-1.5 py-0 text-[10px] font-normal">
            {WORKFLOW_LABEL[preview.workflow] ?? preview.workflow}
          </Badge>
        )}
        <div className="ml-auto flex items-center gap-1">
          {preview && preview.prompt && <CopyButton text={preview.prompt} />}
          <Button
            variant="outline"
            size="xs"
            onClick={() => void load()}
            disabled={loading}
          >
            {loading ? <Loader2 className="animate-spin" /> : <RefreshCw />}
            {loading ? '取得中…' : '再取得'}
          </Button>
        </div>
      </div>

      {failure && <Banner>{failure}</Banner>}

      {preview && preview.error && <Banner>{preview.error}</Banner>}

      {preview && !preview.error && (
        <>
          {preview.workflow_reason && (
            <p className="text-[11px] text-muted-foreground">
              {preview.workflow_reason}
            </p>
          )}
          <pre
            className="max-h-56 overflow-auto whitespace-pre-wrap break-words rounded-md border border-border bg-background/60 p-2 font-mono text-[11px] text-foreground/90"
            aria-label="投入される最終プロンプト"
          >
            {preview.prompt}
          </pre>
          {preview.references.length > 0 && (
            <div className="text-[11px] text-muted-foreground">
              <span className="text-muted-foreground/80">添付される参照素材:</span>
              <ul className="mt-1 space-y-0.5">
                {preview.references.map((reference) => (
                  <li key={`${reference.tag}-${reference.name}`}>
                    <code className="text-sky-300">{reference.tag}</code>{' '}
                    {reference.name}（{reference.kind}
                    {reference.path ? ` / ${fileName(reference.path)}` : ''}）
                  </li>
                ))}
              </ul>
            </div>
          )}
          {preview.start_frame && (
            <p className="text-[11px] text-muted-foreground">
              開始フレーム: {fileName(preview.start_frame)}
            </p>
          )}
          {preview.context_latent && (
            <p className="text-[11px] text-muted-foreground">
              ラテント引き継ぎ: 有効（引き継ぎ元ラテント:{' '}
              {fileName(preview.context_latent)}
              {preview.context_video
                ? ` / 引き継ぎ元動画: ${fileName(preview.context_video)}`
                : ''}
              ）
            </p>
          )}
          {preview.will_translate && (
            <p className="text-[11px] text-amber-300/90">
              投入時に英語へ自動変換されます（引用符の中の台詞と参照タグはそのまま）。
            </p>
          )}
        </>
      )}
    </div>
  )
}
