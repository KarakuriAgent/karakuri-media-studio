// crtOff: CRT の電源断。横一線に潰れて白点になって消える。
// 黒画面(screen / endCard)の上に重ねて使う。

import React from 'react';
import { interpolate, useCurrentFrame, useVideoConfig } from 'remotion';
import { useFxCtx } from '../lib/fx';
import type { FxEventOf } from '../schema';

export const FxCrtOff: React.FC<{ ev: FxEventOf<'crtOff'> }> = ({ ev }) => {
  const frame = useCurrentFrame();
  const { durationInFrames } = useVideoConfig();
  const ctx = useFxCtx();

  const total = Math.max(2, Math.min(ev.frames, durationInFrames));
  const q = frame / (total - 1);
  const h = interpolate(q, [0, 0.45], [ctx.height * 0.047, ctx.height * 0.004], {
    extrapolateRight: 'clamp',
  });
  const w = interpolate(q, [0.45, 0.88], [ctx.width, ctx.width * 0.008], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });
  const opacity = interpolate(q, [0.86, 1], [1, 0], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });
  const glow = ctx.fs(18) + ctx.fs(40) * (1 - q);

  return (
    <div style={{ position: 'absolute', inset: 0, background: '#000000' }}>
      <div
        style={{
          position: 'absolute',
          left: (ctx.width - w) / 2,
          top: (ctx.height - h) / 2,
          width: w,
          height: h,
          background: ctx.color(ev.color),
          opacity,
          filter: `blur(${Math.max(1, ctx.fs(1.2))}px)`,
          boxShadow: `0 0 ${glow}px rgba(220,240,255,${0.75 * opacity})`,
        }}
      />
    </div>
  );
};
