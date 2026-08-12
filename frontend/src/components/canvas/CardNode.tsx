import { CircleHelp, Paperclip, Pencil, Plus } from 'lucide-react'

import type { CanvasCard, StudioProjectDetail } from '../../types'
import { Button } from '../ui/button'
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
        <p className="line-clamp-3 whitespace-pre-wrap break-words text-xs text-foreground/85">
          {asset.caption || asset.prompt_caption || (
            <span className="text-muted-foreground">（説明なし）</span>
          )}
        </p>
        {references > 0 && (
          <span
            className="chip w-fit !px-1.5 !py-0 text-[11px] text-muted-foreground"
            title="声サンプル・動画リファレンス・追加画像（編集で見られます）"
          >
            <Paperclip className="size-3 shrink-0" aria-hidden="true" />
            リファレンス {references}
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
          <p className="line-clamp-2 whitespace-pre-wrap break-words text-xs text-foreground/85">
            {scene.synopsis || scene.time_of_day}
          </p>
        )}
        <div className="flex items-center gap-1">
          <p className="tnum text-[11px] text-muted-foreground">
            カット {shots.length} 件
          </p>
          {onAddShot && (
            <Button
              variant="outline"
              size="xs"
              className="h-5 px-1.5 text-[11px]"
              title="この場にカットを追加"
              aria-label={`${cardTitle(card, detail)} にカットを追加`}
              disabled={busy}
              onPointerDown={(event) => event.stopPropagation()}
              onClick={(event) => {
                event.stopPropagation()
                onAddShot(scene.id)
              }}
            >
              <Plus aria-hidden="true" />
              カット
            </Button>
          )}
        </div>
        <ul className="min-h-0 flex-1 overflow-hidden">
          {shots.map((shot, index) => (
            <li key={shot.id} className="flex items-center gap-1.5 text-[11px]">
              <span className="tnum w-4 shrink-0 text-muted-foreground">{index + 1}</span>
              <span className="min-w-0 flex-1 truncate text-foreground/85">
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
          <span className={`chip !px-1.5 !py-0 text-[11px] ${SHOT_STATUS_CLASS[shot.status]}`}>
            {SHOT_STATUS_LABEL[shot.status]}
          </span>
          {/* 場に入れていないカットは、どの話にも属さず作品共通タブに出る */}
          {isLooseShot(card, detail) && (
            <span className="chip !px-1.5 !py-0 text-[11px] text-muted-foreground">
              未分類
            </span>
          )}
          <span className="tnum text-[11px] text-muted-foreground">
            {shot.duration_seconds} 秒
          </span>
        </div>
        <p className="line-clamp-5 whitespace-pre-wrap break-words text-xs text-foreground/85">
          {shot.prompt || shot.action || (
            <span className="text-muted-foreground">（まだ何も書かれていません）</span>
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
        <span className={`chip w-fit !px-1.5 !py-0 text-[11px] ${TAKE_STATUS_CLASS[take.status]}`}>
          {TAKE_STATUS_LABEL[take.status]}
        </span>
        {media ? (
          <div className="min-h-0 flex-1 overflow-hidden">
            <Preview media={media} alt="生成結果" />
          </div>
        ) : (
          <p className="line-clamp-3 text-xs text-muted-foreground">
            {take.error || 'まだ成果物がありません'}
          </p>
        )}
      </div>
    )
  }

  const summary = cardSummary(card, detail)
  return (
    <p className="line-clamp-6 whitespace-pre-wrap break-words text-xs text-foreground/85">
      {summary || <span className="text-muted-foreground">（空のカード）</span>}
    </p>
  )
}

/**
 * ボード上のカード 1 枚。
 *
 * 掴んで動かすのはヘッダー（本文の中の動画やリンクを操作できるように）で、
 * ダブルクリックか鉛筆ボタンで編集を開く。
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
  const KindIcon = KIND_ICON[card.kind] ?? CircleHelp // 未知の種別でも落とさない
  return (
    <div
      className={`absolute flex flex-col overflow-hidden rounded-lg border bg-card text-left shadow-elevation-2 ${
        selected ? 'border-primary' : 'border-border'
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
        <KindIcon className="size-3.5 shrink-0" aria-hidden="true" />
        <span className="shrink-0 opacity-80">{KIND_LABEL[card.kind]}</span>
        <span className="truncate font-semibold text-foreground">
          {cardTitle(card, detail)}
        </span>
        <Button
          variant="ghost"
          size="icon-xs"
          className="ml-auto shrink-0 text-current opacity-60 hover:bg-transparent hover:text-current hover:opacity-100"
          title="編集"
          aria-label={`${cardTitle(card, detail)} を編集`}
          onPointerDown={(event) => event.stopPropagation()}
          onClick={(event) => {
            event.stopPropagation()
            onEdit()
          }}
        >
          <Pencil aria-hidden="true" />
        </Button>
      </div>

      <div className="min-h-0 flex-1 overflow-hidden p-2 text-xs text-foreground/85">
        <CardBody card={card} detail={detail} busy={busy} onAddShot={onAddShot} />
      </div>
    </div>
  )
}
