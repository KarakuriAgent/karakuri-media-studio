/**
 * 生成物 `jizura.js`（JIZURA のエンジンを結合した ESM）の型。
 *
 * 手書き。JIZURA 本体は素の JavaScript なので、**LyricMotion が実際に使うところ
 * だけ**を宣言してある（部品の登録簿は `unknown` 寄りにして、使う側で絞る）。
 * vendor/jizura/src/ を更新して API が変わったらここも直すこと。
 */

/** 部品のグループ（layout / enter / … は `J.order(group)` のキー）。 */
export type JizuraGroup =
  | 'layout' | 'enter' | 'hold' | 'exit' | 'decor'
  | 'treat' | 'bg' | 'cam' | 'fx' | 'trans';

/** 1 行に掛ける上書き（`project.overrides[行番号]`）。 */
export interface JizuraOverride {
  layout?: string;
  enter?: string;
  hold?: string;
  exit?: string;
  decor?: string[];
  treat?: string;
  bg?: string;
  cam?: string;
  single?: boolean;
  seed?: number;
  lock?: boolean;
  lockedSeed?: number;
}

/** `J.defaultProject()` が返すプロジェクト。 */
export interface JizuraProject {
  version: number;
  title: string;
  artist: string;
  lyrics: string;
  style: string;
  mood: string | null;
  extra: boolean;
  wa: boolean;
  lang: string;
  keyBg: string;
  seed: number;
  aspect: string;
  res: number;
  fps: number;
  fx: Record<string, number | boolean | string>;
  enabled: Record<string, Record<string, boolean>>;
  timing: {
    bpm: number;
    offset: number;
    snap: boolean;
    tail: number;
    lineTimes: Record<string, number>;
    lineScale: number;
    useAudioLength?: boolean;
  };
  overrides: Record<string, JizuraOverride>;
  colors: Record<string, unknown>;
  fonts: Record<string, string>;
}

/** `J.plan()` に渡す音源解析（無ければ null）。 */
export interface JizuraAudioLike {
  beats: number[];
  duration: number;
  energy?: number[] | Float32Array;
  energyRate?: number;
}

/** `J.plan()` の結果。描画に要るところだけ宣言する。 */
export interface JizuraPlan {
  W: number;
  H: number;
  fps: number;
  duration: number;
  keyBg: string | null;
  cuts: Array<{ start: number; end: number; [key: string]: unknown }>;
  events: Array<{ t: number; type: string; amp: number; dur: number }>;
  [key: string]: unknown;
}

/** `renderer.frame()` の第 4 引数。 */
export interface JizuraFrameOptions {
  /** デザインサイズ（plan.W × plan.H）に対する描画倍率。 */
  scale?: number;
  /** 背景を塗らずアルファを残す（透過 PNG / ProRes 4444 用）。 */
  transparent?: boolean;
  /** 透過時に前景・背景を分けて描く。 */
  layer?: 'back' | 'front' | null;
  /** 重い処理（ブラー・ブルーム）を省く。 */
  fast?: boolean;
  noTrans?: boolean;
  noPost?: boolean;
  noHud?: boolean;
}

export interface JizuraRenderer {
  frame(
    ctx: CanvasRenderingContext2D,
    plan: JizuraPlan,
    t: number,
    opt?: JizuraFrameOptions,
  ): void;
  /** 紙テクスチャ（生成時に Math.random を使うので事前に作らせたいことがある）。 */
  paper(W: number, H: number): HTMLCanvasElement;
}

/** 部品の定義（登録簿の値）。 */
export interface JizuraEntry {
  name: string;
  tags?: string[];
  pack?: string;
  w?: number;
  special?: boolean;
  extra?: boolean;
  wa?: boolean;
  [key: string]: unknown;
}

/** JIZURA の名前空間（`window.J`）。 */
export interface Jizura {
  SAMPLE_LYRICS: string;
  STYLE_ORDER: string[];
  STYLES: Record<string, JizuraEntry>;
  FONTS: Record<string, JizuraEntry>;
  MOODS: Record<string, { name: string; [key: string]: unknown }>;
  GROUP_KEYS: JizuraGroup[];
  KEY_BG: Record<string, string>;
  Renderer: new () => JizuraRenderer;
  defaultProject(): JizuraProject;
  plan(project: JizuraProject, audio: JizuraAudioLike | null): JizuraPlan;
  designSize(aspect: string): [number, number];
  outputSize(project: JizuraProject): [number, number];
  order(group: JizuraGroup): string[];
  registry(group: JizuraGroup): Record<string, JizuraEntry>;
  /** 歌詞を行に割る（LRC・`/`・`*強調*`・`|注釈`・`!` を解く。`08_planner.js`）。 */
  parseLyrics(raw: string): {
    lines: Array<{
      text: string;
      note: string | null;
      impact: boolean;
      emph: string[];
      manual: string[] | null;
      gapBefore: boolean;
      lrc: number | null;
    }>;
    meta: Record<string, string>;
  };
  /** 初版より後に足された部品か（`extra: false` ならランダムには選ばれない）。 */
  isExtra(group: JizuraGroup | 'style' | 'font', key: string): boolean;
  /** 和風モチーフの部品か（`wa: false` ならランダムには選ばれない）。 */
  isWa(group: JizuraGroup | 'style' | 'font', key: string): boolean;
  beatGrid(bpm: number, offset: number, duration: number): number[];
  ensureFonts(text: string, keys?: string[] | null): Promise<void>;
  fontsOfPlan(plan: JizuraPlan): string[];
  omakase(project: JizuraProject, rnd?: () => number): Partial<JizuraProject>;
  [key: string]: unknown;
}

/** エンジンを評価して `J` を返す（2 回目以降は同じもの）。ブラウザ専用。 */
export function loadJizura(): Jizura;

export default loadJizura;
