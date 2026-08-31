"""Remotion（React で組んだ動画）のレンダリング（SPEC §5.2）。

ComfyUI とまったく同じ考え方で、**外で構築したバックエンドをアプリが参照する**
だけの薄い層。Remotion プロジェクト（Node のリポジトリ）はこのリポジトリの外に
あり、設定 ``remotion_project_dir`` がその場所を指す（空 = 機能ごと無効）。ここが
やるのは 2 つだけ:

- :func:`list_compositions` — ``npx remotion compositions <entry>`` を叩いて、
  そのプロジェクトが持つ composition の ID を並べる（短時間キャッシュ）。
- :func:`render` — ``npx remotion render`` をサブプロセスで回し、標準出力の
  進捗を呼び出し元（:mod:`app.jobs`）へ流し、出来上がった mp4 のパスを返す。

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
import re
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from pathlib import Path
from typing import Any

from .config import load_settings
from .paths import REMOTION_TMP_DIR, rebase_stored_path

log = logging.getLogger(__name__)

#: composition 一覧の取得に許す秒数
LIST_TIMEOUT = 60.0
#: 一覧のキャッシュを持つ秒数（プロジェクトを書き換えた直後でもすぐ追いつける長さ）
CACHE_TTL = 60.0

#: エントリポイントの既定（``package.json`` の ``config.remotionEntry`` が優先）
DEFAULT_ENTRY = "src/index.ts"

#: Remotion を起動するコマンド（``npx remotion …`` を実行する）
NPX = "npx"

#: 失敗理由に載せる stderr の長さ（そのままユーザーに出る）
ERROR_TAIL = 800

#: 進捗の見出しとして無視できない行の目印。Remotion の出力は版によって
#: 「Rendered 120/300」「Rendering frames | ██ | 120/300」などまちまちなので、
#: 「フレームらしい語 + a/b」の形だけを緩く拾う。
_PROGRESS_WORDS = ("render", "frame", "encod", "stitch", "bundl")
_FRACTION_RE = re.compile(r"(\d+)\s*/\s*(\d+)")
#: ANSI エスケープ（進捗バーの色・カーソル移動）
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]")

#: 一覧の表形式（``--quiet`` が効かない版）で読み飛ばす見出し
_TABLE_HEADERS = frozenset({"composition", "id"})
#: composition の ID に Remotion が許す文字（``^[a-zA-Z0-9-]+$``）。数字始まり
#: （``4kIntro``）は通し、アンダースコアは Remotion 側が拒むので通さない。
_ID_RE = re.compile(r"[A-Za-z0-9-]+")
#: 一覧の表の 2 列目以降（フレーム数・FPS・``1920x1080``）らしいトークン
_NUMERIC_RE = re.compile(r"[\d.]+(x[\d.]+)?%?")

#: 進捗の中継先（``(0..1 の割合 or None, 出力行)``）
ProgressCallback = Callable[[float | None, str], Awaitable[None]]


class RemotionError(Exception):
    """Remotion の実行に失敗した（そのままユーザー向けの文言として出す）。"""


class RemotionNotConfigured(RemotionError):
    """``remotion_project_dir`` が空 = 機能が無効（呼び出し側は 400 にする）。"""


# --------------------------------------------------------------------------
# プロジェクトの場所とエントリポイント
# --------------------------------------------------------------------------

def project_dir() -> Path:
    """設定が指す Remotion プロジェクトのディレクトリ。

    空なら :class:`RemotionNotConfigured`、実在しなければ :class:`RemotionError`。
    記録されたパスは :func:`app.paths.rebase_stored_path` を通す（Docker の中と
    ホストでプレフィックスが違う構成でも同じ設定で動くように、ほかの作業
    ディレクトリと揃える）。
    """
    stored = (load_settings().remotion_project_dir or "").strip()
    if not stored:
        raise RemotionNotConfigured(
            "Remotion プロジェクトのパスが設定されていません"
            "（設定ページの「接続」で構築済みプロジェクトの場所を指定してください）"
        )
    directory = rebase_stored_path(Path(stored).expanduser())
    if not directory.is_dir():
        raise RemotionError(
            f"Remotion プロジェクトのディレクトリがありません: {directory}"
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


def _failure(action: str, code: int, stdout: str, stderr: str) -> RemotionError:
    detail = (stderr.strip() or stdout.strip() or "(no output)")[-ERROR_TAIL:]
    return RemotionError(f"Remotion の{action}に失敗しました (exit {code}): {detail}")


# --------------------------------------------------------------------------
# composition の一覧
# --------------------------------------------------------------------------

#: ``(プロジェクト, entry) -> (取得した時刻, ID の並び)``
_cache: dict[tuple[str, str], tuple[float, list[str]]] = {}


def clear_cache() -> None:
    """一覧のキャッシュを捨てる（設定を変えたときとテスト用）。"""
    _cache.clear()


def parse_compositions(stdout: str) -> list[str]:
    """``remotion compositions`` の出力から ID を拾う（寛容に）。

    ``--quiet`` なら 1 行 1 ID だが、効かない版は表（ID・フレーム数・FPS・
    寸法）を出す。そこで拾うのは

    - 先頭トークンが Remotion の ID 文法（:data:`_ID_RE`）に収まっていて、
      英数字を 1 文字以上含み（``-----`` のような罫線を捨てる）、
    - 見出し（``Composition``）ではなく、
    - 続きのトークンが**すべて数字らしい**（表の行）か、そもそも無い（``--quiet``）

    行だけ。``Bundling video 100%`` のようなログ行は 2 つ目が数字でないので
    落ちる。重複は先勝ちで落とす。
    """
    ids: list[str] = []
    for raw in stdout.splitlines():
        line = _strip_ansi(raw).strip()
        if not line:
            continue
        token, *rest = line.split()
        if token.lower() in _TABLE_HEADERS:
            continue
        if not _ID_RE.fullmatch(token) or not any(c.isalnum() for c in token):
            continue
        if rest and not all(_NUMERIC_RE.fullmatch(word) for word in rest):
            continue
        if token not in ids:
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
        [NPX, "remotion", "compositions", entry, "--quiet"],
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
    return output
