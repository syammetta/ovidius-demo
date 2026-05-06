"""Vector similarity search against Postgres pgvector."""

from dataclasses import dataclass

import numpy as np

from app.config import settings
from app.db import get_pool
from app.ingestion.embedder import embed_texts


@dataclass
class RetrievedChunk:
    chunk_id: str
    parent_id: str
    content: str
    contextual_content: str | None
    source_url: str
    source_title: str
    section: str
    document_type: str
    score: float
    retrieval_method: str = "vector"


async def vector_search(query: str, top_n: int | None = None) -> list[RetrievedChunk]:
    """Embed query and retrieve top-N similar chunks from pgvector."""
    top_n = top_n or settings.retrieval_top_n

    embeddings = await embed_texts([query])
    query_vec = np.array(embeddings[0], dtype=np.float32)

    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT chunk_id, parent_id, content, contextual_content,
                   source_url, source_title, section, document_type,
                   1 - (embedding <=> $1) AS similarity
            FROM documents
            ORDER BY embedding <=> $1
            LIMIT $2
            """,
            query_vec,
            top_n,
        )

    return [
        RetrievedChunk(
            chunk_id=row["chunk_id"],
            parent_id=row["parent_id"],
            content=row["content"],
            contextual_content=row["contextual_content"],
            source_url=row["source_url"],
            source_title=row["source_title"],
            section=row["section"],
            document_type=row["document_type"],
            score=float(row["similarity"]),
            retrieval_method="vector",
        )
        for row in rows
    ]


async def bm25_search(query: str, top_n: int | None = None) -> list[RetrievedChunk]:
    """Full-text search using Postgres tsvector/tsquery (BM25-style ranking)."""
    top_n = top_n or settings.retrieval_top_n

    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT chunk_id, parent_id, content, contextual_content,
                   source_url, source_title, section, document_type,
                   ts_rank_cd(tsv, websearch_to_tsquery('english', $1)) AS rank
            FROM documents
            WHERE tsv @@ websearch_to_tsquery('english', $1)
            ORDER BY rank DESC
            LIMIT $2
            """,
            query,
            top_n,
        )

    return [
        RetrievedChunk(
            chunk_id=row["chunk_id"],
            parent_id=row["parent_id"],
            content=row["content"],
            contextual_content=row["contextual_content"],
            source_url=row["source_url"],
            source_title=row["source_title"],
            section=row["section"],
            document_type=row["document_type"],
            score=float(row["rank"]),
            retrieval_method="bm25",
        )
        for row in rows
    ]
