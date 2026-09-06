import { Plus, Trash2 } from 'lucide-react'

import type {
  BlockingAspectRatio,
  BlockingCameraKeyframe,
  BlockingCapabilities,
  BlockingEasing,
  BlockingMoveType,
  BlockingScene,
} from '../../types'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { NativeSelect } from '../NativeSelect'
import { NumberField } from './BlockingFields'
import {
  ASPECT_RATIOS,
  EASING_LABELS,
  MOVES_IN_METERS,
  MOVES_NEEDING_TARGET,
  MOVE_LABELS,
  clamp,
  labelOf,
  round2,
} from './blocking'

/**
 * 尺を縮めたときに、はみ出したキーフレームを落とす。
 *
 * `t` が尺を越えたまま送ると 400 になるので、UI 側で先に整える（先頭は必ず
 * 残す。カメラは `t=0` を含んでいないといけないため）。
 */
function withinDuration<T extends { t: number }>(keyframes: T[], duration: number): T[] {
  const kept = keyframes.filter((keyframe, index) => index === 0 || keyframe.t <= duration)
  return kept.map((keyframe, index) =>
    index === 0 ? { ...keyframe, t: Math.min(keyframe.t, duration) } : keyframe,
  )
}

/**
 * 右の列の上半分: カメラのつまみ（位置以外）と、出力の規格。
 *
 * 平面図で動かせるのは XZ だけなので、高さ・画角・傾き・プリセットはここで
 * 触る。編集の対象は**いまの時刻に一番近いカメラキーフレーム**（`cameraIndex`）。
 */
export default function BlockingCameraPanel({
  scene,
  capabilities,
  cameraIndex,
  disabled,
  onScene,
}: {
  scene: BlockingScene
  capabilities: BlockingCapabilities
  cameraIndex: number
  disabled?: boolean
  onScene: (scene: BlockingScene) => void
}) {
  const keyframes = scene.camera.keyframes
  const current = keyframes[Math.min(cameraIndex, keyframes.length - 1)]
  const move = scene.camera.move
  const aspectRatios = (
    capabilities.aspect_ratios.length > 0
      ? capabilities.aspect_ratios
      : ASPECT_RATIOS
  ) as BlockingAspectRatio[]

  const patchCamera = (patch: Partial<BlockingScene['camera']>) =>
    onScene({ ...scene, camera: { ...scene.camera, ...patch } })

  const patchKeyframe = (patch: Partial<BlockingCameraKeyframe>) =>
    patchCamera({
      keyframes: keyframes.map((keyframe, index) =>
        index === cameraIndex ? { ...keyframe, ...patch } : keyframe,
      ),
    })

  const addKeyframe = () => {
    if (keyframes.length >= capabilities.max_keyframes) return
    const last = keyframes[keyframes.length - 1]
    const t = round2(
      last.t + 1 <= scene.duration ? last.t + 1 : (last.t + scene.duration) / 2,
    )
    if (t <= last.t) return
    patchCamera({ keyframes: [...keyframes, { ...last, t }] })
  }

  const setDuration = (value: number) => {
    const duration = clamp(
      round2(value),
      capabilities.min_duration,
      capabilities.max_duration,
    )
    onScene({
      ...scene,
      duration,
      camera: {
        ...scene.camera,
        keyframes: withinDuration(scene.camera.keyframes, duration),
      },
      objects: scene.objects.map((object) => ({
        ...object,
        keyframes: withinDuration(object.keyframes, duration),
      })),
    })
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center justify-between">
        <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
          カメラ
        </h3>
        <div className="flex items-center gap-1">
          <span className="text-[11px] text-muted-foreground">
            キーフレーム {cameraIndex + 1} / {keyframes.length}
          </span>
          <Button
            size="icon-xs"
            variant="ghost"
            title="カメラのキーフレームを足す"
            aria-label="カメラのキーフレームを追加"
            disabled={disabled || keyframes.length >= capabilities.max_keyframes}
            onClick={addKeyframe}
          >
            <Plus />
          </Button>
          <Button
            size="icon-xs"
            variant="ghost"
            title="このカメラキーフレームを削除"
            aria-label="カメラのキーフレームを削除"
            disabled={disabled || keyframes.length <= 1 || cameraIndex === 0}
            onClick={() =>
              patchCamera({
                keyframes: keyframes.filter((_, index) => index !== cameraIndex),
              })
            }
          >
            <Trash2 />
          </Button>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-2">
        <NumberField
          label="このキーフレームの t（秒）"
          value={current.t}
          step={0.1}
          min={0}
          max={scene.duration}
          disabled={disabled || cameraIndex === 0}
          onChange={(t) => patchKeyframe({ t })}
        />
        <NumberField
          label="カメラの高さ（m）"
          value={round2(current.position[1])}
          step={0.05}
          disabled={disabled}
          onChange={(y) =>
            patchKeyframe({
              position: [current.position[0], y, current.position[2]],
            })
          }
        />
        <NumberField
          label="注視点の高さ（m）"
          value={round2(current.look_at[1])}
          step={0.05}
          disabled={disabled}
          onChange={(y) =>
            patchKeyframe({ look_at: [current.look_at[0], y, current.look_at[2]] })
          }
        />
        <NumberField
          label="画角 fov（度・対角）"
          value={round2(current.fov_deg)}
          step={1}
          min={20}
          max={110}
          disabled={disabled}
          onChange={(fov_deg) => patchKeyframe({ fov_deg })}
        />
        <NumberField
          label="傾き roll（度）"
          value={round2(current.roll_deg)}
          step={1}
          disabled={disabled}
          onChange={(roll_deg) => patchKeyframe({ roll_deg })}
        />
        <div>
          <Label className="mb-1 text-[11px] text-muted-foreground">補間</Label>
          <NativeSelect
            className="h-7 text-xs"
            aria-label="補間"
            value={scene.camera.easing}
            disabled={disabled}
            onChange={(event) =>
              patchCamera({ easing: event.target.value as BlockingEasing })
            }
          >
            {(Object.keys(EASING_LABELS) as BlockingEasing[]).map((value) => (
              <option key={value} value={value}>
                {EASING_LABELS[value]}
              </option>
            ))}
          </NativeSelect>
        </div>
      </div>

      <div className="rounded-md border border-border p-2">
        <Label className="mb-1 text-[11px] text-muted-foreground">
          カメラの動き（プリセット）
        </Label>
        <NativeSelect
          className="h-7 text-xs"
          aria-label="カメラの動き"
          value={move?.type ?? ''}
          disabled={disabled}
          onChange={(event) => {
            const type = event.target.value
            if (!type) {
              patchCamera({ move: null })
              return
            }
            patchCamera({
              move: {
                type: type as BlockingMoveType,
                amount: move?.amount ?? 1,
                target: move?.target ?? null,
              },
            })
          }}
        >
          <option value="">使わない（キーフレームで動かす）</option>
          {(Object.keys(MOVE_LABELS) as BlockingMoveType[]).map((value) => (
            <option key={value} value={value}>
              {MOVE_LABELS[value]}
            </option>
          ))}
        </NativeSelect>
        {move && (
          <div className="mt-2 grid grid-cols-2 gap-2">
            {move.type !== 'static' && (
              <NumberField
                label={
                  MOVES_IN_METERS.includes(move.type) ? '移動量（m）' : '移動量（度）'
                }
                value={round2(move.amount)}
                step={MOVES_IN_METERS.includes(move.type) ? 0.1 : 5}
                disabled={disabled}
                onChange={(amount) => patchCamera({ move: { ...move, amount } })}
              />
            )}
            {(MOVES_NEEDING_TARGET.includes(move.type) ||
              move.type.startsWith('arc_')) && (
              <div>
                <Label className="mb-1 text-[11px] text-muted-foreground">
                  注視の対象
                </Label>
                <NativeSelect
                  className="h-7 text-xs"
                  aria-label="注視の対象"
                  value={move.target ?? ''}
                  disabled={disabled}
                  onChange={(event) =>
                    patchCamera({
                      move: { ...move, target: event.target.value || null },
                    })
                  }
                >
                  <option value="">指定しない</option>
                  {scene.objects.map((object) => (
                    <option key={object.id} value={object.id}>
                      {labelOf(object)}
                    </option>
                  ))}
                </NativeSelect>
              </div>
            )}
          </div>
        )}
        <p className="mt-1 text-[11px] text-muted-foreground">
          プリセットを選ぶと、焼くときに 1 つ目のキーフレームを起点に展開されます
          （手で置いた 2 つ目以降は使われません）。
        </p>
      </div>

      <div className="grid grid-cols-2 gap-2">
        <div>
          <Label className="mb-1 text-[11px] text-muted-foreground">アスペクト比</Label>
          <NativeSelect
            className="h-7 text-xs"
            aria-label="アスペクト比"
            value={scene.aspect_ratio}
            disabled={disabled}
            onChange={(event) =>
              onScene({
                ...scene,
                aspect_ratio: event.target.value as BlockingAspectRatio,
              })
            }
          >
            {aspectRatios.map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </NativeSelect>
        </div>
        <NumberField
          label={`尺（秒。${capabilities.min_duration}〜${capabilities.max_duration}）`}
          value={scene.duration}
          step={0.5}
          min={capabilities.min_duration}
          max={capabilities.max_duration}
          disabled={disabled}
          // 打っている途中の値で縮めるとキーフレームが落ちるので、確定してから。
          commitOnBlur
          onChange={setDuration}
        />
      </div>
    </div>
  )
}
