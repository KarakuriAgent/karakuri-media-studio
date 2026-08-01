"""Job API (SPEC §9)."""

from fastapi import APIRouter, HTTPException, Query

from .. import jobs as service
from ..models import (
    Job,
    JobContinue,
    JobCreate,
    JobRerun,
    NsfwUpdate,
    VeoExtend,
    VeoUpscale,
)

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


def _validation_error(exc: service.JobValidationError) -> HTTPException:
    return HTTPException(status_code=422, detail=str(exc))


@router.post("", response_model=Job, status_code=201)
async def create_job(payload: JobCreate) -> Job:
    """Create a job and put it on the queue (per-mode requirements -> 422)."""
    try:
        return await service.create_job(payload)
    except service.JobValidationError as exc:
        raise _validation_error(exc) from exc


@router.get("", response_model=list[Job])
async def list_jobs(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> list[Job]:
    return await service.list_jobs(limit=limit, offset=offset)


@router.get("/{job_id}", response_model=Job)
async def get_job(job_id: str) -> Job:
    job = await service.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return job


@router.delete("/{job_id}", status_code=204)
async def delete_job(job_id: str) -> None:
    if not await service.delete_job(job_id):
        raise HTTPException(status_code=404, detail="job not found")


@router.post("/{job_id}/nsfw", response_model=Job)
async def set_job_nsfw(job_id: str, payload: NsfwUpdate) -> Job:
    """NSFW フラグの手動トグル（manual として保存し、自動判定に上書きされない）。"""
    job = await service.set_nsfw(job_id, payload.nsfw)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return job


@router.post("/{job_id}/rerun", response_model=Job, status_code=201)
async def rerun_job(job_id: str, payload: JobRerun | None = None) -> Job:
    """Re-build the workflow from the stored params (optionally with a new seed)."""
    try:
        return await service.rerun_job(job_id, payload or JobRerun())
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="job not found") from exc
    except service.JobValidationError as exc:
        raise _validation_error(exc) from exc


@router.post("/{job_id}/continue", response_model=Job, status_code=201)
async def continue_job(job_id: str, payload: JobContinue | None = None) -> Job:
    """Start a mode-B job from this job's last frame (SPEC §2)."""
    try:
        return await service.continue_job(job_id, payload or JobContinue())
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="job not found") from exc
    except service.JobValidationError as exc:
        raise _validation_error(exc) from exc


@router.post("/{job_id}/veo/extend", response_model=Job, status_code=201)
async def veo_extend(job_id: str, payload: VeoExtend) -> Job:
    """Veo の動画に **+7 秒**を継ぎ足すジョブを作る（SPEC §5.2 / issue #26）。

    ラストフレームからの続き生成（``/continue``）と違い、元動画そのものを
    延長した 1 本が返る。掛けられるジョブかどうかは ``Job.followups`` に出る。
    """
    try:
        return await service.veo_extend_job(job_id, payload)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="job not found") from exc
    except service.JobValidationError as exc:
        raise _validation_error(exc) from exc


@router.post("/{job_id}/veo/1080p", response_model=Job, status_code=201)
async def veo_1080p(job_id: str, payload: VeoUpscale | None = None) -> Job:
    """720p で生成した Veo の動画の **1080P 版**を取りに行く（5 credits）。"""
    try:
        return await service.veo_1080p_job(job_id, payload or VeoUpscale())
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="job not found") from exc
    except service.JobValidationError as exc:
        raise _validation_error(exc) from exc
