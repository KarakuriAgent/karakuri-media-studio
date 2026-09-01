// sprite: 透過画像を 1 枚貼る。ロゴ押印・小物・キャラの立ち絵。
// 位置は anchor(隅・辺)か cx / cy(画面比)。動きは motion で選ぶ。

import React from 'react';
import { interpolate, useCurrentFrame, useVideoConfig } from 'remotion';
import { FxSprite } from '../components/FxSprite';
import { anchorCenter, jitterOffset, motionTransform, spriteHalftone, useFxCtx } from '../lib/fx';
import { rng } from '../lib/rng';
import type { FxEventOf } from '../schema';

export const FxSpriteEvent: React.FC<{ ev: FxEventOf<'sprite'>; seed: number }> = ({ ev, seed }) => {
  const frame = useCurrentFrame();
  const { fps, durationInFrames } = useVideoConfig();
  const ctx = useFxCtx();

  const anchored = anchorCenter(ev.anchor, ev.w, ev.maxH, ev.margin);
  // 微振動(貼ったあと小刻みに揺れる)。jitter を書かなければ 0。
  const jitter = jitterOffset(ev.jitter, frame / fps, seed % 7, ctx.scale);
  const cx = (ev.cx ?? anchored.cx) * ctx.width + jitter.dx;
  const cy = (ev.cy ?? anchored.cy) * ctx.height + jitter.dy;

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

  const spriteWidth = ctx.width * ev.w;
  const borderColor = ctx.color(ev.border?.color ?? 'fg');
  const halftone = spriteHalftone(ev.halftone, spriteWidth, ctx.scale);
  return (
    <div style={{ position: 'absolute', inset: 0, transform: motion.transform || undefined }}>
      <FxSprite
        src={ev.src}
        cx={cx}
        cy={cy}
        width={spriteWidth}
        maxHeight={ctx.height * ev.maxH}
        rot={ev.rot + jitter.rot}
        scale={motion.scale}
        opacity={opacity}
        tint={ev.tint ? ctx.color(ev.tint) : undefined}
        border={
          ev.border
            ? { color: borderColor, width: ctx.fs(ev.border.width), inset: ev.border.inset }
            : undefined
        }
        halftone={halftone ? { ...halftone, color: borderColor } : undefined}
      />
    </div>
  );
};
