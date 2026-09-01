import React from 'react';
import { Img } from 'remotion';
import { resolveSource } from '../media';

/**
 * 透過画像を「中心・幅・高さ上限」で貼る。FxOverlay のスプライト系はすべてこれを通す。
 *
 * - 画像は必ず Remotion の <Img>。素の <img> だと読み込みを待たないので、
 *   その素材が初めて出るフレームだけ抜ける。
 * - 幅 width / 高さ上限 maxHeight の箱に objectFit: contain で収めるので、
 *   縦長の素材でも画面からはみ出さず、比も崩れない。
 * - color: / gradient: の疑似素材を渡したときは色面として塗る(素材が無くても組める)。
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
}> = ({ src, cx, cy, width, maxHeight, rot = 0, scale = 1, opacity = 1, whiten = 0 }) => {
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
  if (source.kind === 'color') {
    return <div style={{ ...box, background: source.css }} />;
  }
  const imgStyle: React.CSSProperties = {
    position: 'absolute',
    inset: 0,
    width: '100%',
    height: '100%',
    objectFit: 'contain',
    display: 'block',
  };
  return (
    <div style={box}>
      <Img src={source.url} style={imgStyle} />
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
