import React from 'react';
import {
  AbsoluteFill,
  Easing,
  Sequence,
  interpolate,
  useCurrentFrame,
  useVideoConfig,
} from 'remotion';
import type { LyricLine } from '../schema';
import { FONT_FAMILY } from '../fonts';

const FADE_SECONDS = 0.3;

const outlineShadow = (color: string, px: number): string | undefined => {
  if (!color) {
    return undefined;
  }
  const o = Math.max(1, Math.round(px));
  return [
    `${o}px ${o}px 0 ${color}`,
    `${-o}px ${o}px 0 ${color}`,
    `${o}px ${-o}px 0 ${color}`,
    `${-o}px ${-o}px 0 ${color}`,
    `0 ${o}px 0 ${color}`,
    `0 ${-o}px 0 ${color}`,
    `${o}px 0 0 ${color}`,
    `${-o}px 0 0 ${color}`,
  ].join(', ');
};

const LyricLineView: React.FC<{ line: LyricLine; scale: number }> = ({ line, scale }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const totalFrames = Math.max(1, Math.round((line.end - line.start) * fps));
  const fadeFrames = Math.max(1, Math.round(FADE_SECONDS * fps));
  const style = line.style ?? 'fade';

  const opacity = interpolate(
    frame,
    [0, fadeFrames, Math.max(fadeFrames, totalFrames - fadeFrames), totalFrames],
    [0, 1, 1, 0],
    { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' },
  );

  const popOffset =
    style === 'pop'
      ? interpolate(frame, [0, fadeFrames], [1, 0], {
          extrapolateLeft: 'clamp',
          extrapolateRight: 'clamp',
          easing: Easing.out(Easing.back(1.6)),
        })
      : 0;

  const fontSize = (line.fontSize ?? 64) * scale;
  const justifyContent =
    line.position === 'top' ? 'flex-start' : line.position === 'center' ? 'center' : 'flex-end';
  const padding =
    line.position === 'top'
      ? { paddingTop: '8%' }
      : line.position === 'center'
        ? {}
        : { paddingBottom: '9%' };

  const baseTextStyle: React.CSSProperties = {
    fontFamily: FONT_FAMILY,
    fontSize,
    fontWeight: line.bold === false ? 500 : 800,
    lineHeight: 1.25,
    letterSpacing: '0.02em',
    textAlign: 'center',
    whiteSpace: 'pre-wrap',
    margin: 0,
    width: '100%',
    boxSizing: 'border-box',
    padding: '0 6%',
  };

  if (style === 'karaoke') {
    // 表示区間の進行に合わせて左から色を送る(文字送り)。
    const progress = interpolate(frame, [0, totalFrames], [0, 1], {
      extrapolateLeft: 'clamp',
      extrapolateRight: 'clamp',
    });
    const stop = `${progress * 100}%`;
    return (
      <AbsoluteFill
        style={{
          justifyContent,
          alignItems: 'center',
          ...(padding as React.CSSProperties),
          opacity,
        }}
      >
        <div style={{ position: 'relative', width: '100%' }}>
          {/* 縁取りは別レイヤーで敷く(background-clip:text と text-shadow は併用できないため) */}
          <div
            style={{
              ...baseTextStyle,
              position: 'absolute',
              inset: 0,
              color: 'transparent',
              textShadow: outlineShadow(line.outlineColor ?? '', fontSize * 0.045),
            }}
          >
            {line.text}
          </div>
          <div
            style={{
              ...baseTextStyle,
              backgroundImage: `linear-gradient(90deg, ${line.highlightColor ?? '#ffd54a'} 0%, ${
                line.highlightColor ?? '#ffd54a'
              } ${stop}, ${line.color ?? '#ffffff'} ${stop}, ${line.color ?? '#ffffff'} 100%)`,
              WebkitBackgroundClip: 'text',
              backgroundClip: 'text',
              color: 'transparent',
              position: 'relative',
            }}
          >
            {line.text}
          </div>
        </div>
      </AbsoluteFill>
    );
  }

  return (
    <AbsoluteFill
      style={{
        justifyContent,
        alignItems: 'center',
        ...(padding as React.CSSProperties),
        opacity,
        transform: `translateY(${popOffset * fontSize * 0.6}px)`,
      }}
    >
      <div
        style={{
          ...baseTextStyle,
          color: line.color ?? '#ffffff',
          textShadow: outlineShadow(line.outlineColor ?? '', fontSize * 0.045),
        }}
      >
        {line.text}
      </div>
    </AbsoluteFill>
  );
};

export const Lyrics: React.FC<{ lyrics: LyricLine[] }> = ({ lyrics }) => {
  const { fps, height } = useVideoConfig();
  const scale = height / 1080;

  return (
    <>
      {lyrics.map((line, index) => {
        const from = Math.round(line.start * fps);
        const durationInFrames = Math.max(1, Math.round((line.end - line.start) * fps));
        return (
          <Sequence
            key={`lyric-${index}`}
            from={from}
            durationInFrames={durationInFrames}
            layout="none"
          >
            <LyricLineView line={line} scale={scale} />
          </Sequence>
        );
      })}
    </>
  );
};
