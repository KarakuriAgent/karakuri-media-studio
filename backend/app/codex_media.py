"""Codex CLI 経由の画像生成（4 つめの生成バックエンド、SPEC §5.4 / issue #23）。

OpenAI の従量課金 API（``OPENAI_API_KEY``）ではなく、**ChatGPT Plus / Pro の
サブスクリプション枠**で動く公式 CLI（``codex``）をヘッドレス実行し、組み込み
スキル ``$imagegen``（``.system`` に同梱、インストール不要）に gpt-image-2 で
描かせる。テキストの描画・フォトリアル・人物同一性の保持が強いので、
「高品質枠として少量」使う位置づけ（画像生成ターンは通常ターンの 3〜5 倍速く
サブスク枠を消費する、issue #23）。

:mod:`app.grok_media`（Grok Build CLI）と発想は同じ

- **専用の作業ディレクトリ**（``runtime/codex-media-workdir``）で走らせる。
  ``codex exec`` はコーディングエージェントで、``--sandbox workspace-write`` は
  その作業根の下への書き込みを許すので、リポジトリの中で走らせない
- **成否は言葉ではなくファイルで判定する**（下記の 3 段構え）
- **失敗したら 1 回だけやり直す**。ただしサブスク枠を使い切った気配のときは
  やり直しても無駄なので、そのままユーザー向けの文言にして投げる

一方でコマンド体系は Grok CLI とまったく違うので、共通化はしていない:

- 起動は ``codex exec --skip-git-repo-check --sandbox workspace-write
  -C <作業ディレクトリ> --output-last-message <一時ファイル> '<指示>'``
- **最終応答は標準出力ではなくファイルで受け取る**（``--output-last-message``）。
  標準出力は進捗ログなので、合図（``OK`` / ``FAILED``）はそのファイルから読む
- 保存先を指示する引数は無く、**プロンプトでコピーさせるのが公式想定フロー**
  （imagegen の SKILL.md）。既定の保存先は ``~/.codex/generated_images/``

成否の判定（issue #23、3 段構え）:

1. **終了コード**。0 でも「タスクは失敗したがターンは完走した」があり得るので、
   単独では信用しない
2. ``--output-last-message`` のファイルを合図（``OK <パス>`` / ``FAILED <理由>``）で
   パースする
3. **出力パスにファイルが実在し、サイズ > 0 で、PNG のマジックバイトを持つ**。
   これが最終判定で、合図と食い違ったら**ファイルを採る**。見つからなければ
   ``~/.codex/generated_images/`` を mtime 順で探す保険を掛ける
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import tempfile
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .backends import BackendStatus
from .config import load_settings
from .models import GenerationParams
from .paths import CODEX_MEDIA_WORKDIR
from .workflows import INPUT_FIELDS, KIE_SELECT_PREFIX, WorkflowSpec

log = logging.getLogger(__name__)

#: 1 枚の生成に許す既定の秒数（設定 ``codex_timeout`` で上書きできる）
DEFAULT_TIMEOUT = 300.0
#: 「接続確認」ボタンの実行チェックに許す秒数
CHECK_TIMEOUT = 60.0
#: 失敗したときにやり直す回数
RETRIES = 1

#: 環境から必ず外す変数（残っていると API 従量課金にフォールバックしうる）
API_KEY_ENV = "OPENAI_API_KEY"
#: CLI の設定・認証の置き場を指す環境変数（既定は ``~/.codex``）
HOME_ENV = "CODEX_HOME"

#: 認証情報のファイル名（``codex login`` が CLI ホームに書く）
AUTH_FILENAME = "auth.json"
#: ``codex login status`` が「ChatGPT でサインイン済み」と答える条件。auth.json の
#: このキーにトークン一式が入っていればサブスク枠で動く（API キーだけのログインは
#: 従量課金なので、この経路では使わない）。
AUTH_TOKENS_KEY = "tokens"
#: imagegen が既定で生成物を置くディレクトリ（CLI ホームからの相対）
GENERATED_IMAGES_RELNAME = "generated_images"

#: 合図（この 2 つだけを出せ、と指示する）
OK_MARKER = "OK "
FAILED_MARKER = "FAILED "

#: PNG のマジックバイト（③ の最終判定。JPEG やテキストを掴まされない）
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

#: サブスク枠を使い切った気配のある文言（そのままではユーザーに伝わらない）
QUOTA_MARKERS = (
    "usage limit",
    "rate limit",
    "rate_limit",
    "ratelimit",
    "too many requests",
    "quota",
    "limit reached",
    "out of credits",
    "insufficient_quota",
    "429",
)

#: 未認証の気配のある文言（案内をサインインの手順に差し替える）
AUTH_MARKERS = (
    "not logged in",
    "not authenticated",
    "unauthorized",
    "please run codex login",
    "codex login",
    "401",
)


class CodexMediaError(Exception):
    """生成に失敗した（そのままジョブの失敗理由として出す）。"""


class CodexQuotaError(CodexMediaError):
    """サブスク枠を使い切った（やり直しても無駄なのでリトライしない）。"""


# --------------------------------------------------------------------------
# 設定
# --------------------------------------------------------------------------

def command() -> str:
    return (load_settings().codex_command or "codex").strip()


def timeout() -> float:
    return float(load_settings().codex_timeout or DEFAULT_TIMEOUT)


def workdir() -> Path:
    """``codex exec -C`` に渡す作業ディレクトリ（専用の空ディレクトリ）。"""
    return Path(CODEX_MEDIA_WORKDIR)


def codex_home() -> Path:
    """CLI の設定・認証・生成物の置き場（``CODEX_HOME`` があればそちら）。"""
    return Path(os.environ.get(HOME_ENV) or (Path.home() / ".codex"))


def auth_path() -> Path:
    """``~/.codex/auth.json``（``codex login status`` の一次情報）。"""
    return codex_home() / AUTH_FILENAME


def clean_env() -> dict[str, str]:
    """CLI に渡す環境変数（``OPENAI_API_KEY`` を落としたもの）。

    残っていると CLI が API キー認証（従量課金）に倒れうるので、サブスク枠で
    回す約束を守るために必ず外す（SPEC §5.4）。
    """
    return {k: v for k, v in os.environ.items() if k != API_KEY_ENV}


async def _exec(
    argv: list[str], cwd: str | Path, seconds: float
) -> tuple[int, str, str]:
    """CLI を 1 回実行する（テストが差し替える継ぎ目）。

    :func:`app.grok._exec` と同じ形だが、見つからないときの案内が Codex のもの
    なので別に持つ。失敗の型はここで :class:`CodexMediaError` に揃える。
    """
    directory = Path(cwd)
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise CodexMediaError(
            f"codex の作業ディレクトリを作成できません: {directory} ({exc})"
        ) from exc
    try:
        process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(directory),
            env=clean_env(),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise CodexMediaError(
            f"'{argv[0]}' コマンドが見つかりません。Codex CLI をインストール"
            " (npm install -g @openai/codex) してください"
        ) from exc
    except OSError as exc:
        raise CodexMediaError(f"'{argv[0]}' を起動できませんでした: {exc}") from exc

    try:
        out, err = await asyncio.wait_for(process.communicate(), timeout=seconds)
    except asyncio.TimeoutError as exc:
        with suppress(ProcessLookupError):
            process.kill()
        with suppress(Exception):  # reap the child so its pipes are closed
            await process.communicate()
        raise CodexMediaError(
            f"codex CLI が {seconds:.0f} 秒以内に応答しませんでした（タイムアウト）"
        ) from exc
    return (
        process.returncode or 0,
        out.decode("utf-8", "replace"),
        err.decode("utf-8", "replace"),
    )


# --------------------------------------------------------------------------
# 指示文（定型テンプレート）
# --------------------------------------------------------------------------

#: 生成の指示。``$imagegen`` は Codex の組み込みスキルの呼び出しで、保存先を渡す
#: 引数は無いので**プロンプトでコピーさせる**のが公式想定フロー。**出力の約束事**
#: （合図だけを出せ）を最後に置くのは、直前の指示ほど守られやすいため。サイズと
#: 品質は自然文で効く前提だが、API 同等の厳密保証は無い（issue #23）。
INSTRUCTION = """\
$imagegen Generate exactly one image with your built-in image generation skill.

## IMAGE PROMPT
{prompt}

## REQUIREMENTS
- Size: {size} pixels.
- Quality: {quality}.
- The skill writes into its own folder; once it is done, copy the finished
  picture **as a PNG file** to this exact absolute path:
  {dest}
- Create the parent directory if it is missing, and overwrite any existing file.
- Do not edit any other file, do not ask for confirmation, and do not stop
  before the file exists on disk.

## OUTPUT CONTRACT
Your final message must be one single line and nothing else:
- `OK {dest}` once the PNG exists at that path.
- `FAILED <one-line reason>` if you could not produce it.
"""


@dataclass(frozen=True)
class ImageRequest:
    """CLI に投げる 1 回分の生成（履歴に残して再現できるようにする）。

    Grok CLI の :class:`app.grok_media.MediaRequest` にあたるもの。gpt-image-2 は
    今のところ text-to-image だけなので、入力ファイルも動画向けの項目も持たない。
    """

    #: モデルに渡すプロンプト本文
    prompt: str
    #: 生成物を置く**絶対パス**（PNG）
    dest: Path
    #: 希望する大きさ（``"1024x1024"`` などの選択式フィールドの値）
    size: str = ""
    #: 希望する品質（``low`` / ``medium`` / ``high``）
    quality: str = ""

    @property
    def instruction(self) -> str:
        """``codex exec`` に渡す指示文。"""
        return INSTRUCTION.format(
            prompt=self.prompt.strip() or "(no prompt given)",
            size=self.size.strip() or "as you see fit",
            quality=self.quality.strip() or "as you see fit",
            dest=self.dest,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "media": "image",
            "prompt": self.prompt,
            "size": self.size,
            "quality": self.quality,
            "dest": str(self.dest),
            "instruction": self.instruction,
        }


def request_values(
    spec: WorkflowSpec, params: GenerationParams, inputs: dict[str, str]
) -> dict[str, str]:
    """宣言された論理名 -> 指示文に織り込む値（kie の ``task_values`` にあたる層）。

    ``select:<名前>`` は選ばれた値（未指定ならマニフェストの既定）に解決する。
    宣言していないものはここにも入らないので、指示文にも出ない。
    """
    task = spec.codex
    values: dict[str, str] = {}
    if task is None:
        return values
    for name in task.values:
        if name.startswith(KIE_SELECT_PREFIX):
            plain = name[len(KIE_SELECT_PREFIX):]
            select = spec.select(plain)
            if select is not None:
                values[plain] = params.selects.get(plain) or select.fallback
        elif name == "aspect_ratio":
            values["aspect_ratio"] = params.aspect_ratio
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

    ComfyUI の :mod:`app.workflow`、kie.ai の :func:`app.kie.build_request`、
    Grok CLI の :func:`app.grok_media.build_request` にあたる層。
    """
    task = spec.codex
    if task is None:
        raise CodexMediaError(
            f"workflow '{spec.id}' に codex_cli のタスク宣言がありません"
        )
    values = request_values(spec, params, inputs or {})
    return ImageRequest(
        prompt=params.image_prompt,
        dest=Path(dest),
        size=values.get("size", ""),
        quality=values.get("quality", ""),
    )


# --------------------------------------------------------------------------
# 出力の読み取り
# --------------------------------------------------------------------------

def parse_signal(text: str) -> tuple[str, str]:
    """合図を読む: ``("ok" | "failed" | "", 添えられた文字列)``。

    エージェントは指示に反して前置きを書くことがあるので、**最後に現れた合図**を
    採る（囲みのバッククォートは剥がす）。
    """
    signal = ""
    detail = ""
    for line in (text or "").splitlines():
        stripped = line.strip().strip("`").strip()
        if stripped.startswith(OK_MARKER):
            signal, detail = "ok", stripped[len(OK_MARKER):].strip()
        elif stripped.startswith(FAILED_MARKER):
            signal, detail = "failed", stripped[len(FAILED_MARKER):].strip()
    return signal, detail


def looks_like_quota(text: str) -> bool:
    lowered = (text or "").lower()
    return any(marker in lowered for marker in QUOTA_MARKERS)


def looks_like_auth_error(text: str) -> bool:
    lowered = (text or "").lower()
    return any(marker in lowered for marker in AUTH_MARKERS)


def is_png(path: Path) -> bool:
    """先頭 8 バイトが PNG のマジックバイトか（③ の最終判定）。"""
    try:
        with open(path, "rb") as fh:
            return fh.read(len(PNG_MAGIC)) == PNG_MAGIC
    except OSError:
        return False


def _usable(path: Path | None) -> Path | None:
    """実在してサイズ > 0 で、中身が PNG のファイルだけを返す。"""
    if path is None:
        return None
    try:
        if not path.is_file() or path.stat().st_size <= 0:
            return None
    except OSError:
        return None
    return path if is_png(path) else None


def recover_generated(since: float) -> Path | None:
    """``~/.codex/generated_images/`` から今回の生成物を拾う保険（mtime 順）。

    エージェントがコピーを忘れて既定の置き場に残した場合の受け皿。``since`` より
    古いファイル（前回の残り）は見ない。
    """
    folder = codex_home() / GENERATED_IMAGES_RELNAME
    if not folder.is_dir():
        return None
    found: list[tuple[float, Path]] = []
    for path in folder.rglob("*"):
        if _usable(path) is None:
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


def _collect(source: Path | None, dest: Path) -> Path | None:
    """``source`` を ``dest`` に持ってくる（同じファイルならそのまま）。"""
    usable = _usable(source)
    if usable is None:
        return None
    if usable.resolve() == dest.resolve():
        return dest
    try:
        shutil.copy2(usable, dest)
    except OSError as exc:
        raise CodexMediaError(f"{usable} を {dest} に置けませんでした: {exc}") from exc
    return dest


# --------------------------------------------------------------------------
# 生成
# --------------------------------------------------------------------------

#: 進捗を伝えるコールバック（WS 配信へ中継するために渡す）
ProgressCallback = Callable[[str], Awaitable[None]]


def _argv(request: ImageRequest, directory: Path, message_file: Path) -> list[str]:
    """ヘッドレス実行の引数（issue #23 の推奨形）。

    - ``--skip-git-repo-check``: 作業ディレクトリは git リポジトリではない
    - ``--sandbox workspace-write``: 生成物を書き出せないと何も残らない
    - ``-C``: 専用の作業ディレクトリ（リポジトリの中では走らせない）
    - ``--output-last-message``: **最終応答をファイルで受け取る**（標準出力は
      進捗ログなので、合図はこのファイルから読む）
    """
    return [
        command(),
        "exec",
        "--skip-git-repo-check",
        "--sandbox",
        "workspace-write",
        "-C",
        str(directory),
        "--output-last-message",
        str(message_file),
        request.instruction,
    ]


def _message_file(directory: Path) -> Path:
    """``--output-last-message`` に渡す一時ファイル（作業ディレクトリの中）。"""
    handle, name = tempfile.mkstemp(
        prefix="last-message-", suffix=".txt", dir=str(directory)
    )
    os.close(handle)
    return Path(name)


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


async def _attempt(request: ImageRequest, directory: Path) -> Path:
    """1 回だけ実行して成果物のパスを返す（3 段構えの判定はここ）。"""
    dest = request.dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    # 前回の生成物が残っていると「作られた」と誤認するので先に消す。
    if dest.exists():
        dest.unlink()
    started = time.time()
    message_file = _message_file(directory)
    try:
        code, out, err = await _exec(_argv(request, directory, message_file), directory,
                                     timeout())
        last = _read(message_file)
    finally:
        with suppress(OSError):
            message_file.unlink()
    detail = (err.strip() or out.strip() or "(no output)")[:500]

    # ② 合図（最終応答のファイルから読む。無ければ標準出力も見る）
    signal, note = parse_signal(last or out)
    # 枠切れは「やり直しても無駄」なので、ファイルを探すより先に切り上げる。
    # 成功した実行のログに紛れた語で誤判定しないよう、**失敗しているとき**だけ見る。
    if signal == "failed" and looks_like_quota(note):
        raise CodexQuotaError(_quota_message(note))
    if code != 0 and looks_like_quota(f"{out}\n{err}"):
        raise CodexQuotaError(_quota_message(detail))

    # ③ 出力パスの実在・サイズ・PNG マジックバイト（合図と食い違ったらこちらを採る）
    saved = _usable(dest)
    if saved is None and note:
        saved = _collect(Path(note), dest)
    if saved is None:
        saved = _collect(recover_generated(started), dest)
    if saved is not None:
        if signal != "ok":
            log.info("codex CLI は合図を返しませんでしたが %s は出来ています", saved)
        return saved

    # ① 終了コード（ファイルが無いときだけ、失敗の説明として使う）
    if code != 0:
        if looks_like_auth_error(f"{out}\n{err}"):
            raise CodexMediaError(_auth_message(detail))
        raise CodexMediaError(f"codex CLI が失敗しました (exit {code}): {detail}")
    if signal == "failed":
        raise CodexMediaError(
            f"Codex が画像を生成できませんでした: {note or '(理由の説明なし)'}"
        )
    if dest.exists():
        raise CodexMediaError(
            f"codex CLI が置いたファイルは PNG ではありません（{dest}）: {detail}"
        )
    raise CodexMediaError(
        f"codex CLI は終了しましたが画像が見つかりません"
        f"（{dest} も {GENERATED_IMAGES_RELNAME}/ も空）: {detail}"
    )


def _quota_message(detail: str) -> str:
    return (
        "ChatGPT のサブスク枠（5 時間 / 週次のプール）を使い切ったようです。"
        "画像生成は通常のターンより速く枠を消費します。時間をおいてから試して"
        f"ください: {detail[:300]}"
    )


def _auth_message(detail: str) -> str:
    return (
        "codex CLI が認証されていません。ターミナルで `codex login` を実行して"
        f"ChatGPT アカウントでサインインしてください: {detail}"
    )


async def generate(
    request: ImageRequest, *, on_progress: ProgressCallback | None = None
) -> Path:
    """CLI に 1 枚描かせる（失敗したら 1 回だけやり直す）。"""
    directory = workdir()
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise CodexMediaError(
            f"codex の作業ディレクトリを作成できません: {directory} ({exc})"
        ) from exc

    failure: CodexMediaError | None = None
    for attempt in range(RETRIES + 1):
        if attempt:
            log.warning("codex CLI の生成をやり直します (%s): %s", attempt, failure)
            if on_progress is not None:
                await on_progress(f"生成をやり直しています（{attempt + 1} 回目）")
        try:
            return await _attempt(request, directory)
        except CodexQuotaError:
            raise  # 枠を使い切ったのでやり直しても無駄
        except CodexMediaError as exc:
            failure = exc
    raise failure or CodexMediaError("codex CLI の生成に失敗しました")


# --------------------------------------------------------------------------
# 可用性（SPEC §5.2 / §5.4）
# --------------------------------------------------------------------------

def _key_warning() -> str:
    """``OPENAI_API_KEY`` が環境に残っているときの注意書き（外して実行はする）。"""
    return (
        f" / 注意: 環境変数 {API_KEY_ENV} が設定されていますが、"
        "サブスク枠で回すため実行時には外します"
        if os.environ.get(API_KEY_ENV)
        else ""
    )


def _signed_in(auth: Path) -> bool:
    """``auth.json`` が ChatGPT サインイン（サブスク枠）のものか。

    ``codex login status` が「Logged in using ChatGPT」と答える状態は、この
    ファイルに :data:`AUTH_TOKENS_KEY` のトークン一式が入っていること。API キー
    だけのログインは従量課金なので、この経路では使えないものとして扱う。
    """
    try:
        parsed = json.loads(auth.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(parsed, dict) and isinstance(parsed.get(AUTH_TOKENS_KEY), dict)


async def check_backend() -> BackendStatus:
    """起動時・設定保存時の確認（SPEC §5.2）。

    毎回 CLI を実際に回すとサブスク枠を消費して遅いので、判定は
    **コマンドが実行できること**と**``~/.codex/auth.json`` が ChatGPT
    サインインのものであること**の 2 つ（``codex login status`` 相当、issue #23）。
    実際に通るかどうかは設定ページの「接続確認」（:func:`check_live`）で確かめる。
    """
    cmd = command()
    if not cmd:
        return BackendStatus("codex_cli", "not_configured", "codex_command が空です")
    if shutil.which(cmd) is None and not Path(cmd).is_file():
        return BackendStatus(
            "codex_cli",
            "not_configured",
            f"'{cmd}' コマンドが見つかりません。Codex CLI をインストール"
            " (npm install -g @openai/codex) してください",
        )
    auth = auth_path()
    if not auth.is_file():
        return BackendStatus(
            "codex_cli",
            "not_configured",
            f"codex CLI が未認証です（{auth} がありません）。ターミナルで"
            " `codex login` を実行して ChatGPT アカウントでサインインしてください",
        )
    if not _signed_in(auth):
        return BackendStatus(
            "codex_cli",
            "not_configured",
            f"{auth} に ChatGPT のサインイン情報がありません（API キーでの"
            "ログインは従量課金なのでこの経路では使いません）。`codex login` で"
            "サインインし直してください",
        )
    return BackendStatus(
        "codex_cli", "ok", f"{cmd} / ChatGPT サインイン済み ({auth}){_key_warning()}"
    )


async def check_live() -> BackendStatus:
    """設定ページの「接続確認」: 実際に ``codex login status`` を回してみる。

    画像は生成しないので枠を消費しない（生成ターンは通常の 3〜5 倍速く枠を
    食うので、確認のたびに 1 枚描かせるわけにはいかない）。
    """
    status = await check_backend()
    if not status.available:
        return status
    directory = workdir()
    try:
        directory.mkdir(parents=True, exist_ok=True)
        code, out, err = await _exec(
            [command(), "login", "status"], directory, CHECK_TIMEOUT
        )
    except CodexMediaError as exc:
        return BackendStatus("codex_cli", "error", str(exc))
    detail = (out.strip() or err.strip() or "(no output)")[:300]
    if code != 0:
        if looks_like_auth_error(f"{out}\n{err}"):
            return BackendStatus("codex_cli", "error", _auth_message(detail))
        return BackendStatus(
            "codex_cli", "error", f"codex CLI が失敗しました (exit {code}): {detail}"
        )
    return BackendStatus("codex_cli", "ok", f"{detail}{_key_warning()}")
