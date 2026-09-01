---
name: karakuri-remotion
description: karakuri-media-studio から焼く Remotion コンポジション(MusicVideo / Slate)の props を書くためのスキル。MV・歌詞アニメーション・ビート同期・トランジションを JSON で組む必要があるときに使う。
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
| `Slate` | 疎通確認用。テキストを出すだけ |

props スキーマの正本は **`remotion/src/schema.ts`(zod)**。迷ったらこれを読む。
サンプルは `remotion/examples/music-video.json` / `remotion/examples/slate.json`
(どちらも外部素材ゼロで焼ける)。

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
