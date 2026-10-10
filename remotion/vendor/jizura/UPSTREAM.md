# JIZURA（取り込み元）

`LyricMotion` コンポジションと `FxOverlay` の歌詞モーション層（どちらも
`remotion/src/LyricCanvas.tsx` が描く）が使う、文字PV 自動構成エンジン **JIZURA 字面**
のソース。**無改変**で置いてある（足りないところは下の「手元パッチ」でエンジンの外から補う）。

- 取り込み元: <https://github.com/852wa/JIZURA>
- コミット: `fc16bfe43ea4a6c25a21f1caf04d17326de14f00`
- 取り込み日: 2026-10-10
- ファイル: `src/*.js` のうち 38 本
- ライセンス: MIT（全文は隣の `LICENSE`。Copyright (c) 2026 hakoniwa）

### 取り込みの記録

| 取り込み日 | コミット | 本数 | 主な変化 |
| --- | --- | --- | --- |
| 2026-09-24 | `1b48bea` | 29 | 初回 |
| 2026-09-26 | `bae339e`（v0.9.0） | 38 | 部品セット 3 つ（文字PV系 `11p_typo*` / キネティック `11p_kinetic*` / ホラー `11p_horror*`、+153 部品・スタイル 3・雰囲気 `horror`、`11q_sets.js` の `J.SETS`）。`J.defaultProject()` に `typo` / `kinetic` / `horror` / `unify`（統一感）/ `typeset`（文字整列）/ `centerFree` / `centerDir`（中央を空ける）/ `locks`。overrides にカット単位の指名（`cuts` / `cutLayouts` / `cutTech`）とロックの中身（`lockedCuts`）。歌詞の `[間奏]` 行、モーフ（統一感）。トランジションは 20 → 27 種 |
| 2026-10-10 | `fc16bfe`（v0.10.1） | 38 | 本数・部品キー（860）・スタイル（27）は変わらず、変わったのは `03_text.js` / `06_layouts.js` / `08_planner.js` / `08b_omakase.js` / `11p_layoutsA.js` / `11p_layoutsC.js`（`09_render.js` は無変更）。おまかせのテーマ `J.THEMES` / `J.THEME_ORDER`（`J.omakase(project, rnd, themeId)` の第 3 引数）と `project.themeId`、飾りの数字・時刻を出さない `fx.hideNo` / `fx.hideTime`、overrides のカットの開始秒 `cutTime`、ラテン文字歌詞の単語単位の改行。LRC の時刻タグの無い行が末尾へ回らず直前の行の後ろに残る（`project.timingOrder: 2`、旧順との対応は `J.parseOrderV1`）、行の開始時刻は固定時刻（LRC・`lineTimes`）と推定の混在で解く。**前回の `bae339e` は本家の履歴から消えている**（履歴が書き換えられた模様。本家での v0.9.0 は `55817c4`） |

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
| `08b_omakase.js` | おまかせ（雰囲気からまとめて決める）とテーマ（`J.THEMES`） |
| `09_render.js` | 1 フレームの描画（`J.Renderer#frame`） |
| `10_audio.js` | 音源解析とビートグリッド（`J.beatGrid`） |
| `11p_*.js` | 表現パック 15 本（レイアウト・入り・抜け・装飾・質感・背景・カメラ・FX） |
| `11p_typo1〜3.js` | 部品セット「文字PV系」（`set: 'typo'`。既定 ON） |
| `11p_kinetic1〜3.js` | 部品セット「キネティック」（`set: 'kinetic'`。既定 ON） |
| `11p_horror1〜3.js` | 部品セット「ホラー」（`set: 'horror'`。既定 OFF、雰囲気 `horror` とスタイル 3 つ） |
| `11q_sets.js` | ランダムに選んでよい部品の集合（追加分 / 和風 / 部品セットの切り替え） |

部品セットのファイルは読み込み時に DOM へ触らない（砂嵐の画像などは描くときに初めて
`document.createElement('canvas')` する）ので、`export-jizura-catalog.mjs` の Node の
スタブでもそのまま評価できる。

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
`dist/jizura.d.ts`（LyricCanvas と手元パッチが使うところだけ）。

**`dist/jizura.js` を直接 import しないこと。** 読む入口は `remotion/src/lib/jizura.ts` の
`loadJizura()` 1 か所で、そこで手元パッチを 1 回だけ掛ける（描画の `LyricCanvas.tsx` も、
編集画面の「歌詞モーション」パネルが `@fx/lib/jizura` で読む部品の登録簿も同じ入口）。

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
部品グループのキー）が変わっていたら `remotion/src/LyricCanvas.tsx`（`buildProject()`）と
`remotion/src/schema.ts`（`lyricMotionSchema` / `lyricOverrideSchema`）、`dist/jizura.d.ts`、
編集画面（`frontend/src/components/studio/LyricInspector.tsx` / `lyric.ts`）を追随させること。
そのうえで下の「手元パッチ」の確認をやる。

## 手元パッチ（エンジンの外で包んでいるもの）

エンジンのソースは無改変のまま、`remotion/src/lib/jizura.ts` の `loadJizura()` が評価直後に
1 回だけ（`J.__karakuriPatched` で二重掛けを防ぐ）次の 2 つを掛ける。どちらも実行時に
部品の関数を包むだけで、**包んだ先に指定が無いとき（位置ずらしなし・不透明で描くとき）は
元の関数をそのまま呼ぶ**ので、不透明な絵は本家と 1 ビットも変わらない。

### (a) 行・カット単位の位置ずらし（`remotion/src/lib/jizuraOffset.ts`）

- props の `overrides[行]` の `offset: {x, y}`（画面比）・`scale`・`cutOffsets[カット番号]`
  を読む。これらのキーは `splitPlacement()` で**planner に渡す overrides から抜く**
  （`08_planner.js` は「行の指名が 1 つでもあれば統一感を掛けない」ので、位置だけの行が
  統一感から外れないように）。
- `LyricCanvas.tsx` の `prepare()` が `J.plan()` のあとに `attachPlacement()` を呼び、
  カットのオブジェクト（と `centerFree` の相方 `cut.companion`）→ ずらす量を `WeakMap` に
  紐づける。カット番号は「その行の `utext` のあるカット」の通し番号（本家の `cutTech` と
  同じ数え方）。行間の自動の間奏とタイトルは動かさない。
- `J.CAMERA[全キー].get(env, params)` を包み、`env.cut` に紐づいた量を戻り値の
  `x += offset.x × env.W` / `y += offset.y × env.H` / `s *= scale` に足す。`09_render.js` は
  カメラの値で座標を変えてから `drawCut` するので、文字・装飾・色ズレの残像が一緒に動く。
  `env.W` / `env.H` は `centerFree` のとき帯の大きさなので、ずらす量は帯に対する比になる。
- トランジション中の前カットは `frame()` の再帰で描き直されるが、`env.cut` が前カットなので
  前カットの位置で描かれる。モーフの字形の記録も `frame()` を通るので同じ。

**本家を更新したら確かめること:** `09_render.js` の content passes が今も
「`J.CAMERA[cut.cam].get(env, cut.camP)` → `translate(W/2 + cam.x, H/2 + cam.y)` → `scale(cam.s)`
→ `drawCut`」の順か、`makeEnv` の `env.W` / `env.H` / `env.cut`、`plan.cuts[].line` /
`utext` / `companion`、`plan.lines[].interlude` があるか。カメラの部品が `get` 以外の名前で
値を返すようになっていないか。

### (b) 透過で描くときにトランジションが画面を塗らない（`remotion/src/lib/jizuraTransparent.ts`）

本家のトランジション（`J.TRANS[k].draw(ctx, A, B, p, info)`）は不透明な絵が前提で、
「スタイルの背景色で全面を塗る」（`uncover` / `zoomThrough` / `doorsOpen` / `spinOut` /
`whipPan` / `knCornerSwing` / `knStutterCut`）、「黒を半透明で全面にかぶせる」（`cover` /
`doorsOpen` / `knStripSlam` / `hrBlink` のまぶた）、「背景色と黒を混ぜた色で全面を塗る」
（`cubeTurn`）、「A の上に B を重ねれば A は隠れる」（ほぼ全部）という描き方をする。透過で
重ねると下の映像が背景色や黒で塗りつぶされる（実例: ある MV で 224 秒中 14 回、19.83 秒の
`cubeTurn` で 12 フレームほぼ真っ黒）。

- `J.Renderer.prototype.frame` を包み、`opt.transparent` を覚えておく（入れ子の `frame()`
  ごとに退避・復帰）。
- `J.TRANS[全キー].draw` を包み、**透過のときだけ** ctx を Proxy に差し替えて呼ぶ:
  1. 描く前に ctx を消す
  2. `drawImage`（A / B / 作業用キャンバス）は「その矩形を置き換える」= 矩形を同じ不透明度で
     `destination-out` してから `lighter` で置く（不透明な絵での重ね = 線形補間と同じ式）
  3. 背景色（`info.sc` / `info.scPrev` / スタイル全配色の `bg`）と黒（`#000000` /
     `rgba(0,0,0,a)`、黒だけのグラデーション）の塗り・線は同じ不透明度で消す
  4. それ以外の色・パターンで画面の 9 割以上を塗るもの（フラッシュ・砂嵐・`cubeTurn` の奥）は
     `source-atop`（描かれている文字の上だけ）
  5. 全面でないアクセントの帯・線・枠はそのまま
  `info.tmp()` の作業用キャンバスも、`getContext('2d')` を一時的に差し替えて同じ規則で包む。

**本家を更新したら確かめること:** `09_render.js` の cut-to-cut transition 節が今も
「B = いまの ctx の写し、A = 前カットを `frame()` で描き直したもの、`J.TRANS[…].draw(ctx, A, B,
p, {cw, ch, sc, scPrev, st, tmp, …})`」の形か。新しく増えたトランジションが上の規則に収まるか
（背景色・黒以外の色で「面」を作っていないか、`globalCompositeOperation` を自分で切り替えて
いないか）。全トランジションを透過で描いて、色の付いた下地に重ねたときに全面単色のフレームが
出ないことを数値で確かめる（下地と焼き上がりを 64x36 に縮め、最多色の割合が下地より大きく
跳ねたフレームを数える。2026-09-26 の取り込みでは 27 種 × 4 スタイルで跳ね 0.03 以下）。

2026-10-10（v0.10.1）のやり方と結果: `FxOverlay`（640x360・24fps、`base` は
`gradient:135:#1f7a8c,#e2a03f`、`ambient` なし）の `lyric` に、28 行の歌詞を 1.6 秒おきに
`timing.lineTimes` で置き、全行 `layout: "center"` / `single: true` / `bg: "none"`、
2 行目以降の `trans` に 27 種を 1 つずつ割り当て（`fx.flash: false`、`horror: true`）、
スタイル noir / paper / hrNightRec / candy で 1 本ずつ焼く。`lyric` を抜いた下地だけの
1 本と並べ、各フレームを 64x36 に縮めて RGB を各 3 ビットに丸めた最多色の割合の差を取り、
各トランジションの窓（カットの頭から 1.2 秒）の最大を見る。結果は全 108 通りで 0.006 以下
（下地の最多色割合は 0.25）。同じ props を透過パッチを外して焼くと 0.63（`whipPan` /
`uncover` / `inkBlob` …、跳ね 0.1 超のフレーム 78）なので、この測り方で塗りつぶしは拾える。
窓の外では noir の 1 か所（行の終わりに画面が割れて落ちる絵柄）で 0.044 が出たが、
パッチを外しても v0.9 のエンジンでも同じ値で、トランジションではない。

**包んでいないもの:** レイアウト・装飾などの本体の絵柄は透過でもそのまま描く。幕（`curtain`）・
ジッパー（`zipper`）・障子（`shoji`）・新聞（`newspaper`）・ホラーの懐中電灯（`hrFlashlight`）や
扉の隙間（`hrDoorGap`）の暗闇などは、絵柄として画面の大半を覆う。画面効果（`J.FXE`）は本家の
`Renderer#alphaGuard` が透過用に面倒を見ている。

## フォント

JIZURA は Google Fonts（`fonts.googleapis.com` / `fonts.gstatic.com`）を実行時に
読む。`J.ensureFonts()` が `J.FONTS[key].gf`（例 `Noto+Sans+JP:wght@300;500;700;900`）
から `<link rel="stylesheet">` を足す作りなので、**ローカル化するときは
`J.FONTS[key].gf` を消して同名のフォントファミリを OS / `remotion/public/fonts/` から
読ませる**（詳しくは `remotion/README.md` の LyricMotion の節）。フォント自体は
取り込んでいない（いずれも SIL Open Font License 1.1）。
