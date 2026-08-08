import {
  lorasForTarget,
  megapixelsFor,
  workflowSelects,
} from '../../../form'
import type {
  CanvasModelData,
  CanvasModelParams,
  CanvasModelTarget,
  Lora,
  LoraRef,
  Options,
  WorkflowOption,
} from '../../../types'
import WorkflowPicker from '../../WorkflowPicker'
import { AreaField, Field, NumberField } from './common'

const TARGETS: { value: CanvasModelTarget; label: string }[] = [
  { value: 'image', label: '画像' },
  { value: 'video', label: '動画' },
  { value: 'audio', label: '音声' },
]

/** その target で選べるワークフロー（選択肢が届いていなければ空）。 */
export function workflowsForTarget(
  options: Options | null,
  target: CanvasModelTarget,
): WorkflowOption[] {
  if (!options) return []
  if (target === 'image') return options.image_workflows
  if (target === 'video') return options.video_workflows
  return options.audio_workflows
}

function toRef(lora: Lora): LoraRef {
  return {
    lora_name: lora.lora_name,
    trigger_word: lora.trigger_word,
    strength: lora.default_strength ?? 1.0,
  }
}

function LoraChooser({
  label,
  loras,
  selected,
  onChange,
}: {
  label: string
  loras: Lora[]
  selected: LoraRef[]
  onChange: (refs: LoraRef[]) => void
}) {
  if (loras.length === 0) return null
  return (
    <Field label={label} hint="選んだ LoRA は生成時にこの強度で挿し込まれる">
      <div className="flex max-h-40 flex-col gap-1 overflow-y-auto rounded-md border border-ink-600 bg-ink-800/60 p-2">
        {loras.map((lora) => {
          const picked = selected.find((item) => item.lora_name === lora.lora_name)
          return (
            <div key={lora.id} className="flex items-center gap-2 text-xs">
              <label className="flex flex-1 cursor-pointer items-center gap-2">
                <input
                  type="checkbox"
                  className="h-3 w-3 accent-accent-500"
                  checked={!!picked}
                  onChange={() =>
                    onChange(
                      picked
                        ? selected.filter(
                            (item) => item.lora_name !== lora.lora_name,
                          )
                        : [...selected, toRef(lora)],
                    )
                  }
                />
                <span className="truncate text-slate-300">{lora.display_name}</span>
              </label>
              {picked && (
                <input
                  className="field !w-20 !py-0.5"
                  aria-label={`${lora.display_name} の強度`}
                  type="number"
                  step={0.05}
                  value={picked.strength}
                  onChange={(event) =>
                    onChange(
                      selected.map((item) =>
                        item.lora_name === lora.lora_name
                          ? { ...item, strength: Number(event.target.value) }
                          : item,
                      ),
                    )
                  }
                />
              )}
            </div>
          )
        })}
      </div>
    </Field>
  )
}

/**
 * model カードの編集フォーム。
 *
 * ワークフローは生成フォームと同じ「モデル → モード」2 段プルダウン
 * （`WorkflowPicker`）で選ぶ。パラメータはカードに保存されるだけで、実際の
 * ジョブ投入は generate アクション（P3）が行う。
 */
export default function ModelFields({
  data,
  onChange,
  options,
}: {
  data: CanvasModelData
  onChange: (data: CanvasModelData) => void
  options: Options | null
}) {
  const patch = (changes: Partial<CanvasModelData>) => onChange({ ...data, ...changes })
  const patchParams = (changes: Partial<CanvasModelParams>) =>
    patch({ params: { ...data.params, ...changes } })

  const workflows = workflowsForTarget(options, data.target)
  const workflow = workflows.find((item) => item.id === data.workflow) ?? null
  const selects = workflowSelects(workflow)
  const imageLoras = lorasForTarget(options?.loras ?? [], 'image', workflow?.family)
  const videoLoras = lorasForTarget(options?.loras ?? [], 'video', workflow?.family)

  return (
    <div className="flex flex-col gap-3">
      <Field label="生成対象">
        <select
          className="field"
          aria-label="生成対象"
          value={data.target}
          onChange={(event) => {
            const target = event.target.value as CanvasModelTarget
            // 対象が変わればワークフローの候補ごと入れ替わるので、そのまま
            // 残さず先頭の候補（無ければ未選択）に寄せる。
            const next = workflowsForTarget(options, target)
            patch({ target, workflow: next[0]?.id ?? '' })
          }}
        >
          {TARGETS.map((item) => (
            <option key={item.value} value={item.value}>
              {item.label}
            </option>
          ))}
        </select>
      </Field>

      <WorkflowPicker
        workflows={workflows}
        value={data.workflow}
        // 生成フォームと同じく、モデルが想定している画角に合わせる（SPEC §3.1）
        onChange={(workflowId) =>
          patch({
            workflow: workflowId,
            params: {
              ...data.params,
              megapixels: megapixelsFor(
                workflows.find((item) => item.id === workflowId),
              ),
            },
          })
        }
        modelLabel="モデル"
        modeLabel="モード"
        fallbackLabel="ワークフロー ID"
      />

      {data.target !== 'audio' && (
        <div className="grid gap-2 sm:grid-cols-2">
          <Field label="アスペクト比">
            <select
              className="field"
              aria-label="アスペクト比"
              value={data.params.aspect_ratio}
              onChange={(event) => patchParams({ aspect_ratio: event.target.value })}
            >
              {(options?.aspect_ratios ?? [data.params.aspect_ratio]).map((ratio) => (
                <option key={ratio} value={ratio}>
                  {ratio}
                </option>
              ))}
            </select>
          </Field>
          <NumberField
            label="メガピクセル"
            value={data.params.megapixels}
            step={0.1}
            min={0.1}
            onChange={(megapixels) => patchParams({ megapixels })}
          />
        </div>
      )}

      {data.target !== 'image' && (
        <div className="grid gap-2 sm:grid-cols-2">
          <NumberField
            label="長さ（秒）"
            value={data.params.duration}
            step={0.5}
            min={0}
            onChange={(duration) => patchParams({ duration })}
          />
          {data.target === 'video' && (
            <NumberField
              label="fps"
              value={data.params.fps}
              step={1}
              min={1}
              onChange={(fps) => patchParams({ fps })}
            />
          )}
        </div>
      )}

      {selects.map((select) => (
        <Field key={select.name} label={select.label} hint={select.hint}>
          <select
            className="field"
            aria-label={select.label}
            value={data.params.selects[select.name] ?? ''}
            onChange={(event) => {
              const next = { ...data.params.selects }
              if (event.target.value) next[select.name] = event.target.value
              else delete next[select.name]
              patchParams({ selects: next })
            }}
          >
            <option value="">
              {select.auto ? '自動（入力に合わせる）' : `既定（${select.default}）`}
            </option>
            {select.choices.map((choice) => (
              <option key={choice} value={choice}>
                {choice}
              </option>
            ))}
          </select>
        </Field>
      ))}

      {data.target === 'image' && (
        <LoraChooser
          label="画像 LoRA"
          loras={imageLoras}
          selected={data.params.loras}
          onChange={(loras) => patchParams({ loras })}
        />
      )}
      {data.target === 'video' && (
        <LoraChooser
          label="動画 LoRA"
          loras={videoLoras}
          selected={data.params.video_loras}
          onChange={(video_loras) => patchParams({ video_loras })}
        />
      )}

      <AreaField
        label="ネガティブプロンプト"
        value={data.params.negative_prompt}
        rows={2}
        onChange={(negative_prompt) => patchParams({ negative_prompt })}
        hint="空ならジョブの既定値に任せる"
      />
      <AreaField
        label="メモ"
        value={data.note}
        rows={2}
        onChange={(note) => patch({ note })}
        hint="何用のモデル設定か"
      />
    </div>
  )
}
