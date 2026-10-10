/**
 * 生成物 `jizura.js`（JIZURA のエンジンを結合した ESM）の型。
 *
 * 手書き。JIZURA 本体は素の JavaScript なので、**LyricMotion と手元パッチ
 * （`remotion/src/lib/jizura*.ts`）が実際に使うところだけ**を宣言してある（部品の
 * 登録簿は `unknown` 寄りにして、使う側で絞る）。vendor/jizura/src/ を更新して
 * API が変わったらここも直すこと。
 */

/** 部品のグループ（layout / enter / … は `J.order(group)` のキー）。 */
export type JizuraGroup =
  | 'layout' | 'enter' | 'hold' | 'exit' | 'decor'
  | 'treat' | 'bg' | 'cam' | 'fx' | 'trans';

/** 部品セット（`J.SETS` のキー。`project[セット名]` の真偽で ON / OFF）。 */
export type JizuraSetKey = 'typo' | 'kinetic' | 'horror';

/**
 * 行の中の 1 カットだけに掛ける指名（`overrides[行].cutTech[カット番号]`、`08_planner.js`）。
 * `decor` は 1 つだけ（`'none'` / null で装飾なし）、`trans` は `'none'` でつなぎなし。
 */
export interface JizuraCutTech {
  layout?: string;
  enter?: string;
  exit?: string;
  hold?: string;
  decor?: string | null;
  treat?: string;
  bg?: string;
  cam?: string;
  trans?: string;
}

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
  trans?: string;
  single?: boolean;
  seed?: number;
  /** この行のカット数（1〜12。行が短ければ文字の途中でも割る）。 */
  cuts?: number;
  /** カット番号（0 始まりの文字列キー）→ そのカットのレイアウト。 */
  cutLayouts?: Record<string, string>;
  /** カット番号 → そのカットだけの指名。 */
  cutTech?: Record<string, JizuraCutTech>;
  lock?: boolean;
  lockedSeed?: number;
  /** ロック時に保存したカットの中身（本家エディタが書く。`J.lineSnapshot`）。 */
  lockedCuts?: Array<Record<string, unknown>>;
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
  /** 部品セット（`J.SETS`）。typo / kinetic は既定 ON、horror は既定 OFF。 */
  typo: boolean;
  kinetic: boolean;
  horror: boolean;
  lang: string;
  keyBg: string;
  /** 統一感（パートごとの部品の揃え・繰り返しの再現・キメ・モーフ・太さ）。 */
  unify: boolean;
  /** 文字整列（カナの詰め・助詞を小さく・0.2 秒先出し・演出の抑制）。 */
  typeset: boolean;
  /** 中央を空ける: カットを左右（縦長なら上下）の帯に割り付けて帯の中で組む。 */
  centerFree: boolean;
  /** 中央を空けるときの縦長画面での向き（'tb' 上下 / 'lr' 左右）。 */
  centerDir: 'tb' | 'lr';
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
  /** おまかせ・シャッフルで変えないもの（本家エディタ側だけが使う）。 */
  locks?: { tech: Record<string, unknown>; params: Record<string, unknown> };
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

/** 中央を空けるときの帯（デザインサイズの px。`J.sideZones`）。 */
export interface JizuraZone {
  x: number;
  y: number;
  w: number;
  h: number;
  side: 'left' | 'right' | 'top' | 'bottom';
}

/** plan の 1 カット。描画と手元パッチが読むところだけ宣言する。 */
export interface JizuraCut {
  index: number;
  /** 行番号（0 始まり。タイトルは -1、行間の間奏はその前の行の番号）。 */
  line: number;
  start: number;
  end: number;
  dur: number;
  layout: string;
  cam: string;
  trans: string | null;
  /** このカットが受け持つ歌詞（歌詞のカットだけにある。タイトル・間奏には無い）。 */
  utext?: string;
  /** 中央を空けるときの帯。 */
  zone?: JizuraZone | null;
  /** 中央を空けるときの相方（もう片方の帯に出す後半。`plan.cuts` には入らない）。 */
  companion?: JizuraCut;
  [key: string]: unknown;
}

/** plan の 1 行。 */
export interface JizuraPlanLine {
  index: number;
  text: string;
  /** `[間奏]` の行。 */
  interlude?: boolean;
  start: number;
  end: number;
  [key: string]: unknown;
}

/** `J.plan()` の結果。描画に要るところだけ宣言する。 */
export interface JizuraPlan {
  W: number;
  H: number;
  fps: number;
  duration: number;
  keyBg: string | null;
  lines: JizuraPlanLine[];
  cuts: JizuraCut[];
  events: Array<{ t: number; type: string; amp: number; dur: number }>;
  centerFree?: boolean;
  zones?: JizuraZone[] | null;
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

/** Renderer の組み立て中に作る部品の環境（`Renderer#makeEnv`）。手元パッチが読むところだけ。 */
export interface JizuraEnv {
  ctx: CanvasRenderingContext2D;
  /** このカットを組む枠の大きさ（中央を空けるときは帯、それ以外はデザインサイズ）。 */
  W: number;
  H: number;
  cut: JizuraCut | null;
  plan: JizuraPlan;
  lt: number;
  [key: string]: unknown;
}

/** カメラの戻り（`J.CAMERA[k].get`）。x / y はデザイン px、s は倍率。 */
export interface JizuraCameraState {
  x?: number;
  y?: number;
  s?: number;
  sx?: number;
  sy?: number;
  rot?: number;
  skx?: number;
  blur?: number;
  [key: string]: unknown;
}

/** カメラの部品。 */
export interface JizuraCamera extends JizuraEntry {
  get(env: JizuraEnv, params: Record<string, unknown>): JizuraCameraState | null | undefined;
}

/** カット間のトランジションに渡る情報（`09_render.js` の cut-to-cut transition 節）。 */
export interface JizuraTransInfo {
  cw: number;
  ch: number;
  /** このカットの配色（`bg` / `fg` / `accent` / `accent2` …）。 */
  sc: Record<string, string>;
  /** 前のカットの配色。 */
  scPrev?: Record<string, string>;
  /** スタイル（`schemes` に全配色）。 */
  st: { schemes?: Array<Record<string, string>>; [key: string]: unknown };
  P: Record<string, unknown>;
  step: number;
  t: number;
  scale: number;
  allowFilter: boolean;
  seed: number;
  /** 作業用キャンバス（呼ぶたびに同じものを大きさだけ変えて返す）。 */
  tmp(w: number, h: number): HTMLCanvasElement;
  [key: string]: unknown;
}

/** トランジションの部品。A = 前カットの静止フレーム、B = このカットのフレーム。 */
export interface JizuraTrans extends JizuraEntry {
  dur?: number;
  draw(
    ctx: CanvasRenderingContext2D,
    A: HTMLCanvasElement,
    B: HTMLCanvasElement,
    p: number,
    info: JizuraTransInfo,
  ): void;
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
  /** 部品セット（`typo` / `kinetic` / `horror`）に属していればその名前。 */
  set?: JizuraSetKey;
  [key: string]: unknown;
}

/** JIZURA の名前空間（`window.J`）。 */
export interface Jizura {
  SAMPLE_LYRICS: string;
  STYLE_ORDER: string[];
  STYLES: Record<string, JizuraEntry>;
  FONTS: Record<string, JizuraEntry>;
  /** おまかせの雰囲気。`set` があるもの（ホラー）はそのセットが ON のときだけ選ばれる。 */
  MOODS: Record<string, { name: string; set?: JizuraSetKey; [key: string]: unknown }>;
  GROUP_KEYS: JizuraGroup[];
  KEY_BG: Record<string, string>;
  /** 部品セットと既定の ON / OFF（`11q_sets.js`）。 */
  SETS: Record<JizuraSetKey, { on: boolean }>;
  SET_ORDER: JizuraSetKey[];
  CAMERA: Record<string, JizuraCamera>;
  TRANS: Record<string, JizuraTrans>;
  Renderer: { new (): JizuraRenderer; prototype: JizuraRenderer };
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
      /** `[間奏]` / `[間奏 8]` の行（text は空）。 */
      interlude?: boolean;
      /** 間奏の秒数（書いてあれば）。 */
      secs?: number | null;
    }>;
    meta: Record<string, string>;
  };
  /** 初版より後に足された部品か（`extra: false` ならランダムには選ばれない）。 */
  isExtra(group: JizuraGroup | 'style' | 'font', key: string): boolean;
  /** 和風モチーフの部品か（`wa: false` ならランダムには選ばれない）。 */
  isWa(group: JizuraGroup | 'style' | 'font', key: string): boolean;
  /** 部品が属するセット（無ければ null）。 */
  setOf(group: JizuraGroup | 'style' | 'font', key: string): JizuraSetKey | null;
  /** project でそのセットが ON か（書いていなければ `J.SETS[set].on`）。 */
  setOn(project: Partial<JizuraProject>, set: JizuraSetKey): boolean;
  /** 中央を空けるときの帯（左右 / 上下の 2 つ）。 */
  sideZones(W: number, H: number, dir: 'tb' | 'lr'): JizuraZone[];
  /** 時刻 t のカット（無ければ null）。 */
  cutAt(plan: JizuraPlan, t: number): JizuraCut | null;
  beatGrid(bpm: number, offset: number, duration: number): number[];
  ensureFonts(text: string, keys?: string[] | null): Promise<void>;
  fontsOfPlan(plan: JizuraPlan): string[];
  omakase(project: JizuraProject, rnd?: () => number): Partial<JizuraProject>;
  [key: string]: unknown;
}

/** エンジンを評価して `J` を返す（2 回目以降は同じもの）。ブラウザ専用。 */
export function loadJizura(): Jizura;

export default loadJizura;
