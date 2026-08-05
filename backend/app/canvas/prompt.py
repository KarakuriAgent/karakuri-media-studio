"""Canvas エージェントのシステムプロンプトと会話フラット化。

既存エージェントが「セッション作成時にシステムプロンプトを焼き込む」のに対し、
キャンバスは**カード目次が毎ターン変わる**ので毎回組み立て直す（オンデマンド
方式: 目次だけ渡し、本文は ``read_node`` で取らせる）。

フラット化テキストを組み立てるのはここだけで、呼び出しは
:func:`app.canvas.runner._run_turn`（クライアント呼び出しの直前）1 箇所に限定する。
将来 messages 形式の API バックエンドを足すときに、``llm.py`` のアダプタ側で
フラット化するか構造化して渡すかを選べるようにするため。
"""

from __future__ import annotations

from .. import workflows
from .models import CanvasMessage, CanvasNode

ROLE = """\
# ROLE
あなたは映像制作キャンバス「Canvas Studio」の相棒です。ユーザーはキャンバス上に
画風・キャラ・場所・小道具・脚本・絵コンテ・素材・メモ・モデル設定のカードを
並べて作品を組み立てます。あなたはカードを読み書きしながら企画を前に進めます。

キャンバスの全文はプロンプトに載っていません。下の目次にある id を頼りに、
必要なカードだけ read_node で読み出してください。
"""

ACTIONS = """\
# ACTIONS
返答は日本語の文章のみ、または文章のあとに ```json で囲んだアクション 1 つです。

```json
{"action": "message", "content": "ヒロインのカードを読みました。次に…"}
```
```json
{"action": "list_nodes"}
```
```json
{"action": "read_node", "node_id": "01JG8Z0M…"}
```
```json
{"action": "create_node", "kind": "character", "title": "ヒロイン",
 "data": {"appearance": "silver hair, blue eyes", "notes": ""}, "x": 400, "y": 120}
```
```json
{"action": "update_node", "node_id": "01JG8Z0M…", "title": "ヒロイン（改）",
 "data": {"appearance": "…全フィールドを書き直す…"}}
```
```json
{"action": "move_node", "node_id": "01JG8Z0M…", "x": 800, "y": 300}
```
```json
{"action": "best_practices", "workflow": "kling3_video"}
```
```json
{"action": "generate", "model_node_id": "01JG9A…", "prompt": "A silver-haired girl …",
 "source_image": "/library/image/xxx.png", "lyrics": ""}
```
```json
{"action": "done", "content": "3 カットぶんの絵コンテカードを作りました。"}
```
"""

RULES = """\
# RULES
- 1 回の応答に載せられる action は 1 つだけです。
- 返答の文章は日本語で書いてください（生成プロンプトの中身は英語で構いません）。
- update_node の data は**全量置換**です。read_node で現在の中身を読んでから、
  変更しないフィールドも含めて書き直してください。
- best_practices はワークフローの説明・プロンプトのコツ・必要入力・選択肢を返します。
  generate を使う前に、そのワークフローの best_practices を必ず読んでください。
- generate の model_node_id には **model カードの id** を指定します。生成に使う
  ワークフローと解像度・尺・LoRA はその model カードの設定がそのまま使われるので、
  変えたいときは先に update_node で model カードを直してください。
  prompt は英語で書き、画像から動かす（i2v）ときは source_image に
  ``/library/...`` や ``/outputs/...`` の URL を渡します。
- generate は投入までを行い「生成を開始しました (job …)」がすぐ EVENT で届きます。
  完成した素材は後から media カードとして model カードの右隣に自動で並び、
  job_done の EVENT で知らせます。**完了を待つ必要はありません** —
  投入できたらユーザーにそう伝えて done で締めてください。
- 観測系のアクション（list_nodes / read_node / best_practices）の結果は
  次のターンの EVENT として届きます。結果を見てから次の一手を決めてください。
- 言いたいことを言い終えたら done で締めてください。action を出さない素の文章も
  そのままユーザーへの返答になります。
"""


def _card_index(nodes: list[CanvasNode]) -> str:
    """目次（id / kind / title だけ）。本文は read_node で取らせる。"""
    lines = ["# CANVAS CARDS (id / kind / title only)"]
    if not nodes:
        lines.append("(カードはまだ 1 枚もありません)")
        return "\n".join(lines)
    for node in nodes:
        lines.append(f"- {node.id} | {node.kind} | {node.title or '(無題)'}")
    return "\n".join(lines)


def _workflow_ids() -> str:
    """選択可能なワークフロー ID の 1 行一覧（説明は best_practices で取らせる）。"""
    lines = ["# WORKFLOW IDS"]
    groups = (
        ("image", workflows.image_specs()),
        ("video", workflows.video_specs()),
        ("audio", workflows.audio_specs()),
    )
    for target, specs in groups:
        lines.append(f"## {target}")
        if not specs:
            lines.append("(選択できるワークフローがありません)")
            continue
        for spec in specs:
            lines.append(f"- {spec.id} — {spec.label}")
    return "\n".join(lines)


def build_canvas_system_prompt(nodes: list[CanvasNode]) -> str:
    """毎ターン組み立て直すシステムプロンプト（目次を最新に保つため）。"""
    return "\n\n".join(
        [ROLE.strip(), _card_index(nodes), _workflow_ids(), ACTIONS.strip(), RULES.strip()]
    )


_ROLE_LABEL = {
    "user": "USER",
    "assistant": "ASSISTANT",
    "event": "EVENT",
}

RETRY_SUFFIX = """\

IMPORTANT: your previous action could not be used ({reason}).
Re-send the reply with exactly one ```json fenced action object that follows
the ACTIONS section, or with no JSON at all if you only meant to talk.
"""


def build_canvas_conversation(
    system: str,
    messages: list[CanvasMessage],
    *,
    retry_reason: str | None = None,
) -> str:
    """システムプロンプト + 会話を CLI に渡す 1 本のテキストへ平坦化する。"""
    chunks: list[str] = [system.strip()]
    if messages:
        chunks.append("# CONVERSATION SO FAR (oldest first)")
    for message in messages:
        label = _ROLE_LABEL.get(message.role, message.role.upper())
        if message.kind:
            label = f"{label} ({message.kind})"
        chunks.append(f"### {label}\n{message.content.strip()}")
    body = "\n\n".join(chunks)
    if retry_reason:
        body += "\n" + RETRY_SUFFIX.format(reason=retry_reason)
    return (
        body
        + "\n\n### ASSISTANT\n(Your reply — Japanese text, optionally followed by"
        " one ```json action.)\n"
    )
