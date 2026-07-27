"""NSFW 自動判定。

作品には NSFW と非 NSFW が混在するため、ジョブ／エージェントセッションには
``nsfw`` フラグを持たせ、UI のトグルがオフのあいだは NSFW の存在自体を見せない。

判定は **常にベストエフォート**で、生成をブロックしない:

1. ジョブ／セッションは ``nsfw_source = ''``（未判定）で作られ、
2. :func:`spawn` の fire-and-forget タスクが Grok にワンショットで尋ね、
3. 失敗したらキーワードのヒューリスティックに落ち、
4. ``nsfw_source`` がまだ ''（= 手動指定で上書きされていない）ときだけ
   ``auto`` として書き戻し、WS で画面に伝える。

判定そのものが失敗してもジョブは一切失敗しない（例外は握って捨てる）。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any

from . import grok, ws
from .db import get_db

log = logging.getLogger(__name__)

# 判定はおまけなので、チャット用の既定 (120s) より短く切り上げる。
CLASSIFY_TIMEOUT = 60.0

CLASSIFY_PROMPT = """\
あなたは画像／動画生成プロンプトの内容審査です。次のテキストが性的・露骨な
（いわゆる NSFW / R-18 の）内容かどうかだけを判定してください。

- ヌード、性行為、性器・胸部の露出、下着姿の強調などがあれば nsfw は true
- 水着や軽い露出のみ、暴力・グロのみ、健全な内容は false
- 説明や前置きは書かず、JSON オブジェクトだけを 1 つ返してください

出力形式:
```json
{"nsfw": true}
```

--- 判定対象のテキスト ---
%s
--- ここまで ---
"""

# ヒューリスティック用の語彙（判定に失敗したときのフォールバック）。
KEYWORDS: tuple[str, ...] = (
    # 英語
    "nsfw",
    "nude",
    "nudity",
    "naked",
    "topless",
    "bottomless",
    "explicit",
    "erotic",
    "eroticism",
    "porn",
    "hentai",
    "sex",
    "sexual",
    "intercourse",
    "orgasm",
    "cum",
    "genital",
    "penis",
    "vagina",
    "pussy",
    "nipple",
    "nipples",
    "areola",
    "breasts",
    "cleavage",
    "lingerie",
    "panties",
    "underwear",
    "bra ",
    "masturbat",
    "fellatio",
    "creampie",
    "bukkake",
    "r-18",
    "r18",
    # 日本語
    "全裸",
    "裸",
    "ヌード",
    "性的",
    "性行為",
    "セックス",
    "エロ",
    "官能",
    "絡み",
    "濡れ場",
    "下着",
    "パンティ",
    "パンツ姿",
    "ブラジャー",
    "乳首",
    "乳房",
    "おっぱい",
    "陰部",
    "露出",
    "アダルト",
    "18禁",
    "成人向け",
)


# --------------------------------------------------------------------------
# 判定
# --------------------------------------------------------------------------

def heuristic(text: str) -> bool:
    """キーワード判定（Grok が使えないときのフォールバック）。"""
    lowered = (text or "").lower()
    return any(word in lowered for word in KEYWORDS)


async def classify(text: str) -> bool | None:
    """Grok にワンショットで尋ねる。判定できなければ None（失敗を隠さない）。"""
    body = (text or "").strip()
    if not body:
        return None
    client = grok.get_client()
    # 判定はおまけなので待ち時間を短くする（CLI 実装のみ timeout を持つ）。
    timeout = getattr(client, "timeout", None)
    if isinstance(timeout, (int, float)) and timeout > CLASSIFY_TIMEOUT:
        client.timeout = CLASSIFY_TIMEOUT  # type: ignore[attr-defined]
    try:
        answer = await client.complete(CLASSIFY_PROMPT % body[:4000])
    except grok.LLMError as exc:
        log.info("NSFW 判定に Grok を使えませんでした: %s", exc)
        return None
    for parsed in grok.iter_json_objects(answer):
        if isinstance(parsed, dict) and isinstance(parsed.get("nsfw"), bool):
            return bool(parsed["nsfw"])
    log.info("NSFW 判定の応答から JSON を取り出せませんでした")
    return None


async def classify_or_heuristic(text: str) -> tuple[bool, str]:
    """``(nsfw, 'auto')``。Grok が判定できなければキーワード判定に落とす。"""
    verdict = await classify(text)
    if verdict is None:
        verdict = heuristic(text)
    return verdict, "auto"


def job_text(
    image_prompt: str | None, video_prompt: str | None, user_input: str | None
) -> str:
    """判定に渡すテキスト（ジョブの各プロンプトを連結）。"""
    return "\n".join(part.strip() for part in (image_prompt, video_prompt, user_input) if part)


# --------------------------------------------------------------------------
# 反映（未判定のものだけを auto で上書きする）
# --------------------------------------------------------------------------

async def _apply(table: str, row_id: str, nsfw: bool) -> bool:
    """``nsfw_source`` がまだ '' の行だけ更新する。True なら反映できた。"""
    async with get_db() as conn:
        cur = await conn.execute(
            f"UPDATE {table} SET nsfw = ?, nsfw_source = 'auto'"
            f" WHERE id = ? AND nsfw_source = ''",
            (1 if nsfw else 0, row_id),
        )
        await conn.commit()
        return cur.rowcount > 0


async def _job_status(job_id: str) -> str | None:
    async with get_db() as conn:
        async with conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)) as cur:
            row = await cur.fetchone()
    return row["status"] if row else None


async def _session_status(session_id: str) -> str | None:
    async with get_db() as conn:
        async with conn.execute(
            "SELECT status FROM agent_sessions WHERE id = ?", (session_id,)
        ) as cur:
            row = await cur.fetchone()
    return row["status"] if row else None


async def escalate_session(session_id: str) -> bool:
    """セッション内のジョブが NSFW だったら、セッションも NSFW に引き上げる。

    手動指定（``manual``）は尊重する。エージェント以外のセッション ID なら何もしない。
    """
    async with get_db() as conn:
        cur = await conn.execute(
            "UPDATE agent_sessions SET nsfw = 1, nsfw_source = 'auto'"
            " WHERE id = ? AND nsfw = 0 AND nsfw_source != 'manual'",
            (session_id,),
        )
        await conn.commit()
        if cur.rowcount <= 0:
            return False
    status = await _session_status(session_id)
    if status:
        await ws.publish_agent(session_id, status, message="NSFW と判定しました")
    return True


async def classify_job(
    job_id: str, text: str, session_id: str | None = None
) -> None:
    """1 ジョブ分の自動判定（バックグラウンドタスクの本体）。例外は投げない。"""
    try:
        nsfw, _ = await classify_or_heuristic(text)
        if not await _apply("jobs", job_id, nsfw):
            return  # 手動指定で確定済み、またはジョブが消えた
        status = await _job_status(job_id)
        if status:
            await ws.publish(job_id, status, nsfw=nsfw)
        if nsfw and session_id:
            await escalate_session(session_id)
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - 判定の失敗でジョブを壊さない
        log.exception("job %s の NSFW 判定に失敗しました", job_id)


async def classify_session(session_id: str, text: str) -> None:
    """1 セッション分の自動判定（バックグラウンドタスクの本体）。例外は投げない。"""
    try:
        nsfw, _ = await classify_or_heuristic(text)
        if not await _apply("agent_sessions", session_id, nsfw):
            return
        status = await _session_status(session_id)
        if status:
            await ws.publish_agent(
                session_id, status, message="NSFW と判定しました" if nsfw else None
            )
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001
        log.exception("session %s の NSFW 判定に失敗しました", session_id)


# --------------------------------------------------------------------------
# fire-and-forget
# --------------------------------------------------------------------------

# タスクが GC で消えないように参照を持っておく。
_tasks: set[asyncio.Task[None]] = set()
# 実行中の判定キー（同じ対象を二重に判定しない）。
_inflight: set[str] = set()


def spawn(
    coro: Coroutine[Any, Any, None], *, key: str | None = None
) -> asyncio.Task[None] | None:
    """判定タスクを投げっぱなしで開始する。

    ``key`` が同じ判定がまだ走っているときは何もしない（Grok の二重呼び出し防止）。
    イベントループの外（同期スクリプト等）では判定を諦める。
    """
    if key is not None and key in _inflight:
        coro.close()
        return None
    try:
        task = asyncio.get_running_loop().create_task(coro)
    except RuntimeError:  # イベントループがない（同期スクリプト等）
        coro.close()
        return None
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    if key is not None:
        _inflight.add(key)
        task.add_done_callback(lambda _t, k=key: _inflight.discard(k))
    return task
