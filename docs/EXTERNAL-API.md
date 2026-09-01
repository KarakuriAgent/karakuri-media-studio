# 外部公開 API（/api/v1）設計

外部のエージェント（手元の Claude Code / Codex / Cursor CLI や、karakuri-world の
ログを監視するブリッジなど）が、脚本づくりから生成・素材の整理・つなぎ・書き出しまでを
自分で回すための API。**制作を回す主体はアプリの中ではなく外**にあり、その段取りは
[`.agents/skills/karakuri-studio/SKILL.md`](../.agents/skills/karakuri-studio/SKILL.md)
（`AGENTS.md` / `CLAUDE.md` からリンク）に置いてある。

- 既存の内部 API（`/api/studio` など）と UI には手を入れない。
- 実体は薄いラッパー: 既存の `app.studio` / `app.jobs` / `app.timeline` のサービス関数と
  Pydantic モデルをそのまま呼ぶだけ。ビジネスロジックはここに書かない。

実装は完了し、実機で一通り動作を確認済みです（§8）。

## 1. 全体方針

| 項目 | 決定 |
|---|---|
| プレフィックス | `/api/v1`（内部 API の `/api/...` とは別系統。バージョンを持つ） |
| 認証 | `X-API-Key` ヘッダ。設定 `external_api_key` と定数時間比較 |
| 既定状態 | `external_api_key` が空 = **外部 API 全体が無効**（404 を返す） |
| 公開範囲 | 人が UI でできることのほぼ全部（下表）。**削除はプロジェクト以外** |
| 正本 | 公開範囲は `GET /api/v1/openapi.json`（この API だけの縮小版 OpenAPI） |
| 想定配置 | ループバック直結、または Cloudflare Tunnel + Access 経由（§5） |
| 完了通知 | ポーリングのみ（Take / Job / Export の GET）。webhook は将来課題 |
| 暴走ガード | 「生成」と「書き出し」の 2 プール。上限（既定 20）に達していたら投入を 429 で拒む |

### 公開している範囲

正本は `GET /api/v1/openapi.json`（アプリ全体のスキーマから `/api/v1` のパスと、そこから
`$ref` で辿れるスキーマだけを抜き出した縮小版。内部 API は載らない）。ここでは何が
どこまで見えているかの目安だけを並べる。

| 群 | 代表的なエンドポイント |
|---|---|
| プロジェクト | `GET/POST /projects`・`GET/PATCH /projects/{id}` |
| 話 / 場 / カット | `POST /projects/{id}/episodes`・`POST /episodes/{id}/scenes`・`POST /projects/{id}/shots` と各 `PATCH` / `DELETE`、`POST .../reorder`（並べ替え） |
| 投入前の確認 | `GET /shots/{id}/prompt-preview`（実際に投入されるプロンプト・ワークフロー・その理由・`render_blocker`）・`POST /shots/{id}/translate` |
| 素材（World Bible） | `POST /projects/{id}/assets`（JSON / multipart）・`assets/from-job`・`PATCH/DELETE /assets/{id}`・素材のリファレンス（`/assets/{id}/files`・`DELETE /asset-files/{id}`） |
| 生成と Take | `POST /shots/{id}/render`・`GET /shots/{id}/takes`・`POST /takes/{id}/select`・`reject`・`cancel`・`DELETE /takes/{id}` |
| 汎用ジョブ | `GET/POST /jobs`・`GET /jobs/{id}`・`POST /jobs/{id}/cancel`・`rerun`・`continue` |
| ライブラリ | `GET /library`・`POST /library/image` / `POST /library/audio` / `POST /library/upload`（multipart）・`POST /library/from-job`・`POST /library/sheet`・`POST /library/{id}/key`・`POST /library/key`・`POST /library/key-from-job`・`PATCH /library/{id}`（**削除は非公開**） |
| 素材の下ごしらえ | `POST /images/text`・`GET /images/text/fonts`・`POST /videos/contact-sheet`（§3.4） |
| 編集（タイムライン） | `POST /projects/{id}/timelines`・`GET/PATCH/DELETE /timelines/{id}`・`PUT /timelines/{id}/clips`・`POST /timelines/{id}/clips/insert`・トラック CRUD・`generate-subtitles`・`sync-preview` / `sync`・`missing` / `missing/resolve`・`GET /projects/{id}/media` |
| 演出（FX トラック） | `GET/PUT /timelines/{id}/fx`・`POST /timelines/{id}/fx/events`・`PATCH/DELETE /timelines/{id}/fx/events/{event_id}`（§3.3） |
| 書き出し | `POST /timelines/{id}/export`（202。`fx: true` で演出付き）・`GET /timelines/{id}/exports`・`GET /exports/{id}`・`POST /exports/{id}/save-to-library` |
| 編集履歴 | `GET /projects/{id}/revisions`・`GET .../{seq}/diff`・`POST .../{seq}/restore`（§3.1） |
| 画面 | `GET/PATCH /ui/generate-form`・`POST /ui/navigate`（§3.2） |
| Remotion | `GET /remotion/compositions`（§3.3） |
| 音源解析 | `POST /jobs` の `mode: "audio_analysis"`（§3.3.2）。成果物は `analysis_url` |
| 参照系 | `GET /openapi.json`・`capabilities`・`options`・`prompt-guide`・`prompt-examples` |
| 一括投入 | `POST /stories`（§2） |

`GET /api/v1/capabilities` は「いまの接続先でラテント連続性 / ラテントアップスケールが
使えるか」を、`GET /api/v1/options` は生成フォームと同じ選択肢（アスペクト比の表記・
ワークフローの一覧と制約・LoRA・ライブラリ）を返す。`options` は ComfyUI の所在を
外に出さないため `comfy_url` を空にして返し、ComfyUI が落ちていても `comfy_error` を
入れた 200 になる。

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
| `latent_upscale` | プロジェクトの `latent_upscale`（既定 ON。接続先が対応しなければ黙って `off`） |

ボディごと省けば従来どおりの投入。範囲外の値は 400（`StudioError`）で、実際に使われた
値は Take の元ジョブの `params`（`GET /api/v1/jobs/{id}`）に残る。

**公開しないもの**: **プロジェクトの削除**（リビジョンごとカスケードで消えて復元できない
ので、外には出さず人に頼む運用にする）、ライブラリ素材の削除、設定（`/api/settings`）、
モデルのダウンロード、プロンプト作成チャット（`/api/chat`）。必要になった時点で個別に
追加を検討する。

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
- 段取りそのもの（何から読み、どういう順で作り、どこで人に確認するか）はガイドではなく
  SKILL（`.agents/skills/karakuri-studio/SKILL.md`）が持つ。ここで配るのは
  **脚本とプロンプトの書き方**だけ。

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
- データの正本は `backend/app/h3_examples.py`。§2.1 のガイドが載せる代表例も同じ
  ところから選ぶ（`select_examples`）ので、ガイドと実例集で食い違わない。

## 3. 認証・安全弁・横断的な仕組み

- 設定 `Settings.external_api_key: str = ""` を追加（`SettingsUpdate` にも追加、
  設定画面に入力欄を出す）。
- FastAPI の依存関係 `require_external_key` を `/api/v1` ルーター全体に付ける:
  - `external_api_key` が空 → 404（外部 API という機能ごと存在しないふるまい）
  - `X-API-Key` ヘッダ欠落 / 不一致 → 401。比較は `secrets.compare_digest`
- CORS は変更しない（サーバー間通信のためブラウザの制約は無関係）。
- 待受は今までどおり `127.0.0.1` を既定とする。ネット越しの公開は §5 の
  Cloudflare 構成で行い、アプリを直接 `0.0.0.0` に開けない。

### 暴走ガード（投入上限。2 つのプール）

外部からの投入は 2 つのプールで見張り、そのプールが上限に達しているあいだは 429。

| プール | 数えるもの | 掛かるエンドポイント |
|---|---|---|
| 生成 | 未完了のジョブ（queued / running。Take は必ずジョブを 1 本持つので Take も含む） | `POST /shots/{id}/render`・`POST /stories`（`render: true`）・`POST /jobs`・`jobs/{id}/rerun`・`continue` |
| 書き出し | 走っている書き出し（ffmpeg） | `POST /timelines/{id}/export` |

- 上限はどちらも設定 `external_max_pending_takes: int = 20`（0 = 無制限）。
- **プールを分ける**理由: 走るものが GPU（生成）と CPU（ffmpeg）で詰まり方が違い、
  互いの枠を食い合うと片方が動かなくなる。**Shot のレンダリングと汎用ジョブは同じ
  プールを分け合う**（別々に数えると、どちらも上限まで投入できてしまう）。
- 「数えてから投入する」までは錠で括る（並行リクエストが数え合いになって、上限に
  達していても全部すり抜けるのを防ぐ）。錠はプールごとに別で、生成は
  `app.studio.PENDING_JOBS_LOCK`、書き出しは `external._EXPORTS_LOCK`。書き出しの受付は
  初回に ffprobe を回して遅いので、同じ錠に相乗りさせると無関係な生成まで待たせてしまう。
- バグったブリッジの無限投入が GPU キュー占有と課金（RunPod / Comfy Cloud）に
  直結するのを防ぐ最小の安全弁。**内部 API（UI からの操作）には掛けない**。

### 楽観ロック（`base_revision`）

外部エージェントが**人の変更を黙って上書きする**のを防ぐ安全弁。`GET
/api/v1/projects/{id}` の応答に入る `revision_seq`（そのプロジェクトの履歴の
連番）を控えておき、PATCH のボディに `base_revision` として送り返す:

```json
PATCH /api/v1/shots/sht_x  {"prompt": "…", "base_revision": 42}
```

`base_revision` 以降に**同じエンティティ（entity + id）を触った変更**があると
409（`detail` に現在の連番と衝突した項目名）。別のカットや別の素材が動いた
だけなら通る（プロジェクト全体の連番だけで比べると、並行編集がすべて 409 に
なってしまうため）。省略すれば今までどおり無条件に書き込む。まだ存在しない
連番（現在より大きい値）を送ると 400。

### 3.1 編集履歴（リビジョン）の API

上の 409 から立ち直るための 3 本。スタジオへの書き込みは 1 操作 = 1 リビジョンとして
積まれていて（SPEC §7.4）、外部 API の変更は `actor = "external"` で残る（`user` = UI /
`chat` = アプリ内のチャット）。

```
GET  /api/v1/projects/{id}/revisions?entity_kind=shot&entity_id=sht_x
GET  /api/v1/projects/{id}/revisions/{seq}/diff
POST /api/v1/projects/{id}/revisions/{seq}/restore
```

- **一覧**は新しい順の見出しだけ（中身は含めない）。`entity_kind` / `entity_id` で
  「そのカットの履歴」に絞れる。409 で弾かれたらここを引き、次の `diff` で**人が何を
  変えたか**を読んでから書き直す。
- **差分**は直前のリビジョンとの項目単位の before / after。`updated_at` のような毎回
  動く列は落としてある。
- **復元**はボディ無しならプロジェクト丸ごと。`{"entity": "shot", "id": "sht_x",
  "fields": ["prompt"]}` のように送ると**その 1 件・その項目だけ**の部分復元になる。
  書き換える**前**の状態も 1 リビジョン残る（「復元前の自動スナップショット」）ので、
  復元そのものもやり直せる。消しすぎたカットや素材はこれで戻せる。
- **Take は「載っているものは戻す・知らないものは触らない」**: 生成そのものはリビジョンを
  作らないので、脚本を 1 つ戻しただけで直後に焼いた Take の目録が消えないようにしてある。
  採用（`selected_take_id`）はスナップショット側の値に戻り、新しい Take は候補として
  ぶら下がったまま残る。
- 履歴の深さは 1 プロジェクトあたり 1000 件（`app.studio.REVISION_LIMIT`）。超えたぶんは
  古いものから捨てる。
- 同じ 3 本は内部 API（`/api/studio/projects/{id}/revisions…`）にも出ている。

### 3.2 画面の操作（`ui/generate-form` / `ui/navigate`）

「エージェントがフォームを埋めて、人が確かめてから押す」「エージェントが人の画面を
目的の場所へ連れて行く」ための 2 本（SPEC §7.5）。

```
GET   /api/v1/ui/generate-form            → {"values": {…}, "revision": 7, "updated_by": "ui"}
PATCH /api/v1/ui/generate-form            {"values": {"duration": 5}, "base_revision": 7}
POST  /api/v1/ui/navigate                 {"view": "studio", "project_id": "prj_x", "shot_id": "sht_y"}
```

- `PATCH` は**送ったキーだけ**を書き換える（触れなかった項目は今のまま）。`base_revision`
  を付けるとその間に人が触っていた場合 409（本文に現在値が入るので取り直さずに済む）、
  未来の連番なら 400。省略すると強制上書き。
- 保存に成功すると WS（`type: "form"`）で開いているブラウザのフォームへ流し込まれる。
  人がフォームを触れば `PUT /api/ui/generate-form` で書き戻るので、双方向に同期する。
- そのまま投入したいときは `POST /api/v1/jobs` に `{"from_form": true}`。一緒に送った
  項目はその上から重ねる（「今のフォームで、尺だけ 5 秒にして流して」）。写せない下書き
  （ワークフロー id が壊れている等）は 400。
- `navigate` の行き先は `main`（生成）/ `studio` / `settings`。`project_id` / `shot_id` は
  `studio` のときだけ渡せ、実在と噛み合わせを確かめてから流す（存在しないものへ飛ばして
  画面を空にしないため）。ブラウザが 1 つも開いていなくても 204（誰も受け取らないだけ）。

### 3.3 Remotion

React で組んだ動画のレンダリングも、ふつうのジョブとして投げられる（SPEC §5.2）。

```
GET  /api/v1/remotion/compositions        → {"compositions": ["Opening", "Credits"]}
POST /api/v1/jobs  {"mode": "remotion", "remotion_composition": "Opening",
                    "remotion_props": {"title": "第3話"}}
```

- Remotion プロジェクトはリポジトリの `remotion/` に**同梱**されている。連携は
  設定 `remotion_enabled` が持ち、**既定は OFF**（Remotion が独自ライセンスのため）。
  無効のあいだは一覧も投入も 400。使うのは**常に同梱の `remotion/`**で、composition を
  足す・直すときは `remotion/src/` を編集する。
- 依存が入っていない（通常は `run.sh` が初回に入れる）ときも 400 で、その旨を返す。
- `remotion_props` の書き方は `.agents/skills/karakuri-remotion/SKILL.md` と
  `remotion/README.md`（正本は `remotion/src/schema.ts`）。
- 出来た mp4 は他のジョブと同じく `GET /api/v1/jobs/{id}` の `video_url` に出るので、
  ライブラリ登録・素材登録・タイムラインへの取り込みもそのまま使える。
- **音声はアプリ側で焼き直す**。Remotion の mp4 は音声が 2,048 サンプル
  （48kHz で約 42.67ms ≒ 1 フレーム）遅れるので、`remotion_props` の `audio.src`
  が同じマシンの `/outputs/…` などに解決できるときは、レンダリング後に
  **映像はコピーのまま音声だけ元音源から焼き直す**（`audio.startFrom` /
  `volume` / `fadeOut` も再現する）。焼き直せなかったときは元の mp4 のまま
  （ジョブは失敗しない）。

#### 演出はタイムラインに保存する（FX トラック）

`FxOverlay` の演出は、ジョブの props に置きっぱなしにせず**タイムラインへ保存する**
（SPEC §7.3）。そうすると編集画面の FX トラックに帯として並び、人がプレビューを見ながら
秒・位置を直したり要らないものを消したりできて、そのまま演出付きで書き出せる。
**ジョブへ props を直接投げるのは、手元で 1 本だけ確かめたいときの近道**。

```
GET  /api/v1/timelines/{id}/fx
  → {"timeline_id": "…", "theme": {…}, "seed": 1, "ambient": {…},
     "backgroundColor": "#000000",
     "events": [{"id": "…", "enabled": true, "event": {"type": "lyric", "t": 45.96, …}}]}

PUT  /api/v1/timelines/{id}/fx        # 全置換。FxOverlay の props をそのまま投げられる
  {"theme": {…}, "seed": 1, "ambient": {…}, "events": [{"type": "lyric", "t": 45.96, …}, …],
   "base_revision": 12}

POST   /api/v1/timelines/{id}/fx/events            {"event": {…}, "enabled": true}
PATCH  /api/v1/timelines/{id}/fx/events/{event_id} {"event": {"t": 46.5}, "enabled": false}
DELETE /api/v1/timelines/{id}/fx/events/{event_id}?base_revision=12
```

- `PUT` の `events` は**生のイベント**でも、`GET` が返す `{id, enabled, event}` の形でも
  受ける（`id` を省くと採番）。`base` / `audio` / `fps` / `width` / `height` /
  `durationInSeconds` は**タイムラインが持っている**ので、送られても無視する
- `PATCH` の `event` は**浅いマージ**（送った項目だけ上書き。`null` を送るとその項目が
  消える）。`enabled: false` は「消さずに外しておく」
- 検証は「`event` がオブジェクトで `type` が文字列・`t` が数値」まで。中身の正本は
  `remotion/src/schema.ts`（zod）なので、細かい誤りはプレビューとレンダで出る
- `base_revision` は他と同じ楽観ロック（§3 の楽観ロック）。演出も EDL と同じく
  リビジョンのスナップショットに載る
- **演出付き書き出し**: `POST /api/v1/timelines/{id}/export` に `{"fx": true}`。ffmpeg の
  mp4 が焼き上がったあと、それを下地に `FxOverlay` の Remotion ジョブが続けて走る。
  結果は書き出しの `fx_job_id` / `fx_status` / `fx_video_url` に出る（`GET /exports/{id}`
  をポーリングする）。Remotion 連携が無効なら 400

### 3.3.1 音源基準のタイムライン（MV のときだけ）

**通常のドラマ制作では使わない**（カットの並び順で十分）。音源に映像を合わせる制作
（MV・モーショングラフィックス）でだけ使う（SPEC §7.3「音源基準の配置」）。

```
POST  /api/v1/library/audio         multipart（file=@ban.wav）→ 棚の音源（A1 に置く）
POST  /api/v1/projects/{id}/timelines
      {"episode_id":"…","planned_end_seconds":193.48}
PATCH /api/v1/shots/{id}            {"planned_start_seconds": 16.6}
PATCH /api/v1/shots/{id}            {"timeline_role": "insert_only"}
POST  /api/v1/timelines/{id}/sync   {}
POST  /api/v1/timelines/{id}/clips/insert
      {"track_id":"…","start_ms":43900,"duration_ms":1500,
       "source_kind":"take","source_id":"…","in_ms":0}
POST  /api/v1/timelines/{id}/export {}
GET   /api/v1/exports/{id}          → {"fps":24,"width":1280,"height":720,
                                       "frames":4728,"duration_ms":197000,"warnings":[]}
GET   /api/v1/timelines/{id}/exports → 上の履歴（id を控え損ねたときの拾い先）
```

1. **音源は `POST /library/audio`（multipart）で棚に入れる**。タイムラインに置けるのは
   棚の音だけで、作品の素材（`assets`）に上げた音は素材ビンに出てこない。返った `id` を
   `PUT /clips` の `source_kind: "library"` / `source_id` に渡して A1 へ置く
2. カットに **音源上の開始秒**（`planned_start_seconds`）を書く。音源解析の結果
   （歌詞のアライン・onset・ビート）から出した秒で、**決め打ちしない**
3. `POST /timelines/{id}/sync` を 1 回。計画秒つきのカットはその位置に置かれ、素材が
   計画尺に届かないぶんは**前のカットの末尾静止で埋まる**（タイムラインの
   `gap_fill`。既定 `clone`。書き出しの `warnings` に `PAD …` が出る。`black` にすると
   今までどおり黒＋無音の `gap`）。**採用 Take を差し替えても同じ秒へ置き直される**。
   計画秒を持たないカットは今までどおり末尾へ詰む。`planned_start_seconds` に `null` を
   送れば並び順に戻る
4. 最後のカットは**音源の尺で締まる**（タイムラインの `planned_end_seconds` → 無ければ
   A1 の最初のクリップの終わり → それも無ければ Take の尺）。曲より長い尻尾を残さない
5. 差し込み専用のカット（決めポーズなど計画秒を持たないもの）は
   `PATCH /shots/{id}` で `timeline_role: "insert_only"` にする。自動配置にも
   `sync-preview` にも出てこなくなり、末尾へ押し出されない
6. 短いカットを割り込ませたいときは `clips/insert`。下のクリップが前後に割れるだけで
   **トラックの全長は変わらない**（`base_revision` を添えれば楽観ロック）。
   **差し込みは `sync` を済ませてから**（`sync` は並べ直しなので、順番が逆だと手間が増える）
7. `POST /timelines/{id}/export` → `GET /exports/{id}`。焼き上がりの
   `fps` / `width` / `height` / `frames` / `duration_ms` が返るので、そのまま Remotion の
   `FxOverlay` の `base`（`{"src": "<output_url>", …}`）と props の規格に使う。
   素材が足りずに末尾静止で埋めたところは `warnings` に `PAD <カット> <不足秒>s`、
   総フレーム数が計画とずれたときもここに出る

### 3.3.2 音源解析（MV のときだけ・指示があったときだけ）

演出の秒を決め打ちしないための材料を音源から出す（SPEC §5.2）。**ふつうのジョブ**
として投げ、成果物は JSON 1 つ。

```
POST /api/v1/jobs
  {"mode": "audio_analysis",
   "analysis": {"audio": {"item_id": "<ライブラリの音源>"},
                "lyrics": "今日も見張ってる 24時間 ログの海\n質問 挨拶 お世話 全部 わたしの仕事\n…",
                "stems": [{"item_id": "<ボーカルステム>"}],
                "tasks": ["align", "onsets", "beats", "silence"],
                "language": "ja",
                "align_substitutions": {"BAN!": "バン"},
                "model": "medium"}}

GET /api/v1/jobs/{id}   → {"status": "done",
                           "analysis_url": "/outputs/{id}/analysis.json"}
```

- `analysis.audio` は必須（`MediaRef`: `job_id` / `item_id` / `export_id` / `path` の
  どれか 1 つ）。ジョブの出力を指すときは**どの出力か**も書く
  （音声ジョブなら `{"job_id": "…", "source": "audio"}`。既定は `video`）。
  `stems` を渡すとアラインと onset はステムから採る（精度が上がる）
- `lyrics` があれば `align`（行と 1 文字ごとの秒）、無ければ `transcribe`（自由書き起こし）。
  `tasks` を省くと回せるものを全部回す
- `align_substitutions` は**アラインの前処理**。`BAN!` のような英字＋感嘆符は読みが
  当たりにくいので `{"BAN!": "バン"}` のように仮名へ直す（`？` `…` `「」` などの記号は
  既定で落ちる）。置換で当てた文字列が元の行と変わった行には `aligned_text` が付く
- `model` は `small`（既定）/ `medium` / `large-v2`。GPU が無ければ CPU で動く（遅い）
- **解析用の依存が入っていなければ 400**（何をどこに入れればよいかを本文で返す）。
  依存はアプリの環境ではなく専用の venv に入れ、設定 `audio_analysis_python` で指す
- `librosa` が無いときは `onsets` / `beats` だけ、ffmpeg が無いときは `silence` だけを
  飛ばして `warnings` に理由が入る（ジョブは成功する）

`analysis.json` の中身:

```jsonc
{ "duration": 193.48, "sample_rate": 48000,
  "lines":  [{ "i": 1, "start": 16.6, "end": 20.3, "text": "今日も見張ってる",
               "chars": [{ "c": "今", "s": 16.6, "e": 16.8 }] }],
  "onsets": [{ "t": 43.90, "strength": 0.9 }],
  "beats":  { "bpm": 116, "times": [0.0, 0.52] },
  "silence": [{ "start": 180.5, "end": 185.3 }],
  "sections": [],          // 手で書き足す欄
  "warnings": [] }
```

使い道はそのまま渡すだけ: `lines[].chars` → `FxOverlay` の `lyric.chars`、
`beats` → `MusicVideo.beats` / `FxOverlay` の `beatMarker`、`onsets` → 決めの演出の秒、
`lines[].start` → カットの `planned_start_seconds`（§3.3.1）。
**アラインの秒より実測 onset を優先する**（アラインは 100〜250ms 遅れることがある）。

### 3.4 素材の下ごしらえ（スプライト / フォント画像 / コンタクトシート）

演出用の素材を作るための 4 本。**MV・モーショングラフィックスを求められたとき、
または明示的に指示されたときだけ使う**（通常のドラマ制作では出番が無い）。

```
POST /api/v1/library/image          multipart（file / name / tags / category / nsfw）
POST /api/v1/library/audio          multipart（同上。MV の音源はここから入れる）
POST /api/v1/library/upload         multipart（kind は拡張子 / MIME で自動判定）
POST /api/v1/library/{id}/key       {"method":"black","tolerance":0.1,"trim":true}
POST /api/v1/library/key            {"source":{"path":"/assets/image/logo.png"},"method":"white"}
POST /api/v1/library/key-from-job   {"job_id":"…","source":"image","method":"black"}
GET  /api/v1/images/text/fonts      → {"fonts":[{"name":"Noto Sans CJK JP Bold",…}],"default":"…"}
POST /api/v1/images/text            {"text":"撃ち抜け","size":220,"outline":{"color":"#08080a","width":10}}
POST /api/v1/videos/contact-sheet   {"source":{"job_id":"…"},"seconds":[43.9,44.2],"columns":4}
```

- **手持ちのファイルの登録**（`library/image` / `library/audio` / `library/upload`）…
  multipart（`file`）で棚に入れる。`name`（空なら元のファイル名）/ `tags`（カンマ区切り）
  / `category` / `nsfw` をフォームで添えられる。**Docker で動かしているアプリにはホストの
  絶対パスは見えない**ので、手元のファイルを渡すときはこちらを使う
  （`scripts/studio.sh upload /library/audio file=@ban.wav`）。種別を書きたくない
  ときは `library/upload`（拡張子 → MIME の順で image / video / audio に振り分ける）。

- **透過キー**（`library/{id}/key`）… 棚の画像の背景を抜いて、**新しいライブラリ
  項目**（RGBA PNG、タグ `sprite`、`source: "sprite"`）にする。元の素材は触らない。
  `method` は
  - `black` / `white` … ルミナンスキー。**外側から floodfill** するので、文字や
    ロゴの**内側の同じ色は穴として残る**（縁取りの中が抜けない）。`tolerance`
    （0..1、既定 0.1）で明るさの閾値を動かす
  - `chroma` … `color`（既定 `#00ff00`）との距離で抜く。内側の同色も抜ける
  - `rembg` … 任意依存。入っていなければ **400** で入れ方を返す
    （`pip install -r backend/requirements-optional.txt`）
  - `trim`（既定 true）… 不透明な部分の bbox に切り詰める
  - `flatten`（既定なし）… 抜いたあとに残った部分を**その色一色**に塗る
    （`"#ffffff"` など。α はそのままなので、白抜きロゴが 1 手で作れる）
  応答の `url`（`/library/image/….png`）を Remotion の `sprite` / `imageSlam` /
  `stickerStack` の `src` にそのまま書ける。ジョブの生成画像を棚に入れずに
  直接抜きたいときは `library/key-from-job`（`source` は `image` / `last_frame`）。
  棚にもジョブにも無い画像——World Bible の素材や書き出し——は
  `POST /library/key` に `source`（`job_id` / `item_id` / `export_id` / `path` の
  どれか 1 つ。コンタクトシートと同じ `MediaRef`）を書く。
- **フォント画像**（`images/text`）… インストール済みの書体で文字を組んで
  RGBA PNG にする（タグ `text-image`、`source: "text"`）。`font` は
  `GET /images/text/fonts` の `name` をそのまま書く（省略すると Noto Sans CJK JP
  Bold 相当）。`size` / `color` / `outline{color,width}` / `bg`（`transparent`
  か色）/ `rotate` / `padding` / `align` と、本文の改行が使える。用途は 2 つ:
  そのままスプライトとして貼る／**画像生成の字形参照**として参照画像に添える
  （日本語が誤字になるモデルでも字形が直る）。
- **コンタクトシート**（`videos/contact-sheet`）… 動画のコマを 1 枚の jpg に
  束ねる（タグ `contact-sheet`）。`source` は `job_id`（+ `source`、既定
  `video`）/ `item_id` / `export_id` / `path`（`/outputs/…` の URL か置き場の中の
  絶対パス）の**どれか 1 つだけ**。抜く秒は `seconds` → `range{start,end,step}`
  → `frames`（fps が読めるときだけ）の順に見て、どれも無ければ尺を 12 等分した
  位置。`columns` / `width`（1 コマの幅）/ `labels`（秒とフレーム番号を焼く、
  既定 true）。応答は `{item, seconds, columns}` で、`seconds` に**実際に抜いた
  秒**が左上から順に並ぶ。**演出の配置（`cx` / `cy` / `w`）を触ったら必ずこれで
  確かめる。**

## 4. ファイルの受け渡し

- **読み**: API レスポンスの `url` 欄（`/outputs/...` / `/assets/...` /
  `/library/...`）をそのまま GET する。静的配信には認証が無いので、ループバック
  運用が前提（ネット越し公開時はプロキシで守る）。
- **書き（素材登録）**: 2 ルート。
  - multipart: `POST /api/v1/projects/{id}/assets` にファイル添付（内部 API と
    同じ受け口を流用）。ライブラリへ直接入れるなら `POST /api/v1/library/image`。
    **どちらの置き場でも、手元のファイルを渡すときはこちらが確実。**
  - JSON: 同 URL（`StudioAssetCreate`）の `path` に書いたパスから
    `assets/<kind>/` へ複製される。ここでいうパスは
    **アプリのプロセスから見えるパス**で、Docker で動かしているなら
    **コンテナの中のパス**。ホスト側の絶対パスを書いても見つからない
    （`compose.yml` でマウントしていない限り）ので、**Docker 運用では multipart
    を使うこと**。

## 5. デプロイ: Cloudflare 越しの公開

**アプリをそのまま公開してはいけない**: `/api/v1` 以外の内部 API は無認証で、
`PUT /api/settings`（保存済み API キーの読み書き）・`/docs`（全エンドポイントの
列挙と実行）・チャット API（ホスト上で LLM CLI を起動）・生成物の静的配信が
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
| `backend/app/routers/external.py` | ルーター本体。`APIRouter(prefix="/api/v1", dependencies=[Depends(require_external_key)])`。各ハンドラは `app.studio` / `app.jobs` / `app.timeline` / `app.library` / `app.ui_state` / `app.remotion` の既存関数を呼ぶだけ |
| `backend/app/models.py` | `Settings.external_api_key` / `external_max_pending_takes`。一括投入の `StoryCreate` / `StoryResult`、`UiFormState` / `UiFormUpdate` / `UiNavigate` などのモデル |
| `backend/app/studio.py` | 一括投入 `create_story()`、編集履歴（`_record_revision` / `diff_revision` / `restore_revision`、§3.1） |
| `backend/app/ui_state.py` | 生成フォームの下書きの共有（§3.2） |
| `backend/app/ws.py` | ブラウザへの配信（`studio` / `form` / `ui` フレーム） |
| `backend/app/remotion.py` | Remotion（同梱の `remotion/`）の composition 一覧とレンダリング（§3.3） |
| `backend/app/main.py` | `external.router` の include（1 行） |
| `frontend/`（設定画面） | `external_api_key` の入力欄（[生成] ボタン付き） |
| `.agents/skills/karakuri-studio/` | 外部エージェント向けの SKILL と curl ラッパー（`AGENTS.md` / `CLAUDE.md` からリンク） |
| `README.md` / `docs/SPEC.md` | 外部 API の節（有効化の手順と公開時の注意） |

## 8. 動作確認（2026-08-10 / ローカル ComfyUI + MiniMax H3）

ローカルの ComfyUI（MiniMax H3）に接続した実機で、当時のエンドポイントを `X-API-Key`
付きで通し、**素材登録からレンダリング・Take 採否まで一連の流れが動くことを確認
済み**です（**この記録は公開範囲を広げる前のもの**で、あとから足した汎用ジョブ・
ライブラリ・タイムライン・リビジョン・`ui/*`・Remotion はこの表には入っていません）。

| 範囲 | 状態 |
|---|---|
| 認証の 3 態（キー未設定 = 404 / キー無し = 401 / 正キー = 200） | 確認済み |
| プロジェクト（`GET` / `POST` / `GET {id}` / `PATCH {id}`） | 確認済み |
| 話・場（`POST .../episodes`・`PATCH /episodes/{id}`・`POST .../scenes`・`PATCH /scenes/{id}`） | 確認済み |
| カット（`POST .../shots`・`PATCH /shots/{id}`・`DELETE /shots/{id}`） | 確認済み。削除は 204、再削除で 404 |
| 一括投入 `POST /stories` | 確認済み。`render: true` で話 → 場 → カット作成からレンダリング投入まで 1 リクエスト |
| 素材登録 3 方式（JSON の `path` 複製 / multipart 添付 / `assets/from-job`） | 確認済み。`PATCH /assets/{id}` も含む。`path` はアプリのプロセスから見えるパス（Docker ならコンテナ内） |
| プロンプト中の `@素材名` 参照 | 確認済み。画像素材を参照したカットは自動で `minimax_h3_r2v` に切り替わり、`reference_images` に添付される |
| レンダリングと Take（`POST /shots/{id}/render` → `GET /shots/{id}/takes`・`GET /jobs/{id}` → `POST /takes/{id}/select` / `reject`） | 確認済み。864x480 / 5 秒 / h264 + aac 音声つきの動画が生成され、採用でカットが `done` に |
| 投入前の確認 `GET /shots/{id}/prompt-preview` | 実装済み。実際に投入されるプロンプト・ワークフローとその理由（`workflow_reason`）・`will_translate` を読み取りだけで返す（組み立てられないカットも 400 ではなく `error` 入りの 200） |
| 暴走ガードの 429（`external_max_pending_takes` 超過） | **未テスト**（実装のみ。生成 / 書き出しの 2 プールとも） |

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
  ただし**直前カットの採用 Take がまだ無いだけ**のときは、
  `GET /api/v1/shots/{id}/prompt-preview` と `POST /api/v1/shots/{id}/translate`
  は通ります（本文は `minimax_h3_r2v_context` の形で組み立てます）。前カットの
  完成を待たずに英訳しておけるようにするためで、プレビューはそのとき
  `render_blocker` に「まだ投入できない理由」を入れて返します（`error` は
  組み立てそのものができないときだけ）。投入（`render`）は今までどおり 400 です。
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
  焼く経路はアプリ側に無く、素材画像を作るのは**外部エージェント**（この API 経由の
  Claude Code / Codex / Cursor CLI など）なので、この設定は**外部エージェントへの
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
- JSON の `path` による素材登録は、**アプリのプロセスから見えるパス**を指します
  （Docker ならコンテナ内のパス）。ホストのパスを渡したいときは multipart で。

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
- プロジェクトの削除とライブラリ素材の削除（どちらも取り返しがつかないので保留）
- リクエスト回数のレート制限（投入キュー上限は §3 の暴走ガードとして v1 に含む）
