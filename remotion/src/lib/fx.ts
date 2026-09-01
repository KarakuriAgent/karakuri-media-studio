/**
 * FxOverlay の共通の道具立て。
 *
 * - 色トークン("accent" / "fg" / "bg" / "0" / CSS 色)の解決
 * - 秒 → フレーム(round。切り上げにすると 1 フレーム遅れる)
 * - イベントの尺(until / duration / 型ごとの既定)
 * - 画面比 → ピクセル、1080p 基準のフォントサイズ → ピクセル
 */
import React from 'react';
import { FONT_FAMILY, MONO_FONT_FAMILY } from '../fonts';
import type { FxAnchor, FxCorner, FxEvent, FxTheme } from '../schema';

/** 秒をフレームに直す。切り上げると決めが 1 フレーム遅れるので必ず round。 */
export const toFrames = (seconds: number, fps: number): number => Math.round(seconds * fps);

const PALETTE_ROLES: Record<string, number> = { accent: 0, fg: 1, bg: 2 };

/** 色トークンを CSS の色にする。 */
export const resolveColor = (token: string, palette: readonly string[]): string => {
  const key = token.trim();
  if (key === '' || key === 'none') {
    return key === '' ? 'transparent' : 'none';
  }
  const role = PALETTE_ROLES[key];
  if (role !== undefined) {
    return palette[role] ?? '#ffffff';
  }
  if (/^\d+$/.test(key)) {
    return palette[Number(key)] ?? '#ffffff';
  }
  return key;
};

export type FxCtx = {
  fps: number;
  width: number;
  height: number;
  /** 1080p 基準の値を今の解像度に合わせる係数。 */
  scale: number;
  palette: readonly string[];
  fontFamily: string;
  monoFamily: string;
  /** props 全体の乱数の種。 */
  seed: number;
  color: (token: string) => string;
  /** 1080p 基準のフォントサイズ(px)を今の解像度に直す。 */
  fs: (size: number) => number;
};

export const makeFxCtx = (args: {
  fps: number;
  width: number;
  height: number;
  theme: FxTheme;
  seed: number;
}): FxCtx => {
  const palette = args.theme.palette.length ? args.theme.palette : ['#dc1428', '#f5f5f5', '#08080a'];
  const scale = args.height / 1080;
  return {
    fps: args.fps,
    width: args.width,
    height: args.height,
    scale,
    palette,
    fontFamily: args.theme.fontFamily || FONT_FAMILY,
    monoFamily: args.theme.monoFamily || MONO_FONT_FAMILY,
    seed: args.seed,
    color: (token: string) => resolveColor(token, palette),
    fs: (size: number) => size * scale,
  };
};

const FxCtxContext = React.createContext<FxCtx>(
  makeFxCtx({
    fps: 30,
    width: 1920,
    height: 1080,
    theme: { palette: ['#dc1428', '#f5f5f5', '#08080a'], fontFamily: '', monoFamily: '' },
    seed: 1,
  }),
);

export const FxCtxProvider = FxCtxContext.Provider;
export const useFxCtx = (): FxCtx => React.useContext(FxCtxContext);

/** イベント固有の乱数の種。props の seed と並び順と開始秒から決まる。 */
export const eventSeed = (ev: FxEvent, index: number, globalSeed: number): number =>
  ev.seed ?? (globalSeed * 1000003 + index * 7919 + Math.round(ev.t * 1000)) >>> 0;

/** until / duration が書かれていないときの、型ごとの既定尺(秒)。 */
export const defaultEventSeconds = (ev: FxEvent, fps: number): number => {
  switch (ev.type) {
    case 'card':
      return (Math.max(1, ev.sequence.length) * ev.frames) / fps;
    case 'invertShake':
      return ev.frames / fps + ev.shakeTail;
    case 'imageSlam':
      return 1.5;
    case 'terminalText': {
      const stages = ev.then.length > 0 ? 2 : 1;
      const held = (stages * ev.frames) / fps;
      if (!ev.typing) {
        return held;
      }
      const longest = ev.lines.reduce((max, line) => Math.max(max, line.length), 0);
      return Math.max(held, longest / ev.cps + 0.6);
    }
    case 'screen':
      return 1.0;
    case 'glitchCut':
      return ev.frames / fps;
    case 'collapse':
      return ev.fallSeconds;
    case 'crtOff':
      return ev.frames / fps;
    case 'sprite':
      return 2.0;
    case 'stickerStack': {
      const last = ev.target.keyframes.reduce((max, k) => Math.max(max, k.t), ev.t);
      const blow = ev.blowOutAt !== undefined ? ev.blowOutAt + ev.blowOutSeconds : 0;
      return Math.max(0.5, Math.max(last, blow) - ev.t);
    }
    case 'credits':
      return 3.0;
    case 'lyric': {
      const last = ev.chars.reduce((max, c) => Math.max(max, c.s), ev.t);
      return Math.max(2.0, last - ev.t + 0.4);
    }
    case 'endCard':
      return ev.black + (ev.logo?.duration ?? 1.5);
    case 'beatMarker':
      return ev.beat * ev.count;
    case 'shape':
      return 1.0;
    default:
      return 1.0;
  }
};

export type FxSpan = {
  /** 開始フレーム。 */
  from: number;
  /** 長さ(フレーム)。1 未満にはしない。 */
  durationInFrames: number;
  /** 終了秒。 */
  endSeconds: number;
};

/** until > duration > 型ごとの既定、の順で尺を決める。 */
export const eventSpan = (ev: FxEvent, fps: number): FxSpan => {
  const seconds =
    ev.until !== undefined
      ? Math.max(0, ev.until - ev.t)
      : ev.duration !== undefined
        ? ev.duration
        : defaultEventSeconds(ev, fps);
  const from = toFrames(ev.t, fps);
  const durationInFrames = Math.max(1, toFrames(ev.t + seconds, fps) - from);
  return { from, durationInFrames, endSeconds: ev.t + seconds };
};

/** events 全体の終端(秒)。 */
export const eventsEndSeconds = (events: readonly FxEvent[], fps: number): number =>
  events.reduce((max, ev) => Math.max(max, eventSpan(ev, fps).endSeconds), 0);

/** anchor を画面比の中心座標に直す。w / h は表示サイズの画面比。 */
export const anchorCenter = (
  anchor: FxAnchor,
  wRatio: number,
  hRatio: number,
  margin: number,
): { cx: number; cy: number } => {
  const left = margin + wRatio / 2;
  const right = 1 - margin - wRatio / 2;
  const top = margin + hRatio / 2;
  const bottom = 1 - margin - hRatio / 2;
  const map: Record<FxAnchor, [number, number]> = {
    topLeft: [left, top],
    top: [0.5, top],
    topRight: [right, top],
    left: [left, 0.5],
    center: [0.5, 0.5],
    right: [right, 0.5],
    bottomLeft: [left, bottom],
    bottom: [0.5, bottom],
    bottomRight: [right, bottom],
  };
  const [cx, cy] = map[anchor];
  return { cx, cy };
};

/** corner をテキストブロックの寄せ方(CSS)に直す。 */
export const cornerStyle = (corner: FxCorner, marginPx: number): React.CSSProperties => {
  switch (corner) {
    case 'topLeft':
      return { top: marginPx, left: marginPx, textAlign: 'left' };
    case 'topRight':
      return { top: marginPx, right: marginPx, textAlign: 'right' };
    case 'bottomLeft':
      return { bottom: marginPx, left: marginPx, textAlign: 'left' };
    case 'bottomRight':
      return { bottom: marginPx, right: marginPx, textAlign: 'right' };
    case 'center':
    default:
      return {
        top: '50%',
        left: '50%',
        transform: 'translate(-50%, -50%)',
        textAlign: 'center',
      };
  }
};

/** 縁取りを text-shadow で作る(8 方向)。 */
export const outlineShadow = (color: string, px: number): string | undefined => {
  if (!color || color === 'transparent' || px <= 0) {
    return undefined;
  }
  const o = Math.max(1, Math.round(px));
  return [
    [o, o],
    [-o, o],
    [o, -o],
    [-o, -o],
    [0, o],
    [0, -o],
    [o, 0],
    [-o, 0],
  ]
    .map(([x, y]) => `${x}px ${y}px 0 ${color}`)
    .join(', ');
};

/** motion 一種ぶんの transform。u は 0..1 の進行、fi は開始からのフレーム数。 */
export const motionTransform = (
  motion: string,
  fi: number,
  fps: number,
  amountPx: number,
  seedRandom: () => number,
): { transform: string; scale: number } => {
  const t = fi / fps;
  switch (motion) {
    case 'pop': {
      // 3 フレームで 1.4 倍から着地
      const k = Math.min(1, fi / 3);
      return { transform: '', scale: 1.4 - 0.4 * k };
    }
    case 'stamp': {
      // 押印。1f で大きく、2f でやや大きく、その後は減衰しながら揺れて止まる
      const scale =
        fi < 1 ? 1.32 : fi < 2 ? 1.12 : 1 + 0.05 * Math.exp(-(t - 2 / fps) * 9) * Math.cos((t - 2 / fps) * 30);
      return { transform: '', scale };
    }
    case 'float': {
      const dy = Math.sin(t * 2 * Math.PI * 0.6) * amountPx;
      return { transform: `translateY(${dy}px)`, scale: 1 };
    }
    case 'spin':
      return { transform: `rotate(${t * 180}deg)`, scale: 1 };
    case 'shake': {
      const dx = (seedRandom() - 0.5) * 2 * amountPx;
      const dy = (seedRandom() - 0.5) * 2 * amountPx;
      return { transform: `translate(${dx}px, ${dy}px)`, scale: 1 };
    }
    case 'none':
    default:
      return { transform: '', scale: 1 };
  }
};
