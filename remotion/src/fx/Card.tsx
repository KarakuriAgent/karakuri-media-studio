// card: 全画面の色地に極太文字を数フレームだけ叩き込む。決めの起点になるカード。
//
// sequence に "背景色/文字色" を並べたぶんだけ、frames フレームごとに色が切り替わる。
// 傾き・横ずれは seed から決まる(props が同じなら毎回同じ絵)。フェードは掛けない。
//
// wipe を書くと、斜めのカラーワイプが画面を渡り、通り過ぎたところだけ
// 文字と斜線が wipe.color に置き換わる。chroma を書くと端だけ RGB 分離する。
// どちらも省略時は無効で、従来どおりの「色地 + 文字」。

import React from 'react';
import { AbsoluteFill, useCurrentFrame } from 'remotion';
import { HALFTONE_RADIUS_RATIO, chromaPixels, useFxCtx } from '../lib/fx';
import { rng } from '../lib/rng';
import { ChromaDefs, chromaId } from './filters';
import type { FxEventOf } from '../schema';

// 従来の(halftone: true の)見た目。点は薄く粗い。
const HALFTONE_ALPHA = 0.13;
const HALFTONE_DOT = 26;

/**
 * halftone の指定を、実際に描く濃さ・点の間隔 px・点の半径 px に直す。
 * true / false は従来どおり、{alpha, dot} は書かれたとおり(dot は 1080p 基準 px)。
 */
const cardHalftone = (
  value: FxEventOf<'card'>['halftone'],
  fs: (size: number) => number,
): { alpha: number; dot: number; radius: number } | null => {
  if (typeof value === 'boolean') {
    if (!value) {
      return null;
    }
    const dot = Math.max(6, fs(HALFTONE_DOT));
    return { alpha: HALFTONE_ALPHA, dot, radius: dot * 0.1 };
  }
  if (value.alpha <= 0) {
    return null;
  }
  const dot = Math.max(2, fs(value.dot));
  return { alpha: value.alpha, dot, radius: dot * HALFTONE_RADIUS_RATIO };
};

/** カードの中身(背景色は外側が塗る)。クロマ収差用に 2 回描くので id を分ける。 */
const CardBody: React.FC<{
  ev: FxEventOf<'card'>;
  fg: string;
  cx: number;
  cy: number;
  rot: number;
  /** ワイプの進み(0..1)。ワイプが無ければ 0。 */
  p: number;
  wipeColor: string;
  idp: string;
  /** 背景色。クロマ収差用の重ね描きでだけ塗る(下の絵を完全に置き換えるため)。 */
  bg?: string;
}> = ({ ev, fg, cx, cy, rot, p, wipeColor, idp, bg }) => {
  const ctx = useFxCtx();
  const { width, height } = ctx;
  const halftone = cardHalftone(ev.halftone, ctx.fs);
  const stripe = Math.max(6, ctx.fs(22));
  const angle = ev.wipe?.angle ?? 0;
  // 回転した座標系で画面を渡りきる幅。足りないとワイプが途中で終わる。
  const wx0 = -0.2 * width;
  const wlen = 1.42 * width;
  const edge = Math.max(2, ctx.fs(5));

  const text = (fill: string) =>
    ev.text ? (
      <text
        x={cx}
        y={cy}
        // SVG の rotate は時計回りが正なので符号を反転する
        transform={`rotate(${-rot} ${cx} ${cy})`}
        textAnchor="middle"
        dominantBaseline="central"
        fontFamily={ctx.fontFamily}
        fontSize={ctx.fs(ev.fontSize)}
        fontWeight={900}
        fill={fill}
        xmlSpace="preserve"
      >
        {ev.text}
      </text>
    ) : null;

  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      style={{ position: 'absolute', inset: 0 }}
    >
      <defs>
        {halftone ? (
          <pattern
            id={`${idp}-dot`}
            width={halftone.dot}
            height={halftone.dot}
            patternUnits="userSpaceOnUse"
          >
            <circle
              cx={halftone.dot / 2}
              cy={halftone.dot / 2}
              r={halftone.radius}
              fill={fg}
            />
          </pattern>
        ) : null}
        {ev.wipe ? (
          <>
            <pattern
              id={`${idp}-stripe`}
              width={stripe}
              height={stripe}
              patternUnits="userSpaceOnUse"
              patternTransform={`rotate(${angle})`}
            >
              <rect width={stripe / 2} height={stripe} fill={wipeColor} />
            </pattern>
            <clipPath id={`${idp}-wipe`}>
              {/* clipPath の子に <g> は使えないので、rect に直接 transform を掛ける */}
              <rect
                x={wx0}
                y={-height}
                width={p * wlen}
                height={3 * height}
                transform={`rotate(${angle} ${width / 2} ${height / 2})`}
              />
            </clipPath>
          </>
        ) : null}
      </defs>
      {bg ? <rect x={0} y={0} width={width} height={height} fill={bg} /> : null}
      {halftone ? (
        <rect
          x={0}
          y={0}
          width={width}
          height={height}
          fill={`url(#${idp}-dot)`}
          opacity={halftone.alpha}
        />
      ) : null}
      {ev.wipe ? (
        <g clipPath={`url(#${idp}-wipe)`}>
          <rect
            x={0}
            y={0}
            width={width}
            height={height}
            fill={`url(#${idp}-stripe)`}
            opacity={HALFTONE_ALPHA}
          />
        </g>
      ) : null}
      {text(fg)}
      {ev.wipe ? (
        <>
          <g clipPath={`url(#${idp}-wipe)`}>{text(wipeColor)}</g>
          {/* ワイプの境界線 */}
          <g transform={`rotate(${angle} ${width / 2} ${height / 2})`}>
            <rect
              x={wx0 + p * wlen - edge}
              y={-height}
              width={edge}
              height={3 * height}
              fill={wipeColor}
              opacity={0.9}
            />
          </g>
        </>
      ) : null}
    </svg>
  );
};

export const FxCard: React.FC<{ ev: FxEventOf<'card'>; seed: number }> = ({ ev, seed }) => {
  const frame = useCurrentFrame();
  const ctx = useFxCtx();
  const { width, height } = ctx;

  const sequence = ev.sequence.length ? ev.sequence : ['accent/fg'];
  const index = Math.min(sequence.length - 1, Math.floor(frame / ev.frames));
  const [bgToken, fgToken] = sequence[index].split('/');
  const bg = ctx.color((bgToken ?? 'accent').trim());
  const fg = ctx.color((fgToken ?? 'fg').trim());

  // 1 枚ごとに振り直す。同じ色が続いても「別のカード」に見えるのはこの振れのおかげ。
  const r = rng(seed + index * 7919);
  const rot = ev.jitterDeg ? r.range(-ev.jitterDeg, ev.jitterDeg) : 0;
  const dx = ev.jitterPx ? r.range(-ev.jitterPx, ev.jitterPx) * width : 0;

  const cx = width / 2 + dx;
  const cy = height / 2;
  // ワイプはカードの先頭から数える(1 枚目の途中で終わらないよう +1 して進める)。
  const p = ev.wipe ? Math.min(1, (frame + 1) / ev.wipe.frames) : 0;
  const wipeColor = ctx.color(ev.wipe?.color ?? 'accent');
  const chroma = chromaPixels(ev.chroma, ctx.scale);
  const idp = `fx-card-${seed}-${index}`;
  const body = (suffix: string, bgFill?: string) => (
    <CardBody
      ev={ev}
      fg={fg}
      cx={cx}
      cy={cy}
      rot={rot}
      p={p}
      wipeColor={wipeColor}
      idp={idp + suffix}
      bg={bgFill}
    />
  );

  if (chroma <= 0) {
    return (
      <AbsoluteFill style={{ background: bg, overflow: 'hidden' }}>{body('')}</AbsoluteFill>
    );
  }
  // 端(画面の外周)だけクロマ収差。同じ中身をフィルタ付きで重ね、中心を抜くマスクを掛ける。
  const edgeMask =
    'radial-gradient(ellipse 62% 62% at 50% 50%, rgba(0,0,0,0) 52%, rgba(0,0,0,1) 100%)';
  return (
    <AbsoluteFill style={{ background: bg, overflow: 'hidden' }}>
      <ChromaDefs amount={chroma} />
      {body('')}
      <div
        style={{
          position: 'absolute',
          inset: 0,
          filter: `url(#${chromaId(chroma)})`,
          WebkitMaskImage: edgeMask,
          maskImage: edgeMask,
        }}
      >
        {body('-e', bg)}
      </div>
    </AbsoluteFill>
  );
};
