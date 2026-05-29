from __future__ import annotations
import asyncio
import json
import uuid
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sse_starlette.sse import EventSourceResponse
from backend.config import settings
from backend.db.session import get_db, engine
from backend.deps import get_current_user
from backend.models.user import User
from backend.models.job import Job, JobRound, JobEvent
from backend.schemas.jobs import CreateJobRequest, JobResponse, JobListResponse
from backend.schemas.auth import JobConfig

router = APIRouter(prefix="/api/jobs", tags=["jobs"])

ROUND_SEQUENCE = [
    "round00_preprocess", "round01_antioxidant_split", "round02_safety_screen",
    "round03_precompute", "round03_deep_scoring", "round03_phase2_graphcpp",
    "round04_enumerate", "round04_phase2_bepipred3", "round05_3d",
    "round06_pdb_eval", "round07_final",
]


@router.post("", response_model=JobResponse, status_code=status.HTTP_201_CREATED)
async def create_job(
    req: CreateJobRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    JobConfig(**req.config)

    job_id = uuid.uuid4()
    output_path = str(Path(settings.output_root) / "jobs" / str(job_id))
    Path(output_path).mkdir(parents=True, exist_ok=True)

    job = Job(
        id=job_id,
        user_id=current_user.id,
        name=req.name,
        output_path=output_path,
        config_snapshot=req.config,
    )
    db.add(job)

    for round_name in ROUND_SEQUENCE:
        db.add(JobRound(job_id=job_id, round_name=round_name))

    await db.commit()
    await db.refresh(job)
    return _job_with_rounds(job, [])


@router.get("", response_model=JobListResponse)
async def list_jobs(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    status_filter: str | None = Query(default=None, alias="status"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    q = select(Job).where(Job.user_id == current_user.id)
    if status_filter:
        q = q.where(Job.status == status_filter)

    total_result = await db.execute(select(func.count()).select_from(q.subquery()))
    total = total_result.scalar_one()

    q = q.order_by(Job.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(q)
    jobs = result.scalars().all()

    return JobListResponse(jobs=[_job_with_rounds(j, []) for j in jobs],
                           total=total, page=page, page_size=page_size)


@router.get("/{job_id}", response_model=JobResponse)
async def get_job(
    job_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    job = await _get_job_or_404(db, job_id, current_user.id)
    rounds_result = await db.execute(select(JobRound).where(JobRound.job_id == job_id))
    rounds = rounds_result.scalars().all()
    return _job_with_rounds(job, list(rounds))


@router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
async def pause_job(
    job_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    job = await _get_job_or_404(db, job_id, current_user.id)
    if job.status not in ("pending", "running"):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail=f"Cannot pause job with status '{job.status}'")
    job.status = "paused"
    await db.commit()


_TERMINAL_STATUSES = {"completed", "failed", "paused"}


@router.get("/{job_id}/events")
async def stream_job_events(
    job_id: uuid.UUID,
    last_event_id: str | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _get_job_or_404(db, job_id, current_user.id)

    async def generate():
        last_seen_id = last_event_id
        while True:
            async with AsyncSession(engine) as loop_db:
                q = select(JobEvent).where(JobEvent.job_id == job_id)
                if last_seen_id:
                    ts_subq = (
                        select(JobEvent.timestamp)
                        .where(JobEvent.id == uuid.UUID(last_seen_id))
                        .scalar_subquery()
                    )
                    last_uuid = uuid.UUID(last_seen_id)
                    q = q.where(
                        (JobEvent.timestamp > ts_subq)
                        | ((JobEvent.timestamp == ts_subq) & (JobEvent.id > last_uuid))
                    )
                q = q.order_by(JobEvent.timestamp.asc(), JobEvent.id.asc()).limit(50)
                result = await loop_db.execute(q)
                events = result.scalars().all()

                for e in events:
                    payload = json.dumps({
                        "id": str(e.id),
                        "round": e.round,
                        "event": e.event,
                        "message": e.message,
                        "timestamp": e.timestamp.isoformat(),
                    })
                    yield f"event: job_event\ndata: {payload}\n\n"
                    last_seen_id = str(e.id)

                job_result = await loop_db.execute(
                    select(Job.status).where(Job.id == job_id)
                )
                job_status = job_result.scalar_one_or_none()

            if job_status in _TERMINAL_STATUSES and not events:
                yield "event: done\ndata: {}\n\n"
                return

            await asyncio.sleep(1)

    return EventSourceResponse(generate())


async def _get_job_or_404(db: AsyncSession, job_id: uuid.UUID, user_id: uuid.UUID) -> Job:
    result = await db.execute(
        select(Job).where(Job.id == job_id, Job.user_id == user_id)
    )
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return job


def _job_with_rounds(job: Job, rounds: list[JobRound]) -> JobResponse:
    from backend.schemas.jobs import JobRoundResponse
    return JobResponse(
        id=job.id,
        user_id=job.user_id,
        config_id=job.config_id,
        name=job.name,
        status=job.status,
        current_round=job.current_round,
        processed_count=job.processed_count,
        total_count=job.total_count,
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        error_message=job.error_message,
        rounds=[JobRoundResponse.model_validate(r) for r in rounds],
    )
