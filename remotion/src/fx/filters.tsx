// グリッチ系で使う SVG フィルタ。CSS の filter: url(#id) から参照する。
//
// - chroma: RGB 分離(赤を右・青を左へずらして screen 合成)
// - displace: feTurbulence を横方向だけの変位に潰した「走査線ずれ」
//
// id は内容(量・seed)から作る。フレームごとに seed が変われば id も変わるので、
// 前のフレームの定義を掴んだままになることがない。

import React from 'react';

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
