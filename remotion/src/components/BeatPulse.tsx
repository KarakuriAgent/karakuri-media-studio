import React from 'react';
import { AbsoluteFill, Easing, interpolate, useCurrentFrame, useVideoConfig } from 'remotion';

/** 1 拍のパルスが減衰しきるまでの秒数。 */
const PULSE_SECONDS = 0.18;

/**
 * beats(秒)の直後だけ軽く拡大する。ビートに「乗って」見せるための味付けで、
 * カットの切り替わりそのものはエージェントが cuts[].start をビート時刻に合わせて作る。
 */
export const BeatPulse: React.FC<{
  beats: number[];
  strength: number;
  children: React.ReactNode;
}> = ({ beats, strength, children }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const time = frame / fps;

  let sinceLastBeat = Number.POSITIVE_INFINITY;
  for (const beat of beats) {
    if (beat <= time) {
      sinceLastBeat = Math.min(sinceLastBeat, time - beat);
    }
  }

  const scale =
    sinceLastBeat > PULSE_SECONDS
      ? 1
      : interpolate(sinceLastBeat, [0, PULSE_SECONDS], [strength, 1], {
          extrapolateLeft: 'clamp',
          extrapolateRight: 'clamp',
          easing: Easing.out(Easing.quad),
        });

  return (
    <AbsoluteFill style={{ transform: `scale(${scale})`, transformOrigin: '50% 50%' }}>
      {children}
    </AbsoluteFill>
  );
};
