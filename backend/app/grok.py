"""LLM client used for prompt authoring (SPEC §4.1).

The default (and currently only) implementation shells out to the official
**Grok Build CLI** in headless mode::

    grok --model grok-4.5 -p "<prompt>"

Everything the app needs from an LLM is expressed by the tiny
:class:`LLMClient` interface so that the CLI can later be swapped for the
official xAI API (``XAI_API_KEY``) or a local model without touching the chat
router.  The CLI is a coding agent with file-system powers, so it is always
started inside the dedicated empty work directory (``runtime/grok-workdir``).

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

from .config import load_settings
from .models import HealthStatus
from .paths import GROK_WORKDIR

DEFAULT_TIMEOUT = 120.0
VERSION_TIMEOUT = 20.0

# Substrings that mean "the CLI runs but you are not signed in" (SPEC §4.1).
AUTH_MARKERS = (
    "not authenticated",
    "not logged in",
    "unauthenticated",
    "unauthorized",
    "authentication required",
    "please sign in",
    "please log in",
    "sign in to",
    "run `grok` to sign in",
    "no credentials",
    "invalid api key",
    "401",
)

RESULT_KEYS = ("image_prompt", "video_prompt", "notes")


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


def _normalize(payload: object) -> dict[str, str | None] | None:
    """Validate a parsed candidate as ``{image_prompt?, video_prompt?, notes?}``."""
    if not isinstance(payload, dict):
        return None
    result: dict[str, str | None] = {}
    for key in RESULT_KEYS:
        value = payload.get(key)
        if value is None:
            result[key] = None
        elif isinstance(value, str):
            result[key] = value.strip() or None
        else:  # a wrong type means this is not our result object
            return None
    # A question may legitimately contain some other JSON; only accept the
    # object when it actually carries a prompt.
    if not (result["image_prompt"] or result["video_prompt"]):
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


def extract_result(text: str) -> dict[str, str | None] | None:
    """Return ``{image_prompt, video_prompt, notes}`` from an answer, or None."""
    for parsed in iter_json_objects(text):
        result = _normalize(parsed)
        if result is not None:
            return result
    return None


# --------------------------------------------------------------------------
# process plumbing (the seam the tests monkeypatch)
# --------------------------------------------------------------------------

async def _exec(
    argv: list[str], cwd: str | Path, timeout: float
) -> tuple[int, str, str]:
    """Run ``argv`` and return ``(returncode, stdout, stderr)``."""
    workdir = Path(cwd)
    try:
        workdir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise LLMError(f"grok 作業ディレクトリを作成できません: {workdir} ({exc})") from exc
    try:
        process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(workdir),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise LLMError(
            f"'{argv[0]}' コマンドが見つかりません。Grok Build CLI をインストール"
            " (curl -fsSL https://x.ai/cli/install.sh | bash) してください"
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
            f"grok CLI が {timeout:.0f} 秒以内に応答しませんでした（タイムアウト）"
        ) from exc
    return (
        process.returncode or 0,
        out.decode("utf-8", "replace"),
        err.decode("utf-8", "replace"),
    )


def looks_like_auth_error(text: str) -> bool:
    lowered = (text or "").lower()
    return any(marker in lowered for marker in AUTH_MARKERS)


def _failure_message(returncode: int, stdout: str, stderr: str) -> str:
    detail = (stderr.strip() or stdout.strip() or "(no output)")[:500]
    if looks_like_auth_error(detail):
        return (
            "grok CLI が認証されていません。ターミナルで `grok` を実行して"
            f" サインインしてください: {detail}"
        )
    return f"grok CLI が失敗しました (exit {returncode}): {detail}"


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
    def __init__(
        self,
        command: str | None = None,
        model: str | None = None,
        workdir: str | Path | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        extra_args: list[str] | None = None,
    ) -> None:
        settings = load_settings()
        self.command = (command if command is not None else settings.grok_command) or "grok"
        self.model = (model if model is not None else settings.grok_model) or ""
        self.workdir = Path(
            workdir or settings.grok_workdir or GROK_WORKDIR
        )
        self.timeout = timeout
        # Tool-permission flags for agent mode (AGENT-MODE §3.4). The CLI is
        # beta, so the flags stay configurable instead of hard coded.
        self.extra_args = list(extra_args or [])

    async def complete(self, prompt: str) -> str:
        extra = self.extra_args
        attempts: list[list[str]] = []
        if self.model:
            attempts.append([self.command, "--model", self.model, *extra, "-p", prompt])
        attempts.append([self.command, *extra, "-p", prompt])
        if extra:
            # The CLI is beta: tool-permission flags unknown to an older CLI
            # must degrade to the plain (tool-less) run, not kill the turn.
            attempts.append([self.command, "-p", prompt])

        last_failure = ""
        for argv in attempts:
            code, out, err = await _exec(argv, self.workdir, self.timeout)
            if code == 0 and out.strip():
                return out.strip()
            last_failure = (
                "grok CLI が空の応答を返しました"
                if code == 0
                else _failure_message(code, out, err)
            )
            # The CLI is beta: an unknown --model flag must not be fatal, but a
            # genuine auth problem will not be fixed by dropping the flag.
            if looks_like_auth_error(err or out):
                break
        raise LLMError(last_failure or "grok CLI が失敗しました")

    async def health(self) -> HealthStatus:
        if not self.command:
            return HealthStatus(status="not_configured", detail="grok_command is empty")
        try:
            code, out, err = await _exec(
                [self.command, "--version"], self.workdir, VERSION_TIMEOUT
            )
        except LLMError as exc:
            return HealthStatus(status="error", detail=str(exc))
        if code != 0:
            return HealthStatus(status="error", detail=_failure_message(code, out, err))
        version = (out.strip() or err.strip() or "unknown").splitlines()[0][:200]
        return HealthStatus(
            status="ok",
            detail=f"{self.command} {version} (model={self.model or 'default'})",
        )


def get_client() -> LLMClient:
    """Factory: CLI by default; swap here for the API-key backed client."""
    return GrokCliClient()


def get_agent_client(workdir: str | Path) -> LLMClient:
    """Client for one agent session (AGENT-MODE §3.4 / §6).

    Runs inside the session work dir with the longer agent timeout and the
    configured tool-permission flags (empty by default -> same safe ``-p`` run
    as the chat flow).
    """
    settings = load_settings()
    return GrokCliClient(
        workdir=workdir,
        timeout=settings.agent_grok_timeout or DEFAULT_TIMEOUT,
        extra_args=settings.agent_grok_args,
    )


async def check_grok() -> HealthStatus:
    """/api/health "grok" section (SPEC §4.1)."""
    return await get_client().health()
