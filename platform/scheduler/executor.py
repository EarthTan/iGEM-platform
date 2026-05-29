from __future__ import annotations
import asyncio
import json
import os
import sys

from scheduler.config import settings
from scheduler import db as scheduler_db

ROUND_SEQUENCE = [
    "round00_preprocess",
    "round01_antioxidant_split",
    "round02_safety_screen",
    "round03_precompute",
    "round03_deep_scoring",
    "round03_phase2_graphcpp",
    "round04_enumerate",
    "round04_phase2_bepipred3",
    "round05_3d",
    "round06_pdb_eval",
    "round07_final",
]

RETRYABLE_ROUNDS = {"round01_antioxidant_split", "round02_safety_screen"}


async def run_round(
    job_id: str,
    round_name: str,
    output_dir: str,
    config_snapshot: dict,
    conn,
) -> bool:
    module = f"main.stages4.s4_{round_name}"
    cmd = [
        sys.executable, "-m", module,
        "--job-id", job_id,
        "--output-dir", output_dir,
    ]

    extra_args = _build_round_args(round_name, config_snapshot)
    cmd.extend(extra_args)

    env = os.environ.copy()
    env["PIPELINE_JOB_CONFIG"] = json.dumps(config_snapshot)
    env["SKIP_DOCKER_START"] = "1"
    env["PYTHONPATH"] = settings.pipeline_root

    await scheduler_db.upsert_job_round(conn, job_id, round_name, "running")
    await scheduler_db.update_current_round(conn, job_id, round_name)
    await scheduler_db.insert_job_event(conn, job_id, round_name, "round_started",
                                        f"Starting {round_name}")

    retries = 2 if round_name in RETRYABLE_ROUNDS else 0
    delay = 5.0

    proc = None
    for attempt in range(retries + 1):
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=settings.pipeline_root,
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            stdout, _ = await asyncio.wait_for(
                proc.communicate(),
                timeout=settings.round_timeout,
            )
            output = stdout.decode(errors="replace") if stdout else ""

            if proc.returncode == 0:
                await scheduler_db.upsert_job_round(conn, job_id, round_name, "completed")
                await scheduler_db.insert_job_event(conn, job_id, round_name, "round_completed",
                                                    f"Completed {round_name}")
                return True

            error_tail = output[-1000:] if output else "no output"
            if attempt < retries:
                await asyncio.sleep(delay)
                delay *= 3
                continue

            await scheduler_db.upsert_job_round(conn, job_id, round_name, "failed")
            await scheduler_db.insert_job_event(conn, job_id, round_name, "round_failed",
                                                error_tail)
            return False

        except asyncio.TimeoutError:
            try:
                if proc is not None:
                    proc.kill()
            except Exception:
                pass
            await scheduler_db.upsert_job_round(conn, job_id, round_name, "failed")
            await scheduler_db.insert_job_event(conn, job_id, round_name, "round_failed",
                                                f"{round_name} timed out after {settings.round_timeout}s")
            return False

    return False


async def execute_job(job: dict, conn) -> None:
    job_id = job["id"]
    output_dir = job["output_path"]
    config = job["config_snapshot"]

    for round_name in ROUND_SEQUENCE:
        if await scheduler_db.is_job_paused(conn, job_id):
            await scheduler_db.insert_job_event(conn, job_id, None, "job_paused",
                                                "Job paused by user")
            return

        success = await run_round(job_id, round_name, output_dir, config, conn)
        if not success:
            await scheduler_db.mark_job_failed(conn, job_id,
                                               f"Round {round_name} failed")
            await scheduler_db.insert_job_event(conn, job_id, None, "job_failed",
                                                f"Failed at {round_name}")
            return

    await scheduler_db.mark_job_completed(conn, job_id)
    await scheduler_db.insert_job_event(conn, job_id, None, "job_completed",
                                        "All rounds completed successfully")


def _build_round_args(round_name: str, config: dict) -> list[str]:
    args: list[str] = []
    if round_name == "round01_antioxidant_split":
        if "round1_top_pct" in config.get("round_splits", {}):
            args += ["--top-pct", str(config["round_splits"]["round1_top_pct"])]
        if "round1_bottom_pct" in config.get("round_splits", {}):
            args += ["--bottom-pct", str(config["round_splits"]["round1_bottom_pct"])]
    elif round_name == "round02_safety_screen":
        thresholds = config.get("thresholds", {})
        if "toxinpred3" in thresholds:
            args += ["--toxin-threshold", str(thresholds["toxinpred3"])]
        if "hemopi2" in thresholds:
            args += ["--hemo-threshold", str(thresholds["hemopi2"])]
        if "mhcflurry" in thresholds:
            args += ["--mhc-threshold", str(thresholds["mhcflurry"])]
    return args
