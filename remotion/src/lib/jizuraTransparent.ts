/**
 * JIZURA の手元パッチ (b): 透過で描くときに、カット間のトランジションが画面を塗らない。
 *
 * JIZURA のトランジション（`J.TRANS[キー].draw(ctx, A, B, p, info)`、A = 前カットの
 * 静止フレーム、B = このカットのフレーム）は**不透明な絵**を前提に書かれていて、
 *
 *   - 「スタイルの背景色で全面を塗ってから B を縮めて置く」（`uncover` / `zoomThrough` …）
 *   - 「黒を半透明で全面にかぶせて前のカットを暗くする」（`cover` / `doorsOpen` …）
 *   - 「背景色と黒を混ぜた色で全面を塗る」（`cubeTurn` の奥）
 *   - 「A を描いてから B を上に重ねれば A は隠れる」（ほぼ全部）
 *
 * といった描き方をする。`FxOverlay` の歌詞層のように透過で重ねると、下の映像が
 * 0.1〜0.5 秒ずつ背景色や黒で塗りつぶされ、A と B が二重に見える。
 *
 * ここではエンジンのソースには触らず、**透過で描いているときだけ** `draw` に渡す
 * ctx を Proxy で包み、描き方を「透過の絵で同じ意味になる」ものに読み替える。
 *
 *   1. 描き始める前に ctx を消す（不透明なら最初の A / 背景の塗りが全面を覆う）
 *   2. 画像（A / B / 作業用キャンバス）の `drawImage` は「その矩形を置き換える」:
 *      矩形を同じ不透明度で消して（destination-out）から加算（lighter）で置く。
 *      不透明な絵での `globalAlpha = a` の重ね（= 線形補間）と同じ式になるので、
 *      クロスフェードや残像の平均もそのまま透過で成り立つ
 *   3. 背景色（このカット・前のカット・スタイルの全配色の `bg`）と黒
 *      （`#000000` / `rgba(0,0,0,a)`、黒だけのグラデーション = 影）の塗り・線は
 *      **同じ不透明度で消す**（= 前のカットを暗くする → 薄くする）
 *   4. それ以外の色・パターンで画面のほぼ全面（9 割以上）を塗るもの（フラッシュ、
 *      砂嵐、`cubeTurn` の奥の色）は、**いま描かれている絵の上だけ**に掛ける
 *      （source-atop。文字は光るが、空の背景は透けたまま）。JIZURA 自身の
 *      透過用のガード（`Renderer#alphaGuard`、画面効果に掛かる）と同じ考え方
 *   5. アクセントの帯・線・枠など、全面でない色の塗りはそのまま描く
 *
 * 作業用キャンバス（`info.tmp()`。`whipPan` のブレ、`pixelate` のモザイク）の ctx も
 * 同じ規則で包む。不透明（`transparent` でない）ときは元の `draw` をそのまま呼ぶので、
 * **不透明な絵は一切変わらない**。
 *
 * 「いま透過で描いているか」は `Renderer#frame` を包んで opt.transparent を覚えて
 * おく（トランジションの前カットを描き直す再帰の `frame()` も同じ opt を引き継ぐ）。
 */

import type { Jizura, JizuraTrans, JizuraTransInfo } from '../../vendor/jizura/dist/jizura.js';

/** いま `Renderer#frame` が透過で描いているか（入れ子の frame() は呼び出しごとに退避・復帰）。 */
let drawingTransparent = false;

const MARK = '__karakuriTransparent';

type Marked = { [MARK]?: boolean };

/**
 * `Renderer#frame` と `J.TRANS` の全エントリの `draw` を包む（何度呼んでも 1 回だけ）。
 */
export function patchTransparentTransitions(J: Jizura): void {
  const proto = J.Renderer.prototype as typeof J.Renderer.prototype & Marked;
  if (!proto[MARK]) {
    const frame = proto.frame;
    proto.frame = function transparentAwareFrame(this: unknown, ctx, plan, t, opt) {
      const previous = drawingTransparent;
      drawingTransparent = !!(opt && opt.transparent);
      try {
        return frame.call(this, ctx, plan, t, opt);
      } finally {
        drawingTransparent = previous;
      }
    };
    proto[MARK] = true;
  }
  for (const key of Object.keys(J.TRANS)) {
    const trans = J.TRANS[key] as JizuraTrans & Marked;
    if (!trans || typeof trans.draw !== 'function' || trans[MARK]) continue;
    const original = trans.draw;
    trans.draw = function transparentSafeDraw(this: unknown, ctx, A, B, p, info) {
      if (!drawingTransparent) return original.call(this, ctx, A, B, p, info);
      const guard = new TransparentTransition(ctx, info);
      try {
        guard.clear();
        return original.call(this, guard.ctx, A, B, p, guard.info);
      } finally {
        guard.dispose();
      }
    };
    trans[MARK] = true;
  }
}

/** 塗り・線をどう読み替えるか。 */
type Mode = 'draw' | 'erase' | 'wash';

/** 塗りの対象（全面判定に使う矩形。パスの塗りは矩形が分からないので null）。 */
type Rect = { x: number; y: number; w: number; h: number } | null;

/** 画面のこれ以上を覆う塗りを「全面」とみなす。 */
const FULL_COVER = 0.9;

/** 色の正規化に使う ctx（`fillStyle` に入れて読み戻すと `#rrggbb` / `rgba(…)` に揃う）。 */
let probe: CanvasRenderingContext2D | null = null;

function normalizeColor(color: unknown): string | null {
  if (typeof color !== 'string' || !color) return null;
  if (!probe) probe = document.createElement('canvas').getContext('2d');
  if (!probe) return null;
  probe.fillStyle = '#010203';
  probe.fillStyle = color;
  const out = probe.fillStyle;
  return typeof out === 'string' ? out.toLowerCase() : null;
}

/** 正規化した色が黒（不透明の #000000 か rgba(0, 0, 0, a)）か。 */
function isBlack(color: string): boolean {
  return color === '#000000' || /^rgba\(0, 0, 0, [0-9.e-]+\)$/.test(color);
}

/** 1 回のトランジションの描画ぶんの読み替え。 */
class TransparentTransition {
  readonly ctx: CanvasRenderingContext2D;
  readonly info: JizuraTransInfo;
  private readonly real: CanvasRenderingContext2D;
  /** 背景色（消す色）。 */
  private readonly backgrounds = new Set<string>();
  /** 配色の文字色・アクセント（黒でも線や帯として描く色）。 */
  private readonly graphics = new Set<string>();
  /** 作ったグラデーション → 色の一覧（黒だけなら影として消す）。 */
  private readonly gradients = new WeakMap<CanvasGradient, string[]>();
  private readonly proxies = new Map<CanvasRenderingContext2D, CanvasRenderingContext2D>();
  /** getContext を差し替えた作業用キャンバス（終わったら戻す）。 */
  private readonly patchedCanvases = new Set<HTMLCanvasElement>();

  constructor(real: CanvasRenderingContext2D, info: JizuraTransInfo) {
    this.real = real;
    const schemes = [info.sc, info.scPrev, ...(info.st?.schemes ?? [])];
    for (const scheme of schemes) {
      if (!scheme) continue;
      const bg = normalizeColor(scheme.bg);
      if (bg) this.backgrounds.add(bg);
    }
    for (const scheme of [info.sc, info.scPrev]) {
      if (!scheme) continue;
      for (const name of ['fg', 'accent', 'accent2', 'sub']) {
        const color = normalizeColor(scheme[name]);
        if (color) this.graphics.add(color);
      }
    }
    this.ctx = this.wrap(real);
    const tmp = info.tmp;
    this.info = {
      ...info,
      tmp: (w: number, h: number) => this.patchCanvas(tmp.call(info, w, h)),
    };
  }

  /** 描き始める前に全面を消す（不透明なら最初の A や背景の塗りが全面を覆う）。 */
  clear(): void {
    const real = this.real;
    real.save();
    real.setTransform(1, 0, 0, 1, 0, 0);
    real.clearRect(0, 0, real.canvas.width, real.canvas.height);
    real.restore();
  }

  dispose(): void {
    for (const canvas of this.patchedCanvases) {
      delete (canvas as unknown as { getContext?: unknown }).getContext;
    }
    this.patchedCanvases.clear();
  }

  /** 作業用キャンバスの `getContext('2d')` が包んだ ctx を返すようにする。 */
  private patchCanvas(canvas: HTMLCanvasElement): HTMLCanvasElement {
    if (!canvas || this.patchedCanvases.has(canvas)) return canvas;
    const getContext = HTMLCanvasElement.prototype.getContext;
    const self = this;
    Object.defineProperty(canvas, 'getContext', {
      configurable: true,
      writable: true,
      value(this: HTMLCanvasElement, type: string, options?: unknown) {
        const context = (getContext as (this: HTMLCanvasElement, t: string, o?: unknown) => unknown).call(
          canvas,
          type,
          options,
        );
        return type === '2d' && context
          ? self.wrap(context as CanvasRenderingContext2D)
          : context;
      },
    });
    this.patchedCanvases.add(canvas);
    return canvas;
  }

  /** 塗り・線の読み替え方を決める。 */
  private modeOf(style: unknown, rect: Rect, real: CanvasRenderingContext2D): Mode {
    if (real.globalCompositeOperation !== 'source-over') return 'draw';
    const full = rect ? this.covers(rect, real) : false;
    if (typeof style === 'string') {
      const color = style.toLowerCase();
      if (this.backgrounds.has(color)) return 'erase';
      if (isBlack(color)) {
        // 配色そのものが黒なら、全面でない帯・線はアクセントとして描く
        if (this.graphics.has(color) && !full) return 'draw';
        return 'erase';
      }
      return full ? 'wash' : 'draw';
    }
    if (typeof CanvasGradient !== 'undefined' && style instanceof CanvasGradient) {
      const stops = this.gradients.get(style);
      if (stops && stops.length && stops.every(isBlack)) return 'erase';
      return full ? 'wash' : 'draw';
    }
    // パターン（砂嵐など）
    return full ? 'wash' : 'draw';
  }

  /** 矩形（いまの変換を掛けたもの）が画面のほぼ全面を覆うか。 */
  private covers(rect: NonNullable<Rect>, real: CanvasRenderingContext2D): boolean {
    const cw = real.canvas.width;
    const ch = real.canvas.height;
    if (!(cw > 0 && ch > 0)) return false;
    const m = real.getTransform();
    const xs: number[] = [];
    const ys: number[] = [];
    for (const [px, py] of [
      [rect.x, rect.y],
      [rect.x + rect.w, rect.y],
      [rect.x, rect.y + rect.h],
      [rect.x + rect.w, rect.y + rect.h],
    ]) {
      xs.push(m.a * px + m.c * py + m.e);
      ys.push(m.b * px + m.d * py + m.f);
    }
    const x0 = Math.max(0, Math.min(...xs));
    const x1 = Math.min(cw, Math.max(...xs));
    const y0 = Math.max(0, Math.min(...ys));
    const y1 = Math.min(ch, Math.max(...ys));
    if (x1 <= x0 || y1 <= y0) return false;
    return ((x1 - x0) * (y1 - y0)) / (cw * ch) >= FULL_COVER;
  }

  /** 読み替えた合成で 1 回描く。 */
  private run(real: CanvasRenderingContext2D, mode: Mode, draw: () => void): void {
    if (mode === 'draw') {
      draw();
      return;
    }
    real.save();
    real.globalCompositeOperation = mode === 'erase' ? 'destination-out' : 'source-atop';
    try {
      draw();
    } finally {
      real.restore();
    }
  }

  /** ctx を包む（同じ ctx には同じ Proxy を返す）。 */
  private wrap(real: CanvasRenderingContext2D): CanvasRenderingContext2D {
    const cached = this.proxies.get(real);
    if (cached) return cached;
    const self = this;
    const target = real as unknown as Record<string | symbol, unknown>;
    const bound = new Map<string | symbol, unknown>();

    const overrides: Record<string, unknown> = {
      fillRect(x: number, y: number, w: number, h: number) {
        const mode = self.modeOf(real.fillStyle, { x, y, w, h }, real);
        self.run(real, mode, () => real.fillRect(x, y, w, h));
      },
      fill(...args: unknown[]) {
        const mode = self.modeOf(real.fillStyle, null, real);
        self.run(real, mode, () => (real.fill as (...a: unknown[]) => void).apply(real, args));
      },
      strokeRect(x: number, y: number, w: number, h: number) {
        const mode = self.modeOf(real.strokeStyle, null, real);
        self.run(real, mode, () => real.strokeRect(x, y, w, h));
      },
      stroke(...args: unknown[]) {
        const mode = self.modeOf(real.strokeStyle, null, real);
        self.run(real, mode, () => (real.stroke as (...a: unknown[]) => void).apply(real, args));
      },
      drawImage(image: CanvasImageSource, ...args: number[]) {
        self.replaceImage(real, image, args);
      },
      createLinearGradient(x0: number, y0: number, x1: number, y1: number) {
        return self.recordStops(real.createLinearGradient(x0, y0, x1, y1));
      },
      createRadialGradient(
        x0: number,
        y0: number,
        r0: number,
        x1: number,
        y1: number,
        r1: number,
      ) {
        return self.recordStops(real.createRadialGradient(x0, y0, r0, x1, y1, r1));
      },
    };

    const proxy = new Proxy(target, {
      get(obj, prop) {
        if (typeof prop === 'string' && Object.prototype.hasOwnProperty.call(overrides, prop)) {
          return overrides[prop];
        }
        const value = Reflect.get(obj, prop, obj);
        if (typeof value !== 'function') return value;
        let fn = bound.get(prop);
        if (!fn) {
          fn = (value as (...a: unknown[]) => unknown).bind(obj);
          bound.set(prop, fn);
        }
        return fn;
      },
      set(obj, prop, value) {
        return Reflect.set(obj, prop, value, obj);
      },
    }) as unknown as CanvasRenderingContext2D;
    this.proxies.set(real, proxy);
    return proxy;
  }

  /** グラデーションの色を覚えておく（黒だけのもの = 影を見分けるため）。 */
  private recordStops(gradient: CanvasGradient): CanvasGradient {
    const stops: string[] = [];
    this.gradients.set(gradient, stops);
    const add = gradient.addColorStop.bind(gradient);
    Object.defineProperty(gradient, 'addColorStop', {
      configurable: true,
      writable: true,
      value(offset: number, color: string) {
        const normalized = normalizeColor(color);
        if (normalized) stops.push(normalized);
        add(offset, color);
      },
    });
    return gradient;
  }

  /**
   * 画像を「その矩形を置き換える」形で置く（不透明な絵で上に重ねたのと同じ結果）。
   * 消す: dst × (1 − a)、置く: + src × a（premultiplied なので lighter の加算で足りる）。
   */
  private replaceImage(
    real: CanvasRenderingContext2D,
    image: CanvasImageSource,
    args: number[],
  ): void {
    const draw = () =>
      (real.drawImage as (...a: unknown[]) => void).apply(real, [image, ...args]);
    if (real.globalCompositeOperation !== 'source-over') {
      draw();
      return;
    }
    const rect = destinationRect(image, args);
    real.save();
    try {
      if (rect) {
        real.globalCompositeOperation = 'destination-out';
        real.fillStyle = '#000000';
        real.fillRect(rect.x, rect.y, rect.w, rect.h);
      }
      real.globalCompositeOperation = 'lighter';
      draw();
    } finally {
      real.restore();
    }
  }
}

/** `drawImage` の引数から描き先の矩形を出す（3 / 5 / 9 引数の形）。 */
function destinationRect(image: CanvasImageSource, args: number[]): NonNullable<Rect> | null {
  if (args.length >= 8) return { x: args[4], y: args[5], w: args[6], h: args[7] };
  if (args.length >= 4) return { x: args[0], y: args[1], w: args[2], h: args[3] };
  if (args.length >= 2) {
    const size = image as { width?: unknown; height?: unknown };
    const w = typeof size.width === 'number' ? size.width : 0;
    const h = typeof size.height === 'number' ? size.height : 0;
    if (!(w > 0 && h > 0)) return null;
    return { x: args[0], y: args[1], w, h };
  }
  return null;
}
