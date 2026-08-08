"""Agent execution loop (AGENT-MODE §5.3).

```
ユーザー発言 ─→ Grok ターン ─→ action?
                    │ plan      → 承認待ち
   承認 ──────────→ │ run_task  → JobRunner 投入 → 完了イベント追記 ─┐
                    │ inspect   → ffmpeg 展開 → 結果イベント追記 ──┤→ 次の Grok ターン
                    │ checkin   → 応答待ち                        │
                    │ done      → 納品サマリ → ループ終了 ←─────────┘
```

One asyncio task per session (started by the router, all cancelled from the
FastAPI lifespan).  Jobs go through the existing :class:`app.jobs.JobRunner`
queue — the loop only waits for the job row to reach a final state, so ComfyUI
still runs one job at a time (SPEC §5).

暴走防止: 連続 Grok ターン上限 (:data:`MAX_TURNS`)、セッションあたりの生成本数
上限 (``auto_limit``)、同一タスクの自動リトライは 1 回まで。自走（auto）セッションは
さらに 1 回のプラン提案で増やせる新規ジョブ数も制限する (:func:`plan_task_limits`)。

生成本数の上限は「そこで打ち切る」のではなく「超える直前にユーザーへ確認する」
（:func:`_request_limit_checkin`）。承認 1 回につき ``auto_limit`` 本ぶん枠が伸び、
次の区切りでまた確認する。断られたらそこで停止する。
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from . import (
    agent_protocol,
    autotag,
    canvas,
    grok,
    jobs,
    library,
    prompts,
    sheets,
    studio,
    ws,
)
from .agent_protocol import ActionError
from .agent_store import (
    load,
    now,
    session_dir,
    update,
)
from .config import load_settings
from .db import get_db
from .models import (
    AgentAction,
    AgentArtifact,
    AgentMessage,
    AgentSession,
    AgentTask,
    CanvasCardCreate,
    Job,
    JobContinue,
    JobCreate,
    JobRerun,
    StudioAssetCreate,
    StudioEpisodeCreate,
    StudioProjectDetail,
    StudioSceneCreate,
    StudioShotCreate,
    StudioShotUpdate,
    StudioTake,
)
from .paths import rebase_stored_path

log = logging.getLogger(__name__)

# Tunables (monkeypatched by the tests to keep them fast).
POLL_INTERVAL = 1.0
JOB_WAIT_TIMEOUT = 6 * 60 * 60.0
MAX_TURNS = 20  # 連続 Grok ターン上限
MAX_TASK_RETRIES = 1
MAX_INSPECT_FRAMES = 8

_loops: dict[str, asyncio.Task[None]] = {}
_stop_requests: set[str] = set()
# Grok ターンを実行中のセッション（「Grok が考えています…」の唯一の情報源）。
# ブラウザ発の API 呼び出しだけでなく、ループが回すターンもここに入る。
_thinking: set[str] = set()
# ターン実行中の活動テキスト（「思考中」「ツール実行中: ls」…）。ACP クライアントの
# コールバックで更新し、WS + ポーリングの両方で UI に届ける。
_activity: dict[str, str] = {}


def is_thinking(session_id: str) -> bool:
    """Grok ターンが走っているか（インメモリ。DB には保存しない）。"""
    return session_id in _thinking


def current_activity(session_id: str) -> str | None:
    """実行中の活動テキスト（インメモリ。DB には保存しない）。"""
    return _activity.get(session_id)


async def _set_thinking(session_id: str, value: bool) -> None:
    """thinking フラグを更新し、WS で通知する（取りこぼしはポーリングで拾える）。"""
    if value:
        _thinking.add(session_id)
    else:
        _thinking.discard(session_id)
        _activity.pop(session_id, None)  # ターン終了で活動表示を消す
    session = await load(session_id)
    await ws.publish_agent(
        session_id,
        session.status if session else "idle",
        thinking=value,
        activity=_activity.get(session_id),
    )


async def _set_activity(session_id: str, activity: str | None) -> None:
    """ACP から届いた活動を保存して WS で通知する。"""
    if activity:
        _activity[session_id] = activity
    else:
        _activity.pop(session_id, None)
    if not is_thinking(session_id):
        return  # ターン外の取りこぼし通知は無視する
    session = await load(session_id)
    await ws.publish_agent(
        session_id,
        session.status if session else "idle",
        thinking=True,
        activity=activity,
    )


# --------------------------------------------------------------------------
# transcript helpers
# --------------------------------------------------------------------------

async def append_message(session_id: str, message: AgentMessage) -> AgentSession | None:
    session = await load(session_id)
    if session is None:
        return None
    session.messages.append(message)
    await update(session_id, messages=session.messages)
    return session


async def add_artifact(session_id: str, artifact: AgentArtifact) -> None:
    session = await load(session_id)
    if session is None:
        return
    session.artifacts.append(artifact)
    await update(session_id, artifacts=session.artifacts)
    await ws.publish_agent(session_id, session.status, artifact=artifact)


async def _event(
    session_id: str, kind: str, content: str, **data: Any
) -> AgentSession | None:
    """Append one system event to the transcript (AGENT-MODE §4)."""
    return await append_message(
        session_id,
        AgentMessage(role="event", kind=kind, content=content, ts=now(), data=data),
    )


async def _set_status(session_id: str, status: str, message: str | None = None) -> None:
    await update(session_id, status=status)
    await ws.publish_agent(session_id, status, message=message)


def _turns(session: AgentSession) -> int:
    """Assistant turns since the last human input (暴走防止のカウンタ)."""
    count = 0
    for message in reversed(session.messages):
        if message.role in ("user", "system"):
            break
        if message.role == "assistant":
            count += 1
    return count


def generated_count(session: AgentSession) -> int:
    """Jobs this session has started (auto_limit の対象)。"""
    return len([m for m in session.messages if m.kind == "job_started"])


def next_task(session: AgentSession) -> AgentTask | None:
    if not session.plan.approved:
        return None
    return next((t for t in session.plan.tasks if t.status == "pending"), None)


async def _save_task(session_id: str, task: AgentTask) -> None:
    session = await load(session_id)
    if session is None:
        return
    for index, existing in enumerate(session.plan.tasks):
        if existing.id == task.id:
            session.plan.tasks[index] = task
            break
    await update(session_id, plan=session.plan)
    await ws.publish_agent(
        session_id,
        session.status,
        task_id=task.id,
        task_status=task.status,
        job_id=task.job_id,
    )


def plan_task_limits(session: AgentSession) -> tuple[int | None, int]:
    """``(max new tasks per plan, already finished tasks)`` for this session.

    ``every_job`` / ``milestone`` always put a human between the plan and the
    generation (プラン承認 + チェックイン) so the plan size needs no cap: the
    limit returns ``None``. Only ``auto`` (完了まで自走) is capped, and there it
    counts the **new** jobs of one proposal — a revised plan replaces the whole
    task list (:func:`_apply_plan`), so the finished tasks it re-lists are
    subtracted. セッション全体の生成本数は別途 ``auto_limit`` が守る。
    """
    if session.checkin_mode != "auto":
        return None, 0
    max_tasks = load_settings().agent_max_plan_tasks or agent_protocol.MAX_PLAN_TASKS
    done = sum(1 for task in session.plan.tasks if task.status == "done")
    return max_tasks, done


async def known_lora_names() -> dict[str, str]:
    """``{lora_name: target}`` — the registry as the plan validator sees it."""
    async with get_db() as conn:
        async with conn.execute("SELECT lora_name, target FROM loras") as cur:
            rows = await cur.fetchall()
    return {row["lora_name"]: row["target"] or "image" for row in rows}


async def known_lora_families() -> dict[str, str]:
    """``{lora_name: family}`` of the **image** LoRAs (SPEC §3.4).

    Video LoRAs have no family (LTX 2.3 is the only video model), so they are
    left out and the family check never fires for ``video_loras``.
    """
    async with get_db() as conn:
        async with conn.execute(
            "SELECT lora_name, family FROM loras WHERE target = 'image'"
        ) as cur:
            rows = await cur.fetchall()
    return {row["lora_name"]: row["family"] or "krea2" for row in rows}


# --------------------------------------------------------------------------
# one Grok turn
# --------------------------------------------------------------------------

async def run_turn(session_id: str) -> tuple[str, AgentAction | None]:
    """Ask Grok once, store the answer and parse its action.

    An unusable action is retried **once** with a format reminder (§3.1); a
    still-broken answer is kept as plain text and reported to the user.
    """
    session = await load(session_id)
    if session is None:
        raise LookupError(session_id)

    # 「Grok が考えています…」はここが唯一の情報源。例外でも必ず解除する。
    await _set_thinking(session_id, True)
    try:
        return await _run_turn(session_id, session)
    finally:
        await _set_thinking(session_id, False)


async def _run_turn(
    session_id: str, session: AgentSession
) -> tuple[str, AgentAction | None]:
    async def on_activity(activity: str | None) -> None:
        await _set_activity(session_id, activity)

    client = grok.get_agent_client(session_dir(session_id), on_activity)
    known = await known_lora_names() or None
    families = await known_lora_families() or None
    max_tasks, done_tasks = plan_task_limits(session)

    def parse(text: str) -> AgentAction | None:
        return agent_protocol.parse_action(
            text,
            known_loras=known,
            known_families=families,
            max_tasks=max_tasks,
            done_tasks=done_tasks,
        )

    answer = await client.complete(prompts.build_agent_conversation(session.messages))
    action: AgentAction | None = None
    reason = ""
    try:
        action = parse(answer)
    except ActionError as exc:
        reason = str(exc)
    if action is None and (reason or agent_protocol.looks_like_action_attempt(answer)):
        history = [
            *session.messages,
            AgentMessage(role="assistant", content=answer, ts=now()),
        ]
        answer = await client.complete(
            prompts.build_agent_conversation(
                history, retry_reason=reason or "JSON を解釈できませんでした"
            )
        )
        try:
            action = parse(answer)
            reason = ""
        except ActionError as exc:
            reason = str(exc)

    await append_message(
        session_id, AgentMessage(role="assistant", content=answer, ts=now())
    )
    if reason:
        await _event(
            session_id,
            "action_invalid",
            f"アクションを解釈できませんでした: {reason}",
            error=reason,
        )
    return answer, action


# --------------------------------------------------------------------------
# job execution
# --------------------------------------------------------------------------

async def _wait_for_job(job_id: str) -> Job:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + JOB_WAIT_TIMEOUT
    while True:
        job = await jobs.get_job(job_id, include_workflow=False)
        if job is None:
            raise jobs.JobError(f"job {job_id} が見つかりません")
        if job.status in ("done", "failed", "canceled"):
            return job
        if loop.time() > deadline:
            raise jobs.JobError(f"job {job_id} の完了を待てませんでした（タイムアウト）")
        await asyncio.sleep(POLL_INTERVAL)


async def _register_job_artifacts(
    session_id: str, job: Job, label: str
) -> None:
    if job.image_url:
        await add_artifact(
            session_id,
            AgentArtifact(
                kind="image", title=f"{label} 生成画像", ts=now(),
                url=job.image_url, job_id=job.id,
            ),
        )
    if job.video_url:
        await add_artifact(
            session_id,
            AgentArtifact(
                kind="video", title=f"{label} 動画", ts=now(),
                url=job.video_url, job_id=job.id,
            ),
        )
    if job.audio_output_url:
        await add_artifact(
            session_id,
            AgentArtifact(
                kind="audio", title=f"{label} 音声", ts=now(),
                url=job.audio_output_url, job_id=job.id,
            ),
        )


async def _run_and_wait(
    session_id: str, job: Job, *, label: str, task: AgentTask | None = None
) -> Job:
    """Announce a queued job, wait for it and append the result event."""
    await _event(
        session_id,
        "job_started",
        f"{label} を開始しました (job {job.id})",
        job_id=job.id,
        task_id=task.id if task else None,
    )
    await ws.publish_agent(
        session_id,
        "running",
        task_id=task.id if task else None,
        task_status="running",
        job_id=job.id,
    )
    finished = await _wait_for_job(job.id)
    if finished.status == "done":
        await _register_job_artifacts(session_id, finished, label)
        await _event(
            session_id,
            "job_done",
            f"{label} が完了しました (job {finished.id})。"
            f"{'動画: ' + finished.video_url + '。' if finished.video_url else ''}"
            f"{'画像: ' + finished.image_url + '。' if finished.image_url else ''}"
            f"{'音声: ' + finished.audio_output_url + '。' if finished.audio_output_url else ''}"
            # 音声ジョブには映像が無いので inspect（ffmpeg でのフレーム抽出）は使えない
            + (
                "音声ファイルは聴けないので、判断はプロンプトと設定から行ってください。"
                if finished.mode == "audio"
                else "必要なら inspect でフレームを確認してください。"
            ),
            job_id=finished.id,
            task_id=task.id if task else None,
            video_url=finished.video_url,
            image_url=finished.image_url,
            audio_url=finished.audio_output_url,
        )
    else:
        await _event(
            session_id,
            "job_failed",
            f"{label} が失敗しました (job {finished.id}): {finished.error or 'unknown'}",
            job_id=finished.id,
            task_id=task.id if task else None,
            error=finished.error,
        )
    return finished


async def _session_nsfw(session_id: str) -> bool:
    session = await load(session_id)
    return bool(session and session.nsfw)


async def execute_task(session_id: str, task: AgentTask) -> None:
    """Run one approved plan task, with a single automatic retry on failure."""
    task.status = "running"
    await _save_task(session_id, task)

    label = task.label or "タスク"
    try:
        payload = JobCreate(**task.job).model_copy(
            update={"chat_session_id": session_id}
        )
        # NSFW セッションのジョブは判定を待たずにフラグを継承する。
        job = await jobs.create_job(payload, inherit_nsfw=await _session_nsfw(session_id))
    except (jobs.JobValidationError, ValueError) as exc:
        task.status = "failed"
        task.error = str(exc)
        await _save_task(session_id, task)
        await _event(
            session_id, "task_failed", f"{label} を投入できませんでした: {exc}",
            task_id=task.id, error=str(exc),
        )
        return

    task.job_id = job.id
    await _save_task(session_id, task)
    finished = await _run_and_wait(session_id, job, label=label, task=task)

    if finished.status == "done":
        task.status = "done"
        task.error = None
    elif task.retries < MAX_TASK_RETRIES:
        task.retries += 1
        task.status = "pending"  # 自動リトライは 1 回まで
        task.error = finished.error
        await _event(
            session_id,
            "task_retry",
            f"{label} を 1 回だけ自動リトライします。",
            task_id=task.id,
        )
    else:
        task.status = "failed"
        task.error = finished.error
    await _save_task(session_id, task)


# --------------------------------------------------------------------------
# inspect (ffmpeg でのフレーム検分)
# --------------------------------------------------------------------------

async def extract_frames(
    video_path: Path, dest_dir: Path, interval: float = 1.0, limit: int = MAX_INSPECT_FRAMES
) -> list[Path]:
    """Split a video into at most ``limit`` frames (既定 1 秒間隔)。"""
    dest_dir.mkdir(parents=True, exist_ok=True)
    for old in dest_dir.glob("frame_*.png"):
        old.unlink(missing_ok=True)
    fps = 1.0 / max(interval, 0.1)
    cmd = [
        jobs.FFMPEG, "-y", "-i", str(video_path),
        "-vf", f"fps={fps:g}", "-frames:v", str(limit),
        str(dest_dir / "frame_%03d.png"),
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise jobs.JobError(f"ffmpeg が見つかりません: {exc}") from exc
    _, stderr = await proc.communicate()
    frames = sorted(dest_dir.glob("frame_*.png"))
    if not frames:
        detail = (stderr or b"").decode("utf-8", "replace").strip()[-300:]
        raise jobs.JobError(f"ffmpeg がフレームを抽出できませんでした: {detail}")
    return frames


def task_label_of_job(session: AgentSession | None, job_id: str) -> str:
    """そのジョブを生成したプランタスクの label（無ければ空文字）。"""
    if session is None:
        return ""
    for task in session.plan.tasks:
        if task.job_id == job_id:
            return task.label or ""
    return ""


async def _inspect(session_id: str, action: AgentAction) -> None:
    job_id = action.job_id or ""
    job = await jobs.get_job(job_id, include_workflow=False)
    # 記録は絶対パスなので、いまの ROOT に載せ替えてから読む（app.paths）。
    video = rebase_stored_path(job.video_path or "") if job else None
    if job is None or not job.video_path or video is None or not video.is_file():
        await _event(
            session_id,
            "inspect_failed",
            f"job {job_id} には検分できる動画がありません。",
            job_id=job_id,
        )
        return

    dest_dir = session_dir(session_id) / f"inspect_{job_id}"
    try:
        frames = await extract_frames(video, dest_dir, interval=action.interval)
    except jobs.JobError as exc:
        await _event(
            session_id, "inspect_failed", f"フレーム検分に失敗しました: {exc}",
            job_id=job_id, error=str(exc),
        )
        return

    last_frame = rebase_stored_path(job.last_frame_path or "")
    if job.last_frame_path and last_frame.is_file():
        last = dest_dir / "last_frame.png"
        last.write_bytes(last_frame.read_bytes())
        frames.append(last)

    workdir = session_dir(session_id)
    names = [str(f.relative_to(workdir)) for f in frames]
    # タイトルはタスクの label 基準（フロントは job_id ごとに 1 枚のカードへまとめる）
    label = task_label_of_job(await load(session_id), job_id) or f"job {job_id}"
    for index, name in enumerate(names, start=1):
        await add_artifact(
            session_id,
            AgentArtifact(
                kind="frame",
                title=f"{label} フレーム検分 {index}",
                ts=now(),
                name=name,
                url=f"/api/agent/sessions/{session_id}/artifacts/{name}",
                job_id=job_id,
            ),
        )
    await _event(
        session_id,
        "inspect_result",
        f"job {job_id} の動画を {action.interval:g} 秒間隔で分解しました。"
        "作業ディレクトリの次のフレーム画像を開いて品質を判断してください: "
        + ", ".join(names),
        job_id=job_id,
        frames=names,
    )


# --------------------------------------------------------------------------
# actions
# --------------------------------------------------------------------------

async def _note(session_id: str, action: AgentAction) -> None:
    workdir = session_dir(session_id)
    name = ""
    text = action.content.strip() or None
    if action.filename:
        candidate = (workdir / action.filename).resolve()
        if candidate.is_file() and workdir.resolve() in candidate.parents:
            name = str(candidate.relative_to(workdir.resolve()))
        elif text:
            # Grok が本文だけ返した場合はこちらでファイル化する
            candidate = workdir / Path(action.filename).name
            candidate.write_text(text, encoding="utf-8")
            name = candidate.name
    elif text:
        candidate = workdir / f"note_{len(list(workdir.glob('note_*.md'))) + 1}.md"
        candidate.write_text(text, encoding="utf-8")
        name = candidate.name

    await add_artifact(
        session_id,
        AgentArtifact(
            kind=action.kind,
            title=action.title or "メモ",
            ts=now(),
            name=name,
            url=(
                f"/api/agent/sessions/{session_id}/artifacts/{name}" if name else None
            ),
            text=text,
        ),
    )
    await _event(
        session_id, "note_saved", f"メモ「{action.title or 'メモ'}」を保存しました。",
        name=name,
    )


def _rename_targets(
    session: AgentSession, action: AgentAction
) -> list[AgentArtifact]:
    """rename の対象成果物（name 一致 / job_id[+kind] 一致）。"""
    if action.name:
        wanted = action.name
        base = Path(wanted).name
        return [
            artifact
            for artifact in session.artifacts
            if artifact.name and (artifact.name == wanted or Path(artifact.name).name == base)
        ]
    if not action.job_id:
        return []
    return [
        artifact
        for artifact in session.artifacts
        if artifact.job_id == action.job_id
        and (not action.artifact_kind or artifact.kind == action.artifact_kind)
    ]


async def _rename(session_id: str, action: AgentAction) -> None:
    """成果物のタイトルを付け直す（承認不要。AGENT-MODE §4）。"""
    session = await load(session_id)
    if session is None:
        return
    targets = _rename_targets(session, action)
    if not targets:
        where = action.name or f"job {action.job_id}"
        await _event(
            session_id,
            "action_failed",
            f"リネーム対象の成果物が見つかりません（{where}）。"
            "成果物の name か job_id を確認してください。",
            job_id=action.job_id,
        )
        return

    # フレーム検分のように複数枚ある場合は連番を添えて区別できるようにする
    numbered = len(targets) > 1
    for index, artifact in enumerate(targets, start=1):
        artifact.title = f"{action.title} {index}" if numbered else action.title
    await update(session_id, artifacts=session.artifacts)
    for artifact in targets:
        await ws.publish_agent(session_id, session.status, artifact=artifact)
    await _event(
        session_id,
        "artifact_renamed",
        f"成果物のタイトルを「{action.title}」に変更しました（{len(targets)} 件）。",
        job_id=action.job_id,
        title=action.title,
    )


async def _library(session_id: str, action: AgentAction) -> None:
    """ジョブの出力をライブラリに取っておく（承認不要。SPEC §7.2）。

    ライブラリはジョブを消しても残る棚なので、あとで素材として使い回せる。
    """
    job = await jobs.get_job(action.job_id or "", include_workflow=False)
    if job is None:
        await _event(
            session_id,
            "action_failed",
            f"ライブラリ登録の対象 job {action.job_id} が見つかりません。",
            job_id=action.job_id,
        )
        return
    try:
        item = await library.add_from_job(
            job, action.source or "", action.title, action.tags, action.category
        )
    except library.LibraryDuplicate as exc:
        # 二重登録はエラーではなく「もう棚にある」という案内にする。
        await _event(
            session_id,
            "library_exists",
            f"その出力は既にライブラリにあります（名前: 「{exc.item.name}」、"
            f"{exc.item.path}）。そのまま入力に使えます。",
            job_id=job.id,
            library_id=exc.item.id,
            path=exc.item.path,
        )
        return
    except library.LibraryError as exc:
        await _event(
            session_id,
            "action_failed",
            f"ライブラリに登録できませんでした: {exc}",
            job_id=action.job_id,
            error=str(exc),
        )
        return
    # 表示名とタグを明示しなかったぶんは Grok に考えさせる（SPEC §7.2）
    autotag.spawn_for(item, job, named=bool(action.title))
    tags = f" タグ: {', '.join(item.tags)}。" if item.tags else ""
    category = f" 分類: {item.category}。" if item.category else ""
    await _event(
        session_id,
        "library_added",
        f"「{item.name}」をライブラリに登録しました（{item.path}）。{tags}{category}"
        "以降のジョブの入力にこのパスを使えます。",
        job_id=job.id,
        library_id=item.id,
        path=item.path,
    )


#: library_search が 1 回で返す件数（続きは offset で辿らせる）
LIBRARY_SEARCH_LIMIT = 50


def _library_line(item) -> str:
    tags = f" [{', '.join(item.tags)}]" if item.tags else ""
    nsfw = " 🫣NSFW" if item.nsfw else ""
    # 分類は必ず出す。未分類のものは明示値（'none'）で書いておくと、そのまま
    # library_search の絞り込み条件として書き写せる（SPEC §7.2）
    category = item.category or library.UNCATEGORIZED
    return f"- `{item.path}` — 「{item.name}」（{item.kind} / {category}）{tags}{nsfw}"


def _library_search_text(
    items: list, total: int, offset: int, criteria: str
) -> str:
    """検索結果を、そのまま次のターンで読める 1 通のイベント本文にする。"""
    if not items:
        return (
            f"ライブラリ検索（{criteria}）: 該当なし（全 {total} 件中 {offset} 件目以降）。"
            "条件を緩めるか、CHOICES の一覧から選んでください。"
        )
    shown = offset + len(items)
    lines = [
        f"ライブラリ検索（{criteria}）: {total} 件中 {offset + 1}〜{shown} 件目。",
        "",
        *[_library_line(item) for item in items],
    ]
    if shown < total:
        lines += [
            "",
            f"まだ {total - shown} 件あります。続きは"
            f' `{{"action": "library_search", "offset": {shown}, …}}`'
            "（同じ絞り込み条件のまま）で取得してください。",
        ]
    return "\n".join(lines)


async def _library_search(session_id: str, action: AgentAction) -> None:
    """ライブラリを絞り込んで結果をイベントに残す（承認不要。SPEC §7.2）。

    CHOICES に焼き込めるのは種別ごとの新しい 50 件だけなので、それ以前のものや
    タグ・名前での絞り込みはこのアクションで取りに来てもらう。
    """
    try:
        items, total = await library.search_items(
            kind=action.library_kind,
            category=action.category,
            query=action.query,
            tag=action.tag,
            limit=LIBRARY_SEARCH_LIMIT,
            offset=action.offset,
        )
    except library.LibraryError as exc:
        await _event(
            session_id,
            "action_failed",
            f"ライブラリを検索できませんでした: {exc}",
            error=str(exc),
        )
        return
    criteria = ", ".join(
        part
        for part in (
            f"q={action.query!r}" if action.query else "",
            f"tag={action.tag!r}" if action.tag else "",
            f"kind={action.library_kind}" if action.library_kind else "",
            f"category={action.category}" if action.category else "",
        )
        if part
    ) or "絞り込みなし"
    await _event(
        session_id,
        "library_search_result",
        _library_search_text(items, total, action.offset, criteria),
        total=total,
        offset=action.offset,
        returned=len(items),
    )


async def _library_sheet(session_id: str, action: AgentAction) -> None:
    """ライブラリの画像素材を 1 枚のリファレンスシートに合成する（承認不要。SPEC §7.2）。

    `ltx2_3_ic_lora_image` が参照入力に取る「複数パネルを並べた 1 枚」を組み立てる。
    ``item_ids`` の**並び順に意味がある**（左上から詰め、``character`` の素材だけ
    大きいパネルになる）。出来上がったシートはふつうの素材として棚に残るので、
    そのまま次のジョブの `source_image` に書ける。
    """
    try:
        item = await library.add_sheet(
            action.item_ids,
            action.title,
            action.width or sheets.DEFAULT_WIDTH,
            action.height or sheets.DEFAULT_HEIGHT,
        )
    except library.LibraryError as exc:
        await _event(
            session_id,
            "action_failed",
            f"リファレンスシートを作れませんでした: {exc}",
            error=str(exc),
        )
        return
    await _event(
        session_id,
        "library_sheet_added",
        f"リファレンスシート「{item.name}」を作ってライブラリに登録しました"
        f"（id: {item.id}、{item.path}、URL: {item.url}）。"
        f"{len(action.item_ids)} 枚を指定の順に並べています。"
        " このパスを `ltx2_3_ic_lora_image` の `source_image` に指定してください。",
        library_id=item.id,
        path=item.path,
        url=item.url,
        item_ids=action.item_ids,
    )


# --------------------------------------------------------------------------
# ドラマスタジオ (app.studio) の操作
# --------------------------------------------------------------------------
#
# HTTP は経由せず :mod:`app.studio` のサービス層をそのまま呼ぶ（ルーターと同じ
# 入り口）。結果はイベント本文にまとめて次のターンでそのまま読めるようにし、
# 失敗は他のアクションと同じ ``action_failed`` に落とす。

#: リビジョン履歴に残す主体（人の操作と区別できるようにする）。キャンバスの
#: 盤面操作も同じプロジェクトの履歴に載るので、そちらでも使う
STUDIO_ACTOR = "agent"


def _studio_project_line(project) -> str:
    code = f"（作品コード `{project.code}`）" if project.code else ""
    return (
        f"- `{project.id}` 「{project.name}」{code} — Shot {project.shot_count} 件"
        f" / 素材 {project.asset_count} 件 / Take {project.take_count} 件"
        f"（採用済み {project.selected_take_count} 件）"
    )


#: 素材のリファレンスの役割 -> プロンプトに書く呼び名
_ASSET_FILE_LABELS = {"voice": "声", "video": "動画", "image": "画像"}


def _asset_files_text(asset) -> str:
    """素材にぶら下がるリファレンス（声サンプル・動画・追加画像）の 1 行。

    生成ワークフローには自動では流れないが、パスが分かればエージェントは
    自分で開いて確かめられる（人物の声や動きを掴むのに要る）。
    """
    files = getattr(asset, "files", None) or []
    if not files:
        return ""
    parts = [
        f"{_ASSET_FILE_LABELS.get(item.role, item.role)} `{item.path}`"
        + (f"（{item.caption}）" if item.caption else "")
        for item in files
    ]
    return " 参照: " + " / ".join(parts)


def _studio_asset_line(asset) -> str:
    where = (
        f"ファイルあり `{asset.path}`"
        if asset.path
        else "メタデータのみ（参照には添付されず説明文に展開されます）"
    )
    caption = asset.caption or asset.prompt_caption
    return (
        f"- `{asset.id}` `@{asset.name}`（{asset.category} / {asset.kind}、{where}）"
        + (f" — {caption}" if caption else "")
        + _asset_files_text(asset)
    )


def _studio_take_line(take: StudioTake) -> str:
    marks = []
    if take.stale:
        marks.append("stale: " + " / ".join(take.stale_reasons))
    if take.warning:
        marks.append(take.warning)
    if take.error:
        marks.append(f"error: {take.error}")
    video = f" 動画 {take.video_url}" if take.video_url else ""
    return (
        f"Take `{take.id}` {take.status}"
        f"（job `{take.job_id}` {take.job_status or '不明'}"
        f"{', ' + take.video_workflow if take.video_workflow else ''}）{video}"
        + (" ⚠ " + " / ".join(marks) if marks else "")
    )


def _studio_shot_lines(shot, takes: list[StudioTake], indent: str) -> list[str]:
    carry = "、前 Shot のラストフレームを引き継ぐ" if shot.carry_over_end_frame else ""
    lines = [
        f"{indent}- Shot `{shot.id}`「{shot.title}」"
        f"（{shot.duration_seconds:g} 秒 / status {shot.status}"
        f" / workflow {shot.workflow_override or '自動'}{carry}）"
    ]
    for label, value in (
        ("prompt", shot.prompt),
        ("台詞", shot.dialogue),
        ("SE", shot.soundscape),
        ("BGM", shot.bgm),
        ("カメラ", shot.camera),
    ):
        if value:
            lines.append(f"{indent}  {label}: {value}")
    for take in takes:
        selected = " ← 採用中" if shot.selected_take_id == take.id else ""
        lines.append(f"{indent}  {_studio_take_line(take)}{selected}")
    return lines


def _studio_detail_text(detail: StudioProjectDetail) -> str:
    """プロジェクト 1 本ぶんを、次のターンでそのまま読める本文にする。"""
    code = f"（作品コード `{detail.code}`）" if detail.code else ""
    translate = (
        "on（日本語で書けば投入時に英語へ直されます）"
        if detail.auto_translate
        else "off（プロンプトは英語で書いてください）"
    )
    lines = [
        f"プロジェクト `{detail.id}` 「{detail.name}」{code} / auto_translate: {translate}",
    ]
    if detail.synopsis:
        lines.append(f"あらすじ: {detail.synopsis}")
    if detail.world_notes:
        lines.append(f"World Bible: {detail.world_notes}")

    lines += ["", f"素材 {len(detail.assets)} 件（プロンプトからは `@名前` で呼びます）:"]
    lines += [_studio_asset_line(asset) for asset in detail.assets] or ["- (なし)"]

    takes: dict[str, list[StudioTake]] = {}
    for take in detail.takes:
        takes.setdefault(take.shot_id, []).append(take)
    shots: dict[str | None, list] = {}
    for shot in detail.shots:
        shots.setdefault(shot.scene_id, []).append(shot)

    lines += ["", f"構成（Shot {len(detail.shots)} 件）:"]
    if not detail.episodes and not detail.shots:
        lines.append("- (なし)")
    for episode in detail.episodes:
        lines.append(f"- 話 `{episode.id}`「{episode.title}」{episode.synopsis}".rstrip())
        scenes = [s for s in detail.scenes if s.episode_id == episode.id]
        if not scenes:
            lines.append("  - (場なし)")
        for scene in scenes:
            time_of_day = f"（{scene.time_of_day}）" if scene.time_of_day else ""
            lines.append(f"  - 場 `{scene.id}`「{scene.title}」{time_of_day}")
            for shot in shots.get(scene.id, []):
                lines += _studio_shot_lines(shot, takes.get(shot.id, []), "    ")
    orphans = shots.get(None, [])
    if orphans:
        lines.append("- 場に属さない Shot:")
        for shot in orphans:
            lines += _studio_shot_lines(shot, takes.get(shot.id, []), "  ")
    return "\n".join(lines)


async def _studio_list_projects(params: dict[str, Any]) -> tuple[str, str, dict]:
    projects = await studio.list_projects()
    if not projects:
        return (
            "studio_projects",
            "スタジオにはまだプロジェクトがありません。"
            "`studio_create_project` で作れます。",
            {"count": 0},
        )
    text = "\n".join(
        [
            "スタジオのプロジェクト:",
            "",
            *[_studio_project_line(project) for project in projects],
        ]
    )
    return (
        "studio_projects",
        text,
        {"count": len(projects), "project_ids": [p.id for p in projects]},
    )


async def _studio_get_project(params: dict[str, Any]) -> tuple[str, str, dict]:
    detail = await studio.project_detail(params["project_id"])
    if detail is None:
        raise studio.StudioError(
            f"プロジェクト `{params['project_id']}` が見つかりません"
        )
    return "studio_project", _studio_detail_text(detail), {"project_id": detail.id}


async def _studio_create_project(params: dict[str, Any]) -> tuple[str, str, dict]:
    body = params["body"]
    project = await studio.create_project(
        str(body.get("name") or ""),
        str(body.get("code") or ""),
        str(body.get("synopsis") or ""),
        str(body.get("world_notes") or ""),
        bool(body.get("auto_translate", True)),
        actor=STUDIO_ACTOR,
    )
    return (
        "studio_saved",
        f"プロジェクト「{project.name}」を作りました（project_id `{project.id}`）。"
        "話 / 場 / Shot と World Bible の素材をこの id にぶら下げてください。",
        {"project_id": project.id},
    )


async def _studio_update_project(params: dict[str, Any]) -> tuple[str, str, dict]:
    project = await studio.update_project(
        params["project_id"], actor=STUDIO_ACTOR, **params["body"]
    )
    if project is None:
        raise studio.StudioError(
            f"プロジェクト `{params['project_id']}` が見つかりません"
        )
    return (
        "studio_saved",
        f"プロジェクト「{project.name}」を更新しました（project_id `{project.id}`）。",
        {"project_id": project.id},
    )


async def _studio_upsert_episode(params: dict[str, Any]) -> tuple[str, str, dict]:
    if params.get("id"):
        episode = await studio.update_episode(
            params["id"], actor=STUDIO_ACTOR, **params["body"]
        )
        if episode is None:
            raise studio.StudioError(f"話 `{params['id']}` が見つかりません")
        verb = "更新"
    else:
        episode = await studio.create_episode(
            params["project_id"],
            StudioEpisodeCreate(**params["body"]),
            actor=STUDIO_ACTOR,
        )
        verb = "追加"
    return (
        "studio_saved",
        f"話「{episode.title}」を{verb}しました（episode_id `{episode.id}`）。",
        {"project_id": episode.project_id, "episode_id": episode.id},
    )


async def _studio_upsert_scene(params: dict[str, Any]) -> tuple[str, str, dict]:
    if params.get("id"):
        scene = await studio.update_scene(
            params["id"], actor=STUDIO_ACTOR, **params["body"]
        )
        if scene is None:
            raise studio.StudioError(f"場 `{params['id']}` が見つかりません")
        verb = "更新"
    else:
        scene = await studio.create_scene(
            params["episode_id"],
            StudioSceneCreate(**params["body"]),
            actor=STUDIO_ACTOR,
        )
        verb = "追加"
    return (
        "studio_saved",
        f"場「{scene.title}」を{verb}しました（scene_id `{scene.id}`）。",
        {"project_id": scene.project_id, "scene_id": scene.id},
    )


async def _studio_upsert_shot(params: dict[str, Any]) -> tuple[str, str, dict]:
    if params.get("id"):
        shot = await studio.update_shot(
            params["id"],
            StudioShotUpdate(**params["body"]).changes(),
            actor=STUDIO_ACTOR,
        )
        if shot is None:
            raise studio.StudioError(f"Shot `{params['id']}` が見つかりません")
        verb = "更新"
    else:
        shot = await studio.create_shot(
            params["project_id"],
            StudioShotCreate(**params["body"]),
            actor=STUDIO_ACTOR,
        )
        verb = "追加"
    return (
        "studio_saved",
        f"Shot「{shot.title}」を{verb}しました（shot_id `{shot.id}`）。"
        "`studio_render_shot` で生成できます。",
        {"project_id": shot.project_id, "shot_id": shot.id},
    )


async def _studio_delete_shot(params: dict[str, Any]) -> tuple[str, str, dict]:
    if not await studio.delete_shot(params["id"], actor=STUDIO_ACTOR):
        raise studio.StudioError(f"Shot `{params['id']}` が見つかりません")
    return (
        "studio_saved",
        f"Shot `{params['id']}` を削除しました（Take も一緒に消えます）。",
        {"shot_id": params["id"]},
    )


async def _studio_upsert_asset(params: dict[str, Any]) -> tuple[str, str, dict]:
    if params.get("id"):
        asset = await studio.update_asset(
            params["id"], actor=STUDIO_ACTOR, **params["body"]
        )
        if asset is None:
            raise studio.StudioError(f"素材 `{params['id']}` が見つかりません")
        verb = "更新"
    else:
        payload = StudioAssetCreate(**params["body"])
        asset = await studio.add_asset(
            params["project_id"],
            name=payload.name,
            kind=payload.kind,
            path=payload.path,
            category=payload.category,
            caption=payload.caption,
            prompt_caption=payload.prompt_caption,
            locked=payload.locked,
            sort_order=payload.sort_order,
            actor=STUDIO_ACTOR,
        )
        verb = "登録"
    where = (
        f"ファイルは {asset.path} に置きました。"
        if asset.path
        else "ファイルなし（メタデータのみ）の素材です。"
    )
    return (
        "studio_saved",
        f"素材「{asset.name}」を{verb}しました（asset_id `{asset.id}`）。{where}"
        f"Shot の本文からは `@{asset.name}` で呼べます。",
        {"project_id": asset.project_id, "asset_id": asset.id},
    )


async def _studio_register_asset_from_job(
    params: dict[str, Any]
) -> tuple[str, str, dict]:
    """自分で生成した成果物を World Bible の素材として登録する。"""
    job = await jobs.get_job(params["job_id"], include_workflow=False)
    if job is None:
        raise studio.StudioError(f"job `{params['job_id']}` が見つかりません")
    attribute, kind, label = library.SOURCES[params["source"]]
    path = getattr(job, attribute, None)
    if not path:
        raise studio.StudioError(f"job `{job.id}` には{label}がありません")
    try:
        dest = jobs.copy_into_assets(path, kind)
    except jobs.JobValidationError as exc:
        raise studio.StudioError(str(exc)) from exc
    payload = StudioAssetCreate(**params["body"])
    asset = await studio.add_asset(
        params["project_id"],
        name=payload.name,
        kind=kind,
        path=str(dest),
        category=payload.category,
        caption=payload.caption,
        prompt_caption=payload.prompt_caption,
        locked=payload.locked,
        sort_order=payload.sort_order,
        actor=STUDIO_ACTOR,
    )
    return (
        "studio_saved",
        f"job `{job.id}` の{label}を素材「{asset.name}」として登録しました"
        f"（asset_id `{asset.id}`、{asset.path}）。"
        f"Shot の本文で `@{asset.name}` と書けば参照素材として添付されます。",
        {"project_id": asset.project_id, "asset_id": asset.id, "job_id": job.id},
    )


async def _studio_render_shot(params: dict[str, Any]) -> tuple[str, str, dict]:
    shot_id = params["shot_id"]
    override = params.get("workflow_override")
    if override:
        # 強制指定は Shot に残す（次の Take も同じモードで作れるようにする）
        if await studio.update_shot(
            shot_id, {"workflow_override": override}, actor=STUDIO_ACTOR
        ) is None:
            raise studio.StudioError(f"Shot `{shot_id}` が見つかりません")
    take = await studio.render_shot(shot_id)
    text = (
        f"Shot `{shot_id}` の生成を投入しました"
        f"（take_id `{take.id}` / job_id `{take.job_id}`）。"
        "完了は `studio_get_takes` で確認してください"
        "（status が candidate になれば見られます）。"
    )
    if take.warning:
        text += f" 注意: {take.warning}"
    return (
        "studio_render_started",
        text,
        {"shot_id": shot_id, "take_id": take.id, "job_id": take.job_id},
    )


async def _studio_get_takes(params: dict[str, Any]) -> tuple[str, str, dict]:
    shot_id = params["shot_id"]
    shot = await studio.get_shot(shot_id)
    if shot is None:
        raise studio.StudioError(f"Shot `{shot_id}` が見つかりません")
    takes = await studio.list_takes(shot_id)
    if not takes:
        text = (
            f"Shot `{shot_id}`「{shot.title}」にはまだ Take がありません。"
            "`studio_render_shot` で生成できます。"
        )
    else:
        text = "\n".join(
            [
                f"Shot `{shot_id}`「{shot.title}」の Take:",
                "",
                *[f"- {_studio_take_line(take)}" for take in takes],
            ]
        )
    return (
        "studio_takes",
        text,
        {
            "shot_id": shot_id,
            "count": len(takes),
            "take_ids": [take.id for take in takes],
        },
    )


async def _studio_select_take(params: dict[str, Any]) -> tuple[str, str, dict]:
    take = await studio.select_take(params["take_id"], actor=STUDIO_ACTOR)
    if take is None:
        raise studio.StudioError(f"Take `{params['take_id']}` が見つかりません")
    return (
        "studio_take_selected",
        f"Take `{take.id}` を採用しました（Shot `{take.shot_id}`）。"
        "次の Shot は `carry_over_end_frame` でこのラストフレームを引き継げます。",
        {"take_id": take.id, "shot_id": take.shot_id},
    )


async def _studio_reject_take(params: dict[str, Any]) -> tuple[str, str, dict]:
    take = await studio.reject_take(params["take_id"], actor=STUDIO_ACTOR)
    if take is None:
        raise studio.StudioError(f"Take `{params['take_id']}` が見つかりません")
    return (
        "studio_take_rejected",
        f"Take `{take.id}` を不採用にしました（Shot `{take.shot_id}`）。"
        "作り直すなら `studio_render_shot` をもう一度投入してください。",
        {"take_id": take.id, "shot_id": take.shot_id},
    )


_STUDIO_HANDLERS = {
    "studio_list_projects": _studio_list_projects,
    "studio_get_project": _studio_get_project,
    "studio_create_project": _studio_create_project,
    "studio_update_project": _studio_update_project,
    "studio_upsert_episode": _studio_upsert_episode,
    "studio_upsert_scene": _studio_upsert_scene,
    "studio_upsert_shot": _studio_upsert_shot,
    "studio_delete_shot": _studio_delete_shot,
    "studio_upsert_asset": _studio_upsert_asset,
    "studio_register_asset_from_job": _studio_register_asset_from_job,
    "studio_render_shot": _studio_render_shot,
    "studio_get_takes": _studio_get_takes,
    "studio_select_take": _studio_select_take,
    "studio_reject_take": _studio_reject_take,
}


# --------------------------------------------------------------------------
# キャンバス (app.canvas) の操作
# --------------------------------------------------------------------------
#
# スタジオ操作と同じ流儀（サービス層を直に呼び、結果を次のターンで読める本文に
# する）。カードが持つのは参照と座標だけなので、ここも「置く・動かす・数える」
# しかできない: 中身を直すのは studio_* の仕事で、例外は中身を自分で持つ
# text / model カードの ``data`` だけ。

def _card_entity_label(card, detail: StudioProjectDetail | None) -> str:
    """カードが指しているスタジオの行の呼び名（見つからなければその旨）。"""
    if card.kind in ("text", "model"):
        return "キャンバス専用（参照先なし）"
    if detail is None or not card.entity_id:
        return "参照先なし"
    for asset in detail.assets:
        if asset.id == card.entity_id:
            return f"素材 `{asset.id}` `@{asset.name}`"
    for scene in detail.scenes:
        if scene.id == card.entity_id:
            return f"場 `{scene.id}`「{scene.title}」"
    for shot in detail.shots:
        if shot.id == card.entity_id:
            return f"Shot `{shot.id}`「{shot.title}」"
    for take in detail.takes:
        if take.id == card.entity_id:
            return f"Take `{take.id}`（{take.status}）"
    return f"参照先 `{card.entity_id}` は見つかりません"


def _card_line(card, detail: StudioProjectDetail | None, tab: str = "") -> str:
    line = (
        f"- カード `{card.id}` [{card.kind}] 位置 ({card.x:g}, {card.y:g})"
        f" 大きさ {card.w:g}×{card.h:g} — {_card_entity_label(card, detail)}"
    )
    if tab:
        line += f" — タブ: {tab}"
    if card.kind == "text":
        body = str(card.data.get("body") or "").strip().splitlines()
        return line + (f" — {body[0]}" if body else "")
    if card.kind == "model":
        return line + f" — {card.data.get('workflow') or 'ワークフロー未選択'}"
    return line


#: 「作品共通」タブの呼び名（素材と未分類のカットが出る盤面）
COMMON_TAB_LABEL = "作品共通"


def canvas_tab_label(episode_id: str | None, detail: StudioProjectDetail | None) -> str:
    """タブの呼び名（``None`` = 作品共通、話は「第 n 話」かそのタイトル）。"""
    if episode_id is None:
        return COMMON_TAB_LABEL
    episodes = detail.episodes if detail else []
    for number, episode in enumerate(episodes, start=1):
        if episode.id == episode_id:
            return episode.title or f"第 {number} 話"
    return f"話 `{episode_id}`"


def canvas_board_text(
    cards: list,
    detail: StudioProjectDetail | None,
    *,
    tab: str | None = None,
    whole_board: bool = False,
) -> str:
    """盤面に出ているカードを、次のターンでそのまま読める本文にする。

    ``whole_board`` なら作品ぜんぶのカードを、行ごとにどのタブのものかを
    添えて並べる。そうでなければ ``cards`` は ``tab``（``None`` = 作品共通）の
    ぶんだけが渡ってきているものとして、そのタブの盤面として書く。
    """
    label = canvas_tab_label(tab, detail)
    if not cards:
        return (
            f"キャンバスの「{label}」タブにはまだカードがありません。"
            if not whole_board
            else "キャンバスにはまだカードがありません"
            "（この作品にはまだ素材も脚本もありません）。"
        )
    index = (
        canvas.entity_episodes(detail.scenes, detail.shots, detail.takes)
        if detail
        else {}
    )
    lines = [
        _card_line(
            card,
            detail,
            canvas_tab_label(canvas.card_episode(card, index), detail)
            if whole_board
            else "",
        )
        for card in cards
    ]
    head = (
        f"キャンバスのカード {len(cards)} 枚:"
        if whole_board
        else f"キャンバス「{label}」タブのカード {len(cards)} 枚:"
    )
    return "\n".join([head, "", *lines])


def canvas_tabs_text(detail: StudioProjectDetail | None, tab: str | None) -> str:
    """タブの一覧（どれを開いているか + 各タブの中身の数）。"""
    if detail is None:
        return ""
    index = canvas.entity_episodes(detail.scenes, detail.shots, detail.takes)
    loose = [shot for shot in detail.shots if index.get(shot.id) is None]
    rows = [
        (
            None,
            COMMON_TAB_LABEL,
            f"素材 {len(detail.assets)} 件 / 未分類のカット {len(loose)} 件",
        )
    ]
    for number, episode in enumerate(detail.episodes, start=1):
        scenes = [
            scene for scene in detail.scenes if scene.episode_id == episode.id
        ]
        shots = [
            shot for shot in detail.shots if index.get(shot.id) == episode.id
        ]
        rows.append(
            (
                episode.id,
                episode.title or f"第 {number} 話",
                f"場 {len(scenes)} 件 / カット {len(shots)} 件",
            )
        )
    lines = [
        f"- {'▶ ' if episode_id == tab else ''}「{label}」"
        + (f"（`{episode_id}`）" if episode_id else "（`common`）")
        + f": {summary}"
        for episode_id, label, summary in rows
    ]
    return "\n".join(["キャンバスのタブ（▶ = いま開いているタブ）:", "", *lines])


async def _canvas_list_cards(params: dict[str, Any]) -> tuple[str, str, dict]:
    project_id = params["project_id"]
    detail = await studio.project_detail(project_id)
    if detail is None:
        raise studio.StudioError(f"プロジェクト `{project_id}` が見つかりません")
    # episode_id を省いたら盤面ぜんぶ（タブ名つき）。指定すればそのタブだけ。
    wanted = params.get("episode_id")
    if wanted is None:
        cards = await canvas.list_cards(project_id)
        text = canvas_board_text(cards, detail, whole_board=True)
    else:
        tab = canvas.tab_of(str(wanted))
        cards = await canvas.list_tab_cards(project_id, tab)
        text = canvas_board_text(cards, detail, tab=tab)
    return (
        "canvas_cards",
        text,
        {"project_id": project_id, "count": len(cards),
         "card_ids": [card.id for card in cards]},
    )


async def _canvas_place_card(params: dict[str, Any]) -> tuple[str, str, dict]:
    card = await canvas.create_card(
        params["project_id"],
        CanvasCardCreate(**params["body"]),
        actor=STUDIO_ACTOR,
    )
    return (
        "canvas_card_placed",
        f"カード `{card.id}` [{card.kind}] を ({card.x:g}, {card.y:g}) に置きました。",
        {"project_id": card.project_id, "card_id": card.id, "kind": card.kind},
    )


async def _canvas_move_card(params: dict[str, Any]) -> tuple[str, str, dict]:
    body = params["body"]
    card = await canvas.move_card(
        params["card_id"],
        body["x"],
        body["y"],
        body.get("w"),
        body.get("h"),
        body.get("z"),
    )
    if card is None:
        raise canvas.CanvasError(f"カード `{params['card_id']}` が見つかりません")
    return (
        "canvas_card_moved",
        f"カード `{card.id}` を ({card.x:g}, {card.y:g}) へ動かしました。",
        {"project_id": card.project_id, "card_id": card.id},
    )


async def _canvas_update_card(params: dict[str, Any]) -> tuple[str, str, dict]:
    card = await canvas.update_card(
        params["card_id"], dict(params["body"]), actor=STUDIO_ACTOR
    )
    if card is None:
        raise canvas.CanvasError(f"カード `{params['card_id']}` が見つかりません")
    return (
        "canvas_card_updated",
        f"カード `{card.id}` [{card.kind}] を更新しました。",
        {"project_id": card.project_id, "card_id": card.id},
    )


_CANVAS_HANDLERS = {
    "canvas_list_cards": _canvas_list_cards,
    "canvas_place_card": _canvas_place_card,
    "canvas_move_card": _canvas_move_card,
    "canvas_update_card": _canvas_update_card,
}

#: 目録の読み書きだけで完結するツール（スタジオ + キャンバス）。実行は
#: :func:`run_tool` に集約してあり、キャンバスのチャット（:mod:`app.canvas_agent`）
#: も同じ入り口を使う。
TOOL_HANDLERS: dict[str, Any] = {**_STUDIO_HANDLERS, **_CANVAS_HANDLERS}

#: ツールの失敗が返すイベント種別（本文にそのまま理由が入る）
TOOL_FAILED = "action_failed"


async def run_tool(action: AgentAction) -> tuple[str, str, dict]:
    """studio_* / canvas_* を 1 つ実行し、``(イベント種別, 本文, data)`` を返す。

    失敗も例外にせず :data:`TOOL_FAILED` のイベントとして返す（次のターンで
    エージェントが読んで直せるようにするため。制作記録に残すのは呼び出し側）。
    """
    handler = TOOL_HANDLERS[action.action]
    params = action.canvas if action.action in _CANVAS_HANDLERS else action.studio
    try:
        return await handler(params)
    except (
        studio.StudioError,
        canvas.CanvasError,
        jobs.JobValidationError,
        ValidationError,
    ) as exc:
        return (
            TOOL_FAILED,
            f"{action.action} を実行できませんでした: {exc}",
            {"error": str(exc)},
        )


async def _tool_action(session_id: str, action: AgentAction) -> None:
    """ツールを 1 つ実行し、結果をイベントとして制作記録に残す。"""
    kind, text, data = await run_tool(action)
    await _event(session_id, kind, text, **data)


async def _apply_plan(session_id: str, action: AgentAction) -> None:
    session = await load(session_id)
    if session is None:
        return
    session.plan.version += 1
    session.plan.notes = action.notes
    session.plan.approved = False
    session.plan.tasks = action.tasks
    await update(session_id, plan=session.plan, status="planning")
    await add_artifact(
        session_id,
        AgentArtifact(
            kind="plan",
            title=f"プラン v{session.plan.version}",
            ts=now(),
            text=action.notes,
        ),
    )
    await _event(
        session_id,
        "plan_proposed",
        f"プラン v{session.plan.version}（{len(action.tasks)} 件）を提示しました。"
        "承認されるまで生成は開始しません。",
        version=session.plan.version,
    )
    await ws.publish_agent(session_id, "planning", message="plan proposed")


async def _checkin(
    session_id: str,
    question: str,
    options: list[str],
    *,
    kind: str = "checkin",
    data: dict[str, Any] | None = None,
) -> None:
    await append_message(
        session_id,
        AgentMessage(
            role="checkin",
            kind=kind,
            content=question,
            ts=now(),
            data={"options": options, **(data or {})},
        ),
    )
    await _set_status(session_id, "waiting_checkin", message=question)


# --------------------------------------------------------------------------
# プラン外アクションの承認ゲート (AGENT-MODE §2 / §7)
# --------------------------------------------------------------------------

APPROVAL_OPTIONS = ["実行する", "やめる"]
_APPROVE_WORDS = (
    "実行する", "実行", "はい", "ok", "okay", "yes", "承認", "お願い",
    "進める", "進めて", "どうぞ", "いいよ", "やって",
    "続ける", "続けて", "続行",
)
_DECLINE_WORDS = (
    "やめる", "やめて", "いいえ", "no", "中止", "キャンセル", "しない",
    "止める", "スキップ", "不要",
)


def _action_label(action: AgentAction) -> str:
    return "続き生成" if action.action == "continue" else "再生成"


def last_open_checkin(session: AgentSession) -> int | None:
    """まだ応答されていない最後のチェックイン（種別は問わない）の位置。"""
    for index in range(len(session.messages) - 1, -1, -1):
        message = session.messages[index]
        if message.role != "checkin":
            continue
        return None if message.data.get("resolved") else index
    return None


def pending_approval(session: AgentSession) -> tuple[int, AgentAction] | None:
    """承認待ちで保留しているアクション（セッション JSON に永続化済み）。

    プラン外の continue / rerun（``approval``）と、生成本数の上限を超える直前で
    止めたアクション（``limit``）の両方が対象。
    """
    index = last_open_checkin(session)
    if index is None:
        return None
    message = session.messages[index]
    if message.kind not in ("approval", "limit"):
        return None
    raw = message.data.get("action")
    if not isinstance(raw, dict):
        return None
    try:
        return index, AgentAction(**raw)
    except ValidationError:
        return None


def _is_approval(answer: str) -> bool:
    """肯定的な回答だけを承認とみなす（判断できない返答は実行しない）。"""
    text = answer.strip().lower()
    if any(word in text for word in _DECLINE_WORDS):
        return False
    return any(word in text for word in _APPROVE_WORDS)


async def _request_approval(session_id: str, action: AgentAction) -> None:
    """プラン外の continue / rerun は実行前に承認待ちチェックインを立てる。"""
    label = _action_label(action)
    await _event(
        session_id,
        "approval_required",
        f"プラン外の{label}（job {action.job_id}）は承認が必要です。"
        "ユーザーの回答を待っています。",
        job_id=action.job_id,
    )
    await _checkin(
        session_id,
        f"プラン外の{label}をリクエストしています（対象 job {action.job_id}）。"
        "実行してよいですか？",
        APPROVAL_OPTIONS,
        kind="approval",
        data={"action": action.model_dump(mode="json")},
    )


async def resolve_checkin(session_id: str, answer: str) -> AgentAction | None:
    """チェックイン応答を処理し、承認されたプラン外アクションを返す。"""
    session = await load(session_id)
    if session is None:
        return None
    index = last_open_checkin(session)
    if index is None:
        return None
    found = pending_approval(session)
    message = session.messages[index]
    approved = _is_approval(answer)
    # 応答済みマークは種別を問わず付ける（フロントの「応答済み」判定の根拠）。
    message.data["resolved"] = True
    if message.kind == "limit":
        # 上限の延長は承認済みチェックインの本数だけで決まる（唯一の情報源）。
        message.data["approved"] = approved
    await update(session_id, messages=session.messages)
    if message.kind == "limit" and not approved:
        await _halt(session_id, _limit_stopped(session))
        return None
    if found is None:
        return None
    _, action = found

    if approved:
        action.approved = True
        return action
    label = _action_label(action)
    await _event(
        session_id,
        "action_skipped",
        f"ユーザーの判断で{label}（job {action.job_id}）は実行しませんでした。"
        "別の手を検討してください。",
        job_id=action.job_id,
    )
    return None


async def apply_action(session_id: str, action: AgentAction) -> bool:
    """Execute one action. Returns True when the loop must pause / stop."""
    if action.action == "plan":
        await _apply_plan(session_id, action)
        return True
    if action.action == "checkin":
        await _checkin(session_id, action.question, action.options)
        return True
    if action.action == "done":
        summary = action.summary or action.notes or "作業を完了しました。"
        await _event(session_id, "done", summary)
        await _set_status(session_id, "done", message=summary)
        return True
    if action.action == "note":
        await _note(session_id, action)
        return False
    if action.action == "rename":
        await _rename(session_id, action)
        return False
    if action.action == "library":
        await _library(session_id, action)
        return False
    if action.action == "library_search":
        await _library_search(session_id, action)
        return False
    if action.action == "library_sheet":
        await _library_sheet(session_id, action)
        return False
    if action.action == "inspect":
        await _inspect(session_id, action)
        return False
    if action.action in agent_protocol.STUDIO_ACTIONS + agent_protocol.CANVAS_ACTIONS:
        # スタジオ / キャンバスの操作は目録の読み書き（+ 生成の投入）だけで、
        # 完了は待たない。
        await _tool_action(session_id, action)
        return False
    if action.action in ("continue", "rerun"):
        session = await load(session_id)
        # 自走モードだけ即実行（auto_limit で保護済み）。他は承認必須（§2）。
        auto = session is not None and session.checkin_mode == "auto"
        if not action.approved and not auto:
            await _request_approval(session_id, action)
            return True
        if session is not None and over_limit(session):
            # 承認済みでも上限を超える 1 本目は必ず確認を挟む。
            await _request_limit_checkin(session_id, session, action)
            return True
        await _continue_or_rerun(session_id, action)
        return False
    # run_task: 次の pending タスクはループが拾う
    return False


async def _continue_or_rerun(session_id: str, action: AgentAction) -> None:
    """既存 jobs.py の continue / rerun をルーターを介さず内部呼び出しする。"""
    kind = action.action
    label = "続き生成" if kind == "continue" else "再生成"
    inherit = await _session_nsfw(session_id)
    try:
        if kind == "continue":
            payload = JobContinue(
                **action.overrides, chat_session_id=session_id
            )
            job = await jobs.continue_job(
                action.job_id or "", payload, inherit_nsfw=inherit
            )
        else:
            job = await jobs.rerun_job(
                action.job_id or "", JobRerun(**action.overrides), inherit_nsfw=inherit
            )
    except LookupError:
        await _event(
            session_id, "action_failed",
            f"{label}の対象 job {action.job_id} が見つかりません。",
            job_id=action.job_id,
        )
        return
    except (jobs.JobValidationError, ValueError) as exc:
        await _event(
            session_id, "action_failed", f"{label}を開始できませんでした: {exc}",
            job_id=action.job_id, error=str(exc),
        )
        return
    await _run_and_wait(session_id, job, label=label)


# --------------------------------------------------------------------------
# loop
# --------------------------------------------------------------------------

LIMIT_OPTIONS = ["続ける", "止める"]


def limit_grants(session: AgentSession) -> int:
    """ユーザーが「上限を超えて続ける」を承認した回数。"""
    return sum(
        1
        for m in session.messages
        if m.role == "checkin" and m.kind == "limit" and m.data.get("approved") is True
    )


def effective_limit(session: AgentSession) -> int:
    """いま許されている生成本数。承認 1 回につき作成時の設定値ぶん伸びる。"""
    return session.auto_limit * (limit_grants(session) + 1)


def over_limit(session: AgentSession) -> bool:
    return generated_count(session) >= effective_limit(session)


def _limit_stopped(session: AgentSession) -> str:
    return f"生成本数が上限（{effective_limit(session)} 本）に達したため停止しました。"


async def _request_limit_checkin(
    session_id: str, session: AgentSession, action: AgentAction | None = None
) -> None:
    """上限を超える直前でユーザーに続行を確認する（停止はしない）。

    ``action`` を渡すと、承認されたときにそのアクションをそのまま再開できる
    （プラン外の continue / rerun 用）。プランのタスクはループが拾い直す。
    """
    limit = effective_limit(session)
    await _event(
        session_id,
        "limit_reached",
        f"生成本数が設定上限（{limit} 本）に達しました。"
        "続行してよいかユーザーに確認しています。",
        limit=limit,
        generated=generated_count(session),
    )
    await _checkin(
        session_id,
        f"生成本数が設定上限（{limit} 本）に達しました。このまま生成を続けますか？"
        f"（続ける場合、次はあと {session.auto_limit} 本ぶん進めてまた確認します）",
        LIMIT_OPTIONS,
        kind="limit",
        data={"action": action.model_dump(mode="json")} if action else {},
    )


def _stopping(session_id: str) -> bool:
    """停止要求を 1 度だけ消費する。"""
    if session_id not in _stop_requests:
        return False
    _stop_requests.discard(session_id)
    return True


async def _halt(session_id: str, reason: str) -> None:
    await _event(session_id, "stopped", reason)
    await _set_status(session_id, "stopped", message=reason)


async def _loop(session_id: str, action: AgentAction | None = None) -> None:
    while True:
        session = await load(session_id)
        if session is None or session.status != "running":
            return
        if _stopping(session_id):
            await _halt(session_id, "ユーザーの操作で停止しました。")
            return

        executed = False
        if action is None:
            task = next_task(session)
            if task is not None:
                if over_limit(session):
                    # 上限で打ち切らず、ユーザーに続行を確認する（承認で枠が伸びる）。
                    await _request_limit_checkin(session_id, session)
                    return
                await execute_task(session_id, task)
                executed = True
                session = await load(session_id)
                if session is None:
                    return
                if _stopping(session_id):
                    await _halt(session_id, "ユーザーの操作で停止しました。")
                    return
            if _turns(session) >= MAX_TURNS:
                await _halt(
                    session_id,
                    f"連続ターンが上限（{MAX_TURNS}）に達したため停止しました。",
                )
                return
            try:
                _, action = await run_turn(session_id)
            except grok.LLMError as exc:
                await _halt(session_id, f"Grok の呼び出しに失敗しました: {exc}")
                return
            # ターン中に停止を押されたら、返ってきたアクションは実行しない
            # （新しいジョブを投入してしまわないため）。
            if _stopping(session_id):
                await _halt(session_id, "ユーザーの操作で停止しました。")
                return

        if action is not None:
            paused = await apply_action(session_id, action)
            action = None
            if paused:
                return
        elif not executed and next_task(session) is None:
            await _set_status(session_id, "idle", message="待機中")
            return
        else:
            action = None

        if executed and session.checkin_mode == "every_job":
            await _checkin(
                session_id,
                "1 本完了しました。このまま次のタスクに進めますか？",
                ["進める", "止める"],
            )
            return


# --------------------------------------------------------------------------
# lifecycle
# --------------------------------------------------------------------------

def is_running(session_id: str) -> bool:
    task = _loops.get(session_id)
    return task is not None and not task.done()


async def start_loop(session_id: str, action: AgentAction | None = None) -> None:
    """Start (or restart) the execution loop of one session.

    二重起動防止: is_running() の判定とタスク登録の間で await しない（approve や
    checkin の連打で 2 本走らないようにするため）。``status = running`` への遷移は
    タスク側で行い、呼び出し元はそれが済むまで待ってから応答を返す。
    """
    if is_running(session_id):
        return
    _stop_requests.discard(session_id)
    started = asyncio.Event()
    _loops[session_id] = asyncio.create_task(
        _guarded_loop(session_id, action, started), name=f"agent-loop-{session_id}"
    )
    await started.wait()


async def _guarded_loop(
    session_id: str, action: AgentAction | None, started: asyncio.Event | None = None
) -> None:
    try:
        try:
            await _set_status(session_id, "running", message="running")
        finally:
            if started is not None:
                started.set()
        await _loop(session_id, action)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - ループは絶対に落とさない
        log.exception("agent loop %s failed", session_id)
        await _halt(session_id, f"エージェントの実行中にエラーが発生しました: {exc}")
    finally:
        _loops.pop(session_id, None)


def forget(session_id: str) -> None:
    """セッション削除時にインメモリ状態（停止要求 / thinking / activity）を落とす。"""
    _stop_requests.discard(session_id)
    _thinking.discard(session_id)
    _activity.pop(session_id, None)


async def request_stop(session_id: str) -> None:
    """⏹: 実行中のジョブは完了を待ってから停止する（AGENT-MODE §2）。"""
    _stop_requests.add(session_id)
    if not is_running(session_id):
        _stop_requests.discard(session_id)
        await _halt(session_id, "ユーザーの操作で停止しました。")


async def stop_all() -> None:
    """Cancel every loop (FastAPI lifespan shutdown)."""
    tasks = list(_loops.values())
    _loops.clear()
    _stop_requests.clear()
    _thinking.clear()
    _activity.clear()
    for task in tasks:
        task.cancel()
    for task in tasks:
        try:
            await task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
