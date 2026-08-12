import { useEffect, useRef, useState } from 'react'
import { api } from '../api'
import {
  AUTHOR_NEGATIVE_PROMPT,
  DEFAULT_NEGATIVE_PROMPT,
  MAX_STEPS,
  MODE_HINTS,
  MODE_LABELS,
  NEGATIVE_PRESET_LABELS,
  hiddenFields,
  imageWorkflowNeedsSource,
  elementsLimits,
  joinTriggers,
  lorasForTarget,
  matchesLoraQuery,
  megapixelsFor,
  multiShotLimits,
  needsReferenceSheet,
  newElement,
  newShot,
  promptChars,
  referenceFields,
  sheetSize,
  toSelected,
  toggleReference,
  workflowsForMode,
  type FormState,
  type ReferenceField,
  type SelectedLora,
} from '../form'
import type {
  Asset,
  ComfyTarget,
  Job,
  JobMode,
  KlingElement,
  LibraryItem,
  Lora,
  MultiShot,
  Options,
  WorkflowOption,
} from '../types'
import AudioFields from './AudioFields'
import HistoryPickerModal, {
  assetExtension,
  type HistoryCandidate,
  type HistoryKind,
} from './HistoryPickerModal'
import LibraryPickerModal from './LibraryPickerModal'
import ModelPicker from './ModelPicker'
import SheetBuilderModal from './SheetBuilderModal'
import TargetSelector from './TargetSelector'
import WorkflowPicker from './WorkflowPicker'
import WorkflowSelects from './WorkflowSelects'
import { Banner, FieldError, Modal, Section } from './ui'

// 音声も「モード」の一つ。ただし走るのは音声ワークフロー 1 本きりで、画像→動画の
// 連結（full）とは繋がらない独立ジョブ。
const MODES: JobMode[] = ['full', 'i2v', 'image_only', 'audio']

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
  /**
   * ComfyUI の接続先（`GET /api/settings` の `comfy_target`、SPEC §5）。設定を
   * 読み込む前は null で、プルダウンは無効になる。
   */
  comfyTarget: ComfyTarget | null
  /** 接続先を変える（App が `PUT /api/settings` で保存する） */
  onComfyTarget: (target: ComfyTarget) => void
  /** NSFW フィルタ前の全ジョブ（履歴モーダルが自前でフィルタする） */
  jobs: Job[]
  /** ヘッダーの NSFW 表示トグル（履歴モーダルの初期値） */
  showNsfw: boolean
  /** ライブラリが変わるたびに増える値（開いているモーダルを読み直させる） */
  libraryVersion?: number
}

/**
 * 選択モーダル（履歴 / ライブラリ）の選択先: どの種別を選び、どの欄へ入れるか。
 *
 * 履歴とライブラリで種別の区分は同じ（image / video / audio）なので共用する。
 */
interface PickerTarget {
  kind: HistoryKind
  /** モーダルのタイトルに使う欄名（「開始フレーム」など） */
  title: string
  apply: (url: string) => Partial<FormState>
  /**
   * 参照素材の欄から開いたときの宣言（マルチモーダル参照、SPEC §3.1）。
   * 単発の入れ替えではなく**選択の出し入れ**になり、ライブラリのモーダルは
   * 複数選べるよう開いたままになる。
   */
  reference?: ReferenceField
  /**
   * Elements の何番目の要素の参照画像を選んでいるか（SPEC §3.1）。参照素材と
   * 同じく**選択の出し入れ**になり、ライブラリのモーダルは開いたままになる。
   */
  element?: { index: number; limit: number }
}

/** LoRA chips + strength sliders + trigger words, shared by both stages. */
function LoraPicker({
  loras,
  selected,
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
  triggerText: string
  triggerDirty: boolean
  emptyHint: string
  onToggle: (lora: Lora) => void
  onStrength: (index: number, strength: number) => void
  onTrigger: (value: string) => void
  onTriggerReset: () => void
}) {
  const [pickerOpen, setPickerOpen] = useState(false)
  const [query, setQuery] = useState('')
  const visibleLoras = loras.filter((lora) => matchesLoraQuery(lora, query))

  return (
    <div>
      {loras.length === 0 ? (
        <p className="text-xs text-slate-500">{emptyHint}</p>
      ) : (
        <div className="flex flex-wrap items-center gap-2">
          <button
            className="btn-ghost text-xs"
            onClick={() => {
              // 前回の検索語が残っていると絞り込まれた状態で開いてしまうため、
              // 開くたびに検索欄をリセットする。
              setQuery('')
              setPickerOpen(true)
            }}
          >
            LoRAを選ぶ
          </button>
          <span className="rounded-full border border-ink-600 bg-ink-800 px-2 py-1 text-[11px] text-slate-400">
            選択 {selected.length} / 候補 {loras.length}
          </span>
          {selected.length === 0 && (
            <span className="text-[11px] text-slate-600">未選択</span>
          )}
        </div>
      )}

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
          onChange={(event) => onTrigger(event.target.value)}
        />
      </div>

      {pickerOpen && (
        <Modal title="LoRAを選択" onClose={() => setPickerOpen(false)} wide closeOnBackdrop>
          <div className="sticky top-0 z-10 -mx-1 mb-3 border-b border-ink-600 bg-ink-800/95 px-1 pb-3 backdrop-blur">
            <label className="label" htmlFor="lora-picker-search">
              名前・ファイル名・トリガーで検索
            </label>
            <div className="flex items-center gap-2">
              <input
                id="lora-picker-search"
                className="field"
                placeholder="LoRAを検索"
                value={query}
                autoFocus
                onChange={(event) => setQuery(event.target.value)}
              />
              {query && (
                <button className="btn-ghost shrink-0 text-xs" onClick={() => setQuery('')}>
                  クリア
                </button>
              )}
            </div>
            <div className="mt-2 flex items-center justify-between text-[11px] text-slate-500">
              <span>
                表示 {visibleLoras.length} / 全 {loras.length}
              </span>
              <span>選択中 {selected.length}件</span>
            </div>
          </div>

          {visibleLoras.length === 0 ? (
            <p className="rounded-lg border border-dashed border-ink-600 p-6 text-center text-xs text-slate-500">
              条件に一致するLoRAがありません
            </p>
          ) : (
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
              {visibleLoras.map((lora) => {
                const active = selected.some((item) => item.id === lora.id)
                const sample = lora.sample_images[0]
                return (
                  <button
                    key={lora.id}
                    type="button"
                    aria-label={lora.display_name}
                    aria-pressed={active}
                    className={`flex min-h-20 items-center gap-3 rounded-lg border p-2 text-left transition-colors ${
                      active
                        ? 'border-accent-500 bg-accent-500/15 text-accent-300'
                        : 'border-ink-600 bg-ink-800 text-slate-300 hover:border-ink-500 hover:bg-ink-700'
                    }`}
                    title={lora.lora_name}
                    onClick={() => onToggle(lora)}
                  >
                    {sample ? (
                      <img
                        src={sample}
                        alt=""
                        aria-hidden="true"
                        loading="lazy"
                        className="h-14 w-14 shrink-0 rounded-md border border-ink-600 object-cover"
                      />
                    ) : (
                      <span
                        aria-hidden="true"
                        className="flex h-14 w-14 shrink-0 items-center justify-center rounded-md border border-ink-600 bg-ink-900 text-lg font-semibold text-slate-600"
                      >
                        L
                      </span>
                    )}
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-xs font-medium">
                        {lora.display_name}
                      </span>
                      <span className="mt-1 block truncate text-[10px] text-slate-500">
                        {lora.trigger_word || 'トリガーなし'}
                      </span>
                      <span className="block text-[10px] tabular-nums text-slate-600">
                        強度 {lora.default_strength.toFixed(2)}
                      </span>
                    </span>
                    <span
                      aria-hidden="true"
                      className={`flex h-5 w-5 shrink-0 items-center justify-center rounded-full border text-[11px] ${
                        active
                          ? 'border-accent-500 bg-accent-500 text-white'
                          : 'border-ink-500 text-transparent'
                      }`}
                    >
                      ✓
                    </span>
                  </button>
                )
              })}
            </div>
          )}

          <div className="sticky bottom-0 -mx-1 mt-4 flex items-center justify-between border-t border-ink-600 bg-ink-800/95 px-1 pt-3 backdrop-blur">
            <span className="text-xs text-slate-500">{selected.length}件を選択中</span>
            <button className="btn-primary text-xs" onClick={() => setPickerOpen(false)}>
              選択を完了
            </button>
          </div>
        </Modal>
      )}
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
  onOpenHistory,
  onOpenLibrary,
  onOpenSheet,
  children,
}: {
  kind: 'image' | 'video'
  value: string
  assets: Asset[]
  busy: boolean
  onPick: (url: string) => void
  onUpload: (file: File) => void
  /** 履歴から選ぶモーダルを開く（渡さなければボタンを出さない） */
  onOpenHistory?: () => void
  /** ライブラリから選ぶモーダルを開く（同上） */
  onOpenLibrary?: () => void
  /** ライブラリの素材からリファレンスシートを合成する（同上、SPEC §7.2） */
  onOpenSheet?: () => void
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
        {onOpenLibrary && (
          <button className="btn-ghost text-xs" disabled={busy} onClick={onOpenLibrary}>
            ライブラリから選択
          </button>
        )}
        {onOpenSheet && (
          <button className="btn-ghost text-xs" disabled={busy} onClick={onOpenSheet}>
            ライブラリから作成
          </button>
        )}
        {onOpenHistory && (
          <button className="btn-ghost text-xs" disabled={busy} onClick={onOpenHistory}>
            履歴から選択
          </button>
        )}
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

/**
 * 選んである URL に対応するライブラリ id（**選んだ順**）。
 *
 * フォームは URL で持つが、ライブラリの複数選択モードは id で選択状態を描くので
 * ここで引き当てる。ライブラリ以外（アップロード・履歴）から入れた素材は id を
 * 持たないので、単に選択済みバッジが付かないだけで済む。
 */
export function selectedLibraryIds(
  library: LibraryItem[],
  urls: string[],
): string[] {
  return urls.flatMap((url) => {
    const hit = library.find((item) => item.url === url)
    return hit ? [hit.id] : []
  })
}

/**
 * 複数ファイルを取る参照入力 1 欄（マルチモーダル参照、SPEC §3.1 / §8）。
 *
 * 1 本きりの :func:`AssetPicker` と違い、選んだものが**順番つきで積み上がる**
 * （並び順がそのまま外部 API に渡る配列の順序）。上限に達したら追加の操作を
 * 無効にして、送る前に件数で 422 にならないようにする。
 */
function ReferencePicker({
  item,
  values,
  busy,
  onUpload,
  onOpenLibrary,
  onOpenHistory,
  onRemove,
}: {
  item: ReferenceField
  values: string[]
  busy: boolean
  onUpload: (file: File) => void
  onOpenLibrary: () => void
  onOpenHistory: () => void
  onRemove: (url: string) => void
}) {
  const input = useRef<HTMLInputElement>(null)
  const full = values.length >= item.limit

  return (
    <div className="flex flex-col gap-2 rounded-lg border border-dashed border-ink-600 p-2">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs text-slate-300">{item.label}</span>
        <span className="text-[11px] tabular-nums text-slate-500">
          {values.length} / {item.limit} 件
        </span>
        <input
          ref={input}
          type="file"
          accept={`${item.kind}/*`}
          className="hidden"
          onChange={(event) => {
            const file = event.target.files?.[0]
            event.target.value = ''
            if (file) onUpload(file)
          }}
        />
        <button
          className="btn-ghost ml-auto text-xs"
          disabled={busy || full}
          onClick={() => input.current?.click()}
        >
          アップロード
        </button>
        <button
          className="btn-ghost text-xs"
          disabled={busy}
          onClick={onOpenLibrary}
        >
          ライブラリから選択
        </button>
        <button
          className="btn-ghost text-xs"
          disabled={busy || full}
          onClick={onOpenHistory}
        >
          履歴から選択
        </button>
      </div>
      {values.length > 0 && (
        <ul className="flex flex-col gap-1">
          {values.map((url, index) => (
            <li key={url} className="flex items-center gap-2">
              <span className="w-5 shrink-0 text-[11px] tabular-nums text-slate-500">
                {index + 1}.
              </span>
              {item.kind === 'image' && (
                <img
                  src={url}
                  alt=""
                  className="h-10 w-10 shrink-0 rounded border border-ink-600 object-cover"
                />
              )}
              <span className="flex-1 truncate text-[11px] text-slate-400">{url}</span>
              <button className="btn-ghost text-xs" onClick={() => onRemove(url)}>
                外す
              </button>
            </li>
          ))}
        </ul>
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
  comfyTarget,
  onComfyTarget,
  jobs,
  showNsfw,
  libraryVersion,
}: Props) {
  const [uploadError, setUploadError] = useState<string | null>(null)
  const [busyUpload, setBusyUpload] = useState(false)
  const [showAdvanced, setShowAdvanced] = useState(false)
  // 構造化パラメータは行が増えてフォームが伸びるので、既定は畳んでおく（§8）
  const [showShots, setShowShots] = useState(false)
  const [showElements, setShowElements] = useState(false)
  // 履歴 / ライブラリのモーダルを開いている入力欄（null = 閉じている）
  const [historyTarget, setHistoryTarget] = useState<PickerTarget | null>(null)
  const [libraryTarget, setLibraryTarget] = useState<PickerTarget | null>(null)
  // リファレンスシートの合成モーダル（IC-LoRA の画像欄でだけ開ける）
  const [buildingSheet, setBuildingSheet] = useState(false)

  const registeredLoras: Lora[] = options?.loras ?? []
  const audioAssets = options?.audio_assets ?? []
  const imageAssets = options?.image_assets ?? []
  const library = options?.library ?? []
  const videoAssets = options?.video_assets ?? []
  const aspectRatios = options?.aspect_ratios ?? []
  const videoWorkflows: WorkflowOption[] = options?.video_workflows ?? []
  const imageWorkflows: WorkflowOption[] = options?.image_workflows ?? []
  const negativePresets = options?.negative_presets ?? {
    current: DEFAULT_NEGATIVE_PROMPT,
    author: AUTHOR_NEGATIVE_PROMPT,
  }

  const usable = workflowsForMode(form.mode, videoWorkflows)
  const workflow =
    videoWorkflows.find((item) => item.id === form.videoWorkflow) ?? null
  const imageWorkflow =
    imageWorkflows.find((item) => item.id === form.imageWorkflow) ?? null
  // 使わない項目は無効化ではなく描画しない（値は FormState に残るので、
  // 使うモードに戻せば入力はそのまま復元される）。
  const hidden = hiddenFields(form.mode, workflow, imageWorkflow)
  const imageEdits = form.mode !== 'i2v' && imageWorkflowNeedsSource(imageWorkflow)
  // プロンプトを選択項目から組み立てるワークフローでは任意入力。
  const promptOptional = workflow?.prompt_required === false
  // 編集系の画像ワークフローでは入力画像そのもの、それ以外は動画の開始フレーム。
  const startImageLabel = imageEdits
    ? (imageWorkflow?.image_label ?? '編集元画像')
    : (workflow?.image_label ?? '開始フレーム')
  // マルチモーダル参照。宣言の無いワークフローでは空なので、
  // 参照素材のセクションそのものが出ない。
  const references = hidden.references ? [] : referenceFields(workflow)
  const addReference = (item: ReferenceField, url: string) => {
    if (form[item.field].includes(url) || form[item.field].length >= item.limit) return
    patch({ [item.field]: [...form[item.field], url] } as Partial<FormState>)
  }
  const toggleRef = (item: ReferenceField, url: string) => {
    if (!form[item.field].includes(url) && form[item.field].length >= item.limit) return
    patch({
      [item.field]: toggleReference(form[item.field], url),
    } as Partial<FormState>)
  }
  // ショット割り / Elements（SPEC §3.1）。宣言の無いワークフローや
  // 動画ステージを走らせない mode では null なので、セクションごと出ない。
  const shotLimits = hidden.multiShots ? null : multiShotLimits(workflow)
  const elementLimits = hidden.elements ? null : elementsLimits(workflow)
  /** ``@要素名`` の消費ぶんを含めた残り文字数（上限の宣言が無ければ null）。 */
  const charsLeft = (text: string) => {
    const limit = workflow?.max_prompt_chars ?? 0
    if (!limit) return null
    return limit - promptChars(text, elementLimits?.reference_chars ?? 0)
  }
  const patchShot = (index: number, change: Partial<MultiShot>) =>
    patch({
      multiShots: form.multiShots.map((shot, at) =>
        at === index ? { ...shot, ...change } : shot,
      ),
    })
  const patchElement = (index: number, change: Partial<KlingElement>) =>
    patch({
      klingElements: form.klingElements.map((element, at) =>
        at === index ? { ...element, ...change } : element,
      ),
    })
  const toggleElementImage = (index: number, url: string) => {
    const images = form.klingElements[index]?.images ?? []
    if (!images.includes(url) && images.length >= (elementLimits?.max_images ?? 0)) {
      return
    }
    patchElement(index, { images: toggleReference(images, url) })
  }
  // 画像欄がリファレンスシート（IC-LoRA）なら、ライブラリの素材から合成できる。
  // 画像を編集するワークフローのときの欄は別物（編集元画像）なので出さない。
  const sheetInput =
    form.mode !== 'image_only' && !imageEdits && needsReferenceSheet(workflow)

  // Image LoRAs are family-scoped: only the ones matching the selected image
  // workflow can be used (the backend rejects the rest).
  const imageLoras = lorasForTarget(
    registeredLoras,
    'image',
    imageWorkflow?.family,
  )
  const videoLoras = lorasForTarget(registeredLoras, 'video')

  // Full generation needs a workflow that can take the generated still; switch
  // away from e.g. t2v instead of letting the request 422.
  useEffect(() => {
    if (usable.length === 0) return
    if (!usable.some((item) => item.id === form.videoWorkflow)) {
      patch({ videoWorkflow: usable[0].id })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [form.mode, form.videoWorkflow, videoWorkflows])

  // モデルによって想定している画角が違うので、動画ステージが走るモードで
  // 動画ワークフローを切り替えたら「メガピクセル」もそのモデルの既定に戻す
  // （MiniMax H3 は 0.4MP 前提。1.0MP のままだと VRAM が足りない、SPEC §3.1）。
  // 切り替えたあとに手で変えた値はそのまま（次に切り替えるまで維持される）。
  useEffect(() => {
    if (form.mode !== 'full' && form.mode !== 'i2v') return
    if (videoWorkflows.length === 0) return
    const next = megapixelsFor(workflow)
    if (next !== form.megapixels) patch({ megapixels: next })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [form.mode, form.videoWorkflow, videoWorkflows])

  // Switching the image workflow can switch the model family: LoRAs of the old
  // family would be rejected by the backend, so drop them from the selection.
  useEffect(() => {
    if (!imageWorkflow || form.loras.length === 0) return
    const usableIds = new Set(imageLoras.map((item) => item.id))
    const kept = form.loras.filter((item) => usableIds.has(item.id))
    if (kept.length === form.loras.length) return
    patch({
      loras: kept,
      ...(form.triggerDirty ? {} : { triggerText: joinTriggers(kept) }),
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [form.imageWorkflow, imageLoras.length, form.loras])

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

  /**
   * Outputs live outside assets/, so copy the picked one into assets via upload.
   *
   * 開始フレーム / 最後のフレーム / 参照動画 / リファレンス音声で共通。どの欄に
   * 入れるかは開いたときの `historyTarget` が持っている。
   */
  const useFromHistory = async (
    target: PickerTarget,
    candidate: HistoryCandidate,
  ) => {
    setHistoryTarget(null)
    setUploadError(null)
    setBusyUpload(true)
    try {
      const response = await fetch(candidate.url)
      if (!response.ok) {
        throw new Error(`履歴の${target.title}を取得できません (${response.status})`)
      }
      const blob = await response.blob()
      const name = `${candidate.source}_${candidate.job.id}${assetExtension(
        candidate.url,
        target.kind,
      )}`
      const file = new File([blob], name, { type: blob.type })
      const asset =
        target.kind === 'image'
          ? await api.uploadImage(file)
          : target.kind === 'video'
            ? await api.uploadVideo(file)
            : await api.uploadAudio(file)
      // 参照素材 / Elements の欄は入れ替えではなく積み上げ（選んだ順 = API の順）
      if (target.reference) addReference(target.reference, asset.url)
      else if (target.element) toggleElementImage(target.element.index, asset.url)
      else patch(target.apply(asset.url))
      onReloadOptions()
    } catch (error) {
      setUploadError(error instanceof Error ? error.message : String(error))
    } finally {
      setBusyUpload(false)
    }
  }

  /** ライブラリに追加して、そのまま欄に入れる（リファレンス音声のアップロード）。 */
  const uploadToLibrary = async (
    kind: 'image' | 'video' | 'audio',
    file: File,
    field: keyof FormState,
  ) => {
    setUploadError(null)
    setBusyUpload(true)
    try {
      const item = await api.uploadToLibrary(kind, file)
      patch({ [field]: item.url } as Partial<FormState>)
      onReloadOptions()
    } catch (error) {
      setUploadError(error instanceof Error ? error.message : String(error))
    } finally {
      setBusyUpload(false)
    }
  }

  /** 選択中のリファレンス音声の表示名（ライブラリ → アセット → 生の値の順）。 */
  const audioLabel =
    library.find((item) => item.url === form.audioPath)?.name ??
    audioAssets.find((asset) => asset.url === form.audioPath)?.name ??
    form.audioPath

  const audioInput = useRef<HTMLInputElement>(null)

  return (
    <div className="flex flex-col gap-3">
      {/* ComfyUI の接続先（SPEC §5）。選択はサーバー側の設定に保存されるので、
          次に開いたときも前回の接続先が使われる。詳しい接続情報は設定ページ。 */}
      <TargetSelector target={comfyTarget} onChange={onComfyTarget} />

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

      {/* 音声モード: 音声ワークフロー 1 本きりの独立ジョブ（画像・動画とは無関係） */}
      {form.mode === 'audio' && (
        <AudioFields
          form={form}
          patch={patch}
          options={options}
          onOpenChat={onOpenChat}
          fieldErrors={fieldErrors}
        />
      )}

      {/* 画像・動画のセクション一式（音声モードでは丸ごと出さない） */}
      {form.mode !== 'audio' && (
        <>
          {form.mode !== 'image_only' && (
            <Section title="動画ワークフロー">
              <WorkflowPicker
                workflows={usable}
                value={form.videoWorkflow}
                onChange={(id) => patch({ videoWorkflow: id })}
                modelLabel="動画モデル"
                modeLabel="動画モード"
                fallbackLabel="動画ワークフロー"
              />
              {/* ワークフローが宣言した選択式フィールド（踊りの
                  種類・動きの大きさ・尺。§3.1） */}
              <WorkflowSelects workflow={workflow} form={form} patch={patch} />
              <ModelPicker
                slots={options?.model_slots}
                workflowId={form.videoWorkflow}
                form={form}
                patch={patch}
              />
            </Section>
          )}

          {form.mode !== 'i2v' && (
            <Section title="画像ワークフロー">
              <WorkflowPicker
                workflows={imageWorkflows}
                value={form.imageWorkflow}
                onChange={(id) => patch({ imageWorkflow: id })}
                modelLabel="画像モデル"
                modeLabel="画像モード"
                fallbackLabel="画像ワークフロー"
              />
              {imageEdits && (
                <p className="mt-1 text-[11px] text-amber-400">
                  入力画像を編集するワークフローです。参照画像が必須で、解像度は入力画像から決まります。
                </p>
              )}
              {/* 画像ワークフローが宣言した選択式フィールド（
                  大きさ・品質。§3.1 / §5.4） */}
              <WorkflowSelects
                workflow={imageWorkflow}
                form={form}
                patch={patch}
              />
              <ModelPicker
                slots={options?.model_slots}
                workflowId={form.imageWorkflow}
                form={form}
                patch={patch}
              />
            </Section>
          )}

          {!hidden.startImage && (
            <Section title={startImageLabel}>
              <AssetPicker
                kind="image"
                value={form.sourceImage}
                assets={imageAssets}
                busy={busyUpload}
                onPick={(url) => patch({ sourceImage: url })}
                onUpload={(file) => void upload('image', file, (url) => ({ sourceImage: url }))}
                onOpenHistory={() =>
                  setHistoryTarget({
                    kind: 'image',
                    title: startImageLabel,
                    apply: (url) => ({ sourceImage: url }),
                  })
                }
                onOpenLibrary={() =>
                  setLibraryTarget({
                    kind: 'image',
                    title: startImageLabel,
                    apply: (url) => ({ sourceImage: url }),
                  })
                }
                onOpenSheet={sheetInput ? () => setBuildingSheet(true) : undefined}
              />
              <FieldError message={fieldErrors.source_image} />
            </Section>
          )}

          {!hidden.endImage && (
            <Section title="最後のフレーム">
              <AssetPicker
                kind="image"
                value={form.endImage}
                assets={imageAssets}
                busy={busyUpload}
                onPick={(url) => patch({ endImage: url })}
                onUpload={(file) => void upload('image', file, (url) => ({ endImage: url }))}
                onOpenHistory={() =>
                  setHistoryTarget({
                    kind: 'image',
                    title: '最後のフレーム',
                    apply: (url) => ({ endImage: url }),
                  })
                }
                onOpenLibrary={() =>
                  setLibraryTarget({
                    kind: 'image',
                    title: '最後のフレーム',
                    apply: (url) => ({ endImage: url }),
                  })
                }
              />
              <FieldError message={fieldErrors.end_image} />
            </Section>
          )}

          {!hidden.referenceVideo && (
            <Section title="参照動画（モーション転写）">
              <AssetPicker
                kind="video"
                value={form.referenceVideo}
                assets={videoAssets}
                busy={busyUpload}
                onPick={(url) => patch({ referenceVideo: url })}
                onUpload={(file) => void upload('video', file, (url) => ({ referenceVideo: url }))}
                onOpenHistory={() =>
                  setHistoryTarget({
                    kind: 'video',
                    title: '参照動画',
                    apply: (url) => ({ referenceVideo: url }),
                  })
                }
                onOpenLibrary={() =>
                  setLibraryTarget({
                    kind: 'video',
                    title: '参照動画',
                    apply: (url) => ({ referenceVideo: url }),
                  })
                }
              />
              <FieldError message={fieldErrors.reference_video} />
            </Section>
          )}

          {references.length > 0 && (
            <Section title="マルチモーダル参照（素材参照ワークフロー）">
              <div className="flex flex-col gap-2">
                {references.map((item) => (
                  <div key={item.name}>
                    <ReferencePicker
                      item={item}
                      values={form[item.field]}
                      busy={busyUpload}
                      onUpload={(file) =>
                        void upload(item.kind, file, (url) => ({
                          [item.field]: [...form[item.field], url],
                        }) as Partial<FormState>)
                      }
                      onOpenLibrary={() =>
                        setLibraryTarget({
                          kind: item.kind,
                          title: item.label,
                          reference: item,
                          apply: (url) => ({ [item.field]: [url] }) as Partial<FormState>,
                        })
                      }
                      onOpenHistory={() =>
                        setHistoryTarget({
                          kind: item.kind,
                          title: item.label,
                          reference: item,
                          apply: (url) => ({ [item.field]: [url] }) as Partial<FormState>,
                        })
                      }
                      onRemove={(url) =>
                        patch({
                          [item.field]: form[item.field].filter((one) => one !== url),
                        } as Partial<FormState>)
                      }
                    />
                    <FieldError message={fieldErrors[item.name]} />
                  </div>
                ))}
              </div>
              <FieldError message={fieldErrors.references} />
            </Section>
          )}

          {!hidden.audio && (
            <Section title="リファレンス音声">
              {/* 音声の一覧はライブラリに一本化した（SPEC §7.2）。アップロードも
                  そのままライブラリ登録になるので、選んだものは次回も残る。 */}
              <div className="flex flex-col gap-2">
                <div className="flex flex-wrap items-center gap-2">
                  <input
                    ref={audioInput}
                    type="file"
                    accept="audio/*"
                    className="hidden"
                    onChange={(event) => {
                      const file = event.target.files?.[0]
                      event.target.value = ''
                      if (file) void uploadToLibrary('audio', file, 'audioPath')
                    }}
                  />
                  <button
                    className="btn-ghost text-xs"
                    disabled={busyUpload}
                    onClick={() =>
                      setLibraryTarget({
                        kind: 'audio',
                        title: 'リファレンス音声',
                        apply: (url) => ({ audioPath: url }),
                      })
                    }
                  >
                    ライブラリから選択
                  </button>
                  <button
                    className="btn-ghost text-xs"
                    disabled={busyUpload}
                    onClick={() =>
                      setHistoryTarget({
                        kind: 'audio',
                        title: 'リファレンス音声',
                        apply: (url) => ({ audioPath: url }),
                      })
                    }
                  >
                    履歴から選択
                  </button>
                  <button
                    className="btn-ghost text-xs"
                    disabled={busyUpload}
                    onClick={() => audioInput.current?.click()}
                  >
                    {busyUpload ? 'アップロード中…' : 'アップロード'}
                  </button>
                  {form.audioPath && (
                    <button
                      className="btn-ghost text-xs"
                      onClick={() => patch({ audioPath: '' })}
                    >
                      クリア
                    </button>
                  )}
                </div>
                {form.audioPath ? (
                  <div className="flex items-center gap-2">
                    <span
                      className="max-w-[12rem] truncate text-xs text-slate-300"
                      title={form.audioPath}
                    >
                      {audioLabel}
                    </span>
                    <audio className="h-8 flex-1" controls src={form.audioPath} />
                  </div>
                ) : (
                  <p className="text-[11px] text-slate-500">（未選択）</p>
                )}
                <FieldError message={fieldErrors.audio_path} />
              </div>
            </Section>
          )}

          {!hidden.videoLoras && (
            <Section title="LoRA（動画）">
              <LoraPicker
                loras={videoLoras}
                selected={form.videoLoras}
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
            </Section>
          )}

          {!hidden.resolution && (
            <Section title="解像度">
              {imageEdits && (
                <p className="mb-2 text-[11px] text-amber-400">
                  選択中の画像ワークフローは入力画像から解像度を決めます。ここの設定は動画側にのみ効きます。
                </p>
              )}
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
            </Section>
          )}

          {!hidden.loras && (
            <Section title="LoRA（画像）">
              <LoraPicker
                loras={imageLoras}
                selected={form.loras}
                triggerText={form.triggerText}
                triggerDirty={form.triggerDirty}
                emptyHint={`画像用の登録済み LoRA がありません${
                  imageWorkflow ? `（${imageWorkflow.label} と同じファミリーのもの）` : ''
                }（設定 → LoRA 管理で追加）`}
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
            </Section>
          )}

          <Section
            title="プロンプト"
            right={
              <button className="btn-ghost !py-1 text-xs" onClick={onOpenChat}>
                Grokで生成
              </button>
            }
          >
            <div className="flex flex-col gap-3">
              {!hidden.imagePrompt && (
                <div>
                  <label className="label">画像プロンプト</label>
                  <textarea
                    className="field h-28 resize-y"
                    value={form.imagePrompt}
                    placeholder="自然文 1 段落で詳細に"
                    onChange={(event) => patch({ imagePrompt: event.target.value })}
                  />
                  <FieldError message={fieldErrors.image_prompt} />
                </div>
              )}
              {!hidden.videoPrompt && (
                <div>
                  <label className="label">
                    動画プロンプト
                    {promptOptional && (
                      <span className="ml-1 font-normal text-slate-500">
                        （任意）
                      </span>
                    )}
                  </label>
                  <textarea
                    className="field h-28 resize-y"
                    value={form.videoPrompt}
                    placeholder={
                      promptOptional
                        ? '空欄でよい（選択項目からプロンプトが組み立てられます）'
                        : '1 段落 4〜8 文。動き・カメラ・音声を含める'
                    }
                    onChange={(event) => patch({ videoPrompt: event.target.value })}
                  />
                  <div className="flex items-center gap-2">
                    <FieldError message={fieldErrors.video_prompt} />
                    {charsLeft(form.videoPrompt) !== null && (
                      <span
                        className={`ml-auto text-[11px] tabular-nums ${
                          charsLeft(form.videoPrompt)! < 0
                            ? 'text-rose-400'
                            : 'text-slate-500'
                        }`}
                      >
                        残り {charsLeft(form.videoPrompt)} 文字
                      </span>
                    )}
                  </div>
                </div>
              )}
            </div>
          </Section>

          {shotLimits && (
            <Section
              title="マルチショット"
              right={
                <button
                  className="text-xs text-slate-400 hover:text-slate-200"
                  onClick={() => setShowShots((value) => !value)}
                >
                  {showShots
                    ? '閉じる'
                    : `開く（${form.multiShots.length} / ${shotLimits.max_shots} ショット）`}
                </button>
              }
            >
              {showShots && (
                <div className="flex flex-col gap-2">
                  {form.multiShots.map((shot, index) => (
                    <div
                      key={index}
                      className="flex flex-col gap-1 rounded-lg border border-dashed border-ink-600 p-2"
                    >
                      <div className="flex items-center gap-2">
                        <span className="text-xs text-slate-300">
                          Shot {index + 1}
                        </span>
                        {charsLeft(shot.prompt) !== null && (
                          <span
                            className={`text-[11px] tabular-nums ${
                              charsLeft(shot.prompt)! < 0
                                ? 'text-rose-400'
                                : 'text-slate-500'
                            }`}
                          >
                            残り {charsLeft(shot.prompt)} 文字
                          </span>
                        )}
                        <label className="ml-auto text-[11px] text-slate-500">
                          秒数
                        </label>
                        <input
                          type="number"
                          className="field w-20 !py-1 text-xs"
                          min={shotLimits.min_duration}
                          max={shotLimits.max_duration}
                          value={shot.duration}
                          aria-label={`Shot ${index + 1} の秒数`}
                          onChange={(event) =>
                            patchShot(index, {
                              duration: Number(event.target.value),
                            })
                          }
                        />
                        <button
                          className="btn-ghost text-xs"
                          onClick={() =>
                            patch({
                              multiShots: form.multiShots.filter(
                                (_, at) => at !== index,
                              ),
                            })
                          }
                        >
                          削除
                        </button>
                      </div>
                      <textarea
                        className="field h-20 resize-y"
                        value={shot.prompt}
                        aria-label={`Shot ${index + 1} のプロンプト`}
                        placeholder="カメラの動き、動作、画面内の位置、音"
                        onChange={(event) =>
                          patchShot(index, { prompt: event.target.value })
                        }
                      />
                      <FieldError message={fieldErrors[`multi_shots.${index}`]} />
                    </div>
                  ))}
                  <button
                    className="btn-ghost self-start text-xs"
                    disabled={form.multiShots.length >= shotLimits.max_shots}
                    onClick={() =>
                      patch({
                        multiShots: [...form.multiShots, newShot(shotLimits)],
                      })
                    }
                  >
                    ショットを追加
                  </button>
                  <FieldError message={fieldErrors.multi_shots} />
                </div>
              )}
            </Section>
          )}

          {elementLimits && (
            <Section
              title="Elements（@要素名 でのキャラ固定）"
              right={
                <button
                  className="text-xs text-slate-400 hover:text-slate-200"
                  onClick={() => setShowElements((value) => !value)}
                >
                  {showElements
                    ? '閉じる'
                    : `開く（${form.klingElements.length} / ${elementLimits.max_elements} 要素）`}
                </button>
              }
            >
              {showElements && (
                <div className="flex flex-col gap-2">
                  {form.klingElements.map((element, index) => (
                    <div
                      key={index}
                      className="flex flex-col gap-1 rounded-lg border border-dashed border-ink-600 p-2"
                    >
                      <div className="flex items-center gap-2">
                        <input
                          className="field w-32 !py-1 text-xs"
                          value={element.name}
                          aria-label={`要素 ${index + 1} の名前`}
                          placeholder="kaori"
                          onChange={(event) =>
                            patchElement(index, { name: event.target.value })
                          }
                        />
                        <input
                          className="field flex-1 !py-1 text-xs"
                          value={element.description}
                          aria-label={`要素 ${index + 1} の説明`}
                          placeholder="灰色のコートの女性"
                          onChange={(event) =>
                            patchElement(index, {
                              description: event.target.value,
                            })
                          }
                        />
                        <button
                          className="btn-ghost text-xs"
                          onClick={() =>
                            patch({
                              klingElements: form.klingElements.filter(
                                (_, at) => at !== index,
                              ),
                            })
                          }
                        >
                          削除
                        </button>
                      </div>
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="text-[11px] tabular-nums text-slate-500">
                          {element.images.length} / {elementLimits.max_images} 枚
                        </span>
                        <button
                          className="btn-ghost text-xs"
                          disabled={busyUpload}
                          onClick={() =>
                            setLibraryTarget({
                              kind: 'image',
                              title: `要素 ${index + 1} の参照画像`,
                              element: {
                                index,
                                limit: elementLimits.max_images,
                              },
                              apply: () => ({}),
                            })
                          }
                        >
                          ライブラリから選択
                        </button>
                        <button
                          className="btn-ghost text-xs"
                          disabled={
                            busyUpload ||
                            element.images.length >= elementLimits.max_images
                          }
                          onClick={() =>
                            setHistoryTarget({
                              kind: 'image',
                              title: `要素 ${index + 1} の参照画像`,
                              element: {
                                index,
                                limit: elementLimits.max_images,
                              },
                              apply: () => ({}),
                            })
                          }
                        >
                          履歴から選択
                        </button>
                      </div>
                      {element.images.length > 0 && (
                        <ul className="flex flex-col gap-1">
                          {element.images.map((url) => (
                            <li key={url} className="flex items-center gap-2">
                              <img
                                src={url}
                                alt=""
                                className="h-10 w-10 shrink-0 rounded border border-ink-600 object-cover"
                              />
                              <span className="flex-1 truncate text-[11px] text-slate-400">
                                {url}
                              </span>
                              <button
                                className="btn-ghost text-xs"
                                onClick={() =>
                                  patchElement(index, {
                                    images: element.images.filter(
                                      (one) => one !== url,
                                    ),
                                  })
                                }
                              >
                                外す
                              </button>
                            </li>
                          ))}
                        </ul>
                      )}
                      <FieldError
                        message={fieldErrors[`kling_elements.${index}`]}
                      />
                    </div>
                  ))}
                  <button
                    className="btn-ghost self-start text-xs"
                    disabled={
                      form.klingElements.length >= elementLimits.max_elements
                    }
                    onClick={() =>
                      patch({
                        klingElements: [...form.klingElements, newElement()],
                      })
                    }
                  >
                    要素を追加
                  </button>
                  <FieldError message={fieldErrors.kling_elements} />
                </div>
              )}
            </Section>
          )}

          {!hidden.negative && (
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
          )}

          <Section title="出力設定">
            {(!hidden.duration || !hidden.fps) && (
              <div className="grid grid-cols-2 gap-2">
                {!hidden.duration && (
                  <div>
                    <label className="label">秒数（上限なし）</label>
                    <input
                      className="field"
                      type="number"
                      min="1"
                      step="1"
                      value={form.duration}
                      onChange={(event) =>
                        patch({ duration: Number(event.target.value) || 0 })
                      }
                    />
                  </div>
                )}
                {!hidden.fps && (
                  <div>
                    <label className="label">fps</label>
                    <input
                      className="field"
                      type="number"
                      min="1"
                      step="1"
                      value={form.fps}
                      onChange={(event) => patch({ fps: Number(event.target.value) || 0 })}
                    />
                  </div>
                )}
              </div>
            )}
            {!hidden.steps && (
              <div className="mt-3">
                <label className="label" htmlFor="generate-steps">
                  ステップ数
                </label>
                <input
                  id="generate-steps"
                  className="field"
                  type="number"
                  min="0"
                  max={MAX_STEPS}
                  step="1"
                  placeholder="未指定＝既定"
                  value={form.steps || ''}
                  onChange={(event) =>
                    patch({ steps: Number(event.target.value) || 0 })
                  }
                />
              </div>
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
                value={form.seed}
                disabled={!form.seedLocked}
                onChange={(event) => patch({ seed: Number(event.target.value) || 0 })}
              />
            </div>
          </Section>
        </>
      )}

      {uploadError && <Banner onClose={() => setUploadError(null)}>{uploadError}</Banner>}

      {/* NSFW の手動指定（SPEC §7.1）。オンで投げたジョブは manual 扱いになり、
          生成後の自動判定で上書きされない。オフなら従来どおり自動判定に任せる。 */}
      <label className="flex items-center gap-2 text-xs text-slate-300">
        <input
          type="checkbox"
          className="accent-accent-500"
          checked={form.nsfw}
          onChange={(event) => patch({ nsfw: event.target.checked })}
        />
        🫣 NSFW として投入（オフなら生成後に自動判定）
      </label>

      <button className="btn-primary w-full py-2.5" onClick={onSubmit} disabled={submitting}>
        {submitting ? '送信中…' : '実行'}
      </button>

      {historyTarget && (
        <HistoryPickerModal
          kind={historyTarget.kind}
          title={`履歴から選択: ${historyTarget.title}`}
          jobs={jobs}
          showNsfw={showNsfw}
          onSelect={(candidate) => void useFromHistory(historyTarget, candidate)}
          onClose={() => setHistoryTarget(null)}
        />
      )}

      {buildingSheet && (
        <SheetBuilderModal
          showNsfw={showNsfw}
          // シートは出力動画と同じ縦横比にしておく（ワークフローが黒で
          // パディングするので、比が合っていれば余白が出ない）。
          {...sheetSize(form.aspectRatio)}
          reloadKey={libraryVersion}
          onCreated={(item) => {
            patch({ sourceImage: item.url })
            setBuildingSheet(false)
          }}
          onClose={() => setBuildingSheet(false)}
          onChanged={onReloadOptions}
        />
      )}

      {libraryTarget && (
        <LibraryPickerModal
          kind={libraryTarget.kind}
          title={`ライブラリから選択: ${libraryTarget.title}`}
          showNsfw={showNsfw}
          reloadKey={libraryVersion}
          // 参照素材は複数選べるので、選択の出し入れができるようモーダルは開いたまま
          selectedIds={
            libraryTarget.reference
              ? selectedLibraryIds(library, form[libraryTarget.reference.field])
              : libraryTarget.element
                ? selectedLibraryIds(
                    library,
                    form.klingElements[libraryTarget.element.index]?.images ?? [],
                  )
                : undefined
          }
          footer={
            (libraryTarget.reference || libraryTarget.element) && (
              <div className="mt-3 flex items-center gap-2">
                <span className="text-xs text-slate-400">
                  {libraryTarget.reference
                    ? form[libraryTarget.reference.field].length
                    : (form.klingElements[libraryTarget.element!.index]?.images
                        .length ?? 0)}{' '}
                  /{' '}
                  {libraryTarget.reference?.limit ?? libraryTarget.element!.limit}{' '}
                  件
                </span>
                <button
                  className="btn-primary ml-auto !py-1 text-xs"
                  onClick={() => setLibraryTarget(null)}
                >
                  選択を終える
                </button>
              </div>
            )
          }
          // ライブラリのファイルは /library で配信済みなので、コピーせず URL を入れる
          onSelect={(item) => {
            if (libraryTarget.reference) {
              toggleRef(libraryTarget.reference, item.url)
              return
            }
            if (libraryTarget.element) {
              toggleElementImage(libraryTarget.element.index, item.url)
              return
            }
            patch(libraryTarget.apply(item.url))
            setLibraryTarget(null)
          }}
          onClose={() => setLibraryTarget(null)}
          onChanged={onReloadOptions}
        />
      )}
    </div>
  )
}
