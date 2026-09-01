// stickerStack: 同じ画像を、キーフレームで指定した位置へ次々に貼って積む。
//
// 顔にステッカーを貼る・画面に札を積み上げる、といった「増えていく」演出。
// キーフレームの間は線形に補間する。blowOutAt を書くと、そこから外へ吹き飛んで消える。

import React from 'react';
import { useCurrentFrame, useVideoConfig } from 'remotion';
import { FxSprite } from '../components/FxSprite';
import { useFxCtx } from '../lib/fx';
import { rng } from '../lib/rng';
import type { FxEventOf } from '../schema';

type Placed = { x: number; y: number; w: number; rot: number };

const lerp = (a: number, b: number, u: number) => a + (b - a) * u;

/** 秒 t の位置。まだ最初のキーフレームに達していなければ null。 */
const placeAt = (
  keyframes: FxEventOf<'stickerStack'>['target']['keyframes'],
  t: number,
): Placed | null => {
  const visible = keyframes.filter((k) => k.visible);
  if (visible.length === 0 || t < visible[0].t) {
    return null;
  }
  let prev = visible[0];
  let next = visible[visible.length - 1];
  for (const k of visible) {
    if (k.t <= t) {
      prev = k;
    }
    if (k.t >= t) {
      next = k;
      break;
    }
  }
  const u = next.t === prev.t ? 0 : Math.max(0, Math.min(1, (t - prev.t) / (next.t - prev.t)));
  return {
    x: lerp(prev.x, next.x, u),
    y: lerp(prev.y, next.y, u),
    w: lerp(prev.w, next.w, u),
    rot: lerp(prev.rot, next.rot, u),
  };
};

/** 貼った直後の 3 フレームだけ大きめから落として着地させる。 */
const POP_FRAMES = 3;

export const FxStickerStack: React.FC<{ ev: FxEventOf<'stickerStack'>; seed: number }> = ({
  ev,
  seed,
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const ctx = useFxCtx();
  const time = ev.t + frame / fps;

  const keyframes = [...ev.target.keyframes].sort((a, b) => a.t - b.t);
  const place = placeAt(keyframes, time);
  if (!place) {
    return null;
  }

  const toPx = (v: number, span: number) => (ev.target.space === 'ratio' ? v * span : v);
  let cx = toPx(place.x, ctx.width);
  let cy = toPx(place.y, ctx.height);
  const width = toPx(place.w, ctx.width);

  const born = keyframes.find((k) => k.visible)?.t ?? ev.t;
  const fi = Math.round((time - born) * fps);
  let scale = fi < POP_FRAMES ? 1 + (ev.pop - 1) * (1 - fi / POP_FRAMES) : 1;
  let rot = place.rot;
  let opacity = ev.opacity;

  if (ev.blowOutAt !== undefined && time >= ev.blowOutAt) {
    // 中心から外へ放り出しながら消える
    const u = Math.min(1, (time - ev.blowOutAt) / ev.blowOutSeconds);
    const r = rng(seed + 17);
    const k = Math.pow(u, 1.6) * 3.2;
    cx += (cx - ctx.width / 2) * k + r.range(-0.05, 0.05) * ctx.width * u;
    cy += (cy - ctx.height / 2) * k - 0.17 * ctx.height * u;
    rot += 130 * u;
    scale *= 1 + u * 0.7;
    opacity *= Math.max(0, 1 - u * 1.15);
    if (opacity <= 0) {
      return null;
    }
  }

  return (
    <FxSprite
      src={ev.src}
      cx={cx}
      cy={cy}
      width={width}
      maxHeight={ctx.height}
      rot={rot}
      scale={scale}
      opacity={opacity}
    />
  );
};
