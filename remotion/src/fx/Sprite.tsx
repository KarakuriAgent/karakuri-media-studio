// sprite: 透過画像を 1 枚貼る。ロゴ押印・小物・キャラの立ち絵。
// 位置は anchor(隅・辺)か cx / cy(画面比)。動きは motion で選ぶ。
// cover: true なら、素材の中の点(coverOrigin)を cx / cy に合わせて画面の四隅まで覆う大きさにする
// (集中線・効果線。計算は lib/fit.ts の coverBox)。

import React from 'react';
import { interpolate, useCurrentFrame, useVideoConfig } from 'remotion';
import { FxSprite } from '../components/FxSprite';
import { coverBox } from '../lib/fit';
import { anchorCenter, jitterOffset, motionTransform, spriteHalftone, useFxCtx } from '../lib/fx';
import { useMediaAspect } from '../lib/mediaAspect';
import { rng } from '../lib/rng';
import { resolveSource } from '../media';
import type { FxEventOf } from '../schema';

export const FxSpriteEvent: React.FC<{ ev: FxEventOf<'sprite'>; seed: number }> = ({ ev, seed }) => {
  const frame = useCurrentFrame();
  const { fps, durationInFrames } = useVideoConfig();
  const ctx = useFxCtx();

  const anchored = anchorCenter(ev.anchor, ev.w, ev.maxH, ev.margin);
  // 微振動(貼ったあと小刻みに揺れる)。jitter を書かなければ 0。
  const jitter = jitterOffset(ev.jitter, frame / fps, seed % 7, ctx.scale);
  const source = resolveSource(ev.src);
  // cover のときだけ素材の縦横比を読む(色面は画面と同じ比で覆う)
  const aspect = useMediaAspect(ev.cover && source.kind !== 'color' ? source.url : null, 'image');
  const covered = ev.cover
    ? coverBox({
        screenW: ctx.width,
        screenH: ctx.height,
        x: (ev.cx ?? 0.5) * ctx.width,
        y: (ev.cy ?? 0.5) * ctx.height,
        aspect: source.kind === 'color' ? ctx.width / ctx.height : (aspect ?? 1),
        origin: ev.coverOrigin,
        rot: ev.rot,
        margin: ev.coverMargin,
      })
    : null;
  const cx = (covered ? covered.cx : (ev.cx ?? anchored.cx) * ctx.width) + jitter.dx;
  const cy = (covered ? covered.cy : (ev.cy ?? anchored.cy) * ctx.height) + jitter.dy;

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

  const spriteWidth = covered ? covered.width : ctx.width * ev.w;
  const borderColor = ctx.color(ev.border?.color ?? 'fg');
  const halftone = spriteHalftone(ev.halftone, spriteWidth, ctx.scale);
  return (
    <div style={{ position: 'absolute', inset: 0, transform: motion.transform || undefined }}>
      <FxSprite
        src={ev.src}
        cx={cx}
        cy={cy}
        width={spriteWidth}
        maxHeight={covered ? covered.height : ctx.height * ev.maxH}
        rot={ev.rot + jitter.rot}
        // 覆っているときは縮まない(揺れで 1 倍を割ると端が見える)
        scale={covered ? Math.max(1, motion.scale) : motion.scale}
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
