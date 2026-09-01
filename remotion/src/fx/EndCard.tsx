// endCard: 終わりの黒 + ロゴ。black 秒だけ何も出さず、そのあとロゴ(と文字)を出す。

import React from 'react';
import { AbsoluteFill, useCurrentFrame, useVideoConfig } from 'remotion';
import { FxSprite } from '../components/FxSprite';
import { FxText } from '../components/FxText';
import { useFxCtx } from '../lib/fx';
import type { FxEventOf } from '../schema';

export const FxEndCard: React.FC<{ ev: FxEventOf<'endCard'> }> = ({ ev }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const ctx = useFxCtx();
  const shown = frame >= Math.round(ev.black * fps);

  return (
    <AbsoluteFill style={{ background: ctx.color(ev.bg), overflow: 'hidden' }}>
      {shown && ev.logo ? (
        <FxSprite
          src={ev.logo.src}
          cx={ctx.width / 2}
          cy={ctx.height / 2}
          width={ctx.width * ev.logo.w}
          maxHeight={ctx.height * 0.6}
          tint={ev.logo.tint ? ctx.color(ev.logo.tint) : undefined}
        />
      ) : null}
      {shown && ev.text ? (
        <AbsoluteFill
          style={{ justifyContent: 'flex-end', alignItems: 'center', paddingBottom: '14%' }}
        >
          <FxText
            fontFamily={ctx.fontFamily}
            fontSize={ctx.fs(ev.fontSize)}
            color={ctx.color(ev.textColor)}
            style={{ textAlign: 'center', letterSpacing: '0.06em' }}
          >
            {ev.text}
          </FxText>
        </AbsoluteFill>
      ) : null}
    </AbsoluteFill>
  );
};
