# IC-LoRA 統合メモ（旧 video-gen.json 時代の調査記録）

> **これは歴史的な記録**です。単一の合体グラフ `video-gen.json` は廃止され、現行の実行の正は
> `workflow/` 配下の分離テンプレート（画像: krea2_turbo / 動画: LTX 2.3 の 7 種）と
> `backend/app/workflows.py` の注入マニフェストです（SPEC §2 / §3）。IC-LoRA は
> `ltx2_3_ic_lora_image`（Ingredients）と `ltx2_3_ic_lora_motion`（Union Control）に分かれています。
> 以下のノード ID は旧グラフのもので現行テンプレートとは対応しません。
> **調査で判明したパラメータの意味・強度の勘所・既知の問題**を残すために保存しています。

LTX-2.3 の IC-LoRA 機構（画像参照 = Ingredients / 動画参照 = Union Control）を
当時の `video-gen.json` に統合した際の変更点と、`backend/app/workflow.py` 側で必要になる
注入パラメータをまとめたもの。

参照元テンプレート（Comfy 公式）:

- `template_ltx2_3_ic_lora_ingredients`（サブグラフ「LTX-2.3 IC-LoRA Video Generation」）
- `video_ltx2_3_ic_lora`（サブグラフ「First-Last-Frame to Video (LTX-2.3)」+「Video Depth Estimation (MoGe)」）

---

## 1. モード定義（確定仕様）

| パターン | スタート画像 | identity 系 | 追加入力 |
|---|---|---|---|
| 1 | あり | ID LoRA (talkvid) + 音声リップシンク | リファレンス音声 |
| 2 | あり | 画像参照（Ingredients IC-LoRA） | リファレンスシート画像 |
| 3 | あり | 動画参照（Union IC-LoRA + MoGe 深度） | 参照動画 |
| 4 | なし | 画像参照 | リファレンスシート画像 |
| 5 | なし | 動画参照 | 参照動画 |

ID LoRA / 画像参照 / 動画参照 は排他。音声リップシンク（`432` LoadAudio +
`433:349` LTXVReferenceAudio）はパターン 1 専用。

---

## 2. 追加ノード一覧（すべて `433:` プレフィックス）

新ノードはすべて動画サブグラフ側なので、モード C（`image_only`）の
`_drop_prefix(wf, "433:")` で自動的に削除される。**この命名は必須**
（`7xx` 等の別プレフィックスにするとモード C でリンク切れになる）。

### 2.1 モード切替

| ID | class_type | 役割 |
|---|---|---|
| `433:700` | PrimitiveInt | **identity_mode**。1=id_lora / 2=image_ref / 3=video_ref。既定 `1` |
| `433:701` | ComfyMathExpression | `a > 1.5` → BOOL（**出力スロット 2**）= 参照モードか |
| `433:702` | ComfyMathExpression | `a > 2.5` → BOOL（スロット 2）= 動画参照か |

`ComfyMathExpression` の出力は `[FLOAT, INT, BOOLEAN]`（FLOAT=0 / INT=1 / BOOL=2）。
`ComfySwitchNode` の `switch` 入力には `["433:701", 2]` のように **スロット 2** を渡す。

### 2.2 IC-LoRA モデル

| ID | class_type | 役割 |
|---|---|---|
| `433:710` | LoraLoaderModelOnly | `ltx-2.3-22b-ic-lora-ingredients-0.9.safetensors`, strength 1.0, model ← `433:427`（distil LoRA 出力） |
| `433:711` | LoraLoaderModelOnly | `ltx-2.3-22b-ic-lora-union-control-ref0.5.safetensors`, strength 1.0, model ← `433:427` |
| `433:712` | ComfySwitchNode | `433:702` で 710/711 を選択 |
| `433:713` | GetICLoRAParameters | `433:712` から reference_downscale_factor 等を取得 → AddGuide へ |

キャラ LoRA / ID LoRA（`433:346`）は IC-LoRA チェーンには入らない（排他仕様どおり）。

### 2.3 画像参照（リファレンスシート）前処理

| ID | class_type | 役割 |
|---|---|---|
| `433:720` | LoadImage | リファレンスシート（既定は placeholder `reference_sheet.png`） |
| `433:721` | RepeatImageBatch | シートをフレーム数ぶん複製（`amount` ← `["433:329", 1]` = 8n+1 フレーム数） |
| `433:722` | ResizeAndPadImage | **1 段目の解像度**（`["433:419",1]` × `["433:418",1]` = W/2 × H/2）へリサイズ + 黒パディング |

### 2.4 動画参照（MoGe 深度）前処理

| ID | class_type | 役割 |
|---|---|---|
| `433:730` | LoadVideo | 参照動画（既定は placeholder `reference_video.mp4`） |
| `433:731` | GetVideoComponents | → images(0) / audio(1) / fps(2) |
| `433:732` | ImageFromBatch | 先頭からフレーム数ぶん切り出し（不足時は自動クランプ） |
| `433:733` | ResizeAndPadImage | 1 段目の解像度へリサイズ |
| `433:734` | LoadMoGeModel | `moge_2_vitl_normal_fp16.safetensors` |
| `433:735` | MoGeInference | resolution_level 9 / fov 0 / batch_size 4 |
| `433:736` | MoGeRender | `output = "depth"` |

`433:740` (ComfySwitchNode) が `433:702` で `433:722` / `433:736` を選び、
これが **control_images**（IC-LoRA ガイド）になる。

### 2.5 ガイド注入と切替

| ID | class_type | 役割 |
|---|---|---|
| `433:750` | LTXVAddGuide | positive/negative ← `433:408`、vae ← `433:428`(2)、latent ← `433:416`、image ← `433:740`、`frame_idx=0` / `strength=1.0`、iclora_parameters ← `433:713` |
| `433:751` | ComfySwitchNode | 1 段目 latent: `433:416`（OFF） / `433:750`(2)（ON） → `433:425.video_latent` |
| `433:752` | ComfySwitchNode | positive: `433:408`(0) / `433:750`(0) → `433:414` と `433:401` |
| `433:753` | ComfySwitchNode | negative: `433:408`(1) / `433:750`(1) → 同上 |
| `433:754` | ComfySwitchNode | 2 段目入力 latent: `433:410`(0)（OFF） / `433:401`(2)（ON = CropGuides でガイドフレーム除去後） → `433:402.samples` |
| `433:755` | ComfySwitchNode | 1 段目モデル: `433:349`(0)（ID LoRA + 音声）/ `433:712`（IC-LoRA） → `433:414.model` |
| `433:756` | ComfySwitchNode | positive ソース: `433:349`(1) / `433:407`（素の CLIPTextEncode） → `433:408.positive` |
| `433:757` | ComfySwitchNode | negative ソース: `433:349`(2) / `433:413` → `433:408.negative` |

IC-LoRA は **1 段目（半解像度）のみ**に適用する。2 段目（`433:399` / `433:427`）は従来どおり
distil のみで、ガイドフレームは `433:401` LTXVCropGuides で除去した latent（`433:754` 経由）を
アップスケールする。

### 2.6 スタート画像バイパス（パターン 4 / 5）

| ID | class_type | 役割 |
|---|---|---|
| `433:760` | PrimitiveBoolean | **no_start_image**。既定 `false`。`433:416.bypass` / `433:403.bypass` にリンク |
| `433:761` | EmptyImage | 512×512 のダミー画像（bypass 時に使用） |
| `433:762` | ComfySwitchNode | `433:334`（前処理済みスタート画像） / `433:761` を選択 → `433:416.image` と `433:403.image` |

### 2.7 既存ノードの配線変更（これ以外の変更なし）

- `433:408.positive/negative` → `433:756` / `433:757`
- `433:414.model/positive/negative` → `433:755` / `433:752` / `433:753`
- `433:401.positive/negative` → `433:752` / `433:753`
- `433:425.video_latent` → `433:751`
- `433:402.samples` → `433:754`
- `433:416` / `433:403` の `image` → `433:762`、`bypass` → `433:760`（widget から link へ変更）

**identity_mode = 1 かつ no_start_image = false のとき、すべての Switch は従来の入力を素通しする**
ため、実行結果はこれまでと同一（実機で確認済み、後述）。

---

## 3. workflow.py が注入すべき新パラメータ

| パラメータ | ノード / フィールド | 値 |
|---|---|---|
| `identity_mode` | `433:700.value` | 1 / 2 / 3 |
| `no_start_image` | `433:760.value` | bool（パターン 4/5 で true） |
| `reference_image_name` | `433:720.image` | アップロード済みファイル名 |
| `reference_video_name` | `433:730.file` | アップロード済みファイル名（`/upload/image` で input に配置） |
| `ic_lora_strength`（画像参照） | `433:710.strength_model` | 既定 1.0 |
| `ic_lora_strength`（動画参照） | `433:711.strength_model` | 既定 1.0 |
| `ic_guide_strength` | `433:750.strength` | 既定 1.0（`frame_idx` は 0 固定） |
| MoGe 品質 | `433:735.resolution_level` / `batch_size` | 既定 9 / 4（OOM 時に下げる） |

### 3.1 モード別プルーニング（推奨）

`ComfySwitchNode` が遅延評価かどうかに依存しない実装にするため、
使わないブランチは JSON から削除して Switch の両入力を残る側に向けるのが安全
（スモーク検証もこの形で実施した）。

- **パターン 1**: `433:710`–`433:713`, `433:720`–`433:722`, `433:730`–`433:736`, `433:740`,
  `433:750`–`433:754` を削除し、`433:425.video_latent = ["433:416",0]`,
  `433:402.samples = ["433:410",0]`, `433:414/433:401` の pos/neg を `433:408` 直結、
  `433:414.model = ["433:349",0]` に戻す（＝現行グラフと完全一致）。
  そのまま残しても実機では成功したが、無駄な MoGe / LoRA ロードを避けられる。
- **パターン 2 / 4**: 動画参照ノード（`433:730`–`433:736`）を削除し `433:740` の両入力を `433:722` に。
  さらに `432`（LoadAudio）, `433:346`（ID LoRA）, `433:349`（LTXVReferenceAudio）を削除し、
  `433:755` の両入力を `433:712`、`433:756/757` の両入力を `433:407` / `433:413` に。
- **パターン 3 / 5**: 画像参照ノード（`433:720`–`433:722`）を削除し `433:740` の両入力を `433:736` に。以下同上。
- **パターン 4 / 5**: `433:762` の両入力を `433:761` に向け、スタート画像チェーン
  （`435`/`365:*`, `433:431`, `433:417`, `433:334`）を削除。`433:760.value = true`。
- **モード C（image_only）**: 既存の `_drop_prefix(wf, "433:")` で新ノードもすべて消える（対応不要）。

### 3.2 既知の影響（次ステップで対応が必要）

- `backend/tests/test_workflow.py::test_model_fields_extraction` が失敗する。
  `model_fields()` が `LoraLoaderModelOnly.lora_name` を拾うため、
  **`433:710.lora_name` と `433:711.lora_name` が新たに設定可能なモデルファイル項目として増える**。
  期待値セットに 2 件追加すればよい（IC-LoRA のファイル名も環境依存なので設定可能なままにするのが妥当）。
  他 193 件のテストは pass。

---

## 4. 実機スモーク検証（Comfy Cloud）

条件: duration 2 秒 / fps 25 / frames 49（8n+1）/ `16:9` 0.25MP・multiple 64 →
出力 640×384、1 段目 320×192。

**重要**: 検証に使った Comfy Cloud アカウントには本番のモデル
（`sexgodPinkcherryLTX23_v16bDev.safetensors`, `ltx_2.3_22b_distilled_1.1_lora_dynamic_fro09_avg_rank_111_bf16.safetensors`,
画像側 `redcraft23INT8INT4FP8_30Krea2` 等）が存在しないため、
`model_overrides` 相当で以下に差し替え、画像生成側（`365:*`）は i2v モード同様に削除して実行した。
**`video-gen.json` 内のモデル名は一切変更していない。**

- `433:428 / 433:335 / 433:429 .ckpt_name` → `ltx-2.3-22b-dev-fp8.safetensors`
- `433:427.lora_name` → `ltx-2.3-22b-distilled-1.1_lora-dynamic_fro09_avg_rank_111_bf16.safetensors`
- スタート画像 / リファレンスシート → 既存入力 `bedroom.mp4` のフレーム
- リファレンス音声 → `LTXVEmptyLatentAudio` を `LTXVAudioVAEDecode` した無音（後述の理由）

| パターン | 結果 | 備考 |
|---|---|---|
| 1（ID LoRA + 音声） | **成功**（動画出力あり） | 参照ブランチを残したまま identity_mode=1 で実行。回帰なし |
| 4（画像参照・スタート画像なし） | **成功**（動画出力あり） | Ingredients IC-LoRA + bypass=true。公式テンプレートと同構成 |
| 3（動画参照・スタート画像あり） | **失敗**（原因未特定） | 下記参照 |
| 2 / 5 | 未実行（実行回数上限） | 2 は 4 と、5 は 3 と同経路 |

### 4.1 判明した注意点

- IC-LoRA のファイル名は Cloud 上に**そのまま存在する**:
  `ltx-2.3-22b-ic-lora-ingredients-0.9.safetensors` /
  `ltx-2.3-22b-ic-lora-union-control-ref0.5.safetensors`。
  MoGe モデルも `moge_2_vitl_normal_fp16.safetensors` で存在。
- `LoadAudio` に mp4 を渡すとジョブが失敗する（音声は wav/mp3 を使うこと）。
  パターン 1 の初回失敗はこれが原因だった（IC-LoRA 統合とは無関係）。
- Comfy Cloud MCP のジョブ失敗は `error_type: unknown` としか返らずログが取れない。
  原因特定には「疑わしいノードだけの小さなグラフ」を投げる二分探索が必要。
- MoGe ブランチ単体（LoadVideo → GetVideoComponents → ImageFromBatch(49) →
  ResizeAndPadImage(320×192) → MoGeInference → MoGeRender(depth)）は**単体では成功**する。
  したがってパターン 3 の失敗は前処理ではなく、
  **Union IC-LoRA（reference_downscale_factor 0.5）と LTXVAddGuide の組み合わせ**、
  または MoGe と 22B モデル同居時のメモリが疑わしい。
  次に試すべきこと: (a) `433:711.strength_model` を下げる、(b) 1 段目の解像度を上げて
  ガイドの内部ダウンスケール後サイズを 32 の倍数に余裕をもたせる、
  (c) `433:735.batch_size` を 1 に下げる、(d) `433:750.strength` を 0.5 程度に下げる。
- 強度の初期値は公式テンプレートどおり **IC-LoRA strength 1.0 / AddGuide strength 1.0 / frame_idx 0**。
- control_images は **1 段目 latent と同解像度**（W/2 × H/2）で与えること。
  `LTXVAddGuide` の image は 8n+1 フレームである必要があり、余りは自動で切り捨てられる。

---

## 5. UI 版ワークフロー（`video-gen.ui.json`）の運用

> 現行では不要な手順（`video-gen.json` / `video-gen.ui.json` は廃止済み）。
> 現在は `workflow/` 配下の各テンプレートを個別に ComfyUI で開いて編集し、API フォーマットで
> 書き出したうえで `backend/app/workflows.py` の注入マニフェストを合わせて更新する運用です
> （不一致は起動時・`GET /api/health`・pytest の `validate_specs()` が検出します）。

`video-gen.json` は API フォーマットで、ComfyUI のエディタでは開けない。
編集用に同内容のグラフ（UI）フォーマット版 **`video-gen.ui.json`** を用意してある。

**実行の正は常に `video-gen.json`。** UI 版は編集の作業台であり、
アプリはこのファイルを読まない。

### 手順

1. ComfyUI で `video-gen.ui.json` を開いて編集する
2. `ワークフロー > エクスポート (API)` で API フォーマットを書き出す
3. 書き出した JSON で `video-gen.json` を差し替える
4. `pytest backend/tests` を流して回帰確認（ノード ID 参照が壊れていないか）

### 構成

- トップレベル: `366`(解像度) / `435`(スタート画像) / `432`(音声) / `393`(プレビュー) / `75`(SaveVideo)
- サブグラフ **「画像生成 (t2i)」**（ノード id **365**）= `365:*` 一式
- サブグラフ **「動画生成 + IC-LoRA 参照機構」**（ノード id **433**）= `433:*` 一式
  内部は「パラメータ」「モデル」「IC-LoRA ①②③④⑤a⑤b」「条件付け」「1 段目」「2 段目」「出力」の
  グループに分けてある
- よく触るパラメータ（プロンプト・シード・identity_mode・スタート画像なし・
  リファレンスシート/参照動画・duration/fps・IC-LoRA 強度・ガイド強度・MoGe 設定・
  キャラ LoRA）はサブグラフノード上のウィジェットに昇格（`proxyWidgets`）してある

### ⚠ ノード ID を壊さないこと

エクスポート時の API ノード ID は **`<サブグラフノードの id>:<内部ノードの id>`**
（frontend の `[...subgraphNodePath, node.id].join(":")`）。
`backend/app/workflow.py` は `365:3` / `433:394` などを直接参照し、
`model_overrides` の DB キーも `<node_id>.<field>` なので、以下を必ず守ること。

- 画像サブグラフのノード id は **365**、動画サブグラフのノード id は **433** のまま
- `75 / 366 / 393 / 432 / 435` はトップレベルのまま
- 既存ノードを削除して作り直さない（id が変わる）
- **IC-LoRA 部分を入れ子サブグラフにしない**（id が `433:<入れ子 id>:7xx` になって壊れる）。
  そのためグループでの視覚的分割にとどめてある

### 参照系ノードの既定バイパス

`433:720` LoadImage（リファレンスシート）と `433:730` LoadVideo（参照動画）は、
モード 1 では使わないのにファイルが未設定だと **投入時バリデーションで落ちる**。
そのためモード 1 で不要な参照系 15 ノードを **既定でバイパス（litegraph `mode: 4`）**
にしてある。エクスポート時に ComfyUI がこれらを除去するので、
既定状態の Export(API) は **86 ノードの「参照系なし＝従来相当グラフ」** になる。

既定バイパスのノード（グループ単位で Ctrl+B できるよう配置してある）:

| グループ | ノード |
|---|---|
| ② IC-LoRA ローダー | `433:710` `433:711` `433:713` |
| ③ 画像参照 前処理 | `433:720` `433:721` `433:722` |
| ④ 動画参照 前処理 (MoGe) | `433:730`–`433:736` |
| ⑤a ガイド注入 | `433:740` `433:750` |

**⑤b モード切替スイッチ**（`433:712` `433:751`–`433:757` `433:761` `433:762`）と
**① モード切替**（`433:700`–`433:702` `433:760`）は常時有効のまま。

バイパスの伝播（エクスポート時に ComfyUI が同じ型の入力を素通しする）:

- `433:710` / `433:711` LoraLoaderModelOnly → MODEL 素通し ⇒ `433:712` の両入力が `433:427` になる
- `433:750` LTXVAddGuide → positive / negative / latent 素通し
  ⇒ `433:751` `433:752` `433:753` の on_true が on_false と同じ先（`433:416` / `433:408`）になる
- `433:713` `433:720`–`433:722` `433:730`–`433:736` `433:740` は `433:750` 専用なので一緒に消える

### モード別の操作手順（UI 版）

| モード | 手順 |
|---|---|
| **1**（ID LoRA + 音声） | 既定のまま。`identity_mode = 1`、何も解除しない |
| **2**（画像参照） | `identity_mode = 2` → グループ **②** と **③** を Ctrl+B で解除 → ⑤a は **`433:750` だけ** 解除（**`433:740` はバイパスのまま**）→ `433:720` にシート画像 |
| **3**（動画参照） | `identity_mode = 3` → **② ③ ④ ⑤a をすべて** Ctrl+B で解除 → `433:730` に参照動画、`433:720` にも任意の画像（ダミー可） |

- モード 2 で `433:740` をバイパスのままにするのは、バイパス中の `ComfySwitchNode` が
  on_false（= `433:722`）を素通しするため。これで参照動画を用意せずに画像参照が成立する。
- モード 3 で `433:720` にもファイルが要るのは、**有効な `433:740` が on_false / on_true の
  両方の入力を要求する**ため（`ComfySwitchNode` は遅延評価ではない）。
  アプリ実行時は §3.1 のプルーニングでこの分岐ごと削除されるので不要。

### 既知の差分（等価性検証済み）

**バイパスをすべて解除した状態**で ComfyUI 1.45 に読み込み → `エクスポート (API)` した
結果と `video-gen.json` の差分は以下の 2 件のみで、いずれも UI 専用フィールド
（実行に影響しない）。

- `75.video-preview`（旧 frontend が付けていた空文字。新 frontend は出力しない）
- `432.audioUI`（LoadAudio のプレビュー URL。新 frontend が付ける）

なお `365:391 ImpactWildcardEncode` の `Select to add Wildcard` は Impact-Pack の
JS が独自の表示文字列に書き換えるが、このノードは `build_workflow` が必ず削除する。
