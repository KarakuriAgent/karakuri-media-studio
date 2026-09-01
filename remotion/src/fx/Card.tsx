// card: 全画面の色地に極太文字を数フレームだけ叩き込む。決めの起点になるカード。
//
// sequence に "背景色/文字色" を並べたぶんだけ、frames フレームごとに色が切り替わる。
// 傾き・横ずれは seed から決まる(props が同じなら毎回同じ絵)。フェードは掛けない。

import React from 'react';
import { AbsoluteFill, useCurrentFrame } from 'remotion';
import { useFxCtx } from '../lib/fx';
import { rng } from '../lib/rng';
import type { FxEventOf } from '../schema';

const HALFTONE_ALPHA = 0.13;

export const FxCard: React.FC<{ ev: FxEventOf<'card'>; seed: number }> = ({ ev, seed }) => {
  const frame = useCurrentFrame();
  const ctx = useFxCtx();
  const { width, height } = ctx;

  const sequence = ev.sequence.length ? ev.sequence : ['accent/fg'];
  const index = Math.min(sequence.length - 1, Math.floor(frame / ev.frames));
  const [bgToken, fgToken] = sequence[index].split('/');
  const bg = ctx.color((bgToken ?? 'accent').trim());
  const fg = ctx.color((fgToken ?? 'fg').trim());

  // 1 枚ごとに振り直す。同じ色が続いても「別のカード」に見えるのはこの振れのおかげ。
  const r = rng(seed + index * 7919);
  const rot = ev.jitterDeg ? r.range(-ev.jitterDeg, ev.jitterDeg) : 0;
  const dx = ev.jitterPx ? r.range(-ev.jitterPx, ev.jitterPx) * width : 0;

  const cx = width / 2 + dx;
  const cy = height / 2;
  const dot = Math.max(6, ctx.fs(26));
  const patternId = `fx-card-dot-${seed}-${index}`;

  return (
    <AbsoluteFill style={{ background: bg, overflow: 'hidden' }}>
      <svg
        width={width}
        height={height}
        viewBox={`0 0 ${width} ${height}`}
        style={{ position: 'absolute', inset: 0 }}
      >
        {ev.halftone ? (
          <>
            <defs>
              <pattern id={patternId} width={dot} height={dot} patternUnits="userSpaceOnUse">
                <circle cx={dot / 2} cy={dot / 2} r={dot * 0.1} fill={fg} />
              </pattern>
            </defs>
            <rect
              x={0}
              y={0}
              width={width}
              height={height}
              fill={`url(#${patternId})`}
              opacity={HALFTONE_ALPHA}
            />
          </>
        ) : null}
        {ev.text ? (
          <text
            x={cx}
            y={cy}
            // SVG の rotate は時計回りが正なので符号を反転する
            transform={`rotate(${-rot} ${cx} ${cy})`}
            textAnchor="middle"
            dominantBaseline="central"
            fontFamily={ctx.fontFamily}
            fontSize={ctx.fs(ev.fontSize)}
            fontWeight={900}
            fill={fg}
            xmlSpace="preserve"
          >
            {ev.text}
          </text>
        ) : null}
      </svg>
    </AbsoluteFill>
  );
};
