import { useState } from 'react'
import { Loader2 } from 'lucide-react'
import { modelSlotsForJob } from '../form'
import type { Job, JobContinue, Options } from '../types'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { NativeSelect } from './NativeSelect'
import WorkflowPicker from './WorkflowPicker'
import { Banner, Modal } from './ui'

/** 「元ジョブを引き継ぐ」を表す空値（プルダウンの先頭・入力欄の空欄）。 */
const INHERIT = ''

/** 元ジョブの params から 1 項目を読む（無ければ空文字）。 */
function inherited(job: Job, key: string): string {
  const value = (job.params ?? {})[key]
  if (value == null) return ''
  return typeof value === 'object' ? '' : String(value)
}

/** 入力欄のプレースホルダ（引き継ぐ値が分かるように元の値を出す）。 */
function placeholderOf(job: Job, key: string): string {
  const value = inherited(job, key)
  return value ? `元ジョブを引き継ぐ（${value}）` : '元ジョブを引き継ぐ'
}

/** 空欄なら未指定、そうでなければ数値（数にならない入力は未指定扱い）。 */
function numberOrUndefined(raw: string): number | undefined {
  if (raw.trim() === '') return undefined
  const value = Number(raw)
  return Number.isFinite(value) ? value : undefined
}

/** 空文字を落として `JobContinue` にする（未変更の項目は送らない）。 */
export function continuePayload(
  draft: Record<string, string>,
  modelOverrides: Record<string, string>,
): JobContinue {
  const body: JobContinue = {}
  for (const key of ['video_workflow', 'video_prompt', 'negative_prompt', 'aspect_ratio', 'audio_path', 'end_image', 'reference_video'] as const) {
    const value = (draft[key] ?? '').trim()
    if (value) body[key] = value
  }
  for (const key of ['megapixels', 'duration', 'fps', 'seed'] as const) {
    const value = numberOrUndefined(draft[key] ?? '')
    if (value !== undefined) body[key] = value
  }
  // model_overrides は送ると丸ごと差し替わるので、1 つでも選んだときだけ送る。
  if (Object.keys(modelOverrides).length > 0) body.model_overrides = modelOverrides
  return body
}

/**
 * 「続きを生成」の上書きフォーム（SPEC §2 / `POST /api/jobs/{id}/continue`）。
 *
 * 既定は全項目「元ジョブを引き継ぐ」なので、開いてそのまま［このまま続き生成］を
 * 押せば従来どおり空ボディで投げる。変えたい欄だけ埋めれば、その項目だけが
 * 上書きとして送られる。
 */
export default function ContinueModal({
  job,
  options,
  busy,
  error,
  onClose,
  onSubmit,
}: {
  job: Job
  options: Options | null
  busy: boolean
  error: string | null
  onClose: () => void
  onSubmit: (body: JobContinue) => void
}) {
  const [draft, setDraft] = useState<Record<string, string>>({})
  const [modelOverrides, setModelOverrides] = useState<Record<string, string>>({})

  const set = (key: string, value: string) =>
    setDraft((previous) => ({ ...previous, [key]: value }))

  // 続きは動画ステージだけを走らせるので、モデルスロットも動画ワークフローのぶん。
  // ワークフローを付け替えたときは付け替え先のスロットに切り替える。
  const workflowId = draft.video_workflow || inherited(job, 'video_workflow')
  const slots = modelSlotsForJob(options?.model_slots, [workflowId])

  const field = (key: string, label: string, type: 'text' | 'number' = 'text') => (
    <div>
      <Label className="mb-1" htmlFor={`continue-${key}`}>
        {label}
      </Label>
      <Input
        id={`continue-${key}`}
        className={type === 'number' ? 'tnum' : undefined}
        type={type}
        value={draft[key] ?? INHERIT}
        placeholder={placeholderOf(job, key)}
        onChange={(event) => set(key, event.target.value)}
      />
    </div>
  )

  return (
    <Modal title="続きを生成" onClose={onClose}>
      <div className="space-y-3">
        <p className="text-xs text-muted-foreground">
          空欄の項目は元ジョブの設定をそのまま引き継ぎます。変えたい欄だけ埋めてください。
        </p>

        {error && <Banner>{error}</Banner>}

        <div>
          <p className="mb-1 block text-xs font-medium tracking-wide text-muted-foreground">
            動画ワークフロー
          </p>
          <WorkflowPicker
            workflows={options?.video_workflows ?? []}
            value={workflowId}
            onChange={(id) => set('video_workflow', id)}
            modelLabel="モデル"
            modeLabel="モード"
            fallbackLabel="ワークフロー ID"
          />
        </div>

        <div>
          <Label className="mb-1" htmlFor="continue-video_prompt">
            動画プロンプト
          </Label>
          <Textarea
            id="continue-video_prompt"
            className="h-24 resize-y"
            value={draft.video_prompt ?? INHERIT}
            placeholder={placeholderOf(job, 'video_prompt')}
            onChange={(event) => set('video_prompt', event.target.value)}
          />
        </div>

        <div>
          <Label className="mb-1" htmlFor="continue-negative_prompt">
            ネガティブプロンプト
          </Label>
          <Textarea
            id="continue-negative_prompt"
            className="h-16 resize-y"
            value={draft.negative_prompt ?? INHERIT}
            placeholder={placeholderOf(job, 'negative_prompt')}
            onChange={(event) => set('negative_prompt', event.target.value)}
          />
        </div>

        <div className="grid gap-2 sm:grid-cols-2">
          <div>
            <Label className="mb-1" htmlFor="continue-aspect_ratio">
              アスペクト比
            </Label>
            <NativeSelect
              id="continue-aspect_ratio"
              value={draft.aspect_ratio ?? INHERIT}
              onChange={(event) => set('aspect_ratio', event.target.value)}
            >
              <option value={INHERIT}>{placeholderOf(job, 'aspect_ratio')}</option>
              {(options?.aspect_ratios ?? []).map((ratio) => (
                <option key={ratio} value={ratio}>
                  {ratio}
                </option>
              ))}
            </NativeSelect>
          </div>
          {field('megapixels', 'メガピクセル', 'number')}
          {field('duration', '長さ（秒）', 'number')}
          {field('fps', 'fps', 'number')}
          {field('seed', 'seed', 'number')}
          {field('audio_path', 'リファレンス音声のパス')}
          {field('end_image', '最後のフレームのパス')}
          {field('reference_video', '参照動画のパス')}
        </div>

        {slots.length > 0 && (
          <div className="flex flex-col gap-2">
            {slots.map((slot) => {
              const title = `使用モデル: ${slot.label || `${slot.node_id}.${slot.field}`}`
              return (
                <div key={slot.key}>
                  <Label className="mb-1">{title}</Label>
                  <NativeSelect
                    aria-label={title}
                    value={modelOverrides[slot.key] ?? INHERIT}
                    onChange={(event) =>
                      setModelOverrides((previous) => {
                        const next = { ...previous }
                        if (event.target.value) next[slot.key] = event.target.value
                        else delete next[slot.key]
                        return next
                      })
                    }
                  >
                    <option value={INHERIT}>元ジョブを引き継ぐ</option>
                    {slot.choices.map((name) => (
                      <option key={name} value={name}>
                        {name === slot.default ? `${name}（既定）` : name}
                      </option>
                    ))}
                  </NativeSelect>
                </div>
              )
            })}
          </div>
        )}

        <div className="flex flex-wrap gap-2 border-t border-border pt-3">
          <Button
            disabled={busy}
            onClick={() => onSubmit(continuePayload(draft, modelOverrides))}
          >
            {busy && <Loader2 className="animate-spin" />}
            {busy ? '送信中…' : 'この設定で続き生成'}
          </Button>
          <Button
            variant="outline"
            disabled={busy}
            title="入力を無視して、元ジョブの設定をそのまま引き継ぎます"
            onClick={() => onSubmit({})}
          >
            そのまま続き生成
          </Button>
          <Button variant="ghost" className="ml-auto" disabled={busy} onClick={onClose}>
            取消
          </Button>
        </div>
      </div>
    </Modal>
  )
}
