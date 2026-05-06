"""Hybrid search: combine vector similarity, BM25 keyword search, and optional
metadata-boosted vector search via Reciprocal Rank Fusion.

Two-lane (default): vector + BM25
Three-lane (metadata-aware): vector + BM25 + metadata-filtered vector

The metadata lane ensures chunks matching the classified document type get an
RRF score boost without hard-filtering out other results.

Reference: Cormack, Clarke, Buettcher (2009) — Reciprocal Rank Fusion
"""

from dataclasses import dataclass

from app.retrieval.vector_store import (
    RetrievedChunk,
    vector_search,
    bm25_search,
    metadata_vector_search,
)

RRF_K = 60


@dataclass
class HybridSearchResult:
    chunks: list[RetrievedChunk]
    vector_count: int
    bm25_count: int
    both_count: int
    metadata_boosted_count: int = 0
    lanes_used: int = 2


def reciprocal_rank_fusion(
    *ranked_lists: list[RetrievedChunk],
    top_n: int = 20,
) -> list[RetrievedChunk]:
    """Fuse N ranked lists using Reciprocal Rank Fusion."""
    chunk_scores: dict[str, float] = {}
    chunk_map: dict[str, RetrievedChunk] = {}

    for result_list in ranked_lists:
        for rank, chunk in enumerate(result_list):
            rrf_score = 1.0 / (rank + RRF_K)
            chunk_scores[chunk.chunk_id] = chunk_scores.get(chunk.chunk_id, 0) + rrf_score
            if chunk.chunk_id not in chunk_map:
                chunk_map[chunk.chunk_id] = chunk

    sorted_ids = sorted(chunk_scores.keys(), key=lambda cid: chunk_scores[cid], reverse=True)

    fused = []
    for chunk_id in sorted_ids[:top_n]:
        chunk = chunk_map[chunk_id]
        chunk.score = chunk_scores[chunk_id]
        chunk.retrieval_method = "hybrid_rrf"
        fused.append(chunk)

    return fused


async def hybrid_search(
    query: str,
    top_n: int = 20,
    boost_doc_types: list[str] | None = None,
) -> HybridSearchResult:
    """Run hybrid search with optional metadata-boosted third lane.

    When boost_doc_types is provided, a third retrieval lane runs a vector
    search filtered to those document types. RRF fusion naturally promotes
    chunks that appear in multiple lanes.
    """
    vector_results = await vector_search(query, top_n=top_n)
    bm25_results = await bm25_search(query, top_n=top_n)

    vector_ids = {c.chunk_id for c in vector_results}
    bm25_ids = {c.chunk_id for c in bm25_results}

    lanes: list[list[RetrievedChunk]] = [vector_results, bm25_results]
    metadata_ids: set[str] = set()
    lanes_used = 2

    if boost_doc_types:
        metadata_results = await metadata_vector_search(query, boost_doc_types, top_n=top_n)
        if metadata_results:
            lanes.append(metadata_results)
            metadata_ids = {c.chunk_id for c in metadata_results}
            lanes_used = 3

    fused = reciprocal_rank_fusion(*lanes, top_n=top_n)
    fused_ids = {c.chunk_id for c in fused}

    both = fused_ids & vector_ids & bm25_ids
    vector_only = fused_ids & vector_ids - bm25_ids - metadata_ids
    bm25_only = fused_ids & bm25_ids - vector_ids - metadata_ids
    metadata_boosted = fused_ids & metadata_ids - (vector_ids & bm25_ids)

    return HybridSearchResult(
        chunks=fused,
        vector_count=len(vector_only),
        bm25_count=len(bm25_only),
        both_count=len(both),
        metadata_boosted_count=len(metadata_boosted),
        lanes_used=lanes_used,
    )
