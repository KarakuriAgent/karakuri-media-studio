"""LLM client used for prompt authoring (SPEC §4.1).

The implementation shells out to a **coding CLI** in headless mode::

    grok --model grok-4.5 -p "<prompt>"

どの CLI を回すかは設定 ``agent_cli``（grok / claude / codex / cursor）で選び、
コマンドの組み立てと認証エラーの見分けは :mod:`app.llm_cli` のアダプタが持つ。
Everything the app needs from an LLM is expressed by the tiny
:class:`LLMClient` interface so that the CLI can later be swapped for the
official xAI API (``XAI_API_KEY``) or a local model without touching the chat
router.  The CLI is a coding agent with file-system powers, so it is always
started inside a dedicated empty work directory.

The CLI is beta and its flag surface is not stable: when a call with
``--model`` fails we retry **once** without it before giving up.
"""

from __future__ import annotations

import asyncio
import json
import re
from abc import ABC, abstractmethod
from contextlib import suppress
from pathlib import Path
from typing import Any, Callable

from .config import load_settings
from .llm_cli import (
    COMMON_AUTH_MARKERS,
    CliAdapter,
    active_adapter,
    command_for,
    install_hint_for,
    label_for,
    model_for,
)
from .models import HealthStatus
from .paths import GROK_WORKDIR, resolve_workdir

DEFAULT_TIMEOUT = 120.0
VERSION_TIMEOUT = 20.0

# Substrings that mean "the CLI runs but you are not signed in" (SPEC §4.1).
# CLI 固有のものは :mod:`app.llm_cli` のアダプタが足す。
AUTH_MARKERS = COMMON_AUTH_MARKERS

RESULT_KEYS = ("image_prompt", "video_prompt", "notes")
#: mode 'audio' のセッションが返す追加キー（すべて文字列）。
AUDIO_RESULT_KEYS = ("audio_prompt", "lyrics", "negative_tags")


class LLMError(Exception):
    """Any failure while talking to the LLM (missing CLI, auth, timeout…)."""


# --------------------------------------------------------------------------
# JSON extraction (SPEC §4.1 "正規表現で最初の JSON ブロックを抽出してパース")
# --------------------------------------------------------------------------

_FENCE_JSON_RE = re.compile(r"```json\s*(.*?)```", re.DOTALL | re.IGNORECASE)
_FENCE_ANY_RE = re.compile(r"```[a-zA-Z0-9_-]*\s*(.*?)```", re.DOTALL)


def has_json_fence(text: str) -> bool:
    """True when the answer looks like it *tried* to deliver the final JSON."""
    return bool(_FENCE_JSON_RE.search(text or "")) or "```" in (text or "")


def _brace_blocks(text: str):
    """Yield every balanced ``{...}`` block, outermost first, left to right."""
    depth = 0
    start = -1
    in_string = False
    escaped = False
    for i, ch in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth:
                depth -= 1
                if depth == 0 and start >= 0:
                    yield text[start : i + 1]


def _candidates(text: str):
    for match in _FENCE_JSON_RE.finditer(text):
        yield match.group(1)
    for match in _FENCE_ANY_RE.finditer(text):
        yield match.group(1)
    yield from _brace_blocks(text)


def _normalize(payload: object) -> dict[str, object] | None:
    """Validate a parsed candidate as the final proposal object.

    Image / video sessions answer with ``{image_prompt, video_prompt, notes}``
    and audio ones with ``{audio_prompt, lyrics, …}``; both shapes go through
    here, and an object carrying none of the three prompts is not ours.
    """
    if not isinstance(payload, dict):
        return None
    result: dict[str, object] = {}
    for key in (*RESULT_KEYS, *AUDIO_RESULT_KEYS):
        value = payload.get(key)
        if value is None:
            result[key] = None
        elif isinstance(value, str):
            result[key] = value.strip() or None
        else:  # a wrong type means this is not our result object
            return None
    # A question may legitimately contain some other JSON; only accept the
    # object when it actually carries a prompt.
    if not (result["image_prompt"] or result["video_prompt"] or result["audio_prompt"]):
        return None
    return result


def iter_json_objects(text: str):
    """Yield every JSON value found in ``text``, best candidate first.

    ```json fences win, then any other fence, then the balanced ``{…}`` blocks.
    Shared by the chat result parser and the agent action protocol
    (AGENT-MODE §4).
    """
    for candidate in _candidates(text or ""):
        candidate = candidate.strip()
        if not candidate.startswith("{"):
            # a fence may wrap prose; fall back to the braces inside it
            inner = next(_brace_blocks(candidate), None)
            if inner is None:
                continue
            candidate = inner
        try:
            yield json.loads(candidate)
        except ValueError:
            continue


def extract_result(text: str) -> dict[str, object] | None:
    """Return the final proposal object of an answer, or None.

    Image / video sessions yield ``{image_prompt, video_prompt, notes}``; audio
    ones additionally fill ``audio_prompt`` / ``lyrics`` / ``negative_tags``
    (see :class:`app.models.PromptResult`).
    """
    for parsed in iter_json_objects(text):
        result = _normalize(parsed)
        if result is not None:
            return result
    return None


# --------------------------------------------------------------------------
# process plumbing (the seam the tests monkeypatch)
# --------------------------------------------------------------------------

async def _exec(
    argv: list[str],
    cwd: str | Path,
    timeout: float | None,
    env: dict[str, str] | None = None,
) -> tuple[int, str, str]:
    """Run ``argv`` and return ``(returncode, stdout, stderr)``.

    ``env`` replaces the inherited environment when given.  Media generation
    (:mod:`app.grok_media`) uses it to drop ``XAI_API_KEY`` so that the CLI can
    never silently fall back to the metered API (SPEC §4.1).

    ``timeout=None`` は「待ち続ける」（設定で 0 = タイムアウトなし にしたとき）。
    """
    workdir = Path(cwd)
    try:
        workdir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise LLMError(f"CLI の作業ディレクトリを作成できません: {workdir} ({exc})") from exc
    try:
        process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(workdir),
            env=env,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        hint = install_hint_for(argv[0])
        raise LLMError(
            f"'{argv[0]}' コマンドが見つかりません。"
            + (hint or "設定ページでコマンド名を確認してください")
        ) from exc
    except OSError as exc:
        raise LLMError(f"'{argv[0]}' を起動できませんでした: {exc}") from exc

    try:
        out, err = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except asyncio.TimeoutError as exc:
        with suppress(ProcessLookupError):
            process.kill()
        with suppress(Exception):  # reap the child so its pipes are closed
            await process.communicate()
        raise LLMError(
            f"{label_for(argv[0])} CLI が {timeout:.0f} 秒以内に応答しませんでした"
            "（タイムアウト）"
        ) from exc
    return (
        process.returncode or 0,
        out.decode("utf-8", "replace"),
        err.decode("utf-8", "replace"),
    )


def looks_like_auth_error(text: str, adapter: CliAdapter | None = None) -> bool:
    """「動いてはいるがサインインしていない」出力か（既定は選択中の CLI）。"""
    return (adapter or active_adapter()).looks_like_auth_error(text)


def _failure_message(
    returncode: int, stdout: str, stderr: str, adapter: CliAdapter | None = None
) -> str:
    cli = adapter or active_adapter()
    detail = (stderr.strip() or stdout.strip() or "(no output)")[:500]
    if looks_like_auth_error(detail, cli):
        return (
            f"{cli.label} CLI が認証されていません。ターミナルで"
            f" `{cli.oneshot_command}` を実行してサインインしてください: {detail}"
        )
    return f"{cli.label} CLI が失敗しました (exit {returncode}): {detail}"


# --------------------------------------------------------------------------
# client interface + CLI implementation
# --------------------------------------------------------------------------

class LLMClient(ABC):
    """Minimal LLM abstraction (SPEC §4.1 fallback requirement)."""

    @abstractmethod
    async def complete(self, prompt: str) -> str:
        """One stateless completion: prompt in, raw answer text out."""

    @abstractmethod
    async def health(self) -> HealthStatus:
        """Is the backend usable right now?"""


class GrokCliClient(LLMClient):
    """選ばれている CLI を 1 発だけ回すクライアント（``grok -p …`` 相当）。

    名前は歴史的なもので、実際に叩くコマンドは設定 ``agent_cli`` のアダプタが
    決める（:mod:`app.llm_cli`）。
    """

    def __init__(
        self,
        command: str | None = None,
        model: str | None = None,
        workdir: str | Path | None = None,
        timeout: float | None = DEFAULT_TIMEOUT,
        extra_args: list[str] | None = None,
        adapter: CliAdapter | None = None,
    ) -> None:
        settings = load_settings()
        self.adapter = adapter or active_adapter(settings)
        self.command = (
            command
            if command is not None
            else command_for(self.adapter, settings, oneshot=True)
        ) or self.adapter.oneshot_command
        self.model = (
            model if model is not None else model_for(self.adapter, settings)
        ) or ""
        # 設定に入っているのは保存した時点の絶対パスなので、いまの ROOT の下へ
        # 載せ替えてから使う（Docker 内ではホスト側のパスは作れない）。
        self.workdir = resolve_workdir(
            workdir or settings.grok_workdir, GROK_WORKDIR
        )
        self.timeout = timeout
        # Tool-permission flags for agent mode (AGENT-MODE §3.4). The CLI is
        # beta, so the flags stay configurable instead of hard coded.
        self.extra_args = list(extra_args or [])

    def _attempts(self, prompt: str) -> list[list[str]]:
        """モデル / 追加フラグを落としながら試す argv の並び。

        CLI はどれもフラグ面が安定していないので、知らないフラグで落ちても
        素の実行まで降りて答えを取りに行く。
        """
        extra = self.extra_args
        attempts: list[list[str]] = []

        def argv(model: bool, extras: bool) -> list[str]:
            return self.adapter.oneshot_argv(
                prompt,
                self.command,
                self.model if model else "",
                extra=extra if extras else (),
            )

        if self.model:
            attempts.append(argv(True, True))
        attempts.append(argv(False, True))
        if extra:
            # tool-permission flags unknown to an older CLI must degrade to the
            # plain (tool-less) run, not kill the turn.
            attempts.append(argv(False, False))
        return attempts

    async def complete(self, prompt: str) -> str:
        last_failure = ""
        seen: set[tuple[str, ...]] = set()
        for argv in self._attempts(prompt):
            key = tuple(argv)
            if key in seen:
                continue
            seen.add(key)
            code, out, err = await _exec(argv, self.workdir, self.timeout)
            if code == 0 and out.strip():
                return out.strip()
            last_failure = (
                f"{self.adapter.label} CLI が空の応答を返しました"
                if code == 0
                else _failure_message(code, out, err, self.adapter)
            )
            # An unknown --model flag must not be fatal, but a genuine auth
            # problem will not be fixed by dropping the flag.
            if looks_like_auth_error(err or out, self.adapter):
                break
        raise LLMError(last_failure or f"{self.adapter.label} CLI が失敗しました")

    async def health(self) -> HealthStatus:
        if not self.command:
            return HealthStatus(
                status="not_configured",
                detail=f"{self.adapter.label} のコマンドが未設定です",
            )
        try:
            code, out, err = await _exec(
                self.adapter.version_argv(self.command), self.workdir, VERSION_TIMEOUT
            )
        except LLMError as exc:
            return HealthStatus(status="error", detail=str(exc))
        if code != 0:
            return HealthStatus(
                status="error", detail=_failure_message(code, out, err, self.adapter)
            )
        version = (out.strip() or err.strip() or "unknown").splitlines()[0][:200]
        return HealthStatus(
            status="ok",
            detail=f"{self.command} {version} (model={self.model or 'default'})",
        )


def configured_timeout() -> float | None:
    """設定 ``agent_grok_timeout`` の制限時間。**0 = タイムアウトなし**（``None``）。

    ``None`` はそのまま :func:`asyncio.wait_for` に渡せる（待ち続ける）。
    """
    timeout = load_settings().agent_grok_timeout
    return timeout if timeout > 0 else None


def get_client(timeout: float | None = DEFAULT_TIMEOUT) -> LLMClient:
    """Factory: 設定 ``agent_cli`` の CLI をワンショットで回すクライアント。"""
    return GrokCliClient(timeout=timeout)


def get_agent_client(
    workdir: str | Path,
    on_activity: "Callable[[str | None], Any] | None" = None,
) -> LLMClient:
    """Client for one agent session (AGENT-MODE §3.4 / §6).

    Runs inside the session work dir with the longer agent timeout and the
    configured tool-permission flags (empty by default -> same safe ``-p`` run
    as the chat flow).

    ``agent_use_acp``（既定 True）のときは ``grok agent stdio``（ACP）で回し、
    実行中の活動を ``on_activity`` に流す。ACP を開始できなければ内部で従来の
    ワンショット実行へフォールバックする。

    制限時間は設定 ``agent_grok_timeout``（0 = タイムアウトなし）。
    """
    settings = load_settings()
    timeout = configured_timeout()
    oneshot = GrokCliClient(
        workdir=workdir,
        timeout=timeout,
        extra_args=settings.agent_grok_args,
    )
    if not settings.agent_use_acp:
        return oneshot
    from .acp import AcpAgentClient  # 循環インポートを避けるため遅延 import

    return AcpAgentClient(
        workdir=workdir,
        timeout=timeout,
        on_activity=on_activity,
        fallback=oneshot,
    )


async def check_grok() -> HealthStatus:
    """/api/health の CLI 欄（SPEC §4.1）。選ばれている CLI を見る。"""
    return await get_client().health()
