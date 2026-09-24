# karakuri-remotion

karakuri-media-studio に**同梱**されている **Remotion レンダリングプロジェクト**です。

アプリはこのディレクトリを Remotion のバックエンドとして参照し、
コンポジション ID と props(JSON)を渡して `npx remotion render` を実行します。
props を書くのは人ではなく **AI エージェント**である、というのが設計の前提です。

- カット繋ぎ + 単純なテロップ → アプリ内蔵の **ffmpeg エクスポート**
- MV・モーショングラフィックス(歌詞アニメーション、ビート同期、トランジション) → **この Remotion プロジェクト**

## セットアップ

Node v24 系で動作確認しています。依存は `run.sh` が初回に入れます（Docker で動かす
場合はホスト側で `npm --prefix remotion install`）。手で入れるなら:

```bash
cd remotion
npm install
```

初回のレンダリング時に Remotion が Chrome Headless Shell を自動でダウンロードします(約 90MB)。
ネットワークが制限された環境では、あらかじめ次のコマンドで落としておいてください。

```bash
npx remotion browser ensure
```

プレビュー(Remotion Studio):

```bash
npx remotion studio
# → http://localhost:3000
```

## コンポジション

| ID | 用途 | 尺 |
|---|---|---|
| `MusicVideo` | カット割り・トランジション・歌詞・BGM を props で組み上げる MV 本体 | props から自動算出 |
| `FxOverlay` | 出来上がった映像(mp4)の上に、イベント駆動で文字演出・エフェクトを載せる | props から自動算出 |
| `LyricMotion` | 歌詞から文字PV(リリックモーション)を自動で組み立てる。中身は OSS の [JIZURA 字面](https://github.com/852wa/JIZURA)(MIT) | props から自動算出 |
| `Slate` | 動作確認用。props のテキストを表示するだけ | `durationInSeconds`(既定 5 秒) |

解像度・fps・尺はすべて props から `calculateMetadata` で決まります。
`<Composition>` に書いてある `1920x1080 / 30fps` は Studio 用の初期値にすぎません。

## レンダリング

```bash
# 動作確認用スレート
npx remotion render src/index.ts Slate out/slate.mp4 --props=examples/slate.json

# サンプル MV(外部素材に依存しない 8 秒)
npx remotion render src/index.ts MusicVideo out/music-video.mp4 --props=examples/music-video.json

# 演出レイヤーのサンプル(全イベント型を 1 回ずつ含む 14 秒)
npx remotion render src/index.ts FxOverlay out/fx-overlay.mp4 --props=examples/fx-overlay.json

# 文字PV のサンプル(外部素材に依存しない 5 秒)
npx remotion render src/index.ts LyricMotion out/lyric-motion.mp4 --props=examples/lyric-motion.json

# コンポジション一覧(アプリ側の一覧取得 API と同じもの)
npx remotion compositions src/index.ts
```

`--props` にはファイルパスのほか、JSON 文字列も直接渡せます。

## props スキーマ

正本は [`src/schema.ts`](src/schema.ts)(zod)です。以下はその要約。
**時間の単位はすべて「秒」**、座標は左上原点の `width x height` ピクセル空間です。

### `MusicVideo`

```jsonc
{
  "fps": 30,
  "width": 1920,
  "height": 1080,
  "durationInSeconds": 8.0,        // 省略時は cuts / lyrics / title の終端から自動算出
  "backgroundColor": "#000000",

  "audio": {
    "src": "http://localhost:8000/outputs/xxxx/bgm.mp3",
    "volume": 1.0,
    "startFrom": 0,                 // 音源側の頭出し秒
    "fadeOut": 1.5                  // 末尾のフェードアウト秒(0 で無効)
  },

  "cuts": [
    {
      "src": "http://localhost:8000/outputs/xxxx/clip1.mp4",
      "start": 0.0,                 // タイムライン上の開始秒
      "in": 0.5,                    // 素材側の頭出し秒(動画のみ)
      "duration": 4.0,              // 画面に出す長さ
      "fit": "cover",               // cover | contain | fill
      "volume": 0,                  // 動画素材の音量(既定 0 = 無音)
      "playbackRate": 1,
      "opacity": 1,
      "filter": "saturate(1.15)",   // 任意の CSS filter
      "transition": {               // このカットが「入ってくる」ときの演出
        "type": "crossfade",        // cut | crossfade | fadeblack | fadewhite | slide | wipe
        "duration": 0.4,
        "direction": "left"         // slide / wipe のみ: left | right | up | down
      }
    },
    {
      "src": "http://localhost:8000/outputs/xxxx/still.png",
      "start": 4.0,
      "duration": 2.0,
      "kenBurns": { "from": 1.0, "to": 1.15, "originX": 0.5, "originY": 0.5 }
    }
  ],

  "beats": [0.0, 0.52, 1.04],       // ビート時刻(秒)。beatPulse を使うときだけ必要
  "beatPulse": true,                 // ビート直後に画面全体を軽く拡大(1.00 → beatPulseScale)
  "beatPulseScale": 1.02,

  "lyrics": [
    {
      "text": "歌詞の一行",
      "start": 1.0,
      "end": 3.5,
      "style": "karaoke",           // fade | karaoke | pop
      "position": "bottom",         // top | center | bottom
      "fontSize": 64,               // 1080p 基準。height に応じて自動スケール
      "color": "#ffffff",           // karaoke では「まだ歌っていない」色
      "highlightColor": "#ffd54a",  // karaoke で色が送られたあとの色
      "outlineColor": "rgba(0,0,0,0.75)",
      "bold": true
    }
  ],

  "title": {
    "text": "曲名",
    "artist": "アーティスト名",
    "showUntil": 2.0,               // この秒数まで表示し、直前 0.6 秒でフェードアウト
    "position": "bottomLeft",       // topLeft | center | bottomLeft
    "color": "#ffffff",
    "fontSize": 72
  }
}
```

必須なのは `cuts[].src` / `start` / `duration`、`lyrics[].text` / `start` / `end`、`title.text`、`audio.src` だけで、
残りは既定値が入ります(既定値は `src/schema.ts` の `.default()` 参照)。

#### トランジションの挙動

トランジションは「入ってくる側のカット」に書きます。前のカットは必要なぶんだけ自動で延長されます。

| type | 挙動 |
|---|---|
| `cut` | 瞬間切り替え(既定) |
| `crossfade` | `duration` 秒かけて前のカットに重ねながらフェードイン |
| `fadeblack` / `fadewhite` | `duration` 秒かけて黒(白)に沈み、明ける。切り替わりは中央 |
| `slide` | `direction` の向きに `duration` 秒かけてスライドイン |
| `wipe` | `direction` の向きに `duration` 秒かけてワイプ |

#### `src` に書けるもの

| 形式 | 例 | 用途 |
|---|---|---|
| http(s) URL | `http://localhost:8000/outputs/xxxx/clip1.mp4` | **推奨。** アプリの `/outputs` 配信をそのまま渡す |
| 絶対パス | `/mnt/data/clip1.mp4` | `file://` に変換して読む |
| `public/` 相対パス | `logo.png` | このリポジトリの `public/` に置いた固定素材 |
| 疑似ソース(単色) | `color:#223344` | 素材なしで色面を出す |
| 疑似ソース(グラデーション) | `gradient:135:#223344,#5566aa` | 角度は省略可(既定 180) |

拡張子が `.mp4` / `.webm` / `.mov` / `.mkv` / `.m4v` / `.avi` なら動画、それ以外は静止画として扱います。

### `FxOverlay`

**タイムラインで組み上げた mp4 の上に、演出のレイヤーを載せる**ためのコンポジション。
`MusicVideo` が「カットを並べて 1 本にする」のに対して、こちらは「もう出来ている 1 本に
文字とエフェクトを足す」。BAN!BAN!BAN! の MV で使った演出コードを props 駆動に一般化したもの。

- 時間は秒、位置と大きさは**画面比(0..1)**、`fontSize` は 1080p 基準。解像度・fps に依存しない
- 秒 → フレームの変換は `round(t * fps)`(切り上げると決めが 1 フレーム遅れる)
- 傾き・横ずれ・ノイズの位置は `seed` から決まるので、同じ props なら毎回同じ絵になる
- **イベントを書かなければ何も乗らない**。無音区間・見せ場でない所には単に置かない

```jsonc
{
  "fps": 24, "width": 1280, "height": 720,
  "base":  { "src": "http://localhost:8000/outputs/xxxx/video.mp4", "muted": true },
  "audio": { "src": "http://localhost:8000/outputs/yyyy/audio.wav", "startFrom": 0 },
  "durationInSeconds": 197.0,        // 省略時は base の尺と events の終端の大きいほう
  "theme": {
    "palette": ["#dc1428", "#f5f5f5", "#08080a"],   // [accent, fg, bg]
    "fontFamily": "", "monoFamily": ""              // 空なら src/fonts.ts の既定
  },
  "ambient": { "scanline": false, "vignette": false },
  "seed": 1,
  "events": [
    { "t": 43.9, "type": "card", "text": "BAN", "frames": 5,
      "sequence": ["accent/fg", "fg/accent", "bg/fg"] },
    { "t": 44.11, "type": "invertShake", "frames": 3, "shakeTail": 0.15 },
    { "t": 45.96, "until": 47.9, "type": "imageSlam",
      "src": "http://localhost:8000/outputs/zzzz/logo.png",
      "cx": 0.5, "cy": 0.76, "w": 0.62, "maxH": 0.4, "flash": 0.55, "spring": true }
  ]
}
```

`events[]` は `type` で形が変わる(zod の discriminated union)。共通のフィールドは

| フィールド | 意味 |
|---|---|
| `t` | 開始秒(**必須**) |
| `until` / `duration` | 終わり。どちらも書かなければ型ごとの既定尺 |
| `seed` | 乱数の種。省略時は `seed`(全体)と並び順と `t` から決まる |
| `z` | 重なりの順(小さいほど下)。省略時は型ごとの既定層。`screen` の上に何か出したいときだけ書く |

重なりの既定層は `src/FxOverlay.tsx` の `EVENT_LAYER`(下から `invertShake`/`collapse`=0 →
`glitchCut`=1 → `beatMarker`=2 → `sprite`/`stickerStack`/`shape`=3 → `imageSlam`=4 →
`lyric`/`terminalText`/`credits`=5 → `screen`=6 → `card`=7 → `endCard`=8 → `crtOff`=9)。
同じ層なら `events` に書いた順。`z` は小数で書けるので、黒画面(`screen`)の上に歌詞を残すなら
その `lyric` に `"z": 6.5` を書く。

イベント型は次の 15 種。

| type | 何が起きるか | よく使うフィールド |
|---|---|---|
| `card` | 全画面の色地に極太文字を数フレーム叩き込む | `text` / `frames` / `sequence`("背景色/文字色") / `jitterDeg` / `jitterPx` / `wipe` / `chroma` / `halftone`(`true` or `{alpha,dot}`) |
| `invertShake` | 反転(ネガ)数フレーム + 減衰シェイク。`card` の直後に置く | `frames` / `shakeTail` / `amplitude` / `mode`(`invert` \| `flash`) / `hitStop` |
| `imageSlam` | 決め台詞の画像を叩き込む | `src` / `cx` / `cy` / `w` / `maxH` / `snap` / `spring` / `flash` / `tint` / `outGlitch` |
| `terminalText` | 等幅の端末表示。`then` で同じ場所を差し替え | `lines` / `then` / `frames` / `corner` または `cx`/`cy` / `margin` / `typing` / `cps` / `cursor` / `outGlitch` |
| `screen` | 全画面を塗る板(黒画面・タイトルカード) | `bg` / `text` / `src` / `glitch` |
| `glitchCut` | 走査線ずれ + ブロックノイズを数フレーム | `frames` / `displace` / `blocks` / `chroma` |
| `collapse` | 画面をタイルに割って落とす | `cols` / `rows` / `fallSeconds` |
| `crtOff` | CRT の電源断(横一線 → 白点 → 消灯) | `frames` |
| `sprite` | 透過画像を 1 枚貼る | `src` / `anchor` または `cx`/`cy` / `w` / `maxH` / `motion` / `tint` / `border`(`inset` 可) / `halftone`(`0..1` or `{alpha,dot}`) / `jitter` / `outGlitch` |
| `stickerStack` | 同じ画像をキーフレームの位置へ次々に貼って積む | `src` / `target.keyframes[]`(`t`/`x`/`y`/`w`/`rot`/`visible`/`pop`) / `blowOutAt` / `border`(`inset` 可) / `halftone`(`0..1` or `{alpha,dot}`) / `jitter` |
| `credits` | 隅の小さなクレジット(白 + 縁取り) | `lines`(文字列 or `{text,fontSize,color}`) / `corner` または `cx`/`cy` / `fontSize` |
| `lyric` | 歌詞テロップ。行そのまま or 1 文字送り | `text` / `chars[]`(`c`/`s`) / `style`(`line` \| `karaoke`) / `position` / `outGlitch` |
| `endCard` | 終わりの黒 + ロゴ | `black` / `logo`(`src`/`duration`/`w`/`tint`) / `text` |
| `beatMarker` | 隅で拍を刻むマーカー列(間奏の間つなぎ) | `beat` / `count` / `corner` / `label` / `glitchEvery` |
| `shape` | SVG で描く記号 | `shape` / `cx` / `cy` / `size` / `fill` / `stroke` / `motion` |

`shape` で描けるもの: `bolt`(雷) / `heart` / `speedlines`(集中線) / `bubble`(吹き出し・`text` 可) /
`star` / `circle` / `arrow` / `burst`(爆発) / `cross`(ばつ)。
`motion` は `none` / `pop` / `float` / `spin` / `shake` / `stamp`。

色は `theme.palette` の役割名(`accent` = 0 / `fg` = 1 / `bg` = 2)か番号(`"0"`)で書けるほか、
CSS の色(`#dc1428` / `red`)をそのまま書いてもよい。

`base.src` / `sprite.src` などに書けるものは `MusicVideo` の `cuts[].src` と同じ(上の「`src` に書けるもの」)。
サンプルは `examples/fx-overlay.json`(外部素材ゼロ・全イベント型を 1 回ずつ・14 秒。
`z` / `card.wipe` / `invertShake.hitStop` / `sticker` の `border`・`halftone`・`jitter` / `tint` /
`outGlitch`(出際を走査線ずれ + RGB 分離で飛ばして消す)・`border.inset`・キーフレームの `pop: false` など
追加オプションも一通り入っている)。

#### `lyric`: 歌詞モーション(JIZURA)の層を重ねる

`lyric` を書くと、`base` の上・`events` の下に **JIZURA の文字PV が透過で 1 枚**重なります
(`src/LyricCanvas.tsx`。`LyricMotion` と同じ描画を共有していて、違うのはキャンバスの
実寸の決め方だけ)。中身は下の `LyricMotion` の props から、**この props が持っている値**
——`fps` / `width` / `height` / `durationInSeconds` / `res` / `aspect` と `audio` の再生
まわり(`src` / `volume` / `startFrom` / `fadeOut`)——を抜いたもの(`src/schema.ts` の
`lyricOverlaySchema` = `lyricMotionSchema` からの派生)。拍(`audio.beats`)とエネルギーは
そのまま置けます。

画面比は `width` / `height` にいちばん近い `aspect` を選び、そのデザインサイズを
**cover** で収めます(`keyBg` は層として重ねる以上つねに透過)。サンプルは
`examples/fx-overlay-lyric.json`:

```bash
npx remotion render src/index.ts FxOverlay out/fx-lyric.mp4 --props=examples/fx-overlay-lyric.json
```

編集画面(タイムラインの FX トラック)に保存する形も同じで、`PUT /api/v1/timelines/{id}/fx/lyric`
に `lyric` をそのまま入れます(SPEC §7.3)。

### `LyricMotion`

歌詞テキストを入れると、行を切って・レイアウトを選んで・入り / 保持 / 抜けの
アニメーションを割り当てて、文字PV を 1 本組み上げます。エンジンは OSS の
**JIZURA 字面**(MIT)で、ソースは `vendor/jizura/` に**無改変**で置いてあります
(取り込み元・除外したもの・更新手順は [`vendor/jizura/UPSTREAM.md`](vendor/jizura/UPSTREAM.md))。

props の正本は `src/schema.ts` の `lyricMotionSchema`。最小はこれだけです。

```jsonc
{
  "lyrics": "[00:00.30]夜明けの色を/覚えてる\n[00:01.40]ほどけた声が遠くで鳴った",
  "style": "noir",
  "seed": 20260922
}
```

| props | 意味 |
|---|---|
| `lyrics` | 歌詞。1 行 1 フレーズ。記法は下記 |
| `style` | 配色と質感(24 種)。**空文字なら任せる**(`mood` があればそれに合うものが選ばれる) |
| `mood` | `glitch` / `calm` / `pop` / `graphic` / `editorial` / `emotional` / `chaos`。書くと「おまかせ」で部品とスライダーをまとめて選ぶ |
| `seed` | 乱数の種。同じ種・同じ歌詞なら毎回同じ絵 |
| `aspect` / `res` / `fps` | `16:9` `9:16` `4:3` `3:4` `1:1` `4:5` `21:9` / 短辺のピクセル数 / フレームレート |
| `keyBg` | `off` / `green`(グリーンバック) / `black`(ブラックバック) / `transparent`(下地を塗らない) |
| `extra` / `wa` | 初版より後に足された部品(追加分) / 和風モチーフをランダムに選んでよいか |
| `fx` | 演出の強さ。`motion` `glitch` `chroma` `decor` `density` `texture` `bgSwitch`(0..1)、`koma`(1 秒あたりの作画枚数)、`hud` |
| `enabled` | 使ってよい部品の絞り込み。`{"layout": {"tile": false}}` のように**書いたところだけ**効く |
| `overrides` | 行番号(0 始まり)ごとの指名。`{"3": {"layout": "huge", "exit": "explode"}}` |
| `colors` / `fonts` | 配色 / 書体の役割ごとの差し替え |
| `timing` | `bpm` `offset` `snap` `tail` `lineScale` `lineTimes` |
| `audio` | BGM(`src`)と解析結果(`beats` / `duration` / `energy` / `energyRate`) |
| `durationInSeconds` | 明示的な尺。省略すると構成の終端から決まる |

歌詞の記法:

- `[mm:ss.xx]` 行頭のタイムスタンプ(LRC)。**全行に付ければ**そのまま時刻になる
- `/` でその行をカットに割る
- `*強調*` でその語を強く出す
- 行末の `!` で決めのカットにする
- `歌詞|注釈` で小さな注釈を添える
- `#` で始まる行はコメント、空行は間

**部品キーの一覧**(`layout` / `enter` / `hold` / `exit` / `decor` / `treat` / `bg` /
`cam` / `fx` / `trans` の中身、スタイル、雰囲気、書体)は
[`workspace/.agents/skills/karakuri-remotion/jizura-catalog.json`](../workspace/.agents/skills/karakuri-remotion/jizura-catalog.json)
にすべて並んでいます(部品 707・スタイル 24・書体 23)。作り直すには:

```bash
node scripts/export-jizura-catalog.mjs
```

#### 透過・グリーンバックで焼く

`keyBg: "transparent"` は下地を塗らずアルファを残すので、アルファを持てる
コンテナで書き出します(mp4 / h264 はアルファを持てません)。

```bash
# ProRes 4444(.mov)
npx remotion render src/index.ts LyricMotion out/lyric.mov --props=examples/lyric-motion.json \
  --codec=prores --prores-profile=4444 --image-format=png --pixel-format=yuva444p10le

# VP8 の WebM(.webm。軽い)
npx remotion render src/index.ts LyricMotion out/lyric.webm --props=examples/lyric-motion.json \
  --codec=vp8 --image-format=png --pixel-format=yuva420p
```

アプリのジョブから焼くときは `remotion_render_options`(props とは別物)で同じことを
指定します(下の「karakuri-media-studio 側の設定」)。合成側が透過を扱えないときは
`keyBg: "green"` / `"black"` で、全カットを白文字 + 単色背景にした形でも出せます。

#### エンジンを更新する

```bash
scripts/sync-jizura.sh          # upstream を clone して vendor/jizura/src を入れ替える
node scripts/build-jizura-bundle.mjs   # vendor/jizura/dist/jizura.js を作り直す(git に入れる)
```

`vendor/jizura/src/*.js` は素のスクリプト(グローバル `J` を共有する前提)なので
webpack からそのままは読めません。`build-jizura-bundle.mjs` が JIZURA 本体の
`build.py` と同じ順で結合し、`loadJizura()` を export する ESM にします。
**生成物 `vendor/jizura/dist/jizura.js` は git に入れてあります**
(`run.sh` の Remotion 初期化は `npm --prefix remotion install` だけなので、
Docker やクリーンチェックアウトで手順を増やさないため)。

#### 並列レンダリングと再現性

`frame(ctx, plan, t)` は時刻 t だけから絵を決める作りで、カット間のトランジションも
前カットの絵を**その場で描き直して**合成します。つまり Remotion が複数のタブに
フレームをばらまいても構いません。グレインと紙テクスチャだけは `Math.random()` で
作られるので、`LyricCanvas.tsx` が Renderer を作る間だけ種付きの乱数に差し替えて
揃えています。

残る差は字形の破片に振られるグローバル連番 id(どの字を先に描いたかで変わる)由来で、
実測(1920x1080 / 120 フレーム)で `--concurrency=1` と `--concurrency=4` の間に
差が出たのは 22 枚・最大 19/255・画面平均 0.001/255(PSNR 66〜94dB)でした。目には
見えませんが、**ビット単位で揃えたいときは `--concurrency=1`** で焼いてください。

### `Slate`

```jsonc
{
  "text": "karakuri-remotion",
  "subtitle": "動作確認",
  "fps": 30,
  "width": 1920,
  "height": 1080,
  "durationInSeconds": 5,
  "backgroundColor": "#101820",
  "color": "#ffffff"
}
```

## karakuri-media-studio 側の設定

アプリの設定画面の「Remotion 連携」を **ON** にします(既定は OFF)。
アプリが使うのは**常にこの同梱ディレクトリ(`remotion/`)**です。コンポジションを
足す・直すときは `remotion/src/` を編集してください。

依存は `run.sh` が初回に入れています(入っていないと、レンダリング時にその旨の
エラーが出ます。その場合は上の「セットアップ」を参照)。
ON にする前に、**Remotion のライセンス**(このファイル末尾)に目を通してください。

これでアプリ側から次のことができます。

- コンポジション一覧の取得(`npx remotion compositions src/index.ts` 相当)
- コンポジション ID + props JSON を渡してのレンダリングジョブ投入。進捗は WebSocket、出力は `outputs/` に置かれ、
  通常のジョブと同じように履歴・ライブラリ・素材登録・タイムラインの素材ビンに乗ります

素材はアプリが `/outputs` 配下で静的配信しているので、その URL を `cuts[].src` / `audio.src` にそのまま書けます。
ダウンロードして置き直す必要はありません。

## エージェント向け SKILL

props の書き方・ビート同期の作法・運用ルールは外部エージェントの作業フォルダにある
[`workspace/.agents/skills/karakuri-remotion/SKILL.md`](../workspace/.agents/skills/karakuri-remotion/SKILL.md) にまとめてあります。
`workspace/.claude/skills/karakuri-remotion` はそこへのシンボリックリンクです。

## フォント

`MusicVideo` / `FxOverlay` / `Slate` の歌詞・タイトルは、レンダリングマシンに
インストールされている CJK フォントを使います(`src/fonts.ts` の `FONT_FAMILY`)。
Web フォントは使いません。日本語が豆腐になる場合は `fonts-noto-cjk` 等を入れてください。

**`LyricMotion` だけは別**で、JIZURA が持つ書体目録(`vendor/jizura/src/02_fonts.js` の
`J.FONTS`。Noto Sans/Serif JP・Dela Gothic One・Zen Old Mincho など 23 種)を
**Google Fonts から実行時に取ってきます**。ネットワークが無い環境ではフォールバックの
書体で焼かれます(止まりはしません)。

オフラインでも同じ絵にしたい場合は、`J.FONTS[key].gf`(Google Fonts の family 指定)を
消して、同名のファミリをローカルから読ませます。手順:

1. 必要な書体(すべて SIL Open Font License 1.1)を `public/fonts/` に置く
2. `src/LyricCanvas.tsx` で `loadJizura()` の直後に、`@font-face` を
   `staticFile('fonts/…')` で流し込み、`J.FONTS` の各エントリから `gf` を消す
   (`delete J.FONTS[key].gf`)。`J.ensureFonts()` は `gf` が無いエントリについては
   `<link>` を足さず、`document.fonts.load()` だけを行うので、ローカルに同名の
   ファミリが在れば同じ結果になります
3. OS に入っている書体で済ませたいなら `J.FONTS[key].family` を差し替える手もあります

第 1 段では同梱していません(取り込んでいるのはエンジンのソースだけ)。

## ライセンス

**Remotion 自体のライセンスに注意してください。**
Remotion は個人・非営利、および従業員 3 人以下の企業であれば無料で利用できますが、
それを超える規模の企業が利用する場合は会社ライセンス(Remotion Company License)の購入が必要です。
詳細は https://www.remotion.dev/license を参照してください。

アプリ側で Remotion 連携が**既定で OFF** になっているのはこのためです。設定画面で有効にする前に、
ライセンス条件を満たしていることを確かめてください。

このディレクトリ自身のコードは karakuri-media-studio プロジェクト内部での利用を想定しています。

### 第三者のソフトウェア

| 取り込んだもの | 場所 | ライセンス |
|---|---|---|
| [JIZURA 字面](https://github.com/852wa/JIZURA)(文字PV 自動構成エンジン。`LyricMotion` の中身) | `vendor/jizura/src/` と生成物 `vendor/jizura/dist/jizura.js` | MIT(全文は [`vendor/jizura/LICENSE`](vendor/jizura/LICENSE)。Copyright (c) 2026 hakoniwa) |

取り込んだコミット・取り込んだファイルの範囲・除外したものと理由・更新手順は
[`vendor/jizura/UPSTREAM.md`](vendor/jizura/UPSTREAM.md) にあります。JIZURA が
実行時に読む書体(Noto Sans JP ほか)は**同梱しておらず**、いずれも
SIL Open Font License 1.1 で配布されているものを Google Fonts から取得します。
