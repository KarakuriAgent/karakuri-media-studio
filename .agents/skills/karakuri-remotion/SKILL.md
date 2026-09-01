---
name: karakuri-remotion
description: karakuri-media-studio から焼く Remotion コンポジション(MusicVideo / FxOverlay / Slate)の props を書くためのスキル。MV・歌詞アニメーション・ビート同期・トランジション・文字演出レイヤーを JSON で組む必要があるときに使う。
---

# karakuri-remotion: MV / モーショングラフィックスの props を書く

karakuri-media-studio に同梱された `remotion/` ディレクトリが、この Studio の
**Remotion レンダリングバックエンド**。
あなたの仕事は `.mp4` を自分で焼くことではなく、**コンポジションに渡す props(JSON)を書くこと**。

## 運用の原則

- **レンダリングは原則アプリ側の `POST /api/v1/jobs`(`mode: "remotion"`)経由で投入する。**
  出力が `outputs/` に入り、履歴・ライブラリ・素材登録・タイムラインの素材ビンに自動で乗るため。
  完了待ちは既存の `GET /api/v1/jobs/{id}` をポーリング。
- `remotion/` で直接 `npx remotion render` を叩くのは、**コンポジションを開発・改修しているときと、
  props の見た目を手元で確かめたいときだけ**。成果物をアプリの外に置いても納品フローに乗らない。
- 素材は **アプリの `/outputs` URL をそのまま `src` に書ける**。ダウンロードもコピーも不要。
  (`http://<studio>/outputs/xxxx/clip1.mp4` のような URL。静的配信は無認証)
- 新しい表現がどうしても props で書けないときは、コンポジション側(`src/`)を直す。
  その場合は既存 props の後方互換を壊さないこと(フィールドは追加のみ、既定値つきで)。

## コンポジション

| ID | 用途 |
|---|---|
| `MusicVideo` | 本命。カット割り + トランジション + 歌詞 + タイトル + BGM |
| `FxOverlay` | 出来上がった 1 本の mp4 の上に、イベントで文字演出・エフェクトを載せる |
| `Slate` | 疎通確認用。テキストを出すだけ |

props スキーマの正本は **`remotion/src/schema.ts`(zod)**。迷ったらこれを読む。
サンプルは `remotion/examples/music-video.json` / `remotion/examples/fx-overlay.json` /
`remotion/examples/slate.json`(どれも外部素材ゼロで焼ける)。

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

### 手元で確認する

```bash
cd remotion
npx remotion render src/index.ts FxOverlay out/fx.mp4 --props=examples/fx-overlay.json
```

`examples/fx-overlay.json` は全イベント型を 1 回ずつ含む 14 秒のサンプル(外部素材ゼロ)。
効果の見た目を確かめたいときは、これを複製して該当イベントだけ残すのが速い。

## 手元で確認する(開発時のみ)

以下は**リポジトリの `remotion/` ディレクトリで**実行する(`cd remotion`)。

```bash
npm install                                     # 通常は run.sh が入れる(手動起動時のみ)
npx remotion compositions src/index.ts          # 一覧と尺の確認
npx remotion render src/index.ts MusicVideo out/mv.mp4 --props=examples/music-video.json
npx remotion studio                             # ブラウザでプレビュー
```

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
