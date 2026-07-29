"""ACP (Agent Client Protocol) 経由で grok CLI を 1 ターン走らせるクライアント。

``grok --permission-mode auto -p "<prompt>"`` のワンショット実行は完了まで何の
進捗も出さないため、UI に「いま何をしているか」を出せない。grok CLI は
``grok agent stdio`` で **改行区切りの JSON-RPC**（ACP）を喋るので、こちらを使うと
実行中に ``session/update`` 通知が流れてくる。

1 ターンの流れ（grok 0.2.112 で実機確認済み）::

    initialize          → protocolVersion / clientCapabilities を申告
    session/new         → {"cwd": <workdir>, "mcpServers": []} → result.sessionId
    session/prompt      → {"sessionId":…, "prompt":[{"type":"text","text":…}]}
      ← session/update            実行中の活動（思考 / ツール / 応答）
      ← session/request_permission ツール実行の許可要求（自動許可する）
    session/prompt の応答 → ターン完了（result.stopReason）

最終応答テキストは ``agent_message_chunk`` の連結で得る。プロセスはターン毎に
spawn / 終了する（既存のステートレスなターン設計に合わせる）。

ACP を起動できない・initialize に失敗した場合は既存の
:class:`app.grok.GrokCliClient`（ワンショット）にフォールバックする。
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
from contextlib import suppress
from pathlib import Path
from typing import Any, Awaitable, Callable

from .config import load_settings
from .grok import DEFAULT_TIMEOUT, GrokCliClient, LLMClient, LLMError
from .models import HealthStatus
from .paths import GROK_WORKDIR

log = logging.getLogger(__name__)

#: 活動テキストのコールバック。``None`` は「活動なし（ターン終了）」。
ActivityCallback = Callable[[str | None], Awaitable[None] | None]

PROTOCOL_VERSION = 1

# 活動テキスト（UI にそのまま出る）
ACTIVITY_THINKING = "思考中"
ACTIVITY_ANSWERING = "応答を作成中"


def tool_activity(title: str) -> str:
    return f"ツール実行中: {title}"


class AcpUnavailable(Exception):
    """ACP を開始できなかった（起動 / initialize / session/new の失敗）。

    ワンショット実行へのフォールバック条件。ターン中の失敗には使わない。
    """


#: 1 回の read で受け取る最大バイト数（行の長さの上限ではない）。
READ_CHUNK = 65536


class _LineReader:
    """``StreamReader`` を行単位で読む。1 行の長さに上限を設けない。

    ``StreamReader.readline()`` は StreamReader の limit（``create_subprocess_exec``
    の既定は 64KiB）を超える行で ``LimitOverrunError`` を投げる。大きな
    ``session/update`` 通知でこれに当たるため、チャンク読み + 自前バッファで
    改行を探す方式にしている。
    """

    def __init__(self, stream: asyncio.StreamReader) -> None:
        self._stream = stream
        self._buffer = bytearray()
        self._eof = False

    async def readline(self) -> bytes:
        """次の 1 行（改行を含む）。EOF でバッファも空なら ``b""``。

        EOF 時にバッファが残っていれば、改行が無くても最終行として返す。
        """
        while True:
            index = self._buffer.find(b"\n")
            if index >= 0:
                line = bytes(self._buffer[: index + 1])
                del self._buffer[: index + 1]
                return line
            if self._eof:
                line = bytes(self._buffer)
                self._buffer.clear()
                return line
            chunk = await self._stream.read(READ_CHUNK)
            if not chunk:
                self._eof = True
                continue
            self._buffer.extend(chunk)


class _Turn:
    """1 ターン分の JSON-RPC 会話（プロセスは呼び出し側が管理する）。"""

    def __init__(self, process: asyncio.subprocess.Process) -> None:
        self.process = process
        self._next_id = 0
        self.text_parts: list[str] = []
        assert process.stdout is not None
        self._lines = _LineReader(process.stdout)

    # ------------------------------------------------------------- transport
    def _write(self, message: dict[str, Any]) -> None:
        stdin = self.process.stdin
        if stdin is None or stdin.is_closing():
            raise LLMError("grok ACP エージェントの標準入力が閉じています")
        stdin.write((json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8"))

    def request(self, method: str, params: dict[str, Any]) -> int:
        self._next_id += 1
        self._write(
            {"jsonrpc": "2.0", "id": self._next_id, "method": method, "params": params}
        )
        return self._next_id

    def respond(self, message_id: Any, result: dict[str, Any]) -> None:
        self._write({"jsonrpc": "2.0", "id": message_id, "result": result})

    def respond_error(self, message_id: Any, code: int, message: str) -> None:
        self._write(
            {
                "jsonrpc": "2.0",
                "id": message_id,
                "error": {"code": code, "message": message},
            }
        )

    async def read(self) -> dict[str, Any] | None:
        """次の JSON-RPC メッセージ。EOF なら ``None``。壊れた行は読み飛ばす。"""
        while True:
            line = await self._lines.readline()
            if not line:
                return None
            stripped = line.strip()
            if not stripped:
                continue
            try:
                message = json.loads(stripped)
            except ValueError:
                log.debug("grok ACP: JSON として読めない行を無視します: %r", stripped[:200])
                continue
            if isinstance(message, dict):
                return message


def _allow_option_id(options: list[dict[str, Any]]) -> str | None:
    """許可要求から「許可」に当たる選択肢の id（``--permission-mode auto`` 相当）。"""
    for option in options:
        if "allow" in str(option.get("kind", "")):
            return option.get("optionId")
    return options[0].get("optionId") if options else None


class AcpAgentClient(LLMClient):
    """ACP でエージェントを 1 ターン実行するクライアント。

    :param on_activity: 実行中の活動が変わるたびに呼ばれる。同じ内容では呼ばない。
    """

    def __init__(
        self,
        command: str | None = None,
        model: str | None = None,
        workdir: str | Path | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        on_activity: ActivityCallback | None = None,
        fallback: LLMClient | None = None,
    ) -> None:
        settings = load_settings()
        self.command = (command if command is not None else settings.grok_command) or "grok"
        self.model = (model if model is not None else settings.grok_model) or ""
        self.workdir = Path(workdir or settings.grok_workdir or GROK_WORKDIR)
        self.timeout = timeout
        self.on_activity = on_activity
        self._fallback = fallback
        self._activity: str | None = None

    # ------------------------------------------------------------- plumbing
    def argv(self) -> list[str]:
        argv = [self.command, "agent"]
        if self.model:
            argv += ["-m", self.model]
        argv.append("stdio")
        return argv

    def fallback_client(self) -> LLMClient:
        """ACP が使えないときのワンショット実行クライアント。"""
        if self._fallback is None:
            settings = load_settings()
            self._fallback = GrokCliClient(
                command=self.command,
                model=self.model,
                workdir=self.workdir,
                timeout=self.timeout,
                extra_args=settings.agent_grok_args,
            )
        return self._fallback

    async def _emit(self, activity: str | None) -> None:
        """活動を通知する（状態が変わったときだけ）。コールバックの例外は握る。"""
        if activity == self._activity:
            return
        self._activity = activity
        if self.on_activity is None:
            return
        try:
            result = self.on_activity(activity)
            if inspect.isawaitable(result):
                await result
        except Exception:  # noqa: BLE001 - 通知の失敗でターンを壊さない
            log.debug("grok ACP: activity コールバックが失敗しました", exc_info=True)

    async def _spawn(self) -> asyncio.subprocess.Process:
        try:
            self.workdir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise LLMError(
                f"grok 作業ディレクトリを作成できません: {self.workdir} ({exc})"
            ) from exc
        try:
            return await asyncio.create_subprocess_exec(
                *self.argv(),
                cwd=str(self.workdir),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except (OSError, ValueError) as exc:  # FileNotFoundError を含む
            raise AcpUnavailable(f"'{self.command} agent stdio' を起動できません: {exc}") from exc

    @staticmethod
    async def _terminate(process: asyncio.subprocess.Process) -> None:
        """プロセスを確実に終わらせる（タイムアウト・キャンセル時も通る）。"""
        if process.stdin is not None:
            with suppress(Exception):
                process.stdin.close()
        if process.returncode is None:
            with suppress(ProcessLookupError):
                process.kill()
        with suppress(Exception):
            await process.wait()

    @staticmethod
    async def _drain(stream: asyncio.StreamReader | None) -> None:
        """stderr をログへ流す（溜まってパイプが詰まるのを防ぐ）。"""
        if stream is None:
            return
        lines = _LineReader(stream)
        while True:
            line = await lines.readline()
            if not line:
                return
            log.debug("grok ACP stderr: %s", line.decode("utf-8", "replace").rstrip())

    # ------------------------------------------------------------- protocol
    async def _handle_update(self, update: dict[str, Any], turn: _Turn) -> None:
        kind = update.get("sessionUpdate")
        if kind == "agent_thought_chunk":
            await self._emit(ACTIVITY_THINKING)
        elif kind == "tool_call":
            title = str(update.get("title") or "ツール")
            await self._emit(tool_activity(title))
        elif kind == "agent_message_chunk":
            await self._emit(ACTIVITY_ANSWERING)
            content = update.get("content")
            if isinstance(content, dict) and isinstance(content.get("text"), str):
                turn.text_parts.append(content["text"])
        # tool_call_update / plan / available_commands_update は表示に使わない

    async def _converse(self, turn: _Turn, prompt: str) -> str:
        init_id = turn.request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "clientCapabilities": {
                    "fs": {"readTextFile": False, "writeTextFile": False}
                },
            },
        )
        new_id: int | None = None
        prompt_id: int | None = None
        stop_reason: str | None = None

        while True:
            try:
                message = await turn.read()
            except Exception as exc:  # noqa: BLE001 - 読み取り失敗で 500 にしない
                # プロンプト送信前なら「ACP が使えなかった」扱いにしてワンショット
                # 実行へ回せる。送信後はターンが進んでいるので通常のエラーにする。
                if prompt_id is None:
                    raise AcpUnavailable(
                        f"grok ACP の出力を読み取れませんでした: {exc}"
                    ) from exc
                raise LLMError(
                    f"grok ACP エージェントの出力を読み取れませんでした: {exc}"
                ) from exc
            if message is None:
                if prompt_id is None:
                    raise AcpUnavailable("grok ACP エージェントが応答せずに終了しました")
                raise LLMError("grok ACP エージェントが応答途中で終了しました")

            message_id = message.get("id")
            method = message.get("method")

            # --- サーバー → クライアントの通知 / リクエスト
            if method is not None:
                if method == "session/update":
                    params = message.get("params") or {}
                    update = params.get("update")
                    if isinstance(update, dict):
                        await self._handle_update(update, turn)
                elif method == "session/request_permission":
                    params = message.get("params") or {}
                    options = params.get("options") or []
                    option_id = _allow_option_id(options)
                    if option_id is None:
                        turn.respond(message_id, {"outcome": {"outcome": "cancelled"}})
                    else:
                        turn.respond(
                            message_id,
                            {"outcome": {"outcome": "selected", "optionId": option_id}},
                        )
                elif method.startswith("_"):
                    pass  # ベンダー拡張通知（_x.ai/…）は無視する
                elif message_id is not None:
                    # 未対応のリクエストは即エラーで返す（放置すると相手が止まる）
                    turn.respond_error(message_id, -32601, f"unsupported method: {method}")
                continue

            # --- こちらのリクエストへの応答
            if "error" in message:
                detail = str(message["error"])[:500]
                if message_id in (init_id, new_id):
                    raise AcpUnavailable(f"grok ACP の初期化に失敗しました: {detail}")
                raise LLMError(f"grok ACP エージェントがエラーを返しました: {detail}")

            result = message.get("result")
            if message_id == init_id:
                new_id = turn.request(
                    "session/new", {"cwd": str(self.workdir), "mcpServers": []}
                )
            elif message_id == new_id:
                session_id = (result or {}).get("sessionId")
                if not session_id:
                    raise AcpUnavailable("grok ACP が sessionId を返しませんでした")
                prompt_id = turn.request(
                    "session/prompt",
                    {
                        "sessionId": session_id,
                        "prompt": [{"type": "text", "text": prompt}],
                    },
                )
            elif message_id == prompt_id:
                stop_reason = (result or {}).get("stopReason")
                break

        answer = "".join(turn.text_parts).strip()
        if not answer:
            raise LLMError(
                "grok ACP エージェントが空の応答を返しました"
                f"（stopReason={stop_reason or 'unknown'}）"
            )
        return answer

    # ---------------------------------------------------------------- public
    async def complete(self, prompt: str) -> str:
        try:
            return await self._run(prompt)
        except AcpUnavailable as exc:
            log.warning("grok ACP を利用できないためワンショット実行に切り替えます: %s", exc)
            return await self.fallback_client().complete(prompt)
        finally:
            await self._emit(None)

    async def _run(self, prompt: str) -> str:
        process = await self._spawn()
        stderr_task = asyncio.create_task(self._drain(process.stderr))
        turn = _Turn(process)
        try:
            return await asyncio.wait_for(self._converse(turn, prompt), self.timeout)
        except asyncio.TimeoutError as exc:
            raise LLMError(
                f"grok ACP エージェントが {self.timeout:.0f} 秒以内に応答しませんでした"
                "（タイムアウト）"
            ) from exc
        finally:
            stderr_task.cancel()
            with suppress(asyncio.CancelledError):
                await stderr_task
            await self._terminate(process)

    async def health(self) -> HealthStatus:
        # 健全性は従来どおり `grok --version` で見る（ACP 固有の確認はしない）。
        return await self.fallback_client().health()
