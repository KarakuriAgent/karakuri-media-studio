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

ワークフローは画像・動画・音声で分離しており、1 ジョブは **1 つまたは 2 つのステージ**で構成される
（各ステージはそのワークフローのバックエンド＝ ComfyUI のプロンプトか kie.ai のタスク、§5.2）。

| モード | 内部名 | 実行されるワークフロー | 開始フレーム |
|---|---|---|---|
| 画像＋動画 | `full` | 選択した画像ワークフロー → 選択した動画ワークフロー（2 段） | 1 段目の生成画像 |
| 動画生成 | `i2v` | 選択した動画ワークフローのみ | ワークフローが要求する入力（アップロード / 履歴 / なし） |
| 画像のみ | `image_only` | 選択した画像ワークフローのみ | ― |
| 音声 | `audio` | 選択した音声ワークフローのみ（独立ジョブ） | ― |

モードはワークフローの**実行エンジンとは独立**で、選んだワークフローが kie.ai の
ものなら同じモードのまま外部 API で実行される（§5.2）。

`audio` は他の 3 モードと連結しない独立モード。画像・動画のフィールド（`video_workflow` /
`source_image` / `loras` など）は一切使わず、指定すると 422 で拒否される（§2.4）。

### 2.1 「画像＋動画」の 2 ジョブ連結

旧方式のようにグラフを合体させず、同一 `job_id` のもとで順に実行する:

1. 選択した画像ワークフローを `/prompt` に投入 → 完了を待つ
2. `SaveImage` の出力を `/view` でダウンロードし `outputs/{job_id}/image.png` に保存
3. その画像を 2 段目に渡せる形にする（**2 段目のバックエンド次第**）:
   ComfyUI なら `/upload/image` で input ディレクトリへ、kie.ai なら File Upload API で公開 URL に
4. 選択した動画ワークフローに開始フレームとして注入して投入 → 完了を待つ
   （ComfyUI は `LoadImage` のファイル名、kie.ai は `imageUrls` の 1 枚目）
5. 動画をダウンロードし、ffmpeg でラストフレームを抽出

- 進捗は 1 ジョブとして配信され、メッセージが「画像生成 (1/2)」→「動画生成 (2/2)」と切り替わる
- **2 段でバックエンドが違ってもよい**: 1 段目 = ComfyUI の画像ワークフロー、2 段目 = kie.ai の
  動画ワークフロー（Veo など）という「ローカルで画像 → 外部 API で動画化」が本命の使い方。
  逆向き（kie.ai の画像 → ComfyUI の動画）は受け渡しが未実装なので 422（§5.2）
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
| `veo3_1_fast` | Veo 3.1 Fast（音声つき・外部 API） | kie.ai `veo3_fast` | なし（画像・最終フレーム画像は任意） | ○ |
| `veo3_1_quality` | Veo 3.1 Quality（音声つき・外部 API） | kie.ai `veo3` | なし（画像・最終フレーム画像は任意） | ○ |

- id はファイル名（拡張子なし）。`tx2_3_i2v` / `tx2_3_ia2v` の綴りは配布ファイル名そのまま
- **`wan_dancer`（`workflow/video/wan/`、family `wan`）** は LTX 系とは作りが違う: 渡した曲に合わせて踊る映像を作り、
  プロンプトは自由記述ではなく**選択式フィールド**（§3.1）で決まる。`video_prompt` は任意で、書けば Global 側の
  テンプレ文（`<dance style>` を含められる）を差し替える。ユーザー LoRA を挿すチェーンは持たないので、
  動画 LoRA を指定したジョブは 422 になり、フォームは LoRA（動画）欄を出さない
- **`veo3_1_fast` / `veo3_1_quality`（family `veo`、backend `kie`）** はテンプレートを持たない**外部 API**の
  ワークフロー（§5.2）。映像と**音声（環境音・効果音・セリフ）を同時に生成**し、尺 4/6/8 秒・縦横比 16:9 / 9:16・
  解像度 720p / 1080p を**選択式フィールド**（§3.1）で選ぶ。画像は任意で、1 枚なら開始フレーム、
  最終フレーム画像も渡すと flf2v 相当（`imageUrls` 2 枚）になる。`generationType` は渡した枚数から自動で決まる。
  ユーザー LoRA・`fps`・自前の解像度指定は使えない。Fast は日常の量産、Quality は本番カット用（約 4 倍の値段）。
  プロンプトの書き方は Grok 側にモデル専用ガイド（`prompts.VIDEO_SPECS`）を注入する。
  生成後の 1080P / 4K 追加取得と延長（extend）は未対応。
  `full`（画像→動画）は **ComfyUI の画像ワークフローと組み合わせられる**（§2.1 / §5.2）:
  1 段目の生成画像が kie の File Upload API 経由で開始フレームになる
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
- **モデルの指定は接続先ごと**（SPEC §5）: `Settings.model_overrides` / `model_choices` は `{"<comfy_target>": {"<スロットキー>": …}}` の 2 段で持つ。どのファイルが在るかは ComfyUI の環境ごとに違うため。`GET/PUT /api/models` は `?target=`（PUT はボディの `target`）で対象環境を選び、省略すると現在の接続先。**書き込みは選んだ環境だけ**で他の環境の指定は残る。ジョブ実行・`/api/options` の `model_slots`・エージェントの検証はすべて「現在の接続先」の値（`Settings.overrides_for()` / `choices_for()`）を使う。接続先を分ける前の設定（1 組だけ）は読み込み時に**3 環境すべてへ複製**される（`config._per_target`）: 分けた瞬間に指定が消えて既定モデルで走り出すのを避けるため
- 上書きキーは**ワークフロー ID でスコープ**する: `"<workflow_id>/<node_id>.<field>": "<ファイル名>"`。テンプレート間で同じノード ID（例: `340:317` が ia2v と id_lora の両方にある）が衝突しないため。旧レイアウトの非スコープキーは無視される（マイグレーション不要）
- **実行ごとのモデル切り替え**: 同じキー形式で「そのスロットで選べるファイル名」を設定に持てる（`Settings.model_choices`、`GET/PUT /api/models` で読み書き）。既定値（`model_overrides` → 無ければテンプレート値）と合わせて **2 件以上**になったスロットは *switchable* とみなし、`GET /api/options` の `model_slots`（キー・ラベル・既定値・候補一覧）に出す。ジョブは `model_overrides`（`JobCreate` / `JobContinue` のフィールド）で 1 回ぶんだけ差し替えられ、実行時に設定の既定値の上へマージされる（`jobs.run_job`）。検証（`models.model_override_problem`、Web UI とエージェントで共通）は「キーが `model_fields()` に存在」「そのジョブが走らせるワークフロー（`models.job_workflow_ids`）に属する」「値が候補（既定値を含む）に入っている」を満たさないものを 422 で拒否する。再実行は params ごと引き継ぎ、続き生成は動画ワークフローぶんのキーだけを引き継ぐ（`workflow.scoped_model_overrides`）
- **不足モデルの自動ダウンロード**: ComfyUI 本体にも Comfy Cloud にもモデル取得 API は無いので、**落とし先の環境に合わせて**取ってくる（`POST /api/models/download` の `target`、省略時は現在の接続先）。設定ページは「その行の値が `GET /api/options` の `model_files` の該当リストに無い」ことを不足の判定に使い、**未検出**バッジ・URL 入力欄・[DL] ボタンを出す（`model_files` が空＝ComfyUI 未接続のときは判定しない）
  - `local` … バックエンドが自分でダウンロードして ComfyUI の models ディレクトリ（**環境変数 `COMFY_MODELS_DIR`**）へ直接置く（ComfyUI はフォルダの mtime を見て一覧を作り直すので再起動は不要）
  - `runpod` … Pod の中で動く小さな API（`deploy/runpod/model_api.py`、`127.0.0.1:8190`。caddy が ComfyUI と同じ認証で `/studio/models/*` だけを通す）に `POST /download` で依頼し、`GET /downloads` を 2 秒ごとにポーリングして**ローカルと同じ WS フレーム**に変換して流す。アプリを再起動しても Pod 側は走り続けるので、`GET /api/models/downloads?target=runpod` は Pod の一覧を取り込んで見張りを再開する。Pod が古いイメージ（この API を持たない）なら 404 を「イメージを作り直してください」という 400 にして返す
  - `comfy_cloud` … ファイルシステムに触れないので 400（モデルは Comfy Cloud 側の管理）
  - **一括ダウンロード**（[全DL]、`POST /api/models/download-all`）: 選んだ環境の `/object_info` と比べて未検出、かつ `model_download_urls` に URL があるものをまとめて開始する。対象はワークフローの各スロットの実効値・候補リストと、その環境の LoRA 登録。URL が無いものは `missing_urls` として返して UI が知らせる。ComfyUI に繋がらないときは 400（何が足りないか判定できないため）
  - 置き場所は `class_type`＋入力フィールドから決める（`workflow.MODEL_SUBFOLDERS` → `ModelField.subfolder`）: checkpoints = CheckpointLoaderSimple.ckpt_name / LTXVAudioVAELoader.ckpt_name / LTXAVTextEncoderLoader.ckpt_name、diffusion_models = UNETLoader.unet_name、text_encoders = CLIPLoader.clip_name / DualCLIPLoader.clip_name1・clip_name2 / LTXAVTextEncoderLoader.text_encoder、clip_vision = CLIPVisionLoader.clip_name、vae = VAELoader.vae_name、loras = LoraLoader.lora_name / LoraLoaderModelOnly.lora_name、latent_upscale_models = LatentUpscaleModelLoader.model_name、geometry_estimation = LoadMoGeModel.model_name。未知のローダーは空（＝ UI で入力させる。当てずっぽうに置いても ComfyUI からは見えない）
  - `POST /api/models/download` は保存先を検証（`..` / 絶対パス / パス区切りを拒否し、`resolve()` 後に models ディレクトリ配下であることを確認）してからバックグラウンドタスクを起こす。httpx のストリームをチャンクで `<ファイル名>.part` に書き、完走したときだけ本来の名前に `rename` する（失敗・中断時は `.part` を削除）。進捗は WS `/api/ws` に `type: "model_download"` として流れる。同じファイル名の同時ダウンロードは 409
  - 認証は URL のホストで出し分ける: huggingface.co / hf.co（サブドメイン含む）は `Settings.hf_token`、civitai.com は `Settings.civitai_api_key` を `Authorization: Bearer …` として付ける（未設定なら付けない）。**リダイレクトは httpx に任せず自分で追う**（最大 10 ホップ、相対 `Location` は urljoin で解決、301/302/303/307/308 を GET のまま追う）: クライアント既定ヘッダに認証を載せると転送先の別ホストにトークンが漏れるため、ホップごとに URL を再検証して認証ヘッダを計算し直し、そのリクエストにだけ渡す（HF → `*.hf.co` の CDN には付き、無関係なホストには付かない）。URL はファイル名ごとに `Settings.model_download_urls` へ保存する（同じファイルが複数スロットに出るため、キーはスロットではなくファイル名）
  - 保存先は**環境変数 `COMFY_MODELS_DIR` だけ**が決める（設定 `runtime/config.json` には持たない）。UI からパスを入れられても、Docker で同じ絶対パスをマウントしていなければ書けないため。`.env` に書けば `run.sh`（ホスト実行、`.env` を読んで `export`）と `docker compose`（同一パスのマウント＋`environment:` で受け渡し）の双方に効く。設定に残すのは `hf_token` / `civitai_api_key` / `model_download_urls` だけで、旧バージョンが書いた `comfy_models_dir` キーは読み込み時に捨てる
  - **取得元ページ（エージェント用）**: 登録した URL はダウンロード用の直リンクなので、そのままではエージェントが使い方を調べられない。`app/model_sources.py` が配布ページ URL に変換し、エージェントのシステムプロンプトへ焼き込む（AGENT-MODE §3.1）。Hugging Face は `…/resolve|blob|raw|tree/<rev>/<path>` から `https://huggingface.co/<org>/<repo>` を切り出すだけ（`datasets/` 名前空間も対応。`cdn-lfs.hf.co` 等の CDN 直リンクはリポジトリ名が読めないので変換しない）。Civitai の `…/api/download/models/<versionId>` は modelId を含まないので `https://civitai.com/api/v1/model-versions/<versionId>` を 1 回だけ叩いて引き、`https://civitai.com/models/<modelId>?modelVersionId=<versionId>` を組み立てる（認証は `model_download.auth_headers` と共通）。結果は `Settings.model_page_urls`（ダウンロード URL → ページ URL）にキャッシュするので 2 回目以降は API を叩かない。失敗しても例外は投げず、ページ URL 無し（＝ダウンロード URL だけ）として扱う。`model_page_urls` は自動生成のキャッシュなので `SettingsUpdate` には無く、設定ページからは触らない
  - `GET /api/models/dir-status` が `{configured, exists, writable, path}` を返す。これは**ローカルに落とすときだけ**の話（RunPod では Pod 側の models ディレクトリに置く）。**UI はこの状態でダウンロード機能を隠さない**: [DL] / [全DL] は常に出し、落とせない事情（`COMFY_MODELS_DIR` 未設定・存在しない・書けない）は押したときの 400 の本文で知らせる。ローカルを選んでいるあいだは同じ理由をタブ上部の警告にも出す（`comfy_cloud` を選んだときだけダウンロード関連を出さない）

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

- 接続先: **ComfyCloud / RunPod / ローカルの 3 プロファイル**を設定に持ち、`comfy_target`（`comfy_cloud` / `runpod` / `local`）がそのどれを使うかを決める。各プロファイルは URL と（ローカル以外は）API キーを持つ（ComfyCloud は URL 固定 `COMFY_CLOUD_URL = https://cloud.comfy.org` で `comfy_cloud_api_key` のみ、RunPod は `runpod_comfy_url` / `runpod_comfy_api_key`、ローカルは `local_comfy_url`）、ComfyUI クライアントはリクエストのたびに `Settings.active_comfy_url()` / `active_comfy_api_key()` で解決する（設定変更・接続先の切り替えが再起動なしで効く）。切り替えは設定ページの「ComfyUI 接続先」と**生成フォーム最上部のプルダウン**の両方から行え、どちらも `PUT /api/settings` の `comfy_target` に保存されるので次回起動時も前回の選択が使われる。旧レイアウト（単一の `comfy_url` / `comfy_api_key`）の設定ファイルは読み込み時に移行する（`runpod_enabled` なら RunPod、`comfy.org` ホストか API キー付きなら ComfyCloud、それ以外はローカル。API キーは URL を引き取ったプロファイルへ）
- **Comfy Cloud**: Cloud 向けのエンドポイント URL と認証設定を設定画面から入力できる（ホストが `comfy.org` のとき自動で Cloud 互換モード）。エンドポイントは `https://cloud.comfy.org` 固定（設定項目にはしない）で、使い方は「[API キー発行ページ](https://docs.comfy.org/development/cloud/overview) で作ったキーを `comfy_cloud_api_key` に設定」＋「接続先を ComfyCloud にする」の 2 手順。Cloud 互換モードではエンドポイントに `/api` プレフィックスが付き、認証は `X-API-Key` ヘッダー、`/view` は 302 署名 URL リダイレクトを追う。API アクセスは有料プラン（Standard 以上）が必要で Free では使えない。ワークフローが参照するモデル・LoRA・リファレンス音声は Cloud 側のストレージに存在している必要がある（ファイルシステムに届かないので §3.3 の自動ダウンロードは使えない）
- 使用 API:
  - `GET /object_info` … ResolutionSelector のアスペクト比選択肢、LoRA 一覧、class_type の存在確認
  - `POST /upload/image` … 開始フレーム画像・リファレンス音声・参照動画、および**`full` 1 段目の生成画像**のアップロード（ComfyUI はこのエンドポイントで input ディレクトリに任意ファイルを受ける）
  - `POST /prompt` … ワークフロー投入（`client_id` を付与）。`full` は 1 ジョブで 2 回投入する
  - `WS /ws?clientId=…` … 進捗（ノード実行状況・プレビュー）の受信
  - `GET /history/{prompt_id}` … 出力ファイル名の取得
  - `GET /view?filename=…&type=output` … 成果物ダウンロード
- **環境ごとのデータ**: モデルの指定（§3.3）と **LoRA 登録**は接続先ごとに持つ。LoRA は `loras.comfy_target` 列（`NULL` = 全環境で出す）で分け、`GET /api/loras?target=` と `/api/options` は「その環境のもの + 共通（`NULL`）」を返す。接続先を分ける前の登録行は `NULL` のまま＝どの環境でも見えるので、既存の登録が消えたように見えることはない。新規登録は設定ページで選んでいる環境に紐づく。取得元 URL（`model_download_urls`）だけは**環境共通**（同じファイルはどの環境でも同じ場所から落とすため）
- **接続エラーの見せ方**: `ComfyError` は「ComfyUI に届いていない失敗」を `unreachable`（接続不可・タイムアウトと 502/503/504/52x/530 などゲートウェイ系ステータス）として持つ。エラー本文が HTML のときは `<title>` だけを拾って畳み、**生 HTML を UI に流さない**（Pod 停止中の Cloudflare Tunnel は数 KB のエラーページを返すため）。読み取り系エンドポイント（`/api/options` の `comfy_error`、`/api/health` の detail）は `comfy.display_error()` を通し、接続先が RunPod の到達不能を「RunPod の ComfyUI が起動していません。ジョブを投入すると自動で Pod を起動します」（`runpod_enabled` が無効なら「設定画面から Pod を起動するか URL を確認してください」）に言い換える。到達したうえでの失敗（400 など）は言い換えない。ジョブの失敗理由は言い換えない（実行中に落ちた場合に「投入すれば自動起動」は当たらないため）
- 同時実行は 1 ジョブ（ComfyUI 側キューに任せるが、アプリ側でもジョブキューを持ち順次投入）
- タイムアウト・ComfyUI 未起動・ノード不足（custom nodes 未導入）はジョブを failed にして UI に理由を表示

### 5.1 RunPod の Pod で動かす（自動起動）

ComfyUI を **RunPod の Pod（GPU 時間貸し）**に置く構成では、使っていない間 Pod を
落としておきたい。そのため「**ジョブ投入の直前に疎通を確かめ、落ちていれば Pod を
立ち上げて待つ**」機能を持つ（`backend/app/runpod.py`、既定は無効）。

- 設定は `runpod_enabled` / `runpod_api_key` / `runpod_template_id` /
  `runpod_gpu_type` / `runpod_network_volume_id` の 5 つ（`GET/PUT /api/settings`）。
  接続先そのものは RunPod プロファイル（`runpod_comfy_url` / `runpod_comfy_api_key`）で、
  Pod 側は Cloudflare Tunnel で固定ホスト名を持つので**起動のたびに設定を書き換えなくてよい**。
  自動起動が働くのは **`comfy_target == "runpod"` かつ `runpod_enabled`** のときだけ
  （他の接続先を選んでいるあいだは Pod を作らない）
- `jobs.run_job` はワークフロー投入の前に `runpod.ensure_pod_running()` を呼ぶ。
  無効なとき・既に疎通しているときは何もしないので、実行経路に無条件に置ける
- 疎通確認は `comfy.get_object_info()`（§5 の `/object_info`）をそのまま使う。
  通らなければ RunPod REST API `POST https://rest.runpod.io/v1/pods`
  （`Authorization: Bearer <runpod_api_key>`）に `templateId` / `gpuTypeIds` /
  `networkVolumeId` / `cloudType: "COMMUNITY"` を投げて Pod を 1 つ作る
- **GPU が確保できない等のエラーはそのままジョブの失敗理由にする**（別の GPU や
  SECURE クラウドへ勝手に振り替えると、意図しない課金が黙って起きるため）
- 作成後は `runpod_comfy_url` に繋がるまでポーリング（全体 15 分。初回は Network Volume への
  モデル配置があるため長め）。繋がらないままならタイムアウトでエラーにする
- 起動処理は `asyncio.Lock` で **single-flight**。同時に走ったジョブはロックを
  取った時点でもう一度疎通を確かめるので、Pod が 2 つ作られることはない
- 起動待ちの進捗は既存の WS `type: "job"`（`status: "running"` + `message`）で流す。
  新しいメッセージ種別は増やさない
- **Pod の停止はアプリ側では行わない**。アイドルが続いたら自分を terminate するのは
  イメージ内の `deploy/runpod/watchdog.py` の役目で、アプリが落ちていても課金が止まる

Pod 側のイメージ（Dockerfile / entrypoint / 認証つき Caddy プロキシ / モデル
マニフェスト / watchdog）と手順書は [`deploy/runpod/`](../deploy/runpod/README.md)。
モデルの取得は §3.3 と同じ流儀（`.part` に書いて完走時のみ rename、リダイレクトを
自分で追い、ホストごとに `HF_TOKEN` / `CIVITAI_API_KEY` を出し分け）で実装してある。

### 5.2 kie.ai（外部生成バックエンド）

ComfyUI と並ぶ **2 つめの生成バックエンド**として、外部 API アグリゲータ
[kie.ai](https://docs.kie.ai/) を使える（`backend/app/kie.py`）。自前の GPU では
動かないモデル（Veo / Kling / Seedance / Suno など）をそのまま同じフォーム・同じ
履歴で扱うための共通基盤で、個別モデルの対応はこの上に載せる。

- **バックエンド軸**: ワークフローのマニフェスト（§3 / §4.3）が `backend`
  （`comfyui` / `kie`、将来 `grok_cli` / `codex_cli`）を宣言する。ComfyUI 用は
  `workflow/*.json` のテンプレート + 注入マニフェスト、kie 用はテンプレートの代わりに
  `KieTask`（`model` と「論理名 → `input` のキー」の対応、固定値、概算クレジット）を持つ。
  論理名は ComfyUI の注入マニフェストと同じ語彙（`prompt` / `image` / `duration` /
  `select:<名前>` …）なので、`description` / `prompt_hint` から生成される
  UI・エージェントのカタログの作り方は §4.3 のまま変わらない
- **ディスパッチはステージ単位**: `jobs._run_job_stages` がステージごとにそのマニフェストの
  `backend` を見て実行経路を選ぶ（ジョブ単位ではない）。成果物の置き場・jobs 行の列・WS の
  進捗表示はバックエンドに依らず共通なので、履歴・ライブラリ・UI からは区別が付かない
- **バックエンドをまたぐ 2 段**（`jobs._STAGE_BRIDGES`）: 実装してあるのは
  **ComfyUI の画像 → kie.ai の動画**の向きだけ。1 段目の静止画を File Upload API で
  公開 URL にして 2 段目の `imageUrls` に入れる（§2.1）。逆向き（kie.ai の画像 →
  ComfyUI の動画）は kie の画像ワークフローが入ってから実装するので、それまでは
  投入時に 422 で断る。ComfyUI の下ごしらえ（RunPod の Pod 起動・入力ファイルの
  アップロード）は**最初の ComfyUI ステージの直前に 1 度だけ**行うので、1 段目から
  kie のジョブでは ComfyUI に一切触らない
- **可用性の判定（`backend/app/backends.py`）**: 外部バックエンドは
  **認証情報が入っていて、実際に通ることを確認できたときだけ**選択肢に出る。
  確認は軽いヘルスチェック（kie.ai は残クレジット照会 `GET /api/v1/chat/credit`）で、
  結果はプロセス内にキャッシュし、**起動時**と**設定保存時**（`PUT /api/settings`）と
  `POST /api/kie/check` で取り直す。未設定・確認失敗のあいだは
  `GET /api/options` のワークフロー一覧にもエージェントのカタログにも一切出ず、
  投入しようとしても 422。状態は `/api/options` の `backends` と `/api/health` の
  `kie` に出る（`ok` / `not_configured` / `error`）。新しいバックエンドを足すときは
  「確認する関数」を 1 つ登録するだけでこの出し分けに乗る
- **API キー**: 設定の `kie_api_key`（設定ページの「kie.ai」欄）が一次で、空のときだけ
  環境変数 `KIE_API_KEY` に落ちる
- **実行の流れ**: 入力ファイルは File Upload API（`https://kieai.redpandaai.co/api/file-base64-upload`）で
  公開 URL にしてから `input` に入れる（外部モデルは base64 直指定を受け付けない）→
  `POST /api/v1/jobs/createTask`（Market 系の統一 API）で `taskId` を得る →
  `GET /api/v1/jobs/recordInfo` を **10 秒間隔**でポーリング（`429` は**指数バックオフ**で
  最大 30 秒。webhook はローカル運用では受け取れないので使わない）→ `success` で
  `resultUrls` を取り出す。`resultJson` は **JSON 文字列なので二重パースが要る**
- **エンドポイントとステータスの読み方は差し替え可能**（`kie.TaskApi`）。Kling /
  Seedance は Market 系だが、Veo（`successFlag`）と Suno（`PENDING → … → SUCCESS`）は
  旧専用系でパスもステータス語彙も違うため、ポーリングループは共通のまま系統だけを差し替える
- **Veo の旧専用系**（`kie.VeoTaskApi`、マニフェストの `api: "veo"`）: 生成は
  `POST /api/v1/veo/generate`、照会は `GET /api/v1/veo/record-info?taskId=`
  （`successFlag` 0 = 生成中 / 1 = 成功 / 2, 3 = 失敗、成果物は `response.resultUrls`）。
  ボディは `input` で包まず平らに並べ、`generationType` は `imageUrls` の枚数から決める
  （2 枚 = `FIRST_AND_LAST_FRAMES_2_VIDEO`、0〜1 枚 = `TEXT_2_VIDEO`）。`enableTranslation` は
  既定値が docs 内で食い違うので**明示して送る**。マニフェストの `KieTask.list_keys` に挙げたキーは
  配列になり、同じキーに複数の論理入力を宣言できる（`imageUrls` は宣言順 = 開始フレーム, 最終フレーム）
- **成果物は即ダウンロード**: kie 側の URL は 14 日（モデルによっては 24 時間）で
  失効するので、完了を検知したその場で `outputs/{job_id}/` に落とす（§6 と同じ置き場・
  同じ命名で、ラストフレーム抽出も同じ）
- **進捗**: 既存の WS `type: "job"`（`status: "running"` + `message`）に
  「外部 API 生成中（キュー待ち / 生成中）」として中継する。新しいメッセージ種別は増やさない
- **課金**: クレジット制（1 credit = $0.005）。成功したタスクの `creditsConsumed` を
  `jobs.credits_consumed` に記録する（**失敗したタスクは kie 側で返金される**ので記録しない）。
  残クレジットは `GET /api/kie/credits`
- **投入内容の保存**: `workflow_json` に段階別で `{"backend": "kie", "task_id": …,
  "request": {"model": …, "api": …, "input": {…}}}` を残す。`rerun` は今までどおり
  `params` から作り直す
- モデル名・価格は**マニフェストと設定にだけ**書く（kie.ai は上流の都合でモデルが
  増減し、価格も改定されるため、コードにハードコードしない）

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
  nsfw_source   TEXT NOT NULL DEFAULT '',  -- 判定の出所（auto / manual）
  credits_consumed REAL                     -- kie.ai が消費したクレジット（§5.2。ComfyUI は NULL）
);
```

- `params` には `video_workflow` / `image_workflow` / `audio_workflow`（ワークフロー ID）と、`end_image` / `reference_video`、音声モードの `audio_prompt` / `lyrics` / `bpm` / `keyscale` / `language` / `audio_category` / `reprompt` / `audio_seed` も保存する
- 後から足したカラム（`nsfw` / `nsfw_source` / `audio_prompt` / `audio_output_path` / `credits_consumed` など）は起動時に `PRAGMA table_info` と突き合わせて不足分だけ `ALTER TABLE` する（`db.MIGRATIONS`）
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
  tags          TEXT NOT NULL DEFAULT '[]',-- 分類タグの JSON 配列（後から足したカラム）
  category      TEXT                       -- 分類（'character'|'background'|'prop'。NULL = 未分類）
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
- **カテゴリ（分類）**: タグとは別に、各素材は **1 つだけ**分類を持つ（棚の仕切り）。値は
  `character`（キャラクター）/ `background`（背景）/ `prop`（小物）で、それ以外は**未分類**
  （DB は NULL、カラムを足す前の既存行もそのまま未分類になる）。登録時（アップロード・from-job）に
  指定でき、`PATCH` で変更できる。不正な値は **400**（`library.check_category`）。
  API では「指定なし」と「未分類そのもの」を区別する必要があるため、**未分類は明示値
  `none`**（`library.UNCATEGORIZED`、空文字も同義）で表す: `PATCH` で `category` を送らなければ
  変更なし、`"none"` を送ると未分類に戻す（`tags: []` と同じ考え方）。この分類は後段の
  キャラクターシート合成で `character` を大パネル、`background` / `prop` を小パネルに割り当てる
- **リファレンスシートの合成**（`POST /api/library/sheet`、`app/sheets.py`）: 棚の画像素材を選ぶと、
  IC-LoRA の動画ワークフロー（`ltx2_3_ic_lora_image`）が参照入力に取る「複数パネルを並べた 1 枚」を
  自動で組み立てる。body は `{item_ids, name?, width?, height?}` で、**`item_ids` の並び順に意味がある**
  （左上から詰める）。1〜8 枚（`sheets.MAX_ITEMS`）、すべて `kind='image'`。存在しない id・画像以外・
  0 枚・上限超過・大きすぎるキャンバス（1 辺 4096px 超）・読めない画像は **400**。
  出来上がったシートは `library/image/` に置いてふつうの素材として登録する（`kind='image'` /
  `category='character'` / タグ `reference-sheet` / `source='sheet'`）。素材のどれかが NSFW ならシートも
  NSFW（引き継ぎなので `nsfw_source='auto'`）。そのまま `source_image` に指定できる

  レイアウト規則（決定的。`sheets.plan_layout` に純粋な計算として切り出してあり、描画はそれに従うだけ）:
  1. 背景は**黒**。パネルは**カテゴリで大小 2 種**に分かれる（`character` = 主役 / `background`・`prop`・
     未分類 = 脇役）。Lightricks のモデルカードいわく「大きいパネルほど精密に再現される」ため
  2. 片方の群しか無ければ、キャンバス全体を 1 つの格子に切って並べる
  3. 両方あればキャンバスを**左右に分割**し、左を主役・右を脇役に割り当てる。分割位置は重み
     （主役 1 件 = 2、脇役 1 件 = 1）の比で決めるので、**主役のパネルは必ず脇役より広くなる**
  4. 各領域の格子は「セルの縦横比がキャンバスの縦横比にいちばん近くなる」列数を選ぶ（同点なら
     キャンバスの向きに合わせる）。行は上から、列は左から、指定された順に埋める
  5. 各パネルの中では縦横比を保って内側に収め（contain、周囲に 8px の余白）、小さい素材は
     lanczos で拡大する。テキストラベルは入れない（モデルカードの指定）

  既定のシートサイズは 1280x720 だが、**出力動画と同じ縦横比**にするのが望ましい（ワークフローの
  `ResizeAndPadImage` が黒でパディングするので、比が合っていれば余白が出ない）。プロンプトは
  `Reference sheet: … / Generated video: …` の 2 部構成で書く（`WorkflowSpec.prompt_hint`、§3.1）
- **検索とページング**: `GET /api/library` は `kind`（種別）/ `category`（分類。未指定なら全件、
  `none` で未分類のみ）/ `q`（表示名とタグへの部分一致、大文字小文字無視）/ `tag`（完全一致）/
  `limit`（既定 50、最大 200）/ `offset` を取り、
  `{items, total, limit, offset, tags}` を返す。`total` は**絞り込み後の総件数**なので
  「まだ何件あるか」が分かり、`tags` は補完用の全タグ一覧。`kind` と `category` だけ SQL で絞り、
  `q` / `tag` は Python 側で判定する（タグは 1 カラムに JSON 配列で持っており、LIKE では誤検出
  しやすいため。個人利用の規模なら読み切ってから絞るほうが確実）
- フォーム / エージェント用に `GET /api/options` の `library` には全件が入る。表示名・NSFW・タグ・
  カテゴリは `PATCH /api/library/{id}`、登録解除は `DELETE /api/library/{id}`（ファイルも消す）
- DB とファイル操作は `app/library.py` に集約し、ルーターとエージェント（`library` /
  `library_search` / `library_sheet` アクション）が共用する
- **エージェントからも同じ棚を使える**（AGENT-MODE §3.1）: `library` は登録時に `category` を、
  `library_search` は絞り込みに `kind` / `category` を取り、検索結果の各行には分類を
  `（image / character）`（未分類は `none`）の形で出す。`library_sheet` は `add_sheet` をそのまま
  呼ぶアクションで、成功すると `library_sheet_added` イベントにシートの id・パス・URL が入り、
  そのパスを `ltx2_3_ic_lora_image` の `source_image` に指定して動画化する流れまでを
  システムプロンプトで指示している（承認不要の即時アクション）

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

- 進捗は **ワークフロー全体を通した 0→100%**（ノード単位の 0→100% の繰り返しではない）。ComfyUI の WS イベントから、`executing` で通過したノードと `execution_cached` のノードを「完了」、実行中ノードの `progress`（`value/max`）を端数として数え、`(完了ノード数 + 端数) / ワークフローのノード総数` を 1 ステージ分の割合とする。ノードの重みは均等。「画像＋動画」の 2 ステージジョブでは画像が 0〜50%、動画が 50〜100%（一般には `(stage_index + 割合) / ステージ数`）。値は単調非減少で、後退しない。進捗を持たないフレーム（メッセージだけの通知）が来てもバーは直前の値を保つ
- 実行中でもキュー追加可能（ジョブキュー表示）
- **入力リソースは「ライブラリから選択」「履歴から選択」で使い回せる**: 開始フレーム / 編集元画像・最後のフレーム・参照動画・リファレンス音声の各欄に 2 つのボタンを置く。[ライブラリから選択] は取っておいた素材の一覧（`LibraryPickerModal`、§7.2）で、選ぶと `/library/…` URL をそのまま欄に入れる（配信済みなのでコピーしない）。モーダル内から素材のアップロード追加・リネーム・タグ編集・削除もできる。一覧は `GET /api/library` から 50 件ずつ読み、**検索ボックス（名前・タグの部分一致）・カテゴリのプルダウン（すべて / キャラクター / 背景 / 小物 / 未分類）・タグチップでの絞り込み**、[さらに表示] での continue 読み込みに対応する（絞り込みとページングはサーバー側）。素材ごとのカテゴリはタイル下のプルダウンでその場で変えられ、モーダルからのアップロードには絞り込み中のカテゴリがそのまま付く
- **リファレンスシートを「ライブラリから作成」**: リファレンスシートを入力に取る動画ワークフロー（`ltx2_3_ic_lora_image`）を選んでいるときだけ、画像欄に [ライブラリから作成] を足す（`SheetBuilderModal`）。押すと `LibraryPickerModal` の複数選択モード（タイルに選択順のバッジが出る）で画像素材を **2〜8 枚**選べ、[この順で作成] で `POST /api/library/sheet`（§7.2）を呼ぶ。シートの大きさは選択中のアスペクト比から長辺 1280px で決める（`form.sheetSize`。プリセットが読めなければ 1280x720）。出来上がったシートはそのまま画像欄に入り、ライブラリにも残る。作成中はボタンを [作成中…] にし、失敗はモーダル内にそのまま出す
- **リファレンス音声はライブラリに一本化**: `assets/audio` のプルダウンは廃止し、[ライブラリから選択] / [履歴から選択] / [アップロード]（アップロードはそのままライブラリ登録）と、選択中の名前 + プレビューだけを出す。LoRA の `default_audio` などが指す従来の `/assets/…` も入力としては引き続き有効
- **生成物のライブラリ登録**: 結果ペイン（表示中の成果物 1 件）と履歴詳細（その job が持つ出力すべて）に [☆ ライブラリに登録] を置く（`LibraryAddButton`）。既に登録済みのものは `/api/options` の library から判定して押す前から [★ 登録済みです] を出し、押してしまった場合も 409 を失敗扱いにせず同じ表示にする（§7.2）。ボタンの隣にカテゴリのプルダウン（既定は未分類）を置き、登録と同時に分類できる
- [履歴から選択] は過去ジョブの出力から選ぶ（`HistoryPickerModal`）。**検索ボックス**でジョブの文言（動画 / 画像 / 音声プロンプト → 最初の指示）に部分一致するものだけに絞れる（ジョブは全件フロントにあるのでクライアント側で絞る）。候補は完了ジョブのみを新しい順に並べ、欄の種別で絞る（画像欄 = 生成画像とラストフレームの両方（ラベルで区別）、動画欄 = 生成動画、音声欄 = 音声ジョブの出力）。生成物は `outputs/` にあって `assets/` の外なので、選ぶと fetch → `POST /api/assets/{kind}` で assets へコピーしてから欄に入れる。モーダル内には独自の「🫣 NSFW表示」チェックボックスがあり、初期値はヘッダーのグローバルトグルに従うが、ここでの切り替えは `sessionStorage` に残さない（この画面かぎり）。オフのあいだは NSFW ジョブを一覧に出さない。Esc / 背景クリックで閉じる
- LoRA 選択はチップ型マルチセレクト（強度スライダー付き）。選択するとトリガーワード連結欄（編集可）に反映される。セクションは 2 つあり、**「LoRA（動画）」は動画設定群の中**（登録 `target = 'video'` のみ）、**「LoRA（画像）」は画像設定群の中**（`target = 'image'` かつ選択中の画像ワークフローと同じファミリーのみ）に置く
- **接続先プルダウン**（§5）: フォーム最上部に「接続先」（ComfyCloud / RunPod / ローカル）を置く。値は `GET /api/settings` の `comfy_target` 由来で、変えると即 `PUT /api/settings` に保存し、選択肢（`/api/options`）と `/api/health` を取り直す（ComfyUI が変われば使えるモデル・LoRA も変わるため）。設定を読み込むまでは無効化しておく
- **モードとワークフローに応じた項目の非表示**（`form.hiddenFields`）: 使わない項目はグレーアウトではなく**その欄ごと表示しない**。ただし値は `FormState` に残るので、その項目を使うモード / ワークフローへ戻せば入力内容が復元される
  - 動画生成モードでは画像ワークフロー・画像プロンプト・LoRA（画像）・トリガーワードを出さない（LoRA（動画）は出す）。画像のみモードでは動画ワークフロー・動画プロンプト・ネガティブ・リファレンス音声・秒数・fps・LoRA（動画）を出さない
  - **選択した動画ワークフローのマニフェスト**に従い、音声入力を持たないワークフローでは音声欄を出さず、必要な入力（最終フレーム / 参照動画）の欄だけを出す。**必須ではないが受け取れる入力**（Veo の開始フレーム・最終フレーム画像）も欄は出す（`requires` だけでなく `supports` を見る）。渡すかどうかはユーザー次第で、空なら送らない
  - **画像ワークフロー**も同様で、編集系（qwen-image）では参照画像の欄が出る代わりにアスペクト比 / メガピクセルが消える
  - 音声モードでは画像・動画のセクション一式を出さず、音声ワークフローと、そのワークフローが露出しているつまみだけを出す
- 「画像＋動画」モードのプルダウンには開始フレームを受け取れる動画ワークフローのみを出す（選択中のものが対象外になったら自動で切り替える）
- **選択式フィールド**を宣言しているワークフロー（wan_dancer）では、ワークフローセレクトの直下にその選択肢のプルダウンが並ぶ（§3.1）。自動決定できる項目には「自動（入力に合わせる）」、それ以外には「既定（<値>）」が先頭に入る。`video_prompt` が任意のワークフローではプロンプト欄に「（任意）」と出す
- LoRA チェーンを持たないワークフロー（wan_dancer）では LoRA（動画）セクションを出さない（挿せないため。指定したジョブはバックエンドが 422 にする）
- 動画ネガティブはプリセット選択（ワークフロー既定 / 現行値 / モデル作者版）+ 編集可（詳細設定アコーディオン内）
- 設定は**モーダルではなく専用ページ（フルページ）**。ヘッダーの [設定] で画面遷移し、ページ左上の [← 戻る] で生成画面に復帰する。3 タブ構成:
  - **接続 / Grok**: 「ComfyUI 接続先」（[接続先] のプルダウン + ComfyCloud / RunPod / ローカルのサブセクション。RunPod のサブセクションには Pod の ComfyUI URL・APIキーに続けて §5.1 の自動起動の設定を置く） / grok CLI コマンドと**使用モデル（既定: grok-4.5、変更可）**  / **モデル自動ダウンロード**のブロック（常に表示。ローカルの保存先パスは環境変数由来なので読み取り専用で見せ、「書き込み可 ✓」「パスが見つかりません」等の状態と、**Hugging Face トークン**・**Civitai APIキー**（どちらも `type="password"`。RunPod へ落とすときは Pod 側の環境変数が使われる）を並べる、§3.3）
  - **LoRA 管理**: 表示名・ファイル名・**対象ワークフロー（画像用 / 動画用）**・**モデルファミリー（画像用のみ）**・トリガーワード・既定強度・既定音声・並び順・**取得元 URL（任意）**の CRUD とサンプル画像の登録。一覧のバッジには対象とファミリーを出し、取得元 URL が登録済みなら `URL ✓`（title に URL）を添える。取得元 URL は LoRA 本体と同じ [追加] / [更新] で保存し、保存先はモデルタブと同じ `model_download_urls`（キーは `lora_name`）。**空欄で保存するとキーを消し**、**ファイル名を変えた場合は旧キーを消して新キーへ移す**（URL に変化が無ければ設定は PUT しない）。ここではダウンロードせず、モデルタブと同じく [DL] / [全DL]（§3.3）の取得元として使う
  - **モデル** / **LoRA 管理**: どちらもタブの先頭に [対象の接続先]（ComfyCloud / RunPod / ローカル。現在の接続先には「（現在の接続先）」を添える）のプルダウンを置き、選んだ環境の登録を読み書きする（初期値は現在の接続先。繋いでいない環境も整理できるよう、接続先そのものとは独立に切り替えられる。切り替えると未保存の編集は捨てて読み直す、§5）
  - **モデル**: 全ワークフローのモデルファイル名一覧を **画像 / 動画 / 音声の大分類 → ワークフローごとの折りたたみ**（既定は閉じ、見出しに項目数・未保存件数・既定から変更した件数のバッジ）に整理し、行ごとにテキスト入力で上書き。変更行はハイライト、[既定に戻す] で復帰、[保存] で全行を一括 PUT。各行にはさらに**候補リスト**（チップ + 追加/削除）があり、既定値と合わせて 2 件以上にすると生成フォーム / エージェントが実行ごとに選べるようになる。既定値入力・候補追加入力はどちらも `/api/options` の `model_files`（`"<class_type>.<field>"` ごとの ComfyUI ファイル一覧。LoRA は従来の `lora_files` で補う）があれば datalist で補完。さらに各行には**不足モデルのダウンロード**の UI がある: 値が `model_files` の該当リストに無ければ**未検出**バッジ、URL 入力欄（`model_download_urls`。キーはファイル名なので同じファイルを使う行では共有）と [DL] ボタン、進行中は進捗バーと取得済みバイト数（WS の `model_download` を購読）。**取得元 URL の登録・編集は環境や `COMFY_MODELS_DIR` の有無に関係なく常に使える**（いま繋いでいない環境ぶんの URL も先に登録しておけるため）。[DL] と、タブ上部の **[全DL]**（未検出かつ URL 登録済みを一括開始）は `comfy_cloud` 以外で常に出し、落とせない事情は押したときの 400 で知らせる（ローカル選択中は `dir-status` の理由をタブ上部の警告にも出す、§3.3）。**未検出バッジは「いま繋いでいる環境」を編集しているときだけ**出す（`model_files` は接続中の ComfyUI のものなので、他の環境の在庫は分からない）。バッジが出ない行でも [取得元 URL] を開けば [URL保存] と [DL] が並ぶ。**検出済みの行**でも取得元 URL は登録できる（手元には在るが RunPod の Pod には無いモデルを、あとで [DL] / [全DL] で入れるため）: 表がうるさくならないよう既定は畳んでおき、[▸ 取得元 URL]（登録済みならアクセント色 + ✓）を押すと URL 欄と [URL保存] が開く。[URL保存] はダウンロードせず `model_download_urls` だけを PUT し、**空欄で保存するとそのファイル名のキーを消す**（登録解除）
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
GET  /api/health                 … ComfyUI/Grok/kie.ai 疎通チェック
GET  /api/kie/credits            … kie.ai の残クレジット（1 credit = $0.005、§5.2）
POST /api/kie/check              … kie.ai の API キーを確認し直す（選択肢の出し分けに反映、§5.2）
GET  /api/options                … 画像/動画/音声ワークフロー一覧（必要入力・露出しているつまみ・秒数レンジつき）・アスペクト比・LoRA一覧・アセット一覧・ライブラリ一覧（library, §7.2）・実行時に選べるモデルスロット（model_slots）と ComfyUI のモデルファイル一覧（model_files）・生成バックエンドの可用性（backends, §5.2）
GET/POST/PUT/DELETE /api/loras   … アプリ内 LoRA 登録リストの CRUD（GET は `?target=` でその接続先のもの + 共通行、POST は `comfy_target` で紐づけ先を指定、§5）
GET  /api/library                … ライブラリ検索（kind / category / q / tag / limit / offset → items + total + tags、§7.2）
POST /api/library/{kind}         … ファイルをアップロードして登録
POST /api/library/from-job       … ジョブの出力（image / last_frame / video / audio）を登録
POST /api/library/sheet          … 画像素材を 1 枚のリファレンスシートに合成して登録（item_ids の順に配置、§7.2）
PATCH  /api/library/{id}         … 表示名 / NSFW フラグ / タグ / カテゴリの変更
DELETE /api/library/{id}         … 登録解除（ファイルも削除）
GET  /api/models                 … 全ワークフローのモデルファイル名一覧（既定値+現在値+候補リスト、キーは workflow_id でスコープ。`?target=` でその接続先のもの、省略時は現在の接続先）
PUT  /api/models                 … モデルファイル名の上書きと候補リストの保存（既定値と同値/空は削除、候補が空のキーは削除。`choices` 省略時は保存済みの候補を保持。`target` の環境だけを書き換える）
GET  /api/models/dir-status      … ローカルの models ディレクトリの状態（configured / exists / writable / path、§3.3）
GET  /api/models/downloads       … 進行中と直近のモデルダウンロード一覧
POST /api/models/download        … 不足モデルのダウンロード開始（filename / url / subfolder / target。local は自前・runpod は Pod の API へ・comfy_cloud は 400。保存先を検証して 400、二重実行は 409。進捗は WS、§3.3）
POST /api/models/download-all    … 未検出かつ取得元 URL 登録済みを一括開始（target。started / missing_urls / errors を返す、§3.3）
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
  app/routers/      health / settings / loras / models_config / model_download / assets / options / chat / jobs / kie / agent
  app/comfy.py      ComfyUI クライアント（/object_info, /upload/image, /prompt, /ws, /history, /view）
  app/kie.py        kie.ai クライアント（createTask / recordInfo ポーリング・ファイルアップロード・成果物 DL、§5.2）
  app/backends.py   生成バックエンドの可用性判定とキャッシュ（§5.2）
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
  app/model_sources.py   取得元 URL → 配布ページ URL（エージェントの調べ先、§3.3）
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
