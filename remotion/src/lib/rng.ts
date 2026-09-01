/**
 * seed から決まる擬似乱数(mulberry32)。
 *
 * 演出の「ばらつき」(カードの傾き・横ずれ・シェイクの向き・ブロックノイズの位置)は
 * すべてここを通す。同じ props なら何度焼いても同じ絵になる、というのが唯一の条件。
 */
export type Rng = {
  next: () => number;
  /** 両端を含む整数。 */
  randint: (lo: number, hi: number) => number;
  /** 配列から 1 つ選ぶ。 */
  choice: <T>(xs: readonly T[]) => T;
  /** lo 以上 hi 未満の実数。 */
  range: (lo: number, hi: number) => number;
};

export const rng = (seed: number): Rng => {
  let a = (Math.trunc(seed) | 0) >>> 0;
  const next = () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
  return {
    next,
    randint: (lo, hi) => lo + Math.floor(next() * (hi - lo + 1)),
    choice: <T,>(xs: readonly T[]) => xs[Math.floor(next() * xs.length)],
    range: (lo, hi) => lo + next() * (hi - lo),
  };
};
