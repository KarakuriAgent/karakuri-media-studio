import { useCallback, useEffect, useRef, useState } from 'react'

import type { BlockingScene } from '../../types'
import {
  cameraAt,
  horizontalFov,
  labelOf,
  objectAt,
  round2,
  sizeOf,
} from './blocking'

/** SVG の内部座標（viewBox）の 1 辺。実寸は CSS 側に任せる。 */
const VIEW = 480

/** ドラッグで掴めるもの（オブジェクトは id、カメラは 2 点）。 */
export type PlanTarget =
  | { kind: 'object'; id: string }
  | { kind: 'camera' }
  | { kind: 'look_at' }

/** 視錐台の扇形を近似する分割数。 */
const FRUSTUM_STEPS = 12

/**
 * 上から見た平面図（SVG）。
 *
 * 床グリッド（1m）・原点・オブジェクトの経路・カメラと注視点・視錐台（水平画角の
 * 扇形）を描き、図形をドラッグすると `onMove` で新しい `x` / `z`（m）を返す。
 * どのキーフレームを動かすかは呼び出し側が決める（このコンポーネントは
 * 「掴んだものと行き先」だけを知らせる）。
 *
 * 画面の右が +X、下が +Z、中心が原点。カメラは既定で +Z 側（画面の下）にいる。
 */
export default function BlockingPlanView({
  scene,
  t,
  zoom,
  selected,
  selectedKeyframe,
  onSelect,
  onSelectKeyframe,
  onMove,
}: {
  scene: BlockingScene
  /** いま描く時刻（秒） */
  t: number
  /** 1m あたりの px（viewBox の中での値） */
  zoom: number
  selected: PlanTarget | null
  /** 選択中のオブジェクトのキーフレーム番号（点を強調するためだけに使う） */
  selectedKeyframe: number
  onSelect: (target: PlanTarget) => void
  onSelectKeyframe: (id: string, index: number) => void
  onMove: (target: PlanTarget, x: number, z: number) => void
}) {
  const svg = useRef<SVGSVGElement>(null)
  const [dragging, setDragging] = useState<PlanTarget | null>(null)

  const toMeters = useCallback(
    (clientX: number, clientY: number): [number, number] => {
      const rect = svg.current?.getBoundingClientRect()
      const width = rect?.width || VIEW
      const height = rect?.height || VIEW
      const vx = ((clientX - (rect?.left ?? 0)) / width) * VIEW
      const vy = ((clientY - (rect?.top ?? 0)) / height) * VIEW
      return [round2((vx - VIEW / 2) / zoom), round2((vy - VIEW / 2) / zoom)]
    },
    [zoom],
  )

  // 掴んでいるあいだは SVG の外へ出ても追いかける（離したら終わり）。
  useEffect(() => {
    if (!dragging) return
    const move = (event: PointerEvent) => {
      const [x, z] = toMeters(event.clientX, event.clientY)
      onMove(dragging, x, z)
    }
    const stop = () => setDragging(null)
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', stop)
    window.addEventListener('pointercancel', stop)
    return () => {
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', stop)
      window.removeEventListener('pointercancel', stop)
    }
  }, [dragging, onMove, toMeters])

  const grab = (target: PlanTarget) => (event: React.PointerEvent) => {
    event.preventDefault()
    onSelect(target)
    setDragging(target)
  }

  const sx = (x: number) => VIEW / 2 + x * zoom
  const sy = (z: number) => VIEW / 2 + z * zoom
  /** 画面に入っている範囲（m。グリッドを引く広さ） */
  const extent = Math.ceil(VIEW / 2 / zoom)

  const camera = cameraAt(scene.camera.keyframes, t)
  const cameraX = sx(camera.position[0])
  const cameraY = sy(camera.position[2])
  const lookX = sx(camera.look_at[0])
  const lookY = sy(camera.look_at[2])

  // 視錐台（上から見た広がり）。奥行きは注視点までの距離の 1.6 倍まで描く。
  const facing = Math.atan2(lookY - cameraY, lookX - cameraX)
  const half = (horizontalFov(camera.fov_deg, scene.aspect_ratio) * Math.PI) / 360
  const reach = Math.max(3 * zoom, Math.hypot(lookX - cameraX, lookY - cameraY) * 1.6)
  const frustum = [`${cameraX},${cameraY}`]
  for (let step = 0; step <= FRUSTUM_STEPS; step += 1) {
    const angle = facing - half + (2 * half * step) / FRUSTUM_STEPS
    frustum.push(
      `${cameraX + Math.cos(angle) * reach},${cameraY + Math.sin(angle) * reach}`,
    )
  }

  const isSelected = (target: PlanTarget) =>
    selected?.kind === target.kind &&
    (target.kind !== 'object' ||
      (selected.kind === 'object' && selected.id === target.id))

  return (
    <svg
      ref={svg}
      viewBox={`0 0 ${VIEW} ${VIEW}`}
      role="img"
      aria-label="上から見た平面図"
      className="aspect-square w-full touch-none select-none rounded-md border border-border bg-[#f4f4f4]"
    >
      {/* 床グリッド（1m 間隔）と、原点を通る軸 */}
      <g stroke="#d0d0d0" strokeWidth={1}>
        {Array.from({ length: extent * 2 + 1 }, (_, index) => index - extent).map(
          (meter) => (
            <g key={meter}>
              <line x1={sx(meter)} y1={0} x2={sx(meter)} y2={VIEW} />
              <line x1={0} y1={sy(meter)} x2={VIEW} y2={sy(meter)} />
            </g>
          ),
        )}
      </g>
      <g stroke="#a8a8a8" strokeWidth={1.5}>
        <line x1={sx(0)} y1={0} x2={sx(0)} y2={VIEW} />
        <line x1={0} y1={sy(0)} x2={VIEW} y2={sy(0)} />
      </g>
      <circle cx={sx(0)} cy={sy(0)} r={3} fill="#7a7a7a" />
      <text x={sx(0) + 6} y={sy(0) - 6} fontSize={11} fill="#6a6a6a">
        原点 (0, 0)
      </text>
      <text x={8} y={VIEW - 10} fontSize={11} fill="#6a6a6a">
        1 マス = 1m ／ 右が +X・下が +Z
      </text>

      {/* 視錐台（カメラの見ている広がり） */}
      <polygon
        points={frustum.join(' ')}
        fill="#3b82f6"
        fillOpacity={0.12}
        stroke="#3b82f6"
        strokeOpacity={0.35}
      />

      {scene.objects.map((object) => {
        const [width, , depth] = sizeOf(object)
        const now = objectAt(object, t)
        const cx = sx(now.position[0])
        const cy = sy(now.position[2])
        // 0 度で +Z（画面の下）。SVG は y が下向きなので回す向きは逆。
        const rotate = `rotate(${-now.yaw_deg} ${cx} ${cy})`
        const nose = [
          cx + Math.sin((now.yaw_deg * Math.PI) / 180) * (depth / 2 + 0.25) * zoom,
          cy + Math.cos((now.yaw_deg * Math.PI) / 180) * (depth / 2 + 0.25) * zoom,
        ]
        const target: PlanTarget = { kind: 'object', id: object.id }
        const active = isSelected(target)
        return (
          <g key={object.id}>
            {/* 経路（キーフレームを結ぶ線と点） */}
            {object.keyframes.length > 1 && (
              <polyline
                points={object.keyframes
                  .map((keyframe) => `${sx(keyframe.position[0])},${sy(keyframe.position[2])}`)
                  .join(' ')}
                fill="none"
                stroke={object.color}
                strokeOpacity={0.7}
                strokeDasharray="4 3"
                strokeWidth={1.5}
              />
            )}
            {object.keyframes.map((keyframe, index) => (
              <circle
                key={index}
                cx={sx(keyframe.position[0])}
                cy={sy(keyframe.position[2])}
                r={active && index === selectedKeyframe ? 5 : 3.5}
                fill={active && index === selectedKeyframe ? object.color : '#ffffff'}
                stroke={object.color}
                strokeWidth={1.5}
                className="cursor-pointer"
                role="button"
                aria-label={`${labelOf(object)} の ${keyframe.t}秒 のキーフレーム`}
                onPointerDown={(event) => {
                  event.preventDefault()
                  onSelectKeyframe(object.id, index)
                  setDragging(target)
                }}
              />
            ))}

            {/* いまの姿（四角はそのまま、丸いものは円） */}
            <g
              role="button"
              aria-label={`${labelOf(object)} を動かす`}
              className="cursor-move"
              onPointerDown={grab(target)}
            >
              {object.shape === 'box' ? (
                <rect
                  x={cx - (width / 2) * zoom}
                  y={cy - (depth / 2) * zoom}
                  width={width * zoom}
                  height={depth * zoom}
                  transform={rotate}
                  fill={object.color}
                  fillOpacity={0.85}
                  stroke={active ? '#1d4ed8' : '#4a4a4a'}
                  strokeWidth={active ? 2.5 : 1}
                />
              ) : (
                <circle
                  cx={cx}
                  cy={cy}
                  r={(width / 2) * zoom}
                  fill={object.color}
                  fillOpacity={0.85}
                  stroke={active ? '#1d4ed8' : '#4a4a4a'}
                  strokeWidth={active ? 2.5 : 1}
                />
              )}
              {/* 正面（鼻）。向きが読めるように短い線を出す */}
              <line
                x1={cx}
                y1={cy}
                x2={nose[0]}
                y2={nose[1]}
                stroke="#333333"
                strokeWidth={2}
              />
              <text x={cx + 8} y={cy - 8} fontSize={11} fill="#333333">
                {labelOf(object)}
              </text>
            </g>
          </g>
        )
      })}

      {/* カメラの経路 */}
      {scene.camera.keyframes.length > 1 && (
        <polyline
          points={scene.camera.keyframes
            .map((keyframe) => `${sx(keyframe.position[0])},${sy(keyframe.position[2])}`)
            .join(' ')}
          fill="none"
          stroke="#1d4ed8"
          strokeDasharray="5 3"
          strokeWidth={1.5}
        />
      )}
      <line
        x1={cameraX}
        y1={cameraY}
        x2={lookX}
        y2={lookY}
        stroke="#1d4ed8"
        strokeWidth={1.5}
      />
      <g
        role="button"
        aria-label="カメラを動かす"
        className="cursor-move"
        onPointerDown={grab({ kind: 'camera' })}
      >
        <circle
          cx={cameraX}
          cy={cameraY}
          r={9}
          fill="#1d4ed8"
          fillOpacity={0.9}
          stroke={isSelected({ kind: 'camera' }) ? '#0f172a' : '#ffffff'}
          strokeWidth={2}
        />
        <text x={cameraX + 12} y={cameraY + 4} fontSize={11} fill="#1d4ed8">
          カメラ
        </text>
      </g>
      <g
        role="button"
        aria-label="注視点を動かす"
        className="cursor-move"
        onPointerDown={grab({ kind: 'look_at' })}
      >
        <circle
          cx={lookX}
          cy={lookY}
          r={7}
          fill="#ffffff"
          stroke="#1d4ed8"
          strokeWidth={isSelected({ kind: 'look_at' }) ? 3 : 2}
        />
        <line x1={lookX - 10} y1={lookY} x2={lookX + 10} y2={lookY} stroke="#1d4ed8" />
        <line x1={lookX} y1={lookY - 10} x2={lookX} y2={lookY + 10} stroke="#1d4ed8" />
      </g>
    </svg>
  )
}
