import { useEffect } from 'react'
import {
  AUDIO_CATEGORIES,
  CATEGORY_LABELS,
  LANGUAGE_LABELS,
  audioSupports,
  clampToWorkflow,
  durationRange,
  type FormState,
} from '../form'
import type { Options, WorkflowOption } from '../types'
import ModelPicker from './ModelPicker'
import WorkflowSelects from './WorkflowSelects'
import { FieldError, Section } from './ui'

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
  const workflow = workflows.find((item) => item.id === form.audioWorkflow) ?? null
  const range = durationRange(workflow)

  const hasLyrics = audioSupports(workflow, 'lyrics')
  const hasNegativeTags = audioSupports(workflow, 'negative_tags')
  // 長さを宣言しないモデル（Suno は API に尺のパラメータが無い）では、効かない
  // つまみを見せないよう秒数の入力ごと隠す（§2.4）。
  const hasDuration = workflow == null || workflow.max_duration > 0
  const hasBpm = audioSupports(workflow, 'bpm')
  const hasKeyscale = audioSupports(workflow, 'keyscale')
  const hasLanguage = audioSupports(workflow, 'language')
  const hasCategory = audioSupports(workflow, 'audio_category')
  const hasReprompt = audioSupports(workflow, 'reprompt')

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
        {workflows.length > 0 ? (
          <select
            className="field"
            aria-label="音声ワークフロー"
            value={form.audioWorkflow}
            onChange={(event) => patch({ audioWorkflow: event.target.value })}
          >
            {workflows.map((item) => (
              <option key={item.id} value={item.id}>
                {item.label}
              </option>
            ))}
          </select>
        ) : (
          <input
            className="field"
            aria-label="音声ワークフロー"
            value={form.audioWorkflow}
            onChange={(event) => patch({ audioWorkflow: event.target.value })}
          />
        )}
        {workflow?.notes && (
          <p className="mt-1 text-[11px] text-slate-500">{workflow.notes}</p>
        )}
        <p className="mt-1 text-[11px] text-slate-500">
          音声は単独で生成されます（画像・動画とは連結されません）。
        </p>
        {/* ワークフローが宣言した選択式フィールド（Suno のモデル・
            ボーカルの性別。§3.1） */}
        <WorkflowSelects workflow={workflow} form={form} patch={patch} />
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
          <button className="btn-ghost !py-1 text-xs" onClick={onOpenChat}>
            Grokで生成
          </button>
        }
      >
        <label className="label" htmlFor="audio-prompt">
          音声プロンプト
        </label>
        <textarea
          id="audio-prompt"
          className="field h-28 resize-y"
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
        <p className="mt-1 text-[11px] text-slate-500">
          {hasLyrics
            ? '曲の説明（キャプション）です。歌う言葉は下の「歌詞」に書いてください。'
            : 'カテゴリに合わせた 1〜2 文で。末尾に BPM / Length を書くのがモデル作者の流儀です。'}
        </p>
      </Section>

      {hasLyrics && (
        <Section title="歌詞（空欄でインストゥルメンタル）">
          <textarea
            className="field h-40 resize-y font-mono text-xs"
            aria-label="歌詞"
            value={form.lyrics}
            placeholder={'[Verse 1]\n最終列車が街を抜ける\n\n[Chorus]\nもう一度だけ'}
            onChange={(event) => patch({ lyrics: event.target.value })}
          />
          <p className="mt-1 text-[11px] text-slate-500">
            [Intro] / [Verse] / [Pre-Chorus] / [Chorus] / [Bridge] / [Outro]
            などの構造タグを行頭に置きます。1 行 6〜10 音節が目安。
          </p>
        </Section>
      )}

      {hasNegativeTags && (
        <Section title="除外タグ（任意）">
          <input
            className="field"
            aria-label="除外タグ"
            value={form.negativeTags}
            placeholder="distorted guitar, screaming, heavy drums"
            onChange={(event) => patch({ negativeTags: event.target.value })}
          />
          <p className="mt-1 text-[11px] text-slate-500">
            曲に入れたくない要素を英語のカンマ区切りで。プロンプトや歌詞に
            「〜なし」と書くと逆効果なので、こちらに書きます。
          </p>
        </Section>
      )}

      {hasCategory && (
        <Section title="カテゴリ">
          <select
            className="field"
            aria-label="カテゴリ"
            value={form.audioCategory}
            onChange={(event) => patch({ audioCategory: event.target.value })}
          >
            {categories.map((value) => (
              <option key={value} value={value}>
                {CATEGORY_LABELS[value] ?? value}
              </option>
            ))}
          </select>
          <p className="mt-1 text-[11px] text-slate-500">
            カテゴリごとに内蔵のプロンプトテンプレートが変わります。
          </p>
        </Section>
      )}

      <Section title="出力設定">
        <div className={hasBpm && hasDuration ? 'grid grid-cols-2 gap-2' : undefined}>
          {hasDuration && (
            <div>
              <label className="label" htmlFor="audio-duration">
                長さ（秒）
                {range && (
                  <span className="ml-1 text-slate-600">
                    {range.min}〜{range.max}
                  </span>
                )}
              </label>
              <input
                id="audio-duration"
                className="field"
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
              <label className="label" htmlFor="audio-bpm">
                BPM
              </label>
              <input
                id="audio-bpm"
                className="field"
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
                <label className="label" htmlFor="audio-keyscale">
                  キー / スケール
                </label>
                <select
                  id="audio-keyscale"
                  className="field"
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
                </select>
              </div>
            )}
            {hasLanguage && (
              <div>
                <label className="label" htmlFor="audio-language">
                  歌詞の言語
                </label>
                <select
                  id="audio-language"
                  className="field"
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
                </select>
              </div>
            )}
          </div>
        )}

        {hasReprompt && (
          <label className="mt-3 flex items-start gap-2 text-xs text-slate-300">
            <input
              type="checkbox"
              className="mt-0.5 accent-accent-500"
              checked={form.reprompt}
              onChange={(event) => patch({ reprompt: event.target.checked })}
            />
            <span>
              内蔵 LLM でプロンプトを展開する
              <span className="mt-0.5 block text-[11px] text-slate-500">
                短い一言だけ書いたときに有効。自分で構造化して書いたならオフのままに。
              </span>
            </span>
          </label>
        )}

        <div className="mt-3 flex items-center gap-2">
          <label className="flex items-center gap-2 text-xs text-slate-300">
            <input
              type="checkbox"
              className="accent-accent-500"
              checked={form.seedLocked}
              onChange={(event) => patch({ seedLocked: event.target.checked })}
            />
            seed 固定
          </label>
          <input
            className="field flex-1"
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
