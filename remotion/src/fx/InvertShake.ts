// invertShake: 反転(ネガ)を数フレーム入れ、そのあと減衰しながら画面を揺らす。
//
// 画面そのものを触るので、他の効果のように「上に重ねる」のではなく、
// FxBaseLayer に渡す値を作る形にしてある。
// シェイクの起点は「反転が明けたところ」= card の終わり(BAN!BAN!BAN! で確定した仕様)。

import { rng } from '../lib/rng';
import type { FxEventOf } from '../schema';

export type InvertShakeState = {
  invert: boolean;
  flash: number;
  /** シェイク量(px) */
  dx: number;
  dy: number;
  /** hitStop の拡大率(1 = 等倍) */
  scale: number;
  /** hitStop のクロマ収差(0..1。px への換算は呼び出し側) */
  chroma: number;
};

export const invertShakeState = (
  ev: FxEventOf<'invertShake'>,
  /** イベント開始からのフレーム数 */
  fi: number,
  fps: number,
  width: number,
  seed: number,
): InvertShakeState => {
  const amplitude = ev.amplitude * width;
  const r = rng(seed + fi * 131);
  if (fi < ev.frames) {
    // 反転(または白飛ばし)の最中も揺らす
    return {
      invert: ev.mode === 'invert',
      flash: ev.mode === 'flash' ? 0.9 : 0,
      dx: Math.round(r.range(-amplitude, amplitude)),
      dy: Math.round(r.range(-amplitude, amplitude)),
      scale: 1,
      chroma: 0,
    };
  }
  // hitStop: 反転が明けた最初の数フレームだけ拡大 + クロマ収差(決めの「止め」)
  const hit =
    ev.hitStop && fi < ev.frames + ev.hitStop.frames
      ? { scale: ev.hitStop.scale, chroma: ev.hitStop.chroma }
      : { scale: 1, chroma: 0 };
  const dt = (fi - ev.frames) / fps;
  if (ev.shakeTail <= 0 || dt >= ev.shakeTail) {
    return { invert: false, flash: 0, dx: 0, dy: 0, ...hit };
  }
  const amp = amplitude * (1 - dt / ev.shakeTail);
  return {
    invert: false,
    flash: 0,
    dx: Math.round(r.range(-amp, amp)),
    dy: Math.round(r.range(-amp, amp)),
    ...hit,
  };
};
