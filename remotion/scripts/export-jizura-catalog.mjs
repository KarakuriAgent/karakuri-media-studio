#!/usr/bin/env node
/**
 * JIZURA の部品カタログ（エージェントが `overrides` に書くキーの一覧）を JSON に出す。
 *
 *   node remotion/scripts/export-jizura-catalog.mjs \
 *     [出力先.json]   # 既定: workspace/.agents/skills/karakuri-remotion/jizura-catalog.json
 *
 * 中身:
 *   groups  … `J.order(group)` の順で `{key: {name, tags, pack, extra, wa}}`
 *   styles  … `J.STYLE_ORDER` の順
 *   moods   … `J.MOODS`（おまかせの雰囲気）
 *   fonts   … `J.FONTS`（`project.fonts` に指定できるフォントキー）
 *   aspects … `J.designSize()` が知っているアスペクト比とデザインサイズ
 *
 * JIZURA のエンジンは読み込み時に DOM（`document.createElement('canvas')` など）を
 * 触るので、Node では**最小限のスタブ**を差してから評価する。描画はしないため、
 * 何を呼ばれても自分を返すだけの Proxy で足りる（カタログの中身は登録簿を読むだけ）。
 * npm 依存は使わない。
 */

import { writeFileSync, mkdirSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const BUNDLE = join(HERE, '..', 'vendor', 'jizura', 'dist', 'jizura.js');
const DEFAULT_OUT = join(
  HERE, '..', '..', 'workspace', '.agents', 'skills', 'karakuri-remotion', 'jizura-catalog.json',
);

/** 何をしても自分を返すだけの偽 DOM オブジェクト。 */
const stub = new Proxy(function stubbed() {}, {
  get: (_target, key) => {
    if (key === 'width' || key === 'height') return 0;
    if (key === Symbol.toPrimitive) return () => 0;
    return stub;
  },
  set: () => true,
  apply: () => stub,
  construct: () => stub,
});

function installDom() {
  globalThis.window = globalThis;
  globalThis.document = {
    createElement: () => stub,
    head: null,          // フォントの <link> 追加を黙って諦めさせる
    fonts: undefined,    // J.ensureFonts を呼ばないので未定義でよい
    documentElement: stub,
    body: stub,
  };
  globalThis.FontFace = function FontFace() {};
}

/** 登録簿 1 グループ分を `{key: {name, tags, pack, …}}` に。 */
function groupCatalog(J, group) {
  const registry = J.registry(group) || {};
  const out = {};
  for (const key of J.order(group)) {
    const def = registry[key];
    if (!def) continue;
    const entry = { name: def.name, pack: def.pack || 'core' };
    if (def.tags && def.tags.length) entry.tags = [...def.tags].sort();
    if (def.extra) entry.extra = true;   // 追加分（project.extra=true のときだけランダムに選ばれる）
    if (def.wa) entry.wa = true;         // 和風（project.wa=false でランダムから外れる）
    if (def.special) entry.special = true;
    out[key] = entry;
  }
  return out;
}

async function main() {
  installDom();
  const { loadJizura } = await import(BUNDLE);
  const J = loadJizura();

  const groups = {};
  for (const group of J.GROUP_KEYS) groups[group] = groupCatalog(J, group);

  const styles = {};
  for (const key of J.STYLE_ORDER) {
    const style = J.STYLES[key];
    if (!style) continue;
    styles[key] = { name: style.name || key };
    if (style.moods && style.moods.length) styles[key].moods = [...style.moods];
    if (style.extra) styles[key].extra = true;
    if (style.wa) styles[key].wa = true;
  }

  const moods = {};
  for (const [key, mood] of Object.entries(J.MOODS)) moods[key] = { name: mood.name };

  const fonts = {};
  for (const [key, font] of Object.entries(J.FONTS)) {
    fonts[key] = { label: font.label, kind: font.kind };
    if (font.extra) fonts[key].extra = true;
  }

  const aspects = {};
  for (const aspect of ['16:9', '9:16', '4:3', '3:4', '1:1', '4:5', '21:9']) {
    const [W, H] = J.designSize(aspect);
    aspects[aspect] = { designWidth: W, designHeight: H };
  }

  const total = Object.values(groups).reduce((n, g) => n + Object.keys(g).length, 0);
  const catalog = {
    _note:
      'remotion/scripts/export-jizura-catalog.mjs が生成。LyricMotion の overrides / style / mood に'
      + ' 書けるキーの一覧。手で編集しないこと。',
    generatedFrom: 'remotion/vendor/jizura (UPSTREAM.md にコミット SHA)',
    counts: { parts: total, styles: Object.keys(styles).length, fonts: Object.keys(fonts).length },
    groups, styles, moods, fonts, aspects,
    sampleLyrics: J.SAMPLE_LYRICS,
  };

  const out = resolve(process.argv[2] || DEFAULT_OUT);
  mkdirSync(dirname(out), { recursive: true });
  writeFileSync(out, JSON.stringify(catalog, null, 2) + '\n', 'utf8');
  console.log(`${out}: 部品 ${total} / スタイル ${catalog.counts.styles} / フォント ${catalog.counts.fonts}`);
}

await main();
