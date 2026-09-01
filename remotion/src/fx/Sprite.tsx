// sprite: 透過画像を 1 枚貼る。ロゴ押印・小物・キャラの立ち絵。
// 位置は anchor(隅・辺)か cx / cy(画面比)。動きは motion で選ぶ。

import React from 'react';
import { interpolate, useCurrentFrame, useVideoConfig } from 'remotion';
import { FxSprite } from '../components/FxSprite';
import { anchorCenter, motionTransform, useFxCtx } from '../lib/fx';
import { rng } from '../lib/rng';
import type { FxEventOf } from '../schema';

export const FxSpriteEvent: React.FC<{ ev: FxEventOf<'sprite'>; seed: number }> = ({ ev, seed }) => {
  const frame = useCurrentFrame();
  const { fps, durationInFrames } = useVideoConfig();
  const ctx = useFxCtx();

  const anchored = anchorCenter(ev.anchor, ev.w, ev.maxH, ev.margin);
  const cx = (ev.cx ?? anchored.cx) * ctx.width;
  const cy = (ev.cy ?? anchored.cy) * ctx.height;

  const r = rng(seed + frame * 97);
  const motion = motionTransform(ev.motion, frame, fps, ctx.fs(10), r.next);

  const fadeFrames = Math.max(0, Math.round(ev.fade * fps));
  const opacity =
    fadeFrames > 0
      ? ev.opacity *
        interpolate(
          frame,
          [0, fadeFrames, Math.max(fadeFrames, durationInFrames - fadeFrames), durationInFrames],
          [0, 1, 1, 0],
          { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' },
        )
      : ev.opacity;

  return (
    <div style={{ position: 'absolute', inset: 0, transform: motion.transform || undefined }}>
      <FxSprite
        src={ev.src}
        cx={cx}
        cy={cy}
        width={ctx.width * ev.w}
        maxHeight={ctx.height * ev.maxH}
        rot={ev.rot}
        scale={motion.scale}
        opacity={opacity}
      />
    </div>
  );
};
