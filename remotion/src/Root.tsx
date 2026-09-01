import React from 'react';
import { Composition } from 'remotion';
import { MusicVideo, calculateMusicVideoMetadata } from './MusicVideo';
import { Slate, calculateSlateMetadata } from './Slate';
import { musicVideoSchema, slateSchema } from './schema';
import musicVideoExample from '../examples/music-video.json';
import slateExample from '../examples/slate.json';

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
    </>
  );
};
