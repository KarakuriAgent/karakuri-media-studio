// terminalText: 等幅フォントの端末表示。lines を出し、then があれば同じ場所で差し替える。
//
// 「READ ✓ → BANNED」のような 2 段の見せ方と、起動シーケンス(typing: true)の
// タイプライタ表示を 1 つの型で賄う。

import React from 'react';
import { AbsoluteFill, useCurrentFrame } from 'remotion';
import { cornerStyle, useFxCtx } from '../lib/fx';
import type { FxEventOf } from '../schema';

export const FxTerminalText: React.FC<{ ev: FxEventOf<'terminalText'> }> = ({ ev }) => {
  const frame = useCurrentFrame();
  const ctx = useFxCtx();

  // then があるときは frames フレームで 2 段目へ差し替える
  const stage2 = ev.then.length > 0 && frame >= ev.frames;
  const rows: { text: string; color: string }[] = stage2
    ? ev.then.map((line) => ({ text: line.text, color: ctx.color(line.color) }))
    : ev.lines.map((text) => ({ text, color: ctx.color(ev.color) }));

  const fontSize = ctx.fs(ev.fontSize);
  const margin = ctx.width * 0.045;
  const cursorOn = ev.cursor && Math.floor((frame / ctx.fps) * 3) % 2 === 0;
  const startFrame = stage2 ? ev.frames : 0;

  return (
    <AbsoluteFill style={{ opacity: ev.opacity }}>
      <div
        style={{
          position: 'absolute',
          ...cornerStyle(ev.corner, margin),
          fontFamily: ctx.monoFamily,
          fontSize,
          fontWeight: 700,
          lineHeight: 1.45,
          letterSpacing: '0.04em',
          whiteSpace: 'pre',
        }}
      >
        {rows.map((row, i) => {
          // typing のときは行ごとに 1 行ぶんずつ遅らせて打ち出す
          const delay = ev.typing ? (i * ev.frames) / ctx.fps : 0;
          const elapsed = (frame - startFrame) / ctx.fps - delay;
          if (ev.typing && elapsed < 0) {
            return null;
          }
          const shown = ev.typing
            ? row.text.slice(0, Math.max(0, Math.floor(elapsed * ev.cps)))
            : row.text;
          const isLast = i === rows.length - 1;
          return (
            <div key={i} style={{ color: row.color }}>
              {shown}
              {isLast && cursorOn ? '_' : ''}
            </div>
          );
        })}
      </div>
    </AbsoluteFill>
  );
};
