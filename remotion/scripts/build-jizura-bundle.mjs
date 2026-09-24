#!/usr/bin/env node
/**
 * vendor/jizura/src/*.js を 1 本の ESM（vendor/jizura/dist/jizura.js）にまとめる。
 *
 * JIZURA 本体の `build.py` と同じく **`sorted(glob('src/*.js'))` の順で単純結合**する
 * だけ（本家は結合したものを 1 つの `<script>` に埋める）。各ファイルは
 * `(() => { 'use strict'; … })()` の IIFE で `J.xxx` を生やし、`01_util.js` の
 * 先頭にある `const J = (window.J = window.J || {})` を共有する。つまり
 * **同じスクリプトスコープに並んでいること**が動く条件なので、ここでも 1 つの
 * 関数の本体にそのまま並べる（無改変のまま使える）。
 *
 * webpack から `import` できるよう、外側だけ薄く包む:
 *
 *   let installed = null;
 *   function evalJizura() { …結合したソース…; return J; }
 *   export function loadJizura() { … }   // 2 回目以降は同じ J を返す
 *
 * 遅延評価にしてあるのは、`02_fonts.js` が読み込み時点で
 * `document.createElement('canvas')` を触るため。バンドルを import しただけでは
 * 何も起きず、ブラウザ（Remotion のヘッドレス Chrome）で `loadJizura()` を
 * 呼んだときに初めて評価される。
 *
 * 生成物 `dist/jizura.js` は **git に入れる**（Docker やクリーンチェックアウトで
 * ビルド手順を増やさないため）。src/ を差し替えたら必ずこれを実行し直すこと:
 *
 *   node remotion/scripts/build-jizura-bundle.mjs
 *
 * npm 依存は使わない（Node 18 以降の標準モジュールだけ）。
 */

import { readFileSync, readdirSync, writeFileSync, mkdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const VENDOR = join(HERE, '..', 'vendor', 'jizura');
const SRC_DIR = join(VENDOR, 'src');
const OUT_DIR = join(VENDOR, 'dist');
const OUT_FILE = join(OUT_DIR, 'jizura.js');

/** 本家 build.py の `sorted(glob('src/*.js'))` と同じ並び（バイト順）。 */
function sources() {
  return readdirSync(SRC_DIR)
    .filter((name) => name.endsWith('.js'))
    .sort();
}

function build() {
  const names = sources();
  if (!names.length) throw new Error(`${SRC_DIR} に .js がありません`);
  const body = names
    .map((name) => `/* ===== vendor/jizura/src/${name} ===== */\n${readFileSync(join(SRC_DIR, name), 'utf8')}`)
    .join('\n');

  // 先頭の関数宣言は巻き上げられるので、結合したソースの 'use strict' が
  // evalJizura() の directive prologue のまま残る（本家の 1 スクリプトと同じ扱い）。
  const out = `/* 自動生成: remotion/scripts/build-jizura-bundle.mjs が
   vendor/jizura/src/*.js を結合したもの。**直接編集しないこと**。
   取り込み元とライセンスは vendor/jizura/UPSTREAM.md と vendor/jizura/LICENSE。
   結合したファイル (${names.length} 本):
${names.map((n) => `     ${n}`).join('\n')} */
let installed = null;

function evalJizura() {
${body}
return J;
}

/** JIZURA のエンジンを評価して名前空間 \`J\` を返す（2 回目以降は同じものを返す）。
 *  ブラウザでのみ呼べる（読み込み時に document / window を使う）。 */
export function loadJizura() {
  if (installed) return installed;
  installed = evalJizura();
  return installed;
}

export default loadJizura;
`;
  mkdirSync(OUT_DIR, { recursive: true });
  writeFileSync(OUT_FILE, out, 'utf8');
  const kb = (Buffer.byteLength(out, 'utf8') / 1024).toFixed(0);
  console.log(`${OUT_FILE}: ${names.length} ファイル / ${kb} KB`);
}

build();
