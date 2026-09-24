/**
 * JIZURA のエンジンだけを取り出す入口。
 *
 * 描画（`LyricCanvas.tsx`）は Remotion の中でしか動かないが、**部品の登録簿**
 * （`J.order()` / `J.registry()` / `J.STYLES` / `J.MOODS`）は素の JavaScript で、
 * 編集画面の「歌詞モーション」パネルが選択肢を並べるのに要る。SPA からは
 * `@fx/lib/jizura` を**動的 import** してここだけを読む（バンドル約 1MB ぶんを
 * 初期表示に載せないため）。
 */

export { loadJizura } from '../../vendor/jizura/dist/jizura.js';
export type {
  Jizura,
  JizuraEntry,
  JizuraGroup,
} from '../../vendor/jizura/dist/jizura.js';
