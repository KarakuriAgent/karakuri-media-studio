"""Chat-style prompt authoring API (SPEC §4.3 / §9).

Grok's CLI is stateless, so the transcript lives here: the session row keeps
``[{role, content, ts}]`` with the assembled system prompt as ``messages[0]``
and every turn re-sends the whole thing.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException

from .. import grok, library, prompts
from ..config import load_settings
from ..db import get_db
from ..ids import new_id
from ..jobs import JobValidationError, resolve_asset_path
from ..models import (
    ChatMessage,
    ChatReference,
    ChatReply,
    ChatSendMessage,
    ChatSession,
    ChatSessionCreate,
    LibraryItem,
    PromptResult,
)
from ..paths import GROK_WORKDIR, rebase_stored_path, resolve_workdir

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chat", tags=["chat"])


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _workdir() -> Path:
    return resolve_workdir(load_settings().grok_workdir, GROK_WORKDIR)


def _copy_input_image(source: str, session_id: str, *, field: str, stem: str) -> str:
    """入力画像を CLI の隣に置いて中身を見られるようにする（§4.3）。

    ``field`` はエラー文言に出す欄の名前、``stem`` はコピー先のファイル名の頭
    （``start_frame`` / ``end_frame``）。
    """
    src = resolve_asset_path(source, field=field)
    workdir = _workdir()
    workdir.mkdir(parents=True, exist_ok=True)
    dest = workdir / f"{stem}_{session_id}{src.suffix or '.png'}"
    shutil.copy2(src, dest)
    return dest.name


def _stage_image(source: str, session_id: str, *, field: str, stem: str) -> str | None:
    """:func:`_copy_input_image` の best effort 版（失敗しても本文だけで続ける）。

    パスの検証で弾かれたら 422 に変換するが、コピーそのものの失敗（権限など）は
    致命的ではないので、ファイル名を渡さずテキストだけのセッションに落とす。
    """
    if not (source or "").strip():
        return None
    try:
        return _copy_input_image(source, session_id, field=field, stem=stem)
    except JobValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except OSError as exc:  # copying is best effort: text-only fallback
        log.warning("could not stage %s for grok: %s", field, exc)
        return None


def _reference_of(
    raw: str, kind: str, by_path: dict[Path, LibraryItem]
) -> ChatReference:
    """参照 1 件をライブラリと突き合わせる（未登録ならファイル名だけ）。

    パスが解決できない（消えている・ライブラリ外）ときもエラーにはしない: これは
    ジョブの入力ではなくチャットに渡すメタデータなので、書いてある文字列の
    ファイル名だけを伝えて会話は続ける。
    """
    filename = Path(raw.strip()).name
    try:
        resolved = resolve_asset_path(raw, field=f"reference_{kind}s")
    except JobValidationError:
        resolved = None
    item = by_path.get(resolved) if resolved is not None else None
    if item is None:
        return ChatReference(kind=kind, filename=filename)
    return ChatReference(
        kind=kind,
        filename=Path(item.path).name,
        name=item.name,
        category=item.category or "",
        tags=list(item.tags),
        nsfw=item.nsfw,
    )


def _index_by_path(items: list[LibraryItem]) -> dict[Path, LibraryItem]:
    """ライブラリを解決済みパスで引けるようにする（同期・スレッドで回す）。"""
    by_path: dict[Path, LibraryItem] = {}
    for item in items:
        try:
            by_path[rebase_stored_path(item.path).resolve()] = item
        except OSError:  # 消えている行はメタデータだけ残っていることがある
            continue
    return by_path


async def _resolve_references(payload: ChatSessionCreate) -> list[ChatReference]:
    """フォームで選ばれている参照素材を、種別ごとの順番を保ったまま解決する。

    並びは ``<Picture N>` / `<Video N>` / `<Audio N>`` のタグ番号そのものなので、
    画像 -> 動画 -> 音声の順に積む（:func:`app.prompts.reference_tags` が同じ順で
    番号を振る）。
    """
    groups = [
        ("image", payload.reference_images),
        ("video", payload.reference_videos),
        ("audio", payload.reference_audios),
    ]
    if not any(values for _, values in groups):
        return []
    items = await library.list_items()
    # 行数ぶんの stat が走るので、イベントループを止めないよう別スレッドで組む。
    by_path = await asyncio.to_thread(_index_by_path, items)
    return [
        _reference_of(raw, kind, by_path)
        for kind, values in groups
        for raw in values
        if (raw or "").strip()
    ]


# --------------------------------------------------------------------------
# persistence
# --------------------------------------------------------------------------

def _row_to_session(row) -> ChatSession:
    try:
        raw = json.loads(row["messages"] or "[]")
    except ValueError:
        raw = []
    messages = [ChatMessage(**m) for m in raw if isinstance(m, dict)]
    return ChatSession(
        id=row["id"],
        created_at=row["created_at"],
        job_id=row["job_id"],
        messages=messages,
    )


async def _load_session(session_id: str) -> ChatSession:
    async with get_db() as conn:
        async with conn.execute(
            "SELECT * FROM chat_sessions WHERE id = ?", (session_id,)
        ) as cur:
            row = await cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="chat session not found")
    return _row_to_session(row)


def _dump(messages: list[ChatMessage]) -> str:
    return json.dumps([m.model_dump() for m in messages], ensure_ascii=False)


async def _save_messages(session_id: str, messages: list[ChatMessage]) -> None:
    async with get_db() as conn:
        await conn.execute(
            "UPDATE chat_sessions SET messages = ? WHERE id = ?",
            (_dump(messages), session_id),
        )
        await conn.commit()


# --------------------------------------------------------------------------
# endpoints
# --------------------------------------------------------------------------

@router.post("/sessions", response_model=ChatSession, status_code=201)
async def create_session(payload: ChatSessionCreate) -> ChatSession:
    """Start a session; the form snapshot becomes the stored system message."""
    session_id = new_id()

    # 入力画像は mode を問わず（編集系の画像ワークフローでも）見せる: 欄が出て
    # いるときだけフォームが送ってくるので、来ていれば実際に使われる画像。
    # コピーは数 MB になることがあるので、イベントループの外で行う。
    start_image_filename = await asyncio.to_thread(
        _stage_image,
        payload.start_image_path or "",
        session_id,
        field="start_image_path",
        stem="start_frame",
    )
    end_image_filename = await asyncio.to_thread(
        _stage_image,
        payload.end_image_path or "",
        session_id,
        field="end_image_path",
        stem="end_frame",
    )
    references = await _resolve_references(payload)

    system = ChatMessage(
        role="system",
        content=prompts.build_system_prompt(
            payload,
            start_image_filename,
            end_image_filename=end_image_filename,
            references=references,
            # 接続先ごとに勧める MiniMax H3 の版が変わる（agent と同じ扱いで、
            # セッション作成時に焼き込む）。
            comfy_target=load_settings().comfy_target,
        ),
        ts=_now(),
    )
    session = ChatSession(id=session_id, created_at=_now(), messages=[system])
    async with get_db() as conn:
        await conn.execute(
            "INSERT INTO chat_sessions (id, created_at, job_id, messages)"
            " VALUES (?,?,?,?)",
            (session.id, session.created_at, None, _dump(session.messages)),
        )
        await conn.commit()
    return session


@router.get("/sessions/{session_id}", response_model=ChatSession)
async def get_session(session_id: str) -> ChatSession:
    return await _load_session(session_id)


@router.post("/sessions/{session_id}/messages", response_model=ChatReply)
async def send_message(session_id: str, payload: ChatSendMessage) -> ChatReply:
    """Append a user turn, ask Grok, store and return its answer."""
    content = (payload.content or "").strip()
    if not content:
        raise HTTPException(status_code=422, detail="content is empty")

    session = await _load_session(session_id)
    messages = [*session.messages, ChatMessage(role="user", content=content, ts=_now())]

    # 相談も研究や検分と同じくらい待たされることがあるので、エージェントと同じ
    # 設定（``agent_grok_timeout``。0 = タイムアウトなし）を使う。
    client = grok.get_client(timeout=grok.configured_timeout())
    try:
        answer = await client.complete(prompts.build_conversation(messages))
        result = grok.extract_result(answer)
        if result is None and grok.has_json_fence(answer):
            # It tried to deliver JSON but we could not parse it: one retry
            # with an explicit format reminder (SPEC §4.1).
            retry_messages = [
                *messages,
                ChatMessage(role="assistant", content=answer, ts=_now()),
            ]
            answer = await client.complete(
                prompts.build_conversation(retry_messages, retry=True)
            )
            result = grok.extract_result(answer)
    except grok.LLMError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    messages.append(ChatMessage(role="assistant", content=answer, ts=_now()))
    await _save_messages(session_id, messages)
    return ChatReply(
        content=answer, result=PromptResult(**result) if result else None
    )
