// stickerStack: 同じ画像を、キーフレームで指定した位置へ次々に貼って積む。
//
// 顔にステッカーを貼る・画面に札を積み上げる、といった「増えていく」演出。
// キーフレームの間は線形に補間する。blowOutAt を書くと、そこから外へ吹き飛んで消える。
//
// visible: false のキーフレームからは消える(次に visible: true のキーフレームが
// 来たらそこでまた貼り直す)。消えている区間では補間もしない。

import React from 'react';
import { useCurrentFrame, useVideoConfig } from 'remotion';
import { FxSprite } from '../components/FxSprite';
import { jitterOffset, spriteHalftone, useFxCtx } from '../lib/fx';
import { rng } from '../lib/rng';
import type { FxEventOf } from '../schema';

type Keyframes = FxEventOf<'stickerStack'>['target']['keyframes'];
/**
 * born は「今の表示区間が始まったキーフレームの秒」。pop の起点に使う。
 * pop はその起点のキーフレームの pop(false なら貼り直しても等倍のまま出す)。
 */
type Placed = { x: number; y: number; w: number; rot: number; born: number; pop: boolean };

const lerp = (a: number, b: number, u: number) => a + (b - a) * u;

/** 秒 t の位置。まだ最初のキーフレームに達していない・消えている区間なら null。 */
const placeAt = (keyframes: Keyframes, t: number): Placed | null => {
  if (keyframes.length === 0 || t < keyframes[0].t) {
    return null;
  }
  let prevIndex = 0;
  for (let i = 0; i < keyframes.length; i++) {
    if (keyframes[i].t <= t) {
      prevIndex = i;
    }
  }
  const prev = keyframes[prevIndex];
  if (!prev.visible) {
    return null;
  }
  // 今の表示区間の頭(visible: false を跨いだら、その次の true が起点)
  let born = prev.t;
  let pop = prev.pop;
  for (let i = prevIndex; i >= 0; i--) {
    if (!keyframes[i].visible) {
      break;
    }
    born = keyframes[i].t;
    pop = keyframes[i].pop;
  }
  const next = keyframes[prevIndex + 1];
  // 次が消えるキーフレームなら、そこまでは動かさずに止めておく
  if (!next || !next.visible) {
    return { x: prev.x, y: prev.y, w: prev.w, rot: prev.rot, born, pop };
  }
  const u = next.t === prev.t ? 0 : Math.max(0, Math.min(1, (t - prev.t) / (next.t - prev.t)));
  return {
    x: lerp(prev.x, next.x, u),
    y: lerp(prev.y, next.y, u),
    w: lerp(prev.w, next.w, u),
    rot: lerp(prev.rot, next.rot, u),
    born,
    pop,
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

  const fi = Math.round((time - place.born) * fps);
  // キーフレームに pop: false と書かれていれば、貼り直しでも大きくならない
  let scale = place.pop && fi < POP_FRAMES ? 1 + (ev.pop - 1) * (1 - fi / POP_FRAMES) : 1;
  let rot = place.rot;
  let opacity = ev.opacity;

  // 微振動(積んだカードが小刻みに揺れる)
  const jitter = jitterOffset(ev.jitter, time, seed % 7, ctx.scale);
  cx += jitter.dx;
  cy += jitter.dy;
  rot += jitter.rot;

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

  const borderColor = ev.border ? ctx.color(ev.border.color) : ctx.color('fg');
  const halftone = spriteHalftone(ev.halftone, width, ctx.scale);
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
      border={
        ev.border
          ? { color: borderColor, width: ctx.fs(ev.border.width), inset: ev.border.inset }
          : undefined
      }
      halftone={halftone ? { ...halftone, color: borderColor } : undefined}
    />
  );
};
