"""Query logging — persist every query from every interface for analytics.

Each interface (API, agent, MCP, copilot) calls log_query() after
completing a request. The trace_id is auto-extracted from the current
OpenTelemetry context if not provided.
"""

import json
from dataclasses import dataclass, field

from app.db import get_pool
from app.telemetry import get_current_trace_id


@dataclass
class QueryLogEntry:
    question: str
    answer: str = ""
    citations: list[dict] | None = None
    confidence: str | None = None
    retrieval_method: str | None = None
    pipeline_steps: list[dict] | None = None
    chunks_used: int | None = None
    parent_chunks_used: int | None = None
    latency_ms: float | None = None
    retrieval_ms: float | None = None
    generation_ms: float | None = None
    trace_id: str | None = None
    session_id: str | None = None
    interface: str = "api"


async def log_query(entry: QueryLogEntry) -> None:
    """Insert a query log entry. Fire-and-forget safe — swallows exceptions."""
    try:
        if entry.trace_id is None:
            entry.trace_id = get_current_trace_id()

        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO query_logs
                   (question, answer, citations, confidence, retrieval_method,
                    pipeline_steps, chunks_used, parent_chunks_used,
                    latency_ms, retrieval_ms, generation_ms,
                    trace_id, session_id, interface)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)""",
                entry.question,
                entry.answer,
                json.dumps(entry.citations or []),
                entry.confidence,
                entry.retrieval_method,
                json.dumps(entry.pipeline_steps or []),
                entry.chunks_used,
                entry.parent_chunks_used,
                entry.latency_ms,
                entry.retrieval_ms,
                entry.generation_ms,
                entry.trace_id,
                entry.session_id,
                entry.interface,
            )
    except Exception:
        pass
