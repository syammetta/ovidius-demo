"""Full retrieval orchestrator: hybrid search → rerank → corrective evaluation → parent expansion.

This is the shared retrieval core that every interface (API, agent, MCP, copilot) calls.
The pipeline:
1. Hybrid search (vector + BM25 + RRF) for broad candidate retrieval
2. Cross-encoder reranking for precision
3. Corrective RAG evaluation for confidence routing
4. Parent chunk expansion for generation context

Every stage is instrumented with OpenTelemetry spans.
"""

import time
from dataclasses import dataclass

from app.db import get_pool
from app.retrieval.hybrid_search import hybrid_search
from app.retrieval.reranker import rerank
from app.retrieval.corrective import evaluate_retrieval, CorrectedRetrieval, RetrievalConfidence
from app.retrieval.vector_store import RetrievedChunk
from app.telemetry import (
    get_tracer,
    record_confidence,
    record_chunks_retrieved,
    record_retrieval_latency,
    record_rerank_latency,
)


@dataclass
class RetrievalResult:
    """Full retrieval result with child chunks, parent context, and pipeline metadata."""
    children: list[RetrievedChunk]
    parent_contents: dict[str, str]
    corrective: CorrectedRetrieval
    retry_performed: bool = False


async def retrieve(query: str, top_k: int | None = None) -> RetrievalResult:
    """Full retrieval pipeline: hybrid search → rerank → corrective → parent expansion."""
    tracer = get_tracer("retrieval")
    t_start = time.perf_counter()

    with tracer.start_as_current_span("retrieve_pipeline") as pipeline_span:
        pipeline_span.set_attribute("query", query[:500])
        pipeline_span.set_attribute("top_k", top_k or 5)

        with tracer.start_as_current_span("hybrid_search") as hs_span:
            candidates = await hybrid_search(query, top_n=20)
            hs_span.set_attribute("candidate_count", len(candidates))

        with tracer.start_as_current_span("rerank") as rr_span:
            t_rr = time.perf_counter()
            reranked = await rerank(query, candidates, top_k=top_k)
            rr_ms = round((time.perf_counter() - t_rr) * 1000, 1)
            rr_span.set_attribute("input_count", len(candidates))
            rr_span.set_attribute("output_count", len(reranked))
            rr_span.set_attribute("duration_ms", rr_ms)
            record_rerank_latency(rr_ms)

        with tracer.start_as_current_span("corrective_eval") as ce_span:
            corrected = await evaluate_retrieval(query, reranked)
            ce_span.set_attribute("confidence", corrected.confidence.value)
            ce_span.set_attribute("filtered_count", corrected.filtered_count)
            ce_span.set_attribute("original_count", corrected.original_count)
            record_confidence(corrected.confidence.value)

        retry_performed = False
        if corrected.confidence == RetrievalConfidence.LOW_CONFIDENCE and corrected.transformed_query:
            with tracer.start_as_current_span("query_transform_retry") as retry_span:
                retry_span.set_attribute("original_query", query[:200])
                retry_span.set_attribute("transformed_query", corrected.transformed_query[:200])

                retry_candidates = await hybrid_search(corrected.transformed_query, top_n=20)
                retry_reranked = await rerank(corrected.transformed_query, retry_candidates, top_k=top_k)
                retry_corrected = await evaluate_retrieval(corrected.transformed_query, retry_reranked)

                retry_span.set_attribute("retry_confidence", retry_corrected.confidence.value)
                retry_span.set_attribute("retry_filtered_count", retry_corrected.filtered_count)

                if retry_corrected.filtered_count > corrected.filtered_count:
                    corrected = retry_corrected
                    retry_performed = True

        with tracer.start_as_current_span("parent_fetch") as pf_span:
            parent_ids = list({c.parent_id for c in corrected.chunks if c.parent_id})
            pf_span.set_attribute("parent_count", len(parent_ids))
            parent_contents = await _fetch_parents(parent_ids)
            pf_span.set_attribute("parents_found", len(parent_contents))

        total_ms = round((time.perf_counter() - t_start) * 1000, 1)
        pipeline_span.set_attribute("total_ms", total_ms)
        pipeline_span.set_attribute("final_chunk_count", len(corrected.chunks))
        pipeline_span.set_attribute("confidence", corrected.confidence.value)
        pipeline_span.set_attribute("retry_performed", retry_performed)

        record_retrieval_latency(total_ms)
        record_chunks_retrieved(len(corrected.chunks))

    return RetrievalResult(
        children=corrected.chunks,
        parent_contents=parent_contents,
        corrective=corrected,
        retry_performed=retry_performed,
    )


async def _fetch_parents(parent_ids: list[str]) -> dict[str, str]:
    """Fetch parent chunk content for generation context."""
    if not parent_ids:
        return {}

    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT parent_id, content FROM parent_chunks WHERE parent_id = ANY($1)",
            parent_ids,
        )

    return {row["parent_id"]: row["content"] for row in rows}
