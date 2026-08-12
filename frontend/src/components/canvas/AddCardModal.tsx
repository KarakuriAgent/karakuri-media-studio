import { useState } from 'react'
import type {
  CanvasCardCreate,
  CanvasCardKind,
  StudioAssetKind,
  StudioProjectDetail,
} from '../../types'
import { Banner, Modal } from '../ui'
import { NativeSelect } from '../NativeSelect'
import { Button } from '../ui/button'
import { Input } from '../ui/input'
import { Label } from '../ui/label'
import { ASSET_KIND_LABEL, sceneOptions } from '../studio/studio'
import { KIND_LABEL, isAssetKind, isStandalone } from './logic'

/**
 * カードを 1 枚**新しく作る**ときの入力。
 *
 * 既にあるものを選ぶ口は無い: キャンバスはスタジオの鏡で、素材・場・カット・
 * 生成結果はサーバー側が自動でカードにするため、ここで作るのは「まだ無いもの」
 * だけ。参照カードはスタジオ側の行も一緒にでき、メモ・モデル設定はキャンバス
 * だけのカードになる。
 */
export default function AddCardModal({
  kind,
  detail,
  tab = null,
  busy,
  error,
  onClose,
  onCreate,
}: {
  kind: CanvasCardKind
  detail: StudioProjectDetail
  /** 置き先のタブ（null = 作品共通）。カットの場の候補を絞るのに使う。 */
  tab?: string | null
  busy: boolean
  error: string | null
  onClose: () => void
  onCreate: (payload: Omit<CanvasCardCreate, 'kind' | 'x' | 'y'>) => void
}) {
  const [title, setTitle] = useState('')
  const [assetKind, setAssetKind] = useState<StudioAssetKind>('image')
  const [sceneId, setSceneId] = useState('')

  // カットの出るタブは所属する場で決まるので、いま開いている話の場だけ出す
  // （作品共通タブでは全部の場から選べて、選ばなければ「未分類」になる）。
  const inTab = new Set(
    detail.scenes
      .filter((scene) => !tab || scene.episode_id === tab)
      .map((scene) => scene.id),
  )
  const scenes = sceneOptions(detail).filter((scene) => inTab.has(scene.id))

  const submit = () =>
    onCreate({
      title: title.trim(),
      ...(isAssetKind(kind) ? { asset_kind: assetKind } : {}),
      ...(kind === 'shot' ? { scene_id: sceneId || null } : {}),
    })

  return (
    <Modal title={`${KIND_LABEL[kind]}カードを作る`} onClose={onClose}>
      <div className="flex flex-col gap-3">
        {error && <Banner>{error}</Banner>}

        {isStandalone(kind) ? null : (
          <>
            <div>
              <Label className="mb-1" htmlFor="canvas-new-title">
                {isAssetKind(kind) ? '素材名（@ で呼ぶ名前）' : 'タイトル'}
              </Label>
              <Input
                id="canvas-new-title"
                value={title}
                placeholder={isAssetKind(kind) ? '省略すると自動で付きます' : ''}
                onChange={(event) => setTitle(event.target.value)}
              />
            </div>
            {isAssetKind(kind) && (
              <div>
                <Label className="mb-1" htmlFor="canvas-new-asset-kind">
                  素材の種別
                </Label>
                <NativeSelect
                  id="canvas-new-asset-kind"
                  value={assetKind}
                  onChange={(event) =>
                    setAssetKind(event.target.value as StudioAssetKind)
                  }
                >
                  {(['image', 'video', 'audio'] as StudioAssetKind[]).map((value) => (
                    <option key={value} value={value}>
                      {ASSET_KIND_LABEL[value]}
                    </option>
                  ))}
                </NativeSelect>
              </div>
            )}
            {kind === 'shot' && (
              <div>
                <Label className="mb-1" htmlFor="canvas-new-scene">
                  所属する場
                </Label>
                <NativeSelect
                  id="canvas-new-scene"
                  value={sceneId}
                  onChange={(event) => setSceneId(event.target.value)}
                >
                  <option value="">未分類</option>
                  {scenes.map((scene) => (
                    <option key={scene.id} value={scene.id}>
                      {scene.label}
                    </option>
                  ))}
                </NativeSelect>
              </div>
            )}
          </>
        )}

        <div className="flex justify-end gap-2 border-t border-border pt-3">
          <Button variant="outline" onClick={onClose} disabled={busy}>
            キャンセル
          </Button>
          <Button onClick={submit} disabled={busy}>
            {busy ? '追加中…' : '作る'}
          </Button>
        </div>
      </div>
    </Modal>
  )
}
