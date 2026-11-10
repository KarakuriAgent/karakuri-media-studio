/**
 * クロマキー(色を抜く)の画素の計算。video イベントの chromaKey が使う。
 *
 * 色の近さは YCbCr の色差(Cb, Cr。明るさを除いた色み)の距離で測る。明るさを見ないので、
 * 照明のむらで明るさが揺れるグリーンバックでも抜け方がそろう。
 *
 * - 距離が similarity より近い画素は完全に透明
 * - similarity から similarity + smoothness までは徐々に不透明へ(縁のなめらかさ)
 * - 残った画素は、色差のうち「抜く色の向き」の成分を spill の割合だけ取り除く
 *   (緑かぶり・青かぶりの縁を抑える。明るさは保つ)
 */

export type Rgb = readonly [number, number, number];

/** 0..1 の RGB を [Y, Cb, Cr] にする(BT.601)。 */
export const toYCbCr = (r: number, g: number, b: number): [number, number, number] => {
  const y = 0.299 * r + 0.587 * g + 0.114 * b;
  return [y, (b - y) * 0.564, (r - y) * 0.713];
};

const clamp01 = (v: number) => (v < 0 ? 0 : v > 1 ? 1 : v);

/**
 * RGBA の画素列(0..255)をその場で書き換えて色を抜く。
 *
 * `key` は抜く色(0..255 の RGB)。元のアルファ(透過の素材)は掛け合わせて保つ。
 */
export const keyPixels = (
  data: Uint8ClampedArray,
  key: Rgb,
  similarity: number,
  smoothness: number,
  spill: number,
): void => {
  const [, kcb, kcr] = toYCbCr(key[0] / 255, key[1] / 255, key[2] / 255);
  const klen = Math.hypot(kcb, kcr);
  // 抜く色が灰色(色みが無い)なら向きが決まらないので、にじみ抑えはしない
  const ux = klen > 1e-6 ? kcb / klen : 0;
  const uy = klen > 1e-6 ? kcr / klen : 0;
  const soft = Math.max(1e-6, smoothness);
  for (let i = 0; i < data.length; i += 4) {
    const a0 = data[i + 3];
    if (a0 === 0) {
      continue;
    }
    const r = data[i] / 255;
    const g = data[i + 1] / 255;
    const b = data[i + 2] / 255;
    const [y, cb, cr] = toYCbCr(r, g, b);
    const d = Math.hypot(cb - kcb, cr - kcr);
    let alpha: number;
    if (d <= similarity) {
      alpha = 0;
    } else if (d < similarity + smoothness) {
      alpha = (d - similarity) / soft;
    } else {
      alpha = 1;
    }
    if (alpha <= 0) {
      data[i + 3] = 0;
      continue;
    }
    if (spill > 0 && klen > 1e-6) {
      const along = cb * ux + cr * uy;
      if (along > 0) {
        const ncb = cb - spill * along * ux;
        const ncr = cr - spill * along * uy;
        const nr = y + ncr / 0.713;
        const nb = y + ncb / 0.564;
        const ng = (y - 0.299 * nr - 0.114 * nb) / 0.587;
        data[i] = Math.round(clamp01(nr) * 255);
        data[i + 1] = Math.round(clamp01(ng) * 255);
        data[i + 2] = Math.round(clamp01(nb) * 255);
      }
    }
    data[i + 3] = Math.round(a0 * alpha);
  }
};

const colorCache = new Map<string, Rgb>();

/** CSS の色を RGB(0..255)にする(ブラウザに塗らせて読む)。読めなければ緑。 */
export const cssColorToRgb = (css: string): Rgb => {
  const hit = colorCache.get(css);
  if (hit) {
    return hit;
  }
  const hex = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(css.trim());
  let rgb: Rgb = [0, 255, 0];
  if (hex) {
    const body = hex[1].length === 3 ? hex[1].replace(/./g, (c) => c + c) : hex[1];
    rgb = [0, 2, 4].map((at) => parseInt(body.slice(at, at + 2), 16)) as unknown as Rgb;
  } else if (typeof document !== 'undefined') {
    const canvas = document.createElement('canvas');
    canvas.width = 1;
    canvas.height = 1;
    const c = canvas.getContext('2d');
    if (c) {
      c.fillStyle = '#00ff00';
      c.fillStyle = css;
      c.fillRect(0, 0, 1, 1);
      const px = c.getImageData(0, 0, 1, 1).data;
      rgb = [px[0], px[1], px[2]];
    }
  }
  colorCache.set(css, rgb);
  return rgb;
};
