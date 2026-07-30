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

from . import agent_protocol, autotag, grok, jobs, library, prompts, ws
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
    Job,
    JobContinue,
    JobCreate,
    JobRerun,
)

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
    if job is None or not job.video_path or not Path(job.video_path).is_file():
        await _event(
            session_id,
            "inspect_failed",
            f"job {job_id} には検分できる動画がありません。",
            job_id=job_id,
        )
        return

    dest_dir = session_dir(session_id) / f"inspect_{job_id}"
    try:
        frames = await extract_frames(
            Path(job.video_path), dest_dir, interval=action.interval
        )
    except jobs.JobError as exc:
        await _event(
            session_id, "inspect_failed", f"フレーム検分に失敗しました: {exc}",
            job_id=job_id, error=str(exc),
        )
        return

    if job.last_frame_path and Path(job.last_frame_path).is_file():
        last = dest_dir / "last_frame.png"
        last.write_bytes(Path(job.last_frame_path).read_bytes())
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
            job, action.source or "", action.title, action.tags
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
    await _event(
        session_id,
        "library_added",
        f"「{item.name}」をライブラリに登録しました（{item.path}）。{tags}"
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
    return f"- `{item.path}` — 「{item.name}」（{item.kind}）{tags}{nsfw}"


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
    items, total = await library.search_items(
        kind=action.library_kind,
        query=action.query,
        tag=action.tag,
        limit=LIBRARY_SEARCH_LIMIT,
        offset=action.offset,
    )
    criteria = ", ".join(
        part
        for part in (
            f"q={action.query!r}" if action.query else "",
            f"tag={action.tag!r}" if action.tag else "",
            f"kind={action.library_kind}" if action.library_kind else "",
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
    if action.action == "inspect":
        await _inspect(session_id, action)
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
