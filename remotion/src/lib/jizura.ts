/**
 * JIZURA のエンジンを読む**唯一の入口**（手元パッチはここで 1 回だけ掛ける）。
 *
 * 描画（`LyricCanvas.tsx`。`LyricMotion` と `FxOverlay` の歌詞層、編集画面の
 * プレビューもこれ）と、編集画面の「歌詞モーション」パネルが選択肢を並べるための
 * **部品の登録簿**（`J.order()` / `J.registry()` / `J.STYLES` / `J.MOODS`）の両方が
 * ここから `loadJizura()` を取る。どちらも同じ「パッチ済みの J」を使うので、
 * プレビューと書き出しで絵が食い違わない。SPA からは `@fx/lib/jizura` を**動的
 * import** してここだけを読む（バンドル約 2MB ぶんを初期表示に載せないため）。
 *
 * エンジン本体（`vendor/jizura/src/*.js`）は**無改変**のまま置く（本家の更新を
 * `remotion/scripts/sync-jizura.sh` で取り込みやすくするため）。足りないところは
 * エンジンの外（このディレクトリ）で、実行時に部品を包んで補う:
 *
 *   - `jizuraOffset.ts` … 行・カット単位の位置ずらし（カメラの `get` を包む）
 *   - `jizuraTransparent.ts` … 透過で描くときにトランジションが画面を塗らない
 *     （`Renderer#frame` と `J.TRANS[*].draw` を包む）
 *
 * 何をどう包んでいるか・本家を更新したら何を確かめるかは
 * `vendor/jizura/UPSTREAM.md` の「手元パッチ」の節。
 */

import { loadJizura as loadRawJizura } from '../../vendor/jizura/dist/jizura.js';
import type { Jizura } from '../../vendor/jizura/dist/jizura.js';
import { patchCameras } from './jizuraOffset';
import { patchTransparentTransitions } from './jizuraTransparent';

export type {
  Jizura,
  JizuraAudioLike,
  JizuraEntry,
  JizuraGroup,
  JizuraPlan,
  JizuraProject,
  JizuraRenderer,
  JizuraSetKey,
} from '../../vendor/jizura/dist/jizura.js';
export { attachPlacement, splitPlacement } from './jizuraOffset';
export type { LyricPlace, LyricPlacement } from './jizuraOffset';

const PATCHED = '__karakuriPatched';

/**
 * エンジンを評価し、手元パッチを掛けた `J` を返す（2 回目以降は同じもの）。
 * ブラウザ専用（読み込み時に document / window を使う）。
 */
export function loadJizura(): Jizura {
  const J = loadRawJizura() as Jizura & { [PATCHED]?: boolean };
  if (!J[PATCHED]) {
    patchCameras(J);
    patchTransparentTransitions(J);
    Object.defineProperty(J, PATCHED, { value: true });
  }
  return J;
}

export default loadJizura;
