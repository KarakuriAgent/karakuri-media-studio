import { Boxes, FolderOpen, ImageIcon } from 'lucide-react'

import type { StudioAssetFile, StudioAssetItem } from '../../types'
import { Button } from '@/components/ui/button'
import { assetFileRoleLabel } from '../studio/AssetFilesPanel'
import { ASSET_CATEGORY_LABEL } from '../studio/studio'
import { KIND_LABELS, timestampOf } from './library'

/** 追加リファレンス 1 本のプレビュー（声は audio、動画は video、画像はそのまま）。 */
function ReferencePreview({ file }: { file: StudioAssetFile }) {
  if (file.role === 'voice') return <audio src={file.url} controls className="w-full" />
  if (file.role === 'video')
    return (
      <video
        src={file.url}
        controls
        preload="metadata"
        className="max-h-32 w-full object-contain"
      />
    )
  return (
    <img
      src={file.url}
      alt={file.caption || 'リファレンス'}
      className="max-h-32 w-full object-contain"
    />
  )
}

/**
 * プロジェクト素材（World Bible）1 件の詳細（ファイルタブの「プロジェクト素材」）。
 *
 * ここは**読み取りだけ**にしてある: 素材はその作品の持ち物で、名前や
 * キャプションを直すとプロンプトと Take の stale 判定に効くため、編集は
 * スタジオの World Bible タブ（`components/studio/WorldView.tsx`）に任せる。
 */
export default function ProjectAssetDetail({
  asset,
  onOpenInStudio,
}: {
  asset: StudioAssetItem
  /** スタジオタブのこの作品の World Bible へ移る。 */
  onOpenInStudio?: () => void
}) {
  // ライブラリ由来の素材は「取り込んだ版」と「元が進んでいるか」を出す
  // （反映そのものはスタジオ側の [反映] で行う）。
  const fromLibrary = Boolean(asset.source_library_id)
  // メインのファイルに足したリファレンス（`GET /api/studio/assets/{id}/files`
  // と同じ中身。横断一覧が素材ごとに載せてくるので、ここでは読み直さない）。
  const references = asset.files ?? []

  return (
    <div className="flex flex-col gap-3">
      <div className="flex h-40 items-center justify-center overflow-hidden rounded-md border border-border bg-background">
        {!asset.url ? (
          <span className="flex flex-col items-center gap-1 text-[11px] text-muted-foreground">
            <ImageIcon className="size-6" aria-hidden />
            メタデータのみの素材（ファイルなし）
          </span>
        ) : asset.kind === 'image' ? (
          <img src={asset.url} alt={asset.name} className="max-h-40 w-full object-contain" />
        ) : asset.kind === 'video' ? (
          <video src={asset.url} controls className="max-h-40 w-full" />
        ) : (
          <audio src={asset.url} controls className="w-full p-2" />
        )}
      </div>

      <dl className="grid grid-cols-[auto_1fr] gap-x-2 gap-y-1 text-[11px] text-muted-foreground">
        <dt>作品</dt>
        <dd className="text-foreground/85">{asset.project_name}</dd>
        <dt>分類</dt>
        <dd className="text-foreground/85">
          {ASSET_CATEGORY_LABEL[asset.category] ?? asset.category}
        </dd>
        <dt>種別</dt>
        <dd className="text-foreground/85">{KIND_LABELS[asset.kind]}</dd>
        <dt>作成日</dt>
        <dd className="tnum text-foreground/85">{timestampOf(asset.created_at)}</dd>
        <dt>出どころ</dt>
        <dd className="text-foreground/85">
          {fromLibrary
            ? `ライブラリ（版 ${asset.source_library_version ?? 1}）`
            : '作品の素材'}
        </dd>
        {asset.path && (
          <>
            <dt>ファイル</dt>
            <dd className="break-all text-foreground/85">{asset.path}</dd>
          </>
        )}
      </dl>

      {(asset.caption || asset.prompt_caption) && (
        <div>
          <p className="mb-1 text-[11px] text-muted-foreground">キャプション</p>
          <p className="whitespace-pre-wrap rounded-md border border-border bg-surface-sunken p-2 text-[11px] text-foreground/85">
            {asset.caption || asset.prompt_caption}
          </p>
        </div>
      )}

      {(asset.library_blocking || asset.library_update_available) && (
        <div className="flex flex-wrap items-center gap-2">
          {asset.library_blocking && (
            <span className="chip border-primary bg-primary/15 text-foreground">
              <Boxes className="size-3" aria-hidden />
              構図リファレンス
            </span>
          )}
          {asset.library_update_available && (
            <span className="chip border-amber-700 bg-amber-950 text-amber-300">
              更新あり
            </span>
          )}
        </div>
      )}

      {references.length > 0 && (
        <div>
          <p className="mb-1 text-[11px] text-muted-foreground">
            追加リファレンス {references.length} 件
          </p>
          <ul className="flex flex-col gap-2">
            {references.map((file) => (
              <li
                key={file.id}
                className="overflow-hidden rounded-md border border-border bg-surface-sunken"
              >
                <div className="flex items-center justify-center bg-background">
                  <ReferencePreview file={file} />
                </div>
                <div className="flex items-center gap-1 px-1.5 py-1">
                  <span className="chip !py-0.5 shrink-0 border-border bg-card text-[10px] text-foreground/85">
                    {assetFileRoleLabel(file.role)}
                  </span>
                  {file.caption && (
                    <span className="truncate text-[11px] text-muted-foreground">
                      {file.caption}
                    </span>
                  )}
                </div>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="flex flex-wrap gap-2">
        {onOpenInStudio && (
          <Button variant="outline" size="sm" onClick={onOpenInStudio}>
            <FolderOpen />
            スタジオで開く
          </Button>
        )}
      </div>
      <p className="text-[11px] text-muted-foreground">
        素材の編集はスタジオの World Bible タブで行います。
      </p>
    </div>
  )
}
