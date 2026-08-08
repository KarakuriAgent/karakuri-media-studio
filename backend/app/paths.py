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
# One work dir per agent session (AGENT-MODE §5.2).
AGENT_SESSIONS_DIR = RUNTIME_DIR / "agent-sessions"

FRONTEND_DIST_DIR = ROOT / "frontend" / "dist"

DB_PATH = ROOT / "app.db"
CONFIG_PATH = RUNTIME_DIR / "config.json"
# Folder of API-format ComfyUI templates (see app/workflows.py for the manifests).
WORKFLOW_DIR = ROOT / "workflow"


#: ROOT 直下の「データの置き場」の名前。保存済みパスを載せ替えるときの継ぎ目に使う。
#: :func:`ensure_dirs` が作るディレクトリのうち、DB に絶対パスが残るものだけを並べる。
REBASE_ANCHORS: tuple[str, ...] = ("outputs", "assets", "library", "runtime")


def rebase_stored_path(path: str | Path) -> Path:
    """DB に入っている絶対パスを、いまの :data:`ROOT` の下に載せ替える。

    成果物と素材のパスは**絶対パス**で jobs / library テーブルに入る。ところが
    :data:`ROOT` は起動したディレクトリで変わりうる（同じリポジトリが
    ``/home/…/video-studio`` にも ``/mnt/…/video-studio`` にも見える環境や、
    ``${PWD}`` をそのままマウントする Docker 起動）ので、別のプレフィックスで
    記録された行はそのままでは開けず、履歴の URL が出なくなる。

    そこで「記録されたパスの中の :data:`REBASE_ANCHORS`（ROOT 直下の置き場）
    より後ろ」を、いまの ROOT に接ぎ直したものを候補にする。アンカーは**後ろから**
    探す: リポジトリ自体が ``outputs/`` のような名前のディレクトリの下にあっても、
    実際の置き場（末尾側）を優先するため。

    ただし**実在するパスだけを載せ替える**: そのまま開けるなら何もせず、候補が
    実在しなければ元のパスを返す。つまり「解決できるなら解決する」だけの働きで、
    存在確認や ``relative_to`` の判定は呼び出し側の責任のまま変わらない。
    """
    original = Path(path)
    if original.exists():
        return original
    parts = original.parts
    for index in range(len(parts) - 1, 0, -1):
        if parts[index] not in REBASE_ANCHORS:
            continue
        candidate = ROOT.joinpath(*parts[index:])
        if candidate.exists():
            return candidate
    return original


def ensure_dirs() -> None:
    for d in (
        OUTPUTS_DIR,
        ASSETS_DIR,
        LIBRARY_DIR,
        RUNTIME_DIR,
        GROK_WORKDIR,
        AGENT_SESSIONS_DIR,
    ):
        d.mkdir(parents=True, exist_ok=True)
