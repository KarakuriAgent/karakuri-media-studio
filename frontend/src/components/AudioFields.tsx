import { useEffect } from 'react'
import { Sparkles } from 'lucide-react'
import {
  AUDIO_CATEGORIES,
  CATEGORY_LABELS,
  LANGUAGE_LABELS,
  MAX_STEPS,
  audioSupports,
  clampToWorkflow,
  durationRange,
  workflowSelects,
  type FormState,
} from '../form'
import type { Options, WorkflowOption } from '../types'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { NativeSelect } from './NativeSelect'
import ModelPicker from './ModelPicker'
import WorkflowPicker from './WorkflowPicker'
import WorkflowSelects from './WorkflowSelects'
import { FieldError, Section } from './ui'

/** 選択中の音声ワークフロー（見つからなければ null）。 */
function audioWorkflowOf(
  form: FormState,
  options: Options | null,
): WorkflowOption | null {
  const workflows: WorkflowOption[] = options?.audio_workflows ?? []
  return workflows.find((item) => item.id === form.audioWorkflow) ?? null
}

/**
 * 音声モードで `FieldError` の表示先があるエラーのキー（SPEC §8）。
 *
 * 「出ていない欄のエラーは、送信ボタンのそばにまとめて出す」フォールバック
 * （`GenerateForm`）が、ここに無いキーを拾う。下の `hasDuration` / `hasBpm` と
 * 同じ条件を使うので、欄を出し分ける条件を変えるときは両方そろえること。
 */
export function audioErrorKeys(
  form: FormState,
  options: Options | null,
): string[] {
  const workflow = audioWorkflowOf(form, options)
  const keys = ['audio_prompt']
  if (workflow == null || workflow.max_duration > 0) keys.push('duration')
  if (audioSupports(workflow, 'bpm')) keys.push('bpm')
  // 選択式の相関エラー（`selectRequiresErrors`）はセレクトの下に出る
  for (const select of workflowSelects(workflow)) keys.push(select.name)
  return keys
}

/**
 * 生成フォームの `mode: 'audio'` ブロック。
 *
 * 音声はモードの一つだが独立ジョブなので、ここには画像・動画のワークフロー選択も
 * LoRA も解像度も無い。どのつまみを出すかは選択中の音声ワークフローの
 * `supports`（バックエンドのマニフェスト）が決める。
 */
export default function AudioFields({
  form,
  patch,
  options,
  onOpenChat,
  fieldErrors,
}: {
  form: FormState
  patch: (patch: Partial<FormState>) => void
  options: Options | null
  onOpenChat: () => void
  fieldErrors: Record<string, string>
}) {
  const workflows: WorkflowOption[] = options?.audio_workflows ?? []
  const workflow = audioWorkflowOf(form, options)
  const range = durationRange(workflow)

  const hasLyrics = audioSupports(workflow, 'lyrics')
  const hasNegativeTags = audioSupports(workflow, 'negative_tags')
  // 長さを宣言しないモデル（API に尺のパラメータが無いもの）では、効かない
  // つまみを見せないよう秒数の入力ごと隠す（§2.4）。
  const hasDuration = workflow == null || workflow.max_duration > 0
  const hasBpm = audioSupports(workflow, 'bpm')
  const hasKeyscale = audioSupports(workflow, 'keyscale')
  const hasLanguage = audioSupports(workflow, 'language')
  const hasCategory = audioSupports(workflow, 'audio_category')
  const hasReprompt = audioSupports(workflow, 'reprompt')
  const hasSteps = audioSupports(workflow, 'steps')

  // 選択肢が届いたら未知のワークフロー id を既定へ寄せる（動画側と同じ挙動）。
  useEffect(() => {
    if (workflows.length === 0) return
    if (!workflows.some((item) => item.id === form.audioWorkflow)) {
      patch({ audioWorkflow: options?.default_audio_workflow || workflows[0].id })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workflows, form.audioWorkflow])

  // モデルごとに対応秒数が違うので、範囲外なら切り替え時にその既定へ戻す。
  useEffect(() => {
    const changes = clampToWorkflow(form, workflow)
    if (Object.keys(changes).length > 0) patch(changes)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [form.audioWorkflow, workflow?.max_duration])

  const keyscales = options?.keyscales ?? []
  const languages = options?.languages ?? []
  const categories = options?.audio_categories ?? AUDIO_CATEGORIES

  return (
    <>
      <Section title="音声ワークフロー">
        <WorkflowPicker
          workflows={workflows}
          value={form.audioWorkflow}
          onChange={(id) => patch({ audioWorkflow: id })}
          modelLabel="音声モデル"
          modeLabel="音声モード"
          fallbackLabel="音声ワークフロー"
        />
        {/* ワークフローが宣言した選択式フィールド（モデル・
            ボーカルの性別。§3.1） */}
        <WorkflowSelects
          workflow={workflow}
          form={form}
          patch={patch}
          errors={fieldErrors}
        />
        <ModelPicker
          slots={options?.model_slots}
          workflowId={form.audioWorkflow}
          form={form}
          patch={patch}
        />
      </Section>

      <Section
        title="プロンプト"
        right={
          <Button variant="outline" size="xs" onClick={onOpenChat}>
            <Sparkles />
            Grokで生成
          </Button>
        }
      >
        <Label className="mb-1" htmlFor="audio-prompt">
          音声プロンプト
        </Label>
        <Textarea
          id="audio-prompt"
          className="h-28 resize-y"
          aria-label="音声プロンプト"
          value={form.audioPrompt}
          placeholder={
            hasLyrics
              ? 'ジャンル・楽器・音色・雰囲気・ボーカルの声質など（例: dreamy city-pop, female vocal, warm rhodes, laid-back drums）'
              : '鳴らしたい音の説明（例: Heavy rain hitting a metal roof, distant thunder. Length: 30 seconds）'
          }
          onChange={(event) => patch({ audioPrompt: event.target.value })}
        />
        <FieldError message={fieldErrors.audio_prompt} />
      </Section>

      {hasLyrics && (
        <Section title="歌詞（空欄でインストゥルメンタル）">
          <Textarea
            className="h-40 resize-y font-mono text-xs"
            aria-label="歌詞"
            value={form.lyrics}
            placeholder={'[Verse 1]\n最終列車が街を抜ける\n\n[Chorus]\nもう一度だけ'}
            onChange={(event) => patch({ lyrics: event.target.value })}
          />
        </Section>
      )}

      {hasNegativeTags && (
        <Section title="除外タグ（任意）">
          <Input
            aria-label="除外タグ"
            value={form.negativeTags}
            placeholder="distorted guitar, screaming, heavy drums"
            onChange={(event) => patch({ negativeTags: event.target.value })}
          />
        </Section>
      )}

      {hasCategory && (
        <Section title="カテゴリ">
          <NativeSelect
            aria-label="カテゴリ"
            value={form.audioCategory}
            onChange={(event) => patch({ audioCategory: event.target.value })}
          >
            {categories.map((value) => (
              <option key={value} value={value}>
                {CATEGORY_LABELS[value] ?? value}
              </option>
            ))}
          </NativeSelect>
        </Section>
      )}

      <Section title="出力設定">
        <div className={hasBpm && hasDuration ? 'grid grid-cols-2 gap-2' : undefined}>
          {hasDuration && (
            <div>
              <Label className="mb-1" htmlFor="audio-duration">
                長さ（秒）
                {range && (
                  <span className="tnum ml-1 font-normal text-muted-foreground/70">
                    {range.min}〜{range.max}
                  </span>
                )}
              </Label>
              <Input
                id="audio-duration"
                className="tnum"
                type="number"
                min={range?.min ?? 1}
                max={range?.max ?? undefined}
                step="1"
                value={form.audioDuration}
                onChange={(event) =>
                  patch({ audioDuration: Number(event.target.value) || 0 })
                }
              />
              <FieldError message={fieldErrors.duration} />
            </div>
          )}
          {hasBpm && (
            <div>
              <Label className="mb-1" htmlFor="audio-bpm">
                BPM
              </Label>
              <Input
                id="audio-bpm"
                className="tnum"
                type="number"
                min="10"
                max="300"
                step="1"
                value={form.bpm}
                onChange={(event) => patch({ bpm: Number(event.target.value) || 0 })}
              />
              <FieldError message={fieldErrors.bpm} />
            </div>
          )}
        </div>

        {(hasKeyscale || hasLanguage) && (
          <div className="mt-2 grid grid-cols-2 gap-2">
            {hasKeyscale && (
              <div>
                <Label className="mb-1" htmlFor="audio-keyscale">
                  キー / スケール
                </Label>
                <NativeSelect
                  id="audio-keyscale"
                  value={form.keyscale}
                  onChange={(event) => patch({ keyscale: event.target.value })}
                >
                  {!keyscales.includes(form.keyscale) && (
                    <option value={form.keyscale}>{form.keyscale}</option>
                  )}
                  {keyscales.map((value) => (
                    <option key={value} value={value}>
                      {value}
                    </option>
                  ))}
                </NativeSelect>
              </div>
            )}
            {hasLanguage && (
              <div>
                <Label className="mb-1" htmlFor="audio-language">
                  歌詞の言語
                </Label>
                <NativeSelect
                  id="audio-language"
                  value={form.language}
                  onChange={(event) => patch({ language: event.target.value })}
                >
                  {!languages.includes(form.language) && (
                    <option value={form.language}>{form.language}</option>
                  )}
                  {languages.map((value) => (
                    <option key={value} value={value}>
                      {LANGUAGE_LABELS[value] ?? value}
                    </option>
                  ))}
                </NativeSelect>
              </div>
            )}
          </div>
        )}

        {hasSteps && (
          <div className="mt-2">
            <Label className="mb-1" htmlFor="audio-steps">
              ステップ数
            </Label>
            <Input
              id="audio-steps"
              className="tnum"
              type="number"
              min="0"
              max={MAX_STEPS}
              step="1"
              placeholder="未指定＝既定"
              value={form.steps || ''}
              onChange={(event) => patch({ steps: Number(event.target.value) || 0 })}
            />
          </div>
        )}

        {hasReprompt && (
          <div className="mt-3 flex items-start gap-2">
            <Checkbox
              id="audio-reprompt"
              className="mt-0.5"
              checked={form.reprompt}
              onCheckedChange={(checked) => patch({ reprompt: checked === true })}
            />
            <Label
              htmlFor="audio-reprompt"
              className="cursor-pointer text-xs text-foreground/85"
            >
              内蔵 LLM でプロンプトを展開する
            </Label>
          </div>
        )}

        <div className="mt-3 flex items-center gap-2">
          <div className="flex shrink-0 items-center gap-2">
            <Checkbox
              id="audio-seed-locked"
              checked={form.seedLocked}
              onCheckedChange={(checked) => patch({ seedLocked: checked === true })}
            />
            <Label
              htmlFor="audio-seed-locked"
              className="cursor-pointer text-xs text-foreground/85"
            >
              seed 固定
            </Label>
          </div>
          <Input
            className="tnum flex-1"
            type="number"
            min="0"
            aria-label="seed"
            value={form.seed}
            disabled={!form.seedLocked}
            onChange={(event) => patch({ seed: Number(event.target.value) || 0 })}
          />
        </div>
      </Section>
    </>
  )
}
