---
name: karakuri-remotion
description: karakuri-media-studio から焼く Remotion コンポジション(MusicVideo / FxOverlay / LyricMotion / Slate)の props を書くためのスキル。MV・歌詞アニメーション・文字PV(リリックモーション)・ビート同期・トランジション・文字演出レイヤーを JSON で組む必要があるときに使う。
---

# karakuri-remotion: MV / モーショングラフィックスの props を書く

karakuri-media-studio に同梱された `remotion/` ディレクトリが、この Studio の
**Remotion レンダリングバックエンド**。
あなたの仕事は `.mp4` を自分で焼くことではなく、**コンポジションに渡す props(JSON)を書くこと**。

作業フォルダはこのワークスペース(`workspace/`)で、リポジトリ本体は 1 つ上(`..`)。
以下の `remotion/…` はリポジトリ直下のものを指すので、ワークスペースからは
`../remotion/…` と読む。書いた props の JSON はこのワークスペースに置いてよい
(コミットされない)。

## 運用の原則

- **`FxOverlay` の演出はタイムラインに保存する**(`PUT /api/v1/timelines/{id}/fx`)。
  そこに入れておくと編集画面の FX トラックに帯として並び、人がプレビューを見ながら
  秒・位置を直したり要らないものを消したりできて、`POST /timelines/{id}/export` の
  `{"fx": true}` でそのまま焼ける。**ジョブに props を直接投げるのは、手元で 1 本だけ
  確かめたいときだけ**(詳しくは `.agents/skills/karakuri-studio/SKILL.md` §8)。
- **歌詞モーション(JIZURA)もタイムラインに保存する**(`PUT /api/v1/timelines/{id}/fx/lyric`)。
  同じ FX トラックに**全長 1 本の層**として乗り、編集画面で人がスタイル・雰囲気・種と
  **行ごとの指名**を直せて、`{"fx": true}` の書き出しでそのまま焼ける。`lyric` は
  `LyricMotion` の props から `fps` / `width` / `height` / `durationInSeconds` / `res` /
  `aspect` / `audio.src` を抜いたもの(画の大きさ・尺・BGM はタイムラインが持つ。
  `audio.beats` のような拍の情報だけは載せてよい)。**`LyricMotion` のジョブを直接
  投げるのは、素材の無い単体の文字PV を 1 本焼くときだけ**。
- **レンダリングは原則アプリ側の `POST /api/v1/jobs`(`mode: "remotion"`)経由で投入する。**
  出力が `outputs/` に入り、履歴・ライブラリ・素材登録・タイムラインの素材ビンに自動で乗るため。
  完了待ちは既存の `GET /api/v1/jobs/{id}` をポーリング。
- `remotion/` で直接 `npx remotion render` を叩くのは、**props の見た目を手元で
  確かめたいときだけ**。成果物をアプリの外に置いても納品フローに乗らない。
- 素材は **アプリの `/outputs` URL をそのまま `src` に書ける**。ダウンロードもコピーも不要。
  (`http://<studio>/outputs/xxxx/clip1.mp4` のような URL。静的配信は無認証)
- 表現は **props で書ける範囲でつくる**。コンポジション側(`../remotion/src/`)は
  読んで確かめる正本であって、ここから書き換えるものではない。props でどうしても
  書けない表現は、その旨を人に伝えて代案(別のイベント型・別の演出)で組む。

## コンポジション

| ID | 用途 |
|---|---|
| `MusicVideo` | 本命。カット割り + トランジション + 歌詞 + タイトル + BGM |
| `FxOverlay` | 出来上がった 1 本の mp4 の上に、イベントで文字演出・エフェクトを載せる |
| `LyricMotion` | 歌詞だけから**文字PV(リリックモーション)**を自動で組む。素材(映像・画像)は要らない |
| `Slate` | 疎通確認用。テキストを出すだけ |

props スキーマの正本は **`remotion/src/schema.ts`(zod)**。迷ったらこれを読む。
サンプルは `remotion/examples/music-video.json` / `remotion/examples/fx-overlay.json` /
`remotion/examples/lyric-motion.json` / `remotion/examples/slate.json`
(どれも外部素材ゼロで焼ける)。

**どれを使うか**: 撮った / 生成した素材を並べるなら `MusicVideo`、出来上がった 1 本の
上に演出を載せるなら `FxOverlay`、**素材が無く歌詞だけで 1 本にするなら
`LyricMotion`**(§`LyricMotion`)。

## 単位と座標系

- **時間はすべて秒**(フレームではない)。`0.52` のような小数で書く。
- 座標系は左上原点、`width` x `height` のピクセル空間。
- `fontSize` は **1080p 基準**のピクセル値。`height` が 1080 以外なら自動でスケールされるので、
  縦動画(1080x1920)でも 1080p のつもりで書いてよい。
- 尺は `durationInSeconds` を書かなければ `cuts` / `lyrics` / `title` の終端から自動で決まる。
  **BGM の長さには合わせてくれない**ので、曲の尻まで焼きたいなら `durationInSeconds` を明示する。

## カット割りの書き方

```jsonc
"cuts": [
  { "src": "http://…/outputs/ab12/clip1.mp4", "start": 0.0, "in": 0.5, "duration": 4.0,
    "fit": "cover" },
  { "src": "http://…/outputs/ab12/still.png", "start": 4.0, "duration": 2.0,
    "kenBurns": { "from": 1.0, "to": 1.15 },
    "transition": { "type": "crossfade", "duration": 0.4 } }
]
```

- `start` は**タイムライン上**の開始秒、`in` は**素材側**の頭出し秒。混同しない。
- `duration` は画面に出す長さ。素材の残り尺を超えると動画は最終フレームで止まる。
- トランジションは**入ってくる側のカット**に書く。前のカットは必要なぶんだけ自動延長されるので、
  `start` を重ねてオーバーラップさせる必要はない。**カットは隙間なく詰めるのが基本**
  (`cuts[i].start + cuts[i].duration == cuts[i+1].start`)。
- 静止画は必ず `kenBurns` を付ける。止め絵が 2 秒以上続くと死んで見える。
- `volume` の既定は 0(無音)。BGM を主役にするため。素材の音を混ぜたいときだけ上げる。

### トランジションの使い分け

| type | 使いどころ |
|---|---|
| `cut` | 既定。ビートに合わせて切るならこれが基本 |
| `crossfade` | 場面が地続きのとき。`duration` は 0.3〜0.6 |
| `fadeblack` / `fadewhite` | 章の切れ目。`duration` は 0.6〜1.2。切り替わりは中央 |
| `slide` / `wipe` | 勢いを出したいとき。`direction` は `left`(既定) / `right` / `up` / `down` |

多用すると散らかる。**基本 `cut`、サビ頭に `fadewhite`、間奏に `crossfade`** くらいの配分でよい。

## 秒の出どころ: `analysis.json`

**歌詞つきの MV で秒を決め打ちしない。** スタジオの音源解析ジョブ
(`mode: "audio_analysis"`、karakuri-studio SKILL §8)が出す
`/outputs/{job_id}/analysis.json` をそのまま props に写す。

| analysis.json | 写す先 |
|---|---|
| `lines[].start` / `end` | `MusicVideo.lyrics[].start` / `end`、`FxOverlay` の `lyric` の `start` / `end` |
| `lines[].text` | 同 `text` |
| `lines[].chars`(`{c,s,e}`) | `FxOverlay` の `lyric.chars`(`{c,s}` だけ使う。`style: "karaoke"` のとき) |
| `beats.times` | `MusicVideo.beats`、`FxOverlay` の `beatMarker` の `start` と `beat`(= 拍の間隔) |
| `beats.bpm` | `beatMarker.beat` を `60 / bpm` で出す |
| `onsets[].t` | 決めの演出(`card` / `imageSlam` / `glitchCut`)の `start` |
| `silence[]` | 間奏・無音の扱い(そこに文字を置かない / `beatMarker` で間を持たせる) |

- **アラインの秒より実測 onset を優先する。** アラインの語頭は実際の発音より
  100〜250ms 遅れることがある(BAN!BAN!BAN! の実測)。歌詞テロップはアライン秒、
  叩き込む演出は onset 秒、と使い分ける。
- `lines[].aligned_text` が付いている行は、置換(`{"BAN!": "バン"}`)を当てて
  アラインした行。`chars` はその読みなので、**`text` をそのまま出すなら
  `style: "line"`**(カラオケの文字送りは字数が合わない)。

## ビート同期の作法

**ビート同期は Remotion 側ではなくあなたがやる。** `beats` を貰ったら、
`cuts[].start` をビート時刻に**スナップ**して書くのが仕事。

1. 曲のビート時刻(秒)の配列を得る(解析結果を貰う / BPM から生成する)。
2. カットの切り替えたい時刻を、**最も近いビート時刻に丸めて** `start` に入れる。
   ずれは ±30ms 以内に収める。
3. `duration` は「次のカットの `start` − 自分の `start`」で埋める(隙間を作らない)。
4. 尺の長いカットは 2 拍・4 拍・8 拍の倍数に乗せる。半端な拍数はハネて見える。

```jsonc
// BPM 120 → 1 拍 0.5 秒。4 拍(2 秒)ごとに切る例
"beats": [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0],
"cuts": [
  { "src": "…", "start": 0.0, "duration": 2.0 },
  { "src": "…", "start": 2.0, "duration": 2.0 }
]
```

`beats` を props に渡す意味があるのは **`beatPulse: true` を使うときだけ**。
これはビート直後に画面全体を軽く拡大する味付け(`beatPulseScale`、既定 1.02)。
**1.05 を超えると酔うので上げない。** 落ち着いた映像では `beatPulse` は off でよい。

## 歌詞・テロップ

```jsonc
"lyrics": [
  { "text": "歌詞の一行", "start": 1.0, "end": 3.5, "style": "karaoke",
    "position": "bottom", "fontSize": 64 }
]
```

- `style`
  - `karaoke`: 表示区間の進行に合わせて左から色が送られる。`color`(未歌唱) → `highlightColor`(歌唱済み)。
    **`start` / `end` はその行を歌っている区間そのものに合わせる。** ずれると色送りが目立って破綻する。
  - `fade`: フェードイン/アウトだけ。訳詞・字幕・クレジット向け。
  - `pop`: 下から跳ね上がる。掛け声・強調向け。
- 行は**重ねてよい**(2 行同時表示など)。`position` を変えれば衝突しない。
- 1 行は 20 文字程度まで。長いと自動で折り返して 2 行になり、下端に寄って読みにくい。
  長い歌詞は行を分けて `start` / `end` をずらす。
- `outlineColor` の既定は半透明の黒。**明るい映像の上に白文字を置くときは必ず縁取りを残す**(空文字にしない)。

## タイトル

```jsonc
"title": { "text": "曲名", "artist": "アーティスト名", "showUntil": 3.0, "position": "bottomLeft" }
```

冒頭から `showUntil` 秒まで表示し、直前 0.6 秒でフェードアウトする。イントロの尺に合わせる。

## BGM

```jsonc
"audio": { "src": "http://…/outputs/ab12/bgm.mp3", "volume": 1.0, "startFrom": 0, "fadeOut": 1.5 }
```

映像の尺で切れるので、**尻切れを避けたいなら `fadeOut` を 1〜2 秒入れる**。
`startFrom` は音源側の頭出し秒(イントロを飛ばすとき)。

## 素材のない状態で組み立てる

素材がまだ生成できていない段階でも、疑似ソースで構成だけ確認できる。

- `"src": "color:#223344"` — 単色
- `"src": "gradient:135:#223344,#5566aa"` — グラデーション(角度は省略可)

`remotion/examples/music-video.json` はこれだけで組んであるので、雛形として複製して使う。

## `FxOverlay`: 演出レイヤーを載せる

### 使ってよいとき

**MV・モーショングラフィックスを求められたとき、または明示的に指示されたときだけ。**
ドラマ制作の通常フロー(脚本 → Take → タイムライン → ffmpeg 書き出し)では**使わない**。
「テロップを入れて」程度ならアプリ内蔵の ffmpeg エクスポートで足りる。呼ぶのは
「MV にして」「エフェクトを盛って」「決め台詞を叩き込んで」と言われたときだけ。

### 何をするコンポジションか

`MusicVideo` が「カットを並べて 1 本にする」のに対し、`FxOverlay` は
**もう出来ている 1 本(`base`)の上に演出を足す**。ふつうはタイムラインの書き出し mp4 を
`base.src` に渡し、元音源を `audio.src` に渡す。

**制作の本筋では `base` / `audio` / `fps` / `width` / `height` / `durationInSeconds` を
自分で書かない**: `events` と `theme` / `seed` / `ambient` を
`PUT /api/v1/timelines/{id}/fx` に入れておけば、`{"fx": true}` の書き出しが
「焼いた mp4 を下地に・A1 の音を乗せて・タイムラインの規格で」レンダリングする。
下の JSON は**手元で 1 本だけ確かめるとき**の形。

```jsonc
{
  "fps": 24, "width": 1280, "height": 720,
  "base":  { "src": "http://…/outputs/<export>/video.mp4", "muted": true },
  "audio": { "src": "http://…/outputs/<audio>/audio.wav" },
  "theme": { "palette": ["#dc1428", "#f5f5f5", "#08080a"] },
  "seed": 1,
  "events": [ { "t": 43.9, "type": "card", "text": "BAN", "frames": 5 } ]
}
```

- `events[]` は `type` で形が変わる。共通は `t`(開始秒・必須)と `until` / `duration`(省略可)と `seed`。
- 型は 15 種: `card` / `invertShake` / `imageSlam` / `terminalText` / `screen` / `glitchCut` /
  `collapse` / `crtOff` / `sprite` / `stickerStack` / `credits` / `lyric` / `endCard` /
  `beatMarker` / `shape`。**各型のフィールドと既定値は `remotion/src/schema.ts` を読む**
  (ここには写さない。増減する)。
- `sprite` / `imageSlam` / `stickerStack` の `src` に渡す**透過 PNG は、スタジオの
  透過キー API で作る**(`POST /api/v1/library/{id}/key`、または生成画像から直接抜く
  `POST /api/v1/library/key-from-job`)。文字だけの素材は `POST /api/v1/images/text`
  でフォントから組める。作り方と抜き方の選び方は
  `.agents/skills/karakuri-studio/SKILL.md` §10。自分で PNG を探しに行かない。
- **雷・ハート・集中線・吹き出しのような単純な記号は `shape` で描く。**
  画像生成 → 透過キーに回すのは、キャラ・小物・ロゴ文字だけ。
- 色は `theme.palette` の役割名(`accent` / `fg` / `bg`)か番号、または CSS の色。
- 位置と大きさは**画面比(0..1)**、`fontSize` は 1080p 基準。縦動画でも書き方は変わらない。
- 尺は `durationInSeconds` を書かなければ `base` の尺と `events` の終端の大きいほう。

### 重なり(`z`)

イベントの重なりは**型ごとの既定層**で決まる(下から `invertShake`/`collapse` → `glitchCut` →
`beatMarker` → `sprite`/`stickerStack`/`shape` → `imageSlam` →
`lyric`/`terminalText`/`credits` → `screen` → `card` → `endCard` → `crtOff`。
同じ層なら `events` に書いた順)。ふつうはこれで足りるので **`z` は書かない**。

**`z` を書くのは「`screen`(全画面の板)の上に何か出したいとき」だけ。**
`screen` は既定で歌詞・記号より上なので、黒画面の上に歌詞を残す・ランプを点滅させる、は
`z` 無しでは書けない。そのときだけ、上に出したいイベントに `screen` より大きい `z` を書く。

```jsonc
{ "t": 60.0, "until": 64.0, "type": "screen", "bg": "bg" },
{ "t": 60.2, "until": 64.0, "type": "lyric",  "text": "…", "z": 6.5 },
{ "t": 60.2, "until": 64.0, "type": "shape", "shape": "circle", "cx": 0.9, "cy": 0.12,
  "size": 0.05, "fill": "accent", "z": 6.5 }
```

`z` は小数で書ける(`screen`=6 と `card`=7 のあいだなら 6.5)。既定層の番号は
`remotion/src/FxOverlay.tsx` の `EVENT_LAYER`、または `schema.ts` の `z` のコメントにある。

### 決めを強くするオプション(どれも既定は無効)

BAN!BAN!BAN!(過去に作った MV)で確立したもの。**書かなければ従来どおりの絵**なので、
「もう一段強くしたい」ところにだけ足す。

| 型 | フィールド | 何が起きるか |
|---|---|---|
| `card` | `wipe: {angle, frames, color}` | 斜めのカラーワイプが渡り、通り過ぎたところだけ文字と斜線が `color` になる |
| `card` | `chroma: 0..1` | 画面の端(外周)だけ RGB 分離。1 が 1080p の 14px 相当。0.6 前後が目安 |
| `invertShake` | `hitStop: {scale, chroma, frames}` | 反転が明けた最初の 1f だけ base を拡大 + クロマ収差(決めの「止め」) |
| `card` | `halftone: {alpha, dot}` | 背景の点の濃さと細かさ。`dot` は点の間隔(1080p 基準 px)。BAN は `{alpha: 0.18, dot: 21}`。`true` のままなら従来どおり薄く粗い |
| `sprite` / `stickerStack` | `border: {color, width, inset}` | 画像の輪郭(アルファ)に沿った枠。`width` は 1080p 基準 px。`inset: true` で輪郭の**内側**に引く(`color:` の色面と同じ見た目) |
| `sprite` / `stickerStack` | `halftone: 0..1` または `{alpha, dot}` | 不透明部分にハーフトーンの点。点の色は `border.color`(無ければ `fg`)。数値は従来どおり薄め、`{alpha, dot}` は書いたとおりの濃さ・細かさ |
| `sprite` / `stickerStack` | `jitter: {px, hz, rotDeg}` | 貼ったあとの微振動。積んだカードが小刻みに揺れる |
| `sprite` / `imageSlam` / `endCard.logo` | `tint: "#ffffff"` | 不透明部分を 1 色で塗る(白抜きロゴを配色に合わせる) |
| `lyric` / `sprite` / `imageSlam` / `terminalText` | `outGlitch: {frames, displace, chroma}` | 出際の `frames` フレームを走査線ずれ + RGB 分離で飛ばして消す。フェードではなく「電波が切れる」消え方。既定は `{frames: 2, displace: 39, chroma: 0.85}`(BAN の消し方) |
| `stickerStack` | キーフレームの `pop: false` | そのキーフレームで貼り直すときに大きく出さない(等倍のまま)。同じ絵を消して再登場させるときに、毎回跳ねさせたくないところへ書く |

### 位置まわりの追加

- `terminalText` / `credits` に `cx` / `cy`(画面比)。**書くと `corner` より優先**され、そこが中心になる
  (片方だけ書いたら、もう片方は 0.5 = 画面中央)。隅ではなく画面の任意の場所に置きたいときだけ使う。
- `terminalText` の `margin`(画面幅に対する比、既定 0.045)で `corner` に寄せるときの余白を変えられる。
- `credits` の `lines` は文字列のほかに `{"text": "…", "fontSize": 40, "color": "accent"}` で書ける
  (`fontSize` は 1080p 基準)。文字列だけのときは従来どおり 1 行目が大きく、以降 0.82 倍。
- `stickerStack` の キーフレームの `visible: false` で、その秒から消える
  (次に `visible: true` のキーフレームが来たらそこでまた貼り直す)。「途中で消して再登場」用。
  再登場で跳ねさせたくないときは、そのキーフレームに `pop: false` を書く。

### 単位・座標・書き分けの落とし穴

数字の意味を取り違えると、絵は出るのに「なんとなく違う」ものになる。焼く前にここを確認する。

| 書くもの | 単位 | 720p の値からの直し方 |
|---|---|---|
| `chroma`(`card` / `invertShake.hitStop` / `outGlitch`) | 0..1。**1 = 1080p の 14px** | 720p の px ÷ 14 × 1.5 |
| `outGlitch.displace` | 1080p 基準 px | 720p の px × 1.5(BAN の 26px → 39) |
| `jitter.px` / `border.width` / `fontSize` / `halftone.dot` / `beatMarker.size` | 1080p 基準 px | 720p の px × 1.5(BAN の dot 14px → 21) |
| `cx` / `cy` / `w` / `maxH` / `size` / `margin` | 画面比(0..1) | 解像度に依らない。そのまま |

- **`terminalText` / `credits` の `cx` / `cy` は「文字ブロックの中心」**(隅寄せの `corner` とは
  基準が違う)。左上を (x, y) に置きたいなら `cx = x + ブロック幅/2`、`cy = y + ブロック高/2`。
  ブロック高はおおよそ `行数 × fontSize × 1.45 / 高さ`(`lineHeight` は 1.45)。
  合っているかはコンタクトシートで確かめる。数字だけで詰めない。
- **`stickerStack` は「同じ絵は 1 イベントにまとめる」。** 消える・再登場するところは
  `visible: false` / `true` のキーフレームで書く。イベントを分けて書くと、
  分けたほうの尺が終わった時点で絵が消える(積んだものが途中で欠ける)。
- **外部の実装や台本から秒を移すときは、`round(t * fps)` でフレームに直してから秒に戻す。**
  `FxOverlay` は秒をフレームに丸めてから並べるので、元が別 fps の秒だと 1 フレームずれる。
  決めの 1 フレームずれは、見れば分かるくらい効く。
- **`flatten`(スタジオの透過キー)と `sprite.tint` は「輝度を捨てるベタ塗り」**で、乗算ではない。
  塗ったあとに元の陰影は残らない。陰影を残したい絵に掛けない。
- **透過 PNG の源は「抜く前」の画像を渡す。** 抜いた RGBA をもう一度 `key` に通すと、
  白背景に合成されてから抜かれるので、`flatten` が白い四角になる。詳しくはスタジオ SKILL §10。

### 配置ルール(BAN!BAN!BAN! で確立したもの)

守らないと「盛った」ではなく「散らかった」になる。

- **文字は短く、出る時間も短く。** 決めのカード(`card`)は 5 フレーム前後。単語 1 つ。
- **フェード禁止。** 出現は 2〜3 フレームでスナップさせる(`imageSlam` の `snap` / `spring`)。
  じわっと出るのは弱さにしか見えない。
- **決め台詞の画像は顔(特に主役)と楽器の手元を避け、画面の下 1/3 に、できるだけ大きく。**
  目安は `w` >= 0.45、`cx` は 0.28〜0.72、`cy` は 0.7 前後。小さく置くと何も言っていないのと同じ。
  はみ出すと `imageSlam` がレンダリング時にコンソールへ警告を出す。
- **配置(`cx` / `cy` / `w`)を触ったら必ずコンタクトシートで確認する。**
  `POST /api/v1/videos/contact-sheet` に焼いたジョブと該当の秒を渡し、返ってきた
  画像を自分の目で見る(スタジオ SKILL §10)。数字だけで詰めない。
- **無音区間には何も乗せない。** 音が止まっているところに絵の情報を足すと嘘になる。
  `events` をそこに書かなければよい(`ambient` も切る)。
- **補助効果(`invertShake` / `glitchCut` / クロマ収差)は「決め」だけに使う。**
  常時掛けると効果ではなく画質の劣化になる。`ambient` の走査線・ビネットも既定 OFF のままが基本。
- `invertShake` の起点は**カードが明けたところ**。`card` の `t` + カードの尺に置く。
- 秒は決め打ちせず、**音源解析の結果(歌詞アライン・onset・ビート)から算出する**。

### `lyric`: 歌詞モーションの層

`FxOverlay` の props に `lyric` を書くと、`base` の上・`events` の下に **JIZURA の
文字PV が透過で 1 枚**重なる(`LyricMotion` と同じエンジン)。中身は `LyricMotion` の
props から `fps` / `width` / `height` / `durationInSeconds` / `res` / `aspect` と
`audio` の再生まわりを抜いたもの——画の大きさ・尺・BGM は `FxOverlay` 側が持つ
(`audio.beats` のような拍の情報だけは `lyric` に置いてよい)。画面比は
`width` / `height` にいちばん近いものが自動で選ばれ、cover で収まる。

**タイムラインに保存するのが原則**(`PUT /api/v1/timelines/{id}/fx/lyric`)。ここに props
として直接書くのは、手元で 1 本焼いて見た目を確かめるときだけ。

### 手元で確認する

```bash
cd ../remotion
npx remotion render src/index.ts FxOverlay ../workspace/tmp/fx.mp4 --props=examples/fx-overlay.json
```

```bash
# 歌詞モーション(JIZURA)の層を重ねた版
npx remotion render src/index.ts FxOverlay ../workspace/tmp/fx-lyric.mp4 \
  --props=examples/fx-overlay-lyric.json
```

`examples/fx-overlay.json` は全イベント型を 1 回ずつ含む 14 秒のサンプル(外部素材ゼロ)。
上の `z` と新オプション(`outGlitch` / `halftone: {alpha, dot}` / `border.inset` /
キーフレームの `pop: false`)も一通り入っているので、見た目を確かめるならここを引くのが速い。
効果の見た目を確かめたいときは、これを複製して該当イベントだけ残すのが速い。

## `LyricMotion`: 歌詞だけで文字PV を組む

素材(映像・画像)が 1 枚も無くても、**歌詞テキストだけ**で 1 本焼けるコンポジション。
中身は OSS の自動構成エンジン **JIZURA 字面**(MIT)で、行を切って・レイアウトを選んで・
入り / 保持 / 抜けのアニメーションを割り当てるところまで自動でやる。あなたの仕事は
**歌詞と雰囲気と種を渡し、気に入らない行だけ指名し直すこと**。

タイムラインの上に文字PV を重ねたいときは、このコンポジションを単体で焼くのではなく
**`FxOverlay` の `lyric` に入れてタイムラインへ保存する**(`PUT /api/v1/timelines/{id}/fx/lyric`。
props はこの節のものから `fps` / `res` / `aspect` / `durationInSeconds` / `audio.src` を
抜いた形)。ジョブへ直接投げるのは**素材の無い単体 PV を 1 本焼くとき**だけ。

### いちばん短い形

```jsonc
{
  "mode": "remotion",
  "remotion_composition": "LyricMotion",
  "remotion_props": {
    "lyrics": "[00:00.30]夜明けの色を/覚えてる\n[00:01.40]ほどけた声が遠くで鳴った",
    "mood": "emotional",
    "seed": 20260922
  }
}
```

### 歌詞の記法(1 行 1 フレーズ)

| 書き方 | 意味 |
|---|---|
| `[mm:ss.xx]歌詞` | その行が出る時刻(LRC)。**全行に付ければ**そのまま時刻になる |
| `前半/後半` | その行を 2 カットに割る |
| `*強調*` | その語を強く出す |
| `歌詞!` | 決めのカットにする(行末の `!`) |
| `歌詞\|注釈` | 小さな注釈を添える(英訳・読みなど) |
| 空行 | 間(前の行との間を空ける) |
| `#` 始まり | コメント |

### 段取り

1. **秒を作る。** 音源解析ジョブ(`mode: "audio_analysis"`)の
   `/outputs/{job_id}/analysis.json` から、`lines[].start` を LRC のタイムスタンプに、
   `beats.times` を `audio.beats` に写す。ここが他のコンポジションと同じ流儀。

   ```python
   # analysis.json → LyricMotion の props(抜粋)
   def lrc(t):  # 12.34 -> "[00:12.34]"
       return f"[{int(t // 60):02d}:{t % 60:05.2f}]"

   props = {
       "lyrics": "\n".join(lrc(l["start"]) + l["text"] for l in a["lines"]),
       "audio": {
           "src": f"/outputs/{job_id}/vocal.wav",   # BGM を mp4 に載せる
           "beats": a["beats"]["times"],            # カットの境目を拍に吸わせる
           "duration": a["duration"],
       },
       "timing": {"bpm": a["beats"]["bpm"], "snap": True},
   }
   ```

   解析が無いときは LRC を書かず `timing.lineTimes`(`{"0": 0.4, "1": 2.1}`)で
   秒を入れてもよい。どちらも無ければ字数から自動で決まる(尺合わせは
   `timing.lineScale`)。

2. **まず `mood` と `seed` でおまかせ。** `glitch` / `calm` / `pop` / `graphic` /
   `editorial` / `emotional` / `chaos` のどれかを書けば、それに合うスタイル・部品・
   スライダーがまとめて決まる。`seed` を変えると別の案が出る(同じ種なら毎回同じ絵)。
   **最初から `overrides` を書き並べない。** 種を 3〜4 回振って、いちばん近いものを選ぶ。

3. **気になる行だけ `overrides`。** キーは**行番号(0 始まりの文字列)**。

   ```jsonc
   "overrides": {
     "0": { "layout": "center", "enter": "blur", "exit": "drift" },
     "3": { "layout": "huge", "enter": "slice", "exit": "explode", "decor": ["sparks"] }
   }
   ```

   指名できるのは `layout` / `enter` / `hold` / `exit` / `decor`(配列) / `treat` /
   `bg` / `cam` / `trans` と、`single`(行を割らず 1 カットに収める) / `seed`(この行だけ
   振り直す) / `lock`。

4. **全体の当たりを調整する。** `fx` は**書いたところだけ**効く部分指定。
   `{"fx": {"glitch": 0.2, "texture": 0.8}}` のように 1〜2 個だけ触る。
   `koma` は 1 秒あたりの作画枚数(12 = 2 コマ打ち、0 = 毎フレーム)。

5. **出したくない部品を外す。** `enabled` も部分指定で、
   `{"enabled": {"layout": {"tile": false, "marquee": false}}}` のように
   **落としたいものだけ** `false` を書く。

### 部品キーの調べ方

**キーは推測しない。** 全部このファイルに並んでいる:

```
.agents/skills/karakuri-remotion/jizura-catalog.json
```

構造は `{groups: {layout: {center: {name, tags, pack, extra?, wa?}, …}, enter: …},
styles, moods, fonts, aspects, sampleLyrics}`。`tags` は雰囲気(mood)で、
`mood` を書いたときに優先して選ばれる目印。`extra: true` は初版より後に足された部品で、
**ランダムには `"extra": true` を書かないと選ばれない**(手で `overrides` に指名するのは
いつでもできる)。`wa: true` は和風モチーフで、`"wa": false` でランダムから外れる。

```bash
# 例: 「縦書き」系のレイアウトを探す
jq -r '.groups.layout | to_entries[] | select(.value.name | test("縦")) | "\(.key)\t\(.value.name)"' \
  .agents/skills/karakuri-remotion/jizura-catalog.json

# 例: emotional に向くという印の付いた入り
jq -r '.groups.enter | to_entries[] | select(.value.tags // [] | index("emotional")) | .key' \
  .agents/skills/karakuri-remotion/jizura-catalog.json
```

カタログが無い / 古いときは `node remotion/scripts/export-jizura-catalog.mjs` で出し直す。

### 画面の大きさ・透過

- `aspect`(`16:9` `9:16` `4:3` `3:4` `1:1` `4:5` `21:9`)と `res`(短辺のピクセル数)で
  実寸が決まる。`width` / `height` は書かない。
- 合成用に抜きたいときは `keyBg`:
  `"green"` / `"black"` はそのまま mp4 で焼ける。`"transparent"` は**アルファを持てる
  コンテナが要る**ので、ジョブ側で `remotion_render_options` を添える。

  ```jsonc
  {
    "mode": "remotion",
    "remotion_composition": "LyricMotion",
    "remotion_props": { "keyBg": "transparent", "lyrics": "…" },
    "remotion_render_options": {
      "codec": "prores", "prores_profile": "4444",
      "image_format": "png", "pixel_format": "yuva444p10le"
    }
  }
  ```

  このとき成果物は `video.mp4` ではなく **`video.mov`**(vp8 なら `video.webm`)。

### やりがちな失敗(LyricMotion)

- **`style` と `mood` を両方書いて悩む。** `mood` に任せるなら `style` は書かない
  (空文字が「任せる」)。逆に配色を決め打ちしたいなら `style` を書く。
- **`overrides` のキーを 1 始まりにする。** 0 始まり。
- **`fx` や `enabled` を全部書く。** どちらも部分指定。書いたところだけ効く。
- **知らない部品キーを書く。** そのキーは無視されるだけで警告も出ない。
  必ず `jizura-catalog.json` から取る。
- **`res` を上げすぎる。** 1080 で 1920x1080。2160 は 4 倍重い。確認は 540 で十分。

## 手元で尺と見た目を確かめる(任意)

以下は**リポジトリの `remotion/` ディレクトリで**実行する(ワークスペースからは
`cd ../remotion`。依存は `run.sh` が入れているので `npm install` は不要)。

```bash
npx remotion compositions src/index.ts          # 一覧と尺の確認
npx remotion render src/index.ts MusicVideo ../workspace/tmp/mv.mp4 --props=examples/music-video.json
npx remotion studio                             # ブラウザでプレビュー
```

焼いた mp4 はワークスペースの `tmp/` に出す(リポジトリ側に作業ファイルを残さない)。

- **props を書いたら、まず `npx remotion compositions` で尺が意図どおりか確認する。**
  `durationInSeconds` の書き忘れ・`start` の桁違いはここで出る。
- 焼いたものは `ffprobe` で尺と解像度を、`ffmpeg` でフレームを抜いて目視で確認する。
- 1080p は 1 秒あたり数秒かかる。確認は 5〜10 秒の抜粋で済ませ、フル尺はアプリ側のジョブに投げる。

## やりがちな失敗

- `start` に**フレーム番号**を書く → 単位は秒。
- `in` と `start` の取り違え → `in` は素材側、`start` はタイムライン上。
- `cuts` に隙間を作る → `backgroundColor` が出る。隙間なく詰める。
- BGM より映像が短い → `durationInSeconds` を明示する。
- `karaoke` の `start` / `end` を「表示したい時間」で書く → 「歌っている区間」で書く。
- トランジションを全カットに付ける → 散らかる。基本は `cut`。
