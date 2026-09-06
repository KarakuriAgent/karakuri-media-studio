import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Check, Copy, Loader2, Save } from 'lucide-react'

import { api } from '../../api'
import type {
  BlockingCapabilities,
  BlockingMapResult,
  BlockingResult,
  BlockingScene,
  LibraryItem,
} from '../../types'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Banner, Modal } from '../ui'
import BlockingCameraPanel from './BlockingCameraPanel'
import BlockingObjectPanel from './BlockingObjectPanel'
import BlockingPlanView, { type PlanTarget } from './BlockingPlanView'
import {
  FALLBACK_CAPABILITIES,
  clamp,
  initialBlockingScene,
  labelOf,
  nearestKeyframe,
  round2,
} from './blocking'

/** つまみを動かしているあいだ投げ続けないための待ち時間（ms）。 */
const DEBOUNCE_MS = 150

/** 平面図のズーム（1m あたりの px）。 */
const MIN_ZOOM = 8
const MAX_ZOOM = 80

function tagsOf(text: string): string[] {
  return text
    .split(',')
    .map((value) => value.trim())
    .filter(Boolean)
}

/** クリップボードに写すボタン（プロンプトへ貼るための文章に付ける）。 */
function CopyButton({ text, label }: { text: string; label: string }) {
  const [done, setDone] = useState(false)
  return (
    <Button
      size="xs"
      variant="outline"
      onClick={() => {
        void navigator.clipboard?.writeText(text)
        setDone(true)
        window.setTimeout(() => setDone(false), 1500)
      }}
    >
      {done ? <Check /> : <Copy />}
      {label}
    </Button>
  )
}

/**
 * 構図リファレンス動画（ブロッキング、SPEC §7.2）を人が組むモーダル。
 *
 * 左でオブジェクトを足して属性を決め、中央の平面図（上から見た図）で位置と
 * カメラを動かし、右でカメラのつまみを触りながら 1 コマのプレビューと
 * `location_map`（プロンプトへ写す英文）を見る、という 3 列。
 *
 * `item` を渡すと**その項目の焼き直し**（`POST /api/library/{id}/blocking`。
 * mp4 のパスも id も変わらず版番号だけ上がる）、渡さなければ新規登録
 * （`POST /api/library/blocking`）。
 */
export default function BlockingBuilderModal({
  item,
  onSaved,
  onClose,
}: {
  /** 構図を直す対象（null / 省略 = 新しく作る） */
  item?: LibraryItem | null
  /** 焼き上がったあと（ライブラリ一覧を読み直してもらう） */
  onSaved: (result: BlockingResult) => void
  onClose: () => void
}) {
  const editing = Boolean(item)
  const [scene, setScene] = useState<BlockingScene>(
    () => item?.blocking ?? initialBlockingScene(),
  )
  const [name, setName] = useState('')
  const [tags, setTags] = useState('')
  const [time, setTime] = useState(0)
  const [zoom, setZoom] = useState(28)
  const [selected, setSelected] = useState<PlanTarget | null>(
    () => {
      const first = (item?.blocking ?? initialBlockingScene()).objects[0]
      return first ? { kind: 'object', id: first.id } : null
    },
  )
  const [keyframe, setKeyframe] = useState(0)
  const [capabilities, setCapabilities] = useState<BlockingCapabilities>(
    FALLBACK_CAPABILITIES,
  )
  const [preview, setPreview] = useState<string | null>(null)
  const [previewBusy, setPreviewBusy] = useState(false)
  const [map, setMap] = useState<BlockingMapResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [result, setResult] = useState<BlockingResult | null>(null)

  // 追い越した応答を捨てるための番号と、いま表示している blob URL。
  const previewSeq = useRef(0)
  const mapSeq = useRef(0)
  const previewUrl = useRef<string | null>(null)

  const cameraIndex = nearestKeyframe(scene.camera.keyframes, time)

  /** 上限（ffmpeg の有無と最大値）。取れなくても編集はできるので握りつぶす。 */
  useEffect(() => {
    let alive = true
    api
      .getStudioCapabilities()
      .then((value) => {
        if (alive && value?.blocking) setCapabilities(value.blocking)
      })
      .catch(() => {})
    return () => {
      alive = false
    }
  }, [])

  // 尺を縮めたら、時刻スライダーもその中へ戻す。
  useEffect(() => {
    setTime((current) => Math.min(current, scene.duration))
  }, [scene.duration])

  /** 1 コマのプレビュー（連続操作は最後の 1 回だけ投げ、追い越されたら捨てる）。 */
  useEffect(() => {
    const timer = window.setTimeout(() => {
      const seq = (previewSeq.current += 1)
      setPreviewBusy(true)
      api
        .blockingPreview(scene, time)
        .then((url) => {
          if (seq !== previewSeq.current) {
            URL.revokeObjectURL?.(url)
            return
          }
          if (previewUrl.current) URL.revokeObjectURL?.(previewUrl.current)
          previewUrl.current = url
          setPreview(url)
          setPreviewBusy(false)
          setError(null)
        })
        .catch((caught: unknown) => {
          if (seq !== previewSeq.current) return
          setPreviewBusy(false)
          setError(caught instanceof Error ? caught.message : String(caught))
        })
    }, DEBOUNCE_MS)
    return () => window.clearTimeout(timer)
  }, [scene, time])

  /** プロンプトへ写す文章（レンダしないので安い。こちらも最後の 1 回だけ）。 */
  useEffect(() => {
    const timer = window.setTimeout(() => {
      const seq = (mapSeq.current += 1)
      api
        .blockingLocationMap(scene)
        .then((value) => {
          if (seq === mapSeq.current) setMap(value)
        })
        .catch(() => {
          if (seq === mapSeq.current) setMap(null)
        })
    }, DEBOUNCE_MS)
    return () => window.clearTimeout(timer)
  }, [scene])

  // 閉じるときに、最後に取ったプレビューの blob を捨てる。
  useEffect(
    () => () => {
      if (previewUrl.current) URL.revokeObjectURL?.(previewUrl.current)
    },
    [],
  )

  /**
   * 平面図のドラッグ: 動かすのは**選択中のキーフレーム**（別のものを掴んだ
   * ときは、その時刻に一番近いキーフレーム）。
   */
  const moveOnPlan = useCallback(
    (target: PlanTarget, x: number, z: number) => {
      setScene((current) => {
        if (target.kind === 'object') {
          return {
            ...current,
            objects: current.objects.map((object) => {
              if (object.id !== target.id) return object
              const index =
                selected?.kind === 'object' && selected.id === target.id
                  ? Math.min(keyframe, object.keyframes.length - 1)
                  : nearestKeyframe(object.keyframes, time)
              return {
                ...object,
                keyframes: object.keyframes.map((frame, at) =>
                  at === index
                    ? { ...frame, position: [x, frame.position[1], z] }
                    : frame,
                ),
              }
            }),
          }
        }
        const index = nearestKeyframe(current.camera.keyframes, time)
        return {
          ...current,
          camera: {
            ...current.camera,
            keyframes: current.camera.keyframes.map((frame, at) => {
              if (at !== index) return frame
              return target.kind === 'camera'
                ? { ...frame, position: [x, frame.position[1], z] }
                : { ...frame, look_at: [x, frame.look_at[1], z] }
            }),
          },
        }
      })
    },
    [keyframe, selected, time],
  )

  const save = async () => {
    setSaving(true)
    setError(null)
    try {
      const saved = item
        ? await api.rerenderLibraryBlocking(item.id, scene)
        : await api.createLibraryBlocking({
            scene,
            name: name.trim(),
            tags: tagsOf(tags),
          })
      setResult(saved)
      onSaved(saved)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      setSaving(false)
    }
  }

  /** 現在時刻の画面上の位置（location-map の 3 点のうち、いちばん近いもの）。 */
  const positions = useMemo(() => {
    if (!map || map.positions.length === 0) return null
    return map.positions[nearestKeyframe(map.positions, time)]
  }, [map, time])

  const objectLabel = (id: string) => {
    const object = scene.objects.find((entry) => entry.id === id)
    return object ? labelOf(object) : id
  }

  return (
    <Modal
      title={editing ? `構図を編集: ${item?.name}` : '構図リファレンスを作る'}
      onClose={onClose}
      wide
    >
      <div className="flex flex-col gap-3">
        {error && <Banner onClose={() => setError(null)}>{error}</Banner>}
        {!capabilities.available && capabilities.error && (
          <Banner tone="warn">{capabilities.error}</Banner>
        )}

        {result && (
          <div className="flex flex-col gap-2 rounded-md border border-primary bg-primary/10 p-3">
            <p className="text-xs">
              {editing
                ? `焼き直しました（版 ${result.item.blocking_version ?? 1}）。`
                : `ライブラリに登録しました（${result.item.name}）。`}
              {' '}
              {result.width}×{result.height} / {result.fps}fps / {result.frames}コマ /{' '}
              {result.duration}秒
            </p>
            <div>
              <Label className="mb-1 text-[11px] text-muted-foreground">
                location_map（カット本文へ写す）
              </Label>
              <pre className="max-h-40 overflow-auto rounded-md bg-surface-sunken p-2 text-[11px] whitespace-pre-wrap">
                {result.location_map}
              </pre>
            </div>
            <div>
              <Label className="mb-1 text-[11px] text-muted-foreground">
                reference_note（retention_analysis へ写す）
              </Label>
              <pre className="max-h-24 overflow-auto rounded-md bg-surface-sunken p-2 text-[11px] whitespace-pre-wrap">
                {result.reference_note}
              </pre>
            </div>
            <div className="flex flex-wrap gap-2">
              <CopyButton text={result.location_map} label="location_map をコピー" />
              <CopyButton
                text={result.reference_note}
                label="reference_note をコピー"
              />
              <Button size="xs" className="ml-auto" onClick={onClose}>
                閉じる
              </Button>
            </div>
          </div>
        )}

        <div className="grid gap-3 lg:grid-cols-[14rem_minmax(0,1fr)_18rem]">
          <BlockingObjectPanel
            scene={scene}
            capabilities={capabilities}
            selectedId={selected?.kind === 'object' ? selected.id : null}
            selectedKeyframe={keyframe}
            disabled={saving}
            onScene={setScene}
            onSelect={(id) => setSelected({ kind: 'object', id })}
            onSelectKeyframe={setKeyframe}
          />

          <div className="flex flex-col gap-2">
            <BlockingPlanView
              scene={scene}
              t={time}
              zoom={zoom}
              selected={selected}
              selectedKeyframe={keyframe}
              onSelect={(target) => {
                setSelected(target)
                if (target.kind === 'object') {
                  const object = scene.objects.find((entry) => entry.id === target.id)
                  if (object) setKeyframe(nearestKeyframe(object.keyframes, time))
                }
              }}
              onSelectKeyframe={(id, index) => {
                setSelected({ kind: 'object', id })
                setKeyframe(index)
              }}
              onMove={moveOnPlan}
            />
            <div className="flex items-center gap-2">
              <Label className="text-[11px] text-muted-foreground" htmlFor="blocking-zoom">
                ズーム
              </Label>
              <input
                id="blocking-zoom"
                type="range"
                className="flex-1"
                aria-label="平面図のズーム"
                min={MIN_ZOOM}
                max={MAX_ZOOM}
                step={1}
                value={zoom}
                onChange={(event) =>
                  setZoom(clamp(Number(event.target.value), MIN_ZOOM, MAX_ZOOM))
                }
              />
              <span className="tnum text-[11px] text-muted-foreground">
                1m = {zoom}px
              </span>
            </div>
            <p className="text-[11px] text-muted-foreground">
              図形とカメラ（青い丸）・注視点（十字）はドラッグで動かせます。動くのは
              選んでいるキーフレーム（無ければ今の時刻に一番近いもの）です。
            </p>
          </div>

          <div className="flex flex-col gap-3">
            <BlockingCameraPanel
              scene={scene}
              capabilities={capabilities}
              cameraIndex={cameraIndex}
              disabled={saving}
              onScene={setScene}
            />

            <div>
              <div className="mb-1 flex items-center justify-between">
                <Label className="text-[11px] text-muted-foreground">
                  プレビュー（{time.toFixed(2)} 秒）
                </Label>
                {previewBusy && (
                  <Loader2 className="size-3 animate-spin text-primary" aria-hidden />
                )}
              </div>
              <div className="flex aspect-video items-center justify-center overflow-hidden rounded-md border border-border bg-background">
                {preview ? (
                  <img
                    src={preview}
                    alt={`${time.toFixed(2)} 秒のプレビュー`}
                    className="size-full object-contain"
                  />
                ) : (
                  <span className="text-[11px] text-muted-foreground">
                    読み込み中…
                  </span>
                )}
              </div>
              <input
                type="range"
                className="mt-1 w-full"
                aria-label="プレビューの時刻"
                min={0}
                max={scene.duration}
                step={round2(1 / (capabilities.fps || 24))}
                value={time}
                onChange={(event) => setTime(Number(event.target.value))}
              />
            </div>

            {positions && (
              <div>
                <Label className="mb-1 text-[11px] text-muted-foreground">
                  画面上の位置（{positions.t} 秒）
                </Label>
                <ul className="tnum flex flex-col gap-0.5 text-[11px] text-foreground/85">
                  {positions.objects.map((object) => (
                    <li key={object.id}>
                      {object.label || objectLabel(object.id)}:{' '}
                      {object.center.x_percent.toFixed(0)}%,{' '}
                      {object.center.y_percent.toFixed(0)}%
                      {object.center.visible ? '' : '（画面の外）'}
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {map && (
              <div>
                <div className="mb-1 flex items-center justify-between">
                  <Label className="text-[11px] text-muted-foreground">
                    location_map
                  </Label>
                  <CopyButton text={map.location_map} label="コピー" />
                </div>
                <pre className="max-h-40 overflow-auto rounded-md border border-border bg-surface-sunken p-2 text-[11px] whitespace-pre-wrap">
                  {map.location_map}
                </pre>
                <div className="mt-1 flex items-center justify-between">
                  <Label className="text-[11px] text-muted-foreground">
                    reference_note
                  </Label>
                  <CopyButton text={map.reference_note} label="コピー" />
                </div>
                <pre className="max-h-24 overflow-auto rounded-md border border-border bg-surface-sunken p-2 text-[11px] whitespace-pre-wrap">
                  {map.reference_note}
                </pre>
              </div>
            )}
          </div>
        </div>

        <div className="flex flex-wrap items-end gap-2 border-t border-border pt-3">
          {!editing && (
            <>
              <div className="min-w-[10rem] flex-1">
                <Label className="mb-1 text-[11px] text-muted-foreground" htmlFor="blocking-name">
                  名前（空ならオブジェクトの並びから決まります）
                </Label>
                <Input
                  id="blocking-name"
                  className="h-8 text-xs"
                  value={name}
                  disabled={saving}
                  onChange={(event) => setName(event.target.value)}
                />
              </div>
              <div className="min-w-[10rem] flex-1">
                <Label className="mb-1 text-[11px] text-muted-foreground" htmlFor="blocking-tags">
                  タグ（カンマ区切り）
                </Label>
                <Input
                  id="blocking-tags"
                  className="h-8 text-xs"
                  placeholder="屋上, 対峙"
                  value={tags}
                  disabled={saving}
                  onChange={(event) => setTags(event.target.value)}
                />
              </div>
            </>
          )}
          <Button
            className="ml-auto"
            size="sm"
            disabled={saving || !capabilities.available}
            onClick={() => void save()}
          >
            {saving ? <Loader2 className="animate-spin" /> : <Save />}
            {saving ? '焼いています…' : editing ? '焼き直して保存' : '保存'}
          </Button>
        </div>
      </div>
    </Modal>
  )
}
