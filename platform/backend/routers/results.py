from __future__ import annotations
import uuid
from pathlib import Path

import duckdb
import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from backend.db.session import get_db
from backend.deps import get_current_user
from backend.models.user import User
from backend.models.job import Job

router = APIRouter(prefix="/api/results", tags=["results"])


async def _get_completed_job(db: AsyncSession, job_id: uuid.UUID, user_id: uuid.UUID) -> Job:
    result = await db.execute(
        select(Job).where(Job.id == job_id, Job.user_id == user_id)
    )
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    if job.status != "completed":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail=f"Job is not completed (status: {job.status})")
    return job


def _safe_pdb_path(output_path: str, construct_id: str) -> Path:
    """Resolve PDB path with traversal protection."""
    pdb_dir = (Path(output_path) / "pdb").resolve()
    requested = (pdb_dir / f"{construct_id}.pdb").resolve()
    if not requested.is_relative_to(pdb_dir):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Invalid construct ID")
    if not requested.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="PDB not found")
    return requested


@router.get("/{job_id}/ranking")
async def get_ranking(
    job_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    job = await _get_completed_job(db, job_id, current_user.id)
    final_dir = Path(job.output_path) / "final"
    if not final_dir.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Ranking data not found")

    records = []
    for csv_file in sorted(final_dir.glob("*.csv")):
        df = pd.read_csv(csv_file)
        records.extend(df.to_dict(orient="records"))

    if not records:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="No ranking data found")

    return {"items": records, "total": len(records)}


@router.get("/{job_id}/funnel")
async def get_funnel(
    job_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    job = await _get_completed_job(db, job_id, current_user.id)
    db_path = Path(job.output_path) / "pipeline.db"
    if not db_path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Pipeline database not found")

    try:
        conn = duckdb.connect(str(db_path), read_only=True)
        try:
            df = conn.execute(
                "SELECT round, step, status, total_items, processed_items, started_at, completed_at "
                "FROM checkpoint ORDER BY started_at NULLS LAST"
            ).fetchdf()
        finally:
            conn.close()
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                            detail=f"Failed to read pipeline database: {exc}") from exc

    rows = df.to_dict(orient="records")
    # Convert timestamps to ISO strings for JSON serialization
    for row in rows:
        for key in ("started_at", "completed_at"):
            val = row.get(key)
            if val is pd.NaT or val is None:
                row[key] = None
            elif hasattr(val, "isoformat"):
                row[key] = val.isoformat()

    return {"rounds": rows}


@router.get("/{job_id}/pdb/{construct_id}")
async def get_pdb(
    job_id: uuid.UUID,
    construct_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    job = await _get_completed_job(db, job_id, current_user.id)
    pdb_path = _safe_pdb_path(job.output_path, construct_id)

    def iterfile():
        with open(pdb_path, "rb") as f:
            while chunk := f.read(65536):
                yield chunk

    return StreamingResponse(
        iterfile(),
        media_type="chemical/x-pdb",
        headers={"Content-Disposition": f'attachment; filename="{construct_id}.pdb"'},
    )
