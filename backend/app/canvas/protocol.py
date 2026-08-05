"""Canvas エージェントのアクション JSON パース（agent_protocol.py の canvas 版）。

CLI は素のテキストを返すだけなので、「ツール呼び出し」は応答に混ぜた JSON
オブジェクトとして定義する。抽出は :func:`app.grok.iter_json_objects` を再利用
するが、既存エージェントの語彙とは無関係なので ``agent_protocol`` は import
しない（キャンバスは独立サブシステム）。

不正な JSON は :class:`CanvasActionError` を投げ、呼び出し側（runner）が
リマインダー付きで 1 回だけ再試行する。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, ValidationError

from .. import grok
from .models import NODE_DATA_MODELS, validate_node_data

ACTION_NAMES = (
    "message",
    "list_nodes",
    "read_node",
    "create_node",
    "update_node",
    "move_node",
    "best_practices",
    "generate",
    "done",
)

#: 本文を返して会話を終えるアクション（それ以外は結果を読ませて次のターンへ）
TERMINAL_ACTIONS = ("message", "done")


class CanvasActionError(Exception):
    """不正なアクション JSON（リマインダー付きで 1 回だけ再試行する）。"""


class CanvasAction(BaseModel):
    action: str
    content: str = ""                     # message / done の本文
    node_id: str | None = None            # read/update/move
    kind: str | None = None               # create_node
    title: str = ""                       # create/update
    data: dict[str, Any] = Field(default_factory=dict)   # create/update（全量）
    x: float | None = None                # create/move
    y: float | None = None
    workflow: str = ""                    # best_practices
    model_node_id: str | None = None      # generate
    prompt: str = ""                      # generate
    source_image: str | None = None       # generate（i2v の開始フレーム URL）
    lyrics: str = ""                      # generate（audio）


def find_action_payload(text: str) -> dict[str, Any] | None:
    """``action`` キーを持つ最初の JSON オブジェクト。"""
    for parsed in grok.iter_json_objects(text):
        if isinstance(parsed, dict) and isinstance(parsed.get("action"), str):
            return parsed
    return None


def looks_like_action_attempt(text: str) -> bool:
    """アクションを出そうとして失敗した応答か（再試行するかの判断）。"""
    return grok.has_json_fence(text) or find_action_payload(text) is not None


def _pydantic_detail(exc: ValidationError) -> str:
    parts = []
    for error in exc.errors()[:3]:
        location = ".".join(str(x) for x in error.get("loc", ())) or "(root)"
        parts.append(f"{location}: {error.get('msg', '')}")
    return " / ".join(parts)


def _text(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    return value.strip() if isinstance(value, str) else ""


def _require_node_id(payload: dict[str, Any], name: str) -> str:
    node_id = _text(payload, "node_id")
    if not node_id:
        raise CanvasActionError(f"{name} には対象の node_id が必要です")
    return node_id


def _number(payload: dict[str, Any], key: str, name: str) -> float:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CanvasActionError(f"{name} の {key} には数値が必要です")
    return float(value)


def _data(payload: dict[str, Any], name: str) -> dict[str, Any]:
    value = payload.get("data")
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise CanvasActionError(f"{name} の data は JSON オブジェクトである必要があります")
    return value


def validate_data(kind: str, data: dict[str, Any]) -> dict[str, Any]:
    """kind のスキーマで data を検証・正規化する（違反は CanvasActionError）。

    ``update_node`` は対象カードを読むまで kind が分からないので、runner からも
    この関数を呼んで同じメッセージで返せるようにしている。
    """
    try:
        return validate_node_data(kind, data)
    except ValidationError as exc:
        raise CanvasActionError(
            f"data が {kind} のスキーマに合いません: {_pydantic_detail(exc)}"
        ) from exc


def parse_action(text: str) -> CanvasAction | None:
    """1 応答ぶんの（省略可能な）アクションを取り出す。不正は CanvasActionError。"""
    payload = find_action_payload(text)
    if payload is None:
        return None

    name = str(payload.get("action") or "").strip()
    if name not in ACTION_NAMES:
        raise CanvasActionError(
            f"未知の action '{name}' です。使えるのは {' / '.join(ACTION_NAMES)} です"
        )

    action = CanvasAction(action=name)
    action.content = _text(payload, "content")
    action.title = _text(payload, "title")

    if name in ("message", "done", "list_nodes"):
        return action

    if name == "read_node":
        action.node_id = _require_node_id(payload, name)
        return action

    if name == "create_node":
        kind = _text(payload, "kind")
        if kind not in NODE_DATA_MODELS:
            raise CanvasActionError(
                f"create_node の kind '{kind}' は使えません。"
                f"使えるのは {' / '.join(NODE_DATA_MODELS)} です"
            )
        action.kind = kind
        action.data = validate_data(kind, _data(payload, name))
        if payload.get("x") is not None:
            action.x = _number(payload, "x", name)
        if payload.get("y") is not None:
            action.y = _number(payload, "y", name)
        return action

    if name == "update_node":
        action.node_id = _require_node_id(payload, name)
        raw = _data(payload, name)
        kind = _text(payload, "kind")
        if kind:
            if kind not in NODE_DATA_MODELS:
                raise CanvasActionError(
                    f"update_node の kind '{kind}' は使えません。"
                    f"使えるのは {' / '.join(NODE_DATA_MODELS)} です"
                )
            action.kind = kind
            raw = validate_data(kind, raw)
        # kind が無いときは対象カードの kind が分からないので、runner が
        # カードを読み出してから検証する。
        action.data = raw
        return action

    if name == "move_node":
        action.node_id = _require_node_id(payload, name)
        action.x = _number(payload, "x", name)
        action.y = _number(payload, "y", name)
        return action

    if name == "best_practices":
        workflow = _text(payload, "workflow")
        if not workflow:
            raise CanvasActionError("best_practices には workflow が必要です")
        action.workflow = workflow
        return action

    # generate
    model_node_id = _text(payload, "model_node_id")
    if not model_node_id:
        raise CanvasActionError("generate には model カードの model_node_id が必要です")
    prompt = _text(payload, "prompt")
    if not prompt:
        raise CanvasActionError("generate には prompt が必要です")
    action.model_node_id = model_node_id
    action.prompt = prompt
    action.source_image = _text(payload, "source_image") or None
    action.lyrics = _text(payload, "lyrics")
    return action
