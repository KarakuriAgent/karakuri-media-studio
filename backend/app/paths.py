from pathlib import Path

# backend/app/paths.py -> project root
ROOT = Path(__file__).resolve().parents[2]

OUTPUTS_DIR = ROOT / "outputs"
ASSETS_DIR = ROOT / "assets"
# 手元に取っておく素材（ライブラリ、SPEC §7.2）。生成物やアップロードのうち
# 「残すと決めたもの」だけがここに入り、DB の library テーブルが目録になる。
LIBRARY_DIR = ROOT / "library"
RUNTIME_DIR = ROOT / "runtime"
GROK_WORKDIR = RUNTIME_DIR / "grok-workdir"
# Grok Build CLI をメディア生成に使うときの作業ディレクトリ（SPEC §5.2）。
# プロンプト用（GROK_WORKDIR）とは分ける: メディア生成は CLI が
# `.grok/generated-media/` にファイルを書き散らすので、チャットのセッションと
# 同じ場所で走らせると取り違える。
GROK_MEDIA_WORKDIR = RUNTIME_DIR / "grok-media-workdir"
# Codex CLI（ChatGPT サブスク枠）で画像を生成するときの作業ディレクトリ
# （SPEC §5.4）。`codex exec -C <ここ>` の作業根になる場所で、`--sandbox
# workspace-write` が書き込みを許すのもこの下だけ。リポジトリの中で走らせると
# エージェントが手元のコードを触りうるので、必ず専用の空ディレクトリにする。
CODEX_MEDIA_WORKDIR = RUNTIME_DIR / "codex-media-workdir"
# One work dir per agent session (AGENT-MODE §5.2).
AGENT_SESSIONS_DIR = RUNTIME_DIR / "agent-sessions"

FRONTEND_DIST_DIR = ROOT / "frontend" / "dist"

DB_PATH = ROOT / "app.db"
CONFIG_PATH = RUNTIME_DIR / "config.json"
# Folder of API-format ComfyUI templates (see app/workflows.py for the manifests).
WORKFLOW_DIR = ROOT / "workflow"


def ensure_dirs() -> None:
    for d in (
        OUTPUTS_DIR,
        ASSETS_DIR,
        LIBRARY_DIR,
        RUNTIME_DIR,
        GROK_WORKDIR,
        GROK_MEDIA_WORKDIR,
        CODEX_MEDIA_WORKDIR,
        AGENT_SESSIONS_DIR,
    ):
        d.mkdir(parents=True, exist_ok=True)
