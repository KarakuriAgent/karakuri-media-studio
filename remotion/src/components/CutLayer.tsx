import React from 'react';
import {
  AbsoluteFill,
  Easing,
  Img,
  OffthreadVideo,
  interpolate,
  useCurrentFrame,
  useVideoConfig,
} from 'remotion';
import { resolveSource } from '../media';
import type { Cut, Transition } from '../schema';

/** そのカットが「入ってくる」ときに前のカットと重ねる秒数。 */
export const transitionOverlapSeconds = (transition?: Transition): number => {
  if (!transition || transition.type === 'cut') {
    return 0;
  }
  const duration = transition.duration ?? 0.4;
  if (transition.type === 'fadeblack' || transition.type === 'fadewhite') {
    // 前半で黒(白)に沈み、後半で明ける。重ねるのは前半ぶんだけ。
    return duration / 2;
  }
  return duration;
};

type EnterStyle = {
  opacity: number;
  transform: string;
  clipPath?: string;
};

const enterStyle = (
  transition: Transition | undefined,
  frame: number,
  fps: number,
): EnterStyle => {
  const none: EnterStyle = { opacity: 1, transform: '' };
  if (!transition || transition.type === 'cut' || transition.duration <= 0) {
    return none;
  }
  const frames = Math.max(1, Math.round(transition.duration * fps));
  if (frame >= frames) {
    return none;
  }
  const linear = interpolate(frame, [0, frames], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });
  const eased = interpolate(linear, [0, 1], [0, 1], {
    easing: Easing.out(Easing.cubic),
  });
  const remaining = (1 - eased) * 100;
  const direction = transition.direction ?? 'left';

  switch (transition.type) {
    case 'crossfade':
      return { opacity: linear, transform: '' };
    case 'fadeblack':
    case 'fadewhite':
      // 覆いかぶさるオーバーレイ側で表現するのでカット自体は素通し。
      return none;
    case 'slide': {
      const offset =
        direction === 'left'
          ? `translateX(${remaining}%)`
          : direction === 'right'
            ? `translateX(${-remaining}%)`
            : direction === 'up'
              ? `translateY(${remaining}%)`
              : `translateY(${-remaining}%)`;
      return { opacity: 1, transform: offset };
    }
    case 'wipe': {
      const clipPath =
        direction === 'left'
          ? `inset(0 0 0 ${remaining}%)`
          : direction === 'right'
            ? `inset(0 ${remaining}% 0 0)`
            : direction === 'up'
              ? `inset(${remaining}% 0 0 0)`
              : `inset(0 0 ${remaining}% 0)`;
      return { opacity: 1, transform: '', clipPath };
    }
    default:
      return none;
  }
};

export const CutLayer: React.FC<{
  cut: Cut;
  /** トランジションぶんの延長を含まない、本来のカット長(フレーム) */
  bodyDurationInFrames: number;
}> = ({ cut, bodyDurationInFrames }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const source = resolveSource(cut.src);
  const enter = enterStyle(cut.transition, frame, fps);

  const kenBurns = cut.kenBurns;
  const scale = kenBurns
    ? interpolate(frame, [0, bodyDurationInFrames], [kenBurns.from, kenBurns.to], {
        extrapolateLeft: 'clamp',
        extrapolateRight: 'clamp',
      })
    : 1;
  const originX = kenBurns?.originX ?? 0.5;
  const originY = kenBurns?.originY ?? 0.5;

  const fit = cut.fit ?? 'cover';
  const mediaStyle: React.CSSProperties = {
    width: '100%',
    height: '100%',
    objectFit: fit,
  };

  const inner = (() => {
    if (source.kind === 'color') {
      return <AbsoluteFill style={{ background: source.css }} />;
    }
    if (source.kind === 'video') {
      return (
        <OffthreadVideo
          src={source.url}
          startFrom={Math.round((cut.in ?? 0) * fps)}
          volume={cut.volume ?? 0}
          playbackRate={cut.playbackRate ?? 1}
          style={mediaStyle}
        />
      );
    }
    return <Img src={source.url} style={mediaStyle} />;
  })();

  return (
    <AbsoluteFill
      style={{
        opacity: enter.opacity * (cut.opacity ?? 1),
        transform: enter.transform || undefined,
        clipPath: enter.clipPath,
        overflow: 'hidden',
        filter: cut.filter,
      }}
    >
      <AbsoluteFill
        style={{
          transform: `scale(${scale})`,
          transformOrigin: `${originX * 100}% ${originY * 100}%`,
        }}
      >
        {inner}
      </AbsoluteFill>
    </AbsoluteFill>
  );
};
