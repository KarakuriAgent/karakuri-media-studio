import type { CanvasCard, StudioProjectDetail } from '../../types'
import { SHOT_STATUS_CLASS, SHOT_STATUS_LABEL, TAKE_STATUS_CLASS, TAKE_STATUS_LABEL } from '../studio/studio'
import {
  KIND_ICON,
  KIND_LABEL,
  KIND_STYLE,
  assetMedia,
  assetOf,
  cardSummary,
  cardTitle,
  isDangling,
  isLooseShot,
  sceneOf,
  shotOf,
  shotsInScene,
  takeMedia,
  takeOf,
} from './logic'

/** 素材・生成物のサムネイル（音声は再生できるだけの帯にする）。 */
function Preview({
  media,
  alt,
}: {
  media: { kind: 'image' | 'video' | 'audio'; url: string }
  alt: string
}) {
  if (media.kind === 'video') {
    return (
      <video
        className="max-h-full w-full rounded object-contain"
        src={media.url}
        controls
        preload="metadata"
      />
    )
  }
  if (media.kind === 'audio') {
    return <audio className="w-full" src={media.url} controls preload="metadata" />
  }
  return (
    <img className="max-h-full w-full rounded object-contain" src={media.url} alt={alt} />
  )
}

/** カードの中身（種別ごとの見せ方）。 */
function CardBody({
  card,
  detail,
  busy,
  onAddShot,
}: {
  card: CanvasCard
  detail: StudioProjectDetail
  busy?: boolean
  onAddShot?: (sceneId: string) => void
}) {
  if (isDangling(card, detail)) {
    return (
      <p className="text-xs text-amber-300">
        参照先がスタジオから消えています（カードを外してください）
      </p>
    )
  }

  const asset = assetOf(card, detail)
  if (asset) {
    const media = assetMedia(asset)
    const references = asset.files?.length ?? 0
    return (
      <div className="flex h-full flex-col gap-1">
        {media && (
          <div className="min-h-0 flex-1 overflow-hidden">
            <Preview media={media} alt={asset.name} />
          </div>
        )}
        <p className="line-clamp-3 whitespace-pre-wrap break-words text-xs text-slate-300">
          {asset.caption || asset.prompt_caption || (
            <span className="text-slate-600">（説明なし）</span>
          )}
        </p>
        {references > 0 && (
          <span
            className="chip w-fit !px-1.5 !py-0 text-[10px] text-slate-400"
            title="声サンプル・動画リファレンス・追加画像（編集で見られます）"
          >
            📎 リファレンス {references}
          </span>
        )}
      </div>
    )
  }

  const scene = sceneOf(card, detail)
  if (scene) {
    const shots = shotsInScene(detail, scene.id)
    return (
      <div className="flex h-full flex-col gap-1">
        {(scene.synopsis || scene.time_of_day) && (
          <p className="line-clamp-2 whitespace-pre-wrap break-words text-xs text-slate-300">
            {scene.synopsis || scene.time_of_day}
          </p>
        )}
        <div className="flex items-center gap-1">
          <p className="text-[11px] text-slate-500">カット {shots.length} 件</p>
          {onAddShot && (
            <button
              className="btn-ghost !px-1.5 !py-0 text-[10px]"
              title="この場にカットを追加"
              aria-label={`${cardTitle(card, detail)} にカットを追加`}
              disabled={busy}
              onPointerDown={(event) => event.stopPropagation()}
              onClick={(event) => {
                event.stopPropagation()
                onAddShot(scene.id)
              }}
            >
              ＋カット
            </button>
          )}
        </div>
        <ul className="min-h-0 flex-1 overflow-hidden">
          {shots.map((shot, index) => (
            <li key={shot.id} className="flex items-center gap-1.5 text-[11px]">
              <span className="w-4 shrink-0 text-slate-600">{index + 1}</span>
              <span className="min-w-0 flex-1 truncate text-slate-300">
                {shot.title || '無題のカット'}
              </span>
            </li>
          ))}
        </ul>
      </div>
    )
  }

  const shot = shotOf(card, detail)
  if (shot) {
    return (
      <div className="flex h-full flex-col gap-1">
        <div className="flex flex-wrap items-center gap-1">
          <span className={`chip !px-1.5 !py-0 text-[10px] ${SHOT_STATUS_CLASS[shot.status]}`}>
            {SHOT_STATUS_LABEL[shot.status]}
          </span>
          {/* 場に入れていないカットは、どの話にも属さず作品共通タブに出る */}
          {isLooseShot(card, detail) && (
            <span className="chip !px-1.5 !py-0 text-[10px] text-slate-400">
              未分類
            </span>
          )}
          <span className="text-[10px] text-slate-500">{shot.duration_seconds} 秒</span>
        </div>
        <p className="line-clamp-5 whitespace-pre-wrap break-words text-xs text-slate-300">
          {shot.prompt || shot.action || (
            <span className="text-slate-600">（まだ何も書かれていません）</span>
          )}
        </p>
      </div>
    )
  }

  const take = takeOf(card, detail)
  if (take) {
    const media = takeMedia(take)
    return (
      <div className="flex h-full flex-col gap-1">
        <span className={`chip w-fit !px-1.5 !py-0 text-[10px] ${TAKE_STATUS_CLASS[take.status]}`}>
          {TAKE_STATUS_LABEL[take.status]}
        </span>
        {media ? (
          <div className="min-h-0 flex-1 overflow-hidden">
            <Preview media={media} alt="生成結果" />
          </div>
        ) : (
          <p className="line-clamp-3 text-xs text-slate-500">
            {take.error || 'まだ成果物がありません'}
          </p>
        )}
      </div>
    )
  }

  const summary = cardSummary(card, detail)
  return (
    <p className="line-clamp-6 whitespace-pre-wrap break-words text-xs text-slate-300">
      {summary || <span className="text-slate-600">（空のカード）</span>}
    </p>
  )
}

/**
 * ボード上のカード 1 枚。
 *
 * 掴んで動かすのはヘッダー（本文の中の動画やリンクを操作できるように）で、
 * ダブルクリックか ✎ で編集を開く。
 */
export default function CardNode({
  card,
  detail,
  selected,
  onSelect,
  onEdit,
  onDragStart,
  busy,
  onAddShot,
}: {
  card: CanvasCard
  detail: StudioProjectDetail
  selected: boolean
  onSelect: () => void
  onEdit: () => void
  onDragStart: (event: React.PointerEvent) => void
  busy?: boolean
  /** 場カードの「＋カット」（渡さなければボタンを出さない）。 */
  onAddShot?: (sceneId: string) => void
}) {
  return (
    <div
      className={`absolute flex flex-col overflow-hidden rounded-lg border bg-ink-900 text-left shadow-lg ${
        selected ? 'border-accent-500' : 'border-ink-600'
      }`}
      style={{ left: card.x, top: card.y, width: card.w, height: card.h, zIndex: card.z }}
      onPointerDown={onSelect}
      onDoubleClick={onEdit}
      data-card-id={card.id}
    >
      <div
        className={`flex cursor-grab items-center gap-1.5 border-b px-2 py-1 text-[11px] ${KIND_STYLE[card.kind]}`}
        onPointerDown={onDragStart}
        title="ドラッグで移動 / ダブルクリックで編集"
      >
        <span>{KIND_ICON[card.kind]}</span>
        <span className="shrink-0 opacity-80">{KIND_LABEL[card.kind]}</span>
        <span className="truncate font-semibold text-slate-100">
          {cardTitle(card, detail)}
        </span>
        <button
          className="ml-auto shrink-0 opacity-60 hover:opacity-100"
          title="編集"
          aria-label={`${cardTitle(card, detail)} を編集`}
          onPointerDown={(event) => event.stopPropagation()}
          onClick={(event) => {
            event.stopPropagation()
            onEdit()
          }}
        >
          ✎
        </button>
      </div>

      <div className="min-h-0 flex-1 overflow-hidden p-2 text-xs text-slate-300">
        <CardBody card={card} detail={detail} busy={busy} onAddShot={onAddShot} />
      </div>
    </div>
  )
}
