# Karakuri Media Studio

動画・画像・音声を制作できるメディア制作ツールです。スタジオ機能では
「作品 → 話 → 場 → カット」の脚本から、カットごとの Take、タイムライン編集、演出、
mp4 書き出しまでを通して、**長編の映像も 1 つのアプリで**作れます。

生成のバックエンドは **ComfyUI**（ローカル / RunPod / Comfy Cloud）を基本に、
**Remotion**（MV・モーショングラフィックスの演出）や **Whisper**（歌詞のアライン・
音源解析）などの補助ツールも内包しています。

人間が直接使うこともできますが、UI は基本的に**閲覧・確認用**で、制作は
**外部の AI エージェント**が API キー付きの外部 API（`/api/v1`）から操作する前提で
設計しています。

![生成画面](docs/images/screen-image.png)

---

## クイックスタート

**自分でやる**

```bash
git clone https://github.com/KarakuriAgent/karakuri-media-studio.git
cd karakuri-media-studio && ./run.sh    # 依存を整えて http://127.0.0.1:8000 で起動
```

ブラウザで `http://127.0.0.1:8000` を開き、設定ページで ComfyUI の接続先を入れます。
前提条件・Docker・任意機能は [`docs/SETUP.md`](docs/SETUP.md) を参照してください。

**エージェントに任せる**

```bash
git clone https://github.com/KarakuriAgent/karakuri-media-studio.git
cd karakuri-media-studio && claude      # Codex / Cursor CLI などでも可
```

「セットアップして」と頼めば、[`karakuri-setup`](.agents/skills/karakuri-setup/SKILL.md)
スキルが `scripts/setup.sh status` で保存状態と環境を見て、**未完了の段階から**
環境確認・起動・ComfyUI 接続・キー発行・動作確認まで進めます（人にしかできない作業
—— サインイン、キーの用意、ライセンス確認 —— だけは指示して待ちます）。

---

## ドキュメント

| 文書 | 内容 |
|---|---|
| [`docs/SETUP.md`](docs/SETUP.md) | 前提条件・起動・Docker・任意機能（Remotion / 音源解析 / RunPod / モデル DL）・設定キー・トラブル |
| [`docs/USAGE.md`](docs/USAGE.md) | 使い方（モード・ワークフロー・ライブラリ・LoRA・履歴 / NSFW）と外部 API の概要 |
| [`docs/SPEC.md`](docs/SPEC.md) | 仕様・設計・内部 API |
| [`docs/EXTERNAL-API.md`](docs/EXTERNAL-API.md) | 外部公開 API（`/api/v1`・`X-API-Key`）の設計とデプロイ |
| [`docs/RUNPOD-QUICKSTART.md`](docs/RUNPOD-QUICKSTART.md) | ComfyUI を RunPod の Pod で動かす手順 |
| [`docs/prompt-samples.md`](docs/prompt-samples.md) | プロンプトの実例 |
| [`.agents/skills/karakuri-setup/SKILL.md`](.agents/skills/karakuri-setup/SKILL.md) | エージェント向け: 導入・再開・点検 |
| [`.agents/skills/karakuri-studio/SKILL.md`](.agents/skills/karakuri-studio/SKILL.md) | エージェント向け: 外部 API で映像を作る |
| [`.agents/skills/karakuri-remotion/SKILL.md`](.agents/skills/karakuri-remotion/SKILL.md) | エージェント向け: Remotion の props を書く |

---

## ライセンスの注意

- **Remotion は既定 OFF**です。MIT などのオープンソースライセンスではなく独自の
  Remotion License で提供されており、個人利用および従業員 3 名以下の会社は無償ですが、
  それ以上の規模では会社ライセンス（有償）が必要です。有効にする前に
  <https://www.remotion.dev/license> を確認してください
- 本アプリは成人向けコンテンツをローカル生成する個人利用ツールです。生成物・プロンプトは
  すべてローカルにのみ保存され、ComfyUI と LLM CLI 以外へは送信されません
- LoRA で実在人物を無断利用しないでください（利用者責任）
