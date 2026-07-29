# Karakuri Media Studio 仕様書（ドラフト v0.2）

`workflow/` 配下の ComfyUI ワークフロー群（画像: Krea 2 turbo / 動画: LTX 2.3 の 7 種）をバックエンドとして使う動画生成アプリの仕様。
プロンプト作成は Grok（サブスクリプション認証）に委譲し、実行・成果物管理・履歴保存を本アプリが担う。

> v0.2 での変更: 単一の合体グラフ `video-gen.json` を廃止し、分離された複数テンプレートを
> **注入マニフェスト**（ノード ID 直指定、`backend/app/workflows.py`）で駆動する方式に移行した。
> フル生成は「画像ワークフロー → 生成画像をアップロード → 動画ワークフロー」の **2 ジョブ連結**になった。

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
3. 選択したワークフローのテンプレートにパラメータを注入し、ComfyUI `/prompt` API に投入（フル生成は 2 段）
4. WebSocket で進捗を UI に中継（フル生成は「画像生成 (1/2)」→「動画生成 (2/2)」の 2 段表示）
5. 完了後、**生成画像・動画**を ComfyUI から取得、**ラストフレーム**を ffmpeg で抽出
6. プロンプト・パラメータ・チャット履歴・成果物パスを SQLite に保存し、UI のギャラリー/履歴に表示

---

## 2. 動作モード

ワークフローは画像と動画で分離しており、1 ジョブは **1 つまたは 2 つの ComfyUI プロンプト**で構成される。

| モード | 内部名 | 実行されるワークフロー | 開始フレーム |
|---|---|---|---|
| フル生成 | `full` | 画像ワークフロー → 選択した動画ワークフロー（2 段） | 1 段目の生成画像 |
| 動画生成 | `i2v` | 選択した動画ワークフローのみ | ワークフローが要求する入力（アップロード / 履歴 / なし） |
| 画像のみ | `image_only` | 画像ワークフローのみ | ― |

### 2.1 フル生成の 2 ジョブ連結

旧方式のようにグラフを合体させず、同一 `job_id` のもとで順に実行する:

1. 画像ワークフロー（krea2）を `/prompt` に投入 → 完了を待つ
2. `SaveImage` の出力を `/view` でダウンロードし `outputs/{job_id}/image.png` に保存
3. その画像を ComfyUI `/upload/image` で input ディレクトリへアップロード
4. 選択した動画ワークフローの `LoadImage` にそのファイル名を注入して投入 → 完了を待つ
5. 動画をダウンロードし、ffmpeg でラストフレームを抽出

- 進捗は 1 ジョブとして配信され、メッセージが「画像生成 (1/2)」→「動画生成 (2/2)」と切り替わる
- `workflow_json` には **両方のグラフ**を `{"image": {...}, "video": {...}}` の形で保存する（各要素は `workflow_id` / `prompt_id` / `graph`）。再現性の担保はこれで行い、`rerun` は `params` から作り直す
- フル生成で選べるのは**開始フレームを受け取れる動画ワークフローだけ**（`accepts_start_image`）。t2v と IC-LoRA リファレンスシートは対象外で、選択すると 422 になる

### 2.2 動画ワークフロー（`workflow/video/ltx2.3/`）

| id | 表示名 | ckpt | 必要入力 | フル生成可 |
|---|---|---|---|---|
| `ltx2_3_t2v` | テキスト→動画 (t2v) | dev-fp8 | なし | ✕ |
| `tx2_3_i2v` | 画像→動画 (i2v) | dev-fp8 | 画像 | ○ |
| `tx2_3_ia2v` | 画像+音声→動画 (ia2v) | dev-fp8 | 画像・音声 | ○ |
| `ltx2_3_id_lora` | 画像+参照音声→動画・リップシンク (ID-LoRA) | dev-fp8 + talkvid ID-LoRA | 画像・音声 | ○（既定） |
| `ltx2_3_flf2v` | 最初と最後のフレーム指定 (flf2v) | distilled-fp8 | 画像・最終フレーム画像 | ○ |
| `ltx2_3_ic_lora_image` | リファレンスシート (IC-LoRA) | distilled-fp8 + ingredients IC-LoRA | リファレンスシート画像 | ✕ |
| `ltx2_3_ic_lora_motion` | 参照動画からモーション転写 (IC-LoRA + MoGe) | distilled-fp8 + union-control IC-LoRA | 画像・参照動画 | ○ |

- id はファイル名（拡張子なし）。`tx2_3_i2v` / `tx2_3_ia2v` の綴りは配布ファイル名そのまま
- 既定は `ltx2_3_id_lora`（旧 `video-gen.json` の動画側と同じ構成なので、既存ジョブ・エージェントの計画がそのまま通る）
- ラストフレーム連鎖: 履歴の動画から「ラストフレームを開始フレームにして続きを生成」できる。元ジョブの動画ワークフローが開始フレームを受け取れない場合は既定ワークフローにフォールバックする

---

## 3. ワークフロー解析とパラメータ注入ポイント

テンプレートは API フォーマット。各テンプレートの書き換え対象は **注入マニフェスト**
（`backend/app/workflows.py` の `WorkflowSpec.inject`）で宣言する。

### 3.0 なぜノード ID 直指定か

`class_type` + タイトルでは特定できない: ポジ/ネガの `CLIPTextEncode` が同じタイトル
「CLIPテキストエンコード（プロンプト）」、`RandomNoise` が 1 グラフに 2 個、`ComfyMathExpression`
は「数式」が多数ある。そのためマニフェストは **ノード ID** を指定し、代わりに整合性チェックを持つ:

- 起動時（`lifespan`）と `GET /api/health` で、マニフェストの各ノードが**実在し `class_type` が一致し
  指定フィールドを持つ**ことを検証する（`validate_specs`）。不一致は起動ログとヘルスに出る
- テストでも同じ検証を行う（`tests/test_workflow.py::test_every_manifest_matches_its_template`）
- **ワークフロー JSON を差し替えたら、マニフェストも合わせて更新する**運用

### 3.1 ユーザーが UI から設定する項目

| 項目 | 注入先（論理名 → ノード） | UI |
|---|---|---|
| 動画ワークフロー | ― | プルダウン（`/api/options` の `video_workflows`）。選択に応じて必要入力の欄が出る |
| アスペクト比 / メガピクセル | 画像: `aspect_ratio` / `megapixels` → `49` (ResolutionSelector)。動画: アプリが幅・高さを計算して `width` / `height` に注入 | セレクト（選択肢は `/object_info` の ResolutionSelector から動的取得）+ 数値 |
| LoRA（画像・複数可） | 画像ワークフローの `lora_chain` を動的構築（§3.4） | 「LoRA（画像）」セクション。登録 LoRA のうち `target = 'image'` のものを複数選択＋強度スライダー |
| LoRA トリガーワード（画像） | `trigger_concat` → `30:27` (StringConcatenate) / `trigger_switch` → `30:28` | 選択 LoRA のトリガーワードを自動連結（編集可） |
| LoRA（動画・複数可） | 動画ワークフローの `lora_chain` を動的構築（§3.4） | 「LoRA（動画）」セクション。登録 LoRA のうち `target = 'video'` のものを複数選択＋強度スライダー |
| LoRA トリガーワード（動画） | 動画プロンプト文字列の先頭に前置 | 同上（自動連結・編集可） |
| リファレンス音声 | `audio` → `276` (LoadAudio)。要求するワークフローのみ | アップロード（`/upload/image` で送信 → ファイル名を注入） |
| 開始フレーム / 最終フレーム / 参照動画 | `image` / `end_image` / `video` | ワークフローの必要入力に応じて表示。画像は D&D・履歴のラストフレームからも選べる |
| 秒数 (Duration) | `duration` | 数値・**上限なし**。長尺は VRAM 次第で ComfyUI 側エラーになり得ることを UI に注記 |
| フレームレート | `fps` | 数値（既定 25） |
| 画像・動画プロンプト | `prompt`（画像 `30:19` / 動画は各テンプレート） | テキストエリア（手動入力が基本。Grok チャット §4.3 の結果を反映して編集も可） |
| 動画ネガティブ | `negative` | プリセット切替（ワークフロー既定 / 現行値 / モデル作者版）+ 直接編集可。**空欄ならテンプレート既定値のまま**（dev 系は `pc game, …`、distilled 系は品質ネガ） |

#### 解像度の計算

画像側は `49` (ResolutionSelector) にアスペクト比とメガピクセルをそのまま渡す。
動画側の新テンプレートは幅・高さの `PrimitiveInt` 指定になったため、アプリが同じ式で計算する
（ComfyUI `comfy_extras/nodes_resolution.py` と一致。各辺を 8 の倍数に丸め）:

```
scale  = sqrt(megapixels * 1024 * 1024 / (w_ratio * h_ratio))
width  = round(w_ratio * scale / 8) * 8
height = round(h_ratio * scale / 8) * 8
```

参照画像（開始フレーム）を取るワークフロー（`accepts_start_image=True`）で `source_image` が
指定されている場合は、`w_ratio:h_ratio` にプリセットではなく **参照画像の実寸比** を使う
（メガピクセルの総画素数と 8 の倍数丸めはそのまま）。比が合わないとテンプレート内の
`ResizeImageMaskNode`（crop=center）でセンタークロップされ画が切れるため。画像の寸法が
読めなかった場合はプリセットにフォールバックする。`full` モードの 1 段目は生成画像が
プリセット通りなので、2 段目もプリセットを使う。

例外: `ltx2_3_ic_lora_image` は幅・高さがリファレンスシートのパディング結果
（`722` ResizeAndPadImage の `target_width` / `target_height`）から決まるため、そこに注入する。
潜在側の丸めは `EmptyLTXVLatentVideo` が行う。

#### フレーム数

各テンプレートの `ComfyMathExpression`（`a * b + 1` もしくは `a * b`）を、アプリが計算した
`8n + 1` の定数に固定する。式は `a * 0 + b * 0 + <frames>` に書き換え、入力リンクは温存するので
グラフ形状と出力型は変わらない。`ltx2_3_ic_lora_motion` はフレーム数が参照動画の長さで決まるため
式の固定を行わず、代わりに秒数を `692` (Video Slice) の `duration` に注入する。

### 3.2 アプリが自動注入する項目

| 項目 | 論理名 | 方針 |
|---|---|---|
| 画像プロンプト / 動画プロンプト | `prompt` | フォームの確定値（手動 or Grok チャット反映後） |
| 画像 seed | `seed` → `30:3` | 実行毎にランダム（固定オプションあり）。`params` に保存して再現可能 |
| 動画 noise seed | `seeds`（低解像度パス + アップスケールパスの `RandomNoise`、IC-LoRA 系は `KSampler.seed`） | 同上。seed が 1 個しか渡らない場合は全サンプラーで共用 |
| 出力プレフィックス | `save_prefix` | 画像 `images/{job_id}` / 動画 `video/{job_id}` にして成果物とジョブを紐付け |
| ローカル LLM リファイン | `refine_enable` → `30:24` | **false 固定**（プロンプト整形は Grok が担う）。`ComfySwitchNode` は遅延評価（`check_lazy_status`）なので `30:16` (TextGenerate) は実行されない |
| プロンプト拡張 | `prompt_enhance` → 各テンプレートの `Boolean (Enable Prompt Enhance)` | **false 固定**（同上）。IC-LoRA 系は false なのでスイッチのリテラル側 `on_false` にプロンプトを注入する |

### 3.3 固定（触らない）ノード

- 画像側: UNET `krea2_turbo_fp8_scaled` / CLIP `qwen3vl_4b_fp8_scaled` / VAE `qwen_image_vae`、KSampler 設定（euler / simple / 8 steps / CFG 1）
- 動画側: checkpoint `ltx-2.3-22b-dev-fp8` または `ltx-2.3-22b-distilled-fp8`、distil LoRA (strength 0.5)、talkvid ID-LoRA + `LTXVReferenceAudio`（identity_guidance_scale 3）、IC-LoRA と MoGe、2 段サンプリング（半解像度 → LatentUpsampler x2）、ManualSigmas
- **モデルファイル名は利用者の ComfyUI 環境依存**のため、設定ページ（`GET/PUT /api/models`）で上書き可能。既定値は各テンプレートの値。対象は UNETLoader.unet_name / CLIPLoader.clip_name / VAELoader.vae_name / CheckpointLoaderSimple.ckpt_name / LTXVAudioVAELoader.ckpt_name / LTXAVTextEncoderLoader.text_encoder・ckpt_name / LatentUpscaleModelLoader.model_name / LoadMoGeModel.model_name / LoraLoaderModelOnly.lora_name / LoraLoader.lora_name（§3.4 で置換される krea2 のプレースホルダは除く。LTX 側の固定 LoRA ノードはユーザー LoRA と共存するので上書き対象のまま）
- 上書きキーは**ワークフロー ID でスコープ**する: `"<workflow_id>/<node_id>.<field>": "<ファイル名>"`。テンプレート間で同じノード ID（例: `340:317` が ia2v と id_lora の両方にある）が衝突しないため。旧レイアウトの非スコープキーは無視される（マイグレーション不要）

### 3.4 複数 LoRA の動的注入

LoRA は**登録時に対象（`target`）を選ぶ**: `image` なら画像ワークフロー（Krea 2）、
`video` なら動画ワークフロー（LTX 2.3）に注入される。ジョブは両者を別フィールドで持つ
（`loras` / `trigger_text` と `video_loras` / `video_trigger_text`）。

#### 3.4.1 画像 LoRA チェーン

人物 LoRA を複数同時に適用できるよう、krea2 テンプレートが持つ 5 個の
`LoraLoaderModelOnly`（strength 0 のプレースホルダ `30:61:*`）は使わず、アプリが API JSON 生成時に
**LoRA チェーンを動的に構築**する:

```
30:10 (UNETLoader)                       … lora_chain.head
  → app_lora_0 (LoraLoaderModelOnly: 1個目, strength_model=各LoRAの強度)
  → app_lora_1 (2個目)
  → … 選択数ぶん連結（テンプレートのプレースホルダ数を超えてもよい）
  → 30:3 (KSampler) の model 入力へ    … lora_chain.consumers
```

- ノード ID はアプリが採番（`app_lora_0`, `app_lora_1`, …）。プレースホルダはテンプレートから削除
- LoRA 0 件選択時は `30:3.model` が `30:10` を直接指す
- トリガーワードは選択 LoRA の trigger_word を `", "` で連結（UI で編集可）し、`30:27`（StringConcatenate）で**画像プロンプトの先頭**に付与する: `string_a` = トリガーワード（リテラル）、`string_b` = プロンプトのリンク（`30:20`）、`delimiter` = `", "`
  - Grok がプロンプト本文中で既にトリガーワードを使っている場合は重複を避けるため、カンマ区切りの語単位で（大文字小文字無視・単語境界一致）未使用の語だけを付与する
  - 付与すべき語が無い場合は `string_a` と `delimiter` を空にし、さらに `30:28` (Switch) を `false` にして連結ノードを丸ごとバイパスする（先頭に `", "` が残らないようにする）

#### 3.4.2 動画 LoRA チェーン

LTX 2.3 の各テンプレートは動作に必須の固定 LoRA（distilled-1.1 / talkvid ID-LoRA /
IC-LoRA）を持つので、ユーザー LoRA は**その後段**へ同じ仕組みで直列挿入する。
`LoraChain` はプレースホルダを持たず、「`head` の MODEL 出力を読んでいた入力（`consumers`）を
チェーン末尾に付け替える」という 1 本の辺の切り開きとして表現する:

| ワークフロー | head（挿入位置の直前） | consumers（付け替える入力） |
|---|---|---|
| `ltx2_3_t2v` | `267:232` distill LoRA | `267:213` / `267:231` CFGGuider.model |
| `tx2_3_i2v` | `320:285` distill LoRA | `320:282` / `320:314` CFGGuider.model |
| `tx2_3_ia2v` | `340:293` distill LoRA | `340:290` / `340:315` CFGGuider.model |
| `ltx2_3_id_lora` | `340:293` distill LoRA | `340:290` CFGGuider.model / `340:346` ID-LoRA.model |
| `ltx2_3_flf2v` | `129:300` distill LoRA | `129:116` CFGGuider.model |
| `ltx2_3_ic_lora_image` / `_motion` | `129:195` IC-LoRA | `129:704` KSampler.model |

- ノード ID は `app_video_lora_0`, `app_video_lora_1`, … と採番する
- 0 件選択時は consumers が `head` を直接指す（テンプレートと同一のグラフ）
- テキストエンコーダ側の Gemma `LoraLoader` や `GetICLoRAParameters` は付け替えない
  （MODEL 出力を使わない／IC-LoRA 自体のパラメータ取得に使うため）
- 動画側テンプレートには StringConcatenate が無いので、トリガーワードは
  `video_trigger_text`（空なら選択 LoRA の trigger_word 連結）のうち**動画プロンプトに
  未出現の語だけ**をプロンプト文字列の先頭に前置する（判定は画像側と同じ単語境界一致）
- マニフェスト検証（`GET /api/health`）は、consumers が実際に `head`（または画像側の
  プレースホルダ）を読んでいるかまで確認する。読んでいなければ健全性エラーになる
- `video_loras` は動画ステージが走るモード（`full` / `i2v`）でのみ有効。`image_only` や
  `lora_chain` を持たないワークフローに指定するとジョブ作成が 422 で拒否される
- 人物の同一性は、画像 LoRA → 生成画像 → 開始フレームという経路で動画にも引き継がれる

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
- 作者が実際に使うネガティブは品質系+音声系（blurry, …, distorted sound, saturated loud 等）で、テンプレート既定値と異なる。**アプリからネガティブも選択可能にする**（既定は現行値、プリセットで作者版を用意）
- RedCraft 画像プロンプトは品質語プレフィックス（例: "masterpiece, very aesthetic"）+ 自然文 1 段落が実例でも主流

**画像プロンプト（RedCraft 赤佬3.0 / Krea 2 ベース、TE は Qwen3-VL 4B）**

- Krea 2 公式ガイド（krea-ai/krea-2 `docs/prompting.md`）準拠: **自然文 1 段落・長く詳細なほど良い**。画像内に文字を描画する場合は対象語を引用符で囲む
- Grok 用システムプロンプトは Krea 2 公式の LLM 拡張プロンプト（`workflow/image/krea2/krea2_turbo.json` のノード `30:18` に同一物が組込済み）をベースに、本アプリの用途・LoRA トリガー・出力 JSON 形式に合わせて調整する
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

1. フォームの「Grokで生成」ボタン → チャットパネル（モーダル）を開く。フォームの現在値（モード、**選択中の動画ワークフロー** `video_workflow`、選択 LoRA とトリガーワード、秒数、既存プロンプト下書き）がコンテキストとして自動で渡る
2. ユーザーが作りたいものをひとこと入力（例: 「かおりが楽しそうにダンスをしている」）
3. Grok は**不足情報を質問で聞き返す**よう指示されている: 場所・服装・時間帯/照明・カメラ（ショットスケール/動き）・表情/ムード・セリフや音・動きの展開など。ユーザーが「おまかせ」と言えば残りは Grok が補完
4. 情報が揃ったら Grok が `image_prompt` / `video_prompt` の最終案を JSON で提示 → 「フォームに反映」ボタンでプロンプト欄へ挿入
5. 反映後もチャットを続けて再調整可能（「もっと引きのカメラで」等 → 更新版 JSON を再提示）

実装:

- grok CLI のヘッドレス実行（`grok -p`）は 1 発呼び出しのため、**会話履歴はアプリ側で保持**し、毎ターン「システムプロンプト + 履歴全文 + 最新発言」を組み立てて渡す
- システムプロンプトの構成: ①役割（プロンプトエンジニア兼インタビュアー）②各モデルのプロンプト仕様（§4.2）+ few-shot 実例（docs/prompt-samples.md）③ヒアリング項目チェックリスト ④選択中の動画ワークフローの特性（下記）⑤最終出力は ```json フェンス内の `{image_prompt, video_prompt, notes}` のみ、というルール
- **ワークフロー特性の反映**: CONTEXT には選択中の `video_workflow` の用途・必要入力・音声の扱い・`video_prompt` の書き方を出す。文面は `app/workflows.py` の `WorkflowSpec`（`description` / `audio_role` / `prompt_hint`）から自動生成する単一情報源なので、ワークフローを追加したらマニフェスト側に書けばチャット・エージェント両方に反映される（未記入は `validate_specs()` = ヘルスチェックで検出）。例: flf2v なら開始→終了フレーム間の遷移を書かせる、t2v / リファレンスシート IC-LoRA なら開始フレーム前提にしない、ia2v なら渡した音声がそのまま音声トラックになるのでセリフをプロンプトに書かせない、ic_lora_motion ならカメラ・テンポは参照動画由来なので書かせない
- 応答の判定: 応答に JSON フェンスがあれば「最終案の提示」、なければ「質問継続」として UI に表示
- 十分詳細な初回入力なら Grok は質問を飛ばして即 JSON を返してよい（ワンショット生成はチャットの特殊ケースとして自然に実現）
- モード B ではスタートフレーム画像を grok 作業ディレクトリにコピーし、CLI に読ませて内容を踏まえた `video_prompt` を作らせる（読めない場合はテキストのみでフォールバック）。ワークフローが開始フレームを取らない場合（t2v 等）はモード B でも「見た目もプロンプトで決める」指示に切り替わる
- チャット履歴は `chat_sessions` に保存し、ジョブに紐付ける（後から「どういう指示で作ったか」を追える）

---

## 5. ComfyUI 連携

- 接続先: `http://<comfy-host>:8188`（設定画面で URL 変更可）。実行環境はローカル / LAN 上の別 PC / Comfy Cloud のいずれでも動くよう、ComfyUI クライアントは「接続 URL + 任意の認証ヘッダー（API キー）」を設定できる抽象化された 1 モジュールにする
- **Comfy Cloud**: Cloud 向けのエンドポイント URL と認証設定を設定画面から入力できる（ホストが `comfy.org` のとき自動で Cloud 互換モード）
- 使用 API:
  - `GET /object_info` … ResolutionSelector のアスペクト比選択肢、LoRA 一覧、class_type の存在確認
  - `POST /upload/image` … 開始フレーム画像・リファレンス音声・参照動画、および**フル生成 1 段目の生成画像**のアップロード（ComfyUI はこのエンドポイントで input ディレクトリに任意ファイルを受ける）
  - `POST /prompt` … ワークフロー投入（`client_id` を付与）。フル生成は 1 ジョブで 2 回投入する
  - `WS /ws?clientId=…` … 進捗（ノード実行状況・プレビュー）の受信
  - `GET /history/{prompt_id}` … 出力ファイル名の取得
  - `GET /view?filename=…&type=output` … 成果物ダウンロード
- 同時実行は 1 ジョブ（ComfyUI 側キューに任せるが、アプリ側でもジョブキューを持ち順次投入）
- タイムアウト・ComfyUI 未起動・ノード不足（custom nodes 未導入）はジョブを failed にして UI に理由を表示

## 6. 成果物の取得

| 成果物 | 取得方法 |
|---|---|
| 生成画像 | 画像ワークフローの `SaveImage`（`29`）の出力を history から取得し `/view` でダウンロードして `outputs/{job_id}/image.png` に保存（フル生成でも SaveImage なので `type=output`） |
| 動画 | 動画ワークフローの `SaveVideo` の出力ファイルを `/view` でダウンロードし `outputs/{job_id}/video.mp4` に保存。出力ノード ID はワークフローごとに異なる（`75` / `341` / `68`）ためマニフェストの `output_node` を使う |
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
  params        TEXT NOT NULL,             -- ワークフローID/アスペクト比/MP/LoRA/秒数/fps/seed 等の JSON
  workflow_json TEXT NOT NULL,             -- 投入した API JSON（{"image": …, "video": …} の段階別）
  comfy_prompt_id TEXT,
  image_path    TEXT,
  video_path    TEXT,
  last_frame_path TEXT,
  source_image  TEXT,                      -- 開始フレーム（アップロード元 or 参照した job id）
  audio_path    TEXT,                      -- リファレンス音声
  error         TEXT
);
```

- `params` には `video_workflow` / `image_workflow`（ワークフロー ID）と、`end_image` / `reference_video` も保存する
- `workflow_json` を保存するため、任意の過去ジョブの投入内容をあとから完全に確認できる（`rerun` は `params` から作り直す）
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
  sort_order    INTEGER DEFAULT 0,
  sample_images TEXT NOT NULL DEFAULT '[]', -- サンプル画像ファイル名の JSON 配列
  target        TEXT NOT NULL DEFAULT 'image'  -- 'image' = 画像WF / 'video' = 動画WF（§3.4）
);

CREATE TABLE chat_sessions (
  id         TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  job_id     TEXT,                          -- 反映先ジョブ（実行後に紐付け）
  messages   TEXT NOT NULL                  -- [{role, content, ts}] の JSON
);
```

- 設定画面に LoRA 管理タブ（追加/編集/削除/並び替え）。`lora_name` は手入力＋ComfyUI の LoRA 一覧からの補完候補（datalist）。**対象ワークフロー（画像用 / 動画用）を選んで登録**し、サンプル画像・トリガーワード・既定強度はどちらでも同じように登録できる
- `target` は後から追加したカラムなので、既存レコードは `image`（従来どおり画像 LoRA）として移行される
- ジョブの `params` には選択した LoRA の配列 `[{lora_name, trigger_word, strength}]` を**画像用 `loras` と動画用 `video_loras` に分けて**スナップショット保存（後から登録リストを変更しても過去ジョブの再現性を保つ）。`video_loras` / `video_trigger_text` を持たない古い params は空として読む
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
│ ◦ モード切替 [フル生成|動画生成|画像のみ]                  │
│ ◦ 動画ワークフロー(プルダウン)│ ◦ 進捗バー + 実行中ノード表示  │
│ ◦ 開始フレーム/最終フレーム/  │ ◦ 生成画像プレビュー          │
│    参照動画(D&D/履歴から選択) │                            │
│ ◦ アスペクト比 / MP         │ ◦ 動画プレイヤー              │
│ ◦ リファレンス音声選択       │ ◦ ラストフレーム              │
│ ◦ LoRA(動画) 複数選択        │   [この画像で続きを生成]       │
│ ◦ LoRA(画像) 複数選択        │                            │
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
- LoRA 選択はチップ型マルチセレクト（強度スライダー付き）。選択するとトリガーワード連結欄（編集可）に反映される。セクションは 2 つあり、**「LoRA（動画）」は動画設定群の中**（登録 `target = 'video'` のみ）、**「LoRA（画像）」は画像設定群の中**（`target = 'image'` のみ）に置く
- **モードとワークフローに応じた項目の無効化**: 動画生成モードでは画像プロンプト・LoRA（画像）・トリガーワードをグレーアウト（画像ワークフローを使わないため。LoRA（動画）は有効のまま）。画像のみモードでは動画プロンプト・ネガティブ・リファレンス音声・秒数・fps・LoRA（動画）をグレーアウト。さらに**選択した動画ワークフローのマニフェスト**に従って、音声を受け取らないワークフローでは音声欄を無効化し、必要な入力（最終フレーム / 参照動画）の欄だけを表示する
- フル生成モードのプルダウンには開始フレームを受け取れるワークフローのみを出す（選択中のものが対象外になったら自動で切り替える）
- 動画ネガティブはプリセット選択（ワークフロー既定 / 現行値 / モデル作者版）+ 編集可（詳細設定アコーディオン内）
- 設定は**モーダルではなく専用ページ（フルページ）**。ヘッダーの [設定] で画面遷移し、ページ左上の [← 戻る] で生成画面に復帰する。3 タブ構成:
  - **接続 / Grok**: ComfyUI 接続先（URL / APIキー） / grok CLI コマンドと**使用モデル（既定: grok-4.5、変更可）**
  - **LoRA 管理**: 表示名・ファイル名・**対象ワークフロー（画像用 / 動画用）**・トリガーワード・既定強度・既定音声・並び順の CRUD とサンプル画像の登録
  - **モデル**: 全ワークフローのモデルファイル名一覧（ワークフロー / タイトル / ノード・フィールド / 既定値）をテーブル表示し、行ごとにテキスト入力で上書き。変更行はハイライト、[既定に戻す] で復帰、[保存] で一括 PUT。LoRA 行は `/api/options` の `lora_files` があれば datalist で補完（§3.3）

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
GET  /api/options                … 動画/画像ワークフロー一覧（必要入力の宣言つき）・アスペクト比・LoRA一覧・アセット一覧
GET/POST/PUT/DELETE /api/loras   … アプリ内 LoRA 登録リストの CRUD
GET  /api/models                 … 全ワークフローのモデルファイル名一覧（既定値+現在値、キーは workflow_id でスコープ）
PUT  /api/models                 … モデルファイル名の上書き保存（既定値と同値/空は削除）
POST /api/chat/sessions          … チャット開始（フォーム現在値をコンテキストとして渡す。`video_workflow` を含む）
POST /api/chat/sessions/{id}/messages … 発言送信 → Grok 応答（質問 or 最終JSON案）を返す
GET  /api/chat/sessions/{id}     … 履歴取得
POST /api/jobs                   … ジョブ作成・実行（プロンプト確定値+パラメータ）
GET  /api/jobs?limit=…           … 履歴一覧
GET  /api/jobs/{id}              … 詳細
POST /api/jobs/{id}/rerun        … 再実行（seed 変更オプション）
POST /api/jobs/{id}/continue     … ラストフレームを開始フレームに新規ジョブ（`video_workflow` / `end_image` / `reference_video` 等を差分指定可。開始フレームを取れないワークフローは既定に戻す）
DELETE /api/jobs/{id}
POST /api/assets/audio|image|video … アセットアップロード（video は参照動画用）
WS   /api/ws                     … 進捗配信
GET  /outputs/…                  … 静的配信（画像/動画）
```

---

## 10. 制約・注意事項

1. **Grok Build CLI 依存**: `grok` CLI のインストールとサブスクリプションでのサインインが前提。CLI はベータ段階のため出力形式・挙動が変わる可能性があり、LLM クライアントは抽象化して公式 API / ローカル LLM に差し替え可能に設計する。NSFW プロンプト生成を Grok が拒否した場合のリトライ指示（システムプロンプト側の調整）とエラー表示も用意する
2. **コンテンツ**: 本アプリは成人向けコンテンツをローカル生成する個人利用ツール。生成物・プロンプトはすべてローカル保存のみで外部送信しない。LoRA は実在人物の無断利用を行わないこと（利用者責任）
3. **ComfyUI 依存**: ResolutionSelector / ComfySwitchNode / LTXV 系 / ComfyMath / ResizeImage 系 / ResizeAndPadImage / MoGe 系 / LoadVideo / Video Slice 等の custom nodes が導入済みである前提。起動時と `/api/health` で `/object_info` に対し **`workflow/` 配下の全テンプレートに含まれる class_type** の存在チェックを行い、不足があれば UI に警告する（どのワークフローを使うか実行前には分からないため、集合は全テンプレート横断）。同時にマニフェストとテンプレートの整合性も検証する（§3.0）
4. **プロンプト拡張ブランチのモデルファイル**: 各動画テンプレートは prompt enhance 用に `gemma-3-12b-it-abliterated_lora`（`LoraLoader`）を参照している。アプリは enhance を常に false にするので実行はされないが、ComfyUI は投入グラフ全体の入力を検証するためファイル自体は存在する必要がある。無い場合は設定ページの「モデル」タブで別名に差し替える
5. モデル既定値（steps/CFG/sigmas 等）は配布ページ推奨値でワークフローに固定済みのため、アプリからは変更しない（上級者向けに将来開放余地あり）

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
