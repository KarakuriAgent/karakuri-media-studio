# Karakuri Media Studio 仕様書（ドラフト v0.2）

`workflow/` 配下の ComfyUI ワークフロー群（画像 4 種 / 動画: LTX 2.3 の 7 種 / 音声 2 種）をバックエンドとして使うメディア生成アプリの仕様。
プロンプト作成は Grok（サブスクリプション認証）に委譲し、実行・成果物管理・履歴保存を本アプリが担う。

> v0.2 での変更: 単一の合体グラフ `video-gen.json` を廃止し、分離された複数テンプレートを
> **注入マニフェスト**（ノード ID 直指定、`backend/app/workflows.py`）で駆動する方式に移行した。
> 「画像＋動画」モード（内部名 `full`）は「画像ワークフロー → 生成画像をアップロード →
> 動画ワークフロー」の **2 ジョブ連結**になった。
>
> v0.3 での変更: 画像ワークフローを 4 種（krea2 / anima / z-image / qwen-image-edit）から
> 選択式にし、画像 LoRA を**モデルファミリー**で仕分けるようにした。あわせて**音声モード**
> （ACE-Step 1.5 / Stable Audio 3）を追加した。音声は画像・動画と連結しない独立ジョブ。

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
3. 選択したワークフローのテンプレートにパラメータを注入し、ComfyUI `/prompt` API に投入（`full` は 2 段）
4. WebSocket で進捗を UI に中継（`full` は「画像生成 (1/2)」→「動画生成 (2/2)」の 2 段表示）
5. 完了後、**生成画像・動画・音声**を ComfyUI から取得、**ラストフレーム**を ffmpeg で抽出
6. プロンプト・パラメータ・チャット履歴・成果物パスを SQLite に保存し、UI のギャラリー/履歴に表示

---

## 2. 動作モード

ワークフローは画像・動画・音声で分離しており、1 ジョブは **1 つまたは 2 つの ComfyUI プロンプト**で構成される。

| モード | 内部名 | 実行されるワークフロー | 開始フレーム |
|---|---|---|---|
| 画像＋動画 | `full` | 選択した画像ワークフロー → 選択した動画ワークフロー（2 段） | 1 段目の生成画像 |
| 動画生成 | `i2v` | 選択した動画ワークフローのみ | ワークフローが要求する入力（アップロード / 履歴 / なし） |
| 画像のみ | `image_only` | 選択した画像ワークフローのみ | ― |
| 音声 | `audio` | 選択した音声ワークフローのみ（独立ジョブ） | ― |

`audio` は他の 3 モードと連結しない独立モード。画像・動画のフィールド（`video_workflow` /
`source_image` / `loras` など）は一切使わず、指定すると 422 で拒否される（§2.4）。

### 2.1 「画像＋動画」の 2 ジョブ連結

旧方式のようにグラフを合体させず、同一 `job_id` のもとで順に実行する:

1. 選択した画像ワークフローを `/prompt` に投入 → 完了を待つ
2. `SaveImage` の出力を `/view` でダウンロードし `outputs/{job_id}/image.png` に保存
3. その画像を ComfyUI `/upload/image` で input ディレクトリへアップロード
4. 選択した動画ワークフローの `LoadImage` にそのファイル名を注入して投入 → 完了を待つ
5. 動画をダウンロードし、ffmpeg でラストフレームを抽出

- 進捗は 1 ジョブとして配信され、メッセージが「画像生成 (1/2)」→「動画生成 (2/2)」と切り替わる
- `workflow_json` には **両方のグラフ**を `{"image": {...}, "video": {...}}` の形で保存する（各要素は `workflow_id` / `prompt_id` / `graph`）。単段ジョブも同じ形（キーは `image` / `video` / `audio`）。再現性の担保はこれで行い、`rerun` は `params` から作り直す
- `full` で選べるのは**開始フレームを受け取れる動画ワークフローだけ**（`accepts_start_image`）。t2v と IC-LoRA リファレンスシートは対象外で、選択すると 422 になる

### 2.2 動画ワークフロー（`workflow/video/<family>/`）

| id | 表示名 | ckpt | 必要入力 | `full` 可 |
|---|---|---|---|---|
| `ltx2_3_t2v` | テキスト→動画 (t2v) | dev-fp8 | なし | ✕ |
| `tx2_3_i2v` | 画像→動画 (i2v) | dev-fp8 | 画像 | ○ |
| `tx2_3_ia2v` | 画像+音声→動画 (ia2v) | dev-fp8 | 画像・音声 | ○ |
| `ltx2_3_id_lora` | 画像+参照音声→動画・リップシンク (ID-LoRA) | dev-fp8 + talkvid ID-LoRA | 画像・音声 | ○（既定） |
| `ltx2_3_flf2v` | 最初と最後のフレーム指定 (flf2v) | distilled-fp8 | 画像・最終フレーム画像 | ○ |
| `ltx2_3_ic_lora_image` | リファレンスシート (IC-LoRA) | distilled-fp8 + ingredients IC-LoRA | リファレンスシート画像 | ✕ |
| `ltx2_3_ic_lora_motion` | 参照動画からモーション転写 (IC-LoRA + MoGe) | distilled-fp8 + union-control IC-LoRA | 画像・参照動画 | ○ |
| `wan_dancer` | 画像+音声→ダンス動画 (Wan Dancer) | wan2.2 global/local 2 段 + lightx2v | 画像・音声 | ○ |

- id はファイル名（拡張子なし）。`tx2_3_i2v` / `tx2_3_ia2v` の綴りは配布ファイル名そのまま
- **`wan_dancer`（`workflow/video/wan/`、family `wan`）** は LTX 系とは作りが違う: 渡した曲に合わせて踊る映像を作り、
  プロンプトは自由記述ではなく**選択式フィールド**（§3.1）で決まる。`video_prompt` は任意で、書けば Global 側の
  テンプレ文（`<dance style>` を含められる）を差し替える。ユーザー LoRA を挿すチェーンは持たないので、
  動画 LoRA を指定したジョブは 422 になり、フォームは LoRA（動画）欄を出さない
- 既定は `ltx2_3_id_lora`（旧 `video-gen.json` の動画側と同じ構成なので、既存ジョブ・エージェントの計画がそのまま通る）
- ラストフレーム連鎖: 履歴の動画から「ラストフレームを開始フレームにして続きを生成」できる。元ジョブの動画ワークフローが開始フレームを受け取れない場合は既定ワークフローにフォールバックする

### 2.3 画像ワークフロー（`workflow/image/<family>/`）

画像ワークフローも `image_workflow` でプルダウン選択する。フォルダ名がそのまま
**モデルファミリー**で、画像 LoRA の適用可否を決める（§3.4.1）。

| id | 表示名 | family | 必要入力 | 備考 |
|---|---|---|---|---|
| `krea2_turbo` | Krea 2 turbo | `krea2` | なし | 既定。text-to-image（`ResolutionSelector`） |
| `anima` | Anima | `anima` | なし | text-to-image、アニメ・イラスト系（`ResolutionSelector`） |
| `z_image_turbo` | Z-Image turbo | `z-image` | なし | text-to-image、8 steps 蒸留。ResolutionSelector が無いのでアプリが幅・高さを計算して注入 |
| `qwen_image_edit_2511` | Qwen-Image Edit 2511 | `qwen-image` | 画像（編集元画像） | **編集系**。`source_image` 必須で、出力解像度は入力画像から決まる（`aspect_ratio` / `megapixels` は無視） |

- 既定は `krea2_turbo`（選択式になる前の唯一の画像ワークフロー）
- `qwen_image_edit_2511` は画像ステージが走るモード（`full` / `image_only`）で必ず `source_image` を要求する。
  `full` では編集結果がそのまま 2 段目の開始フレームになる
- `image_prompt` の書き方はファミリーごとに違い（krea2 は長い自然文、qwen は編集指示）、
  Grok のシステムプロンプトにはファミリー別のガイドが埋め込まれる（§4.2）

### 2.4 音声ワークフロー（`workflow/audio/`）

`mode: "audio"` のときだけ走る**独立した 1 ステージ**。開始フレームを取らず、生成もしない。
LoRA チェーンも持たない（テンプレートに LoRA ノードが無い）ので、LoRA を指定したジョブは 422 になる。

| id | 表示名 | family | 秒数（min/既定/max） | 固有フィールド |
|---|---|---|---|---|
| `ace_step1_5_xl_sft` | ACE-Step 1.5 XL（音楽・歌もの） | `ace-step` | 10 / 120 / 600 | `lyrics`（空でインスト）・`bpm`（10-300）・`keyscale`・`language` |
| `stable_audio_3_medium_base` | Stable Audio 3 Medium（効果音・環境音・音楽） | `stable-audio` | 1 / 60 / 380 | `audio_category`（Music / Instrument / SFX / One-shot）・`reprompt`（内蔵 LLM でのプロンプト展開） |

- 既定は `ace_step1_5_xl_sft`
- ジョブの必須項目は `audio_prompt` のみ。`duration` がワークフローの範囲外、`keyscale` / `language` /
  `audio_category` が ComfyUI ノードの COMBO 値に無い、`bpm` が範囲外、といったものはジョブ投入前に 422 で弾く
  （どれも ComfyUI 側で prompt 全体が失敗するため）
- 出力は mp3（`SaveAudioMP3`）で `outputs/{job_id}/audio.mp3` に保存し、`jobs.audio_output_path` に記録する
- 秒数の上下限・COMBO 値の一覧は `backend/app/workflows.py`（`min_duration` / `max_duration` /
  `KEYSCALES` / `LANGUAGES` / `BPM_RANGE` / `AUDIO_CATEGORIES`）が単一の情報源で、
  フォーム・Grok カタログ・バリデータが同じ集合を見る

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
| 画像ワークフロー | ― | プルダウン（`/api/options` の `image_workflows`）。画像ステージが走るモードでのみ表示 |
| 音声ワークフロー | ― | プルダウン（`/api/options` の `audio_workflows`）。`mode: "audio"` でのみ表示 |
| アスペクト比 / メガピクセル | 画像: `aspect_ratio` / `megapixels` → ResolutionSelector（krea2 は `49`、anima は `91`）。z-image と動画: アプリが幅・高さを計算して `width` / `height` に注入。qwen-image-edit は入力画像から決まるので注入しない | セレクト（選択肢は `/object_info` の ResolutionSelector から動的取得）+ 数値 |
| 音声プロンプト・歌詞・BPM・キー・言語・カテゴリ・展開 | `prompt` / `lyrics` / `bpm` / `keyscale` / `language` / `audio_category` / `reprompt` | `mode: "audio"` のみ。選択中の音声ワークフローが露出しているつまみだけ表示 |
| LoRA（画像・複数可） | 画像ワークフローの `lora_chain` を動的構築（§3.4） | 「LoRA（画像）」セクション。登録 LoRA のうち `target = 'image'` かつ**選択中の画像ワークフローと同じファミリー**のものを複数選択＋強度スライダー |
| LoRA トリガーワード（画像） | `trigger_concat` → `30:27` (StringConcatenate) / `trigger_switch` → `30:28`。この 2 つを持つのは krea2 テンプレートだけで、他の画像ワークフローには自動前置の口が無い（トリガーワードは `image_prompt` 本文に書く） | 選択 LoRA のトリガーワードを自動連結（編集可） |
| LoRA（動画・複数可） | 動画ワークフローの `lora_chain` を動的構築（§3.4） | 「LoRA（動画）」セクション。登録 LoRA のうち `target = 'video'` のものを複数選択＋強度スライダー |
| LoRA トリガーワード（動画） | 動画プロンプト文字列の先頭に前置 | 同上（自動連結・編集可） |
| リファレンス音声 | `audio` → `276` (LoadAudio)。要求するワークフローのみ | アップロード（`/upload/image` で送信 → ファイル名を注入） |
| 開始フレーム / 最終フレーム / 参照動画 | `image` / `end_image` / `video` | ワークフローの必要入力に応じて表示。画像は D&D・履歴のラストフレームからも選べる |
| 秒数 (Duration) | `duration` | 数値・**上限なし**。長尺は VRAM 次第で ComfyUI 側エラーになり得ることを UI に注記。`duration` を持たないワークフロー（wan_dancer は尺を選択式で持つ）では欄ごと出さない |
| 選択式フィールド | ワークフローの `selects`（論理名 → CustomCombo 等） | 宣言のあるワークフローだけ、ワークフローセレクトの直下にプルダウンが並ぶ（下記） |
| フレームレート | `fps` | 数値（既定 25） |
| 画像・動画プロンプト | `prompt`（画像 `30:19` / 動画は各テンプレート） | テキストエリア（手動入力が基本。Grok チャット §4.3 の結果を反映して編集も可） |
| 動画ネガティブ | `negative` | プリセット切替（ワークフロー既定 / 現行値 / モデル作者版）+ 直接編集可。**空欄ならテンプレート既定値のまま**（dev 系は `pc game, …`、distilled 系は品質ネガ） |

#### 選択式フィールド（`WorkflowSpec.selects`）

自由記述ではなく**決まった選択肢**で挙動が決まるワークフローのための汎用の仕組み。
`SelectSpec(label, choices, target, default, index_field, numeric_target, auto, hint)` を
マニフェストに宣言すると、

- 生成フォームが選択肢からプルダウンを自動生成し（`WorkflowSelects`）、
- ジョブは `selects: {"<論理名>": "<選んだ値>"}` で値を持ち（宣言外の名前・選択肢外の値は 422。
  検証は `models.select_problem` で Web UI とエージェント共通）、
- エージェントのワークフローカタログにも選択肢がそのまま載る（`prompts._select_lines`）。

宣言していないワークフローでは何も増えないので、既存の挙動は変わらない。注入時の要点:

- ComfyUI の `CustomCombo` は選んだ文字列（`choice`）と 0 始まりの番号（`index`）を持ち、
  **グラフが読むのは番号側**（`choice` は表示用。番号で「n 行目」を引く RegexExtract に繋がる）。
  そのため両方を書き込む。`validate_specs()` は選択肢がテンプレートの `option*` と一致するかも見る
- `numeric_target` があれば同じ値を数値としても入れる（wan_dancer の尺はコンボと
  `TrimAudioDuration.duration` の両方に入れないと、映像だけ伸びて音声は 25 秒で切れる）
- `auto: "audio_duration"` の項目は、**未指定なら入力音声の実長**（`jobs.probe_media_duration`、
  ffprobe）を選択肢に切り上げて決める（上限は最大の選択肢）。決めた値は params に残るので
  再実行でも同じ尺になる。ffprobe が無い・読めない場合は宣言した既定値に落ちる（登録は止めない）
- UI は `auto` の項目に「自動（入力に合わせる）」、それ以外に「既定（<値>）」を先頭の選択肢として置く

#### 解像度の計算

画像側は ResolutionSelector を持つテンプレート（krea2 の `49` / anima の `91`）にアスペクト比と
メガピクセルをそのまま渡す。ResolutionSelector を持たない z-image は、下の式で計算した幅・高さを
`EmptySD3LatentImage` に直接注入する。qwen-image-edit は入力画像から解像度が決まる
（`FluxKontextImageScale`）ので、どちらも注入しない。
動画側の新テンプレートは幅・高さの `PrimitiveInt` 指定になったため、アプリが同じ式で計算する
（wan_dancer も同じ扱い。テンプレート既定は 720x1280 だが、開始フレームがあればその実比に従う）
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
読めなかった場合はプリセットにフォールバックする。`full` モードでは 1 段目の生成画像を
2 段目に渡す時点で `start_image_size` を捨てるので、2 段目はプリセットで計算する
（生成画像はプリセット通りの比で出るため。ただし解像度が入力画像依存の
`qwen_image_edit_2511` を 1 段目に選んだ場合だけは、両者がずれることがある）。

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
| 画像 / 動画 / 音声プロンプト | `prompt` | フォームの確定値（手動 or Grok チャット反映後） |
| 画像 seed | `seed`（krea2 は `30:3` の `KSampler.seed`。テンプレートごとに異なる） | 実行毎にランダム（固定オプションあり）。`params` に保存して再現可能 |
| 動画 noise seed | `seeds`（低解像度パス + アップスケールパスの `RandomNoise`、IC-LoRA 系は `KSampler.seed`） | 同上。seed が 1 個しか渡らない場合は全サンプラーで共用 |
| 音声 seed | `seed`（ACE-Step は `109` の `PrimitiveInt`、Stable Audio は `KSampler.seed`） | 同上（`params` には `audio_seed` として保存） |
| 音声の長さ | `duration` / `latent_seconds` | ACE-Step は conditioning と空ラテントの両方に同じ秒数を入れる。Stable Audio は空ラテントが同じ `PrimitiveFloat` を読むので 1 か所 |
| 出力プレフィックス | `save_prefix` | 画像 `images/{job_id}` / 動画 `video/{job_id}` / 音声 `audio/{job_id}` にして成果物とジョブを紐付け |
| ローカル LLM リファイン | `refine_enable` → `30:24`（krea2 のみ） | **false 固定**（プロンプト整形は Grok が担う）。`ComfySwitchNode` は遅延評価（`check_lazy_status`）なので `30:16` (TextGenerate) は実行されない |
| プロンプト拡張 | `prompt_enhance` → 各テンプレートの `Boolean (Enable Prompt Enhance)` | **false 固定**（同上）。IC-LoRA 系は false なのでスイッチのリテラル側 `on_false` にプロンプトを注入する |

Stable Audio の `reprompt`（内蔵 LLM でのプロンプト展開）だけは例外で、**ユーザーが選ぶ**
チェックボックスとしてフォーム / ジョブのフィールドになっている（既定 false）。

### 3.3 固定（触らない）ノード

- 画像側: 各ファミリーの UNET / CLIP / VAE（krea2 = `krea2_turbo_fp8_scaled` + `qwen3vl_4b_fp8_scaled` + `qwen_image_vae`、anima = `anima-base-v1.0`、z-image = `z_image_turbo_bf16`、qwen-image = `qwen_image_edit_2511_int8_convrot` + Lightning 4steps LoRA）と KSampler 設定
- 音声側: ACE-Step `acestep_v1.5_xl_sft_bf16` + `qwen_0.6b_ace15` / `qwen_4b_ace15` + `ace_1.5_vae`、Stable Audio `stable_audio_3_medium_base` + `t5gemma_b_b_ul2` / `qwen3.5_2b_bf16`、およびサンプラー設定
- 動画側: checkpoint `ltx-2.3-22b-dev-fp8` または `ltx-2.3-22b-distilled-fp8`、distil LoRA (strength 0.5)、talkvid ID-LoRA + `LTXVReferenceAudio`（identity_guidance_scale 3）、IC-LoRA と MoGe、2 段サンプリング（半解像度 → LatentUpsampler x2）、ManualSigmas
- **モデルファイル名は利用者の ComfyUI 環境依存**のため、設定ページ（`GET/PUT /api/models`）で上書き可能。既定値は各テンプレートの値。対象は UNETLoader.unet_name / CLIPLoader.clip_name / CLIPVisionLoader.clip_name / VAELoader.vae_name / CheckpointLoaderSimple.ckpt_name / LTXVAudioVAELoader.ckpt_name / LTXAVTextEncoderLoader.text_encoder・ckpt_name / LatentUpscaleModelLoader.model_name / LoadMoGeModel.model_name / LoraLoaderModelOnly.lora_name / LoraLoader.lora_name（§3.4 で削除される画像テンプレートのプレースホルダは除く。LTX 側の固定 LoRA ノードや qwen-image の Lightning LoRA はユーザー LoRA と共存するので上書き対象のまま）
- 上書きキーは**ワークフロー ID でスコープ**する: `"<workflow_id>/<node_id>.<field>": "<ファイル名>"`。テンプレート間で同じノード ID（例: `340:317` が ia2v と id_lora の両方にある）が衝突しないため。旧レイアウトの非スコープキーは無視される（マイグレーション不要）
- **実行ごとのモデル切り替え**: 同じキー形式で「そのスロットで選べるファイル名」を設定に持てる（`Settings.model_choices`、`GET/PUT /api/models` で読み書き）。既定値（`model_overrides` → 無ければテンプレート値）と合わせて **2 件以上**になったスロットは *switchable* とみなし、`GET /api/options` の `model_slots`（キー・ラベル・既定値・候補一覧）に出す。ジョブは `model_overrides`（`JobCreate` / `JobContinue` のフィールド）で 1 回ぶんだけ差し替えられ、実行時に設定の既定値の上へマージされる（`jobs.run_job`）。検証（`models.model_override_problem`、Web UI とエージェントで共通）は「キーが `model_fields()` に存在」「そのジョブが走らせるワークフロー（`models.job_workflow_ids`）に属する」「値が候補（既定値を含む）に入っている」を満たさないものを 422 で拒否する。再実行は params ごと引き継ぎ、続き生成は動画ワークフローぶんのキーだけを引き継ぐ（`workflow.scoped_model_overrides`）
- **不足モデルの自動ダウンロード**: ComfyUI 本体にも Comfy Cloud にもモデル取得 API は無いので、**バックエンドが自分でダウンロードして** ComfyUI の models ディレクトリ（**環境変数 `COMFY_MODELS_DIR`**）へ直接置く（ComfyUI はフォルダの mtime を見て一覧を作り直すので再起動は不要）。設定ページは「その行の値が `GET /api/options` の `model_files` の該当リストに無い」ことを不足の判定に使い、**未検出**バッジ・URL 入力欄・[DL] ボタンを出す（`model_files` が空＝ComfyUI 未接続のときは判定しない）
  - 置き場所は `class_type`＋入力フィールドから決める（`workflow.MODEL_SUBFOLDERS` → `ModelField.subfolder`）: checkpoints = CheckpointLoaderSimple.ckpt_name / LTXVAudioVAELoader.ckpt_name / LTXAVTextEncoderLoader.ckpt_name、diffusion_models = UNETLoader.unet_name、text_encoders = CLIPLoader.clip_name / DualCLIPLoader.clip_name1・clip_name2 / LTXAVTextEncoderLoader.text_encoder、clip_vision = CLIPVisionLoader.clip_name、vae = VAELoader.vae_name、loras = LoraLoader.lora_name / LoraLoaderModelOnly.lora_name、latent_upscale_models = LatentUpscaleModelLoader.model_name、geometry_estimation = LoadMoGeModel.model_name。未知のローダーは空（＝ UI で入力させる。当てずっぽうに置いても ComfyUI からは見えない）
  - `POST /api/models/download` は保存先を検証（`..` / 絶対パス / パス区切りを拒否し、`resolve()` 後に models ディレクトリ配下であることを確認）してからバックグラウンドタスクを起こす。httpx のストリームをチャンクで `<ファイル名>.part` に書き、完走したときだけ本来の名前に `rename` する（失敗・中断時は `.part` を削除）。進捗は WS `/api/ws` に `type: "model_download"` として流れる。同じファイル名の同時ダウンロードは 409
  - 認証は URL のホストで出し分ける: huggingface.co / hf.co（サブドメイン含む）は `Settings.hf_token`、civitai.com は `Settings.civitai_api_key` を `Authorization: Bearer …` として付ける（未設定なら付けない）。**リダイレクトは httpx に任せず自分で追う**（最大 10 ホップ、相対 `Location` は urljoin で解決、301/302/303/307/308 を GET のまま追う）: クライアント既定ヘッダに認証を載せると転送先の別ホストにトークンが漏れるため、ホップごとに URL を再検証して認証ヘッダを計算し直し、そのリクエストにだけ渡す（HF → `*.hf.co` の CDN には付き、無関係なホストには付かない）。URL はファイル名ごとに `Settings.model_download_urls` へ保存する（同じファイルが複数スロットに出るため、キーはスロットではなくファイル名）
  - 保存先は**環境変数 `COMFY_MODELS_DIR` だけ**が決める（設定 `runtime/config.json` には持たない）。UI からパスを入れられても、Docker で同じ絶対パスをマウントしていなければ書けないため。`.env` に書けば `run.sh`（ホスト実行、`.env` を読んで `export`）と `docker compose`（同一パスのマウント＋`environment:` で受け渡し）の双方に効く。設定に残すのは `hf_token` / `civitai_api_key` / `model_download_urls` だけで、旧バージョンが書いた `comfy_models_dir` キーは読み込み時に捨てる
  - `GET /api/models/dir-status` が `{configured, exists, writable, path}` を返す。`configured` は環境変数が設定されているか。**未設定なら機能ごと無効**で、UI はダウンロード関連（接続タブのブロック、モデルタブの列）を一切出さず警告も出さない（Comfy Cloud 利用などでは正常な状態）。設定済みなのに `exists=false`（Docker でマウントしていない等）／`writable=false` のときは表示したうえで理由を出す

#### ローカル ComfyUI でモデル名が違うときの直し方

テンプレートの既定モデル名は Comfy Cloud のストレージに合わせてあるため、ローカルの ComfyUI では
ファイル名が違うことがある。ノード ID はモデルのドロップダウンを変えただけでは変わらない
（変わるのはノードの追加・削除・サブグラフの再構成をしたとき）。一方で `workflow/` の JSON は
**API フォーマットなので ComfyUI の GUI には直接読み込めない**。直し方は 3 つ:

1. **設定ページ「モデル」タブで上書きする（推奨）**: テンプレートを一切触らず、環境差分だけが
   `model_overrides` に保存される。リポジトリの更新（テンプレート差し替え）とも衝突しない
2. **ワークフロー JSON をテキストエディタで直接編集する**: モデルファイル名の文字列だけを
   書き換える範囲ならノード ID は変わらないので安全。既定値そのものが変わるので上書き設定は不要になる
3. **元の GUI 用ワークフロー（API エクスポート前のもの）を持っている場合**は、GUI でモデルだけ変更して
   API フォーマットに再エクスポートし `workflow/` のファイルを差し替えてもよい。ただしノードの追加・削除や
   サブグラフの編集をするとノード ID が変わり、注入マニフェスト（ノード ID 直指定）と食い違う

ID がズレた場合は起動時の検証と `GET /api/health` が検知して警告するので、`backend/app/workflows.py` の
マニフェストを新しいノード ID に合わせて直す（§3.0）。

### 3.4 複数 LoRA の動的注入

LoRA は**登録時に対象（`target`）を選ぶ**: `image` なら画像ワークフロー、
`video` なら動画ワークフロー（LTX 2.3）に注入される。ジョブは両者を別フィールドで持つ
（`loras` / `trigger_text` と `video_loras` / `video_trigger_text`）。
音声ワークフローは LoRA チェーンを持たないので、`mode: "audio"` に LoRA を指定すると 422 になる。

**モデルファミリー**: 画像 LoRA はさらに登録時に学習元のファミリー（`krea2` / `anima` /
`z-image` / `qwen-image`）を選ぶ。別ファミリーの LoRA はロードできても破綻した出力になるため、
`loras` に選択中の `image_workflow` と違うファミリーが混ざったジョブは 422 で拒否する
（`models.image_lora_family_problem`）。フォームの LoRA ピッカーも同じファミリーのものだけを出し、
エージェントのシステムプロンプトには LoRA ごとのファミリーが明記される。
動画 LoRA は LTX 2.3 しか無いのでファミリーを使わない。

#### 3.4.1 画像 LoRA チェーン

人物 LoRA を複数同時に適用できるよう、画像テンプレートが持つプレースホルダの
`LoraLoaderModelOnly`（strength 0。krea2 は 5 個 `30:61:*`、anima は `90:83`、z-image は `57:63`）は
使わず、アプリが API JSON 生成時に **LoRA チェーンを動的に構築**する（以下は krea2 の例）:

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
  - `trigger_concat` / `prompt_source` を持つのは krea2 テンプレートだけなので、**他の画像ワークフローではトリガーワードの自動前置は行われない**（Grok には `image_prompt` 本文でトリガーワードを主語として使うよう指示している）
- ファミリー別の head / consumers: anima は `90:78` (UNETLoader) → `90:76` KSampler.model、
  z-image は `57:28` (UNETLoader) → `57:11` ModelSamplingAuraFlow.model、
  qwen-image は `170:152` (CFGNorm) → `170:153` Lightning LoRA.model と `170:163` Switch.on_false
  （Lightning 4steps LoRA の前段に挿し、4steps のオン / オフどちらでもユーザー LoRA が効く）

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

プロンプト作成は**手動が基本**。Grok を使う場合はチャット形式（§4.3）で要件を掘り下げ、最終的に JSON（`image_prompt`, `video_prompt`, `notes`。`mode: "audio"` では `audio_prompt`, `lyrics`, `bpm`, `keyscale`, `language`, `notes`）を出力させてフォームに反映する。システムプロンプトに各モデルのプロンプト仕様を埋め込む。

チャットのシステムプロンプトには**選択中のワークフローに対応する仕様だけ**を入れる
（画像はファミリー別、動画はワークフロー別、音声はモデル別）。エージェントは 1 セッションで
複数のワークフローを使い分けるので、全ファミリー・全モデルのガイドをまとめて焼き込む
（`prompts.image_prompt_guides_section()` / `audio_prompt_guides_section()`）。

**実例集**: Civitai の公開ギャラリー（モデル作者投稿の動画・画像）に埋め込まれたワークフローから実際のプロンプトを抽出し、`docs/prompt-samples.md` にまとめた。Grok のシステムプロンプトには、この実例を few-shot として埋め込むこと。実例から得られた重要な知見:

- 動画プロンプトは `<シーン種別> scene.` の宣言で始めるのが作者流（例: "voyeur style ... scene."）
- **引用符 `"..."` で囲んだセリフはそのまま音声合成される**（英語のセリフ+話者の声質形容: "in a british voice she says …"）。セリフ機能を UI のオプションとして扱う
- 音・声の描写（moaning, sigh, 効果音）を文中に散りばめる
- 作者が実際に使うネガティブは品質系+音声系（blurry, …, distorted sound, saturated loud 等）で、テンプレート既定値と異なる。**アプリからネガティブも選択可能にする**（既定は現行値、プリセットで作者版を用意）
- RedCraft 画像プロンプトは品質語プレフィックス（例: "masterpiece, very aesthetic"）+ 自然文 1 段落が実例でも主流

**画像プロンプト（ファミリー別）**

画像ワークフローが 4 種になったため、`image_prompt` の書き方は**モデルファミリーごとに別仕様**
（`prompts.IMAGE_SPECS`。いずれも各モデルの公式ガイドに準拠）:

| family | 要点 |
|---|---|
| `krea2` | 自然文 1 段落・長く詳細に（下記） |
| `anima` | 品質＋レーティングタグ → Danbooru 系タグ（小文字・アンダースコアなし、絵師タグは `@` 前置）。自然文の併記も可。ネガティブはテンプレート固定なので書かない |
| `z-image` | 長く密度の高い自然文 1 段落（フォトリアル寄り、英中の文字描画が得意）。CFG 蒸留なのでネガティブは書かない |
| `qwen-image` | シーン描写ではなく**編集指示**（「X を Y に変える、それ以外は変えない」）。出力サイズは入力画像に従うので構図・比率は指示しない |

**画像プロンプト（krea2 = Krea 2 turbo、TE は Qwen3-VL 4B）**

- Krea 2 公式ガイド（krea-ai/krea-2 `docs/prompting.md`）準拠: **自然文 1 段落・長く詳細なほど良い**。画像内に文字を描画する場合は対象語を引用符で囲む
- Grok 用システムプロンプトは Krea 2 公式の LLM 拡張プロンプト（`workflow/image/krea2/krea2_turbo.json` のノード `30:18` に同一物が組込済み）をベースに、本アプリの用途・LoRA トリガー・出力 JSON 形式に合わせて調整する
- 構成順序（ワークフロー内の既存プロンプトをテンプレートとして踏襲）:
  1. LoRA トリガーワード（LoRA 有効時。Grok には表示名→トリガーワードの対応表を渡し、`image_prompt` の被写体名としてトリガーワードを文中で使わせる。未使用の語だけをアプリが `30:27` で先頭に補完する。他のファミリーは補完しない）
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

**音声プロンプト（`mode: "audio"`）**

`prompts.ACE_STEP_AUDIO_SPEC` / `STABLE_AUDIO_SPEC` を、選択中の音声ワークフローに応じて埋め込む
（出典は ACE-Step 1.5 と Stable Audio 3 の公式ドキュメント、および ComfyUI の各ノード実装）:

- **ACE-Step 1.5**: `audio_prompt` は曲そのものの**キャプション**（ジャンル・雰囲気・楽器と音色・
  プロダクション・テンポ感・ボーカルの声質）。歌詞は `audio_prompt` ではなく `lyrics` に、
  `[Verse]` / `[Chorus]` の構造タグ付きで書く。`bpm` / `keyscale` / `language` も Grok が提案する
- **Stable Audio 3**: 音そのものを説明する短い自然文 1 つ（音楽ならジャンル・楽器・ムード・テンポ、
  効果音なら音源・素材・空間）。歌わないので歌詞は書かない。ネガティブプロンプトは公式にも
  テンプレートにも存在しないので書かない
- カテゴリ（`audio_category`）は Grok ではなくフォームで選ぶ

### 4.3 チャット型プロンプト作成フロー

「かおりが楽しそうにダンスをしている」のような雑な指示から Grok が勝手に決め打ちで生成してしまうのを防ぐため、**インタビュー形式のチャット UI** を設ける。

フロー:

1. フォームの「Grokで生成」ボタン → チャットパネル（モーダル）を開く。フォームの現在値（モード、**選択中の動画 / 画像 / 音声ワークフロー** `video_workflow` / `image_workflow` / `audio_workflow`、選択 LoRA とトリガーワード、秒数、既存プロンプト下書き（音声モードでは `audio_prompt_draft` / `lyrics_draft`））がコンテキストとして自動で渡る
2. ユーザーが作りたいものをひとこと入力（例: 「かおりが楽しそうにダンスをしている」）
3. Grok は**不足情報を質問で聞き返す**よう指示されている: 場所・服装・時間帯/照明・カメラ（ショットスケール/動き）・表情/ムード・セリフや音・動きの展開など。ユーザーが「おまかせ」と言えば残りは Grok が補完
4. 情報が揃ったら Grok が `image_prompt` / `video_prompt` の最終案を JSON で提示 → 「フォームに反映」ボタンでプロンプト欄へ挿入
5. 反映後もチャットを続けて再調整可能（「もっと引きのカメラで」等 → 更新版 JSON を再提示）

実装:

- grok CLI のヘッドレス実行（`grok -p`）は 1 発呼び出しのため、**会話履歴はアプリ側で保持**し、毎ターン「システムプロンプト + 履歴全文 + 最新発言」を組み立てて渡す
- システムプロンプトの構成: ①役割（プロンプトエンジニア兼インタビュアー）②各モデルのプロンプト仕様（§4.2。画像は選択中ワークフローのファミリーのものだけ）+ few-shot 実例（docs/prompt-samples.md）③ヒアリング項目チェックリスト ④選択中の画像・動画ワークフローの特性（下記）⑤最終出力は ```json フェンス内の `{image_prompt, video_prompt, notes}` のみ、というルール
- `mode: "audio"` では専用のシステムプロンプト（`build_audio_system_prompt`）に切り替わる: 選択中の音声ワークフローの仕様とそのモデルが読むフィールドだけを提示し、出力は `{audio_prompt, lyrics, bpm, keyscale, language, notes}`。画像・動画のプロンプトは書かせない。フォーム側も、選択中のワークフローが持たないつまみ（Stable Audio の `lyrics` など）は反映しない
- **ワークフロー特性の反映**: CONTEXT には選択中の `video_workflow` の用途・必要入力・音声の扱い・`video_prompt` の書き方と、`image_workflow` の用途・ファミリー・必要入力・`image_prompt` の書き方を出す。文面は `app/workflows.py` の `WorkflowSpec`（`description` / `audio_role` / `prompt_hint`）から自動生成する単一情報源なので、ワークフローを追加したらマニフェスト側に書けばチャット・エージェント両方に反映される（未記入は `validate_specs()` = ヘルスチェックで検出）。例: flf2v なら開始→終了フレーム間の遷移を書かせる、t2v / リファレンスシート IC-LoRA なら開始フレーム前提にしない、ia2v なら渡した音声がそのまま音声トラックになるのでセリフをプロンプトに書かせない、ic_lora_motion ならカメラ・テンポは参照動画由来なので書かせない
- 応答の判定: 応答に JSON フェンスがあれば「最終案の提示」、なければ「質問継続」として UI に表示
- 十分詳細な初回入力なら Grok は質問を飛ばして即 JSON を返してよい（ワンショット生成はチャットの特殊ケースとして自然に実現）
- モード B ではスタートフレーム画像を grok 作業ディレクトリにコピーし、CLI に読ませて内容を踏まえた `video_prompt` を作らせる（読めない場合はテキストのみでフォールバック）。ワークフローが開始フレームを取らない場合（t2v 等）はモード B でも「見た目もプロンプトで決める」指示に切り替わる
- チャット履歴は `chat_sessions` に保存し、ジョブに紐付ける（後から「どういう指示で作ったか」を追える）

---

## 5. ComfyUI 連携

- 接続先: `http://<comfy-host>:8188`（設定画面で URL 変更可）。実行環境はローカル / LAN 上の別 PC / Comfy Cloud のいずれでも動くよう、ComfyUI クライアントは「接続 URL + 任意の認証ヘッダー（API キー）」を設定できる抽象化された 1 モジュールにする
- **Comfy Cloud**: Cloud 向けのエンドポイント URL と認証設定を設定画面から入力できる（ホストが `comfy.org` のとき自動で Cloud 互換モード）。使い方は「`comfy_url` に `https://cloud.comfy.org`」＋「[API キー発行ページ](https://docs.comfy.org/development/cloud/overview) で作ったキーを `comfy_api_key` に設定」の 2 手順。Cloud 互換モードではエンドポイントに `/api` プレフィックスが付き、認証は `X-API-Key` ヘッダー、`/view` は 302 署名 URL リダイレクトを追う。API アクセスは有料プラン（Standard 以上）が必要で Free では使えない。ワークフローが参照するモデル・LoRA・リファレンス音声は Cloud 側のストレージに存在している必要がある（ファイルシステムに届かないので §3.3 の自動ダウンロードは使えない）
- 使用 API:
  - `GET /object_info` … ResolutionSelector のアスペクト比選択肢、LoRA 一覧、class_type の存在確認
  - `POST /upload/image` … 開始フレーム画像・リファレンス音声・参照動画、および**`full` 1 段目の生成画像**のアップロード（ComfyUI はこのエンドポイントで input ディレクトリに任意ファイルを受ける）
  - `POST /prompt` … ワークフロー投入（`client_id` を付与）。`full` は 1 ジョブで 2 回投入する
  - `WS /ws?clientId=…` … 進捗（ノード実行状況・プレビュー）の受信
  - `GET /history/{prompt_id}` … 出力ファイル名の取得
  - `GET /view?filename=…&type=output` … 成果物ダウンロード
- 同時実行は 1 ジョブ（ComfyUI 側キューに任せるが、アプリ側でもジョブキューを持ち順次投入）
- タイムアウト・ComfyUI 未起動・ノード不足（custom nodes 未導入）はジョブを failed にして UI に理由を表示

## 6. 成果物の取得

| 成果物 | 取得方法 |
|---|---|
| 生成画像 | 画像ワークフローの `SaveImage` / `SaveImageAdvanced` の出力を history から取得し `/view` でダウンロードして `outputs/{job_id}/image.png` に保存。出力ノード ID はワークフローごとに異なる（`29` / `46` / `9` / `195`）ためマニフェストの `output_node` を使う |
| 動画 | 動画ワークフローの `SaveVideo` の出力ファイルを `/view` でダウンロードし `outputs/{job_id}/video.mp4` に保存。出力ノード ID はワークフローごとに異なる（`75` / `341` / `68`）ためマニフェストの `output_node` を使う |
| 音声 | 音声ワークフローの `SaveAudioMP3`（`107` / `19`）の出力を `outputs/{job_id}/audio.mp3` に保存し `jobs.audio_output_path` に記録する |
| ラストフレーム | ダウンロードした動画から ffmpeg で抽出: `ffmpeg -sseof -0.5 -i video.mp4 -update 1 -q:v 1 last_frame.png`（次回生成の開始フレームに再利用可能） |

---

## 7. データ永続化

SQLite（`app.db`）+ ファイルストア（`outputs/` = 生成物、`assets/` = アップロード、`library/` = 取っておく素材 §7.2）。

```sql
CREATE TABLE jobs (
  id            TEXT PRIMARY KEY,          -- ULID
  created_at    TEXT NOT NULL,
  mode          TEXT NOT NULL,             -- 'full' | 'i2v' | 'image_only' | 'audio'
  status        TEXT NOT NULL,             -- queued | prompting | running | done | failed | canceled
  user_input    TEXT,                      -- Grok チャットでの最初の指示（手動作成時は NULL）
  image_prompt  TEXT,                      -- Grok 生成（編集後の最終値）
  video_prompt  TEXT,
  audio_prompt  TEXT,                      -- mode 'audio' の指示（曲・音のキャプション）
  grok_raw      TEXT,                      -- Grok の生レスポンス(JSON)
  params        TEXT NOT NULL,             -- ワークフローID/アスペクト比/MP/LoRA/秒数/fps/seed 等の JSON
  workflow_json TEXT NOT NULL,             -- 投入した API JSON（{"image": …, "video": …, "audio": …} の段階別）
  comfy_prompt_id TEXT,
  image_path    TEXT,
  video_path    TEXT,
  last_frame_path TEXT,
  source_image  TEXT,                      -- 開始フレーム（アップロード元 or 参照した job id）
  audio_path    TEXT,                      -- リファレンス音声（入力）
  audio_output_path TEXT,                  -- mode 'audio' が生成した mp3（出力）
  error         TEXT,
  nsfw          INTEGER NOT NULL DEFAULT 0,
  nsfw_source   TEXT NOT NULL DEFAULT ''   -- 判定の出所（auto / manual）
);
```

- `params` には `video_workflow` / `image_workflow` / `audio_workflow`（ワークフロー ID）と、`end_image` / `reference_video`、音声モードの `audio_prompt` / `lyrics` / `bpm` / `keyscale` / `language` / `audio_category` / `reprompt` / `audio_seed` も保存する
- 後から足したカラム（`nsfw` / `nsfw_source` / `audio_prompt` / `audio_output_path` など）は起動時に `PRAGMA table_info` と突き合わせて不足分だけ `ALTER TABLE` する（`db.MIGRATIONS`）
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
  target        TEXT NOT NULL DEFAULT 'image', -- 'image' = 画像WF / 'video' = 動画WF（§3.4）
  family        TEXT NOT NULL DEFAULT 'krea2'  -- 画像 LoRA のモデルファミリー（§3.4）
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
- `family` も後から追加したカラム。画像ワークフローが選択式になる前の登録はすべて Krea 2 用なので、既定値の `krea2` がそのまま移行後の値になる（`target = 'video'` の行では使わない）
- ジョブの `params` には選択した LoRA の配列 `[{lora_name, trigger_word, strength}]` を**画像用 `loras` と動画用 `video_loras` に分けて**スナップショット保存（後から登録リストを変更しても過去ジョブの再現性を保つ）。`video_loras` / `video_trigger_text` を持たない古い params は空として読む
- 複数 LoRA 選択時の既定リファレンス音声は、選択順で最初に `default_audio` を持つ LoRA の値を採用（手動変更可）
- 初期データは持たない（LoRA は利用者の環境依存データのため、設定画面の LoRA 管理から登録する）

### 7.2 ライブラリ（取っておく素材）

履歴（ジョブ）は生成の記録なので、消せば成果物も消える。**ライブラリ**はそこから「残す」と決めたものと、
手元からアップロードしたものを集めた棚で、ジョブとは独立に生き続け、次の生成の入力素材として選べる。

```sql
CREATE TABLE library (
  id            TEXT PRIMARY KEY,          -- ULID
  created_at    TEXT NOT NULL,
  kind          TEXT NOT NULL,             -- 'image' | 'video' | 'audio'
  name          TEXT NOT NULL,             -- 表示名（既定はファイル名 / 元ジョブのプロンプト。変更可）
  path          TEXT NOT NULL,             -- library/{kind}/… の絶対パス
  nsfw          INTEGER NOT NULL DEFAULT 0,
  nsfw_source   TEXT NOT NULL DEFAULT '',  -- '' / 'auto'（元ジョブから継承） / 'manual'
  source_job_id TEXT,                      -- 生成物から登録した場合の元ジョブ（アップロードは NULL）
  tags          TEXT NOT NULL DEFAULT '[]' -- 分類タグの JSON 配列（後から足したカラム）
);
```

- ファイル実体は **`library/{kind}/`**（`paths.LIBRARY_DIR`）に置き、`/library` の静的マウントで配信する。
  受け付ける拡張子は `assets/` と同じホワイトリスト（`library.ALLOWED_EXT`）
- 登録経路は 2 つ: **アップロード**（`POST /api/library/{kind}`）と、**生成物から**
  （`POST /api/library/from-job`、`source` は `image` / `last_frame` / `video` / `audio`）。後者は
  `outputs/` から **コピー**するので元のジョブを消しても残り、NSFW フラグは元ジョブから引き継ぐ（`auto`）
- **二重登録の検知**: 同じ `source_job_id` × 同じ `source` が既にあるときはコピーを増やさず
  **409**（`{message, item}` で既存アイテムを添える）を返す。`source_job_id` だけでは生成画像と
  ラストフレームを区別できないため `source` カラムを持つ。`source` を足す前の行は NULL なので
  重複判定の対象外（既存データを壊さない）。アップロードは元ジョブが無いので常に別の 1 件になる
- **日本語タグ・表示名の自動生成**: from-job 登録で `tags`（および `name`）を指定しなかったときは、
  NSFW 自動判定（§9 `app/nsfw.py`）と同じ形で **Grok にワンショットで尋ねて背景で書き戻す**
  （`app/autotag.py`）。既定の表示名は英語プロンプトの先頭なので、日本語の短い作品名と 3〜5 個の
  日本語タグに置き換えて探しやすくする。指定済みの項目は上書きしない。Grok が使えなければ静かに
  諦める（タグ無しのまま。ログのみ）。反映できたら WS（`type: "library"`）で画面に伝え、開いている
  ライブラリモーダルは一覧を読み直す。アップロードした素材はプロンプトが無いので対象外
- **ジョブの入力として使える**: `source_image` / `end_image` / `reference_video` / `audio_path` は
  `library/` 配下の絶対パスと `/library/…` URL を受け付ける（`jobs.resolve_asset_path`。従来の
  `assets/` も引き続き有効なので、LoRA の `default_audio` など既存の値は壊れない）
- **タグ**: 各素材に分類タグを付けられる（登録時 / `PATCH` で編集）。前後の空白・空・重複（大文字小文字
  無視）は保存時に落とし、順序は書いたまま。1 タグ 40 文字まで（`library.normalize_tags`）
- **検索とページング**: `GET /api/library` は `kind`（種別）/ `q`（表示名とタグへの部分一致、
  大文字小文字無視）/ `tag`（完全一致）/ `limit`（既定 50、最大 200）/ `offset` を取り、
  `{items, total, limit, offset, tags}` を返す。`total` は**絞り込み後の総件数**なので
  「まだ何件あるか」が分かり、`tags` は補完用の全タグ一覧。`kind` だけ SQL で絞り、`q` / `tag` は
  Python 側で判定する（タグは 1 カラムに JSON 配列で持っており、LIKE では誤検出しやすいため。
  個人利用の規模なら読み切ってから絞るほうが確実）
- フォーム / エージェント用に `GET /api/options` の `library` には全件が入る。表示名・NSFW・タグは
  `PATCH /api/library/{id}`、登録解除は `DELETE /api/library/{id}`（ファイルも消す）
- DB とファイル操作は `app/library.py` に集約し、ルーターとエージェント（`library` /
  `library_search` アクション）が共用する

---

## 8. UI 仕様

SPA 1 画面 + 履歴。ダークテーマの生成系ツールらしい見た目。

```
┌────────────────────────────────────────────────────────┐
│ ヘッダー: 接続状態(ComfyUI ● / Grok ●)   [設定]          │
├───────────────────────────┬────────────────────────────┤
│ 左ペイン(入力)              │ 右ペイン(結果)               │
│ ◦ モード切替 [画像＋動画|動画生成|画像のみ|音声]             │
│ ◦ 動画/画像ワークフロー(選択) │ ◦ 進捗バー + 実行中ノード表示  │
│ ◦ 開始フレーム/最終フレーム/  │ ◦ 生成画像プレビュー          │
│    参照動画(D&D/履歴から選択) │                            │
│ ◦ アスペクト比 / MP         │ ◦ 動画プレイヤー              │
│ ◦ リファレンス音声選択       │ ◦ ラストフレーム              │
│   (履歴から選択)            │                            │
│ ◦ LoRA(動画) 複数選択        │   [この画像で続きを生成]       │
│ ◦ LoRA(画像) 複数選択        │ ◦ 音声プレイヤー(音声モード)    │
│ ◦ 画像プロンプト (textarea)  │ ◦ 使用プロンプト表示(コピー可)  │
│ ◦ 動画プロンプト (textarea)  │                            │
│   └ [Grokで生成] →チャットへ │                            │
│ ◦ 秒数 / fps / seed 固定    │                            │
│ ※ 音声モードは上記の代わりに  │                            │
│   音声WF/プロンプト/歌詞/     │                            │
│   BPM・キー・言語・カテゴリ    │                            │
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
- **入力リソースは「ライブラリから選択」「履歴から選択」で使い回せる**: 開始フレーム / 編集元画像・最後のフレーム・参照動画・リファレンス音声の各欄に 2 つのボタンを置く。[ライブラリから選択] は取っておいた素材の一覧（`LibraryPickerModal`、§7.2）で、選ぶと `/library/…` URL をそのまま欄に入れる（配信済みなのでコピーしない）。モーダル内から素材のアップロード追加・リネーム・タグ編集・削除もできる。一覧は `GET /api/library` から 50 件ずつ読み、**検索ボックス（名前・タグの部分一致）とタグチップでの絞り込み**、[さらに表示] での continue 読み込みに対応する（絞り込みとページングはサーバー側）
- **リファレンス音声はライブラリに一本化**: `assets/audio` のプルダウンは廃止し、[ライブラリから選択] / [履歴から選択] / [アップロード]（アップロードはそのままライブラリ登録）と、選択中の名前 + プレビューだけを出す。LoRA の `default_audio` などが指す従来の `/assets/…` も入力としては引き続き有効
- **生成物のライブラリ登録**: 結果ペイン（表示中の成果物 1 件）と履歴詳細（その job が持つ出力すべて）に [☆ ライブラリに登録] を置く（`LibraryAddButton`）。既に登録済みのものは `/api/options` の library から判定して押す前から [★ 登録済みです] を出し、押してしまった場合も 409 を失敗扱いにせず同じ表示にする（§7.2）
- [履歴から選択] は過去ジョブの出力から選ぶ（`HistoryPickerModal`）。**検索ボックス**でジョブの文言（動画 / 画像 / 音声プロンプト → 最初の指示）に部分一致するものだけに絞れる（ジョブは全件フロントにあるのでクライアント側で絞る）。候補は完了ジョブのみを新しい順に並べ、欄の種別で絞る（画像欄 = 生成画像とラストフレームの両方（ラベルで区別）、動画欄 = 生成動画、音声欄 = 音声ジョブの出力）。生成物は `outputs/` にあって `assets/` の外なので、選ぶと fetch → `POST /api/assets/{kind}` で assets へコピーしてから欄に入れる。モーダル内には独自の「🫣 NSFW表示」チェックボックスがあり、初期値はヘッダーのグローバルトグルに従うが、ここでの切り替えは `sessionStorage` に残さない（この画面かぎり）。オフのあいだは NSFW ジョブを一覧に出さない。Esc / 背景クリックで閉じる
- LoRA 選択はチップ型マルチセレクト（強度スライダー付き）。選択するとトリガーワード連結欄（編集可）に反映される。セクションは 2 つあり、**「LoRA（動画）」は動画設定群の中**（登録 `target = 'video'` のみ）、**「LoRA（画像）」は画像設定群の中**（`target = 'image'` かつ選択中の画像ワークフローと同じファミリーのみ）に置く
- **モードとワークフローに応じた項目の非表示**（`form.hiddenFields`）: 使わない項目はグレーアウトではなく**その欄ごと表示しない**。ただし値は `FormState` に残るので、その項目を使うモード / ワークフローへ戻せば入力内容が復元される
  - 動画生成モードでは画像ワークフロー・画像プロンプト・LoRA（画像）・トリガーワードを出さない（LoRA（動画）は出す）。画像のみモードでは動画ワークフロー・動画プロンプト・ネガティブ・リファレンス音声・秒数・fps・LoRA（動画）を出さない
  - **選択した動画ワークフローのマニフェスト**に従い、音声入力を持たないワークフローでは音声欄を出さず、必要な入力（最終フレーム / 参照動画）の欄だけを出す
  - **画像ワークフロー**も同様で、編集系（qwen-image）では参照画像の欄が出る代わりにアスペクト比 / メガピクセルが消える
  - 音声モードでは画像・動画のセクション一式を出さず、音声ワークフローと、そのワークフローが露出しているつまみだけを出す
- 「画像＋動画」モードのプルダウンには開始フレームを受け取れる動画ワークフローのみを出す（選択中のものが対象外になったら自動で切り替える）
- **選択式フィールド**を宣言しているワークフロー（wan_dancer）では、ワークフローセレクトの直下にその選択肢のプルダウンが並ぶ（§3.1）。自動決定できる項目には「自動（入力に合わせる）」、それ以外には「既定（<値>）」が先頭に入る。`video_prompt` が任意のワークフローではプロンプト欄に「（任意）」と出す
- LoRA チェーンを持たないワークフロー（wan_dancer）では LoRA（動画）セクションを出さない（挿せないため。指定したジョブはバックエンドが 422 にする）
- 動画ネガティブはプリセット選択（ワークフロー既定 / 現行値 / モデル作者版）+ 編集可（詳細設定アコーディオン内）
- 設定は**モーダルではなく専用ページ（フルページ）**。ヘッダーの [設定] で画面遷移し、ページ左上の [← 戻る] で生成画面に復帰する。3 タブ構成:
  - **接続 / Grok**: ComfyUI 接続先（URL / APIキー） / grok CLI コマンドと**使用モデル（既定: grok-4.5、変更可）**  / **モデル自動ダウンロード**のブロック（`dir-status` の `configured=true` のときだけ表示。保存先パスは環境変数由来なので読み取り専用で見せ、「書き込み可 ✓」「パスが見つかりません」等の状態と、**Hugging Face トークン**・**Civitai APIキー**（どちらも `type="password"`）を並べる。`configured=false` のときはブロックごと出さない、§3.3）
  - **LoRA 管理**: 表示名・ファイル名・**対象ワークフロー（画像用 / 動画用）**・**モデルファミリー（画像用のみ）**・トリガーワード・既定強度・既定音声・並び順の CRUD とサンプル画像の登録。一覧のバッジには対象とファミリーを出す
  - **モデル**: 全ワークフローのモデルファイル名一覧を **画像 / 動画 / 音声の大分類 → ワークフローごとの折りたたみ**（既定は閉じ、見出しに項目数・未保存件数・既定から変更した件数のバッジ）に整理し、行ごとにテキスト入力で上書き。変更行はハイライト、[既定に戻す] で復帰、[保存] で全行を一括 PUT。各行にはさらに**候補リスト**（チップ + 追加/削除）があり、既定値と合わせて 2 件以上にすると生成フォーム / エージェントが実行ごとに選べるようになる。既定値入力・候補追加入力はどちらも `/api/options` の `model_files`（`"<class_type>.<field>"` ごとの ComfyUI ファイル一覧。LoRA は従来の `lora_files` で補う）があれば datalist で補完。さらに各行には**不足モデルのダウンロード**の UI がある: 値が `model_files` の該当リストに無ければ**未検出**バッジ、URL 入力欄（`model_download_urls`。キーはファイル名なので同じファイルを使う行では共有）と [DL] ボタン、進行中は進捗バーと取得済みバイト数（WS の `model_download` を購読）。`COMFY_MODELS_DIR` が未設定（`dir-status` の `configured=false`。Comfy Cloud 利用などでは正常な状態）のあいだは URL 欄・[DL] 列そのものを描画せず、タブ上部の警告も出さずに「使うなら `.env` に `COMFY_MODELS_DIR` を設定して再起動」の案内だけ添える。設定済みで `exists`／`writable` が false のときは列を出したうえで [DL] を disabled にし、`title` とタブ上部に理由を出す（§3.3）
- **実行ごとのモデル切り替え**: 選択中のワークフローに候補が 2 件以上あるスロットがあれば、そのワークフローセレクトの直下に「使用モデル: <ノード名>」のセレクトを出す（画像 / 動画 / 音声それぞれのセクション内）。候補が 1 件以下のスロットは何も出さない。送信時は**走らせるワークフローのぶんだけ**、かつ既定値と違う選択だけを `params.model_overrides` に載せる（§3.3）
- ヘッダーの NSFW 表示トグルは `sessionStorage` に保持する（既定オフ。タブを開き直すと必ずオフに戻る）

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
GET  /api/options                … 画像/動画/音声ワークフロー一覧（必要入力・露出しているつまみ・秒数レンジつき）・アスペクト比・LoRA一覧・アセット一覧・ライブラリ一覧（library, §7.2）・実行時に選べるモデルスロット（model_slots）と ComfyUI のモデルファイル一覧（model_files）
GET/POST/PUT/DELETE /api/loras   … アプリ内 LoRA 登録リストの CRUD
GET  /api/library                … ライブラリ検索（kind / q / tag / limit / offset → items + total + tags、§7.2）
POST /api/library/{kind}         … ファイルをアップロードして登録
POST /api/library/from-job       … ジョブの出力（image / last_frame / video / audio）を登録
PATCH  /api/library/{id}         … 表示名 / NSFW フラグ / タグの変更
DELETE /api/library/{id}         … 登録解除（ファイルも削除）
GET  /api/models                 … 全ワークフローのモデルファイル名一覧（既定値+現在値+候補リスト、キーは workflow_id でスコープ）
PUT  /api/models                 … モデルファイル名の上書きと候補リストの保存（既定値と同値/空は削除、候補が空のキーは削除。`choices` 省略時は保存済みの候補を保持）
GET  /api/models/dir-status      … ComfyUI の models ディレクトリの状態（configured / exists / writable / path、§3.3）
GET  /api/models/downloads       … 進行中と直近のモデルダウンロード一覧
POST /api/models/download        … 不足モデルのダウンロード開始（filename / url / subfolder。保存先を検証して 400、二重実行は 409。進捗は WS、§3.3）
POST /api/chat/sessions          … チャット開始（フォーム現在値をコンテキストとして渡す。`video_workflow` / `image_workflow` / `audio_workflow` を含む）
POST /api/chat/sessions/{id}/messages … 発言送信 → Grok 応答（質問 or 最終JSON案）を返す
GET  /api/chat/sessions/{id}     … 履歴取得
POST /api/jobs                   … ジョブ作成・実行（プロンプト確定値+パラメータ。`selects` で選択式フィールド §3.1、`model_overrides` でそのジョブだけモデルを差し替え可 §3.3）
GET  /api/jobs?limit=…           … 履歴一覧
GET  /api/jobs/{id}              … 詳細
POST /api/jobs/{id}/rerun        … 再実行（seed 変更オプション）
POST /api/jobs/{id}/continue     … ラストフレームを開始フレームに新規ジョブ（`video_workflow` / `end_image` / `reference_video` / `model_overrides` 等を差分指定可。開始フレームを取れないワークフローは既定に戻す）
DELETE /api/jobs/{id}
POST /api/assets/audio|image|video … アセットアップロード（video は参照動画用）
GET  /library/…                  … 静的配信（ライブラリの素材、§7.2）
WS   /api/ws                     … 進捗配信（`type: "job"` / `"agent"` / `"library"` / `"model_download"`）
GET  /outputs/…                  … 静的配信（画像/動画/音声）
```

### ディレクトリ構成

```
backend/            FastAPI アプリ
  app/routers/      health / settings / loras / models_config / model_download / assets / options / chat / jobs / agent
  app/comfy.py      ComfyUI クライアント（/object_info, /upload/image, /prompt, /ws, /history, /view）
  app/workflows.py  ワークフロー登録簿と注入マニフェスト（ノード ID 直指定）+ プロンプト用カタログ
  app/workflow.py   テンプレートへのパラメータ注入・LoRA チェーン動的注入・解像度計算
  app/grok.py       grok CLI 呼び出し（LLM クライアントは差し替え可能な抽象化）
  app/prompts.py    チャット / エージェントのシステムプロンプト
  app/jobs.py       asyncio ジョブキューと実行、成果物取得・ラストフレーム抽出
  app/agent_*.py    エージェントのアクションプロトコル・実行ループ・セッション永続化
  app/library.py    ライブラリ（取っておく素材）の保存・目録
  app/autotag.py    ライブラリ素材の日本語タグ・表示名の自動生成（Grok）
  app/nsfw.py       ジョブ / セッションの NSFW 判定
  app/model_download.py  不足モデルのダウンロード（models ディレクトリへ直接保存）
  tests/            pytest
frontend/           React + Vite + Tailwind の SPA（ビルド成果物は frontend/dist）
  src/components/   GenerateForm / AudioFields / ResultPane / HistoryGallery / ChatModal /
                    SettingsPage / agent/
docs/SPEC.md        仕様書
docs/AGENT-MODE.md  エージェントモード設計書
workflow/           ComfyUI ワークフロー（API フォーマット）テンプレート ※実行の正
  image/            krea2/ anima/ z-image/ qwen-image/（モデルファミリーごと）
  video/ltx2.3/     t2v / i2v / ia2v / id_lora / flf2v / ic_lora_image / ic_lora_motion
  video/wan/        wan_dancer（画像+音声→ダンス動画）
  audio/            ace_step1_5_xl_sft.json / stable_audio_3_medium_base.json
app.db              SQLite（jobs / loras / library / chat_sessions / agent_sessions）
outputs/            生成物（/outputs で静的配信）
assets/             アップロードした画像・音声・参照動画・LoRA サンプル（/assets で静的配信）
library/            ライブラリ（取っておいた素材。image/ video/ audio/、/library で静的配信）
runtime/            config.json / grok 作業ディレクトリ / agent-sessions/
```

---

## 10. 制約・注意事項

1. **Grok Build CLI 依存**: `grok` CLI のインストールとサブスクリプションでのサインインが前提。CLI はベータ段階のため出力形式・挙動が変わる可能性があり、LLM クライアントは抽象化して公式 API / ローカル LLM に差し替え可能に設計する。NSFW プロンプト生成を Grok が拒否した場合のリトライ指示（システムプロンプト側の調整）とエラー表示も用意する
2. **コンテンツ**: 本アプリは成人向けコンテンツをローカル生成する個人利用ツール。生成物・プロンプトはすべてローカル保存のみで外部送信しない。LoRA は実在人物の無断利用を行わないこと（利用者責任）
3. **ComfyUI 依存**: ResolutionSelector / ComfySwitchNode / CustomCombo / LTXV 系 / ComfyMath / ResizeImage 系 / ResizeAndPadImage / MoGe 系 / LoadVideo / Video Slice 等の custom nodes が導入済みである前提。起動時と `/api/health` で `/object_info` に対し **`workflow/` 配下の全テンプレートに含まれる class_type** の存在チェックを行い、不足があれば UI に警告する（どのワークフローを使うか実行前には分からないため、集合は全テンプレート横断）。同時にマニフェストとテンプレートの整合性も検証する（§3.0）
4. **プロンプト拡張ブランチのモデルファイル**: 各動画テンプレートは prompt enhance 用に `gemma-3-12b-it-abliterated_lora`（`LoraLoader`）を参照している。アプリは enhance を常に false にするので実行はされないが、ComfyUI は投入グラフ全体の入力を検証するためファイル自体は存在する必要がある。無い場合は設定ページの「モデル」タブで別名に差し替えるか、同タブの [DL] でダウンロードする（§3.3）
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

決定済み（v0.3）:

11. 画像ワークフロー: **選択式**（krea2 / anima / z-image / qwen-image-edit）。`image_prompt` の
    仕様はファミリーごとに別物として扱う（§2.3 / §4.2）
12. 画像 LoRA: **モデルファミリーで仕分け**、`image_workflow` と一致するものだけ使用可（§3.4）
13. 音声生成: **独立モード**（画像・動画とは連結しない）。ACE-Step 1.5 と Stable Audio 3、出力は mp3（§2.4）
14. 未使用項目: **グレーアウトではなく非表示**（値はフォーム状態として保持）（§8）

残課題: なし（実装着手可能）
