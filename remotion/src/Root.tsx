import React from 'react';
import { Composition } from 'remotion';
import { FxOverlay, calculateFxOverlayMetadata } from './FxOverlay';
import { LyricMotion, calculateLyricMotionMetadata } from './LyricMotion';
import { MusicVideo, calculateMusicVideoMetadata } from './MusicVideo';
import { Slate, calculateSlateMetadata } from './Slate';
import { fxOverlaySchema, lyricMotionSchema, musicVideoSchema, slateSchema } from './schema';
import musicVideoExample from '../examples/music-video.json';
import slateExample from '../examples/slate.json';
import fxOverlayExample from '../examples/fx-overlay.json';
import lyricMotionExample from '../examples/lyric-motion.json';

export const RemotionRoot: React.FC = () => {
  return (
    <>
      <Composition
        id="MusicVideo"
        component={MusicVideo}
        schema={musicVideoSchema}
        defaultProps={musicVideoSchema.parse(musicVideoExample)}
        calculateMetadata={calculateMusicVideoMetadata}
        // calculateMetadata で props から上書きするが、初期値として必要
        fps={30}
        width={1920}
        height={1080}
        durationInFrames={240}
      />
      <Composition
        id="Slate"
        component={Slate}
        schema={slateSchema}
        defaultProps={slateSchema.parse(slateExample)}
        calculateMetadata={calculateSlateMetadata}
        fps={30}
        width={1920}
        height={1080}
        durationInFrames={150}
      />
      <Composition
        id="FxOverlay"
        component={FxOverlay}
        schema={fxOverlaySchema}
        defaultProps={fxOverlaySchema.parse(fxOverlayExample)}
        calculateMetadata={calculateFxOverlayMetadata}
        fps={30}
        width={1920}
        height={1080}
        durationInFrames={300}
      />
      <Composition
        id="LyricMotion"
        component={LyricMotion}
        schema={lyricMotionSchema}
        defaultProps={lyricMotionSchema.parse(lyricMotionExample)}
        calculateMetadata={calculateLyricMotionMetadata}
        // JIZURA のデザインサイズ(16:9 = 1920x1080)を res に合わせて拡縮したものが
        // 実寸になるので、ここの値は calculateMetadata が必ず上書きする。
        fps={24}
        width={1920}
        height={1080}
        durationInFrames={120}
      />
    </>
  );
};
