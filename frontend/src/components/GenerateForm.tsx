import { useEffect, useRef, useState } from 'react'
import { api } from '../api'
import {
  AUTHOR_NEGATIVE_PROMPT,
  DEFAULT_NEGATIVE_PROMPT,
  MODE_HINTS,
  MODE_LABELS,
  NEGATIVE_PRESET_LABELS,
  disabledFields,
  joinTriggers,
  lorasForTarget,
  toSelected,
  workflowsForMode,
  type FormState,
  type SelectedLora,
} from '../form'
import type { Asset, Job, JobMode, Lora, Options, WorkflowOption } from '../types'
import { Banner, FieldError } from './ui'

const MODES: JobMode[] = ['full', 'i2v', 'image_only']

interface Props {
  form: FormState
  patch: (patch: Partial<FormState>) => void
  options: Options | null
  optionsError: string | null
  onReloadOptions: () => void
  onOpenChat: () => void
  onSubmit: () => void
  submitting: boolean
  fieldErrors: Record<string, string>
  jobs: Job[]
}

function Section({
  title,
  children,
  right,
}: {
  title: string
  children: React.ReactNode
  right?: React.ReactNode
}) {
  return (
    <section className="card p-3">
      <div className="mb-2 flex items-center justify-between">
        <h3 className="text-xs font-semibold uppercase tracking-wider text-slate-400">
          {title}
        </h3>
        {right}
      </div>
      {children}
    </section>
  )
}

/** LoRA chips + strength sliders + trigger words, shared by both stages. */
function LoraPicker({
  loras,
  selected,
  disabled,
  triggerText,
  triggerDirty,
  emptyHint,
  onToggle,
  onStrength,
  onTrigger,
  onTriggerReset,
}: {
  loras: Lora[]
  selected: SelectedLora[]
  disabled: boolean
  triggerText: string
  triggerDirty: boolean
  emptyHint: string
  onToggle: (lora: Lora) => void
  onStrength: (index: number, strength: number) => void
  onTrigger: (value: string) => void
  onTriggerReset: () => void
}) {
  return (
    <div className={disabled ? 'pointer-events-none opacity-40' : undefined}>
      <div className="flex flex-wrap gap-1.5">
        {loras.length === 0 && <p className="text-xs text-slate-500">{emptyHint}</p>}
        {loras.map((lora) => {
          const active = selected.some((item) => item.id === lora.id)
          return (
            <button
              key={lora.id}
              className={`chip ${
                active
                  ? 'border-accent-500 bg-accent-500/20 text-accent-400'
                  : 'border-ink-500 bg-ink-700 text-slate-300 hover:bg-ink-600'
              }`}
              disabled={disabled}
              onClick={() => onToggle(lora)}
            >
              {lora.display_name}
            </button>
          )
        })}
      </div>

      {selected.length > 0 && (
        <div className="mt-3 flex flex-col gap-2">
          {selected.map((lora, index) => (
            <div key={lora.id} className="flex items-center gap-2">
              <span className="w-24 shrink-0 truncate text-xs text-slate-300">
                {lora.display_name}
              </span>
              <input
                type="range"
                min="0"
                max="2"
                step="0.05"
                className="flex-1 accent-accent-500"
                value={lora.strength}
                disabled={disabled}
                onChange={(event) => onStrength(index, Number(event.target.value))}
              />
              <span className="w-10 text-right text-xs tabular-nums text-slate-400">
                {lora.strength.toFixed(2)}
              </span>
            </div>
          ))}
        </div>
      )}

      <div className="mt-3">
        <div className="flex items-center justify-between">
          <label className="label">トリガーワード（自動連結・編集可）</label>
          {triggerDirty && (
            <button
              className="mb-1 text-[11px] text-slate-400 hover:text-slate-200"
              onClick={onTriggerReset}
            >
              自動連結に戻す
            </button>
          )}
        </div>
        <input
          className="field"
          value={triggerText}
          disabled={disabled}
          onChange={(event) => onTrigger(event.target.value)}
        />
      </div>
    </div>
  )
}

/** Asset select + upload + preview, shared by every image / video input. */
function AssetPicker({
  kind,
  value,
  assets,
  busy,
  onPick,
  onUpload,
  children,
}: {
  kind: 'image' | 'video'
  value: string
  assets: Asset[]
  busy: boolean
  onPick: (url: string) => void
  onUpload: (file: File) => void
  children?: React.ReactNode
}) {
  const input = useRef<HTMLInputElement>(null)
  const [dragOver, setDragOver] = useState(false)

  const pickFile = (files: FileList | File[] | null) => {
    const file = Array.from(files ?? []).find((item) =>
      item.type.startsWith(`${kind}/`),
    )
    if (file) onUpload(file)
  }

  return (
    <div
      className={`flex flex-col gap-2 rounded-lg border border-dashed p-2 transition-colors ${
        dragOver ? 'border-accent-500 bg-accent-500/10' : 'border-ink-600'
      }`}
      onDragOver={(event) => {
        event.preventDefault()
        setDragOver(true)
      }}
      onDragLeave={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget as Node | null)) {
          setDragOver(false)
        }
      }}
      onDrop={(event) => {
        event.preventDefault()
        setDragOver(false)
        pickFile(event.dataTransfer.files)
      }}
    >
      <div className="flex items-center gap-2">
        <input
          ref={input}
          type="file"
          accept={`${kind}/*`}
          className="hidden"
          onChange={(event) => {
            pickFile(event.target.files)
            event.target.value = ''
          }}
        />
        <button
          className="btn-ghost text-xs"
          disabled={busy}
          onClick={() => input.current?.click()}
        >
          {kind === 'image' ? '画像をアップロード' : '動画をアップロード'}
        </button>
        <span className="text-[11px] text-slate-500">
          {busy ? 'アップロード中…' : 'またはここにドロップ'}
        </span>
        {value && (
          <button className="btn-ghost text-xs" onClick={() => onPick('')}>
            クリア
          </button>
        )}
      </div>
      <select
        className="field"
        value={value}
        onChange={(event) => onPick(event.target.value)}
      >
        <option value="">（未選択）</option>
        {value && !assets.some((asset) => asset.url === value) && (
          <option value={value}>{value}</option>
        )}
        {assets.map((asset) => (
          <option key={asset.url} value={asset.url}>
            {asset.name}
          </option>
        ))}
      </select>
      {children}
      {value && kind === 'image' && (
        <img
          src={value}
          alt=""
          className="max-h-40 w-fit rounded border border-ink-600 object-contain"
        />
      )}
      {value && kind === 'video' && (
        <video src={value} controls className="max-h-40 w-fit rounded border border-ink-600" />
      )}
    </div>
  )
}

export default function GenerateForm({
  form,
  patch,
  options,
  optionsError,
  onReloadOptions,
  onOpenChat,
  onSubmit,
  submitting,
  fieldErrors,
  jobs,
}: Props) {
  const [uploadError, setUploadError] = useState<string | null>(null)
  const [busyUpload, setBusyUpload] = useState(false)
  const [showAdvanced, setShowAdvanced] = useState(false)

  const registeredLoras: Lora[] = options?.loras ?? []
  const imageLoras = lorasForTarget(registeredLoras, 'image')
  const videoLoras = lorasForTarget(registeredLoras, 'video')
  const audioAssets = options?.audio_assets ?? []
  const imageAssets = options?.image_assets ?? []
  const videoAssets = options?.video_assets ?? []
  const aspectRatios = options?.aspect_ratios ?? []
  const videoWorkflows: WorkflowOption[] = options?.video_workflows ?? []
  const negativePresets = options?.negative_presets ?? {
    current: DEFAULT_NEGATIVE_PROMPT,
    author: AUTHOR_NEGATIVE_PROMPT,
  }

  const usable = workflowsForMode(form.mode, videoWorkflows)
  const workflow =
    videoWorkflows.find((item) => item.id === form.videoWorkflow) ?? null
  const disabled = disabledFields(form.mode, workflow)

  // Full generation needs a workflow that can take the generated still; switch
  // away from e.g. t2v instead of letting the request 422.
  useEffect(() => {
    if (usable.length === 0) return
    if (!usable.some((item) => item.id === form.videoWorkflow)) {
      patch({ videoWorkflow: usable[0].id })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [form.mode, form.videoWorkflow, videoWorkflows])

  const toggleLora = (lora: Lora) => {
    const already = form.loras.some((item) => item.id === lora.id)
    const next = already
      ? form.loras.filter((item) => item.id !== lora.id)
      : [...form.loras, toSelected(lora)]
    const changes: Partial<FormState> = { loras: next }
    if (!form.triggerDirty) changes.triggerText = joinTriggers(next)
    // SPEC §7: first selected LoRA carrying a default audio wins.
    if (!already && !form.audioPath && lora.default_audio) {
      changes.audioPath = lora.default_audio
    }
    patch(changes)
  }

  const toggleVideoLora = (lora: Lora) => {
    const already = form.videoLoras.some((item) => item.id === lora.id)
    const next = already
      ? form.videoLoras.filter((item) => item.id !== lora.id)
      : [...form.videoLoras, toSelected(lora)]
    const changes: Partial<FormState> = { videoLoras: next }
    if (!form.videoTriggerDirty) changes.videoTriggerText = joinTriggers(next)
    if (!already && !form.audioPath && lora.default_audio) {
      changes.audioPath = lora.default_audio
    }
    patch(changes)
  }

  const upload = async (
    kind: 'image' | 'audio' | 'video',
    file: File,
    apply: (url: string) => Partial<FormState>,
  ) => {
    setUploadError(null)
    setBusyUpload(true)
    try {
      const asset =
        kind === 'image'
          ? await api.uploadImage(file)
          : kind === 'audio'
            ? await api.uploadAudio(file)
            : await api.uploadVideo(file)
      patch(apply(asset.url))
      onReloadOptions()
    } catch (error) {
      setUploadError(error instanceof Error ? error.message : String(error))
    } finally {
      setBusyUpload(false)
    }
  }

  /** Outputs live outside assets/, so copy the frame into assets via upload. */
  const useLastFrame = async (job: Job) => {
    if (!job.last_frame_url) return
    setUploadError(null)
    setBusyUpload(true)
    try {
      const response = await fetch(job.last_frame_url)
      if (!response.ok) throw new Error(`ラストフレームを取得できません (${response.status})`)
      const blob = await response.blob()
      const file = new File([blob], `last_frame_${job.id}.png`, {
        type: blob.type || 'image/png',
      })
      const asset = await api.uploadImage(file)
      patch({ sourceImage: asset.url })
      onReloadOptions()
    } catch (error) {
      setUploadError(error instanceof Error ? error.message : String(error))
    } finally {
      setBusyUpload(false)
    }
  }

  const lastFrameJobs = jobs.filter((job) => job.last_frame_url)
  const audioInput = useRef<HTMLInputElement>(null)

  return (
    <div className="flex flex-col gap-3">
      {optionsError && (
        <Banner tone="warn">
          ComfyUI に接続できないため選択肢を取得できません（手入力で続行できます）:
          {'\n'}
          {optionsError}
        </Banner>
      )}

      {/* mode tabs */}
      <div className="flex gap-1 rounded-lg border border-ink-600 bg-ink-800 p-1">
        {MODES.map((mode) => (
          <button
            key={mode}
            onClick={() => patch({ mode })}
            title={MODE_HINTS[mode]}
            className={`flex-1 rounded-md px-2 py-1.5 text-xs font-medium transition-colors ${
              form.mode === mode
                ? 'bg-accent-500 text-white'
                : 'text-slate-400 hover:bg-ink-700'
            }`}
          >
            {MODE_LABELS[mode]}
          </button>
        ))}
      </div>

      {form.mode !== 'image_only' && (
        <Section title="動画ワークフロー">
          {usable.length > 0 ? (
            <select
              className="field"
              value={form.videoWorkflow}
              onChange={(event) => patch({ videoWorkflow: event.target.value })}
            >
              {usable.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.label}
                </option>
              ))}
            </select>
          ) : (
            <input
              className="field"
              value={form.videoWorkflow}
              onChange={(event) => patch({ videoWorkflow: event.target.value })}
            />
          )}
          {workflow?.notes && (
            <p className="mt-1 text-[11px] text-slate-500">{workflow.notes}</p>
          )}
          {form.mode === 'full' && (
            <p className="mt-1 text-[11px] text-slate-500">
              フル生成では開始フレームを受け取れるワークフローのみ選べます。
            </p>
          )}
        </Section>
      )}

      {!disabled.startImage && (
        <Section title={workflow?.image_label ?? '開始フレーム'}>
          <AssetPicker
            kind="image"
            value={form.sourceImage}
            assets={imageAssets}
            busy={busyUpload}
            onPick={(url) => patch({ sourceImage: url })}
            onUpload={(file) => void upload('image', file, (url) => ({ sourceImage: url }))}
          >
            {lastFrameJobs.length > 0 && (
              <div>
                <label className="label">履歴のラストフレームから選択</label>
                <div className="flex gap-2 overflow-x-auto pb-1">
                  {lastFrameJobs.slice(0, 24).map((job) => (
                    <button
                      key={job.id}
                      title={job.id}
                      disabled={busyUpload}
                      className="shrink-0 rounded border border-ink-600 hover:border-accent-500"
                      onClick={() => void useLastFrame(job)}
                    >
                      <img
                        src={job.last_frame_url ?? ''}
                        alt={job.id}
                        className="h-16 w-24 rounded object-cover"
                      />
                    </button>
                  ))}
                </div>
              </div>
            )}
          </AssetPicker>
          <p className="mt-1 text-[11px] text-slate-500">
            履歴のラストフレームから続きを生成する場合は、履歴詳細の「続きを生成」を使ってください。
          </p>
          <FieldError message={fieldErrors.source_image} />
        </Section>
      )}

      {!disabled.endImage && (
        <Section title="最後のフレーム">
          <AssetPicker
            kind="image"
            value={form.endImage}
            assets={imageAssets}
            busy={busyUpload}
            onPick={(url) => patch({ endImage: url })}
            onUpload={(file) => void upload('image', file, (url) => ({ endImage: url }))}
          />
          <FieldError message={fieldErrors.end_image} />
        </Section>
      )}

      {!disabled.referenceVideo && (
        <Section title="参照動画（モーション転写）">
          <AssetPicker
            kind="video"
            value={form.referenceVideo}
            assets={videoAssets}
            busy={busyUpload}
            onPick={(url) => patch({ referenceVideo: url })}
            onUpload={(file) => void upload('video', file, (url) => ({ referenceVideo: url }))}
          />
          <p className="mt-1 text-[11px] text-slate-500">
            秒数の設定ぶんだけ先頭から切り出して深度を取り、モーションを転写します。
          </p>
          <FieldError message={fieldErrors.reference_video} />
        </Section>
      )}

      <Section title="リファレンス音声">
        <div className={disabled.audio ? 'pointer-events-none opacity-40' : undefined}>
          <div className="flex flex-col gap-2">
            {disabled.audio && form.mode !== 'image_only' && (
              <p className="text-[11px] text-slate-500">
                このワークフローは音声を受け取りません（音声は動画と同時に生成されます）。
              </p>
            )}
            <select
              className="field"
              value={form.audioPath}
              disabled={disabled.audio}
              onChange={(event) => patch({ audioPath: event.target.value })}
            >
              <option value="">（未選択）</option>
              {!audioAssets.some((asset) => asset.url === form.audioPath) &&
                form.audioPath && (
                  <option value={form.audioPath}>{form.audioPath}</option>
                )}
              {audioAssets.map((asset) => (
                <option key={asset.url} value={asset.url}>
                  {asset.name}
                </option>
              ))}
            </select>
            <div className="flex items-center gap-2">
              <input
                ref={audioInput}
                type="file"
                accept="audio/*"
                className="hidden"
                onChange={(event) => {
                  const file = event.target.files?.[0]
                  if (file) void upload('audio', file, (url) => ({ audioPath: url }))
                  event.target.value = ''
                }}
              />
              <button
                className="btn-ghost text-xs"
                disabled={busyUpload || disabled.audio}
                onClick={() => audioInput.current?.click()}
              >
                音声をアップロード
              </button>
              {form.audioPath && (
                <audio className="h-8 flex-1" controls src={form.audioPath} />
              )}
            </div>
            <FieldError message={fieldErrors.audio_path} />
          </div>
        </div>
      </Section>

      <Section title="LoRA（動画）">
        <LoraPicker
          loras={videoLoras}
          selected={form.videoLoras}
          disabled={disabled.videoLoras}
          triggerText={form.videoTriggerText}
          triggerDirty={form.videoTriggerDirty}
          emptyHint="動画用の登録済み LoRA がありません（設定 → LoRA 管理で追加）"
          onToggle={toggleVideoLora}
          onStrength={(index, strength) => {
            const next = [...form.videoLoras]
            next[index] = { ...next[index], strength }
            patch({ videoLoras: next })
          }}
          onTrigger={(value) =>
            patch({ videoTriggerText: value, videoTriggerDirty: true })
          }
          onTriggerReset={() =>
            patch({
              videoTriggerDirty: false,
              videoTriggerText: joinTriggers(form.videoLoras),
            })
          }
        />
        <p className="mt-2 text-[11px] text-slate-500">
          動画ワークフロー（LTX 2.3）の既定 LoRA の後ろに直列で挿入され、
          トリガーワードは動画プロンプトの先頭に付きます。
        </p>
      </Section>

      <Section title="解像度">
        <div className="grid grid-cols-2 gap-2">
          <div>
            <label className="label">アスペクト比</label>
            {aspectRatios.length > 0 ? (
              <select
                className="field"
                value={form.aspectRatio}
                onChange={(event) => patch({ aspectRatio: event.target.value })}
              >
                {!aspectRatios.includes(form.aspectRatio) && (
                  <option value={form.aspectRatio}>{form.aspectRatio}</option>
                )}
                {aspectRatios.map((ratio) => (
                  <option key={ratio} value={ratio}>
                    {ratio}
                  </option>
                ))}
              </select>
            ) : (
              <input
                className="field"
                value={form.aspectRatio}
                placeholder="4:3 (Standard)"
                onChange={(event) => patch({ aspectRatio: event.target.value })}
              />
            )}
          </div>
          <div>
            <label className="label">メガピクセル</label>
            <input
              className="field"
              type="number"
              step="0.05"
              min="0.1"
              value={form.megapixels}
              onChange={(event) =>
                patch({ megapixels: Number(event.target.value) || 0 })
              }
            />
          </div>
        </div>
        <p className="mt-1 text-[11px] text-slate-500">
          動画側の幅・高さはこの組み合わせから 8 の倍数で計算されます。
        </p>
      </Section>

      <Section title="LoRA（画像）">
        <LoraPicker
          loras={imageLoras}
          selected={form.loras}
          disabled={disabled.loras}
          triggerText={form.triggerText}
          triggerDirty={form.triggerDirty}
          emptyHint="画像用の登録済み LoRA がありません（設定 → LoRA 管理で追加）"
          onToggle={toggleLora}
          onStrength={(index, strength) => {
            const next = [...form.loras]
            next[index] = { ...next[index], strength }
            patch({ loras: next })
          }}
          onTrigger={(value) => patch({ triggerText: value, triggerDirty: true })}
          onTriggerReset={() =>
            patch({ triggerDirty: false, triggerText: joinTriggers(form.loras) })
          }
        />
        <p className="mt-2 text-[11px] text-slate-500">
          画像ワークフロー（Krea 2）に適用され、トリガーワードは画像プロンプトの先頭に付きます。
        </p>
      </Section>

      <Section
        title="プロンプト"
        right={
          <button className="btn-ghost !py-1 text-xs" onClick={onOpenChat}>
            Grokで生成
          </button>
        }
      >
        <div className="flex flex-col gap-3">
          <div>
            <label className="label">画像プロンプト</label>
            <textarea
              className="field h-28 resize-y"
              value={form.imagePrompt}
              disabled={disabled.imagePrompt}
              placeholder={
                disabled.imagePrompt
                  ? '動画生成モードでは使用しません'
                  : '自然文 1 段落で詳細に'
              }
              onChange={(event) => patch({ imagePrompt: event.target.value })}
            />
            <FieldError message={fieldErrors.image_prompt} />
          </div>
          <div>
            <label className="label">動画プロンプト</label>
            <textarea
              className="field h-28 resize-y"
              value={form.videoPrompt}
              disabled={disabled.videoPrompt}
              placeholder={
                disabled.videoPrompt
                  ? '画像のみモードでは使用しません'
                  : '1 段落 4〜8 文。動き・カメラ・音声を含める'
              }
              onChange={(event) => patch({ videoPrompt: event.target.value })}
            />
            <FieldError message={fieldErrors.video_prompt} />
          </div>
        </div>
      </Section>

      <Section
        title="動画ネガティブ"
        right={
          <button
            className="text-xs text-slate-400 hover:text-slate-200"
            onClick={() => setShowAdvanced((value) => !value)}
          >
            {showAdvanced ? '閉じる' : '詳細設定'}
          </button>
        }
      >
        {showAdvanced && (
          <div className="flex flex-col gap-2">
            <select
              className="field"
              value={form.negativePreset}
              disabled={disabled.negative}
              onChange={(event) => {
                const key = event.target.value
                patch({
                  negativePreset: key,
                  negativePrompt:
                    key === 'custom'
                      ? form.negativePrompt
                      : (negativePresets[key] ?? form.negativePrompt),
                })
              }}
            >
              {Object.keys(negativePresets).map((key) => (
                <option key={key} value={key}>
                  {NEGATIVE_PRESET_LABELS[key] ?? key}
                </option>
              ))}
              <option value="custom">{NEGATIVE_PRESET_LABELS.custom}</option>
            </select>
            <textarea
              className="field h-20 resize-y font-mono text-xs"
              value={form.negativePrompt}
              disabled={disabled.negative}
              placeholder="空欄ならワークフロー既定のネガティブを使います"
              onChange={(event) =>
                patch({ negativePrompt: event.target.value, negativePreset: 'custom' })
              }
            />
          </div>
        )}
        {!showAdvanced && (
          <p className="truncate text-xs text-slate-500">
            {NEGATIVE_PRESET_LABELS[form.negativePreset] ?? form.negativePreset}:{' '}
            {form.negativePrompt || '（ワークフロー既定）'}
          </p>
        )}
      </Section>

      <Section title="出力設定">
        <div className="grid grid-cols-2 gap-2">
          <div>
            <label className="label">秒数（上限なし）</label>
            <input
              className="field"
              type="number"
              min="1"
              step="1"
              value={form.duration}
              disabled={disabled.duration}
              onChange={(event) => patch({ duration: Number(event.target.value) || 0 })}
            />
          </div>
          <div>
            <label className="label">fps</label>
            <input
              className="field"
              type="number"
              min="1"
              step="1"
              value={form.fps}
              disabled={disabled.fps}
              onChange={(event) => patch({ fps: Number(event.target.value) || 0 })}
            />
          </div>
        </div>
        <p className="mt-1 text-[11px] text-slate-500">
          長尺は VRAM 次第で ComfyUI 側がエラーになることがあります。
        </p>
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
            value={form.seed}
            disabled={!form.seedLocked}
            onChange={(event) => patch({ seed: Number(event.target.value) || 0 })}
          />
        </div>
      </Section>

      {uploadError && <Banner onClose={() => setUploadError(null)}>{uploadError}</Banner>}

      <button className="btn-primary w-full py-2.5" onClick={onSubmit} disabled={submitting}>
        {submitting ? '送信中…' : '実行'}
      </button>
    </div>
  )
}
