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

props の書き方・ビート同期の作法・運用ルールはリポジトリルートの
[`.agents/skills/karakuri-remotion/SKILL.md`](../.agents/skills/karakuri-remotion/SKILL.md) にまとめてあります。
`.claude/skills/karakuri-remotion` はそこへのシンボリックリンクです。

## フォント

歌詞・タイトルはレンダリングマシンにインストールされている CJK フォントを使います
(`src/fonts.ts` の `FONT_FAMILY`)。Web フォントは使いません。
日本語が豆腐になる場合は `fonts-noto-cjk` 等を入れてください。

## ライセンス

**Remotion 自体のライセンスに注意してください。**
Remotion は個人・非営利、および従業員 3 人以下の企業であれば無料で利用できますが、
それを超える規模の企業が利用する場合は会社ライセンス(Remotion Company License)の購入が必要です。
詳細は https://www.remotion.dev/license を参照してください。

アプリ側で Remotion 連携が**既定で OFF** になっているのはこのためです。設定画面で有効にする前に、
ライセンス条件を満たしていることを確かめてください。

このディレクトリ自身のコードは karakuri-media-studio プロジェクト内部での利用を想定しています。
