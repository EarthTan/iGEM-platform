from __future__ import annotations
import asyncpg
import json
from datetime import datetime, timezone
from scheduler.config import settings


async def get_conn() -> asyncpg.Connection:
    return await asyncpg.connect(settings.database_url.replace("+asyncpg", ""))


async def select_next_pending_job(conn: asyncpg.Connection) -> dict | None:
    row = await conn.fetchrow("""
        SELECT id, config_snapshot, output_path, name
        FROM jobs
        WHERE status = 'pending'
        ORDER BY created_at
        LIMIT 1
        FOR UPDATE SKIP LOCKED
    """)
    if row is None:
        return None
    return {
        "id": str(row["id"]),
        "config_snapshot": json.loads(row["config_snapshot"]) if isinstance(row["config_snapshot"], str) else row["config_snapshot"],
        "output_path": row["output_path"],
        "name": row["name"],
    }


async def mark_job_running(conn: asyncpg.Connection, job_id: str) -> None:
    await conn.execute("""
        UPDATE jobs SET status = 'running', started_at = $1, last_heartbeat = $1
        WHERE id = $2
    """, datetime.now(timezone.utc), job_id)


async def mark_job_completed(conn: asyncpg.Connection, job_id: str) -> None:
    await conn.execute("""
        UPDATE jobs SET status = 'completed', completed_at = $1 WHERE id = $2
    """, datetime.now(timezone.utc), job_id)


async def mark_job_failed(conn: asyncpg.Connection, job_id: str, error: str) -> None:
    await conn.execute("""
        UPDATE jobs SET status = 'failed', completed_at = $1, error_message = $2 WHERE id = $3
    """, datetime.now(timezone.utc), error[-2000:], job_id)


async def update_heartbeat(conn: asyncpg.Connection, job_id: str) -> None:
    await conn.execute("""
        UPDATE jobs SET last_heartbeat = $1 WHERE id = $2
    """, datetime.now(timezone.utc), job_id)


async def update_current_round(conn: asyncpg.Connection, job_id: str, round_name: str) -> None:
    await conn.execute("""
        UPDATE jobs SET current_round = $1 WHERE id = $2
    """, round_name, job_id)


async def upsert_job_round(conn: asyncpg.Connection, job_id: str, round_name: str, status: str) -> None:
    now = datetime.now(timezone.utc)
    if status == "running":
        await conn.execute("""
            INSERT INTO job_rounds (job_id, round_name, status, started_at)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (job_id, round_name) DO UPDATE SET status = $3, started_at = $4
        """, job_id, round_name, status, now)
    else:
        await conn.execute("""
            INSERT INTO job_rounds (job_id, round_name, status, completed_at)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (job_id, round_name) DO UPDATE SET status = $3, completed_at = $4
        """, job_id, round_name, status, now)


async def insert_job_event(conn: asyncpg.Connection, job_id: str, round_name: str | None,
                           event: str, message: str) -> None:
    await conn.execute("""
        INSERT INTO job_events (job_id, round, event, message)
        VALUES ($1, $2, $3, $4)
    """, job_id, round_name, event, message)


async def is_job_paused(conn: asyncpg.Connection, job_id: str) -> bool:
    row = await conn.fetchrow("SELECT status FROM jobs WHERE id = $1", job_id)
    return row is not None and row["status"] == "paused"
