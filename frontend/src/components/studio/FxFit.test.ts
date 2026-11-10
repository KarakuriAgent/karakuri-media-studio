import { describe, expect, it } from 'vitest'

import { containSize, coverBox, fitSlamStart } from '@fx/lib/fit'

// FxOverlay の画面に合わせる計算（remotion/src/lib/fit.ts）。
// sprite / video の cover（集中線を画面の四隅まで覆う）と、imageSlam の fitStart
// （叩き込みはじめの倍率を画面に収める）。

const W = 1080
const H = 1920

/** 箱（中心・大きさ・回転）が画面の 4 つの隅を含むか。 */
function coversCorners(
  box: { cx: number; cy: number; width: number; height: number },
  rot: number,
): boolean {
  const a = (rot * Math.PI) / 180
  // 画面の点を箱の向きへ戻す（描画は CSS の rotate(-rot)）
  return [
    [0, 0],
    [W, 0],
    [0, H],
    [W, H],
  ].every(([x, y]) => {
    const dx = x - box.cx
    const dy = y - box.cy
    const u = dx * Math.cos(a) - dy * Math.sin(a)
    const v = dx * Math.sin(a) + dy * Math.cos(a)
    return Math.abs(u) <= box.width / 2 + 1e-6 && Math.abs(v) <= box.height / 2 + 1e-6
  })
}

describe('coverBox', () => {
  it('中心を合わせると、前の道具の式と同じ大きさになる', () => {
    // 正方形の集中線を、顔（上寄り）に中心を合わせて置く
    const box = coverBox({
      screenW: W,
      screenH: H,
      x: 0.5 * W,
      y: 0.3 * H,
      aspect: 1,
      origin: [0.5, 0.5],
      rot: 0,
      margin: 1.06,
    })
    // 箱の高さ = max(2·max(cy,1−cy)·H, 2·max(cx,1−cx)·W / 縦横比) × margin
    const expected = Math.max(2 * 0.7 * H, 2 * 0.5 * W) * 1.06
    expect(box.height).toBeCloseTo(expected, 6)
    expect(box.width).toBeCloseTo(expected, 6)
    expect(box.cx).toBeCloseTo(0.5 * W, 6)
    expect(box.cy).toBeCloseTo(0.3 * H, 6)
    expect(coversCorners(box, 0)).toBe(true)
  })

  it('横長の素材でも、幅の側から足りる大きさにする', () => {
    const box = coverBox({
      screenW: W,
      screenH: H,
      x: 0.2 * W,
      y: 0.5 * H,
      aspect: 0.4, // 縦長の素材（幅が足りなくなりやすい）
      origin: [0.5, 0.5],
      rot: 0,
      margin: 1,
    })
    expect(box.height).toBeCloseTo((2 * 0.8 * W) / 0.4, 6)
    expect(coversCorners(box, 0)).toBe(true)
  })

  it('素材の中の点を合わせても、その点が画面の指定の位置に来て四隅を覆う', () => {
    const box = coverBox({
      screenW: W,
      screenH: H,
      x: 0.6 * W,
      y: 0.25 * H,
      aspect: 16 / 9,
      origin: [0.3, 0.7],
      rot: 0,
      margin: 1,
    })
    // 箱の左上から origin の点までのずれを戻すと、指定の位置になる
    expect(box.cx - box.width / 2 + 0.3 * box.width).toBeCloseTo(0.6 * W, 6)
    expect(box.cy - box.height / 2 + 0.7 * box.height).toBeCloseTo(0.25 * H, 6)
    expect(coversCorners(box, 0)).toBe(true)
  })

  it('回しても四隅を覆い、origin の点は動かない', () => {
    for (const rot of [15, -30, 90]) {
      const origin: [number, number] = [0.4, 0.55]
      const box = coverBox({
        screenW: W,
        screenH: H,
        x: 0.45 * W,
        y: 0.35 * H,
        aspect: 1.5,
        origin,
        rot,
        margin: 1,
      })
      expect(coversCorners(box, rot)).toBe(true)
      // origin の点（箱の中の局所の座標）を画面へ写すと指定の位置
      const lx = (origin[0] - 0.5) * box.width
      const ly = (origin[1] - 0.5) * box.height
      const a = (-rot * Math.PI) / 180
      const sx = box.cx + lx * Math.cos(a) - ly * Math.sin(a)
      const sy = box.cy + lx * Math.sin(a) + ly * Math.cos(a)
      expect(sx).toBeCloseTo(0.45 * W, 6)
      expect(sy).toBeCloseTo(0.35 * H, 6)
    }
  })
})

describe('containSize', () => {
  it('箱に素材の比のまま収める（比が分からなければ箱のまま）', () => {
    expect(containSize(800, 400, 4)).toEqual({ width: 800, height: 200 })
    expect(containSize(800, 400, 1)).toEqual({ width: 400, height: 400 })
    expect(containSize(800, 400, null)).toEqual({ width: 800, height: 400 })
  })
})

describe('fitSlamStart', () => {
  const base = {
    screenW: W,
    screenH: H,
    cx: 0.5 * W,
    cy: 0.76 * H,
    rot: 0,
    min: 1.12,
  }

  it('幅の広い文字は、画面の左右に収まる倍率まで抑える', () => {
    const drawnW = 0.8 * W
    const start = fitSlamStart({ ...base, drawnW, drawnH: 100, snap: [1.6, 1] })
    // 前の道具の式 0.98 / w と同じ（中央に置いたとき）
    expect(start).toBeCloseTo(0.98 / 0.8, 6)
    expect(start * drawnW).toBeLessThanOrEqual(0.98 * W + 1e-6)
  })

  it('勢いのため最低の倍率は残す', () => {
    const start = fitSlamStart({ ...base, drawnW: 0.95 * W, drawnH: 100, snap: [1.6, 1] })
    expect(start).toBeCloseTo(1.12, 6)
  })

  it('収まっていれば、書いた倍率のまま', () => {
    const start = fitSlamStart({ ...base, drawnW: 0.4 * W, drawnH: 100, snap: [1.6, 1] })
    expect(start).toBe(1.6)
  })

  it('最低の倍率より小さい倍率はそのまま、着地より小さくはしない', () => {
    expect(fitSlamStart({ ...base, drawnW: 0.99 * W, drawnH: 100, snap: [1.05, 1] })).toBe(1.05)
    expect(fitSlamStart({ ...base, drawnW: 0.99 * W, drawnH: 100, snap: [0.8, 1] })).toBe(0.8)
    expect(
      fitSlamStart({ ...base, drawnW: 1.2 * W, drawnH: 100, snap: [1.6, 1], min: 1 }),
    ).toBe(1)
  })

  it('端に寄せた絵と回した絵は、外接の箱で測る', () => {
    const left = fitSlamStart({
      ...base,
      cx: 0.3 * W,
      drawnW: 0.5 * W,
      drawnH: 100,
      snap: [1.6, 1],
    })
    // 左の端までは 0.29·W、半分の幅は 0.25·W
    expect(left).toBeCloseTo(0.29 / 0.25, 6)
    const flat = fitSlamStart({ ...base, drawnW: 0.7 * W, drawnH: 200, snap: [1.6, 1] })
    const tilted = fitSlamStart({ ...base, drawnW: 0.7 * W, drawnH: 200, rot: 8, snap: [1.6, 1] })
    expect(tilted).toBeLessThan(flat)
  })
})
