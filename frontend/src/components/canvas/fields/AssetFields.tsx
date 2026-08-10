import { useState } from 'react'
import type {
  StudioAsset,
  StudioAssetFileRole,
  StudioAssetUpdate,
} from '../../../types'
import AssetFilesPanel from '../../studio/AssetFilesPanel'
import { ASSET_KIND_LABEL, assetHasFile } from '../../studio/studio'
import { PROFILE_FIELDS, profileForm, profilePayload } from '../logic'
import { AreaField, TextField, UrlListField } from './common'

/**
 * 素材カード（キャラ / 場所 / 小物 / 画風 / 参照）の編集フォーム。
 *
 * 中身の正はスタジオ側なので、保存先は `PATCH /api/studio/assets/{id}`。分類は
 * カードの種別と結びついている（環境の素材を「場所」カードにしている）ので、
 * ここでは変えさせない——付け替えはスタジオの World Bible で行う。ファイルの
 * 差し替えとリファレンスの追加もスタジオと同じ部品（`AssetFilesPanel`）を使う。
 */
export default function AssetFields({
  asset,
  busy,
  onSave,
  onReplaceFile,
  onAddReference,
  onRemoveReference,
}: {
  asset: StudioAsset
  busy: boolean
  onSave: (patch: StudioAssetUpdate) => void
  /** メインのファイルを付ける / 差し替える。 */
  onReplaceFile: (file: File) => void
  onAddReference: (
    file: File,
    role: StudioAssetFileRole,
    caption: string,
  ) => void
  onRemoveReference: (fileId: string) => void
}) {
  const [name, setName] = useState(asset.name)
  const [caption, setCaption] = useState(asset.caption)
  const [promptCaption, setPromptCaption] = useState(asset.prompt_caption)
  const [profile, setProfile] = useState<Record<string, string>>(() =>
    profileForm(asset),
  )

  const patchProfile = (key: string, value: string) =>
    setProfile((current) => ({ ...current, [key]: value }))

  return (
    <div className="flex flex-col gap-3">
      <div className="overflow-hidden rounded-md border border-ink-600 bg-ink-900">
        {!assetHasFile(asset) ? (
          <p className="px-3 py-6 text-center text-[11px] text-slate-500">
            ファイルなしの素材です。参照には添付されず、投入時に下のプロンプト用
            キャプションが説明文として本文に展開されます。
          </p>
        ) : asset.kind === 'audio' ? (
          <audio src={asset.url} controls className="w-full" />
        ) : asset.kind === 'video' ? (
          <video src={asset.url} controls className="max-h-56 w-full object-contain" />
        ) : (
          <img
            src={asset.url}
            alt={asset.name}
            className="max-h-56 w-full object-contain"
          />
        )}
      </div>

      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className="text-slate-500">{ASSET_KIND_LABEL[asset.kind]}</span>
        {asset.locked && <span className="text-amber-300">🔒 差し替え禁止</span>}
        <button
          className="btn-ghost ml-auto !py-1 text-xs"
          disabled={busy}
          onClick={() => onSave({ locked: !asset.locked })}
        >
          {asset.locked ? '🔓 ロック解除' : '🔒 ロック'}
        </button>
      </div>

      <AssetFilesPanel
        asset={asset}
        busy={busy}
        onReplaceMain={onReplaceFile}
        onAddReference={onAddReference}
        onRemoveReference={onRemoveReference}
      />

      <TextField
        label="素材名（@ で呼ぶ名前）"
        value={name}
        onChange={setName}
      />
      <AreaField
        label="キャプション（人間向け）"
        value={caption}
        rows={3}
        onChange={setCaption}
      />
      <AreaField
        label="プロンプト用キャプション（英語推奨）"
        value={promptCaption}
        rows={3}
        onChange={setPromptCaption}
        placeholder="参照を添付できないモードで @メンションの代わりに埋め込まれます"
      />

      <div className="flex flex-col gap-3 border-t border-ink-700 pt-3">
        {PROFILE_FIELDS[asset.category].map((field) =>
          field.kind === 'urls' ? (
            <UrlListField
              key={field.key}
              label={field.label}
              value={(profile[field.key] ?? '')
                .split('\n')
                .map((line) => line.trim())
                .filter(Boolean)}
              onChange={(urls) => patchProfile(field.key, urls.join('\n'))}
            />
          ) : field.kind === 'text' ? (
            <TextField
              key={field.key}
              label={field.label}
              value={profile[field.key] ?? ''}
              onChange={(value) => patchProfile(field.key, value)}
            />
          ) : (
            <AreaField
              key={field.key}
              label={field.label}
              rows={2}
              value={profile[field.key] ?? ''}
              onChange={(value) => patchProfile(field.key, value)}
            />
          ),
        )}
      </div>

      <button
        className="btn-primary self-start"
        disabled={busy || !name.trim()}
        onClick={() =>
          onSave({
            name: name.trim(),
            caption,
            prompt_caption: promptCaption,
            profile: profilePayload(asset.category, profile),
          })
        }
      >
        {busy ? '保存中…' : '保存'}
      </button>
    </div>
  )
}
