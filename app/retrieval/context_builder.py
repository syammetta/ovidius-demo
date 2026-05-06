"""Full retrieval orchestrator: classify → hybrid search → rerank → corrective → parent expansion.

This is the shared retrieval core that every interface (API, agent, MCP, copilot) calls.
The pipeline:
0. Query classification — determine intent, topics, and strategy
1. Hybrid search (vector + BM25 + metadata-boosted + RRF) for broad candidate retrieval
2. Cross-encoder reranking for precision
3. Corrective RAG evaluation for confidence routing
4. Parent chunk expansion for generation context

Every stage is instrumented with OpenTelemetry spans.
"""

import time
from dataclasses import dataclass
from typing import Callable, Awaitable

from app.db import get_pool
from app.retrieval.classifier import classify_query, QueryClassification, RetrievalStrategy
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
    classification: QueryClassification | None = None
    strategy: RetrievalStrategy | None = None


ProgressCallback = Callable[[str, str, dict], Awaitable[None]]


async def _noop_progress(stage: str, status: str, detail: dict) -> None:
    pass


async def retrieve(
    query: str,
    top_k: int | None = None,
    on_progress: ProgressCallback | None = None,
) -> RetrievalResult:
    """Full retrieval pipeline: classify → hybrid search → rerank → corrective → parent expansion."""
    tracer = get_tracer("retrieval")
    t_start = time.perf_counter()
    progress = on_progress or _noop_progress

    with tracer.start_as_current_span("retrieve_pipeline") as pipeline_span:
        pipeline_span.set_attribute("query", query[:500])

        # Stage 0: Classify query
        await progress("classify", "running", {})
        with tracer.start_as_current_span("classify_query") as cls_span:
            t_cls = time.perf_counter()
            classification, strategy = await classify_query(query)
            cls_ms = round((time.perf_counter() - t_cls) * 1000, 1)
            cls_span.set_attribute("intent", classification.intent)
            cls_span.set_attribute("topics", ",".join(classification.topics))
            cls_span.set_attribute("strategy", strategy.name)

        effective_top_k = top_k or strategy.top_k
        pipeline_span.set_attribute("top_k", effective_top_k)
        pipeline_span.set_attribute("intent", classification.intent)
        pipeline_span.set_attribute("strategy", strategy.name)

        await progress("classify", "complete", {
            "intent": classification.intent,
            "topics": classification.topics,
            "doc_types": classification.doc_types,
            "sections": classification.sections,
            "strategy": strategy.name,
            "strategy_desc": strategy.description,
            "reasoning": classification.reasoning,
            "duration_ms": cls_ms,
        })

        # Stage 1: Hybrid search (with optional metadata boost)
        await progress("hybrid_search", "running", {})
        with tracer.start_as_current_span("hybrid_search") as hs_span:
            t_hs = time.perf_counter()
            hs_result = await hybrid_search(
                query,
                top_n=strategy.top_n,
                boost_doc_types=strategy.boost_doc_types if strategy.metadata_boost else None,
            )
            candidates = hs_result.chunks
            hs_ms = round((time.perf_counter() - t_hs) * 1000, 1)
            hs_span.set_attribute("candidate_count", len(candidates))
            hs_span.set_attribute("vector_count", hs_result.vector_count)
            hs_span.set_attribute("bm25_count", hs_result.bm25_count)
            hs_span.set_attribute("both_count", hs_result.both_count)
            hs_span.set_attribute("metadata_boosted", hs_result.metadata_boosted_count)
            hs_span.set_attribute("lanes_used", hs_result.lanes_used)
        await progress("hybrid_search", "complete", {
            "candidates": len(candidates),
            "vector_hits": hs_result.vector_count,
            "bm25_hits": hs_result.bm25_count,
            "both_hits": hs_result.both_count,
            "metadata_boosted": hs_result.metadata_boosted_count,
            "lanes": hs_result.lanes_used,
            "duration_ms": hs_ms,
        })

        # Stage 2: Cross-encoder rerank
        await progress("rerank", "running", {})
        with tracer.start_as_current_span("rerank") as rr_span:
            t_rr = time.perf_counter()
            reranked = await rerank(query, candidates, top_k=effective_top_k)
            rr_ms = round((time.perf_counter() - t_rr) * 1000, 1)
            rr_span.set_attribute("input_count", len(candidates))
            rr_span.set_attribute("output_count", len(reranked))
            rr_span.set_attribute("duration_ms", rr_ms)
            record_rerank_latency(rr_ms)
        await progress("rerank", "complete", {"input": len(candidates), "output": len(reranked), "duration_ms": rr_ms})

        # Stage 3: Corrective RAG evaluation
        await progress("corrective_eval", "running", {})
        with tracer.start_as_current_span("corrective_eval") as ce_span:
            t_ce = time.perf_counter()
            corrected = await evaluate_retrieval(query, reranked)
            ce_ms = round((time.perf_counter() - t_ce) * 1000, 1)
            ce_span.set_attribute("confidence", corrected.confidence.value)
            ce_span.set_attribute("filtered_count", corrected.filtered_count)
            ce_span.set_attribute("original_count", corrected.original_count)
            record_confidence(corrected.confidence.value)
        await progress("corrective_eval", "complete", {
            "confidence": corrected.confidence.value,
            "filtered": corrected.filtered_count,
            "original": corrected.original_count,
            "duration_ms": ce_ms,
        })

        # Stage 3b: Query transform & retry (only if low confidence)
        retry_performed = False
        if corrected.confidence == RetrievalConfidence.LOW_CONFIDENCE and corrected.transformed_query:
            await progress("query_retry", "running", {"transformed_query": corrected.transformed_query})
            with tracer.start_as_current_span("query_transform_retry") as retry_span:
                retry_span.set_attribute("original_query", query[:200])
                retry_span.set_attribute("transformed_query", corrected.transformed_query[:200])

                retry_hs = await hybrid_search(
                    corrected.transformed_query,
                    top_n=strategy.top_n,
                    boost_doc_types=strategy.boost_doc_types if strategy.metadata_boost else None,
                )
                retry_reranked = await rerank(corrected.transformed_query, retry_hs.chunks, top_k=effective_top_k)
                retry_corrected = await evaluate_retrieval(corrected.transformed_query, retry_reranked)

                retry_span.set_attribute("retry_confidence", retry_corrected.confidence.value)
                retry_span.set_attribute("retry_filtered_count", retry_corrected.filtered_count)

                if retry_corrected.filtered_count > corrected.filtered_count:
                    corrected = retry_corrected
                    retry_performed = True
            await progress("query_retry", "complete", {
                "improved": retry_performed,
                "transformed_query": corrected.transformed_query,
                "retry_confidence": corrected.confidence.value if retry_performed else None,
            })

        # Stage 4: Parent chunk expansion
        await progress("parent_fetch", "running", {})
        with tracer.start_as_current_span("parent_fetch") as pf_span:
            t_pf = time.perf_counter()
            parent_ids = list({c.parent_id for c in corrected.chunks if c.parent_id})
            pf_span.set_attribute("parent_count", len(parent_ids))
            parent_contents = await _fetch_parents(parent_ids)
            pf_ms = round((time.perf_counter() - t_pf) * 1000, 1)
            pf_span.set_attribute("parents_found", len(parent_contents))

            doc_type_counts: dict[str, int] = {}
            for c in corrected.chunks:
                doc_type_counts[c.document_type] = doc_type_counts.get(c.document_type, 0) + 1

        await progress("parent_fetch", "complete", {
            "parents": len(parent_contents),
            "doc_types": doc_type_counts,
            "duration_ms": pf_ms,
        })

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
        classification=classification,
        strategy=strategy,
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
