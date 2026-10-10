/**
 * JIZURA の手元パッチ (a): 行・カット単位の位置ずらし。
 *
 * JIZURA 本体には「行やカットの位置をずらす」手段が無い（どこに出るかはレイアウトの
 * 型で決まる）。ここでは**エンジンのソースには触らずに**、カメラの部品
 * （`J.CAMERA[キー].get(env, params)`）を包んで、そのカットに指定があれば戻り値の
 * `x` / `y` / `s` に足し込む。
 *
 * `09_render.js` は content の各パス（色ズレの残像 A / B と本体）で、カットごとに
 *
 *     cam = J.CAMERA[cut.cam].get(env, cut.camP)
 *     translate(W/2 + … + cam.x, H/2 + … + cam.y); rotate; scale(cam.s); translate(-W/2, -H/2)
 *     drawCut(env)
 *
 * と座標を変えてから描くので、ここで足した量は文字・装飾・残像のすべてに同じだけ効く
 * （拡大は枠の中心まわり）。`env.W` / `env.H` はそのカットを組む枠の大きさ
 * （`centerFree` のときは帯）なので、ずらす量は**枠に対する比**になる。
 *
 * どのカットにどれだけ効かせるかは `attachPlacement()` が plan を組んだあとに
 * カットのオブジェクトへ紐づける（`WeakMap`。plan 自体は汚さない）。カメラは
 * `env.cut` しか見ないので、
 *
 *   - トランジション中に前カットの静止フレームを `frame()` の再帰で描き直すときも、
 *     前カットは前カットの位置で描かれる（行が替われば次の位置へ瞬間移動する）
 *   - `centerFree` の相方（`cut.companion`。もう片方の帯に出る後半）も同じ量だけ動く
 *   - モーフ（統一感）の字形の記録も `frame()` を通るので、ずれた位置から動き出す
 *
 * 位置ずらしのキー（`offset` / `scale` / `cutOffsets`）は JIZURA の planner には
 * 渡さない（`splitPlacement()`）。planner は「行の指名が 1 つでもあれば統一感を
 * 掛けない」作りなので、位置だけ動かした行が統一感から外れないようにするため。
 */

import type {
  Jizura,
  JizuraCameraState,
  JizuraCut,
  JizuraEnv,
  JizuraPlan,
} from '../../vendor/jizura/dist/jizura.js';

/** 1 カットぶんの位置ずらし（x / y は枠に対する比、s は拡大率）。 */
export interface LyricPlace {
  x: number;
  y: number;
  s: number;
}

/** overrides の値のうち、位置ずらしに使うところ（`lyricOverrideSchema` の一部）。 */
export interface LyricPlaceSource {
  offset?: { x?: number; y?: number };
  scale?: number;
  cutOffsets?: Record<string, { x?: number; y?: number; scale?: number }>;
}

/** 行ごとの位置ずらし（行の指定と、カット番号 → カットだけの指定）。 */
export type LyricPlacement = Map<
  number,
  { line: Partial<LyricPlace>; cuts: Map<number, Partial<LyricPlace>> }
>;

/** planner に渡さないキー（位置ずらし）。 */
const PLACE_KEYS: ReadonlyArray<keyof LyricPlaceSource> = ['offset', 'scale', 'cutOffsets'];

const finite = (value: unknown): number | undefined =>
  typeof value === 'number' && Number.isFinite(value) ? value : undefined;

function partOf(source: { x?: number; y?: number; scale?: number } | undefined): Partial<LyricPlace> {
  const out: Partial<LyricPlace> = {};
  if (!source) return out;
  const x = finite(source.x);
  const y = finite(source.y);
  const s = finite(source.scale);
  if (x !== undefined) out.x = x;
  if (y !== undefined) out.y = y;
  if (s !== undefined && s > 0) out.s = s;
  return out;
}

/**
 * overrides を「JIZURA の planner に渡すもの」と「位置ずらし」に分ける。
 *
 * 位置ずらしのキーだけだった行は planner 側から丸ごと落とす（空の `{}` を残さない）。
 * 位置ずらしが 1 つも無ければ `placement` は null。
 */
export function splitPlacement<T extends LyricPlaceSource>(
  overrides: Record<string, T>,
): { overrides: Record<string, Omit<T, keyof LyricPlaceSource>>; placement: LyricPlacement | null } {
  const rest: Record<string, Omit<T, keyof LyricPlaceSource>> = {};
  const placement: LyricPlacement = new Map();
  for (const [key, value] of Object.entries(overrides)) {
    if (!value || typeof value !== 'object') continue;
    const kept: Record<string, unknown> = {};
    for (const [name, v] of Object.entries(value)) {
      if (!(PLACE_KEYS as ReadonlyArray<string>).includes(name)) kept[name] = v;
    }
    if (Object.keys(kept).length) rest[key] = kept as Omit<T, keyof LyricPlaceSource>;

    const line = Number(key);
    if (!Number.isInteger(line) || line < 0) continue;
    const linePart = partOf({ ...value.offset, scale: value.scale });
    const cuts = new Map<number, Partial<LyricPlace>>();
    for (const [cutKey, cutValue] of Object.entries(value.cutOffsets ?? {})) {
      const k = Number(cutKey);
      if (!Number.isInteger(k) || k < 0) continue;
      const part = partOf(cutValue);
      if (Object.keys(part).length) cuts.set(k, part);
    }
    if (Object.keys(linePart).length || cuts.size) placement.set(line, { line: linePart, cuts });
  }
  return { overrides: rest, placement: placement.size ? placement : null };
}

/** カット（と `centerFree` の相方）→ 位置ずらし。plan を組み直せば古いカットごと消える。 */
const PLACES = new WeakMap<object, LyricPlace>();

/**
 * plan のカットに位置ずらしを紐づける（`J.plan()` のあと、描き始める前に 1 回）。
 *
 * カット番号は「その行の歌詞のカット」の 0 始まりの通し番号で、本家の `cutTech` /
 * `cutLayouts` と同じ数え方（`utext` のあるカットだけを数える）。行の間に自動で
 * 挟まる間奏（前の行の番号が付いている）とタイトルは動かさない。`[間奏]` の行は
 * その行の指定で動く（カット番号は 0）。
 */
export function attachPlacement(plan: JizuraPlan, placement: LyricPlacement | null): void {
  if (!placement || !placement.size) return;
  const interludeLines = new Set<number>();
  for (const line of plan.lines ?? []) if (line && line.interlude) interludeLines.add(line.index);
  const counts = new Map<number, number>();
  for (const cut of plan.cuts) {
    const line = cut.line;
    if (typeof line !== 'number' || line < 0) continue;
    const lyric = typeof cut.utext === 'string';
    if (!lyric && !interludeLines.has(line)) continue;
    const k = lyric ? counts.get(line) ?? 0 : 0;
    if (lyric) counts.set(line, k + 1);
    const spec = placement.get(line);
    if (!spec) continue;
    const own = spec.cuts.get(k) ?? {};
    const place: LyricPlace = {
      x: own.x ?? spec.line.x ?? 0,
      y: own.y ?? spec.line.y ?? 0,
      s: own.s ?? spec.line.s ?? 1,
    };
    if (place.x === 0 && place.y === 0 && place.s === 1) continue;
    PLACES.set(cut, place);
    if (cut.companion) PLACES.set(cut.companion, place);
  }
}

/** そのカットの位置ずらし（無ければ undefined）。 */
export function placeOf(cut: JizuraCut | null | undefined): LyricPlace | undefined {
  return cut ? PLACES.get(cut) : undefined;
}

const MARK = '__karakuriPlace';

/**
 * `J.CAMERA` の全エントリの `get` を包む（何度呼んでも 1 回しか包まない）。
 * 位置ずらしの紐づいていないカットでは、元の戻り値を**そのまま**返す。
 */
export function patchCameras(J: Jizura): void {
  for (const key of Object.keys(J.CAMERA)) {
    const camera = J.CAMERA[key] as (typeof J.CAMERA)[string] & { [MARK]?: boolean };
    if (!camera || typeof camera.get !== 'function' || camera[MARK]) continue;
    const original = camera.get;
    camera.get = function placedCamera(
      this: unknown,
      env: JizuraEnv,
      params: Record<string, unknown>,
    ): JizuraCameraState | null | undefined {
      const state = original.call(this, env, params);
      const place = env ? placeOf(env.cut) : undefined;
      if (!place) return state;
      const base: JizuraCameraState = state || {};
      return {
        ...base,
        x: (base.x || 0) + place.x * env.W,
        y: (base.y || 0) + place.y * env.H,
        s: (base.s ?? 1) * place.s,
      };
    };
    camera[MARK] = true;
  }
}
