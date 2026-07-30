# Karakuri Media Studio

`workflow/` 配下の ComfyUI ワークフロー（画像 4 種 / 動画: LTX 2.3 の 7 種 / 音声 2 種）を Web UI から実行し、
プロンプト作成を Grok に委譲、生成物と履歴をローカルに保存する個人利用向けのメディア生成アプリです。

![生成画面](docs/images/screen-image.png)

- バックエンド: Python 3.12 + FastAPI + SQLite（`app.db`）
- フロントエンド: React + Vite + Tailwind（ダークテーマの SPA。「生成」と「エージェント」の 2 ビュー + 設定ページ）
- 生成本体: ComfyUI（ローカル / LAN 上の別 PC / Comfy Cloud）
- プロンプト生成: Grok Build CLI（サブスクリプション認証、API キー不要）

詳細な仕様は [`docs/SPEC.md`](docs/SPEC.md)、エージェントモードの設計は
[`docs/AGENT-MODE.md`](docs/AGENT-MODE.md)、プロンプト実例は
[`docs/prompt-samples.md`](docs/prompt-samples.md) を参照してください。

---

## 前提条件

| 依存 | 内容 |
|---|---|
| ComfyUI | 稼働中であること（既定 `http://127.0.0.1:8188`）。Comfy Cloud も可 |
| custom nodes | ResolutionSelector / ComfySwitchNode / CustomCombo / LTXV 系 / ComfyMath / ResizeImage 系 / ResizeAndPadImage / MoGe 系 / LoadVideo / Video Slice など、`workflow/` 配下のワークフローが使うノード一式 |
| モデル | 使うワークフローのぶんだけあれば十分です。画像: `krea2_turbo_fp8_scaled` / `anima-base-v1.0` / `z_image_turbo_bf16` / `qwen_image_edit_2511_int8_convrot` + それぞれの CLIP・VAE、動画: `ltx-2.3-22b-dev-fp8` と `ltx-2.3-22b-distilled-fp8` + distil LoRA / talkvid ID-LoRA / IC-LoRA、TE `gemma_3_12B_it_fp4_mixed`、音声: `acestep_v1.5_xl_sft_bf16` / `stable_audio_3_medium_base` + それぞれの TE・VAE、および使用する人物 LoRA |
| grok CLI | `curl -fsSL https://x.ai/cli/install.sh \| bash` でインストール後、一度 `grok` を起動してブラウザでサインイン（SuperGrok / X Premium+ のサブスクリプションで利用可） |
| ffmpeg | 動画からのラストフレーム抽出に使用（`ffmpeg` が PATH にあること） |
| Python | 3.12 以上 |
| Node.js | 18 以上（npm 同梱） |

不足している custom node は起動後 `GET /api/health`（ヘッダーの接続インジケーター）で検出され、
`workflow/` 配下の全テンプレートに含まれる class_type をチェックします。同時に各ワークフローの
注入マニフェスト（ノード ID 直指定）がテンプレートと一致しているかも検証されます。

---

## セットアップと起動

```bash
git clone https://github.com/KarakuriAgent/karakuri-media-studio.git
cd karakuri-media-studio
```

```bash
./run.sh          # 本番: 依存を整えて frontend をビルドし、http://127.0.0.1:8000 で起動
./run.sh --dev    # 開発: uvicorn --reload (:8000) と vite dev (:5173) を並行起動
```

`run.sh` は初回に以下を自動で行います。

1. `.venv` がなければ作成し `backend/requirements.txt` を install
2. `frontend/node_modules` がなければ `npm install`
3. 本番モードで `frontend/dist` がなければ `npm run build`
4. `uvicorn app.main:app` を起動（`frontend/dist` があれば SPA も同一ポートから配信）

環境変数 `HOST` / `PORT` で待受を変更できます（既定 `127.0.0.1:8000`）。
シェルで渡すほか、リポジトリ直下に `.env`（gitignore 済み）を置いても読み込まれます:

```bash
# .env
HOST=0.0.0.0
PORT=8080
```

シェルの環境変数と `.env` が両方ある場合はシェル側が優先されます。
開発モードではブラウザで <http://localhost:5173> を開きます（`/api` はバックエンドのポートへプロキシ。`HOST`/`PORT` の変更に自動追従）。

手動で起動する場合:

```bash
python3.12 -m venv .venv && .venv/bin/pip install -r backend/requirements.txt
cd frontend && npm install && npm run build && cd ..
PYTHONPATH=backend .venv/bin/uvicorn app.main:app --port 8000
```

### Docker (docker compose) で起動する

```bash
# 初回のみ: フロントエンドはホスト側でビルドしておく
npm --prefix frontend install && npm --prefix frontend run build

./compose.sh up -d --build   # 起動
./compose.sh logs -f         # ログ
./compose.sh down            # 停止
```

設計は「ランタイムだけコンテナ、データとワークスペースはローカル」です。
`compose.sh` は `docker compose` の薄いラッパーで、以下を自動で行います。

- **実体パスへの解決**: リポジトリ全体をホストと同じ絶対パスにマウントします。
  `app.db` や `runtime/config.json`、ジョブ履歴には絶対パスが記録されている
  ため、パスを揃えることで `./run.sh` 実行と Docker 実行を自由に行き来
  できます（シンボリックリンク経由のディレクトリから叩いても大丈夫です）
- **権限の引き継ぎ**: コンテナを `UID:GID`（ローカルユーザー）で動かすので、
  生成されるファイル（`outputs/`・`app.db` など）の所有者は root ではなく
  ローカルユーザーのままです

補足:

- grok CLI はホストの `~/.grok`（バイナリ＋サインイン状態）をマウントして
  使います。**事前にホスト側で grok のインストールとサインイン**を済ませて
  ください（コンテナ内での新規サインインは想定していません）
- `HOST` / `PORT` は `run.sh` と同じく `.env` から読まれます
  （既定 `127.0.0.1:8000`）
- ComfyUI にローカルホストの URL（`http://127.0.0.1:8188` など）を使っている
  場合、コンテナからは `127.0.0.1` がコンテナ自身を指すため届きません。
  設定画面の ComfyUI URL を `http://host.docker.internal:8188` か LAN の IP に
  変更してください（Comfy Cloud の場合はそのままで動きます）
- `docker compose` を直接使う場合は、リポジトリの実体パスから
  `UID=$(id -u) GID=$(id -g) docker compose up -d` のように実行してください
- サービス名は `media-studio` です（旧 `video-studio`）。アプリ名の変更に伴って
  コンテナ名も変わるため、改名前に起動していた古いコンテナが残っている場合は
  `docker rm -f <旧コンテナ名>` で片付けてください

テスト:

```bash
cd backend && ../.venv/bin/pytest                  # バックエンド
cd frontend && npm run build && npx vitest run     # フロントエンド（型チェック込み）
```

---

## 使い方

### 4 つの生成モード

| モード | 内容 |
|---|---|
| 画像＋動画 | 画像を生成し、その画像を開始フレームにして動画を生成（2 段実行） |
| 動画生成 | 選択した動画ワークフローを単発実行。画像プロンプト・画像 LoRA の欄は出ない |
| 画像のみ | 画像生成だけを実行。開始フレーム候補を量産して良いものを「動画生成」モードに渡す用途 |
| 音声 | 音声ワークフローを単発実行。画像・動画とは連結しない独立したジョブ |

左ペインでモードを選び、動画・画像ワークフロー・プロンプト・アスペクト比・メガピクセル・LoRA・
ワークフローが要求する入力（開始フレーム / 最後のフレーム / 音声 / 参照動画）・
秒数 / fps / seed を設定して「実行」。進捗は WebSocket で右ペインにリアルタイム表示され、
完了すると生成画像・動画・ラストフレーム（音声モードでは音声プレイヤー）がプレビューされます。

**現在のモードと選択中のワークフローが使わない項目は、フォームに表示されません**
（無効化ではなく非表示）。入力した値はフォームの状態として保持されるので、
その項目を使うモード・ワークフローに戻せば元の内容が復元されます。

秒数に上限はありませんが、長尺は VRAM 次第で ComfyUI 側がエラーになります
（音声モードだけはモデルの対応範囲に上下限があり、範囲外は実行前に弾かれます）。

#### 「画像＋動画」は 2 段実行

このモードはグラフを合体させず、**同一ジョブ ID のもとで 2 つの ComfyUI プロンプトを順に実行**します。

1. 選択した画像ワークフローを実行 → 生成画像を `outputs/{job_id}/image.png` に保存
2. その画像を ComfyUI にアップロードし、選択した動画ワークフローの開始フレームに注入して実行
3. 動画をダウンロードし、ffmpeg でラストフレームを抽出

進捗は「画像生成 (1/2)」→「動画生成 (2/2)」と 1 ジョブとして表示されます。
**画像は 1 段目の完了時点で確定保存される**ため、動画段が失敗しても生成画像は履歴に残り、
そのまま「動画生成」モードの開始フレームとして再利用できます。

### 画像ワークフローの選択

画像は `workflow/image/` の 4 種から**プルダウンで選択**します
（「画像＋動画」「画像のみ」モードで表示）。

| ワークフロー | ファミリー | 内容 |
|---|---|---|
| Krea 2 turbo（既定） | `krea2` | テキスト→画像。自然文 1 段落の長いプロンプト向き |
| Anima | `anima` | テキスト→画像。アニメ・イラスト系 |
| Z-Image turbo | `z-image` | テキスト→画像。8 ステップの蒸留モデル |
| Qwen-Image Edit 2511 | `qwen-image` | 画像**編集**。参照画像が必須で、`image_prompt` は編集指示として書く |

- Qwen-Image Edit は入力画像を書き換えるワークフローなので、**参照画像（編集元画像）が必須**です。
  出力解像度は入力画像から決まるため、アスペクト比・メガピクセルの欄は出ません
  （「画像＋動画」モードでは、編集結果がそのまま動画の開始フレームになります）
- Z-Image turbo のテンプレートには ResolutionSelector が無いので、アプリが幅・高さを計算して直接注入します
- LoRA はモデルファミリー単位で登録され、**選択中の画像ワークフローと同じファミリーの LoRA だけ**が候補に出ます

### 動画ワークフローの選択

動画は `workflow/video/ltx2.3/` の 7 種から**プルダウンで選択**します。
選択に応じて、そのワークフローが必要とする入力の欄だけがフォームに現れます
（音声入力を持たないワークフローでは音声欄そのものが出ません。音声はモデルが映像と同時に生成します）。

| ワークフロー | 必要な入力 | 用途 |
|---|---|---|
| テキスト→動画 (t2v) | なし | テキストだけから動画を生成する。画面に写るものはすべてプロンプトで決まる |
| 画像→動画 (i2v) | 開始フレーム | 開始フレーム画像から動画を生成する。被写体とセットは画像が決め、プロンプトは動きを担当 |
| 画像+音声→動画 (ia2v) | 開始フレーム / 音声 | 渡した音声がそのままクリップの音声トラックになり、映像はその音に合わせて動く |
| 画像+参照音声→動画・リップシンク (ID-LoRA) | 開始フレーム / リファレンス音声 | talkvid ID-LoRA で口の動きが揃った喋りの動画を生成。音声はモデルが生成し、リファレンス音声は声質とリップシンクの参照に使う |
| 最初と最後のフレーム指定 (flf2v) | 最初のフレーム / 最後のフレーム画像 | 2 枚の画像の間の動きを補間する |
| リファレンスシート (IC-LoRA) | リファレンスシート画像 | 複数カットを並べたリファレンスシートから動画を生成（Ingredients IC-LoRA）。画像は開始フレームではなく見た目の参照 |
| 参照動画からモーション転写 (IC-LoRA + MoGe) | 開始フレーム / 参照動画 | 参照動画のカメラワークとモーションを MoGe 深度経由で転写。クリップの長さは参照動画から切り出す区間の長さになる |

- 既定は **ID-LoRA**（リップシンク）です
- 「画像＋動画」モードのプルダウンには**開始フレームを受け取れるワークフローだけ**が並びます
  （t2v とリファレンスシート IC-LoRA は対象外。モード切替時に自動で選び直されます）
- 開始フレームを指定した動画生成では、**その画像の実寸比がそのままクリップの縦横比**になります
  （メガピクセル指定は維持。センタークロップで画が切れるのを防ぐため。
  リファレンスシート IC-LoRA は対象外で、画像の寸法が読めない場合はプリセットに戻ります）
- ワークフローごとの正確な用途・音声の扱い・プロンプトの書き方は
  `backend/app/workflows.py` の `WorkflowSpec` が単一の情報源で、Grok のシステムプロンプトにも
  ここから自動生成したカタログが渡ります

### 音声生成

「音声」モードは `workflow/audio/` の 2 種から選んで**単発実行**します。画像・動画とは一切連結せず、
成果物は mp3（`outputs/{job_id}/audio.mp3`）で、右ペインと履歴からプレイヤーで再生できます。

| ワークフロー | 用途 | フォームに出る項目 |
|---|---|---|
| ACE-Step 1.5 XL（音楽・歌もの） | 歌もの / インスト曲。歌詞を書けばボーカル入り、空ならインスト | 音声プロンプト（曲のキャプション）・歌詞・BPM・キー・言語・秒数（10〜600 秒） |
| Stable Audio 3 Medium（効果音・環境音・音楽） | 効果音・ワンショット・単一楽器・インスト曲。歌は歌えません | 音声プロンプト・カテゴリ（Music / Instrument / SFX / One-shot）・内蔵 LLM でのプロンプト展開・秒数（1〜380 秒） |

- どの項目が出るかはワークフローのマニフェスト（露出しているつまみ）で決まります
- 「Grokで生成」は音声モードにも対応し、選んだモデル向けのプロンプト（ACE-Step では歌詞・BPM・
  キー・言語も）を提案して、フォームへ反映できます
- 音声ジョブは LoRA を使いません（どちらのテンプレートにも LoRA ノードがないため、指定すると拒否されます）

### 入力ファイルの指定

- 画像ファイルを開始フレーム / 最後のフレーム欄に**ドラッグ&ドロップ**すると自動アップロードされます
- 「画像をアップロード」ボタン、または登録済みアセットのセレクトからも選べます
- 履歴のラストフレームのサムネイルをクリックすると、その画像が開始フレームになります
- 音声・参照動画も同様にアップロードでき、`assets/` に保存されて再利用できます

### Grok チャットでプロンプト作成

プロンプトは手動入力が基本ですが、「Grokで生成」ボタンでチャットモーダルを開けます。

1. フォームの現在値（モード・選択中の画像 / 動画 / 音声ワークフローとその書き方の指針・
   選択 LoRA とトリガーワード・秒数・下書き）がコンテキストとして渡ります
2. 「サクラが楽しそうにダンスをしている」程度の指示（サクラ=登録済みキャラの表示名の例）を入力すると、Grok が場所・服装・照明・
   カメラ・表情・セリフ / 音などを**質問で聞き返します**（「おまかせ」と言えば Grok が補完）
3. 情報が揃うと `image_prompt` / `video_prompt`（音声モードでは `audio_prompt` と歌詞・BPM 等）の
   最終案が JSON で提示されます
4. 「フォームに反映」でプロンプト欄に挿入。反映後も会話を続けて再調整できます

チャット履歴は `chat_sessions` に保存され、実行したジョブに紐付きます。
grok CLI は空の作業ディレクトリ（`runtime/grok-workdir/`）を cwd にして実行されるため、
プロジェクトのファイルには触れません。

### エージェントモード

ヘッダーの「エージェント」タブは、Grok が**同僚のように制作を回すビュー**です。
詳細な設計は [`docs/AGENT-MODE.md`](docs/AGENT-MODE.md) を参照してください。

1. 「ダンス動画を 3 本」のような目標を伝えると、Grok が必要なことだけ質問して
   **プラン**（何を何本・どのワークフローで・どの設定で）を提示します
2. プランを**承認するまで一切生成しません**。承認するとバックエンドがジョブを 1 本ずつ実行し、
   完了・失敗を Grok にイベントとして返します
3. Grok は結果を見て次の一手を打ちます: 動画をフレーム分解して品質を検分（`inspect`）、
   外れのシード再抽選（`rerun`）、当たりのラストフレームから続き生成（`continue`）、完了宣言（`done`）
4. 節目での確認（チェックイン）や、上限本数を決めた自走を選べます。⏹ でいつでも停止できます。
   自走時に上限本数へ達しても即打ち切りではなく、続行するか確認するチェックインが出ます
   （承認するたびに上限本数ぶん枠が伸びます）
5. チャット入力欄と新規セッションフォームの 📎 ボタンから**ファイルを添付**できます。添付ファイルは
   セッション作業場の `attachments/` に保存され、Grok がそれを読んだうえで応答します

エージェントには生成フォームと**同等の全項目**（`image_workflow` / `video_workflow` / `audio_workflow` /
`end_image` / `reference_video` を含む）と、`backend/app/workflows.py` から自動生成したワークフローカタログが
渡るため、**画像 4 種・動画 7 種・音声 2 種すべてのワークフロー**を使い分けて計画できます
（`continue` でのワークフロー切替も可。ただし開始フレームを受け取れるワークフローのみ）。
LoRA / アスペクト比 / アセットの選択肢も焼き込まれるので、実在しない値は指定できません。
画像 LoRA は生成フォームと同じくモデルファミリーの一致が必須で、不一致のプランは検証で弾かれます。

成果物は右の成果物パネルに時系列のリンクカード（プラン / リサーチ / メモ / 画像 / 動画 / 音声 / 検分フレーム）
として並び、サムネイルは出さないので NSFW が不意に表示されることはありません
（音声カードは開くとプレイヤーで再生できます）。
セッションの作業場は `runtime/agent-sessions/<id>/` です。

### LoRA 管理

設定画面の LoRA 管理タブで、人物 LoRA を登録（表示名 / ComfyUI 上のファイル名 / 対象（画像用 / 動画用）/
**モデルファミリー（画像用のみ）** / トリガーワード / 既定強度 / 既定リファレンス音声 / 並び順）できます。
ファイル名は手入力ですが、ComfyUI の `/object_info` から取得した一覧が補完候補として出ます。

画像用 LoRA は学習元のモデルファミリー（`krea2` / `anima` / `z-image` / `qwen-image`）を指定します。
生成フォームでもエージェントでも、**選択中の画像ワークフローと同じファミリーの LoRA しか使えません**
（不一致のジョブはバックエンドが拒否します）。動画用 LoRA は LTX 2.3 のみなのでファミリーは使いません。
ファミリー追加前に登録済みだった LoRA は、起動時のマイグレーションで `krea2` として扱われます。
**サンプル画像**も登録でき（`assets/lora_samples/<id>/`）、エージェントモードでは Grok が
出力と見比べる基準として参照します。

生成フォームではチップ型マルチセレクトで**複数の LoRA を同時適用**でき、各 LoRA に強度スライダーが付きます。
トリガーワードは選択順に連結されてトリガー欄へ自動反映（編集可）。
既定リファレンス音声は、選択した LoRA のうち最初に `default_audio` を持つものが採用されます。
選択内容はジョブの `params` にスナップショット保存されるため、後から登録を変えても過去ジョブは再現できます。

### 履歴・続き生成

画面下部の履歴ギャラリーでサムネイルをクリックすると詳細が開きます。

- **再実行**: 保存済みの `params` から同じ設定で再投入（seed はランダム化）
- **続きを生成**: 動画のラストフレームを開始フレームにした「動画生成」モードの新規ジョブを作成
  （元ジョブのワークフローが開始フレームを受け取れない場合は既定ワークフローに戻します）
- **削除**: ジョブと成果物を削除

音声ジョブはサムネイルの代わりに 🎵 が並び、詳細でプレイヤー再生できます。

履歴は無制限に保存され、削除は手動のみです。
ジョブとエージェントセッションには NSFW フラグが付き（Grok による自動判定、手動上書き可）、
ヘッダーの NSFW トグルがオフのあいだは履歴・ビューアから除外されます。
トグルの状態は `sessionStorage` に持つので、**タブを閉じて開き直す（新しいアクセス）と必ずオフに戻ります**。

---

## 設定

設定はヘッダーの「設定」から専用ページを開いて編集でき、`runtime/config.json` に保存されます。
ページは「接続 / Grok」「LoRA 管理」「モデル」の 3 タブ構成です。

| キー | 内容 | 既定 |
|---|---|---|
| `comfy_url` | ComfyUI の接続先 URL | `http://127.0.0.1:8188` |
| `comfy_api_key` | 認証ヘッダー用 API キー（Comfy Cloud など。不要なら空） | 空 |
| `grok_command` | grok CLI のコマンド名 / パス | `grok` |
| `grok_model` | 使用モデル | `grok-4.5` |
| `grok_workdir` | grok CLI の作業ディレクトリ | `runtime/grok-workdir` |
| `model_overrides` | モデルファイル名の上書き（`{"<workflow_id>/<node_id>.<field>": "<ファイル名>"}`） | `{}` |
| `model_choices` | 実行時に選べるモデルの候補リスト（キーは同上、値はファイル名の配列） | `{}` |
| `agent_grok_args` | エージェントモードで grok CLI に渡す追加フラグ（ツール許可） | `["--permission-mode", "auto"]` |
| `agent_grok_timeout` | エージェントの 1 ターンのタイムアウト秒（リサーチ・検分は長い） | `300` |
| `agent_max_plan_tasks` | 自走モードで 1 回のプラン提案に追加できる新規ジョブ数の上限（毎ジョブ確認 / 節目のみ確認では無制限） | `5` |

設定ページで編集できるのは接続 / Grok / LoRA / モデルの項目です。`agent_*` は
`PUT /api/settings` か `runtime/config.json` を直接編集して変更します。

### モデルファイル名の上書き

`workflow/` 配下の各ワークフローに書かれている UNET / CLIP / VAE / チェックポイント /
テキストエンコーダ / アップスケーラ / distil LoRA / talkvid LoRA / IC-LoRA / MoGe のファイル名は、
**Comfy Cloud 上で動作確認済みの構成**で、アプリはこれを既定値として使います。
自分の環境に別名のファイルしか無い場合は、設定ページの「モデル」タブで各行を書き換えてください。
一覧はワークフローから自動抽出され、**画像 / 動画 / 音声の大分類 → ワークフローごとの折りたたみ**（既定は閉）
の中に、既定値（テンプレートの値）と現在値が並んで表示されます。折りたたみの見出しには項目数と、
未保存の変更・既定から変更済みの件数がバッジで出ます。
変更した行はハイライトされ、[既定に戻す] でテンプレートの値へ戻せます。保存すると既定値と異なる
エントリだけが `model_overrides` に記録され、ジョブ投入時にワークフローへ適用されます
（キーはワークフロー ID でスコープされるため、テンプレート間で同じノード ID が衝突しません）。

### 実行ごとのモデル切り替え

同じ「モデル」タブの各行には**候補リスト**もあります。既定値とは別のファイル名を候補に足すと
（候補の追加欄は ComfyUI から取得できたファイル一覧で補完されます）、そのスロットは

- **生成フォーム**: ワークフローセレクトの下に「使用モデル: …」のセレクトが出て、実行ごとに選べる
- **エージェントモード**: システムプロンプトに候補が焼き込まれ、Grok がジョブごとに指定できる

ようになります。候補が既定値と合わせて 1 件しかないスロットには何も表示されません（従来どおり）。
選択はジョブの `model_overrides` として保存され、実行時に設定の既定値の上に重ねられます。候補に
無いファイル名や、そのジョブが走らせないワークフローのスロットを指定したジョブは 422 で拒否します。
再実行は同じモデルを引き継ぎ、続き生成では動画ワークフローぶんの指定だけを引き継ぎます。

### Tips: ローカル ComfyUI で動かす場合のモデル設定

テンプレートの既定モデル名は Comfy Cloud のストレージに合わせてあるため、ローカルの ComfyUI では
ファイル名が違うことがあります。ノード ID はモデルのドロップダウンを変えただけでは変わりません
（変わるのはノードの追加・削除・サブグラフの再構成をしたとき）。一方で `workflow/` の JSON は
**API フォーマットなので ComfyUI の GUI には直接読み込めません**。そのため変更方法は次の 3 つです。

1. **設定ページ「モデル」タブで上書きする（推奨・最も簡単）**: テンプレートを一切触らず、
   環境差分だけが `model_overrides` に保存されます。リポジトリの更新（テンプレート差し替え）とも
   衝突しません
2. **ワークフロー JSON をテキストエディタで直接編集する**: モデルファイル名の文字列だけを
   書き換える範囲ならノード ID は変わらないので安全です。既定値そのものが変わるので
   上書き設定は不要になります
3. **元の GUI 用ワークフロー（API エクスポートする前のもの）を持っている場合**は、GUI でモデルだけ
   変更して API フォーマットに再エクスポートし、`workflow/` のファイルを差し替えても構いません。
   ただしノードの追加・削除やサブグラフの編集をするとノード ID が変わり、アプリの注入マニフェスト
   （ノード ID 直指定）と食い違います

ID がズレた場合は起動時の検証と `GET /api/health`（ヘッダーの接続インジケーター）が検知して警告します。
その場合は `backend/app/workflows.py` のマニフェストを新しいノード ID に合わせて直してください。

### Comfy Cloud を使う場合

1. `comfy_url` に **`https://cloud.comfy.org`** を設定
2. [Comfy Cloud の API キー発行ページ](https://docs.comfy.org/development/cloud/overview)でキーを作成し、`comfy_api_key` に設定

アプリはホストが `comfy.org` のとき自動で Cloud 互換モードになります（エンドポイントに `/api` プレフィックス、認証は `X-API-Key` ヘッダー、`/view` の 302 署名 URL リダイレクト追従）。API アクセスは有料プラン（Standard 以上）が必要で、Free プランでは使えません。ワークフローが参照するモデル・LoRA・リファレンス音声は Cloud 側のストレージに存在している必要があります。

---

## ディレクトリ構成

```
backend/            FastAPI アプリ
  app/routers/      health / settings / loras / models_config / assets / options / chat / jobs / agent
  app/comfy.py      ComfyUI クライアント（/object_info, /upload/image, /prompt, /ws, /history, /view）
  app/workflows.py  ワークフロー登録簿と注入マニフェスト（ノード ID 直指定）+ プロンプト用カタログ
  app/workflow.py   テンプレートへのパラメータ注入・LoRA チェーン動的注入・解像度計算
  app/grok.py       grok CLI 呼び出し（LLM クライアントは差し替え可能な抽象化）
  app/prompts.py    チャット / エージェントのシステムプロンプト
  app/jobs.py       asyncio ジョブキューと実行、成果物取得・ラストフレーム抽出
  app/agent_*.py    エージェントのアクションプロトコル・実行ループ・セッション永続化
  app/nsfw.py       ジョブ / セッションの NSFW 判定
  tests/            pytest
frontend/           React + Vite + Tailwind の SPA（ビルド成果物は frontend/dist）
  src/components/   GenerateForm / AudioFields / ResultPane / HistoryGallery / ChatModal /
                    SettingsPage / agent/
docs/SPEC.md        仕様書
docs/AGENT-MODE.md  エージェントモード設計書
workflow/           ComfyUI ワークフロー（API フォーマット）テンプレート ※実行の正
  image/            krea2/ anima/ z-image/ qwen-image/（モデルファミリーごと）
  video/ltx2.3/     t2v / i2v / ia2v / id_lora / flf2v / ic_lora_image / ic_lora_motion
  audio/            ace_step1_5_xl_sft.json / stable_audio_3_medium_base.json
app.db              SQLite（jobs / loras / chat_sessions / agent_sessions）
outputs/            生成物（/outputs で静的配信）
assets/             アップロードした画像・音声・参照動画・LoRA サンプル（/assets で静的配信）
runtime/            config.json / grok 作業ディレクトリ / agent-sessions/
```

主な API（詳細は SPEC §9、起動後 `/docs` でも参照可）:

```
GET  /api/health                       ComfyUI / Grok 疎通と custom node チェック
GET  /api/options                      画像/動画/音声ワークフロー一覧（必要入力つき）・アスペクト比・
                                       LoRA ファイル一覧・アセット・ネガティブプリセット・
                                       実行時に選べるモデルスロット（model_slots / model_files）
GET/PUT /api/settings                  設定の取得・更新
GET/POST/PUT/DELETE /api/loras         アプリ内 LoRA 登録リスト
POST/DELETE /api/loras/{id}/samples    LoRA サンプル画像の登録・削除
GET/PUT /api/models                    ワークフローのモデルファイル名一覧・上書き・候補リスト
POST/GET /api/chat/sessions[/{id}]     Grok チャット
POST /api/jobs, GET /api/jobs?limit=…  ジョブ作成・履歴
GET/DELETE /api/jobs/{id}              詳細・削除
POST /api/jobs/{id}/rerun|continue     再実行・ラストフレームから続き生成
POST /api/jobs/{id}/nsfw               NSFW フラグの手動指定
POST/GET /api/assets/audio|image|video アセットのアップロード・一覧
POST/GET/DELETE /api/agent/sessions[/{id}]        エージェントセッション
POST /api/agent/sessions/{id}/messages|approve    発言・プラン承認
POST /api/agent/sessions/{id}/checkin|stop|nsfw   チェックイン応答・停止・NSFW 指定
POST /api/agent/sessions/{id}/attachments         ファイル添付（workdir の attachments/ へ保存）
GET  /api/agent/sessions/{id}/artifacts/{name}    成果物（メモ・検分フレーム）の配信
WS   /api/ws                           進捗配信（ジョブ進捗 + エージェント状態）
```

---

## 注意事項

- 本アプリは成人向けコンテンツをローカル生成する個人利用ツールです。生成物・プロンプトは
  すべてローカルにのみ保存され、ComfyUI と Grok CLI 以外へは送信されません
- LoRA で実在人物を無断利用しないでください（利用者責任）
- grok CLI はベータのため出力形式が変わる可能性があります。Grok が生成を拒否した場合は
  エラーが UI に表示されます
- 生成物 (`outputs/`, `assets/`)、`app.db`、`runtime/`、`.venv/`、`node_modules/`、`frontend/dist/` は
  すべて `.gitignore` 済みです
