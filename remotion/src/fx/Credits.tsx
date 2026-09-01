// credits: 隅に出す小さなクレジット。白 + 縁取り。フェードはしない(出るときは出る)。

import React from 'react';
import { AbsoluteFill } from 'remotion';
import { FxText } from '../components/FxText';
import { cornerStyle, useFxCtx } from '../lib/fx';
import type { FxEventOf } from '../schema';

export const FxCredits: React.FC<{ ev: FxEventOf<'credits'> }> = ({ ev }) => {
  const ctx = useFxCtx();
  const margin = ctx.width * ev.margin;
  const fontSize = ctx.fs(ev.fontSize);

  return (
    <AbsoluteFill style={{ opacity: ev.opacity }}>
      <div style={{ position: 'absolute', ...cornerStyle(ev.corner, margin) }}>
        {ev.lines.map((line, i) => (
          <FxText
            key={i}
            fontFamily={ctx.fontFamily}
            // 1 行目をいちばん大きく、以降は少し小さく
            fontSize={i === 0 ? fontSize : fontSize * 0.82}
            color={ctx.color(ev.color)}
            outlineColor={ctx.color(ev.outlineColor)}
            outlineWidth={ctx.fs(ev.outlineWidth) * (i === 0 ? 1 : 0.8)}
            style={{ letterSpacing: '0.03em' }}
          >
            {line}
          </FxText>
        ))}
      </div>
    </AbsoluteFill>
  );
};
