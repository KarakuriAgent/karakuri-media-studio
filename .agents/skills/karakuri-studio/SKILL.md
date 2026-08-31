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
   作り直さない（`GET /projects/{id}` は素材・話・場・カット・Take と
   `revision_seq` を一度に返す）。
2. **作品を作る**: `POST /projects`。`synopsis` / `world_notes` を書く。
3. **素材を用意する**（見た目を固定したいものは必ずファイル実体を持たせる）
   - 生成して登録: `POST /jobs {"mode":"image_only", …}` → `wait-job` →
     `POST /projects/{id}/assets/from-job {"job_id":…,"name":"アリス",
     "source":"image"}`。
   - 手元のファイル: `POST /projects/{id}/assets` に multipart（`file=@…`）、
     または同じマシンの絶対パスを JSON の `path` で。
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

## 6. 鉄則

- **テキスト項目の PATCH には必ず `base_revision`。** 取得系が返す
  `revision_seq` をそのまま渡す。付けないと人の編集を黙って踏み潰す。
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
7. `POST /timelines/{id}/export`（202 即受付）→ `GET /exports/{id}` を
   ポーリング（`scripts/studio.sh wait-export <id>`）→
   `POST /exports/{id}/save-to-library`

## 9. Remotion（MV・モーショングラフィックス）

1. `GET /api/v1/remotion/compositions` で composition ID の一覧
2. `POST /jobs {"mode":"remotion","remotion_composition":"…","remotion_props":{…}}`
3. 進捗はふつうのジョブと同じ（`GET /jobs/{id}`）。mp4 は `video_url`。

`remotion_props` の中身の正本は **Remotion プロジェクト側のリポジトリ**（アプリの
設定 `remotion_project_dir` が指す先）の SKILL / README。そこを読んでから書く。

## 10. やってはいけない

- 静的配信（`/outputs` など）は**無認証**。ネット越しに晒さない。生成物の URL を
  外部に配らない。
- `PUT /api/settings` などの内部 API（`/api/studio/...`、`/api/settings`）を外部から
  叩く運用にしない。外から触るのは `/api/v1` だけ。
- ポーリング間隔を 5 秒未満にしない。
- プロジェクトを消そうとしない（API に無い＝人の仕事）。
- 生成物を見ずに採用しない。
