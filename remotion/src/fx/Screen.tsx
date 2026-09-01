// screen: 全画面を塗りつぶす板。ブレイクの黒画面・タイトルカード・章の切れ目に使う。
// 背景色 + 任意のテキスト / 画像。glitch: true なら頭の数フレームだけ荒らしてから確定させる。

import React from 'react';
import { AbsoluteFill, useCurrentFrame } from 'remotion';
import { FxSprite } from '../components/FxSprite';
import { FxText } from '../components/FxText';
import { useFxCtx } from '../lib/fx';
import { rng } from '../lib/rng';
import { ChromaDefs, DisplaceDefs, chromaId, displaceId } from './filters';
import { GlitchBlocks } from './GlitchCut';
import type { FxEventOf } from '../schema';

export const FxScreen: React.FC<{ ev: FxEventOf<'screen'>; seed: number }> = ({ ev, seed }) => {
  const frame = useCurrentFrame();
  const ctx = useFxCtx();

  const body = (
    <>
      {ev.src ? (
        <FxSprite
          src={ev.src}
          cx={ctx.width / 2}
          cy={ctx.height / 2}
          width={ctx.width * ev.imageWidth}
          maxHeight={ctx.height * 0.7}
        />
      ) : null}
      {ev.text ? (
        <AbsoluteFill style={{ justifyContent: 'center', alignItems: 'center' }}>
          <FxText
            fontFamily={ev.mono ? ctx.monoFamily : ctx.fontFamily}
            fontSize={ctx.fs(ev.fontSize)}
            color={ctx.color(ev.textColor)}
            bold={!ev.mono}
            style={{ textAlign: 'center', letterSpacing: ev.mono ? '0.06em' : '0.02em' }}
          >
            {ev.text}
          </FxText>
        </AbsoluteFill>
      ) : null}
    </>
  );

  const glitching = ev.glitch && frame < ev.glitchFrames;
  if (!glitching) {
    return (
      <AbsoluteFill style={{ background: ctx.color(ev.bg), overflow: 'hidden' }}>{body}</AbsoluteFill>
    );
  }

  // 1 フレーム目がいちばん荒れて、最後のフレームでほぼ収まる
  const k = 1 - frame / ev.glitchFrames;
  const disp = Math.max(4, Math.round(0.022 * ctx.width * k));
  const chroma = Math.max(1, Math.round(0.006 * ctx.width * k));
  const r = rng(seed + frame * 5171);
  return (
    <AbsoluteFill style={{ background: ctx.color(ev.bg), overflow: 'hidden' }}>
      <DisplaceDefs scale={disp} seed={frame + 1} />
      <ChromaDefs amount={chroma} />
      <AbsoluteFill
        style={{
          filter: `url(#${displaceId(disp, frame + 1)}) url(#${chromaId(chroma)})`,
          transform: `translate(${r.range(-0.007, 0.007) * ctx.width}px, ${
            r.range(-0.004, 0.004) * ctx.height
          }px)`,
        }}
      >
        {body}
      </AbsoluteFill>
      <GlitchBlocks seed={seed + frame * 811} count={9} alpha={0.6 * k + 0.2} />
    </AbsoluteFill>
  );
};
