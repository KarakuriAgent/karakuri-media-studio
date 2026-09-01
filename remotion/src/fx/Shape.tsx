// shape: SVG で描く記号。雷・ハート・集中線・吹き出し・星・円・矢印・爆発・ばつ。
//
// キャラや小物は画像生成に回すが、この手の「ただの記号」は生成するだけ無駄なので
// ここで描く。図形は 100x100 のローカル座標で定義し、size(画面幅比)へ拡大する。

import React from 'react';
import { AbsoluteFill, useCurrentFrame, useVideoConfig } from 'remotion';
import { motionTransform, useFxCtx } from '../lib/fx';
import { rng } from '../lib/rng';
import type { FxEventOf, FxShapeKind } from '../schema';

/** 100x100 のローカル座標で図形を返す。fill / stroke は呼び出し側が渡す。 */
const shapeBody = (kind: FxShapeKind, common: React.SVGProps<SVGElement>): React.ReactNode => {
  const p = common as React.SVGProps<never>;
  switch (kind) {
    case 'bolt':
      return <polygon {...(p as object)} points="58,2 22,54 46,54 34,98 78,40 52,40 70,2" />;
    case 'heart':
      return (
        <path
          {...(p as object)}
          d="M50 92 C 12 64, 4 40, 18 24 C 30 10, 46 14, 50 28 C 54 14, 70 10, 82 24 C 96 40, 88 64, 50 92 Z"
        />
      );
    case 'speedlines':
      // 中心を空けた集中線。線の長さと角度をばらして「描いた感」を出す。
      return (
        <>
          {Array.from({ length: 36 }, (_, i) => {
            const a = (i / 36) * Math.PI * 2;
            const inner = 26 + (i % 3) * 4;
            const outer = 70 + (i % 4) * 3;
            return (
              <line
                {...(p as object)}
                key={i}
                x1={50 + Math.cos(a) * inner}
                y1={50 + Math.sin(a) * inner}
                x2={50 + Math.cos(a) * outer}
                y2={50 + Math.sin(a) * outer}
              />
            );
          })}
        </>
      );
    case 'bubble':
      return (
        <path
          {...(p as object)}
          d="M8 14 H92 V70 H58 L44 92 L40 70 H8 Z"
          strokeLinejoin="round"
        />
      );
    case 'star':
      return (
        <polygon
          {...(p as object)}
          points="50,4 61,36 95,36 68,57 78,90 50,70 22,90 32,57 5,36 39,36"
        />
      );
    case 'circle':
      return <circle {...(p as object)} cx={50} cy={50} r={44} />;
    case 'arrow':
      return <polygon {...(p as object)} points="4,38 60,38 60,16 96,50 60,84 60,62 4,62" />;
    case 'burst':
      return (
        <polygon
          {...(p as object)}
          points="50,0 60,22 82,10 78,34 100,38 82,52 98,70 74,72 78,96 56,84 50,100 40,84 20,96 22,72 0,68 16,52 2,36 24,32 20,10 40,20"
        />
      );
    case 'cross':
      return (
        <>
          <line {...(p as object)} x1={12} y1={12} x2={88} y2={88} strokeLinecap="round" />
          <line {...(p as object)} x1={88} y1={12} x2={12} y2={88} strokeLinecap="round" />
        </>
      );
    default:
      return null;
  }
};

/** 線しか描かない図形。fill を指定しても意味がないので strokeOnly として扱う。 */
const STROKE_ONLY: readonly FxShapeKind[] = ['speedlines', 'cross'];

export const FxShape: React.FC<{ ev: FxEventOf<'shape'>; seed: number }> = ({ ev, seed }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const ctx = useFxCtx();

  const sizePx = ctx.width * ev.size;
  const r = rng(seed + frame * 89);
  const motion = motionTransform(ev.motion, frame, fps, ctx.fs(12), r.next);

  const strokeOnly = STROKE_ONLY.includes(ev.shape);
  const fill = strokeOnly ? 'none' : ctx.color(ev.fill);
  // 線が無指定でも strokeOnly の図形は塗り色を線に回す
  const strokeToken = ev.stroke === 'none' && strokeOnly ? ev.fill : ev.stroke;
  const stroke = ctx.color(strokeToken);
  // 100 単位の viewBox なので、ピクセル指定の線幅をローカル単位に直す
  const strokeWidth = stroke === 'none' ? 0 : (ctx.fs(ev.strokeWidth) * 100) / Math.max(1, sizePx);

  return (
    <AbsoluteFill style={{ opacity: ev.opacity }}>
      <div
        style={{
          position: 'absolute',
          left: ctx.width * ev.cx,
          top: ctx.height * ev.cy,
          width: sizePx,
          height: sizePx,
          transform: [
            'translate(-50%, -50%)',
            ev.rot ? `rotate(${-ev.rot}deg)` : '',
            motion.transform,
            motion.scale !== 1 ? `scale(${motion.scale})` : '',
          ]
            .filter(Boolean)
            .join(' '),
        }}
      >
        <svg
          width={sizePx}
          height={sizePx}
          viewBox="0 0 100 100"
          style={{ position: 'absolute', inset: 0, overflow: 'visible' }}
        >
          {shapeBody(ev.shape, {
            fill,
            stroke,
            strokeWidth,
          } as React.SVGProps<SVGElement>)}
        </svg>
        {ev.shape === 'bubble' && ev.text ? (
          <div
            style={{
              position: 'absolute',
              left: 0,
              top: 0,
              width: sizePx,
              height: sizePx * 0.7,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              padding: sizePx * 0.1,
              boxSizing: 'border-box',
              fontFamily: ctx.fontFamily,
              fontSize: ctx.fs(ev.fontSize),
              fontWeight: 800,
              lineHeight: 1.2,
              textAlign: 'center',
              color: ctx.color(ev.textColor),
            }}
          >
            {ev.text}
          </div>
        ) : null}
      </div>
    </AbsoluteFill>
  );
};
