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
| `studio_render_shot` | `POST /api/v1/shots/{id}/render` |
| `studio_get_takes` | `GET /api/v1/shots/{id}/takes` |
| `studio_select_take` | `POST /api/v1/takes/{id}/select` |
| `studio_reject_take` | `POST /api/v1/takes/{id}/reject` |
| （ジョブ状態の参照） | `GET /api/v1/jobs/{id}`（読み取りのみ） |

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
- ファイル添付（multipart）は拡張子から `kind` を自動判定します。

### 運用上の注意（実機テストで判明）

- **ワークフロー JSON はプロセス内キャッシュ**です。`workflow/` 配下を編集したら
  サーバーを再起動しないと反映されません。
- **カットの `megapixels` 未指定はバックエンドの既定値**にフォールバックします。
  VRAM 8GB 級の GPU では 1.0MP で CUDA OOM になるため、0.4MP 程度を明示するのが
  安全です。
- 素材の `kind` は `image` / `video` / `audio` のみです（メモだけの素材は登録
  できません）。素材名に `@` は使えません（`@素材名` 参照と衝突するため）。

## 9. 将来課題（v1 ではやらない）

- webhook コールバック（レンダリング完了通知）
- 静的配信の**アプリ内**認証（ネット越しは §5 の Cloudflare Access で守るため、
  Cloudflare を使わない公開形態が必要になったときだけ）
- 複数 API キー / スコープ付きキー
- 汎用ジョブ投入・キャンバス操作・エージェントセッション起動の公開
- リクエスト回数のレート制限（投入キュー上限は §3 の暴走ガードとして v1 に含む）
