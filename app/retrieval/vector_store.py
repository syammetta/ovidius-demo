"""Vector similarity search against Postgres pgvector."""

import time
from dataclasses import dataclass

import numpy as np

from app.config import settings
from app.db import get_pool
from app.ingestion.embedder import embed_texts
from app.telemetry import get_tracer


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
    tracer = get_tracer("vector_store")
    top_n = top_n or settings.retrieval_top_n

    with tracer.start_as_current_span("vector_search") as span:
        span.set_attribute("top_n", top_n)

        t0 = time.perf_counter()
        embeddings = await embed_texts([query])
        embed_ms = round((time.perf_counter() - t0) * 1000, 1)
        span.set_attribute("query_embed_ms", embed_ms)

        query_vec = np.array(embeddings[0], dtype=np.float32)

        t1 = time.perf_counter()
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
        db_ms = round((time.perf_counter() - t1) * 1000, 1)
        span.set_attribute("db_query_ms", db_ms)
        span.set_attribute("result_count", len(rows))
        if rows:
            span.set_attribute("top_score", float(rows[0]["similarity"]))

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


async def metadata_vector_search(
    query: str,
    doc_types: list[str],
    top_n: int | None = None,
) -> list[RetrievedChunk]:
    """Vector search filtered to specific document types — the metadata-boosted lane."""
    if not doc_types:
        return []

    tracer = get_tracer("vector_store")
    top_n = top_n or settings.retrieval_top_n

    with tracer.start_as_current_span("metadata_vector_search") as span:
        span.set_attribute("top_n", top_n)
        span.set_attribute("doc_types", ",".join(doc_types))

        t0 = time.perf_counter()
        embeddings = await embed_texts([query])
        embed_ms = round((time.perf_counter() - t0) * 1000, 1)
        span.set_attribute("query_embed_ms", embed_ms)

        query_vec = np.array(embeddings[0], dtype=np.float32)

        t1 = time.perf_counter()
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT chunk_id, parent_id, content, contextual_content,
                       source_url, source_title, section, document_type,
                       1 - (embedding <=> $1) AS similarity
                FROM documents
                WHERE document_type = ANY($3)
                ORDER BY embedding <=> $1
                LIMIT $2
                """,
                query_vec,
                top_n,
                doc_types,
            )
        db_ms = round((time.perf_counter() - t1) * 1000, 1)
        span.set_attribute("db_query_ms", db_ms)
        span.set_attribute("result_count", len(rows))

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
            retrieval_method="metadata_vector",
        )
        for row in rows
    ]


async def bm25_search(query: str, top_n: int | None = None) -> list[RetrievedChunk]:
    """Full-text search using Postgres tsvector/tsquery (BM25-style ranking)."""
    tracer = get_tracer("vector_store")
    top_n = top_n or settings.retrieval_top_n

    with tracer.start_as_current_span("bm25_search") as span:
        span.set_attribute("top_n", top_n)

        t0 = time.perf_counter()
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
        db_ms = round((time.perf_counter() - t0) * 1000, 1)
        span.set_attribute("db_query_ms", db_ms)
        span.set_attribute("result_count", len(rows))
        if rows:
            span.set_attribute("top_score", float(rows[0]["rank"]))

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
