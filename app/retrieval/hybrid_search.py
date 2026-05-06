"""Hybrid search: combine vector similarity and BM25 keyword search via Reciprocal Rank Fusion.

Vector search captures semantic similarity ("car" ↔ "automobile").
BM25 captures exact term matches ("claude-sonnet-4-6", "pgvector").
RRF fuses both ranked lists without needing score normalization.

Reference: Cormack, Clarke, Buettcher (2009) — Reciprocal Rank Fusion
"""

from app.retrieval.vector_store import RetrievedChunk, vector_search, bm25_search

RRF_K = 60


def reciprocal_rank_fusion(
    *result_lists: list[RetrievedChunk],
    top_n: int = 20,
) -> list[RetrievedChunk]:
    """Fuse multiple ranked lists using Reciprocal Rank Fusion.

    RRF score = sum(1 / (rank_i + k)) across all lists.
    Uses only rank position, not raw scores — robust to score distribution differences.
    """
    chunk_scores: dict[str, float] = {}
    chunk_map: dict[str, RetrievedChunk] = {}

    for result_list in result_lists:
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


async def hybrid_search(query: str, top_n: int = 20) -> list[RetrievedChunk]:
    """Run both vector and BM25 search, fuse results with RRF."""
    vector_results = await vector_search(query, top_n=top_n)
    bm25_results = await bm25_search(query, top_n=top_n)

    return reciprocal_rank_fusion(vector_results, bm25_results, top_n=top_n)
