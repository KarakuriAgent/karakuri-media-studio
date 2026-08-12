import { useState } from 'react'
import type {
  CanvasCard,
  CanvasModelData,
  CanvasTextData,
  Options,
  StudioAssetFileRole,
  StudioAssetUpdate,
  StudioProjectDetail,
  StudioSceneUpdate,
  StudioShotUpdate,
} from '../../types'
import { Trash2 } from 'lucide-react'

import { Banner, Modal } from '../ui'
import { Button } from '../ui/button'
import AssetFields from './fields/AssetFields'
import MediaFields from './fields/MediaFields'
import ModelFields from './fields/ModelFields'
import SceneFields from './fields/SceneFields'
import ShotFields from './fields/ShotFields'
import TextFields from './fields/TextFields'
import {
  KIND_LABEL,
  assetOf,
  cardTitle,
  isStandalone,
  modelDataOf,
  sceneOf,
  shotOf,
  takeOf,
  textDataOf,
} from './logic'

/**
 * カードの編集モーダル。
 *
 * 参照カード（素材 / 場 / カット / 生成物）の中身はスタジオ側が唯一の正なので、
 * 保存先はスタジオの API。キャンバス専用の text / model カードだけ、カードの
 * `data` を丸ごと PATCH する。
 */
export default function CardEditor({
  card,
  detail,
  options,
  busy,
  error,
  onClose,
  onSaveData,
  onSaveAsset,
  onUploadAssetFile,
  onAddAssetReference,
  onRemoveAssetReference,
  onSaveScene,
  onSaveShot,
  onRemove,
}: {
  card: CanvasCard
  detail: StudioProjectDetail
  options: Options | null
  busy: boolean
  error: string | null
  onClose: () => void
  /** text / model カードの中身（バックエンドは部分マージしないので全量）。 */
  onSaveData: (data: Record<string, unknown>) => void
  onSaveAsset: (id: string, patch: StudioAssetUpdate) => void
  /** 素材のメインのファイルを付ける / 差し替える。 */
  onUploadAssetFile: (id: string, file: File) => void
  /** 素材にリファレンス（声・動画・追加画像）を足す。 */
  onAddAssetReference: (
    id: string,
    file: File,
    role: StudioAssetFileRole,
    caption: string,
  ) => void
  onRemoveAssetReference: (fileId: string) => void
  onSaveScene: (id: string, patch: StudioSceneUpdate) => void
  onSaveShot: (id: string, patch: StudioShotUpdate) => void
  /**
   * カードを外す。参照カードはスタジオ側の行ごとしか消せない（写しなので、
   * カードだけ消しても開き直せば戻ってくる）ので `deleteEntity` は必ず true。
   */
  onRemove: (deleteEntity: boolean) => void
}) {
  // キャンバス専用カードの中身（保存を押すまでは手元に持つ）。
  const [text, setText] = useState<CanvasTextData>(() => textDataOf(card))
  const [model, setModel] = useState<CanvasModelData>(() => modelDataOf(card))

  const asset = assetOf(card, detail)
  const scene = sceneOf(card, detail)
  const shot = shotOf(card, detail)
  const take = takeOf(card, detail)

  const body = () => {
    if (card.kind === 'text') {
      return (
        <div className="flex flex-col gap-3">
          <TextFields data={text} onChange={setText} />
          <Button
            className="self-start"
            disabled={busy}
            onClick={() => onSaveData(text as unknown as Record<string, unknown>)}
          >
            {busy ? '保存中…' : '保存'}
          </Button>
        </div>
      )
    }
    if (card.kind === 'model') {
      return (
        <div className="flex flex-col gap-3">
          <ModelFields data={model} onChange={setModel} options={options} />
          <Button
            className="self-start"
            disabled={busy}
            onClick={() => onSaveData(model as unknown as Record<string, unknown>)}
          >
            {busy ? '保存中…' : '保存'}
          </Button>
        </div>
      )
    }
    if (asset) {
      return (
        <AssetFields
          asset={asset}
          busy={busy}
          onSave={(patch) => onSaveAsset(asset.id, patch)}
          onReplaceFile={(file) => onUploadAssetFile(asset.id, file)}
          onAddReference={(file, role, caption) =>
            onAddAssetReference(asset.id, file, role, caption)
          }
          onRemoveReference={onRemoveAssetReference}
        />
      )
    }
    if (scene) {
      return (
        <SceneFields
          scene={scene}
          detail={detail}
          busy={busy}
          onSave={(patch) => onSaveScene(scene.id, patch)}
        />
      )
    }
    if (shot) {
      return (
        <ShotFields
          shot={shot}
          detail={detail}
          busy={busy}
          onSave={(patch) => onSaveShot(shot.id, patch)}
        />
      )
    }
    if (take) return <MediaFields take={take} detail={detail} />
    return (
      <Banner tone="warn">
        参照先がスタジオから消えています。この表示は取り直せば消えます。
      </Banner>
    )
  }

  return (
    <Modal
      title={`${KIND_LABEL[card.kind]}: ${cardTitle(card, detail)}`}
      onClose={onClose}
      wide
    >
      <div className="flex flex-col gap-3">
        {error && <Banner>{error}</Banner>}

        {body()}

        <div className="flex flex-wrap items-center gap-2 border-t border-border pt-3">
          {isStandalone(card.kind) ? (
            <Button
              variant="destructive"
              size="sm"
              disabled={busy}
              title="このカードを消します（キャンバスだけのカードです）"
              onClick={() => onRemove(false)}
            >
              <Trash2 aria-hidden="true" />
              {KIND_LABEL[card.kind]}を削除
            </Button>
          ) : (
            <Button
              variant="destructive"
              size="sm"
              disabled={busy}
              title="スタジオからも消えます（カードだけ外すことはできません）"
              onClick={() => onRemove(true)}
            >
              <Trash2 aria-hidden="true" />
              {KIND_LABEL[card.kind]}ごと削除
            </Button>
          )}
          <Button variant="outline" className="ml-auto" onClick={onClose} disabled={busy}>
            閉じる
          </Button>
        </div>
      </div>
    </Modal>
  )
}
