"""FastAPI application — QA endpoint, agent, dashboard, pipeline observability."""

import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.db import get_pool, close_pool
from app.retrieval.context_builder import retrieve
from app.generation.answerer import generate_answer
from app.agent.routes import router as agent_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    await get_pool()
    yield
    await close_pool()


app = FastAPI(title="Ovidius Doc QA", version="0.1.0", lifespan=lifespan)
app.include_router(agent_router)

STATIC_DIR = Path(__file__).parent.parent.parent / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


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


@app.post("/qa", response_model=QAResponse)
async def ask_question(req: QARequest):
    pipeline_steps = []
    total_start = time.perf_counter()

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
    )


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


@app.get("/")
async def dashboard():
    return FileResponse(str(STATIC_DIR / "index.html"))


from app.config import settings  # noqa: E402
