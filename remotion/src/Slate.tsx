import React from 'react';
import { AbsoluteFill, interpolate, useCurrentFrame, useVideoConfig } from 'remotion';
import type { CalculateMetadataFunction } from 'remotion';
import { slateSchema, type SlateProps } from './schema';
import { FONT_FAMILY } from './fonts';

/** props のテキストを出すだけの動作確認用コンポジション。 */
export const Slate: React.FC<SlateProps> = (props) => {
  const frame = useCurrentFrame();
  const { fps, durationInFrames, height } = useVideoConfig();
  const scale = height / 1080;

  const opacity = interpolate(
    frame,
    [0, Math.round(0.4 * fps), durationInFrames - Math.round(0.4 * fps), durationInFrames],
    [0, 1, 1, 0],
    { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' },
  );
  const seconds = (frame / fps).toFixed(1);

  return (
    <AbsoluteFill
      style={{
        backgroundColor: props.backgroundColor ?? '#101820',
        justifyContent: 'center',
        alignItems: 'center',
        fontFamily: FONT_FAMILY,
        color: props.color ?? '#ffffff',
      }}
    >
      <AbsoluteFill style={{ justifyContent: 'center', alignItems: 'center', opacity }}>
        <div style={{ fontSize: 96 * scale, fontWeight: 800, textAlign: 'center', padding: '0 8%' }}>
          {props.text}
        </div>
        {props.subtitle ? (
          <div style={{ fontSize: 44 * scale, marginTop: 28 * scale, opacity: 0.8 }}>
            {props.subtitle}
          </div>
        ) : null}
      </AbsoluteFill>
      <div
        style={{
          position: 'absolute',
          right: 40 * scale,
          bottom: 32 * scale,
          fontSize: 32 * scale,
          fontVariantNumeric: 'tabular-nums',
          opacity: 0.6,
        }}
      >
        {seconds}s / {(durationInFrames / fps).toFixed(1)}s
      </div>
    </AbsoluteFill>
  );
};

export const calculateSlateMetadata: CalculateMetadataFunction<SlateProps> = ({ props }) => {
  const parsed = slateSchema.parse(props);
  return {
    props: parsed,
    fps: parsed.fps,
    width: Math.round(parsed.width),
    height: Math.round(parsed.height),
    durationInFrames: Math.max(1, Math.round(parsed.durationInSeconds * parsed.fps)),
  };
};
