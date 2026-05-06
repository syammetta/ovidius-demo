"""Cross-encoder reranking using FlashRank.

Replaces the naive LLM-as-reranker approach with a dedicated cross-encoder model.
FlashRank is ~4MB, runs on CPU, no PyTorch dependency, and delivers ~95% of LLM
reranking accuracy at 100x the speed.

For production at scale, benchmark against Cohere Rerank v3 or a fine-tuned
cross-encoder (bge-reranker-v2-m3).
"""

from flashrank import Ranker, RerankRequest

from app.config import settings
from app.retrieval.vector_store import RetrievedChunk

_ranker: Ranker | None = None


def _get_ranker() -> Ranker:
    global _ranker
    if _ranker is None:
        _ranker = Ranker()
    return _ranker


async def rerank(
    query: str,
    chunks: list[RetrievedChunk],
    top_k: int | None = None,
) -> list[RetrievedChunk]:
    """Rerank chunks using FlashRank cross-encoder, return top-K."""
    top_k = top_k or settings.rerank_top_k

    if len(chunks) <= top_k:
        return chunks

    ranker = _get_ranker()

    passages = [
        {"id": chunk.chunk_id, "text": chunk.contextual_content or chunk.content}
        for chunk in chunks
    ]

    rerank_request = RerankRequest(query=query, passages=passages)
    results = ranker.rerank(rerank_request)

    chunk_map = {c.chunk_id: c for c in chunks}
    reranked = []
    for result in results[:top_k]:
        chunk = chunk_map.get(result["id"])
        if chunk:
            chunk.score = float(result["score"])
            chunk.retrieval_method = f"{chunk.retrieval_method}+rerank"
            reranked.append(chunk)

    return reranked
