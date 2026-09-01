import React from 'react';
import { Img } from 'remotion';
import { InsetBorderDefs, insetBorderId } from '../fx/filters';
import { resolveSource } from '../media';

/**
 * 透過画像を「中心・幅・高さ上限」で貼る。FxOverlay のスプライト系はすべてこれを通す。
 *
 * - 画像は必ず Remotion の <Img>。素の <img> だと読み込みを待たないので、
 *   その素材が初めて出るフレームだけ抜ける。
 * - 幅 width / 高さ上限 maxHeight の箱に objectFit: contain で収めるので、
 *   縦長の素材でも画面からはみ出さず、比も崩れない。
 * - color: / gradient: の疑似素材を渡したときは色面として塗る(素材が無くても組める)。
 * - tint / halftone は画像のアルファをマスクにして重ねるので、透過 PNG の
 *   不透明部分だけが塗られる(箱いっぱいには広がらない)。
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
  const dots =
    halftone && halftone.alpha > 0
      ? {
          position: 'absolute' as const,
          inset: 0,
          backgroundImage: `radial-gradient(circle at 50% 50%, ${halftone.color} 0 ${
            halftone.radius
          }px, transparent ${halftone.radius + 0.5}px)`,
          backgroundSize: `${halftone.dot}px ${halftone.dot}px`,
          opacity: Math.min(1, halftone.alpha),
        }
      : null;

  if (source.kind === 'color') {
    // 色面は箱そのものが絵なので、枠は箱の外周に引く。
    const outline =
      border && border.width > 0
        ? { outline: `${border.width}px solid ${border.color}`, outlineOffset: -border.width }
        : null;
    return (
      <div style={{ ...box, ...outline, background: tint ?? source.css }}>
        {dots ? <div style={dots} /> : null}
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
  // 画像のアルファをマスクに使う(objectFit: contain と同じ収め方に揃える)。
  const maskStyle: React.CSSProperties = {
    position: 'absolute',
    inset: 0,
    WebkitMaskImage: `url(${source.url})`,
    maskImage: `url(${source.url})`,
    WebkitMaskSize: 'contain',
    maskSize: 'contain',
    WebkitMaskPosition: 'center',
    maskPosition: 'center',
    WebkitMaskRepeat: 'no-repeat',
    maskRepeat: 'no-repeat',
  };
  return (
    <div style={box}>
      <Img src={source.url} style={{ ...imgStyle, filter: borderFilter }} />
      {insetBorder ? (
        // 輪郭の内側の罫線。同じ画像をもう 1 枚重ね、縁のリングだけを残す。
        <>
          <InsetBorderDefs width={insetBorder.width} color={insetBorder.color} />
          <Img
            src={source.url}
            style={{
              ...imgStyle,
              filter: `url(#${insetBorderId(insetBorder.width, insetBorder.color)})`,
            }}
          />
        </>
      ) : null}
      {tint ? <div style={{ ...maskStyle, backgroundColor: tint }} /> : null}
      {dots ? <div style={{ ...maskStyle, ...dots }} /> : null}
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
