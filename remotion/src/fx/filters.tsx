// グリッチ系・スプライト系で使う SVG フィルタ。
// CSS の filter: url(#id) からも、SVG の <image filter="url(#id)"> からも参照する。
//
// - chroma: RGB 分離(赤を右・青を左へずらして screen 合成)
// - displace: feTurbulence を横方向だけの変位に潰した「走査線ずれ」
// - insetBorder: 画像のアルファを削って、輪郭の内側に残る縁(罫線)だけを取り出す
// - alphaWhite: 画像を「元のアルファを保った白」にする(SVG の輝度マスク用)
//
// chroma と displace を重ねた「出際に飛ばして消す」ラッパー(FxOutGlitch)もここに置く。
//
// id は内容(量・seed)から作る。フレームごとに seed が変われば id も変わるので、
// 前のフレームの定義を掴んだままになることがない。

import React from 'react';
import { useCurrentFrame, useVideoConfig } from 'remotion';
import { chromaPixels, useFxCtx } from '../lib/fx';
import { rng } from '../lib/rng';
import type { FxOutGlitch as OutGlitchSpec } from '../schema';

export const chromaId = (amount: number) => `fx-chroma-${Math.round(amount * 10)}`;
export const displaceId = (scale: number, seed: number) => `fx-disp-${Math.round(scale)}-${seed}`;

/** RGB 分離。amount px だけ R を右・B を左へ。 */
export const ChromaDefs: React.FC<{ amount: number }> = ({ amount }) => (
  <svg width={0} height={0} style={{ position: 'absolute' }} aria-hidden>
    <defs>
      <filter id={chromaId(amount)} x="-5%" y="-5%" width="110%" height="110%">
        <feColorMatrix
          in="SourceGraphic"
          type="matrix"
          values="1 0 0 0 0  0 0 0 0 0  0 0 0 0 0  0 0 0 1 0"
          result="r"
        />
        <feColorMatrix
          in="SourceGraphic"
          type="matrix"
          values="0 0 0 0 0  0 1 0 0 0  0 0 0 0 0  0 0 0 1 0"
          result="g"
        />
        <feColorMatrix
          in="SourceGraphic"
          type="matrix"
          values="0 0 0 0 0  0 0 0 0 0  0 0 1 0 0  0 0 0 1 0"
          result="b"
        />
        <feOffset in="r" dx={amount} dy={0} result="ro" />
        <feOffset in="b" dx={-amount} dy={Math.round(amount / 3)} result="bo" />
        <feBlend in="ro" in2="g" mode="screen" result="rg" />
        <feBlend in="rg" in2="bo" mode="screen" />
      </filter>
    </defs>
  </svg>
);

/**
 * 横方向だけの変位(走査線ずれ)。
 * feTurbulence の G を 0.5 に固定して、feDisplacementMap の Y 成分を殺している。
 */
export const DisplaceDefs: React.FC<{ scale: number; seed: number }> = ({ scale, seed }) => (
  <svg width={0} height={0} style={{ position: 'absolute' }} aria-hidden>
    <defs>
      <filter id={displaceId(scale, seed)} x="-10%" y="0%" width="120%" height="100%">
        <feTurbulence
          type="fractalNoise"
          baseFrequency="0.00012 0.45"
          numOctaves={1}
          seed={seed}
          result="noise"
        />
        <feColorMatrix
          in="noise"
          type="matrix"
          values="1 0 0 0 0  0 0 0 0 0.5  0 0 0 0 0  0 0 0 0 1"
          result="nx"
        />
        <feDisplacementMap
          in="SourceGraphic"
          in2="nx"
          scale={scale}
          xChannelSelector="R"
          yChannelSelector="G"
        />
      </filter>
    </defs>
  </svg>
);

/** 内側罫線のフィルタ id。太さと色が同じなら使い回せる。 */
export const insetBorderId = (width: number, color: string) =>
  `fx-inset-${Math.round(width * 10)}-${color.replace(/[^a-zA-Z0-9]/g, '')}`;

/**
 * 輪郭の内側の罫線。
 * アルファを width だけ削った芯を作り、元のシルエットから芯を抜いてリングだけ残す。
 * 元絵に重ねて使う(元絵そのものは触らない)。
 *
 * <filter> 単体を返すので、呼び出し側の <defs> の中に置くこと。
 */
export const InsetBorderFilter: React.FC<{ width: number; color: string }> = ({ width, color }) => (
  <filter id={insetBorderId(width, color)} x="0%" y="0%" width="100%" height="100%">
    <feFlood floodColor={color} result="col" />
    <feComposite in="col" in2="SourceAlpha" operator="in" result="sil" />
    <feMorphology in="SourceAlpha" operator="erode" radius={width} result="core" />
    <feComposite in="sil" in2="core" operator="out" />
  </filter>
);

/** シルエット塗りのフィルタ id。色が同じなら使い回せる。 */
export const silhouetteId = (color: string) => `fx-sil-${color.replace(/[^a-zA-Z0-9]/g, '')}`;

/**
 * 画像のアルファを 1 色で塗りつぶす(tint)。
 * CSS の mask-image は Chromium が no-cors で取りにいくので、別オリジンの画像だと
 * 不透明レスポンスになって何も描かれない。アルファはフィルタ側で読む。
 *
 * <filter> 単体を返すので、呼び出し側の <defs> の中に置くこと。
 */
export const SilhouetteFilter: React.FC<{ color: string }> = ({ color }) => (
  <filter id={silhouetteId(color)} x="0%" y="0%" width="100%" height="100%">
    <feFlood floodColor={color} result="col" />
    <feComposite in="col" in2="SourceAlpha" operator="in" />
  </filter>
);

/** 「アルファを保った白」フィルタの id。 */
export const alphaWhiteId = 'fx-alpha-white';

/**
 * 画像を、元のアルファを保ったまま真っ白にする。
 * SVG の <mask> は既定で輝度マスクなので、これを通した画像を入れると
 * マスクの濃度 = 元画像のアルファになる(ハーフトーンの点を輪郭内に収めるのに使う)。
 *
 * <filter> 単体を返すので、呼び出し側の <defs> の中に置くこと。
 */
export const AlphaWhiteFilter: React.FC = () => (
  <filter id={alphaWhiteId} x="0%" y="0%" width="100%" height="100%">
    <feColorMatrix
      in="SourceGraphic"
      type="matrix"
      values="0 0 0 0 1  0 0 0 0 1  0 0 0 0 1  0 0 0 1 0"
    />
  </filter>
);

/**
 * 出際の数フレームだけ、走査線ずれ + RGB 分離で children を飛ばして消す。
 * BAN!BAN!BAN! の breakCarryLyric(黒画面に残した歌詞を 2f のグリッチで消す)と同じ消し方。
 *
 * out が無ければ children をそのまま返す(既定は無効)。イベントの Sequence の中で使う。
 */
export const FxOutGlitch: React.FC<{
  out?: OutGlitchSpec;
  seed: number;
  children: React.ReactNode;
}> = ({ out, seed, children }) => {
  const frame = useCurrentFrame();
  const { durationInFrames } = useVideoConfig();
  const ctx = useFxCtx();
  // 出際からのフレーム数。0 未満(まだ出際ではない)なら素通し。
  const gi = frame - (durationInFrames - (out?.frames ?? 0));
  if (!out || gi < 0) {
    return <>{children}</>;
  }
  // 1f 目が荒れて、最後の 1f でほぼ飛ぶ
  const k = 1 - gi / out.frames;
  const disp = Math.max(2, Math.round(out.displace * ctx.scale * (1.2 - k)));
  const chroma = Math.max(1, Math.round(chromaPixels(out.chroma, ctx.scale) * (1.2 - k)));
  const r = rng(seed + gi * 3313 + 11);
  const shake = 14 * ctx.scale;
  return (
    <>
      <DisplaceDefs scale={disp} seed={gi + 41} />
      <ChromaDefs amount={chroma} />
      <div
        style={{
          position: 'absolute',
          inset: 0,
          opacity: 0.9 * k,
          filter: `url(#${displaceId(disp, gi + 41)}) url(#${chromaId(chroma)})`,
          transform: `translate(${Math.round(r.range(-shake, shake))}px, ${Math.round(
            r.range(-shake / 4, shake / 4),
          )}px)`,
        }}
      >
        {children}
      </div>
    </>
  );
};
