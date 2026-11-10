// video: 動画を 1 本重ねる。動く漫符・エフェクト素材など。
// 位置と大きさは sprite と同じ(anchor か cx / cy、幅 w × 高さ maxH の箱に fit で収める)。
//
// - 透過: アルファ付きの WebM は OffthreadVideo の transparent でそのまま重なる
// - クロマキー: chromaKey を書くと、動画を隠して 1 コマずつ canvas に描き、色を抜く
//   (onVideoFrame。書き出しでもプレビューでも同じ)
// - loop: 素材の 1 周(loopSeconds か素材の尺)ごとに頭から繰り返す(<Loop>)。尺を読む
//   あいだは動画を置かず、読めてから 1 度だけ置く(置き直すと待ちが残って落ちる)

import React from 'react';
import {
  Loop,
  OffthreadVideo,
  continueRender,
  delayRender,
  interpolate,
  useCurrentFrame,
  useVideoConfig,
} from 'remotion';
import { getVideoMetadata } from '@remotion/media-utils';
import { cssColorToRgb, keyPixels } from '../lib/chroma';
import { anchorCenter, motionTransform, toFrames, useFxCtx } from '../lib/fx';
import { rng } from '../lib/rng';
import { resolveMediaUrl } from '../media';
import type { FxChromaKey, FxEventOf } from '../schema';

type VideoEvent = FxEventOf<'video'>;

/**
 * 1 周の長さ(コンポジションのフレーム)。繰り返さないなら null。
 *
 * 素材の尺を読む必要があるあいだは `ready: false` で、そのあいだは動画を**まだ
 * 置かない**。尺が分かってから `<Loop>` で包むかどうかを決めて 1 度だけ置く。
 *
 * (以前は先に動画を置き、尺が分かった時点で `<Loop>` で包み直していた。包み直すと
 * OffthreadVideo が外されて置き直され、ちょうどそのとき前の OffthreadVideo の
 * フレームの取得が終わっていると、その delayRender が解かれないまま残って
 * 「Fetching … from server was called but not cleared after 28000ms」で書き出しが
 * 落ちた。素材を初めて読むコマ(取り寄せと切り出しが遅い)で起きやすく、同じ
 * 素材でもタイムラインやタブの並びによって起きたり起きなかったりした。)
 */
const useLoopFrames = (
  ev: VideoEvent,
  url: string,
  fps: number,
): { ready: boolean; frames: number | null } => {
  const needsProbe = ev.loop && ev.loopSeconds === undefined;
  const [seconds, setSeconds] = React.useState<number | null>(
    ev.loopSeconds !== undefined ? ev.loopSeconds : null,
  );
  const [probed, setProbed] = React.useState(!needsProbe);
  const [handle] = React.useState(() =>
    needsProbe ? delayRender(`video の尺を読む: ${url}`) : null,
  );
  const released = React.useRef(false);
  const release = React.useCallback(() => {
    if (handle !== null && !released.current) {
      released.current = true;
      continueRender(handle);
    }
  }, [handle]);
  React.useEffect(() => {
    if (handle === null) {
      return;
    }
    let alive = true;
    getVideoMetadata(url)
      .then((meta) => {
        if (alive) {
          setSeconds(Math.max(0, meta.durationInSeconds - ev.in));
        }
      })
      .catch((err) => {
        // 尺が読めなければ繰り返さない(最後のコマのまま)。描画は止めない。
        console.warn(`video: 素材の尺を読めませんでした(繰り返しません): ${url}`, err);
      })
      .finally(() => {
        if (alive) {
          setProbed(true);
        }
      });
    return () => {
      alive = false;
      // 尺を読み終える前に外された(Sequence を抜けた)ときも、待ちを残さない。
      release();
    };
  }, [handle, url, ev.in, release]);
  // 尺を読み終えたら、動画を置いた描画が確定したあとで待ちを解く(子の
  // OffthreadVideo は layout effect で自分の待ちを先に足しているので、コマが
  // 動画の無いまま撮られることはない)。
  React.useEffect(() => {
    if (probed) {
      release();
    }
  }, [probed, release]);
  if (!probed) {
    return { ready: false, frames: null };
  }
  if (!ev.loop || seconds === null || seconds <= 0) {
    return { ready: true, frames: null };
  }
  return { ready: true, frames: Math.max(1, Math.round((seconds / ev.playbackRate) * fps)) };
};

/** 1 コマの元の大きさ(要素の種類ごとに読み方が違う)。 */
const sourceSize = (frame: CanvasImageSource): [number, number] => {
  const f = frame as unknown as Record<string, unknown>;
  const pick = (w: string, h: string): [number, number] | null =>
    typeof f[w] === 'number' && typeof f[h] === 'number' && (f[w] as number) > 0
      ? [f[w] as number, f[h] as number]
      : null;
  return (
    pick('videoWidth', 'videoHeight') ??
    pick('naturalWidth', 'naturalHeight') ??
    pick('displayWidth', 'displayHeight') ??
    pick('width', 'height') ?? [0, 0]
  );
};

/** 色を抜いて canvas に描く動画。動画そのものは隠しておき、コマが来るたびに描く。 */
const KeyedVideo: React.FC<{
  url: string;
  trimBefore: number;
  playbackRate: number;
  fit: VideoEvent['fit'];
  chroma: FxChromaKey;
  keyColor: string;
  boxW: number;
  boxH: number;
}> = ({ url, trimBefore, playbackRate, fit, chroma, keyColor, boxW, boxH }) => {
  const canvasRef = React.useRef<HTMLCanvasElement>(null);
  const warned = React.useRef(false);
  const key = React.useMemo(() => cssColorToRgb(keyColor), [keyColor]);

  const onVideoFrame = React.useCallback(
    (frame: CanvasImageSource) => {
      const canvas = canvasRef.current;
      if (!canvas) {
        return;
      }
      const [w, h] = sourceSize(frame);
      if (!w || !h) {
        return;
      }
      // 箱より大きな素材は、箱に要る大きさまで落としてから抜く(画素の計算を減らす)
      const s = Math.min(1, Math.max(boxW / w, boxH / h));
      const cw = Math.max(1, Math.round(w * s));
      const ch = Math.max(1, Math.round(h * s));
      if (canvas.width !== cw || canvas.height !== ch) {
        canvas.width = cw;
        canvas.height = ch;
      }
      const c = canvas.getContext('2d', { willReadFrequently: true });
      if (!c) {
        return;
      }
      c.clearRect(0, 0, cw, ch);
      c.drawImage(frame, 0, 0, cw, ch);
      try {
        const img = c.getImageData(0, 0, cw, ch);
        keyPixels(img.data, key, chroma.similarity, chroma.smoothness, chroma.spill);
        c.putImageData(img, 0, 0);
      } catch (err) {
        // 別オリジンの素材で CORS が無いと画素を読めない。抜かずに出す。
        if (!warned.current) {
          warned.current = true;
          console.warn(`video: 色を抜けませんでした(画素を読めません): ${url}`, err);
        }
      }
    },
    [boxW, boxH, chroma.similarity, chroma.smoothness, chroma.spill, key, url],
  );

  const fill: React.CSSProperties = { position: 'absolute', inset: 0, width: '100%', height: '100%' };
  return (
    <>
      <OffthreadVideo
        src={url}
        trimBefore={trimBefore}
        playbackRate={playbackRate}
        muted
        toneMapped={false}
        crossOrigin="anonymous"
        onVideoFrame={onVideoFrame}
        style={{ ...fill, opacity: 0 }}
      />
      <canvas ref={canvasRef} style={{ ...fill, objectFit: fit }} />
    </>
  );
};

export const FxVideoEvent: React.FC<{ ev: VideoEvent; seed: number }> = ({ ev, seed }) => {
  const frame = useCurrentFrame();
  const { fps, durationInFrames } = useVideoConfig();
  const ctx = useFxCtx();
  const url = resolveMediaUrl(ev.src);
  const loop = useLoopFrames(ev, url, fps);

  const anchored = anchorCenter(ev.anchor, ev.w, ev.maxH, ev.margin);
  const cx = (ev.cx ?? anchored.cx) * ctx.width;
  const cy = (ev.cy ?? anchored.cy) * ctx.height;
  const boxW = ctx.width * ev.w;
  const boxH = ctx.height * ev.maxH;

  const r = rng(seed + frame * 97);
  const motion = motionTransform(ev.motion, frame, fps, ctx.fs(10), r.next);

  const fadeFrames = Math.max(0, Math.round(ev.fade * fps));
  const opacity =
    fadeFrames > 0
      ? ev.opacity *
        interpolate(
          frame,
          [0, fadeFrames, Math.max(fadeFrames, durationInFrames - fadeFrames), durationInFrames],
          [0, 1, 1, 0],
          { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' },
        )
      : ev.opacity;

  const transforms = ['translate(-50%, -50%)'];
  if (ev.rot) {
    // CSS の rotate は時計回りが正なので符号を反転する(sprite と同じ)。
    transforms.push(`rotate(${-ev.rot}deg)`);
  }
  if (motion.scale !== 1) {
    transforms.push(`scale(${motion.scale})`);
  }

  const trimBefore = toFrames(ev.in, fps);
  const media = ev.chromaKey ? (
    <KeyedVideo
      url={url}
      trimBefore={trimBefore}
      playbackRate={ev.playbackRate}
      fit={ev.fit}
      chroma={ev.chromaKey}
      keyColor={ctx.color(ev.chromaKey.color)}
      boxW={boxW}
      boxH={boxH}
    />
  ) : (
    <OffthreadVideo
      src={url}
      trimBefore={trimBefore}
      playbackRate={ev.playbackRate}
      muted
      transparent={ev.transparent}
      toneMapped={false}
      style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', objectFit: ev.fit }}
    />
  );

  return (
    <div style={{ position: 'absolute', inset: 0, transform: motion.transform || undefined }}>
      <div
        style={{
          position: 'absolute',
          left: cx,
          top: cy,
          width: boxW,
          height: boxH,
          transform: transforms.join(' '),
          opacity,
        }}
      >
        {!loop.ready ? null : loop.frames !== null ? (
          <Loop durationInFrames={loop.frames} layout="none">
            {media}
          </Loop>
        ) : (
          media
        )}
      </div>
    </div>
  );
};
