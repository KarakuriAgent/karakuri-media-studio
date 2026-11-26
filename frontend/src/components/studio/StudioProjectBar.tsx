import { useState } from 'react'
import { ArrowLeft, SlidersHorizontal } from 'lucide-react'

import { DEFAULT_MEGAPIXELS } from '../../form'
import type {
  ComfyTarget,
  Lora,
  LoraRef,
  StudioImageQuality,
  StudioRefineName,
  StudioVideoModel,
  StudioVideoModelChoice,
  StudioVideoQuality,
} from '../../types'
import { Modal } from '../ui'
import { Button } from '../ui/button'
import { Checkbox } from '../ui/checkbox'
import { Input } from '../ui/input'
import { Label } from '../ui/label'
import { NativeSelect } from '../NativeSelect'
import TargetSelector from '../TargetSelector'
import VideoLoraEditor from './VideoLoraEditor'
import {
  IMAGE_QUALITIES,
  IMAGE_QUALITY_HINT,
  IMAGE_QUALITY_LABEL,
  MAX_STEPS,
  REFINE_HINT,
  REFINE_LABEL,
  REFINE_NAMES,
  VIDEO_MODELS,
  VIDEO_MODEL_HINT,
  VIDEO_MODEL_LABEL,
  VIDEO_MODEL_QUALITY_NOTE,
  VIDEO_QUALITIES,
  VIDEO_QUALITY_HINT,
  VIDEO_QUALITY_LABEL,
  formatLoraList,
  videoModelIgnoresQuality,
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
  videoModel = 'minimax-h3',
  onVideoModelChange,
  videoModelChoices = [],
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
  refine,
  onRefineChange,
  refineAvailable = true,
  refineReason = '',
  videoLoras = [],
  registeredVideoLoras = [],
  onVideoLorasChange,
  busy,
}: {
  name: string
  isWide: boolean
  onBack: () => void
  comfyTarget?: ComfyTarget | null
  onComfyTarget?: (target: ComfyTarget) => void
  /** 動画モデル（作品既定。カット単位で上書きできる）。 */
  videoModel?: StudioVideoModel
  /** 省略すると動画モデルの欄を出さない。 */
  onVideoModelChange?: (model: StudioVideoModel) => void
  /** いまの接続先で選べるか（GET /api/studio/capabilities の `video_models`）。 */
  videoModelChoices?: StudioVideoModelChoice[]
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
  /**
   * 補正（作品既定。テイク生成のたびに上書きできる）。省略すると補正の欄を出さない。
   */
  refine?: Record<StudioRefineName, boolean>
  onRefineChange?: (name: StudioRefineName, value: boolean) => void
  /** いまの接続先で補正を ON にできるか（Comfy Cloud では false）。 */
  refineAvailable?: boolean
  /** `refineAvailable` が false のときに出す理由。 */
  refineReason?: string
  /** 作品共通の動画 LoRA（テイク生成のたびに上書きできる）。 */
  videoLoras?: LoraRef[]
  /** 動画用に登録された LoRA（いまの接続先のぶん）。 */
  registeredVideoLoras?: Lora[]
  /** 省略すると動画 LoRA の欄を出さない。 */
  onVideoLorasChange?: (loras: LoraRef[]) => void
  busy: boolean
}) {
  const [settingsOpen, setSettingsOpen] = useState(false)
  const summary = formatProjectSettingsSummary({
    target: onComfyTarget ? comfyTarget : undefined,
    quality,
    videoModel: onVideoModelChange ? videoModel : undefined,
    imageQuality,
    aspectRatio,
    megapixels,
    steps,
    imageAspectRatio,
    imageMegapixels,
    imageSteps,
    latentUpscale,
    videoLoraCount: onVideoLorasChange ? videoLoras.length : 0,
    // 使えない接続先では投入時に OFF へ落ちるので、要約にも出さない
    refine: refineAvailable ? refine : undefined,
  })

  const fields = (
    <ProjectSettingsFields
      stacked={!isWide}
      comfyTarget={comfyTarget}
      onComfyTarget={onComfyTarget}
      videoModel={videoModel}
      onVideoModelChange={onVideoModelChange}
      videoModelChoices={videoModelChoices}
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
      refine={refine}
      onRefineChange={onRefineChange}
      refineAvailable={refineAvailable}
      refineReason={refineReason}
      videoLoras={videoLoras}
      registeredVideoLoras={registeredVideoLoras}
      onVideoLorasChange={onVideoLorasChange}
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
  videoModel,
  onVideoModelChange,
  videoModelChoices,
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
  refine,
  onRefineChange,
  refineAvailable,
  refineReason,
  videoLoras,
  registeredVideoLoras,
  onVideoLorasChange,
  busy,
}: {
  stacked: boolean
  comfyTarget?: ComfyTarget | null
  onComfyTarget?: (target: ComfyTarget) => void
  videoModel: StudioVideoModel
  onVideoModelChange?: (model: StudioVideoModel) => void
  videoModelChoices: StudioVideoModelChoice[]
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
  refine?: Record<StudioRefineName, boolean>
  onRefineChange?: (name: StudioRefineName, value: boolean) => void
  refineAvailable: boolean
  refineReason: string
  videoLoras: LoraRef[]
  registeredVideoLoras: Lora[]
  onVideoLorasChange?: (loras: LoraRef[]) => void
  busy: boolean
}) {
  // 動画 LoRA は強度のスライダーを動かすたびに PATCH しないよう、モーダルの中で
  // 下書きを持ち、「保存」で丸ごと置き換える。
  const [loraDraft, setLoraDraft] = useState<LoraRef[] | null>(null)
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

  // 動画モデル。いまの接続先で使えないもの（Comfy Cloud の 10Eros Max）は選べなく
  // するが、すでに選んである値はそのまま見せる（投入時に理由つきで断られる）。
  const choiceOf = (model: StudioVideoModel) =>
    videoModelChoices.find((choice) => choice.id === model)
  const currentChoice = choiceOf(videoModel)
  const videoModelUnavailable =
    currentChoice !== undefined && !currentChoice.available
  const videoModelSelect = onVideoModelChange ? (
    <NativeSelect
      id="studio-video-model"
      value={videoModel}
      disabled={busy}
      title={[
        'この作品のカットを焼く動画モデル（カット単位で上書きできる）。',
        ...VIDEO_MODELS.map((value) => VIDEO_MODEL_HINT[value]),
        ...(videoModelUnavailable && currentChoice ? [currentChoice.reason] : []),
      ].join('\n')}
      onChange={(event) => onVideoModelChange(event.target.value as StudioVideoModel)}
    >
      {VIDEO_MODELS.map((value) => {
        const choice = choiceOf(value)
        const unavailable = choice !== undefined && !choice.available
        return (
          <option
            key={value}
            value={value}
            disabled={unavailable && value !== videoModel}
          >
            {VIDEO_MODEL_LABEL[value]}
            {unavailable ? '（この接続先では不可）' : ''}
          </option>
        )
      })}
    </NativeSelect>
  ) : null
  const videoModelNote =
    videoModelUnavailable && currentChoice ? (
      <p
        role="note"
        className={
          stacked
            ? 'text-[11px] text-amber-300'
            : 'max-w-56 text-[11px] leading-tight text-amber-300'
        }
      >
        {currentChoice.reason}
      </p>
    ) : null
  // 10Eros Max は品質の版も補正も持たない。H3 に戻したカットには効くので、
  // 欄は触れるままにして「効かない」ことだけを添える。
  const qualityIgnored = onVideoModelChange !== undefined && videoModelIgnoresQuality(videoModel)

  const qualitySelect = (
    <NativeSelect
      id="studio-quality"
      value={quality}
      disabled={busy}
      className={qualityIgnored ? 'opacity-60' : undefined}
      title={[
        ...(qualityIgnored ? [`品質は${VIDEO_MODEL_QUALITY_NOTE}`] : []),
        ...VIDEO_QUALITIES.map((value) => VIDEO_QUALITY_HINT[value]),
      ].join('\n')}
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
        '素材の静止画（P-H3-Image / Qwen-Image 2.1）の品質。動画の品質とは独立です。',
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
        'P-H3-Image は t2i 8MP・編集 4MP）'
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

  // 補正（FastDerope / FaceDetail / AnimeCadence）。カスタムノードが要るので、
  // 入れられない接続先（Comfy Cloud）ではトグルを無効にして理由を出す。チェックは
  // 作品設定のまま見せる（接続先を戻せばまた効く）。
  const refineToggles =
    refine && onRefineChange ? (
      <div
        className={
          stacked
            ? 'flex flex-col gap-2'
            : 'flex shrink-0 flex-nowrap items-center gap-3'
        }
      >
        {REFINE_NAMES.map((name) => (
          <div key={name} className="flex shrink-0 items-center gap-2">
            <Checkbox
              id={`studio-refine-${name}`}
              checked={refine[name]}
              disabled={busy || !refineAvailable}
              onCheckedChange={(checked) => onRefineChange(name, checked === true)}
            />
            <Label
              htmlFor={`studio-refine-${name}`}
              className={
                refineAvailable
                  ? 'cursor-pointer text-foreground/85'
                  : 'text-muted-foreground'
              }
              title={
                refineAvailable
                  ? REFINE_HINT[name]
                  : `${REFINE_HINT[name]}\n${refineReason}`
              }
            >
              {REFINE_LABEL[name]}
            </Label>
          </div>
        ))}
        {!refineAvailable && refineReason && (
          <p
            role="note"
            className={
              stacked
                ? 'text-[11px] text-muted-foreground'
                : 'max-w-56 text-[11px] leading-tight text-muted-foreground'
            }
          >
            {refineReason}
          </p>
        )}
        {refineAvailable && qualityIgnored && (
          <p
            role="note"
            className={
              stacked
                ? 'text-[11px] text-muted-foreground'
                : 'max-w-56 text-[11px] leading-tight text-muted-foreground'
            }
          >
            補正は{VIDEO_MODEL_QUALITY_NOTE}
          </p>
        )}
      </div>
    ) : null

  const videoLoraButton = onVideoLorasChange ? (
    <Button
      variant="outline"
      size="sm"
      className="shrink-0"
      disabled={busy}
      aria-haspopup="dialog"
      title={
        videoLoras.length > 0
          ? `作品共通の動画 LoRA: ${formatLoraList(videoLoras)}`
          : '作品共通の動画 LoRA（未設定）。テイク生成のたびに上書きできます'
      }
      onClick={() => setLoraDraft(videoLoras.map((lora) => ({ ...lora })))}
    >
      {stacked ? '動画 LoRA' : 'LoRA'}{' '}
      <span className="tnum text-muted-foreground">{videoLoras.length}</span>
    </Button>
  ) : null

  const videoLoraModal =
    onVideoLorasChange && loraDraft !== null ? (
      <Modal title="動画 LoRA（作品共通）" onClose={() => setLoraDraft(null)}>
        <p className="mb-3 text-xs text-muted-foreground">
          この作品のカットを焼くたびに挿す動画 LoRA です（上から順に直列で挿さり、
          トリガーワードはプロンプトの先頭に付きます）。生成ダイアログで 1 回だけ
          変えることもできます。LoRA を挿せないワークフローになったカットでは外して
          投入され、Take に警告が出ます。
        </p>
        <VideoLoraEditor
          registered={registeredVideoLoras}
          value={loraDraft}
          onChange={setLoraDraft}
          searchId="studio-project-lora-search"
        />
        <div className="mt-4 flex items-center justify-end gap-2">
          <Button variant="outline" onClick={() => setLoraDraft(null)}>
            やめる
          </Button>
          <Button
            disabled={busy}
            onClick={() => {
              onVideoLorasChange(loraDraft)
              setLoraDraft(null)
            }}
          >
            保存
          </Button>
        </div>
      </Modal>
    ) : null

  if (stacked) {
    return (
      <div className="flex flex-col gap-3">
        {targetSelector}
        <fieldset className="rounded-md border border-border px-3 pb-3">
          <legend className="px-1 text-[11px] text-muted-foreground">動画</legend>
          <div className="grid grid-cols-2 gap-2">
            {videoModelSelect && (
              <div className="col-span-2 flex flex-col items-stretch gap-1">
                <Label htmlFor="studio-video-model">
                  <span className="sr-only">動画</span>モデル
                </Label>
                {videoModelSelect}
                {videoModelNote}
              </div>
            )}
            <div className="flex flex-col items-stretch gap-1">
              <Label htmlFor="studio-quality">
                <span className="sr-only">動画</span>品質
              </Label>
              {qualitySelect}
              {qualityIgnored && (
                <p role="note" className="text-[11px] text-muted-foreground">
                  {VIDEO_MODEL_QUALITY_NOTE}
                </p>
              )}
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
            {videoLoraButton && <div className="col-span-2">{videoLoraButton}</div>}
          </div>
        </fieldset>
        {refineToggles && (
          <fieldset className="rounded-md border border-border px-3 pb-3">
            <legend className="px-1 text-[11px] text-muted-foreground">補正</legend>
            {refineToggles}
          </fieldset>
        )}
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
        {videoLoraModal}
      </div>
    )
  }

  // 幅が足りないときは fieldset ごと次の行へ落ちるだけで、グループの中身は
  // 崩さない（shrink-0 + flex-nowrap）。
  return (
    <>
      <fieldset className="flex shrink-0 flex-nowrap items-center gap-2 rounded-md border border-border px-2 py-1">
        <legend className="px-1 text-[11px] text-muted-foreground">動画</legend>
        {videoModelSelect && (
          <>
            <Label className="shrink-0" htmlFor="studio-video-model">
              <span className="sr-only">動画</span>モデル
            </Label>
            <div className="w-32">{videoModelSelect}</div>
            {videoModelNote}
          </>
        )}
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
        {videoLoraButton}
      </fieldset>
      {refineToggles && (
        <fieldset className="flex shrink-0 flex-nowrap items-center gap-2 rounded-md border border-border px-2 py-1">
          <legend className="px-1 text-[11px] text-muted-foreground">補正</legend>
          {refineToggles}
        </fieldset>
      )}
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
      {videoLoraModal}
    </>
  )
}
