"""Full retrieval orchestrator: hybrid search → rerank → corrective evaluation → parent expansion.

This is the shared retrieval core that every interface (API, agent, MCP, copilot) calls.
The pipeline:
1. Hybrid search (vector + BM25 + RRF) for broad candidate retrieval
2. Cross-encoder reranking for precision
3. Corrective RAG evaluation for confidence routing
4. Parent chunk expansion for generation context
"""

from dataclasses import dataclass

from app.db import get_pool
from app.retrieval.hybrid_search import hybrid_search
from app.retrieval.reranker import rerank
from app.retrieval.corrective import evaluate_retrieval, CorrectedRetrieval, RetrievalConfidence
from app.retrieval.vector_store import RetrievedChunk


@dataclass
class RetrievalResult:
    """Full retrieval result with child chunks, parent context, and pipeline metadata."""
    children: list[RetrievedChunk]
    parent_contents: dict[str, str]
    corrective: CorrectedRetrieval
    retry_performed: bool = False


async def retrieve(query: str, top_k: int | None = None) -> RetrievalResult:
    """Full retrieval pipeline: hybrid search → rerank → corrective → parent expansion."""

    candidates = await hybrid_search(query, top_n=20)
    reranked = await rerank(query, candidates, top_k=top_k)
    corrected = await evaluate_retrieval(query, reranked)

    retry_performed = False
    if corrected.confidence == RetrievalConfidence.LOW_CONFIDENCE and corrected.transformed_query:
        retry_candidates = await hybrid_search(corrected.transformed_query, top_n=20)
        retry_reranked = await rerank(corrected.transformed_query, retry_candidates, top_k=top_k)
        retry_corrected = await evaluate_retrieval(corrected.transformed_query, retry_reranked)

        if retry_corrected.filtered_count > corrected.filtered_count:
            corrected = retry_corrected
            retry_performed = True

    parent_ids = list({c.parent_id for c in corrected.chunks if c.parent_id})
    parent_contents = await _fetch_parents(parent_ids)

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
