// imageSlam: 決め台詞の画像を叩き込む。
//
// 配置は画面比(cx / cy / w / maxH)。BAN!BAN!BAN! で決まった作法は
// 「顔と楽器の手元を避けて画面下 1/3、できるだけ大きく(幅 45% 以上、cx は 0.28〜0.72)」。
// はみ出しはレンダリング後には気づけないので、コンソールに警告を出す。
// fitStart: true なら、叩き込みはじめの倍率(snap[0])を画面に収まる所まで自動で抑える
// (勢いのため fitStartMin までは残す。計算は lib/fit.ts の fitSlamStart)。

import React from 'react';
import { spring, useCurrentFrame, useVideoConfig } from 'remotion';
import { FxSprite } from '../components/FxSprite';
import { containSize, fitSlamStart } from '../lib/fit';
import { useFxCtx } from '../lib/fx';
import { useMediaAspect } from '../lib/mediaAspect';
import { resolveSource } from '../media';
import type { FxEventOf } from '../schema';

/** 同じイベントで毎フレーム警告を出さないための記録。 */
const warned = new Set<string>();

const warnIfOutOfScreen = (ev: FxEventOf<'imageSlam'>) => {
  const left = ev.cx - ev.w / 2;
  const right = ev.cx + ev.w / 2;
  const top = ev.cy - ev.maxH / 2;
  const bottom = ev.cy + ev.maxH / 2;
  const out = left < 0 || right > 1 || top < 0 || bottom > 1;
  if (!out) {
    return;
  }
  const key = `${ev.t}:${ev.src}`;
  if (warned.has(key)) {
    return;
  }
  warned.add(key);
  // eslint-disable-next-line no-console
  console.warn(
    `[FxOverlay] imageSlam t=${ev.t} の bbox が画面外に出ています ` +
      `(left=${left.toFixed(3)} right=${right.toFixed(3)} ` +
      `top=${top.toFixed(3)} bottom=${bottom.toFixed(3)})。` +
      ' cx / cy / w / maxH を見直してください。',
  );
};

export const FxImageSlam: React.FC<{ ev: FxEventOf<'imageSlam'> }> = ({ ev }) => {
  const frame = useCurrentFrame();
  const { fps, durationInFrames } = useVideoConfig();
  const ctx = useFxCtx();
  warnIfOutOfScreen(ev);

  const boxW = ctx.width * ev.w;
  const boxH = ctx.height * ev.maxH;
  // fitStart のときだけ素材の縦横比を読む(箱の中で実際に見える大きさで測る)
  const source = resolveSource(ev.src);
  const aspect = useMediaAspect(ev.fitStart && source.kind === 'image' ? source.url : null, 'image');
  const [, snapTo] = ev.snap;
  let snapFrom = ev.snap[0];
  if (ev.fitStart) {
    const drawn = containSize(boxW, boxH, aspect);
    snapFrom = fitSlamStart({
      screenW: ctx.width,
      screenH: ctx.height,
      cx: ctx.width * ev.cx,
      cy: ctx.height * ev.cy,
      drawnW: drawn.width,
      drawnH: drawn.height,
      rot: ev.rot,
      snap: ev.snap,
      min: ev.fitStartMin,
    });
  }
  const scale = ev.spring
    ? // spring は 1 を越えて振れるので、行き過ぎて数フレーム揺れて着地する
      snapFrom + (snapTo - snapFrom) * spring({ frame, fps, config: { damping: 12, mass: 0.6 } })
    : frame < ev.snapFrames
      ? snapFrom + (snapTo - snapFrom) * (frame / ev.snapFrames)
      : snapTo;

  // 引きぎわだけ少し縮める(次の絵に譲る)
  const outScale = frame >= durationInFrames - Math.max(1, Math.round(0.15 * fps)) ? ev.outScale : 1;

  return (
    <FxSprite
      src={ev.src}
      cx={ctx.width * ev.cx}
      cy={ctx.height * ev.cy}
      width={boxW}
      maxHeight={boxH}
      rot={ev.rot}
      scale={scale * outScale}
      whiten={frame === 0 ? ev.flash : 0}
      tint={ev.tint ? ctx.color(ev.tint) : undefined}
    />
  );
};
