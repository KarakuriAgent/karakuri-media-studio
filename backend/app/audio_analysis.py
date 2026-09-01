"""音源解析（``mode: "audio_analysis"``、SPEC §5.2）のアプリ側。

歌詞つきの映像は「何秒に何を出すか」を決め打ちできない。歌詞のアライン
（1 文字ごとの秒）・実測の onset・ビート・無音区間を音源から出し、演出の秒は
その結果から算出する（BAN!BAN!BAN! の ``analysis/`` でやっていたこと）。

**重い依存はアプリの環境に入れない**のがこの層の眼目で、ComfyUI・Remotion と
同じ「外で構築したバックエンドを参照する」やり方をとる:

- 解析の本体は :mod:`app.audio_analysis_worker`（``app`` に依存しない単独の
  スクリプト）。torch / faster-whisper / stable-ts / librosa を import するのは
  そちらだけ
- ここは設定 ``audio_analysis_python``（解析用 venv の python。空ならアプリ自身の
  interpreter）でそのスクリプトをサブプロセス実行し、標準出力の進捗を呼び出し元
  （:mod:`app.jobs`）へ流す（:mod:`app.remotion` と同じ流儀）
- 依存が入っていなければワーカーは終了コード 3 で落ちる。それは**ジョブの失敗
  ではなく設定不足**なので、:class:`AudioAnalysisNotConfigured` にして 400 と
  「何を入れればよいか」を返す。ジョブ投入の時点でも :func:`check_dependencies`
  が同じ確認をするので、履歴に無駄な失敗ジョブは残らない

成果物は ``outputs/{job_id}/analysis.json`` 1 つだけ（画像も動画も作らない）。
"""

from __future__ import annotations

import asyncio
import logging
import re
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path

from .config import load_settings
from .paths import AUDIO_ANALYSIS_TMP_DIR

log = logging.getLogger(__name__)

#: ワーカー（このファイルの隣。``app`` を import しない単独スクリプト）
WORKER = Path(__file__).with_name("audio_analysis_worker.py")

#: 依存を確かめるだけの実行に許す秒数（import はせず find_spec で見るだけ）
CHECK_TIMEOUT = 60.0
#: 解析 1 回に許す秒数（CPU の small でも 5 分の曲が収まる長さ）
ANALYZE_TIMEOUT = 3 * 60 * 60.0

#: ワーカーが依存不足で返す終了コード（:mod:`app.audio_analysis_worker` と対）
EXIT_MISSING_DEPENDENCY = 3

#: 進捗の行（``PROGRESS 0.250 align を実行中``）
_PROGRESS_RE = re.compile(r"^PROGRESS\s+([\d.]+)\s*(.*)$")

#: 失敗理由に載せる文字数の上限（そのままユーザーに出る）
ERROR_TAIL = 400

#: 依存の入れ方（400 の本文に必ず添える）
INSTALL_HINT = (
    "解析用の venv を作って `pip install -r backend/requirements-optional.txt` を実行し、"
    "設定ページの「音源解析」でその venv の python を `audio_analysis_python` に"
    "指定してください（重い依存はアプリの環境には入れません）"
)

#: 進捗の中継先（``(0..1 の割合 or None, 出力行)``）
ProgressCallback = Callable[[float | None, str], Awaitable[None]]


class AudioAnalysisError(Exception):
    """解析に失敗した（そのままユーザー向けの文言として出す）。"""


class AudioAnalysisNotConfigured(AudioAnalysisError):
    """依存が入っていない = 設定不足（呼び出し側は 400 にする）。"""


def interpreter() -> str:
    """ワーカーを走らせる python（設定が空ならアプリ自身の interpreter）。"""
    configured = str(load_settings().audio_analysis_python or "").strip()
    return configured or sys.executable


def parse_progress(line: str) -> tuple[float | None, str]:
    """ワーカーの 1 行を ``(割合, 表示する文言)`` にする。"""
    matched = _PROGRESS_RE.match(line)
    if not matched:
        return None, line
    try:
        fraction = float(matched.group(1))
    except ValueError:
        return None, line
    return min(max(fraction, 0.0), 1.0), matched.group(2).strip() or line


async def _run(
    argv: list[str],
    *,
    timeout: float,
    on_line: Callable[[str], Awaitable[None]] | None = None,
) -> tuple[int, str, str]:
    """ワーカーを走らせて ``(returncode, stdout, stderr)``。

    :mod:`app.remotion` と同じく、**中断とタイムアウトでは必ず子を殺す**
    （ジョブのキャンセルがタスクの cancel だけなので、殺さないと GPU を掴んだ
    まま解析が走り続ける）。
    """
    try:
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise AudioAnalysisNotConfigured(
            f"音源解析の python が見つかりません: {argv[0]}"
            f"（設定 `audio_analysis_python` を確かめてください）"
        ) from exc
    except OSError as exc:
        raise AudioAnalysisError(f"'{argv[0]}' を起動できませんでした: {exc}") from exc

    out: list[str] = []
    err: list[str] = []

    async def pump(stream, sink: list[str], relay: bool) -> None:
        if stream is None:
            return
        while True:
            raw = await stream.readline()
            if not raw:
                break
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            sink.append(line)
            if relay and on_line is not None:
                await on_line(line)

    async def collect() -> int:
        await asyncio.gather(
            pump(process.stdout, out, True), pump(process.stderr, err, False)
        )
        return await process.wait()

    try:
        code = await asyncio.wait_for(collect(), timeout=timeout)
    except asyncio.TimeoutError as exc:
        await _kill(process)
        raise AudioAnalysisError(
            f"音源解析が {timeout:.0f} 秒以内に終わりませんでした（タイムアウト）"
        ) from exc
    except BaseException:
        await _kill(process)
        raise
    return code, "\n".join(out), "\n".join(err)


async def _kill(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    try:
        process.kill()
    except ProcessLookupError:
        pass
    try:
        await process.wait()
    except Exception:  # noqa: BLE001 - パイプを閉じて子を回収するだけ
        pass


def _detail(stdout: str, stderr: str) -> str:
    text = stderr.strip() or stdout.strip()
    lines = [line for line in text.splitlines() if line.strip()]
    return (lines[-1] if lines else "(no output)")[:ERROR_TAIL]


def _not_configured(detail: str) -> AudioAnalysisNotConfigured:
    return AudioAnalysisNotConfigured(f"音源解析の依存が足りません: {detail}。{INSTALL_HINT}")


async def check_dependencies(tasks: list[str], *, has_lyrics: bool = True) -> None:
    """``tasks`` に要る依存が入っているか（入っていなければ 400 の例外）。

    ワーカーの ``--check`` は import ではなく ``find_spec`` で見るだけなので、
    ジョブ投入のたびに呼んでも待たされない。
    """
    argv = [interpreter(), str(WORKER), "--check", "--tasks", ",".join(tasks)]
    if has_lyrics:
        # 歌詞ファイルは要らない（``--check`` は読まない）が、align を落とさせない
        argv += ["--lyrics", "-"]
    code, stdout, stderr = await _run(argv, timeout=CHECK_TIMEOUT)
    if code == EXIT_MISSING_DEPENDENCY:
        raise _not_configured(_detail(stdout, stderr))
    if code != 0:
        raise AudioAnalysisError(
            f"音源解析のワーカーを確認できませんでした (exit {code}): "
            f"{_detail(stdout, stderr)}"
        )


def write_lyrics(job_id: str, lyrics: str) -> Path:
    """歌詞を一時ファイルに書き出す（引数に埋めると改行で壊れるため）。"""
    AUDIO_ANALYSIS_TMP_DIR.mkdir(parents=True, exist_ok=True)
    path = AUDIO_ANALYSIS_TMP_DIR / f"{job_id or 'analysis'}.lyrics.txt"
    path.write_text(lyrics, encoding="utf-8")
    return path


def build_command(
    *,
    audio: Path,
    output: Path,
    tasks: list[str],
    lyrics_file: Path | None = None,
    stems: list[Path] | None = None,
    language: str = "ja",
    model: str = "small",
    substitutions: str = "",
) -> list[str]:
    """ワーカーのコマンドライン（純関数。テストで固定できる）。"""
    argv = [
        interpreter(), str(WORKER),
        "--audio", str(audio),
        "--out", str(output),
        "--tasks", ",".join(tasks),
        "--language", language,
        "--model", model,
    ]
    if lyrics_file is not None:
        argv += ["--lyrics", str(lyrics_file)]
    for stem in stems or []:
        argv += ["--stem", str(stem)]
    if substitutions:
        argv += ["--substitutions", substitutions]
    return argv


async def analyze(
    job_id: str,
    *,
    audio: Path,
    output: Path,
    tasks: list[str],
    lyrics: str = "",
    stems: list[Path] | None = None,
    language: str = "ja",
    model: str = "small",
    substitutions: str = "",
    on_progress: ProgressCallback | None = None,
) -> Path:
    """解析を 1 回走らせて ``analysis.json`` のパスを返す。

    ``on_progress(fraction, message)`` はワーカーの出力行ごとに呼ばれる
    （``fraction`` は ``PROGRESS`` 行のときだけ 0..1）。WS への配信は呼び出し元
    （:mod:`app.jobs`）の仕事にして、この層は「実行して成果物を置く」だけにする。
    """
    output.parent.mkdir(parents=True, exist_ok=True)
    lyrics_file = write_lyrics(job_id, lyrics) if lyrics.strip() else None

    async def on_line(line: str) -> None:
        if on_progress is not None:
            fraction, message = parse_progress(line)
            await on_progress(fraction, message)

    try:
        code, stdout, stderr = await _run(
            build_command(
                audio=audio, output=output, tasks=tasks, lyrics_file=lyrics_file,
                stems=stems, language=language, model=model,
                substitutions=substitutions,
            ),
            timeout=ANALYZE_TIMEOUT,
            on_line=on_line,
        )
    finally:
        if lyrics_file is not None:
            try:
                lyrics_file.unlink()
            except OSError:
                pass
    if code == EXIT_MISSING_DEPENDENCY:
        raise _not_configured(_detail(stdout, stderr))
    if code != 0:
        log.error(
            "audio analysis failed (exit %s)\n--- stdout ---\n%s\n--- stderr ---\n%s",
            code, stdout.strip(), stderr.strip(),
        )
        raise AudioAnalysisError(
            f"音源解析に失敗しました (exit {code}): {_detail(stdout, stderr)}"
        )
    if not output.is_file() or not output.stat().st_size:
        raise AudioAnalysisError(
            f"解析は成功しましたが出力ファイルがありません: {output}"
        )
    return output
