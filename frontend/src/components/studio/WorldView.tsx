import { useEffect, useRef, useState } from 'react'
import type {
  StudioAsset,
  StudioAssetCategory,
  StudioAssetFileRole,
  StudioAssetUpdate,
} from '../../types'
import { Banner, Section } from '../ui'
import AssetFilesPanel from './AssetFilesPanel'
import {
  ASSET_CATEGORIES,
  ASSET_CATEGORY_CLASS,
  ASSET_CATEGORY_LABEL,
  ASSET_KIND_LABEL,
  assetHasFile,
  assetKindFromFile,
  assetNameFromFile,
} from './studio'

/** 素材の見た目（画像はそのまま、動画は 1 コマ目、音声は記号）。 */
function AssetThumb({ asset }: { asset: StudioAsset }) {
  if (!assetHasFile(asset)) {
    return (
      <span className="flex h-full w-full items-center justify-center text-2xl opacity-50">
        📝
      </span>
    )
  }
  if (asset.kind === 'image') {
    return <img src={asset.url} alt={asset.name} className="h-full w-full object-cover" />
  }
  if (asset.kind === 'video') {
    return (
      <video
        src={asset.url}
        className="h-full w-full object-cover"
        muted
        playsInline
        preload="metadata"
      />
    )
  }
  return (
    <span className="flex h-full w-full items-center justify-center text-2xl opacity-60">
      🎵
    </span>
  )
}

function AssetCard({
  asset,
  active,
  onSelect,
}: {
  asset: StudioAsset
  active: boolean
  onSelect: () => void
}) {
  return (
    <button
      className={`relative overflow-hidden rounded-md border text-left transition-colors ${
        active
          ? 'border-accent-500 ring-2 ring-accent-500/60'
          : 'border-ink-600 hover:border-ink-500'
      }`}
      onClick={onSelect}
      aria-current={active ? 'true' : undefined}
    >
      <span className="block h-24 w-full bg-ink-900">
        <AssetThumb asset={asset} />
      </span>
      <span className="absolute left-1 top-1 flex flex-wrap gap-1">
        <span
          className={`chip !px-1.5 !py-0 text-[10px] ${ASSET_CATEGORY_CLASS[asset.category]}`}
        >
          {ASSET_CATEGORY_LABEL[asset.category]}
        </span>
        {!assetHasFile(asset) && (
          <span
            className="chip !px-1.5 !py-0 border-slate-600 bg-slate-800 text-[10px] text-slate-300"
            title="ファイルを持たない素材です。参照には添付されず、投入時に説明文として展開されます"
          >
            ファイルなし
          </span>
        )}
        {asset.locked && (
          <span
            className="chip !px-1.5 !py-0 border-amber-800 bg-amber-950 text-[10px] text-amber-300"
            title="差し替え禁止"
          >
            🔒 LOCKED
          </span>
        )}
        {(asset.files?.length ?? 0) > 0 && (
          <span
            className="chip !px-1.5 !py-0 text-[10px] text-slate-300"
            title="声サンプル・動画リファレンス・追加画像"
          >
            📎 {asset.files?.length}
          </span>
        )}
      </span>
      <span className="block truncate bg-ink-800 px-2 py-1 text-[11px] text-slate-200">
        @{asset.name}
      </span>
    </button>
  )
}

/** インスペクタ: 選んだ素材のキャプション類の編集・ロック・削除。 */
function AssetInspector({
  asset,
  onSave,
  onDelete,
  onUploadFile,
  onAddReference,
  onRemoveReference,
  busy,
}: {
  asset: StudioAsset
  onSave: (patch: StudioAssetUpdate) => void
  onDelete: () => void
  /** メインのファイルを付ける / 差し替える。 */
  onUploadFile: (file: File) => void
  onAddReference: (
    file: File,
    role: StudioAssetFileRole,
    caption: string,
  ) => void
  onRemoveReference: (fileId: string) => void
  busy: boolean
}) {
  const [caption, setCaption] = useState(asset.caption)
  const [promptCaption, setPromptCaption] = useState(asset.prompt_caption)
  const [category, setCategory] = useState<StudioAssetCategory>(asset.category)

  useEffect(() => {
    setCaption(asset.caption)
    setPromptCaption(asset.prompt_caption)
    setCategory(asset.category)
  }, [asset.id, asset.caption, asset.prompt_caption, asset.category])

  return (
    <Section title="素材の詳細">
      <div className="space-y-3">
        <div className="overflow-hidden rounded-md border border-ink-600 bg-ink-900">
          {!assetHasFile(asset) ? (
            <p className="px-3 py-6 text-center text-[11px] text-slate-500">
              ファイルなしの素材です。参照には添付されず、投入時に下の
              プロンプト用キャプションが説明文として本文に展開されます。
            </p>
          ) : asset.kind === 'audio' ? (
            <audio src={asset.url} controls className="w-full" />
          ) : asset.kind === 'video' ? (
            <video src={asset.url} controls className="max-h-56 w-full object-contain" />
          ) : (
            <img src={asset.url} alt={asset.name} className="max-h-56 w-full object-contain" />
          )}
        </div>

        <div className="flex flex-wrap items-center gap-2 text-xs">
          <span className="font-mono text-accent-400">@{asset.name}</span>
          <span className="text-slate-500">{ASSET_KIND_LABEL[asset.kind]}</span>
          {!assetHasFile(asset) && <span className="text-slate-400">ファイルなし</span>}
          {asset.locked && <span className="text-amber-300">🔒 差し替え禁止</span>}
        </div>

        <AssetFilesPanel
          asset={asset}
          busy={busy}
          onReplaceMain={onUploadFile}
          onAddReference={onAddReference}
          onRemoveReference={onRemoveReference}
        />

        <div>
          <label className="label" htmlFor="studio-asset-category">
            カテゴリ
          </label>
          <select
            id="studio-asset-category"
            className="field"
            value={category}
            onChange={(event) =>
              setCategory(event.target.value as StudioAssetCategory)
            }
          >
            {ASSET_CATEGORIES.map((value) => (
              <option key={value} value={value}>
                {ASSET_CATEGORY_LABEL[value]}
              </option>
            ))}
          </select>
        </div>

        <div>
          <label className="label" htmlFor="studio-asset-caption">
            キャプション（人間向け）
          </label>
          <textarea
            id="studio-asset-caption"
            className="field h-20 resize-y"
            value={caption}
            onChange={(event) => setCaption(event.target.value)}
          />
        </div>

        <div>
          <label className="label" htmlFor="studio-asset-prompt-caption">
            プロンプト用キャプション（英語推奨）
          </label>
          <textarea
            id="studio-asset-prompt-caption"
            className="field h-20 resize-y"
            value={promptCaption}
            placeholder="参照を添付できないモードで @メンションの代わりに埋め込まれます"
            onChange={(event) => setPromptCaption(event.target.value)}
          />
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <button
            className="btn-primary"
            disabled={busy}
            onClick={() =>
              onSave({ caption, prompt_caption: promptCaption, category })
            }
          >
            保存
          </button>
          <button
            className="btn-ghost"
            disabled={busy}
            onClick={() => onSave({ locked: !asset.locked })}
          >
            {asset.locked ? '🔓 ロック解除' : '🔒 ロック'}
          </button>
          <button className="btn-danger ml-auto" disabled={busy} onClick={onDelete}>
            削除
          </button>
        </div>
      </div>
    </Section>
  )
}

/**
 * World Bible タブ: 素材のグリッドとインスペクタ、アップロード。
 *
 * 素材は名前が識別子で、Shot のプロンプトから `@名前` で呼べる。
 */
export default function WorldView({
  assets,
  selectedId,
  onSelect,
  onAdd,
  onSave,
  onDelete,
  onUploadFile,
  onAddReference,
  onRemoveReference,
  busy,
}: {
  assets: StudioAsset[]
  selectedId: string | null
  onSelect: (id: string) => void
  /** `file` が null なら**ファイルなしの素材**（名前とキャプションだけ）を作る。 */
  onAdd: (
    file: File | null,
    name: string,
    category: StudioAssetCategory,
    caption: string,
  ) => void
  onSave: (id: string, patch: StudioAssetUpdate) => void
  onDelete: (id: string) => void
  /** 素材のメインのファイルを付ける / 差し替える。 */
  onUploadFile: (id: string, file: File) => void
  /** 声サンプル・動画リファレンス・追加画像を足す。 */
  onAddReference: (
    id: string,
    file: File,
    role: StudioAssetFileRole,
    caption: string,
  ) => void
  onRemoveReference: (fileId: string) => void
  busy: boolean
}) {
  const fileRef = useRef<HTMLInputElement>(null)
  const [file, setFile] = useState<File | null>(null)
  const [name, setName] = useState('')
  const [caption, setCaption] = useState('')
  const [category, setCategory] = useState<StudioAssetCategory>('character')

  const selected = assets.find((asset) => asset.id === selectedId) ?? null

  // ファイルなしでは名前が識別子そのものなので必須（ファイルがあれば主部で補える）。
  const assetName = name.trim() || (file ? assetNameFromFile(file.name) : '')

  const submit = () => {
    if (!file && !assetName) return
    onAdd(file, assetName, category, caption.trim())
    setFile(null)
    setName('')
    setCaption('')
    if (fileRef.current) fileRef.current.value = ''
  }

  return (
    <div className="grid min-h-0 gap-3 lg:grid-cols-[1fr_22rem]">
      <div className="space-y-3">
        <Banner tone="info">
          素材の名前はそのまま識別子です。カットのプロンプトに{' '}
          <span className="font-mono">@素材名</span>{' '}
          と書くとここの素材を参照します（名前に空白や記号が入るときは{' '}
          <span className="font-mono">@{'{素材名}'}</span>）。参照を添付できない
          モードでは、プロンプト用キャプションが本文に埋め込まれます。
        </Banner>

        <Section
          title={`World Bible（${assets.length} 件）`}
          right={
            <span className="text-[11px] text-slate-500">
              クリックで右の詳細に出ます
            </span>
          }
        >
          {assets.length === 0 ? (
            <p className="px-3 py-6 text-center text-xs text-slate-600">
              まだ素材がありません
            </p>
          ) : (
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 xl:grid-cols-4">
              {assets.map((asset) => (
                <AssetCard
                  key={asset.id}
                  asset={asset}
                  active={asset.id === selectedId}
                  onSelect={() => onSelect(asset.id)}
                />
              ))}
            </div>
          )}
        </Section>

        <Section title="素材を追加">
          <div className="space-y-2">
            <input
              ref={fileRef}
              type="file"
              aria-label="素材ファイル（任意）"
              className="field cursor-pointer file:mr-2 file:rounded file:border-0 file:bg-ink-600 file:px-2 file:py-1 file:text-slate-200"
              onChange={(event) => {
                const picked = event.target.files?.[0] ?? null
                setFile(picked)
                if (picked && !name) setName(assetNameFromFile(picked.name))
              }}
            />
            <p className="text-[11px] text-slate-500">
              ファイルを付けずに名前とキャプションだけで登録すると、参照には
              添付されず、投入時に説明文として本文へ展開されます。
            </p>
            <div className="grid gap-2 sm:grid-cols-2">
              <div>
                <label className="label" htmlFor="studio-upload-name">
                  素材名（@ で呼ぶ名前）
                </label>
                <input
                  id="studio-upload-name"
                  className="field"
                  value={name}
                  onChange={(event) => setName(event.target.value)}
                />
              </div>
              <div>
                <label className="label" htmlFor="studio-upload-category">
                  カテゴリ
                </label>
                <select
                  id="studio-upload-category"
                  className="field"
                  value={category}
                  onChange={(event) =>
                    setCategory(event.target.value as StudioAssetCategory)
                  }
                >
                  {ASSET_CATEGORIES.map((value) => (
                    <option key={value} value={value}>
                      {ASSET_CATEGORY_LABEL[value]}
                    </option>
                  ))}
                </select>
              </div>
            </div>
            <div>
              <label className="label" htmlFor="studio-upload-caption">
                キャプション（任意）
              </label>
              <textarea
                id="studio-upload-caption"
                className="field h-16 resize-y"
                value={caption}
                placeholder="どんな素材か（ファイルなしの素材ではこれが説明文になります）"
                onChange={(event) => setCaption(event.target.value)}
              />
            </div>
            {file && (
              <p className="text-[11px] text-slate-500">
                種別: {ASSET_KIND_LABEL[assetKindFromFile(file.name)]}（拡張子から判定）
              </p>
            )}
            <button
              className="btn-primary"
              onClick={submit}
              disabled={busy || (!file && !assetName)}
            >
              {file ? 'アップロード' : 'ファイルなしで追加'}
            </button>
          </div>
        </Section>
      </div>

      <div>
        {selected ? (
          <AssetInspector
            asset={selected}
            busy={busy}
            onSave={(patch) => onSave(selected.id, patch)}
            onDelete={() => onDelete(selected.id)}
            onUploadFile={(file) => onUploadFile(selected.id, file)}
            onAddReference={(file, role, caption) =>
              onAddReference(selected.id, file, role, caption)
            }
            onRemoveReference={onRemoveReference}
          />
        ) : (
          <p className="rounded-md border border-ink-600 bg-ink-800 px-3 py-6 text-center text-xs text-slate-600">
            素材を選ぶとここに詳細が出ます
          </p>
        )}
      </div>
    </div>
  )
}
