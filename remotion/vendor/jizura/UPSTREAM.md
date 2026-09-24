# JIZURA（取り込み元）

`LyricMotion` コンポジション（`remotion/src/LyricMotion.tsx`）が使う、文字PV 自動構成
エンジン **JIZURA 字面** のソース。**無改変**で置いてある。

- 取り込み元: <https://github.com/852wa/JIZURA>
- コミット: `1b48bea2d74e60f9b0ff2c247aa5919ba03a6551`
- 取り込み日: 2026-09-24
- ファイル: `src/*.js` のうち 29 本
- ライセンス: MIT（全文は隣の `LICENSE`。Copyright (c) 2026 hakoniwa）

## 何を取ったか

upstream の `src/` にある「エンジン」だけ。`src/*.js` は
`(() => { 'use strict'; … })()` の IIFE で、`01_util.js` の先頭にある
`const J = (window.J = window.J || {})` に機能を生やしていく作りで、本家の
`build.py` は `sorted(glob('src/*.js'))` の順に**単純結合**して 1 つの `<script>` に
埋める。ここでも同じ順で結合する（`../../scripts/build-jizura-bundle.mjs`）。

| ファイル | 中身 |
| --- | --- |
| `01_util.js` | 数学・イージング・決定的な乱数・色 |
| `02_fonts.js` | フォント目録、Google Fonts の読み込み、字形の分解 |
| `02b_lang.js` | 歌詞の言語判定と言語ごとの書体 |
| `03_text.js` | 行組み・縦書き・ルビ |
| `04_styles.js` | スタイル（配色・質感）と合成用背景（`keyBg`） |
| `05_anim.js` | 入り・保持・抜けの基本アニメーション |
| `05b_registry.js` | 部品グループの登録簿（`J.register` / `J.order`） |
| `06_layouts.js` `07_decor.js` | 基本のレイアウトと装飾 |
| `08_planner.js` | 歌詞 → 行 → カット → イベントの構成（`J.plan`） |
| `08b_omakase.js` | おまかせ（雰囲気からまとめて決める） |
| `09_render.js` | 1 フレームの描画（`J.Renderer#frame`） |
| `10_audio.js` | 音源解析とビートグリッド（`J.beatGrid`） |
| `11p_*.js` | 表現パック 15 本（レイアウト・入り・抜け・装飾・質感・背景・カメラ・FX） |
| `11q_sets.js` | ランダムに選んでよい部品の集合（追加分 / 和風の切り替え） |

## 何を外したか（理由）

| ファイル | 理由 |
| --- | --- |
| `src/11_export.js` | ブラウザ内での MP4 / ZIP 書き出し（WebCodecs + mp4-muxer）。書き出しは Remotion がやるので不要。これを外したので upstream が同梱する **mp4-muxer（MIT）も取り込んでいない**。 |
| `src/12_ui.js` | 本家エディタの UI。DOM も `localStorage` も要り、Remotion では使わない。 |
| `app/` `ae/` `cep/` `tools/` `dev/` `index.html` `JIZURA_AE*.jsx` `JIZURA_CEP*.zip` | 本家のアプリ本体・After Effects / CEP 版・ビルド生成物。 |

`11_export.js` と `12_ui.js` を外しても、`J.defaultProject()` / `J.plan()` /
`new J.Renderer().frame()` は動く（本家の `dev/smoke_all.py` もこの 3 つだけで
全部品を描いている）。

## バンドル（Remotion から読むもの）

webpack は無改変の `src/*.js` をそのままは `import` できない（`J` を共有する前提の
素のスクリプトなので）。`remotion/scripts/build-jizura-bundle.mjs` が同じ順で結合して

    remotion/vendor/jizura/dist/jizura.js   （`loadJizura()` を export する ESM）

を作る。**この生成物は git に入れてある**。`run.sh` の Remotion 初期化は
`npm --prefix remotion install` しかしないので、生成をビルド手順に足すと Docker や
クリーンチェックアウトで一手増えてしまうため。型は手書きの
`dist/jizura.d.ts`（LyricMotion が使うところだけ）。

`src/` を触ったら**必ず**作り直すこと:

    node remotion/scripts/build-jizura-bundle.mjs
    node remotion/scripts/export-jizura-catalog.mjs   # 部品カタログも出し直す

## 更新手順

    remotion/scripts/sync-jizura.sh [ref]      # ref 既定: main

upstream を一時ディレクトリに clone し、`src/*.js`（除外ファイル以外）と `LICENSE` を
入れ替え、この UPSTREAM.md のコミット SHA・取り込み日・ファイル数を書き直し、
`dist/jizura.js` と部品カタログ
（`workspace/.agents/skills/karakuri-remotion/jizura-catalog.json`）を作り直す。

そのあと必ず確認する:

    cd remotion && npm run typecheck
    npx remotion render src/index.ts LyricMotion --props=examples/lyric-motion.json out/lyric-motion.mp4

API（`J.defaultProject()` の項目、`J.plan()` の戻り、`J.Renderer#frame` の引数、
部品グループのキー）が変わっていたら `remotion/src/LyricMotion.tsx` と
`remotion/src/schema.ts`、`dist/jizura.d.ts` を追随させること。

## フォント

JIZURA は Google Fonts（`fonts.googleapis.com` / `fonts.gstatic.com`）を実行時に
読む。`J.ensureFonts()` が `J.FONTS[key].gf`（例 `Noto+Sans+JP:wght@300;500;700;900`）
から `<link rel="stylesheet">` を足す作りなので、**ローカル化するときは
`J.FONTS[key].gf` を消して同名のフォントファミリを OS / `remotion/public/fonts/` から
読ませる**（詳しくは `remotion/README.md` の LyricMotion の節）。フォント自体は
取り込んでいない（いずれも SIL Open Font License 1.1）。
