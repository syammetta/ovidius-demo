"""Durable ingestion queue backed by Postgres with optional Redis acceleration."""

from __future__ import annotations

import json
import uuid
from typing import Any

import asyncpg

from app.db import get_pool
from app.cache import get_client as get_redis_client

QUEUE_KEY = "ovidius:ingestion:queue"


def _row_to_job(row: asyncpg.Record) -> dict[str, Any]:
    payload = row["payload"]
    if isinstance(payload, str):
        payload = json.loads(payload)
    progress = row["progress"]
    if isinstance(progress, str):
        progress = json.loads(progress)
    return {
        "job_id": row["job_id"],
        "job_type": row["job_type"],
        "source": row["source"],
        "payload": payload or {},
        "status": row["status"],
        "progress": progress or {},
        "error": row["error"],
        "attempts": row["attempts"],
        "max_attempts": row["max_attempts"],
        "claimed_by": row["claimed_by"],
        "claimed_at": row["claimed_at"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


async def enqueue_job(job_type: str, source: str, payload: dict[str, Any] | None = None) -> str:
    job_id = str(uuid.uuid4())[:8]
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO ingestion_jobs (job_id, job_type, source, payload, status)
               VALUES ($1, $2, $3, $4::jsonb, 'queued')""",
            job_id,
            job_type,
            source,
            json.dumps(payload or {}),
        )
    await append_job_log(job_id, "Queued")

    client = await get_redis_client()
    if client:
        try:
            await client.lpush(QUEUE_KEY, job_id)
        except Exception:
            pass
    return job_id


async def append_job_log(job_id: str, message: str) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO ingestion_job_logs (job_id, log) VALUES ($1, $2)",
            job_id,
            message,
        )


async def update_job_progress(job_id: str, progress: dict[str, Any]) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """UPDATE ingestion_jobs
               SET progress = COALESCE(progress, '{}'::jsonb) || $1::jsonb,
                   updated_at = now()
               WHERE job_id = $2""",
            json.dumps(progress),
            job_id,
        )


async def claim_job_by_id(job_id: str, worker_id: str) -> dict[str, Any] | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """UPDATE ingestion_jobs
               SET status = 'running',
                   attempts = attempts + 1,
                   claimed_by = $2,
                   claimed_at = now(),
                   started_at = COALESCE(started_at, now()),
                   updated_at = now()
               WHERE job_id = $1 AND status = 'queued'
               RETURNING *""",
            job_id,
            worker_id,
        )
    return _row_to_job(row) if row else None


async def claim_next_job(worker_id: str) -> dict[str, Any] | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """WITH next_job AS (
                   SELECT job_id
                   FROM ingestion_jobs
                   WHERE status = 'queued'
                   ORDER BY created_at ASC
                   FOR UPDATE SKIP LOCKED
                   LIMIT 1
               )
               UPDATE ingestion_jobs j
               SET status = 'running',
                   attempts = attempts + 1,
                   claimed_by = $1,
                   claimed_at = now(),
                   started_at = COALESCE(started_at, now()),
                   updated_at = now()
               FROM next_job
               WHERE j.job_id = next_job.job_id
               RETURNING j.*""",
            worker_id,
        )
    return _row_to_job(row) if row else None


async def complete_job(job_id: str, stats: dict[str, Any] | None = None) -> None:
    progress = {"phase": "completed", "completion": 100, "stats": stats or {}}
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """UPDATE ingestion_jobs
               SET status = 'completed',
                   progress = COALESCE(progress, '{}'::jsonb) || $1::jsonb,
                   finished_at = now(),
                   updated_at = now()
               WHERE job_id = $2""",
            json.dumps(progress),
            job_id,
        )


async def fail_job(job_id: str, error: str) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """UPDATE ingestion_jobs
               SET status = 'failed',
                   error = $1,
                   finished_at = now(),
                   updated_at = now()
               WHERE job_id = $2""",
            error,
            job_id,
        )


async def get_job(job_id: str) -> dict[str, Any] | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM ingestion_jobs WHERE job_id = $1",
            job_id,
        )
        if not row:
            return None
        logs = await conn.fetch(
            "SELECT log FROM ingestion_job_logs WHERE job_id = $1 ORDER BY created_at ASC",
            job_id,
        )
    job = _row_to_job(row)
    job["logs"] = [r["log"] for r in logs]
    return job


async def list_jobs(limit: int = 50) -> list[dict[str, Any]]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM ingestion_jobs ORDER BY created_at DESC LIMIT $1",
            limit,
        )
        job_ids = [r["job_id"] for r in rows]
        logs_map: dict[str, list[str]] = {job_id: [] for job_id in job_ids}
        if job_ids:
            logs = await conn.fetch(
                """SELECT job_id, log
                   FROM ingestion_job_logs
                   WHERE job_id = ANY($1)
                   ORDER BY created_at ASC""",
                job_ids,
            )
            for row in logs:
                logs_map[row["job_id"]].append(row["log"])

    jobs = []
    for row in rows:
        job = _row_to_job(row)
        job["logs"] = logs_map.get(job["job_id"], [])
        jobs.append(job)
    return jobs


async def pop_job_id(timeout_seconds: int = 1) -> str | None:
    """Pop the next job ID from the Redis queue.

    timeout_seconds must stay below the shared client's socket_timeout (2s)
    or BRPOP will be killed by the socket read timeout before Redis responds.
    """
    client = await get_redis_client()
    if not client:
        return None
    try:
        item = await client.brpop(QUEUE_KEY, timeout=timeout_seconds)
        if not item:
            return None
        _, job_id = item
        return job_id
    except Exception:
        return None


async def recover_stale_running_jobs(stale_after_seconds: int) -> list[str]:
    """Requeue jobs that were left in running state by interrupted workers."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """UPDATE ingestion_jobs
               SET status = 'queued',
                   claimed_by = NULL,
                   claimed_at = NULL,
                   updated_at = now()
               WHERE status = 'running'
                 AND claimed_at IS NOT NULL
                 AND claimed_at < now() - make_interval(secs => $1)
                 AND attempts < max_attempts
               RETURNING job_id""",
            stale_after_seconds,
        )
    return [r["job_id"] for r in rows]


async def request_pause(job_id: str) -> dict[str, Any] | None:
    """Pause queued jobs immediately; request cooperative pause for running jobs."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """UPDATE ingestion_jobs
               SET status = 'paused',
                   claimed_by = NULL,
                   claimed_at = NULL,
                   updated_at = now()
               WHERE job_id = $1 AND status = 'queued'
               RETURNING *""",
            job_id,
        )
        if not row:
            row = await conn.fetchrow(
                """UPDATE ingestion_jobs
                   SET progress = progress || '{"pause_requested": true}'::jsonb,
                       updated_at = now()
                   WHERE job_id = $1 AND status = 'running'
                   RETURNING *""",
                job_id,
            )
    return _row_to_job(row) if row else None


async def mark_job_paused(job_id: str) -> None:
    """Mark a running job as paused after worker checkpoint."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """UPDATE ingestion_jobs
               SET status = 'paused',
                   progress = progress - 'pause_requested',
                   claimed_by = NULL,
                   claimed_at = NULL,
                   updated_at = now()
               WHERE job_id = $1 AND status = 'running'""",
            job_id,
        )


async def resume_job(job_id: str) -> dict[str, Any] | None:
    """Resume a paused job by moving it back to queued state."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """UPDATE ingestion_jobs
               SET status = 'queued',
                   progress = progress - 'pause_requested',
                   updated_at = now()
               WHERE job_id = $1 AND status = 'paused'
               RETURNING *""",
            job_id,
        )
    if not row:
        return None
    client = await get_redis_client()
    if client:
        try:
            await client.lpush(QUEUE_KEY, job_id)
        except Exception:
            pass
    return _row_to_job(row)


async def clear_pause_request(job_id: str) -> dict[str, Any] | None:
    """Clear pending pause request for a running job (acts like resume/continue)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """UPDATE ingestion_jobs
               SET progress = progress - 'pause_requested',
                   updated_at = now()
               WHERE job_id = $1
                 AND status = 'running'
                 AND (progress->>'pause_requested')::boolean IS TRUE
               RETURNING *""",
            job_id,
        )
    return _row_to_job(row) if row else None
