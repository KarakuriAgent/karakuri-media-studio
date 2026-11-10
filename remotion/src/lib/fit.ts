// 画面に合わせて大きさを決める純関数(sprite / video の cover と、imageSlam の fitStart)。
//
// どれも px で受けて px で返す。描画(React・Remotion)には触らないので、そのままテストできる。
// 角度 rot は schema と同じ「度、反時計回りが正」。描画は CSS の rotate(-rot) で回すので、
// 画面の座標(y が下向き)では、箱の中のベクトル v は R(-rot)·v に写る。

/** 画面座標(y 下向き)で、CSS の rotate(deg) と同じ回し方をする。 */
const rotateCss = (x: number, y: number, deg: number): [number, number] => {
  const a = (deg * Math.PI) / 180;
  const c = Math.cos(a);
  const s = Math.sin(a);
  return [x * c - y * s, x * s + y * c];
};

export type CoverInput = {
  /** 画面の大きさ(px)。 */
  screenW: number;
  screenH: number;
  /** 画像の中の点(origin)を合わせる画面の位置(px)。 */
  x: number;
  y: number;
  /** 素材の縦横比(幅 / 高さ)。 */
  aspect: number;
  /** 画面の (x, y) に合わせる、画像の中の点(画像の幅・高さに対する比)。中心は [0.5, 0.5]。 */
  origin: readonly [number, number];
  /** 傾き(度、反時計回りが正)。 */
  rot: number;
  /** 余裕の倍率(1 でちょうど四隅に届く)。 */
  margin: number;
};

export type CoverBox = {
  /** 箱の中心(px)。FxSprite などの cx / cy にそのまま渡す。 */
  cx: number;
  cy: number;
  /** 箱の幅・高さ(px)。素材と同じ縦横比なので、contain でちょうど埋まる。 */
  width: number;
  height: number;
};

/**
 * 画像の中の点 `origin` を画面の (x, y) に合わせたまま、画面の四隅まで必ず覆う
 * いちばん小さい箱(× margin)を返す。集中線・効果線の中心を顔に寄せても端が切れない。
 *
 * 画面の四隅を、origin を原点にした画像の向き(回転を戻した向き)へ写し、
 * 画像の左右・上下の取り分(origin から端までの比)で割った値のいちばん大きいものが
 * 画像の高さになる。回転していなければ、中心(origin = 0.5)のとき
 * 高さ = max(2·max(y, H−y), 2·max(x, W−x) / aspect) × margin。
 */
export const coverBox = (input: CoverInput): CoverBox => {
  const aspect = input.aspect > 0 && Number.isFinite(input.aspect) ? input.aspect : 1;
  const ox = clamp(input.origin[0], 0.01, 0.99);
  const oy = clamp(input.origin[1], 0.01, 0.99);
  const margin = Math.max(1, input.margin);
  const corners: Array<[number, number]> = [
    [0, 0],
    [input.screenW, 0],
    [0, input.screenH],
    [input.screenW, input.screenH],
  ];
  // 画像の高さ h として、幅は h·aspect。origin から左へ ox·h·aspect、右へ (1−ox)·h·aspect、
  // 上へ oy·h、下へ (1−oy)·h まで届く。四隅がその中に入る h のいちばん小さい値を探す。
  let h = 1;
  for (const [cx, cy] of corners) {
    const [dx, dy] = rotateCss(cx - input.x, cy - input.y, input.rot);
    h = Math.max(
      h,
      dx < 0 ? -dx / (ox * aspect) : dx / ((1 - ox) * aspect),
      dy < 0 ? -dy / oy : dy / (1 - oy),
    );
  }
  const height = h * margin;
  const width = height * aspect;
  // 箱の中心は origin から ((0.5−ox)·幅, (0.5−oy)·高さ) ずれた所。描画は箱の中心で回すので、
  // そのずれも回してから足す(origin が画面の (x, y) に残る)。
  const [ddx, ddy] = rotateCss((0.5 - ox) * width, (0.5 - oy) * height, -input.rot);
  return { cx: input.x + ddx, cy: input.y + ddy, width, height };
};

/** 箱(boxW × boxH)に縦横比 aspect の素材を contain で収めたときの大きさ。 */
export const containSize = (
  boxW: number,
  boxH: number,
  aspect: number | null,
): { width: number; height: number } => {
  if (!aspect || !(aspect > 0) || !Number.isFinite(aspect)) {
    return { width: boxW, height: boxH };
  }
  const width = Math.min(boxW, boxH * aspect);
  return { width, height: width / aspect };
};

export type FitStartInput = {
  screenW: number;
  screenH: number;
  /** 絵の中心(px)。 */
  cx: number;
  cy: number;
  /** 着地(倍率 1)のときに見える絵の大きさ(px)。 */
  drawnW: number;
  drawnH: number;
  /** 傾き(度)。回した絵の外接の箱で測る。 */
  rot: number;
  /** [叩き込みはじめの倍率, 着地の倍率]。 */
  snap: readonly [number, number];
  /** 勢いのために残す最低の倍率(はじめの倍率がこれより小さければそのまま)。 */
  min: number;
  /** 画面の端に残す余白(画面の幅・高さに対する比)。 */
  edge?: number;
};

/**
 * imageSlam の叩き込みはじめの倍率を、絵が画面の左右・上下からはみ出さない所まで抑える。
 *
 * - 回した絵の外接の箱が、画面の端から `edge` 内側に収まる倍率までにする
 * - ただし勢いが無くならないよう、`min`(既定 1.12)までは下げない
 *   (もとの倍率が `min` より小さければ、もとの倍率のまま)
 * - 着地の倍率より小さくはしない(叩き込みが「縮んで入る」形を保つ)
 * - はじめの倍率が着地以下(拡大して入る・止まったまま)なら何もしない
 */
export const fitSlamStart = (input: FitStartInput): number => {
  const [start, land] = input.snap;
  if (!(start > land)) {
    return start;
  }
  const edge = input.edge ?? 0.01;
  const a = (input.rot * Math.PI) / 180;
  const c = Math.abs(Math.cos(a));
  const s = Math.abs(Math.sin(a));
  const halfW = (input.drawnW * c + input.drawnH * s) / 2;
  const halfH = (input.drawnW * s + input.drawnH * c) / 2;
  const roomX = Math.min(input.cx, input.screenW - input.cx) - edge * input.screenW;
  const roomY = Math.min(input.cy, input.screenH - input.cy) - edge * input.screenH;
  const room = Math.min(
    halfW > 0 ? roomX / halfW : Number.POSITIVE_INFINITY,
    halfH > 0 ? roomY / halfH : Number.POSITIVE_INFINITY,
  );
  const fitted = Math.max(Math.min(start, room), Math.min(start, input.min));
  return Math.max(land, fitted);
};

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));
