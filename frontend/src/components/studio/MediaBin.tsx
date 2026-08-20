import { useCallback, useEffect, useState } from 'react'
import { Image, Loader2, Music, Plus, RefreshCw, Video } from 'lucide-react'

import { api } from '../../api'
import type { TimelineMediaItem, TimelineMediaKind } from '../../types'
import { Section } from '../ui'
import { Button } from '../ui/button'
import { formatSeconds } from './timeline'

/** 1 回に読む件数（「もっと読む」で足していく）。 */
const PAGE_SIZE = 30

const KINDS: { value: TimelineMediaKind; label: string; icon: typeof Video }[] = [
  { value: 'video', label: '動画', icon: Video },
  { value: 'audio', label: '音声', icon: Music },
  { value: 'image', label: '画像', icon: Image },
]

/**
 * 素材ビン。タイムラインへ足せるものを種別ごとに並べる。
 *
 * 出どころはサーバーが 1 本に混ぜて返す（テイク・ライブラリ・単発ジョブ・作品の
 * 素材ファイル）。ここは「どれを押したか」を親へ上げるだけで、どのトラックの
 * どこへ置くかは親（`EditView`）が決める。
 *
 * 押した先は種別で決まる: 動画・画像は V1 の末尾、音声は選んでいる音声トラックの
 * 空いているところ。ドラッグ&ドロップは持たない（クリック 1 回で置いてから
 * タイムライン上で動かすほうが、細かい位置合わせがやりやすい）。
 */
export default function MediaBin({
  projectId,
  canAddAudio,
  onAdd,
}: {
  projectId: string
  /** 音声トラックがあるか（無ければ音声の追加ボタンは出さない）。 */
  canAddAudio: boolean
  onAdd: (item: TimelineMediaItem) => void
}) {
  const [kind, setKind] = useState<TimelineMediaKind>('video')
  const [items, setItems] = useState<TimelineMediaItem[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(
    async (target: TimelineMediaKind, offset: number) => {
      setLoading(true)
      setError(null)
      try {
        const page = await api.listStudioTimelineMedia(
          projectId,
          target,
          PAGE_SIZE,
          offset,
        )
        setItems((current) =>
          offset === 0 ? page.items : [...current, ...page.items],
        )
        setTotal(page.total)
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : String(cause))
      } finally {
        setLoading(false)
      }
    },
    [projectId],
  )

  useEffect(() => {
    setItems([])
    void load(kind, 0)
  }, [kind, load])

  const addable = kind !== 'audio' || canAddAudio

  return (
    <Section
      title="素材ビン"
      right={
        <Button
          type="button"
          size="icon-xs"
          variant="ghost"
          title="読み直す"
          onClick={() => void load(kind, 0)}
          disabled={loading}
        >
          {loading ? (
            <Loader2 className="size-4 animate-spin" aria-hidden="true" />
          ) : (
            <RefreshCw className="size-4" aria-hidden="true" />
          )}
          <span className="sr-only">読み直す</span>
        </Button>
      }
    >
      <div className="flex flex-col gap-2">
        <div className="flex gap-1">
          {KINDS.map((option) => (
            <Button
              key={option.value}
              type="button"
              size="sm"
              variant={kind === option.value ? 'secondary' : 'ghost'}
              onClick={() => setKind(option.value)}
            >
              <option.icon className="size-4" aria-hidden="true" />
              {option.label}
            </Button>
          ))}
        </div>

        {error && (
          <p className="rounded border border-red-900/70 bg-red-950/50 px-2 py-1.5 text-xs text-red-200">
            素材を読めませんでした: {error}
          </p>
        )}

        {kind === 'audio' && !canAddAudio && (
          <p className="text-[11px] text-amber-300">
            音声トラックがありません。「A1 を追加」で音声トラックを作ってください。
          </p>
        )}

        <ul className="flex max-h-64 flex-col gap-1 overflow-y-auto">
          {items.map((item) => (
            <li
              key={`${item.source_kind}:${item.source_id}`}
              className="flex items-center gap-2 rounded border border-border bg-secondary/40 px-2 py-1.5"
            >
              {item.media_kind === 'image' && item.url ? (
                <img
                  src={item.url}
                  alt=""
                  className="size-8 shrink-0 rounded object-cover"
                  loading="lazy"
                />
              ) : (
                <span className="flex size-8 shrink-0 items-center justify-center rounded bg-background/60 text-muted-foreground">
                  {item.media_kind === 'audio' ? (
                    <Music className="size-4" aria-hidden="true" />
                  ) : (
                    <Video className="size-4" aria-hidden="true" />
                  )}
                </span>
              )}
              <div className="min-w-0 flex-1">
                <p className="truncate text-[11px] font-medium">{item.name}</p>
                <p className="truncate text-[10px] text-muted-foreground">
                  {item.origin}
                  {item.duration_ms != null &&
                    ` / ${formatSeconds(item.duration_ms)}`}
                </p>
              </div>
              <Button
                type="button"
                size="icon-xs"
                variant="ghost"
                title="タイムラインへ足す"
                disabled={!addable}
                onClick={() => onAdd(item)}
              >
                <Plus className="size-4" aria-hidden="true" />
                <span className="sr-only">タイムラインへ足す</span>
              </Button>
            </li>
          ))}
        </ul>

        {items.length === 0 && !loading && !error && (
          <p className="text-xs text-muted-foreground">
            置ける{KINDS.find((option) => option.value === kind)?.label}
            がありません。
          </p>
        )}

        {items.length < total && (
          <Button
            type="button"
            size="sm"
            variant="ghost"
            disabled={loading}
            onClick={() => void load(kind, items.length)}
          >
            もっと読む（{items.length} / {total}）
          </Button>
        )}
      </div>
    </Section>
  )
}
