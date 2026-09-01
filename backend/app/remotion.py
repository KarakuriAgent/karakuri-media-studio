"""Remotion（React で組んだ動画）のレンダリング（SPEC §5.2）。

ComfyUI とまったく同じ考え方で、**Remotion プロジェクトをアプリが参照する**
だけの薄い層。プロジェクト（Node のリポジトリ）はリポジトリルートの ``remotion/``
に同梱してあり、常にそこを使う（composition を足す・直すときは
``remotion/src/`` を編集する）。**Remotion は独自ライセンス**
（個人・従業員 3 名以下の会社は無償、それ以上は会社ライセンスが必要）なので、
連携そのものは ``remotion_enabled`` が **既定 OFF**。ここがやるのは 3 つだけ:

- :func:`list_compositions` — ``npx remotion compositions <entry>`` を叩いて、
  そのプロジェクトが持つ composition の ID を並べる（短時間キャッシュ）。
- :func:`render` — ``npx remotion render`` をサブプロセスで回し、標準出力の
  進捗を呼び出し元（:mod:`app.jobs`）へ流し、出来上がった mp4 のパスを返す。
- :func:`remux_audio` — その mp4 の**音声だけ元音源から焼き直す**（Remotion の
  出力は音声が 1 フレームぶん遅れるため。後述）。

**ジョブとして扱う**のが要点で、レンダリングは ComfyUI / Grok と並ぶもう 1 つの
生成経路として ``outputs/{job_id}/`` に成果物を置く。そのため履歴・WS・ライブラリ・
素材登録・タイムラインの素材ビンには何も足さずに乗る。

エントリポイントは ``src/index.ts`` を既定とし、プロジェクトの ``package.json`` に
``config.remotionEntry`` があればそちらを使う（Remotion の慣習に合わせた逃げ道）。

``props`` は CLI の引数に直接埋めると長さと引用符で壊れるので、**一時 JSON
ファイル**（``runtime/remotion/``）に書いて ``--props=<file>`` で渡し、終わったら
消す。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from pathlib import Path
from typing import Any

from .config import load_settings
from .paths import (
    ASSETS_DIR,
    LIBRARY_DIR,
    OUTPUTS_DIR,
    REMOTION_BUNDLED_DIR,
    REMOTION_TMP_DIR,
    rebase_stored_path,
)

log = logging.getLogger(__name__)

#: composition 一覧の取得に許す秒数
LIST_TIMEOUT = 60.0
#: 一覧のキャッシュを持つ秒数（プロジェクトを書き換えた直後でもすぐ追いつける長さ）
CACHE_TTL = 60.0

#: エントリポイントの既定（``package.json`` の ``config.remotionEntry`` が優先）
DEFAULT_ENTRY = "src/index.ts"

#: Remotion を起動するコマンド（``npx remotion …`` を実行する）
NPX = "npx"

#: 失敗理由に載せる文字数の上限（そのままユーザーに出る）
ERROR_TAIL = 400
#: 失敗理由に載せる行数（要点だけ。スタックトレース全文はサーバーログへ）
ERROR_LINES = 2

#: 要点らしい行（``Error: …`` / ``TypeError: …`` / Remotion の ``✕`` 見出し）
_ERROR_LINE_RE = re.compile(
    r"(?:^|\s)(?:[A-Za-z_$][\w$]*)?Error\b|^\s*[✕✖×]|^\s*(?:Failed|FATAL)\b",
    re.IGNORECASE,
)
#: スタックトレースの構成要素（``at foo (…)`` / ``node:internal/…`` / 桁合わせの
#: ``^^^``）。ユーザー向けの文言には出さない
_STACK_LINE_RE = re.compile(
    r"^(?:at\s|\.{3}\s|node:internal|Require stack:|-\s+/|\^+$|\|)"
)

#: 進捗の見出しとして無視できない行の目印。Remotion の出力は版によって
#: 「Rendered 120/300」「Rendering frames | ██ | 120/300」などまちまちなので、
#: 「フレームらしい語 + a/b」の形だけを緩く拾う。
_PROGRESS_WORDS = ("render", "frame", "encod", "stitch", "bundl")
_FRACTION_RE = re.compile(r"(\d+)\s*/\s*(\d+)")
#: ANSI エスケープ（進捗バーの色・カーソル移動）
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]")

#: 一覧の表形式で読み飛ばす見出し
_TABLE_HEADERS = frozenset({"composition", "id"})
#: 表の直前に必ず出る見出し行。この行より後ろだけが表なので、ブラウザの
#: ダウンロードログ（``Got Headless Shell`` など）を丸ごと切り落とせる。
#: 版によって末尾のコロンや大文字小文字が違いうるので寛容に見る。
_HEADING_RE = re.compile(r"the following compositions are available:?$", re.IGNORECASE)
#: composition の ID に Remotion が許す文字（``^[a-zA-Z0-9-]+$``）。数字始まり
#: （``4kIntro``）は通し、アンダースコアは Remotion 側が拒むので通さない。
_ID_RE = re.compile(r"[A-Za-z0-9-]+")
#: 一覧の表の 2 列目以降らしいトークン。実際の 1 行は
#: ``MusicVideo    30      1920x1080      240 (8.00 sec)`` なので、FPS・
#: ``1920x1080`` に加えて括弧付きの秒数（``(8.00`` / ``sec)``）と、静止画の
#: ``Still`` まで数え方に入れる。
_TABLE_CELL_RE = re.compile(r"\(?[\d.]+(?:x[\d.]+)?%?\)?|sec\)?|still", re.IGNORECASE)

#: 進捗の中継先（``(0..1 の割合 or None, 出力行)``）
ProgressCallback = Callable[[float | None, str], Awaitable[None]]


class RemotionError(Exception):
    """Remotion の実行に失敗した（そのままユーザー向けの文言として出す）。"""


class RemotionNotConfigured(RemotionError):
    """``remotion_enabled`` が false = 機能が無効（呼び出し側は 400 にする）。"""


# --------------------------------------------------------------------------
# プロジェクトの場所とエントリポイント
# --------------------------------------------------------------------------

def project_dir() -> Path:
    """いま使う Remotion プロジェクトのディレクトリ（同梱の ``remotion/``）。

    連携が無効（``remotion_enabled`` が false）なら :class:`RemotionNotConfigured`。
    有効なら :data:`app.paths.REMOTION_BUNDLED_DIR` を返す。依存が入っていなければ
    :class:`RemotionError`。
    """
    settings = load_settings()
    if not settings.remotion_enabled:
        raise RemotionNotConfigured(
            "Remotion 連携が無効です"
            "（設定ページの「Remotion 連携」で有効にしてください。"
            "ライセンスの注意書きも確認してください）"
        )
    directory = REMOTION_BUNDLED_DIR
    # 依存（node_modules/）は通常 ``run.sh`` が初回に入れるが、手で起動した場合や
    # Docker のようにホスト側で入れ忘れた場合は無いことがある。入っていないと
    # ``npx remotion`` が意味不明なエラーで落ちるので、ここで何をすればよいかまで言う。
    if not (directory / "node_modules").is_dir():
        raise RemotionError(
            f"Remotion の依存が入っていません: {directory}"
            "（`npm --prefix remotion install` を実行してください。"
            "通常は `run.sh` が自動で行います）"
        )
    return directory


def entry_point(directory: Path) -> str:
    """エントリポイントの**プロジェクト相対**パス（既定 ``src/index.ts``）。

    ``package.json`` の ``config.remotionEntry`` があればそれを優先する。読めない
    / 壊れている package.json は既定に倒す（プロジェクト側の問題は実行時の
    エラーで分かる）。
    """
    manifest = directory / "package.json"
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return DEFAULT_ENTRY
    config = data.get("config") if isinstance(data, dict) else None
    entry = (config or {}).get("remotionEntry") if isinstance(config, dict) else None
    return str(entry).strip() if entry and str(entry).strip() else DEFAULT_ENTRY


def resolve_entry(directory: Path) -> str:
    """エントリポイントを決めて、実在することまで確かめる。"""
    entry = entry_point(directory)
    if not (directory / entry).exists():
        raise RemotionError(
            f"Remotion のエントリポイントが見つかりません: {directory / entry}"
            "（package.json の config.remotionEntry で指定できます）"
        )
    return entry


# --------------------------------------------------------------------------
# サブプロセス（テストが差し替える継ぎ目）
# --------------------------------------------------------------------------

def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


async def _pump(
    stream: asyncio.StreamReader | None,
    on_line: Callable[[str], Awaitable[None]],
) -> None:
    """``\\r`` 区切りの進捗バーも 1 行として読み出す。

    Remotion は進捗を ``\\r`` で上書きするので、``readline`` では最後まで 1 行も
    取れないことがある。生のチャンクを読んで自分で区切る。
    """
    if stream is None:
        return
    buffer = ""
    while True:
        chunk = await stream.read(4096)
        if not chunk:
            break
        buffer += chunk.decode("utf-8", "replace")
        parts = re.split(r"[\r\n]", buffer)
        buffer = parts.pop()
        for part in parts:
            line = _strip_ansi(part).strip()
            if line:
                await on_line(line)
    line = _strip_ansi(buffer).strip()
    if line:
        await on_line(line)


async def _run(
    argv: list[str],
    cwd: Path,
    *,
    timeout: float | None = None,
    on_line: Callable[[str], Awaitable[None]] | None = None,
) -> tuple[int, str, str]:
    """``argv`` を ``cwd`` で実行し ``(returncode, stdout, stderr)`` を返す。

    ``on_line`` を渡すと標準出力の行を届いた順に呼ぶ（進捗の中継）。**中断
    （:class:`asyncio.CancelledError`）とタイムアウトでは必ず子プロセスを殺す**:
    ジョブのキャンセルは実行中のタスクを cancel するだけなので、ここで殺さないと
    レンダリングが裏で走り続ける。
    """
    try:
        process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(cwd),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise RemotionError(
            f"'{argv[0]}' コマンドが見つかりません。Node.js を入れて"
            " Remotion プロジェクトで `npm install` を済ませてください"
            "（通常は `run.sh` が自動で行います）"
        ) from exc
    except OSError as exc:
        raise RemotionError(f"'{argv[0]}' を起動できませんでした: {exc}") from exc

    out: list[str] = []
    err: list[str] = []

    async def keep_out(line: str) -> None:
        out.append(line)
        if on_line is not None:
            await on_line(line)

    async def keep_err(line: str) -> None:
        err.append(line)

    async def collect() -> int:
        await asyncio.gather(
            _pump(process.stdout, keep_out), _pump(process.stderr, keep_err)
        )
        return await process.wait()

    try:
        code = await asyncio.wait_for(collect(), timeout=timeout)
    except asyncio.TimeoutError as exc:
        await _kill(process)
        raise RemotionError(
            f"Remotion が {timeout:.0f} 秒以内に応答しませんでした（タイムアウト）"
        ) from exc
    except BaseException:
        # 中断（CancelledError）でも、進捗の中継が投げても、子は必ず片付ける。
        await _kill(process)
        raise
    return code, "\n".join(out), "\n".join(err)


async def _kill(process: asyncio.subprocess.Process) -> None:
    """子プロセスを確実に終わらせる（既に終わっていれば何もしない）。"""
    if process.returncode is not None:
        return
    with suppress(ProcessLookupError):
        process.kill()
    with suppress(Exception):  # パイプを閉じて子を回収する
        await process.wait()


def summarize_error(stdout: str, stderr: str) -> str:
    """CLI の出力から**ユーザーに見せる 1〜2 行**を抜く。

    Node のスタックトレースをそのまま外部 API の 400 本文に載せると読めないので、
    スタックのフレーム（``at foo (…)``）を落としたうえで、末尾に一番近い
    「``Error:`` らしい行」とその次の行だけを返す。それらしい行が無ければ末尾の
    数行に倒す。全文は :func:`_failure` がサーバーログへ流す。
    """
    text = stderr.strip() or stdout.strip()
    lines = [
        line
        for line in (_strip_ansi(raw).strip() for raw in text.splitlines())
        if line and not _STACK_LINE_RE.match(line)
    ]
    if not lines:
        return "(no output)"
    for index in range(len(lines) - 1, -1, -1):
        if _ERROR_LINE_RE.search(lines[index]):
            start = index
            break
    else:
        start = max(0, len(lines) - ERROR_LINES)
    detail = " / ".join(lines[start : start + ERROR_LINES])
    return detail[:ERROR_TAIL]


def _failure(action: str, code: int, stdout: str, stderr: str) -> RemotionError:
    log.error(
        "remotion %s failed (exit %s)\n--- stdout ---\n%s\n--- stderr ---\n%s",
        action, code, stdout.strip(), stderr.strip(),
    )
    detail = summarize_error(stdout, stderr)
    return RemotionError(f"Remotion の{action}に失敗しました (exit {code}): {detail}")


# --------------------------------------------------------------------------
# composition の一覧
# --------------------------------------------------------------------------

#: ``(プロジェクト, entry) -> (取得した時刻, ID の並び)``
_cache: dict[tuple[str, str], tuple[float, list[str]]] = {}


def clear_cache() -> None:
    """一覧のキャッシュを捨てる（設定を変えたときとテスト用）。"""
    _cache.clear()


def _row_id(line: str) -> str | None:
    """一覧の 1 行から composition の ID を読む（表の行でなければ None）。

    通すのは「先頭トークンが Remotion の ID 文法（:data:`_ID_RE`）に収まり、
    英数字を 1 文字以上含み（``-----`` のような罫線を捨てる）、見出し
    （``Composition``）ではなく、続きのトークンが**すべて表のセルらしい**
    （:data:`_TABLE_CELL_RE`）か、そもそも無い」行だけ。
    ``Bundling code 100%`` のようなログ行は 2 つ目（``code``）がセルに収まらない
    ので落ちる。
    """
    token, *rest = line.split()
    if token.lower() in _TABLE_HEADERS:
        return None
    if not _ID_RE.fullmatch(token) or not any(c.isalnum() for c in token):
        return None
    if not all(_TABLE_CELL_RE.fullmatch(word) for word in rest):
        return None
    return token


def parse_compositions(stdout: str) -> list[str]:
    """``remotion compositions`` の出力から ID を拾う。

    Remotion は ``The following compositions are available:`` の見出しの後ろに
    表（ID・FPS・寸法・尺）を出す。**見出しがあればその後ろだけ**を見るのが要点で、
    初回実行時に混ざるブラウザ取得のログ（``Downloading Chrome Headless Shell …``
    / ``Got Headless Shell``）や bundle の進捗は見出しより前なので丸ごと落ちる。

    見出しが無い版のために出力全体を舐める道も残すが、拾うのは
    :func:`_row_id` が通す行（1 行 1 ID か、表の行）だけにする。かつては
    「行の全トークンが ID 文法なら全部 ID」という ``--quiet`` 向けの救済も
    持っていたが、``Got Headless Shell`` をそのまま ID として拾ってしまったので
    やめた（``--quiet`` は :func:`list_compositions` からも外してある）。

    重複は先勝ちで落とす。
    """
    lines = [
        line
        for line in (_strip_ansi(raw).strip() for raw in stdout.splitlines())
        if line
    ]
    for index, line in enumerate(lines):
        if _HEADING_RE.search(line):
            lines = lines[index + 1 :]
            break

    ids: list[str] = []
    for line in lines:
        token = _row_id(line)
        if token is not None and token not in ids:
            ids.append(token)
    return ids


async def list_compositions(*, use_cache: bool = True) -> list[str]:
    """プロジェクトが持つ composition ID の並び（短時間キャッシュ）。"""
    directory = project_dir()
    entry = resolve_entry(directory)
    key = (str(directory), entry)
    cached = _cache.get(key)
    if use_cache and cached is not None and time.monotonic() - cached[0] < CACHE_TTL:
        return list(cached[1])

    code, stdout, stderr = await _run(
        # ``--quiet`` は付けない: 版によっては全 ID を空白区切りで 1 行に並べる
        # だけになり、初回のブラウザ取得ログ（``Got Headless Shell``）と見分けが
        # 付かなくなる。見出し + 表のまま読むほうが確実（:func:`parse_compositions`）。
        [NPX, "remotion", "compositions", entry],
        directory,
        timeout=LIST_TIMEOUT,
    )
    if code != 0:
        raise _failure("composition 一覧の取得", code, stdout, stderr)
    ids = parse_compositions(stdout)
    _cache[key] = (time.monotonic(), list(ids))
    return ids


# --------------------------------------------------------------------------
# レンダリング
# --------------------------------------------------------------------------

def parse_progress(line: str) -> float | None:
    """進捗行から 0..1 の割合を読む（読めなければ None）。

    版によって出方が違うので「フレームらしい語を含み、``a/b`` がある」行だけを
    拾う。``a > b`` や ``b == 0`` は進捗として使えないので捨てる。
    """
    lowered = line.lower()
    if not any(word in lowered for word in _PROGRESS_WORDS):
        return None
    matches = _FRACTION_RE.findall(line)
    if not matches:
        return None
    done, total = (int(value) for value in matches[-1])
    if total <= 0 or done > total:
        return None
    return done / total


async def render(
    job_id: str,
    composition: str,
    props: dict[str, Any],
    output_path: str | Path,
    *,
    on_progress: ProgressCallback | None = None,
) -> Path:
    """``composition`` を ``output_path`` に書き出し、そのパスを返す。

    ``on_progress(fraction, message)`` は標準出力の進捗行ごとに呼ばれる
    （``fraction`` は読めたときだけ 0..1、読めなければ None）。WS への配信は
    呼び出し元（:mod:`app.jobs`）の仕事にして、この層は ComfyUI 経路と同じく
    「実行して成果物を置く」ことだけをする。
    """
    directory = project_dir()
    entry = resolve_entry(directory)
    name = str(composition or "").strip()
    if not name:
        raise RemotionError("composition が指定されていません")

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    REMOTION_TMP_DIR.mkdir(parents=True, exist_ok=True)
    props_file = REMOTION_TMP_DIR / f"{job_id or 'render'}.props.json"
    props_file.write_text(
        json.dumps(props or {}, ensure_ascii=False), encoding="utf-8"
    )

    async def on_line(line: str) -> None:
        if on_progress is not None:
            await on_progress(parse_progress(line), line)

    try:
        code, stdout, stderr = await _run(
            [
                NPX,
                "remotion",
                "render",
                entry,
                name,
                f"--props={props_file}",
                f"--output={output}",
                "--overwrite",
            ],
            directory,
            on_line=on_line,
        )
    finally:
        with suppress(OSError):
            props_file.unlink()
    if code != 0:
        raise _failure("レンダリング", code, stdout, stderr)
    if not output.exists() or output.stat().st_size == 0:
        raise RemotionError(
            f"Remotion は成功しましたが出力ファイルがありません: {output}"
        )
    await remux_audio(output, props or {})
    return output


# --------------------------------------------------------------------------
# 音声の焼き直し（Remotion の 1 フレーム遅れの補正）
# --------------------------------------------------------------------------
#
# Remotion が書き出す mp4 は **音声が 2,048 サンプル（48kHz で 42.67ms ≒ 1 フレーム）
# 遅れる**（AAC のプライミングが実体として入り、edit list で相殺されない）。
# BAN!BAN!BAN! の `fx/remotion/tools/mux_audio.py` で実測して確かめた挙動で、
# 決めの効果を 1 フレーム単位で合わせている映像ではそのまま音ズレになる。
#
# そこで**映像はストリームコピーのまま、音声だけ元音源から焼き直す**。props の
# ``audio.src`` がローカルの置き場（``outputs/`` / ``library/`` / ``assets/``）に
# 解決できるときだけ働き、それ以外（外部 URL・音声なし）は何もしない。
# ``MusicVideo`` / ``FxOverlay`` のどちらも props の形は同じ（``audioSchema``）
# なので、composition で分ける必要はない。
#
# **失敗してもジョブは失敗させない**: 焼き直せなくても mp4 自体は出来ているので、
# 元のまま残してログに残すだけにする。

#: ffmpeg / ffprobe の呼び出し名（:mod:`app.jobs` と同じ流儀でテストが差し替える）
FFMPEG = "ffmpeg"
FFPROBE = "ffprobe"

#: 焼き直しに許す秒数（映像はコピーなので音声の長さぶんしか掛からない）
REMUX_TIMEOUT = 600.0

#: 焼き直す音声のビットレート（BAN の `encode()` と同じ）
AUDIO_BITRATE = "320k"

#: ``audio.src`` に許す配信 URL の接頭辞 -> 置き場
AUDIO_ROOTS: dict[str, Path] = {
    "/outputs/": OUTPUTS_DIR,
    "/library/": LIBRARY_DIR,
    "/assets/": ASSETS_DIR,
}


def local_audio_path(src: object) -> Path | None:
    """``audio.src`` をローカルのファイルに解決する（できなければ None）。

    受けるのは ``http://…/outputs/…`` のような配信 URL、``/outputs/…`` の
    パス、そして置き場の中の絶対パス。**置き場の外は受けない**（焼き直しは
    サーバー内のファイルにしか掛けない）。
    """
    text = str(src or "").strip()
    if not text:
        return None
    candidate: Path | None = None
    for prefix, directory in AUDIO_ROOTS.items():
        index = text.find(prefix)
        if index >= 0:
            candidate = directory / text[index + len(prefix):].split("?", 1)[0]
            break
    if candidate is None:
        if text.startswith(("http://", "https://", "data:")):
            return None
        candidate = Path(text)
    resolved = rebase_stored_path(candidate)
    try:
        resolved = resolved.resolve()
    except OSError:
        return None
    allowed = [directory.resolve() for directory in AUDIO_ROOTS.values()]
    if not any(root in resolved.parents for root in allowed):
        return None
    return resolved if resolved.is_file() else None


def audio_filters(
    volume: float, fade_out: float, duration: float | None
) -> list[str]:
    """``-af`` に渡すフィルタの並び（音量 → フェードアウト → 尺合わせ）。

    ``apad`` を最後に置いて ``-shortest`` と組ませるのは、音源が映像よりわずかに
    短いときに末尾のフレームが音無しにならないようにするため（BAN と同じ）。
    フェードアウトは映像の尺が分かるときだけ掛けられる。
    """
    filters: list[str] = []
    if volume != 1.0:
        filters.append(f"volume={volume:.4f}")
    if fade_out > 0:
        if duration and duration > 0:
            start = max(0.0, duration - fade_out)
            filters.append(f"afade=t=out:st={start:.3f}:d={fade_out:.3f}")
        else:
            log.info("映像の尺が読めないので audio.fadeOut は掛けません")
    filters.append("apad")
    return filters


def build_remux_command(
    video: str | Path,
    audio: str | Path,
    dest: str | Path,
    *,
    start_from: float = 0.0,
    volume: float = 1.0,
    fade_out: float = 0.0,
    duration: float | None = None,
) -> list[str]:
    """音声だけ焼き直す ffmpeg のコマンドライン（純関数。テストで固定できる）。

    映像は ``-c:v copy``（再エンコードしない）。``start_from`` は**音源側の入力の
    前**に ``-ss`` として置くので、その入力だけの頭出しになる。
    """
    argv = [FFMPEG, "-v", "error", "-y", "-i", str(video)]
    if start_from > 0:
        argv += ["-ss", f"{start_from:.3f}"]
    argv += [
        "-i", str(audio),
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-c:v", "copy",
    ]
    filters = audio_filters(volume, fade_out, duration)
    if filters:
        argv += ["-af", ",".join(filters)]
    argv += [
        "-c:a", "aac",
        "-b:a", AUDIO_BITRATE,
        "-shortest",
        "-movflags", "+faststart",
        str(dest),
    ]
    return argv


async def probe_duration(path: str | Path) -> float | None:
    """メディアの長さ（秒。読めなければ None）。"""
    try:
        process = await asyncio.create_subprocess_exec(
            FFPROBE, "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except (OSError, ValueError) as exc:
        log.info("ffprobe を実行できませんでした（%s）: %s", path, exc)
        return None
    stdout, _ = await process.communicate()
    try:
        seconds = float(stdout.decode("utf-8", "replace").strip())
    except ValueError:
        return None
    return seconds if seconds > 0 else None


async def remux_audio(output: Path, props: dict[str, Any]) -> bool:
    """出来上がった mp4 の音声を、props の音源から焼き直す（できたら True）。

    props に ``audio.src`` が無い / ローカルに解決できない / ffmpeg が無い、の
    どれでも静かに諦める（False）。**ジョブは失敗させない**。
    """
    audio = props.get("audio") if isinstance(props, dict) else None
    if not isinstance(audio, dict):
        return False
    source = local_audio_path(audio.get("src"))
    if source is None:
        return False
    try:
        volume = float(audio.get("volume", 1) or 0)
        start_from = max(0.0, float(audio.get("startFrom", 0) or 0))
        fade_out = max(0.0, float(audio.get("fadeOut", 0) or 0))
    except (TypeError, ValueError):
        log.warning("audio の値を読めないので音声の焼き直しをやめます: %s", audio)
        return False

    duration = await probe_duration(output) if fade_out > 0 else None
    temporary = output.with_suffix(output.suffix + ".remux.mp4")
    argv = build_remux_command(
        output, source, temporary,
        start_from=start_from, volume=volume, fade_out=fade_out, duration=duration,
    )
    try:
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(
            process.communicate(), timeout=REMUX_TIMEOUT
        )
    except asyncio.CancelledError:
        with suppress(OSError):
            temporary.unlink(missing_ok=True)
        raise
    except (OSError, ValueError, asyncio.TimeoutError) as exc:
        log.warning(
            "音声の焼き直しをやめました（元の mp4 のままにします）: %s", exc
        )
        with suppress(OSError):
            temporary.unlink(missing_ok=True)
        return False
    if process.returncode != 0 or not temporary.is_file() or not temporary.stat().st_size:
        log.warning(
            "ffmpeg が音声を焼き直せませんでした（元の mp4 のままにします）: %s",
            stderr.decode("utf-8", "replace").strip()[:400],
        )
        with suppress(OSError):
            temporary.unlink(missing_ok=True)
        return False
    try:
        os.replace(temporary, output)
    except OSError as exc:
        log.warning("焼き直した mp4 を差し替えられませんでした: %s", exc)
        with suppress(OSError):
            temporary.unlink(missing_ok=True)
        return False
    log.info("Remotion の音声を %s から焼き直しました: %s", source, output)
    return True
