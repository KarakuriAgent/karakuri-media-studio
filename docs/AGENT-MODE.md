# エージェントモード 設計書（ドラフト v0.1）

ChatGPT Work / Claude Cowork を参考にした「同僚型エージェント」モード。
Grok がチャットから生成設定一式を組み立て、複数の動画ジョブを計画・実行し、
成果物を自分で確認して次の一手（再生成・続き生成など）まで回す。

既存の「生成」ビュー（フォーム + ビューア）と「Grok プロンプト作成」チャット（SPEC §4.3）は
**一切変更せず**、独立した第3のビューとして追加する。

---

## 1. 全体像

```
┌─────────────────────────────────────────────────────┐
│ ヘッダー [生成] [エージェント] [⚙]                     │
├──────┬──────────────────────────┬───────────────────┤
│ｾｯｼｮﾝ │ チャット                   │ 成果物パネル (8)    │
│一覧   │                          │ ┌───────────────┐ │
│      │ You: ダンス動画3本         │ │📋 プラン v2      │ │
│・ダンス│ Grok: トレンドを調べ       │ │🔍 リサーチまとめ  │ │
│・海辺 │ てから提案します…          │ │📝 企画メモ.md    │ │
│・新規+│ ┌📋 プラン────────┐       │ │🖼 ①生成画像      │ │
│      │ │☑1 ☑2 ⏳3 ☐4    │       │ │🎬 ①動画         │ │
│      │ │(実行中は上部固定) │       │ │🎞 ①フレーム検分  │ │
│      │ └─────────────────┘       │ │🎬 ②動画 ⏳45%   │ │
│      │ ✅ ①完了 シード当たり       │ └───────────────┘ │
│      │ ⚠ ﾁｪｯｸｲﾝ: ②照明変更OK?    │ クリックで拡大表示   │
│      │ [OK][そのまま]             │                   │
│      │ [入力欄]          [⏹停止] │                   │
└──────┴──────────────────────────┴───────────────────┘
```

3 カラム構成:

- **セッション一覧**（折りたたみ可）: 過去のエージェントセッション。会話 = 制作記録
- **チャット**: タスクリスト・チェックイン・短い作業ログが流れる。入力欄と ⏹ 停止ボタン
- **成果物パネル**: セッションの成果物・中間成果物を時系列カードで一覧
  （プラン / リサーチ / メモ / 生成画像 / 動画 / 音声 / フレーム検分結果）。
  音声カードは開くと `<audio>` プレイヤーで再生する（テキスト表示ではない）。
  NSFW が混ざるので**カードはサムネイルを出さない「リンクカード」**
  （アイコン + タイトル + 種別チップ + 時刻）。中身は開くまで見えない。
  タップで大型ビューア（刷新済み ResultPane の部品を流用）またはテキストビューアが開く。
  ビューアの自動オープンはしない（新着はバッジとパネルの自動展開だけで知らせる）。
  フレーム検分は job ごとに 1 枚の「🎞 … フレーム検分 (n枚)」カードにまとめ、
  開くとグリッド一覧 → 1 枚タップで全画面。
  カードのタイトルは Grok が付ける日本語の作品名（`rename` アクション、§4）。
  生成中のものは進捗 % 付き。狭幅時は全画面オーバーレイ

## 2. Work 流の実行制御

| 制御 | 本アプリでの意味 |
|---|---|
| プランモード | 実行前に必ずタスクリスト（何を何本・どの設定で）を提示。承認まで生成しない。プランは v1, v2… とバージョン管理 |
| アクション承認 | 「生成開始」「プラン外の追加生成」「ジョブ削除」は承認必須。設定の組み立て・リサーチ・検分は自律 |
| チェックイン | セッション単位で「毎ジョブ確認 / 節目のみ / 完了まで自走」を選択。自走時は上限本数必須 |
| 停止 | ⏹ を常時表示。実行中の ComfyUI ジョブは完了を待って中断（キャンセル API は現状なし） |

## 3. エージェントの能力

### 3.1 ジョブ設定 — 既存フォームと完全に同等

エージェントが指定できるジョブ定義は既存の `JobCreate` スキーマ **そのもの**とする。
つまり現行 UI で設定できる全項目が対象:

`mode`（full / i2v / image_only / audio）, **`image_workflow`**, **`video_workflow`**,
**`audio_workflow`**, `image_prompt`, `video_prompt`,
`negative_prompt`, `aspect_ratio`, `megapixels`,
`loras[]`（画像用 LoRA: lora_name / trigger_word / strength）, `trigger_text`,
`video_loras[]`（動画用 LoRA・同形式）, `video_trigger_text`, `duration`, `fps`,
`audio_path`, `source_image`, **`end_image`**, **`reference_video`**, `seed`（固定 or 抽選）、
**`model_overrides`**（実行ごとのモデル切り替え）、
および音声モード専用の **`audio_prompt`** / `lyrics` / `bpm` / `keyscale` / `language` /
`audio_category` / `reprompt`

LoRA は登録時の対象（SPEC §3.4）で振り分ける: 画像用は `loras`、動画用は `video_loras`。
システムプロンプトの CHOICES は両者を別見出しで列挙し、取り違えたプラン
（画像用を `video_loras` に入れる等）は検証エラーとして 1 回リトライさせる。
画像 LoRA には**モデルファミリー**があり、CHOICES には LoRA ごとのファミリーも出す。
選択した `image_workflow` と違うファミリーの LoRA を混ぜたプランは検証エラーになる。

#### ワークフローカタログ（単一情報源）

動画は 7 種、画像は 4 種、音声は 2 種のワークフローから選べる（SPEC §2.2〜§2.4）。
どれを選ぶかで必要な入力が変わるため、システムプロンプトには `app/workflows.py` の
`WorkflowSpec` から自動生成した **IMAGE WORKFLOWS / VIDEO WORKFLOWS / AUDIO WORKFLOWS
セクション**を焼き込む。動画 1 ワークフローぶんの内容:

- ワークフロー ID（`video_workflow` に書く値）と表示名、既定かどうか
- 用途（`description`）
- 必要入力（`requires` → `source_image` / `audio_path` / `end_image` / `reference_video`。
  ラベルはワークフローごとに違い、flf2v なら「最初のフレーム」、
  リファレンスシート IC-LoRA なら「リファレンスシート画像」）
- 音声の扱い（`audio_role`。音声入力を持たないワークフローは「モデルが生成する」と明記）
- モード別の必須フィールド（`models.missing_job_fields` / `video_workflow_problem` から
  生成するので、実際に 422 になる条件と常に一致する）。フル生成に使えないワークフローは
  「`mode: "full"` は使えない」と出す
- `video_prompt` の書き方（`prompt_hint`。flf2v は開始→終了フレームの遷移、
  ic_lora_motion はカメラ・テンポを書かない、ia2v はセリフを書かない など）

`description` / `prompt_hint` / `audio_role` の未記入は `validate_specs()`（ヘルスチェックと
テスト）が検出するので、ワークフローを追加したらプロンプト側の追記漏れは起きない。

画像カタログは各ワークフローのモデルファミリーと `image_prompt` の書き方（ファミリー別）を、
音声カタログは秒数の対応範囲とそのモデルが読むフィールド（`lyrics` / `bpm` / `audio_category` 等）を
同じ仕組みで出す。あわせて **IMAGE PROMPT GUIDES / AUDIO PROMPT SPEC**（モデル別の書き方、
公式ドキュメント準拠）も全種ぶん焼き込む。エージェントは 1 セッションで複数のモデルを
使い分けるため、チャット（SPEC §4.3）と違って選択中のものだけに絞らない。

検証は既存の JobCreate バリデーション（LoRA 実在チェック・全論理入力のアセット解決を含む）を
そのまま通す。加えて `agent_protocol.validate_job` はプラン検証の段階で
`missing_job_fields` を使ってワークフロー必須入力の不足を検出し、
「video_workflow `ltx2_3_flf2v` を mode 'i2v' で使うには end_image が必要です」のように
ワークフロー名込みで返す（実行時ではなく plan 時に弾く）。
不正ならフォーマットリマインダー付きで Grok に 1 回リトライ（§4.1 と同じ方式）。

利用可能な選択肢（LoRA 一覧・トリガーワード・アスペクト比・音声/画像/**動画**アセット・
ネガティブプリセット = `GET /api/options` 相当）はシステムプロンプトに焼き込み、
実在する値しか使えないようにする。

#### 使用モデルの切り替え（`model_overrides`）

設定ページの「モデル」タブで**候補リスト**を 2 件以上登録したモデルスロット（SPEC §3.3）は、
CHOICES の「使用モデルの切り替え」見出しにワークフローごとに列挙される:

```
- `krea2_turbo`（Krea 2 Turbo）:
  - `krea2_turbo/30:10.unet_name`（Load Diffusion Model、既定 `base.safetensors`）: `base.safetensors`, `alt.safetensors`
```

エージェントはジョブに `model_overrides: {"<キー>": "<ファイル名>"}` を付けることで、
その 1 回だけモデルを差し替えられる（同じ画で絵柄違いを比べる、ユーザーが特定チェックポイントを
指定した、等）。省略すれば設定の既定値が使われる。検証は Web UI と同じ
`models.model_override_problem` を通し、

- 不明なキー
- そのジョブが走らせないワークフローのキー（画像のみのジョブに動画スロット等）
- 候補リスト（既定値を含む）に無いファイル名

はプラン検証の段階で `ActionError` にしてフォーマットリマインダー付きで 1 回リトライさせる。
候補が何も登録されていない環境では CHOICES に「切り替え候補は登録されていません」と出し、
`model_overrides` を書かせない。`continue` アクションでも差分項目として指定できる。

#### ライブラリの素材（SPEC §7.2）

利用者が「取っておく」と決めた画像・動画・音声は**ライブラリ**に入っており、ジョブを削除しても
残る。CHOICES には種別ごとに（各 50 件まで、新しい順）`path`・表示名・タグ・NSFW 印を焼き込む:

```
Library（取っておいた素材、`path` をそのままジョブの入力に書けます）:
- image（source_image / end_image、全 62 件）:
  - `/…/library/image/pose_01H….png` — 「決めポーズ」 [キャラ, 立ち絵]
  - …ほか 12 件（ここに載るのは新しい 50 件だけ。`library_search` で辿れます）
- video（reference_video、全 0 件）:
  - (none)
- audio（audio_path、全 1 件）:
  - `/…/library/audio/bgm_01H….mp3` — 「テーマ曲」 [音楽]
```

エージェントはこの `path` を `source_image` / `end_image` / `reference_video` / `audio_path` に
そのまま書ける（`jobs.resolve_asset_path` が `assets/` と同じように受け付ける）。素材選びでは
アセット一覧より**ライブラリを優先**するようプロンプトで指示している（利用者が選んだものだから）。

**50 件の壁を作らない**: 焼き込むのは種別ごとの新しい 50 件だけなので、CHOICES には各種別の
**総件数**と「ここに出していない素材が N 件ある」ことを必ず併記し、`library_search` アクションで
全体を検索できると明示する。これがないと「ライブラリには 50 件しか無い」と誤解したまま話が進む。

`library_search` は名前・タグの部分一致（`q`）、タグの完全一致（`tag`）、種別（`kind`）で
ライブラリ全体を検索し、1 回 50 件（`agent_runner.LIBRARY_SEARCH_LIMIT`）を
`library_search_result` イベントとして返す。本文には該当総件数と「〜件目まで表示」が入り、
続きがあるときは次に投げる `offset` 付きの JSON をそのまま示す:

```
ライブラリ検索（q='サクラ'）: 62 件中 1〜50 件目。

- `/…/library/image/pose_01H….png` — 「決めポーズ」（image） [キャラ, 立ち絵]
…
まだ 12 件あります。続きは `{"action": "library_search", "offset": 50, …}`（同じ絞り込み条件のまま）で取得してください。
```

逆に、生成した中で後々使えそうなもの（良い開始フレーム、参照クリップ）は `library` アクションで
自分から棚に入れられる。`tags` を付けておくと後で `library_search` で見つけやすい。登録すると
`library_added` イベントが制作記録に残り、`path` がそのまま次のジョブに使える。

同じ出力を二度取っておこうとした場合はコピーを増やさず、`library_exists` イベントで
「既にライブラリにあります（名前: …、パス）」と返す（エラーではないのでリトライさせない）。
`tags` / `title` を書かなかった場合は、登録後にバックエンドが Grok へ別途問い合わせて**日本語の
短い表示名とタグ**を背景で付ける（SPEC §7.2 の自動生成。エージェント自身は何もしなくてよい）。

### 3.2 複数実行

プランは複数ジョブを含み、承認後に既存 JobRunner キューへ順次投入（1 本ずつ、SPEC §5 のまま）。
プランの長さは毎ジョブ確認 / 節目のみ確認では無制限（プラン承認とチェックインで
必ず人間が挟まるため）。自走モードだけ「1 回のプラン提案で増やせる**新規**ジョブ数」を
既定 5 に制限する（`agent_max_plan_tasks`、設定変更可）。プラン改訂は前のプランを
丸ごと置き換えるので、完了済みタスクを再掲した分は上限にカウントしない。

### 3.3 結果を見て次の一手を打つ（自律ループ）

ジョブの完了 / 失敗イベントは会話履歴に自動追記され、バックエンドが次の Grok ターンを
自動で回す。Grok は結果を踏まえて以下ができる:

- **フレーム検分**: 成果物の動画を ffmpeg でフレーム分解（例: 1 秒間隔 + ラストフレーム）して
  セッション workdir に展開し、Grok が画像として実際に見て品質判断
  （破綻・手の崩れ・シードの当たり外れ）。検分結果は成果物パネルに残る
- **続き生成**: 当たりの動画のラストフレームを開始画像に、次の動画ジョブを提案・実行
  （既存の continue API と同じ経路。i2v の連鎖で長尺化）
- **再生成**: 外れジョブのシード再抽選・プロンプト修正版の再実行
- **画像先行**: image_only で開始フレーム候補を量産 → 検分で選抜 → 当たりを i2v へ
  （SPEC §2 モード C の使い方をエージェントが自動で回す）
- **音声ジョブ**（`mode: "audio"`）は映像を持たないので `inspect` の対象外。完了イベントでも
  「音声は聴けないので判断はプロンプトと設定から」と明示し、フレーム検分を促さない

### 3.4 Grok CLI のエージェント能力の解放

Grok CLI はファイル操作・シェル・Web 検索ツールを持つエージェント型 CLI。
現行はプロンプト作成のみ（空 workdir で `-p` ワンショット）だが、エージェントモードでは
セッション専用 workdir 上でツール利用を許可して実行する:

| 能力 | 用途 | 成果物パネル |
|---|---|---|
| Web 検索 | トレンド・ロケーション・振り付け等のリサーチをプランに反映 | 🔍 リサーチまとめ |
| ファイル読み | 生成画像・ラストフレーム・検分フレームを見て品質判断 | 判断はチェックイン / 報告に反映 |
| ファイル書き | 企画メモ・プロンプト案・設定下書きを workdir に出力 | 📝 メモ類 |
| アプリ操作 | アクションプロトコル（§4）でジョブ投入等 | 🖼 🎬 生成物 |

実機確認済み（grok 0.2.112）: ヘッドレス `-p` 実行に `--permission-mode auto` を
付けると、ファイル読み書き（画像の閲覧を含む）と Web 検索が動作する。
これを `agent_grok_args` の既定値とし、フラグを知らない古い CLI では素の `-p`
実行（検索・ファイル操作なし、アプリ操作のみ）に自動フォールバックする。
タイムアウトは現行 120 秒から延長（リサーチ・検分ターンは 300 秒）。

## 4. アクションプロトコル

Grok CLI はステートレスなテキスト入出力なので、ツール呼び出しは JSON アクションとして自前定義する
（既存 `grok.extract_result` の拡張。```json フェンス優先、brace フォールバックも同様）。
返答は「会話文」または「会話文 + 1 アクション」。

```json
{
  "action": "plan",
  "notes": "雰囲気違いの 3 本を提案します",
  "tasks": [
    {
      "label": "① 明るいスタジオ",
      "job": {
        "mode": "full",
        "image_workflow": "krea2_turbo",
        "video_workflow": "ltx2_3_id_lora",
        "image_prompt": "...", "video_prompt": "...",
        "negative_prompt": "...",
        "aspect_ratio": "9:16", "megapixels": 1.0,
        "loras": [{"lora_name": "kaori.safetensors", "trigger_word": "kaori", "strength": 0.8}],
        "trigger_text": "kaori",
        "video_loras": [{"lora_name": "motion.safetensors", "trigger_word": "smooth motion", "strength": 1.0}],
        "video_trigger_text": "smooth motion",
        "duration": 5, "fps": 24,
        "audio_path": "/assets/audio/reference.mp3",
        "source_image": null, "end_image": null, "reference_video": null,
        "seed": null
      }
    },
    {
      "label": "② 主題歌デモ（音声のみ）",
      "job": {
        "mode": "audio",
        "audio_workflow": "ace_step1_5_xl_sft",
        "audio_prompt": "...", "lyrics": "[Verse 1]\n...",
        "bpm": 92, "keyscale": "F# minor", "language": "ja",
        "duration": 120, "seed": null
      }
    }
  ]
}
```

音声タスクは画像・動画のフィールド（`video_prompt` / `source_image` / `loras` 等）を持たない。
書くと「音声は独立ジョブ」という検証エラーになり、1 回リトライさせる。

| action | 内容 | 承認 |
|---|---|---|
| `plan` | タスクリスト提案（新規 or 改訂版）。`job` は JobCreate と同一スキーマ（`video_workflow` 込み） | 実行開始に必要 |
| `run_task` | 承認済みプラン内タスクの実行 / 自走モードでの実行 | プラン承認で包括承認 |
| `continue` | 既存ジョブのラストフレームから続き生成（対象 job_id + `JobContinue` の差分項目 = `video_workflow` / `video_prompt` / `negative_prompt` / `aspect_ratio` / `megapixels` / `duration` / `fps` / `audio_path` / `end_image` / `reference_video` / `seed`） | プラン外なら必要 |
| `rerun` | シード再抽選 / 修正版で再実行（対象 job_id + 差分） | プラン外なら必要 |
| `inspect` | 動画のフレーム分解検分を依頼（対象 job_id, 間隔秒）。バックエンドが ffmpeg で展開し、次ターンで Grok がフレームを見る | 不要（自律） |
| `note` | メモ / リサーチまとめの成果物登録（workdir ファイル or 本文） | 不要（自律） |
| `rename` | 既存成果物のタイトル付け直し（`name` または `job_id` + `kind`, `title`）。対象が無ければ `action_failed` | 不要（自律） |
| `library` | ジョブの出力をライブラリに取っておく（`job_id` + `source`: `image` / `last_frame` / `video` / `audio`、任意の `title` / `tags[]`）。SPEC §7.2。既に同じ出力が登録済みなら `library_exists`（エラーではなく案内）、対象が無ければ `action_failed` | 不要（自律） |
| `library_search` | ライブラリ**全体**を絞り込む（`q` = 名前・タグの部分一致 / `tag` = 完全一致 / `kind` / `offset`）。結果は `library_search_result` イベントとして次ターンに届く | 不要（自律） |
| `checkin` | ユーザーへの確認（選択肢ボタン付き吹き出し）。応答まで次タスク保留 | ― |
| `done` | プラン完了宣言 → 納品サマリ | ― |

`continue` はラストフレームを開始フレームに使うので、`video_workflow` の切り替え先は
**開始フレームを受け取れるワークフロー**（カタログで `mode: "full"` が使えると出ているもの）
だけ。それ以外を指定した場合はアプリが既定ワークフローに戻す。切り替え先が要求する
追加入力（flf2v の `end_image` 等）は同じアクションで渡す必要がある。

1 返信 1 アクションの制約は `rename` / `library` / `library_search` も同じ（`plan` / `checkin` 等と同列）。
生成を伴わない即時アクション（`plan` / `checkin` / `done` / `note` / `rename` / `library` /
`library_search`）は
発言リクエストの中で処理し、実行系（`run_task` / `continue` / `rerun` / `inspect`）だけを
バックグラウンドループに委ねる（`routers/agent._dispatch`）。
タスクの `label` と `note` / `rename` のタイトルは、ユーザーがひと目で分かる
日本語の作品名（例:「夕暮れ屋上ダンス・引きカメラ」）にすることをシステムプロンプトで
指示している。ファイル名的な文字列・ID・シード値はタイトルに使わせない。

会話履歴にはユーザー / Grok の発言に加え、システムイベント
（`task_started` / `progress` / `task_done` / `task_failed` / `inspect_result` 等）を
型付きメッセージとして追記し、毎ターン全量を再送する（現行チャットと同じステートレス方式）。

## 5. バックエンド

### 5.1 API（`/api/agent`）

| エンドポイント | 内容 |
|---|---|
| `POST /api/agent/sessions` | セッション開始（チェックイン設定・自走上限を受け取り、options を焼き込んだシステムプロンプト生成） |
| `GET /api/agent/sessions` / `GET .../{id}` | 一覧 / 詳細（メッセージ・タスク状態・成果物インデックス） |
| `POST .../{id}/messages` | ユーザー発言 → Grok ターン → アクション解釈まで（チェックイン待ちのあいだは「チェックインへの自由回答」として `checkin` と同じ経路に流す。実行ループ中は 409。`attachments` の workdir 相対パスは発言本文の末尾に列挙して渡す。本文が空でも添付だけで送信できる） |
| `POST .../{id}/approve` | プラン承認（承認後バックエンドがタスク実行ループを開始。実行中・チェックイン待ちは 409） |
| `POST .../{id}/checkin` | チェックインへの応答 |
| `POST .../{id}/stop` | 実行ループ停止 |
| `POST .../{id}/attachments` | ファイル添付（workdir の `attachments/` に保存し、`{name, path}` を返す。拡張子は画像・音声・動画とテキスト系のみ） |
| `GET .../{id}/artifacts/{name}` | workdir 内ファイル（メモ・検分フレーム等）の配信 |
| `DELETE .../{id}` | セッション削除（workdir ごと） |

進捗は既存 WebSocket を拡張（`type: "agent"` フレームでタスク状態・新着成果物を通知。
ジョブ進捗は既存 `type: "job"` をそのまま利用）。

Grok ターンの実行中は `thinking` で通知する（WS フレームの `thinking: true/false` と
`GET .../{id}` の `thinking`）。バックエンドのインメモリ状態で DB には保存しない。
ブラウザ発の呼び出しに限らずループが回すターンでも立つので、フロントの
「Grok が考えています…」はこれを唯一の情報源にする（WS の取りこぼしはポーリングで拾う）。

### 5.2 データ

- `agent_sessions` テーブル: `id, created_at, title, status(idle|planning|running|waiting_checkin|stopped|done),
  checkin_mode, auto_limit, messages(JSON), plan(JSON: tasks + 状態), artifacts(JSON)`
- ジョブとの紐付けは既存 `jobs.chat_session_id` を流用（エージェントセッション ID を格納）
- セッション workdir: `runtime/agent-sessions/<id>/`（Grok の作業場 + 検分フレーム + メモ。
  生成画像 / ラストフレームは outputs からコピーして見せる）

### 5.3 実行ループ

```
ユーザー発言 ─→ Grok ターン ─→ action?
                    │ plan      → プランカード提示（承認待ち）
   承認 ──────────→ │ run_task  → JobRunner 投入 → 完了イベント追記 ─┐
                    │ inspect   → ffmpeg 展開 → 結果イベント追記 ──┤→ 次の Grok ターンを自動実行
                    │ checkin   → 吹き出し提示（応答待ち）           │   （チェックイン設定と上限本数で制御）
                    │ done      → 納品サマリ → ループ終了 ←─────────┘
```

暴走防止: 自走時の連続 Grok ターン数・セッションの生成本数（`auto_limit`）・
1 回のプラン提案で増やせる新規ジョブ数（`agent_max_plan_tasks`、自走モードのみ）に上限。
失敗の同一タスク自動リトライは 1 回まで。
生成本数の上限は打ち切りではなく**続行確認**: 次の 1 本が上限を超える時点で
`kind = "limit"` のチェックインを出し、承認されたら `auto_limit` 本ぶん枠を伸ばして
続行、断られたら停止する（枠は承認済みチェックインの本数から算出するので DB 変更なし）。

## 6. フロントエンド

- `AgentView.tsx` 新設（ヘッダーにビュー切替を追加: main / agent / settings）
- 構成部品: `SessionList` / `AgentChat`（タスクリストカード・チェックイン吹き出し・
  作業ログ行・入力欄 + 停止）/ `ArtifactPanel`（成果物カード一覧 + ビューアオーバーレイ。
  動画 / 画像ビューアは ResultPane の部品を抽出して共用、音声は `<audio>` プレイヤー）
- **ファイル添付**: `AgentChat` の入力欄と `SessionList` の新規セッションフォームの両方に
  📎 ボタンを置く（チップ表示・拡張子検査・画像はサムネイル）。既存セッションでは即
  `POST .../attachments` でアップロードし、セッション作成前は `File` のまま持って
  作成直後にアップロードする。チェックイン待ちの回答にも添付できる（承認判定は本文のみを見る）
- 既存の GenerateForm / ResultPane / HistoryGallery / ChatModal / SettingsPage は変更しない

## 7. ガードレール

- 自走モードのみ 1 回のプラン提案で新規 5 ジョブまで（設定可。完了済みの再掲は除く）、
  さらに自走モードは上限本数（`auto_limit`）必須。生成本数の上限自体はどのチェックイン
  モードでも効き、達したら打ち切りではなく続行確認のチェックインを出す（§5.3）。
  プランのタスクだけでなく `continue` / `rerun` も同じ枠で数える
- 生成開始・プラン外アクション・削除は承認必須。実行前にプランカードで全設定が見える
- 不正 JSON・実在しない LoRA / アセット指定は自動リトライ + ユーザーにも可視化
- Grok CLI のシェル実行はセッション workdir 内に限定する設定で起動（CLI 側の許可設定に従う）

## 8. 実装ステップ

1. バックエンド基盤: アクションプロトコル・システムプロンプト・`/api/agent`・`agent_sessions`・workdir 管理
2. 実行ループ: 承認 → JobRunner 投入 → イベント追記 → 自動ターン、inspect（ffmpeg）、checkin、stop
3. フロント: AgentView（3 カラム + 成果物ビューア）
4. Grok CLI ツール許可の実機確認と組み込み（検索・ファイル操作）、自走モード仕上げ

---

未確定事項（実装時に判断）: 検分フレームの既定間隔（暫定 1 秒）/
セッションタイトルの自動命名（現状: goal 冒頭 40 文字）。
~~Grok CLI のツール許可フラグ~~ → 実機確認済み、§3.4 参照。
