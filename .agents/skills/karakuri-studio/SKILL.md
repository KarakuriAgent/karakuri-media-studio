---
name: karakuri-studio
description: Karakuri Media Studio（動画・画像・音声生成スタジオ）を外部 API で操作して映像制作する。プロジェクト/脚本の作成、素材画像生成、カットのレンダリング、Take 採否、タイムライン編集、動画書き出し、Remotion MV まで。
---

# Karakuri Media Studio を外部 API で動かす

このアプリは「作品（プロジェクト）→ 話 → 場 → カット（Shot）」で脚本を持ち、
カットを 1 回焼くごとに **Take** が 1 本できる。Take を採用（select）したものが
そのカットの完成尺で、それをタイムラインに並べて mp4 に書き出す。素材（キャラ・
場所・小道具＝ World Bible の asset）は `@名前` で本文から参照する。

**エンドポイントの正本は OpenAPI、脚本とプロンプトの書き方の正本は prompt-guide。**
このファイルはそこに書いていない段取りと落とし穴だけを書く。

## 1. 接続

- BASE: 環境変数 `KARAKURI_STUDIO_URL`。無ければリポジトリ直下 `.env` の
  `HOST` / `PORT` から `http://HOST:PORT`（既定 `127.0.0.1:8000`。`HOST=0.0.0.0`
  は待受の意味なので宛先は `127.0.0.1` に読み替える）。
- キー: 環境変数 `KARAKURI_STUDIO_API_KEY`。無ければ `runtime/config.json` の
  `external_api_key`。`X-API-Key` ヘッダで送る。
- **キーの値をログ・返答・コミットに貼らない。**
- 応答の読み方: **404 = キーが未設定**（外部 API 自体が無効。アプリの設定画面で
  発行してもらう）/ **401 = キー不一致** / 429 = 未完了ジョブか書き出しが上限
  （完了を待つ）/ 409 = `base_revision` が古い。
- 接続できない＝アプリが起動していない。リポジトリ直下で `./run.sh`（開発時は
  `./run.sh --dev`）を人に実行してもらう。

同梱のラッパーが上の解決を全部やる:

```bash
scripts/studio.sh GET /projects
scripts/studio.sh POST /projects '{"name":"新作","auto_translate":true}'
scripts/studio.sh PATCH /shots/<id> '{"prompt":"…","base_revision":12}'
scripts/studio.sh upload /library/audio file=@/path/to/ban.wav name=BAN  # multipart
scripts/studio.sh wait-job <job_id> [interval_sec]     # 完了まで待つ（既定 10 秒）
scripts/studio.sh wait-export <export_id> [interval_sec]
```

## 2. 最初に読むもの（毎セッション）

| 取得 | 何の正本か |
|---|---|
| `GET /api/v1/openapi.json` | 全エンドポイントとリクエスト/レスポンス schema |
| `GET /api/v1/prompt-guide` | 脚本・プロンプトの書き方。`guide_version` が同じならキャッシュを使い回してよい |
| `GET /api/v1/capabilities` | この接続先でラテント連続性 / ラテントアップスケールが使えるか |
| `GET /api/v1/options` | `aspect_ratio` の正しい表記、ワークフロー一覧と制約、LoRA、ライブラリ |

補助: `GET /api/v1/prompt-examples`（MiniMax H3 の実例。`mode` / `category` /
`id` で絞ると本文まで返る）。

エンドポイントを推測で叩かない。OpenAPI に無いものは無い。

## 3. 制作フロー

1. **既存を確かめる**: `GET /projects` → `GET /projects/{id}`。すでにある作品を
   作り直さない。応答は**すべてトップレベルの配列**で、`assets` / `episodes` /
   `scenes` / `shots` / `takes` と `revision_seq` が並ぶ。
   - **`takes` はトップレベルの 1 本の配列**。`shots[].takes` は無い。Shot 側に
     あるのは `selected_take_id` だけなので、カットの Take を見たいときは
     `takes` を `shot_id` で自分で束ねる（`scenes` も同じく `episode_id` で束ねる）。
   - **タイムラインは入らない**。編集面は別で `GET /projects/{id}/timelines`。
2. **作品を作る**: `POST /projects`。`synopsis` / `world_notes` を書く。
3. **素材を用意する**（見た目を固定したいものは必ずファイル実体を持たせる）
   - **`kind` と `category` は別物**。`kind` は**メディア種別**で
     `image` / `video` / `audio` の 3 つだけ。キャラ・場所・小道具の分類は
     `category` で `character` / `environment` / `prop` / `style` / `reference`。
     `kind:"character"` のように混ぜると **422**。
   - 生成して登録: `POST /jobs {"mode":"image_only", …}` → `wait-job` →
     `POST /projects/{id}/assets/from-job {"job_id":…,"name":"アリス",
     "category":"character","source":"image"}`
     （`from-job` の `kind` と `path` は `source` が選んだ出力から決まるので、
     書いても無視される）。
   - 手元のファイル: `POST /projects/{id}/assets` に multipart（`file=@…`）。
     JSON の `path` でも登録できるが、そこに書くのは**アプリのプロセスから
     見えるパス**（Docker で動いているなら**コンテナの中のパス**）で、手元の
     マシンの絶対パスではない。**Docker 運用では multipart を使うこと。**
     JSON なら例えば `{"name":"アリスの部屋","kind":"image",
     "category":"environment","path":"/app/assets/image/room.png"}`。
     既定は `kind:"image"` / `category:"reference"`。
   - メタデータだけの素材は `prompt_caption`（英語）を必ず書く。書かないと本文の
     `@名前` は何も足さない。
4. **脚本**: `POST /projects/{id}/episodes` → `.../episodes/{id}/scenes` →
   `POST /projects/{id}/shots`。話 1 本を丸ごと入れるなら **`POST /stories`**
   （話→場→カットを 1 トランザクションで作る。途中で落ちたら全部ロールバック）。
5. **焼く前に必ず `GET /shots/{id}/prompt-preview`**。実際に投入される本文・
   ワークフロー・参照素材が出る。見るところ:
   - `error` … 組み立てられない（直してから焼く）
   - `render_blocker` … 組み立てはできるが投入できない（引き継ぎ元の Take がまだ無い等）
   - `workflow_reason` … どのモード・品質になったか、フォールバックしたか
   - `will_translate` / `english_stale` … 英訳がこれから走るか
6. **焼く**: `POST /shots/{id}/render`（ボディで解像度・尺・steps・seed を上書き可）。
   返る Take の `job_id` を `GET /jobs/{id}` で **5〜15 秒間隔**でポーリング
   （`scripts/studio.sh wait-job <job_id>`）。status は
   `queued` / `prompting` / `running` / `done` / `failed` / `canceled`。
7. **検分**: 完了したジョブ / Take の `video_url` を必ず自分で見る。

   ```bash
   scripts/inspect.sh <video_url> 1     # 尺・音声の有無 + 1 秒ごとのフレーム PNG
   ```

   出た PNG を読んで、指示どおりの人物・動き・カメラになっているか、音声が
   入っているかを確かめる。焼きっぱなしで採用しない。
8. **採否**: `POST /takes/{id}/select` / `POST /takes/{id}/reject`。
   採用 Take がそのカットの完成尺になる。

## 4. モードは自動で決まる

レンダリングごとに次の順で選ばれる（`workflow_override` で固定もできるが、
固定したモードの入力が欠けていると**フォールバックせずに断られる**）。

1. カットが `carry_over_end_frame: true` かつ**直前カットに採用 Take がある** → i2v
   （前カットのラストフレームが開始フレーム）
2. 本文の `@名前` が**ファイル実体を持つ**素材を指している → r2v（参照として添付）
3. それ以外 → t2v

引き継ぎのあるカットは、**前のカットの Take を先に select してから**焼く。
`@名前` が効くのは `prompt` 本文の中だけ（`action` や `purpose` に書いても死に文字）。

## 5. プロジェクトのつまみ

- `quality`（動画）と `image_quality`（静止画）は**独立**。動画を turbo で回して
  いても、素材の静止画は `image_quality` に従う。逆も同じ。
- `megapixels` / `image_megapixels` は未設定ならビルド既定。ローカル GPU の VRAM が
  小さいなら `0.4` あたりに落とす（大きいほど遅く、落ちやすい）。
  `aspect_ratio` の表記は `GET /options` のものをそのまま使う。
- `latent_continuity`（ラテント連続性）の前提: 前カットの採用 Take があること、
  本文に**ファイル実体のある `@素材`** があること、途中で解像度・アスペクトを
  変えないこと、接続先に専用カスタムノードがあること（`GET /capabilities` で確認。
  無い接続先では使えない）。条件が欠けると降格せず拒否される。
- `auto_translate`（既定 on）: **日本語で書く**。投入時に英訳が走る。
  完成した英文を `prompt` に入れない。`@名前` を英語の説明文に置き換えない
  （参照が外れる）。
- Shot の `camera` は `The camera <camera>.` という**英文の一部として本文に
  合成される**ので、`auto_translate` が off の作品では camera も英語で書く
  （`pushes in with small amplitude at slow speed` のように動詞から書く）。on なら
  日本語で書いても英訳が直す。

## 6. 鉄則

- **テキスト項目の PATCH には必ず `base_revision`。** 渡す値は
  **`GET /projects/{id}` のトップレベル `revision_seq`**。付けないと人の編集を
  黙って踏み潰す。
  - **プロジェクト単位の連番**であって、Shot / Scene / Episode ごとの版数では
    ない。どのエンティティの PATCH でも同じ値（直前に読んだ
    `GET /projects/{id}` の `revision_seq`）を渡す。409 になるのは
    「読んだあとに**同じエンティティ**が触られたとき」だけで、別のカットが
    動いただけなら通る。
  - `POST /projects` の応答には `revision_seq` が**入らない**（null 扱い）。
    作った直後も含め、PATCH の前に必ず `GET /projects/{id}` で読み直す。
  - Shot / Scene / Take の一覧にも `revision_seq` は無い。探しに行かない。
  409 が返ったら
  `GET /projects/{id}/revisions?entity_kind=shot&entity_id=…` で**そのカットの
  履歴**を引き、`GET /projects/{id}/revisions/{seq}/diff` で人が何を変えたかを
  項目ごとに読んでから、その上に自分の変更を乗せ直す。
- **削除**: episode / scene / shot / asset / asset-file / take / timeline は API で
  消せる。誤って消したら
  `POST /projects/{id}/revisions/{seq}/restore` で戻す（`{"entity":"shot",
  "id":"…","fields":["prompt"]}` のように 1 件・1 項目だけの部分復元もできる。
  書き戻す前の状態も自動でスナップショットとして残るので、復元自体もやり直せる）。
  **プロジェクトの削除は外部 API に無い**（`DELETE /projects/{id}` は存在しない）。
  作品ごと消す必要があるときは**必ず人に依頼する**。
- Take の `stale: true` は、その Take を焼いたあとに脚本か参照素材が変わった印。
  **採用する前に焼き直す**。
- `workflow/` の JSON はプロセス内キャッシュ。編集したらサーバーを再起動しないと
  反映されない。
- 生成は時間と GPU を食う。まとめて焼く前にカット一覧を人に見せて確認する。

## 7. 生成フォームと画面操作

- **人に確認してほしい投入**: `PATCH /api/v1/ui/generate-form` で画面の生成フォームに
  値を置き、`POST /api/v1/ui/navigate` でその画面を見せる（人が「生成」を押す）。
  フォームにも `revision` があるので `base_revision` を付ける。
- **機械的な量産**: `POST /jobs` を直接叩く。
- **いま画面に出ている下書きをそのまま投入**: `POST /jobs {"from_form": true, …}`
  （一緒に送った項目だけ上書きされる）。

## 8. タイムライン → 納品

1. `POST /projects/{id}/timelines`（`episode_id` を送るとその話の採用 Take を
   V1 に自動配置）
2. 素材を探す: `GET /projects/{id}/media?kind=video|audio|image`
3. `PUT /timelines/{id}/clips` で EDL を丸ごと置き換え（重なり・尺の矛盾は 400）
4. テロップ: `POST /timelines/{id}/generate-subtitles`（字幕トラックは置き換え）
5. 脚本の変更を取り込む: `GET /timelines/{id}/sync-preview` →
   `POST /timelines/{id}/sync`
6. 欠落メディア: `GET /timelines/{id}/missing` →
   `POST /timelines/{id}/resolve-missing`
7. 演出（MV のときだけ）: `PUT /timelines/{id}/fx` に `FxOverlay` の props を入れる
   （下の「演出はタイムラインに保存する」）
8. `POST /timelines/{id}/export`（202 即受付。演出まで焼くなら `{"fx": true}`）→
   `GET /exports/{id}` をポーリング（`scripts/studio.sh wait-export <id>`）→
   `POST /exports/{id}/save-to-library`

書き出しの結果には焼き上がりの `fps` / `width` / `height` / `frames` /
`duration_ms` と `warnings` が入る。`warnings` の `PAD <カット> <不足秒>s` は
「素材が足りずに末尾を静止で埋めた」印なので、気になるならそのカットを焼き直す。

**運用の注意（音源基準で組むときは特に）**

- **差し込み（`clips/insert`）は `sync` を済ませてから**。`sync` は並べ直しなので、
  先に差し込むと二度手間になる。差し込み専用のカットは
  `PATCH /shots/{id}` で `timeline_role: "insert_only"` にしておく
  （自動配置にも `sync-preview` にも出てこなくなる）
- **`POST /export` が返す `id` を控える**。控え損ねたら
  `GET /timelines/{id}/exports`（新しい順）から拾う
- **書き出したら `frames` を検算する**: `round(音源の尺 * fps)` と突き合わせて、
  合わなければ音とずれている（`warnings` にも「フレーム数が計画と違います」が出る）
- **音源は `POST /library/audio`（multipart）で棚に入れて A1 に置く**。
  タイムラインに置けるのは棚の音だけで、作品の素材（`assets`）に上げた音は
  素材ビンに出てこない（`scripts/studio.sh upload /library/audio file=@ban.wav`）

### 演出はタイムラインに保存する（FX トラック。MV のときだけ）

**鉄則: `FxOverlay` の演出はタイムラインに保存する。ジョブに props を直接投げるのは
手元で 1 本だけ確かめたいときだけ。**

タイムラインに入れておくと編集画面の **FX トラック**に帯として並び、人がプレビューを
見ながら秒・位置を直したり要らないものを消したりできる。そのまま演出付きで書き出せる。

```
PUT /timelines/{id}/fx        # 全置換。FxOverlay の props をそのまま投げてよい
  {"theme": {…}, "seed": 1, "ambient": {…},
   "events": [{"type":"lyric","t":45.96,"until":47.5,…}, …]}
GET    /timelines/{id}/fx                            # {events:[{id, enabled, event}], …}
POST   /timelines/{id}/fx/events   {"event": {…}}     # 1 つ足す
PATCH  /timelines/{id}/fx/events/{event_id} {"event": {"t": 46.5}}   # 浅いマージ
DELETE /timelines/{id}/fx/events/{event_id}
POST   /timelines/{id}/export      {"fx": true}       # 演出付きで焼く
```

- `base` / `audio` / `fps` / `width` / `height` / `durationInSeconds` は**送らなくてよい**
  （タイムラインが持っているので無視される）。下地は書き出した mp4、音は A1 の
  最初の音声クリップが自動で入る
- `fx: true` の書き出しは、ffmpeg の mp4 のあとに Remotion が続けて走る。結果は
  `GET /exports/{id}` の `fx_status` / `fx_video_url`（レンダは数分〜十数分かかる）
- 検証は `type` と `t` だけ。中身の正本は `remotion/src/schema.ts`（zod）で、
  書き方は `.agents/skills/karakuri-remotion/SKILL.md`
- 直したいイベントだけ `PATCH`（`event` は浅いマージ、`null` でその項目を削除）。
  消さずに外したいときは `{"enabled": false}`
- Remotion 連携が無効なら `fx: true` は 400。設定は人に頼む

### 音源解析（歌詞つきの MV。指示があったときだけ）

**通常のドラマ制作からは呼ばない。** 歌詞つきの MV・モーショングラフィックスを
頼まれたとき、または明示的に指示されたときだけ。

**鉄則: 歌詞つきの MV を作るときは先に音源解析を回し、演出の秒は決め打ちしない。**

```
POST /jobs {"mode":"audio_analysis",
            "analysis":{"audio":{"item_id":"<音源>"},
                        "lyrics":"1 行目\n2 行目\n…",
                        "stems":[{"item_id":"<ボーカルステム>"}],
                        "align_substitutions":{"BAN!":"バン"},
                        "model":"medium"}}
→ wait-job → GET /jobs/{id} の analysis_url（/outputs/{id}/analysis.json）
```

- `lyrics` があれば行と 1 文字ごとの秒（アライン）、無ければ自由書き起こし。
  `stems`（ボーカルだけの音源）を渡せるなら渡す（伴奏に埋もれず精度が上がる）
- **前処理の置換**: `BAN!` のような英字＋感嘆符は whisper が読みを当てられない。
  `align_substitutions` に `{"BAN!": "バン"}` のように**仮名の読み**を書く
  （`？` `…` `「」` などの記号は既定で落ちる）
- **アラインの秒より実測 onset を優先する**。アラインの語頭は実際の発音より
  100〜250ms 遅れることがあるので、叩き込む演出（カード・シェイク）の秒は
  `onsets[].t` に寄せる。歌詞テロップは `lines[].start` / `end` でよい
- 使い道は `.agents/skills/karakuri-remotion/SKILL.md`「秒の出どころ:
  `analysis.json`」の対応表のとおり（`lyric.chars` / `beatMarker` /
  `MusicVideo.beats` / カットの `planned_start_seconds`）
- 解析用の依存が入っていないと **400**（何を入れればよいかが本文に出る）。
  そのときは人に頼む。エージェント側から `PUT /api/settings` で設定を変えない

### 音源基準で組むとき（MV のときだけ）

**通常のドラマ制作では使わない。** カットの並び順で十分で、計画秒を勝手に足さない。
音に映像を合わせる制作（MV・モーショングラフィックス）で、しかも秒が音源解析から
出せるときだけ:

0. 音源を `POST /library/audio` で棚に入れ、A1（`POST /timelines/{id}/tracks`）へ
   `source_kind: "library"` のクリップとして置く
1. `PATCH /shots/{id}` の `planned_start_seconds` に**音源上の開始秒**を書く
   （秒は決め打ちせず、歌詞のアライン・onset・ビートから出す。`null` で解除）。
   計画秒を持たない差し込み用のカットは `timeline_role: "insert_only"` にする
2. `POST /timelines/{id}/sync` を 1 回。計画秒つきのカットはその位置に置かれ、
   素材が足りないぶんは**前のカットの末尾静止で埋まる**（黒コマは作らない。
   書き出しの `warnings` に `PAD …` が出る。黒で埋めたいときはタイムラインの
   `gap_fill: "black"`）。採用 Take を差し替えても同じ秒へ置き直る
3. 最後のカットは音源の尺で締まる（タイムラインの `planned_end_seconds` →
   無ければ A1 の最初のクリップの終わり）。曲より長い尻尾を残さない
4. 短いカットを割り込ませるなら `POST /timelines/{id}/clips/insert`
   （下のクリップが前後に割れるだけで、トラックの全長は変わらない）。
   **`sync` を済ませてから**行う
5. 演出は `PUT /timelines/{id}/fx` に入れて `POST /timelines/{id}/export`
   の `{"fx": true}` で焼く（下地・fps・解像度・尺はタイムラインの値が自動で入るので、
   props に書かなくてよい）。手元で 1 本だけ確かめたいときに限り、書き出した
   `/outputs/exports/{id}/final.mp4` を `base.src` にしてジョブへ直接投げる
   （そのときは props の fps / 解像度を書き出しの値に合わせる）

## 9. Remotion（MV・モーショングラフィックス）

1. `GET /api/v1/remotion/compositions` で composition ID の一覧
2. `POST /jobs {"mode":"remotion","remotion_composition":"…","remotion_props":{…}}`
3. 進捗はふつうのジョブと同じ（`GET /jobs/{id}`）。mp4 は `video_url`。

`remotion_props` の中身の正本は Studio に同梱された **`remotion/`**（スキーマは
`remotion/src/schema.ts`）と **`.agents/skills/karakuri-remotion/SKILL.md`**。
そこを読んでから書く。

**ただし `FxOverlay` の演出はタイムラインに保存する**（§8「演出はタイムラインに保存
する」）。ジョブへ props を直接投げるのは手元で 1 本だけ確かめたいときで、制作の
本筋は `PUT /timelines/{id}/fx` → `POST /timelines/{id}/export {"fx": true}`。

連携は**既定 OFF**（Remotion が独自ライセンスのため）。一覧が 400 で「Remotion 連携が
無効です」と返るときは、設定ページの「Remotion 連携」を有効にしてもらう（依存は
`run.sh` が初回に入れている）。エージェント側から `PUT /api/settings` で
勝手に有効化しない。

## 10. 演出用スプライトと検分（指示があったときだけ）

**MV・モーショングラフィックスを求められたとき、または明示的に指示されたときだけ
使う。** 通常のドラマ制作で勝手にスプライトを足さない。

### スプライト（透過 PNG）の作り方

素材の出どころは 4 通り。**まず「本当に画像が要るか」を考える**: 雷・ハート・
集中線・吹き出しのような単純な記号は Remotion の `shape` イベント（SVG）で描ける。
**生成するのはキャラ・小物・ロゴ文字だけ。**

1. **画像生成 → 抜く**（いちばん多い）
   - プロンプトの定型: **黒背景・被写体は単体・中央・影なし**（英語で
     `on a pure black background, single subject, centered, no shadow, no text` を
     足す）。キャラの見た目を合わせたいときは World Bible の素材を参照に渡して r2i
   - `POST /jobs {"mode":"image_only", …}` → `wait-job` →
     `POST /library/key-from-job {"job_id":"…","source":"image","method":"black"}`
2. **フォント画像**（下記）をそのまま使う
3. **手持ちの PNG**: `POST /library/image` に multipart（`file=@logo.png`。
   `name` / `tags` / `category` / `nsfw` をフォームで添えられる。
   `scripts/studio.sh upload /library/image file=@logo.png` で送れる。種別を
   書きたくないときは `POST /library/upload`）→ 返った `id` を
   `POST /library/{id}/key`。**Docker で動いているアプリにホストの絶対パスは
   見えない**ので、手元のファイルは必ずこの multipart で渡す。
4. **棚にもジョブにも無い画像**（World Bible の素材・書き出し）:
   `POST /library/key {"source":{"path":"/assets/image/logo.png"}}`。`source` は
   コンタクトシートと同じ指し方（`job_id` / `item_id` / `export_id` / `path` の
   どれか 1 つ）で、`path` は `outputs/` `library/` `assets/` の中だけ

### 抜き方（`method`）の選び方

| 元の背景 | `method` | 補足 |
|---|---|---|
| 黒（生成時に指定したもの） | `black` | 既定。**文字やロゴの内側の黒は穴として残る**（外側から floodfill するため） |
| 白 | `white` | 上の明るさを反転しただけ |
| 単色（グリーンバック等） | `chroma` + `color` | 内側の同色も抜ける。ロゴ文字には向かない |
| 写真・複雑な背景 | `rembg` | 任意依存。入っていなければ 400 が返るので、そのときは諦めるか人に頼む |

- 抜けが甘い / 抜きすぎるときは `tolerance`（0..1、既定 0.1）を動かす。
- `trim`（既定 true）で余白が落ちる。**余白を残したまま Remotion に渡すと、
  `w` を大きくしても絵が小さく見える。**
- `flatten`（例 `"#ffffff"`）で、抜いたあとに残った部分を**その色一色**に塗れる
  （α はそのまま）。色つきのロゴから白抜きロゴを 1 手で作るとき。
- **`flatten` と Remotion の `sprite.tint` は「輝度を捨てるベタ塗り」**で、乗算ではない。
  塗った時点で元の陰影は消える。陰影を残したい絵には掛けない。
- **抜くときの源は必ず「抜く前」の画像を渡す。** 一度抜いた RGBA をもう一度
  `key` / `key-from-job` に通すと、白背景に合成されてから抜き直されるので、
  `flatten` を足したときに**白い四角**になる。白抜きロゴが要るなら、
  抜いた PNG からではなく**元ジョブの画像**に対して
  `key-from-job` + `"method":"black"` + `"flatten":"#ffffff"` を 1 回で掛ける。
- 結果の `url`（`/library/image/….png`）をそのまま `sprite` / `imageSlam` /
  `stickerStack` の `src` に書く。

### フォント画像

```bash
scripts/studio.sh GET /images/text/fonts
scripts/studio.sh POST /images/text '{"text":"撃ち抜け","size":220,"color":"#f5f5f5","outline":{"color":"#08080a","width":10}}'
```

用途は 2 つ。

1. **そのままスプライトにする**（決め台詞・カードの文字）。背景は既定で透明。
2. **画像生成の字形参照**。日本語を描かせて誤字になったら、同じ文言をフォントで
   組んだ画像を参照画像として添える（`reference_images` / 素材の `@名前`）と字形が
   直る。

`font` は `GET /images/text/fonts` の `name` をそのまま書く（省略すると
Noto Sans CJK JP Bold 相当）。存在しない名前は 400。

### コンタクトシートで検分する

```bash
scripts/studio.sh POST /videos/contact-sheet '{"source":{"job_id":"<job>"},"seconds":[43.9,44.2,46.0],"columns":3}'
```

- `source` は `job_id` / `item_id` / `export_id` / `path` の**どれか 1 つだけ**。
- 秒は `seconds` / `range{start,end,step}` / `frames`（フレーム番号）で指定でき、
  どれも書かなければ尺を 12 等分した位置。`frames` で頼んだ番号のコマがそのまま
  抜ける（コマの下のラベル `#1054` と絵は一致する）。
- 応答の `item.url` を GET して **自分の目で見る**。`seconds` に実際に抜いた秒が
  並ぶ。
- **演出の配置（`cx` / `cy` / `w`）を触ったら必ずこれで確かめる。**
- 手元で 1 秒ごとのフレームを並べて見たいときは `scripts/inspect.sh`（人が手元で
  使う道具）。API のコンタクトシートは**外部エージェントが必要な秒だけ束ねて見る**
  ためのもので、役割が違う。

## 11. やってはいけない

- 静的配信（`/outputs` など）は**無認証**。ネット越しに晒さない。生成物の URL を
  外部に配らない。
- `PUT /api/settings` などの内部 API（`/api/studio/...`、`/api/settings`）を外部から
  叩く運用にしない。外から触るのは `/api/v1` だけ。
- ポーリング間隔を 5 秒未満にしない。
- プロジェクトを消そうとしない（API に無い＝人の仕事）。
- 生成物を見ずに採用しない。
- 指示されていないのに演出用スプライトを足さない（§10）。`shape` で描ける記号を
  わざわざ画像生成しない。
- 通常のドラマ制作でカットに `planned_start_seconds`（音源基準の計画秒）を書かない。
  並び順で足りる。音に映像を合わせる制作で、秒を音源解析から出せるときだけ使う（§8）。
- 指示されていないのに音源解析（`mode: "audio_analysis"`）を回さない（§8）。
  逆に、**歌詞つきの MV で解析を回さずに演出の秒を決め打ちしない**。
