"""FastAPI application — QA endpoint, agent, observability, eval, pipeline trace."""

import asyncio
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.config import settings
from app.db import get_pool, close_pool
from app.retrieval.context_builder import retrieve
from app.generation.answerer import generate_answer
from app.agent.routes import router as agent_router
from app.api.eval_routes import router as eval_router
from app.ws.routes import router as ws_router
from app.telemetry import (
    setup_telemetry, get_tracer, get_current_trace_id,
    get_collector, get_metrics_snapshot,
    record_request_latency,
)
from app.middleware.query_logger import log_query, QueryLogEntry


@asynccontextmanager
async def lifespan(app: FastAPI):
    collector = setup_telemetry()
    app.state.trace_collector = collector
    await get_pool()
    yield
    await close_pool()


app = FastAPI(title="Ovidius Doc QA", version="0.1.0", lifespan=lifespan)
app.include_router(agent_router)
app.include_router(eval_router)
app.include_router(ws_router)

STATIC_DIR = Path(__file__).parent.parent.parent / "static"
ASSETS_DIR = STATIC_DIR / "assets"
if ASSETS_DIR.exists():
    app.mount("/assets", StaticFiles(directory=str(ASSETS_DIR)), name="assets")


# ---------------------------------------------------------------------------
# QA endpoint
# ---------------------------------------------------------------------------

class QARequest(BaseModel):
    question: str
    top_k: int = 5


class CitationResponse(BaseModel):
    index: int
    source_url: str
    source_title: str


class PipelineStep(BaseModel):
    step: str
    duration_ms: float
    detail: str = ""


class QAResponse(BaseModel):
    answer: str
    citations: list[CitationResponse]
    confidence: str
    retrieval_method: str
    chunks_used: int
    parent_chunks_used: int
    pipeline: list[PipelineStep]
    total_ms: float
    trace_id: str | None = None


@app.post("/qa", response_model=QAResponse)
async def ask_question(req: QARequest):
    tracer = get_tracer("api")
    pipeline_steps = []
    total_start = time.perf_counter()

    with tracer.start_as_current_span("qa_request") as span:
        trace_id = get_current_trace_id()
        span.set_attribute("question", req.question[:500])
        span.set_attribute("top_k", req.top_k)
        span.set_attribute("interface", "api")

        t0 = time.perf_counter()
        retrieval_result = await retrieve(req.question, top_k=req.top_k)
        retrieve_ms = round((time.perf_counter() - t0) * 1000, 1)
        pipeline_steps.append(PipelineStep(
            step="hybrid_search_rerank_correct",
            duration_ms=retrieve_ms,
            detail=(
                f"confidence={retrieval_result.corrective.confidence.value}, "
                f"filtered={retrieval_result.corrective.filtered_count}/{retrieval_result.corrective.original_count}"
                f"{', query_retried' if retrieval_result.retry_performed else ''}"
            ),
        ))

        t0 = time.perf_counter()
        result = await generate_answer(req.question, retrieval_result)
        generate_ms = round((time.perf_counter() - t0) * 1000, 1)
        pipeline_steps.append(PipelineStep(
            step="generate_answer",
            duration_ms=generate_ms,
            detail=f"model={settings.generation_model}, parents={result.parent_chunks_used}",
        ))

        total_ms = round((time.perf_counter() - total_start) * 1000, 1)
        span.set_attribute("total_ms", total_ms)
        span.set_attribute("confidence", result.confidence)
        record_request_latency(total_ms, interface="api")

        asyncio.create_task(log_query(QueryLogEntry(
            question=req.question,
            answer=result.answer[:1000],
            citations=[{"index": c.index, "source_url": c.source_url, "source_title": c.source_title} for c in result.citations],
            confidence=result.confidence,
            retrieval_method=result.retrieval_method,
            pipeline_steps=[{"step": s.step, "duration_ms": s.duration_ms, "detail": s.detail} for s in pipeline_steps],
            chunks_used=result.chunks_used,
            parent_chunks_used=result.parent_chunks_used,
            latency_ms=total_ms,
            retrieval_ms=retrieve_ms,
            generation_ms=generate_ms,
            trace_id=trace_id,
            interface="api",
        )))

    return QAResponse(
        answer=result.answer,
        citations=[
            CitationResponse(
                index=c.index,
                source_url=c.source_url,
                source_title=c.source_title,
            )
            for c in result.citations
        ],
        confidence=result.confidence,
        retrieval_method=result.retrieval_method,
        chunks_used=result.chunks_used,
        parent_chunks_used=result.parent_chunks_used,
        pipeline=pipeline_steps,
        total_ms=total_ms,
        trace_id=trace_id,
    )


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    pool = await get_pool()
    async with pool.acquire() as conn:
        child_count = await conn.fetchval("SELECT count(*) FROM documents")
        parent_count = await conn.fetchval("SELECT count(*) FROM parent_chunks")
    return {
        "status": "ok",
        "child_chunks": child_count,
        "parent_chunks": parent_count,
    }


# ---------------------------------------------------------------------------
# Observability endpoints
# ---------------------------------------------------------------------------

@app.get("/traces")
async def list_traces(limit: int = 50):
    collector = get_collector()
    if not collector:
        return []
    return collector.get_recent_traces(limit=limit)


@app.get("/traces/{trace_id}")
async def get_trace(trace_id: str):
    collector = get_collector()
    if not collector:
        raise HTTPException(status_code=404, detail="Trace collector not initialized")
    trace = collector.get_trace(trace_id)
    if not trace:
        raise HTTPException(status_code=404, detail="Trace not found")
    return trace


@app.get("/metrics")
async def metrics_endpoint():
    return get_metrics_snapshot()


@app.get("/query-logs")
async def get_query_logs(
    interface: str | None = None,
    session_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
):
    pool = await get_pool()
    async with pool.acquire() as conn:
        conditions = []
        params = []
        idx = 1

        if interface:
            conditions.append(f"interface = ${idx}")
            params.append(interface)
            idx += 1
        if session_id:
            conditions.append(f"session_id = ${idx}")
            params.append(session_id)
            idx += 1

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        params.extend([limit, offset])

        rows = await conn.fetch(
            f"""SELECT id, question, answer, citations, confidence, retrieval_method,
                       pipeline_steps, chunks_used, parent_chunks_used,
                       latency_ms, retrieval_ms, generation_ms,
                       trace_id, session_id, interface, created_at
                FROM query_logs {where}
                ORDER BY created_at DESC
                LIMIT ${idx} OFFSET ${idx + 1}""",
            *params,
        )

    return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@app.get("/")
async def dashboard():
    index = STATIC_DIR / "index.html"
    if not index.exists():
        return {"status": "ok", "message": "Ovidius Doc QA API. Dashboard not yet built."}
    return FileResponse(str(index))
