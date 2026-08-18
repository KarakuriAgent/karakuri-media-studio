# MiniMax H3（Hailuo 3）プロンプトガイド 補強ドラフト

作成日: 2026-08-18 ｜ 状態: **取り込み済み（下の「取り込み状況」を参照）**

このドキュメントは `backend/app/prompts.py` の H3 ガイド群を補強するための素材整理である。
取り込み判断のための提案までで、`prompts.py` への差分はまだ書いていない。

対象の既存定数:

| 定数 | 位置 | 役割 |
|---|---|---|
| `MINIMAX_H3_GUIDE_BODY` | prompts.py 859–904 | t2v / i2v / r2v 共通のショット・カメラ・台詞・音・禁止事項 |
| `MINIMAX_H3_VIDEO_GUIDE` | prompts.py 906–953 | base モード（t2v / i2v / FL2VA / L2VA）の 3 フィールド契約 |
| `MINIMAX_H3_REFERENCE_VIDEO_GUIDE` | prompts.py 955–1038 | Ref2VA の 6 セクション契約 |
| `H3_QUALITY_BAR` | prompts.py 1382–1410 | 品質バー |
| `FEW_SHOT_H3` | prompts.py 1412–1466 | 実例（H3-E1 / E2 / E3 の 3 件のみ） |

---

## 0. 取り込み状況（2026-08-18）

このドラフトの提案は、以下のとおりコードへ反映済み。**ここに書いていない提案は
未着手**（主に 5.3 の QUALITY BAR 追記と、5.5 の実装確認事項）。

| ドラフトの項目 | 反映先 |
|---|---|
| 2.1-1 カット動詞5種 / dissolve・fade・wipe は明示要求時のみ（M5） | `MINIMAX_H3_GUIDE_BODY` |
| 2.1-2 カットしない条件（距離・角度の微変化はカメラモーション） | `MINIMAX_H3_GUIDE_BODY` |
| 2.1-3 amplitude / speed は意味があるときだけ | `MINIMAX_H3_GUIDE_BODY` |
| 2.1-4 スタイル語彙リストと導出規則（+ 2.1-20 スタイルは1つ） | `MINIMAX_H3_GUIDE_BODY` |
| 2.1-5 話者初出時に確立する属性 | `MINIMAX_H3_GUIDE_BODY` |
| 2.1-6 台詞継続表現の定型4種 | `MINIMAX_H3_GUIDE_BODY` |
| 2.1-11 参照音声の書き起こし規約（verbatim / `[unclear]` / 句読点） | `MINIMAX_H3_GUIDE_BODY` |
| M2 768px はローカル運用値でモデル上限ではない | `MINIMAX_H3_GUIDE_BODY` |
| 5.1-13 タイポグラフィ / UI が主題なら末尾の除外文を付けない | `MINIMAX_H3_GUIDE_BODY` |
| 2.1-7 `retention_analysis` の Picture / Video 行書式 | `MINIMAX_H3_REFERENCE_VIDEO_GUIDE` |
| 2.1-8 video editing の summary 定型 | 同上 |
| 2.1-9 task type 選択の判断規則 | 同上 |
| 2.1-10 `(Sx)` の割り当てと `retention_analysis` での禁止 | 同上 |
| 2.1-12 語数の但し書き3項目 | 同上 |
| 2.1-13 copy / reference 関係を書く場所 | 同上 |
| M1 アプリ独自のタグ番号規則の理由 | 同上 |
| 5.4-28〜31 実例の構造化と選択 | 新規 `backend/app/h3_examples.py` |

実例（第4章）の扱い:

- **canonical**（公式 rewrite 形式の完成例）: 既存の H3-E1〜E3 に加え、
  H3-E4（FL2VA / C12）・H3-E5（L2VA / 公式 base Case 4）・H3-E6（生成編集 / C31）・
  H3-E7（マルチ参照 / C17）・H3-E8（多言語2話者 / C03）・H3-E9（クレイアニメ / C11）・
  H3-E10（画面内UI文字 / C14）を**新規に書き起こして**追加した。
- **inspiration**（rewrite 前の生入力）: C01 / C02 / C03 / C09 / C10 / C11 / C12 /
  C14 / C15 / C17 / C19 / C20 / C24 / C30 / C33 / C35 を原文のまま収録し、
  「形は真似しない」と明記した。

配り方:

- 内蔵エージェント / チャット: ワークフローに応じて 1〜2 本だけ埋め込み
  （`app.h3_examples.default_examples_for_workflow`）、足りなければ
  `get_prompt_examples` アクションで取りに行く。
- 外部 API: `GET /api/v1/prompt-examples`（索引 / `id` / `mode`+`category`）と、
  `GET /api/v1/prompt-guide` の「4.3 実例の追加取得」節（実例データから動的生成）。

---

## 1. 調査サマリ

### 1.1 ソース一覧

| # | ソース | 種別 | 件数 | 保管ファイル |
|---|---|---|---:|---|
| 1 | MiniMax 公式 `h3-prompt-writing` スキル（SKILL.md） | 一次 | — | `official-SKILL.md`（35行） |
| 2 | 公式 `VIDEO_PROMPT_WRITING_GUIDE_base_en`（T2VA / I2VA / FL2VA / L2VA） | 一次 | 4ケース | `official-base-en.txt`（222行） |
| 3 | 公式 `VIDEO_PROMPT_WRITING_GUIDE_ref_en`（Ref2VA） | 一次 | 1完全例 | `official-ref-en.txt`（341行） |
| 4 | GitHub 5リポジトリ横断プロンプト集 | 二次（公式例39含む） | 456件 / 16カテゴリ | `github-prompts.md`（42,157行）＋ `all_prompts.json` |
| 5 | apimodels.app プロンプトライブラリ | 二次 | 226件 / 11カテゴリ | `apimodels-prompts.md`（7,687行）＋ `cards.json` |
| 6 | 解説記事8本（imagine.art / krea.ai / inreels.ai / mixio.studio / hailuo3.me / jxp.com / minimax3.com / 公式） | 二次 | 実例約90本 | `articles-guide.md`（1,187行） |

保管場所（一時ディレクトリ・セッション限り）:

```
/mnt/0AB07FFEB07FEF17/tmp/claude-1000/-mnt-0AB07FFEB07FEF17-workspace-video-studio/\
bcde5f4d-517a-4ff7-b26a-bc5dd2e42ef4/scratchpad/h3-research/
```

恒久保存が必要なら `docs/` 配下か別リポジトリへ退避すること。

### 1.2 素材のモード内訳（GitHub 集約 456件）

| モード | 件数 |
|---|---:|
| t2v | 357 |
| r2v / omni-reference | 79 |
| first-last frame（FL2VA） | 10 |
| i2v | 6 |
| multimodal（r2v + 音声リファレンス） | 3 |
| video regeneration（768P→2K） | 1 |

**重要な偏り**: コミュニティ実例のほぼ全ては「公式 rewrite 形式（`integrated_multimodal_description:` /
6セクション）ではない、素の自然言語ブリーフ」である。公式 rewrite 形式の実例は公式ガイドの
5ケース（base 4 + ref 1）しか存在しない。つまり **本アプリの書式そのままの few-shot を増やす素材は
公式ガイド以外にほぼ無い**。素材の価値は「何を書くと効くか（内容の設計）」にあり、
「どう並べるか（書式）」は公式ガイドが唯一の権威である。この前提が第5章の提案を規定する。

---

## 2. 既存ガイドとの差分分析

### 2.1 (a) 既存ガイドに無い新知見

公式一次資料（`official-base-en.txt` / `official-ref-en.txt`）由来のもの。各項目は
`grep` で `prompts.py` に該当記述が無いことを確認済み。

#### A. base モード（`MINIMAX_H3_VIDEO_GUIDE` / `MINIMAX_H3_GUIDE_BODY` に関わる）

1. **カット動詞のバリエーションが5つある**
   公式は `the camera cuts to` / `the shot cuts to` / `the shot transitions to` /
   `the shot changes to` / `the shot switches to` を等価に許可し、
   cross-dissolve / fade / wipe は **ユーザーが明示要求したときのみ**可。
   既存ガイドは「Prefer `the camera cuts to`」としか書いておらず、
   dissolve / fade / wipe の条件付き許可に触れていない（`grep -i "transitions to\|switches to\|dissolve\|wipe"` → 0件）。

2. **「カットすべきでない」条件が明文化されている**
   > A cut should introduce new information about the subject, space, state, viewpoint, or time.
   > If only the distance or a slight angle needs to change, prefer camera motion.

   既存ガイドはカットの書式だけを規定し、この判断基準を持たない。
   エージェントが無意味に `[Shot 2]` を割るのを抑える効き目のあるルール。

3. **amplitude / speed は「意味があるときだけ」書く**
   > Add amplitude and speed only when they are meaningful; medium amplitude and normal speed are usually omitted.

   既存ガイドは `optional 'with small/large amplitude', 'at slow/fast speed'` と
   書くだけで、「中程度・通常速度は省略する」という既定値を示していない。

4. **スタイル語彙の公式リスト**
   `Cinematic` / `live-action` / `2D-animated` / `3D CG` / `claymation` /
   `watercolor` / `vintage film`。既存ガイドは `Live-action, cinematic, …` の
   例文だけで、選択肢の集合を示していない（`grep -i "claymation\|watercolor\|vintage film"` → 0件）。
   加えて「キーフレームタスクではスタイルを参照画像から導出し、T2VA ではユーザーテキストから選ぶ」という導出規則がある。

5. **話者の初出時に確立すべき属性の列挙**
   > character type, age, gender, whether the person is on-screen, pitch, timbre, speaking rate, or accent

   既存ガイドは「Identifying phrase + ID + delivery **outside** `<d>`」とだけ書き、
   *何を*書けば identity が安定するかの内訳を持たない。

6. **台詞継続表現の定型4種**
   `continues seamlessly across the cut` / `continues uninterrupted into the next shot` /
   `carries over from the previous shot` / `remains audible across the transition`。
   既存ガイドは `<scenetrans>` タグの存在だけを書き、対になる英文表現を示していない。

#### B. Ref2VA（`MINIMAX_H3_REFERENCE_VIDEO_GUIDE` に関わる）

7. **`retention_analysis` の Picture / Video 行の書式**
   既存ガイドは Subject 行の形（`<Subject 1> (appears in [Shot 1], [Shot 3]): fully_preserved - …`）
   しか示していない。公式はさらに:
   ```
   <Picture 2> ([Shot 1] first frame): fully_preserved - ...
   <Video 1> (cut and pacing structure): weak_reference - ...
   ```
   括弧の中身が Subject（出現ショット）と Picture/Video（役割）で異なるのが要点
   （`grep "cut and pacing\|first frame)"` → 0件）。

8. **video editing タスクの summary 定型導入文**
   > For video-editing tasks, begin the summary after the task-type prefix with:
   > `The target video is an edited version of <Video 1>.`

   （`grep "edited version of"` → 0件）

9. **task type 選択の判断規則**（既存ガイドは種類の列挙のみ）
   - 参照動画がカメラワーク・カット・リズムだけを供給するなら `video editing` ではなく `reference generation`。
   - 元動画を編集して元音声が残るなら `audio reuse` を併記。
   - 元動画の続きを作り音声信号は複製しないが可聴特性を継続するなら `audio reference`。
   - 「動画/音声が添付されている」だけでは対応する task type は発生しない。

10. **`(Sx)` の割り当てと `retention_analysis` での禁止**
    > Do not write `(Sx)` in `retention_analysis`.

    さらに「再利用 BGM / 完全サウンドトラック内の声だけの発話は `(Sx)` を作らず
    `<Audio N>` を音源として書く」「実体のある人物・ナレーターの声には `(Sx)` を割り当てる」。
    既存ガイドはこの区別を持たない。

11. **参照音声の台詞を書き起こすときの規約**
    - 原語・原文を `<d>` 内に verbatim 保持。
    - 聞き取れない区間は **`[unclear]`** と書き、推測・言い換えをしない。
    - 句読点は `,` `.` `?` `!` の基本記号に正規化し、連続チルダ・絵文字・装飾記号を除去。
    - 音色・リズム・感情・話し方だけを参照する場合は、元の台詞を持ち込まない。

    （`prompts.py` の `unclear` ヒットは 3165 行の別文脈のみ）

12. **`detailed_description` 語数の但し書き**
    既存ガイドは「~350–500 English words」だけ。公式はさらに
    「台詞密度が高い場合は語数より発話タイムラインの完全性を優先」
    「動画編集タスクは元動画の複雑さに応じ、生成タスクのレンジに従わなくてよい」
    「単一ショットであることは短い説明の理由にならない」と条件を付けている。

13. **参照音声の copy/reference 関係を書く場所の規則**
    > ambience and sound effects belong in `overall_soundscape`, while audience-only score belongs in `non_diegetic_music`.

    同一音声が両方を供給する場合は両セクションにそれぞれの関係を書く。

#### C. 運用仕様（二次資料。実装値として採用するかは要判断）

14. **プロンプト長の上限は 7,000 文字**（複数リポジトリのガイドが一致。実際に 7,004 文字の実例あり）。
    既存ガイドに長さの上限記述は無い（`grep "7000\|7,000"` → 0件）。
    現状の H3 ガイド＋QUALITY BAR＋FEW-SHOT を全部踏まえた出力は数千字になりうるので、
    上限の明示は実用上の価値がある。

15. **音声リファレンスは単独送信不可**（画像か動画を必ず伴う）。mixio / imagine.art の両方が明記。
    既存 Ref2VA ガイドは「At least one is required」としか書いておらず、
    「音声だけでは不可」の条件を持たない。

16. **参照アセットの尺**: 動画クリップ各 2–15 秒、音声クリップ各 2–15 秒、**音声は累計 15 秒**（mixio）。
    krea は「video+audio 合計 15 秒」とするが、mixio の方が詳細で、
    安全側は「クリップ各 2–15 秒・音声累計 15 秒」。既存ガイドに記述なし。

17. **First/Last Frame 入力と Reference 入力は同一生成で併用不可**（imagine.art）。
    本アプリでは `minimax_h3_i2v*`（start/end image）と `minimax_h3_r2v*`（参照）が
    別ワークフローなので構造的に守られているが、ガイド本文には明記が無い。
    エージェントが「r2v に end_image を付けたい」と言い出すのを防ぐ意味はある。

18. **`[pan]` `[zoom]` `[static]` のようなブラケット式カメラ指示**が
    キー記述の直後に置ける（ecomimagelab の公式ドキュメント要約）。
    既存ガイドの「カメラは自然な英文の節として書く」と競合しうるので、
    採用するなら「公式 rewrite 形式では使わない」と明示するのが安全。

19. **メタ品質語（`4K` / `8K` / `masterpiece` / `viral` / `high quality`）は効かない**とする記事が複数。
    一方 minimax3.com の成功実例には `8k resolution` が含まれる。
    → 「害は無いが効果も保証されない装飾」。H3 ガイドは現状これに触れていない
    （`prompts.py` の `masterpiece` ヒットは全て画像モデル節）。

20. **スタイルは1つに絞る**（`photorealistic × anime` のような混合は矛盾シグナル）。全記事一致。

### 2.2 (b) 既存ガイドと矛盾する記述

| # | 論点 | 既存ガイドの記述 | 別ソースの記述 | 判定 |
|---|---|---|---|---|
| M1 | **参照動画のサウンドトラックが `<Audio N>` を作るか** | 「**Every reference video is passed with its soundtrack.** Those soundtracks **share `<Audio j>` numbering** … and take the low numbers」（prompts.py 1005–1008） | 公式 ref: 「An ordinary reference video does not create `<Audio N>` merely because the file contains sound.」 | **既存ガイドが正**。既存ガイド自身が「This app's tag numbering (overrides official pairing)」と宣言済み。ComfyUI グラフが常に音声トラックを渡す以上、アプリ側の規定が実態に合う。**変更不要**だが、「なぜ公式と違うのか」の一文があると混乱が減る。 |
| M2 | **ネイティブ解像度** | 「The native canvas is a **768px short edge (max 768x1344)**: keep `megapixels` around 0.4」 | 全二次ソース: ネイティブ 2560×1440（2K）/ 24fps | **両方正**。公式モデルの上限が 2K、本アプリのローカル ComfyUI 実行が 768 短辺という運用値。混同されないよう「ローカル実行の運用値であり、モデル上限ではない」と一言添えるのが望ましい。 |
| M3 | **タイムスタンプ記法** | `[Shot N] At MM:SS.mmm` のみ許可、`[0s-1.5s] Shot 1:` を明示的に禁止（H3_QUALITY_BAR） | コミュニティ実例の大半は `[0–3s]` / `Scene 1 (0–7s):` / `0.0–3.0s —` | **既存ガイドが正**（公式仕様）。ただしコミュニティ記法でも動作報告は多数ある。**変更不要**。素材から取り込むときは記法変換が必須という運用注記だけ加えれば足りる。 |
| M4 | **台詞タグ** | `<d>[Language] …</d>` + `(S1)` 必須 | imagine.art / krea / minimax3.com の実例は素の引用符（`says, "…"`）で成功 | **既存ガイドが正**。複数話者・言語指定・ナレーション（口を閉じる）・カット跨ぎでは公式タグ体系が実質必須。**変更不要**。 |
| M5 | **トランジション語** | 既存ガイドは言及なし（`cuts to` 推奨のみ） | inreels: 「dissolve / fade to black 等は書くな」／公式: 「ユーザー明示要求時のみ可」／minimax3.com 実例: `[match cut transition]` 等を多用して成功 | **公式が正**。「デフォルトはハードカット、特殊トランジションは意図があるときだけ明示」。既存ガイドに明記が無いので 2.1-1 として追記候補。 |
| M6 | **`non_diegetic_music` の書き方** | 「1–3 sentences of instrumentation / tempo / dynamics (**not mood words**)」 | 記事実例の多くは「epic orchestral brass crescendo」等のムード寄り表現 | **既存ガイドが正**（公式の規定そのまま）。素材から音楽記述を借用するときはムード語を楽器・テンポ・強弱に置換する必要あり。 |
| M7 | **尺** | 「Duration 4–15s」 | imagine.art の実例に 20 秒あり | **既存ガイドが正**。公式は up to 15s。imagine.art はプラットフォーム拡張の可能性。 |

**結論: 既存ガイドの記述で「誤り」と判定されたものは無い。** 差分はすべて「不足」か「意図的なアプリ独自規定」。

### 2.3 (c) 既存ガイドが既にカバー済みの点

素材側で「新知見」に見えるが、既に `prompts.py` に書かれているもの（誤って新規扱いしないこと）:

- **L2VA モード（最終フレームのみ）** — `MINIMAX_H3_VIDEO_GUIDE` 923–924 行に定型文つきで既出。
  「公式と jxp しか触れていないレアなモード」だが、本アプリのガイドは既に持っている。
- **4モードのアライン定型文**（I2VA / FL2VA / L2VA の3種）— 917–924 行に完全一致で既出。
- **参照上限 画像9 / 動画3 / 音声3** — `MINIMAX_H3_REFERENCE_VIDEO_GUIDE` 960–962 行に既出
  （「12ファイル」という合計値の表現は無いが、内訳は同じ）。
- **6セクションの固定順とセクション名** — 966–975 行に既出。
- **`<Subject N>` / `<Picture N>` / `<Video N>` / `<Audio N>` のラベル4種と使い分け**
  （画像がキャラ定義だけなら独立 `<Picture N>` を作らず Subject 定義内で引用、を含む）— 977–983 行に既出。
- **task type 6種と ` + ` 連結** — 984–987 行に既出。
- **retention マーカー（視覚4種 / 音声4種）** — 988–993 行に既出。
- **`detailed_description` はスタイル1–2文を `[Shot 1]` の前に置く** — 994–995 行に既出。
- **生成タスク 350–500 語** — 996–997 行および `H3_QUALITY_BAR` 1406–1407 行に既出。
- **カメラ語彙表（Zoom/Push/Pan/Truck/Tilt/Pedestal/Arc/Tracking/Static/Shake/POV/Roll）** — 870–873 行に既出。
- **`(S1)` / `(S1,S2)` の話者ID、`<d>[Language]` 内は原文verbatim、翻訳禁止** — 874–877 行に既出。
- **ボイスオーバー定型 `says in an off-screen voiceover` + 口を閉じる明記** — 878–879 行に既出。
- **`<scenetrans>` / `<cutoff>`** — 879–880 行に既出。
- **画面内テキストは英語ダブルクォートで原文どおり** — 881–882 行に既出。
- **`overall_soundscape` 1–4文・台詞や音楽を重複させない・完全無音時のみ N/A** — 883–885 行に既出。
- **`non_diegetic_music` 1–3文・ムード語禁止・ダイエジェティック音楽は本文へ** — 886–887 行に既出。
- **`Camera:` / `Audio:` フッター禁止、`[0s-1.5s] Shot 1:` 禁止** — 888–889 行 + `H3_QUALITY_BAR` に既出。
- **`[Shot 1]` はタイムスタンプなし、以降は厳密増加のカット時刻** — 863–865 行に既出。
- **FL2VA は単一ショットを優先し、2枚の静止画を再記述せず「path」を書く** — 936–939 行に既出。
- **I2VA の推奨構造（first-frame anchor → action onset → development → result）** — 934–935 行に既出。
- **ネガティブプロンプトが無い / 末尾の `No text, subtitles, logos or watermarks.`** — 898–901 行に既出。
- **`duration` / `megapixels` / `aspect_ratio` は job フィールドで文にしない** — 897 行に既出。

---

## 3. テクニックパターン集（取り込み候補）

素材から抽出した実践パターン。**本アプリの書式に載せるときは、
「公式 rewrite 形式の中でどう表現するか」に必ず変換すること**（記法は公式が唯一の権威）。

> 本章の「実例断片」は説明のための**抜粋**であり、`...` は省略箇所を示す。
> 無改変の全文は第4章にある。

### T1. ブリーフ型ブロック構造

**説明**: `FORMAT / SUBJECTS / ACTION-BEATS / CAMERA / WORLD-PHYSICS / AUDIO / CONSTRAINTS` の
ブロックに分けて書く。目的は「長くすること」ではなく **関係の曖昧さをなくすこと**。
コミュニティの高品質プロンプトの大半が実質この構造。

**実例断片**（@maxescu / Desert Standoff）:

```
SCENE CONTEXT ... TIMELINE ... ACTIVE REFERENCES ... LOCATION MAP ...
FORMAT MODE ... CAMERA ... ACTION TIMING ... PHYSICS ... LIGHTING ... AUDIO ... POSITIVE LOCKS
```

**出典**: Anil-matcha `docs/prompting-guide.md`／apimodels `Desert Standoff — 15s single take`
（https://x.com/maxescu/status/2082563241062875568）

**本アプリへの写像**: ブロック見出しをそのまま出力してはいけない。
`integrated_multimodal_description` の1本の散文に溶かし込み、
`CAMERA` は各ショット内の英文節、`AUDIO` は `overall_soundscape` / `non_diegetic_music`、
`CONSTRAINTS` は末尾の1文に落とす。**「情報の網羅チェックリスト」としてのみ使う**のが正しい使い道。

### T2. timed beats（秒レンジ分解）

**説明**: 尺を `0–3s / 3–6s / …` に割り、各ビートに支配的アクションを1つだけ与える。
「拍が漂う・スライドショー化する」のを防ぐ最重要テク。BeatAPI では ingredient `timed-beats` が 75件以上に付く。
記事側の共通見解は「12秒に3–4ビートまで、1ショット1.5秒以上」。

**実例断片**（TechHalla / 3x3 storyboard）:

```
[TIMELINE]
0-1.7s: Wide establishing shot of a lone figure walking across endless dunes toward twin massive suns.
1.7-3.4s: Extreme close-up of her scarred face, eyes fixed on camera as wind pulls at the headscarf.
```

**出典**: 全リポジトリのガイド／`articles-guide.md` 共通テクニック 2

**本アプリへの写像**: これは公式の `[Shot N] At MM:SS.mmm, the camera cuts to …` と等価。
**ビート数の上限（尺に対する目安）とビート最短長は既存ガイドに無い**ので、
`H3_QUALITY_BAR` に「12秒なら3–4カットまで、1ショット1.5秒以上」の目安を足す価値がある。

### T3. リファレンス役割台帳（role ledger）

**説明**: 各素材に職務を明示的に割り当て、**転写してよいものといけないものを両方書く**。
`only` 付きの排他指定が最良（fal.ai ガイド「Assign every reference a job」）。

**実例断片**（Beginnersblog / 5参照ダンバギー）:

```
Use Image 1 as the strict facial-identity reference for the female racer. ...
Use Image 3 as the action reference for the pursuing drones, vehicle speed, dust trails, ...
Use Image 5 as the cockpit and performance reference. ...
```

汎用テンプレ（fal.ai / Anil-matcha 由来）:

```
Image 1 = character identity, hair, clothing, and proportions.
Video 1 = camera path and edit rhythm only.
Audio 1 = vocal tone and room acoustics only.
Do not copy the references' logos, faces, scenery, subtitles, watermark, or music.
```

**出典**: `github-prompts.md` P384 / P097 / P228、fal.ai プロンプトガイド

**本アプリへの写像**: これは `subject_definitions` + `retention_analysis` そのもの。
既存ガイドはラベル定義を書いているが、**「何を転写しないか（negative role）」を書く発想が無い**。
`retention_analysis` の説明文に「転写しない要素も明示してよい」を足す候補。

### T4. 継続性ロック（continuity lock）

**説明**: 顔・髪・衣装・小道具の**観察可能なディテール**を列挙して固定し、
末尾に継続性チェックリストを置く。編集系では「変えるもの」と「保持するもの」を必ずペアで書く。

**実例断片**（@eijo_AIart / Concrete-Plaza Kickflip Drop）:

```
POSITIVE LOCKS ... Screen direction stays camera-left to camera-right.
Same rider, same face, same cobalt-blue streak, same wardrobe and same board in all five beats.
```

**出典**: `github-prompts.md` P253 / P339、`sections.md` 「アイデンティティ・継続性ロック」

**本アプリへの写像**: r2v では `retention_analysis` の `fully_preserved` 行が担う。
t2v / i2v では受け皿が無いので、`integrated_multimodal_description` 末尾の
「screen direction / wardrobe / prop count を全ショットで保つ」1文として書くしかない。
**マルチショット t2v の継続性指示は既存ガイドの空白地帯**。

### T5. 具体的な AVOID

**説明**: 汎用の "no artifacts" ではなく、**具体的な失敗モードを3〜6個**列挙する。
表示テキストはスペルをロックする。

**実例断片**（Beginnersblog）:

```
Restrictions
No subtitles, titles, logos, watermarks, additional racers, ... distorted hands, incorrect steering,
reversed wheel rotation, teleportation, random explosions, oversized fireballs, weightless motion,
impossible jumps, camera spins, circular camera moves, rapid zooms, fluid morphs, soft dissolves,
or changes in travel direction.
```

スペルロック: `Keep the words ORBITAL, PEAR, TONIGHT exactly as written.`

**出典**: `github-prompts.md` P384 / P315、xianyu110 再構築フォーマットの AVOID セクション

**本アプリへの写像**: H3 にネガティブプロンプトは無いが、**本文末尾の禁止文は有効**という
点で既存ガイドの `No text, subtitles, logos or watermarks.` と同じ発想。
「よくある破綻（手の変形、被写体の増殖、進行方向の反転、勝手なスローモーション）を
1文にまとめて末尾に置いてよい」と拡張する候補。ただし hailuo3.me の警告どおり
**ネガティブだけの羅列は失敗する**（ポジティブ記述が先）。

### T6. JSON 形式プロンプト

**説明**: ショット配列を JSON で渡す。multi-shot の順序・パラメータを厳密化したい場合の選択肢。
主流はブロック形式の自然言語。

**実例断片**（Pan / iv-18）:

```json
{"clip_id":"animated_adventure","total_duration":"15s","shots":[
  {"shot_num":1,"duration":"4s","prompt":"Wide shot of a tiny adventurous mouse ...",
   "camera":"low angle push in","transition":"hard cut"}, ...]}
```

**出典**: `github-prompts.md` P175（https://x.com/sebatheepan/status/2082873433478582726）

**本アプリへの写像**: **採用しない**のが妥当。公式 rewrite 形式と競合する。
「JSON で書きたくなっても公式3フィールド/6セクションに落とす」と `H3_QUALITY_BAR` に
明示的な禁止を1行入れる価値はある（現状は talkvid / `Camera:` フッターしか禁止していない）。

### T7. `[LOCK]` / `[LOOP]` ブロックによるループ設計

**説明**: 「最終フレームは最初のフレームと完全に一致」を成立させるため、
(1) 変化してはならない対象を最高優先度でロック、(2) ループの因果（何が壊れ、何が戻るか）を宣言、
(3) 一時的な効果（レンズ上の汚れ、画面の亀裂）に **消滅デッドライン**を与える。

**実例断片**（@Cia0_exe / Kintsugi Sword Seamless Loop、原文は中国語・約5,000字）:

```
[循环 · 核心设定] ... 最后一帧须与第一帧完全一致。
[剑的恒定 · 最高优先级] 重铸后的剑必须与开场完全相同：长度、宽度、弧度、剑尖、护手雕花 ...
[解剖锁定 · 最高优先级] 全片有且仅有一名人物：两条手臂、两只手、每只手五指、一把剑。
所有以上效果必须在 13.8 秒前彻底清除，最后一帧的画面须与第一帧一样干净。
```

**出典**: `github-prompts.md` P234（https://x.com/Cia0_exe/status/2083525563491524882）

**本アプリへの写像**: シームレスループを作りたいときの設計思想として有用だが、
**プロンプトが長大かつ中国語**なので few-shot 全文採用には向かない。
「ループを作るなら (a) 一致すべき最終フレームの宣言、(b) 一時効果の消滅時刻、
(c) 解剖ロック の3点を書く」という3行のルールに圧縮して取り込むのが現実的。

### T8. 複数パート連作の状態引き継ぎ

**説明**: 15秒を超える芝居を2本のクリップに割り、
2本目の冒頭で「1本目から継承する正確な位置・表情」を宣言する。
共有スタイル・継続性・スクリーン方向を両クリップの上位に置く。

**実例断片**（NΞXUS / Two-part confession）:

```
CONTINUITY
The husband remains frame left beside the coffee table, holding his wife’s unlocked phone ...
Preserve their faces, clothes, positions and screen direction across every cut.

CLIP 2 — THE REASON
Begin from the exact positions and expressions inherited from Clip 1.
```

**出典**: `github-prompts.md` P339（https://x.com/NEXUS_TO_NOVA/status/2082548512286224793）

**本アプリへの写像**: スタジオは 1 カット = 1 ジョブなので、**連続カットを跨ぐ状態引き継ぎの
書き方は既存ガイドに無い**。`minimax_h3_*_context`（連続カット）ワークフローがあるので、
「前カットから継承する状態を Shot 1 冒頭で1文宣言する」パターンとして
`MINIMAX_H3_GUIDE_BODY` に足す価値が高い。

### T9. 音声イベントタイムライン

**説明**: 音声を「装飾」ではなく**イベントのタイムライン**として書く。
音は物理イベントに同期させる（「keys on wood at 1.3s」）。
BGM はジャンル・BPM 感・カットとの同期（beat-synced cuts）で指定。
音声リファレンス使用時は「声/リズム/SE/ルームトーンのどれを供給するか」を宣言。

**実例断片**（@eijo_AIart）:

```
AUDIO Lofi hiphop bed at 72 BPM throughout: dusty vinyl crackle, soft boom-bap kick and brushed
snare, mellow jazz piano loop, warm bass. On top: wheels rumbling over seams, the crack of the tail
on the top step, the board fluttering as it flips, a quiet beat of air as she falls, a hard slap as
the wheels hit the lower plaza.
```

**出典**: `github-prompts.md` P253 / P384、`sections.md` 「音声レイヤリング」

**本アプリへの写像**: 公式の分業（同期音は `integrated_multimodal_description` 内、
全体の環境音は `overall_soundscape`、観客のみの BGM は `non_diegetic_music`）と整合する。
既存ガイドは分業を書いているが、**「同期音は本文の当該ショットに書く」の実演例が
`FEW_SHOT_H3` に薄い**。few-shot 追加で補うべき点。

### T10. 台詞をカットの骨格に使う（beat = line）

**説明**: 各ショットに台詞を1つだけ割り当て、ショットの区切りを発話の区切りに一致させる。
台詞を全文・原語で書き、話者の声質（年齢・音域・訛り）をタグ外に置く。

**実例断片**（WasifAI / Korean noir teaser）:

```
1. She enters: "언니... 여기 있어?"
2. Lighter ignites, revealing his scar. "오랜만이네."
3. She freezes: "당신... 죽은 줄 알았는데."
```

**出典**: `github-prompts.md` P338（https://x.com/doctorwasif/status/2082790356983447606）

**本アプリへの写像**: `<d>[Korean] …</d>` + `(S1)` / `(S2)` に変換すればそのまま公式形式になる。
**`FEW_SHOT_H3` には多言語・複数話者・ショット跨ぎの例が無い**ので、
この形の変換済み例を1本足す価値が高い。

### T11. カメラ言語 — 1ビート1ムーブ

**説明**: 1ビートにつき主要カメラムーブは1つ。
`locked-off`（商品・ループ・文字）/ `slow push-in`（強調）/ `lateral track`（工程・移動）/
`gentle orbit`（単一被写体）/ `handheld follow`（ドキュメンタリー・UGC感）。
`orbit + crane + zoom + whip pan` の積み上げは、変化自体が目的でない限り避ける。

**出典**: `sections.md` 「カメラ言語」／`articles-guide.md` 共通テクニック 3

**本アプリへの写像**: 公式の「motion type + amplitude + speed」語彙と衝突しない補足ルール。
`H3_QUALITY_BAR` に「1ショット1ムーブ」を1行足す候補。

### T12. トランジションを物理イベントとして書く

**説明**: 「シームレスに変わる」ではなく、動き・ブラー・露出変化・カット点・収まり方を
物理現象として記述する。マテリアルマッチのタイミングを明示する。

**実例断片**（Bennett Heyn / fal）:

```
At the exact moment when the cocoa particles, foam contours, and coffee swirl closely resemble the
dune ridges, wind-carved textures, and airborne sand in @Image 2, transition seamlessly into the
desert landscape.
```

**出典**: `github-prompts.md` P317（https://fal.ai/learn/devs/minimax-h3-prompting-guide）

### T13. 1変数ずつのイテレーション

**説明**: 1回の変更は1ブロックのみ。スタイル段落を足して動きが変わるなら、
その挙動を ACTION/CAMERA ブロックへ移す。run card（slug・設定・入力・結果・次の変更1つ）で記録。

**出典**: Anil-matcha `docs/prompting-guide.md`

**本アプリへの写像**: エージェントの再生成ループの指針として、
`H3_QUALITY_BAR` ではなくエージェント運用側のプロンプトに置く方が適切。

---

## 4. カテゴリ別 few-shot 候補（全文）

**選定基準**: (i) 公式例・promptSource=original を優先（reconstructed は不採用）、
(ii) モード網羅、(iii) カテゴリ網羅、(iv) このアプリの書式へ変換しやすいもの。

**全 35 本**。プロンプトは英語原文のまま無改変で掲載（原文が中国語・繁体字のものはその旨を注記）。

### 4.0 内訳

| モード | 本数 | 番号 |
|---|---:|---|
| T2VA | 8 | C01–C08 |
| FL2VA（首尾フレーム） | 7 | C09–C15 |
| Ref2VA（参照生成） | 12 | C16–C27 |
| Ref2VA（生成編集） | 8 | C28–C35 |

| カテゴリ | 番号 |
|---|---|
| シネマティック・ストーリー | C02, C04, C10, C22 |
| 商品CM・ブランドフィルム | C16, C26 |
| アニメ・スタイライズド | C08, C11, C24 |
| VFX・トランジション | C26, C34 |
| UI・ゲーム・モーションデザイン | C13, C14, C25 |
| 対話・ナレーション・音声 | C03, C04, C05, C18, C32 |
| ホラー | C07 |
| UGC・SNS | C27 |
| ファッション・アイデンティティ | C12, C22, C23 |
| アクション | C06, C27, C35 |
| 生成編集（置換・リライティング） | C28–C35 |

---

### 4.1 T2VA（8本）

#### C01 — 公式・最小 T2VA

- **モード**: t2v ｜ **カテゴリ**: アクション/ダンス
- **出典**: ecomimagelab `h3-0001`（https://github.com/ecomimagelab/awesome-minimax-h3-prompts）／ MiniMax 公式 API ドキュメント
  https://platform.minimax.io/docs/guides/video-generation
- **サンプル動画**: https://filecdn.minimax.chat/docs/video-generation-v2/text-to-video.mp4
- **種別**: official (MiniMax) ｜ 5s / 16:9

```text
A tiktok dancer is dancing on a drone, doing flips and tricks.
```

- **適合度**: 要変換。「短文でも動く」ことの証拠として価値がある。
  公式形式にするには `[Shot 1] Live-action, cinematic, …` のスタイル+構図の頭出しと
  `overall_soundscape` / `non_diegetic_music` を補うだけ。
  **エージェントが1行を長文に膨らませすぎるのを戒める対照例として使える**。

#### C02 — 公式 T2VA・実写×手描きアニメの混成 + スマホ撮影質感

- **モード**: t2v ｜ **カテゴリ**: シネマティック / スタイル混成
- **出典**: ecomimagelab `h3-0025` ／ MiniMax 公式ブログ https://www.minimax.io/blog/minimax-h3
- **サンプル動画**: https://www.minimax.io/blog/minimax-h3
- **種別**: official (MiniMax) ｜ 5–15s

```text
15 seconds, 16:9 landscape. Combine a live-action late-night laundromat with hand-drawn luminous animation. The small self-service laundromat has gently flickering fluorescent lights, running washers, plastic baskets, a worn bench, and one sock on the floor. Keep the space quiet and faintly nostalgic.

Use a one-handed phone-camera feel with visible shake, exposure fluctuation under white fluorescent light, environmental reflections in glass, and delayed autofocus at close range. Avoid polished commercial composition; it should feel like an authentic late-night encounter, filmed while following a strange apparition.
```

- **適合度**: 要変換（尺・比率の記述を job フィールドへ移す、`[Shot 1]` 頭出しを付ける）。
  「スタイルは1つに絞れ」の例外＝**意図的な混成**をどう書くかの公式実例として貴重。

#### C03 — 多言語台詞をショットの骨格に使う（韓国語ノワール）

- **モード**: t2v ｜ **カテゴリ**: 対話・ナレーション
- **出典**: imagineVid `iv-03`（https://github.com/imagineVid/Awesome-minimax-h3-prompts-and-skills）
  ／ クリエイター WasifAI https://x.com/doctorwasif/status/2082790356983447606
- **サンプル動画**: https://video.twimg.com/amplify_video/2082790293515186176/vid/avc1/1920x1080/3nKjrxlXhylP5Vo_.mp4?tag=29
- **種別**: community（original）

```text
16:9, 15s, hyper-realistic Korean noir crime teaser.

A rain-soaked Korean woman (late 20s, trench coat) enters an abandoned underground nightclub searching for her missing sister. A scarred crime boss (40s), presumed dead for years, emerges from the shadows with a lit cigarette. Atmosphere: 90s Korean noir, practical neon, rain, smoke, film grain, tactile realism, no CGI gloss.

Shots:

1. She enters: "언니... 여기 있어?"
2. Lighter ignites, revealing his scar. "오랜만이네."
3. She freezes: "당신... 죽은 줄 알았는데."
4. Slow push-in. He smiles: "네 언니가 날 죽였다고 생각했겠지."
5. They lock eyes. Thunder. Cut to black.

Audio: Rain, thunder, jazz crackle, lighter click, intimate silence during dialogue. #MiniMaxH3
```

- **適合度**: 要変換だが**変換価値が最も高い1本**。
  番号付きショット → `[Shot N] At MM:SS.mmm`、台詞 → `<d>[Korean] …</d>` + `(S1)` / `(S2)`、
  `Audio:` 行 → `overall_soundscape`。`FEW_SHOT_H3` に無い「多言語・2話者・5カット」を一度に満たす。

#### C04 — 単一ショット・オフフレーム話者・厳密な台詞台本

- **モード**: t2v ｜ **カテゴリ**: 対話・シネマティック
- **出典**: apimodels.app `Desert Standoff — 15s single take`（https://apimodels.app/minimax-h3-prompts）
  ／ クリエイター @maxescu https://x.com/maxescu/status/2082563241062875568
  （同一プロンプトが GitHub 集約の P328「Don't say my name again」にも収録）
- **サンプル動画**: https://video.twimg.com/amplify_video/2082560936645152769/vid/avc1/642x360/xwdOrY-5pBgcckES.mp4
- **種別**: community（promptSource: original）｜ 15s / 16:9

```text
SCENE CONTEXT A middle-aged man stands in the middle of a dirt road in open desert and holds a pistol level at the person filming him. He gives an instruction, is answered by name, and warns them not to say it again. Nothing is fired.

TIMELINE 0.0–3.0s — he is already aimed and steady; his first line lands. 3.0–6.0s — the off-frame reply uses his name; his jaw sets and the muzzle does not drop. 6.0–9.0s — his second line; he takes half a step forward and re-sights. 9.0–11.0s — the off-frame voice starts his name again and stops. 11.0–15.0s — his last line; wind lifts dust across the road behind him; he holds the aim. Hold to black-free end.

ACTIVE REFERENCES No image or video references are active in this shot. Build the man from description only. Man, fifties, white, average build, standing, thinning brown hair, a brown moustache, thin-rimmed prescription glasses; light green long-sleeved button-down shirt with the sleeves buttoned at the wrist, dark work trousers; stern, brow furrowed, sweat starting at the hairline; frightened underneath and controlling it; speaking voice flat, plain, mid-western American, no theatricality. Second speaker: the unseen person the pistol is aimed at, occupying the camera position, never entering frame and never seen. Voice only — male, forties, close to the lens, low and careful.

LOCATION MAP Camera on the dirt road 3.5 m from him at his chest height, facing him square-on so the muzzle is aligned with the lens axis. FG: rutted dirt and loose gravel across the lower frame. MG: the man alone, centre frame. BG: the dirt road receding to a vanishing point at x 50%, y 44%, low scrub and dry brush from x 0% to x 30% and x 70% to x 100%, a distant fence line at x 62%, y 48%, flat desert horizon at y 46%, overcast sky filling the upper half with one pale blue break low at x 24%, y 36%. He occupies x 50%, y 50%, body frontal, both feet planted, right arm extended toward camera with the silver pistol at x 50%, y 56%, held at the very front of the depth plane. His eyeline is straight down the lens. The first visible frame already has him aimed and in position — no empty establishing frame, no walk-in, no delayed reveal, and he remains the only figure in frame for the entire take.

FORMAT MODE Single continuous take, no internal cuts, no transitions, no fades. Real-time motion. His lines are on camera; the second voice is an intentional off-frame speaker at the camera position. No other voices, no narration, no subtitles, no score.

CAMERA 47° diagonal field of view, camera 3.5 m from him at chest height, natural human-eye perspective, no distortion, natural face and body proportions, the road and horizon readable behind him, comfortable depth of field with the pistol slightly forward of his focal plane. Handheld, shoulder-mounted mass, operator breath and small involuntary settling — the camera is being held by the person he is aiming at, so it flinches minutely rather than gliding. Total travel under 20 cm, no push-in, no zoom, no focus rack. Focus holds his eyes behind the glasses; the pistol stays just soft.

ACTION TIMING He is already sighted along the barrel as the shot opens and speaks without lowering it. The off-frame voice answers with his name; his jaw sets and the muzzle does not waver. He speaks his second line and takes half a step forward on the dirt, re-sighting as his weight lands. The off-frame voice begins his name again and stops. He delivers his last line, and wind lifts a curtain of dust across the road behind him. He holds the aim to the end without firing and without lowering the weapon.

PHYSICS The pistol carries real weight — his wrist and forearm show the load and micro-correct continuously; the muzzle drifts a few millimetres and is pulled back on line. His half step lands heel-first in soft dirt with visible weight transfer and a small dust puff that drifts and thins. Shirt fabric pulls across the extended shoulder and creases at the elbow. Wind moves the loose dust and the dry scrub in the same direction and at the same time. Thinning hair lifts and settles with the gust. Sweat beads at his temple and does not run.

LIGHTING Primary source is the full overcast sky acting as one enormous soft toplight, giving even illumination with minimal harsh shadow — a soft shadow under the brow and the nose only, no hard edges anywhere. Secondary is dry ground bounce filling under the chin and lifting the shadow side of the pistol. Both lenses of his glasses carry a broad soft reflection of the sky, and his eyes stay readable behind them. Exposure is set for his face; the sky sits bright but not clipped. No sun shaft, no lens flare, no added key, no light change during the take.

AUDIO Dry wind across open ground, loose grit moving on the road, dry brush ticking, one distant bird, his own breathing close and unsteady. Ambient ducks under the voices. Exactly this exchange, flat and plain, no shouting — 25 words total: HIM: "Put it on the ground. Slowly." OFF-FRAME: "You're not going to shoot me, Ray." HIM: "I've been wrong about myself before." OFF-FRAME: "Ray—" HIM: "Don't say my name again." His first line opens the shot. The off-frame reply lands as his jaw sets. His second line comes as he takes the half step. The off-frame "Ray—" is cut off by his last line, which is quieter than everything before it. His lips move only for his own lines; there is no dialogue in the pauses and no muttering between beats.

POSITIVE LOCKS Exactly one visible person is in frame for the whole take — nobody enters at any edge, no second figure appears on the road, at the fence line or in the reflections on his glasses, and the off-frame speaker is never seen. The pistol stays in his right hand, stays aimed at the lens, and is never fired, never lowered and never re-holstered; no muzzle flash, no recoil, no gunshot. He stays on the road and never turns away from camera. Naturalistic muted palette of dry greens and browns against pale overcast grey. Live-action photoreal footage shot on ARRI Alexa 35, tense neutral mood, natural film grain.
```

- **適合度**: 要変換（ブロック見出しを散文へ、`AUDIO` の台詞を `<d>[English]` + `(S1)` / `(S2)` へ、
  オフフレーム話者は公式の `says in an off-screen voiceover` ではなく「画面外の第2話者」なので
  `(S2)` を割り当てて off-screen と明記）。
  **「単一ショットでも 350–500 語を書き切る」ことの説得力ある実例**であり、
  `H3_QUALITY_BAR` の「single shot does not justify a shorter description」を裏づける。

#### C05 — 2クリップ連作・状態引き継ぎ

- **モード**: t2v（2本連作） ｜ **カテゴリ**: 対話・ドラマ
- **出典**: imagineVid `iv-15` ／ BeatAPI `beat-live-action-relationship-confession-drama-scene-224793`
  ／ クリエイター NΞXUS https://x.com/NEXUS_TO_NOVA/status/2082548512286224793
- **サンプル動画**: https://video.twimg.com/amplify_video/2082547031676014592/vid/avc1/2560x1440/GtcZPkkTO6DBdSi9.mp4?tag=29
- **種別**: community（original）

```text
Grounded photorealistic live-action relationship drama, natural 24fps motion.
FORMAT

Two connected 15-second clips, 16:9, grounded photorealistic live-action relationship drama, natural 24fps motion.

SHARED STYLE

A small living room at night. One warm floor lamp mixes with cold blue streetlight through the curtains. A framed wedding photograph hangs behind the couple. Restrained handheld camera, realistic skin and fabric, tense silence, no melodramatic shouting.

CONTINUITY

The husband remains frame left beside the coffee table, holding his wife’s unlocked phone containing the evidence. The wife remains frame right near the window. Preserve their faces, clothes, positions and screen direction across every cut.

CLIP 1 — THE CONFESSION

0.0–5.0

Camera: Slow 35mm push toward both characters.

Action: The husband stares at the phone, then raises his eyes toward her.

HUSBAND: “How long has this been going on?”

5.0–8.0

Camera: Cut to a restrained 50mm close-up of the wife.

Action: She cannot meet his eyes. After a short pause:

WIFE: “Six months.”

8.0–15.0

Camera: Close on the husband. His grip tightens around the phone, but he controls his voice.

HUSBAND: “Six months? I gave you twelve years. Why?”

Final frame: The wife finally looks directly at him, preparing to answer.

CLIP 2 — THE REASON

Begin from the exact positions and expressions inherited from Clip 1.

0.0–6.5

Camera: Slow push toward the wife as she folds her arms defensively.

WIFE: “Because I’m tired of being broke. Every dream with you becomes ‘maybe one day.’”

6.5–10.0

Camera: Cut to the husband. His anger gives way to disbelief.

HUSBAND: “So you chose him for his money?”

10.0–15.0

Camera: Return to the wife. She holds his gaze without apologizing.

WIFE: “I chose someone who can give me the life I want.”

Final frame: Hold on the husband as the meaning lands. His hand lowers, and the phone slips onto the sofa.
```

- **適合度**: 要変換（`Camera:` 行 → ショット内の英文節、`HUSBAND:` → `(S1)`、
  秒レンジ → `[Shot N] At MM:SS.mmm`）。
  **スタジオの連続カット（`minimax_h3_*_context`）に対する唯一の実例級素材**。
  `CONTINUITY` ブロックが「前カットから継承する状態の宣言」のお手本。

#### C06 — 5枚参照 + 秒レンジ + 音声演出 + 具体的 AVOID

- **モード**: t2v（画像を参照として言及） ｜ **カテゴリ**: アクション / SF
- **出典**: imagineVid `iv-23` ／ BeatAPI `beat-15-second-16-9-photoreal-cinematic-action-sequence-with-743096`
  ／ クリエイター Beginnersblog https://x.com/beginnersblog1/status/2083039412506743096
- **サンプル動画**: https://video.twimg.com/amplify_video/2083039329493135360/vid/avc1/2548x1080/o_mzrwkWta15lC3H.mp4?tag=29
- **種別**: community（original）

```text
Create a 15-second, 16:9 photoreal cinematic action sequence with native stereo audio. Treat the five images as coordinated multimodal references for identity, vehicle design, environment, performance, action, cinematography, and sound.

Use Image 1 as the strict facial-identity reference for the female racer. Preserve her exact face, dark tied-back hair, amber goggles, dusty skin, black tactical suit, shoulder armor, gloves, and focused expression.

Use Image 2 as the strict reference for her full costume, body proportions, armored black dune buggy, tire scale, exposed suspension, cyan headlights, roll cage, desert lighting, and industrial-outpost background.

Use Image 3 as the action reference for the pursuing drones, vehicle speed, dust trails, fire, debris, chase intensity, and camera proximity.

Use Image 4 as the location reference for Outpost 07, including its rusted towers, pipelines, bridges, rocky terrain, distant mountains, warm sunset atmosphere, and industrial scale.

Use Image 5 as the cockpit and performance reference. Preserve the same steering wheel, open roll cage, goggles, gloves, facial identity, driving posture, and vehicle interior.

Sequence
[0–2.5 seconds]

Open on Image 4 with an extreme-wide aerial establishing shot of Outpost 07 at sunset. The industrial complex fills the right side of frame while an empty desert route curves through the rocky foreground.

The camera dives rapidly toward the road as a small black dune buggy bursts from beneath an elevated pipeline, throwing a long dust plume behind it.

Keep the vehicle moving consistently from left to right toward the outpost. No direction reversal.

Audio begins with dry desert wind, distant industrial machinery, a low cinematic pulse, and the buggy engine approaching rapidly.

[2.5–5 seconds]

Cut at peak engine sound to Image 5.

Tight frontal cockpit shot mounted just ahead of the driver. The vehicle shakes naturally over rough ground. Her hands hold the steering wheel firmly while she makes small, physically accurate corrections.

Her amber goggles remain on top of her head. Loose strands of hair and fabric straps react to wind and vibration. Her eyes briefly check the left mirror, then return immediately to the road.

A red warning light reflects across her face as a targeting alarm begins.

Do not make her turn the steering wheel excessively. Her body, wheel movement, and vehicle direction must remain mechanically connected.

[5–8 seconds]

Cut to a low front three-quarter tracking shot based on Image 3.

Three pursuit drones descend behind the buggy in a triangular formation. Their rotors, stabilizers, and body movement respond realistically to speed and turbulence.

The lead drone fires into the sand beside the buggy. The impact creates a narrow eruption of dirt, sparks, and fragmented rock rather than an oversized fireball.

The racer steers sharply around the impact. The buggy’s front wheels turn first, the suspension compresses, the body leans, and the rear tires slide outward before regaining traction.

The camera tracks beside the vehicle without spinning or overtaking it.

[8–11 seconds]
Continue the same drift into a rear three-quarter shot.

The buggy races toward a narrowing passage between a rock wall and the outer structures of Outpost 07. The racer pulls a mechanical handbrake lever for one brief moment, rotating the vehicle through the opening.

One drone follows too closely and clips a rusted overhead pipe. Its wing breaks, sending the drone tumbling into the sand behind her.

Show the collision in the background while keeping the buggy dominant and moving forward. No slow motion.

Audio: tire scrape, suspension impact, metal tearing, drone rotors failing, engine rev rising.

[11–13 seconds]

Cut to a wheel-level macro shot.

The right rear tire bites into loose sand. Stones fire backward while the suspension rebounds. The camera rises naturally along the buggy’s side and reveals the two remaining drones closing in.

The racer presses a guarded switch beside the steering wheel.

A compact rear-mounted electromagnetic pulse discharges as a restrained blue-white distortion wave, briefly disrupting the drones’ lights and stabilizers.

No magical energy, lightning storm, or giant explosion.

[13–15 seconds]
Cut to a low frontal hero shot as the buggy clears the outpost gate at full speed.

The two disabled drones fall into the dust behind it while the vehicle launches from a shallow ridge. Keep the jump low, heavy, and physically believable.

During the brief airborne moment, cut to Image 1 for a tight close-up of the racer. Preserve her exact identity as warm firelight and cool dashboard light cross her face. Her expression remains controlled and determined.

The buggy lands hard beyond the gate. The suspension compresses, the engine roars, and the vehicle continues directly into the desert.

End with a sharp cut to black on the landing impact.

Visual Direction
Premium live-action science-fiction action trailer with realistic CGI integration, warm orange sunset light, restrained steel-blue technology accents, dusty atmosphere, hard surface detail, cinematic contrast, subtle film grain, realistic motion blur, and 24 FPS movement.

Use wide shots to establish scale, cockpit close-ups for tension, low tracking shots for speed, and mechanical macro shots for physical detail.

Keep the editing fast but readable. Every shot must begin from the physical state established by the previous shot.

Audio Direction
Native stereo sound with:
Aggressive combustion engine
Tire friction over sand and rock
Suspension rattles and chassis vibration
Drone rotors and targeting alarms
Sand impacts, metal collisions, and falling debris
Low percussion and rising electronic tension
One heavy bass impact on the final landing

Keep music underneath the vehicle and environmental sounds. No dialogue or voice-over.

Restrictions
No subtitles, titles, logos, watermarks, additional racers, pedestrians, creatures, motorcycles, futuristic city skyline, nighttime transition, costume changes, facial changes, vehicle redesign, additional wheels, floating vehicle parts, distorted hands, incorrect steering, reversed wheel rotation, teleportation, random explosions, oversized fireballs, weightless motion, impossible jumps, camera spins, circular camera moves, rapid zooms, fluid morphs, soft dissolves, or changes in travel direction.  #MiniMaxH3
```

- **適合度**: 要変換。本アプリでは画像参照があるなら **Ref2VA へ振り分ける**のが正しい
  （t2v で `Image 1` と書いても参照は付かない）。
  変換すると `subject_definitions`（5枚の役割台帳）／`retention_analysis`（`fully_preserved` 5行）／
  `detailed_description`（6ビート）／`overall_soundscape`／`non_diegetic_music` にきれいに割れる。
  **役割台帳（T3）と具体的 AVOID（T5）と音声演出（T9）を1本で全部見せられる**教材価値が高い。

#### C07 — 7ショット・タイムコード付きホラー（繁体字中国語原文）

- **モード**: t2v ｜ **カテゴリ**: ホラー
- **出典**: BeatAPI `beat-1970-japanese-urba-207488`（https://github.com/BeatAPI/awesome-minimax-h3-prompts）
  ／ クリエイター @drjoetw https://x.com/drjoetw/status/2082669221222207488
- **サンプル動画**: https://media.beatapi.io/prompt-gallery/minimax-h3/1970-japanese-urba-207488/video-76b98520e830.webm
- **種別**: community ｜ 15s / 16:9（本文では 9:16 指定）
- **注記**: 原文は繁体字中国語。英語プロンプトではないが、
  **7ショットに時刻を振り、各ショットに台詞1本と音楽の状態変化を割り当てる構造**が
  公式 `[Shot N] At MM:SS.mmm` 形式に最も近いコミュニティ例なので採録した。

```text
😱👇

【風格】
1970年代日本都市傳說恐怖電影（Japanese Urban Legend Horror Movie），經典日系恐怖片風格，昭和時代澀谷街景，高密度群眾演出，電影級分鏡，強烈戲劇張力，歡樂復古流行音樂逐漸扭曲變調為詭異恐怖配樂，緊湊快節奏剪輯，陰森都市怪談氛圍，9:16直式畫面。

【音樂】
開場：歡樂復古1970年代日本流行樂。
中段：音樂逐漸失真、降速、出現不和諧音。
結尾：低沉詭異弦樂、女性怪笑聲、電子故障音（Glitch）。

【總時長】15秒，中文對話

【場景】1970年代東京澀谷區熱鬧商店街 → 暗巷 → 都市傳說恐怖事件

【鏡頭1（00:00 - 00:03）】
熱鬧的1970年代澀谷街頭。  大量打扮前衛新潮的年輕男女逛街聊天。
地面散落報紙、飲料罐與垃圾。  突然一名穿著女僕咖啡廳制服的粉紅色長髮可愛女店員從狹窄暗巷衝出來。  她身穿低胸黑色與白色相間的女僕裝，搭配白色長手套、黑色網襪與高跟鞋，臉色慘白、滿臉恐懼。  一邊奔跑一邊尖叫：
「救命啊！有鬼！」下一秒她重重跌倒在地，當場昏迷。  周圍路人紛紛停下腳步圍觀。
【鏡頭2（00:03 - 00:06）】
一名醉醺醺的平頭流氓推開圍觀群眾。  肩膀露出龍紋刺青。  他粗暴地大喊：
「滾開！」人群驚慌後退。  流氓蹲到女店員旁邊。  不耐煩地用力搖晃她肩膀：
「小姐！妳醒醒！」
【鏡頭3（00:06 - 00:08）】
女店員虛弱睜開眼睛。  顫抖地說：
「有鬼……」她艱難抬起左手。  指向旁邊漆黑暗巷。  接著再次失去意識。  流氓順勢轉頭望向暗巷。  背景音樂突然停止。
【鏡頭4（00:08 - 00:10）】
暗巷深處。  垃圾堆旁飄浮著一名白衣長黑髮女鬼。  長髮遮住半張臉。  蒼白雙眼死死瞪著鏡頭。  周圍空氣微微扭曲。  遠方傳來低沉詭異笑聲。
【鏡頭5（00:10 - 00:12）】
流氓臉部超特寫。  他震驚得不自覺張開嘴巴。  頭緩緩歪向右側。  醉意瞬間消失。  額頭冒出冷汗。
【鏡頭6（00:12 - 00:14）】
女鬼也做出完全相同的動作。  嘴巴慢慢張開。  頭歪向右側。  接著發出尖銳而詭異的笑聲：
「呵呵呵呵呵……」下一秒。  女鬼突然高速朝鏡頭飛撲而來。  長髮與白衣瞬間佔滿整個畫面。
【鏡頭7（00:14 - 00:15）】
畫面突然發生強烈電子故障。  Glitch！  黑白噪訊瘋狂閃爍。  尖銳雜音爆發。  畫面瞬間切成全黑。  只剩女鬼最後一聲詭異笑聲在黑暗中迴盪。
```

- **適合度**: 要変換（本文は英語化し、台詞だけ `<d>[Chinese] …</d>` に原文保持。
  `【音樂】` の3段変化は `non_diegetic_music` の「dynamic development」に相当）。
  **ホラーの音楽ダイナミクス（明るいポップ → 歪み → 不協和）を非ダイエジェティック音楽として
  どう書くかの好例**。既存 few-shot にホラーもジャンル音楽の変化も無い。

#### C08 — JSON 構造化プロンプト（4ショット・3Dアニメ）

- **モード**: t2v ｜ **カテゴリ**: アニメ・スタイライズド
- **出典**: imagineVid `iv-18` ／ BeatAPI `beat-pixar-style-mouse-adventure-3d-animation-582726`
  ／ クリエイター Pan https://x.com/sebatheepan/status/2082873433478582726
- **サンプル動画**: https://video.twimg.com/amplify_video/2082872957668397056/vid/avc1/2560x1440/5ofiFcuAyAb_e_Dr.mp4?tag=29
- **種別**: community（original）

```text
{       "clip_id": "animated_adventure",       "genre": "3D Animation / Pixar Style",       "total_duration": "15s",       "aspect_ratio": "16:9",       "style_keywords": "Pixar-style 3D animation, vibrant colors, expressive characters, cinematic lighting, whimsical, high detail textures",       "shots": [         {           "shot_num": 1,           "duration": "4s",           "prompt": "Wide shot of a tiny adventurous mouse wearing a leather aviator cap standing on the edge of a giant mushroom in an enchanted forest, giant flowers towering overhead, Pixar-style 3D animation, vibrant colors, soft cinematic lighting",           "camera": "low angle push in",           "transition": "hard cut"         },         {           "shot_num": 2,           "duration": "4s",           "prompt": "Medium shot of the mouse launching into the air on a dandelion seed parachute, floating through sunbeams, pollen particles sparkling around, joyful expression, 3D animated film style, warm golden light",           "camera": "tracking shot from below",           "transition": "match cut on motion"         },         {           "shot_num": 3,           "duration": "4s",           "prompt": "Dynamic action shot of the mouse swooping through a hollow log, fireflies lighting the way, exaggerated squash-and-stretch animation, motion blur on background, Pixar-style adventure sequence",           "camera": "POV following through tunnel",           "transition": "whip pan"         },         {           "shot_num": 4,           "duration": "3s",           "prompt": "Wide shot of the mouse landing triumphantly on a lily pad in a moonlit pond, ripples spreading outward, bioluminescent plants glowing, 3D animation, magical atmosphere, satisfied smile",           "camera": "crane shot rising up",           "transition": "end"         }       ]     },
```

- **適合度**: **反例として採用推奨**。動作報告はあるが公式 rewrite 形式ではない。
  `H3_QUALITY_BAR` に「JSON のショット配列で書かない。公式3フィールドに落とす」を追加する根拠。
  なお `duration` の合計（4+4+4+3=15s）→ `[Shot 2] At 00:04.000` … への機械的変換は容易。

---

### 4.2 FL2VA（首尾フレーム）（7本）

#### C09 — 公式・最小 FL2VA

- **モード**: first-last frame ｜ **カテゴリ**: トランジション
- **出典**: ecomimagelab `h3-0003` ／ MiniMax 公式 API ドキュメント
  https://platform.minimax.io/docs/guides/video-generation
- **サンプル動画**: https://filecdn.minimax.chat/docs/video-generation-v2/first-last-frame.mp4
- **種別**: official (MiniMax) ｜ 5s

```text
A little girl grows up.
```

- **適合度**: 要変換（アライン行 + `[Shot 1]` + 3フィールド）。
  「FL2VA は2枚の静止画を再記述せず**間の path** を書く」を最短で示す極端例。

#### C10 — 公式 FL2VA・タイポグラフィ主導のティザー

- **モード**: first-last frame ｜ **カテゴリ**: シネマティック / タイトルシーケンス
- **出典**: ecomimagelab `h3-0022` ／ https://www.minimax.io/blog/minimax-h3
- **サンプル動画**: https://www.minimax.io/blog/minimax-h3
- **種別**: official (MiniMax) ｜ 5–15s

```text
Epic theatrical space-opera teaser

Keep the pace fast and the scale enormous without letting the edit drag. Use sharp hard cuts, a shaking command deck, white-hot flashes, split-second black frames, and a violent jump-to-warp impact. Title cards should use wide-tracked cinematic typography—not pure white—with restrained material texture, subtle illumination, and a faint edge glow. Animate the titles by emerging from deep-space shadow, catching a sweep of starlight, opening their letter spacing, leaving a slight afterimage, and flashing briefly against black.
```

- **適合度**: 要変換。文字アニメーションを「素材・光・残像・字間」で書く公式の書きぶりが参考になる。
  本アプリの `No text, subtitles, logos or watermarks.` と衝突するので、
  **意図的なタイポグラフィ演出時はその末尾文を出さない**という分岐を
  `MINIMAX_H3_GUIDE_BODY` に明示する必要がある（現状は無条件に近い）。

#### C11 — 公式 FL2VA・クレイアニメ + カメラの大胆な移動

- **モード**: first-last frame ｜ **カテゴリ**: アニメ・スタイライズド
- **出典**: ecomimagelab `h3-0035` ／ https://www.minimax.io/blog/minimax-h3
- **サンプル動画**: https://www.minimax.io/blog/minimax-h3
- **種別**: official (MiniMax) ｜ 5–15s

```text
Claymation. A fox sprints to the edge of a cliff and launches without hesitation, making a dramatic heroic leap in slow motion over an immense lava canyon. Midair, the camera races beneath the fox’s belly in a bold dynamic move, revealing the terrifying depth of the chasm and the fully extended motion of its clay body.
```

- **適合度**: **ほぼそのまま使える**。スタイル語を文頭に置く公式の作法（`Claymation.`）が
  既存ガイドの `Live-action, cinematic,` と同じ形。`[Shot 1]` を前置し3フィールドに包むだけ。
  既存 few-shot に実写以外のスタイルが1件も無いので補完価値が高い。

#### C12 — 公式 FL2VA・精密な衣装スワップ（変えるもの / 変えないもの）

- **モード**: first-last frame ｜ **カテゴリ**: ファッション / VFX
- **出典**: ecomimagelab `h3-0054` ／ https://www.minimax.io/blog/minimax-h3
- **サンプル動画**: https://www.minimax.io/blog/minimax-h3
- **種別**: official (MiniMax) ｜ 5–15s

```text
Two magicians stand onstage facing the audience and perform a “swap” illusion. They wave their wands simultaneously and smoke rises. When it clears, their suit colors have exchanged: the magician on the left now wears white, and the one on the right now wears black. Their glove colors do not change. They bow; the red curtain closes behind them and gradually shifts from deep red to dark blue.
```

- **適合度**: **ほぼそのまま使える**。`Their glove colors do not change.` の1文が
  T4（継続性ロック）の最小形。**変える対象と変えない対象を隣接して書く**公式の作法。

#### C13 — 公式 FL2VA・FPS ゲームプレイ（カメラ節を分けて書く）

- **モード**: first-last frame ｜ **カテゴリ**: ゲーム / UI
- **出典**: ecomimagelab `h3-0038` ／ https://www.minimax.io/blog/minimax-h3
- **サンプル動画**: https://www.minimax.io/blog/minimax-h3
- **種別**: official (MiniMax) ｜ 5–15s

```text
Camera: first-person, eye level, handheld gameplay. Simulate a player operating a modern-warfare FPS, holding an assault rifle and advancing slowly around the perimeter of a military base. Move forward along a road beside cover, sweep the reticle across the passage ahead, pause to fire several rounds at a distant target, then continue pushing forward like authentic player-controlled footage.

Lighting: cool natural light across a modern military base, mixed with smoke and firelight. Keep the image photoreal and crisp, with AAA-quality weapons, materials, dust, and battlefield haze.

Camera movement: subtle player-driven sway while moving; begin with a slow advance, make small checks left and right, add light recoil when firing, then continue forward steadily.
```

- **適合度**: 要変換。注意点として、**公式例なのに `Camera:` ラベルを使っている**。
  本アプリのガイドは `Camera:` フッターを明確に禁止しており、これは
  「rewrite 前のユーザー入力」の形。**取り込むなら変換後の姿を示すべき**で、
  「公式ブログの例は rewrite 前の生入力である」ことを注記しないと矛盾に見える。

#### C14 — 公式 FL2VA・ゲーム UI トランジション

- **モード**: first-last frame ｜ **カテゴリ**: ゲーム / UI
- **出典**: ecomimagelab `h3-0039` ／ https://www.minimax.io/blog/minimax-h3
- **サンプル動画**: https://www.minimax.io/blog/minimax-h3
- **種別**: official (MiniMax) ｜ 5–15s

```text
Interactive Otome Game

Use the first image as the exact opening frame and the second as the exact ending frame. Create a transition within a premium Chinese otome visual-novel interface, capturing an intimate backstage moment before and after a performance. Move naturally from “Choose to watch his performance” to “Han Xu reacts with intrigued interest after hearing the heroine.” Reveal UI copy, choices, and dialogue boxes with refined otome-game motion design. Keep transitions fluid and the romantic tension suggestive but restrained.
```

- **適合度**: 要変換。`Use the first image as the exact opening frame and the second as the exact ending frame.`
  は公式アライン行に置き換わる。UI テキストを引用符で書く作法が
  既存ガイドの「画面内テキストは英語ダブルクォート」と一致。

#### C15 — 公式 FL2VA・Web UI アニメーション（最小）

- **モード**: first-last frame ｜ **カテゴリ**: UI / モーションデザイン
- **出典**: ecomimagelab `h3-0033` ／ https://www.minimax.io/blog/minimax-h3
- **サンプル動画**: https://www.minimax.io/blog/minimax-h3
- **種別**: official (MiniMax) ｜ 5–15s

```text
Animate the website UI: the top headline slides down into place, the copy panel below slides up, and the car’s lights shift from dark to red.
```

- **適合度**: **ほぼそのまま使える**（`[Shot 1]` + 3フィールドで包むだけ）。
  「FL2VA は path だけ書く」の理想形。UI という非人物モチーフの例が既存 few-shot に無い。

---

### 4.3 Ref2VA — 参照生成（12本）

#### C16 — 公式 Ref2VA・4枚キーフレーム + マスク固定 + タイポグラフィ

- **モード**: r2v (omni-reference) ｜ **カテゴリ**: ブランドフィルム / 商品
- **出典**: ecomimagelab `h3-0017` ／ https://www.minimax.io/blog/minimax-h3
- **サンプル動画**: https://www.minimax.io/blog/minimax-h3
- **種別**: official (MiniMax) ｜ 5–15s

```text
Use Images 1–4 as sequential keyframes, seen through a vintage binocular viewfinder searching for the MINIMAX installation. Open out of focus with subtle handheld shake, then push in quickly and rack focus onto Image 1. Between keyframes, use fast binocular-scan transitions with whip movement, motion blur, optical smearing, and brief exposure flicker. Cut at peak blur, then settle and snap back into focus.

Keep the twin circular lens mask absolutely fixed throughout: identical position, scale, feathered black vignette, and edge softness, with no warping or drift. Only the image inside the mask may move.

In Image 2, let the fabric move gently in the wind while the MINIMAX lettering follows the folds and remains legible. In Image 3, the subject should feel like a stylish passerby caught by chance, walking, turning, and swinging their arms naturally. In Image 4, the subject adjusts their glasses or lifts their chin slightly with a cool, effortless fashion-campaign attitude.

Red typography should resolve with the focus: begin slightly blurred and at low opacity, then fade into clarity over 0.3–0.5 seconds. A subtle vertical slide or slight tracking expansion is allowed. Fade it out before the next transition or let motion blur carry it away. No spins, bounces, or large fly-ins/outs.

Visual language: a voyeuristic, Wes Anderson-inspired 35 mm film look with fine grain, soft highlight halation, restrained color, and red typographic accents. Minimal, premium, lightly playful. Do not add people, vehicles, buildings, or logos. Preserve the core composition and the MINIMAX installation exactly.
```

- **適合度**: 要変換だが**構造が6セクションにほぼ1対1で対応する最良の公式素材**。
  - `subject_definitions`: `<Picture 1>`–`<Picture 4>`（sequential keyframes = 具体フレームアンカーなので独立エントリが正当）
  - `summary`: `[keyframe completion]`
  - `retention_analysis`: 各 Picture に `fully_preserved`、マスクは `fully_preserved`
  - `detailed_description`: focus / whip / exposure flicker の物理記述
  - タイポグラフィのタイミング（0.3–0.5秒）は本文へ。
  **既存 few-shot の H3-E3 は Subject 1件のみで、複数キーフレーム型の例が無い**。

#### C17 — 公式 Ref2VA・画像 + 動画 + 音声のマルチモーダル最小例

- **モード**: r2v (omni-reference, multimodal) ｜ **カテゴリ**: ミュージックビデオ / 音声参照
- **出典**: ecomimagelab `h3-0005` ／ https://www.minimax.io/blog/minimax-h3
- **サンプル動画**: https://filecdn.minimax.chat/public/h3-en-v2-video-003-1785473642166.mp4
- **種別**: official (MiniMax)

```text
Reference the Hitchcock camera movement from Video 1, have the character in Image 2 sing, with the vocals matching Audio 3.
```

- **適合度**: 要変換。**3種の参照がそれぞれ別の職務を持つ最短例**。
  - `<Video 1>` = カメラワークのみ → `retention_analysis` は `weak_reference`（構造のみ）
  - `<Picture 2>` はキャラ定義だけなので独立エントリを作らず `<Subject 1>` 内で引用
  - `<Audio 3>` = 声の参照 → `reference`（信号は複製しない）
  - `summary` は `[reference generation + audio reference]`
  なお本アプリのタグ番号規則では、参照動画のサウンドトラックが低い `<Audio j>` を取るため
  番号が公式例と一致しない点に注意（M1 参照）。

#### C18 — 公式 Ref2VA・音声から声を借りて台詞を差し替える

- **モード**: r2v (omni-reference) ｜ **カテゴリ**: 対話 / 音声参照
- **出典**: ecomimagelab `h3-0045` ／ https://www.minimax.io/blog/minimax-h3
- **サンプル動画**: https://www.minimax.io/blog/minimax-h3
- **種別**: official (MiniMax) ｜ 5–15s

```text
The character says: “Follow the wind, live free. Leave worries behind, enjoy the moment.” Match the voice in Audio 1.
```

- **適合度**: 要変換。**「音色だけ参照して元台詞は持ち込まない」公式ルール（2.1-11）の実演**。
  `<Audio 1>: reference - timbre only, signal not copied.` と
  `<d>[English] Follow the wind, live free. …</d>` に分かれる。

#### C19 — 公式 Ref2VA・モーション転写（動画=動き / 画像=人物）

- **モード**: r2v (omni-reference) ｜ **カテゴリ**: パフォーマンス
- **出典**: ecomimagelab `h3-0043` ／ https://www.minimax.io/blog/minimax-h3
- **サンプル動画**: https://www.minimax.io/blog/minimax-h3
- **種別**: official (MiniMax) ｜ 5–15s

```text
Use Video 1 as the motion reference for a street-dance performance. Use Images 1 and 2 as the character references.
```

- **適合度**: 要変換。公式の分業原則
  「動画から取り出した人物・動作は `<Subject N>`、`<Video N>` は構造・素材の識別子」
  をそのまま説明できる最小例。`summary` は `[reference generation]`
  （動画が動きだけを供給するので `video editing` ではない ← 2.1-9）。

#### C20 — 公式 Ref2VA・絵コンテ画像 + 固定キャラクター

- **モード**: r2v (omni-reference) ｜ **カテゴリ**: ファンタジー / キャラクター
- **出典**: ecomimagelab `h3-0036` ／ https://www.minimax.io/blog/minimax-h3
- **サンプル動画**: https://www.minimax.io/blog/minimax-h3
- **種別**: official (MiniMax) ｜ 5–15s

```text
Use Image 2 as the locked character reference. Preserve the half-up long black hair, openwork silver crown, indigo ribbon, layered pale hanfu, translucent blue outer robe, deep-blue sash, silver floral fastener, and long tassels. Use Image 1 for storyboard order and pacing.

Render in high-quality 4K, 16:9 Chinese-inspired 3D with cinematic xianxia production value: intense, solemn, and shaped by destiny. Follow the storyboard beat by beat, with natural camera movement and seamless transitions—never a slideshow. Show the face only in close-up or extreme close-up. In wide shots, use back view, rear three-quarter view, or empty environment shots; never show a distant frontal face.
```

- **適合度**: 要変換。**`<Picture N>` が絵コンテとして働く公式ケース**
  （既存ガイドは「storyboard」に一言触れているが実例が無い）。
  `<Picture 1> is a storyboard reference for [Shot 1] … [Shot N], defining their viewpoint, subject placement, and shot order.` に落ちる。
  「never a slideshow」「遠景で正面顔を出さない」といった**失敗モードの先回り指示**も参考になる。

#### C21 — 15秒シームレス360°オービット + 台詞を跨がせる

- **モード**: r2v (omni-reference) ｜ **カテゴリ**: ファッション / トラベル
- **出典**: apimodels.app `Seamless Character 360 Orbit`
  ／ クリエイター @PromptSin https://x.com/PromptSin/status/2082534878139355561
- **サンプル動画**: https://video.twimg.com/amplify_video/2082534851224514560/vid/avc1/640x360/FiV1Dd-PSeWM2sf9.mp4?tag=14
- **種別**: community（promptSource: original）｜ 16:9

```text
🖼️ OMNI REFERENCE — [Ref_Image1]

Use [Ref_Image1] as the strict character identity, wardrobe and multi-angle reference. Preserve Freya’s exact face, blue eyes, long platinum-blonde hair with darker roots, skin tone, age and body proportions from [Ref_Image1]. Keep the same buttoned white linen shirt, cream linen shorts, bare feet and gold bracelet shown in [Ref_Image1]. Its six views represent one person from different angles; reconstruct one coherent 3D character without averaging or redesigning her face.

🏝️ SCENE

Freya stands near the waterline on a quiet Caribbean beach in warm late-afternoon sunlight: pale sand, transparent turquoise sea, distant palms and blue sky. A gentle breeze moves her hair and linen shirt while small waves reach the shore.

🎥 15-SECOND 360° ORBIT

One uninterrupted shot with no cuts. A stabilized camera moves clockwise around stationary Freya in one complete, physically correct 360° orbit.

0–2s: Start medium-wide in a frontal three-quarter view; she makes eye contact, smiles and breathes naturally as the orbit begins immediately.

2–5s: Pass her left profile with strong shoreline parallax; she begins speaking: “If paradise had a heartbeat…”

5–8s: Reach the full back view; she turns only her head slightly toward the moving camera and continues: “…I think it would sound…”

8–12s: Pass her right profile; she looks briefly at the horizon, returns her gaze to the lens and finishes: “…exactly like this.” She smiles naturally.

12–15s: Complete the orbit and return precisely to the opening frontal composition for a seamless loop.

📷 CAMERA

Constant clockwise direction, orbital radius, horizon and subject scale. Smooth gimbal motion, 35mm cinematic lens, full-body framing from head to bare feet, realistic foreground/background parallax. The camera travels around Freya; she does not spin and the background does not rotate artificially.

✨ LOOK

Premium photorealistic travel-fashion film, natural skin and linen texture, warm sunlight, soft shadows.
```

- **適合度**: 要変換（絵文字見出しを削り6セクションへ）。価値が高いのは2点。
  (1) **多視点シートを1人格に統合させる指示**
  （`Its six views represent one person from different angles; reconstruct one coherent 3D character without averaging`）
  → `subject_definitions` の書き方として既存ガイドに無い。
  (2) **1文の台詞を単一ショット内で3分割**して秒に割り当てている
  → 公式では単一ショットなので `<scenetrans>` は不要、
  ただし途中で切れないよう `[Shot 1]` 内に発話進行として書く必要がある。

#### C22 — 3×3 絵コンテ → 9ショットの一貫した15秒

- **モード**: r2v (omni-reference) ｜ **カテゴリ**: シネマティック / SF
- **出典**: imagineVid `iv-12` ／ BeatAPI `beat-hard-sci-fi-desert-3x3-grid-animation-sequence-845158`
  ／ クリエイター TechHalla https://x.com/techhalla/status/2082611421225845158
- **サンプル動画**: https://video.twimg.com/amplify_video/2082610596818685952/vid/avc1/1600x1800/6qd7aSJ7rEJWiEdF.mp4?tag=29
- **種別**: community（original）

```text
Epic desert sci-fi, harsh golden-hour light, volumetric sand haze, anamorphic lens flares, subtle handheld drift, heavy atmospheric particles, 35mm film texture.

Use the provided 3x3 grid as the only visual reference. Lock the exact same woman, including face, scars, green eyes, headscarf, desert armor, cape, and environments from every panel. Do not invent new outfits or faces.

[TIMELINE]
0-1.7s: Wide establishing shot of a lone figure walking across endless dunes toward twin massive suns.
1.7-3.4s: Extreme close-up of her scarred face, eyes fixed on camera as wind pulls at the headscarf.
3.4-5.1s: Tight insert of a gloved hand adjusting its grip on a rusted multi-tool weapon.
5.1-6.8s: Low wide silhouette of an industrial structure against the sun, with dust storms at its base.
6.8-8.5s: Medium tracking shot as she advances through the sandstorm.
8.5-10.2s: Hero wide on a rocky outcrop, cape flowing over the empty desert.
10.2-11.9s: Dynamic low tracking as she charges down a steep dune.
11.9-13.6s: Macro slow motion of backlit sand particles.
13.6-15s: Intimate cave close-up with controlled breathing and shafts of light.
```

- **適合度**: 要変換（`[TIMELINE]` → `[Shot N] At MM:SS.mmm`）。
  **9ショットという密度で本アプリの `duration` 上限に収める判断材料**になる。
  スタイル宣言が `[Shot 1]` の前に置かれている構造は Ref2VA の `detailed_description` と同形。

#### C23 — 音声トラックを編集タイムラインとして使う（アニメタイトル）

- **モード**: r2v (omni-reference) ｜ **カテゴリ**: アニメ / タイトルシーケンス
- **出典**: BeatAPI `beat-jazz-noir-anime-title-sequence-652641`
  ／ クリエイター @AIWarper https://x.com/AIWarper/status/2083045838377652641
- **サンプル動画**: https://media.beatapi.io/prompt-gallery/minimax-h3/jazz-noir-anime-title-sequence-652641/video-7e17bcde8d5c.webm
- **種別**: community ｜ 15s / 16:9

```text
@Arcane_Aii Using the attached reference image for the character and the attached audio track for timing, generate a jazz-noir anime title sequence in a stylized pop-art style. Every cut must land exactly on a beat of the audio — treat the track as the edit timeline.

VISUAL STYLE — apply throughout:
Render characters as flat, high-contrast black silhouettes against solid single-color backgrounds (saturated red, mustard yellow, deep blue). No naturalistic shading — each shot commits to one or two dominant colors only, like a screen-printed poster.

Compose shots like comic-book panels and pulp-magazine covers: bold panel divisions splitting the frame, hard geometric shapes, dramatic off-center cropping, extreme close-ups intercut with full-body poses.

Use freeze-frames as a core device: the character hits a dynamic pose (drawing a weapon, mid-stride, lighting a cigarette, turning toward camera) and the frame HOLDS completely still for one full beat before cutting. Do not smooth or interpolate through these holds — they must be dead stops.

Title typography in mid-century Saul Bass style: stark geometric letterforms, bold sans-serif, text that slides in as flat colored bars or snaps into frame on the beat.

Overlay the entire sequence with heavy analog film grain, slight gate weave, and worn-print color texture — it should look like a scratched 35mm print of a 1960s jazz album cover, not clean digital animation.

MOTION AND EDITING:
Alternate between two modes — (1) fast kinetic bursts of motion (whip pans across silhouettes, rapid cut sequences, a silhouette sprinting across a solid color field) and (2) hard freeze-frame holds. The contrast between the two IS the rhythm. Cuts are hard cuts only: no dissolves, no fades, no morphing between shots. Each new shot snaps in on the downbeat with a fresh background color.

End on the character in silhouette, frozen mid-pose against a solid red field, with the title card snapping in beside them on the final hit of the track.
```

- **適合度**: 要変換。**`<Audio N>` を「編集タイミングの参照」に使う用法**は
  公式ラベル定義の「Referencing beat, rhythm, or audio continuity」に該当するが、
  既存ガイドの例（声の音色参照）しか示していない。
  `retention_analysis` は `<Audio 1>: reference - beat grid only; cuts land on its downbeats.` になる。

#### C24 — 15秒タイムコード付き UI / ゲームシーケンス

- **モード**: r2v (omni-reference) ｜ **カテゴリ**: ゲーム / UI
- **出典**: ecomimagelab `h3-0019` ／ Bennett Heyn / fal
  https://fal.ai/learn/devs/minimax-h3-prompting-guide
- **サンプル動画**: https://fal.ai/learn/devs/minimax-h3-prompting-guide
- **種別**: community（fal 公式ガイド掲載）｜ 15s

```text
Use Image 1 for the character and Image 2 for the UI style.

[0–2 seconds] High-angle overhead shot. The character sits on a vivid, highly saturated purple floor, looks up at camera, and matches Image 1. A game menu appears on the right: START NEW GAME, CONTINUE (highlighted), SETTINGS, EXIT GAME. Player profile MINIMAX appears top left. The cursor selects CONTINUE.

[2–4 seconds] Smoothly push in to her right arm. A RIGHT ARM EQUIPMENT panel slides in from the right. PHANTOM GRIP is selected, then the selection moves to CHRONOS CLAW. Her mechanical hand reconfigures: fingers separate, new claw-like joints lock into place, and cyan LEDs flare brighter.

[4–7 seconds] Arc smoothly to her left. An ARMAMENT CUSTOMIZATION grid slides in, showing hand, forearm, elbow, and upper-arm components. The selector cycles rapidly. Her left arm disassembles section by section: the forearm plate releases, new armor slides in, the elbow joint swaps, and the hand reconfigures, with exposed wiring and pistons visible during the change.

[7–8.5 seconds] Pull back to a medium shot. CONFIRM CONFIG flashes; click it. All UI panels collapse inward and vanish. She uncrosses her legs and settles into a relaxed seated pose with one knee raised, lifting the prosthetic hand for a subtle post-configuration movement.

[8.5–10 seconds] A LOADING bar appears along the bottom and races from 0% to 100%. The saturated purple environment darkens as shadows creep inward and warm golden light begins to bleed through.

[10–15 seconds] As she stands, the full world loads around her: a dense cyberpunk slum with flickering neon, rain-wet streets, moving crowds, passing motorcycles, tangled overhead cables, and stacked buildings stretching toward futuristic towers. Settle into a third-person camera behind her. HUD elements fade in: minimap top right, health and ammo bottom left, then a mission marker. She steps into the street.
```

- **適合度**: 要変換（秒レンジ → `[Shot N] At MM:SS.mmm`。ただし本例は**カットではなく
  連続したカメラ移動**なので、公式流儀では単一ショット内の段階記述にすべき ← 2.1-2）。
  画面内 UI 文字列（`START NEW GAME` など）を**引用符に入れる**必要がある点も変換ポイント。
  既存 few-shot に UI / 文字表示の例が皆無なので価値が高い。

#### C25 — マテリアルマッチによるシームレストランジション

- **モード**: r2v (omni-reference) ｜ **カテゴリ**: VFX / トランジション
- **出典**: ecomimagelab `h3-0020` ／ Bennett Heyn / fal
  https://fal.ai/learn/devs/minimax-h3-prompting-guide
- **サンプル動画**: https://fal.ai/learn/devs/minimax-h3-prompting-guide
- **種別**: community（fal 公式ガイド掲載）

```text
@Image 1: Push in rapidly toward the milk foam, cocoa particles, and dark liquid texture on the coffee until particles, bubbles, and ripples fill the frame. Keep the macro photography realistic, with extremely shallow depth of field and fine powder drifting through backlight. Let the surface feel suspended between granular sand and fluid.

At the exact moment when the cocoa particles, foam contours, and coffee swirl closely resemble the dune ridges, wind-carved textures, and airborne sand in @Image 2, transition seamlessly into the desert landscape. Continue pushing forward until the full dunes from @Image 2 are revealed.

No tearing, black frames, hard cuts, obvious VFX, or compositing seams. Keep it photoreal, quiet, and restrained—as though one granular material naturally expands from the microscopic coffee surface into a vast desert. One continuous shot with no visible edit.
```

- **適合度**: **変換が容易で価値が高い**。
  `@Image 1` / `@Image 2` → `<Picture 1>` / `<Picture 2>`（両方とも構図アンカーなので独立エントリが正当）、
  `summary` は `[keyframe completion]`。
  **「カットせずに1ショットで繋ぐ」という公式ルール（2.1-2）の実演**でもある。

#### C26 — UGC・スマホ縦動画・3行の短い台詞

- **モード**: r2v (omni-reference) ｜ **カテゴリ**: UGC / SNS / 商品
- **出典**: BeatAPI `beat-pizza-night-ugc-domino-s-vlog-197798`
  ／ クリエイター @ShamiWeb3 https://x.com/ShamiWeb3/status/2082799917140197798
- **サンプル動画**: https://media.beatapi.io/prompt-gallery/minimax-h3/pizza-night-ugc-domino-s-vlog-197798/video-a0d77ef8aa24.webm
- **種別**: community ｜ 15s / 16:9

```text
VIDEO PROMPT — "Pizza Night Vlog" (UGC iPhone Style)

Duration: 15 seconds | Aspect Ratio: 16:9 | Style: Authentic UGC / iPhone selfie-vlog, handheld, natural light, slight motion blur, TikTok/Reels energy — NOT cinematic, NOT overly polished. Feels like a real creator filmed this on their phone.
Product Reference: Use the uploaded Domino's Pepperoni Pizza image as the only product reference. Keep crust thickness, cheese texture, bake color, pepperoni placement, and proportions identical in every cut — no redesigning the pizza.
Camera: iPhone 15 Pro front + back camera switching, handheld, natural wobble, autofocus hunting slightly (realistic), vertical-style framing cropped to 16:9, occasional finger near lens edge, natural room lighting + phone flash reflections on the pizza box.

Character Description

Name (for reference): Mia

Awoman in her mid-20s, naturally attractive and beautiful with an approachable, girl-next-door charm — not overly done up. Wavy sandy-blonde hair pulled back loosely, light natural makeup, wearing a cozy oversized cream sweater. Warm, genuine smile, expressive eyes, casual energetic personality like a real lifestyle vlogger. Sits in a softly lit modern kitchen/living room.

Shot Breakdown
SHOT 1 (0–2s) — The Grab

Selfie-angle, she's mid-laugh holding up the Domino's box to camera. Quick jump cut. Dialogue: "Okay so it's officially pizza night—"

SHOT 2 (2–4s) — The Open
Cut to overhead handheld shot, box flips open, steam rising off the pizza, slight camera shake as she leans in.

SHOT 3 (4–6s) — The Zoom
Quick zoom-punch into the pizza, phone camera autofocus adjusts naturally, cheese and pepperoni in focus, ambient kitchen sounds.

SHOT 4 (6–8s) — The Pull
Cut to her hands lifting a slice, natural cheese pull, filmed from a slightly low candid angle like a friend filming across the table.

SHOT 5 (8–10s) — The Reaction
Cut back to selfie-cam, she takes a bite, eyes widen, quick genuine reaction. Dialogue: "Oh my god, that's so good."

SHOT 6 (10–12s) — The Candid Cutaway
Jump cut to a close, slightly shaky shot of the pizza box on the counter, her hand grabbing another slice off-frame, casual b-roll energy.

SHOT 7 (12–14s) — The Wrap-Up
Back to selfie angle, she grins at camera, holding slice up like a toast. Dialogue: "Dominos, y'all know what to do."

SHOT 8 (14–15s) — End Tag
Quick freeze/cut to the box logo close-up, natural handheld wobble, soft text overlay in casual font: "pizza night = solved 🍕" — cut to black.

Look & Feel
Warm indoor lighting, slightly grainy natural phone sensor look, imperfect framing, real reactions, minimal dialogue (3 short lines total), authentic pacing with hard jump cuts instead of smooth transitions.

Negative Prompt

cinematic grade, overly smooth camera moves, studio lighting, professional voiceover, staged acting, CGI look, plastic cheese, distorted pepperoni, extra fingers, warped hands, text glitches, logo distortion, overly polished commercial feel.
```

- **適合度**: 要変換。8ショット/15秒は本アプリの推奨（1ショット1.5秒以上）ぎりぎり。
  `Negative Prompt` セクションは H3 に存在しないので**本文末尾の1文へ畳む**。
  `Duration` / `Aspect Ratio` は job フィールドへ。
  **既存 few-shot に「非シネマティック（意図的に雑なスマホ質感）」の例が無い**ので、
  スタイル指定の幅を示す意味で価値がある。

#### C27 — 5ビート・レンズ画角ロック・物理・音声を全部書き切る

- **モード**: r2v (omni-reference) ｜ **カテゴリ**: アクション / スポーツ
- **出典**: BeatAPI `beat-concrete-plaza-kickflip-drop-082714`
  ／ クリエイター @eijo_AIart https://x.com/eijo_AIart/status/2082684613475082714
- **サンプル動画**: https://media.beatapi.io/prompt-gallery/minimax-h3/concrete-plaza-kickflip-drop-082714/video-d5ce5bc1daa2.webm
- **種別**: community ｜ 10s / 16:9

```text
プロンプト

SCENE CONTEXT Late afternoon, empty two-level concrete plaza. A young woman skateboarder rolls along the raised upper deck to its edge and launches off the TOP of a 10-step stair set with a kickflip, clearing the whole drop in one jump and landing on the lower plaza below. She travels downward from takeoff to landing.  ACTIVE REFERENCES @image1 — young woman skateboarder, long straight black hair with one bright cobalt-blue streak falling on the left side of her face, blunt fringe across the eyebrows, thin black winged eyeliner, freckles over both cheeks. 100% matches the reference.  LOCATION MAP Two levels. She rides the raised UPPER deck: pale polished concrete with black urethane scuff arcs. The upper deck ends at a hard edge, and that edge is the top step of a 10-step stair set descending away from her, chipped steel nosing, matte black handrail on the left. The LOWER plaza sits one human height below, flat open concrete, and is the landing. Beyond it: shuttered glass frontage receding into depth, haze 15% at 30 meters. Low sun at the far left, 15 degrees up, shadows raking left to right. Sunlit concrete warm bone-grey, stair treads cool blue-grey in shadow; the cobalt streak and the handrail carry the only saturated notes.  FIRST FRAME / BLOCKING Frame one is already moving: she is mid-push on the upper deck, right foot swinging back to the tail, board rolling camera-left to camera-right, camera beside her. Past her the deck edge cuts across frame and the lower plaza opens below it, so the ground visibly drops away in the direction she is heading.  FORMAT MODE Timed multishot, five beats. Cuts land only at 2.4s, 3.8s, 7.0s and 9.0s and the camera does not cut on its own.  OPTICS CUT 1 — LENS LOCK 180 degrees fisheye, barrel distortion, WS at wheel height. CUT 2 — LENS LOCK 84 degrees wide rectilinear, WS angled down over the edge. CUT 3 — LENS LOCK 47 degrees neutral, WS full profile of the descent. CUT 4 — LENS LOCK 63 degrees observational, MS to WS low on the landing. CUT 5 — LENS LOCK 18 degrees portrait compression, MCU. LENS CHECK at each cut: FOV as stated, no drift mid-segment.  CAMERA Operator on her right flank, the shadow side, half a beat behind her. CUT 1 — upper deck, glides at 22 km/h level with the trucks, 20 cm ride height, 1 to 2 cm handheld tremor. CUT 2 — upper deck behind her right shoulder, chest height, tilted DOWN 25 degrees so the stair descent and the landing fill the bottom half of frame. CUT 3 — right of the stairs at top-step height, tracking the descent at 3 km/h lateral, focus on the board. CUT 4 — lower plaza, 30 cm off the ground, tilted UP 20 degrees at the stairs above, rising 40 cm as she rolls out. CUT 5 — eye level, slow push from chest to face at 2 km/h.  ACTION 0.0s to 2.4s — CUT 1. On the upper deck she pushes twice, back foot slapping the deck and returning to the tail, rolling at 22 km/h toward the edge. Wheels rumble over the seams. The lower plaza is visible dropping away ahead of her. 2.4s HARD CUT 2.4s to 3.8s — CUT 2. From behind her, the 10-step drop and the landing lie in the lower half of frame. She sinks into a deep crouch, eyes down on the landing, slides her front foot to the bolts and snaps the tail down. The board pops and she leaves the top edge into open air above the steps, the ground now far beneath her feet. 3.8s HARD CUT 3.8s to 7.0s — CUT 3. Full profile of the descent, slow motion at 40 percent speed. She enters at the upper left of frame at the top step and travels down and to the right on a falling arc, the 10 steps passing beneath her, the landing waiting in the bottom right. Her front foot flicks off the nose edge and the board rotates one complete 360-degree roll along its long axis as she falls, knees tucked, arms wide. The griptape comes back under her feet and she catches it over the bolts, wheels aimed down. 7.0s HARD CUT 7.0s to 9.0s — CUT 4. Real time. She drops in from the top of frame, out of the air, and all four wheels bite the lower plaza at once with a hard slap. Her knees compress deep and absorb the drop, and she rolls away camera-right at 20 km/h, the stair set standing above and behind her. 9.0s HARD CUT 9.0s to 10.0s — CUT 5. Real time. Rolling out, she turns her head back over her left shoulder and looks UP at the stairs she came off, holds one beat, faces forward.  PERFORMANCE Pore-level skin realism, living eyes with a hard catch-light from the low sun. On the run-up her jaw is set, eyes down on the landing far below. On landing the impact travels through her shoulders, she blinks once, then exhales and her mouth lifts barely at one corner.  PHYSICS Real board mass: the deck flexes and rebounds on landing, wheels squash against the concrete. From the pop onward she only loses altitude, falling on a clean gravity parabola while forward inertia carries her out over the steps, touching down one human height lower and several meters further out than her takeoff point, gaining speed as she falls. The kickflip rotates at a constant rate. Contact shadows stay welded to the wheels on both levels and detach in the air, hair and fabric lagging above her as she drops.  LIGHTING Low sun as key from the far left at 15 degrees elevation, raking across the upper deck and rimming her left edge, the cobalt streak glowing where light passes through it. The stair treads sit in their own soft shadow while the lower plaza stays open and sunlit, so the two levels read as clearly separated heights. White balance 5600K, fixed across all beats.  WARDROBE Oversized faded black cotton tee flapping upward as she falls, baggy light-wash denim jeans with frayed hems over the heels, black canvas low-top skate shoes with the suede toe worn through from griptape.  AUDIO Lofi hiphop bed at 72 BPM throughout: dusty vinyl crackle, soft boom-bap kick and brushed snare, mellow jazz piano loop, warm bass. On top: wheels rumbling over seams, the crack of the tail on the top step, the board fluttering as it flips, a quiet beat of air as she falls, a hard slap as the wheels hit the lower plaza.  STYLE Photoreal live-action skate promo video, professional street skating footage, fine film grain, real motion blur on the wheels and the flipping board.  OUTPUT SETTINGS 8K, anamorphic 2.39:1, 24 fps. CUTS 1, 2, 4 and 5 real time. CUT 3 slow motion at 40 percent, constant for the whole beat.  POSITIVE LOCKS She starts on the upper deck and finishes on the lower plaza: takeoff at the top of the stairs, landing at the bottom, one human height below. Her motion is downward through the whole flight, entering each airborne frame high and leaving it low. The stair set stays exactly 10 steps and descends away from her direction of travel. The board completes one full 360-degree kickflip rotation and she lands with both feet over the bolts, rolling away upright past the bottom step. Screen direction stays camera-left to camera-right. Same rider, same face, same cobalt-blue streak, same wardrobe and same board in all five beats.
```

- **適合度**: 要変換（ブロック → `detailed_description` の散文へ、
  `AUDIO` の BGM は `non_diegetic_music`、フォーリーは同期音として本文＋`overall_soundscape`）。
  **カット時刻がすべて明示され（2.4s / 3.8s / 7.0s / 9.0s）、
  `[Shot N] At MM:SS.mmm` へほぼ機械的に変換できる唯一級のコミュニティ例**。
  `POSITIVE LOCKS`（＝ネガティブではなく肯定形の制約）という発想も
  「H3 にネガティブプロンプトが無い」本アプリの制約と相性がよい。

---

### 4.4 Ref2VA — 生成編集（8本）

**共通の適合度メモ**: いずれも公式の `[video editing]` タスク。
本アプリの Ref2VA ガイドは task type に `video editing` を列挙しているが、
**編集系の実例が `FEW_SHOT_H3` に1本も無い**。これらは短いので、
6セクションへの変換例を1〜2本作れば残りは同じ型で書ける。
共通の変換ポイント:
`subject_definitions` に `<Video 1> is the source video for the target video edit.` を置き、
`summary` を `[video editing] The target video is an edited version of <Video 1>.` で始め（2.1-8）、
`retention_analysis` で**変えない部分を `fully_preserved`、変える部分を `partially_preserved`**
として明示する。元音声が残るなら `+ audio reuse` を併記する。

#### C28 — 最小の局所編集

- **出典**: ecomimagelab `h3-0021` ／ Bennett Heyn / fal
  https://fal.ai/learn/devs/minimax-h3-prompting-guide ｜ **種別**: community（fal ガイド掲載）

```text
Replace the cat in the video with a dog.
```

- **適合度**: 要変換。**「1オブジェクトの修正は全リロールせず編集指示で」**の最短例。

#### C29 — リライティング（公式）

- **出典**: ecomimagelab `h3-0049` ／ https://www.minimax.io/blog/minimax-h3 ｜ **種別**: official (MiniMax)
- **サンプル動画**: https://www.minimax.io/blog/minimax-h3

```text
Relighting

Change the lighting in the reference video from daytime to night.
```

- **適合度**: 要変換。`retention_analysis` で「被写体・カメラ・タイミングは `fully_preserved`、
  照明のみ `partially_preserved`」と書き分ける最小例。

#### C30 — 多要素の同時編集（公式）

- **出典**: ecomimagelab `h3-0052` ／ https://www.minimax.io/blog/minimax-h3 ｜ **種別**: official (MiniMax)
- **サンプル動画**: https://www.minimax.io/blog/minimax-h3

```text
In the reference video: replace the newspaper with a green hardcover book; replace the chair with a red sofa; remove the subject’s sunglasses and reveal a clear face; remove the burning-car effect and restore the vehicle to normal; replace the photograph taken from the coat with a small black notebook; and add a tree on the left side of frame.
```

- **適合度**: 要変換。セミコロン区切りで6個の編集を並べる公式の書きぶり。
  `retention_analysis` は編集対象ごとに1行が必要になるため、行数が増える型の見本。

#### C31 — 商品・看板・台詞の同時差し替え（公式）

- **出典**: ecomimagelab `h3-0053` ／ https://www.minimax.io/blog/minimax-h3 ｜ **種別**: official (MiniMax)
- **サンプル動画**: https://www.minimax.io/blog/minimax-h3

```text
In the reference video, replace the canned drink shown at the beginning with Coca-Cola. Change the illuminated “FamilyMart” convenience-store sign in the background to “HUHUI.” At the end, replace every snack in the plastic bag with cans of Coca-Cola, and change the final line from “I bought a few snacks” to “I bought a whole bunch of Coke.”
```

- **適合度**: 要変換。**画面内テキストの差し替えと台詞の差し替えを同時に扱う唯一の公式例**。
  変換後は看板文字が `"HUHUI"`（引用符保持）、新しい台詞が `<d>[Chinese] …</d>` として `(S1)` に付く。
  `summary` は `[video editing + audio reuse]` 相当（元音声の一部が残るため）。

#### C32 — 音声から台詞を持ってきて演技を合わせる（公式）

- **出典**: ecomimagelab `h3-0051` ／ https://www.minimax.io/blog/minimax-h3 ｜ **種別**: official (MiniMax)
- **サンプル動画**: https://www.minimax.io/blog/minimax-h3

```text
In Video 1, replace the woman’s line—“There’s no way we can be together. It’s not that I don’t love you; we simply can’t make it to the end.”—with the line from Audio 1: “Please don’t go. This time, let’s not let each other go.” Adjust the performance subtly to match the new dialogue.
```

- **適合度**: 要変換。**`<Video 1>` と `<Audio 1>` が同時に働く編集タスク**。
  `summary` は `[video editing + audio reference]`。
  公式の「参照音声の台詞を直接再利用するときは原文を `<d>` に verbatim 保持」（2.1-11）の実演。

#### C33 — 被写体と衣装の精密置換（公式）

- **出典**: ecomimagelab `h3-0047` ／ https://www.minimax.io/blog/minimax-h3 ｜ **種別**: official (MiniMax)
- **サンプル動画**: https://www.minimax.io/blog/minimax-h3

```text
Precise Subject and Wardrobe Replacement

Replace the child at the back of Video 1 with the golden retriever from Image 1. Replace the khaki jacket worn by the child on the far left with the denim jacket from Image 2.
```

- **適合度**: 要変換。**位置語（at the back / on the far left）で対象を特定する**書き方が要点。
  `retention_analysis` では置換対象が `attribute_transfer`（画像の特性を別の対象へ移す）になりうる、
  マーカー選択の判断材料になる例。

#### C34 — 手描きグラフィック効果の重畳（公式）

- **出典**: ecomimagelab `h3-0055` ／ https://www.minimax.io/blog/minimax-h3 ｜ **種別**: official (MiniMax)
- **サンプル動画**: https://www.minimax.io/blog/minimax-h3

```text
Creative interpretation + animated graphic effects

Add orange-yellow hand-drawn marks like Image 1 around the two people in Video 1. As they move closer, the marks multiply and build from tiny sparks into bright radiance. When they kiss, introduce pink brushstrokes.
```

- **適合度**: 要変換。効果の**進行（増殖 → 収束 → 色の切り替え）を出来事に同期させる**書き方。
  `<Picture 1>` はスタイル定義なので独立エントリを作らず `<Subject N>`（エフェクト様式）内で引用するのが正しい。

#### C35 — 動きの軌跡を完全保持して被写体だけ入れ替える（公式）

- **出典**: ecomimagelab `h3-0044` ／ https://www.minimax.io/blog/minimax-h3 ｜ **種別**: official (MiniMax)
- **サンプル動画**: https://www.minimax.io/blog/minimax-h3

```text
Motion reference for a DIY reaction clip

Match the action in Video 1 from a locked-off wide camera. Replace the three suited men with three highly photoreal capybaras. Preserve the original movement path exactly: all three drop quickly to the floor; the left capybara jumps to center; the center capybara rolls to the far left; the new center capybara rolls to the far right; the right capybara jumps to center; finally, the center capybara jumps onto the other two, forming a pyramid. Keep the camera fixed and integrate fur, lighting, and shadows realistically into the scene.
```

- **適合度**: 要変換。**動きの経路を言葉で完全に書き下す**ことで転写精度を上げる公式の作法。
  `<Video 1>` は動きの構造参照なので `retention_analysis` は
  `<Video 1> (movement path and framing): fully_preserved - …`。
  被写体は入れ替わるので `[reference generation]` 寄り（元動画を編集しているとも読めるので
  `[video editing + reference generation]` が実態に近い ← task type 判断の練習素材）。

---

## 5. 取り込み提案

差分はまだ書かない。以下は箇条書きの提案。

### 5.1 `MINIMAX_H3_GUIDE_BODY`（共通規約）へ足すもの

優先度高（公式一次資料の欠落を埋める。いずれも1〜2行）:

1. カット動詞の全リスト（`cuts to` / `shot cuts to` / `transitions to` / `changes to` / `switches to`）と、
   dissolve / fade / wipe は**ユーザー明示要求時のみ**の条件（2.1-1、M5）。
2. **カットしない条件**: 「距離や角度がわずかに変わるだけならカットせずカメラモーションで書く。
   カットは被写体・空間・状態・視点・時間のいずれかに新情報を持ち込むときだけ」（2.1-2）。
3. amplitude / speed は「意味があるときだけ」書く（中程度・通常速度は省略）（2.1-3）。
4. スタイル語彙の候補リストと、キーフレームタスクでは参照画像からスタイルを導出する規則（2.1-4）。
5. 話者初出時に確立すべき属性（種別・年齢・性別・画面内外・ピッチ・音色・話速・訛り）（2.1-5）。
6. 台詞継続表現の定型4種（`continues seamlessly across the cut` ほか）（2.1-6）。
7. 参照音声の台詞書き起こし規約: `[unclear]`、句読点の正規化、音色のみ参照なら元台詞を持ち込まない（2.1-11）。

優先度中（本アプリ固有の運用値。実装値の確認が必要）:

8. **プロンプト長の上限 7,000 文字**（2.1-14）。ガイド自身が長いので、
   出力側の目安として書く価値がある。
9. **音声リファレンスは単独では送れない**（画像か動画が必ず必要）（2.1-15）。
   → 実装側（`studio` の参照バリデーション）と整合しているか要確認。ズレていればコード側の課題。
10. 参照アセットの尺（クリップ各 2–15 秒 / 音声累計 15 秒）（2.1-16）。
    → 同上。二次資料由来なので、採用するなら「経験則」と明記するか、実測で確認する。
11. **768px 短辺はローカル実行の運用値であり、モデル上限（2K）ではない**という注記（M2）。
    現状の書き方だとエージェントが「H3 は 768 までのモデル」と誤解しうる。
12. マルチショット t2v / 連続カットにおける**継続性ロックの1文**
    （wardrobe / prop count / screen direction / light direction を全ショットで保つ）（T4、T8）。
13. **`No text, subtitles, logos or watermarks.` の例外**:
    タイポグラフィ演出や UI 表示が主題のときは付けない、を明文化（C10 / C14 / C24 が該当）。

### 5.2 `MINIMAX_H3_REFERENCE_VIDEO_GUIDE`（Ref2VA）へ足すもの

14. `retention_analysis` の **Picture 行 / Video 行の書式**（括弧の中身が Subject と異なる）（2.1-7）。
15. video editing タスクの `summary` 定型導入文（2.1-8）。
16. task type 選択の判断規則4項目（2.1-9）。
17. `(Sx)` の割り当て規則と `retention_analysis` での `(Sx)` 禁止、
    BGM 内ボーカルは `<Audio N>` を音源にする（2.1-10）。
18. 参照音声の copy/reference 関係を書く場所（環境音は `overall_soundscape`、
    観客のみの楽曲は `non_diegetic_music`）（2.1-13）。
19. `detailed_description` 語数の但し書き3項目（台詞優先・編集タスクは別・単一ショットでも短くしない）（2.1-12）。
20. **転写しない要素の明示を許可**（`Do not copy the references' logos, faces, subtitles, watermark, or music.`）（T3）。
21. M1（アプリ独自のタグ番号規則）について「なぜ公式と異なるか」の1文
    （ComfyUI グラフが参照動画のサウンドトラックを常に渡すため）。

### 5.3 `H3_QUALITY_BAR` へ足すもの

22. **1ショット1カメラムーブ**（T11）。
23. **ビート密度の目安**: 12秒で3–4カットまで、1ショット1.5秒以上（T2）。
24. **JSON のショット配列で書かない**（C08 を根拠に、talkvid / `Camera:` フッター禁止と並べる）。
25. メタ品質語（`4K` / `8K` / `masterpiece` / `viral`）は書かない（効果が保証されない）（2.1-19）。
26. **スタイルは1つに絞る**。意図的な混成（C02 の実写×手描き）だけが例外で、
    その場合は混ぜ方を明示する（2.1-20）。
27. 具体的な失敗モードを末尾1文で禁止してよい（ただしポジティブ記述が先）（T5）。

### 5.4 `FEW_SHOT_H3` へ足すもの ＋ コンテキストサイズ対策

現状 `FEW_SHOT_H3` は H3-E1 / E2 / E3 の3件で、モードは I2VA / T2VA / Ref2VA を1本ずつ。
**FL2VA・L2VA・生成編集・複数話者・多言語・非実写スタイル・UI/文字表示の例が無い。**

提案:

28. **第4章の候補をそのまま貼らない。** 候補はほぼ全てが「rewrite 前の生入力」であり、
    公式形式の完成例ではない。**変換後の完成形を新規に書き起こして追加する**のが正しい。
    第4章は「何を書くべきかの内容ソース」として使う。
29. 追加する完成例の推奨セット（各1本、既存 E1–E3 と合わせて 9〜10 本）:
    - `H3-E4` **FL2VA**（C15 の UI か C12 の衣装スワップを変換 — 短くて path が明快）
    - `H3-E5` **L2VA**（現在ゼロ。公式 base-en Case 4 のグラス破損例を本アプリ書式に落とす）
    - `H3-E6` **Ref2VA / 生成編集**（C31 の商品・看板・台詞差し替え — 6セクション全部が埋まる）
    - `H3-E7` **Ref2VA / マルチ参照**（C17 の画像+動画+音声 — アプリのタグ番号規則の実演を兼ねる）
    - `H3-E8` **T2VA / 複数話者・多言語**（C03 を変換 — `<d>[Korean]` + `(S1)`/`(S2)` + `<scenetrans>`）
    - `H3-E9` **T2VA / 非実写スタイル**（C11 のクレイアニメ）
    - `H3-E10` **UI・画面内文字**（C24 か C15 — 引用符付き文字列と `No text…` の例外を示す）
30. **コンテキストサイズ対策**: 全部を常時埋め込むと 1 ジョブのシステムプロンプトが肥大する。
    既に `drafting_guide.py` の `_few_shot(*ids)` が
    「`FEW_SHOT_H3` から見出し id で切り出す」仕組みを持っているので、**同じ機構を
    `prompts.py:2094` の組み立て（`parts += [guide, H3_QUALITY_BAR, FEW_SHOT_H3]`）にも適用する**:
    - `ctx.mode`（`t2v` / `i2v` / `r2v`）で必須例を絞る
      （例: t2v なら E2 / E8 / E9、i2v なら E1 / E4 / E5、r2v なら E3 / E6 / E7）。
    - 追加で「カテゴリヒント」（対話が多い / UI・文字が主題 / 生成編集 / 非実写）を
      脚本の内容から1つだけ判定し、対応する1本を足す。
    - **常時埋め込みは 2〜3 本まで**、上限を超えたら見出しだけ列挙して本文を落とす。
31. 例のメタデータ形式を統一する（現状は見出しのみ）。
    `## H3-EN <モード> — <一言> [tags: dialogue, ui, editing, …]` のように
    タグを見出し行に持たせると、30 の選択ロジックが文字列マッチだけで書ける
    （`_few_shot` と同じく本文をコピーせず切り出せる）。

### 5.5 実装に関わる確認事項（ガイド以外）

32. **音声リファレンス単独送信の可否**（2.1-15）が `studio` 側のバリデーションと一致しているか。
    ガイドに書く前にコード側の実挙動を確認すること。
33. **参照アセットの尺制限**（2.1-16）を実装が持っているか。二次資料由来なので、
    ガイドに断定形で書く前に ComfyUI ノードの受け入れ範囲を確認するのが安全。
34. 第1章の素材ディレクトリはセッション限りの一時領域にある。
    第4章の候補を今後も参照するなら、`docs/` 配下か別リポジトリへ退避する。

---

## 付録: 調査素材の再参照方法

`github-prompts.md` は 42,157 行あるため全文 Read は非現実的。以下で選択的に読む。

```bash
BASE=/mnt/0AB07FFEB07FEF17/tmp/claude-1000/-mnt-0AB07FFEB07FEF17-workspace-video-studio/bcde5f4d-517a-4ff7-b26a-bc5dd2e42ef4/scratchpad/h3-research

# 見出し一覧（カテゴリ境界）
grep -n '^### \|^#### P' "$BASE/github-prompts.md" | grep '### '

# 1件だけ取り出す
awk '/^#### P253\./{f=1} f&&/^#### P254\./{exit} f' "$BASE/github-prompts.md"

# 構造化データから絞り込む
python3 -c "
import json
d=json.load(open('$BASE/all_prompts.json'))
for x in d:
    if x['source_type'].startswith('official'):
        print(x['id'], x['mode'], x['title'])
"
```

`cards.json`（apimodels）は `promptSource` が `original` / `reconstructed` を持つので、
再構築プロンプトを除外する場合は `x['promptSource'] == 'original'` で絞ること。

**カテゴリ別の P 番号レンジ**（`github-prompts.md`）:

| カテゴリ | レンジ |
|---|---|
| 商品・コマーシャル | P001–P092 |
| ミュージックビデオ・パフォーマンス | P093–P169 |
| アニメ・アニメーション | P170–P225 |
| アクション・ファンタジー | P226–P255 |
| ゲーム・UI・モーションデザイン | P256–P275 |
| ファッション | P276–P296 |
| ホラー | P297–P303 |
| コメディ | P304–P308 |
| Vlog・SNS・UGC | P309–P315 |
| VFX・トランジション | P316–P327 |
| キャラクター・対話・音声 | P328–P344 |
| ドキュメンタリー・自然・旅行 | P345–P368 |
| カメラ・モーション実験 | P369–P372 |
| リファレンス・編集・再生成 | P373–P391 |
| シネマティック・ストーリー | P392–P450 |
| その他 | P451–P456 |
