import { useEffect, useRef, useState } from 'react'
import {
  FileText,
  Lock,
  Music,
  Paperclip,
  Trash2,
  Unlock,
} from 'lucide-react'

import type {
  StudioAsset,
  StudioAssetCategory,
  StudioAssetFileRole,
  StudioAssetUpdate,
} from '../../types'
import { Banner, Section } from '../ui'
import { Badge } from '../ui/badge'
import { NativeSelect } from '../NativeSelect'
import { Button } from '../ui/button'
import { Input } from '../ui/input'
import { Label } from '../ui/label'
import { Textarea } from '../ui/textarea'
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
      <span className="flex h-full w-full items-center justify-center text-muted-foreground">
        <FileText className="size-6" />
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
    <span className="flex h-full w-full items-center justify-center text-muted-foreground">
      <Music className="size-6" />
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
      className={`relative overflow-hidden rounded-md border text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50 ${
        active
          ? 'border-primary ring-2 ring-primary/60'
          : 'border-border hover:border-primary/50'
      }`}
      onClick={onSelect}
      aria-current={active ? 'true' : undefined}
    >
      <span className="block h-24 w-full bg-background">
        <AssetThumb asset={asset} />
      </span>
      <span className="absolute left-1 top-1 flex flex-wrap gap-1">
        <span
          className={`chip !px-1.5 !py-0 text-[10px] ${ASSET_CATEGORY_CLASS[asset.category]}`}
        >
          {ASSET_CATEGORY_LABEL[asset.category]}
        </span>
        {!assetHasFile(asset) && (
          <Badge
            variant="secondary"
            className="px-1.5 py-0 text-[10px] font-normal"
            title="ファイルを持たない素材です。参照には添付されず、投入時に説明文として展開されます"
          >
            ファイルなし
          </Badge>
        )}
        {asset.locked && (
          <Badge
            variant="warning"
            className="px-1.5 py-0 text-[10px] font-normal"
            title="差し替え禁止"
          >
            <Lock className="size-2.5" />
            LOCKED
          </Badge>
        )}
        {(asset.files?.length ?? 0) > 0 && (
          <Badge
            variant="outline"
            className="bg-card px-1.5 py-0 text-[10px] font-normal"
            title="声サンプル・動画リファレンス・追加画像"
          >
            <Paperclip className="size-2.5" />
            {asset.files?.length}
          </Badge>
        )}
      </span>
      <span className="block truncate bg-card px-2 py-1 text-[11px] text-foreground/90">
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
        <div className="overflow-hidden rounded-md border border-border bg-surface-sunken">
          {!assetHasFile(asset) ? (
            <p className="px-3 py-6 text-center text-[11px] text-muted-foreground">
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
          <span className="text-muted-foreground">{ASSET_KIND_LABEL[asset.kind]}</span>
          {!assetHasFile(asset) && (
            <span className="text-muted-foreground">ファイルなし</span>
          )}
          {asset.locked && (
            <span className="flex items-center gap-1 text-amber-300">
              <Lock className="size-3" />
              差し替え禁止
            </span>
          )}
        </div>

        <AssetFilesPanel
          asset={asset}
          busy={busy}
          onReplaceMain={onUploadFile}
          onAddReference={onAddReference}
          onRemoveReference={onRemoveReference}
        />

        <div className="space-y-1">
          <Label htmlFor="studio-asset-category">カテゴリ</Label>
          <NativeSelect
            id="studio-asset-category"
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
          </NativeSelect>
        </div>

        <div className="space-y-1">
          <Label htmlFor="studio-asset-caption">キャプション（人間向け）</Label>
          <Textarea
            id="studio-asset-caption"
            className="h-20 resize-y"
            value={caption}
            onChange={(event) => setCaption(event.target.value)}
          />
        </div>

        <div className="space-y-1">
          <Label htmlFor="studio-asset-prompt-caption">
            プロンプト用キャプション（英語推奨）
          </Label>
          <Textarea
            id="studio-asset-prompt-caption"
            className="h-20 resize-y"
            value={promptCaption}
            placeholder="参照を添付できないモードで @メンションの代わりに埋め込まれます"
            onChange={(event) => setPromptCaption(event.target.value)}
          />
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <Button
            disabled={busy}
            onClick={() =>
              onSave({ caption, prompt_caption: promptCaption, category })
            }
          >
            保存
          </Button>
          <Button
            variant="outline"
            disabled={busy}
            onClick={() => onSave({ locked: !asset.locked })}
          >
            {asset.locked ? <Unlock /> : <Lock />}
            {asset.locked ? 'ロック解除' : 'ロック'}
          </Button>
          <Button
            variant="destructive"
            className="ml-auto"
            disabled={busy}
            onClick={onDelete}
          >
            <Trash2 />
            削除
          </Button>
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
            <span className="text-[11px] text-muted-foreground">
              クリックで右の詳細に出ます
            </span>
          }
        >
          {assets.length === 0 ? (
            <p className="px-3 py-6 text-center text-xs text-muted-foreground">
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
            <Input
              ref={fileRef}
              type="file"
              aria-label="素材ファイル（任意）"
              className="cursor-pointer py-1.5 file:mr-2 file:rounded file:border-0 file:bg-secondary file:px-2 file:py-1 file:text-foreground/85"
              onChange={(event) => {
                const picked = event.target.files?.[0] ?? null
                setFile(picked)
                if (picked && !name) setName(assetNameFromFile(picked.name))
              }}
            />
            <div className="grid gap-2 sm:grid-cols-2">
              <div className="space-y-1">
                <Label htmlFor="studio-upload-name">素材名（@ で呼ぶ名前）</Label>
                <Input
                  id="studio-upload-name"
                  value={name}
                  onChange={(event) => setName(event.target.value)}
                />
              </div>
              <div className="space-y-1">
                <Label htmlFor="studio-upload-category">カテゴリ</Label>
                <NativeSelect
                  id="studio-upload-category"
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
                </NativeSelect>
              </div>
            </div>
            <div className="space-y-1">
              <Label htmlFor="studio-upload-caption">キャプション（任意）</Label>
              <Textarea
                id="studio-upload-caption"
                className="h-16 resize-y"
                value={caption}
                placeholder="どんな素材か（ファイルなしの素材ではこれが説明文になります）"
                onChange={(event) => setCaption(event.target.value)}
              />
            </div>
            {file && (
              <p className="text-[11px] text-muted-foreground">
                種別: {ASSET_KIND_LABEL[assetKindFromFile(file.name)]}（拡張子から判定）
              </p>
            )}
            <Button onClick={submit} disabled={busy || (!file && !assetName)}>
              {file ? 'アップロード' : 'ファイルなしで追加'}
            </Button>
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
          <p className="rounded-md border border-border bg-surface-sunken px-3 py-6 text-center text-xs text-muted-foreground">
            素材を選ぶとここに詳細が出ます
          </p>
        )}
      </div>
    </div>
  )
}
