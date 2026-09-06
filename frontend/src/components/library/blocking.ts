/**
 * 構図リファレンス動画（ブロッキング、SPEC §7.2）を組むための小道具。
 *
 * 表示と編集のための**純関数と定数**だけを置く（API 呼び出しと状態は
 * :file:`BlockingBuilderModal.tsx`）。上限や既定値の正本は
 * `backend/app/blocking.py` なので、ここに置くのは
 * `GET /api/studio/capabilities` が取れなかったときの控えとして使う写し。
 */
import type {
  BlockingAspectRatio,
  BlockingCameraKeyframe,
  BlockingCapabilities,
  BlockingEasing,
  BlockingFacing,
  BlockingMoveType,
  BlockingObject,
  BlockingObjectKeyframe,
  BlockingScene,
  BlockingShape,
  BlockingVec3,
} from '../../types'

/** 形状の表示名（プルダウンの並び順もこの通り）。 */
export const SHAPE_LABELS: Record<BlockingShape, string> = {
  figure: '人型',
  box: '四角',
  sphere: '丸',
  cylinder: '円柱',
  capsule: 'カプセル',
}

/** 向きの決め方。 */
export const FACING_LABELS: Record<BlockingFacing, string> = {
  keyframe: 'キーフレームの向き',
  camera: '常にカメラを見る',
  path: '進行方向を向く',
}

/** キーフレーム間の補間。 */
export const EASING_LABELS: Record<BlockingEasing, string> = {
  ease_in_out: 'なめらか（ease_in_out）',
  linear: '等速（linear）',
}

/** カメラプリセット。 */
export const MOVE_LABELS: Record<BlockingMoveType, string> = {
  static: '動かさない',
  push_in: '寄る（push in）',
  pull_out: '引く（pull out）',
  pan_left: '左へパン',
  pan_right: '右へパン',
  tilt_up: '上へティルト',
  tilt_down: '下へティルト',
  truck_left: '左へ平行移動',
  truck_right: '右へ平行移動',
  arc_left: '左へ回り込む',
  arc_right: '右へ回り込む',
  follow: '対象を追う（follow）',
}

/** 移動量の単位が m のプリセット（残りは度。`static` は量を使わない）。 */
export const MOVES_IN_METERS: BlockingMoveType[] = [
  'push_in',
  'pull_out',
  'truck_left',
  'truck_right',
]

/** `target`（注視対象の id）が要るプリセット。 */
export const MOVES_NEEDING_TARGET: BlockingMoveType[] = ['follow']

/** アスペクト比の並び順（capabilities が取れなかったときの控えでもある）。 */
export const ASPECT_RATIOS: BlockingAspectRatio[] = [
  '16:9',
  '9:16',
  '1:1',
  '4:3',
  '3:4',
  '21:9',
]

/** 形状ごとの既定の大きさ（m。`[w, h, d]`）。`app.blocking.DEFAULT_SIZES` の写し。 */
export const DEFAULT_SIZES: Record<BlockingShape, BlockingVec3> = {
  box: [0.6, 0.6, 0.6],
  sphere: [0.5, 0.5, 0.5],
  cylinder: [0.5, 1.0, 0.5],
  capsule: [0.5, 1.7, 0.5],
  figure: [0.45, 1.7, 0.3],
}

/**
 * 人物の識別に使う色（低彩度のプリセット）。
 *
 * 彩度を上げると H3 が「色そのもの」を拾ってしまうので、区別が付く範囲で
 * くすんだ色だけを並べる（背景の `#e6e6e6` と床の上でも見分けられる明度）。
 */
export const OBJECT_COLORS: { value: string; name: string }[] = [
  { value: '#9a9a9a', name: 'グレー' },
  { value: '#6f6f6f', name: 'チャコール' },
  { value: '#8f9bab', name: 'ブルーグレー' },
  { value: '#8fa08a', name: 'モスグリーン' },
  { value: '#b09189', name: 'テラコッタ' },
  { value: '#b3a98f', name: 'サンド' },
  { value: '#a294a8', name: 'モーヴ' },
  { value: '#7f8794', name: 'スレート' },
]

/** capabilities が取れなかったときに使う上限（`app.blocking` の定数の写し）。 */
export const FALLBACK_CAPABILITIES: BlockingCapabilities = {
  available: true,
  error: '',
  fps: 24,
  long_edge: 768,
  min_duration: 0.5,
  max_duration: 10,
  max_objects: 20,
  max_keyframes: 32,
  aspect_ratios: [...ASPECT_RATIOS],
  shapes: Object.keys(SHAPE_LABELS) as BlockingShape[],
  facings: Object.keys(FACING_LABELS) as BlockingFacing[],
  camera_moves: Object.keys(MOVE_LABELS) as BlockingMoveType[],
}

/**
 * 最初に出すシーン（16:9 / 4 秒 / 正面 6m のカメラ / 原点に人型 1 体）。
 *
 * カメラの高さ 1.6m・注視点 1.0m・画角 47 度は H3 のガイドが使う既定と同じ。
 */
export function initialBlockingScene(): BlockingScene {
  return {
    aspect_ratio: '16:9',
    duration: 4,
    background: { floor_grid: true, color: '#e6e6e6' },
    camera: {
      keyframes: [
        {
          t: 0,
          position: [0, 1.6, 6],
          look_at: [0, 1.0, 0],
          fov_deg: 47,
          roll_deg: 0,
        },
      ],
      easing: 'ease_in_out',
      move: null,
    },
    objects: [
      {
        id: 'person_1',
        label: 'person 1',
        shape: 'figure',
        size: null,
        color: OBJECT_COLORS[0].value,
        keyframes: [{ t: 0, position: [0, 0, 0], yaw_deg: 0 }],
        facing: 'keyframe',
      },
    ],
  }
}

/** 形状ごとの通し番号で、まだ使っていない id を作る（英数字と `_` だけ）。 */
export function nextObjectId(scene: BlockingScene, shape: BlockingShape): string {
  const prefix = shape === 'figure' ? 'person' : shape
  for (let index = 1; index <= 99; index += 1) {
    const id = `${prefix}_${index}`
    if (!scene.objects.some((object) => object.id === id)) return id
  }
  return `${prefix}_${Date.now()}`
}

/** 追加するオブジェクト 1 件（色は使っていないプリセットから順に配る）。 */
export function newBlockingObject(
  scene: BlockingScene,
  shape: BlockingShape,
): BlockingObject {
  const id = nextObjectId(scene, shape)
  const used = new Set(scene.objects.map((object) => object.color))
  const color =
    OBJECT_COLORS.find((entry) => !used.has(entry.value))?.value ??
    OBJECT_COLORS[0].value
  // 既にいる人と重ならないよう、右へ 1m ずつずらして置く。
  const x = scene.objects.length
  return {
    id,
    label: id.replace('_', ' '),
    shape,
    size: null,
    color,
    keyframes: [{ t: 0, position: [x, 0, 0], yaw_deg: 0 }],
    facing: 'keyframe',
  }
}

/** 大きさ（未指定なら形状ごとの既定）。 */
export function sizeOf(object: BlockingObject): BlockingVec3 {
  return object.size ?? DEFAULT_SIZES[object.shape]
}

/** 表示名（空なら id）。 */
export function labelOf(object: BlockingObject): string {
  return object.label.trim() || object.id
}

export function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value))
}

/** 平面図の座標を扱いやすい桁に丸める（cm 単位）。 */
export function round2(value: number): number {
  return Math.round(value * 100) / 100
}

/**
 * `t` に一番近いキーフレームの番号（同点なら手前）。
 *
 * 平面図のドラッグはこれで拾ったキーフレームを動かす（選択中のキーフレームが
 * あればそちらを優先するのは呼び出し側の仕事）。
 */
export function nearestKeyframe(
  keyframes: { t: number }[],
  t: number,
): number {
  let best = 0
  let distance = Number.POSITIVE_INFINITY
  keyframes.forEach((keyframe, index) => {
    const gap = Math.abs(keyframe.t - t)
    if (gap < distance) {
      best = index
      distance = gap
    }
  })
  return best
}

/**
 * `t` 秒での値を線形に補間する（平面図の下描き用）。
 *
 * 焼き上がりの補間は `easing` に従うが、平面図は「どこを通るか」が読めれば
 * よいので直線で足りる（範囲の外は端の値のまま）。
 */
function interpolate<T extends { t: number }>(
  keyframes: T[],
  t: number,
): { before: T; after: T; ratio: number } {
  if (keyframes.length === 1 || t <= keyframes[0].t) {
    return { before: keyframes[0], after: keyframes[0], ratio: 0 }
  }
  const last = keyframes[keyframes.length - 1]
  if (t >= last.t) return { before: last, after: last, ratio: 0 }
  let index = 0
  while (index < keyframes.length - 2 && keyframes[index + 1].t < t) index += 1
  const before = keyframes[index]
  const after = keyframes[index + 1]
  const span = after.t - before.t
  return { before, after, ratio: span > 1e-6 ? (t - before.t) / span : 0 }
}

function mix(a: number, b: number, ratio: number): number {
  return a + (b - a) * ratio
}

function mixVec(a: BlockingVec3, b: BlockingVec3, ratio: number): BlockingVec3 {
  return [
    mix(a[0], b[0], ratio),
    mix(a[1], b[1], ratio),
    mix(a[2], b[2], ratio),
  ]
}

/** `t` 秒でのオブジェクトの底面中心と向き（度）。 */
export function objectAt(
  object: BlockingObject,
  t: number,
): { position: BlockingVec3; yaw_deg: number } {
  const { before, after, ratio } = interpolate<BlockingObjectKeyframe>(
    object.keyframes,
    t,
  )
  return {
    position: mixVec(before.position, after.position, ratio),
    yaw_deg: mix(before.yaw_deg, after.yaw_deg, ratio),
  }
}

/** `t` 秒でのカメラ（位置・注視点・画角）。 */
export function cameraAt(
  keyframes: BlockingCameraKeyframe[],
  t: number,
): { position: BlockingVec3; look_at: BlockingVec3; fov_deg: number } {
  const { before, after, ratio } = interpolate(keyframes, t)
  return {
    position: mixVec(before.position, after.position, ratio),
    look_at: mixVec(before.look_at, after.look_at, ratio),
    fov_deg: mix(before.fov_deg, after.fov_deg, ratio),
  }
}

/**
 * 対角画角から**水平**画角（度）を出す。
 *
 * 平面図に描く視錐台は上から見た広がりなので、対角のままでは広すぎる。
 * `tan(diag/2) : tan(h/2) = 対角 : 横` で換算する。
 */
export function horizontalFov(
  fovDeg: number,
  aspectRatio: BlockingAspectRatio,
): number {
  const [wide, tall] = aspectRatio.split(':').map(Number)
  const diagonal = Math.hypot(wide, tall)
  const half = Math.atan(
    Math.tan((fovDeg * Math.PI) / 360) * (wide / diagonal),
  )
  return (half * 360) / Math.PI
}
