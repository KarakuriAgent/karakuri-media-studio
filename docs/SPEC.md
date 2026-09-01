# Karakuri Media Studio 仕様書（ドラフト v0.2）

`workflow/` 配下の ComfyUI ワークフロー群（画像 4 種 / 動画: MiniMax H3 の 5 種 / 音声 2 種）をバックエンドとして使うメディア生成アプリの仕様。
プロンプト作成は Grok（サブスクリプション認証）に委譲し、実行・成果物管理・履歴保存を本アプリが担う。

> v0.2 での変更: 単一の合体グラフ `video-gen.json` を廃止し、分離された複数テンプレートを
> **注入マニフェスト**（ノード ID 直指定、`backend/app/workflows.py`）で駆動する方式に移行した。
> 「画像＋動画」モード（内部名 `full`）は「画像ワークフロー → 生成画像をアップロード →
> 動画ワークフロー」の **2 ジョブ連結**になった。
>
> v0.3 での変更: 画像ワークフローを 4 種（krea2 / anima / z-image / qwen-image-edit）から
> 選択式にし、画像 LoRA を**モデルファミリー**で仕分けるようにした。あわせて**音声モード**
> （MiniMax Music 3 / Stable Audio 3）を追加した。音声は画像・動画と連結しない独立ジョブ。

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
（各ステージはそのワークフローのバックエンド＝ ComfyUI のプロンプト、§5）。

| モード | 内部名 | 実行されるワークフロー | 開始フレーム |
|---|---|---|---|
| 画像＋動画 | `full` | 選択した画像ワークフロー → 選択した動画ワークフロー（2 段） | 1 段目の生成画像 |
| 動画生成 | `i2v` | 選択した動画ワークフローのみ | ワークフローが要求する入力（アップロード / 履歴 / なし） |
| 画像のみ | `image_only` | 選択した画像ワークフローのみ | ― |
| 音声 | `audio` | 選択した音声ワークフローのみ（独立ジョブ） | ― |
| Remotion | `remotion` | ComfyUI を通さず、同梱の Remotion プロジェクトを `npx remotion render`（§5.2。既定 OFF） | ― |
| 音源解析 | `audio_analysis` | 何も生成せず、音源から歌詞アライン・onset・ビート・無音区間を出す（§5.2。依存は別 venv） | ― |

`audio_analysis` は生成を 1 つも行わない解析モード。成果物は
`outputs/{job_id}/analysis.json` だけで、`GET /api/v1/jobs/{id}` の `analysis_url` に出る。
歌詞つきの映像で「何秒に何を出すか」を決め打ちしないための材料を作る（§5.2）。

`audio` は他の 3 モードと連結しない独立モード。画像・動画のフィールド（`video_workflow` /
`source_image` / `loras` など）は一切使わず、指定すると 422 で拒否される（§2.4）。

### 2.1 「画像＋動画」の 2 ジョブ連結

旧方式のようにグラフを合体させず、同一 `job_id` のもとで順に実行する:

1. 選択した画像ワークフローを `/prompt` に投入 → 完了を待つ
2. `SaveImage` の出力を `/view` でダウンロードし `outputs/{job_id}/image.png` に保存
3. その画像を `/upload/image` で ComfyUI の input ディレクトリへ上げる
4. 選択した動画ワークフローに開始フレームとして注入して投入 → 完了を待つ
   （`LoadImage` のファイル名）
5. 動画をダウンロードし、ffmpeg でラストフレームを抽出

- 進捗は 1 ジョブとして配信され、メッセージが「画像生成 (1/2)」→「動画生成 (2/2)」と切り替わる
- `workflow_json` には **両方のグラフ**を `{"image": {...}, "video": {...}}` の形で保存する（各要素は `workflow_id` / `prompt_id` / `graph`）。単段ジョブも同じ形（キーは `image` / `video` / `audio`）。再現性の担保はこれで行い、`rerun` は `params` から作り直す
- `full` で選べるのは**開始フレームを受け取れる動画ワークフローだけ**（`accepts_start_image`）。t2v と参照専用（r2v）は対象外で、選択すると 422 になる

### 2.2 動画ワークフロー（`workflow/video/<family>/`）

| id | 表示名 | ckpt | 必要入力 | `full` 可 |
|---|---|---|---|---|
| `minimax_h3_t2v` | テキスト→動画・音声つき (MiniMax H3 t2v) | minimax_h3 fl2va int8 | なし | ✕ |
| `minimax_h3_t2v_save` | テキスト→動画・音声つき・ラテント保存 (MiniMax H3 t2v + Save Latent) | 同 `minimax_h3_t2v` | 同 `minimax_h3_t2v` | ✕ |
| `minimax_h3_t2v_turbo` | テキスト→動画・音声つき (MiniMax H3 t2v Turbo) | minimax_h3 fl2va w4a8 + turbo 4step LoRA | 同 `minimax_h3_t2v` | ✕ |
| `minimax_h3_t2v_save_turbo` | テキスト→動画・音声つき・ラテント保存 (MiniMax H3 t2v Turbo + Save Latent) | 同 `minimax_h3_t2v_turbo` | 同 `minimax_h3_t2v` | ✕ |
| `minimax_h3_t2v_opt` | テキスト→動画・音声つき (MiniMax H3 t2v Optimized) | minimax_h3 fl2va w4a8（蒸留 LoRA なし・20 steps） | 同 `minimax_h3_t2v` | ✕ |
| `minimax_h3_t2v_save_opt` | テキスト→動画・音声つき・ラテント保存 (MiniMax H3 t2v Optimized + Save Latent) | 同 `minimax_h3_t2v_opt` | 同 `minimax_h3_t2v` | ✕ |
| `minimax_h3_i2v` | 画像→動画・音声つき (MiniMax H3 i2v) | minimax_h3 fl2va int8 | 画像（最終フレーム画像は任意） | ○（既定） |
| `minimax_h3_i2v_save` | 画像→動画・音声つき・ラテント保存 (MiniMax H3 i2v + Save Latent) | 同 `minimax_h3_i2v` | 同 `minimax_h3_i2v` | ○ |
| `minimax_h3_i2v_turbo` | 画像→動画・音声つき (MiniMax H3 i2v Turbo) | minimax_h3 fl2va w4a8 + turbo 4step LoRA | 同 `minimax_h3_i2v` | ○ |
| `minimax_h3_i2v_save_turbo` | 画像→動画・音声つき・ラテント保存 (MiniMax H3 i2v Turbo + Save Latent) | 同 `minimax_h3_i2v_turbo` | 同 `minimax_h3_i2v` | ○ |
| `minimax_h3_i2v_opt` | 画像→動画・音声つき (MiniMax H3 i2v Optimized) | minimax_h3 fl2va w4a8（蒸留 LoRA なし・20 steps） | 同 `minimax_h3_i2v` | ○ |
| `minimax_h3_i2v_save_opt` | 画像→動画・音声つき・ラテント保存 (MiniMax H3 i2v Optimized + Save Latent) | 同 `minimax_h3_i2v_opt` | 同 `minimax_h3_i2v` | ○ |
| `minimax_h3_r2v` | 参照素材→動画・音声つき (MiniMax H3 r2v) | minimax_h3 ref2va int8 | `reference_images` 9 枚 / `reference_videos` 3 本 / `reference_audios` 3 本まで・合計 1 件以上（開始フレームは不可） | ✕ |
| `minimax_h3_r2v_save` | 参照素材→動画・音声つき・ラテント保存 (MiniMax H3 r2v + Save Latent) | 同 `minimax_h3_r2v` | 同 `minimax_h3_r2v` | ✕ |
| `minimax_h3_r2v_context` | 参照素材→動画・音声つき・連続カット (MiniMax H3 r2v + Motion Context) | minimax_h3 ref2va int8 | 同 `minimax_h3_r2v` に加えて `reference_video`（直前カットの動画）と `context_latent_path`（直前カットの AV ラテント）が必須。`context_latent_hires_path`（2 パス目のラテント）は任意（無ければ 1 段引き継ぎ） | ✕ |
| `minimax_h3_r2v_turbo` | 参照素材→動画・音声つき (MiniMax H3 r2v Turbo) | minimax_h3 fl2va w4a8 + ref LoRA + turbo 4step LoRA | 同 `minimax_h3_r2v` | ✕ |
| `minimax_h3_r2v_save_turbo` | 参照素材→動画・音声つき・ラテント保存 (MiniMax H3 r2v Turbo + Save Latent) | 同 `minimax_h3_r2v_turbo` | 同 `minimax_h3_r2v` | ✕ |
| `minimax_h3_r2v_context_turbo` | 参照素材→動画・音声つき・連続カット (MiniMax H3 r2v Turbo + Motion Context) | 同 `minimax_h3_r2v_turbo` | 同 `minimax_h3_r2v_context` | ✕ |
| `minimax_h3_r2v_opt` | 参照素材→動画・音声つき (MiniMax H3 r2v Optimized) | minimax_h3 fl2va w4a8 + ref LoRA（蒸留 LoRA なし・20 steps） | 同 `minimax_h3_r2v` | ✕ |
| `minimax_h3_r2v_save_opt` | 参照素材→動画・音声つき・ラテント保存 (MiniMax H3 r2v Optimized + Save Latent) | 同 `minimax_h3_r2v_opt` | 同 `minimax_h3_r2v` | ✕ |
| `minimax_h3_r2v_context_opt` | 参照素材→動画・音声つき・連続カット (MiniMax H3 r2v Optimized + Motion Context) | 同 `minimax_h3_r2v_opt` | 同 `minimax_h3_r2v_context` | ✕ |

- id はファイル名（拡張子なし）
- **`_save` / `_context` は手動の生成フォームにもプロンプト用のカタログにも出ない**
  （`WorkflowSpec.studio_only`）。この 12 個はドラマスタジオが
  「ラテント連続性」×「動画生成品質」から id を組み立てて使うだけの版で、入力の形も
  仕上がりも素の版と同じなので人が手で選ぶ意味が無く、選べると「ラテント連続性 OFF
  なのに保存版」のような矛盾だけが増える。落としているのは
  `app.workflows.selectable_specs`（＝ `/api/options` とプロンプトのカタログ）だけで、
  **id 直指定（`get_spec`）は従来どおり通る**: スタジオの解決（`_plan_render`）・
  ジョブの実行・マニフェスト検証（`validate_specs`）・外部 API の `video_workflow`
  直指定はどれも 21 件すべてを見る
- `_turbo` / `_opt` / `_context` / `_save` はカスタムノード前提なので、**接続先が `comfy_cloud` のときは選択肢に出ない**（§3.1）
- **`_turbo` / `_opt`** はドラマスタジオからは直接選ばず、プロジェクトの
  **「動画生成品質」**（`quality` = `normal` / `opt` / `turbo`）として持つ。品質は論理モード
  （t2v / i2v / r2v / r2v_context）ともラテント連続性とも直交していて、ワークフロー id は
  **3 段**で決まる（`app.studio._plan_render`）:
  1. 論理モード（`_pick_workflow`。t2v / i2v / r2v / r2v_context）
  2. `latent_continuity` が ON なら保存付き（`_save`）への読み替え（`_latent_save_workflow`）
  3. そこまでで決まった論理ワークフロー × 品質 → バリアント（`_quality_workflow`）

  3 段目の表（`app.studio.QUALITY_WORKFLOWS`）は 7 つの論理ワークフロー
  （`minimax_h3_{t2v,i2v,r2v}` / `minimax_h3_{t2v,i2v,r2v}_save` / `minimax_h3_r2v_context`）
  すべてに `_turbo` / `_opt` を持つので、**ラテント連続性が ON でも t2v でも品質は効く**。
  素へ落ちるのは接続先が対応しない（`comfy_cloud`）ときだけで、そのときも 2 段目までの結果
  （＝保存付きの版）は保ったまま品質だけを落とす。理由は投入プレビューの `workflow_reason` に出る。
- **画像生成品質**（`image_quality` = `normal` / `opt` / `turbo`、既定 `normal`）は、上の
  「動画生成品質」と**同じ 3 段だが独立したプロジェクト設定**。効くのは作品の素材となる
  静止画を MiniMax H3 Image で焼くときだけで、`minimax_h3_{t2i,i2i,r2i}` の素 / `_opt` /
  `_turbo` を選ぶ（`app.studio.image_quality_workflow`。接続先が対応しなければ素へ落とす）。
  動画の `quality` は静止画には流用しない（動画を turbo で回していても素材の絵は素で焼く、
  という使い分けのために分けてある）。いまのところアプリ側に静止画を焼く経路は無く、
  素材画像を作るのは**外部エージェント**（SKILL 経由で `/api/v1` を叩く Claude Code /
  Codex / Cursor CLI など、§9「外部公開 API」）なので、この値はまず**外部エージェントへの
  指示値**として効く。
- **素材画像の画質**（`image_megapixels` / `image_aspect_ratio` / `image_steps`、
  既定は `NULL` / `NULL` / `0`）は、下の動画側の `megapixels` / `aspect_ratio` / `steps` と
  同じ 3 項目を**素材の静止画用に別で持つ**もの。素材の静止画ジョブ（`mode: "image_only"`）には
  こちらを使い、**動画用の値は流用しない**。`NULL` / `0` = 指定しない＝テンプレートの既定のまま
  （MiniMax H3 Image は約 0.98MP）で、`image_steps` の上限は動画側と同じ `MAX_STEPS`（150）。
  「設定されているものだけを渡す」形にまとめるのは `app.studio.image_render_defaults`。
- 画質のほうはワークフローを選ばず、プロジェクトの **`megapixels` / `aspect_ratio`**
  （生成フォームと同じ 2 項目を作品単位の既定として持つ）が投入時の値を決める。
  どちらも `NULL` = **明示しない**（＝これまでどおりワークフロー宣言の
  `default_megapixels` / グローバル既定 0.4MP、画面比は既定のまま）。効き方は
  **テイク 1 回ぶんの上書き → Shot 個別 → プロジェクト → グローバル既定**の順で、2 つは
  それぞれ独立に解決する。
  品質（`quality`）とも直交していて、どちらのバリアントに落ちても同じ画素数で焼く。
- **サンプリング回数**もプロジェクトの設定（`steps`）として持てる。`0` = 未指定 =
  **テンプレートの既定のまま**（turbo は 4、normal / opt は 20）で、上限は `MAX_STEPS`（150）。
  `steps` を宣言しているワークフロー（MiniMax H3 は全バリアントが `BasicScheduler.steps` を
  宣言している）にだけ注入される。効き方は**テイク 1 回ぶんの上書き → プロジェクト →
  テンプレートの既定**で、Shot 単位の設定は持たない（品質を変えずに刻みだけ増減したい、
  という使い方のための作品共通のつまみ）。
- **ラテントアップスケール**（`latent_upscale`、既定 **ON**）もプロジェクトの設定として持つ。
  こちらはワークフロー id を変えず、投入するジョブの選択式
  （`selects.latent_upscale` = `on` / `off`。§3.1 の「ラテントアップスケール」）に落ちる。
  効き方は**テイク 1 回ぶんの上書き → プロジェクト → 既定（ON）**で、Shot 単位の設定は持たない。
  接続先が `MinimaxH3LatentUpscaler3D` を入れられない（`comfy_cloud`）ときは
  ON を頼んでも **黙って `off` に落とし**（投入そのものは通す）、理由を投入プレビューの
  `workflow_reason` に足す（`app.studio._resolve_selects`）。宣言を持たないワークフローには
  `selects` を載せない。**連鎖の途中で on / off を切り替えるとラテント連続性が合わなくなる**
  （→ §3.1「2 段引き継ぎ」の制約）。
- テイク 1 回ぶんの上書きは `POST /api/studio/shots/{id}/render` の**任意のボディ**
  （`app.models.StudioRenderRequest`。`megapixels` / `aspect_ratio` / `duration` / `steps` /
  `seed` / `latent_upscale`、すべて任意）。送った項目だけがその 1 回の投入に効き、**Shot もプロジェクトも
  書き換えない**（何を使ったかは Take の元ジョブの `params` に残る）。ボディを省けば
  今までどおり。`steps` だけは `0` も「テンプレートの既定のまま」の**明示**として扱い、
  プロジェクトの設定より優先される。範囲外の `steps`（0〜150 の外）と `duration`
  （1〜15 秒の外）は `StudioError` → 400。
- **`minimax_h3_*_save`** は素の t2v / i2v / r2v に `MiniMaxH3MotionContextSaveLatent` →
  `PreviewAny` の 2 ノードだけを足した版で、**AV ラテントを保存する以外は素の版とまったく同じ**
  （Motion Context の読み込み・`…Trim` は入っていないので尺も変わらない）。ドラマスタジオは
  「ラテント連続性」が ON のプロジェクトでは通常カットもこちらに読み替えて投げる: **連鎖の起点になる
  カットがラテントを残さないと、次のカットに引き継ぐものが無く連鎖を始められない**ため。素の版の
  テンプレートは触っていないので、OFF のプロジェクトと Comfy Cloud は今までどおり素の版を使う。
  `_save` にも `_turbo` / `_opt` があり（`minimax_h3_*_save_turbo` / `…_save_opt`）、中身は
  **その品質のテンプレート + 保存の 2 ノード**。品質のテンプレートは高速化パッチのチェーンで
  ノード 150〜155 を使い切っているので、保存の 2 ノードは素の版の 155 / 156 ではなく
  **160〜166 の側（165 / 166）**に置いてある。
- **`minimax_h3_r2v_context`** は素の r2v に Motion Context（`MiniMaxH3MotionContext` /
  `…LoadLatent` / `…SaveLatent` / `…Trim`）を足した**連続カット専用**の版。ドラマスタジオの
  「ラテント連続性」（プロジェクトの `latent_continuity`）だけが選ぶ。`ReferenceToVideo` の
  CONDITIONING に直前カットの末尾フレームと音を追記し、動き・音・見た目をつないだまま次のカットを作る。
  受け取るのは r2v の参照素材一式に加えて `reference_video`（直前カットの mp4。`LoadVideo` →
  `GetVideoComponents` でフレーム列にする）と `context_latent_path`（直前カットのサンプラー出力を
  `…SaveLatent` が safetensors で保存したもの。**ComfyUI 側のパス**なのでアップロードは通さない）。
  Motion Context のつまみ（`context_length` = `"22"`（文字列コンボ `"22"` / `"5"` / `"39"` /
  `"56"`）・`audio_context_length` = 0（映像の窓に追従））は**テンプレートの固定値**でジョブからは
  動かせない。`encode_mode` / `anchor_mode` / `crop` / `audio_mode` は本家 ComfyUI-H3-Motion-Context
  v0.2.0 には入力として存在せず、ノード内部で固定されている。ピン留めした 22 フレームが出力の
  先頭に返り、`…Trim` が映像と音声を揃えて落とすため、**仕上がりの尺は指定した尺より 22 フレーム
  （24fps で約 0.9 秒）短くなる**。`context_latent` は生成するクリップと同じ解像度である必要がある。**連鎖の途中でプロジェクトの
  `megapixels` / `aspect_ratio` を変えると、次のカットは前クリップと違う解像度で焼かれてラテント連続性が
  合わなくなる**（設定変更はそれ以降の生成にしか効かないので、変えるなら連鎖の切れ目で）。
  このカットぶんのラテントは `h3_context/{job_id}` に保存し、パスは `PreviewAny` 経由で `/history`
  から回収して Take の `latent_path` に控える（回収できなければ NULL のままで、次のカットは
  「引き継ぎ元が無い」として断られる）。`latent_upscale` が `on` のときは 2 パス目
  （最終解像度）のラテントも `h3_context/{job_id}_hires` に保存して Take の `latent_hires_path`
  に控え、次のカットは 2 本とも受け取る（`context_latent_path` / `context_latent_hires_path`）。
  2 本目が無い過去テイクからは従来どおりの 1 段引き継ぎになる（→「ラテントアップスケール」の
  §2 段引き継ぎ）。連続カット版にも `_turbo` / `_opt`
  （`minimax_h3_r2v_context_turbo` / `…_context_opt`）があり、中身は**その品質のテンプレート +
  Motion Context の 5 ノード + 保存の 2 ノード**。ノード ID は素の版の 150〜156 を 10 ずらした
  **160〜166**（品質のテンプレートが 150〜155 を使うため）。
- **`minimax_h3_*`（`workflow/video/minimax-h3/`、family `minimax-h3`）** は**映像とステレオ音声を同時に生成する**
  ローカルモデル（MiniMax H3）。プロンプトは公式 rewrite 契約で書く: ベース（t2v / i2v）は任意の
  アライメント行のあと `integrated_multimodal_description:` / `overall_soundscape:` /
  `non_diegetic_music:`、参照（r2v）はそれに加えて `subject_definitions:` / `summary:` /
  `retention_analysis:` / `detailed_description:`。`[Shot 1]` にタイムスタンプは無く、以降は
  `[Shot N] At MM:SS.mmm, the camera cuts to …`。カメラはショット内の自然文、台詞は
  `<d>[Language] …</d>`（話者 ID と言い方は `<d>` の外）。`Camera:` / `Audio:` フッタや
  `[0s-1.5s] Shot 1:` は使わない。CFG を使わない（`BasicGuider`）ので **negative prompt は無く**、
  除外したいものは本文に書く。24fps 固定で、
  尺は 17k+5 フレームの格子に**切り上げ**（`FrameGrid`、5 秒 = 124 フレーム）。既定の画角は短辺 768px
  （最大 768x1344・32 の倍数）なので `megapixels` は 0.4 前後。`minimax_h3_i2v` は `end_image` を**任意**で
  受け取り、渡すと `MiniMaxH3ImageToVideo.last_frame` に繋いで最終フレーム指定（fl2va）になる。渡さなければ
  雛形の `LoadImage` ごとグラフから外れる（`optional_loaders`、下記）。`minimax_h3_r2v` の入力は開始フレームでは
  なく**同一性・動き・音の参照**で、他の参照専用ワークフロー（`*_ref`）と同じ `reference_images`（9 枚）/
  `reference_videos`（3 本）/ `reference_audios`（3 本）で受け取る（合計 1 件以上必須）。プロンプトからは種類ごとに
  渡した順で `<Picture i>` / `<Video k>` / `<Audio j>` と呼び、**参照動画のサウンドトラックは常に一緒に渡されて
  `<Audio j>` の連番を単独音声と共有する**（動画のぶんが先に番号を消費する）。参照動画は 24fps 前提で fps 変換は
  しない。**件数ぶんのローダーはビルダーがグラフに生やす**（`RefMediaFan`、下記）。ユーザー LoRA を挿すチェーンは
  持たない。
  MiniMaxH3 系ノードは新しめの ComfyUI master にしか無いので、ヘルスチェックが「custom node なし」と出たら
  ComfyUI を更新する
- 既定は `minimax_h3_i2v`（開始フレームを受け取れて `full` の 2 段目になれる、いちばん素直な構成）
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
| `minimax_h3_t2i` / `_opt` / `_turbo` | MiniMax H3 Image t2i | `minimax-h3-image` | なし | text-to-image。H3（音声つき動画モデル）でフレームのパケットを作り 1 枚を選ぶ（枚数は `selects` の `quality_profile` で 5 / 9 / 13 / 20）。`ResolutionSelector` は無く、アプリが幅・高さを **32 の倍数**で計算して注入（既定 0.98MP） |
| `minimax_h3_i2i` / `_opt` / `_turbo` | MiniMax H3 Image i2i | `minimax-h3-image` | 画像（編集元画像） | **編集系**。`source_image` を fl2va のフレーム 0 に置く。解像度は `aspect_ratio` + `megapixels`（合わせ方は `selects` の `source_fit`・既定 crop_center） |
| `minimax_h3_r2i` / `_opt` / `_turbo` | MiniMax H3 Image r2i | `minimax-h3-image` | 参照画像 1〜9 枚 | **参照編集系**（base は ref2va、`_opt` / `_turbo` は fl2va + 参照 LoRA）。`reference_images` を渡した順に `<Picture 1>` … で参照。開始フレーム（`source_image`）は受け取らない |
| `grok_imagine_t2i` | Grok Imagine 画像生成（サブスク CLI） | `grok-imagine` | なし | **ComfyUI 非依存**（`backend: "grok_cli"`、§5.2）。text-to-image |
| `grok_imagine_edit` | Grok Imagine 画像編集（サブスク CLI） | `grok-imagine` | 画像（編集元画像） | **ComfyUI 非依存**の編集系。`source_image` 必須で、出力解像度は入力画像から決まる |

- 既定は `krea2_turbo`（選択式になる前の唯一の画像ワークフロー）
- `qwen_image_edit_2511` は画像ステージが走るモード（`full` / `image_only`）で必ず `source_image` を要求する。
  `full` では編集結果がそのまま 2 段目の開始フレームになる
- `image_prompt` の書き方はファミリーごとに違い（krea2 は長い自然文、qwen は編集指示）、
  Grok のシステムプロンプトにはファミリー別のガイドが埋め込まれる（§4.2）。
  `grok-imagine` はグラフを持たず LoRA も差せないので、`image_families()`（LoRA 登録の
  選択肢とプロンプトガイドの単位）には**並ばない**
- `minimax-h3-image` は外部のカスタムノード（ComfyUI-MiniMax-H3-Image-Studio、
  `deploy/runpod/custom_nodes.txt` に固定）が要る。`H3*` の 6 クラスを
  `OPTIONAL_CLASS_TYPES` に載せてあるので、カスタムノードが無い接続先
  （Comfy Cloud を含む）では base / `_opt` / `_turbo` とも丸ごと選択肢から落ちる
  （§3.2。`_opt` / `_turbo` は動画側と同じ高速化ノードも焼き込んである）。
  `_turbo` の Turbo LoRA は `LoraLoaderModelOnly` で読むので設定画面から
  差し替えできる（§3.3）
- `minimax-h3-image` のモデル構成は**動画側（`minimax-h3`）とそろえてある**:

  | | base | `_opt` | `_turbo` |
  |---|---|---|---|
  | UNET（t2i / i2i） | `minimax_h3_fl2va_pruned_w4a8_mixed` | 同左 | 同左 |
  | UNET（r2i） | `minimax_h3_ref2va_pruned_w4a8_mixed` | `minimax_h3_fl2va_pruned_w4a8_mixed` | 同左 |
  | CLIP | `qwen3vl_32b_heretic_minimax_h3_nvfp4` | 同左 | 同左 |
  | 動画 VAE | `minimax_h3_video_vae_fp16` | `minimax_h3_video_vae_int8_convrot` | 同左 |
  | LoRA | なし | r2i のみ参照 LoRA（ノード 144） | 4step 蒸留 LoRA（t2i / i2i はノード 150、r2i は 143 → 144 で参照 LoRA と 2 段） |
  | `H3SamplingSettings` | res_multistep / simple / 20 | 同左 | 4 ステップ（t2i / i2i は res_multistep、r2i は euler） |

  **r2i の `_opt` / `_turbo` だけは土台が違い**、動画の r2v と同じく ref2va の量子化ウェイトではなく
  `minimax_h3_fl2va_pruned_w4a8_mixed` に参照 LoRA `minimax_h3_ref_lora_rank_256_bf16` を
  `LoraLoaderModelOnly`（ノード 144・strength 1.0）で重ねて参照モードにする。`_turbo` はさらに
  4step 蒸留 LoRA `minimax_h3_fl2v_turbo_4step_v1.1_768p_comfyui_bf16` を UNET 直後（ノード 143）に
  挟む（`UNETLoader → 143 → 144 → PathchSageAttentionKJ → …`）。t2i / i2i の `_turbo` は
  蒸留 LoRA 1 段だけ（ノード 150）
- `minimax_h3_r2i` は**画像ステージが参照素材（`reference_images`）を受け取る**唯一の
  ワークフロー。`RefMediaFan` の宣言で、渡された枚数ぶん `LoadImage` を生やして
  1 枚目を `source_image`（= `<Picture 1>`）、2 枚目以降を `reference_image_2` … に
  繋ぎ直す。1 枚以上必須（`min_refs`）
- `minimax-h3-image` の 9 本はノードの widget を**選択式フィールド**（`selects`、§3.1）として
  公開してある。共通が `quality_profile`（フレーム枚数 `recommended | 5 frames` /
  `extended quality | 9 frames` / `high quality | 13 frames` /
  `maximum quality | 20 frames (slow)`。**上げるほど H3 が使える時間方向の文脈が増えて
  品質が上がり、そのぶん遅く VRAM も要る**）・`frame_strategy`（`H3ImageFrameSelector.strategy`。
  デコードしたパケットから 1 枚を選ぶやり方）・`optimize_for_still`（静止画向けの
  プロンプト包み・既定 `on`）。i2i / r2i はさらに `source_fidelity`（0.00〜1.00 の段階。
  **denoise ではなくプロンプトに足す保持要求の強さ**）と `source_fit`、r2i は
  `reference_detail` を持つ。`H3ImageDecode` はフレーム枚数を latent のメタデータから
  読むので注入点を持たず、`quality_profile` 1 つで枚数もデコードも決まる

### 2.4 音声ワークフロー（`workflow/audio/`）

`mode: "audio"` のときだけ走る**独立した 1 ステージ**。開始フレームを取らず、生成もしない。
LoRA チェーンも持たない（テンプレートに LoRA ノードが無い）ので、LoRA を指定したジョブは 422 になる。

| id | 表示名 | family | 秒数（min/既定/max） | 固有フィールド |
|---|---|---|---|---|
| `minimax_music_3` | MiniMax Music 3（音楽・歌もの） | `minimax-music` | 1 / 60 / 300 | `lyrics`（空でインスト） |
| `stable_audio_3_medium_base` | Stable Audio 3 Medium（効果音・環境音・音楽） | `stable-audio` | 1 / 60 / 380 | `audio_category`（Music / Instrument / SFX / One-shot）・`reprompt`（内蔵 LLM でのプロンプト展開） |

- 既定は `minimax_music_3`。テンポ・キー・歌い手といった指定は、専用のつまみではなく
  `audio_prompt`（キャプション）本文に書く
- ジョブの必須項目は `audio_prompt` のみ。`duration` がワークフローの範囲外、`audio_category` が
  ComfyUI ノードの COMBO 値に無い、といったものはジョブ投入前に 422 で弾く
  （どれも ComfyUI 側で prompt 全体が失敗するため）
- 出力は mp3（MiniMax Music 3 は `SaveAudioAdvanced`、Stable Audio は `SaveAudioMP3`）で
  `outputs/{job_id}/audio.mp3` に保存し、`jobs.audio_output_path` に記録する
- 秒数の上下限・COMBO 値の一覧は `backend/app/workflows.py`（`min_duration` / `max_duration` /
  `AUDIO_CATEGORIES`）が単一の情報源で、フォーム・Grok カタログ・バリデータが同じ集合を見る
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
| アスペクト比 / メガピクセル | 画像: `aspect_ratio` / `megapixels` → ResolutionSelector（krea2 は `49`、anima は `91`）。z-image と動画: アプリが幅・高さを計算して `width` / `height` に注入。qwen-image-edit は入力画像から決まるので注入しない | セレクト（選択肢は `/object_info` の ResolutionSelector から動的取得）+ 数値。メガピクセルの既定は 1.0 だが、`default_megapixels` を宣言するワークフローを選ぶとその値になる（下記） |
| 音声プロンプト・歌詞・除外タグ・カテゴリ・展開 | `prompt` / `lyrics` / `negative_tags` / `audio_category` / `reprompt` | `mode: "audio"` のみ。選択中の音声ワークフローが露出しているつまみだけ表示（数値の長さを宣言しないモデルでは秒数欄も出ない）。選択式フィールド（§3.1）も音声ワークフローの宣言に従って描画する |
| LoRA（画像・複数可） | 画像ワークフローの `lora_chain` を動的構築（§3.4） | 「LoRA（画像）」セクション。登録 LoRA のうち `target = 'image'` かつ**選択中の画像ワークフローと同じファミリー**のものを複数選択＋強度スライダー |
| LoRA トリガーワード（画像） | `trigger_concat` → `30:27` (StringConcatenate) / `trigger_switch` → `30:28`。この 2 つを持つのは krea2 テンプレートだけで、他の画像ワークフローには自動前置の口が無い（トリガーワードは `image_prompt` 本文に書く） | 選択 LoRA のトリガーワードを自動連結（編集可） |
| LoRA（動画・複数可） | 動画ワークフローの `lora_chain` を動的構築（§3.4） | 「LoRA（動画）」セクション。登録 LoRA のうち `target = 'video'` のものを複数選択＋強度スライダー |
| LoRA トリガーワード（動画） | 動画プロンプト文字列の先頭に前置 | 同上（自動連結・編集可） |
| リファレンス音声 | `audio` → `276` (LoadAudio)。要求するワークフローのみ | アップロード（`/upload/image` で送信 → ファイル名を注入） |
| 開始フレーム / 最終フレーム / 参照動画 | `image` / `end_image` / `video` | ワークフローの必要入力に応じて表示。画像は D&D・履歴のラストフレームからも選べる |
| マルチモーダル参照（参照画像 / 参照動画 / 参照音声） | `reference_images` / `reference_videos` / `reference_audios`（複数ファイル → 件数ぶんのローダーをグラフに生やす） | 宣言のあるステージが走るときだけ表示。動画側（MiniMax H3 r2v は画像 9 / 動画 3 / 音声 3）は `mode: "i2v"`、画像側（MiniMax H3 Image r2i は画像 9）は `mode: "full"` / `"image_only"`。1 欄が複数ファイルを持ち、選んだ順がグラフに渡る順序になる。**開始フレーム / 最終フレームとは排他**（下記） |
| 秒数 (Duration) | `duration` | 数値・**上限なし**。長尺は VRAM 次第で ComfyUI 側エラーになり得ることを UI に注記。`duration` を持たないワークフローでは欄ごと出さない |
| 選択式フィールド | ワークフローの `selects`（論理名 → CustomCombo 等） | 宣言のあるワークフローだけ、ワークフローセレクトの直下にプルダウンが並ぶ（下記） |
| フレームレート | `fps` | 数値（既定 25） |
| ステップ数 | `steps` → 各テンプレートのサンプラー（`KSampler.steps` / `BasicScheduler.steps`） | 数値・**空欄（0）= 未指定**でテンプレート既定のまま。宣言のあるワークフローでだけ欄が出る（下記） |
| 画像・動画プロンプト | `prompt`（画像 `30:19` / 動画は各テンプレート） | テキストエリア（手動入力が基本。Grok チャット §4.3 の結果を反映して編集も可） |
| 動画ネガティブ | `negative` | プリセット切替（ワークフロー既定 / 現行値 / モデル作者版）+ 直接編集可。**空欄ならテンプレート既定値のまま**（dev 系は `pc game, …`、distilled 系は品質ネガ） |

#### サンプリングのステップ数（`steps`）

「何ステップ回すか」はモデルごとに前提が違う（蒸留された turbo 系は 4、MiniMax Music 3 は 30）ので、
**ワークフローの既定値を正**として扱い、`steps` は**上書きしたいときだけ**指定するつまみにしてある。

- マニフェストに `"steps": T(<node>, "steps", "KSampler" | "BasicScheduler")` を宣言したワークフローだけが
  受け取る（`/api/options` の `supports` に出るので、フォームの欄も自動で出る）
- ジョブの `steps` は `0` が既定 = 未指定で、**正の値のときだけ注入**する（`workflow._inject_steps`）。
  未指定ならテンプレートの値がそのまま残る
- 上限は `models.MAX_STEPS`（150）で、範囲外は 422
- サンプラーの `steps` は INT なので、注入時に整数へ丸める（`workflow._INT_INPUTS`）
- ステップ数の概念を持たないテンプレート（qwen-image-edit の PrimitiveInt スイッチなど）は
  宣言を持たず、欄も出ない

#### 複数ファイルの参照入力（`WorkflowSpec.multi_inputs`）

1 つの入力欄が**複数のファイル**を持つ論理入力の仕組み（MiniMax H3 r2v の参照素材、§2.2）。
**参照モードと先頭フレームモードは排他**なので、宣言を持つのは**参照専用のワークフロー**
（動画の `minimax_h3_r2v` 系と画像の `minimax_h3_r2i` 系）だけで、そちらは
`accepts_start_image=False` かつ `image` / `end_image` の受け取り口を持たない
（マニフェストの検証がこの同居を弾く）。参照素材は**画像ステージと動画ステージのどちらの
入力にもなる**ので、検証（`models.reference_problem`）とアップロード
（`jobs._comfy_reference_materials`）はその mode で走るステージ全体を見る
（`full` では画像・動画の両方が並び、どちらかが宣言していれば通る）。
`multi_inputs = {"reference_images": 9, ...}` をマニフェストに宣言すると、

- ジョブは `reference_images` / `reference_videos` / `reference_audios`（`workflows.MULTI_INPUT_FIELDS`）に
  **パスの配列**を持ち、並び順は指定した順のままグラフに渡る
- 生成フォームは宣言のある欄だけを出し（`form.referenceFields`）、件数と上限を表示する
- 宣言のないワークフローに渡す・上限を超える・拡張子が違う場合は 422（`models.reference_problem`）
- **参照モードでしか作れない設定**は、そのワークフローの `SelectSpec` にそのまま書ける

**参照モードと先頭フレームモードが相互排他**であることは、**ワークフローの分割**
そのものが表現している: 参照版に `source_image` / `end_image` を渡せば「受け取りません」
（`models.start_image_problem`）、`mode: "full"` は `accepts_start_image=False` を見て
`models.video_workflow_problem` が断る。フレーム版に `reference_*` を渡せば「受け取れません」
（`models.reference_problem`）。検証は Web UI（`form.validateForm` + `jobs._validate`）と
API（`JobCreate` の検証。内部 API と外部 API で共通）の 2 経路で同じ関数を通る。
素材のサイズ・解像度・尺の細かい制約はモデル側の判断に任せ、失敗理由をそのまま見せる。

##### ComfyUI 側で参照素材をグラフに展開する（`WorkflowSpec.ref_media` / `RefMediaFan`）

同じ `reference_images` / `reference_videos` / `reference_audios` を**ローカルのグラフ**で受け取るための宣言
（MiniMax H3 r2v）。外部 API は URL の配列を渡すだけで済むが、ComfyUI は 1 件 = 1 ノードなので、**渡された
件数ぶんローダーを作って繋ぐ**必要がある。`RefMediaFan(node=…, image_loader=…, video_loader=…,
video_decoder=…, audio_loader=…, min_refs=1)` を宣言すると、`workflow._build_ref_media` が

1. テンプレートの雛形ローダーと、受け側ノードの `ref_*` の入力を**いったん全部落とし**、
2. ジョブが渡した件数ぶん `app_ref_image_<n>` / `app_ref_video_<n>` + `app_ref_video_parts_<n>` /
   `app_ref_audio_<n>` を作って `ref_image_0`, `ref_video_0`, `ref_audio_0`, … に繋ぎ直す

（LoRA チェーン §3.4 と同じ「テンプレートを雛形として組み替える」やり方）。0 件なら可変入力ごと消えるので、
**雛形のファイル名が ComfyUI 側に無くて落ちる**ことがない。並び順は指定した順で、プロンプトの
`<Picture i>` / `<Video k>` / `<Audio j>` がその順に対応する。

参照動画だけは 2 ノードで 1 本になる: `LoadVideo` → `GetVideoComponents` でフレーム列（出力 0）と音声
（出力 1）に分け、**同じ番号**の `ref_video_N` と `ref_video_audio_N` の両方に繋ぐ。ノード側が番号でペアを
見る（`comfy_extras/nodes_minimax_h3.py`）ので、動画の音声を独立したリストにはせず**常に一緒に渡す**。
その結果 `<Audio j>` の連番は「参照動画のサウンドトラック → 単独の `reference_audios`」の順で消費される
（音声トラックの無い動画は `<Audio j>` を消費しないので番号がずれる点は notes に明記してある）。
`GetVideoComponents` の fps 出力は素材依存で、core に汎用の fps 変換ノードが無いので**変換はしない**
（参照動画は 24fps 前提）。

上限は `multi_inputs[<論理名>]`（単一情報源）、下限は `min_refs`（種類を問わない合計。
`models.reference_problem` が 0 件を 422 にする）。ファイルは他の入力と同じく `jobs._prepare_comfy` が
1 件ずつ `/upload/image`（画像・音声・動画のどれでも入力ディレクトリに置かれる）に上げ、その名前が
`GenerationParams.reference_image_names` / `reference_video_names` / `reference_audio_names` に並ぶ。
マニフェストの検証は「受け側と雛形が実在し、雛形の class_type が種類と合っていて、本当に `ref_*` に
繋がっているか」まで見る。

画像ステージ（`minimax_h3_r2i`）も同じ宣言を使う。ただし `H3ReferenceEditPrepare` は
1 枚目を**必須の `source_image`**（= `<Picture 1>`）で受け、2 枚目以降だけが任意の
`reference_image_2` … になっている。この形は `RefMediaFan(primary_image_field="source_image",
image_prefix="reference_image_", image_offset=1)` で宣言し、`_build_ref_media` が 1 枚目を
`source_image` に、`index` 番目（1 以上）を `reference_image_<index + 1>` に繋ぐ。
画像ステージのビルダー（`workflow.build_image_workflow`）も動画側と同じ
`_prune_optional_loaders` / `_build_ref_media` / `_inject_selects` を通る。

##### 渡されなかった任意入力をグラフから外す（`WorkflowSpec.optional_loaders`）

`inject` はファイル**名**を書き込むだけなので、任意の入力を空のままにすると雛形のローダーがグラフに残り、
ComfyUI が「テンプレートにしか無いファイル」を探して落ちる。`optional_loaders=("end_image",)` を宣言すると
`workflow._prune_optional_loaders` が、その入力が渡されなかったジョブでは**雛形のノードと、それを読んでいる
リンクごと**落とす（`RefMediaFan` と同じ手口の単数版）。受け側の入力はノード定義でも optional なので、
「リンクが無い」ことがそのまま「渡されていない」を意味する。MiniMax H3 i2v の `end_image` →
`MiniMaxH3ImageToVideo.last_frame` がこれ。

##### MiniMax H3 Turbo: 高速化をテンプレートに焼き込む

生成そのものを速くする仕掛けは**実行時オプションではなくテンプレート**が持つ。MiniMax H3 には
素の t2v / i2v / r2v と対になる **turbo** テンプレート（`minimax_h3_t2v_turbo` /
`minimax_h3_i2v_turbo` / `minimax_h3_r2v_turbo`）があり、ラテント保存版・連続カット版にも同じ差分を
当てた turbo（`minimax_h3_*_save_turbo` / `minimax_h3_r2v_context_turbo`）がある。
受け取る論理入力・プロンプトの書き方・`multi_inputs` は素の版と**完全に同じ**で、
違うのは中身だけ:

| 差分 | 素の版 | turbo |
|---|---|---|
| UNET | `minimax_h3_{fl2va,ref2va}_pruned_w4a8_mixed` | 同左（r2v だけ fl2va + 参照 LoRA。下記） |
| CLIP | `qwen3vl_32b_heretic_minimax_h3_nvfp4` | 同左 |
| 動画 VAE | `minimax_h3_video_vae_fp16` | `minimax_h3_video_vae_int8_convrot` |
| 蒸留 LoRA | なし | `minimax_h3_fl2v_turbo_4step_v1.1_768p_comfyui_bf16` |
| `BasicScheduler.steps` | 20 | **4** |
| `KSamplerSelect.sampler_name` | `res_multistep` | 同左（r2v turbo だけ `euler`） |

素の版も**量子化ウェイトと heretic の text encoder**を使うので、素の版と turbo の
モデルファイルの差は**動画 VAE と蒸留 LoRA だけ**（`opt` は turbo から蒸留 LoRA を抜いて
steps を 20 に戻したもの）。

UNETLoader と BasicGuider の間には、高速化ノードが**テンプレートに直接**直列で入っている:

```
UNETLoader
 → MiniMaxH3TurboLoRA      (minimax_h3_fl2v_turbo_4step_v1.1_768p_comfyui_bf16.safetensors, strength 1)
 → PathchSageAttentionKJ   (sage_attention=auto)
 → MiniMaxH3MemoryEfficientSageAttentionPatch     (入力は model のみ)
 → SolAttnPatch            (tau 1.5 / 0.2〜0.9)   ──→ BasicScheduler.model
 → MiniMaxH3SigmaShift     (video 12 / audio 3)
 → SpectrumApplyMiniMaxH3  (blend_weight 0.75)    ──→ BasicGuider.model
```

`BasicScheduler` は sigmas を作るだけなので **SigmaShift の手前**（`SolAttnPatch` の出力）から
model を取る。guider だけが末尾の `SpectrumApplyMiniMaxH3` を読む。これらは**任意のカスタム
ノード**なので、入れていない環境でヘルスチェックが赤くならないよう、turbo を選ばないかぎり
グラフには現れない。

ワークフローの宣言は `dataclasses.replace` で素の版との差分だけを書く（`workflows.py` の
`MINIMAX_H3_T2V_TURBO` / `MINIMAX_H3_I2V_TURBO` / `MINIMAX_H3_R2V_TURBO`、およびそれらを
さらに `replace` した `…_SAVE_TURBO` / `MINIMAX_H3_R2V_CONTEXT_TURBO`）。family は素の版と同じ
`minimax-h3` なので、2 段プルダウン（モデル → モード）の 2 段目に「… (i2v Turbo)」
「… (r2v Turbo)」として並ぶ。
turbo 版だけは選択式フィールド（下記）で `low_vram`（`MiniMaxH3TurboLoRA` の低 VRAM 読み込み）を
出す。**既定は `off`** で、VRAM が足りずに落ちるときだけ `on` にする。

**r2v の opt / turbo だけは土台が違う**（`_save` / `_context` 版も同じ）。UNET は ref2va では
なく `minimax_h3_fl2va_pruned_w4a8_mixed` で、そこへ参照 LoRA
`minimax_h3_ref_lora_rank_256_bf16` を `LoraLoaderModelOnly`（ノード 144・strength 1.0）で
重ねてから `PathchSageAttentionKJ` 以降の連鎖に流す。turbo はさらに 4step 蒸留 LoRA
`minimax_h3_fl2v_turbo_4step_v1.1_768p_comfyui_bf16` を `LoraLoaderModelOnly`（ノード 143）で
UNET 直後に挟み（`UNETLoader → 143 → 144 → PathchSageAttentionKJ → …`）、
`BasicScheduler.steps` を **4**・`KSamplerSelect.sampler_name` を **`euler`** にする。
つまり **turbo と opt のテンプレートの差はノード 143 と steps / sampler だけ**。
`MiniMaxH3TurboLoRA` を使わないので r2v の turbo は **`low_vram` を持たない**。

#### ラテントアップスケール（`latent_upscale`）

**MiniMax H3 の動画ワークフロー全部**（t2v / i2v / r2v × base / opt / turbo、`_save` / `_context`
版を含む）が選択式フィールド `latent_upscale`（`on` / `off`、**既定 `on`**）を持つ。
テンプレートはどれも 1 パスのままで、`on` のときだけ**ジョブの組み立てがグラフを 2 パスに
組み替える**（`workflows.UpscaleSpec` の宣言を `workflow.splice_latent_upscale` が読む）。

- `off`: テンプレートそのまま。指定解像度（`aspect_ratio` + `megapixels`）で 1 パス。
- `on`:
  1. `width` / `height` の注入先には **0.2MP** で計算した値を書く（縦横比の決め方は従来どおり。
     開始フレームがあればその比、無ければプリセット。32 の倍数に丸める）。
  2. 1 パス目の `SamplerCustomAdvanced` の **denoised_output（出力 1）**を
     `LTXVSeparateAVLatent` で映像／音声に分ける。
  3. 映像側だけ `MinimaxH3LatentUpscaler3D` に通す。`model_name` は
     `minimax_h3_latent_upscaler_3d_bf16.safetensors`（`latent_upscale_models`）、
     `mode` は **`target dimensions`**（`megapixels` モードではない）で、DynamicCombo の
     入力名どおり `mode.width` / `mode.height` に**最終解像度**を書く。`align` 32・
     `device` `cuda`・`precision` `bf16`。
  4. `LTXVConcatAVLatent` で音声と戻し、`ManualSigmas`
     （**`0.9035, 0.6316, 0.3158, 0.0000` 固定・全バリアント共通**）で 2 パス目の
     `SamplerCustomAdvanced` を回す。`noise` / `guider` / `sampler` は 1 パス目と同じものを共有する。
  5. 1 パス目を `samples` で読んでいた `VAEDecode` / `VAEDecodeAudio` だけを 2 パス目に
     付け替える。**テンプレートの `MiniMaxH3MotionContextSaveLatent` と 1 個目の Motion Context は
     入力名が `latent` なので 1 パス目に付いたまま**。
     `MiniMaxH3MotionContextTrim` はデコード後（＝2 パス目のデコード後）に掛かる既存の配線のまま。
  6. **2 段引き継ぎ**（ラテント連続性のバリアント、下記）。

##### 2 段引き継ぎ（ラテント連続性 × `latent_upscale` = `on`）

継ぎ目を**最終解像度でも**合わせるため、ラテントを保存する版（`*_save*` / `*_context*`）で
`on` のときは、**1 パス目（0.2MP）と 2 パス目（最終解像度）のラテントを両方**保存して次のカットに渡す。

- **保存側**（`*_save*` / `*_context*` 共通）: テンプレートの `…SaveLatent`（1 パス目、保存先
  `h3_context/{job_id}`）に加えて、**2 個目の `…SaveLatent`**（2 パス目の
  `SamplerCustomAdvanced` の出力 0、保存先 `h3_context/{job_id}_hires`）と、そのパスを持ち帰る
  **2 個目の `PreviewAny`** を足す。ジョブは 2 本目のパスも `/history` から回収し、Take の
  `latent_hires_path` に控える（`off` のジョブでは NULL のまま）。
- **読み込み側**（`*_context*` のみ）: 直前カットに 2 本目があれば、
  **2 個目の `…LoadLatent`**（`latent_path` = 前カットの `latent_hires_path`）と
  **2 個目の `MiniMaxH3MotionContext`**（`conditioning` / `vae` / `context_frames` / つまみは
  1 個目と同じ、`latent` = `LTXVConcatAVLatent` の出力（＝2 パス目の解像度・フレーム数の形状参照）、
  `context_latent` = 2 個目の `…LoadLatent`）と、**2 個目の `BasicGuider`**（`model` は 1 個目と
  同じモデル連鎖、`conditioning` は 2 個目の Motion Context）を足し、**2 パス目の
  `SamplerCustomAdvanced` の `guider` をこちらへ付け替える**（1 パス目の guider 共有をやめる）。
  `MiniMaxH3MotionContextTrim` の `trim_frames` は 1 個目の Motion Context のまま（値は同じ）。
- **フォールバック**: 直前カットに 2 本目が無い（`off` で作った過去テイクなど）ときは、
  読み込み側の 3 ノードを**足さない** = 2 パス目は 1 パス目と同じ guider を共有する
  従来の 1 段引き継ぎになる（エラーにはしない）。保存側の 2 本目は付くので、次のカットからは
  2 段になる。
- **`off` のとき**は完全に従来どおり（1 本保存・1 段引き継ぎ）。
- **制約**: `MiniMaxH3MotionContext` の `context_latent` は生成するクリップと同じ解像度である
  必要があるので、**連鎖の途中で解像度（`megapixels` / `aspect_ratio`）や `latent_upscale` の
  on / off を変えると、次のカットは解像度不一致で ComfyUI 側が止まる**。変えるなら連鎖の切れ目で。

足すノードの id は `UpscaleSpec` が持つ（2 パスぶんが `900`〜`904`、2 段引き継ぎぶんが
`905`（hires SaveLatent）/ `906`（hires PreviewAny）/ `907`（hires LoadLatent）/
`908`（hires MotionContext）/ `909`（hires BasicGuider）。テンプレートの最大 166 とも、
素の t2v / i2v のサブグラフ由来の `105:xx` とも衝突しない）。1 パス目のサンプラーの id も
`UpscaleSpec.sampler` で宣言する（素の t2v / i2v は `105:14`、それ以外は `125`）。
組み替えは**モデル指定の差し替え（`apply_model_overrides`）より前**に行うので、
アップスケーラの `model_name` も設定ページのスロットとして出るし（`workflow.model_fields` が
テンプレートに `UpscaleSpec` 由来のノードを重ねて見る）、ジョブごとの `model_overrides` も効く。

`MinimaxH3LatentUpscaler3D`（カスタムノード Comfyui_Minimax_h3_latent_Upscaler）は
**テンプレートに現れない**ので、テンプレート由来の接続先判定
（`uses_optional_class_types` / `supported_on_target`）では拾えない。代わりに選択式そのものが
`SelectSpec.requires_class_types` / `restricted_choice` を宣言し、
`SelectSpec.choices_for_target(comfy_target)` が **`comfy_cloud` では選択肢を `off` だけに絞る**
（`GET /api/options` の `selects` が接続先に合わせて絞られる）。値を送ってこない
経路（外部 API・再実行）のために、ジョブ投入時に `jobs._pin_target_selects` が
既定を接続先に合わせて params へ固定し、使えない値が明示されていれば 422 で弾く。

さらに turbo から**蒸留 LoRA だけを抜いた** **opt**（`minimax_h3_t2v_opt` / `minimax_h3_i2v_opt` /
`minimax_h3_r2v_opt` と、その `_save` / `_context` 版）がある。
`MiniMaxH3TurboLoRA` を持たず（`PathchSageAttentionKJ` が UNETLoader 直結）、
`BasicScheduler.steps` は素の版と同じ **20**。量子化ウェイトとアテンション系パッチはそのままなので、
品質は素の版相当のまま実行だけが速い。書き込む先のノードが無いので **`low_vram` は持たない**。
ドラマスタジオからはプロジェクトの「動画生成品質」（`quality`）として選ぶ（§2.2）。

**接続先が `comfy_cloud` のときは turbo / opt（と MiniMax H3 Image の全バリアント）を
選択肢に出さない**。Comfy Cloud には任意のカスタムノードを入れられないためで、判定は id の
ハードコードではなく「テンプレートが `workflows.OPTIONAL_CLASS_TYPES`（turbo / opt と
MiniMax H3 Image だけが使う任意のカスタムノード）を 1 つでも使うか」
（`workflow.uses_optional_class_types` / `workflow.supported_on_target`。spec ごとにプロセス内
キャッシュ）。`GET /api/options` の `image_workflows` / `video_workflows` / `audio_workflows` が
同じ規則で絞られ、フォームの 2 段プルダウンも外部 API の `GET /api/v1/options` もそれに従う。
`local` / `runpod` は自前の ComfyUI なので従来どおり全件出す（入っていなければ実行時に ComfyUI 側で
エラーになる）。保存済みの選択が消えた場合、フロントは `default_video_workflow`（または先頭）へ
自動的に戻す（`App.tsx` の `loadOptions`）。

- ジョブは `selects: {"<論理名>": "<選んだ値>"}` で値を持ち（宣言外の名前・選択肢外の値は 422。
  検証は `models.select_problem` で Web UI・内部 API・外部 API に共通）、
- `GET /api/options`（＝外部 API の `GET /api/v1/options`）にも選択肢がそのまま載る。

宣言していないワークフローでは何も増えないので、既存の挙動は変わらない。**動画・音声だけの
仕組みではなく、画像ワークフローも宣言できる**。ジョブの
`selects` はステージをまたいで 1 つの辞書なので、`models.select_problem` は**そのモードで走る
ステージの宣言をすべて**見て検証し、フォームも走るステージのぶんだけ送る（`form.jobSelects`）。
注入時の要点:

- ComfyUI の `CustomCombo` は選んだ文字列（`choice`）と 0 始まりの番号（`index`）を持ち、
  **グラフが読むのは番号側**（`choice` は表示用。番号で「n 行目」を引く RegexExtract に繋がる）。
  そのため両方を書き込む。`validate_specs()` は選択肢がテンプレートの `option*` と一致するかも見る
- `numeric_target` があれば同じ値を数値としても入れる（尺のように、コンボと音声のトリム長の
  両方に入れないと映像だけ伸びてしまう項目のため）
- 書き込み先が **BOOLEAN の widget**（`workflow._BOOL_INPUTS`）なら、選んだ文字列を
  `on` → `true` / それ以外 → `false` に直してから入れる。ComfyUI は BOOLEAN に文字列を入れると
  型検証で prompt ごと落ちるため。MiniMax H3 turbo の `low_vram`（`MiniMaxH3TurboLoRA.low_vram`、
  4step 蒸留 LoRA を低 VRAM モードで読むか）と MiniMax H3 Image の `optimize_for_still`
  （静止画向けのプロンプト包み・既定 `on`）がこの形
- 書き込み先が **FLOAT の widget** で選択肢が数字の文字列のとき（`workflow._FLOAT_SELECT_INPUTS`）は
  `float()` に直してから入れる（BOOLEAN と同じ理由）。MiniMax H3 Image の `source_fidelity`
  （`H3ImageToImagePrepare` / `H3ReferenceEditPrepare`）がこの形で、ノードの 0.00〜1.00・刻み 0.05 の
  うち実用的な段階（0.00 / 0.25 / 0.50 / 0.75 / 0.90 / 1.00）だけを選択肢にしてある
- `auto: "audio_duration"` の項目は、**未指定なら入力音声の実長**（`jobs.probe_media_duration`、
  ffprobe）を選択肢に切り上げて決める（上限は最大の選択肢）。決めた値は params に残るので
  再実行でも同じ尺になる。ffprobe が無い・読めない場合は宣言した既定値に落ちる（登録は止めない）
- UI は `auto` の項目に「自動（入力に合わせる）」、それ以外に「既定（<値>）」を先頭の選択肢として置く
- **選択肢の表示ラベル**（`SelectSpec.choice_labels`、`選ぶ値 -> 画面に出す文字列`）:
  `decode_recommended` のようなノード由来の enum を読める日本語に置き換えるための飾り。**送る値も
  ComfyUI に入る値も `choices` の生のまま**で、`GET /api/options` の `choice_labels` を
  `WorkflowSelects` が `<option>` の文字にだけ使う（宣言の無い値・宣言そのものが無い選択式は生の値に
  フォールバックするので、既存の選択式は何も変わらない）。**API に送る値は常に生のほう**で、
  ラベルは画面の表示だけに使う。`choices` に無いキーを書くと黙って効かないので
  `validate_specs()` が弾く
- **選択式どうしの相関**（`WorkflowSpec.select_requires`、名前 → `(相手の名前, 相手に必要な値)`）:
  「その項目は相手がこの値のときしか効かない」ことの宣言。相手の値によっては**モデルが黙って無視する**
  項目のために、`{"duration": ("model", "V5_5")}` のように宣言して**既定以外を明示指定したジョブだけ**を
  422 で断る（`models.select_requires_problem`。
  既定のままなら無視されても困らないので何も言わない）。検証は Web UI（`form.selectRequiresErrors`）と
  API の 2 経路で同じ理由になり、フォームはその選択式の直下にエラーを出す

#### 解像度の計算

画像側は ResolutionSelector を持つテンプレート（krea2 の `49` / anima の `91`）にアスペクト比と
メガピクセルをそのまま渡す。ResolutionSelector を持たない z-image は、下の式で計算した幅・高さを
`EmptySD3LatentImage` に直接注入する。qwen-image-edit は入力画像から解像度が決まる
（`FluxKontextImageScale`）ので、どちらも注入しない。
動画側の新テンプレートは幅・高さの `PrimitiveInt` 指定になったため、アプリが同じ式で計算する
（開始フレームがあればその実比に従う）
（ComfyUI `comfy_extras/nodes_resolution.py` と一致。各辺を 8 の倍数に丸め）:

```
scale  = sqrt(megapixels * 1024 * 1024 / (w_ratio * h_ratio))
width  = round(w_ratio * scale / 8) * 8
height = round(h_ratio * scale / 8) * 8
```

##### モデルごとの既定メガピクセル（`WorkflowSpec.default_megapixels`）

フォームのグローバル既定は 1.0MP（`form.ts` の `DEFAULT_MEGAPIXELS`）だが、モデルによっては
テンプレートの `ResolutionSelector` がもっと小さい画角を前提にしていて、1.0MP のまま回すと
VRAM が足りずに CUDA OOM で落ちる。そこで `WorkflowSpec.default_megapixels`（0.0 = 宣言なし）を
宣言でき、値は `GET /api/options` の `video_workflows[].default_megapixels` に出る。
宣言を持つのは **MiniMax H3 の動画 7 つ（t2v / i2v / i2v turbo / i2v opt / r2v / r2v turbo / r2v opt）
= 0.4MP**（短辺 768px・最大 768x1344 の画角）と、**MiniMax H3 Image の 9 つ（t2i / i2i / r2i ×
base / opt / turbo）= 0.98MP**（native canvas 1344x768。こちらは逆に、グローバル既定の 0.4MP の
ままだとネイティブの 4 割の解像度で生成してしまう）。宣言は動画・画像の両方の
`GET /api/options` に出る（`video_workflows[].default_megapixels` /
`image_workflows[].default_megapixels`）。

バックエンド側（`POST /api/jobs` の `megapixels` を省いたとき、および Studio の
カットもプロジェクトも `megapixels` 未設定のとき）の既定は
**0.4MP**（`workflows.py` の `DEFAULT_MEGAPIXELS`）。既定の動画ワークフローが MiniMax H3 で、1.0MP のままだと
8GB 級のローカル GPU で CUDA OOM になるため、フォームが明示的に値を送らない
経路でも安全側に倒している。

フォーム側は**そのモードで実際に走るステージ**のワークフローを切り替えたタイミングに
`megapixelsFor(...)` の値を入れる（宣言が無ければグローバル既定へ戻す）: 画像ステージ
（`full` / `image_only`）は `image_workflow`、動画ステージ（`full` / `i2v`）は `video_workflow`。
**両方走る `full` では 1 つの値を 2 段が共有するので、きつい側（小さいほう）に合わせる**
（画像 0.98MP + 動画 0.4MP なら 0.4MP。動画側で CUDA OOM にしないほうを取る）。
切り替えたあとに手で変えた値はそのまま残り、次に切り替えるまで維持される（音声の
`clampToWorkflow` と同じ「切り替え時にだけ追随させる」形）。

`megapixels` を**そもそも送ってこない経路**（外部 API・古いジョブの再実行）では
グローバル既定のまま届くので、画像ステージのビルダーが `app.workflow.image_megapixels` で
「グローバル既定そのまま = 明示していない」とみなして宣言の値に読み替える。**明示された値は
そのまま尊重する**（0.4MP を意図して選んだジョブを勝手に上げない）ので、宣言を持たない
ワークフロー（krea2 など）の挙動は変わらない。

ドラマスタジオは生成フォームを通さないので、`megapixels` と `aspect_ratio` はカット投入時に
`app.studio.render_shot` が組み立てる: **テイク 1 回ぶんの上書き（render のボディ）
→ Shot 個別 → プロジェクト（どちらも `NULL` = 載せない）
→ `JobCreate` の既定（0.4MP / `4:3 (Standard)`）**。
同じ関数が `duration`（**上書き → Shot の `duration_seconds`**）・`steps`（**上書き →
プロジェクトの `steps` → 載せない = テンプレートの既定**）・`seed`（**上書き → Shot の
`seed` → 載せない = 毎回ランダム**）も同じ流儀で解決する。
`app.jobs._fitted_megapixels` の切り下げ（宣言の `default_megapixels` を上限にする）が掛かるのは
**別のワークフローから params を引き継ぐとき（再実行での付け替え・「続きから」）だけ**なので、
0.4MP より大きい値もそのまま ComfyUI に届く（Shot 個別の指定が従来から効いているのと同じ経路）。

参照画像（開始フレーム）を取るワークフロー（`accepts_start_image=True`）で `source_image` が
指定されている場合は、`w_ratio:h_ratio` にプリセットではなく **参照画像の実寸比** を使う
（メガピクセルの総画素数と 8 の倍数丸めはそのまま）。比が合わないとテンプレート内の
`ResizeImageMaskNode`（crop=center）でセンタークロップされ画が切れるため。画像の寸法が
読めなかった場合はプリセットにフォールバックする。`full` モードでは 1 段目の生成画像を
2 段目に渡す時点で `start_image_size` を捨てるので、2 段目はプリセットで計算する
（生成画像はプリセット通りの比で出るため。ただし解像度が入力画像依存の
`qwen_image_edit_2511` を 1 段目に選んだ場合だけは、両者がずれることがある）。

#### フレーム数

各テンプレートの `ComfyMathExpression`（`a * b + 1` もしくは `a * b`）を、アプリが計算した
定数に固定する（格子はワークフローごと: `WorkflowSpec.frames` / `FrameGrid`。宣言が無ければ
`8n + 1` を切り下げ、MiniMax H3 は 24fps の `17k + 5` を切り上げ）。式は
`a * 0 + b * 0 + <frames>` に書き換え、入力リンクは温存するのでグラフ形状と出力型は変わらない。
`frames_expr` を宣言しないワークフローでは固定を行わない。

### 3.2 アプリが自動注入する項目

| 項目 | 論理名 | 方針 |
|---|---|---|
| 画像 / 動画 / 音声プロンプト | `prompt` | フォームの確定値（手動 or Grok チャット反映後） |
| 画像 seed | `seed`（krea2 は `30:3` の `KSampler.seed`。テンプレートごとに異なる） | 実行毎にランダム（固定オプションあり）。`params` に保存して再現可能 |
| 動画 noise seed | `seeds`（低解像度パス + アップスケールパスの `RandomNoise`、IC-LoRA 系は `KSampler.seed`） | 同上。seed が 1 個しか渡らない場合は全サンプラーで共用 |
| 音声 seed | `seed`（MiniMax Music 3 は `37:38` の `SeedNode`、Stable Audio は `KSampler.seed`） | 同上（`params` には `audio_seed` として保存） |
| 音声の長さ | `duration` / `latent_seconds` | どちらのワークフローも空ラテントがテキストエンコード側の出力（MiniMax Music 3）や同じ `PrimitiveFloat`（Stable Audio）を読むので注入は 1 か所。2 か所に入れるテンプレート用に `latent_seconds` の口は残してある |
| 出力プレフィックス | `save_prefix` | 画像 `images/{job_id}` / 動画 `video/{job_id}` / 音声 `audio/{job_id}` にして成果物とジョブを紐付け |
| ローカル LLM リファイン | `refine_enable` → `30:24`（krea2 のみ） | **false 固定**（プロンプト整形は Grok が担う）。`ComfySwitchNode` は遅延評価（`check_lazy_status`）なので `30:16` (TextGenerate) は実行されない |
| プロンプト拡張 | `prompt_enhance` → 各テンプレートの `Boolean (Enable Prompt Enhance)` | **false 固定**（同上）。IC-LoRA 系は false なのでスイッチのリテラル側 `on_false` にプロンプトを注入する |

Stable Audio の `reprompt`（内蔵 LLM でのプロンプト展開）だけは例外で、**ユーザーが選ぶ**
チェックボックスとしてフォーム / ジョブのフィールドになっている（既定 false）。

### 3.3 固定（触らない）ノード

- 画像側: 各ファミリーの UNET / CLIP / VAE（krea2 = `krea2_turbo_fp8_scaled` + `qwen3vl_4b_fp8_scaled` + `qwen_image_vae`、anima = `anima-base-v1.0`、z-image = `z_image_turbo_bf16`、qwen-image = `qwen_image_edit_2511_int8_convrot` + Lightning 4steps LoRA）と KSampler 設定
- 音声側: MiniMax Music 3 `minimax_music3_dit_fp16` + `minimax_music3_text_encoder_pruned_int8_convrot` + `minimax_music3_dav`、Stable Audio `stable_audio_3_medium_base` + `t5gemma_b_b_ul2` / `qwen3.5_2b_bf16`、およびサンプラー設定
- 動画側: MiniMax H3 の UNET / CLIP / 映像 VAE / 音声 VAE（`minimax_h3_*` 系。素の版から w4a8 量子化ウェイトで、opt / turbo はさらに int8_convrot の映像 VAE + Sage Attention / Sol-Attn / SigmaShift / Spectrum、turbo は 4step 蒸留 LoRA も）とサンプラー設定
- **モデルファイル名は利用者の ComfyUI 環境依存**のため、設定ページ（`GET/PUT /api/models`）で上書き可能。既定値は各テンプレートの値。対象は UNETLoader.unet_name / CLIPLoader.clip_name / CLIPVisionLoader.clip_name / VAELoader.vae_name / CheckpointLoaderSimple.ckpt_name / LatentUpscaleModelLoader.model_name / LoadMoGeModel.model_name / LoraLoaderModelOnly.lora_name / LoraLoader.lora_name / MiniMaxH3TurboLoRA.lora_name（§3.4 で削除される画像テンプレートのプレースホルダは除く。テンプレートが持つ固定 LoRA ノード（qwen-image の Lightning LoRA、MiniMax H3 turbo の 4step 蒸留 LoRA）はユーザー LoRA と共存するので上書き対象のまま）
- **モデルの指定は接続先ごと**（SPEC §5）: `Settings.model_overrides` / `model_choices` は `{"<comfy_target>": {"<スロットキー>": …}}` の 2 段で持つ。どのファイルが在るかは ComfyUI の環境ごとに違うため。`GET/PUT /api/models` は `?target=`（PUT はボディの `target`）で対象環境を選び、省略すると現在の接続先。**書き込みは選んだ環境だけ**で他の環境の指定は残る。ジョブ実行・`/api/options` の `model_slots`・投入時の検証はすべて「現在の接続先」の値（`Settings.overrides_for()` / `choices_for()`）を使う。接続先を分ける前の設定（1 組だけ）は読み込み時に**3 環境すべてへ複製**される（`config._per_target`）: 分けた瞬間に指定が消えて既定モデルで走り出すのを避けるため
- 上書きキーは**ワークフロー ID でスコープ**する: `"<workflow_id>/<node_id>.<field>": "<ファイル名>"`。テンプレート間で同じノード ID（例: `340:317` が ia2v と id_lora の両方にある）が衝突しないため。旧レイアウトの非スコープキーは無視される（マイグレーション不要）
- **実行ごとのモデル切り替え**: 同じキー形式で「そのスロットで選べるファイル名」を設定に持てる（`Settings.model_choices`、`GET/PUT /api/models` で読み書き）。既定値（`model_overrides` → 無ければテンプレート値）と合わせて **2 件以上**になったスロットは *switchable* とみなし、`GET /api/options` の `model_slots`（キー・ラベル・既定値・候補一覧）に出す。ジョブは `model_overrides`（`JobCreate` / `JobContinue` のフィールド）で 1 回ぶんだけ差し替えられ、実行時に設定の既定値の上へマージされる（`jobs.run_job`）。検証（`models.model_override_problem`、Web UI と API で共通）は「キーが `model_fields()` に存在」「そのジョブが走らせるワークフロー（`models.job_workflow_ids`）に属する」「値が候補（既定値を含む）に入っている」を満たさないものを 422 で拒否する。再実行は params ごと引き継ぎ、続き生成は動画ワークフローぶんのキーだけを引き継ぐ（`workflow.scoped_model_overrides`）
- **不足モデルの自動ダウンロード**: ComfyUI 本体にも Comfy Cloud にもモデル取得 API は無いので、**落とし先の環境に合わせて**取ってくる（`POST /api/models/download` の `target`、省略時は現在の接続先）。設定ページは「その行の値が `GET /api/options` の `model_files` の該当リストに無い」ことを不足の判定に使い、**未検出**バッジ・URL 入力欄・[DL] ボタンを出す（`model_files` が空＝ComfyUI 未接続のときは判定しない）
  - `local` … バックエンドが自分でダウンロードして ComfyUI の models ディレクトリ（**環境変数 `COMFY_MODELS_DIR`**）へ直接置く（ComfyUI はフォルダの mtime を見て一覧を作り直すので再起動は不要）
  - `runpod` … Pod の中で動く小さな API（`deploy/runpod/model_api.py`、`127.0.0.1:8190`。caddy が ComfyUI と同じ認証で `/studio/models/*` だけを通す）に `POST /download` で依頼し、`GET /downloads` を 2 秒ごとにポーリングして**ローカルと同じ WS フレーム**に変換して流す。アプリを再起動しても Pod 側は走り続けるので、`GET /api/models/downloads?target=runpod` は Pod の一覧を取り込んで見張りを再開する。Pod が古いイメージ（この API を持たない）なら 404 を「イメージを作り直してください」という 400 にして返す
  - `comfy_cloud` … ファイルシステムに触れないので 400（モデルは Comfy Cloud 側の管理）
  - **一括ダウンロード**（[全DL]、`POST /api/models/download-all`）: 選んだ環境の `/object_info` と比べて未検出、かつ `model_download_urls` に URL があるものをまとめて開始する。対象はワークフローの各スロットの実効値・候補リストと、その環境の LoRA 登録。URL が無いものは `missing_urls` として返して UI が知らせる。ComfyUI に繋がらないときは 400（何が足りないか判定できないため）
  - 置き場所は `class_type`＋入力フィールドから決める（`workflow.MODEL_SUBFOLDERS` → `ModelField.subfolder`）: checkpoints = CheckpointLoaderSimple.ckpt_name、diffusion_models = UNETLoader.unet_name、text_encoders = CLIPLoader.clip_name / DualCLIPLoader.clip_name1・clip_name2、clip_vision = CLIPVisionLoader.clip_name、vae = VAELoader.vae_name、loras = LoraLoader.lora_name / LoraLoaderModelOnly.lora_name / MiniMaxH3TurboLoRA.lora_name、latent_upscale_models = LatentUpscaleModelLoader.model_name、geometry_estimation = LoadMoGeModel.model_name。未知のローダーは空（＝ UI で入力させる。当てずっぽうに置いても ComfyUI からは見えない）
  - `POST /api/models/download` は保存先を検証（`..` / 絶対パス / パス区切りを拒否し、`resolve()` 後に models ディレクトリ配下であることを確認）してからバックグラウンドタスクを起こす。httpx のストリームをチャンクで `<ファイル名>.part` に書き、完走したときだけ本来の名前に `rename` する（失敗・中断時は `.part` を削除）。進捗は WS `/api/ws` に `type: "model_download"` として流れる。同じファイル名の同時ダウンロードは 409
  - 認証は URL のホストで出し分ける: huggingface.co / hf.co（サブドメイン含む）は `Settings.hf_token`、civitai.com は `Settings.civitai_api_key` を `Authorization: Bearer …` として付ける（未設定なら付けない）。**リダイレクトは httpx に任せず自分で追う**（最大 10 ホップ、相対 `Location` は urljoin で解決、301/302/303/307/308 を GET のまま追う）: クライアント既定ヘッダに認証を載せると転送先の別ホストにトークンが漏れるため、ホップごとに URL を再検証して認証ヘッダを計算し直し、そのリクエストにだけ渡す（HF → `*.hf.co` の CDN には付き、無関係なホストには付かない）。URL はファイル名ごとに `Settings.model_download_urls` へ保存する（同じファイルが複数スロットに出るため、キーはスロットではなくファイル名）
  - 保存先は**環境変数 `COMFY_MODELS_DIR` だけ**が決める（設定 `runtime/config.json` には持たない）。UI からパスを入れられても、Docker で同じ絶対パスをマウントしていなければ書けないため。`.env` に書けば `run.sh`（ホスト実行、`.env` を読んで `export`）と `docker compose`（同一パスのマウント＋`environment:` で受け渡し）の双方に効く。設定に残すのは `hf_token` / `civitai_api_key` / `model_download_urls` だけで、旧バージョンが書いた `comfy_models_dir` キーは読み込み時に捨てる
  - **取得元ページ**: 登録した URL はダウンロード用の直リンクなので、そのままでは配布元の使い方を調べられない。`app/model_sources.py` が配布ページ URL に変換する。Hugging Face は `…/resolve|blob|raw|tree/<rev>/<path>` から `https://huggingface.co/<org>/<repo>` を切り出すだけ（`datasets/` 名前空間も対応。`cdn-lfs.hf.co` 等の CDN 直リンクはリポジトリ名が読めないので変換しない）。Civitai の `…/api/download/models/<versionId>` は modelId を含まないので `https://civitai.com/api/v1/model-versions/<versionId>` を 1 回だけ叩いて引き、`https://civitai.com/models/<modelId>?modelVersionId=<versionId>` を組み立てる（認証は `model_download.auth_headers` と共通）。結果は `Settings.model_page_urls`（ダウンロード URL → ページ URL）にキャッシュするので 2 回目以降は API を叩かない。失敗しても例外は投げず、ページ URL 無し（＝ダウンロード URL だけ）として扱う。`model_page_urls` は自動生成のキャッシュなので `SettingsUpdate` には無く、設定ページからは触らない
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
`video` なら動画ワークフローに注入される。ジョブは両者を別フィールドで持つ
（`loras` / `trigger_text` と `video_loras` / `video_trigger_text`）。
音声ワークフローは LoRA チェーンを持たないので、`mode: "audio"` に LoRA を指定すると 422 になる。

**モデルファミリー**: 画像 LoRA はさらに登録時に学習元のファミリー（`krea2` / `anima` /
`z-image` / `qwen-image`）を選ぶ。別ファミリーの LoRA はロードできても破綻した出力になるため、
`loras` に選択中の `image_workflow` と違うファミリーが混ざったジョブは 422 で拒否する
（`models.image_lora_family_problem`）。フォームの LoRA ピッカーも同じファミリーのものだけを出し、
`GET /api/options` の LoRA 一覧にもファミリーが入る。
動画 LoRA はファミリーを使わない。

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

動画テンプレートが動作に必須の固定 LoRA を持つ場合、ユーザー LoRA は**その後段**へ
同じ仕組みで直列挿入する。`LoraChain` はプレースホルダを持たず、「`head` の MODEL 出力を
読んでいた入力（`consumers`）をチェーン末尾に付け替える」という 1 本の辺の切り開きとして
表現する。

**現在、`lora_chain` を宣言する動画ワークフローは無い**（MiniMax H3 の 5 種はどれも
ユーザー LoRA を挿せる場所を持たない）ので、`video_loras` を指定したジョブはすべて 422 で
拒否される（`models.video_lora_problem`）。フォームも欄ごと出さない
（`/api/options` の `accepts_video_loras` が false）。仕組み自体は画像側と共通なので、
チェーンを持つ動画モデルを足せばそのまま効く。

- ノード ID は `app_video_lora_0`, `app_video_lora_1`, … と採番する
- 0 件選択時は consumers が `head` を直接指す（テンプレートと同一のグラフ）
- MODEL 出力を使わないテキストエンコーダ側の `LoraLoader` などは付け替えない
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

#### 使う CLI を選ぶ（`agent_cli`）

LLM を回す CLI は設定 `agent_cli`（既定 `grok`）で選ぶ。**プロンプト作成チャット（§4.3）・スタジオの英訳・ライブラリの自動タグ・ヘルスチェックのすべてが同じ選択に従う**（アプリ内で LLM を呼ぶのはこの 4 つだけで、制作そのものを回すのは外部エージェント＝ SKILL 経由の `/api/v1`）。
**例外: Grok Imagine（画像生成・編集、§5.2 の `app.grok_media`）は常に Grok CLI**（内蔵ツールに乗っているため、`grok_command` を直接見る）。

CLI ごとの違いは `backend/app/llm_cli.py` の `CliAdapter` にまとまっている（起動 argv・契約の渡し方・認証エラーの文字列・`--version`）。

| `agent_cli` | ACP 起動（既定コマンド） | ワンショット | 契約（rules）の渡し方 |
| --- | --- | --- | --- |
| `grok` | `grok agent [-m モデル] stdio` | `grok [--model M] [追加フラグ] [--output-format json] -p <本文> [--resume <id>]` | `session/new` の `_meta.rules` |
| `claude` | `claude-agent-acp` | `claude [--model M] -p <本文>` | cwd の `CLAUDE.md`（**プロンプトにも埋める二重化**） |
| `codex` | `codex-acp` | `codex [-m M] exec <本文>` | cwd の `AGENTS.md` |
| `cursor` | `cursor-agent acp` | `cursor-agent [--model M] -p <本文>` | cwd の `AGENTS.md` |

- 契約をファイルで渡す CLI は、**ホストを開くたび**に作業ディレクトリへそのファイルを書き出す（毎回上書き。プロセス起動前に置く）。claude は Agent SDK の `settingSources` を省略すれば `CLAUDE.md` を読むが、**ACP ブリッジが何を渡すかはブリッジ側の実装次第**で外から強制する環境変数・フラグは公式ドキュメントに無いため、保険として初回プロンプトにも同じ契約を埋める（`GrokSessionHost.wants_contract()`）
- 続き（`--resume`）と JSON 包装（`--output-format json`）を使うのは grok だけ。ほかの CLI のワンショットは毎ターン履歴を組み直して投げる（正本は DB なので結果は同じ）
- コマンド名は `agent_cli_commands`（`{cli: コマンド}`）で上書きできる。値には引数を書いてよく、**2 語以上なら既定の引数を足さずそのまま使う**（`"cursor-agent acp"` のように起動方法が変わっても設定だけで追随できる）。ワンショット側だけを変えたいときは `"<cli>_oneshot"` キー。モデルは `agent_cli_models`（grok は従来の `grok_model`。空なら CLI の既定に任せてフラグごと出さない）
- **cursor のモデル指定**は `grok-4.6[effort=xhigh,fast=false]` のような括弧付き表記が書ける。ワンショットは `--model` がそのまま解釈し、ACP（`cursor-agent acp` は `--model` を受け付けない）は `initialize` の `clientCapabilities._meta.parameterizedModelPicker` を申告したうえで `session/new` のあと `session/set_config_option`（`model` → `effort` → `fast` の順に 1 件ずつ）で渡す。つまり同じ 1 つの設定値が両経路に効く（素の `cursor-grok-4.6-xhigh` 形式はそのまま `model` に渡すのでワンショット向け）
- モデル設定が拒否されても（未知の `configId` / 値は `-32602`）警告ログを残して**ターンは続ける**（CLI の既定モデルで動く）
- **CLI を切り替えると**、保存済みの続き用セッション id（`chat_sessions.grok_session_id`）は別 CLI では通じないので `PUT /api/settings` で空にする。会話そのもの（正本）は残るので、次のターンは履歴を組み直した新しいセッションで続く
- ヘルスチェック（`GET /api/health`）は選択中の CLI の `--version` を見る。応答の `cli` / `cli_label` が選択中の CLI を表し、ヘッダーの接続状態もその名前で出る

### 4.2 プロンプト生成の仕様

プロンプト作成は**手動が基本**。Grok を使う場合はチャット形式（§4.3）で要件を掘り下げ、最終的に JSON（`image_prompt`, `video_prompt`, `notes`。`mode: "audio"` では `audio_prompt`, `lyrics`, `negative_tags`, `notes`）を出力させてフォームに反映する。システムプロンプトに各モデルのプロンプト仕様を埋め込む。

チャットのシステムプロンプトには**選択中のワークフローに対応する仕様だけ**を入れる
（画像はファミリー別、動画はワークフロー別、音声はモデル別）。外部エージェントには
同じ内容を `GET /api/v1/prompt-guide` / `prompt-examples` として配る（§9「外部公開 API」）。

**実例集**: 画像（Krea 2）の few-shot は Civitai 公開ギャラリー由来で `docs/prompt-samples.md` に残してある。動画の few-shot は公式 MiniMax H3 文書（`prompts.FEW_SHOT_H3`）で、古い Civitai 1 段落は埋め込まない。

- MiniMax H3 の台詞は `<d>[Language] …</d>`（話者 ID と言い方は `<d>` の外）。中身は原語のまま音声合成される
- 音は `overall_soundscape`（環境・物理・非言語）と `non_diegetic_music`（観客だけのスコア、無ければ `N/A`）に分ける
- MiniMax H3 に negative prompt は無い（本文末尾の除外文で字幕・ロゴを落とす）
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

**動画プロンプト（MiniMax H3 公式リライト契約。`prompts.MINIMAX_H3_*` + `FEW_SHOT_H3`）**

- Grok に渡すのは公式 H3 文書であって、4〜8 文の 1 段落ではない。自然文 / タグ形式の UI 切替は廃止（talkvid の `[VISUAL]` / `[SPEECH]` は埋め込まない）
- ベース（t2v / i2v）: 必要なときだけアライメント行のあと `integrated_multimodal_description:` / `overall_soundscape:` / `non_diegetic_music:`
- 参照（r2v）: 6 節（`subject_definitions` / `summary` / `retention_analysis` / `detailed_description` / 音 2 節）
- `[Shot 1]` にタイムスタンプは無く、以降は `[Shot N] At MM:SS.mmm, the camera cuts to …`。最後のショットはジョブの `duration` に収める
- カメラはショット内の公式運鏡語彙。台詞は `(S1) says: <d>[Language] …</d>`。`Camera:` / `Audio:` フッタや `[0s-1.5s] Shot 1:` は使わない
- ユーザーが書いていない人物・場所・衣装・台詞は発明せず、述べられた動作を身体・接触・視線・結果・カメラ・音まで展開する
- MiniMax H3 に negative prompt は無い（本文末尾の除外文）

**音声プロンプト（`mode: "audio"`）**

`prompts.MINIMAX_MUSIC_3_SPEC` / `STABLE_AUDIO_SPEC` を、選択中の音声ワークフローに
応じて埋め込む（出典は MiniMax Music 3 と Stable Audio 3 の公式ドキュメント、ComfyUI の各ノード実装）:

- **MiniMax Music 3**: `audio_prompt` は曲そのものの **Structured Caption**（`Global Metadata:` /
  `Vocal Details:` / `Arrangement:` の 3 見出し、250〜450 語）。テンポ・キー・声質・編成は
  すべてこの本文に書く。歌詞は `audio_prompt` ではなく `lyrics` に、`[Verse]` / `[Chorus]` の
  セクションタグ付きで書く。ユーザーが言っていない BPM・キー・ボーカルの性別は確定させない
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

- **会話履歴はアプリ側（`chat_sessions.messages`）が正本**。Grok へは `app/chat_agent.py` が **ACP の継続セッション**（`app/grok_session.py` の `GrokSessionHost`）で渡す: セッションを開くときにシステムプロンプトを**契約**（`session/new` の `_meta.rules`）として渡し、2 通目以降は**新しい発言だけ**を `session/prompt` で送る（履歴の再送はしない）。`session/load` による再開はせず、Grok 側のセッションが消えていたら（`GrokSessionGone`・アプリ再起動）DB の履歴を組み直して新しいセッションの初回プロンプトに詰める
  - 相談は**ツールを使わない**: `--permission-mode auto` は付けず、ACP の `session/request_permission` は拒否する
  - 作業ディレクトリはチャットごと（`runtime/chat-sessions/<session_id>/`、`paths.CHAT_SESSIONS_DIR`）。入力画像のコピー先でもあり、CLI の cwd と同じ場所。`chat_sessions.grok_session_id` / `grok_cwd` に控える
  - 実行中の活動（「思考中」「ツール実行中: …」）は WS `type: "chat"` で流し、`POST /api/chat/sessions/{id}/stop` でターンを止められる（ACP は `session/cancel`、ワンショットはプロセス中断）
  - 設定 `agent_use_acp` がオフ・ACP を起動できない環境では従来どおり `grok -p` のワンショット実行にフォールバックし、毎ターン「システムプロンプト + 履歴全文 + 最新発言」を組み立てて渡す
- システムプロンプトの構成: ①役割（プロンプトエンジニア兼インタビュアー）②各モデルのプロンプト仕様（§4.2。画像は選択中ワークフローのファミリーのものだけ。動画は公式 H3 契約 + `FEW_SHOT_H3`）③ヒアリング項目チェックリスト ④選択中の画像・動画ワークフローの特性（下記）⑤最終出力は ```json フェンス内の `{image_prompt, video_prompt, notes}` のみ、というルール
- `mode: "audio"` では専用のシステムプロンプト（`build_audio_system_prompt`）に切り替わる: 選択中の音声ワークフローの仕様とそのモデルが読むフィールドだけを提示し、出力は `{audio_prompt, lyrics, negative_tags, notes}`。画像・動画のプロンプトは書かせない。フォーム側も、選択中のワークフローが持たないつまみ（Stable Audio の `lyrics` など）は反映しない
- **ワークフロー特性の反映**: CONTEXT には選択中の `video_workflow` の用途・必要入力・音声の扱い・`video_prompt` の書き方と、`image_workflow` の用途・ファミリー・必要入力・`image_prompt` の書き方を出す。文面は `app/workflows.py` の `WorkflowSpec`（`description` / `audio_role` / `prompt_hint`）から自動生成する単一情報源なので、ワークフローを追加したらマニフェスト側に書けばチャットにも反映される（未記入は `validate_specs()` = ヘルスチェックで検出）。例: flf2v なら開始→終了フレーム間の遷移を書かせる、t2v / リファレンスシート IC-LoRA なら開始フレーム前提にしない、ia2v なら渡した音声がそのまま音声トラックになるのでセリフをプロンプトに書かせない、ic_lora_motion ならカメラ・テンポは参照動画由来なので書かせない
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

### 5.2 生成バックエンド: ComfyUI 以外の経路（`WorkflowSpec.backend`）

ワークフローは自分がどのエンジンで走るかを `backend` で宣言する。ディスパッチは
**ジョブ単位ではなくステージ単位**（`jobs._run_job_stages`）なので、`full` の 2 段が
別々のバックエンドでもよい（Grok Imagine で画像を作り、ローカルの ComfyUI で動画にする）。
成果物の置き場（`outputs/{job_id}/`）・jobs 行の列・WS の進捗は共通なので、履歴・
ライブラリ・UI からは区別が付かない。

| backend | 実行 | 対象 |
|---|---|---|
| `comfyui` | `workflow/*.json` のテンプレートを `/prompt` に投入（§5） | 既定。画像・動画・音声のすべて |
| `grok_cli` | Grok Build CLI をヘッドレスで叩く（`app.grok_media`） | `grok_imagine_t2i` / `grok_imagine_edit` |

ワークフローを 1 つも通さない経路として、**Remotion**（`mode: "remotion"`）と
**音源解析**（`mode: "audio_analysis"`）もある（どちらも下記）。

#### Grok Imagine（`backend: "grok_cli"`）

xAI の従量課金 API（`XAI_API_KEY`）ではなく、**SuperGrok / X Premium+ の
サブスクリプション枠**で動く公式 CLI に内蔵ツールの `image_gen`（text-to-image）/
`image_edit`（編集）で描かせる。プロンプト作成のチャット（§4.1）と同じコマンドだが、

- **作業ディレクトリが別**（`runtime/grok-media-workdir`、設定 `grok_media_workdir`）。
  CLI はコーディングエージェントで、セッションと生成物を書き散らすため
- **`XAI_API_KEY` を env から必ず外す**（残っていると API 直叩き＝従量課金に
  フォールバックしうる）。制限時間は設定 `grok_media_timeout`（既定 300 秒）、
  コマンド名 `grok_command` はチャットと共有

グラフが無いので渡せるのは**自然文の指示だけ**。したがって:

- **LoRA は挿せない**（指定したジョブは 422）
- **解像度は選べない**。`aspect_ratio` はフォームのプリセットから比を読み、CLI が
  受ける語彙（`1:1` / `16:9` / `9:16` / `3:2` / `2:3` / `auto`）の一番近いものへ寄せて
  渡す（`workflows.grok_aspect_ratio`）。`megapixels` は使わない
- **モデルのバージョンは指定できない**（CLI に `model` パラメータが無い）
- 編集元画像は CLI のサンドボックスから読めるよう、`<作業ディレクトリ>/inputs/` へ
  **コピーしてから**指示文でファイル名を参照する
- 実行したノードが無いので `/api/health` の ComfyUI チェック・custom node 確認・
  モデルスロット（§3.3）の対象にはならない（`workflows.comfy_specs()` が除く）

**成否は言葉ではなくファイルで判定する**（相手は「作った」と言いながら置かないことが
あるエージェント）。指示文の末尾で `OK <絶対パス>` / `FAILED <理由>` だけを出すよう
約束させ、4 段構えで確かめる:

1. 終了コードが非 0 なら失敗（認証エラー / クォータは文言を言い換える）
2. 出力から合図を読む
3. **指定パスにファイルが実在し、サイズ > 0**（合図だけを信じない）
4. 無ければ CLI 自身の保存先（`~/.grok/sessions/<URL エンコードした cwd>/<session-id>/images/`）
   を mtime 順で探す保険（セッション id は実行のたびに変わるので根から探す）

失敗した実行は **1 回だけやり直す**（モデレーションの誤検知が一定の割合で起きる）。
ただしサブスク枠を使い切った気配（rate limit / quota / 429）はやり直しても無駄なので
`GrokQuotaError` にしてそのままユーザー向けの文言で返す。何を頼んだかは
`workflow_json` に指示文ごと残す（ComfyUI のグラフを残すのと同じ意図）。

疎通は 2 段階: `GET /api/grok/status` は枠を使わない軽い確認（コマンドが在るか・
`~/.grok/auth.json` が在るか）、`POST /api/grok/check` は実際に 1 ターン回す
（設定ページの「grok CLI の接続確認」ボタン。生成はしないので消費は最小）。

#### Remotion（`mode: "remotion"`、`app/remotion.py`）

React で組んだ動画（テロップ・図表・MV のような**決まった絵**）をレンダリングする、
ComfyUI と並ぶもう 1 つの生成経路。Remotion プロジェクト（Node のリポジトリ）は
リポジトリルートの **`remotion/` に同梱**してあり、アプリはそれを ComfyUI と同じく
「レンダリングバックエンド」として参照する。

- 連携は設定 `remotion_enabled` が持ち、**既定は OFF**（ライセンスの都合、下記）。
  無効のあいだは一覧も投入も 400
- 使うプロジェクトは**常に同梱の `remotion/`**（場所の設定は持たない）。composition を
  足す・直すときは `remotion/src/` を編集する
- 同梱の composition は 3 つ: `MusicVideo`（カット割り・トランジション・歌詞・BGM）/
  `FxOverlay`（出来上がった mp4 の上にイベント駆動で文字演出・エフェクトを載せる。
  `card` / `imageSlam` / `glitchCut` / `lyric` など 15 種のイベント）/ `Slate`（疎通確認）。
  props の正本は `remotion/src/schema.ts`（zod）
- 依存（`remotion/node_modules/`）は `run.sh` が初回に入れる（Docker で動かす場合は
  ホスト側で `npm --prefix remotion install`）。入っていなければその旨のエラーで
  400 になる

> **ライセンス**: Remotion は MIT などのオープンソースライセンスではなく、独自の
> Remotion License で提供されている。個人利用および従業員 3 名以下の会社は無償だが、
> それ以上の規模の会社での利用には会社ライセンス（有償）の購入が必要
> （<https://www.remotion.dev/license>）。同梱はしても**既定 OFF** にし、設定ページの
> 「Remotion 連携」に注意書きを出して、利用者が条件を確かめてから有効にする形にしている。
> 依存の導入自体は使用にはあたらないので、`npm install` は `run.sh` が初回に行う。

- `GET /api/v1/remotion/compositions` … `npx remotion compositions <entry>` を叩いて
  composition の ID を並べる（短時間キャッシュ）。エントリポイントは `src/index.ts` を
  既定とし、プロジェクトの `package.json` に `config.remotionEntry` があればそちら
- ジョブは `{"mode": "remotion", "remotion_composition": "<ID>", "remotion_props": {…}}`
  の 2 項目だけを取る（画像・動画・音声のフィールドは使わない）。`npx remotion render` を
  サブプロセスで回し、標準出力の進捗を WS へ流す
- `props` は CLI 引数に直接埋めると長さと引用符で壊れるので、**一時 JSON ファイル**
  （`runtime/remotion/`）に書いて `--props=<file>` で渡す
- 出来た mp4 は他のジョブと同じ `outputs/{job_id}/` に置くので、履歴・WS・ライブラリ・
  素材登録・タイムラインの素材ビンには何も足さずに乗る
- **音声の焼き直し**（`remotion.remux_audio`）: Remotion の出力は音声が
  **2,048 サンプル（48kHz で約 42.67ms ≒ 1 フレーム）遅れる**（AAC のプライミングが
  実体として入り、edit list で相殺されない）。決めの効果を 1 フレーム単位で合わせた
  映像ではそのまま音ズレになるので、レンダリングの後処理で **映像は `-c:v copy` の
  まま、音声だけ元音源から焼き直す**（`ffmpeg -i video.mp4 -i <audio> -map 0:v -map 1:a
  -c:v copy -af "…,apad" -c:a aac -b:a 320k -shortest`）。props の `audio.src` が
  `outputs/` / `library/` / `assets/` の中に解決できるときだけ働き、`audio.startFrom`
  （音源側の入力の直前に `-ss`）・`volume`・`fadeOut`（`afade`。映像の尺が読めるとき
  だけ）も再現する。`MusicVideo` / `FxOverlay` で props の形（`audioSchema`）は同じ
  なので composition では分けない。**失敗してもジョブは失敗させない**（mp4 自体は
  出来ているので、元のまま残してログにだけ書く）

#### 音源解析（`mode: "audio_analysis"`、`app/audio_analysis.py`）

歌詞つきの映像（MV・モーショングラフィックス）は、演出の秒を決め打ちできない。
歌詞のアライン（行と 1 文字ごとの秒）・実測の onset・ビート・無音区間を音源から
出しておき、`FxOverlay` の `lyric.chars` / `beatMarker` / `MusicVideo.beats` や
タイムラインの計画秒（`planned_start_seconds`）はその結果から算出する。

**重い依存はアプリの環境に入れない**のがこの経路の要点で、ComfyUI・Remotion と
同じ「外で構築したバックエンドを参照する」形をとる:

- 解析の本体は `backend/app/audio_analysis_worker.py`。**`app` パッケージに依存
  しない単独のスクリプト**で、torch / faster-whisper / stable-ts / librosa を
  import するのはここだけ
- アプリ側（`app/audio_analysis.py`）は設定 `audio_analysis_python`（解析用 venv の
  python の絶対パス。空ならアプリ自身の interpreter）でそのスクリプトをサブプロセス
  実行し、標準出力の `PROGRESS <0..1> <文言>` を WS へ流す（Remotion と同じ流儀）
- 依存が入っていなければワーカーは**終了コード 3** で落ちる。それはジョブの失敗では
  なく設定不足なので **400** にして「`pip install -r backend/requirements-optional.txt`
  を解析用の venv で実行し、`audio_analysis_python` を指す」ことまで本文で返す。
  同じ確認（`--check`。import せず `find_spec` で見るだけ）は**ジョブ投入の時点**でも
  走るので、履歴に無駄な失敗ジョブは残らない
- GPU が無ければ CPU で動く（faster-whisper は `compute_type=int8`、モデルは既定 `small`）。
  GPU はあっても ComfyUI と取り合いになるので、**メモリ不足で落ちたら CPU でやり直す**
  （遅くなるだけで結果は同じ。`warnings` にその旨が入る）

ジョブの `params` は `analysis` の 1 つだけ:

| 項目 | 内容 |
|---|---|
| `audio` | 解析する音源（`MediaRef`: `job_id` / `item_id` / `export_id` / `path` のどれか 1 つ） |
| `lyrics` | 行区切りの歌詞（任意）。**あれば `align`、無ければ `transcribe`** |
| `stems` | ボーカルステムなど（任意、`MediaRef` の配列）。あればアラインと onset はこの先頭から |
| `tasks` | `align` / `transcribe` / `onsets` / `beats` / `silence` の部分集合（既定は全部） |
| `language` | 既定 `ja` |
| `align_substitutions` | アライン前の置換（`{"BAN!": "バン"}`）。英字＋感嘆符のような読みの当たりにくい語を仮名に直す |
| `model` | `small`（既定）/ `medium` / `large-v2` |

出力は `outputs/{job_id}/analysis.json`:

```jsonc
{ "duration": 193.48, "sample_rate": 48000,
  "lines":  [{ "i": 1, "start": 16.6, "end": 20.3, "text": "今日も見張ってる",
               "chars": [{ "c": "今", "s": 16.6, "e": 16.8 }] }],
  "onsets": [{ "t": 43.90, "strength": 0.9 }],
  "beats":  { "bpm": 116, "times": [0.0, 0.52] },
  "silence": [{ "start": 180.5, "end": 185.3 }],
  "sections": [],          // 手で書き足す欄（解析では埋めない）
  "warnings": [] }
```

- `align` は stable-ts（openai-whisper のモデル）で行ごとに当てる。`chars` は語の
  秒を 1 文字ずつに等分したもの（**語の頭は実測**、ずれるのは語の中だけ）。置換や
  記号落としで当てた文字列が元の行と変わったときは `aligned_text` が付く
- `transcribe` は faster-whisper の自由書き起こし（`chars` は語単位でよい）
- `onsets` / `beats` は librosa、`silence` は ffmpeg の `silencedetect`
  （`noise=-40dB:d=0.5`）。**librosa / ffmpeg が無いときはそのタスクだけ飛ばして
  `warnings` に書く**（400 にするのは whisper 系が要る `align` / `transcribe` だけ）
- 絵も音も作らないので NSFW 判定は Remotion と同じく **false で確定**（判定に掛ける
  プロンプトが無い）。履歴・外部 API では `analysis_url` として JSON へのリンクが出る

> **アラインの秒より実測 onset を優先する**。BAN!BAN!BAN! では「バン」のアライン秒が
> 実際の発音より 100〜250ms ずれたので、決めの演出（カード・叩き込み）の秒は
> ステムから採った onset に寄せた（`EDITING.md` §3.2）。

## 6. 成果物の取得

| 成果物 | 取得方法 |
|---|---|
| 生成画像 | 画像ワークフローの `SaveImage` / `SaveImageAdvanced` の出力を history から取得し `/view` でダウンロードして `outputs/{job_id}/image.png` に保存。出力ノード ID はワークフローごとに異なる（`29` / `46` / `9` / `195`）ためマニフェストの `output_node` を使う |
| 動画 | 動画ワークフローの `SaveVideo` の出力ファイルを `/view` でダウンロードし `outputs/{job_id}/video.mp4` に保存。出力ノード ID はワークフローごとに異なる（`75` / `341` / `68`）ためマニフェストの `output_node` を使う |
| 音声 | 音声ワークフローの `SaveAudioMP3`（`107` / `19`）の出力を `outputs/{job_id}/audio.mp3` に保存し `jobs.audio_output_path` に記録する |
| 追加の成果物 | 1 回の生成で複数返るモデルの 2 つめ以降を `outputs/{job_id}/audio_2.mp3` … に保存し、パスの JSON 配列を `jobs.extra_outputs` に記録する（API では `extra_output_urls` として返り、結果パネルのタブと履歴のバッジに出る）。主成果物の列（`image_path` / `video_path` / `audio_output_path`）に入るのは常に 1 つめ |
| 音源解析の結果 | `mode: "audio_analysis"` のワーカーが書いた JSON を `outputs/{job_id}/analysis.json` に置き `jobs.analysis_path` に記録する（API では `analysis_url`。§5.2） |
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
  extra_outputs TEXT,                      -- 主成果物に収まらない出力のパス（JSON 配列、§6）
  error         TEXT,
  nsfw          INTEGER NOT NULL DEFAULT 0,
  nsfw_source   TEXT NOT NULL DEFAULT '',  -- 判定の出所（auto / manual）
  credits_consumed REAL                     -- 外部バックエンドが消費したクレジット（過去の履歴用。ComfyUI は NULL）
);
```

- `params` には `video_workflow` / `image_workflow` / `audio_workflow`（ワークフロー ID）と、`end_image` / `reference_video` / `reference_images` / `reference_videos` / `reference_audios`、音声モードの `audio_prompt` / `lyrics` / `negative_tags` / `audio_category` / `reprompt` / `audio_seed` も保存する
- 後から足したカラム（`nsfw` / `nsfw_source` / `audio_prompt` / `audio_output_path` / `credits_consumed` / `extra_outputs` など）は起動時に `PRAGMA table_info` と突き合わせて不足分だけ `ALTER TABLE` する（`db.MIGRATIONS`）
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
  messages   TEXT NOT NULL,                 -- [{role, content, ts}] の JSON（会話の正本）
  grok_session_id TEXT NOT NULL DEFAULT '', -- 続き用の grok セッション（§4.3。消えたら組み直す）
  grok_cwd   TEXT NOT NULL DEFAULT ''       -- このチャットの作業ディレクトリ（grok の cwd）
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
- **ジョブの入力として使える**: `source_image` / `end_image` / `reference_video` / `audio_path` / `reference_images` ほかの参照素材は
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
  参照入力に「複数パネルを並べた 1 枚」を取るワークフロー向けのシートを
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
- フォーム用に `GET /api/options` の `library` には全件が入る。表示名・NSFW・タグ・
  カテゴリは `PATCH /api/library/{id}`、登録解除は `DELETE /api/library/{id}`（ファイルも消す）。
  `LibraryPickerModal` のタイルには [名前] / [🫣 NSFW]（ジョブと同じ `NsfwToggle`。manual 扱い）/
  [タグ] / [削除] を並べる
- ジョブ出力の [☆ ライブラリに登録]（`LibraryAddButton`）はカテゴリのプルダウンの隣に [▾] を持ち、
  開くと**表示名とタグ**（カンマ区切り、`LibraryFromJob.name` / `tags`）を入れてから登録できる。
  開かずに押せば従来どおり空のまま即登録（名前はプロンプトから決まり、タグは自動生成に任せる）
- DB とファイル操作は `app/library.py` に集約し、内部 API（`app/routers/library.py`）と
  外部 API（`app/routers/external.py`）のルーターが共用する
- **外部エージェントからも同じ棚を使える**: `GET /api/v1/library`（`kind` / `category` /
  `q` / `tag` で絞り込み）・`POST /api/v1/library/from-job`・`POST /api/v1/library/sheet`・
  `PATCH /api/v1/library/{id}` を公開している（**削除は公開しない**）。シートを作って
  そのパスをジョブの `source_image` に渡す、という流れまで外から回せる

#### 素材の下ごしらえ（スプライト / フォント画像 / コンタクトシート）

MV・モーショングラフィックス（Remotion の `FxOverlay`、§5.2）で使う**演出用の素材**を、
棚の上で作るための 3 つ。**通常のドラマ制作では使わない**（指示があったときだけ）。
実装は `app/sprites.py` / `app/textimage.py` / `app/contact_sheet.py`（どれも純関数の
モジュールで、DB は知らない）と、登録側の `app/library.py`、入り口の
`app/routers/library.py` / `app/routers/media.py`。

- **透過キー**（`POST /api/library/{id}/key`、`app/sprites.py`）: 画像素材の背景を抜いて
  **RGBA PNG の新しい素材**（`kind='image'` / タグ `sprite` / `source='sprite'`）にする。
  元の素材は触らない。NSFW は元素材から引き継ぐ（`auto`）。抜き方（`method`）は
  1. `black` / `white` … **ルミナンスキー**。明るさ（各画素の最大チャンネル）で前景と背景を
     分けるが、単純な閾値だと文字の内側の黒（縁取りの中や「の」の穴）まで抜けてしまう。
     そこで**周囲に余白を足して外側から floodfill** し、「画像の縁と地続きの背景」だけを
     背景と見なす。内側の同じ色は**穴として残る**。境界は閾値の前後を α の傾斜にして
     滑らかにし、最後に 0.6px だけぼかす（BAN!BAN!BAN! の `key_black()` と同じ考え方）
  2. `chroma` … `color`（既定 `#00ff00`）からの距離で α を決める。floodfill は使わないので
     内側の同色も抜ける（単色背景ではそれでよい）
  3. `rembg` … **任意依存**（`backend/requirements-optional.txt`。`requirements.txt` には
     入れない）。import できなければ **400** で入れ方を返す
  `tolerance`（0..1、既定 0.1 = 255 階調の 26）で閾値を、`trim`（既定 true）で不透明部分の
  bbox への切り詰めを指定する。抜いた結果が空なら 400。ジョブの生成画像を棚に入れずに
  直接抜く入り口として `POST /api/library/key-from-job`（`{job_id, source}`。`source` は
  `library.SOURCES` の `image` / `last_frame`。動画・音声は 400）もある。
  出来上がりの `url`（`/library/image/….png`）は Remotion の `sprite` / `imageSlam` /
  `stickerStack` の `src` にそのまま書ける
- **フォント画像**（`POST /api/images/text`、`app/textimage.py`）: インストール済みの書体で
  文字を組んで RGBA PNG にする（タグ `text-image` / `source='text'`）。書体の一覧は
  `GET /api/images/text/fonts`（fontconfig の `fc-list` から `file` / `index` / `family` /
  `style` を拾い、無ければ `/usr/share/fonts` などを走査する。TrueType コレクションは面
  ごとに 1 件、面が分からなければ 0）。既定は Noto Sans CJK JP Bold 相当
  （`textimage.DEFAULT_FAMILIES` の先頭から探す）。`text`（改行可）/ `font` / `size` /
  `color` / `outline{color,width}` / `bg`（`transparent` か色）/ `rotate` / `padding` /
  `align` を取り、回転は文字を組んだあとに `expand=True` で掛けるので端が切れない。
  用途は 2 つ: **そのままスプライトとして貼る**のと、**画像生成の字形参照**として参照画像に
  添えるの（日本語が誤字になるモデルでも字形が直る。BAN!BAN!BAN! で確立した運用）
- **コンタクトシート**（`POST /api/videos/contact-sheet`、`app/contact_sheet.py`）: 動画の
  コマを ffmpeg で 1 枚ずつ抜いて、PIL で 1 枚の jpg に束ねる（タグ `contact-sheet` /
  `source='contact-sheet'`）。`source` は `job_id`（+ どの出力か）/ `item_id` / `export_id` /
  `path` の**どれか 1 つだけ**（解決は `app/media_ref.py`。`outputs/` / `library/` /
  `assets/` の**外は開かない**）。抜く秒は `seconds` → `range{start,end,step}` →
  `frames`（fps が読めるときだけ）の順に見て、どれも無ければ尺を 12 等分した位置。
  `columns` / `width`（1 コマの幅。高さは元の縦横比から決まる）/ `labels`（各コマの下に
  秒とフレーム番号を焼く、既定 true）。応答は `{item, seconds, columns}` で、`seconds` に
  **実際に抜いた秒**が左上から順に並ぶ。手元で見る `scripts/inspect.sh`（1 秒ごとの PNG を
  一時ディレクトリに出す）との棲み分けは「`inspect.sh` は人が手元で、この API は外部
  エージェントが**演出の配置を確かめる**ため」

### 7.3 編集タブ（タイムライン）

ドラマスタジオの**制作**タブが「1 カットを焼く」ところまでなのに対して、**編集**タブは
焼き上がったテイクを並べ直して**1 本の動画に書き出す**面。生成（`studio_*`）とは別の
テーブル群を持ち、クリップはソースの行を**参照するだけ**で複製しない。

```sql
CREATE TABLE studio_timelines (
  id          TEXT PRIMARY KEY,
  project_id  TEXT NOT NULL REFERENCES studio_projects(id) ON DELETE CASCADE,
  episode_id  TEXT,                        -- どの話を組んだものか（NULL = 作品まるごと）
  name        TEXT NOT NULL DEFAULT '',
  fps         REAL NOT NULL DEFAULT 24,    -- 書き出しの規格（クリップはここへ揃える）
  width       INTEGER NOT NULL DEFAULT 1280,
  height      INTEGER NOT NULL DEFAULT 720,
  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL
);

CREATE TABLE timeline_tracks (
  id          TEXT PRIMARY KEY,
  timeline_id TEXT NOT NULL,
  project_id  TEXT NOT NULL REFERENCES studio_projects(id) ON DELETE CASCADE,
  kind        TEXT NOT NULL CHECK(kind IN ('video','audio','subtitle')),
  name        TEXT NOT NULL DEFAULT '',    -- 'V1' など
  sort_order  INTEGER NOT NULL DEFAULT 0,
  muted       INTEGER NOT NULL DEFAULT 0,
  locked      INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE timeline_clips (
  id          TEXT PRIMARY KEY,
  track_id    TEXT NOT NULL,
  timeline_id TEXT NOT NULL,               -- 非正規（1 本ぶんを join なしで引く / 全置換する）
  project_id  TEXT NOT NULL REFERENCES studio_projects(id) ON DELETE CASCADE,
  start_ms    INTEGER NOT NULL DEFAULT 0,  -- タイムライン上の開始位置
  duration_ms INTEGER NOT NULL DEFAULT 0,  -- 尺（等速なので out_ms - in_ms と一致）
  source_kind TEXT NOT NULL
    CHECK(source_kind IN ('take','asset_file','library','job','image','text','gap')),
  source_id   TEXT,                        -- 上の種別の中での id（gap / text は NULL）
  in_ms       INTEGER NOT NULL DEFAULT 0,  -- ソースの中の切り出し位置
  out_ms      INTEGER NOT NULL DEFAULT 0,
  gain_db     REAL NOT NULL DEFAULT 0,
  fade_in_ms  INTEGER NOT NULL DEFAULT 0,
  fade_out_ms INTEGER NOT NULL DEFAULT 0,
  transition_kind TEXT,                    -- **前の**クリップとの繋ぎ（NULL = カット）
  transition_ms INTEGER NOT NULL DEFAULT 0,
  text_payload TEXT,                       -- text クリップの中身（JSON）
  speed       REAL NOT NULL DEFAULT 1,     -- 再生速度（duration_ms = (out-in)/speed）
  sort_order  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE timeline_exports (
  id          TEXT PRIMARY KEY,
  timeline_id TEXT NOT NULL,
  status      TEXT NOT NULL DEFAULT 'queued',  -- queued | running | done | failed
  progress    REAL NOT NULL DEFAULT 0,         -- 0.0〜1.0
  params      TEXT NOT NULL DEFAULT '{}',      -- 書き出し設定（width / height / fps）
  output_path TEXT,
  error       TEXT,
  fps         REAL,                            -- 焼き上がりの規格（走り終わるまで NULL）
  width       INTEGER,
  height      INTEGER,
  frames      INTEGER,                         -- 総フレーム数（ffprobe -count_frames の実測）
  duration_ms INTEGER,
  warnings    TEXT NOT NULL DEFAULT '[]',      -- PAD / フレーム数のずれ（JSON 配列）
  created_at  TEXT NOT NULL,
  finished_at TEXT
);
```

- **ソースへの外部キーは張らない**。元のテイクやジョブが消えてもクリップの並びは残し、
  読み取りのたびに実ファイルの有無を見て `missing`（メディア欠落）として返す。ただし
  **欠落が残っているタイムラインは書き出しを受け付けない**（`POST /export` が 400。黙って
  黒＋無音にすると気づかないまま納品されうる）。直し方は `GET /missing` が返す
  「同じカットの別テイク」への差し替えか、一括削除（`POST /missing/resolve`）
- `source_kind='image'` の `source_id` だけは**出どころの印つき**（`library:<id>` /
  `job:<id>` / `asset_file:<id>`）。1 つの種別に 3 つの出どころがぶら下がるため
- **繋ぎ（トランジション）はオーバーラップ方式**。`transition_kind` / `transition_ms` は
  そのクリップの**前の境界**を指し、置くと前後がその長さだけ重なってタイムラインの全長は
  縮む。置けるのは映像トラックだけ・トラックの先頭以外・長さは 200〜2000ms かつ隣り合う
  2 つの短いほうの 1/2 まで（`app.timeline.validate_clips` / `relayout`、画面側は
  `timeline.ts` の `ripple`）
- **タイムライン削除の後始末はアプリ側**（`app.timeline.delete_timeline`）でトラック・クリップ・
  書き出しの記録を消す。書き出した mp4 は成果物なので残す（ジョブの出力と同じ扱い）
- `timeline_tracks` / `timeline_clips` が `project_id` を持つのは、リビジョンのスナップショット
  （`app.studio._SNAPSHOT_TABLES`）が project_id で束ねて書き戻すため（`studio_asset_files` と同じ持ち方）。
  EDL はリビジョンに載るが、`timeline_exports`（実行結果）は載らない
- スナップショットに**そのテーブルのキーが無い**（編集タブより前に取ったリビジョン）ときは、
  その面を「空だった」と読まずに触らない。復元でタイムラインが丸ごと消えるのを防ぐ

#### 自動配置つきの初期化

`POST /projects/{id}/timelines` に `episode_id` を渡すと、その話のカットを **場 → カット順**
（`app.studio._fetch_shots` と同じ規則）に走査し、`selected_take_id` があって動画が実在する
ものだけを V1 トラックへ**隙間なく**並べる。クリップの尺は ffprobe で読み、読めなければ
5 秒（`app.timeline.FALLBACK_CLIP_MS`）に落とす。`episode_id` を省くと V1 だけの空のタイムライン。
カットが `planned_start_seconds` を持っていれば、隙間なくではなく**音源上のその秒**へ置く
（下の「音源基準の配置」）。

#### トラックと素材

- **V1（映像）** … 並べ替えの正本。1 本きり（増やせず・消せない）。ここだけがリップル方式
  （常に先頭から詰まる）で、繋ぎの重なりぶんだけ縮む
- **A1…（音声）** … `POST /tracks` で何本でも足せる（ミュート・削除・改名は
  `PATCH` / `DELETE`）。クリップは**自由配置**（隙間は空けられるが、同じトラックの中では
  重ねられない）。`gain_db` / `fade_in_ms` / `fade_out_ms` が効く
- **T1（字幕）** … `text` クリップ（`text_payload` = `{text, style}`）。`style` は
  位置（bottom / top）・大きさ（S / M / L）・色（white / yellow）の 3 つだけ
- **素材ビン**（`GET /projects/{id}/media?kind=video|audio|image`）はテイク・ライブラリ・
  終わった**単発**ジョブ（テイクの裏にあるジョブは外す）・作品の素材ファイル
  （`studio_asset_files`）を新しい順に 1 本へ混ぜて返す。長さの下調べは返すページのぶんだけ

#### 台詞からのテロップ生成

`POST /timelines/{id}/generate-subtitles` は V1 のクリップを Take → Shot と辿って
`studio_shots.dialogue` を読み、クリップの区間へ**等分**に割り付ける（改行があればそれが
区切り、無ければ句点・感嘆符・疑問符の後ろで切る。`app/timeline_subtitles.py`）。
字幕トラックの中身は**置き換える**（積み増すと二重に出る。画面側で確認ダイアログを出す）。

#### 脚本との差分（`sync-preview` / `sync`）

タイムラインを作ったあとに脚本が動いた分を 3 つだけ見る:
**増えたカット**（その話に採用テイクつきのカットが増えた）/ **採用が変わったカット**
（クリップが古いテイクを指している）/ **消えたカット**（カットが消えた・採用が外れた）。
反映は項目ごとに選ぶ（`POST /sync` の body）——増えたものは V1 の末尾へ、差し替えは
新しいテイクの尺へ切り出しを丸め、消したものは詰める。音声・字幕トラックは動かさない
（尺に合わせて置いてあるので、勝手にずらすと合っていたものが外れる）。

#### 音源基準の配置（`studio_shots.planned_start_seconds`）

**通常のドラマ制作では使わない**（並び順で十分）。MV のように「音源に映像を合わせる」
制作でだけ、カットに **音源上の計画開始秒**（`planned_start_seconds`。`NULL` = 未使用）を
書いておくと、自動配置と `sync` がその秒へカットを置く（`app.timeline.plan_layout`）。

- 計画秒を持つカットは `start_ms = round(計画秒 * 1000)` に置く。尺は**次の計画秒までの
  間隔**（最後のカットは Take の尺）を上限に、Take の尺で切る
- 空いたところ（先頭まで・短い Take のうしろ）は `gap` クリップ（黒＋無音）で埋まるので、
  次のカットの頭は必ず計画どおりの秒から始まる
- **採用 Take を差し替えても同じ計画秒へ置き直す**（`sync` は「新しいテイクの尺へ丸める」
  ではなく計画を正本にする）。人が手でトリムした値も計画で上書きされる
- 計画秒を持たないカットは、計画の終わったところから**今までどおり**順に詰む
  （`sync` の「増えたカットは V1 の末尾へ」はそのまま）
- 音源基準では**繋ぎ（トランジション）を持てない**（重なるとその先の位置が全部ずれる）
  ので、計画秒つきのクリップからは落とす
- 計画秒が詰まりすぎていて `MIN_CLIP_MS` も置けないときは 400

外部エージェントは `PATCH /shots/{id}` の `planned_start_seconds` に音源解析の結果
（歌詞のアライン・onset）から出した秒を書き、`POST /timelines/{id}/sync` を 1 回呼べば
音源基準のタイムラインができる。

#### 差し込みクリップ（`POST /timelines/{id}/clips/insert`）

指定した位置に重なる既存クリップを**前後に分割**して割り込む
（`app.timeline.insert_into`）。body は `{track_id, start_ms, duration_ms, source_kind,
source_id, in_ms, base_revision}`。

- 下のクリップの**切り出しは動かさない**（後半は `in_ms` をずらして続きから再生される）
  ので、**トラックの全長は変わらない**（前後へ押し出さない）
- 完全に覆われたクリップは消え、`MIN_CLIP_MS` に満たない切れ端も落ちる（そのぶんは隙間）
- 分割された後半は前の境界が変わるので、繋ぎ（`transition_kind`）を持ち越さない
- `base_revision` は他の PATCH と同じ楽観ロック（§7.4）。成功すると 1 リビジョン積む

#### 書き出しエンジン（`app/timeline_export.py`）

EDL → ffmpeg コマンドの**組み立ては純関数**（`build_command`）で、実行（`run_export`）と分けてある。

- クリップごとに `trim` + `setpts` で切り出し、`scale`（`force_original_aspect_ratio=decrease`）
  + `pad` + `setsar=1` + `fps` でタイムラインの規格へ正規化（比の違うソースは切らずに黒帯）。
  音声も `atrim` + `asetpts` + `aresample` + `aformat` で 48kHz / stereo に揃える
- 音声を持たないソースと `gap` には、その尺ぶんの `color`（黒）と `anullsrc`（無音）を `lavfi` から
  足す。全クリップが「映像 1 本 + 音声 1 本」になるので、繋ぎ方に関わらず形が揃う
- **繋ぎのないところは `concat` のまま**。繋ぎで区切られた「まとまり」どうしを `xfade`
  （音声は `acrossfade`）で重ねる。`offset` は純関数 `transition_offsets` が出す
  （`offset = ここまでの全長 - 重なり`）。種別は `TRANSITIONS` が xfade 名へマップする
  （crossfade → `fade` / fadeblack / fadewhite / wipeleft / wiperight / slideleft /
  slideright / circleopen / pixelize）
- **リタイム**は `setpts=(PTS-STARTPTS)/speed` と `atempo`。`atempo` は 0.5〜2.0 しか
  取れないので、積が `speed` になる並びへ割る（純関数 `atempo_chain`）
- **静止画クリップ**は `-loop 1 -t` で尺ぶんの映像にしてから同じ規格へ（音は無音）
- **音声トラック**は `atrim` + `volume` + `afade` + `adelay` で置き場所へずらし、映像側の音と
  `amix=duration=first`（＝タイムライン全長で切る。`apad` はしない）
- **テロップ**は ASS（`app/timeline_subtitles.build_ass`）を書き出して `subtitles` フィルタで
  焼き込む。スタイルは 1 つだけ置き、位置・大きさ・色は行ごとの上書きタグ
  （`{\an8\fs48\c&H0000FFFF}`）。文字サイズは出力の高さに対する比なので解像度に依らない
- **ラウドネス正規化**（既定 ON）は最後に `loudnorm=I=-14:TP=-1.5:LRA=11`（1 パス）
- **書き出しプリセット**（`preset`）は `timeline`（規格のまま）/ `1080p` / `vertical`
  (1080x1920) / `720p`。fps はタイムラインの値のまま。縦横比が変わるときの収め方は
  `fit`: `pad`（レターボックス）/ `crop`（中央クロップ）。どちらも `timeline_exports.params` に残る
- **フレーム精度**: 境界はすべて `round(t * fps)` でフレーム番号に量子化する
  （`frame_count`）。秒のまま切ると端数フレームが捨てられて連結後に最大 2 フレーム先走る
  ので、各クリップは 1 フレームぶん余分に取ってから `trim=end_frame=<枚数>` でちょうど
  その枚数に切り、音も `apad` + `atrim` で同じ長さへ揃える。繋ぎの `offset` / `duration` も
  フレーム数から出し、出力そのものも `-frames:v` で締める
- **不足尺の保険**: 素材の実尺が「切り出し位置 + 尺」に届かないクリップは
  `tpad=stop_mode=clone` の末尾静止で埋める（`ffprobe` で測った実尺との差 + 余裕）。
  1 フレームを超える不足は `PAD <カットの見出し> <不足秒>s` として書き出しの `warnings` に出る
- **焼き上がりの検算**: `ffprobe -count_frames` で総フレーム数を数え、計画
  （`round(全長 * fps)`）と違えば `warnings` とログに出す。結果は `timeline_exports` の
  `fps` / `width` / `height` / `frames` / `duration_ms` / `warnings` に残り、
  `GET /timelines/{id}/exports` から読める（Remotion の `FxOverlay` の `base` に渡すとき、
  props と規格を揃えるために要る）
- 出力は `outputs/exports/{export_id}/final.mp4`（H.264 + AAC / yuv420p / `+faststart`）。
  `OUTPUTS_DIR` の下なので `/outputs` でそのまま配信できる
- 進捗は `-progress pipe:1` の `out_time_us` を読み、`timeline_exports.progress` を更新しつつ
  WS（`type: "timeline_export"`）へ流す。`communicate()` は使わない（stdout を奪い合うため）
- 同じタイムラインで走っている書き出しがあれば **409**（同時に 2 本焼いても得がない）
- ソースの下調べ（ffprobe）は**まとめて並列**に走らせ、`(パス, 更新時刻, 大きさ)` で結果を
  使い回す（`app.timeline.probe_many` / `probe_cached`）。長いタイムラインでも
  読み取りのたびに何十プロセスも直列に待たない

#### 既知の制限

- **BGM のループ再生はできない**。尺より短い音は途中で終わるので、繰り返したいときは
  同じクリップを並べる
- **プレビューは近似**。繋ぎはクロスフェード / 黒・白フェードだけ 2 枚重ねで近似し、
  ワイプ・スライド系はカット表示（正確な絵は書き出しで確認する）。テロップも書体と
  縁取りが焼き込みとは違う。BGM は Web Audio（`AudioBufferSourceNode` + `GainNode`）で
  再生ヘッドに合わせて鳴らすので、フェードは gain のスケジュールによる近似
- 映像トラックは **V1 の 1 本きり**（重ね合成・ピクチャインピクチャはスコープ外）

#### 画面の持ち方（`frontend/src/components/studio/`）

- 画面の状態は**全トラックのクリップ 1 本の配列**（`EditView` の履歴）。並べ替え・トリム・
  分割・繋ぎ・リタイムはどれも `timeline.ts` の純関数で、トラック単位に当てる
  （`applyToTrack`）。V1 だけが `ripple`（詰め直し）を通る
- **Undo / Redo は画面の中だけ**。サーバーには「今の並び」しか無い（`app.timeline.replace_clips` は
  全置換）。編集は 1〜2 秒のデバウンスで `PUT /clips` に自動保存し、状態をインジケータに出す
- **トラックの出し入れ（追加・ミュート・削除）とテロップ生成・差分反映・欠落修復は
  サーバー側の操作**で、返ってくる EDL が画面を丸ごと差し替える。走らせる前に手元の
  変更を流し切る（`withFlush`）——でないと、まだ送っていない編集がその差し替えで消える
- **プレビュー**はクリップごとの `<video>` を切り替える方式（プレイヤースイッチング）。
  次のクリップは先に `in_ms` へシークして待たせてあるので、切り替えの間はそのぶん短い。
  リタイムは `playbackRate` で追う

### 7.4 編集履歴（リビジョン）

人と外部エージェントが**同じ作品を同時に触る**前提の安全網。スタジオへの書き込みは
1 操作 = 1 リビジョンとして `studio_revisions` に積む（実装は `app/studio.py` の
`_record_revision` / `diff_revision` / `restore_revision`）。

```sql
CREATE TABLE studio_revisions (
  id            TEXT PRIMARY KEY,
  project_id    TEXT NOT NULL REFERENCES studio_projects(id) ON DELETE CASCADE,
  seq           INTEGER NOT NULL,           -- プロジェクトごとの 1 始まりの連番
  actor         TEXT NOT NULL DEFAULT 'user',  -- user / external / chat
  action        TEXT NOT NULL DEFAULT '',   -- 変更内容の短い説明（日本語）
  entity_kind   TEXT NOT NULL DEFAULT '',   -- project / episode / scene / shot / asset …
  entity_id     TEXT NOT NULL DEFAULT '',
  snapshot_json TEXT NOT NULL,              -- その時点のプロジェクト全体
  created_at    TEXT NOT NULL
);
```

- **actor は 3 種**（`models.STUDIO_REVISION_ACTORS`）: `user` = UI からの操作、
  `external` = 外部 API（`/api/v1`）、`chat` = アプリ内のチャット。分ける前に書かれた
  過去行の `agent` はそのまま残る
- **スナップショットはプロジェクト全体**（`_SNAPSHOT_TABLES`）: 話 / 場 / カット /
  **Take** / 素材 / 素材のリファレンス / タイムライン・トラック・クリップ。書き出しの
  記録（`timeline_exports`）は実行結果なので入れない
- **差分**（`GET .../revisions/{seq}/diff`）は直前のリビジョンとの項目単位の
  before / after。`id` や `updated_at` のような「毎回動く列」は `_DIFF_NOISE` で落とす
- **復元**（`POST .../revisions/{seq}/restore`）は丸ごとでも、ボディに `entity` / `id`
  （＋ `fields`）を送って**その 1 件・その項目だけ**の部分復元でもよい。書き換える**前**の
  状態を 1 リビジョン残す（`RESTORE_BACKUP_ACTION` = 「復元前の自動スナップショット」）ので、
  復元そのものもやり直せる
- **Take は「載っているものは戻す・知らないものは触らない」**（`_KEEP_UNKNOWN_ROWS`）:
  生成そのものはリビジョンを作らないので、素直に全置換すると脚本を 1 つ戻しただけで
  その後に焼いた Take の目録が消える。採用（`selected_take_id`）はスナップショット側の
  値に戻り、新しい Take は候補としてぶら下がったまま残る
- **`base_revision`（楽観ロック）**: PATCH のボディに「これを見て書いた」連番を添えると、
  それ以降に**同じエンティティ**が触られていた場合だけ 409（`_check_base_revision`）。
  別のカットが動いただけなら通る。省略すれば無条件に書き込む
- **深さは `REVISION_LIMIT`（1000 件）**。超えたぶんは古いものから捨てる（外部エージェントが
  脚本を書き換える運用では 1 セッションで何十件も積むため）

読み書きは内部 API（`GET/POST /api/studio/projects/{id}/revisions…`）と外部 API
（`/api/v1/…` の同じ 3 本）の両方に出ている。

### 7.5 画面の状態（`ui_state`）

「外部エージェントがフォームを埋めて、人が確かめてから押す」ための共有置き場
（`app/ui_state.py`、テーブル `ui_state`）。いま置いているのは**生成フォームの下書き
1 件だけ**（キー `generate_form`）。

- 値のスキーマの正本は**フロントの `FormState`** で、バックエンドは JSON の辞書として
  素通しする（項目が増えてもバックエンドを直さずに済む）。守るのは大きさの上限
  （`MAX_VALUE_BYTES` = 64KB）と `revision` だけ
- `revision` は保存のたびに 1 つ上がる連番。書き手は `base_revision` を添えられ、その間に
  誰かが書いていれば 409（本文に現在値が入る）。省略すると強制上書き
- 双方向: 外部エージェントが `PATCH /api/v1/ui/generate-form` で値を入れるとブラウザは
  WS（`type: "form"`）で受け取ってフォームへ流し込み、人がフォームを触れば
  `PUT /api/ui/generate-form` で書き戻る。**自分が書いた `revision` は送り主が読み飛ばす**
- `POST /api/v1/jobs` に `{"from_form": true}` を入れると、この下書きをそのまま投入できる
  （一緒に送った項目はその上から重ねる）
- 画面そのものを動かす `POST /api/v1/ui/navigate` は DB を使わず WS（`type: "ui"`、
  `op: "navigate"`）だけを流す。行き先は 生成 / スタジオ / 設定 で、`project_id` /
  `shot_id` は実在と噛み合わせを確かめてから流す

---

## 8. UI 仕様

SPA 1 画面 + 履歴。ダークテーマの生成系ツールらしい見た目。

画面は **[生成] と [スタジオ] の 2 タブ**（`Header.tsx` の `VIEWS`）＋ 右端の歯車から開く
**設定ページ**。外部エージェントは `POST /api/v1/ui/navigate` でこの 3 つの行き先へ
ブラウザを連れて行ける（§7.5）。

```
┌────────────────────────────────────────────────────────┐
│ ヘッダー: [生成 | スタジオ]  接続状態(ComfyUI ● / CLI ●)  [⚙]│
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
- **リファレンスシートを「ライブラリから作成」**: リファレンスシート 1 枚を `source_image` に取る動画ワークフロー（`form.REFERENCE_SHEET_WORKFLOWS`。現在は該当なしなので UI には出ない）を選んでいるときだけ、画像欄に [ライブラリから作成] を足す（`SheetBuilderModal`）。押すと `LibraryPickerModal` の複数選択モード（タイルに選択順のバッジが出る）で画像素材を **2〜8 枚**選べ、[この順で作成] で `POST /api/library/sheet`（§7.2）を呼ぶ。シートの大きさは選択中のアスペクト比から長辺 1280px で決める（`form.sheetSize`。プリセットが読めなければ 1280x720）。出来上がったシートはそのまま画像欄に入り、ライブラリにも残る。作成中はボタンを [作成中…] にし、失敗はモーダル内にそのまま出す
- **マルチモーダル参照の欄**（MiniMax H3 r2v、§2.2 / §3.1）: 参照入力を宣言しているワークフローを `mode: "i2v"` で選んだときだけ「マルチモーダル参照（開始フレームとは排他）」セクションを出す。参照画像 / 参照動画 / 参照音声の欄がそれぞれ「n / 上限 件」と [アップロード] [ライブラリから選択] [履歴から選択] を持ち、選んだ素材は**選んだ順**に番号つきで積み上がる（並び順がそのままグラフに渡る順序）。各行の [外す] で個別に取り消せる。ライブラリのモーダルは複数選択モード（`LibraryPickerModal` の `selectedIds`）で開き、選ぶたびに欄へ出し入れしてモーダルは開いたまま、[選択を終える] で閉じる。上限に達したら追加の操作を無効化する。開始フレーム / 最後のフレームと同時に入っている場合と `mode: "full"` の場合は、送信前にフォームがその場でエラーを出す（バックエンドの 422 と同じ理由、`form.validateForm`）
- **リファレンス音声はライブラリに一本化**: `assets/audio` のプルダウンは廃止し、[ライブラリから選択] / [履歴から選択] / [アップロード]（アップロードはそのままライブラリ登録）と、選択中の名前 + プレビューだけを出す。LoRA の `default_audio` などが指す従来の `/assets/…` も入力としては引き続き有効
- [履歴から選択] は過去ジョブの出力から選ぶ（`HistoryPickerModal`）。**検索ボックス**でジョブの文言（動画 / 画像 / 音声プロンプト → 最初の指示）に部分一致するものだけに絞れる（ジョブは全件フロントにあるのでクライアント側で絞る）。候補は完了ジョブのみを新しい順に並べ、欄の種別で絞る（画像欄 = 生成画像とラストフレームの両方（ラベルで区別）、動画欄 = 生成動画、音声欄 = 音声ジョブの出力）。生成物は `outputs/` にあって `assets/` の外なので、選ぶと fetch → `POST /api/assets/{kind}` で assets へコピーしてから欄に入れる。モーダル内には独自の「🫣 NSFW表示」チェックボックスがあり、初期値はヘッダーのグローバルトグルに従うが、ここでの切り替えは `sessionStorage` に残さない（この画面かぎり）。オフのあいだは NSFW ジョブを一覧に出さない。Esc / 背景クリックで閉じる
- LoRA 選択はチップ型マルチセレクト（強度スライダー付き）。選択するとトリガーワード連結欄（編集可）に反映される。セクションは 2 つあり、**「LoRA（動画）」は動画設定群の中**（登録 `target = 'video'` のみ）、**「LoRA（画像）」は画像設定群の中**（`target = 'image'` かつ選択中の画像ワークフローと同じファミリーのみ）に置く
- **接続先プルダウン**（§5）: フォーム最上部に「接続先」（ComfyCloud / RunPod / ローカル）を置く。値は `GET /api/settings` の `comfy_target` 由来で、変えると即 `PUT /api/settings` に保存し、選択肢（`/api/options`）と `/api/health` を取り直す（ComfyUI が変われば使えるモデル・LoRA も変わるため）。設定を読み込むまでは無効化しておく
- **モードとワークフローに応じた項目の非表示**（`form.hiddenFields`）: 使わない項目はグレーアウトではなく**その欄ごと表示しない**。ただし値は `FormState` に残るので、その項目を使うモード / ワークフローへ戻せば入力内容が復元される
  - 動画生成モードでは画像ワークフロー・画像プロンプト・LoRA（画像）・トリガーワードを出さない（LoRA（動画）は出す）。画像のみモードでは動画ワークフロー・動画プロンプト・ネガティブ・リファレンス音声・秒数・fps・LoRA（動画）を出さない
  - **選択した動画ワークフローのマニフェスト**に従い、音声入力を持たないワークフローでは音声欄を出さず、必要な入力（最終フレーム / 参照動画）の欄だけを出す。**必須ではないが受け取れる入力**（任意の開始フレーム・最終フレーム画像）も欄は出す（`requires` だけでなく `supports` を見る）。渡すかどうかはユーザー次第で、空なら送らない
  - **画像ワークフロー**も同様で、編集系（qwen-image）では参照画像の欄が出る代わりにアスペクト比 / メガピクセルが消える
  - 音声モードでは画像・動画のセクション一式を出さず、音声ワークフローと、そのワークフローが露出しているつまみだけを出す
- **ワークフローの選択は「モデル → モード」の 2 段プルダウン**（動画 / 画像 / 音声のどのセクションでも同じ `WorkflowPicker`）: 1 段目がモデル（= ファミリー。表示名は `/api/options` の `family_label` で、外部 API・サブスク CLI といった供給元の注記もここに付く）、2 段目がそのモデルのモード（t2v / i2v / 素材参照 …。表示名は `mode_label` で、1 段目と重複するモデル名は入らない）。フォームが持つ状態は今までどおりワークフロー id 1 つだけで、1 段目はそこから引く（前回選択の復元・ライブラリからの連鎖・外部 API からの下書き反映は id を入れるだけで両方のセレクトが揃う）。モデルを変えるとそのモデルの先頭モードへ切り替わるので、存在しない id のまま送信されることはない。モードが 1 つしかないモデルでも 2 段目は消さず無効化して出す。選択肢そのものが取れないとき（ComfyUI に繋がらない）は従来どおり id の手入力欄になる
- 「画像＋動画」モードのプルダウンには開始フレームを受け取れる動画ワークフローのみを出す（選択中のものが対象外になったら自動で切り替える。モードが 1 つも残らないモデルは 1 段目からも消える）
- **選択式フィールド**を宣言しているワークフローでは、ワークフローセレクトの直下にその選択肢のプルダウンが並ぶ（§3.1）。自動決定できる項目には「自動（入力に合わせる）」、それ以外には「既定（<値>）」が先頭に入る。`video_prompt` が任意のワークフローではプロンプト欄に「（任意）」と出す
- LoRA チェーンを持たないワークフローでは LoRA（動画）セクションを出さない（挿せないため。指定したジョブはバックエンドが 422 にする）
- 動画ネガティブはプリセット選択（ワークフロー既定 / 現行値 / モデル作者版）+ 編集可（詳細設定アコーディオン内）
- 設定は**モーダルではなく専用ページ（フルページ）**。ヘッダーの [設定] で画面遷移し、ページ左上の [← 戻る] で生成画面に復帰する。3 タブ構成:
  - **接続 / LLM CLI**: 「ComfyUI 接続先」（[接続先] のプルダウン + ComfyCloud / RunPod / ローカルのサブセクション。RunPod のサブセクションには Pod の ComfyUI URL・APIキーに続けて §5.1 の自動起動の設定を置く） / **使う CLI**のプルダウン（`agent_cli`。grok / claude / codex / cursor）とその CLI の**コマンド**（空 = 既定。placeholder に既定のコマンド名を出す）・**モデル**（grok は既定 grok-4.5、ほかは空 = CLI の既定）・**CLI の作業ディレクトリ**（`grok_workdir`）・**Grok Imagine の作業ディレクトリ**（`grok_media_workdir`）・**CLI の追加フラグ**（`agent_grok_args`。空白区切りで入力し、**空にすると CLI のツールが丸ごと無効**になる旨を警告として添える）・**ACP でターンを回す**トグル（`agent_use_acp`。オンだと実行中の活動がチャットに出る）  / **モデル自動ダウンロード**のブロック（常に表示。ローカルの保存先パスは環境変数由来なので読み取り専用で見せ、「書き込み可 ✓」「パスが見つかりません」等の状態と、**Hugging Face トークン**・**Civitai APIキー**（どちらも `type="password"`。RunPod へ落とすときは Pod 側の環境変数が使われる）を並べる、§3.3）
  - **LoRA 管理**: 表示名・ファイル名・**対象ワークフロー（画像用 / 動画用）**・**モデルファミリー（画像用のみ）**・トリガーワード・既定強度・既定音声・並び順・**取得元 URL（任意）**の CRUD とサンプル画像の登録。一覧のバッジには対象とファミリーを出し、取得元 URL が登録済みなら `URL ✓`（title に URL）を添える。取得元 URL は LoRA 本体と同じ [追加] / [更新] で保存し、保存先はモデルタブと同じ `model_download_urls`（キーは `lora_name`）。**空欄で保存するとキーを消し**、**ファイル名を変えた場合は旧キーを消して新キーへ移す**（URL に変化が無ければ設定は PUT しない）。ここではダウンロードせず、モデルタブと同じく [DL] / [全DL]（§3.3）の取得元として使う
  - **モデル** / **LoRA 管理**: どちらもタブの先頭に [対象の接続先]（ComfyCloud / RunPod / ローカル。現在の接続先には「（現在の接続先）」を添える）のプルダウンを置き、選んだ環境の登録を読み書きする（初期値は現在の接続先。繋いでいない環境も整理できるよう、接続先そのものとは独立に切り替えられる。切り替えると未保存の編集は捨てて読み直す、§5）
  - **モデル**: 全ワークフローのモデルファイル名一覧を **画像 / 動画 / 音声の大分類 → ワークフローごとの折りたたみ**（既定は閉じ、見出しに項目数・未保存件数・既定から変更した件数のバッジ）に整理し、行ごとにテキスト入力で上書き。変更行はハイライト、[既定に戻す] で復帰、[保存] で全行を一括 PUT。各行にはさらに**候補リスト**（チップ + 追加/削除）があり、既定値と合わせて 2 件以上にすると生成フォーム / API が実行ごとに選べるようになる。既定値入力・候補追加入力はどちらも `/api/options` の `model_files`（`"<class_type>.<field>"` ごとの ComfyUI ファイル一覧。LoRA は従来の `lora_files` で補う）があれば datalist で補完。さらに各行には**不足モデルのダウンロード**の UI がある: 値が `model_files` の該当リストに無ければ**未検出**バッジ、URL 入力欄（`model_download_urls`。キーはファイル名なので同じファイルを使う行では共有）と [DL] ボタン、進行中は進捗バーと取得済みバイト数（WS の `model_download` を購読）。**取得元 URL の登録・編集は環境や `COMFY_MODELS_DIR` の有無に関係なく常に使える**（いま繋いでいない環境ぶんの URL も先に登録しておけるため）。[DL] と、タブ上部の **[全DL]**（未検出かつ URL 登録済みを一括開始）は `comfy_cloud` 以外で常に出し、落とせない事情は押したときの 400 で知らせる（ローカル選択中は `dir-status` の理由をタブ上部の警告にも出す、§3.3）。**未検出バッジは「いま繋いでいる環境」を編集しているときだけ**出す（`model_files` は接続中の ComfyUI のものなので、他の環境の在庫は分からない）。バッジが出ない行でも [取得元 URL] を開けば [URL保存] と [DL] が並ぶ。**検出済みの行**でも取得元 URL は登録できる（手元には在るが RunPod の Pod には無いモデルを、あとで [DL] / [全DL] で入れるため）: 表がうるさくならないよう既定は畳んでおき、[▸ 取得元 URL]（登録済みならアクセント色 + ✓）を押すと URL 欄と [URL保存] が開く。[URL保存] はダウンロードせず `model_download_urls` だけを PUT し、**空欄で保存するとそのファイル名のキーを消す**（登録解除）
- **実行ごとのモデル切り替え**: 選択中のワークフローに候補が 2 件以上あるスロットがあれば、そのワークフローセレクトの直下に「使用モデル: <ノード名>」のセレクトを出す（画像 / 動画 / 音声それぞれのセクション内）。候補が 1 件以下のスロットは何も出さない。送信時は**走らせるワークフローのぶんだけ**、かつ既定値と違う選択だけを `params.model_overrides` に載せる（§3.3）
- **投入時の NSFW 指定**: [実行] の直上に「🫣 NSFW として投入（オフなら生成後に自動判定）」のチェックボックスを置く。**チェックしたときだけ** `JobCreate.nsfw = true`（manual 扱いで自動判定をスキップ）を送り、オフのままなら何も送らず従来どおり生成後の自動判定に任せる（既定オフ）
- **再実行は 2 通り**: [再実行（シード再抽選）] と [再実行（同じシード）]（`JobRerun.randomize_seed`）を結果ペイン・詳細ドロワーの両方に並べる
- **続き生成は上書きフォーム**: [続きを生成] は `ContinueModal` を開く。全項目の既定が「元ジョブを引き継ぐ」（空欄・プレースホルダに元の値）で、[そのまま続き生成] は空ボディ、[この設定で続き生成] は**埋めた欄だけ**を `JobContinue` として送る。動画ワークフロー・プロンプト・ネガティブ・アスペクト比・メガピクセル・尺・fps・seed・リファレンス音声/最後のフレーム/参照動画のパス・使用モデル（切り替え先ワークフローのスロット）を並べる
- ヘッダーの NSFW 表示トグルは `sessionStorage` に保持する（既定オフ。タブを開き直すと必ずオフに戻る）
- **ドラマスタジオのタブは 概要 / 脚本 / World Bible / 制作 / 編集**（`StudioView` の `StudioTab`）。
  一番右の**編集**（`EditView`、§7.3）は、話を選んで [タイムラインを作成] を押すとその話の採用
  テイクを並べたタイムラインができ、上に**プレビュー**（`PreviewMonitor`）、下に**V1 のタイムライン**
  （`TimelinePane`）、右に**クリップの詳細**（`ClipInspector`）と**書き出し**（`ExportPanel`）が並ぶ。
  操作は「本体ドラッグ = 並べ替え（リップル）」「端ドラッグ = トリム」「Ctrl+ホイール = ズーム
  （1 秒あたり 20〜200px）」「ルーラーのクリック / ドラッグ = スクラブ」「Delete = 削除」
  「分割 = 再生ヘッドの位置で 2 つに割る」「Ctrl+Z / Ctrl+Shift+Z = やり直し」。
  メディア欠落のクリップは赤系で [メディア欠落] と出し、保存状態は [保存済み] / [未保存の変更] /
  [保存中…] / [保存に失敗] のバッジに出す
- **外部エージェントの操作がそのまま画面に出る**（§9 の WS フレーム）: 外部 API で脚本や素材が
  書き換われば `type: "studio"` が飛んで開いているスタジオが読み直し、生成フォームの下書きが
  書き換われば `type: "form"` がフォームへ流し込まれ（§7.5）、`type: "ui"` の `navigate` は
  ブラウザの表示そのものを 生成 / スタジオ / 設定 へ切り替える。人が同じ画面を触っていても
  壊れないよう、書き込みには `base_revision`（スタジオは §7.4、フォームは §7.5）がある

---

## 9. 技術スタック（提案）

| レイヤ | 技術 | 理由 |
|---|---|---|
| バックエンド | Python 3.12 + FastAPI + uvicorn | ComfyUI/Grok クライアントとも Python 資産が使える。WS 中継が容易 |
| フロント | React + Vite + Tailwind | SPA 1 枚で十分 |
| DB | SQLite (aiosqlite) | ローカル単体運用 |
| 動画処理 | ffmpeg (subprocess) | ラストフレーム抽出・サムネ生成 |
| ジョブ管理 | アプリ内 asyncio キュー | 外部依存を増やさない |

- **PWA**: `vite-plugin-pwa`（`registerType: "autoUpdate"`）でインストール可能にする。マニフェスト（`Karakuri Media Studio` / `standalone` / テーマ色 `#0a0c11`）とアイコンは `frontend/public/`、Service Worker のプリキャッシュは**ビルド成果物（JS/CSS/HTML/アイコン/Inter）だけ**に限る
- 生成物・素材（`/api` `/outputs` `/assets` `/library`）は動画など大きなファイルを含むので **Service Worker では一切キャッシュせず、SPA フォールバック（`navigateFallbackDenylist`）からも除外する**。バックエンド側も `dist/` の実ファイル（`sw.js` / `manifest.webmanifest` 等）を index.html フォールバックより優先して返す

### バックエンド API（概要）

```
GET  /api/health                 … ComfyUI/Grok 疎通チェック
GET  /api/options                … 画像/動画/音声ワークフロー一覧（必要入力・露出しているつまみ・秒数レンジつき）・アスペクト比・LoRA一覧・アセット一覧・ライブラリ一覧（library, §7.2）・実行時に選べるモデルスロット（model_slots）と ComfyUI のモデルファイル一覧（model_files）
GET/POST/PUT/DELETE /api/loras   … アプリ内 LoRA 登録リストの CRUD（GET は `?target=` でその接続先のもの + 共通行、POST は `comfy_target` で紐づけ先を指定、§5）
GET  /api/library                … ライブラリ検索（kind / category / q / tag / limit / offset → items + total + tags、§7.2）
POST /api/library/{kind}         … ファイルをアップロードして登録
POST /api/library/from-job       … ジョブの出力（image / last_frame / video / audio）を登録
POST /api/library/sheet          … 画像素材を 1 枚のリファレンスシートに合成して登録（item_ids の順に配置、§7.2）
POST /api/library/{id}/key       … 素材の背景を抜いて透過 PNG の新しい素材にする（スプライト、§7.2）
POST /api/library/key-from-job   … ジョブの生成画像を直接抜いてスプライトにする（§7.2）
PATCH  /api/library/{id}         … 表示名 / NSFW フラグ / タグ / カテゴリの変更
DELETE /api/library/{id}         … 登録解除（ファイルも削除）
GET  /api/images/text/fonts      … インストール済みの書体一覧（§7.2）
POST /api/images/text            … フォントで組んだ文字を PNG にして登録（§7.2）
POST /api/videos/contact-sheet   … 動画のコマを 1 枚のグリッド画像にして登録（§7.2）
GET  /api/models                 … 全ワークフローのモデルファイル名一覧（既定値+現在値+候補リスト、キーは workflow_id でスコープ。`?target=` でその接続先のもの、省略時は現在の接続先）
PUT  /api/models                 … モデルファイル名の上書きと候補リストの保存（既定値と同値/空は削除、候補が空のキーは削除。`choices` 省略時は保存済みの候補を保持。`target` の環境だけを書き換える）
GET  /api/models/dir-status      … ローカルの models ディレクトリの状態（configured / exists / writable / path、§3.3）
GET  /api/models/downloads       … 進行中と直近のモデルダウンロード一覧
POST /api/models/download        … 不足モデルのダウンロード開始（filename / url / subfolder / target。local は自前・runpod は Pod の API へ・comfy_cloud は 400。保存先を検証して 400、二重実行は 409。進捗は WS、§3.3）
POST /api/models/download-all    … 未検出かつ取得元 URL 登録済みを一括開始（target。started / missing_urls / errors を返す、§3.3）
POST /api/chat/sessions          … チャット開始（フォーム現在値をコンテキストとして渡す。`video_workflow` / `image_workflow` / `audio_workflow` を含む）
POST /api/chat/sessions/{id}/messages … 発言送信 → Grok 応答（質問 or 最終JSON案）を返す。継続セッションで回し、停止されたときは 409
POST /api/chat/sessions/{id}/stop … ⏹ 走っている Grok のターンを止める（次の発言は履歴を組み直した新しい会話で続く）
GET  /api/chat/sessions/{id}     … 履歴取得
POST /api/jobs                   … ジョブ作成・実行（プロンプト確定値+パラメータ。`selects` で選択式フィールド §3.1、`model_overrides` でそのジョブだけモデルを差し替え可 §3.3、`reference_images` / `reference_videos` / `reference_audios` でマルチモーダル参照 §3.1）
GET  /api/jobs?limit=…           … 履歴一覧
GET  /api/jobs/{id}              … 詳細
POST /api/jobs/{id}/rerun        … 再実行（seed 変更オプション）
POST /api/jobs/{id}/continue     … ラストフレームを開始フレームに新規ジョブ（`video_workflow` / `end_image` / `reference_video` / `model_overrides` 等を差分指定可。開始フレームを取れないワークフローは既定に戻す）
DELETE /api/jobs/{id}
POST /api/assets/audio|image|video … アセットアップロード（video は参照動画用）
GET  /api/ui/generate-form       … 生成フォームの下書き（値 + revision、§7.5）
PUT  /api/ui/generate-form       … 下書きの保存（`base_revision` を省くと強制上書き。保存後に WS `type: "form"`）

… 編集タブ（タイムライン。プレフィックスはスタジオと同じ /api/studio、§7.3）
POST /api/studio/projects/{id}/timelines … タイムライン作成（`episode_id` を送ると自動配置つき初期化）
GET  /api/studio/projects/{id}/timelines … 一覧（中身は含めない）
GET  /api/studio/timelines/{id}  … トラック・クリップ込みのフル EDL（`video_url` / `source_duration_ms` / `missing` 解決済み）
PATCH  /api/studio/timelines/{id} … 名前・規格（fps / width / height）の変更
DELETE /api/studio/timelines/{id} … トラック・クリップ・書き出しの記録ごと削除（mp4 は残る）
PUT  /api/studio/timelines/{id}/clips  … クリップ全置換（自動保存の受け口。重なり / in>=out / 尺と速度の不整合 / 繋ぎの置けない境界は 400）
POST /api/studio/timelines/{id}/tracks … トラック追加（音声 A1… / 字幕 T1。映像は 400）
PATCH  /api/studio/timelines/{id}/tracks/{track_id} … 名前・ミュート・ロック
DELETE /api/studio/timelines/{id}/tracks/{track_id} … トラック削除（中のクリップごと。V1 は 400）
GET  /api/studio/projects/{id}/media?kind=video|audio|image&limit=&offset= … 素材ビン（テイク / ライブラリ / 単発ジョブ / 作品の素材）
POST /api/studio/timelines/{id}/generate-subtitles … 台詞からテロップを一括生成（字幕トラックは置き換え）
GET  /api/studio/timelines/{id}/sync-preview … 作成後に起きた脚本の差分（added / retaken / removed）
POST /api/studio/timelines/{id}/sync … 差分のうち選んだものだけ反映
GET  /api/studio/timelines/{id}/missing … メディア欠落クリップと同じカットの差し替え候補
POST /api/studio/timelines/{id}/missing/resolve … 別テイクへ差し替え / 欠落クリップの一括削除
POST /api/studio/timelines/{id}/export … 書き出し開始（**202 即受付**。`preset` / `fit` / `loudnorm` 指定可。走っているものがあれば 409、メディア欠落が残っていれば 400）
GET  /api/studio/timelines/{id}/exports … 書き出し履歴（新しい順、`output_url` つき）
POST /api/studio/exports/{id}/save-to-library … 完成 mp4 を library/video/ へコピーして登録

GET  /library/…                  … 静的配信（ライブラリの素材、§7.2）
WS   /api/ws                     … 進捗と更新の配信。種別は `type: "job"`（ジョブの状態と進捗）/ `"chat"`（プロンプト作成チャットのターン）/ `"library"`（自動タグの書き戻し）/ `"model_download"`（不足モデルの取得）/ `"timeline_export"`（書き出し）/ **`"studio"`**（外部 API による脚本・素材の更新。`project_id` / `entity` / `id` / `op` だけを流し、正本は DB）/ **`"form"`**（生成フォームの下書き。`revision` / `updated_by` / `values`、§7.5）/ **`"ui"`**（`op: "navigate"` で画面を切り替えさせる）。配信は `app/ws.py` の `publish*` 関数で、**どれも例外を投げない**（通知の失敗で生成や編集を壊さない）
GET  /outputs/…                  … 静的配信（画像/動画/音声）
```

### 外部公開 API（`/api/v1`）

内部 API（`/api/…`）とは別系統に、**外部エージェント向け**の API を持つ。制作を回すのは
このアプリの中の機構ではなく、**外から `/api/v1` を叩くコーディングエージェント**
（Claude Code / Codex / Cursor CLI など）で、その段取りは
[`.agents/skills/karakuri-studio/SKILL.md`](../.agents/skills/karakuri-studio/SKILL.md) に置いてある
（`AGENTS.md` / `CLAUDE.md` からリンク）。

認証は設定 `external_api_key` と突き合わせる `X-API-Key` ヘッダ。キーが空のあいだは
`/api/v1` ごと 404 を返す（既定は無効）。実体は `app.studio` / `app.jobs` /
`app.timeline` を呼ぶだけの薄いラッパー（`backend/app/routers/external.py`）で、内部 API と
UI には影響しない。設計・公開範囲の詳細・Cloudflare 越しの公開手順は
[`docs/EXTERNAL-API.md`](EXTERNAL-API.md)。

公開しているのは、人が UI でできることのほぼ全部:

- 脚本（プロジェクト / 話 / 場 / カット / 素材）の CRUD と並べ替え、投入前の
  `GET /api/v1/shots/{id}/prompt-preview`（実際に投入されるプロンプトとワークフロー）
- 生成（`POST /api/v1/shots/{id}/render` と Take の採否／汎用ジョブの
  `POST /api/v1/jobs`・`cancel`・`rerun`・`continue`）、ライブラリ、
  編集タブ一式（タイムライン / トラック / クリップ / 書き出し）
- 編集履歴（`revisions` の一覧 / `diff` / `restore`、§7.4）と画面の操作
  （`ui/generate-form` / `ui/navigate`、§7.5）、Remotion の composition 一覧（§5.2）
- **削除はプロジェクト以外**。プロジェクトはリビジョンごとカスケードで消えて復元
  できないので、外には出さず人に頼む運用にする

エージェントが最初に読むための参照系が 4 本ある:

- `GET /api/v1/openapi.json` … **公開範囲の正本**。アプリ全体のスキーマから `/api/v1` の
  パスと、そこから `$ref` で辿れるスキーマだけを抜き出した縮小版
- `GET /api/v1/capabilities` … いまの接続先でラテント連続性 / アップスケールが使えるか
- `GET /api/v1/options` … 生成フォームと同じ選択肢（`comfy_url` は伏せる）
- `GET /api/v1/prompt-guide` / `prompt-examples` … **脚本ドラフト作成ガイド**と H3 の実例。
  本文は `backend/app/drafting_guide.py` が既存の定数から組み立てるので静的コピーを持たない:
  尺は `app.studio.SHOT_DURATION_MIN/MAX`、参照素材の上限は
  `app.workflows.MINIMAX_H3_REFERENCE_*`、H3 の書き方は `app.prompts.MINIMAX_H3_GUIDE_BODY`、
  実例は `app.h3_examples` から代表を選抜。フィールドがモデルにどう届くかの
  正本は `app.studio.compose_prompt`（変えたらガイドも直す）

ラッパーでない独自のエンドポイントは `POST /api/v1/stories`（話 1 本＝話 → 場 → Shot の
一括投入。1 トランザクションで全部作れたか・全く作らなかったかの二択）。

**暴走ガード**は「生成（ジョブ + Take）」と「書き出し」の**2 つのプール**で、それぞれが
`external_max_pending_takes`（既定 20 / 0 = 無制限）に達しているあいだは 429。数えてから
投入するまでを別々の錠で括る（生成は `studio.PENDING_JOBS_LOCK`、書き出しは
`external._EXPORTS_LOCK`）。**内部 API（UI からの操作）には掛けない**。

### ディレクトリ構成

```
backend/            FastAPI アプリ
  app/routers/      health / settings / loras / models_config / model_download / library /
                    assets / options / chat / grok / jobs / studio / timelines / ui /
                    external / push
  app/comfy.py      ComfyUI クライアント（/object_info, /upload/image, /prompt, /ws, /history, /view）
  app/workflows.py  ワークフロー登録簿と注入マニフェスト（ノード ID 直指定）+ プロンプト用カタログ
  app/workflow.py   テンプレートへのパラメータ注入・LoRA チェーン動的注入・解像度計算
  app/grok.py       CLI 呼び出し（LLM クライアントは差し替え可能な抽象化）
  app/llm_cli.py    CLI アダプタ（grok / claude / codex / cursor の起動と契約の渡し方、§4.1）
  app/grok_session.py CLI の継続セッション（ACP / ワンショット）のホスト
  app/chat_agent.py 相談チャットの継続セッション・活動通知・停止（§4.3）
  app/prompts.py    プロンプト作成チャットのシステムプロンプト
  app/drafting_guide.py  外部エージェント向け脚本ドラフト作成ガイド（GET /api/v1/prompt-guide）
  app/jobs.py       asyncio ジョブキューと実行、成果物取得・ラストフレーム抽出
  app/studio.py     ドラマスタジオ（脚本 / 素材 / Take / 編集履歴 §7.4）
  app/remotion.py   Remotion プロジェクト（同梱の remotion/）の composition 一覧とレンダリング（§5.2）
  app/audio_analysis.py        音源解析ワーカーの起動と進捗の中継（別 venv の python で回す、§5.2）
  app/audio_analysis_worker.py 解析の本体（歌詞アライン / 書き起こし / onset / ビート / 無音）。app に依存しない単独スクリプト
  app/ui_state.py   生成フォームの下書きの共有（§7.5）
  app/ws.py         ブラウザへの配信（job / chat / studio / form / ui / …）
  app/library.py    ライブラリ（取っておく素材）の保存・目録
  app/sprites.py    透過キー（floodfill 方式のルミナンスキー / クロマキー / rembg、§7.2）
  app/textimage.py  フォント画像の生成と書体の一覧（§7.2）
  app/contact_sheet.py  動画のコマ抜きとグリッド合成（§7.2）
  app/media_ref.py  ジョブ / 素材 / 書き出し / パスの指し方を実ファイルに解決する（§7.2）
  app/timeline.py   編集タブ: タイムライン（EDL）の CRUD と書き出しの管理（§7.3）
  app/timeline_export.py  EDL → ffmpeg コマンドの組み立て（純関数）と実行・進捗
  app/timeline_subtitles.py  テロップ → ASS の組み立てと台詞の割り付け（純関数、§7.3）
  app/autotag.py    ライブラリ素材の日本語タグ・表示名の自動生成（Grok）
  app/nsfw.py       ジョブ / セッションの NSFW 判定
  app/model_download.py  不足モデルのダウンロード（models ディレクトリへ直接保存）
  app/model_sources.py   取得元 URL → 配布ページ URL（§3.3）
  tests/            pytest
frontend/           React + Vite + Tailwind の SPA（ビルド成果物は frontend/dist）
  public/           PWA のアイコン（icon.svg / pwa-192x192.png / pwa-512x512.png /
                    maskable-512x512.png / apple-touch-icon.png）
  src/components/   GenerateForm / AudioFields / ResultPane / HistoryGallery / ChatModal /
                    SettingsPage / studio/（ドラマスタジオと編集タブ）
docs/SPEC.md        仕様書
docs/EXTERNAL-API.md  外部公開 API（/api/v1）の設計
.agents/skills/karakuri-studio/  外部エージェント向け SKILL（AGENTS.md / CLAUDE.md からリンク）
remotion/           同梱の Remotion プロジェクト（composition は remotion/src/。§5.2）
workflow/           ComfyUI ワークフロー（API フォーマット）テンプレート ※実行の正
  image/            krea2/ anima/ z-image/ qwen-image/（モデルファミリーごと）
  video/minimax-h3/ minimax_h3_t2v / minimax_h3_i2v / minimax_h3_r2v（音声つき）
                    minimax_h3_r2v_context（連続カット・Motion Context 版）
                    minimax_h3_t2v_save / _i2v_save / _r2v_save（AV ラテント保存つき）
                    …_turbo（4 ステップ版）/ …_opt（蒸留 LoRA なしの 20 ステップ最適化版）
                    ※ turbo / opt は上の 7 通り（素 3 / _save 3 / _r2v_context）すべてに揃えてある
  audio/            minimax_music_3.json / stable_audio_3_medium_base.json
app.db              SQLite（jobs / loras / library / chat_sessions / studio_*（脚本・Take・
                    編集履歴）/ timeline_* / ui_state）
outputs/            生成物（/outputs で静的配信）
assets/             アップロードした画像・音声・参照動画・LoRA サンプル（/assets で静的配信）
library/            ライブラリ（取っておいた素材。image/ video/ audio/、/library で静的配信）
runtime/            config.json / grok 作業ディレクトリ（プロンプト用）/
                    chat-sessions/（チャットごとの cwd）/ remotion/（props の一時 JSON）/
                    audio-analysis/（解析に渡す歌詞の一時ファイル）
```

---

## 10. 制約・注意事項

1. **Grok Build CLI 依存**: `grok` CLI のインストールとサブスクリプションでのサインインが前提。CLI はベータ段階のため出力形式・挙動が変わる可能性があり、LLM クライアントは抽象化して公式 API / ローカル LLM に差し替え可能に設計する。NSFW プロンプト生成を Grok が拒否した場合のリトライ指示（システムプロンプト側の調整）とエラー表示も用意する
2. **コンテンツ**: 本アプリは成人向けコンテンツをローカル生成する個人利用ツール。生成物・プロンプトはすべてローカル保存のみで外部送信しない。LoRA は実在人物の無断利用を行わないこと（利用者責任）
3. **ComfyUI 依存**: ResolutionSelector / ComfySwitchNode / CustomCombo / MiniMaxH3 系 / ComfyMath / ResizeImage 系 / ResizeAndPadImage / MoGe 系 / LoadVideo 等の custom nodes が導入済みである前提。起動時と `/api/health` で `/object_info` に対し **`workflow/` 配下の全テンプレートに含まれる class_type** の存在チェックを行い、不足があれば UI に警告する（どのワークフローを使うか実行前には分からないため、集合は全テンプレート横断）。同時にマニフェストとテンプレートの整合性も検証する（§3.0）
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
13. 音声生成: **独立モード**（画像・動画とは連結しない）。MiniMax Music 3 / Stable Audio 3（ComfyUI）、出力は mp3（§2.4）
14. 未使用項目: **グレーアウトではなく非表示**（値はフォーム状態として保持）（§8）

決定済み（v0.4 — 制作の主体を外へ出す）:

15. **内蔵エージェントモードとキャンバスは撤去**。制作を回すのは外から `/api/v1` を叩く
    コーディングエージェント（SKILL = `.agents/skills/karakuri-studio/`）に一本化する。
    アプリ内に残る LLM 用途は**プロンプト作成チャット・英訳・自動タグ・ヘルスチェック**の
    4 つだけ（§4.1）
16. **外部 API は「人が UI でできること」とほぼ同じ範囲**まで広げる。ただし
    **削除はプロジェクト以外**、暴走ガードは「生成」「書き出し」の 2 プール（§9）
17. **人と外部エージェントの同時編集は履歴で守る**: 1 操作 = 1 リビジョン、差分・部分復元・
    復元前の自動スナップショット、`base_revision` の楽観ロック（§7.4）
18. **画面はリアルタイムに追随する**: WS の `studio` / `form` / `ui` フレームと `ui_state`
    による生成フォームの双方向同期（§7.5 / §8）
19. **Remotion 連携**: React で組んだ動画も 1 つの生成経路（`mode: "remotion"`）として扱い、
    プロジェクト本体は `remotion/` に同梱。ただし Remotion のライセンスの都合で
    **既定 OFF**（設定 `remotion_enabled`。§5.2）

残課題: なし（実装着手可能）
