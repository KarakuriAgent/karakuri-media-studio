# Video Studio 仕様書（ドラフト v0.1）

ComfyUI 上の `video-gen.json` ワークフロー（画像生成 → i2v 動画生成）をバックエンドとして使う動画生成アプリの仕様。
プロンプト作成は Grok（サブスクリプション認証）に委譲し、実行・成果物管理・履歴保存を本アプリが担う。

---

## 1. 全体像

```
┌──────────┐   入力値(シーン概要/設定)   ┌───────────────┐
│  Web UI  │ ─────────────────────────→ │ バックエンド API │
└──────────┘                            └──────┬────────┘
     ↑ 進捗(WebSocket) / 成果物                  │
     │                              ┌──────────┼──────────────┐
     │                              ↓          ↓              ↓
     │                        grok CLI(公式)   ComfyUI クライアント  SQLite + ファイルストア
     │                        (プロンプト生成)   (ワークフロー実行)     (履歴・成果物)
     │                                         │
     └─────────────────────────────────────────┘
```

処理フロー（標準モード）:

1. ユーザーが UI で**画像・動画プロンプトを手動入力**（基本フロー）、または「Grokで生成」ボタンから**チャット画面**へ。チャットでは Grok が「何を作りたいか」を対話形式でヒアリングし、確定したプロンプト案をフォームに反映する（§4.3）
2. 生成パラメータ（アスペクト比、LoRA、秒数など）を設定し「実行」
3. `video-gen.json` をテンプレートとしてパラメータを注入し、ComfyUI `/prompt` API に投入
4. WebSocket で進捗を UI に中継
5. 完了後、**生成画像・動画**を ComfyUI から取得、**ラストフレーム**を ffmpeg で抽出
6. プロンプト・パラメータ・チャット履歴・成果物パスを SQLite に保存し、UI のギャラリー/履歴に表示

---

## 2. 動作モード

| モード | スタートフレーム | 実行されるサブグラフ |
|---|---|---|
| A: フル生成 (t2i → i2v) | ワークフロー内で画像生成 | 画像生成 (`365:*`) + 動画生成 (`433:*`) |
| B: 画像読み込み (i2v) | アップロードした画像 / 過去生成のラストフレーム | `435` (LoadImage) + 動画生成 (`433:*`) |
| C: 画像のみ生成 | ― | 画像生成 (`365:*`) のみ。スタートフレーム候補を量産して確認し、良いものをモード B に渡す使い方を想定 |

### モード B の配線変更

ワークフロー上は `435` (LoadImage) が未配線。API 投入用 JSON をモードに応じてアプリ側で書き換える:

- **モード A**: 現状どおり `433:431` (ResizeImageMaskNode) の `input` = `["365:8", 0]`（生成画像の VAEDecode 出力）。**未使用の `435` (LoadImage) は JSON から削除**（ComfyUI は投入 JSON 内の全ノードを検証するため、存在しないファイル名を持つ孤立 LoadImage は投入エラーになる）
- **モード B**: `433:431` の `input` を `["435", 0]` に付け替え、画像生成系ノード（`365:*` 全部と `393` PreviewImage。ただし `366` は動画解像度で共用のため残す）を JSON から削除。アップロード画像は ComfyUI `/upload/image` で input ディレクトリに送り、`435.inputs.image` にファイル名を設定
- **モード C**: `433:*`, `75`, `432`, `435` を削除し、`393` (PreviewImage) を SaveImage（`filename_prefix = images/{job_id}`）に差し替えて画像を出力として確定させる

補足: モード B では動画解像度は `366` (ResolutionSelector) の値を使う（読み込み画像は `433:431` で指定解像度に center crop リサイズされ、`433:417` で長辺 1536 に再リサイズ後 `LTXVPreprocess` に渡る）。

- ラストフレーム連鎖: 履歴の動画から「ラストフレームを開始フレームにして続きを生成」できる（モード B の入力にラストフレーム画像を渡すだけ）。

---

## 3. ワークフロー解析とパラメータ注入ポイント

`video-gen.json` は API フォーマット。書き換え対象ノードは以下。

### 3.1 ユーザーが UI から設定する項目

| 項目 | ノード / フィールド | 現在値 | UI |
|---|---|---|---|
| アスペクト比 | `366.inputs.aspect_ratio` | `4:3 (Standard)` | セレクト（選択肢は `/object_info` の ResolutionSelector 定義から動的取得） |
| メガピクセル | `366.inputs.megapixels` | `1` | 数値（0.25〜2 目安） |
| LoRA（複数可） | `365:15` を起点に動的生成（§3.4） | ―（登録リストは空で開始） | アプリ内 LoRA 登録リストから複数選択。各 LoRA に強度スライダー |
| LoRA トリガーワード | `365:27.inputs.string_a` | （プロンプト先頭に連結される文字列） | 選択した LoRA のトリガーワードを自動連結（編集可）。トリガーワードは LoRA 登録リストで管理 |
| リファレンス音声 | `432.inputs.audio` | mp3 ファイル名 | ファイルアップロード（`/upload/image` で送信 → ファイル名を注入） |
| 秒数 (Duration) | `433:331.inputs.value` | `10` | 数値・**上限なし**（フレーム数 = 秒数 × fps + 1 は `433:329` が計算。長尺は VRAM 次第で ComfyUI 側エラーになり得ることを UI に注記） |
| フレームレート | `433:422.inputs.value` | `25` | 数値（既定 25、通常は固定でよい） |
| 開始フレーム画像（モード B） | `435.inputs.image` | ― | 画像アップロード or 履歴から選択 |
| 画像・動画プロンプト | `365:19` / `433:430` | ― | テキストエリア（手動入力が基本。Grok チャット §4.3 の結果を反映して編集も可） |
| 動画ネガティブ | `433:413.inputs.text` | `pc game, …` | プリセット切替（現行値 / モデル作者版）+ 直接編集可 |

### 3.2 アプリが自動注入する項目

| 項目 | ノード / フィールド | 方針 |
|---|---|---|
| 画像プロンプト | `365:19.inputs.value` | フォームの確定値（手動 or Grok チャット反映後） |
| 動画プロンプト | `433:430.inputs.value` | フォームの確定値（同上） |
| 画像 seed | `365:3.inputs.seed` | 実行毎にランダム（固定オプションあり） |
| 動画 noise seed | `433:394` / `433:395.inputs.noise_seed` | 実行毎にランダム（固定オプションあり） |
| 出力プレフィックス | `75.inputs.filename_prefix` | `video/{job_id}` にして成果物とジョブを紐付け |
| ローカル LLM リファイン | `365:24.inputs.value` | **false 固定**（プロンプト整形は Grok が担うため、ComfyUI 内蔵の TextGenerate リファインは使わない。UI に隠しオプションとして残す）。実装時に ComfySwitchNode が遅延評価か確認し、false でも `365:16` (TextGenerate) が実行される場合は `365:16`〜`365:18` をノードごと削除して `365:21` の `on_true` を `365:19` に付け替える |

### 3.3 固定（触らない）ノード

- 画像側: UNET `redcraft23INT8INT4FP8_30Krea2` / CLIP `qwen3vl_4b` / VAE `qwen_image_vae`、KSampler 設定（euler / simple / 8 steps / CFG 1 — RedCraft Krea2 版の推奨値そのまま）
- 動画側: checkpoint `sexgodPinkcherryLTX23_v16bDev`、distil LoRA (strength 0.5)、talkvid ID-LoRA + `LTXVReferenceAudio`（identity_guidance_scale 3）、2 段サンプリング（半解像度 → LatentUpsampler x2）、ManualSigmas
- 動画ネガティブ `433:413` は既定固定だが、プリセット切替可能にする（現行値 / モデル作者版。`docs/prompt-samples.md` 参照）
- `365:391` (ImpactWildcardEncode) は現状どこにも接続されていない孤立ノード → API 投入時に削除する

### 3.4 複数 LoRA の動的注入

人物 LoRA を複数同時に適用できるよう、`365:15`（LoraLoaderModelOnly 1 個固定）は使わず、アプリが API JSON 生成時に **LoRA チェーンを動的に構築**する:

```
365:10 (UNETLoader)
  → lora_0 (LoraLoaderModelOnly: 1個目, strength_model=各LoRAの強度)
  → lora_1 (2個目)
  → … 選択数ぶん連結
  → 365:22 (Switch) の on_true 入力へ
```

- ノード ID はアプリが採番（`app_lora_0`, `app_lora_1`, …）。`365:15` はテンプレートから削除
- LoRA 0 件選択時は `365:23` (Enable LoRA?) を `false` にする（1 件以上で `true`）
- トリガーワードは選択 LoRA の trigger_word を `", "` で連結（UI で編集可）し、`365:27`（StringConcatenate）で**画像プロンプトの先頭**に付与する: `string_a` = トリガーワード（リテラル）、`string_b` = プロンプトのリンク、`delimiter` = `", "`
  - Grok がプロンプト本文中で既にトリガーワードを使っている場合は重複を避けるため、カンマ区切りの語単位で（大文字小文字無視・単語境界一致）未使用の語だけを付与する
  - 付与すべき語が無い場合は `string_a` と `delimiter` を空にして素通し（先頭に `", "` が残らないようにする）
- 注意: 動画側（LTX）は別モデルのため画像側にのみ適用される。人物の同一性は生成画像経由で動画に引き継がれる

---

## 4. Grok 連携

### 4.1 認証・呼び出し方式: Grok Build CLI（公式）

Cookie ベースの非公式 API は規約リスクがあるため**使わない**。xAI 公式の **Grok Build CLI**（`grok` コマンド）をサブプロセスとして呼び出す。

- インストール: `curl -fsSL https://x.ai/cli/install.sh | bash`
- 認証: 初回 `grok` 起動時のブラウザサインイン（**SuperGrok / X Premium+ サブスクリプションで利用可**、API キー不要）。認証情報は CLI がローカルに保持するのでアプリ側で秘匿情報を扱わない
- 呼び出し: ヘッドレスモード `grok -p "<プロンプト>"`（`--single` 同等）を subprocess で実行し、標準出力を取得
- 出力の安定化: システムプロンプトで「コードブロック内の JSON のみを出力」と指示し、アプリ側は正規表現で最初の JSON ブロックを抽出してパース。パース失敗時は 1 回リトライ
- 実行ディレクトリ: CLI はコーディングエージェントでありファイル操作能力を持つため、**空の専用ディレクトリ（例: `runtime/grok-workdir/`）を cwd にして起動**し、プロジェクトに触れさせない
- 起動時ヘルスチェック: `grok --version` の成否と、認証切れ（初回サインイン未実施）をエラーメッセージから検出して UI に表示
- フォールバック: LLM クライアントはインターフェースを抽象化し、`XAI_API_KEY` による公式 API 直叩きにも切り替え可能な構造にしておく（既定は CLI）

### 4.2 プロンプト生成の仕様

プロンプト作成は**手動が基本**。Grok を使う場合はチャット形式（§4.3）で要件を掘り下げ、最終的に JSON（`image_prompt`, `video_prompt`, `notes`）を出力させてフォームに反映する。システムプロンプトに各モデルのプロンプト仕様を埋め込む。

**実例集**: Civitai の公開ギャラリー（モデル作者投稿の動画・画像）に埋め込まれたワークフローから実際のプロンプトを抽出し、`docs/prompt-samples.md` にまとめた。Grok のシステムプロンプトには、この実例を few-shot として埋め込むこと。実例から得られた重要な知見:

- 動画プロンプトは `<シーン種別> scene.` の宣言で始めるのが作者流（例: "voyeur style ... scene."）
- **引用符 `"..."` で囲んだセリフはそのまま音声合成される**（英語のセリフ+話者の声質形容: "in a british voice she says …"）。セリフ機能を UI のオプションとして扱う
- 音・声の描写（moaning, sigh, 効果音）を文中に散りばめる
- 作者が実際に使うネガティブは品質系+音声系（blurry, …, distorted sound, saturated loud 等）で、現行 `video-gen.json` の値と異なる。**アプリからネガティブも選択可能にする**（既定は現行値、プリセットで作者版を用意）
- RedCraft 画像プロンプトは品質語プレフィックス（例: "masterpiece, very aesthetic"）+ 自然文 1 段落が実例でも主流

**画像プロンプト（RedCraft 赤佬3.0 / Krea 2 ベース、TE は Qwen3-VL 4B）**

- Krea 2 公式ガイド（krea-ai/krea-2 `docs/prompting.md`）準拠: **自然文 1 段落・長く詳細なほど良い**。画像内に文字を描画する場合は対象語を引用符で囲む
- Grok 用システムプロンプトは Krea 2 公式の LLM 拡張プロンプト（`docs/expansion.txt` — video-gen.json のノード `365:18` に同一物が組込済み）をベースに、本アプリの用途・LoRA トリガー・出力 JSON 形式に合わせて調整する
- 構成順序（ワークフロー内の既存プロンプトをテンプレートとして踏襲）:
  1. LoRA トリガーワード（LoRA 有効時。Grok には表示名→トリガーワードの対応表を渡し、`image_prompt` の被写体名としてトリガーワードを文中で使わせる。未使用の語だけをアプリが `365:27` で先頭に補完する）
  2. 媒体・様式の宣言（例: "a single still frame from …" のようなスタイル定義）
  3. 被写体・ポーズ・構図の具体描写
  4. 表情・感情のディテール
  5. 照明・雰囲気・肌質などの質感記述
  6. カメラ（ショット種別、被写界深度）、"high detail" 等の品質語
- 推論設定は Steps 8 / CFG 1 / Euler / Simple（モデル配布ページの推奨値、ワークフロー側で固定済み）
- ネガティブプロンプトは不使用（ConditioningZeroOut で代替済み）

**動画プロンプト（SexGod PinkCherry LTX 2.3 / i2v、TE は Gemma-3 12B）**

- LTX 2.3 公式ガイド準拠: **1 つの流れる段落・4〜8 文**。含める要素は「被写体 / 動作 / 環境 / 照明 / カメラの動き / 音声」。i2v では「開始フレームからの続き」を書く（例: "Starting from the given first frame, …"）
- 含めるべき要素:
  1. 被写体と状況の要約（開始フレームと矛盾しないこと）
  2. **動きの推移**（何がどう動くか、テンポ・強度の変化）
  3. 身体・表情のリアクション描写
  4. カメラワーク（static / handheld tremble / ショットスケール / focus 対象など）
  5. **音声の記述**（LTX 2.3 は音声も同時生成。環境音・呼吸・声を必ず文中に含める。**セリフは引用符で囲み、言語・アクセント・声質を形容できる**）
- **リファレンス音声 + ID-LoRA（talkvid）は口の形とタイミングを駆動する（リップシンク）**。comfy.org のワークフロー解説は `[VISUAL]` / `[SPEECH]` / `[SOUNDS]` のタグ形式プロンプトを推奨しており、PinkCherry 作者実例の自然文形式と合わせて**2 種のテンプレートを UI で切替可能にする**（既定: 自然文）
- 継続時間は秒数設定に従う。1 カット（continuous shot）前提で書く
- ネガティブプロンプトは Grok に生成させず、プリセット選択制（§3.1: 現行値 / モデル作者版、編集可）
- モデルが学習済みの動作カテゴリ（配布ページの trained actions リスト）を Grok のシステムプロンプトに語彙リストとして与え、それに寄せた表現を優先させる

### 4.3 チャット型プロンプト作成フロー

「かおりが楽しそうにダンスをしている」のような雑な指示から Grok が勝手に決め打ちで生成してしまうのを防ぐため、**インタビュー形式のチャット UI** を設ける。

フロー:

1. フォームの「Grokで生成」ボタン → チャットパネル（モーダル）を開く。フォームの現在値（モード、選択 LoRA とトリガーワード、秒数、既存プロンプト下書き）がコンテキストとして自動で渡る
2. ユーザーが作りたいものをひとこと入力（例: 「かおりが楽しそうにダンスをしている」）
3. Grok は**不足情報を質問で聞き返す**よう指示されている: 場所・服装・時間帯/照明・カメラ（ショットスケール/動き）・表情/ムード・セリフや音・動きの展開など。ユーザーが「おまかせ」と言えば残りは Grok が補完
4. 情報が揃ったら Grok が `image_prompt` / `video_prompt` の最終案を JSON で提示 → 「フォームに反映」ボタンでプロンプト欄へ挿入
5. 反映後もチャットを続けて再調整可能（「もっと引きのカメラで」等 → 更新版 JSON を再提示）

実装:

- grok CLI のヘッドレス実行（`grok -p`）は 1 発呼び出しのため、**会話履歴はアプリ側で保持**し、毎ターン「システムプロンプト + 履歴全文 + 最新発言」を組み立てて渡す
- システムプロンプトの構成: ①役割（プロンプトエンジニア兼インタビュアー）②各モデルのプロンプト仕様（§4.2）+ few-shot 実例（docs/prompt-samples.md）③ヒアリング項目チェックリスト ④最終出力は ```json フェンス内の `{image_prompt, video_prompt, notes}` のみ、というルール
- 応答の判定: 応答に JSON フェンスがあれば「最終案の提示」、なければ「質問継続」として UI に表示
- 十分詳細な初回入力なら Grok は質問を飛ばして即 JSON を返してよい（ワンショット生成はチャットの特殊ケースとして自然に実現）
- モード B ではスタートフレーム画像を grok 作業ディレクトリにコピーし、CLI に読ませて内容を踏まえた `video_prompt` を作らせる（読めない場合はテキストのみでフォールバック）
- チャット履歴は `chat_sessions` に保存し、ジョブに紐付ける（後から「どういう指示で作ったか」を追える）

---

## 5. ComfyUI 連携

- 接続先: `http://<comfy-host>:8188`（設定画面で URL 変更可）。実行環境はローカル / LAN 上の別 PC / Comfy Cloud のいずれでも動くよう、ComfyUI クライアントは「接続 URL + 任意の認証ヘッダー（API キー）」を設定できる抽象化された 1 モジュールにする
- **Comfy Cloud**: `video-gen.json` は Comfy Cloud 上で動作確認済み（custom nodes・使用モデルとも問題なし）。実装時は Cloud 向けのエンドポイント URL と認証設定を設定画面から入力できるようにする
- 使用 API:
  - `GET /object_info` … ResolutionSelector のアスペクト比選択肢、LoRA 一覧、LoadAudio/LoadImage の選択肢取得
  - `POST /upload/image` … 開始フレーム画像・リファレンス音声のアップロード
  - `POST /prompt` … ワークフロー投入（`client_id` を付与）
  - `WS /ws?clientId=…` … 進捗（ノード実行状況・プレビュー）の受信
  - `GET /history/{prompt_id}` … 出力ファイル名の取得
  - `GET /view?filename=…&type=output` … 成果物ダウンロード
- 同時実行は 1 ジョブ（ComfyUI 側キューに任せるが、アプリ側でもジョブキューを持ち順次投入）
- タイムアウト・ComfyUI 未起動・ノード不足（custom nodes 未導入）はジョブを failed にして UI に理由を表示

## 6. 成果物の取得

| 成果物 | 取得方法 |
|---|---|
| 生成画像 | モード A: `393` PreviewImage の出力を history から取得し `/view?type=temp` でダウンロード（temp はキュー再起動で消えるため即時保存）。モード C: SaveImage 出力を `type=output` で取得。いずれも `outputs/` に恒久保存 |
| 動画 | `75` SaveVideo の出力ファイルを `/view` でダウンロードし `outputs/{job_id}/video.mp4` に保存 |
| ラストフレーム | ダウンロードした動画から ffmpeg で抽出: `ffmpeg -sseof -0.5 -i video.mp4 -update 1 -q:v 1 last_frame.png`（次回生成の開始フレームに再利用可能） |

---

## 7. データ永続化

SQLite（`app.db`）+ ファイルストア（`outputs/`）。

```sql
CREATE TABLE jobs (
  id            TEXT PRIMARY KEY,          -- ULID
  created_at    TEXT NOT NULL,
  mode          TEXT NOT NULL,             -- 'full' | 'i2v' | 'image_only'
  status        TEXT NOT NULL,             -- queued | prompting | running | done | failed | canceled
  user_input    TEXT,                      -- Grok チャットでの最初の指示（手動作成時は NULL）
  image_prompt  TEXT,                      -- Grok 生成（編集後の最終値）
  video_prompt  TEXT,
  grok_raw      TEXT,                      -- Grok の生レスポンス(JSON)
  params        TEXT NOT NULL,             -- アスペクト比/MP/LoRA/秒数/fps/seed 等の JSON
  workflow_json TEXT NOT NULL,             -- 実際に投入した API JSON（完全再現用）
  comfy_prompt_id TEXT,
  image_path    TEXT,
  video_path    TEXT,
  last_frame_path TEXT,
  source_image  TEXT,                      -- モードBの開始フレーム（アップロード元 or 参照した job id）
  audio_path    TEXT,                      -- リファレンス音声
  error         TEXT
);
```

- `workflow_json` を保存するため、任意の過去ジョブを完全再実行（re-run）できる
- リファレンス音声・アップロード画像は `assets/` に保存し再利用可能（名前を付けて管理）
- **LoRA 登録リスト（アプリ内管理）**: 人物 LoRA を複数登録し、生成時に複数選択できる

```sql
CREATE TABLE loras (
  id            INTEGER PRIMARY KEY,
  display_name  TEXT NOT NULL,             -- 例: かおり
  lora_name     TEXT NOT NULL,             -- ComfyUI 上のファイル名（/object_info の一覧から選択して登録）
  trigger_word  TEXT NOT NULL,
  default_strength REAL DEFAULT 1.0,
  default_audio TEXT,                       -- 既定リファレンス音声（任意）
  sort_order    INTEGER DEFAULT 0
);

CREATE TABLE chat_sessions (
  id         TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  job_id     TEXT,                          -- 反映先ジョブ（実行後に紐付け）
  messages   TEXT NOT NULL                  -- [{role, content, ts}] の JSON
);
```

- 設定画面に LoRA 管理タブ（追加/編集/削除/並び替え）。`lora_name` は ComfyUI の LoRA 一覧から選ばせて typo を防ぐ
- ジョブの `params` には選択した LoRA の配列 `[{lora_name, trigger_word, strength}]` をスナップショットとして保存（後から登録リストを変更しても過去ジョブの再現性を保つ）
- 複数 LoRA 選択時の既定リファレンス音声は、選択順で最初に `default_audio` を持つ LoRA の値を採用（手動変更可）
- 初期データは持たない（LoRA は利用者の環境依存データのため、設定画面の LoRA 管理から登録する）

---

## 8. UI 仕様

SPA 1 画面 + 履歴。ダークテーマの生成系ツールらしい見た目。

```
┌────────────────────────────────────────────────────────┐
│ ヘッダー: 接続状態(ComfyUI ● / Grok ●)   [設定]          │
├───────────────────────────┬────────────────────────────┤
│ 左ペイン(入力)              │ 右ペイン(結果)               │
│ ◦ モード切替 [フル生成|画像から|画像のみ]                  │
│ ◦ 開始フレーム(モードB:      │ ◦ 進捗バー + 実行中ノード表示  │
│    D&D / 履歴から選択)      │ ◦ 生成画像プレビュー          │
│ ◦ アスペクト比 / MP         │ ◦ 動画プレイヤー              │
│ ◦ LoRA 複数選択(強度/トリガー)│ ◦ ラストフレーム              │
│ ◦ リファレンス音声選択       │   [この画像で続きを生成]       │
│ ◦ 画像プロンプト (textarea)  │ ◦ 使用プロンプト表示(コピー可)  │
│ ◦ 動画プロンプト (textarea)  │                            │
│   └ [Grokで生成] →チャットへ │                            │
│ ◦ 秒数 / fps / seed 固定    │                            │
│ ◦ [実行]                   │                            │
├───────────────────────────┴────────────────────────────┤
│ 履歴ギャラリー: サムネ一覧 → クリックで詳細(全パラメータ/再実行/│
│ ラストフレームから続き生成/削除)                            │
└────────────────────────────────────────────────────────┘

┌─ Grok チャットモーダル ─────────────────────────────────┐
│ 吹き出し形式の会話ビュー（Grok の質問 ⇄ ユーザーの回答）      │
│ Grok が JSON 最終案を出すと「プロンプトプレビュー」カードを   │
│ 表示 → [フォームに反映] / [続けて調整]                     │
└────────────────────────────────────────────────────────┘
```

- 進捗は ComfyUI の WS イベント（`executing` / `progress`）をそのまま％表示に変換
- 実行中でもキュー追加可能（ジョブキュー表示）
- LoRA 選択はチップ型マルチセレクト。選択するとトリガーワード連結欄（編集可）に反映
- **モードに応じた項目の無効化**: モード B では画像プロンプト・LoRA・トリガーワードをグレーアウト（画像生成サブグラフを使わないため）。モード C では動画プロンプト・ネガティブ・リファレンス音声・秒数・fps をグレーアウト
- 動画ネガティブはプリセット選択（現行値 / モデル作者版）+ 編集可（詳細設定アコーディオン内）
- 設定画面: ComfyUI 接続先（URL / APIキー） / grok CLI 状態と**使用モデル（既定: grok-4.5、変更可）** / **LoRA 管理タブ**（表示名・ファイル名・トリガーワード・既定音声の CRUD）

---

## 9. 技術スタック（提案）

| レイヤ | 技術 | 理由 |
|---|---|---|
| バックエンド | Python 3.12 + FastAPI + uvicorn | ComfyUI/Grok クライアントとも Python 資産が使える。WS 中継が容易 |
| フロント | React + Vite + Tailwind | SPA 1 枚で十分 |
| DB | SQLite (aiosqlite) | ローカル単体運用 |
| 動画処理 | ffmpeg (subprocess) | ラストフレーム抽出・サムネ生成 |
| ジョブ管理 | アプリ内 asyncio キュー | 外部依存を増やさない |

### バックエンド API（概要）

```
GET  /api/health                 … ComfyUI/Grok 疎通チェック
GET  /api/options                … アスペクト比・ComfyUI上のLoRAファイル一覧等（object_info 由来）
GET/POST/PUT/DELETE /api/loras   … アプリ内 LoRA 登録リストの CRUD
POST /api/chat/sessions          … チャット開始（フォーム現在値をコンテキストとして渡す）
POST /api/chat/sessions/{id}/messages … 発言送信 → Grok 応答（質問 or 最終JSON案）を返す
GET  /api/chat/sessions/{id}     … 履歴取得
POST /api/jobs                   … ジョブ作成・実行（プロンプト確定値+パラメータ）
GET  /api/jobs?limit=…           … 履歴一覧
GET  /api/jobs/{id}              … 詳細
POST /api/jobs/{id}/rerun        … 再実行（seed 変更オプション）
POST /api/jobs/{id}/continue     … ラストフレームを開始フレームに新規ジョブ
DELETE /api/jobs/{id}
POST /api/assets/audio|image     … アセットアップロード
WS   /api/ws                     … 進捗配信
GET  /outputs/…                  … 静的配信（画像/動画）
```

---

## 10. 制約・注意事項

1. **Grok Build CLI 依存**: `grok` CLI のインストールとサブスクリプションでのサインインが前提。CLI はベータ段階のため出力形式・挙動が変わる可能性があり、LLM クライアントは抽象化して公式 API / ローカル LLM に差し替え可能に設計する。NSFW プロンプト生成を Grok が拒否した場合のリトライ指示（システムプロンプト側の調整）とエラー表示も用意する
2. **コンテンツ**: 本アプリは成人向けコンテンツをローカル生成する個人利用ツール。生成物・プロンプトはすべてローカル保存のみで外部送信しない。LoRA は実在人物の無断利用を行わないこと（利用者責任）
3. **ComfyUI 依存**: ResolutionSelector / ComfySwitchNode / LTXV 系 / ComfyMath / ResizeImage 系等の custom nodes が導入済みである前提（`ImpactWildcardEncode` は投入時に削除するため不要）。起動時に `/object_info` で、**投入 JSON に実際に含まれる class_type** の存在チェックを行い、不足があれば UI に警告
4. モデル既定値（steps/CFG/sigmas 等）は配布ページ推奨値でワークフローに固定済みのため、アプリからは変更しない（上級者向けに将来開放余地あり）

## 11. 決定事項と残課題

決定済み（2026-07-26 ヒアリング）:

1. モード C（画像のみ生成）: **実装する**
2. リファレンス音声: **LoRA 登録に紐付け**（個別上書き可）
3. 秒数: **UI 上限なし**（既定 10 秒、長尺の失敗リスクは注記で許容）
4. Grok 連携: **Grok Build CLI（公式・サブスク認証）**。Cookie 方式は不採用
5. LoRA: **人物 LoRA を複数同時適用可**（動的チェーン注入 §3.4）。LoRA リスト（名前+トリガーワード+既定音声）は**アプリ内で管理**（§7）
6. プロンプト作成: **手動が基本**。「Grokで生成」ボタンで**チャット形式のヒアリング UI** へ（§4.3）。雑な指示のときは Grok が質問で掘り下げてからプロンプトを確定する
7. ComfyUI 接続先: **ローカル / 別 PC / Comfy Cloud のいずれでも使えるよう接続層を設定式に**。ワークフローは Comfy Cloud での動作確認済みのため持込み関連の懸念なし（§5）
8. grok CLI のモデル: **既定 grok-4.5**、設定画面で変更可能
9. 履歴: **無制限保存**（手動削除のみ）
10. 技術スタック: **FastAPI + React + SQLite で確定**（§9）

残課題: なし（実装着手可能）
