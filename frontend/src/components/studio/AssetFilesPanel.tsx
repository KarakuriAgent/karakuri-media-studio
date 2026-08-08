import { useRef, useState } from 'react'
import type { StudioAsset, StudioAssetFile, StudioAssetFileRole } from '../../types'
import { ASSET_KIND_LABEL, assetHasFile } from './studio'

/** リファレンスの役割 -> 画面に出す名前と実体の種別（backend の ASSET_FILE_ROLE_KINDS）。 */
export const ASSET_FILE_ROLES: {
  value: StudioAssetFileRole
  label: string
  kind: 'image' | 'video' | 'audio'
}[] = [
  { value: 'voice', label: '声サンプル', kind: 'audio' },
  { value: 'video', label: '動画リファレンス', kind: 'video' },
  { value: 'image', label: '追加画像', kind: 'image' },
]

export function assetFileRoleLabel(role: StudioAssetFileRole): string {
  return ASSET_FILE_ROLES.find((item) => item.value === role)?.label ?? role
}

function roleKind(role: StudioAssetFileRole): 'image' | 'video' | 'audio' {
  return ASSET_FILE_ROLES.find((item) => item.value === role)?.kind ?? 'image'
}

/** 1 本ぶんのプレビュー（音声は audio、動画は video、画像はそのまま）。 */
function ReferencePreview({ file }: { file: StudioAssetFile }) {
  const kind = roleKind(file.role)
  if (kind === 'audio') return <audio src={file.url} controls className="w-full" />
  if (kind === 'video')
    return <video src={file.url} controls className="max-h-40 w-full object-contain" />
  return (
    <img
      src={file.url}
      alt={file.caption || 'リファレンス'}
      className="max-h-40 w-full object-contain"
    />
  )
}

/**
 * 素材のファイル: メインの 1 本の差し替えと、追加リファレンス（声・動画・画像）。
 *
 * スタジオの World Bible（インスペクタ）とキャンバスの素材カード編集の両方で
 * 使う。保存先はどちらもスタジオの API（`/api/studio/assets/…`）で、実体は
 * `assets/<kind>/` に置かれる。
 */
export default function AssetFilesPanel({
  asset,
  busy,
  onReplaceMain,
  onAddReference,
  onRemoveReference,
}: {
  asset: StudioAsset
  busy: boolean
  /** メインのファイルを付ける / 差し替える（種別は拡張子から決まる）。 */
  onReplaceMain: (file: File) => void
  onAddReference: (
    file: File,
    role: StudioAssetFileRole,
    caption: string,
  ) => void
  onRemoveReference: (fileId: string) => void
}) {
  const mainRef = useRef<HTMLInputElement>(null)
  const referenceRef = useRef<HTMLInputElement>(null)
  const [role, setRole] = useState<StudioAssetFileRole>('voice')
  const [caption, setCaption] = useState('')

  const files = asset.files ?? []

  const pickReference = (file: File | null) => {
    if (!file) return
    onAddReference(file, role, caption.trim())
    setCaption('')
    if (referenceRef.current) referenceRef.current.value = ''
  }

  return (
    <div className="flex flex-col gap-3">
      <div>
        <label className="label" htmlFor={`asset-main-file-${asset.id}`}>
          {assetHasFile(asset)
            ? `メインのファイルを差し替え（いまは ${ASSET_KIND_LABEL[asset.kind]}）`
            : 'メインのファイルを付ける'}
        </label>
        <input
          id={`asset-main-file-${asset.id}`}
          ref={mainRef}
          type="file"
          disabled={busy}
          className="field cursor-pointer file:mr-2 file:rounded file:border-0 file:bg-ink-600 file:px-2 file:py-1 file:text-slate-200"
          onChange={(event) => {
            const picked = event.target.files?.[0] ?? null
            if (picked) onReplaceMain(picked)
            if (mainRef.current) mainRef.current.value = ''
          }}
        />
        <p className="mt-1 text-[11px] text-slate-500">
          種別（画像 / 動画 / 音声）は拡張子から決まります。
        </p>
      </div>

      <div className="flex flex-col gap-2 border-t border-ink-700 pt-3">
        <p className="text-[11px] text-slate-500">
          リファレンス {files.length} 件（声サンプル・動画・追加画像）。生成には
          まだ自動で流れませんが、エージェントには渡ります。
        </p>

        {files.map((file) => (
          <div
            key={file.id}
            className="flex flex-col gap-1 rounded-md border border-ink-600 bg-ink-900 p-2"
          >
            <div className="flex items-center gap-2 text-[11px]">
              <span className="chip !px-1.5 !py-0">
                {assetFileRoleLabel(file.role)}
              </span>
              <span className="min-w-0 flex-1 truncate text-slate-400">
                {file.caption || file.path.split('/').pop()}
              </span>
              <button
                className="btn-ghost !px-2 !py-0.5 text-xs"
                disabled={busy}
                title="このリファレンスを外す"
                aria-label={`${assetFileRoleLabel(file.role)} を外す`}
                onClick={() => onRemoveReference(file.id)}
              >
                ✕
              </button>
            </div>
            <ReferencePreview file={file} />
          </div>
        ))}

        <div className="flex flex-wrap items-end gap-2">
          <div className="min-w-[8rem]">
            <label className="label" htmlFor={`asset-file-role-${asset.id}`}>
              種類
            </label>
            <select
              id={`asset-file-role-${asset.id}`}
              className="field"
              value={role}
              onChange={(event) =>
                setRole(event.target.value as StudioAssetFileRole)
              }
            >
              {ASSET_FILE_ROLES.map((item) => (
                <option key={item.value} value={item.value}>
                  {item.label}
                </option>
              ))}
            </select>
          </div>
          <div className="min-w-[10rem] flex-1">
            <label className="label" htmlFor={`asset-file-caption-${asset.id}`}>
              メモ（任意）
            </label>
            <input
              id={`asset-file-caption-${asset.id}`}
              className="field"
              value={caption}
              placeholder="怒っているときの声 など"
              onChange={(event) => setCaption(event.target.value)}
            />
          </div>
        </div>
        <input
          ref={referenceRef}
          type="file"
          aria-label="リファレンスを追加"
          disabled={busy}
          className="field cursor-pointer file:mr-2 file:rounded file:border-0 file:bg-ink-600 file:px-2 file:py-1 file:text-slate-200"
          onChange={(event) => pickReference(event.target.files?.[0] ?? null)}
        />
      </div>
    </div>
  )
}
