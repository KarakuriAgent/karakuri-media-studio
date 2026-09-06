import { Clapperboard, History, Music, Wand2 } from 'lucide-react'

import type { Job, LibrarySource } from '../../types'
import { Button } from '@/components/ui/button'
import LibraryAddButton from '../LibraryAddButton'
import { MODE_LABELS } from '../../form'
import { OUTPUT_LABELS, promptHead, timestampOf } from './library'

/** ジョブが実際に流したワークフロー（params から拾えたものだけ）。 */
function workflowOf(job: Job): string {
  const params = job.params ?? {}
  const names = ['video_workflow', 'image_workflow', 'audio_workflow']
    .map((key) => params[key])
    .filter((value): value is string => typeof value === 'string' && value !== '')
  return names.join(' / ')
}

/**
 * 生成履歴の成果物 1 ファイルの詳細（ファイルタブの「生成履歴」）。
 *
 * ライブラリ項目と違ってここでは何も直せない（履歴はジョブの記録なので）。
 * できるのは「棚に取っておく」「もう一度入力に使う」「元のジョブを開く」、
 * そして Take 由来なら「そのカットをスタジオで開く」。
 * 登録は既存の :file:`LibraryAddButton.tsx` をそのまま置く（同じ出力が既にあれば
 * サーバーが 409 を返し、ボタンが「登録済みです」に変わる）。
 */
export default function JobOutputDetail({
  job,
  output,
  url,
  kind,
  onUseInGenerate,
  onOpenJob,
  onOpenInStudio,
  onRegistered,
}: {
  job: Job
  /** ジョブのどの出力か（image / last_frame / video / audio）。 */
  output: LibrarySource
  /** その出力の配信 URL。 */
  url: string
  kind: 'image' | 'video' | 'audio'
  /** この出力を生成フォームの入力欄に入れて [生成] タブへ移る。 */
  onUseInGenerate?: () => void
  /** 元のジョブを [生成] タブで開く。 */
  onOpenJob?: () => void
  /** Take 由来のジョブなら、そのカットを [スタジオ] タブで開く。 */
  onOpenInStudio?: () => void
  /** ライブラリに登録できたら一覧を取り直してもらう。 */
  onRegistered?: () => void
}) {
  const workflow = workflowOf(job)
  const prompt =
    job.video_prompt || job.image_prompt || job.audio_prompt || job.user_input || ''

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-center overflow-hidden rounded-md border border-border bg-background">
        {kind === 'image' ? (
          <img src={url} alt={promptHead(job)} className="max-h-64 w-full object-contain" />
        ) : kind === 'video' ? (
          <video src={url} controls className="max-h-64 w-full" />
        ) : (
          <audio src={url} controls className="w-full p-2" />
        )}
      </div>

      <dl className="grid grid-cols-[auto_1fr] gap-x-2 gap-y-1 text-[11px] text-muted-foreground">
        <dt>成果物</dt>
        <dd className="text-foreground/85">{OUTPUT_LABELS[output]}</dd>
        <dt>モード</dt>
        <dd className="text-foreground/85">{MODE_LABELS[job.mode] ?? job.mode}</dd>
        {workflow && (
          <>
            <dt>ワークフロー</dt>
            <dd className="break-all text-foreground/85">{workflow}</dd>
          </>
        )}
        {job.project_name && (
          <>
            <dt>作品</dt>
            <dd className="text-foreground/85">{job.project_name}</dd>
          </>
        )}
        {job.shot_title && (
          <>
            <dt>カット</dt>
            <dd className="text-foreground/85">{job.shot_title}</dd>
          </>
        )}
        <dt>生成日時</dt>
        <dd className="tnum text-foreground/85">{timestampOf(job.created_at)}</dd>
        <dt>ジョブ</dt>
        <dd className="break-all text-foreground/85">{job.id}</dd>
      </dl>

      {prompt && (
        <div>
          <p className="mb-1 text-[11px] text-muted-foreground">プロンプト</p>
          <p className="max-h-32 overflow-y-auto whitespace-pre-wrap rounded-md border border-border bg-surface-sunken p-2 text-[11px] text-foreground/85">
            {prompt}
          </p>
        </div>
      )}

      <div className="flex flex-wrap items-center gap-2">
        <LibraryAddButton
          job={job}
          source={output}
          label={OUTPUT_LABELS[output]}
          onAdded={onRegistered}
        />
      </div>

      <div className="flex flex-wrap gap-2">
        {onUseInGenerate && (
          <Button variant="outline" size="sm" onClick={onUseInGenerate}>
            <Wand2 />
            生成の入力にする
          </Button>
        )}
        {onOpenJob && (
          <Button variant="outline" size="sm" onClick={onOpenJob}>
            <History />
            ジョブを開く
          </Button>
        )}
        {onOpenInStudio && (
          <Button variant="outline" size="sm" onClick={onOpenInStudio}>
            <Clapperboard />
            スタジオで開く
          </Button>
        )}
      </div>

      {kind === 'audio' && (
        <p className="flex items-center gap-1 text-[11px] text-muted-foreground">
          <Music className="size-3" aria-hidden />
          音声の成果物は履歴を消すと一緒に消えます（残すならライブラリに登録）。
        </p>
      )}
    </div>
  )
}
