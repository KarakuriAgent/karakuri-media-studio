/**
 * LyricCanvas — JIZURA（文字PV 自動構成エンジン, MIT）で 1 フレームを `<canvas>` に描く部品。
 *
 * `LyricMotion`（単体の文字PV）と `FxOverlay` の歌詞モーション層の**両方**がこれを使う。
 * どちらも中身は同じで、違うのは「キャンバスの実寸をどう決めるか」だけ:
 *
 * - `LyricMotion` … JIZURA のデザインサイズを `res` に合わせて拡縮したものが実寸
 * - `FxOverlay`   … タイムラインの `width` / `height` に、いちばん近い `aspect` の
 *   デザインサイズを **cover** で収める（:func:`coverCanvas`）
 *
 * エンジンのソースは `remotion/vendor/jizura/` に**無改変**で置いてある（取り込み元と
 * ライセンスは `vendor/jizura/UPSTREAM.md` / `vendor/jizura/LICENSE`）。読むのは必ず
 * `./lib/jizura` の `loadJizura()` から（手元パッチ = 位置ずらしと透過時の
 * トランジションはそこで 1 回だけ掛かる。編集画面の部品一覧も同じ入口）。
 * ここがやるのは:
 *
 *   1. props（zod で検証済み）を JIZURA の project オブジェクトに組み直す
 *   2. `J.plan(project, audioLike)` で歌詞 → カット → イベントに構成し、
 *      行・カットの位置ずらし（`offset` / `scale` / `cutOffsets`）をカットに紐づける
 *   3. `new J.Renderer().frame(ctx, plan, t, {scale})` を 1 フレームずつ描く
 *
 * **フレームの時刻独立性**: `frame()` は時刻 t だけから絵を決める。カット間の
 * トランジションも、前カットの静止フレームを**その場で描き直して**合成するので
 * （`09_render.js` が自分の `frame()` を再帰で呼ぶ）、「1 フレーム前の絵」を溜めて
 * おく作りにはなっていない。Renderer が持つ状態はどれも作業用キャンバスと
 * キャッシュだけ。つまり Remotion が複数のタブに時刻をばらまいても構わない。
 *
 * ただし**タブごとに変わりうるもの**が 2 つあって、片方はここで潰してある:
 *
 *   - グレインと紙テクスチャは `Math.random()` で作られる → Renderer を作る間だけ
 *     種付きの乱数に差し替える（:func:`makeRenderer`）。これは潰した。
 *   - 字形の破片に振られるグローバル連番 id（`02_fonts.js` の `id: ++_pid`）は
 *     **どの字を先に描いたか**で変わり、砕ける演出の割れ方をわずかに動かす。
 *     実測（1920x1080 / 120 フレーム）で違いが出たのは 22 枚、いちばん差の大きい
 *     画素で 19/255、画面平均 0.001/255（PSNR 66〜94dB）で目には見えない。
 *     全字形を先に分解してしまえば揃うが、1080p × 4 タブで Chrome がメモリ不足に
 *     なるほど重いので**やっていない**。ビット単位で揃えたいときは
 *     `--concurrency=1` で焼くこと。
 */

import React, { useCallback, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { continueRender, delayRender, useCurrentFrame, useVideoConfig } from 'remotion';
import {
  attachPlacement,
  loadJizura,
  splitPlacement,
  type Jizura,
  type JizuraAudioLike,
  type JizuraPlan,
  type JizuraProject,
  type JizuraRenderer,
} from './lib/jizura';
import type { LyricAspect, LyricMotionProps } from './schema';

export { loadJizura };
export type { Jizura };

/** HUD（画面端の小さな文字）が使う字。フォント読み込みに混ぜる。 */
const HUD_CHARS = '0123456789:/.-+%ABCDEFGHIJKLMNOPQRSTUVWXYZ字面';

/** mulberry32（JIZURA の `J.rng` と同じ）。グレイン・紙テクスチャの種に使う。 */
function mulberry32(seed: number): () => number {
  let s = seed >>> 0;
  return () => {
    s = (s + 0x6d2b79f5) >>> 0;
    let t = s;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** `undefined` の値を落とした浅いコピー（zod の optional をそのまま重ねられるように）。 */
function defined<T extends object>(source: T): Partial<T> {
  const out: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(source)) {
    if (value !== undefined) out[key] = value;
  }
  return out as Partial<T>;
}

/**
 * 指定した雰囲気で「おまかせ」を掛ける。
 *
 * `J.omakase()` は**いま設定されている雰囲気以外**からランダムに選ぶ作り（編集画面で
 * 押すたびに変わるボタンなので）。こちらは雰囲気を指名したいので、呼んでいる間だけ
 * `J.MOODS` を「指名した 1 つ」と「部品セットに結び付いた雰囲気（ホラー）」に絞る。
 * セットに結び付いた雰囲気を残すのは、`J.omakase()` が「そのセットの部品はその
 * 雰囲気でだけ使う」を `J.MOODS` から判定するため（残さないと、ホラーの部品が
 * ほかの雰囲気でも選ばれうる）。残したほうが選ばれないよう、おまかせに見せる
 * project ではそのセットを OFF にしておく（指名した雰囲気のセットは ON）。
 * エンジンには一切手を入れずに済ませるための細工で、終わったら必ず元に戻す。
 */
function omakaseWithMood(
  J: Jizura,
  project: JizuraProject,
  mood: string,
  rnd: () => number,
): Partial<JizuraProject> {
  const all = J.MOODS;
  const chosen = all[mood];
  if (!chosen) {
    throw new Error(
      `mood '${mood}' は知らない雰囲気です（${Object.keys(all).join(' / ')} のどれか）`,
    );
  }
  const view: typeof all = {};
  const seen: JizuraProject = { ...project, mood: null };
  for (const [key, entry] of Object.entries(all)) {
    if (key === mood) view[key] = entry;
    else if (entry.set) {
      view[key] = entry;
      seen[entry.set] = false;
    }
  }
  if (chosen.set) seen[chosen.set] = true;
  try {
    (J as { MOODS: typeof all }).MOODS = view;
    return J.omakase(seen, rnd);
  } finally {
    (J as { MOODS: typeof all }).MOODS = all;
  }
}

/**
 * props を JIZURA の project に組み直す。
 *
 * 重ねる順は「既定 → mood のおまかせ → props に書いてあるもの」。`fx` / `enabled` /
 * `fonts` / `colors` は**書いたキーだけ**を重ねるので、雰囲気に任せつつ 1 か所だけ
 * 直す、という書き方ができる。
 *
 * 本家 v0.9 で増えた設定（部品セット `typo` / `kinetic` / `horror`、`unify`、
 * `typeset`、`centerFree` / `centerDir`）は props の既定が `J.defaultProject()` と
 * 同じなので、書かなければエンジンの既定どおりに描く。overrides の位置ずらし
 * （`offset` / `scale` / `cutOffsets`）は planner に渡さない（`prepare()` が plan の
 * カットに紐づける）。
 */
export function buildProject(J: Jizura, props: LyricMotionProps): JizuraProject {
  const project = J.defaultProject();
  project.lyrics = props.lyrics || J.SAMPLE_LYRICS;
  project.seed = props.seed;
  project.aspect = props.aspect;
  project.res = props.res;
  project.fps = props.fps;
  project.extra = props.extra;
  project.wa = props.wa;
  project.typo = props.typo;
  project.kinetic = props.kinetic;
  project.horror = props.horror;
  project.unify = props.unify;
  project.typeset = props.typeset;
  project.centerFree = props.centerFree;
  project.centerDir = props.centerDir;
  project.lang = props.lang;
  // 'transparent' は JIZURA の project には無い（描画側の opt.transparent で出す）
  project.keyBg = props.keyBg === 'transparent' ? 'off' : props.keyBg;
  project.timing = {
    ...project.timing,
    bpm: props.timing.bpm,
    offset: props.timing.offset,
    snap: props.timing.snap,
    tail: props.timing.tail,
    lineScale: props.timing.lineScale,
    lineTimes: { ...props.timing.lineTimes },
  };
  project.overrides = splitPlacement(props.overrides).overrides;

  if (props.mood) {
    // 部品セットに結び付いた雰囲気（ホラー）を指名したら、そのセットも使う
    const moodSet = J.MOODS[props.mood]?.set;
    if (moodSet) project[moodSet] = true;
    const rolled = omakaseWithMood(J, project, props.mood, mulberry32(props.seed));
    project.mood = props.mood;
    if (rolled.style) project.style = rolled.style;
    if (rolled.fx) project.fx = { ...project.fx, ...rolled.fx };
    if (rolled.enabled) project.enabled = rolled.enabled;
    if (rolled.fonts) project.fonts = { ...project.fonts, ...rolled.fonts };
    if (rolled.colors) project.colors = { ...project.colors, ...rolled.colors };
  }

  if (props.style) project.style = props.style;
  project.fx = { ...project.fx, ...defined(props.fx) };
  for (const [group, keys] of Object.entries(props.enabled)) {
    project.enabled[group] = { ...(project.enabled[group] || {}), ...keys };
  }
  project.fonts = { ...project.fonts, ...props.fonts };
  project.colors = { ...project.colors, ...props.colors };
  return project;
}

/** props の `audio` を `J.plan()` が読む形にする（拍もエネルギーも無ければ null）。 */
export function audioLike(props: {
  audio?: {
    beats: number[];
    duration?: number;
    energy?: number[];
    energyRate?: number;
  };
}): JizuraAudioLike | null {
  const audio = props.audio;
  if (!audio) return null;
  if (!audio.beats.length && !audio.duration) return null;
  return {
    beats: audio.beats,
    duration: audio.duration ?? 0,
    energy: audio.energy,
    energyRate: audio.energyRate,
  };
}

/** 出力の実寸（`J.outputSize()` と同じ計算。偶数に丸める）。 */
export function outputSize(J: Jizura, props: LyricMotionProps): [number, number] {
  const [W, H] = J.designSize(props.aspect);
  const k = props.res / Math.min(W, H);
  return [Math.round((W * k) / 2) * 2, Math.round((H * k) / 2) * 2];
}

/** JIZURA が知っている画面比（`lyricAspectSchema` と同じ並び）。 */
const ASPECTS: LyricAspect[] = ['16:9', '9:16', '4:3', '3:4', '1:1', '4:5', '21:9'];

/** `width:height` にいちばん近い JIZURA の画面比。 */
export function nearestAspect(J: Jizura, width: number, height: number): LyricAspect {
  const want = width / Math.max(1, height);
  let best = ASPECTS[0];
  let bestGap = Infinity;
  for (const aspect of ASPECTS) {
    const [W, H] = J.designSize(aspect);
    // 比の差は対数で測る（縦長・横長を同じ重みで見るため）
    const gap = Math.abs(Math.log(W / H) - Math.log(want));
    if (gap < bestGap) {
      bestGap = gap;
      best = aspect;
    }
  }
  return best;
}

/**
 * デザインサイズを `width` × `height` へ **cover** で収めるキャンバスの実寸。
 *
 * `Renderer#frame` は `ctx.setTransform(scale, 0, 0, scale, 0, 0)` を自分で掛けるので、
 * こちら側で原点をずらすことはできない。なのでキャンバスそのものを「はみ出す大きさ」
 * で作り、CSS で中央へ置いて枠からあふれたぶんを切る（:func:`LyricCanvas`）。
 */
export function coverCanvas(
  J: Jizura,
  aspect: LyricAspect,
  width: number,
  height: number,
): { width: number; height: number } {
  const [W, H] = J.designSize(aspect);
  const k = Math.max(width / W, height / H);
  return { width: Math.round(W * k), height: Math.round(H * k) };
}

/**
 * 種を固定した Renderer。
 *
 * グレイン（4 枚のノイズ画像）は `new J.Renderer()` の中で、紙テクスチャは初回の
 * `paper(W, H)` で `Math.random()` から作られる。どちらもタブごとに変わると
 * 並列レンダリングで絵が食い違うので、作る間だけ `Math.random` を種付きに差し替え、
 * 紙も**その場で**作らせてしまう。
 */
function makeRenderer(J: Jizura, plan: JizuraPlan, seed: number): JizuraRenderer {
  const real = Math.random;
  Math.random = mulberry32((seed ^ 0x9e3779b9) >>> 0);
  try {
    const renderer = new J.Renderer();
    renderer.paper(plan.W, plan.H);
    return renderer;
  } finally {
    Math.random = real;
  }
}

/**
 * props から plan を作る（フォントを読んでから組み直すので 2 回 plan する）。
 * 組み上がった plan には overrides の位置ずらしを紐づける（`lib/jizuraOffset.ts`）。
 */
export async function prepare(
  J: Jizura,
  props: LyricMotionProps,
): Promise<{ project: JizuraProject; plan: JizuraPlan }> {
  const project = buildProject(J, props);
  const audio = audioLike(props);
  // 行組みは字幅を測って決まるので、いちど構成してから**その構成が使う書体だけ**を
  // 読み込み、読み終えてから組み直す（編集画面の replan → ensureFonts と同じ順）。
  let plan = J.plan(project, audio);
  try {
    await J.ensureFonts(project.lyrics + HUD_CHARS, J.fontsOfPlan(plan));
  } catch {
    // フォントが取れなくてもフォールバックの書体で焼く（ネットの無い環境でも止めない）
  }
  plan = J.plan(project, audio);
  attachPlacement(plan, splitPlacement(props.overrides).placement);
  return { project, plan };
}

/**
 * 歌詞モーションの 1 フレーム。
 *
 * `width` / `height` は**キャンバスの実寸**。デザインサイズとの比が描画倍率になる
 * ので、`coverCanvas()` で出した大きさを渡すと画面いっぱいに収まる。枠からあふれる
 * ぶんは親側で `overflow: hidden` すること。
 */
export const LyricCanvas: React.FC<{
  props: LyricMotionProps;
  width: number;
  height: number;
  /** 背景を塗らずアルファを残す（`FxOverlay` の層として重ねるときは常に true）。 */
  transparent?: boolean;
  style?: React.CSSProperties;
}> = ({ props, width, height, transparent = false, style }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const [engine, setEngine] = useState<{
    J: Jizura;
    plan: JizuraPlan;
    renderer: JizuraRenderer;
  } | null>(null);
  // 最初のフレームを描き終えるまで Remotion に待ってもらう札。continueRender は
  // 1 回だけなので、済んだかどうかを別に持つ（札を null に戻すと再発行してしまう）。
  const [handle] = useState(() => delayRender('JIZURA: フォント読み込みと構成'));
  const continuedRef = useRef(false);
  const release = useCallback(() => {
    if (continuedRef.current) return;
    continuedRef.current = true;
    continueRender(handle);
  }, [handle]);

  // 構成（plan）と Renderer は props ごとに 1 回だけ。フレームごとには作らない。
  const key = useMemo(() => JSON.stringify(props), [props]);
  useLayoutEffect(() => {
    let alive = true;
    (async () => {
      const J = loadJizura();
      // 字形の分解をどこまで細かくやるか（JIZURA の MP4 書き出しと同じ設定）
      (J.glyphs as { maxRes: number }).maxRes = height >= 1000 ? 768 : 512;
      const { plan } = await prepare(J, props);
      if (!alive) return;
      setEngine({ J, plan, renderer: makeRenderer(J, plan, props.seed) });
    })().catch((error) => {
      // 構成に失敗したら待たせたままにせず、Remotion に例外を見せる
      release();
      throw error;
    });
    return () => {
      alive = false;
    };
    // props 全体が変わったときだけ組み直す
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, height]);

  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas || !engine) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    const { plan, renderer } = engine;
    renderer.frame(ctx, plan, frame / fps, {
      scale: canvas.width / plan.W,
      transparent,
    });
  }, [engine, frame, fps, transparent]);

  // 描いてからフレームを撮らせる（useLayoutEffect なので paint の前に走る）。
  useLayoutEffect(() => {
    draw();
    if (engine) release();
  }, [draw, engine, release]);

  return (
    <canvas
      ref={canvasRef}
      width={width}
      height={height}
      style={style ?? { width: '100%', height: '100%', display: 'block' }}
    />
  );
};
