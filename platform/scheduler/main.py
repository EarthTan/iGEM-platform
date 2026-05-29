from __future__ import annotations
import asyncio
import logging
from scheduler import db as scheduler_db
from scheduler.executor import execute_job
from scheduler.config import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


async def heartbeat_loop(job_id: str, stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        try:
            conn = await scheduler_db.get_conn()
            await scheduler_db.update_heartbeat(conn, job_id)
            await conn.close()
        except Exception as e:
            logger.warning(f"Heartbeat error: {e}")
        try:
            await asyncio.wait_for(asyncio.shield(stop_event.wait()),
                                   timeout=settings.heartbeat_interval)
        except asyncio.TimeoutError:
            pass


async def recover_stale_jobs() -> None:
    conn = await scheduler_db.get_conn()
    try:
        rows = await conn.fetch(
            "SELECT id FROM jobs WHERE status = 'running' "
            "AND (last_heartbeat IS NULL OR last_heartbeat < NOW() - INTERVAL '5 minutes')"
        )
        for row in rows:
            job_id = str(row["id"])
            logger.warning(f"Recovering stale job {job_id}")
            await scheduler_db.mark_job_failed(conn, job_id, "Scheduler crash recovery")
            await scheduler_db.insert_job_event(conn, job_id, None, "job_failed",
                                                "Recovered from scheduler crash")
    finally:
        await conn.close()


async def scheduler_loop() -> None:
    logger.info("Scheduler started")
    await recover_stale_jobs()
    while True:
        conn = await scheduler_db.get_conn()
        try:
            async with conn.transaction():
                job = await scheduler_db.select_next_pending_job(conn)
                if job is None:
                    await asyncio.sleep(settings.poll_interval)
                    continue

                job_id = job["id"]
                logger.info(f"Starting job {job_id}")
                await scheduler_db.mark_job_running(conn, job_id)
                await scheduler_db.insert_job_event(conn, job_id, None, "job_started",
                                                    f"Job {job_id} started")
        finally:
            await conn.close()

        stop_heartbeat = asyncio.Event()
        heartbeat_task = asyncio.create_task(heartbeat_loop(job_id, stop_heartbeat))

        try:
            conn = await scheduler_db.get_conn()
            await execute_job(job, conn)
            await conn.close()
        except Exception as e:
            logger.error(f"Job {job_id} crashed: {e}")
            try:
                conn = await scheduler_db.get_conn()
                await scheduler_db.mark_job_failed(conn, job_id, str(e))
                await conn.close()
            except Exception:
                pass
        finally:
            stop_heartbeat.set()
            await heartbeat_task


def main() -> None:
    asyncio.run(scheduler_loop())


if __name__ == "__main__":
    main()
