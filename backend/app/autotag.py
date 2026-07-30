"""ライブラリ素材の日本語タグ・表示名の自動生成（SPEC §7.2）。

生成物をライブラリに入れるとき、既定の表示名は英語プロンプトの先頭 60 字なので
（「a young woman dancing on a rooftop at sunset（動画）」）、あとで棚を眺めても
探しづらい。そこで **NSFW 自動判定（:mod:`app.nsfw`）と同じ形**で Grok に短い
日本語のタグと表示名を考えさせ、バックグラウンドで書き戻す:

1. ``POST /api/library/from-job`` は素材をすぐ返し（ユーザーを待たせない）、
2. :func:`spawn_for` の投げっぱなしタスクが Grok にワンショットで尋ね、
3. **利用者・エージェントが明示していない項目だけ**を書き戻し、
4. WS（``type: "library"``）で画面に伝える。

Grok が使えなくても静かに諦める（タグ無しのまま。ログには残す）。アップロード
した素材はプロンプトが無いので対象外。
"""

from __future__ import annotations

import asyncio
import logging

from . import grok, library, ws
from .models import Job, LibraryItem
from .nsfw import spawn

log = logging.getLogger(__name__)

#: おまけの処理なので、チャット用の既定より短く切り上げる（nsfw と同じ方針）
DESCRIBE_TIMEOUT = 60.0

#: 付けるタグの数（多すぎると棚がうるさくなる）
MIN_TAGS = 3
MAX_TAGS = 5

DESCRIBE_PROMPT = """\
あなたは映像素材ライブラリの整理係です。次の生成プロンプトから、その素材を
あとで探しやすくする**日本語**の短い表示名とタグを付けてください。

- name: 15 文字程度の日本語の作品名。何が写っているか一目で分かるように
  （例:「夕暮れ屋上のダンス」）。英語やファイル名めいた文字列にしない
- tags: %(min)d〜%(max)d 個の日本語の短いタグ（各 2〜8 文字程度）。被写体・場所・
  時間帯・雰囲気・画角など、探すときの手がかりになる語を選ぶ
  （例: ["女性", "屋上", "夕暮れ", "ダンス"]）
- 元のプロンプトが英語でも、name と tags は必ず日本語で書く
- 説明や前置きは書かず、JSON オブジェクトだけを 1 つ返してください

出力形式:
```json
{"name": "夕暮れ屋上のダンス", "tags": ["女性", "屋上", "夕暮れ", "ダンス"]}
```

--- 素材の生成プロンプト ---
%(text)s
--- ここまで ---
"""


def source_text(job: Job) -> str:
    """タグ付けに渡すテキスト（そのジョブが何を作ろうとしたか）。"""
    return "\n".join(
        part.strip()
        for part in (
            job.video_prompt,
            job.image_prompt,
            job.audio_prompt,
            job.user_input,
        )
        if part and part.strip()
    )


async def describe(text: str) -> tuple[str, list[str]]:
    """Grok にワンショットで尋ねる。``(name, tags)``、駄目なら ``("", [])``。"""
    body = (text or "").strip()
    if not body:
        return "", []
    client = grok.get_client()
    timeout = getattr(client, "timeout", None)
    if isinstance(timeout, (int, float)) and timeout > DESCRIBE_TIMEOUT:
        client.timeout = DESCRIBE_TIMEOUT  # type: ignore[attr-defined]
    prompt = DESCRIBE_PROMPT % {
        "min": MIN_TAGS,
        "max": MAX_TAGS,
        "text": body[:4000],
    }
    try:
        answer = await client.complete(prompt)
    except grok.LLMError as exc:
        log.info("ライブラリのタグ生成に Grok を使えませんでした: %s", exc)
        return "", []
    for parsed in grok.iter_json_objects(answer):
        if not isinstance(parsed, dict):
            continue
        name = parsed.get("name")
        tags = library.normalize_tags(parsed.get("tags"))[:MAX_TAGS]
        if tags or isinstance(name, str):
            return (name.strip() if isinstance(name, str) else ""), tags
    log.info("ライブラリのタグ生成の応答から JSON を取り出せませんでした")
    return "", []


async def annotate(
    item_id: str, text: str, *, set_name: bool, set_tags: bool
) -> None:
    """1 素材分の自動生成（バックグラウンドタスクの本体）。例外は投げない。"""
    try:
        if not (set_name or set_tags):
            return
        name, tags = await describe(text)
        patch: dict[str, object] = {}
        if set_name and name:
            patch["name"] = name
        if set_tags and tags:
            patch["tags"] = tags
        if not patch:
            return
        item = await library.update_item(item_id, **patch)  # type: ignore[arg-type]
        if item is None:
            return  # 生成しているあいだに消された
        await ws.publish_library(item)
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - タグ付けの失敗で登録を壊さない
        log.exception("library %s のタグ生成に失敗しました", item_id)


def spawn_for(item: LibraryItem, job: Job, *, named: bool = False) -> None:
    """登録直後の素材に、足りない表示名とタグを背景で付ける。

    ``named`` は呼び出し側が表示名を明示したかどうか（明示したものは上書き
    しない）。タグも、既に付いていれば触らない。
    """
    text = source_text(job)
    if not text:
        return
    spawn(
        annotate(item.id, text, set_name=not named, set_tags=not item.tags),
        key=f"autotag:{item.id}",
    )
