"""どのコーディング CLI を回すか（SPEC §4.1）。

チャット・エージェント・スタジオ会話・キャンバス・英訳・自動タグ・ヘルスチェック
まで、LLM を使う経路はすべてここで選ばれた 1 つの CLI を通る。設定は
``agent_cli``（既定 ``"grok"``）。

**例外: Grok Imagine（:mod:`app.grok_media`）は常に Grok。** 画像生成は Grok CLI
内蔵のツールに乗っているので、ここの選択とは無関係に ``grok_command`` を使う。

CLI ごとに違うのは次の 5 点だけなので、:class:`CliAdapter` にまとめている:

======== ============================ ========================= ==============
CLI      ACP 起動                     ワンショット              契約（rules）
======== ============================ ========================= ==============
grok     ``grok agent [-m M] stdio``  ``grok [-p] …``           ``_meta.rules``
claude   ``claude-agent-acp``         ``claude -p …``           ``CLAUDE.md``
codex    ``codex-acp``                ``codex exec …``          ``AGENTS.md``
cursor   ``cursor-agent --acp``       ``cursor-agent -p …``     ``AGENTS.md``
======== ============================ ========================= ==============

契約をファイルで渡す CLI では、ホストを開くたびに作業ディレクトリへその
ファイルを書き出す（毎回上書き）。``claude`` だけは読み込みが
``settingSources`` 次第で確実でないため、**初回プロンプトにも契約を埋める**
二重化を行う（:attr:`CliAdapter.rules_in_prompt`）。

コマンド名は設定 ``agent_cli_commands``（``{cli: コマンド}``）で上書きできる。
値は空白区切りの引数を含んでよく、**2 語以上を書いたときは既定の引数を足さずに
そのまま使う**（``"cursor-agent acp"`` のように起動の仕方が変わっても設定だけで
追随できる）。ワンショット側だけを変えたいときは ``"<cli>_oneshot"`` のキーに
書く。モデルは ``agent_cli_models``（``{cli: モデル}``。grok は従来の
``grok_model``）で、空なら CLI の既定に任せる。
"""

from __future__ import annotations

import logging
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from .config import load_settings
from .models import Settings

log = logging.getLogger(__name__)

#: 選べる CLI（設定 ``agent_cli`` の値）
CliId = Literal["grok", "claude", "codex", "cursor"]

#: 「CLI は動くがサインインしていない」を表す共通の文字列（SPEC §4.1）
COMMON_AUTH_MARKERS: tuple[str, ...] = (
    "not authenticated",
    "not logged in",
    "unauthenticated",
    "unauthorized",
    "authentication required",
    "please sign in",
    "please log in",
    "sign in to",
    "no credentials",
    "invalid api key",
    "401",
)


@dataclass(frozen=True)
class CliAdapter:
    """1 つのコーディング CLI の回し方。"""

    id: str
    #: 画面に出す名前（ヘルスチェックやエラー文言に使う）
    label: str
    #: ACP（``session/prompt`` を喋らせる起動）の既定コマンド
    default_command: str
    #: ワンショット（``-p`` 相当）の既定コマンド。ACP と同じなら同じ値
    oneshot_command: str
    #: ACP 起動でコマンドの直後に足す引数
    acp_prefix: tuple[str, ...] = ()
    #: ACP 起動の末尾に足す引数（モデル指定より後ろ）
    acp_suffix: tuple[str, ...] = ()
    #: ACP 起動のモデル指定フラグ（"" = モデルを渡さない）
    acp_model_flag: str = ""
    #: ワンショットのモデル指定フラグ（"" = モデルを渡さない）
    oneshot_model_flag: str = ""
    #: ワンショットでプロンプト本文の直前に置く引数
    prompt_args: tuple[str, ...] = ("-p",)
    #: 応答を JSON で受け取る引数（空 = 対応していない）
    json_args: tuple[str, ...] = ()
    #: 続きから走らせる引数（空 = 対応していない。セッション id を後ろに足す）
    resume_args: tuple[str, ...] = ()
    #: 契約の渡し方。``meta`` = ``session/new`` の ``_meta.rules``、
    #: ``file`` = 作業ディレクトリへ :attr:`rules_filename` を書き出す
    rules_mode: Literal["meta", "file"] = "meta"
    rules_filename: str = ""
    #: ACP でも契約を初回プロンプトに埋めるか（読み込みが確実でない CLI の保険）
    rules_in_prompt: bool = False
    #: この CLI 特有の認証エラー文字列
    auth_markers: tuple[str, ...] = ()
    #: コマンドが見つからないときに出す導入手順
    install_hint: str = ""
    #: コマンド名の見分け（``install_hint_for`` が実行ファイル名と突き合わせる）
    command_aliases: tuple[str, ...] = field(default_factory=tuple)

    # ------------------------------------------------------------- capability
    @property
    def supports_json_output(self) -> bool:
        return bool(self.json_args)

    @property
    def supports_resume(self) -> bool:
        return bool(self.resume_args)

    @property
    def writes_rules_file(self) -> bool:
        return self.rules_mode == "file" and bool(self.rules_filename)

    # ------------------------------------------------------------------- argv
    @staticmethod
    def _split(command: str, fallback: str) -> list[str]:
        """設定のコマンド文字列を argv にする（空白区切り。空なら既定）。"""
        raw = (command or "").strip() or fallback
        try:
            parts = shlex.split(raw)
        except ValueError:  # 閉じていない引用符などは 1 語として扱う
            parts = [raw]
        return parts or [fallback]

    def acp_argv(self, command: str = "", model: str = "") -> list[str]:
        """``ACP``（stdio で JSON-RPC を喋る）起動の argv。

        設定のコマンドに引数まで書いてあるときは、既定の引数を足さずにそちらを
        優先する（起動の仕方が CLI 側で変わっても設定だけで追随できる）。
        """
        parts = self._split(command, self.default_command)
        custom = len(parts) > 1
        argv = list(parts) if custom else [*parts, *self.acp_prefix]
        if model and self.acp_model_flag:
            argv += [self.acp_model_flag, model]
        if not custom:
            argv += list(self.acp_suffix)
        return argv

    def oneshot_argv(
        self,
        prompt: str,
        command: str = "",
        model: str = "",
        *,
        extra: list[str] | tuple[str, ...] = (),
        output_json: bool = False,
        resume_id: str = "",
    ) -> list[str]:
        """1 発だけ走らせる（``grok -p …`` 相当）argv。"""
        argv = self._split(command, self.oneshot_command)
        if model and self.oneshot_model_flag:
            argv += [self.oneshot_model_flag, model]
        argv += list(extra)
        if output_json and self.supports_json_output:
            argv += list(self.json_args)
        argv += [*self.prompt_args, prompt]
        if resume_id and self.supports_resume:
            argv += [*self.resume_args, resume_id]
        return argv

    def version_argv(self, command: str = "") -> list[str]:
        """ヘルスチェック用（``--version``）。ワンショット側の実行ファイルを見る。"""
        return [*self._split(command, self.oneshot_command), "--version"]

    # ------------------------------------------------------------------ rules
    def write_rules(self, workdir: str | Path, rules: str) -> Path | None:
        """契約を作業ディレクトリへ書き出す（``file`` 方式のときだけ）。

        毎回上書きしてよい: 契約はセッションを開くたびに組み直しているので、
        古いものが残っている方が困る。書けなくても致命的ではないので、失敗は
        ログだけ残して ``None`` を返す（呼び出し側はプロンプトで続けられる）。
        """
        if not self.writes_rules_file or not (rules or "").strip():
            return None
        path = Path(workdir) / self.rules_filename
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(rules.strip() + "\n", encoding="utf-8")
        except OSError:
            log.warning("%s を書き出せませんでした: %s", self.rules_filename, path)
            return None
        return path

    # ------------------------------------------------------------------ auth
    def looks_like_auth_error(self, text: str) -> bool:
        lowered = (text or "").lower()
        markers = (*COMMON_AUTH_MARKERS, *self.auth_markers)
        return any(marker in lowered for marker in markers)


GROK = CliAdapter(
    id="grok",
    label="Grok",
    default_command="grok",
    oneshot_command="grok",
    acp_prefix=("agent",),
    acp_suffix=("stdio",),
    acp_model_flag="-m",
    oneshot_model_flag="--model",
    prompt_args=("-p",),
    json_args=("--output-format", "json"),
    resume_args=("--resume",),
    rules_mode="meta",
    auth_markers=("run `grok` to sign in",),
    install_hint=(
        "Grok Build CLI をインストール"
        " (curl -fsSL https://x.ai/cli/install.sh | bash) してください"
    ),
    command_aliases=("grok",),
)

CLAUDE = CliAdapter(
    id="claude",
    label="Claude Code",
    # Agent SDK の ACP ブリッジ（npx @zed-industries/claude-code-acp 等）。
    default_command="claude-agent-acp",
    oneshot_command="claude",
    oneshot_model_flag="--model",
    prompt_args=("-p",),
    # 契約は cwd の CLAUDE.md。Agent SDK は settingSources を省略すると
    # ["user", "project", "local"] 相当（= CLAUDE.md を読む）だが、**ACP ブリッジが
    # 何を渡すかはブリッジ側の実装次第**で、外から強制する環境変数・フラグは
    # 公式ドキュメントに無い（2026-08 時点で確認）。読まれなかったときに契約ごと
    # 落ちるのは痛いので、初回プロンプトにも同じ契約を埋めて取りこぼしを防ぐ。
    rules_mode="file",
    rules_filename="CLAUDE.md",
    rules_in_prompt=True,
    auth_markers=("/login", "credit balance", "api key not found"),
    install_hint=(
        "Claude Code をインストール (npm i -g @anthropic-ai/claude-code) し、"
        " ACP ブリッジ (npm i -g @zed-industries/claude-code-acp) を用意してください"
    ),
    command_aliases=("claude", "claude-agent-acp", "claude-code-acp"),
)

CODEX = CliAdapter(
    id="codex",
    label="Codex",
    default_command="codex-acp",
    oneshot_command="codex",
    oneshot_model_flag="-m",
    prompt_args=("exec",),
    rules_mode="file",
    rules_filename="AGENTS.md",
    auth_markers=("codex login", "not signed in"),
    install_hint="Codex CLI をインストール (npm i -g @openai/codex) してください",
    command_aliases=("codex", "codex-acp"),
)

CURSOR = CliAdapter(
    id="cursor",
    label="Cursor",
    default_command="cursor-agent",
    oneshot_command="cursor-agent",
    acp_suffix=("--acp",),
    oneshot_model_flag="--model",
    prompt_args=("-p",),
    rules_mode="file",
    rules_filename="AGENTS.md",
    auth_markers=("cursor-agent login",),
    install_hint=(
        "Cursor CLI をインストール"
        " (curl https://cursor.com/install -fsS | bash) してください"
    ),
    command_aliases=("cursor-agent", "cursor"),
)

ADAPTERS: dict[str, CliAdapter] = {
    adapter.id: adapter for adapter in (GROK, CLAUDE, CODEX, CURSOR)
}

DEFAULT_CLI = GROK.id


# --------------------------------------------------------------------------
# 設定からの解決
# --------------------------------------------------------------------------

def adapter_for(cli_id: str | None) -> CliAdapter:
    """id からアダプタを引く（未知の値は既定の grok）。"""
    return ADAPTERS.get((cli_id or "").strip().lower(), GROK)


def active_adapter(settings: Settings | None = None) -> CliAdapter:
    """いま選ばれている CLI。"""
    return adapter_for((settings or load_settings()).agent_cli)


def command_for(
    adapter: CliAdapter, settings: Settings | None = None, *, oneshot: bool = False
) -> str:
    """その CLI の実行コマンド（設定の上書き → 既定）。

    grok だけは従来の ``grok_command`` も見る（設定ファイルの移行を不要にする）。
    ワンショット側だけを変えたいときは ``agent_cli_commands`` の
    ``"<cli>_oneshot"`` キー。
    """
    values = settings or load_settings()
    overrides = values.agent_cli_commands or {}
    if oneshot:
        override = str(overrides.get(f"{adapter.id}_oneshot") or "").strip()
        if override:
            return override
        if adapter.oneshot_command != adapter.default_command:
            # ACP ブリッジと本体が別のコマンド（claude / codex）。ACP 側の上書きを
            # そのままワンショットに使うと別物を叩いてしまうので既定に戻す。
            return adapter.oneshot_command
    override = str(overrides.get(adapter.id) or "").strip()
    if override:
        return override
    if adapter.id == GROK.id:
        return (values.grok_command or "").strip() or GROK.default_command
    return adapter.oneshot_command if oneshot else adapter.default_command


def model_for(adapter: CliAdapter, settings: Settings | None = None) -> str:
    """その CLI に渡すモデル名（空 = CLI の既定に任せる）。"""
    values = settings or load_settings()
    model = str((values.agent_cli_models or {}).get(adapter.id) or "").strip()
    if model:
        return model
    if adapter.id == GROK.id:
        return (values.grok_model or "").strip()
    return ""


def install_hint_for(command: str) -> str:
    """実行ファイル名から導入手順を選ぶ（見つからないコマンドの案内）。"""
    name = Path((command or "").strip() or "x").name.lower()
    for adapter in ADAPTERS.values():
        if name in adapter.command_aliases:
            return adapter.install_hint
    return ""


def label_for(command: str) -> str:
    """実行ファイル名から画面に出す CLI 名を選ぶ（分からなければコマンド名）。"""
    name = Path((command or "").strip() or "CLI").name
    for adapter in ADAPTERS.values():
        if name.lower() in adapter.command_aliases:
            return adapter.label
    return name


# --------------------------------------------------------------------------
# CLI を切り替えたとき
# --------------------------------------------------------------------------

#: 保存済みの続き用セッション id を持つテーブル（CLI を変えると全部無効になる）
_SESSION_TABLES = ("chat_sessions", "agent_sessions", "canvas_sessions")


async def forget_saved_sessions() -> None:
    """保存済みの続き用セッション id を捨てる（CLI を切り替えたとき）。

    セッション id は CLI ごとのもので、別の CLI では意味を持たない。放っておいても
    ``session/load`` の失敗（:class:`~app.grok_session.GrokSessionGone`）で履歴の
    組み直しに落ちるが、無駄な 1 往復になるので先に消しておく。会話そのもの
    （正本）はどのテーブルでも別カラムなので失われない。
    """
    from .db import get_db

    async with get_db() as conn:
        for table in _SESSION_TABLES:
            try:
                await conn.execute(f"UPDATE {table} SET grok_session_id = ''")
            except Exception:  # noqa: BLE001 - 無いテーブル / カラムは黙って飛ばす
                log.debug("%s のセッション id を消せませんでした", table, exc_info=True)
        await conn.commit()
