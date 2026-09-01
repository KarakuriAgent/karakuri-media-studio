// lyric: 歌詞テロップ。行そのまま(line)か、1 文字ずつ送る(karaoke)。
//
// karaoke の 1 文字ごとの発音秒は chars で渡す(音源解析の結果をそのまま入れる)。
// 省略したときは表示区間を文字数で等分する。フェードは掛けず、行頭だけ少し大きく出す。

import React from 'react';
import { AbsoluteFill, useCurrentFrame, useVideoConfig } from 'remotion';
import { FxText } from '../components/FxText';
import { outlineShadow, useFxCtx } from '../lib/fx';
import type { FxEventOf } from '../schema';

/** 発音した瞬間に「今ここ」と分かる強調を出す長さ(秒)。 */
const ACTIVE_SECONDS = 0.35;

export const FxLyric: React.FC<{ ev: FxEventOf<'lyric'> }> = ({ ev }) => {
  const frame = useCurrentFrame();
  const { fps, durationInFrames } = useVideoConfig();
  const ctx = useFxCtx();

  const fontSize = ctx.fs(ev.fontSize);
  const outlineWidth = ctx.fs(ev.outlineWidth);
  const snap = frame < ev.snapFrames ? 1.1 : 1;

  const justifyContent =
    ev.position === 'top' ? 'flex-start' : ev.position === 'center' ? 'center' : 'flex-end';
  const padding =
    ev.position === 'top'
      ? { paddingTop: '8%' }
      : ev.position === 'center'
        ? {}
        : { paddingBottom: '9%' };

  const chars = [...ev.text];
  // chars が無ければ表示区間を等分する
  const times: number[] = chars.map((_, i) => {
    const given = ev.chars[i];
    if (given) {
      return given.s;
    }
    return ev.t + ((durationInFrames / fps) * i) / Math.max(1, chars.length);
  });
  const time = ev.t + frame / fps;

  // karaoke は 1 文字ずつ色と縁を切り替える(まだ→歌った→今ここ)。
  const outlineColor = ctx.color(ev.outlineColor);
  const body =
    ev.style === 'karaoke' ? (
      <span style={{ whiteSpace: 'pre-wrap' }}>
        {chars.map((ch, i) => {
          const pending = time < times[i];
          const active = !pending && time < times[i] + ACTIVE_SECONDS;
          return (
            <span
              key={i}
              style={{
                color: pending ? ctx.color(ev.pendingColor) : ctx.color(ev.color),
                textShadow: outlineShadow(
                  active ? ctx.color(ev.activeColor) : outlineColor,
                  outlineWidth * (pending ? 0.7 : 1),
                ),
                opacity: pending ? 0.75 : 1,
                display: 'inline-block',
                transform: active ? 'scale(1.16)' : undefined,
              }}
            >
              {ch}
            </span>
          );
        })}
      </span>
    ) : (
      ev.text
    );

  return (
    <AbsoluteFill
      style={{
        justifyContent,
        alignItems: 'center',
        opacity: ev.opacity,
        ...(padding as React.CSSProperties),
      }}
    >
      <FxText
        fontFamily={ctx.fontFamily}
        fontSize={fontSize}
        color={ctx.color(ev.color)}
        outlineColor={ev.style === 'karaoke' ? undefined : outlineColor}
        outlineWidth={outlineWidth}
        style={{
          textAlign: 'center',
          width: '100%',
          boxSizing: 'border-box',
          padding: '0 6%',
          letterSpacing: '0.02em',
          transform: `scale(${snap})`,
        }}
      >
        {body}
      </FxText>
    </AbsoluteFill>
  );
};
