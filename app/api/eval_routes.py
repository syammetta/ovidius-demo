"""Evaluation API — trigger runs, fetch results, view history."""

import uuid

from fastapi import APIRouter, BackgroundTasks, HTTPException

from app.db import get_pool

router = APIRouter(prefix="/eval", tags=["eval"])


@router.post("/run")
async def trigger_eval_run(background_tasks: BackgroundTasks):
    """Trigger an evaluation run in the background. Returns run_id immediately."""
    from eval.runner import run_eval_persisted

    run_id = str(uuid.uuid4())
    background_tasks.add_task(run_eval_persisted, run_id)
    return {"run_id": run_id, "status": "started"}


@router.get("/runs")
async def list_eval_runs(limit: int = 20):
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT run_id, started_at, finished_at, config, metrics,
                      pair_count, status
               FROM eval_runs
               ORDER BY started_at DESC
               LIMIT $1""",
            limit,
        )
    return [dict(row) for row in rows]


@router.get("/runs/{run_id}")
async def get_eval_run(run_id: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        run_row = await conn.fetchrow(
            "SELECT * FROM eval_runs WHERE run_id = $1", run_id,
        )
        if not run_row:
            raise HTTPException(status_code=404, detail="Eval run not found")

        result_rows = await conn.fetch(
            """SELECT pair_id, tier, question, expected_answer, actual_answer,
                      contexts, metrics, trace_id, created_at
               FROM eval_results
               WHERE run_id = $1
               ORDER BY created_at""",
            run_id,
        )

    return {
        "run": dict(run_row),
        "results": [dict(r) for r in result_rows],
    }


@router.delete("/runs/{run_id}")
async def delete_eval_run(run_id: str):
    """Delete an eval run and all its results (CASCADE)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "DELETE FROM eval_runs WHERE run_id = $1 RETURNING run_id", run_id,
        )
        if not row:
            raise HTTPException(status_code=404, detail="Eval run not found")
    return {"deleted": run_id}


@router.get("/results")
async def get_eval_results(
    run_id: str | None = None,
    tier: str | None = None,
    limit: int = 50,
):
    pool = await get_pool()
    async with pool.acquire() as conn:
        conditions = []
        params = []
        idx = 1

        if run_id:
            conditions.append(f"run_id = ${idx}")
            params.append(run_id)
            idx += 1
        if tier:
            conditions.append(f"tier = ${idx}")
            params.append(tier)
            idx += 1

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        params.append(limit)

        rows = await conn.fetch(
            f"""SELECT er.*, ev.config as run_config
                FROM eval_results er
                JOIN eval_runs ev ON er.run_id = ev.run_id
                {where}
                ORDER BY er.created_at DESC
                LIMIT ${idx}""",
            *params,
        )

    return [dict(r) for r in rows]


@router.get("/summary")
async def eval_summary():
    """Aggregate metrics across all completed eval runs."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        runs = await conn.fetch(
            """SELECT run_id, started_at, metrics, pair_count
               FROM eval_runs
               WHERE status = 'completed'
               ORDER BY started_at DESC
               LIMIT 10""",
        )
        tier_stats = await conn.fetch(
            """SELECT tier,
                      count(*) as count,
                      avg((metrics->>'faithfulness')::float) as avg_faithfulness,
                      avg((metrics->>'answer_relevancy')::float) as avg_relevancy,
                      avg((metrics->>'context_precision')::float) as avg_precision
               FROM eval_results
               WHERE run_id = (SELECT run_id FROM eval_runs WHERE status = 'completed' ORDER BY started_at DESC LIMIT 1)
               GROUP BY tier
               ORDER BY tier""",
        )

    return {
        "recent_runs": [dict(r) for r in runs],
        "tier_breakdown": [dict(r) for r in tier_stats],
    }
