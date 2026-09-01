// credits: 隅に出す小さなクレジット。白 + 縁取り。フェードはしない(出るときは出る)。
//
// lines は文字列でも {text, fontSize, color} でもよい。文字列だけのときは
// 1 行目をいちばん大きく、以降は少し小さくする(従来どおり)。

import React from 'react';
import { AbsoluteFill } from 'remotion';
import { FxText } from '../components/FxText';
import { placeStyle, useFxCtx } from '../lib/fx';
import type { FxEventOf } from '../schema';

export const FxCredits: React.FC<{ ev: FxEventOf<'credits'> }> = ({ ev }) => {
  const ctx = useFxCtx();
  const margin = ctx.width * ev.margin;
  const fontSize = ctx.fs(ev.fontSize);

  return (
    <AbsoluteFill style={{ opacity: ev.opacity }}>
      <div
        style={{
          position: 'absolute',
          ...placeStyle({
            corner: ev.corner,
            marginPx: margin,
            width: ctx.width,
            height: ctx.height,
            cx: ev.cx,
            cy: ev.cy,
          }),
        }}
      >
        {ev.lines.map((line, i) => {
          const row = typeof line === 'string' ? { text: line } : line;
          // 行ごとの指定があればそれを使い、無ければ 1 行目だけ大きく
          const size = row.fontSize !== undefined ? ctx.fs(row.fontSize) : fontSize * (i === 0 ? 1 : 0.82);
          return (
            <FxText
              key={i}
              fontFamily={ctx.fontFamily}
              fontSize={size}
              color={ctx.color(row.color ?? ev.color)}
              outlineColor={ctx.color(ev.outlineColor)}
              outlineWidth={ctx.fs(ev.outlineWidth) * (size / fontSize)}
              style={{ letterSpacing: '0.03em' }}
            >
              {row.text}
            </FxText>
          );
        })}
      </div>
    </AbsoluteFill>
  );
};
