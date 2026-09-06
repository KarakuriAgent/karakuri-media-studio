# AGENTS.md

ここは Karakuri Media Studio で**映像を作るための作業フォルダ**。このフォルダを
cwd にして作業すること。アプリ本体（`run.sh` / `.env` / `runtime/` / `docs/` …）は
1 つ上（`..`）にある。

Karakuri Media Studio は、ComfyUI（ローカル / RunPod / Comfy Cloud）を裏に置いた
動画・画像・音声の生成スタジオ。「作品 → 話 → 場 → カット」の脚本と World Bible の
素材、カットごとの Take、タイムライン編集と mp4 書き出し、Remotion 連携までを
1 つのアプリで持つ。ここからは API キー付きの外部 API（`/api/v1`）で操作する。

## スキル

| スキル | 読むとき |
|---|---|
| `.agents/skills/karakuri-setup/SKILL.md` | 導入・中断したセットアップの再開・「起動しない / 繋がらない」の点検 |
| `.agents/skills/karakuri-studio/SKILL.md` | 外部 API を叩いて映像を作る（脚本・素材・レンダリング・編集・書き出し） |
| `.agents/skills/karakuri-remotion/SKILL.md` | Remotion コンポジションの props（MV・歌詞・FX）を書く |

## 最初にやること

```bash
.agents/skills/karakuri-setup/scripts/setup.sh status
```

保存状態（`../runtime/setup-state.json`）と自動検出を段階ごとに出し、最後の 1 行に
**次にやる段階**が出る。**その段階から始める**。人にしかできない作業（grok CLI の
サインイン、API キーの用意、ライセンス確認）はそこで待つ。

## 作業ファイルの置き場

リクエストの JSON、落とした動画、切り出した PNG、下書きやメモは**このフォルダに
自由に置いてよい**（ここに置いたものはコミットされない）。
`.agents/skills/karakuri-studio/scripts/inspect.sh` が切り出す PNG も `tmp/` の
下に出る。スキル（`.agents/skills/`）とアプリ本体（`..`）のファイルは書き換えない。

## 接続先とキー

- BASE: 環境変数 `KARAKURI_STUDIO_URL`。無ければ `../.env` の `HOST` / `PORT` から
  `http://HOST:PORT`（既定 `127.0.0.1:8000`。`HOST=0.0.0.0` は待受の意味なので
  宛先は `127.0.0.1` に読み替える）。
- キー: 環境変数 `KARAKURI_STUDIO_API_KEY`。無ければ `../runtime/config.json` の
  `external_api_key`。`X-API-Key` ヘッダで送る。
- 同梱のラッパー `.agents/skills/karakuri-studio/scripts/studio.sh` が上の解決を
  全部やる。
- アプリが起動していなければ、`../run.sh` を人に実行してもらう。
- **キーの値をログ・返答・コミットに貼らない**（有無だけを言う）。

## その他

- セットアップ手順の詳細は `../docs/SETUP.md`、外部 API の設計は
  `../docs/EXTERNAL-API.md`。
