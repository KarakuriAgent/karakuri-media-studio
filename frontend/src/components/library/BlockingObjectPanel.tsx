import { useState } from 'react'
import { Plus, Trash2 } from 'lucide-react'

import type {
  BlockingCapabilities,
  BlockingObject,
  BlockingObjectKeyframe,
  BlockingScene,
  BlockingShape,
  BlockingVec3,
} from '../../types'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { NativeSelect } from '../NativeSelect'
import { NumberField } from './BlockingFields'
import {
  DEFAULT_SIZES,
  FACING_LABELS,
  OBJECT_COLORS,
  SHAPE_LABELS,
  labelOf,
  newBlockingObject,
  round2,
  sizeOf,
} from './blocking'

/** id に使える文字（英数字と `_`。サーバー側の検証と同じ）。 */
function sanitizeId(value: string): string {
  return value.replace(/[^A-Za-z0-9_]/g, '').slice(0, 40)
}

/**
 * 左の列: オブジェクトの一覧と、選んだ 1 件の属性・キーフレーム。
 *
 * 変更は `scene` を作り直して `onScene` に渡す（このコンポーネントは状態を
 * 持たない。持つのは「これから追加する形状」だけ）。
 */
export default function BlockingObjectPanel({
  scene,
  capabilities,
  selectedId,
  selectedKeyframe,
  disabled,
  onScene,
  onSelect,
  onSelectKeyframe,
}: {
  scene: BlockingScene
  capabilities: BlockingCapabilities
  selectedId: string | null
  selectedKeyframe: number
  disabled?: boolean
  onScene: (scene: BlockingScene) => void
  onSelect: (id: string) => void
  onSelectKeyframe: (index: number) => void
}) {
  const [shape, setShape] = useState<BlockingShape>('figure')

  const selected = scene.objects.find((object) => object.id === selectedId) ?? null
  const full = scene.objects.length >= capabilities.max_objects

  const replace = (id: string, patch: Partial<BlockingObject>) =>
    onScene({
      ...scene,
      objects: scene.objects.map((object) =>
        object.id === id ? { ...object, ...patch } : object,
      ),
    })

  /** id を付け替える（追っていたプリセットの target も新しい id へ写す）。 */
  const rename = (from: string, to: string) => {
    const move =
      scene.camera.move && scene.camera.move.target === from
        ? { ...scene.camera.move, target: to }
        : scene.camera.move
    onScene({
      ...scene,
      objects: scene.objects.map((object) =>
        object.id === from ? { ...object, id: to } : object,
      ),
      camera: { ...scene.camera, move },
    })
  }

  const add = () => {
    if (full) return
    const object = newBlockingObject(scene, shape)
    onScene({ ...scene, objects: [...scene.objects, object] })
    onSelect(object.id)
    onSelectKeyframe(0)
  }

  const remove = (id: string) => {
    const objects = scene.objects.filter((object) => object.id !== id)
    // 追っていた相手が消えたらプリセットの target も外す（そのままだと 400）。
    const move =
      scene.camera.move && scene.camera.move.target === id
        ? { ...scene.camera.move, target: null }
        : scene.camera.move
    onScene({ ...scene, objects, camera: { ...scene.camera, move } })
    if (objects.length > 0) onSelect(objects[0].id)
  }

  const patchKeyframe = (
    object: BlockingObject,
    index: number,
    patch: Partial<BlockingObjectKeyframe>,
  ) =>
    replace(object.id, {
      keyframes: object.keyframes.map((keyframe, at) =>
        at === index ? { ...keyframe, ...patch } : keyframe,
      ),
    })

  const addKeyframe = (object: BlockingObject) => {
    if (object.keyframes.length >= capabilities.max_keyframes) return
    const last = object.keyframes[object.keyframes.length - 1]
    // 末尾の 1 秒後（尺を越えるなら末尾と尺の真ん中）に足す。
    const t = round2(
      last.t + 1 <= scene.duration ? last.t + 1 : (last.t + scene.duration) / 2,
    )
    if (t <= last.t) return
    replace(object.id, {
      keyframes: [...object.keyframes, { ...last, t }],
    })
    onSelectKeyframe(object.keyframes.length)
  }

  const removeKeyframe = (object: BlockingObject, index: number) => {
    if (object.keyframes.length <= 1) return
    replace(object.id, {
      keyframes: object.keyframes.filter((_, at) => at !== index),
    })
    onSelectKeyframe(Math.max(0, index - 1))
  }

  return (
    <div className="flex flex-col gap-3">
      <div>
        <h3 className="mb-1 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
          オブジェクト（{scene.objects.length} / {capabilities.max_objects}）
        </h3>
        <div className="flex items-center gap-1">
          <NativeSelect
            className="h-8 flex-1 text-xs"
            aria-label="追加する形状"
            value={shape}
            disabled={disabled}
            onChange={(event) => setShape(event.target.value as BlockingShape)}
          >
            {(Object.keys(SHAPE_LABELS) as BlockingShape[]).map((value) => (
              <option key={value} value={value}>
                {SHAPE_LABELS[value]}
              </option>
            ))}
          </NativeSelect>
          <Button size="xs" variant="outline" disabled={disabled || full} onClick={add}>
            <Plus />
            追加
          </Button>
        </div>
        {full && (
          <p className="mt-1 text-[11px] text-amber-400">
            オブジェクトは {capabilities.max_objects} 件までです。
          </p>
        )}
      </div>

      <ul className="flex flex-col gap-1">
        {scene.objects.map((object) => (
          <li key={object.id}>
            <button
              type="button"
              aria-current={object.id === selectedId ? 'true' : undefined}
              className={`flex w-full items-center gap-2 rounded-md border px-2 py-1 text-left text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50 ${
                object.id === selectedId
                  ? 'border-primary bg-primary/15'
                  : 'border-border bg-card hover:bg-secondary'
              }`}
              onClick={() => {
                onSelect(object.id)
                onSelectKeyframe(0)
              }}
            >
              <span
                className="size-3 shrink-0 rounded-sm border border-border"
                style={{ backgroundColor: object.color }}
                aria-hidden
              />
              <span className="flex-1 truncate">{labelOf(object)}</span>
              <span className="text-[11px] text-muted-foreground">
                {SHAPE_LABELS[object.shape]}
              </span>
            </button>
          </li>
        ))}
      </ul>

      {selected && (
        <div className="flex flex-col gap-2 rounded-md border border-border bg-surface-sunken/50 p-2">
          <div className="grid grid-cols-2 gap-2">
            <div>
              <Label className="mb-1 text-[11px] text-muted-foreground">id</Label>
              <Input
                className="h-7 px-1.5 text-xs"
                aria-label="オブジェクトの id"
                value={selected.id}
                disabled={disabled}
                onChange={(event) => {
                  const id = sanitizeId(event.target.value)
                  if (!id || scene.objects.some((object) => object.id === id)) return
                  rename(selected.id, id)
                  onSelect(id)
                }}
              />
            </div>
            <div>
              <Label className="mb-1 text-[11px] text-muted-foreground">表示名</Label>
              <Input
                className="h-7 px-1.5 text-xs"
                aria-label="オブジェクトの表示名"
                value={selected.label}
                disabled={disabled}
                onChange={(event) => replace(selected.id, { label: event.target.value })}
              />
            </div>
          </div>

          <div className="grid grid-cols-2 gap-2">
            <div>
              <Label className="mb-1 text-[11px] text-muted-foreground">形状</Label>
              <NativeSelect
                className="h-7 text-xs"
                aria-label="形状"
                value={selected.shape}
                disabled={disabled}
                onChange={(event) =>
                  // 大きさが既定のまま（null）なら、新しい形状の既定に付いていく。
                  replace(selected.id, { shape: event.target.value as BlockingShape })
                }
              >
                {(Object.keys(SHAPE_LABELS) as BlockingShape[]).map((value) => (
                  <option key={value} value={value}>
                    {SHAPE_LABELS[value]}
                  </option>
                ))}
              </NativeSelect>
            </div>
            <div>
              <Label className="mb-1 text-[11px] text-muted-foreground">色</Label>
              <NativeSelect
                className="h-7 text-xs"
                aria-label="色"
                value={selected.color}
                disabled={disabled}
                onChange={(event) => replace(selected.id, { color: event.target.value })}
              >
                {/* 外から来た色（プリセット外）も選択のまま残す */}
                {!OBJECT_COLORS.some((entry) => entry.value === selected.color) && (
                  <option value={selected.color}>{selected.color}</option>
                )}
                {OBJECT_COLORS.map((entry) => (
                  <option key={entry.value} value={entry.value}>
                    {entry.name}
                  </option>
                ))}
              </NativeSelect>
            </div>
          </div>

          <div>
            <Label className="mb-1 text-[11px] text-muted-foreground">向きの決め方</Label>
            <NativeSelect
              className="h-7 text-xs"
              aria-label="向きの決め方"
              value={selected.facing}
              disabled={disabled}
              onChange={(event) =>
                replace(selected.id, {
                  facing: event.target.value as BlockingObject['facing'],
                })
              }
            >
              {(Object.keys(FACING_LABELS) as BlockingObject['facing'][]).map((value) => (
                <option key={value} value={value}>
                  {FACING_LABELS[value]}
                </option>
              ))}
            </NativeSelect>
          </div>

          <div>
            <div className="mb-1 flex items-center justify-between">
              <Label className="text-[11px] text-muted-foreground">
                大きさ（m。幅・高さ・奥行き）
              </Label>
              {selected.size !== null && (
                <Button
                  size="xs"
                  variant="ghost"
                  disabled={disabled}
                  onClick={() => replace(selected.id, { size: null })}
                >
                  既定に戻す
                </Button>
              )}
            </div>
            <div className="grid grid-cols-3 gap-1">
              {(['幅', '高さ', '奥行き'] as const).map((name, axis) => (
                <NumberField
                  key={name}
                  label={`${name}（m）`}
                  hideLabel
                  value={round2(sizeOf(selected)[axis])}
                  step={0.05}
                  min={0.01}
                  max={50}
                  disabled={disabled}
                  onChange={(value) => {
                    const size = [...sizeOf(selected)] as BlockingVec3
                    size[axis] = value
                    replace(selected.id, { size })
                  }}
                />
              ))}
            </div>
            {selected.size === null && (
              <p className="mt-1 text-[11px] text-muted-foreground">
                いまは {SHAPE_LABELS[selected.shape]} の既定（
                {DEFAULT_SIZES[selected.shape].join(' × ')}m）です。
              </p>
            )}
          </div>

          <div>
            <div className="mb-1 flex items-center justify-between">
              <Label className="text-[11px] text-muted-foreground">
                キーフレーム（{selected.keyframes.length} /{' '}
                {capabilities.max_keyframes}）
              </Label>
              <Button
                size="xs"
                variant="outline"
                disabled={
                  disabled || selected.keyframes.length >= capabilities.max_keyframes
                }
                onClick={() => addKeyframe(selected)}
              >
                <Plus />
                追加
              </Button>
            </div>
            <ul className="flex flex-col gap-1">
              {selected.keyframes.map((keyframe, index) => (
                <li
                  key={index}
                  className={`rounded-md border p-1 ${
                    index === selectedKeyframe
                      ? 'border-primary bg-primary/10'
                      : 'border-border'
                  }`}
                  onFocus={() => onSelectKeyframe(index)}
                >
                  <div className="grid grid-cols-[3rem_1fr_1fr] items-end gap-1">
                    <NumberField
                      label={`キーフレーム ${index + 1} の t（秒）`}
                      hideLabel
                      value={keyframe.t}
                      step={0.1}
                      min={0}
                      max={scene.duration}
                      disabled={disabled}
                      onChange={(t) => patchKeyframe(selected, index, { t })}
                    />
                    <NumberField
                      label={`キーフレーム ${index + 1} の X（m）`}
                      hideLabel
                      value={round2(keyframe.position[0])}
                      disabled={disabled}
                      onChange={(x) =>
                        patchKeyframe(selected, index, {
                          position: [x, keyframe.position[1], keyframe.position[2]],
                        })
                      }
                    />
                    <NumberField
                      label={`キーフレーム ${index + 1} の Z（m）`}
                      hideLabel
                      value={round2(keyframe.position[2])}
                      disabled={disabled}
                      onChange={(z) =>
                        patchKeyframe(selected, index, {
                          position: [keyframe.position[0], keyframe.position[1], z],
                        })
                      }
                    />
                  </div>
                  <div className="mt-1 grid grid-cols-[3rem_1fr_1fr_auto] items-end gap-1">
                    <span className="text-[11px] text-muted-foreground">yaw</span>
                    <NumberField
                      label={`キーフレーム ${index + 1} の yaw（度）`}
                      hideLabel
                      value={round2(keyframe.yaw_deg)}
                      step={5}
                      disabled={disabled}
                      onChange={(yaw_deg) =>
                        patchKeyframe(selected, index, { yaw_deg })
                      }
                    />
                    <NumberField
                      label={`キーフレーム ${index + 1} の Y（m）`}
                      hideLabel
                      value={round2(keyframe.position[1])}
                      disabled={disabled}
                      onChange={(y) =>
                        patchKeyframe(selected, index, {
                          position: [keyframe.position[0], y, keyframe.position[2]],
                        })
                      }
                    />
                    <Button
                      size="icon-xs"
                      variant="ghost"
                      title="このキーフレームを削除"
                      aria-label={`キーフレーム ${index + 1} を削除`}
                      disabled={disabled || selected.keyframes.length <= 1}
                      onClick={() => removeKeyframe(selected, index)}
                    >
                      <Trash2 />
                    </Button>
                  </div>
                </li>
              ))}
            </ul>
            <p className="mt-1 text-[11px] text-muted-foreground">
              左から t（秒）・X・Z、下の段が yaw（度）・Y（m）。
            </p>
          </div>

          <Button
            size="xs"
            variant="destructive"
            className="self-start"
            disabled={disabled || scene.objects.length <= 1}
            onClick={() => remove(selected.id)}
          >
            <Trash2 />
            このオブジェクトを削除
          </Button>
        </div>
      )}
    </div>
  )
}
