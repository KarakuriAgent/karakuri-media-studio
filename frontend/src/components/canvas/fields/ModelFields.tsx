import {
  lorasForTarget,
  megapixelsFor,
  modelSlotsForJob,
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
import { NativeSelect } from '../../NativeSelect'
import { Checkbox } from '../../ui/checkbox'
import { Input } from '../../ui/input'
import { Label } from '../../ui/label'
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
    <Field label={label}>
      <div className="flex max-h-40 flex-col gap-1 overflow-y-auto rounded-md border border-border bg-surface-sunken p-2">
        {loras.map((lora) => {
          const picked = selected.find((item) => item.lora_name === lora.lora_name)
          const id = `canvas-lora-${lora.id}`
          return (
            <div key={lora.id} className="flex items-center gap-2 text-xs">
              <Checkbox
                id={id}
                className="size-3.5"
                checked={!!picked}
                onCheckedChange={() =>
                  onChange(
                    picked
                      ? selected.filter((item) => item.lora_name !== lora.lora_name)
                      : [...selected, toRef(lora)],
                  )
                }
              />
              <Label
                htmlFor={id}
                className="flex-1 cursor-pointer truncate text-xs text-foreground/85"
              >
                {lora.display_name}
              </Label>
              {picked && (
                <Input
                  className="tnum h-7 w-20 px-2 py-0.5"
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
  // 実行時に切り替えられるモデル（SPEC §3.3）。設定で候補を 2 件以上登録した
  // スロットだけが出るので、何も登録していないワークフローでは何も描かない。
  const slots = modelSlotsForJob(options?.model_slots, [data.workflow])

  return (
    <div className="flex flex-col gap-3">
      <Field label="生成対象">
        <NativeSelect
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
        </NativeSelect>
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
            <NativeSelect
              aria-label="アスペクト比"
              value={data.params.aspect_ratio}
              onChange={(event) => patchParams({ aspect_ratio: event.target.value })}
            >
              {(options?.aspect_ratios ?? [data.params.aspect_ratio]).map((ratio) => (
                <option key={ratio} value={ratio}>
                  {ratio}
                </option>
              ))}
            </NativeSelect>
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

      {selects.map((select) => {
        // 画面に出す文字だけを日本語に差し替える（送る値は生のまま、SPEC §3.1）。
        // 生成フォームの `WorkflowSelects` と同じ扱いで、宣言の無い値は生の値。
        const labelOf = (choice: string) =>
          select.choice_labels?.[choice] || choice
        return (
          <Field key={select.name} label={select.label}>
            <NativeSelect
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
                {select.auto
                  ? '自動（入力に合わせる）'
                  : `既定（${labelOf(select.default)}）`}
              </option>
              {select.choices.map((choice) => (
                <option key={choice} value={choice}>
                  {labelOf(choice)}
                </option>
              ))}
            </NativeSelect>
          </Field>
        )
      })}

      {/* 使用モデルの切り替え（生成フォーム / 設定画面と同じスロット単位）。
          既定のままの選択は保存しない（`model_overrides` は差分だけ持つ）。 */}
      {slots.map((slot) => {
        const title = `使用モデル: ${slot.label || `${slot.node_id}.${slot.field}`}`
        return (
          <Field key={slot.key} label={title}>
            <NativeSelect
              aria-label={title}
              value={data.params.model_overrides[slot.key] ?? slot.default}
              onChange={(event) => {
                const next = { ...data.params.model_overrides }
                if (event.target.value === slot.default) delete next[slot.key]
                else next[slot.key] = event.target.value
                patchParams({ model_overrides: next })
              }}
            >
              {slot.choices.map((name) => (
                <option key={name} value={name}>
                  {name === slot.default ? `${name}（既定）` : name}
                </option>
              ))}
            </NativeSelect>
          </Field>
        )
      })}

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
      />
      <AreaField
        label="メモ"
        value={data.note}
        rows={2}
        onChange={(note) => patch({ note })}
      />
    </div>
  )
}
