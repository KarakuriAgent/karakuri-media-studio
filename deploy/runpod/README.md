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
  モデルを足すのにイメージの再ビルドは要りません

---

## ファイル

| ファイル | 役割 |
|---|---|
| `Dockerfile` | CUDA 12.8 + PyTorch cu128 + caddy + cloudflared のランタイム |
| `entrypoint.sh` | 冪等な起動処理（clone → custom nodes → モデル → 各プロセス起動） |
| `custom_nodes.txt` | 追加で入れる custom node（`<git-url> <commit>`）。既定は空 |
| `models.txt` | 必要なモデル（`<subfolder>/<filename> <url>`） |
| `gen_models_manifest.py` | `workflow/` と設定から `models.txt` を作り直すスクリプト |
| `download_models.py` | マニフェスト（複数可）を読んで不足ぶんだけ落とす |
| `Caddyfile` | `:8189` の認証つきリバースプロキシ |
| `watchdog.py` | アイドルが続いたら自分の Pod を terminate |

---

## 1. モデルの URL を埋める

`models.txt` は `workflow/` のテンプレートから自動生成してあります。取得元が確実に
分かっているものだけ URL が入っていて、それ以外は次の形で残っています。

```
# TODO url
# diffusion_models/krea2_turbo_fp8_scaled.safetensors <url>
```

**使うワークフローのぶんだけ**、コメントを外して直リンクを書いてください（全部を
揃える必要はありません）。行の形式は `<subfolder>/<filename> <url>` で、`subfolder`
は `/workspace/ComfyUI/models` からの相対パスです。保存名は行に書いたファイル名に
なるので、配布元のファイル名が違っていても構いません。

ワークフローを差し替えて必要なモデルが変わったら、リポジトリのルートで作り直せます。

```bash
python3 deploy/runpod/gen_models_manifest.py > deploy/runpod/models.txt
```

（`gen_models_manifest.py` は `backend/app/workflow.py` の `MODEL_SUBFOLDERS` を
そのまま使うので、置き場所の対応表はアプリ側と必ず一致します。既に書いた URL は
スクリプト内の `KNOWN_URLS` に足しておくと、次回の生成でも残ります。）

生成には、テンプレートの既定値だけでなく**アプリの設定でモデルを差し替えたもの・
候補として登録したもの**（設定 → モデル）も入ります。その行のコメントには
`（設定で指定）` が付きます。URL は `KNOWN_URLS` → 設定の「モデルのダウンロード URL」
の順で探し、どちらにも無ければ `# TODO url` になります。

さらに、**設定 → LoRA 管理に登録した人物 LoRA**（DB の `loras` テーブル）も
`loras/<ファイル名>` として入ります（コメントは `LoRA「表示名」`）。URL は LoRA の
登録・編集フォームの「取得元 URL」欄に入れたものが使われます。

### モデル構成を変えたときの反映

設定でモデルを差し替えた／候補を足したときは、**イメージを作り直さなくても**
Network Volume 側のマニフェストで足せます。`entrypoint.sh` は焼き込みの
`models.txt` に加えて、**`/workspace/models.local.txt` があればそれも**読みます
（両方処理し、すでに置いてあるファイルは飛ばします）。

```bash
# 手元で（リポジトリのルート）
python3 deploy/runpod/gen_models_manifest.py > models.local.txt
# `# TODO url` の行のうち、使うものだけ URL を埋める
```

これを Network Volume の直下（`/workspace/models.local.txt`）に置きます。手軽なのは
**テンプレートの環境変数で渡す**方法です。Pod にログインする手段が無くても使えます。

```bash
# 手元（リポジトリのルート）
base64 -w0 models.local.txt
```

出てきた 1 行を、テンプレートの Environment Variables に `MODELS_LOCAL_B64` として
入れます。起動のたびに `entrypoint.sh` がデコードして `/workspace/models.local.txt`
に書き出します（**毎回上書き**するので、テンプレート側の値が常に正になります）。
デコードに失敗したときは既存のファイルを残したまま読み飛ばし、起動は続きます。
イメージは公開レジストリに置きますが、テンプレートの環境変数は非公開なので、
個人ごとのモデル URL をイメージに焼かずに済みます。

Pod に入れる手段があるなら、ファイルを直接置いても構いません。Pod を起動した
状態で、次のどちらかで送ります。

```bash
# 手元
runpodctl send models.local.txt
# Pod の web ターミナル / Jupyter
runpodctl receive <コード>        # /workspace に置く
```

Jupyter を有効にしたテンプレートなら、ファイルブラウザから `/workspace` に
ドラッグ＆ドロップするだけでも構いません。次の起動から反映されます。

> **配布 URL の無いモデル（自作 LoRA など）はダウンロードできません。**
> マニフェストに書けるのは直リンクのあるファイルだけなので、手元にしか無い
> LoRA / チェックポイントは**ボリュームに直接アップロード**してください。
> 置き場所は `/workspace/ComfyUI/models/loras/`（LoRA の場合。他は `models.txt`
> の `<subfolder>` と同じ）です。`runpodctl send` / `receive` か、Jupyter の
> ファイルブラウザからのアップロードが手軽です。ファイル名はアプリの設定に
> 入れた名前と**完全に一致**させてください（ComfyUI はこの名前で探します）。

---

## 2. イメージをビルドして push

RunPod からは公開レジストリ（Docker Hub / GHCR など）が見える必要があります。

```bash
cd deploy/runpod

docker build -t <user>/karakuri-comfyui:latest .
docker push <user>/karakuri-comfyui:latest
```

ComfyUI のバージョンはビルド引数で固定します（既定はタグ指定）。

```bash
docker build --build-arg COMFYUI_REF=6c62ca0b6bbee7ef293ca475f7904065af5bfb42 -t <user>/karakuri-comfyui:latest .
```

> `models.txt` と `custom_nodes.txt` はイメージに入るので、内容を変えたら
> **ビルドし直して push** してください。モデルを足すだけなら、ビルドの代わりに
> `/workspace/models.local.txt`（手順 1）でも足せます。

---

## 3. Network Volume を作る

RunPod のコンソール → **Storage** → **Network Volume** で作ります。

- リージョンは、使いたい GPU（RTX PRO 6000 Blackwell / RTX 5090）の在庫があるところ
- 容量はモデルの合計 + 100 GB 程度（LTX 2.3 系を一式入れるなら 500 GB〜）
- Pod には `/workspace` としてマウントされます

ここに ComfyUI 本体・カスタムノード・モデルが置かれ、Pod を作り直しても残ります。
初回だけモデルのダウンロードで時間がかかり、2 回目以降は数十秒で上がります。

---

## 4. Cloudflare Tunnel を作る

Pod は起動のたびに IP が変わるので、**固定のホスト名**をトンネルで用意します。
（アプリ側の `comfy_url` を毎回書き換えなくて済みます。）

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
> ただし Pod ごとに URL が変わるので、そのつど設定の `comfy_url` を直してください。

---

## 5. テンプレートを登録する

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
| `COMFY_API_KEY` | 推奨 | プロキシの API キー。アプリの `comfy_api_key` と同じ値。空にすると**誰でも ComfyUI を叩ける**ので、トンネルで公開するなら必ず設定する |
| `CF_TUNNEL_TOKEN` | ○ | 手順 4 で控えたトンネルのトークン |
| `HF_TOKEN` | 任意 | gated な Hugging Face リポジトリからモデルを落とす場合 |
| `CIVITAI_API_KEY` | 任意 | 要ログインの Civitai ファイルを落とす場合 |
| `RUNPOD_API_KEY` | ○ | watchdog が自分を terminate するのに使う |
| `IDLE_TIMEOUT_MINUTES` | 任意 | アイドルで終了するまでの分数（既定 `10`） |
| `STARTUP_GRACE_MINUTES` | 任意 | 起動直後に絶対に終了しない分数（既定 `15`。初回のモデル取得ぶん） |
| `COMFY_ARGS` | 任意 | ComfyUI に足す起動引数（例 `--highvram`） |
| `MODELS_LOCAL_B64` | 任意 | `models.local.txt` を `base64 -w0` した値（手順 1）。起動時に `/workspace/models.local.txt` へ書き出される |

`RUNPOD_POD_ID` は RunPod が自動で入れるので、自分で設定する必要はありません。

保存すると **テンプレート ID**（`xxxxxxxxxxxx` のような文字列）が表示されます。
アプリの設定に入れるのでこれを控えてください。

---

## 6. アプリ側の設定

Karakuri Media Studio の **設定 → 接続 / Grok** で:

| 項目 | 値 |
|---|---|
| ComfyUI URL | `https://comfy.example.com`（手順 4 のホスト名） |
| ComfyUI APIキー | テンプレートの `COMFY_API_KEY` と同じ値 |
| **RunPod の Pod を自動起動する** | オン |
| RunPod APIキー | RunPod → Settings → API Keys で発行したもの |
| テンプレート ID | 手順 5 で控えたもの |
| GPU 種別 | `NVIDIA RTX PRO 6000 Blackwell Workstation Edition` / `NVIDIA GeForce RTX 5090` など（RunPod の gpuTypeId をそのまま） |
| Network Volume ID | 手順 3 で作ったボリュームの ID |

これで、ジョブを実行したときに ComfyUI へ繋がらなければ Pod が作られ、繋がるまで
待ってから（最大 15 分）ワークフローが投入されます。待っている間は生成画面に
「Pod の起動を待っています…」と出ます。

GPU が確保できないときは**そのままエラーになります**（勝手に別の GPU や
SECURE クラウドへ振り替えると、意図しない課金になるため）。設定の GPU 種別を
変えて再実行してください。

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
`[watchdog]` のプレフィックスで各段階の進み具合が出ます。

---

## つまずきやすいところ

| 症状 | 原因と対処 |
|---|---|
| 15 分待ってもアプリが繋がらない | Pod のログで ComfyUI が上がっているか確認。初回はモデルの取得で 15 分を超えることがあるので、その場合はもう一度実行すれば続きから進む（`.part` は消えて落とし直しになる点に注意） |
| ジョブが「ファイルが見つからない」で失敗する | `models.txt` の `# TODO url` が埋まっていない。埋めてイメージを push し直す |
| 「missing custom nodes on ComfyUI」が出る | そのノードは本体に入っていない。`custom_nodes.txt` に `<git-url> <commit>` を足して push し直す |
| Pod が消えない | `RUNPOD_API_KEY` がテンプレートの環境変数に入っているか。ログの `[watchdog]` に理由が出る |
| Pod がすぐ消える | `IDLE_TIMEOUT_MINUTES` を伸ばす。ComfyUI に繋がらない間はカウントしないので、「生成中なのに消える」ことは起きない |
| 401 が返る | アプリの `comfy_api_key` とテンプレートの `COMFY_API_KEY` が違う |
