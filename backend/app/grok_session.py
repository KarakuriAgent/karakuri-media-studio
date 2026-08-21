"""CLI セッションのホスト（ACP またはワンショット）を 1 ループのあいだ使い回す。

正本はこちらの DB。CLI 側のセッションは続き用のキャッシュで、削除・cwd 違い・
資格情報の消失で load / resume は失敗する。失敗は :class:`GrokSessionGone`。

どの CLI を回すかは設定 ``agent_cli``（:mod:`app.llm_cli` のアダプタ）。契約
（rules）の渡し方もアダプタが決める: grok は ``session/new`` の ``_meta.rules``、
claude / codex / cursor は作業ディレクトリへ ``CLAUDE.md`` / ``AGENTS.md`` を
書き出す。ワンショットは従来どおり呼び出し側が初回 ``-p`` に埋め込む
（:meth:`GrokSessionHost.wants_contract`）。``systemPromptOverride`` は使わない。
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import suppress
from pathlib import Path
from typing import Any

from . import grok
from .acp import (
    AcpAgentClient,
    AcpUnavailable,
    ActivityCallback,
    _Turn,
    config_option_params,
    initialize_params,
    log_config_option_error,
)
from .config import load_settings
from .grok import LLMError, configured_timeout, looks_like_auth_error
from .llm_cli import CliAdapter, active_adapter, command_for, model_for
from .paths import GROK_WORKDIR, resolve_workdir

log = logging.getLogger(__name__)

#: ``session/cancel`` を送ってからプロセス kill にエスカレートするまでの待ち。
CANCEL_GRACE = 3.0

#: テストが ``grok._exec`` を差し替えたときはワンショットのプロセスを握らない。
_REAL_ONESHOT_EXEC = grok._exec


class GrokSessionGone(Exception):
    """Grok 側のセッションが消えた（``session/load`` / ``--resume`` の失敗）。"""


class GrokTurnCancelled(Exception):
    """ユーザー操作で Grok ターンが中断された。"""


class _ConfigOptionRejected(Exception):
    """``session/set_config_option`` が拒否された（ターンは続ける）。"""


def _is_cancelled_reason(result: Any) -> bool:
    if not isinstance(result, dict):
        return False
    reason = str(result.get("stopReason") or "").strip().lower()
    return reason in {"cancelled", "canceled"}


def _parse_session_id(stdout: str) -> str:
    """``--output-format json`` の応答から ``sessionId`` だけ拾う。

    取れなければ空文字。アクション JSON（``{"action": …}``）をセッション包装と
    間違えないよう、``sessionId`` / ``session_id`` があるときだけ採用する。
    """
    text = (stdout or "").strip()
    if not text:
        return ""
    try:
        payload = json.loads(text)
    except ValueError:
        payload = None
        for candidate in grok.iter_json_objects(text):
            if isinstance(candidate, dict) and (
                candidate.get("sessionId") or candidate.get("session_id")
            ):
                payload = candidate
                break
    if not isinstance(payload, dict):
        return ""
    raw = payload.get("sessionId") or payload.get("session_id") or ""
    return str(raw).strip()


def _oneshot_answer(stdout: str) -> str:
    """json 包装があれば本文を、なければ stdout 全体を答えにする。"""
    text = (stdout or "").strip()
    if not text:
        return ""
    try:
        payload = json.loads(text)
    except ValueError:
        return text
    if not isinstance(payload, dict):
        return text
    if not (payload.get("sessionId") or payload.get("session_id")):
        return text
    for key in ("message", "result", "text", "content"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return text


class GrokSessionHost:
    """1 ユーザー発言の実行ループのあいだ使い回す Grok セッション。

    ACP オンならプロセスをホストの寿命のあいだ維持し、``session/load`` または
    ``session/new`` のあと ``session/prompt`` だけを重ねる。ACP オフ（テスト・
    フォールバック）は ``grok -p`` で、id があれば ``--resume`` する。
    """

    def __init__(
        self,
        workdir: str | Path,
        *,
        on_activity: ActivityCallback | None = None,
        timeout: float | None = None,
        use_acp: bool | None = None,
        extra_args: list[str] | None = None,
        allow_tools: bool = True,
        adapter: CliAdapter | None = None,
    ) -> None:
        settings = load_settings()
        #: どの CLI を回すか（設定 ``agent_cli``）
        self.adapter = adapter or active_adapter(settings)
        self.workdir = resolve_workdir(workdir, GROK_WORKDIR)
        self.timeout = timeout if timeout is not None else configured_timeout()
        self.on_activity = on_activity
        self.extra_args = (
            list(settings.agent_grok_args) if extra_args is None else list(extra_args)
        )
        #: ツール実行の許可要求に「許可」で答えるか（相談専用の会話は False）。
        self.allow_tools = allow_tools
        self.command = command_for(self.adapter, settings)
        #: ワンショット（``-p``）側のコマンド。ACP ブリッジと別のことがある
        self.oneshot_command = command_for(self.adapter, settings, oneshot=True)
        self.model = model_for(self.adapter, settings)
        self.use_acp = settings.agent_use_acp if use_acp is None else use_acp
        self.session_id = ""
        self.resumed = False
        self.rebuild = False
        self.primed = False
        self._rules = ""
        self._acp: AcpAgentClient | None = None
        self._turn: _Turn | None = None
        self._stderr_task = None
        self._started = False
        self._closed = False
        self._cancelled = False
        self._prompt_done: asyncio.Event | None = None
        self._oneshot_process: asyncio.subprocess.Process | None = None

    def wants_contract(self) -> bool:
        """このターンのプロンプトの頭に契約（システムプロンプト）を置くべきか。

        ACP なら契約は ``session/new`` の ``_meta.rules`` か、作業ディレクトリの
        ``CLAUDE.md`` / ``AGENTS.md`` として渡っているので、ふつうは不要。ただし
        読み込みが確実でない CLI（claude）だけは保険で埋める
        （:attr:`app.llm_cli.CliAdapter.rules_in_prompt`）。

        ワンショットはこれまでどおり「続きでないとき」だけ埋める。
        """
        if self.use_acp:
            return self.adapter.rules_in_prompt
        return not self.session_id

    # --------------------------------------------------------------- start
    async def start(
        self, rules: str, session_id: str | None, workdir: str | Path | None = None
    ) -> str:
        """CLI のセッションを開く。返るのは実際の session id（空もあり）。"""
        if workdir is not None:
            self.workdir = resolve_workdir(workdir, GROK_WORKDIR)
        self._rules = rules or ""
        wanted = (session_id or "").strip()
        if self.use_acp:
            try:
                return await self._acp_start(wanted)
            except AcpUnavailable as exc:
                log.warning(
                    "%s の ACP を利用できないためワンショット実行に切り替えます: %s",
                    self.adapter.label,
                    exc,
                )
                self.use_acp = False
                await self._acp_close()
        return await self._oneshot_start(wanted)

    async def _oneshot_start(self, session_id: str) -> str:
        self.session_id = session_id
        self._started = True
        return self.session_id

    async def _acp_start(self, session_id: str) -> str:
        # 契約をファイルで渡す CLI（claude / codex / cursor）は、プロセスが
        # 起き上がる前に置いておく必要がある。中身は毎回上書きでよい。
        self.adapter.write_rules(self.workdir, self._rules)
        client = AcpAgentClient(
            command=self.command,
            model=self.model,
            workdir=self.workdir,
            timeout=self.timeout,
            on_activity=self.on_activity,
            adapter=self.adapter,
        )
        process = await client._spawn()
        stderr_task = asyncio.create_task(client._drain(process.stderr))
        turn = _Turn(process)
        self._acp = client
        self._turn = turn
        self._stderr_task = stderr_task
        try:
            await self._acp_initialize(turn)
            if session_id:
                await self._acp_load(turn, session_id)
                self.session_id = session_id
            else:
                self.session_id = await self._acp_new(turn, self._rules)
            # 復帰（load）でも設定し直す: 戻った先のセッションが何のモデルで
            # 動いているかは分からないので、毎回こちらの指定で上書きする。
            await self._acp_configure(turn)
        except GrokSessionGone:
            await self._acp_close()
            raise
        except Exception:
            await self._acp_close()
            raise
        self._started = True
        return self.session_id

    async def _acp_initialize(self, turn: _Turn) -> None:
        req = turn.request("initialize", initialize_params(self.adapter))
        result = await self._acp_wait(turn, req, phase="init")
        if result is None:
            raise AcpUnavailable("grok ACP の initialize が失敗しました")

    async def _acp_configure(self, turn: _Turn) -> None:
        """モデルを ``session/set_config_option`` で渡す（cursor）。

        1 件ずつ送って応答を待つ（まとめて投げない）。拒否されても CLI の既定
        モデルで動けるので、警告だけ残してターンは続ける。
        """
        for config_id, value in self.adapter.acp_config_options(self.model):
            req = turn.request(
                "session/set_config_option",
                config_option_params(self.session_id, config_id, value),
            )
            try:
                await self._acp_wait(turn, req, phase="config")
            except _ConfigOptionRejected as exc:
                log_config_option_error(self.adapter, config_id, value, str(exc))

    async def _acp_new(self, turn: _Turn, rules: str) -> str:
        params: dict[str, Any] = {
            "cwd": str(self.workdir),
            "mcpServers": [],
        }
        # 契約の渡し方は CLI ごと: grok は _meta.rules、ほかは作業ディレクトリに
        # 置いた CLAUDE.md / AGENTS.md（:meth:`_acp_start` が先に書いている）。
        if rules and self.adapter.rules_mode == "meta":
            params["_meta"] = {"rules": rules}
        req = turn.request("session/new", params)
        result = await self._acp_wait(turn, req, phase="init")
        session_id = str((result or {}).get("sessionId") or "").strip()
        if not session_id:
            raise AcpUnavailable(
                f"{self.adapter.label} の ACP が sessionId を返しませんでした"
            )
        return session_id

    async def _acp_load(self, turn: _Turn, session_id: str) -> None:
        req = turn.request(
            "session/load",
            {"sessionId": session_id, "cwd": str(self.workdir)},
        )
        try:
            await self._acp_wait(turn, req, phase="load")
        except AcpUnavailable as exc:
            raise GrokSessionGone(str(exc)) from exc

    # --------------------------------------------------------------- prompt
    async def prompt(self, text: str) -> str:
        if self._cancelled:
            raise GrokTurnCancelled()
        if self._closed:
            raise LLMError("Grok セッションはすでに閉じています")
        if not self._started:
            raise LLMError("Grok セッションが start されていません")
        if self.use_acp and self._turn is not None and self._acp is not None:
            return await self._acp_prompt(text)
        return await self._oneshot_prompt(text)

    async def _acp_prompt(self, text: str) -> str:
        if self._cancelled:
            raise GrokTurnCancelled()
        assert self._turn is not None and self._acp is not None
        turn = self._turn
        turn.text_parts = []
        done = asyncio.Event()
        self._prompt_done = done
        try:
            req = turn.request(
                "session/prompt",
                {
                    "sessionId": self.session_id,
                    "prompt": [{"type": "text", "text": text}],
                },
            )
            try:
                result = await asyncio.wait_for(
                    self._acp_wait(turn, req, phase="prompt"), self.timeout
                )
            except asyncio.TimeoutError as exc:
                if self._cancelled:
                    raise GrokTurnCancelled() from exc
                raise LLMError(
                    f"grok ACP エージェントが {self.timeout:.0f} 秒以内に応答しませんでした"
                    "（タイムアウト）"
                ) from exc
            except LLMError:
                if self._cancelled:
                    raise GrokTurnCancelled() from None
                raise
            if self._cancelled or _is_cancelled_reason(result):
                raise GrokTurnCancelled()
            answer = "".join(turn.text_parts).strip()
            if not answer:
                stop = (
                    (result or {}).get("stopReason") if isinstance(result, dict) else None
                )
                raise LLMError(
                    f"{self.adapter.label} の ACP エージェントが空の応答を返しました"
                    f"（stopReason={stop or 'unknown'}）"
                )
            return answer
        finally:
            done.set()
            if self._prompt_done is done:
                self._prompt_done = None

    async def _oneshot_prompt(self, text: str) -> str:
        if self._cancelled:
            raise GrokTurnCancelled()
        extra = list(self.extra_args)
        # 続き（``--resume``）と json 包装はアダプタが対応している CLI だけ。
        resume = bool(self.session_id) and self.adapter.supports_resume
        want_json = not resume and self.adapter.supports_json_output
        try:
            code, out, err = await self._oneshot_exec(
                text, extra, resume=resume, output_json=want_json
            )
        except GrokTurnCancelled:
            raise
        except LLMError:
            if self._cancelled:
                raise GrokTurnCancelled() from None
            raise
        if self._cancelled:
            raise GrokTurnCancelled()
        if resume and code != 0:
            if looks_like_auth_error(err or out, self.adapter):
                raise LLMError(grok._failure_message(code, out, err, self.adapter))
            raise GrokSessionGone(
                grok._failure_message(code, out, err, self.adapter)
                if code
                else f"{self.adapter.label} の続き実行に失敗しました"
            )
        if code == 0 and out.strip():
            if want_json:
                found = _parse_session_id(out)
                if found:
                    self.session_id = found
                return _oneshot_answer(out)
            return out.strip()
        raise LLMError(
            f"{self.adapter.label} CLI が空の応答を返しました"
            if code == 0
            else grok._failure_message(code, out, err, self.adapter)
        )

    async def _oneshot_exec(
        self,
        text: str,
        extra: list[str],
        *,
        resume: bool,
        output_json: bool,
    ) -> tuple[int, str, str]:
        """ワンショット実行を、モデル / extra / json フラグを落としながら試す。"""

        def argv(model: bool, extras: bool, json_fmt: bool) -> list[str]:
            return self.adapter.oneshot_argv(
                text,
                self.oneshot_command,
                self.model if model else "",
                extra=extra if extras else (),
                output_json=json_fmt,
                resume_id=self.session_id if resume else "",
            )

        attempts: list[list[str]] = []
        if self.model:
            attempts.append(argv(True, True, output_json))
        attempts.append(argv(False, True, output_json))
        if extra:
            attempts.append(argv(False, False, output_json))
        if output_json:
            # json フラグが未知でも答えは取りたい（テストの FakeCli はプレーン）。
            if self.model:
                attempts.append(argv(True, True, False))
            attempts.append(argv(False, True, False))
            if extra:
                attempts.append(argv(False, False, False))

        last = (1, "", "")
        seen: set[tuple[str, ...]] = set()
        for cmd in attempts:
            key = tuple(cmd)
            if key in seen:
                continue
            seen.add(key)
            last = await self._run_oneshot(cmd)
            code, out, err = last
            if code == 0 and out.strip():
                return last
            if looks_like_auth_error(err or out, self.adapter):
                return last
            if resume and code != 0:
                # resume 失敗はフラグを落としても直らない。呼び出し側が組み直す。
                return last
        return last

    async def _run_oneshot(self, cmd: list[str]) -> tuple[int, str, str]:
        """ワンショットを走らせる。本物の ``_exec`` ならプロセスを握る。"""
        if grok._exec is not _REAL_ONESHOT_EXEC:
            return await grok._exec(cmd, self.workdir, self.timeout)
        return await self._oneshot_spawn(cmd)

    async def _oneshot_spawn(self, cmd: list[str]) -> tuple[int, str, str]:
        """``grok -p`` を spawn し、``cancel()`` で kill できるようにする。"""
        workdir = Path(self.workdir)
        try:
            workdir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise LLMError(
                f"grok 作業ディレクトリを作成できません: {workdir} ({exc})"
            ) from exc
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(workdir),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise LLMError(
                f"'{cmd[0]}' コマンドが見つかりません。Grok Build CLI をインストール"
                " (curl -fsSL https://x.ai/cli/install.sh | bash) してください"
            ) from exc
        except OSError as exc:
            raise LLMError(f"'{cmd[0]}' を起動できませんでした: {exc}") from exc

        self._oneshot_process = process
        try:
            if self._cancelled:
                await AcpAgentClient._terminate(process)
                raise GrokTurnCancelled()
            try:
                out, err = await asyncio.wait_for(
                    process.communicate(), timeout=self.timeout
                )
            except asyncio.TimeoutError as exc:
                with suppress(ProcessLookupError):
                    process.kill()
                with suppress(Exception):
                    await process.communicate()
                if self._cancelled:
                    raise GrokTurnCancelled() from exc
                raise LLMError(
                    f"grok CLI が {self.timeout:.0f} 秒以内に応答しませんでした（タイムアウト）"
                ) from exc
            if self._cancelled:
                raise GrokTurnCancelled()
            return (
                process.returncode or 0,
                out.decode("utf-8", "replace"),
                err.decode("utf-8", "replace"),
            )
        finally:
            if self._oneshot_process is process:
                self._oneshot_process = None

    # --------------------------------------------------------------- cancel
    async def cancel(self) -> None:
        """いま走っている Grok 呼び出しを中断する。

        ACP は ``session/cancel`` 通知を送り、数秒待っても prompt が終わらなければ
        プロセスを kill する。ワンショットは subprocess を kill する。
        """
        self._cancelled = True
        if self._closed:
            return
        try:
            await self._interrupt_running()
        except Exception:  # noqa: BLE001 - 停止処理の失敗で呼び出し側を壊さない
            log.debug("Grok ターンの中断に失敗しました", exc_info=True)

    async def _interrupt_running(self) -> None:
        done = self._prompt_done
        if (
            self.use_acp
            and self._turn is not None
            and done is not None
            and not done.is_set()
        ):
            try:
                self._turn.notify(
                    "session/cancel", {"sessionId": self.session_id}
                )
                stdin = self._turn.process.stdin
                if stdin is not None and not stdin.is_closing():
                    await stdin.drain()
            except Exception:  # noqa: BLE001
                log.debug("session/cancel を送れませんでした", exc_info=True)
            try:
                await asyncio.wait_for(done.wait(), timeout=CANCEL_GRACE)
                return
            except asyncio.TimeoutError:
                log.info(
                    "session/cancel しても prompt が終わらないためプロセスを止めます"
                )
            process = self._turn.process if self._turn is not None else None
            if process is not None:
                await AcpAgentClient._terminate(process)
            return
        process = self._oneshot_process
        if process is not None:
            await AcpAgentClient._terminate(process)

    # --------------------------------------------------------------- ACP I/O
    async def _acp_wait(
        self, turn: _Turn, request_id: int, *, phase: str
    ) -> dict[str, Any] | None:
        """1 リクエストの応答まで読む。通知 / 許可要求は途中で処理する。"""
        assert self._acp is not None
        while True:
            try:
                message = await turn.read()
            except Exception as exc:  # noqa: BLE001
                if phase in ("init", "load", "config"):
                    raise AcpUnavailable(
                        f"grok ACP の出力を読み取れませんでした: {exc}"
                    ) from exc
                raise LLMError(
                    f"grok ACP エージェントの出力を読み取れませんでした: {exc}"
                ) from exc
            if message is None:
                if phase in ("init", "load", "config"):
                    raise AcpUnavailable("grok ACP エージェントが応答せずに終了しました")
                raise LLMError("grok ACP エージェントが応答途中で終了しました")

            message_id = message.get("id")
            method = message.get("method")
            if method is not None:
                await self._acp_handle_server(turn, message)
                continue

            if "error" in message:
                detail = str(message["error"])[:500]
                if phase == "config" and message_id == request_id:
                    raise _ConfigOptionRejected(detail)
                if phase == "load" and message_id == request_id:
                    raise GrokSessionGone(f"grok ACP の session/load に失敗しました: {detail}")
                if phase == "init":
                    raise AcpUnavailable(f"grok ACP の初期化に失敗しました: {detail}")
                raise LLMError(f"grok ACP エージェントがエラーを返しました: {detail}")

            if message_id == request_id:
                result = message.get("result")
                return result if isinstance(result, dict) else {}

    async def _acp_handle_server(self, turn: _Turn, message: dict[str, Any]) -> None:
        assert self._acp is not None
        method = message.get("method")
        message_id = message.get("id")
        if method == "session/update":
            params = message.get("params") or {}
            update = params.get("update")
            if isinstance(update, dict):
                await self._acp._handle_update(update, turn)
        elif method == "session/request_permission":
            from .acp import _allow_option_id, _reject_option_id

            params = message.get("params") or {}
            options = params.get("options") or []
            option_id = (
                _allow_option_id(options)
                if self.allow_tools
                else _reject_option_id(options)
            )
            if option_id is None:
                turn.respond(message_id, {"outcome": {"outcome": "cancelled"}})
            else:
                turn.respond(
                    message_id,
                    {"outcome": {"outcome": "selected", "optionId": option_id}},
                )
        elif method and method.startswith("_"):
            pass
        elif message_id is not None:
            turn.respond_error(message_id, -32601, f"unsupported method: {method}")

    # --------------------------------------------------------------- close
    async def close(self) -> None:
        self._closed = True
        await self._acp_close()
        if self._acp is not None:
            with suppress(Exception):
                await self._acp._emit(None)

    async def _acp_close(self) -> None:
        stderr_task = self._stderr_task
        process = self._turn.process if self._turn is not None else None
        self._stderr_task = None
        self._turn = None
        if stderr_task is not None:
            stderr_task.cancel()
            with suppress(asyncio.CancelledError):
                await stderr_task
        if process is not None:
            await AcpAgentClient._terminate(process)


def open_host(
    workdir: str | Path,
    on_activity: Any = None,
    *,
    timeout: float | None = None,
    use_acp: bool | None = None,
    extra_args: list[str] | None = None,
    allow_tools: bool = True,
    adapter: CliAdapter | None = None,
) -> GrokSessionHost:
    """ACP / ワンショットを設定に従って選んだホストを返す。

    ``extra_args`` は ``grok -p`` に足す追加フラグ（``None`` = 設定の
    ``agent_grok_args``、``[]`` = 何も足さない）、``allow_tools`` が ``False`` の
    ホストは ACP のツール実行許可要求を拒否する（相談専用の会話）。
    """
    return GrokSessionHost(
        workdir,
        on_activity=on_activity,
        timeout=timeout,
        use_acp=use_acp,
        extra_args=extra_args,
        allow_tools=allow_tools,
        adapter=adapter,
    )


async def delete_remote_session(session_id: str, workdir: str | Path | None = None) -> None:
    """``grok sessions delete`` を best-effort で呼ぶ（失敗しても黙る）。

    続き用キャッシュの掃除。この形の掃除コマンドを持つのは grok だけなので、
    ほかの CLI では何もしない（残っても :class:`GrokSessionGone` で組み直す）。
    """
    sid = (session_id or "").strip()
    if not sid:
        return
    settings = load_settings()
    adapter = active_adapter(settings)
    if adapter.id != "grok":
        return
    command = command_for(adapter, settings, oneshot=True)
    cwd = resolve_workdir(workdir or settings.grok_workdir, GROK_WORKDIR)
    try:
        await grok._exec([command, "sessions", "delete", sid], cwd, 20.0)
    except Exception:  # noqa: BLE001 - 続き用キャッシュの掃除なので落とさない
        log.debug("grok sessions delete に失敗しました", exc_info=True)
