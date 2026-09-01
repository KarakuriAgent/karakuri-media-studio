// FxOverlay: 出来上がった映像(base)の上に、イベントで演出を載せるコンポジション。
//
// 「何秒に何を出すか」はすべて props(events)にあり、ここには一般化した効果しか無い。
// 単位は秒、位置は画面比、フォントサイズは 1080p 基準(解像度・fps に依存しない)。
//
// レイヤーの重なりは各イベントの z(省略時は EVENT_LAYER の型ごとの既定、小さいほど下)。
// 同じ層なら events に書いた順。screen(黒画面)の上に歌詞やランプを出したいときだけ z を書く。

import React from 'react';
import { AbsoluteFill, Audio, Sequence, interpolate, useCurrentFrame, useVideoConfig } from 'remotion';
import type { CalculateMetadataFunction } from 'remotion';
import { getVideoMetadata } from '@remotion/media-utils';
import { FxBaseLayer } from './components/FxBaseLayer';
import { FxAmbientLayer } from './fx/Ambient';
import { FxBeatMarker } from './fx/BeatMarker';
import { FxCard } from './fx/Card';
import { FxCollapse } from './fx/Collapse';
import { FxCredits } from './fx/Credits';
import { FxCrtOff } from './fx/CrtOff';
import { FxEndCard } from './fx/EndCard';
import { FxGlitchCut, glitchCutState } from './fx/GlitchCut';
import { FxImageSlam } from './fx/ImageSlam';
import { invertShakeState } from './fx/InvertShake';
import { FxLyric } from './fx/Lyric';
import { FxScreen } from './fx/Screen';
import { FxShape } from './fx/Shape';
import { FxSpriteEvent } from './fx/Sprite';
import { FxStickerStack } from './fx/StickerStack';
import { FxTerminalText } from './fx/TerminalText';
import {
  FxCtxProvider,
  chromaPixels,
  eventSeed,
  eventSpan,
  eventsEndSeconds,
  makeFxCtx,
  toFrames,
} from './lib/fx';
import { isColorSource, resolveMediaUrl } from './media';
import { fxOverlaySchema, type FxEvent, type FxOverlayProps } from './schema';

/** 型ごとの既定の重なり順(小さいほど下)。イベントに z があればそちらが優先。 */
const EVENT_LAYER: Record<FxEvent['type'], number> = {
  invertShake: 0, // base を触るだけで、上には何も出さない
  collapse: 0, // base の差し替え
  glitchCut: 1,
  beatMarker: 2,
  sprite: 3,
  stickerStack: 3,
  shape: 3,
  imageSlam: 4,
  lyric: 5,
  terminalText: 5,
  credits: 5,
  screen: 6,
  card: 7,
  endCard: 8,
  crtOff: 9,
};

type Prepared = {
  ev: FxEvent;
  index: number;
  seed: number;
  from: number;
  durationInFrames: number;
};

const prepare = (events: readonly FxEvent[], fps: number, globalSeed: number): Prepared[] =>
  events.map((ev, index) => {
    const span = eventSpan(ev, fps);
    return {
      ev,
      index,
      seed: eventSeed(ev, index, globalSeed),
      from: span.from,
      durationInFrames: span.durationInFrames,
    };
  });

const isActive = (p: Prepared, frame: number) =>
  frame >= p.from && frame < p.from + p.durationInFrames;

/** イベント 1 つぶんの見た目。base を触るだけの型はここでは何も返さない。 */
const FxEventView: React.FC<{ prepared: Prepared }> = ({ prepared }) => {
  const { ev, seed } = prepared;
  switch (ev.type) {
    case 'card':
      return <FxCard ev={ev} seed={seed} />;
    case 'imageSlam':
      return <FxImageSlam ev={ev} />;
    case 'terminalText':
      return <FxTerminalText ev={ev} />;
    case 'screen':
      return <FxScreen ev={ev} seed={seed} />;
    case 'glitchCut':
      return <FxGlitchCut ev={ev} seed={seed} />;
    case 'crtOff':
      return <FxCrtOff ev={ev} />;
    case 'sprite':
      return <FxSpriteEvent ev={ev} seed={seed} />;
    case 'stickerStack':
      return <FxStickerStack ev={ev} seed={seed} />;
    case 'credits':
      return <FxCredits ev={ev} />;
    case 'lyric':
      return <FxLyric ev={ev} />;
    case 'endCard':
      return <FxEndCard ev={ev} />;
    case 'beatMarker':
      return <FxBeatMarker ev={ev} seed={seed} />;
    case 'shape':
      return <FxShape ev={ev} seed={seed} />;
    case 'invertShake':
    case 'collapse':
      // base 側で処理する
      return null;
    default:
      return null;
  }
};

export const FxOverlay: React.FC<FxOverlayProps> = (props) => {
  const frame = useCurrentFrame();
  const { fps, width, height, durationInFrames } = useVideoConfig();

  const ctx = React.useMemo(
    () => makeFxCtx({ fps, width, height, theme: props.theme, seed: props.seed }),
    [fps, width, height, props.theme, props.seed],
  );
  const prepared = React.useMemo(
    () => prepare(props.events, fps, props.seed),
    [props.events, fps, props.seed],
  );

  // --- base に掛かるもの(反転・シェイク・走査線ずれ)を今のフレームぶん集める
  let dx = 0;
  let dy = 0;
  let invert = false;
  let flash = 0;
  let chroma = 0;
  let scale = 1;
  let glitch: [number, number] | null = null;

  for (const p of prepared) {
    if (!isActive(p, frame)) {
      continue;
    }
    if (p.ev.type === 'invertShake') {
      const state = invertShakeState(p.ev, frame - p.from, fps, width, p.seed);
      dx += state.dx;
      dy += state.dy;
      invert = invert || state.invert;
      flash = Math.max(flash, state.flash);
      scale = Math.max(scale, state.scale);
      chroma = Math.max(chroma, chromaPixels(state.chroma, ctx.scale));
    } else if (p.ev.type === 'glitchCut') {
      const state = glitchCutState(p.ev, frame - p.from, width, p.seed);
      glitch = state.glitch;
      chroma = Math.max(chroma, state.chroma);
    }
  }

  // --- collapse が走っているあいだは base をタイルに差し替える
  const collapsing = prepared.find((p) => p.ev.type === 'collapse' && isActive(p, frame));

  // z を書いたイベントはその層へ。書かなければ型ごとの既定層。同じ層なら events に書いた順。
  const layerOf = (p: Prepared) => p.ev.z ?? EVENT_LAYER[p.ev.type];
  const overlays = [...prepared].sort((a, b) => layerOf(a) - layerOf(b) || a.index - b.index);

  return (
    <FxCtxProvider value={ctx}>
      <AbsoluteFill style={{ backgroundColor: props.backgroundColor }}>
        {collapsing && collapsing.ev.type === 'collapse' ? (
          <FxCollapse
            ev={collapsing.ev}
            seed={collapsing.seed}
            base={props.base}
            backgroundColor={props.backgroundColor}
            // 割れる直前の 1 枚で固定する(タイルの数だけ動画を切り出さないため)
            freezeFrame={Math.max(0, collapsing.from - 1)}
            fallFrames={collapsing.durationInFrames}
            fi={frame - collapsing.from}
          />
        ) : (
          <FxBaseLayer
            base={props.base}
            backgroundColor={props.backgroundColor}
            dx={dx}
            dy={dy}
            invert={invert}
            flash={flash}
            chroma={chroma}
            glitch={glitch}
            scale={scale}
          />
        )}

        {overlays.map((p) => {
          if (p.ev.type === 'invertShake' || p.ev.type === 'collapse') {
            return null;
          }
          return (
            <Sequence
              key={`fx-${p.index}`}
              from={p.from}
              durationInFrames={p.durationInFrames}
              layout="none"
              name={`${p.ev.type} @${p.ev.t}s`}
            >
              <FxEventView prepared={p} />
            </Sequence>
          );
        })}

        <FxAmbientLayer ambient={props.ambient} />

        {props.audio ? (
          <Audio
            src={resolveMediaUrl(props.audio.src)}
            startFrom={toFrames(props.audio.startFrom, fps)}
            volume={(f) => {
              const base = props.audio?.volume ?? 1;
              const fadeOut = props.audio?.fadeOut ?? 0;
              if (fadeOut <= 0) {
                return base;
              }
              const fadeFrames = Math.max(1, toFrames(fadeOut, fps));
              return interpolate(f, [durationInFrames - fadeFrames, durationInFrames], [base, 0], {
                extrapolateLeft: 'clamp',
                extrapolateRight: 'clamp',
              });
            }}
          />
        ) : null}
      </AbsoluteFill>
    </FxCtxProvider>
  );
};

/** base が動画なら尺を読む。読めなければ 0(events だけで決める)。 */
const baseDurationInSeconds = async (props: FxOverlayProps): Promise<number> => {
  const src = props.base?.src;
  if (!src || isColorSource(src)) {
    return 0;
  }
  const url = resolveMediaUrl(src);
  if (!/\.(mp4|webm|mov|mkv|m4v|avi)(\?|#|$)/i.test(url)) {
    return 0;
  }
  try {
    const meta = await getVideoMetadata(url);
    return Math.max(0, meta.durationInSeconds - (props.base?.in ?? 0));
  } catch {
    // ネットワーク越しの素材や壊れたヘッダで読めないことがある。events の終端に任せる。
    return 0;
  }
};

/** 尺は durationInSeconds → 無ければ base の尺と events の終端の大きいほう。 */
export const fxOverlayDurationInSeconds = async (props: FxOverlayProps): Promise<number> => {
  if (props.durationInSeconds) {
    return props.durationInSeconds;
  }
  const eventsEnd = eventsEndSeconds(props.events, props.fps);
  const baseEnd = await baseDurationInSeconds(props);
  return Math.max(eventsEnd, baseEnd, 1);
};

export const calculateFxOverlayMetadata: CalculateMetadataFunction<FxOverlayProps> = async ({
  props,
}) => {
  // props(JSON)には既定値が入っていないことがあるので、ここで zod に通して補完する。
  const parsed = fxOverlaySchema.parse(props);
  const seconds = await fxOverlayDurationInSeconds(parsed);
  return {
    props: parsed,
    fps: parsed.fps,
    width: Math.round(parsed.width),
    height: Math.round(parsed.height),
    durationInFrames: Math.max(1, Math.round(seconds * parsed.fps)),
  };
};
