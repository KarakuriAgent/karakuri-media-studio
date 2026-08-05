"""Canvas の ``generate`` / ``best_practices`` アダプタ。

model カード（``kind: "model"``）に書かれたワークフローとパラメータを既存の
:func:`app.jobs.create_job` の :class:`app.models.JobCreate` に翻訳し、完了したら
成果物を media カードとして model カードの右隣に並べる。

``jobs`` / ``workflows`` は **import して使うだけ**（キャンバス専用の分岐を既存
コードに足さない）。エージェントとの往復はすべて ``role="event"`` のメッセージで
行うので、失敗も日本語のイベントとしてトランスクリプトに残り、次のターンで LLM
自身が読んで直せる。
"""

from __future__ import annotations

import asyncio
import logging

from pydantic import ValidationError

from .. import jobs, workflows
from ..models import Job, JobCreate, LoraRef
from . import store
from .models import (
    CanvasNode,
    CanvasNodeCreate,
    CanvasProgress,
    MediaData,
    ModelData,
)
from .protocol import CanvasAction

log = logging.getLogger(__name__)

#: 完了待ちのポーリング間隔（秒。テストが monkeypatch する）
POLL_INTERVAL = 1.0
#: 完了待ちの上限（agent_runner と同じ 6 時間）
JOB_WAIT_TIMEOUT = 6 * 60 * 60.0

#: media カードを model カードの右に置くときの間隔と、複数成果物の縦の刻み
MEDIA_GAP_X = 40.0
MEDIA_STEP_Y = 240.0


# --------------------------------------------------------------------------
# イベント
# --------------------------------------------------------------------------

async def _event(
    project_id: str,
    kind: str,
    content: str,
    *,
    job_id: str | None = None,
    node_id: str | None = None,
) -> None:
    """結果をトランスクリプトに残し、WS でフロントにも知らせる。"""
    await store.append_message(project_id, "event", content, kind=kind)
    await store.publish(
        CanvasProgress(
            project_id=project_id,
            event=kind,
            job_id=job_id,
            node_id=node_id,
            message=content,
        )
    )


# --------------------------------------------------------------------------
# model カード + generate アクション -> JobCreate
# --------------------------------------------------------------------------

def build_job_create(node: CanvasNode, action: CanvasAction) -> JobCreate:
    """model カードの設定と generate アクションを 1 本のジョブ投入に畳む。

    動画は「動画ステージだけ走らせる」``mode="i2v"``。t2v 系ワークフローは
    ``source_image`` を要求しないのでそのまま通り、i2v 系で画像が無いときは
    :func:`app.jobs.missing_job_fields` が弾く（そのメッセージをイベントにする）。
    """
    data = ModelData(**node.data)  # 保存時に検証済みだが防御的に
    params = data.params
    common: dict = dict(
        aspect_ratio=params.aspect_ratio,
        megapixels=params.megapixels,
        duration=params.duration,
        fps=params.fps,
        sage_attention=params.sage_attention,
        easy_cache=params.easy_cache,
        selects=dict(params.selects),
        model_overrides=dict(params.model_overrides),
        # 履歴でキャンバス由来と分かるようにする（chat_session_id は使わない）。
        user_input=f"canvas:{node.project_id}",
    )
    if params.negative_prompt:
        common["negative_prompt"] = params.negative_prompt

    if data.target == "image":
        return JobCreate(
            mode="image_only",
            image_workflow=data.workflow,
            image_prompt=action.prompt,
            loras=[LoraRef(**lora.model_dump()) for lora in params.loras],
            source_image=action.source_image,
            **common,
        )
    if data.target == "video":
        return JobCreate(
            mode="i2v",
            video_workflow=data.workflow,
            video_prompt=action.prompt,
            video_loras=[LoraRef(**lora.model_dump()) for lora in params.video_loras],
            source_image=action.source_image,
            **common,
        )
    return JobCreate(
        mode="audio",
        audio_workflow=data.workflow,
        audio_prompt=action.prompt,
        lyrics=action.lyrics,
        **common,
    )


# --------------------------------------------------------------------------
# 投入 -> 完了待ち -> media カード
# --------------------------------------------------------------------------

async def start_generate(
    project_id: str, node: CanvasNode, action: CanvasAction
) -> Job | None:
    """ジョブを投入して ``job_started`` を書く（失敗なら ``generate_failed``）。

    投入だけ同期で済ませるのは、ターンループが「開始しました」を読んで次の手に
    進めるようにするため。完了待ちは :func:`await_generate` が引き取る。
    """
    try:
        payload = build_job_create(node, action)
        job = await jobs.create_job(payload)
    except (jobs.JobValidationError, ValidationError, ValueError) as exc:
        await _event(
            project_id,
            "generate_failed",
            f"生成を投入できませんでした: {exc}",
            node_id=node.id,
        )
        return None
    await _event(
        project_id,
        "job_started",
        f"生成を開始しました (job {job.id})。完了したら media カードを置きます。",
        job_id=job.id,
        node_id=node.id,
    )
    return job


async def _wait_for_job(job_id: str) -> Job:
    """``done`` / ``failed`` / ``canceled`` になるまで待つ（agent_runner と同型）。"""
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


async def place_media_nodes(
    project_id: str, node: CanvasNode, action: CanvasAction, job: Job
) -> list[CanvasNode]:
    """成果物ごとに media カードを model カードの右へ縦に並べる。"""
    outputs = (
        ("image", job.image_url),
        ("video", job.video_url),
        ("audio", job.audio_output_url),
    )
    created: list[CanvasNode] = []
    offset = 0.0
    for media_type, url in outputs:
        if not url:
            continue
        media = await store.insert_node(
            project_id,
            CanvasNodeCreate(
                kind="media",
                title=f"{node.title or 'モデル'} の生成結果",
                data=MediaData(
                    media_type=media_type,
                    url=url,
                    job_id=job.id,
                    prompt=action.prompt,
                ).model_dump(),
                x=node.x + node.w + MEDIA_GAP_X,
                y=node.y + offset,
            ),
        )
        created.append(media)
        await store.publish(
            CanvasProgress(
                project_id=project_id,
                event="node_created",
                node_id=media.id,
                job_id=job.id,
            )
        )
        offset += MEDIA_STEP_Y
    return created


async def await_generate(
    project_id: str, node: CanvasNode, action: CanvasAction, job: Job
) -> None:
    """完了を待って media カードを配置する（バックグラウンドタスクの本体）。"""
    try:
        finished = await _wait_for_job(job.id)
    except jobs.JobError as exc:
        await _event(
            project_id, "job_failed", f"生成の完了を待てませんでした: {exc}", job_id=job.id
        )
        return
    if finished.status != "done":
        await _event(
            project_id,
            "job_failed",
            f"生成が失敗しました (job {finished.id}): {finished.error or 'unknown'}",
            job_id=finished.id,
        )
        return
    created = await place_media_nodes(project_id, node, action, finished)
    if not created:
        await _event(
            project_id,
            "job_done",
            f"生成は完了しましたが、成果物がありませんでした (job {finished.id})",
            job_id=finished.id,
        )
        return
    await _event(
        project_id,
        "job_done",
        f"生成が完了し、media カードを {len(created)} 枚配置しました"
        f" (job {finished.id})",
        job_id=finished.id,
        node_id=created[0].id,
    )


async def run_generate(
    project_id: str, node: CanvasNode, action: CanvasAction
) -> None:
    """投入から media カード配置までを通しで行う。"""
    job = await start_generate(project_id, node, action)
    if job is None:
        return
    await await_generate(project_id, node, action, job)


# --------------------------------------------------------------------------
# best_practices
# --------------------------------------------------------------------------

def best_practices_text(workflow: str) -> str:
    """1 ワークフローの説明・プロンプトのコツ・必要入力・選択肢をまとめた本文。"""
    try:
        spec = workflows.get_spec(workflow)
    except workflows.WorkflowSpecError:
        return (
            f"ワークフロー '{workflow}' は見つかりませんでした。"
            "システムプロンプトの WORKFLOW IDS にある id から選び直してください。"
        )
    entry = workflows.catalog_entry(spec)
    lines = [f"# {entry.label} ({entry.id})", entry.description]
    if entry.prompt_hint:
        lines += ["", "## PROMPT HINT", entry.prompt_hint]
    if entry.notes:
        lines += ["", "## NOTES", entry.notes]
    if entry.required_inputs:
        lines += [
            "",
            "必要入力: "
            + ", ".join(f"{field}（{label}）" for field, label in entry.required_inputs),
        ]
    if entry.selects:
        lines.append("")
        for name, label, choices, default, _auto, hint in entry.selects:
            suffix = f" {hint}" if hint else ""
            lines.append(
                f"- select `{name}`（{label}）: {', '.join(choices)}"
                f"（既定 {default}）{suffix}"
            )
    return "\n".join(lines)
