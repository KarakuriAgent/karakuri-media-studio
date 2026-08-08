"""Grok Build CLI 経由の画像生成・画像編集（SPEC §5.2 / §4.1）。

ComfyUI と並ぶ **2 つめの生成バックエンド**。xAI の従量課金 API
（``XAI_API_KEY``）ではなく、**SuperGrok / X Premium+ のサブスクリプション枠**で
動く公式 CLI（``grok``）をヘッドレスで叩き、内蔵ツールの ``image_gen``
（text-to-image）/ ``image_edit``（編集）に Grok Imagine で描かせる。使わせる
ツールはマニフェストの :attr:`app.workflows.GrokImagineTask.tool` 1 つで切り替わり、
指示文の組み立て以外（実行・4 段判定・リトライ・クォータの言い換え）は共通。

:mod:`app.grok` がプロンプト作成のためのチャットに使っているのと同じコマンドだが、
用途がまったく違うのでここに分けてある:

- **作業ディレクトリが別**（``runtime/grok-media-workdir``）。CLI はコーディング
  エージェントで、生成した画像をセッションの下に書き散らす。チャットのセッションと
  同じ場所で走らせると取り違えるし、消すのも難しい
- **``XAI_API_KEY`` を env から外す**。環境に残っていると CLI が API 直叩き
  （従量課金）にフォールバックしうるので、サブスク枠で回す約束を守るために
  必ず落とす（SPEC §4.1）
- **成否の判定が 4 段構え**（下記）。相手は「画像を作った」と言いながらファイルを
  置かないことがあるエージェントなので、言葉ではなくファイルを信じる

コマンド名（``grok_command``）はチャットと共有し、タイムアウトと作業ディレクトリは
メディア専用の設定（``grok_media_timeout`` / ``grok_media_workdir``）を持つ。

成否の判定:

1. **終了コード**が非 0 なら失敗
2. 出力から合図（``OK <パス>`` / ``FAILED <理由>``）を読む
3. **指定パスにファイルが実在し、サイズ > 0**（合図だけを信じない）
4. 無ければ CLI 自身の保存先（``~/.grok/sessions/<cwd>/<session>/images/``）を
   mtime 順で探す保険

失敗した実行は **1 回だけやり直す**（モデレーションの誤検知や一時的な失敗が
そこそこある）。ただしサブスク枠を使い切った気配（rate limit / quota）のときは
やり直しても無駄なので、そのままユーザー向けの文言にして投げる。
"""

from __future__ import annotations

import logging
import os
import shutil
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import grok
from .config import load_settings
from .grok import LLMError, iter_json_objects, looks_like_auth_error
from .models import GenerationParams, HealthStatus
from .paths import GROK_MEDIA_WORKDIR, resolve_workdir
from .workflows import INPUT_FIELDS, WorkflowSpec, grok_aspect_ratio

log = logging.getLogger(__name__)

#: 1 回の生成に許す既定の秒数（設定 ``grok_media_timeout`` で上書きできる）
DEFAULT_TIMEOUT = 300.0
#: 「接続確認」ボタンの実行チェックに許す秒数
CHECK_TIMEOUT = 60.0
#: 失敗したときにやり直す回数
RETRIES = 1

#: 環境から必ず外す変数（残っていると API 従量課金にフォールバックしうる）
API_KEY_ENV = "XAI_API_KEY"

#: 認証情報の置き場（CLI がブラウザ / デバイス認証のあとに書く）
AUTH_RELPATH = Path(".grok") / "auth.json"
#: CLI がセッションと生成物を置くディレクトリ（ホームからの相対）。実体は
#: ``~/.grok/sessions/<URL エンコードした作業ディレクトリ>/<session-id>/images/1.jpg``
#: で、セッション id は実行のたびに変わる。**作業ディレクトリ相対だった旧実装の
#: ``.grok/generated-media/`` はもう使われない**ので、保険の探索はここを見る。
SESSIONS_RELPATH = Path(".grok") / "sessions"
#: 入力ファイル（編集元画像）を置くディレクトリ（作業ディレクトリからの相対）。
#: CLI はサンドボックスの外を読めるとは限らないので、渡したいファイルは
#: **作業ディレクトリの中へコピーしてから**指示文で参照する。
INPUTS_RELPATH = Path("inputs")

#: 合図（この 2 つだけを出せ、と指示する）
OK_MARKER = "OK "
FAILED_MARKER = "FAILED "

#: 保険で拾う生成物の拡張子
IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp"})

#: サブスク枠を使い切った気配のある文言（そのままではユーザーに伝わらない）
QUOTA_MARKERS = (
    "rate limit",
    "rate_limit",
    "ratelimit",
    "too many requests",
    "quota",
    "usage limit",
    "limit reached",
    "out of credits",
    "429",
)


class GrokMediaError(Exception):
    """生成に失敗した（そのままジョブの失敗理由として出す）。"""


class GrokQuotaError(GrokMediaError):
    """サブスク枠を使い切った（やり直しても無駄なのでリトライしない）。"""


# --------------------------------------------------------------------------
# 設定（コマンド名はチャットと共有、作業ディレクトリと制限時間は専用）
# --------------------------------------------------------------------------

def command() -> str:
    return (load_settings().grok_command or "grok").strip()


def workdir() -> Path:
    return resolve_workdir(load_settings().grok_media_workdir, GROK_MEDIA_WORKDIR)


def timeout() -> float:
    return float(load_settings().grok_media_timeout or DEFAULT_TIMEOUT)


def auth_path() -> Path:
    """``~/.grok/auth.json``（CLI が認証済みかどうかの一次情報）。"""
    return Path.home() / AUTH_RELPATH


def sessions_dir() -> Path:
    """``~/.grok/sessions``（CLI が生成物を置く場所の根）。"""
    return Path.home() / SESSIONS_RELPATH


def clean_env() -> dict[str, str]:
    """CLI に渡す環境変数（``XAI_API_KEY`` を落としたもの）。"""
    return {k: v for k, v in os.environ.items() if k != API_KEY_ENV}


async def _exec(
    argv: list[str], cwd: str | Path, seconds: float
) -> tuple[int, str, str]:
    """CLI を 1 回実行する（テストが差し替える継ぎ目）。

    プロセスまわりは :func:`app.grok._exec` と同じものを使い、**環境変数だけ**
    差し替える。失敗の型はここで :class:`GrokMediaError` に揃える。
    """
    try:
        return await grok._exec(argv, cwd, seconds, env=clean_env())
    except LLMError as exc:
        raise GrokMediaError(str(exc)) from exc


# --------------------------------------------------------------------------
# 指示文（定型テンプレート）
# --------------------------------------------------------------------------

#: 生成の指示。**出力の約束事**（合図だけを出せ）を最後に置くのは、直前の指示ほど
#: 守られやすいため。縦横比は「希望」であって保証されない。
INSTRUCTION = """\
{headline}
{source}
## IMAGE PROMPT
{prompt}

## REQUIREMENTS
{requirements}- Save the finished image to this exact absolute path:
  {dest}
- Create the parent directory if it is missing, and overwrite any existing file.
- Never ask for confirmation, and do not stop before the file exists on disk.

## OUTPUT CONTRACT
Print one single line and nothing else:
- `OK {dest}` once the file exists at that path.
- `FAILED <one-line reason>` if you could not produce it.
"""

#: ``image_gen``（text-to-image）の書き出し
HEADLINE_GEN = (
    "Generate one image with your built-in `image_gen` tool (Grok Imagine)."
)
#: ``image_edit``（編集）の書き出し
HEADLINE_EDIT = (
    "Edit one image with your built-in `image_edit` tool (Grok Imagine)."
)

#: 編集元画像の渡し方。画像は作業ディレクトリの中にコピーしてあるので、
#: **ファイル名で参照させる**（CLI のサンドボックスの外は読めないことがある）。
SOURCE_IMAGE = """
## SOURCE IMAGE
Pass the image file `{name}` in this working directory ({path}) to `image_edit`
as its reference image. The prompt below is an EDIT INSTRUCTION for that
picture: keep everything it does not mention unchanged.
"""


@dataclass(frozen=True)
class ImageRequest:
    """CLI に投げる 1 回分の生成（履歴に残して再現できるようにする）。"""

    #: 使わせる内蔵ツール（``image_gen`` / ``image_edit``）
    tool: str
    #: モデルに渡すプロンプト本文
    prompt: str
    #: 生成物を置く**絶対パス**
    dest: Path
    #: ``image_gen`` に渡す縦横比（``1:1`` など。空なら指示に書かない）
    aspect_ratio: str = ""
    #: 編集元画像（**作業ディレクトリへコピー済み**の絶対パス）
    source_image: Path | None = None

    @property
    def instruction(self) -> str:
        """CLI の ``-p`` に渡す指示文。"""
        requirements = ""
        if self.aspect_ratio:
            requirements += (
                f"- Call the tool with `aspect_ratio: {self.aspect_ratio}`.\n"
            )
        source = ""
        if self.source_image is not None:
            source = SOURCE_IMAGE.format(
                name=self.source_image.name, path=self.source_image
            )
        return INSTRUCTION.format(
            headline=HEADLINE_EDIT if self.tool == "image_edit" else HEADLINE_GEN,
            prompt=self.prompt.strip() or "(no prompt given)",
            requirements=requirements,
            source=source,
            dest=self.dest,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "backend": "grok_cli",
            "tool": self.tool,
            "prompt": self.prompt,
            "aspect_ratio": self.aspect_ratio,
            "source_image": str(self.source_image) if self.source_image else "",
            "dest": str(self.dest),
            "instruction": self.instruction,
        }


def stage_input(source: str | Path, *, prefix: str = "") -> Path:
    """入力ファイルを grok の作業ディレクトリへコピーし、その絶対パスを返す。

    CLI に渡せるのは自然文だけなので、ファイルは**指示文から参照できる場所**に
    無ければならない。コピー先は ``<作業ディレクトリ>/inputs/<prefix>-<元の名前>``
    で、``prefix``（ジョブ id）を付けるのは同じ名前の画像を取り違えないため。
    """
    path = Path(source)
    if not path.is_file():
        raise GrokMediaError(f"入力ファイルが見つかりません: {path}")
    folder = workdir() / INPUTS_RELPATH
    try:
        folder.mkdir(parents=True, exist_ok=True)
        dest = folder / (f"{prefix}-{path.name}" if prefix else path.name)
        if path.resolve() != dest.resolve():
            shutil.copy2(path, dest)
    except OSError as exc:
        raise GrokMediaError(
            f"{path} を grok の作業ディレクトリに置けませんでした: {exc}"
        ) from exc
    return dest


def request_values(
    spec: WorkflowSpec, params: GenerationParams, inputs: dict[str, str]
) -> dict[str, str]:
    """宣言された論理名 -> 指示文に織り込む値。

    ``aspect_ratio`` はフォームのプリセットを Grok が受ける語彙へ寄せ、入力ファイル
    （``image``）は ``inputs`` の**ローカルパス**に解決する。宣言していないものは
    ここにも入らないので、指示文にも出ない。
    """
    task = spec.grok
    values: dict[str, str] = {}
    if task is None:
        return values
    for name in task.values:
        if name == "aspect_ratio":
            values["aspect_ratio"] = grok_aspect_ratio(params.aspect_ratio)
        elif name in INPUT_FIELDS:
            path = inputs.get(name, "")
            if path:
                values[name] = path
    return values


def build_request(
    spec: WorkflowSpec,
    params: GenerationParams,
    dest: Path,
    inputs: dict[str, str] | None = None,
) -> ImageRequest:
    """マニフェスト + ジョブのパラメータ -> 1 回分の生成。

    ComfyUI の :mod:`app.workflow` にあたる層。CLI にはグラフも ``input`` も
    無いので、宣言された論理値（:attr:`app.workflows.GrokImagineTask.values`）を
    **指示文に織り込む**だけ。``inputs`` は論理入力名（``image``）-> **ローカルの
    パス**で、作業ディレクトリへのコピー（:func:`stage_input`）はここで行う。
    """
    task = spec.grok
    if task is None:
        raise GrokMediaError(
            f"workflow '{spec.id}' に Grok Imagine のタスク宣言がありません"
        )
    dest = Path(dest)
    values = request_values(spec, params, inputs or {})
    source_image = values.get("image", "")
    if task.tool == "image_edit" and not source_image:
        raise GrokMediaError(
            f"workflow '{spec.id}' は編集元画像が必須です（source_image が空）"
        )
    return ImageRequest(
        tool=task.tool,
        prompt=params.image_prompt,
        dest=dest,
        aspect_ratio=values.get("aspect_ratio", ""),
        source_image=(
            stage_input(source_image, prefix=dest.parent.name)
            if source_image
            else None
        ),
    )


# --------------------------------------------------------------------------
# 出力の読み取り
# --------------------------------------------------------------------------

#: JSON 出力のうち、モデルの返答本文が入りうるキー（前から順に見る）
_TEXT_KEYS = ("text", "response", "result", "content", "output")


def result_texts(stdout: str) -> list[str]:
    """CLI の出力から返答本文を取り出す。

    ``--output-format plain`` では標準出力そのものが本文だが、JSON で返ってくる
    形（過去の版・将来の版）でも拾えるように、JSON オブジェクトも先になめる。
    """
    texts: list[str] = []
    for parsed in iter_json_objects(stdout):
        if not isinstance(parsed, dict):
            continue
        for key in _TEXT_KEYS:
            value = parsed.get(key)
            if isinstance(value, str) and value.strip():
                texts.append(value)
                break
    if stdout.strip():
        texts.append(stdout)
    return texts


def parse_signal(texts: list[str]) -> tuple[str, str]:
    """合図を読む: ``("ok" | "failed" | "", 添えられた文字列)``。

    エージェントは指示に反して前置きを書くことがあるので、**最後に現れた合図**を
    採る（囲みのバッククォートは剥がす）。
    """
    signal = ""
    detail = ""
    for text in texts:
        for line in (text or "").splitlines():
            stripped = line.strip().strip("`").strip()
            if stripped.startswith(OK_MARKER):
                signal, detail = "ok", stripped[len(OK_MARKER):].strip()
            elif stripped.startswith(FAILED_MARKER):
                signal, detail = "failed", stripped[len(FAILED_MARKER):].strip()
        if signal:
            # 最初に合図を含んだ本文を採る（標準出力そのものは候補の最後なので、
            # 素のテキストをもう一度なめて上書きしない）
            break
    return signal, detail


def looks_like_quota(text: str) -> bool:
    lowered = (text or "").lower()
    return any(marker in lowered for marker in QUOTA_MARKERS)


def _usable(path: Path | None, suffixes: frozenset[str]) -> Path | None:
    """実在してサイズ > 0 のファイルだけを返す（拡張子は保険探索でのみ見る）。"""
    if path is None:
        return None
    try:
        if not path.is_file() or path.stat().st_size <= 0:
            return None
    except OSError:
        return None
    if suffixes and path.suffix.lower() not in suffixes:
        return None
    return path


def recover_generated(since: float) -> Path | None:
    """CLI 自身の保存先から今回の生成物を拾う保険（mtime 順）。

    指示した絶対パスに置いてくれなかった場合の受け皿。実体は
    ``~/.grok/sessions/<URL エンコードした cwd>/<session-id>/images/1.jpg`` で、
    セッション id は実行のたびに変わるので**セッションの根から丸ごと探す**。
    ``since`` より古いファイル（前回の残り）は見ない。
    """
    folder = sessions_dir()
    if not folder.is_dir():
        return None
    found: list[tuple[float, Path]] = []
    for path in folder.rglob("*"):
        if _usable(path, IMAGE_SUFFIXES) is None:
            continue
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if mtime + 1.0 >= since:
            found.append((mtime, path))
    if not found:
        return None
    return max(found, key=lambda item: item[0])[1]


# --------------------------------------------------------------------------
# 生成
# --------------------------------------------------------------------------

#: 進捗を伝えるコールバック（WS 配信へ中継するために渡す）
ProgressCallback = Callable[[str], Awaitable[None]]


def _argv(instruction: str) -> list[str]:
    """ヘッドレス実行の引数。

    ``--always-approve`` が無いとツール実行の承認待ちでハングする。
    ``--output-format plain`` は合図だけを読み取るのに一番素直な形。
    ``--no-auto-update`` は実行のたびに CLI が自己更新して遅くなるのを防ぐ。
    """
    return [
        command(),
        "-p",
        instruction,
        "--always-approve",
        "--output-format",
        "plain",
        "--no-auto-update",
    ]


async def _attempt(request: ImageRequest, directory: Path) -> Path:
    """1 回だけ実行して成果物のパスを返す（4 段構えの判定はここ）。"""
    dest = request.dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    # 前回の生成物が残っていると「作られた」と誤認するので先に消す。
    if dest.exists():
        dest.unlink()
    started = time.time()

    code, out, err = await _exec(_argv(request.instruction), directory, timeout())
    detail = (err.strip() or out.strip() or "(no output)")[:500]

    # ① 終了コード
    if code != 0:
        if looks_like_quota(f"{out}\n{err}"):
            raise GrokQuotaError(_quota_message(detail))
        if looks_like_auth_error(detail):
            raise GrokMediaError(
                "grok CLI が認証されていません。ターミナルで `grok`"
                "（サーバーでは `grok --device-auth`）を実行してサインインして"
                f"ください: {detail}"
            )
        raise GrokMediaError(f"grok CLI が失敗しました (exit {code}): {detail}")

    # ② 合図
    signal, note = parse_signal(result_texts(out))
    if signal == "failed" and looks_like_quota(note):
        raise GrokQuotaError(_quota_message(note))

    # ③ 指定パスのファイル、④ CLI 自身の保存先からの回収
    saved = _usable(dest, frozenset())
    if saved is None and note:
        saved = _collect(Path(note), dest)
    if saved is None:
        saved = _collect(recover_generated(started), dest)
    if saved is not None:
        if not signal:
            log.info("grok CLI は合図を返しませんでしたが %s は出来ています", saved)
        return saved

    if signal == "failed":
        raise GrokMediaError(
            f"Grok が画像を生成できませんでした: {note or '(理由の説明なし)'}"
        )
    raise GrokMediaError(
        f"grok CLI は終了しましたが画像が見つかりません"
        f"（{dest} も {sessions_dir()} も空）: {detail}"
    )


def _quota_message(detail: str) -> str:
    return (
        "Grok のサブスク枠（Chat / Imagine / Build 共通のプール）を使い切ったよう"
        f"です。時間をおいてから試してください: {detail[:300]}"
    )


def _collect(source: Path | None, dest: Path) -> Path | None:
    """``source`` を ``dest`` に持ってくる（同じファイルならそのまま）。"""
    usable = _usable(source, IMAGE_SUFFIXES)
    if usable is None:
        return None
    if usable.resolve() == dest.resolve():
        return dest
    try:
        shutil.copy2(usable, dest)
    except OSError as exc:
        raise GrokMediaError(f"{usable} を {dest} に置けませんでした: {exc}") from exc
    return dest


async def generate(
    request: ImageRequest, *, on_progress: ProgressCallback | None = None
) -> Path:
    """CLI に 1 枚作らせる（失敗したら 1 回だけやり直す）。"""
    directory = workdir()
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise GrokMediaError(
            f"grok の作業ディレクトリを作成できません: {directory} ({exc})"
        ) from exc

    failure: GrokMediaError | None = None
    for attempt in range(RETRIES + 1):
        if attempt:
            log.warning("grok CLI の生成をやり直します (%s): %s", attempt, failure)
            if on_progress is not None:
                await on_progress(f"生成をやり直しています（{attempt + 1} 回目）")
        try:
            return await _attempt(request, directory)
        except GrokQuotaError:
            raise  # 枠を使い切ったのでやり直しても無駄
        except GrokMediaError as exc:
            failure = exc
    raise failure or GrokMediaError("grok CLI の生成に失敗しました")


# --------------------------------------------------------------------------
# 可用性（SPEC §5.2）
# --------------------------------------------------------------------------

def _key_warning() -> str:
    """``XAI_API_KEY`` が環境に残っているときの注意書き（外して実行はする）。"""
    return (
        f" / 注意: 環境変数 {API_KEY_ENV} が設定されていますが、"
        "サブスク枠で回すため実行時には外します"
        if os.environ.get(API_KEY_ENV)
        else ""
    )


async def check_backend() -> HealthStatus:
    """軽い確認（起動時・設定保存時に回しても枠を消費しないもの）。

    毎回 CLI を実際に回すとサブスク枠を消費して遅いので、判定は
    **コマンドが実行できること**と**``~/.grok/auth.json`` が在ること**の 2 つ。
    実際に通るかどうかは設定ページの「接続確認」（:func:`check_live`）で確かめる。
    """
    cmd = command()
    if not cmd:
        return HealthStatus(status="not_configured", detail="grok_command が空です")
    if shutil.which(cmd) is None and not Path(cmd).is_file():
        return HealthStatus(
            status="not_configured",
            detail=(
                f"'{cmd}' コマンドが見つかりません。Grok Build CLI をインストール"
                " (curl -fsSL https://x.ai/cli/install.sh | bash) してください"
            ),
        )
    auth = auth_path()
    if not auth.is_file():
        return HealthStatus(
            status="not_configured",
            detail=(
                f"grok CLI が未認証です（{auth} がありません）。ターミナルで `grok`、"
                "サーバーでは `grok --device-auth` を実行してサインインしてください"
            ),
        )
    return HealthStatus(
        status="ok", detail=f"{cmd} / 認証済み ({auth}){_key_warning()}"
    )


async def check_live() -> HealthStatus:
    """設定ページの「接続確認」: 実際に 1 ターン回してみる。

    生成はせず短い返答をもらうだけなので、枠の消費は最小で済む。
    """
    status = await check_backend()
    if status.status != "ok":
        return status
    directory = workdir()
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return HealthStatus(
            status="error",
            detail=f"grok の作業ディレクトリを作成できません: {directory} ({exc})",
        )
    argv = [
        command(),
        "-p",
        "Reply with exactly: ok",
        "--always-approve",
        "--output-format",
        "plain",
        "--no-auto-update",
    ]
    try:
        code, out, err = await _exec(argv, directory, CHECK_TIMEOUT)
    except GrokMediaError as exc:
        return HealthStatus(status="error", detail=str(exc))
    detail = (err.strip() or out.strip() or "(no output)")[:300]
    if code != 0:
        if looks_like_quota(f"{out}\n{err}"):
            return HealthStatus(status="error", detail=_quota_message(detail))
        return HealthStatus(
            status="error", detail=f"grok CLI が失敗しました (exit {code}): {detail}"
        )
    return HealthStatus(
        status="ok", detail=f"{command()} で実行できました{_key_warning()}"
    )
