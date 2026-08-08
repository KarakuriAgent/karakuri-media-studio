import { useState } from 'react'
import type {
  CanvasCardCreate,
  CanvasCardKind,
  StudioAssetKind,
  StudioProjectDetail,
} from '../../types'
import { Banner, Modal } from '../ui'
import { ASSET_KIND_LABEL, sceneOptions } from '../studio/studio'
import { KIND_ICON, KIND_LABEL, isAssetKind, isStandalone } from './logic'

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
    <Modal
      title={`${KIND_ICON[kind]} ${KIND_LABEL[kind]}カードを作る`}
      onClose={onClose}
    >
      <div className="flex flex-col gap-3">
        {error && <Banner>{error}</Banner>}

        {isStandalone(kind) ? (
          <p className="text-xs text-slate-500">
            {KIND_LABEL[kind]}カードはキャンバスだけのカードです。中身は置いた
            あとに編集します。
          </p>
        ) : (
          <>
            <p className="text-xs text-slate-500">
              スタジオの{KIND_LABEL[kind]}を新しく 1 つ作って、そのカードを置きます。
            </p>
            <div>
              <label className="label" htmlFor="canvas-new-title">
                {isAssetKind(kind) ? '素材名（@ で呼ぶ名前）' : 'タイトル'}
              </label>
              <input
                id="canvas-new-title"
                className="field"
                value={title}
                placeholder={isAssetKind(kind) ? '省略すると自動で付きます' : ''}
                onChange={(event) => setTitle(event.target.value)}
              />
            </div>
            {isAssetKind(kind) && (
              <div>
                <label className="label" htmlFor="canvas-new-asset-kind">
                  素材の種別
                </label>
                <select
                  id="canvas-new-asset-kind"
                  className="field"
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
                </select>
                <p className="mt-1 text-[11px] text-slate-500">
                  ファイルはスタジオの World Bible から差し替えます
                </p>
              </div>
            )}
            {kind === 'shot' && (
              <div>
                <label className="label" htmlFor="canvas-new-scene">
                  所属する場
                </label>
                <select
                  id="canvas-new-scene"
                  className="field"
                  value={sceneId}
                  onChange={(event) => setSceneId(event.target.value)}
                >
                  <option value="">未分類</option>
                  {scenes.map((scene) => (
                    <option key={scene.id} value={scene.id}>
                      {scene.label}
                    </option>
                  ))}
                </select>
                {tab && (
                  <p className="mt-1 text-[11px] text-slate-500">
                    場を選ばないと未分類のカットになり、「作品共通」タブに出ます
                  </p>
                )}
              </div>
            )}
          </>
        )}

        <div className="flex justify-end gap-2 border-t border-ink-600 pt-3">
          <button className="btn-ghost" onClick={onClose} disabled={busy}>
            キャンセル
          </button>
          <button className="btn-primary" onClick={submit} disabled={busy}>
            {busy ? '追加中…' : '作る'}
          </button>
        </div>
      </div>
    </Modal>
  )
}
