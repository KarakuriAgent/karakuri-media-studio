# RunPod セットアップ（公開イメージをそのまま使う人向け）

このリポジトリを fork せず、公開済みの Docker イメージ
**`ghcr.io/karakuriagent/karakuri-comfyui:latest`** をそのまま使って ComfyUI を
RunPod で動かすまでの手順です。イメージのビルド・push・GitHub の作業は一切
ありません。

イメージを自分で変えたい人（`Dockerfile` / `entrypoint.sh` / 同梱の既定モデル一覧
`models.txt` / `custom_nodes.txt` をいじりたい人）は fork したうえで
[`deploy/runpod/README.md`](../deploy/runpod/README.md) のフル手順を参照して
ください（Actions の workflow は同梱済みで、push すれば自分の GHCR にビルドされます）。

## 全体像

```
[手元] アプリ本体 ──HTTPS(固定URL)──> Cloudflare Tunnel ──> [RunPod Pod]
                                                             caddy :8189（APIキー認証）
                                                               └─> ComfyUI
                                                             watchdog（アイドルで自動終了）
                                                            /workspace = Network Volume
                                                             （ComfyUI 本体・モデルの置き場）
```

- **起動はアプリが自動でやる**: ジョブ実行時に ComfyUI へ繋がらなければ Pod を作って待つ
- **停止は Pod が自分でやる**: アイドルが続くと watchdog が Pod を terminate（消し忘れ課金なし）
- **常時かかる費用は Network Volume の保管料だけ**（例: 400 GB で月 $28 程度）。GPU 代は使った時間ぶん
- 個人ごとの差分（モデル・LoRA・URL・キー）は**すべて RunPod テンプレートの環境変数と
  アプリの設定**に載るので、リポジトリやイメージを自分用に持つ必要はない

## 1. アプリを動かす（手元）

```bash
git clone https://github.com/KarakuriAgent/karakuri-media-studio.git
cd karakuri-media-studio
./run.sh          # または docker compose up -d
```

## 2. アカウントとキーを用意する

| 用意するもの | 場所 | メモ |
|---|---|---|
| RunPod アカウント + API キー | runpod.io → Settings → API Keys | **Read/Write** で発行。課金チャージも済ませる |
| Cloudflare Tunnel トークン | Cloudflare Zero Trust → Networks → Tunnels | 下記参照。ドメインを 1 つ Cloudflare に載せておく（無料プラン可） |
| Civitai API キー | Civitai → アカウント設定 → API Keys | Civitai から落とすモデルがある場合のみ |
| Hugging Face トークン | huggingface.co/settings/tokens | gated / private リポジトリから落とす場合のみ。**Fine-grained** で対象リポの read だけに絞る |

**Cloudflare Tunnel の作成**: Pod は起動のたびに IP が変わるため、固定ホスト名を
トンネルで用意します。

1. Zero Trust → Networks → Tunnels → **Create a tunnel**（Cloudflared 型、名前は `comfy` など）
2. 表示されるインストールコマンド中の**トークン**（`eyJ…` の長い文字列）を控える
3. **Public Hostname** を 1 件追加: Subdomain `comfy` / 自分のドメイン / Service **HTTP** `127.0.0.1:8189`

## 3. RunPod コンソールで Network Volume を作る

Storage → **Network Volume**。

- データセンターは「使いたい GPU の在庫」と「storage 対応」の両方を満たすところを
  選ぶ（GPU によっては storage 対応 DC に在庫が無いことがあるので、先に Pods の
  Deploy 画面で在庫のある DC を確かめると確実）
- 容量はモデル合計 + 100 GB 程度（LTX 2.3 系まで一式なら 400 GB〜）
- 控えるもの: **ボリューム ID**

## 4. RunPod コンソールでテンプレートを登録する

Templates → **New Template**。

| 項目 | 値 |
|---|---|
| Container Image | `ghcr.io/karakuriagent/karakuri-comfyui:latest` |
| Container Disk | 30 GB |
| Volume Mount Path | `/workspace` |
| Expose HTTP Ports | 空で可（Tunnel を使うため） |

**Environment Variables**:

| 変数 | 必須 | 内容 |
|---|---|---|
| `COMFY_API_KEY` | ○ | アプリ ↔ Pod の合言葉。自分で決めた長いランダム文字列（アプリ側にも同じ値を入れる） |
| `CF_TUNNEL_TOKEN` | ○ | 手順 2 で控えたトンネルトークン |
| `RUNPOD_API_KEY` | ○ | 手順 2 の API キー（watchdog が Pod を消すのに使う） |
| `HF_TOKEN` | 任意 | 手順 2 参照 |
| `CIVITAI_API_KEY` | 任意 | 手順 2 参照 |
| `MODELS_LOCAL_B64` | 任意 | 手順 6 で入れる |

控えるもの: 保存後に表示される**テンプレート ID**。

## 5. アプリの設定ページに入力する

設定 → 接続 / Grok:

| 項目 | 値 |
|---|---|
| ComfyUI URL | `https://comfy.自分のドメイン`（手順 2 のホスト名） |
| ComfyUI APIキー | テンプレートの `COMFY_API_KEY` と同じ値 |
| RunPod の Pod を自動起動する | オン |
| RunPod APIキー | 手順 2 の API キー |
| テンプレート ID | 手順 4 で控えたもの |
| GPU 種別 | RunPod の gpuTypeId を**一字一句そのまま**（例 `NVIDIA GeForce RTX 5090`）。綴りが違うと Pod 作成に失敗する |
| Network Volume ID | 手順 3 で控えたもの |

## 6. 自分のモデル構成を Pod に伝える

アプリの設定でモデル・LoRA と**取得元 URL** を登録したら、リポジトリのルートで:

```bash
python3 deploy/runpod/gen_models_manifest.py > models.local.txt
base64 -w0 models.local.txt        # 出力された文字列をコピー
```

この文字列をテンプレートの環境変数 **`MODELS_LOCAL_B64`** に貼ります。Pod は起動の
たびにこれをデコードして `/workspace/models.local.txt` に展開し、不足しているモデル
だけダウンロードします。

- ローカルでは何もダウンロードされません（一覧表を作るだけ）。実際の DL は Pod が
  Network Volume に対して行います
- モデル・LoRA を追加したら、この手順をもう一度（再生成 → base64 → env 更新）。
  イメージの再ビルドは不要です
- 配布 URL の無い自作モデルはダウンロードできないので、Pod に入れる手段
  （`runpodctl` 等）でボリュームへ直接置きます（[`deploy/runpod/README.md`](../deploy/runpod/README.md) 手順 1 参照）

## 7. 最初のジョブを流す

アプリで何か生成を実行すると、Pod が作られて初回セットアップ（ComfyUI 本体 +
モデル一括ダウンロード）が走ります。**初回だけ 30 分〜数時間**かかります。

> **節約のコツ**: ダウンロード工程は GPU を使わないので、初回だけアプリ設定の
> GPU 種別を同じデータセンターの安い GPU（RTX A4000 など）にしておき、
> セットアップ完走後に本命の GPU へ戻すと GPU 代を節約できます。
ダウンロード済みのファイルは Volume に残るので、途中で Pod が消えても再実行すれば
続きから進みます。2 回目以降の起動は数十秒〜数分です。

以降は**アプリで生成ボタンを押すだけ**です。アイドルが続けば Pod は自動で消え、
課金は止まります。

## つまずいたら

[`deploy/runpod/README.md`](../deploy/runpod/README.md) の「つまずきやすいところ」
（401 が返る / Pod が消えない / モデルが見つからない等）を参照してください。
