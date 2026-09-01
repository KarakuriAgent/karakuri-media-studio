import React, { useEffect, useState } from 'react';
import { Img, continueRender, delayRender } from 'remotion';
import {
  AlphaWhiteFilter,
  InsetBorderFilter,
  SilhouetteFilter,
  alphaWhiteId,
  insetBorderId,
  silhouetteId,
} from '../fx/filters';
import { resolveSource } from '../media';

/**
 * 透過画像を「中心・幅・高さ上限」で貼る。FxOverlay のスプライト系はすべてこれを通す。
 *
 * - 画像は必ず Remotion の <Img>。素の <img> だと読み込みを待たないので、
 *   その素材が初めて出るフレームだけ抜ける。
 * - 幅 width / 高さ上限 maxHeight の箱に objectFit: contain で収めるので、
 *   縦長の素材でも画面からはみ出さず、比も崩れない。
 * - color: / gradient: の疑似素材を渡したときは色面として塗る(素材が無くても組める)。
 * - tint / halftone / border.inset は画像のアルファに沿って重ねるので、透過 PNG の
 *   不透明部分だけが塗られる(箱いっぱいには広がらない)。
 *   これらは CSS の mask-image ではなく SVG で描く。Chromium は CSS マスクの画像を
 *   no-cors で取りにいくため、別オリジン(スタジオ配信の http://host:port/...)だと
 *   不透明レスポンスになってレイヤーが丸ごと消えるため。
 */
export const FxSprite: React.FC<{
  src: string;
  /** 中心(px) */
  cx: number;
  cy: number;
  /** 表示幅(px) */
  width: number;
  /** 高さの上限(px) */
  maxHeight: number;
  /** 傾き(度、反時計回りが正) */
  rot?: number;
  /** 追加の拡大率 */
  scale?: number;
  opacity?: number;
  /** 0..1。アルファを保ったまま白へ寄せる(叩き込みの一瞬のフラッシュ)。 */
  whiten?: number;
  /** 不透明部分をこの色 1 色で塗る(白抜きロゴの色替え)。 */
  tint?: string;
  /**
   * 画像の輪郭(アルファ)に沿って付ける枠。width は px。色面のときは箱の内側。
   * inset を立てると、画像でも輪郭の内側に引く(色面の見た目と揃う)。
   */
  border?: { color: string; width: number; inset?: boolean };
  /** 不透明部分に敷くハーフトーン(濃さ・点の色・点の間隔 px・点の半径 px)。 */
  halftone?: { alpha: number; color: string; dot: number; radius: number };
}> = ({
  src,
  cx,
  cy,
  width,
  maxHeight,
  rot = 0,
  scale = 1,
  opacity = 1,
  whiten = 0,
  tint,
  border,
  halftone,
}) => {
  const source = resolveSource(src);
  // SVG の <image> は <Img> と違って読み込みを待たないので、同じ URL を先に読んでおく。
  // 色面(color: / gradient:)のときは待つものが無い。
  const overlayReady = useImageReady(source.kind === 'color' ? null : source.url);

  const transforms = [`translate(-50%, -50%)`];
  if (rot) {
    // CSS の rotate は時計回りが正なので符号を反転する。
    transforms.push(`rotate(${-rot}deg)`);
  }
  if (scale !== 1) {
    transforms.push(`scale(${scale})`);
  }
  const box: React.CSSProperties = {
    position: 'absolute',
    left: cx,
    top: cy,
    width,
    height: maxHeight,
    transform: transforms.join(' '),
    opacity,
  };
  // inset なら輪郭の内側(SVG フィルタで縁を削り出す)、そうでなければ外側に付ける。
  const insetBorder = border && border.width > 0 && border.inset === true ? border : undefined;
  // 枠は画像のアルファの外側に付ける(箱の外周ではないので、透過 PNG でも輪郭に沿う)。
  const borderFilter =
    border && border.width > 0 && !insetBorder
      ? [
          [1, 0],
          [-1, 0],
          [0, 1],
          [0, -1],
          [0.7, 0.7],
          [-0.7, 0.7],
          [0.7, -0.7],
          [-0.7, -0.7],
        ]
          .map(
            ([x, y]) =>
              `drop-shadow(${(x * border.width).toFixed(2)}px ${(y * border.width).toFixed(2)}px 0 ${
                border.color
              })`,
          )
          .join(' ')
      : undefined;
  // ハーフトーンの点。濃さと点の大きさは呼び出し側で解決済み(lib/fx の spriteHalftone)。
  const dots = halftone && halftone.alpha > 0 ? halftone : undefined;

  if (source.kind === 'color') {
    // 色面は箱そのものが絵なので、枠は箱の外周に引く。点も箱いっぱいに敷く。
    const outline =
      border && border.width > 0
        ? { outline: `${border.width}px solid ${border.color}`, outlineOffset: -border.width }
        : null;
    return (
      <div style={{ ...box, ...outline, background: tint ?? source.css }}>
        {dots ? (
          <div
            style={{
              position: 'absolute',
              inset: 0,
              backgroundImage: `radial-gradient(circle at 50% 50%, ${dots.color} 0 ${dots.radius}px, transparent ${dots.radius + 0.5}px)`,
              backgroundSize: `${dots.dot}px ${dots.dot}px`,
              opacity: Math.min(1, dots.alpha),
            }}
          />
        ) : null}
      </div>
    );
  }

  const imgStyle: React.CSSProperties = {
    position: 'absolute',
    inset: 0,
    width: '100%',
    height: '100%',
    objectFit: 'contain',
    display: 'block',
  };
  // SVG の <image> を objectFit: contain と同じ収め方に揃える。
  const imageProps = {
    href: source.url,
    x: 0,
    y: 0,
    width,
    height: maxHeight,
    preserveAspectRatio: 'xMidYMid meet',
  };
  // 点は箱の左上を原点に敷く(CSS の backgroundSize と同じ並び)。
  // id は内容から作るので、同じ見た目のスプライトが複数あっても定義を共有できる。
  const dotsId = dots
    ? `fx-dots-${Math.round(dots.dot * 10)}-${Math.round(dots.radius * 10)}-${dots.color.replace(/[^a-zA-Z0-9]/g, '')}`
    : '';
  const maskId = dots ? `fx-alpha-${hashKey(`${source.url}|${width}|${maxHeight}`)}` : '';
  const hasOverlay = Boolean(tint || dots || insetBorder);

  return (
    <div style={box}>
      <Img src={source.url} style={{ ...imgStyle, filter: borderFilter }} />
      {hasOverlay && overlayReady ? (
        <svg
          width={width}
          height={maxHeight}
          style={{ position: 'absolute', left: 0, top: 0, width: '100%', height: '100%' }}
          aria-hidden
        >
          <defs>
            {insetBorder ? (
              <InsetBorderFilter width={insetBorder.width} color={insetBorder.color} />
            ) : null}
            {tint ? <SilhouetteFilter color={tint} /> : null}
            {dots ? (
              <>
                <AlphaWhiteFilter />
                {/* 画像のアルファをそのまま濃度に持つマスク(白 + 元のアルファ → 輝度マスク)。 */}
                <mask id={maskId}>
                  <image {...imageProps} filter={`url(#${alphaWhiteId})`} />
                </mask>
                <pattern
                  id={dotsId}
                  x={0}
                  y={0}
                  width={dots.dot}
                  height={dots.dot}
                  patternUnits="userSpaceOnUse"
                >
                  <circle
                    cx={dots.dot / 2}
                    cy={dots.dot / 2}
                    r={dots.radius}
                    fill={dots.color}
                  />
                </pattern>
              </>
            ) : null}
          </defs>
          {insetBorder ? (
            // 輪郭の内側の罫線。同じ画像をもう 1 枚重ね、縁のリングだけを残す。
            <image
              {...imageProps}
              filter={`url(#${insetBorderId(insetBorder.width, insetBorder.color)})`}
            />
          ) : null}
          {tint ? <image {...imageProps} filter={`url(#${silhouetteId(tint)})`} /> : null}
          {dots ? (
            <rect
              x={0}
              y={0}
              width={width}
              height={maxHeight}
              fill={`url(#${dotsId})`}
              opacity={Math.min(1, dots.alpha)}
              mask={`url(#${maskId})`}
            />
          ) : null}
        </svg>
      ) : null}
      {whiten > 0 ? (
        // アルファを保ったまま真っ白にしたコピーを重ねる。
        <Img
          src={source.url}
          style={{ ...imgStyle, filter: 'brightness(0) invert(1)', opacity: whiten }}
        />
      ) : null}
    </div>
  );
};

/** 文字列から短い英数字のキーを作る(SVG の id 用)。同じ入力なら毎フレーム同じ id になる。 */
const hashKey = (s: string): string => {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) {
    h = Math.imul(h ^ s.charCodeAt(i), 16777619);
  }
  return (h >>> 0).toString(36);
};

/**
 * SVG の <image> は Remotion の <Img> と違って読み込みを待たないので、
 * 同じ URL を先に読み込み、読めるまで delayRender でレンダを止める。
 * これをしないと、その素材が初めて出るフレームだけ tint / halftone / 罫線が抜ける。
 *
 * url が null(色面)なら何も待たない。読めなかったときも render は進める
 * (素材そのものの読み込み失敗は <Img> 側が報告する)。
 */
const useImageReady = (url: string | null): boolean => {
  const [readyUrl, setReadyUrl] = useState<string | null>(null);
  useEffect(() => {
    if (url === null || readyUrl === url) {
      return;
    }
    const handle = delayRender(`FxSprite: ${url} の読み込み待ち`);
    let done = false;
    const finish = () => {
      if (!done) {
        done = true;
        continueRender(handle);
      }
    };
    const img = new Image();
    img.onload = () => {
      setReadyUrl(url);
      finish();
    };
    img.onerror = finish;
    img.src = url;
    return finish;
  }, [url, readyUrl]);
  return url !== null && readyUrl === url;
};
