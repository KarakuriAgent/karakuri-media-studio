import React from 'react';
import { AbsoluteFill, Easing, interpolate, useCurrentFrame, useVideoConfig } from 'remotion';
import type { TitleProps } from '../schema';
import { FONT_FAMILY } from '../fonts';

const IN_SECONDS = 0.5;
const OUT_SECONDS = 0.6;

export const TitleCard: React.FC<{ title: TitleProps }> = ({ title }) => {
  const frame = useCurrentFrame();
  const { fps, height } = useVideoConfig();
  const scale = height / 1080;

  const showUntilFrames = Math.max(1, Math.round((title.showUntil ?? 3) * fps));
  const inFrames = Math.round(IN_SECONDS * fps);
  const outFrames = Math.round(OUT_SECONDS * fps);

  const opacity = interpolate(
    frame,
    [0, inFrames, Math.max(inFrames, showUntilFrames - outFrames), showUntilFrames],
    [0, 1, 1, 0],
    { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' },
  );
  if (opacity <= 0) {
    return null;
  }

  const slide = interpolate(frame, [0, inFrames], [1, 0], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
    easing: Easing.out(Easing.cubic),
  });

  const position = title.position ?? 'bottomLeft';
  const fontSize = (title.fontSize ?? 72) * scale;
  const color = title.color ?? '#ffffff';

  const align: React.CSSProperties =
    position === 'center'
      ? { justifyContent: 'center', alignItems: 'center', textAlign: 'center' }
      : position === 'topLeft'
        ? { justifyContent: 'flex-start', alignItems: 'flex-start', paddingTop: '8%' }
        : { justifyContent: 'flex-end', alignItems: 'flex-start', paddingBottom: '12%' };

  return (
    <AbsoluteFill
      style={{
        ...align,
        paddingLeft: position === 'center' ? 0 : '7%',
        paddingRight: '7%',
        opacity,
      }}
    >
      <div
        style={{
          fontFamily: FONT_FAMILY,
          color,
          textShadow: `0 ${Math.round(4 * scale)}px ${Math.round(18 * scale)}px rgba(0,0,0,0.65)`,
          transform: `translateX(${slide * 40 * scale}px)`,
        }}
      >
        <div style={{ fontSize, fontWeight: 800, letterSpacing: '0.02em', lineHeight: 1.15 }}>
          {title.text}
        </div>
        {title.artist ? (
          <div
            style={{
              fontSize: fontSize * 0.45,
              fontWeight: 500,
              opacity: 0.85,
              marginTop: fontSize * 0.18,
              letterSpacing: '0.08em',
            }}
          >
            {title.artist}
          </div>
        ) : null}
      </div>
    </AbsoluteFill>
  );
};
