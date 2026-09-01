import React from 'react';
import { AbsoluteFill, Img, OffthreadVideo, useVideoConfig } from 'remotion';
import { resolveSource } from '../media';
import type { FxBase } from '../schema';
import { ChromaDefs, DisplaceDefs, chromaId, displaceId } from '../fx/filters';

/** base の素材そのもの(動画 / 静止画 / 色面)。シェイクのタイル貼りから何度も呼ばれる。 */
export const FxBaseMedia: React.FC<{ base?: FxBase; backgroundColor: string }> = ({
  base,
  backgroundColor,
}) => {
  const { fps } = useVideoConfig();
  if (!base) {
    return <AbsoluteFill style={{ backgroundColor }} />;
  }
  const source = resolveSource(base.src);
  const style: React.CSSProperties = { width: '100%', height: '100%', objectFit: base.fit };
  if (source.kind === 'color') {
    return <AbsoluteFill style={{ background: source.css }} />;
  }
  if (source.kind === 'video') {
    return (
      <OffthreadVideo
        src={source.url}
        startFrom={Math.round(base.in * fps)}
        muted={base.muted}
        volume={base.muted ? 0 : base.volume}
        playbackRate={base.playbackRate}
        // 素材は SDR 前提。トーンマッピングを掛けると色がわずかにずれる。
        toneMapped={false}
        style={style}
      />
    );
  }
  return <Img src={source.url} style={style} />;
};

/** 画面を dx / dy ずらすとき、端に隙間を作らないための 4 枚タイルの原点。 */
const tileOffsets = (dx: number, dy: number, w: number, h: number): [number, number][] => {
  if (dx === 0 && dy === 0) {
    return [[0, 0]];
  }
  const x = ((dx % w) + w) % w;
  const y = ((dy % h) + h) % h;
  return [
    [x - w, y - h],
    [x, y - h],
    [x - w, y],
    [x, y],
  ];
};

/**
 * 演出を載せる下地。イベント(invertShake / glitchCut)から渡された「今フレームの荒れ具合」を
 * まとめて掛ける。既定値のままなら素材をそのまま流すだけ。
 */
export const FxBaseLayer: React.FC<{
  base?: FxBase;
  backgroundColor: string;
  /** シェイク量(px) */
  dx?: number;
  dy?: number;
  invert?: boolean;
  /** 白飛ばし(0..1) */
  flash?: number;
  /** RGB 分離の量(px) */
  chroma?: number;
  /** 走査線ずれ [変位 px, seed] */
  glitch?: [number, number] | null;
  scale?: number;
}> = ({
  base,
  backgroundColor,
  dx = 0,
  dy = 0,
  invert = false,
  flash = 0,
  chroma = 0,
  glitch = null,
  scale = 1,
}) => {
  const { width, height } = useVideoConfig();
  const filters: string[] = [];
  if (glitch && glitch[0] > 0) {
    filters.push(`url(#${displaceId(glitch[0], glitch[1])})`);
  }
  if (chroma > 0) {
    filters.push(`url(#${chromaId(chroma)})`);
  }
  if (invert) {
    filters.push('invert(1)');
  }
  return (
    <AbsoluteFill style={{ backgroundColor, overflow: 'hidden' }}>
      {chroma > 0 ? <ChromaDefs amount={chroma} /> : null}
      {glitch && glitch[0] > 0 ? <DisplaceDefs scale={glitch[0]} seed={glitch[1]} /> : null}
      <AbsoluteFill
        style={{
          overflow: 'hidden',
          filter: filters.length ? filters.join(' ') : undefined,
          transform: scale !== 1 ? `scale(${scale})` : undefined,
        }}
      >
        {tileOffsets(Math.round(dx), Math.round(dy), width, height).map(([x, y]) => (
          <div key={`${x},${y}`} style={{ position: 'absolute', left: x, top: y, width, height }}>
            <FxBaseMedia base={base} backgroundColor={backgroundColor} />
          </div>
        ))}
      </AbsoluteFill>
      {flash > 0 ? (
        <AbsoluteFill style={{ backgroundColor: '#ffffff', opacity: Math.min(1, flash) }} />
      ) : null}
    </AbsoluteFill>
  );
};
