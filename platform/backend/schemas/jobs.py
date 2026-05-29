from __future__ import annotations
from pydantic import BaseModel
from typing import Any
import uuid
from datetime import datetime


class CreateJobRequest(BaseModel):
    config: dict[str, Any]
    name: str | None = None


class JobRoundResponse(BaseModel):
    id: uuid.UUID
    job_id: uuid.UUID
    round_name: str
    status: str
    started_at: datetime | None
    completed_at: datetime | None

    model_config = {"from_attributes": True}


class JobResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    config_id: uuid.UUID | None
    name: str | None
    status: str
    current_round: str | None
    processed_count: int
    total_count: int | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    error_message: str | None
    rounds: list[JobRoundResponse] = []

    model_config = {"from_attributes": True}


class JobListResponse(BaseModel):
    jobs: list[JobResponse]
    total: int
    page: int
    page_size: int
