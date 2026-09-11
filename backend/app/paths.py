import os
from pathlib import Path

# backend/app/paths.py -> project root
ROOT = Path(__file__).resolve().parents[2]

#: データ（成果物・素材・ライブラリ）の置き場の親ディレクトリ。環境変数
#: ``KARAKURI_DATA_DIR`` を書けばリポジトリの外（NAS など）に置ける。未設定なら
#: これまでどおり :data:`ROOT` 直下。空文字は未設定扱い（docker-compose が
#: ``${KARAKURI_DATA_DIR:-}`` で空文字を渡すため）。
#: ``app.db`` と ``runtime/`` は対象外で **ローカル固定**（SQLite を CIFS/NFS に
#: 置くとロックが壊れる、CLI の作業ディレクトリはローカルの方が速い）。
_DATA_DIR_ENV = os.environ.get("KARAKURI_DATA_DIR", "").strip()
DATA_DIR = Path(_DATA_DIR_ENV).expanduser() if _DATA_DIR_ENV else ROOT

OUTPUTS_DIR = DATA_DIR / "outputs"
ASSETS_DIR = DATA_DIR / "assets"
# 手元に取っておく素材（ライブラリ、SPEC §7.2）。生成物やアップロードのうち
# 「残すと決めたもの」だけがここに入り、DB の library テーブルが目録になる。
LIBRARY_DIR = DATA_DIR / "library"
RUNTIME_DIR = ROOT / "runtime"
GROK_WORKDIR = RUNTIME_DIR / "grok-workdir"
# Grok Imagine（画像生成・編集）専用の作業ディレクトリ（SPEC §5.2）。プロンプト
# 作成のチャットとは別にする: CLI はコーディングエージェントで、生成物や
# セッションを作業ディレクトリの下に書き散らすため、取り違えないよう分ける。
GROK_MEDIA_WORKDIR = RUNTIME_DIR / "grok-media-workdir"
# プロンプト生成チャット 1 セッションにつき 1 つの作業ディレクトリ（SPEC §4.3）。
CHAT_SESSIONS_DIR = RUNTIME_DIR / "chat-sessions"
# Remotion に渡す props の一時 JSON を置く場所（:mod:`app.remotion`）。中身は
# レンダリングのあいだしか要らないので、終わったら消す。scratch ではなく
# runtime/ に置くのは、Remotion プロジェクト側を汚さずアプリ側の置き場だけで
# 完結させるため。
REMOTION_TMP_DIR = RUNTIME_DIR / "remotion"
# 同梱の Remotion プロジェクト（:mod:`app.remotion`）。Remotion のレンダリングは
# 常にここを使う（composition を足す・直すときは ``remotion/src/`` を編集する）。
# 依存（``node_modules/``）は ``run.sh`` が初回に入れる。
REMOTION_BUNDLED_DIR = ROOT / "remotion"
# 音源解析（:mod:`app.audio_analysis`）に渡す歌詞テキストの一時置き場。歌詞は
# 改行を含むのでコマンドライン引数には埋められず、ファイルにして渡す。中身は
# 解析のあいだしか要らないので、終わったら消す（Remotion の props と同じ扱い）。
AUDIO_ANALYSIS_TMP_DIR = RUNTIME_DIR / "audio-analysis"

FRONTEND_DIST_DIR = ROOT / "frontend" / "dist"

DB_PATH = ROOT / "app.db"
CONFIG_PATH = RUNTIME_DIR / "config.json"
# Folder of API-format ComfyUI templates (see app/workflows.py for the manifests).
WORKFLOW_DIR = ROOT / "workflow"


#: 「データの置き場」の名前。保存済みパスを載せ替えるときの継ぎ目に使う。
#: :func:`ensure_dirs` が作るディレクトリのうち、DB に絶対パスが残るものだけを並べる。
REBASE_ANCHORS: tuple[str, ...] = ("outputs", "assets", "library", "runtime")

#: 置き場のうち :data:`DATA_DIR` の下にあるもの（残りは :data:`ROOT` の下）。
DATA_ANCHORS: frozenset[str] = frozenset({"outputs", "assets", "library"})


def _anchor_base(name: str) -> Path:
    """置き場の名前から、いまそれがぶら下がっている親ディレクトリを返す。"""
    return DATA_DIR if name in DATA_ANCHORS else ROOT


def rebase_stored_path(path: str | Path) -> Path:
    """DB に入っている絶対パスを、いまの置き場の下に載せ替える。

    成果物と素材のパスは**絶対パス**で jobs / library テーブルに入る。ところが
    置き場の場所は起動のしかたで変わりうる（同じリポジトリが
    ``/home/…/video-studio`` にも ``/mnt/…/video-studio`` にも見える環境や、
    ``${PWD}`` をそのままマウントする Docker 起動、あとから
    ``KARAKURI_DATA_DIR`` で ``outputs/`` などを NAS に移した場合）ので、別の
    プレフィックスで記録された行はそのままでは開けず、履歴の URL が出なくなる。

    そこで「記録されたパスの中の :data:`REBASE_ANCHORS`（置き場の名前）より
    後ろ」を、いまのその置き場の親（``outputs`` / ``assets`` / ``library`` は
    :data:`DATA_DIR`、``runtime`` は :data:`ROOT`）に接ぎ直したものを候補に
    する。アンカーは**後ろから**探す: リポジトリ自体が ``outputs/`` のような
    名前のディレクトリの下にあっても、実際の置き場（末尾側）を優先するため。

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
        candidate = _anchor_base(parts[index]).joinpath(*parts[index:])
        if candidate.exists():
            return candidate
    return original


def resolve_workdir(stored: str | Path | None, default: Path) -> Path:
    """設定に記録された作業ディレクトリを、いまの :data:`ROOT` の下に載せ替える。

    ``runtime/config.json`` の ``grok_workdir`` / ``grok_media_workdir`` には
    **保存した時点の ROOT を前提にした絶対パス**が入る（設定ページの既定値が
    ``<ROOT>/runtime/grok-workdir`` なので、ふつうはホスト側のパスがそのまま
    残る）。バックエンドを Docker の中で動かすとリポジトリは別のプレフィックス
    （``/mnt/…``）に載るので、記録どおりのパスは作れず（``/home/…`` は
    Permission denied）、CLI の実行が丸ごと失敗する。

    そこで :func:`rebase_stored_path` を通し、いまの ROOT の下に**実在する**同じ
    置き場（``runtime/grok-workdir`` は :func:`ensure_dirs` が作る）があれば
    そちらを使う。載せ替え先が無ければ記録どおりのパスをそのまま返すので、
    リポジトリの外を作業ディレクトリに指定した構成はこれまでどおり動く。
    設定が空なら ``default``（ROOT 直下の既定の置き場）。
    """
    if not stored:
        return default
    return rebase_stored_path(stored)


def ensure_dirs() -> None:
    for d in (
        OUTPUTS_DIR,
        ASSETS_DIR,
        LIBRARY_DIR,
        RUNTIME_DIR,
        GROK_WORKDIR,
        GROK_MEDIA_WORKDIR,
        CHAT_SESSIONS_DIR,
        REMOTION_TMP_DIR,
        AUDIO_ANALYSIS_TMP_DIR,
    ):
        d.mkdir(parents=True, exist_ok=True)
