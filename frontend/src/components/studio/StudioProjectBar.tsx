import { useState } from 'react'
import { ArrowLeft, SlidersHorizontal } from 'lucide-react'

import { DEFAULT_MEGAPIXELS } from '../../form'
import type { ComfyTarget, StudioImageQuality, StudioVideoQuality } from '../../types'
import { Modal } from '../ui'
import { Button } from '../ui/button'
import { Checkbox } from '../ui/checkbox'
import { Input } from '../ui/input'
import { Label } from '../ui/label'
import { NativeSelect } from '../NativeSelect'
import TargetSelector from '../TargetSelector'
import {
  IMAGE_QUALITIES,
  IMAGE_QUALITY_HINT,
  IMAGE_QUALITY_LABEL,
  MAX_STEPS,
  VIDEO_QUALITIES,
  VIDEO_QUALITY_HINT,
  VIDEO_QUALITY_LABEL,
  formatProjectSettingsSummary,
} from './studio'

/**
 * プロジェクトを開いたあとの上段バー。
 *
 * 狭い画面は要約チップ + シート、広い画面は接続先・品質などを常時表示。
 * 両方を同時にマウントしない（入力 id の衝突と a11y ツリーの二重化を避ける）。
 */
export default function StudioProjectBar({
  name,
  isWide,
  onBack,
  comfyTarget = null,
  onComfyTarget,
  quality,
  onQualityChange,
  imageQuality,
  onImageQualityChange,
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
  imageAspectRatio,
  onImageAspectRatioChange,
  imageMegapixelsDraft,
  onImageMegapixelsDraftChange,
  onCommitImageMegapixels,
  imageStepsDraft,
  onImageStepsDraftChange,
  onCommitImageSteps,
  imageMegapixels,
  imageSteps,
  latentUpscale,
  onLatentUpscaleChange,
  busy,
}: {
  name: string
  isWide: boolean
  onBack: () => void
  comfyTarget?: ComfyTarget | null
  onComfyTarget?: (target: ComfyTarget) => void
  quality: StudioVideoQuality
  onQualityChange: (quality: StudioVideoQuality) => void
  /** 画像生成の品質（素材の静止画にだけ効く。動画の品質とは独立）。 */
  imageQuality: StudioImageQuality
  onImageQualityChange: (quality: StudioImageQuality) => void
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
  /** 素材画像のアスペクト比（動画の `aspectRatio` とは独立）。 */
  imageAspectRatio: string | null
  onImageAspectRatioChange: (value: string | null) => void
  imageMegapixelsDraft: string
  onImageMegapixelsDraftChange: (value: string) => void
  onCommitImageMegapixels: () => void
  imageStepsDraft: string
  onImageStepsDraftChange: (value: string) => void
  onCommitImageSteps: () => void
  imageMegapixels: number | null
  imageSteps: number
  /** ラテントアップスケール（作品既定。テイク生成のたびに上書きできる）。 */
  latentUpscale: boolean
  onLatentUpscaleChange: (value: boolean) => void
  busy: boolean
}) {
  const [settingsOpen, setSettingsOpen] = useState(false)
  const summary = formatProjectSettingsSummary({
    target: onComfyTarget ? comfyTarget : undefined,
    quality,
    imageQuality,
    aspectRatio,
    megapixels,
    steps,
    imageAspectRatio,
    imageMegapixels,
    imageSteps,
    latentUpscale,
  })

  const fields = (
    <ProjectSettingsFields
      stacked={!isWide}
      comfyTarget={comfyTarget}
      onComfyTarget={onComfyTarget}
      quality={quality}
      onQualityChange={onQualityChange}
      imageQuality={imageQuality}
      onImageQualityChange={onImageQualityChange}
      aspectRatio={aspectRatio}
      aspectRatios={aspectRatios}
      onAspectRatioChange={onAspectRatioChange}
      megapixelsDraft={megapixelsDraft}
      onMegapixelsDraftChange={onMegapixelsDraftChange}
      onCommitMegapixels={onCommitMegapixels}
      stepsDraft={stepsDraft}
      onStepsDraftChange={onStepsDraftChange}
      onCommitSteps={onCommitSteps}
      imageAspectRatio={imageAspectRatio}
      onImageAspectRatioChange={onImageAspectRatioChange}
      imageMegapixelsDraft={imageMegapixelsDraft}
      onImageMegapixelsDraftChange={onImageMegapixelsDraftChange}
      onCommitImageMegapixels={onCommitImageMegapixels}
      imageStepsDraft={imageStepsDraft}
      onImageStepsDraftChange={onImageStepsDraftChange}
      onCommitImageSteps={onCommitImageSteps}
      latentUpscale={latentUpscale}
      onLatentUpscaleChange={onLatentUpscaleChange}
      busy={busy}
    />
  )

  if (isWide) {
    // 上段は「どこで作るか」（接続先・表示モード）、下段は「どう作るか」（動画／
    // 画像の既定値）。1 本の flex-wrap に全部流すと意味の切れ目と無関係な位置で
    // 折り返すので、段を分けたうえで動画・画像を fieldset でまとめる。
    return (
      <div className="flex flex-col gap-2">
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" onClick={onBack}>
            <ArrowLeft />
            プロジェクト一覧
          </Button>
          <h2 className="min-w-0 truncate text-base font-semibold text-foreground">
            {name}
          </h2>
          <div className="ml-auto flex shrink-0 items-center gap-2">
            {onComfyTarget && (
              <TargetSelector
                target={comfyTarget ?? null}
                onChange={onComfyTarget}
                id="studio-comfy-target"
                className="w-52 shrink-0"
              />
            )}
          </div>
        </div>
        <div className="flex flex-wrap justify-end gap-x-3 gap-y-2">{fields}</div>
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
  imageQuality,
  onImageQualityChange,
  aspectRatio,
  aspectRatios,
  onAspectRatioChange,
  megapixelsDraft,
  onMegapixelsDraftChange,
  onCommitMegapixels,
  stepsDraft,
  onStepsDraftChange,
  onCommitSteps,
  imageAspectRatio,
  onImageAspectRatioChange,
  imageMegapixelsDraft,
  onImageMegapixelsDraftChange,
  onCommitImageMegapixels,
  imageStepsDraft,
  onImageStepsDraftChange,
  onCommitImageSteps,
  latentUpscale,
  onLatentUpscaleChange,
  busy,
}: {
  stacked: boolean
  comfyTarget?: ComfyTarget | null
  onComfyTarget?: (target: ComfyTarget) => void
  quality: StudioVideoQuality
  onQualityChange: (quality: StudioVideoQuality) => void
  /** 画像生成の品質（素材の静止画にだけ効く。動画の品質とは独立）。 */
  imageQuality: StudioImageQuality
  onImageQualityChange: (quality: StudioImageQuality) => void
  aspectRatio: string | null
  aspectRatios: string[]
  onAspectRatioChange: (value: string | null) => void
  megapixelsDraft: string
  onMegapixelsDraftChange: (value: string) => void
  onCommitMegapixels: () => void
  stepsDraft: string
  onStepsDraftChange: (value: string) => void
  onCommitSteps: () => void
  /** 素材画像のアスペクト比（動画の `aspectRatio` とは独立）。 */
  imageAspectRatio: string | null
  onImageAspectRatioChange: (value: string | null) => void
  imageMegapixelsDraft: string
  onImageMegapixelsDraftChange: (value: string) => void
  onCommitImageMegapixels: () => void
  imageStepsDraft: string
  onImageStepsDraftChange: (value: string) => void
  onCommitImageSteps: () => void
  latentUpscale: boolean
  onLatentUpscaleChange: (value: boolean) => void
  busy: boolean
}) {
  // 広い画面の接続先は上段バー側（StudioProjectBar）に置くので、ここでは
  // シート（stacked）のときだけ出す。
  const targetSelector =
    stacked && onComfyTarget ? (
      <TargetSelector
        target={comfyTarget ?? null}
        onChange={onComfyTarget}
        id="studio-comfy-target"
        className="w-full flex-col items-stretch gap-1"
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

  const imageQualitySelect = (
    <NativeSelect
      id="studio-image-quality"
      value={imageQuality}
      disabled={busy}
      title={[
        '素材の静止画（MiniMax H3 Image）の品質。動画の品質とは独立です。',
        ...IMAGE_QUALITIES.map((value) => IMAGE_QUALITY_HINT[value]),
      ].join('\n')}
      onChange={(event) =>
        onImageQualityChange(event.target.value as StudioImageQuality)
      }
    >
      {IMAGE_QUALITIES.map((value) => (
        <option key={value} value={value}>
          {IMAGE_QUALITY_LABEL[value]}
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
      aria-label="動画 MP"
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

  // 素材の静止画（`mode: "image_only"` のジョブ）用の画質 3 項目。動画側と
  // 同じ部品を並べるだけだが、値は完全に別で、動画用の値は流用しない。
  const imageAspectSelect = (
    <NativeSelect
      id="studio-image-aspect-ratio"
      value={imageAspectRatio ?? ''}
      disabled={busy}
      title="素材の静止画のアスペクト比の既定（動画のアスペクト比とは独立）"
      onChange={(event) => onImageAspectRatioChange(event.target.value || null)}
    >
      <option value="">既定のまま</option>
      {imageAspectRatio && !aspectRatios.includes(imageAspectRatio) && (
        <option value={imageAspectRatio}>{imageAspectRatio}</option>
      )}
      {aspectRatios.map((ratio) => (
        <option key={ratio} value={ratio}>
          {ratio}
        </option>
      ))}
    </NativeSelect>
  )

  const imageMegapixelsInput = (
    <Input
      id="studio-image-megapixels"
      aria-label="画像 MP"
      className="tnum"
      type="number"
      step="0.05"
      min="0.1"
      value={imageMegapixelsDraft}
      disabled={busy}
      placeholder="既定"
      title={
        '素材の静止画のメガピクセルの既定（空欄でテンプレートの既定。' +
        'MiniMax H3 Image は約 0.98MP）'
      }
      onChange={(event) => onImageMegapixelsDraftChange(event.target.value)}
      onBlur={onCommitImageMegapixels}
      onKeyDown={(event) => {
        if (event.key === 'Enter') event.currentTarget.blur()
      }}
    />
  )

  const imageStepsInput = (
    <Input
      id="studio-image-steps"
      className="tnum"
      type="number"
      step="1"
      min="0"
      max={MAX_STEPS}
      value={imageStepsDraft}
      disabled={busy}
      placeholder="おまかせ"
      title={
        '素材の静止画のサンプリング回数の既定（空欄 = 0 = おまかせ' +
        ' = テンプレートの既定のまま）'
      }
      onChange={(event) => onImageStepsDraftChange(event.target.value)}
      onBlur={onCommitImageSteps}
      onKeyDown={(event) => {
        if (event.key === 'Enter') event.currentTarget.blur()
      }}
    />
  )

  const latentUpscaleToggle = (
    <div className="flex shrink-0 items-center gap-2">
      <Checkbox
        id="studio-latent-upscale"
        checked={latentUpscale}
        disabled={busy}
        onCheckedChange={(checked) => onLatentUpscaleChange(checked === true)}
      />
      <Label
        htmlFor="studio-latent-upscale"
        className="cursor-pointer text-foreground/85"
        title={
          'オン = 1 パス目を 0.2MP で回してからラテントのまま指定解像度へ拡大' +
          '（速くて破綻しにくい）。オフ = 指定解像度で 1 パス。' +
          'テイク生成のたびに上書きできます'
        }
      >
        {stacked ? 'ラテントアップスケール' : '拡大'}
      </Label>
    </div>
  )

  if (stacked) {
    return (
      <div className="flex flex-col gap-3">
        {targetSelector}
        <fieldset className="rounded-md border border-border px-3 pb-3">
          <legend className="px-1 text-[11px] text-muted-foreground">動画</legend>
          <div className="grid grid-cols-2 gap-2">
            <div className="flex flex-col items-stretch gap-1">
              <Label htmlFor="studio-quality">
                <span className="sr-only">動画</span>品質
              </Label>
              {qualitySelect}
            </div>
            <div className="flex flex-col items-stretch gap-1">
              <Label htmlFor="studio-aspect-ratio">
                <span className="sr-only">{'動画 '}</span>比率
              </Label>
              {aspectSelect}
            </div>
            <div className="flex flex-col items-stretch gap-1">
              <Label htmlFor="studio-megapixels">MP</Label>
              {megapixelsInput}
            </div>
            <div className="flex flex-col items-stretch gap-1">
              <Label htmlFor="studio-steps">
                <span className="sr-only">{'動画 '}</span>steps
              </Label>
              {stepsInput}
            </div>
            <div className="col-span-2">{latentUpscaleToggle}</div>
          </div>
        </fieldset>
        <fieldset className="rounded-md border border-border px-3 pb-3">
          <legend className="px-1 text-[11px] text-muted-foreground">画像</legend>
          <div className="grid grid-cols-2 gap-2">
            <div className="flex flex-col items-stretch gap-1">
              <Label htmlFor="studio-image-quality">
                <span className="sr-only">画像</span>品質
              </Label>
              {imageQualitySelect}
            </div>
            <div className="flex flex-col items-stretch gap-1">
              <Label htmlFor="studio-image-aspect-ratio">
                <span className="sr-only">{'画像 '}</span>比率
              </Label>
              {imageAspectSelect}
            </div>
            <div className="flex flex-col items-stretch gap-1">
              <Label htmlFor="studio-image-megapixels">MP</Label>
              {imageMegapixelsInput}
            </div>
            <div className="flex flex-col items-stretch gap-1">
              <Label htmlFor="studio-image-steps">
                <span className="sr-only">{'画像 '}</span>steps
              </Label>
              {imageStepsInput}
            </div>
          </div>
        </fieldset>
      </div>
    )
  }

  // 幅が足りないときは fieldset ごと次の行へ落ちるだけで、グループの中身は
  // 崩さない（shrink-0 + flex-nowrap）。
  return (
    <>
      <fieldset className="flex shrink-0 flex-nowrap items-center gap-2 rounded-md border border-border px-2 py-1">
        <legend className="px-1 text-[11px] text-muted-foreground">動画</legend>
        <Label className="shrink-0" htmlFor="studio-quality">
          <span className="sr-only">動画</span>品質
        </Label>
        <div className="w-24">{qualitySelect}</div>
        <Label className="shrink-0" htmlFor="studio-aspect-ratio">
          <span className="sr-only">{'動画 '}</span>比率
        </Label>
        <div className="w-36">{aspectSelect}</div>
        <div className="w-20">{megapixelsInput}</div>
        <span className="shrink-0 text-[11px] text-muted-foreground">MP</span>
        <Label className="shrink-0" htmlFor="studio-steps">
          <span className="sr-only">{'動画 '}</span>steps
        </Label>
        <div className="w-24">{stepsInput}</div>
        {latentUpscaleToggle}
      </fieldset>
      <fieldset className="flex shrink-0 flex-nowrap items-center gap-2 rounded-md border border-border px-2 py-1">
        <legend className="px-1 text-[11px] text-muted-foreground">画像</legend>
        <Label className="shrink-0" htmlFor="studio-image-quality">
          <span className="sr-only">画像</span>品質
        </Label>
        <div className="w-24">{imageQualitySelect}</div>
        <Label className="shrink-0" htmlFor="studio-image-aspect-ratio">
          <span className="sr-only">{'画像 '}</span>比率
        </Label>
        <div className="w-36">{imageAspectSelect}</div>
        <div className="w-20">{imageMegapixelsInput}</div>
        <span className="shrink-0 text-[11px] text-muted-foreground">MP</span>
        <Label className="shrink-0" htmlFor="studio-image-steps">
          <span className="sr-only">{'画像 '}</span>steps
        </Label>
        <div className="w-24">{imageStepsInput}</div>
      </fieldset>
    </>
  )
}
