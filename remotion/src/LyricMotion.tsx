/**
 * LyricMotion — 歌詞から文字PV（リリックモーション）を組み立てて焼くコンポジション。
 *
 * 絵を描くところは `LyricCanvas.tsx`（`FxOverlay` の歌詞モーション層と共通。エンジンの
 * 素性・決定性の話もそちらのコメントにある）。ここが持つのは**単体の文字PV**として
 * 焼くときの話だけ:
 *
 *   - 実寸は JIZURA のデザインサイズ（`aspect`）を `res` に合わせて拡縮したもの
 *   - 尺は `durationInSeconds`、無ければ構成（plan）の終端
 *   - BGM（`audio.src`）を鳴らす
 */

import React from 'react';
import { Audio, useVideoConfig, type CalculateMetadataFunction } from 'remotion';
import { LyricCanvas, audioLike, buildProject, loadJizura, outputSize } from './LyricCanvas';
import type { LyricMotionProps } from './schema';
import { resolveMediaUrl } from './media';

export const calculateLyricMotionMetadata: CalculateMetadataFunction<
  LyricMotionProps
> = ({ props }) => {
  const J = loadJizura();
  const [width, height] = outputSize(J, props);
  const project = buildProject(J, props);
  // 尺は行の時刻だけで決まる（字幅に依らない）ので、フォントを読む前の plan で足りる。
  const plan = J.plan(project, audioLike(props));
  const seconds = props.durationInSeconds ?? plan.duration;
  return {
    width,
    height,
    fps: props.fps,
    durationInFrames: Math.max(1, Math.ceil(seconds * props.fps)),
  };
};

export const LyricMotion: React.FC<LyricMotionProps> = (props) => {
  const { fps, width, height } = useVideoConfig();
  return (
    <>
      <LyricCanvas
        props={props}
        width={width}
        height={height}
        transparent={props.keyBg === 'transparent'}
      />
      {props.audio ? (
        <Audio
          src={resolveMediaUrl(props.audio.src)}
          volume={props.audio.volume}
          startFrom={Math.round(props.audio.startFrom * fps)}
        />
      ) : null}
    </>
  );
};
