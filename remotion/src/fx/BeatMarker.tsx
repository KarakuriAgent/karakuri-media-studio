// beatMarker: 隅で拍を刻むマーカー列。間奏など「歌っていないところ」の間を持たせる。
//
// 音源解析をしなくても、beat(1 拍の秒数)を渡せば等間隔で送れる。
// glitchEvery 拍ごとにブロックノイズを 1 回差してアクセントにする。

import React from 'react';
import { AbsoluteFill, useCurrentFrame, useVideoConfig } from 'remotion';
import { cornerStyle, useFxCtx } from '../lib/fx';
import { GlitchBlocks } from './GlitchCut';
import type { FxEventOf } from '../schema';

export const FxBeatMarker: React.FC<{ ev: FxEventOf<'beatMarker'>; seed: number }> = ({
  ev,
  seed,
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const ctx = useFxCtx();

  const u = frame / fps / ev.beat;
  const beatIndex = Math.floor(u);
  const phase = u - beatIndex;
  const active = ((beatIndex % ev.count) + ev.count) % ev.count;

  const size = ctx.fs(ev.size);
  const gap = size * 1.9;
  const margin = ctx.width * 0.04;
  const accent = ctx.color(ev.color);
  const idle = ctx.color(ev.idleColor);

  // 拍の頭 2 フレームだけノイズを差す
  const framesIntoBeat = Math.round((u - beatIndex) * ev.beat * fps);
  const glitching =
    ev.glitchEvery > 0 && beatIndex % ev.glitchEvery === ev.glitchEvery - 1 && framesIntoBeat < 2;

  return (
    <AbsoluteFill style={{ opacity: ev.opacity }}>
      <div
        style={{
          position: 'absolute',
          ...cornerStyle(ev.corner, margin),
          display: 'flex',
          alignItems: 'flex-end',
          gap: gap - size,
          height: size * 1.5,
        }}
      >
        {Array.from({ length: ev.count }, (_, i) => {
          const on = i === active;
          const s = size * (on ? 1.4 - 0.4 * phase : 1);
          return (
            <div
              key={i}
              style={{
                width: s,
                height: s,
                background: on ? accent : idle,
                opacity: on ? 1 - phase * 0.45 : 0.2,
              }}
            />
          );
        })}
      </div>
      {ev.label ? (
        <div
          style={{
            position: 'absolute',
            ...cornerStyle(ev.corner === 'bottomRight' ? 'bottomLeft' : ev.corner, margin),
            fontFamily: ctx.monoFamily,
            fontSize: ctx.fs(26),
            letterSpacing: '0.12em',
            color: idle,
            opacity: 0.5,
          }}
        >
          {ev.label}
        </div>
      ) : null}
      {glitching ? <GlitchBlocks seed={seed + beatIndex * 977} count={7} alpha={0.55} /> : null}
    </AbsoluteFill>
  );
};
