# AGENTS.md

Karakuri Media Studio は、ComfyUI（ローカル / RunPod / Comfy Cloud）を裏に置いた
動画・画像・音声の生成スタジオ。FastAPI（`backend/`）+ React SPA（`frontend/`）で、
「作品 → 話 → 場 → カット」の脚本と World Bible の素材、カットごとの Take、
タイムライン編集と mp4 書き出し、Remotion 連携までを 1 つのアプリで持つ。外部の
エージェントは API キー付きの外部 API（`/api/v1`）から同じことができる。

## このアプリを操作して映像を作るとき

`.agents/skills/karakuri-studio/SKILL.md` を読むこと。接続先とキーの解決、
最初に読むべき API（`openapi.json` / `prompt-guide` / `capabilities` / `options`）、
制作の段取り、`base_revision` や削除まわりの鉄則がそこにある。curl ラッパーと
動画検分スクリプトは `.agents/skills/karakuri-studio/scripts/` にある。

## このリポジトリを開発するとき

```bash
./run.sh                                           # 本番モード（SPA をビルドして配信）
./run.sh --dev                                     # 開発モード（uvicorn --reload + vite）
cd backend && ../.venv/bin/pytest                  # バックエンドのテスト
cd frontend && npm run build && npx vitest run     # フロントエンド（型チェック込み）
```

- 待受は `.env` の `HOST` / `PORT`（既定 `127.0.0.1:8000`）。
- 仕様は `docs/SPEC.md`、外部 API は `docs/EXTERNAL-API.md`。
- コメントとドキュメントは日本語で書く（既存のスタイルに合わせる）。
