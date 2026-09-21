"""NSFW 自動判定。

作品には NSFW と非 NSFW が混在するため、ジョブには ``nsfw`` フラグを持たせ、
UI のトグルがオフのあいだは NSFW の存在自体を見せない。

判定は **常にベストエフォート**で、生成をブロックしない:

1. ジョブは ``nsfw_source = ''``（未判定）で作られ、
2. :func:`spawn` の fire-and-forget タスクがキーワードで判定し、
3. ``nsfw_source`` がまだ ''（= 手動指定で上書きされていない）ときだけ
   ``auto`` として書き戻し、WS で画面に伝える。

判定に LLM は使わない（アプリ内で LLM を呼ぶのはプロンプト作成チャットと
ヘルスチェックだけ。SPEC §4.1）ので、語彙に無い書き方は取りこぼす。人が
トグルで直せば ``nsfw_source = 'manual'`` になり、以後は自動判定に触られない。
判定そのものが失敗してもジョブは一切失敗しない（例外は握って捨てる）。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any

from . import ws
from .db import get_db

log = logging.getLogger(__name__)

# 判定に使う語彙（この語のどれかを含めば NSFW とみなす）。
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
    """キーワード判定（自動判定はこれだけ）。"""
    lowered = (text or "").lower()
    return any(word in lowered for word in KEYWORDS)


async def classify_or_heuristic(text: str) -> tuple[bool, str]:
    """``(nsfw, 'auto')``。キーワードだけで判定する（LLM は呼ばない）。"""
    return heuristic(text), "auto"


def job_text(
    image_prompt: str | None,
    video_prompt: str | None,
    user_input: str | None,
    audio_prompt: str | None = None,
) -> str:
    """判定に渡すテキスト（ジョブの各プロンプトを連結）。"""
    return "\n".join(
        part.strip()
        for part in (image_prompt, video_prompt, user_input, audio_prompt)
        if part
    )


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


async def classify_job(job_id: str, text: str) -> None:
    """1 ジョブ分の自動判定（バックグラウンドタスクの本体）。例外は投げない。"""
    try:
        nsfw, _ = await classify_or_heuristic(text)
        if not await _apply("jobs", job_id, nsfw):
            return  # 手動指定で確定済み、またはジョブが消えた
        status = await _job_status(job_id)
        if status:
            await ws.publish(job_id, status, nsfw=nsfw)
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - 判定の失敗でジョブを壊さない
        log.exception("job %s の NSFW 判定に失敗しました", job_id)


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

    ``key`` が同じ判定がまだ走っているときは何もしない（二重実行の防止）。
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
