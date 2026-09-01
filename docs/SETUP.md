# セットアップ

このアプリを新しいマシンに導入して起動するまでと、環境まわりの設定・トラブル対処を
まとめたものです。**エージェントに任せるなら
[`.agents/skills/karakuri-setup/SKILL.md`](../.agents/skills/karakuri-setup/SKILL.md)**
（`scripts/setup.sh status` から未完了の段階だけを進めてくれます）。

---

## 前提条件

| 依存 | 内容 |
|---|---|
| ComfyUI | 稼働中であること（既定 `http://127.0.0.1:8188`）。Comfy Cloud も可 |
| custom nodes | ResolutionSelector / ComfySwitchNode / CustomCombo / MiniMaxH3 系 / ComfyMath / ResizeImage 系 / ResizeAndPadImage / MoGe 系 / LoadVideo など、`workflow/` 配下のワークフローが使うノード一式 |
| custom nodes | MiniMax H3 Image（t2i / i2i / r2i）を使う場合は [ComfyUI-MiniMax-H3-Image-Studio](https://github.com/astropuzzo/ComfyUI-MiniMax-H3-Image-Studio)（`H3TextToImagePrepare` / `H3ImageToImagePrepare` / `H3ReferenceEditPrepare` / `H3SamplingSettings` / `H3ImageDecode` / `H3ImageFrameSelector`）。`deploy/runpod/custom_nodes.txt` にコミットを固定してあるので RunPod では自動で入ります |
| custom nodes（任意） | MiniMax H3 の Turbo / Optimized ワークフロー（動画の i2v / r2v・画像の t2i / i2i / r2i）を使う場合のみ SageAttention 本体と [ComfyUI-KJNodes](https://github.com/kijai/ComfyUI-KJNodes)、および `SolAttnPatch` / `MiniMaxH3TurboLoRA` / `MiniMaxH3MemoryEfficientSageAttentionPatch` / `MiniMaxH3SigmaShift` / `SpectrumApplyMiniMaxH3` を提供する custom node。Turbo / Optimized 以外のワークフローには不要（Optimized は `MiniMaxH3TurboLoRA` だけ使わない） |
| custom nodes（任意） | ドラマスタジオの「ラテント連続性」（連続カット・`minimax_h3_r2v_context` と、起点になる通常カットの `minimax_h3_*_save`）を使う場合のみ、`MiniMaxH3MotionContext` / `MiniMaxH3MotionContextLoadLatent` / `MiniMaxH3MotionContextSaveLatent` を提供する [ComfyUI-H3-Motion-Context](https://github.com/NikoDemon80/ComfyUI-H3-Motion-Context) と、`MiniMaxH3MotionContextTrim` を提供する ComfyUI-MiniMaxH3-Contex-Loop。Comfy Cloud には入れられないので、その接続先ではこの機能が使えません |
| モデル | **使うワークフローのぶんだけ**あれば十分です（各テンプレートの既定ファイル名は SPEC §3.3）。足りないものは後述の「不足モデルの自動ダウンロード」で取得できます |
| grok CLI | `curl -fsSL https://x.ai/cli/install.sh \| bash` でインストール後、一度 `grok` を起動してブラウザでサインイン（サーバーでは `grok --device-auth`）。SuperGrok / X Premium+ のサブスクリプションで利用可。**プロンプト作成のチャットに加えて、画像ワークフローの「Grok Imagine」もこの CLI で走ります**（サインインしていないとそちらは失敗します。設定ページの「grok CLI の接続確認」で確かめられます） |
| ffmpeg | 動画からのラストフレーム抽出に使用（PATH にあること） |
| Python / Node.js | 3.12 以上 / 18 以上 |

不足している custom node やワークフローのノード ID ズレは、起動時とヘッダーの接続
インジケーター（`GET /api/health`）が検出して警告します。

---

## セットアップと起動

```bash
git clone https://github.com/KarakuriAgent/karakuri-media-studio.git
cd karakuri-media-studio

./run.sh          # 本番: 依存を整えて frontend をビルドし http://127.0.0.1:8000 で起動
./run.sh --dev    # 開発: uvicorn --reload (:8000) と vite dev (:5173) を並行起動
```

`run.sh` は初回に venv 作成・`npm install`（frontend と remotion）・`npm run build`
まで面倒を見ます。
待受などの設定は環境変数か、リポジトリ直下の `.env`（gitignore 済み）で渡します
（シェルの環境変数が優先）。

```bash
# .env
HOST=0.0.0.0
PORT=8080
COMFY_MODELS_DIR=/path/to/ComfyUI/models   # 任意（後述の自動ダウンロード用）
```

`run.sh` を使わず手で起動する場合:

```bash
python3.12 -m venv .venv && .venv/bin/pip install -r backend/requirements.txt
cd frontend && npm install && npm run build && cd ..
PYTHONPATH=backend .venv/bin/uvicorn app.main:app --port 8000
```

### Docker (docker compose) で起動する

```bash
npm --prefix frontend install && npm --prefix frontend run build  # 初回のみ
npm --prefix remotion install                                     # Remotion を使うなら

./compose.sh up -d --build   # 起動
./compose.sh logs -f         # ログ
./compose.sh down            # 停止
```

「ランタイムだけコンテナ、データとワークスペースはローカル」という設計で、`compose.sh` が
リポジトリをホストと同じ絶対パスにマウントし、コンテナをローカルユーザーの `UID:GID` で
動かします（`./run.sh` 実行と行き来でき、生成ファイルの所有者も変わりません）。

- grok CLI はホストの `~/.grok` をマウントして使うので、**事前にホスト側でインストールと
  サインイン**を済ませてください
- ComfyUI に `http://127.0.0.1:8188` を使っている場合、コンテナからは届きません。設定画面の
  ComfyUI URL を `http://host.docker.internal:8188` か LAN の IP に変えてください
- `.env` の `COMFY_MODELS_DIR` は、同じ絶対パスでコンテナにもマウントされます
  （Remotion は同梱の `remotion/` をリポジトリごとマウントするので設定は要りません。
  依存だけはホストで `npm --prefix remotion install` を済ませてください）
- `docker compose` を直接使うときは、リポジトリの実体パスから
  `UID=$(id -u) GID=$(id -g) docker compose up -d` のように実行してください
  （サービス名は `media-studio`。旧名 `video-studio` のコンテナが残っていれば `docker rm -f` で片付けます）

### 開発

```bash
cd backend && ../.venv/bin/pytest                  # バックエンド
cd frontend && npm run build && npx vitest run     # フロントエンド（型チェック込み）
cd remotion && npm run typecheck                   # Remotion（使う場合のみ）
```

---

## Remotion 連携（同梱・既定 OFF）

MV やモーショングラフィックスを焼く Remotion プロジェクトを `remotion/` に同梱して
います。依存は `run.sh` が初回に入れる（Docker で動かす場合はホスト側で
`npm --prefix remotion install`）ので、設定画面の「Remotion 連携」を **ON** にすれば
使えます（使うのは常に同梱の `remotion/` です）。書き方は
[`.agents/skills/karakuri-remotion/SKILL.md`](../.agents/skills/karakuri-remotion/SKILL.md) と
[`remotion/README.md`](../remotion/README.md) にあります。

> **ライセンスの注意**: Remotion は MIT などのオープンソースライセンスではなく、独自の
> Remotion License で提供されています。個人利用および従業員 3 名以下の会社は無償ですが、
> それ以上の規模の会社での利用には会社ライセンス（有償）の購入が必要です。既定を OFF に
> しているのはこのためです。有効にする前に
> <https://www.remotion.dev/license> を確認し、条件を満たすことを確かめてください。

---

## 音源解析（歌詞つき MV。オプトイン）

MV の秒（歌詞のアライン・onset・ビート）を出す音源解析（`mode: "audio_analysis"`）は、
重い依存（torch / faster-whisper / stable-ts / librosa）を使うのでアプリの環境とは
**別の venv** で回します。

ホストで動かしている（`./run.sh`）場合:

```bash
python3.12 -m venv .venv-audio
.venv-audio/bin/pip install -r backend/requirements-optional.txt
```

Docker で動かしている場合は、**コンテナの中の python で** venv を作ります（ホストの
python で作った venv は、コンテナに無い `/usr/bin/python3.12` を指すので中では動き
ません）。リポジトリの中に作れば、ホストと同じ絶対パスでコンテナからも見えます:

```bash
docker exec video-studio-media-studio-1 bash -c \
  "cd $(pwd -P) && python3.12 -m venv .venv-audio \
   && .venv-audio/bin/pip install -r backend/requirements-optional.txt"
```

作ったら設定の「接続 / Grok」タブの `audio_analysis_python` に **その venv の python の
絶対パス**（例 `/path/to/video-studio/.venv-audio/bin/python`）を入れて保存します。
空のままだとアプリ自身の python で回そうとして、依存が無ければ 400 で断ります。

GPU は `docker-compose.yml` の `deploy.resources.reservations.devices`（nvidia）で
コンテナに渡しています。nvidia-container-toolkit が無い環境では compose が起動を
拒むので、その 6 行を消してください（解析は CPU で動きます。GPU が ComfyUI と競合して
メモリ不足になったときも、ワーカーが自動で CPU にやり直します）。venv をリポジトリの
外に置くときは `AUDIO_ANALYSIS_VENV` の行を有効にして、`.env` に
`AUDIO_ANALYSIS_VENV=/path/to/venv` を書いてください（マウント先はホストと同じ絶対パス）。

---

## 不足モデルの自動ダウンロード（オプトイン）

ワークフローが使うモデルが手元に無いとき、設定ページからダウンロードして ComfyUI の
models ディレクトリへ直接置けます（ComfyUI の再起動は不要）。**`.env` に
`COMFY_MODELS_DIR` を書いたときだけ**設定画面に現れる機能で、書かなければ UI には一切出ません。

1. `.env` に `COMFY_MODELS_DIR=/path/to/ComfyUI/models` を書く
2. 再起動する（ホスト: `./run.sh` / Docker: `./compose.sh up -d` — compose が同じパスを
   コンテナにマウントします）
3. gated な Hugging Face リポジトリや要ログインの Civitai ファイルを使うなら、設定ページの
   「接続 / Grok」タブで **Hugging Face トークン** / **Civitai APIキー** を入れる
4. 「モデル」タブで **未検出** バッジが付いた行に URL を入れて [DL]。進捗はその行に出ます

保存先（`checkpoints` / `diffusion_models` / `loras` など）はローダーの種類から自動で決まります。
Comfy Cloud 接続では ComfyUI のファイルシステムに届かないため使えません（UI にも出ません）。
配置先の対応表や認証・リダイレクトの扱いは SPEC §3.3 を参照してください。

---

## ComfyUI を RunPod で動かす（オプトイン）

手元に GPU が無い / 大きいモデルを回したい場合、ComfyUI を **RunPod の Pod
（GPU 時間貸し）**に置けます。ジョブを実行したときに ComfyUI へ繋がらなければ
アプリが Pod を立ち上げ、繋がるまで待ってから投入します。使い終わった Pod は
Pod 自身の watchdog がアイドル 10 分で terminate するので、消し忘れで課金が
続くことはありません。

1. RunPod に Network Volume とテンプレートを登録する（イメージは公開済みの
   `ghcr.io/karakuriagent/karakuri-comfyui:latest` をそのまま使えばビルド不要）
2. 設定 →「接続 / Grok」で **RunPod の Pod を自動起動する** をオンにし、
   API キー / テンプレート ID / GPU 種別 / Network Volume ID を入れる
3. RunPod ComfyUI URL には Pod の Cloudflare Tunnel のホスト名を入れ、接続先を
   **RunPod** にする（自動起動は接続先が RunPod のときだけ働きます）

セットアップ手順は
[`RUNPOD-QUICKSTART.md`](RUNPOD-QUICKSTART.md)（公開イメージを
そのまま使う人向け）にまとめてあります。イメージを自分で変えたい場合の
ビルド・push を含むフル手順は [`deploy/runpod/README.md`](../deploy/runpod/README.md)、
設計上の決め事は SPEC §5.1 を参照してください。

---

## 設定

ヘッダーの「設定」から開き、`runtime/config.json` に保存されます。タブは「接続 / Grok」
「LoRA 管理」「モデル」の 3 つです。

| キー | 内容 | 既定 |
|---|---|---|
| `comfy_target` | 使う接続先（`comfy_cloud` / `runpod` / `local`）。生成フォーム上部のプルダウンと同じ値 | `local` |
| `local_comfy_url` | ローカル / LAN の ComfyUI の URL（API キーなし） | `http://127.0.0.1:8188` |
| `runpod_comfy_url` / `runpod_comfy_api_key` | RunPod の Pod 上の ComfyUI の URL と API キー（任意） | 空 |
| `comfy_cloud_api_key` | ComfyCloud の API キー（エンドポイントは `https://cloud.comfy.org` 固定） | 空 |
| `grok_command` / `grok_model` | grok CLI のコマンドと使用モデル | `grok` / `grok-4.5` |
| `grok_workdir` | プロンプト作成チャットが LLM CLI を回す作業ディレクトリ | `runtime/grok-workdir` |
| `grok_media_timeout` / `grok_media_workdir` | Grok Imagine の 1 枚あたりの制限時間（秒）と専用の作業ディレクトリ（プロンプト作成のチャットとは分けます） | `300` / `runtime/grok-media-workdir` |
| `remotion_enabled` | Remotion 連携（`mode: "remotion"`）を使うか。**ライセンスの都合で既定 OFF**（上記） | 無効 |
| `audio_analysis_python` | 音源解析（`mode: "audio_analysis"`）を回す python の絶対パス。重い依存（torch / faster-whisper / stable-ts / librosa）はアプリの環境に入れず、`backend/requirements-optional.txt` を入れた別の venv をここで指す | 空（アプリ自身の python） |
| `agent_grok_args` | LLM CLI に足すフラグ（ツール権限）。**空にすると CLI のツールが無効**になります | `--permission-mode auto` |
| `agent_use_acp` | CLI のターンを ACP で回す（実行中の活動をチャットに出す） | オン |
| `hf_token` / `civitai_api_key` | モデルダウンロード用のトークン | 空 |
| `model_overrides` / `model_choices` | **接続先ごと**のモデルファイル名の上書きと、実行ごとに選べる候補リスト | `{}` |
| `runpod_*` | RunPod Pod の自動起動（有効化 / APIキー / テンプレート ID / GPU 種別 / Network Volume ID） | 無効 |
| `agent_grok_timeout` | LLM CLI 1 回あたりの制限時間（秒。0 = 無制限） | `300` |
| `external_api_key` / `external_max_pending_takes` | 外部 API（`/api/v1`）の共有キーと、未完了ジョブ・走っている書き出しの上限（0 = 無制限） | 空 / `20` |

**モデルタブ**と**LoRA 管理タブ**の先頭には [対象の接続先] のプルダウンがあり、
**モデルの指定と LoRA 登録は接続先ごとに保存されます**（環境によって入っているファイルが
違うため）。既定値はテンプレートの値（＝ Comfy Cloud で動作確認済みの構成）で、同じ行の
**候補リスト**に別のファイル名を足すと、そのスロットは生成フォームと API から
**実行ごとに切り替え**られるようになります。詳細は SPEC §3.3。

不足しているモデル（**未検出**バッジ）は行の [DL]、まとめてなら [全DL] で落とせます。
落とし先は選んでいる接続先で、**ローカル**なら `.env` の `COMFY_MODELS_DIR`、**RunPod**なら
Pod 側の models ディレクトリです（RunPod は Pod のダウンロード API を使うので、
`deploy/runpod` のイメージを作り直す必要があります。Pod 起動時の一括ダウンロードは
行いません）。ComfyCloud はモデルが Comfy Cloud 側の
管理なのでダウンロードできません。

**接続先**は ComfyCloud / RunPod / ローカルの 3 プロファイルを設定に持ち、「接続 / Grok」
タブの「ComfyUI 接続先」でそれぞれの URL・API キーを登録します。実際にどれを使うかは
同じ場所の [接続先] か、**生成フォーム最上部のプルダウン**で切り替えます（サーバー側の
設定に保存されるので、次回起動時も前回の選択が使われます）。

**Comfy Cloud** を使う場合は ComfyCloud の APIキーに
[発行したキー](https://docs.comfy.org/development/cloud/overview)を入れて接続先を ComfyCloud に
します（エンドポイントは `https://cloud.comfy.org` 固定。API アクセスは Standard 以上のプランが
必要）。旧バージョンの `comfy_url` / `comfy_api_key` は、初回読み込み
時にどれか 1 つのプロファイルへ自動で移されます。

---

## よくあるトラブル

| 症状 | 対処 |
|---|---|
| RunPod 接続で「ComfyUI が起動していません」と出る | Pod を落としている間は正常な表示。自動起動が有効ならジョブ投入時に Pod が立ち上がります（モデル名は手入力でも続行可） |
| ヘッダーの接続インジケーターが赤い | ComfyUI が起動しているか、選んでいる接続先の URL が正しいか確認。Docker からは `127.0.0.1` が届かないので `host.docker.internal` か LAN IP に |
| ジョブが「ファイルが見つからない」で失敗する | モデルのファイル名が環境と違う可能性。設定の「モデル」タブで上書きするか、[DL] で取得（SPEC §3.3） |
| custom node 不足・ノード ID ズレの警告が出る | 不足ノードを ComfyUI に導入。テンプレートを差し替えた場合は `backend/app/workflows.py` のマニフェストを合わせる（SPEC §3.0） |
| Grok が応答しない / 認証エラー | ホスト側で `grok` を一度起動してサインイン。Docker では `~/.grok` のマウントが必要 |
| ラストフレームが取れない | `ffmpeg` が PATH にあるか確認 |

---

## 注意事項

- 本アプリは成人向けコンテンツをローカル生成する個人利用ツールです。生成物・プロンプトは
  すべてローカルにのみ保存され、ComfyUI と Grok CLI 以外へは送信されません
- LoRA で実在人物を無断利用しないでください（利用者責任）
- grok CLI はベータのため出力形式が変わる可能性があります
- 生成物 (`outputs/`, `assets/`, `library/`)、`app.db`、`runtime/`、`.venv/`、`node_modules/`、
  `frontend/dist/` はすべて `.gitignore` 済みです
