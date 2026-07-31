# ComfyUI を RunPod の Pod で動かす

Karakuri Media Studio のバックエンド（ComfyUI）を **RunPod の Pod（GPU 時間貸し）**
に置くための Docker イメージ一式です。アプリ本体は手元の PC / サーバーで動かし、
生成のときだけ Pod が立ち上がって、使い終われば自分で消えます。

```
[手元] Karakuri Media Studio ──HTTPS──> Cloudflare Tunnel ──> [RunPod Pod]
                                                               caddy :8189（APIキー認証）
                                                                 └─> ComfyUI 127.0.0.1:8188
                                                               watchdog（アイドルで自分を terminate）
                                                              /workspace = Network Volume
                                                               （ComfyUI 本体・custom nodes・モデル）
```

- **起動**はアプリがやります（設定で有効にすると、ジョブ実行の直前に Pod を作る）
- **停止**は Pod の中の `watchdog.py` がやります（アプリが落ちていても課金が止まる）
- ComfyUI 本体・カスタムノード・モデルは**イメージに焼かず** Network Volume に置くので、
  モデルを足すのにイメージの再ビルドは要りません（モデルはアプリの設定ページの
  [DL] / [全DL] から Pod のダウンロード API 経由で入れます）

---

## ファイル

| ファイル | 役割 |
|---|---|
| `Dockerfile` | CUDA 12.8 + PyTorch cu128 + caddy + cloudflared のランタイム |
| `entrypoint.sh` | 冪等な起動処理（clone → custom nodes → 各プロセス起動） |
| `custom_nodes.txt` | 追加で入れる custom node（`<git-url> <commit>`）。既定は空 |
| `download_models.py` | 1 ファイルを落とす処理（`model_api.py` が使う。レジューム・再試行つき） |
| `Caddyfile` | `:8189` の認証つきリバースプロキシ（`/studio/models/*` だけ DL API へ） |
| `watchdog.py` | アイドルが続いたら自分の Pod を terminate |
| `model_api.py` | アプリの [DL] / [全DL] を受けるモデルダウンロード API（`127.0.0.1:8190`） |

---

## モデルの入れ方

**起動時の一括ダウンロードは行いません。** Pod を上げたら、アプリの
**設定 → モデル / LoRA 管理**で [対象の接続先] を **RunPod** にして、行ごとの
**[DL]** か上部の **[全DL]**（未検出かつ取得元 URL 登録済みを一括）を押してください。
Pod の中のダウンロード API（`model_api.py`）が `/workspace/ComfyUI/models` の所定の
場所に置き、進捗はアプリの設定画面にそのまま出ます。Network Volume に残るので、
Pod を作り直しても入れ直しは要りません。

- 取得元 URL は設定ページで登録します（モデルタブの [取得元 URL]、LoRA は登録
  フォームの「取得元 URL」欄）。キーはファイル名なので、同じファイルを使う行では
  共有されます
- 置き場所（`diffusion_models` / `loras` など）はローダーの種類から決まります
  （`backend/app/workflow.py` の `MODEL_SUBFOLDERS`）
- Hugging Face の gated リポジトリや Civitai の要ログインファイルには、**テンプレートの
  環境変数** `HF_TOKEN` / `CIVITAI_API_KEY` が使われます（アプリ側の設定は Pod には
  渡りません）

> **配布 URL の無いモデル（自作 LoRA など）はダウンロードできません。**
> 手元にしか無い LoRA / チェックポイントは**ボリュームに直接アップロード**して
> ください。置き場所は `/workspace/ComfyUI/models/loras/`（LoRA の場合）などです。
> `runpodctl send` / `receive` か、Jupyter のファイルブラウザからのアップロードが
> 手軽です。ファイル名はアプリの設定に入れた名前と**完全に一致**させてください
> （ComfyUI はこの名前で探します）。

```bash
# 手元
runpodctl send my_lora.safetensors
# Pod の web ターミナル / Jupyter
runpodctl receive <コード>        # /workspace/ComfyUI/models/loras に置く
```

---

## 1. イメージをビルドして push

RunPod からは公開レジストリ（Docker Hub / GHCR など）が見える必要があります。

```bash
cd deploy/runpod

docker build -t <user>/karakuri-comfyui:latest .
docker push <user>/karakuri-comfyui:latest
```

ComfyUI のバージョンは既定で `master`（起動のたびに最新へ追従）です。特定のバージョンに
固定したいときは、ビルド引数にタグやコミットハッシュを渡します。

```bash
docker build --build-arg COMFYUI_REF=v0.27.0 -t <user>/karakuri-comfyui:latest .
```

> `custom_nodes.txt` はイメージに入るので、内容を変えたら**ビルドし直して push**
> してください。モデルを足すだけならビルドは不要です（設定ページの [DL] / [全DL]）。

---

## 2. Network Volume を作る

RunPod のコンソール → **Storage** → **Network Volume** で作ります。

- リージョンは、使いたい GPU（RTX PRO 6000 Blackwell / RTX 5090）の在庫があるところ
- 容量はモデルの合計 + 100 GB 程度（LTX 2.3 系を一式入れるなら 500 GB〜）
- Pod には `/workspace` としてマウントされます

ここに ComfyUI 本体・カスタムノード・モデルが置かれ、Pod を作り直しても残ります。
初回だけ ComfyUI とカスタムノードの用意に時間がかかり、2 回目以降は数十秒で上がります
（モデルは設定ページの [DL] / [全DL] で入れます）。

---

## 3. Cloudflare Tunnel を作る

Pod は起動のたびに IP が変わるので、**固定のホスト名**をトンネルで用意します。
（アプリ側の `runpod_comfy_url` を毎回書き換えなくて済みます。）

1. Cloudflare にドメインを 1 つ載せておく（無料プランで可）
2. Cloudflare Zero Trust → **Networks** → **Tunnels** → **Create a tunnel**
3. コネクタは **Cloudflared** を選び、名前を付ける（例: `comfy`）
4. 表示されるインストールコマンドの中の **トークン**（`eyJ…` の長い文字列）を控える
   → これが `CF_TUNNEL_TOKEN`
5. **Public Hostname** タブで 1 件追加する
   - Subdomain: `comfy` / Domain: 自分のドメイン（例 `comfy.example.com`）
   - Service: **HTTP** / URL: `127.0.0.1:8189`
6. 保存

これで `https://comfy.example.com` が Pod の caddy（:8189）に繋がります。
Pod が落ちている間はトンネルも切れるので、Cloudflare が 502 を返します
（アプリはそれを「ComfyUI が落ちている」と判断して Pod を起動します）。

> トンネルを使わない場合は、RunPod のポート公開（`8189/http`）でも動きます。
> ただし Pod ごとに URL が変わるので、そのつど設定の `runpod_comfy_url` を直してください。

---

## 4. テンプレートを登録する

RunPod のコンソール → **Templates** → **New Template**。

| 項目 | 値 |
|---|---|
| Container Image | `<user>/karakuri-comfyui:latest` |
| Container Disk | 30 GB 程度（実体は Network Volume 側） |
| Volume Mount Path | `/workspace` |
| Expose HTTP Ports | `8189`（Cloudflare Tunnel を使うなら空でも可） |

**Environment Variables**:

| 変数 | 必須 | 内容 |
|---|---|---|
| `COMFY_API_KEY` | 推奨 | プロキシの API キー。アプリの `runpod_comfy_api_key` と同じ値。空にすると**誰でも ComfyUI を叩ける**ので、トンネルで公開するなら必ず設定する |
| `CF_TUNNEL_TOKEN` | ○ | 手順 3 で控えたトンネルのトークン |
| `HF_TOKEN` | 任意 | gated な Hugging Face リポジトリからモデルを落とす場合（[DL] / [全DL] が使う） |
| `CIVITAI_API_KEY` | 任意 | 要ログインの Civitai ファイルを落とす場合（同上） |
| `RUNPOD_API_KEY` | ○ | watchdog が自分を terminate するのに使う |
| `IDLE_TIMEOUT_MINUTES` | 任意 | アイドルで終了するまでの分数（既定 `10`） |
| `STARTUP_GRACE_MINUTES` | 任意 | 起動直後に絶対に終了しない分数（既定 `15`。初回のモデル取得ぶん） |
| `COMFY_ARGS` | 任意 | ComfyUI に足す起動引数（例 `--highvram`） |

`RUNPOD_POD_ID` は RunPod が自動で入れるので、自分で設定する必要はありません。

保存すると **テンプレート ID**（`xxxxxxxxxxxx` のような文字列）が表示されます。
アプリの設定に入れるのでこれを控えてください。

---

## 5. アプリ側の設定

Karakuri Media Studio の **設定 → 接続 / Grok** の「ComfyUI 接続先」→ **RunPod** で:

| 項目 | 値 |
|---|---|
| RunPod ComfyUI URL | `https://comfy.example.com`（手順 3 のホスト名） |
| RunPod ComfyUI APIキー | テンプレートの `COMFY_API_KEY` と同じ値 |
| **RunPod の Pod を自動起動する** | オン |
| RunPod APIキー | RunPod → Settings → API Keys で発行したもの |
| テンプレート ID | 手順 4 で控えたもの |
| GPU 種別 | `NVIDIA RTX PRO 6000 Blackwell Workstation Edition` / `NVIDIA GeForce RTX 5090` など（RunPod の gpuTypeId をそのまま） |
| Network Volume ID | 手順 2 で作ったボリュームの ID |

これで、ジョブを実行したときに ComfyUI へ繋がらなければ Pod が作られ、繋がるまで
待ってから（最大 15 分）ワークフローが投入されます。待っている間は生成画面に
「Pod の起動を待っています…」と出ます。

GPU が確保できないときは**そのままエラーになります**（勝手に別の GPU や
SECURE クラウドへ振り替えると、意図しない課金になるため）。設定の GPU 種別を
変えて再実行してください。

モデルの指定・LoRA 登録は**接続先ごと**に保存されます（設定ページの「モデル」
「LoRA 管理」タブの [対象の接続先] で切り替え）。RunPod を選んでいるあいだの
[DL] / [全DL] は、下の Pod 側 API 経由で **Pod の** models ディレクトリに落ちます。

---

## モデルのダウンロード API（Pod 側）

設定ページの [DL] / [全DL] は、Pod の中で動く小さな API（`model_api.py`、
`127.0.0.1:8190`）に依頼します。公開しているのは caddy 経由の
`/studio/models/*` だけで、**認証は ComfyUI と同じ `COMFY_API_KEY`** です。

| メソッド | パス | 内容 |
|---|---|---|
| POST | `/studio/models/download` | `{"filename", "url", "subfolder"}` を受けて取得を開始（すぐ返る） |
| GET | `/studio/models/downloads` | 進行中・直近の完了 / 失敗の一覧 |

実体は `download_models.py`（`.part` への追記と `Range` レジューム、リダイレクト
ごとの認証、指数バックオフの再試行）なので、数十 GB のファイルでも途中で切られた
ぶんは続きから取り直します。`HF_TOKEN` / `CIVITAI_API_KEY` は Pod の環境変数を
そのまま使います。

> **この API を使うにはイメージの作り直しが必要です。** 既存の Pod / テンプレートの
> ままだと `/studio/models/*` が 404 になり、アプリは「Pod のダウンロード API が
> ありません」と出します。手順 1 のビルドと push をやり直し、テンプレートのイメージ
> タグを更新（同じ `:latest` なら Pod を作り直すだけ）してください。Network Volume の
> 中身（ComfyUI 本体・モデル）はそのまま使えます。

---

## 動作の確認

ローカルで認証だけ試すなら、GPU 無しでも caddy の挙動は見られます。

```bash
docker run --rm -p 8189:8189 -e COMFY_API_KEY=secret \
  -v "$PWD/workspace:/workspace" <user>/karakuri-comfyui:latest

curl -i http://127.0.0.1:8189/system_stats                      # 401
curl -i -H 'Authorization: Bearer secret' http://127.0.0.1:8189/system_stats
curl -i -H 'X-API-Key: secret' http://127.0.0.1:8189/system_stats
```

Pod のログ（RunPod のコンソール）には、`[entrypoint]` / `[models]` /
`[watchdog]` / `[model-api]` のプレフィックスで各段階の進み具合が出ます。

---

## つまずきやすいところ

| 症状 | 原因と対処 |
|---|---|
| 15 分待ってもアプリが繋がらない | Pod のログで ComfyUI が上がっているか確認。初回は ComfyUI とカスタムノードの用意に時間がかかる |
| ジョブが「ファイルが見つからない」で失敗する | そのモデルが Pod に入っていない。設定 → モデル / LoRA 管理を [対象の接続先] = RunPod にして [DL] / [全DL] で入れる（取得元 URL の登録が要る） |
| 「missing custom nodes on ComfyUI」が出る | そのノードは本体に入っていない。`custom_nodes.txt` に `<git-url> <commit>` を足して push し直す |
| Pod が消えない | `RUNPOD_API_KEY` がテンプレートの環境変数に入っているか。ログの `[watchdog]` に理由が出る |
| Pod がすぐ消える | `IDLE_TIMEOUT_MINUTES` を伸ばす。ComfyUI に繋がらない間はカウントしないので、「生成中なのに消える」ことは起きない |
| 401 が返る | アプリの `runpod_comfy_api_key` とテンプレートの `COMFY_API_KEY` が違う |
| [DL] が「Pod のダウンロード API がありません」で失敗する | Pod が古いイメージで動いている。手順 1 をやり直して Pod を作り直す |
| [全DL] が「不足しているモデルを判定できません」で失敗する | Pod が起動していない（判定に Pod の `/object_info` を使う）。ジョブを 1 本投げて自動起動させるか、RunPod のコンソールから起動する |
