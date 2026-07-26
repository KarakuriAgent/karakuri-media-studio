# Video Studio

ComfyUI 上のワークフロー `video-gen.json`（画像生成 → i2v 動画生成）を Web UI から実行し、
プロンプト作成を Grok に委譲、生成物と履歴をローカルに保存する個人利用向けの動画生成アプリです。

- バックエンド: Python 3.12 + FastAPI + SQLite（`app.db`）
- フロントエンド: React + Vite + Tailwind（ダークテーマの SPA 1 画面）
- 生成本体: ComfyUI（ローカル / LAN 上の別 PC / Comfy Cloud）
- プロンプト生成: Grok Build CLI（サブスクリプション認証、API キー不要）

詳細な仕様は [`docs/SPEC.md`](docs/SPEC.md)、プロンプト実例は [`docs/prompt-samples.md`](docs/prompt-samples.md) を参照してください。

---

## 前提条件

| 依存 | 内容 |
|---|---|
| ComfyUI | 稼働中であること（既定 `http://127.0.0.1:8188`）。Comfy Cloud も可 |
| custom nodes | ResolutionSelector / ComfySwitchNode / LTXV 系 / ComfyMath / ResizeImage 系など、`video-gen.json` が使うノード一式 |
| モデル | 画像: `redcraft23INT8INT4FP8_30Krea2` / CLIP `qwen3vl_4b` / VAE `qwen_image_vae`、動画: `sexgodPinkcherryLTX23_v16bDev` + distil LoRA + talkvid ID-LoRA、および使用する人物 LoRA |
| grok CLI | `curl -fsSL https://x.ai/cli/install.sh \| bash` でインストール後、一度 `grok` を起動してブラウザでサインイン（SuperGrok / X Premium+ のサブスクリプションで利用可） |
| ffmpeg | 動画からのラストフレーム抽出に使用（`ffmpeg` が PATH にあること） |
| Python | 3.12 以上 |
| Node.js | 18 以上（npm 同梱） |

不足している custom node は起動後 `GET /api/health`（ヘッダーの接続インジケーター）で検出され、
実際に投入する JSON に含まれる class_type のみをチェックします。

---

## セットアップと起動

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

テスト:

```bash
cd backend && ../.venv/bin/pytest        # バックエンド
cd frontend && npx tsc --noEmit && npm run build   # フロントエンド
```

---

## 使い方

### 3 つの生成モード

| モード | 内容 |
|---|---|
| フル生成 (t2i → i2v) | 画像を生成し、そのままそれを開始フレームとして動画を生成 |
| 画像から (i2v) | アップロード画像 / 過去生成のラストフレームを開始フレームに動画を生成。画像プロンプト・LoRA は無効化 |
| 画像のみ | 画像生成だけを実行。開始フレーム候補を量産して良いものを「画像から」モードに渡す用途 |

左ペインでモードを選び、プロンプト・アスペクト比・メガピクセル・LoRA・リファレンス音声・
秒数 / fps / seed を設定して「実行」。進捗は WebSocket で右ペインにリアルタイム表示され、
完了すると生成画像・動画・ラストフレームがプレビューされます。

秒数に上限はありませんが、長尺は VRAM 次第で ComfyUI 側がエラーになります。

### 開始フレームの指定（画像からモード）

- 画像ファイルを開始フレーム欄に**ドラッグ&ドロップ**すると自動アップロードされます
- 「画像をアップロード」ボタン、または登録済みアセットのセレクトからも選べます
- 履歴のラストフレームのサムネイルをクリックすると、その画像が開始フレームになります

### Grok チャットでプロンプト作成

プロンプトは手動入力が基本ですが、「Grokで生成」ボタンでチャットモーダルを開けます。

1. フォームの現在値（モード・選択 LoRA とトリガーワード・秒数・下書き）がコンテキストとして渡ります
2. 「かおりが楽しそうにダンスをしている」程度の指示を入力すると、Grok が場所・服装・照明・
   カメラ・表情・セリフ / 音などを**質問で聞き返します**（「おまかせ」と言えば Grok が補完）
3. 情報が揃うと `image_prompt` / `video_prompt` の最終案が JSON で提示されます
4. 「フォームに反映」でプロンプト欄に挿入。反映後も会話を続けて再調整できます

チャット履歴は `chat_sessions` に保存され、実行したジョブに紐付きます。
grok CLI は空の作業ディレクトリ（`runtime/grok-workdir/`）を cwd にして実行されるため、
プロジェクトのファイルには触れません。

### LoRA 管理

設定画面の LoRA 管理タブで、人物 LoRA を登録（表示名 / ComfyUI 上のファイル名 / トリガーワード /
既定強度 / 既定リファレンス音声 / 並び順）できます。ファイル名は ComfyUI の `/object_info` から
取得した一覧から選ぶので typo が起きません。

生成フォームではチップ型マルチセレクトで**複数の LoRA を同時適用**でき、各 LoRA に強度スライダーが付きます。
トリガーワードは選択順に連結されてトリガー欄へ自動反映（編集可）。
既定リファレンス音声は、選択した LoRA のうち最初に `default_audio` を持つものが採用されます。
選択内容はジョブの `params` にスナップショット保存されるため、後から登録を変えても過去ジョブは再現できます。

### 履歴・続き生成

画面下部の履歴ギャラリーでサムネイルをクリックすると詳細が開きます。

- **再実行**: 保存済みの `workflow_json` を使って同じ設定で再投入（seed はランダム化）
- **続きを生成**: 動画のラストフレームを開始フレームにした「画像から」モードの新規ジョブを作成
- **削除**: ジョブと成果物を削除

履歴は無制限に保存され、削除は手動のみです。

---

## 設定

設定はヘッダーの「設定」から編集でき、`runtime/config.json` に保存されます。

| キー | 内容 | 既定 |
|---|---|---|
| `comfy_url` | ComfyUI の接続先 URL | `http://127.0.0.1:8188` |
| `comfy_api_key` | 認証ヘッダー用 API キー（Comfy Cloud など。不要なら空） | 空 |
| `grok_command` | grok CLI のコマンド名 / パス | `grok` |
| `grok_model` | 使用モデル | `grok-4.5` |
| `grok_workdir` | grok CLI の作業ディレクトリ | `runtime/grok-workdir` |

---

## ディレクトリ構成

```
backend/            FastAPI アプリ
  app/routers/      health / settings / loras / assets / options / chat / jobs
  app/comfy.py      ComfyUI クライアント（/object_info, /upload/image, /prompt, /ws, /history, /view）
  app/workflow.py   video-gen.json のモード別書き換え・LoRA チェーン動的注入
  app/grok.py       grok CLI 呼び出し（LLM クライアントは差し替え可能な抽象化）
  app/jobs.py       asyncio ジョブキューと実行、成果物取得・ラストフレーム抽出
  tests/            pytest
frontend/           React + Vite + Tailwind の SPA（ビルド成果物は frontend/dist）
docs/SPEC.md        仕様書
video-gen.json      ComfyUI ワークフロー（API フォーマット）テンプレート
app.db              SQLite（jobs / loras / chat_sessions）
outputs/            生成物（/outputs で静的配信）
assets/             アップロードした画像・音声（/assets で静的配信）
runtime/            config.json と grok 作業ディレクトリ
```

主な API（詳細は SPEC §9、起動後 `/docs` でも参照可）:

```
GET  /api/health                       ComfyUI / Grok 疎通と custom node チェック
GET  /api/options                      アスペクト比・LoRA ファイル一覧・アセット・ネガティブプリセット
GET/PUT /api/settings                  設定の取得・更新
GET/POST/PUT/DELETE /api/loras         アプリ内 LoRA 登録リスト
POST/GET /api/chat/sessions[/{id}]     Grok チャット
POST /api/jobs, GET /api/jobs?limit=…  ジョブ作成・履歴
GET/DELETE /api/jobs/{id}              詳細・削除
POST /api/jobs/{id}/rerun|continue     再実行・ラストフレームから続き生成
POST/GET /api/assets/audio|image       アセットのアップロード・一覧
WS   /api/ws                           進捗配信
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
