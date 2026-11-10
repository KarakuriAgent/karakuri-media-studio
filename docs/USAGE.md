# 使い方

起動したあとの操作です。導入と環境まわりは [`SETUP.md`](SETUP.md)、仕様の詳細は
[`SPEC.md`](SPEC.md) にあります。

左ペインでモードとワークフローを選び、プロンプトや必要な入力を埋めて「実行」。進捗は
WebSocket で右ペインにリアルタイム表示され、完了すると生成物がプレビューされます。
**現在のモード・ワークフローが使わない項目はフォームに出ません**（入力値は保持され、戻れば復元されます）。

| モード | 内容 |
|---|---|
| 画像＋動画 | 画像を生成し、その画像を開始フレームにして動画を生成（同一ジョブで 2 段実行。画像は 1 段目で確定保存） |
| 動画生成 | 動画ワークフローを単発実行 |
| 画像のみ | 画像生成だけを実行（開始フレーム候補の量産に） |
| 音声 | 音声ワークフローを単発実行（画像・動画とは連結しない） |

**画像**は Krea 2 turbo（既定）/ Anima / Z-Image turbo / Qwen-Image 2.1（テキスト→画像と
参照編集の 2 本 × 素 / Turbo）/ P-H3-Image（テキスト→画像・画像編集・参照編集の 3 本）/
Grok Imagine（テキスト→画像・画像編集）から選びます。

**Qwen-Image 2.1** は生成と編集が 1 つに統合されたモデルで、`qwen_image_21_t2i`
（テキスト→画像。ネイティブ 1024x1024、2K まで）と `qwen_image_21_edit`（参照編集。
参照画像 1〜10 枚が必須で、2 枚以上ならプロンプトから `<image1>` `<image2>` … と呼び、
1 枚ならタグを使わない。1 枚目が編集キャンバスで出力の縦横比もそれに従う）の 2 本です。
どちらも cfg 1 で動くのでネガティブプロンプトは効きません。ウェイトは
`qwen_image_2.1_int8_convrot`（diffusion_models）/ `qwen3vl_8b_int8_convrot`（text_encoders）
/ `qwen_image_2.1_vae_bf16`（vae）。**Qwen Research License** で配布されているので、
商用利用には別途ライセンスが必要です。

どちらにも公式の 8 steps 蒸留版（Qwen-Image 2.1 Turbo）を使う **Turbo** 版
（`qwen_image_21_t2i_turbo` / `qwen_image_21_edit_turbo`）があります。入力もプロンプトの
書き方も素の版と同じで、サンプリングが 25 → 8 steps になるぶん速く焼けます（CFG は 1 の
まま）。重みは `qwen_image_2.1_turbo_int8_convrot`（diffusion_models）を置くだけで、
テキストエンコーダと VAE は素の版と共通です（bf16 版の `qwen_image_2.1_turbo_bf16` を
使うなら設定ページのモデルスロットで差し替え）。プロジェクトの画像品質を Turbo にすると、
Qwen-Image 2.1 の素材画像は t2i・編集とも Turbo 版で焼かれます（Qwen-Image 2.1 に
Optimized は無いので、Optimized のときは素の版のままです）。

**P-H3-Image**（PrunaAI）は MiniMax H3 をベースにした画像生成・編集モデルで、**8 ステップ・
CFG 無しで 8MP** を描きます。テキスト→画像（`p_h3_t2i`）・画像編集（`p_h3_i2i`。編集元画像が
`<Picture 1>`）・参照編集（`p_h3_r2i`。参照画像 1〜8 枚を渡した順に `<Picture 1>` `<Picture 2>` …
と呼び、1 枚目が編集される側）の 3 本です。版違い（Optimized / Turbo）は無く、プロジェクトの
画像品質をどれにしても同じワークフローで焼かれます。ステップ数も変えられません。ComfyUI 本体
（v0.44.0 以降）のノードだけで動くので custom node は不要で、Comfy Cloud でも選べます
（モデルファイルが Cloud 側にあるかは別です）。

- **解像度**: テキスト→画像は既定 8MP で、アスペクト比ごとにモデルカードの推奨サイズ
  （1:1 2912×2912、4:3 3360×2496、16:9 3872×2176 など）になります。メガピクセルを下げれば
  4MP なども選べますが、低いほど顔・手・細部が崩れやすくなります。編集（i2i / r2i）は
  `<Picture 1>` を既定 4MP に揃え、**その縦横比のまま**描きます（アスペクト比は効きません）。
- **プロンプトアップサンプラ**（既定 on）: Qwen3.5-9B ベースの書き直しモデルが、短い依頼を
  P-H3-Image 向けの詳しいプロンプト（編集なら「何を変えて何を残すか」の編集指示）にして
  から描きます。テキスト→画像ではアスペクト比の行（`Aspect ratio: 16:9` など）をアプリが
  自動で付けるので、プロンプトに比を書く必要はありません。off にすると書いた文がそのまま
  使われるので、モデルカードの書き方（下記）で書いたプロンプト向けです。
- **P-H3-Image LoRA**（既定 on）: 既定の構成は、動画の Turbo と同じ拡散モデル
  `minimax_h3_fl2va_pruned_w4a8_mixed` に P-H3-Image の LoRA（`p-h3-image_lora_bf16`）を重ねて
  画像モデルにするものです（大きな重みを動画と共有できます）。設定ページで UNET を LoRA
  焼き込み済みの専用重み（`p-h3-image_w4a8` など）に差し替えたときだけ off にします。
- **プロンプトの書き方**（英語）: 冒頭の 1 文で「その画像が何であるか」（"a photograph by an
  experienced street photographer"、"a frame from a Japanese anime TV series" など）を言い、
  視点・その瞬間の主役 1 つ・少数の脇役・光源と向きで 1 シーンを書きます。画像に出す文字は
  全部引用符で短く書き、位置を添えて最後に "no other text"。stunning / epic / cinematic /
  8k / masterpiece のような誇張語は入れず、長すぎるプロンプトも避けます。編集は
  `Edit <Picture 1>. <完成した結果>. Keep <残すもの> exactly as it is.` の形です。

モデルは `minimax_h3_fl2va_pruned_w4a8_mixed`（diffusion_models・動画と共有）+
`p-h3-image_lora_bf16`（loras）+ `p-h3-image_text_encoder_w4a8` / `p-h3-image_prompt_upsampler_w4a8`
（text_encoders）+ `p-h3-image_vae_int8_convrot`（vae）。どれも設定ページで bf16 / int8_convrot
版に差し替えられます（取得元と VRAM の目安は SETUP.md）。**MiniMax H3 Community License**
なので、EU・英国・韓国・米国では MiniMax の別途許諾が要り、使った製品には
"Powered by MiniMax H3" の表示が必要です。

以前の **MiniMax H3 Image**（`minimax_h3_t2i` / `_i2i` / `_r2i` とその Optimized / Turbo。
外部の custom node 頼み）は P-H3-Image に置き換えました。履歴やスタジオの設定に古い id が
残っていても、自動で対応する P-H3-Image に読み替えます。

**Grok Imagine** は ComfyUI ではなく **grok CLI のサブスクリプション枠**で走る外部生成です
（GPU もモデルファイルも不要）。テキスト→画像と画像編集の 2 種があり、生成物は他の
ワークフローとまったく同じように outputs / 履歴 / ライブラリに入ります。ただしグラフが
無いので **LoRA は使えず、解像度も選べません**（アスペクト比だけが `1:1` / `16:9` / `9:16`
/ `3:2` / `2:3` の近いものに寄せて渡り、メガピクセルは無視されます）。モデルのバージョンも
指定できません。枠は Grok チャットと共有で、実在人物・著名人・商標はモデレーションで
弾かれます。

**動画**は MiniMax H3 の 8 種から選び、必要な入力の欄だけが出ます。

| ワークフロー | 必要な入力 |
|---|---|
| テキスト→動画・音声つき (MiniMax H3 t2v) | なし |
| テキスト→動画・音声つき (MiniMax H3 t2v Optimized) | なし（LoRA なし・20 ステップのまま実行だけ速い版） |
| 画像→動画・音声つき (MiniMax H3 i2v)（既定） | 開始フレーム（最後のフレームは任意） |
| 画像→動画・音声つき (MiniMax H3 i2v Turbo) | 同上（6 ステップ固定の高速版） |
| 画像→動画・音声つき (MiniMax H3 i2v Optimized) | 同上（LoRA なし・20 ステップのまま実行だけ速い版） |
| 参照素材→動画・音声つき (MiniMax H3 r2v) | 参照画像 9 枚 / 参照動画 3 本 / 参照音声 3 本まで（合計 1 件以上） |
| 参照素材→動画・音声つき (MiniMax H3 r2v Turbo) | 同上（6 ステップ固定の高速版） |
| 参照素材→動画・音声つき (MiniMax H3 r2v Optimized) | 同上（LoRA なし・20 ステップのまま実行だけ速い版） |

MiniMax H3 の 8 種は映像とステレオ音声（台詞・効果音・音楽）を同時生成します。
実行には ComfyUI 本体 **v0.44.0 以降**が必要です（SPEC §2.2。ラテントアップスケールや
Turbo / Optimized の高速化ノードもこのバージョンの本体に入っています）。
**Turbo**（i2v / r2v）は Pika H3 Flex LoRA（i2v は fl2va 用、r2v は ref2va 用）・lcm・
6 ステップ固定のシグマと Comfy Kitchen attention / Sol-Attn をワークフローに焼き込んだ高速版で、
入力の指定は素の版と同じです（ステップ数だけは固定で、指定しても効きません）。
**t2v に Turbo はありません**（Turbo のテンプレートを置いていないため）。
プロジェクトの動画品質を Turbo にしていても、t2v になるカットは Optimized で投入されます。専用の量子化ウェイト
（`*_pruned_w4a8_mixed` / `qwen3vl_32b_heretic_minimax_h3_nvfp4`）と動画 VAE の Sol-H3-VAE
（`minimax_h3_sol_vae_int8_convrot`）が入った環境でのみ動きます。Turbo の高速化ノード
（`ModelAttentionBackend` / `BlockSparseAttention` / `MiniMaxH3SigmaShift`）も Pika H3 Flex LoRA の
`LoraLoaderModelOnly` もシグマの `ManualSigmas` も ComfyUI 本体のノードなので、Turbo に
custom node は要りません（Optimized は `SpectrumApplyMiniMaxH3` の custom node だけ要ります）。
Sol-H3-VAE はデコードが速い代わりにタイル分割ができず、クリップ全体を一度にデコードするので、
長尺・高解像度ほど VRAM を多く使います（足りなければ設定ページで動画 VAE を
`minimax_h3_video_vae_int8_convrot` に戻せます）。
接続先が Comfy Cloud でも Turbo（i2v / r2v）とラテントアップスケールは選べます。その場合は
該当モデル（w4a8 の UNET・Pika H3 Flex LoRA・Sol-H3-VAE・ラテントアップスケーラ
`minimax_h3_latent_upscaler_3d_bf16`）を Cloud 側に入れておいてください。Optimized は
Spectrum の custom node が要るので Comfy Cloud では選べません。

MiniMax H3 の動画には、仕上がりのフレームに掛ける**補正**を 3 つ、独立にオン / オフで
付けられます（どれも既定はオフ。生成フォームではワークフローの下の選択式、ドラマスタジオでは
プロジェクトバーの「補正」欄と生成ダイアログ）:

| 補正 | やること |
|---|---|
| 速い動きの滲み補正（`fast_derope`） | 速い動きで滲む・溶ける区間だけをスローにして描き直し、元の尺に戻して差し込む。速い動きが無いクリップでは何もしない |
| 引きの画の顔補正（`face_detail`） | 引きの画で小さく写った顔だけを拡大して描き直す（大きく写った顔は触らない。口の動きは台詞に合ったまま） |
| 2 コマ / 3 コマ打ち（`anime_cadence`） | デコード後のフレームを 2 コマ / 3 コマ打ちに直す（アニメ調向けの後処理） |

ラテントアップスケールがオンなら最終解像度（2 パス目）の出力に掛かり、音声は元の生成の
ままです。描き直しのぶん生成時間が延びます。外部の custom node（ComfyUI-H3-FastDerope /
FaceDetail / AnimeCadence と ComfyUI-MAINodes）と DMAD LoRA などのモデルが要るので
（導入は [`SETUP.md`](SETUP.md) の「MiniMax H3 動画の補正」）、**Comfy Cloud では選べません**
（生成フォームでは「しない」だけになり、スタジオのトグルは理由つきで無効になります）。

**音声**は MiniMax Music 3（歌もの・インスト。キャプションと歌詞を指定）と
Stable Audio 3 Medium（効果音・環境音・単一楽器）の 2 種です。

各ワークフローの用途・音声の扱い・プロンプトの書き方は SPEC §2 を参照してください。
プロンプトの実例は [`prompt-samples.md`](prompt-samples.md) にあります。

## 入力ファイル

画像・音声・参照動画は**ドラッグ&ドロップ**か [アップロード] で指定でき、`assets/` に
保存されて再利用できます。**[履歴から選択]** で過去の生成物を、**[ライブラリから選択]** で
取っておいた素材をそのまま入力に使えます。

## ライブラリ

履歴はジョブを消すと成果物も消えますが、**ライブラリ**は「残す」と決めた画像・動画・音声を
置いておく棚です（`library/`）。結果ペインや履歴詳細の [☆ ライブラリに登録] で保存でき、
タグ（未指定なら Grok が日本語タグを自動生成）と検索で探せます。

効果音・BGM・漫画記号などは、ファイルタブの **[素材パック]** から同梱の素材パックを選んで
入れられます（目録だけを同梱しており、入れると入手元のサイトから取りに行きます）。入手元ごとの
規約の要点と確かめた日を読み、規約のページを確かめてから同意のチェックを付けて [入れる] を
押します。パックと入手元の一覧は [`asset-packs/README.md`](../asset-packs/README.md)。

## Grok チャットでプロンプト作成

「Grokで生成」ボタンでチャットモーダルが開きます。フォームの現在値を踏まえて Grok が
不足情報を質問し、まとまったら画像 / 動画 / 音声プロンプトの案を提示 → [フォームに反映] で
プロンプト欄に入ります。

## 外部エージェントでの制作（SKILL）

「話を書いて、カットを焼いて、採用して、並べて書き出す」までを**手元のコーディング
エージェント**（Claude Code / Codex / Cursor CLI など）に任せられます。アプリの中に
エージェントを抱えるのではなく、外部 API（`/api/v1`）を叩いてもらう形です。

1. 設定 →「接続 / Grok」タブの **外部 API（/api/v1）** で APIキーを保存する
   （[生成] でランダムなキーを作れます。空のあいだは `/api/v1` は丸ごと 404 です）
2. エージェントに接続先とキーを渡す（環境変数 `KARAKURI_STUDIO_URL` /
   `KARAKURI_STUDIO_API_KEY`。省略時はリポジトリ直下の `.env` と
   `runtime/config.json` から解決されます）
3. `workspace/` を cwd にして作業させるだけで、[`workspace/AGENTS.md`](../workspace/AGENTS.md) /
   [`workspace/CLAUDE.md`](../workspace/CLAUDE.md) から
   [`.agents/skills/karakuri-studio/SKILL.md`](../workspace/.agents/skills/karakuri-studio/SKILL.md) に誘導されます

SKILL には接続とキーの解決、最初に読む API（`openapi.json` / `prompt-guide` /
`capabilities` / `options`）、制作の段取り、`base_revision` や削除まわりの鉄則が書いて
あります。curl ラッパーと動画検分スクリプトは
[`workspace/.agents/skills/karakuri-studio/scripts/`](../workspace/.agents/skills/karakuri-studio/scripts) にあります。

エージェントが書き換えた脚本・素材・生成フォームは、開いているブラウザに WebSocket で
そのまま反映されます（`POST /api/v1/ui/navigate` で画面を目的の場所へ動かすこともできます）。
人が同時に編集していても上書きし合わないよう、書き込みには編集履歴の連番
（`base_revision`）による楽観ロックが掛かります。消しすぎたカットや素材は
スタジオの編集履歴（差分・部分復元）から戻せます。

## LoRA

設定画面の「LoRA 管理」タブで人物 LoRA を登録します（表示名 / ファイル名 / 対象（画像用・動画用）/
画像用はモデルファミリー / トリガーワード / 既定強度 / 既定リファレンス音声 / サンプル画像 /
使い方の説明）。
生成フォームでは複数を同時適用でき、**選択中の画像ワークフローと同じファミリーの LoRA だけ**が候補に出ます。

「使い方の説明」は、その LoRA を**外部エージェントに正しく使わせるためのメモ**です（複数行・
Markdown 可、長文でも可）。入力画像の順番（例: Image 1 = 元画像、Image 2 = 差し替える人物）、
プロンプトのテンプレート、対象人物の指定方法などを書いておくと、エージェントが
`GET /api/v1/options` の `loras[].description` で読んで従います。生成時にプロンプトへ
自動で混ざることはありません（自動で付くのはトリガーワードだけ）。説明のある LoRA は一覧に
「説明」の印が付きます。

## 履歴・NSFW

履歴ギャラリーのサムネイルから詳細を開き、**再実行**（seed ランダム化 / 同じシードの 2 通り）・
**続きを生成**（ラストフレームを開始フレームに）・**削除**ができます。[続きを生成] は上書きフォームを
開き、そのまま押せば元ジョブの設定を丸ごと引き継ぎ、埋めた欄（プロンプト・尺・ワークフローなど）
だけが上書きされます。履歴は無制限に保存され、削除は手動のみです。
ジョブとセッションには NSFW フラグが付き（Grok の自動判定、手動上書き可）、ヘッダーの NSFW トグルが
オフのあいだは履歴・ビューアから除外されます（トグルはタブを開き直すと必ずオフに戻ります）。
生成フォームの [実行] の上にある「🫣 NSFW として投入」をオンにすると、そのジョブは自動判定を
待たずに最初から NSFW 扱いになります（オフのままなら従来どおり生成後に自動判定）。
ドラマスタジオは**プロジェクト単位**で NSFW を決めます（概要タブの「🫣 NSFW プロジェクト」）。
オンならその作品から投入するジョブはすべて NSFW、オフなら非 NSFW で固定され、
どちらの場合も自動判定は走りません。

---

## 外部 API（/api/v1）

外部のエージェント（ログを見て話を納品するブリッジなど）から、スタジオの脚本づくりと
生成を行うための API です。**既定では無効**で、設定の「接続 / Grok」タブにある
**外部 API（/api/v1）** の APIキー欄にキーを入れて保存すると有効になります（[生成] で
ランダムなキーを作れます）。キーが空のあいだは `/api/v1` は丸ごと 404 を返します。

```bash
curl -H "X-API-Key: <保存したキー>" http://127.0.0.1:8000/api/v1/projects
```

**公開範囲の正本は `GET /api/v1/openapi.json`** です（この API だけに絞った縮小版の
OpenAPI で、エージェントにはこれを読ませます）。おおまかには、人が UI でできることの
ほぼ全部が入っています:

- 脚本（プロジェクト / 話 / 場 / カット / 素材）の作成・更新・並べ替えと、投入前の
  `GET /api/v1/shots/{id}/prompt-preview`
- 生成（`POST /api/v1/shots/{id}/render` と Take の採否 / 汎用ジョブの `POST /api/v1/jobs`・
  キャンセル・再実行・続き生成）、ライブラリ、タイムラインの編集と mp4 書き出し
- 編集履歴（一覧 / 差分 / 復元）、生成フォームの下書き（`ui/generate-form`）と画面移動
  （`ui/navigate`）、Remotion の composition 一覧
- 参照系: `capabilities`（いまの接続先で使える機能）/ `options`（フォームと同じ選択肢）/
  `prompt-guide`・`prompt-examples`（脚本とプロンプトの書き方）
- 話 1 本を 1 リクエストで納品する `POST /api/v1/stories`
- **削除はプロジェクト以外**（プロジェクトは履歴ごと消えて戻せないので、外には出していません）

壊れた連携先が積み続けないよう、**未完了のジョブ**と**走っている書き出し**をそれぞれ
上限（`external_max_pending_takes`、既定 20 / 0 で無制限）で見張り、達しているあいだは
投入を 429 で拒みます（UI からの操作には掛かりません）。

プロンプトに `@素材名` を書いて画像素材を参照すると、ワークフローは自動で参照素材版
（r2v）に切り替わります。カットの `megapixels` を省略するとバックエンドの既定値になるので、
VRAM の小さい GPU では小さめの値を明示してください。実機確認の記録と運用上の注意は
[`EXTERNAL-API.md`](EXTERNAL-API.md) §8 にまとめてあります。

**ネット越しに公開する場合はアプリをそのまま外に出さないでください**: `/api/v1` 以外の
内部 API・`/docs`・生成物の静的配信は無認証です。Cloudflare Tunnel + Access を前に置く
構成を [`EXTERNAL-API.md`](EXTERNAL-API.md) §5 に書いてあります。エンドポイントの
一覧と本文の形は `GET /api/v1/openapi.json` を参照してください。
