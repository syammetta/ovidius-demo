"""Worker service entrypoint with HTTP health endpoint for Railway."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.db import close_pool, get_pool
from app.ingestion.worker import run_worker_loop

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await get_pool()
    worker_task = asyncio.create_task(run_worker_loop(worker_id="railway-worker"))
    app.state.worker_task = worker_task
    logger.info("Worker service started")
    try:
        yield
    finally:
        task = getattr(app.state, "worker_task", None)
        if task and not task.done():
            task.cancel()
        await close_pool()
        logger.info("Worker service stopped")


app = FastAPI(title="Ovidius Ingestion Worker", lifespan=lifespan)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "ingestion-worker"}

