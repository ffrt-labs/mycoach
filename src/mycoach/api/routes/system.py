"""System endpoints — health check, scheduler status, manual job triggers."""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from mycoach.api.deps import require_api_key

router = APIRouter(prefix="/api/system", tags=["system"])


class StatusResponse(BaseModel):
    status: str


class SchedulerJob(BaseModel):
    id: str
    next_run_time: str | None
    trigger: str


class SchedulerStatusResponse(BaseModel):
    running: bool
    timezone: str | None = None
    now: str | None = None
    jobs: list[SchedulerJob]


class TriggerResponse(BaseModel):
    job_id: str
    scheduled_for: str


@router.get("/status", response_model=StatusResponse)
async def system_status() -> StatusResponse:
    return StatusResponse(status="ok")


@router.get("/scheduler", response_model=SchedulerStatusResponse)
async def scheduler_status(request: Request) -> SchedulerStatusResponse:
    scheduler = getattr(request.app.state, "scheduler", None)
    if scheduler is None:
        return SchedulerStatusResponse(running=False, jobs=[])

    jobs = [
        SchedulerJob(
            id=job.id,
            next_run_time=job.next_run_time.isoformat() if job.next_run_time else None,
            trigger=str(job.trigger),
        )
        for job in scheduler.get_jobs()
    ]
    return SchedulerStatusResponse(
        running=scheduler.running,
        timezone=str(scheduler.timezone),
        now=datetime.now(UTC).isoformat(),
        jobs=jobs,
    )


@router.post(
    "/scheduler/trigger/{job_id}",
    response_model=TriggerResponse,
    status_code=202,
    dependencies=[Depends(require_api_key)],
)
async def trigger_scheduler_job(job_id: str, request: Request) -> TriggerResponse:
    """Fire a scheduled job immediately, ahead of its normal cron time.

    Reschedules the job's next run to "now" via APScheduler's own threadpool, so
    this returns before the job (an LLM call) finishes. Because these are cron
    jobs, APScheduler recomputes the next fire time from the trigger after the
    run completes — the normal schedule is restored automatically.

    A 202 means "queued", not "email sent" — jobs skip silently on missing
    preconditions (see ``scheduler/jobs.py``), so the inbox and app logs remain
    the real verification signal.
    """
    scheduler = getattr(request.app.state, "scheduler", None)
    if scheduler is None:
        raise HTTPException(
            status_code=503,
            detail="Scheduler is not running (disabled in this environment).",
        )

    job = scheduler.get_job(job_id)
    if job is None:
        valid_ids = sorted(j.id for j in scheduler.get_jobs())
        raise HTTPException(
            status_code=404,
            detail=f"Unknown job '{job_id}'. Valid job ids: {valid_ids}",
        )

    run_at = datetime.now(scheduler.timezone)
    job.modify(next_run_time=run_at)

    return TriggerResponse(job_id=job_id, scheduled_for=run_at.isoformat())
