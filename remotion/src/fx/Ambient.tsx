// ambient: 全編に薄く敷く走査線とビネット。
// 「掛けると画面が古びる」ので既定は両方 OFF。決めの効果を邪魔しない範囲でだけ使う。

import React from 'react';
import { useFxCtx } from '../lib/fx';
import type { FxAmbient } from '../schema';

export const FxAmbientLayer: React.FC<{ ambient: FxAmbient }> = ({ ambient }) => {
  const ctx = useFxCtx();
  if (!ambient.scanline && !ambient.vignette) {
    return null;
  }
  const period = Math.max(2, ctx.fs(ambient.scanlinePeriod));
  return (
    <>
      {ambient.scanline ? (
        <div
          style={{
            position: 'absolute',
            inset: 0,
            pointerEvents: 'none',
            background:
              'repeating-linear-gradient(to bottom,' +
              ` rgba(0,0,0,${ambient.scanlineAlpha}) 0px,` +
              ` rgba(0,0,0,${ambient.scanlineAlpha}) 1px,` +
              ' rgba(0,0,0,0) 1px,' +
              ` rgba(0,0,0,0) ${period}px)`,
          }}
        />
      ) : null}
      {ambient.vignette ? (
        <div
          style={{
            position: 'absolute',
            inset: 0,
            pointerEvents: 'none',
            background:
              'radial-gradient(ellipse 74% 74% at 50% 50%,' +
              ` rgba(0,0,0,0) 52%, rgba(0,0,0,${ambient.vignetteAlpha}) 100%)`,
          }}
        />
      ) : null}
    </>
  );
};
