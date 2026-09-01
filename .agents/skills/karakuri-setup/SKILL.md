---
name: karakuri-setup
description: Karakuri Media Studio を新しいマシンに導入・再開・点検する。セットアップ、インストール、初期設定、起動しない、接続できない、といった依頼で使う。
---

# Karakuri Media Studio をセットアップする

このアプリの導入は **S0〜S8 の 9 段階**に分かれている。段階ごとに「自動でできること」と
「人にしかできないこと」が決まっていて、どこまで済んだかは
`runtime/setup-state.json`（gitignore 済みの `runtime/` 配下）に残る。
**どこからでも再開できる**のがこの仕組みの目的で、途中で会話が切れても、別の日に
続きをやることになっても、状態ファイルを読めば同じところから続けられる。

手順の詳細・設定キーの一覧・トラブル対処は [`docs/SETUP.md`](../../../docs/SETUP.md)。
このファイルは段取りと判定だけを書く。

## 最初にやること

```bash
.agents/skills/karakuri-setup/scripts/setup.sh status
```

保存状態（段階ごとの `done` / `skipped` / `failed`）と自動検出（python / node /
ffmpeg / docker / GPU、`.venv` や `frontend/dist` の有無、`.env` の HOST / PORT、
アプリの疎通、`GET /api/health` の `comfyui` と `grok`、外部 API キーの有無、
`remotion_enabled`、`audio_analysis_python`、`COMFY_MODELS_DIR`）が並び、最後の 1 行に
**次にやる段階**が出る。**その段階から始める**。前の段階をやり直さない。

補助コマンド:

```bash
setup.sh check [--json]                    # 自動検出だけ
setup.sh mark <S0..S8> <done|skipped|failed> [note]
setup.sh choose <key> <value>              # 選択の記録（launch / comfy_target …）
setup.sh reset                             # 状態ファイルを消す（確認あり）
```

自動検出が `ok` でも、**人に確認すべき段階**（S1・S7 など）は勝手に done にしない。

## 段階

### S0 環境確認

- 自動: `setup.sh check` で python 3.12 以上 / node 18 以上 / npm / ffmpeg / git、
  Docker の有無、GPU（`nvidia-smi`）を見る。
- 人: 足りないものの導入（apt / brew / nvidia ドライバ）。何をどう入れるかは
  OS によるので、**足りないものを名指しして依頼する**。
- 完了: python・node・npm・ffmpeg・git が揃っている（Docker と GPU は無くてもよい。
  GPU が無ければ S3 で Comfy Cloud か RunPod を勧める）。
- 記録: `setup.sh mark S0 done "python3.12 / node 24 / ffmpeg あり・GPU なし"`

### S1 起動方法の選択

- 人: **ホスト（`./run.sh`）か Docker（`./compose.sh up -d --build`）か**を決めてもらう。
  ホストは手軽、Docker は依存をコンテナに閉じ込められる（データとワークスペースは
  ローカルのまま）。待受も聞く（`.env` の `HOST` / `PORT`、既定 `127.0.0.1:8000`）。
- 自動: 決まったら `.env` に `HOST` / `PORT` を書く（`.env` は gitignore 済み）。
- 完了: 選択が決まって `.env` が書けた。
- 記録: `setup.sh choose launch host`（または `docker`）→ `setup.sh mark S1 done`

### S2 起動と疎通

- 自動: 選んだ方法で起動し、`curl $BASE/api/health` が JSON を返すまで見る
  （`$BASE` は `.env` の HOST / PORT から。`HOST=0.0.0.0` は待受の意味なので、
  宛先には `127.0.0.1` を使う）。
  - ホスト: `./run.sh`（初回は venv 作成と `npm install` / `npm run build` が走るので
    数分かかる）。
  - Docker: **先にホスト側で** `npm --prefix frontend install && npm --prefix frontend run build`
    と `npm --prefix remotion install` を済ませてから `./compose.sh up -d --build`。
    コンテナはリポジトリをマウントするだけなので、`frontend/dist` と
    `remotion/node_modules` がホストに無いと配信も Remotion も動かない。
- 人: ポートが埋まっている・権限が足りないなど、環境側の解決。
- 完了: `GET /api/health` が返る（`setup.sh status` の「アプリ」が `ok`）。
- 記録: `setup.sh mark S2 done`

### S3 ComfyUI の接続先

- 人: **どこの ComfyUI を使うか**を決めてもらい、必要なものを用意してもらう。
  - `local` … 同じマシン / LAN の ComfyUI（URL だけ。既定 `http://127.0.0.1:8188`）
  - `comfy_cloud` … Comfy Cloud（API キーが要る。Standard 以上のプラン）
  - `runpod` … RunPod の Pod（URL・Pod の API キー・RunPod API キー・
    テンプレート ID・Network Volume ID。手順は
    [`docs/RUNPOD-QUICKSTART.md`](../../../docs/RUNPOD-QUICKSTART.md)）
- 自動: 受け取った値を `PUT /api/settings` で保存する（`comfy_target` と、その
  プロファイルの URL / キー）。**キーの値はログ・返答・コミットに貼らない。**
  設定ページから人に入れてもらってもよい。
- **Docker で動かしている場合、`127.0.0.1` はコンテナから届かない。**
  `http://host.docker.internal:8188` か LAN の IP を使う。
- 完了: `GET /api/health` の `comfyui` が `ok`。
- 記録: `setup.sh choose comfy_target local` → `setup.sh mark S3 done`

### S4 custom node とモデル

- 自動: `GET /api/health` の `comfyui.detail` を読む。`missing custom nodes on
  ComfyUI: …` は**不足ノード**、`workflow template: …` はテンプレートとマニフェストの
  **ノード ID ズレ**。
- 人: 不足ノードの導入（ComfyUI Manager か git clone）。どのノードが要るかは
  detail に列挙されるので、そのまま伝える。
- モデルは**使うワークフローのぶんだけ**でよい（全部は要らない。既定ファイル名は
  SPEC §3.3）。`.env` に `COMFY_MODELS_DIR=/path/to/ComfyUI/models` を書いて再起動すると、
  設定ページの「モデル」タブから未検出のファイルをダウンロードできる（任意）。
- 完了: `comfyui` が `ok`（`… node classes verified` が出る）。
- 記録: `setup.sh mark S4 done`（モデルは後回しにするなら note に書く）

### S5 grok CLI

- 人の作業（ここは代われない）:
  1. `curl -fsSL https://x.ai/cli/install.sh | bash`
  2. 一度 `grok` を起動してブラウザでサインイン（サーバーなら `grok --device-auth`）
  - SuperGrok / X Premium+ のサブスクリプションが要る。プロンプト作成チャットだけで
    なく **画像ワークフローの Grok Imagine もこの CLI で走る**。
- Docker: コンテナはホストの `~/.grok` をマウントして使うので、**ホスト側で**
  インストールとサインインを済ませる。
- 完了: `GET /api/health` の `grok` が `ok`。
- 記録: `setup.sh mark S5 done`（別の LLM CLI を使うなら `skipped` と note）

### S6 外部 API キー

外部 API（`/api/v1`）はキーが空のあいだ**丸ごと 404**。ここで発行すると、以後
`karakuri-studio` スキルの `scripts/studio.sh` がそのまま使えるようになる。

- 自動: ランダムな 32 文字以上のキーを生成して `PUT /api/settings` で保存する。
  **生成した値を標準出力・返答・コミットに出さない**（保存先は `.env` ではなく
  `runtime/config.json`。`studio.sh` はそこから自分で読む）。

  ```bash
  python3 - <<'PY'
  import json, secrets, urllib.request
  key = secrets.token_urlsafe(32)            # 値は表示しない
  req = urllib.request.Request(
      "http://127.0.0.1:8000/api/settings", method="PUT",
      data=json.dumps({"external_api_key": key}).encode(),
      headers={"Content-Type": "application/json"})
  urllib.request.urlopen(req).read()
  print("saved")
  PY
  ```

- 人: 別マシンのエージェントにも渡したい場合は、**設定 →「接続 / Grok」タブの
  外部 API（/api/v1）** で値を自分で確認してもらう（こちらからは貼らない）。
- 完了: `setup.sh status` の「外部 API キー」が「設定済み」／
  `scripts/studio.sh GET /projects` が 200。
- 記録: `setup.sh mark S6 done`

### S7 任意機能

**どれも人が要否を決める。要らないと言われたら `skipped` で記録して次へ。**

| 機能 | やること | 完了の判定 |
|---|---|---|
| Remotion（MV・演出） | **ライセンス確認は人**。Remotion は独自ライセンス（個人・従業員 3 名以下は無償、それ以上は会社ライセンスが有償）。<https://www.remotion.dev/license> を確認して**同意を得てから** `PUT /api/settings {"remotion_enabled": true}` | `remotion_enabled` が `yes` で、composition 一覧が引ける |
| 音源解析（歌詞つき MV） | リポジトリ直下に `.venv-audio` を作り `backend/requirements-optional.txt` を入れ、`PUT /api/settings {"audio_analysis_python": "<実体の絶対パス>/.venv-audio/bin/python"}`。**Docker ならコンテナの中の python で作る**（`docker exec <container> bash -c "cd <実体パス> && python3.12 -m venv .venv-audio && .venv-audio/bin/pip install -r backend/requirements-optional.txt"`。ホストの python で作った venv は中で動かない。パスはシンボリックリンクでなく実体 `pwd -P`）。数 GB 落ちるので先に伝える。GPU は compose の `deploy.resources` で渡してある | `setup.sh status` の `audio_analysis_python` が実在 `yes` |
| RunPod 自動起動 | [`docs/RUNPOD-QUICKSTART.md`](../../../docs/RUNPOD-QUICKSTART.md) の手順。Network Volume・テンプレート・Cloudflare Tunnel は**人の作業**、設定の保存は自動でよい | 接続先 RunPod で `comfyui` が `ok` |
| 不足モデルの自動 DL | `.env` に `COMFY_MODELS_DIR` を書いて再起動。gated なら設定に HF トークン / Civitai キー（**人が用意**） | 設定ページの「モデル」タブに一覧が出る |

- 記録: `setup.sh mark S7 done`（一部だけ入れたなら note に何を入れたか書く）

### S8 動作確認

- 自動: **画像ジョブを 1 本**、外部 API から投入して `done` になるまで見る。
  `GET /api/v1/options` の `image_workflows` から**一番軽いもの**（Z-Image turbo や
  Krea 2 turbo など）を選び、`megapixels` は小さめ（0.4）にする。

  ```bash
  studio.sh POST /jobs '{"mode":"image_only","image_workflow":"z_image_turbo",
                         "image_prompt":"a red apple on a wooden table",
                         "megapixels":0.4}'
  studio.sh wait-job <job_id>
  ```

  （`studio.sh` は `.agents/skills/karakuri-studio/scripts/studio.sh`）
- S7 で Remotion を ON にしたなら、`Slate` の composition も 1 本焼いて確かめる。
- 失敗したら detail を読む: モデルのファイル名違い（S4 に戻る）/ VRAM 不足
  （`megapixels` を下げる）/ 接続先の URL（S3 に戻る）。
- 完了: ジョブが `done` になり、出力ファイルの URL が開ける。
- 記録: `setup.sh mark S8 done`

## 鉄則

- **キーやトークンの値を出力しない。** API キー・HF トークン・Civitai キー・RunPod の
  キーは、返答にもログにもコミットにも貼らない。`setup.sh` は有無しか表示しないし、
  `choose` は `*key*` / `*token*` を含む名前を拒否する。
- **破壊的操作は人に確認する。** `app.db` や `runtime/` の削除、`docker rm` /
  `docker compose down -v`、`.venv` の作り直し。データは全部ローカルにあり、
  消したら戻らない。
- **人にしかできない段階では、何をしてほしいかを具体的に伝えて待つ。**
  「grok CLI を入れてサインインしてください」ではなく、コマンドと、どこで
  サインインするか（ブラウザ / `--device-auth`）と、済んだら何で判定するか
  （`setup.sh status` の `health grok`）まで伝える。勝手に飛ばさない。
- **完了・スキップは必ず `setup.sh mark` で記録する。** 記録しないと次のセッションが
  同じことをやり直す。失敗して止まるときも `failed` と note を残す。
- **別マシンでは `.env` / `runtime/config.json` / `app.db` を持ち込まない**
  （絶対パス・環境固有の URL・キーが入っているので新規に作る）。
  ただし `runtime/setup-state.json` があれば、その続きから再開してよい。
