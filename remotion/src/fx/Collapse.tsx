// collapse: 画面をタイルに割って落とす。
//
// タイルは <Freeze> で「割れる直前の 1 枚」に固定する。理由は 2 つ:
//   - 絵として、割れて落ちるのは静止画のほうがガラスらしい
//   - タイルの数だけ <OffthreadVideo> が base を切り出しにいくので、時刻がフレームごとに
//     動くと同時に何本も切り出しが走って delayRender がタイムアウトする。
//     時刻を 1 点に固定すればキャッシュが効く。

import React from 'react';
import { AbsoluteFill, Freeze } from 'remotion';
import { FxBaseMedia } from '../components/FxBaseLayer';
import { useFxCtx } from '../lib/fx';
import { rng } from '../lib/rng';
import type { FxBase, FxEventOf } from '../schema';

export const FxCollapse: React.FC<{
  ev: FxEventOf<'collapse'>;
  seed: number;
  base?: FxBase;
  backgroundColor: string;
  /** 割れる直前のフレーム(コンポジション全体での通し番号) */
  freezeFrame: number;
  /** 落ちきるまでのフレーム数 */
  fallFrames: number;
  /**
   * イベント開始からのフレーム数。
   * <Freeze> の基準をずらしたくないので Sequence には入れず、呼び出し側から渡す。
   */
  fi: number;
}> = ({ ev, seed, base, backgroundColor, freezeFrame, fallFrames, fi }) => {
  const ctx = useFxCtx();
  const tw = ctx.width / ev.cols;
  const th = ctx.height / ev.rows;

  return (
    <AbsoluteFill style={{ background: ctx.color(ev.background), overflow: 'hidden' }}>
      {Array.from({ length: ev.rows }, (_, row) =>
        Array.from({ length: ev.cols }, (_, col) => {
          const r = rng(seed + (row * ev.cols + col) * 7919 + 31);
          const delay = r.randint(0, Math.max(1, Math.round(fallFrames * 0.2)));
          const spin = r.randint(-46, 46);
          const drift = r.range(-0.031, 0.031) * ctx.width;
          const u = Math.max(0, Math.min(1, (fi - delay) / Math.max(1, fallFrames - delay)));
          if (u >= 1) {
            return null;
          }
          return (
            <div
              key={`${row},${col}`}
              style={{
                position: 'absolute',
                left: col * tw,
                top: row * th,
                width: tw,
                height: th,
                overflow: 'hidden',
                opacity: 1 - u * 0.85,
                transform: `translate(${drift * u * u}px, ${u * u * ctx.height * 1.5}px) rotate(${
                  spin * u
                }deg)`,
              }}
            >
              <div
                style={{
                  position: 'absolute',
                  left: -col * tw,
                  top: -row * th,
                  width: ctx.width,
                  height: ctx.height,
                }}
              >
                <Freeze frame={freezeFrame}>
                  <FxBaseMedia base={base} backgroundColor={backgroundColor} />
                </Freeze>
              </div>
            </div>
          );
        }),
      )}
    </AbsoluteFill>
  );
};
