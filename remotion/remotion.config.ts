import { Config } from '@remotion/cli/config';

Config.setVideoImageFormat('jpeg');
Config.setCodec('h264');
Config.setOverwriteOutput(true);

// 素材を http(s) で受け取るため、ネットワーク読み込みの失敗はログに出すだけにする。
Config.setChromiumOpenGlRenderer('angle');
