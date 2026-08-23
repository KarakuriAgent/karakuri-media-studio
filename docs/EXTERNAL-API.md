# 外部公開 API（/api/v1）設計

外部のエージェント（例: karakuri-world のログを監視するブリッジ）から、スタジオの
話づくりとレンダリングを行えるようにする API。**Grok エージェントに許している操作
（`app.agent_protocol.STUDIO_ACTIONS`）と同じ範囲**を、API キー認証付きの REST と
して公開する。

- 既存の内部 API（`/api/studio` など）と UI・エージェント機構には手を入れない。
- 実体は薄いラッパー: 既存の `app.studio` サービス関数と Pydantic モデルを
  そのまま呼ぶだけ。ビジネスロジックはここに書かない。

実装は完了し、実機で一通り動作を確認済みです（§8）。

## 1. 全体方針

| 項目 | 決定 |
|---|---|
| プレフィックス | `/api/v1`（内部 API の `/api/...` とは別系統。バージョンを持つ） |
| 認証 | `X-API-Key` ヘッダ。設定 `external_api_key` と定数時間比較 |
| 既定状態 | `external_api_key` が空 = **外部 API 全体が無効**（404 を返す） |
| 公開範囲 | Grok エージェントのスタジオ操作と同等（下表）。削除はカット（Shot）のみ |
| 想定配置 | ループバック直結、または Cloudflare Tunnel + Access 経由（§5） |
| 完了通知 | ポーリングのみ（Take / Job の GET）。webhook は将来課題 |
| 暴走ガード | 未完了 Take が上限（既定 20）を超えたらレンダリング投入を 429 で拒む |

### Grok エージェントの操作との対応

| agent_protocol のアクション | 外部 API |
|---|---|
| `studio_list_projects` | `GET /api/v1/projects` |
| `studio_get_project` | `GET /api/v1/projects/{id}` |
| `studio_create_project` | `POST /api/v1/projects` |
| `studio_update_project` | `PATCH /api/v1/projects/{id}` |
| `studio_upsert_episode` | `POST /api/v1/projects/{id}/episodes` / `PATCH /api/v1/episodes/{id}` |
| `studio_upsert_scene` | `POST /api/v1/episodes/{id}/scenes` / `PATCH /api/v1/scenes/{id}` |
| `studio_upsert_shot` | `POST /api/v1/projects/{id}/shots` / `PATCH /api/v1/shots/{id}` |
| `studio_delete_shot` | `DELETE /api/v1/shots/{id}` |
| `studio_upsert_asset` | `POST /api/v1/projects/{id}/assets` / `PATCH /api/v1/assets/{id}` |
| `studio_register_asset_from_job` | `POST /api/v1/projects/{id}/assets/from-job` |
| `studio_render_shot` | `POST /api/v1/shots/{id}/render`（ボディは任意、下記） |
| `studio_get_takes` | `GET /api/v1/shots/{id}/takes` |
| `studio_translate_shot` | `POST /api/v1/shots/{id}/translate` |
| `studio_select_take` | `POST /api/v1/takes/{id}/select` |
| `studio_reject_take` | `POST /api/v1/takes/{id}/reject` |
| （ジョブ状態の参照） | `GET /api/v1/jobs/{id}`（読み取りのみ） |

`POST /api/v1/shots/{id}/render` は**任意の JSON ボディ**を取る（内部 API の
`POST /api/studio/shots/{id}/render` と同じ `StudioRenderRequest`）。送った項目だけが
**その 1 回の投入にだけ**効き、カットもプロジェクトも書き換えない:

| 項目 | 省いたときの解決 |
| --- | --- |
| `megapixels` | カット → プロジェクト → ワークフローの既定（0.4MP） |
| `aspect_ratio` | カット → プロジェクト → `4:3 (Standard)` |
| `duration` | カットの `duration_seconds`（1〜15 秒） |
| `steps` | プロジェクトの `steps` → テンプレートの既定（0〜150。`0` を送れば「既定のまま」の明示で、プロジェクトの設定より優先） |
| `seed` | カットの `seed` → 毎回ランダム |

ボディごと省けば従来どおりの投入。範囲外の値は 400（`StudioError`）で、実際に使われた
値は Take の元ジョブの `params`（`GET /api/v1/jobs/{id}`）に残る。

**公開しないもの**: プロジェクト / エピソード / シーン / Take の削除、設定
（`/api/settings`）、モデルダウンロード、汎用ジョブ投入（`/api/jobs` POST）、
キャンバス操作、エージェントセッション（`/api/agent`）。必要になった時点で
個別に追加を検討する。

## 2. 追加エンドポイント: 一括投入

外部エージェントの主用途「話の一式を 1 回で納品する」ための便利エンドポイント。
個別 CRUD の組み合わせでも同じことはできるが、こちらは**全部作れたか・全く作らな
かったかの二択**（途中失敗の中途半端を残さない）。

```
POST /api/v1/stories
{
  "project_id": "prj_xxx",          // どちらか必須（code でも引ける）
  "project_code": "KW",
  "episode": { "title": "第3話 送金拒否事件", "synopsis": "..." },
  "scenes": [
    {
      "title": "酒場・夜", "time_of_day": "深夜", "synopsis": "...",
      "shots": [
        { "title": "口論の始まり", "dialogue": "金は払わん！",
          "action": "...", "prompt": "...", "duration_seconds": 5 },
        { "title": "決裂", "prompt": "..." }
      ]
    }
  ],
  "render": false                    // true なら作成後に全カットを順次投入
}
```

- 201 で作成した episode / scenes / shots の全 id（`render: true` なら take の id
  も）を返す。
- shot の項目は `StudioShotCreate` と同じ。scene / episode も既存 Create モデルと
  同じ項目。
- 実装は 1 トランザクション（`app.db` の接続で `BEGIN` … `COMMIT`）。途中で
  検証に落ちたら全ロールバックして 400。
- `render: true` のとき、レンダリング投入は作成コミット後に 1 カットずつ行う。
  投入に失敗したカットがあっても作成済みの脚本は残し、結果に per-shot の
  成否を入れて返す（生成は GPU / 課金がからむため、二択にしない）。

### 2.1 脚本ドラフト作成ガイド

上の一括投入に渡す脚本を、**スタジオで実際に映像化できる形**で書くための手引き。
外部の LLM エージェントがそのままプロンプトに貼れる日本語 Markdown を返す。

```
GET /api/v1/prompt-guide
{
  "guide_version": "2026-08-18",     // 中身を変えたら上がる（キャッシュ判定用）
  "markdown": "# 脚本ドラフト作成ガイド…",
  "limits": {                        // 本文と同じ数値の機械可読版
    "shot_duration_min_seconds": 1.0, "shot_duration_max_seconds": 15.0,
    "shot_duration_recommended": "4-15",
    "reference_images_max": 9, "reference_videos_max": 3, "reference_audios_max": 3
  }
}
```

- 本文は `backend/app/drafting_guide.py` が**既存の定数から組み立てる**（静的な
  コピーは持たない）: 尺は `app.studio.SHOT_DURATION_MIN/MAX`、参照素材の上限は
  `app.workflows.MINIMAX_H3_REFERENCE_*`、H3 の書き方は
  `app.prompts.MINIMAX_H3_GUIDE_BODY`、実例は `app.h3_examples` から
  代表を選抜。内部の規約が変われば配るガイドも一緒に変わる。
- 中身は「フィールド契約（`prompt` だけがモデルに届く / `title`・`action`・
  `purpose` はメモ）」「素材メンション `@名前`」「H3 プロンプト規約」「実例
  （代表 2 件 + §2.2 の取得方法）」「stories 投入の注意」の 5 節。フィールドの届き方の正本は
  `app.studio.compose_prompt`（変えたらガイドも直す）。
- 内蔵エージェント向けの `app.prompts.AGENT_STUDIO` はジョブ実行・Take 管理まで
  含むので、そのままは配らない（ドラフト作成に要る分だけのサブセット）。

### 2.2 プロンプト実例（few-shot）

上のガイドが載せている代表例だけでは足りないとき（アニメ調・商品CM・画面内の
文字やUI・複数話者・参照素材の多いカット・既存動画の編集など）に、実例を種類で
引くためのエンドポイント。

```
GET /api/v1/prompt-examples                       // 索引だけ（本文なし）
GET /api/v1/prompt-examples?id=H3-E4              // その 1 件を本文つきで
GET /api/v1/prompt-examples?mode=r2v&category=multi-reference&limit=2
{
  "guide_version": "2026-08-18",   // §2.1 のガイドと同じ版
  "modes": ["t2v", "i2v", "fl2v", "l2v", "r2v", "edit"],
  "categories": ["cinematic", "dialogue", "anime", "…"],
  "total": 2,
  "examples": [
    {
      "id": "H3-E7",
      "mode": "r2v",
      "categories": ["multi-reference", "dialogue"],
      "summary": "Ref2VA — picture + video + audio, one job each …",
      "tier": "canonical",              // canonical = 公式形式の完成例
      "source": "written for this app from …",
      "note": "",
      "body": "subject_definitions:\n…"  // 索引のときは null
    }
  ]
}
```

- クエリ（`mode` / `category` / `id` / `limit`）を 1 つも付けなければ索引を返す
  （`body` は `null`）。1 つでも付けると本文まで返る。
- `mode` は `t2v` / `i2v` / `fl2v` / `l2v` / `r2v` / `edit`、`category` は
  `cinematic` / `dialogue` / `anime` / `product` / `action` / `ui-text` / `ugc` /
  `horror` / `multi-reference` / `multilingual`。**有効な値の一覧はレスポンスの
  `modes` / `categories`** にも入る（ガイド本文にも同じ一覧が載る）。知らない値は
  400、存在しない `id` は 404。
- `tier` は 2 種類: `canonical` は公式 rewrite 形式の**完成例**（そのまま真似して
  よい）、`inspiration` は公式ブログ / コミュニティの**生入力**（発想の素材で、
  形は真似しない）。
- データの正本は `backend/app/h3_examples.py`。選び方（`select_examples`）は内蔵
  エージェントの `get_prompt_examples` アクションと共有していて、外部と内蔵で
  同じ例が出る。

## 3. 認証

- 設定 `Settings.external_api_key: str = ""` を追加（`SettingsUpdate` にも追加、
  設定画面に入力欄を出す）。
- FastAPI の依存関係 `require_external_key` を `/api/v1` ルーター全体に付ける:
  - `external_api_key` が空 → 404（外部 API という機能ごと存在しないふるまい）
  - `X-API-Key` ヘッダ欠落 / 不一致 → 401。比較は `secrets.compare_digest`
- CORS は変更しない（サーバー間通信のためブラウザの制約は無関係）。
- 待受は今までどおり `127.0.0.1` を既定とする。ネット越しの公開は §5 の
  Cloudflare 構成で行い、アプリを直接 `0.0.0.0` に開けない。

### 暴走ガード（投入上限）

外部からのレンダリング投入（`POST /api/v1/shots/{id}/render` と
`POST /api/v1/stories` の `render: true`）は、**未完了の Take**（ジョブが
queued / running のもの）が上限を超えているとき 429 を返す。

- 上限は設定 `external_max_pending_takes: int = 20`（0 = 無制限）。
- バグったブリッジの無限投入が GPU キュー占有と課金（RunPod / Comfy Cloud）に
  直結するのを防ぐ最小の安全弁。**内部 API（UI からの操作）には掛けない**。

## 4. ファイルの受け渡し

- **読み**: API レスポンスの `url` 欄（`/outputs/...` / `/assets/...` /
  `/library/...`）をそのまま GET する。静的配信には認証が無いので、ループバック
  運用が前提（ネット越し公開時はプロキシで守る）。
- **書き（素材登録）**: 2 ルート。
  - JSON: `POST /api/v1/projects/{id}/assets`（`StudioAssetCreate`）。`path` に
    同一マシン上の絶対パスを書くと `assets/<kind>/` へ複製される。
  - multipart: 同 URL にファイル添付（内部 API と同じ受け口を流用）。

## 5. デプロイ: Cloudflare 越しの公開

**アプリをそのまま公開してはいけない**: `/api/v1` 以外の内部 API は無認証で、
`PUT /api/settings`（保存済み API キーの読み書き）・`/docs`（全エンドポイントの
列挙と実行）・エージェント API（ホスト上で grok CLI を起動）・生成物の静的配信が
すべて晒される。認証はアプリに足すのではなく **Cloudflare の入口で全体に掛ける**。

構成（RunPod 連携ですでに使っている Cloudflare Tunnel と同じ道具立て）:

```
ブリッジ ──(CF-Access-Client-Id/Secret + X-API-Key)──▶ Cloudflare エッジ
                                                          │ Access で認証
ブラウザ ──(メール OTP / IdP ログイン)──────────────────┘
                                                          ▼
                                    cloudflared（Tunnel）──▶ 127.0.0.1:8000
```

- Tunnel のホスト名（例 `studio.example.com`）に **Cloudflare Access** の
  ポリシーを付ける。人間はブラウザで OTP / IdP ログイン、ブリッジには
  **サービストークン**を発行し、毎リクエストに `CF-Access-Client-Id` /
  `CF-Access-Client-Secret` ヘッダを付けさせる。
- 認証はエッジで完結するため、アプリへ到達するのは認証済みトラフィックのみ。
  内部 API・静的配信・`/docs` も丸ごと守られ、**アプリ側の改修は不要**。
- cloudflared はホスト内から `127.0.0.1:8000` へ接続するので、アプリの待受は
  ループバックのまま変えない。
- アプリ側の `X-API-Key`（§3）は**二重防御として併用**する。Access ポリシーの
  設定ミスがあっても、書き込み系の外部 API は自前のキーで守られる。
- TLS は Cloudflare が終端する。CORS・バインド設定の変更は不要。

同一マシン運用（ブリッジも同じホスト）の場合はこの節は不要で、ループバック
直結 + `X-API-Key` だけでよい。

## 6. エラー設計

内部 API と同じ移し方（`StudioError` → 400 / `StudioConflict` → 409 /
見つからない → 404 / Pydantic 検証 → 422）に、認証の 401 と無効時の 404 が
乗るだけ。エラー本文は `{"detail": "..."}`（FastAPI 既定）で統一。

## 7. 実装の置き場所

| ファイル | 変更 |
|---|---|
| `backend/app/routers/external.py` | 新設。`APIRouter(prefix="/api/v1", dependencies=[Depends(require_external_key)])`。各ハンドラは `app.studio` / `app.jobs` の既存関数を呼ぶだけ |
| `backend/app/models.py` | `Settings.external_api_key` / `SettingsUpdate` 追加。一括投入の `StoryCreate` / `StoryResult` モデル追加 |
| `backend/app/studio.py` | 一括投入 `create_story()` を追加（既存の create_episode / create_scene / create_shot を 1 接続でまとめる） |
| `backend/app/main.py` | `external.router` の include（1 行） |
| `frontend/`（設定画面） | `external_api_key` の入力欄（生成ボタン付きだと親切） |
| `README.md` / `docs/SPEC.md` | 外部 API の節を追記（有効化の手順と公開時の注意） |

## 8. 動作確認（2026-08-10 / ローカル ComfyUI + MiniMax H3）

ローカルの ComfyUI（MiniMax H3）に接続した実機で、全エンドポイントを `X-API-Key`
付きで通し、**素材登録からレンダリング・Take 採否まで一連の流れが動くことを確認
済み**です。

| 範囲 | 状態 |
|---|---|
| 認証の 3 態（キー未設定 = 404 / キー無し = 401 / 正キー = 200） | 確認済み |
| プロジェクト（`GET` / `POST` / `GET {id}` / `PATCH {id}`） | 確認済み |
| 話・場（`POST .../episodes`・`PATCH /episodes/{id}`・`POST .../scenes`・`PATCH /scenes/{id}`） | 確認済み |
| カット（`POST .../shots`・`PATCH /shots/{id}`・`DELETE /shots/{id}`） | 確認済み。削除は 204、再削除で 404 |
| 一括投入 `POST /stories` | 確認済み。`render: true` で話 → 場 → カット作成からレンダリング投入まで 1 リクエスト |
| 素材登録 3 方式（JSON の `path` 複製 / multipart 添付 / `assets/from-job`） | 確認済み。`PATCH /assets/{id}` も含む |
| プロンプト中の `@素材名` 参照 | 確認済み。画像素材を参照したカットは自動で `minimax_h3_r2v` に切り替わり、`reference_images` に添付される |
| レンダリングと Take（`POST /shots/{id}/render` → `GET /shots/{id}/takes`・`GET /jobs/{id}` → `POST /takes/{id}/select` / `reject`） | 確認済み。864x480 / 5 秒 / h264 + aac 音声つきの動画が生成され、採用でカットが `done` に |
| 暴走ガードの 429（`external_max_pending_takes` 超過） | **未テスト**（実装のみ） |

補足:

- 日本語のプロンプトは `auto_translate` により Grok が英訳したうえでワークフローへ
  渡ることを確認しました。
- プロジェクトの `latent_continuity`（`POST /api/v1/projects` と
  `PATCH /api/v1/projects/{id}` で読み書きできます。既定 `false`）を立てると、
  `carry_over_end_frame` を立てたカットの引き継ぎが**ラストフレーム 1 枚から
  直前カットの動画＋AV ラテント**（`minimax_h3_r2v_context`）に変わります。
  参照素材（`@素材名`）の指定と直前カットの採用 Take が要り、どちらかが欠けて
  いるカットは黙って別のモードに落とさず 400 で断ります。`MiniMaxH3MotionContext`
  系のカスタムノードが無い接続先（Comfy Cloud）でも 400 です。
  また `latent_continuity` が立っているあいだは、**通常のカットも AV ラテントを
  保存する版**（`minimax_h3_t2v_save` / `_i2v_save` / `_r2v_save`）で投入します。
  連鎖の起点になるカットがラテントを残さないと、次のカットに引き継ぐものが無く
  連鎖を始められないためで、仕上がりは素の版と変わりません（こちらもカスタム
  ノード頼みなので、無い接続先では 400 です）。
- プロジェクトの `quality`（`POST /api/v1/projects` と `PATCH /api/v1/projects/{id}`
  で読み書きできます。`"normal"` / `"opt"` / `"turbo"`。既定 `"normal"`）は
  **動画生成の品質**で、モード（t2v / i2v / r2v）とも `latent_continuity` とも
  直交しています。モードが決まり、`latent_continuity` によるラテント保存版への
  読み替えが済んだあとに掛け合わせて、`minimax_h3_{t2v,i2v,r2v}_{turbo,opt}` /
  `minimax_h3_{t2v,i2v,r2v}_save_{turbo,opt}` / `minimax_h3_r2v_context_{turbo,opt}`
  へ解決されます。`turbo` は 4step 蒸留 LoRA 版（速いが粗い）、`opt` は 20 steps の
  まま量子化と高速化だけを入れた版です。カスタムノードの無い接続先
  （Comfy Cloud）では**品質だけを落として**読み替え済みの版で投入します
  （400 にはしません）。どれに当たったかは
  `GET /api/v1/shots/{id}/prompt-preview` の `workflow_reason` に出ます。
- プロジェクトの `image_quality`（`POST /api/v1/projects` と
  `PATCH /api/v1/projects/{id}` で読み書きできます。`"normal"` / `"opt"` /
  `"turbo"`。既定 `"normal"`）は **画像生成の品質**で、上の `quality` とは
  **完全に独立**したつまみです。作品の素材となる静止画を MiniMax H3 Image で
  作るときに、`minimax_h3_{t2i,i2i,r2i}` の素 / `_opt` / `_turbo` のどれを使うかを
  決めます。**動画の `quality` を静止画に流用しないでください** — 動画を
  `turbo` で回している作品でも、素材の絵は `image_quality` に従います
  （その逆も同じ）。`_opt` / `_turbo` は動画側と同じカスタムノード頼みなので、
  入っていない接続先（Comfy Cloud）では素の版を使います。いまのところ静止画を
  焼く経路はアプリ側に無く、素材画像を作るのはエージェント（エージェントモード /
  外部 API 経由の Claude Code・Cursor CLI）なので、この設定は**エージェントへの
  指示値**として効きます。
- プロジェクトの `image_megapixels` / `image_aspect_ratio` / `image_steps`
  （`POST /api/v1/projects` と `PATCH /api/v1/projects/{id}` で読み書きできます。
  既定はそれぞれ `null` / `null` / `0`）は、**素材の静止画の画質・画面比・
  サンプリング回数**です。動画側の `megapixels` / `aspect_ratio` / `steps` と
  同じ 3 項目を静止画用に別で持つもので、素材の静止画ジョブ
  （`mode: "image_only"`）にはこちらを使い、**動画用の値は流用しません**。
  `null` / `0` は「指定しない」＝テンプレートの既定のまま（MiniMax H3 Image は
  約 0.98MP）で、`PATCH` に `null` を明示すると既定へ戻ります（送らなければ
  今の値のまま）。`image_steps` の上限は動画側の `steps` と同じ 150 で、
  外れた値は 400 です。
- プロジェクトの `megapixels` と `aspect_ratio`（`POST /api/v1/projects` と
  `PATCH /api/v1/projects/{id}` で読み書きできます。どちらも既定 `null`）は
  **その作品の画質・画面比の既定値**で、生成フォームと同じ 2 項目です。
  ワークフローの選択には効かず、投入時にそのまま渡ります。`null` は
  **明示しない**（＝ワークフロー宣言 / バックエンドの既定 0.4MP）で、
  `megapixels` を上げるほど遅くなり、VRAM の小さい GPU では CUDA OOM に
  なることがあります。効き方は**カットの値 → プロジェクトの値 → 既定**の順で、
  2 つはそれぞれ独立に解決されます。PATCH で `null` を明示すると既定へ戻ります
  （送らなければ今の値のまま）。変更はそれ以降に投入するカットにだけ効くので、
  `latent_continuity` の連鎖の途中で変えると前のクリップと解像度が合わなくなります。
- プロジェクトの `nsfw`（`POST /api/v1/projects` と `PATCH /api/v1/projects/{id}`
  で読み書きできます。既定 `false`）は**作品まるごとの指定**です。立てるとその
  プロジェクトから投入するジョブはすべて NSFW（`nsfw_source: "manual"`）になり、
  オフなら非 NSFW で固定されます（どちらも明示なので、Grok の自動判定は走りません）。
- ファイル添付（multipart）は拡張子から `kind` を自動判定します。

### 運用上の注意（実機テストで判明）

- **ワークフロー JSON はプロセス内キャッシュ**です。`workflow/` 配下を編集したら
  サーバーを再起動しないと反映されません。
- **カットの `megapixels` 未指定はプロジェクトの `megapixels`、それも未指定なら
  バックエンドの既定値**にフォールバックします。VRAM 8GB 級の GPU では 1.0MP で
  CUDA OOM になるため、0.4MP 程度が安全です。
- 素材の `kind` は `image` / `video` / `audio` のみです（メモだけの素材は登録
  できません）。素材名に `@` は使えません（`@素材名` 参照と衝突するため）。

## 9. 将来課題（v1 ではやらない）

- webhook コールバック（レンダリング完了通知）
- 静的配信の**アプリ内**認証（ネット越しは §5 の Cloudflare Access で守るため、
  Cloudflare を使わない公開形態が必要になったときだけ）
- 複数 API キー / スコープ付きキー
- 汎用ジョブ投入・キャンバス操作・エージェントセッション起動の公開
- リクエスト回数のレート制限（投入キュー上限は §3 の暴走ガードとして v1 に含む）
