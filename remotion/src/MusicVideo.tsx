import React from 'react';
import {
  AbsoluteFill,
  Audio,
  Sequence,
  interpolate,
  useCurrentFrame,
  useVideoConfig,
} from 'remotion';
import type { CalculateMetadataFunction } from 'remotion';
import { CutLayer, transitionOverlapSeconds } from './components/CutLayer';
import { Lyrics } from './components/Lyrics';
import { TitleCard } from './components/TitleCard';
import { BeatPulse } from './components/BeatPulse';
import { musicVideoSchema, type MusicVideoProps } from './schema';
import { resolveMediaUrl } from './media';

/** props からタイムライン全体の尺(秒)を求める。 */
export const musicVideoDurationInSeconds = (props: MusicVideoProps): number => {
  if (props.durationInSeconds) {
    return props.durationInSeconds;
  }
  const cutsEnd = (props.cuts ?? []).reduce(
    (max, cut) => Math.max(max, cut.start + cut.duration),
    0,
  );
  const lyricsEnd = (props.lyrics ?? []).reduce((max, line) => Math.max(max, line.end), 0);
  const titleEnd = props.title?.showUntil ?? 0;
  return Math.max(cutsEnd, lyricsEnd, titleEnd, 1);
};

const BackgroundFade: React.FC<{ color: string; durationInFrames: number }> = ({
  color,
  durationInFrames,
}) => {
  const frame = useCurrentFrame();
  const opacity = interpolate(
    frame,
    [0, durationInFrames / 2, durationInFrames],
    [0, 1, 0],
    { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' },
  );
  return <AbsoluteFill style={{ backgroundColor: color, opacity }} />;
};

export const MusicVideo: React.FC<MusicVideoProps> = (props) => {
  const { fps, durationInFrames } = useVideoConfig();
  const backgroundColor = props.backgroundColor ?? '#000000';
  const cuts = [...(props.cuts ?? [])].sort((a, b) => a.start - b.start);

  const toFrames = (seconds: number) => Math.round(seconds * fps);

  return (
    <AbsoluteFill style={{ backgroundColor }}>
      <BeatPulse
        beats={props.beatPulse ? (props.beats ?? []) : []}
        strength={props.beatPulseScale ?? 1.02}
      >
        {cuts.map((cut, index) => {
          const next = cuts[index + 1];
          const overlap = next ? transitionOverlapSeconds(next.transition) : 0;
          const bodyDurationInFrames = Math.max(1, toFrames(cut.duration));
          const from = toFrames(cut.start);
          const durationWithOverlap = Math.max(
            1,
            Math.min(bodyDurationInFrames + toFrames(overlap), durationInFrames - from),
          );
          if (durationWithOverlap <= 0) {
            return null;
          }
          return (
            <Sequence
              key={`cut-${index}`}
              from={from}
              durationInFrames={durationWithOverlap}
              layout="none"
              name={`cut ${index + 1}`}
            >
              <CutLayer cut={cut} bodyDurationInFrames={bodyDurationInFrames} />
            </Sequence>
          );
        })}
      </BeatPulse>

      {/* fadeblack / fadewhite は全画面のオーバーレイで表現する */}
      {cuts.map((cut, index) => {
        const type = cut.transition?.type;
        if (type !== 'fadeblack' && type !== 'fadewhite') {
          return null;
        }
        const seconds = cut.transition?.duration ?? 0.4;
        const frames = Math.max(1, toFrames(seconds));
        const from = Math.max(0, toFrames(cut.start) - Math.round(frames / 2));
        return (
          <Sequence
            key={`fade-${index}`}
            from={from}
            durationInFrames={frames}
            layout="none"
            name={`${type} ${index + 1}`}
          >
            <BackgroundFade
              color={type === 'fadeblack' ? '#000000' : '#ffffff'}
              durationInFrames={frames}
            />
          </Sequence>
        );
      })}

      {props.title ? (
        <Sequence
          from={0}
          durationInFrames={Math.max(1, toFrames(props.title.showUntil ?? 3))}
          layout="none"
          name="title"
        >
          <TitleCard title={props.title} />
        </Sequence>
      ) : null}

      <Lyrics lyrics={props.lyrics ?? []} />

      {props.audio ? (
        <Audio
          src={resolveMediaUrl(props.audio.src)}
          startFrom={toFrames(props.audio.startFrom ?? 0)}
          volume={(frame) => {
            const base = props.audio?.volume ?? 1;
            const fadeOut = props.audio?.fadeOut ?? 0;
            if (fadeOut <= 0) {
              return base;
            }
            const fadeFrames = Math.max(1, toFrames(fadeOut));
            return interpolate(
              frame,
              [durationInFrames - fadeFrames, durationInFrames],
              [base, 0],
              { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' },
            );
          }}
        />
      ) : null}
    </AbsoluteFill>
  );
};

export const calculateMusicVideoMetadata: CalculateMetadataFunction<MusicVideoProps> = ({
  props,
}) => {
  // props(JSON)には既定値が入っていないことがあるので、ここで zod に通して補完する。
  const parsed = musicVideoSchema.parse(props);
  const seconds = musicVideoDurationInSeconds(parsed);
  return {
    props: parsed,
    fps: parsed.fps,
    width: Math.round(parsed.width),
    height: Math.round(parsed.height),
    durationInFrames: Math.max(1, Math.round(seconds * parsed.fps)),
  };
};
