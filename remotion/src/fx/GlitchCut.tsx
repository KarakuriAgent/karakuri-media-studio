// glitchCut: 走査線ずれ + ブロックノイズを数フレーム。カットの継ぎ目に差す。
//
// 走査線ずれと RGB 分離は base に掛けるので glitchCutState() が値を返し、
// 上に重ねるブロックノイズだけがコンポーネント(GlitchBlocks)になっている。

import React from 'react';
import { useCurrentFrame } from 'remotion';
import { useFxCtx } from '../lib/fx';
import { rng } from '../lib/rng';
import type { FxEventOf } from '../schema';

export type GlitchState = {
  /** 走査線ずれ [変位 px, seed] */
  glitch: [number, number] | null;
  /** RGB 分離の量(px) */
  chroma: number;
  /** 荒れの強さ(0..1)。頭がいちばん荒れて末尾で収まる。 */
  k: number;
};

export const glitchCutState = (
  ev: FxEventOf<'glitchCut'>,
  /** イベント開始からのフレーム数 */
  fi: number,
  width: number,
  seed: number,
): GlitchState => {
  const k = Math.max(0, 1 - fi / Math.max(1, ev.frames));
  return {
    glitch: [Math.round(ev.displace * width * (0.45 + 0.55 * k)), ((seed + fi) % 97) + 1],
    chroma: Math.round(ev.chroma * width * (0.4 + 0.6 * k)),
    k,
  };
};

/** 画面に散らすブロックノイズ。glitchCut / screen(glitch) / beatMarker から使う。 */
export const GlitchBlocks: React.FC<{
  seed: number;
  count: number;
  alpha?: number;
  colors?: readonly string[];
}> = ({ seed, count, alpha = 1, colors }) => {
  const ctx = useFxCtx();
  const { width, height } = ctx;
  const palette = colors ?? [ctx.color('accent'), ctx.color('fg'), '#5ad7ff', ctx.color('bg')];
  const r = rng(seed * 2654435761);
  const blocks = Array.from({ length: count }, () => ({
    x: r.range(-0.13, 0.97) * width,
    y: r.range(0, 0.99) * height,
    w: r.range(0.07, 0.48) * width,
    h: Math.max(2, r.range(0.004, 0.036) * height),
    c: r.choice(palette),
    o: 0.22 + r.next() * 0.45,
  }));
  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      style={{ position: 'absolute', inset: 0, mixBlendMode: 'screen', opacity: alpha }}
    >
      {blocks.map((b, i) => (
        <rect key={i} x={b.x} y={b.y} width={b.w} height={b.h} fill={b.c} opacity={b.o} />
      ))}
    </svg>
  );
};

/** glitchCut イベントの「上に重ねるぶん」。 */
export const FxGlitchCut: React.FC<{ ev: FxEventOf<'glitchCut'>; seed: number }> = ({
  ev,
  seed,
}) => {
  const frame = useCurrentFrame();
  const ctx = useFxCtx();
  if (ev.blocks <= 0) {
    return null;
  }
  const { k } = glitchCutState(ev, frame, ctx.width, seed);
  return <GlitchBlocks seed={seed + frame} count={ev.blocks} alpha={0.45 + 0.45 * k} />;
};
