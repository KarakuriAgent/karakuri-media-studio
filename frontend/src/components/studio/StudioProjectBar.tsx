import { useState } from 'react'
import { ArrowLeft, ClipboardList, LayoutGrid, SlidersHorizontal } from 'lucide-react'

import { DEFAULT_MEGAPIXELS } from '../../form'
import type { ComfyTarget, StudioVideoQuality } from '../../types'
import { Modal } from '../ui'
import { Button } from '../ui/button'
import { Input } from '../ui/input'
import { Label } from '../ui/label'
import { NativeSelect } from '../NativeSelect'
import TargetSelector from '../TargetSelector'
import {
  MAX_STEPS,
  VIDEO_QUALITIES,
  VIDEO_QUALITY_HINT,
  VIDEO_QUALITY_LABEL,
  formatProjectSettingsSummary,
} from './studio'

export type StudioProjectMode = 'studio' | 'canvas'

const MODES: { value: StudioProjectMode; label: string; icon: typeof ClipboardList }[] = [
  { value: 'studio', label: 'スタジオ表示', icon: ClipboardList },
  { value: 'canvas', label: 'キャンバス表示', icon: LayoutGrid },
]

/**
 * プロジェクトを開いたあとの上段バー。
 *
 * 狭い画面は要約チップ + シート、広い画面は接続先・品質などを常時表示。
 * 両方を同時にマウントしない（入力 id の衝突と a11y ツリーの二重化を避ける）。
 */
export default function StudioProjectBar({
  name,
  isWide,
  mode,
  onModeChange,
  onBack,
  comfyTarget = null,
  onComfyTarget,
  quality,
  onQualityChange,
  aspectRatio,
  aspectRatios,
  onAspectRatioChange,
  megapixels,
  megapixelsDraft,
  onMegapixelsDraftChange,
  onCommitMegapixels,
  steps,
  stepsDraft,
  onStepsDraftChange,
  onCommitSteps,
  busy,
}: {
  name: string
  isWide: boolean
  mode: StudioProjectMode
  onModeChange: (mode: StudioProjectMode) => void
  onBack: () => void
  comfyTarget?: ComfyTarget | null
  onComfyTarget?: (target: ComfyTarget) => void
  quality: StudioVideoQuality
  onQualityChange: (quality: StudioVideoQuality) => void
  aspectRatio: string | null
  aspectRatios: string[]
  onAspectRatioChange: (value: string | null) => void
  megapixels: number | null
  megapixelsDraft: string
  onMegapixelsDraftChange: (value: string) => void
  onCommitMegapixels: () => void
  steps: number
  stepsDraft: string
  onStepsDraftChange: (value: string) => void
  onCommitSteps: () => void
  busy: boolean
}) {
  const [settingsOpen, setSettingsOpen] = useState(false)
  const summary = formatProjectSettingsSummary({
    target: onComfyTarget ? comfyTarget : undefined,
    quality,
    aspectRatio,
    megapixels,
    steps,
  })

  const modeToggle = (
    <div
      role="group"
      aria-label="表示モード"
      className="flex shrink-0 items-center gap-0.5 rounded-md border border-border bg-card p-0.5"
    >
      {MODES.map((item) => {
        const current = item.value === mode
        return (
          <Button
            key={item.value}
            variant={current ? 'secondary' : 'ghost'}
            size={isWide ? 'sm' : 'icon-sm'}
            aria-pressed={current}
            aria-label={item.label}
            title={item.label}
            onClick={() => onModeChange(item.value)}
          >
            <item.icon />
            {isWide ? item.label : null}
          </Button>
        )
      })}
    </div>
  )

  const fields = (
    <ProjectSettingsFields
      stacked={!isWide}
      comfyTarget={comfyTarget}
      onComfyTarget={onComfyTarget}
      quality={quality}
      onQualityChange={onQualityChange}
      aspectRatio={aspectRatio}
      aspectRatios={aspectRatios}
      onAspectRatioChange={onAspectRatioChange}
      megapixelsDraft={megapixelsDraft}
      onMegapixelsDraftChange={onMegapixelsDraftChange}
      onCommitMegapixels={onCommitMegapixels}
      stepsDraft={stepsDraft}
      onStepsDraftChange={onStepsDraftChange}
      onCommitSteps={onCommitSteps}
      busy={busy}
    />
  )

  if (isWide) {
    return (
      <div className="flex flex-wrap items-center gap-2">
        <Button variant="outline" size="sm" onClick={onBack}>
          <ArrowLeft />
          プロジェクト一覧
        </Button>
        <h2 className="min-w-0 truncate text-base font-semibold text-foreground">{name}</h2>
        <div className="ml-auto flex flex-wrap items-center justify-end gap-2">
          {fields}
          {modeToggle}
        </div>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center gap-2">
        <Button
          variant="outline"
          size="sm"
          aria-label="プロジェクト一覧"
          onClick={onBack}
        >
          <ArrowLeft />
          一覧
        </Button>
        <h2 className="min-w-0 flex-1 truncate text-base font-semibold text-foreground">
          {name}
        </h2>
        {modeToggle}
      </div>
      <Button
        variant="outline"
        size="sm"
        className="min-w-0 w-full justify-start"
        aria-label={`生成設定: ${summary}`}
        aria-haspopup="dialog"
        aria-expanded={settingsOpen}
        onClick={() => setSettingsOpen(true)}
      >
        <SlidersHorizontal />
        <span className="min-w-0 truncate">{summary}</span>
      </Button>
      {settingsOpen && (
        <Modal title="生成設定" onClose={() => setSettingsOpen(false)} closeOnBackdrop>
          <p className="mb-3 text-xs text-muted-foreground">
            この作品の既定です。Shot 個別の指定があればそちらが優先されます。
          </p>
          {fields}
        </Modal>
      )}
    </div>
  )
}

function ProjectSettingsFields({
  stacked,
  comfyTarget,
  onComfyTarget,
  quality,
  onQualityChange,
  aspectRatio,
  aspectRatios,
  onAspectRatioChange,
  megapixelsDraft,
  onMegapixelsDraftChange,
  onCommitMegapixels,
  stepsDraft,
  onStepsDraftChange,
  onCommitSteps,
  busy,
}: {
  stacked: boolean
  comfyTarget?: ComfyTarget | null
  onComfyTarget?: (target: ComfyTarget) => void
  quality: StudioVideoQuality
  onQualityChange: (quality: StudioVideoQuality) => void
  aspectRatio: string | null
  aspectRatios: string[]
  onAspectRatioChange: (value: string | null) => void
  megapixelsDraft: string
  onMegapixelsDraftChange: (value: string) => void
  onCommitMegapixels: () => void
  stepsDraft: string
  onStepsDraftChange: (value: string) => void
  onCommitSteps: () => void
  busy: boolean
}) {
  const targetSelector = onComfyTarget ? (
    <TargetSelector
      target={comfyTarget ?? null}
      onChange={onComfyTarget}
      id="studio-comfy-target"
      className={stacked ? 'w-full flex-col items-stretch gap-1' : 'w-52 shrink-0'}
    />
  ) : null

  const qualitySelect = (
    <NativeSelect
      id="studio-quality"
      value={quality}
      disabled={busy}
      title={VIDEO_QUALITIES.map((value) => VIDEO_QUALITY_HINT[value]).join('\n')}
      onChange={(event) => onQualityChange(event.target.value as StudioVideoQuality)}
    >
      {VIDEO_QUALITIES.map((value) => (
        <option key={value} value={value}>
          {VIDEO_QUALITY_LABEL[value]}
        </option>
      ))}
    </NativeSelect>
  )

  const aspectSelect = (
    <NativeSelect
      id="studio-aspect-ratio"
      value={aspectRatio ?? ''}
      disabled={busy}
      title="この作品のアスペクト比の既定（Shot 個別の指定があればそちらが優先）"
      onChange={(event) => onAspectRatioChange(event.target.value || null)}
    >
      <option value="">既定のまま</option>
      {aspectRatio && !aspectRatios.includes(aspectRatio) && (
        <option value={aspectRatio}>{aspectRatio}</option>
      )}
      {aspectRatios.map((ratio) => (
        <option key={ratio} value={ratio}>
          {ratio}
        </option>
      ))}
    </NativeSelect>
  )

  const megapixelsInput = (
    <Input
      id="studio-megapixels"
      aria-label="メガピクセル"
      className="tnum"
      type="number"
      step="0.05"
      min="0.1"
      value={megapixelsDraft}
      disabled={busy}
      placeholder={`${DEFAULT_MEGAPIXELS}`}
      title="この作品のメガピクセルの既定（空欄でワークフローの既定。Shot 個別の指定があればそちらが優先）"
      onChange={(event) => onMegapixelsDraftChange(event.target.value)}
      onBlur={onCommitMegapixels}
      onKeyDown={(event) => {
        if (event.key === 'Enter') event.currentTarget.blur()
      }}
    />
  )

  const stepsInput = (
    <Input
      id="studio-steps"
      className="tnum"
      type="number"
      step="1"
      min="0"
      max={MAX_STEPS}
      value={stepsDraft}
      disabled={busy}
      placeholder="おまかせ"
      title={
        'この作品のサンプリング回数の既定（空欄 = 0 = おまかせ' +
        ' = テンプレートの既定のまま。turbo は 4、normal / opt は 20）'
      }
      onChange={(event) => onStepsDraftChange(event.target.value)}
      onBlur={onCommitSteps}
      onKeyDown={(event) => {
        if (event.key === 'Enter') event.currentTarget.blur()
      }}
    />
  )

  if (stacked) {
    return (
      <div className="flex flex-col gap-3">
        {targetSelector}
        <div className="flex flex-col items-stretch gap-1">
          <Label htmlFor="studio-quality">品質</Label>
          {qualitySelect}
        </div>
        <div className="flex flex-col items-stretch gap-1">
          <Label htmlFor="studio-aspect-ratio">画質</Label>
          {aspectSelect}
        </div>
        <div className="flex flex-col items-stretch gap-1">
          <Label htmlFor="studio-megapixels">メガピクセル</Label>
          <div className="flex items-center gap-2">
            <div className="min-w-0 flex-1">{megapixelsInput}</div>
            <span className="shrink-0 text-[11px] text-muted-foreground">MP</span>
          </div>
        </div>
        <div className="flex flex-col items-stretch gap-1">
          <Label htmlFor="studio-steps">ステップ</Label>
          {stepsInput}
        </div>
      </div>
    )
  }

  return (
    <>
      {targetSelector}
      <div className="flex shrink-0 items-center gap-2">
        <Label className="shrink-0" htmlFor="studio-quality">
          品質
        </Label>
        <div className="w-28">{qualitySelect}</div>
      </div>
      <div className="flex shrink-0 items-center gap-2">
        <Label className="shrink-0" htmlFor="studio-aspect-ratio">
          画質
        </Label>
        <div className="w-40">{aspectSelect}</div>
        <div className="w-24">{megapixelsInput}</div>
        <span className="shrink-0 text-[11px] text-muted-foreground">MP</span>
      </div>
      <div className="flex shrink-0 items-center gap-2">
        <Label className="shrink-0" htmlFor="studio-steps">
          ステップ
        </Label>
        <div className="w-24">{stepsInput}</div>
      </div>
    </>
  )
}
