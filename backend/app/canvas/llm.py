"""Canvas エージェントの LLM クライアント選択。

runner は必ずこのファクトリ経由でクライアントを取得する。provider を増やすときは
ここに分岐を足すだけで済ませる（Codex CLI / Claude API などの追加を見込んだ布石）。
"""

from __future__ import annotations

from pathlib import Path

from .. import grok

#: 選択可能な provider（将来 "codex" / "claude" などを追加）
PROVIDERS = ("grok",)

#: プロジェクト作成時の既定 provider
DEFAULT_PROVIDER = "grok"


def get_client(provider: str, workdir: Path):
    """provider に応じた LLMClient を返す（呼び出しは complete(prompt) -> str に統一）。"""
    if provider not in PROVIDERS:
        raise ValueError(f"unknown canvas llm provider: {provider}")
    return grok.get_agent_client(workdir)
