"""Embed contextualized chunks and store parent-child structure in Postgres pgvector.

Embedding calls go through a Redis cache layer — identical texts skip the
Voyage API entirely. This helps at query time (same query = instant embedding)
and during re-ingestion (unchanged chunks skip the API call).
"""

import time

import numpy as np
import voyageai

from app.config import settings
from app.db import get_pool
from app.cache import get_cached_embeddings, cache_embeddings
from app.ingestion.chunker import ParentChunk, ChildChunk
from app.telemetry import get_tracer


async def embed_texts(
    texts: list[str],
    input_type: str = "document",
) -> list[list[float]]:
    """Generate embeddings via Voyage AI with Redis caching.

    input_type should be "document" for indexing and "query" for search.
    """
    if not texts:
        return []

    tracer = get_tracer("embedder")

    with tracer.start_as_current_span("embed_texts") as span:
        span.set_attribute("total_texts", len(texts))
        span.set_attribute("model", settings.embedding_model)
        span.set_attribute("input_type", input_type)

        cached = await get_cached_embeddings(texts, input_type)
        cache_hits = len(cached)
        span.set_attribute("cache_hits", cache_hits)

        if cache_hits == len(texts):
            span.set_attribute("cache_miss", 0)
            span.set_attribute("voyage_api_calls", 0)
            return [cached[i] for i in range(len(texts))]

        miss_indices = [i for i in range(len(texts)) if i not in cached]
        miss_texts = [texts[i] for i in miss_indices]
        span.set_attribute("cache_miss", len(miss_texts))

        client = voyageai.Client(api_key=settings.voyage_api_key)
        fresh_embeddings: list[list[float]] = []
        batch_size = 50
        api_calls = 0

        for i in range(0, len(miss_texts), batch_size):
            batch = miss_texts[i : i + batch_size]
            with tracer.start_as_current_span("voyage_embed_batch") as batch_span:
                batch_span.set_attribute("batch_size", len(batch))
                batch_span.set_attribute("batch_index", i // batch_size)
                t0 = time.perf_counter()
                result = client.embed(
                    batch,
                    model=settings.embedding_model,
                    input_type=input_type,
                )
                api_ms = round((time.perf_counter() - t0) * 1000, 1)
                batch_span.set_attribute("api_latency_ms", api_ms)
                if hasattr(result, "total_tokens"):
                    batch_span.set_attribute("total_tokens", result.total_tokens)
            fresh_embeddings.extend(result.embeddings)
            api_calls += 1

        span.set_attribute("voyage_api_calls", api_calls)
        await cache_embeddings(miss_texts, fresh_embeddings, input_type)

        result_map = dict(cached)
        for idx, emb in zip(miss_indices, fresh_embeddings):
            result_map[idx] = emb

    return [result_map[i] for i in range(len(texts))]


async def store_parents(parents: list[ParentChunk]) -> int:
    """Store parent chunks in Postgres."""
    if not parents:
        return 0

    pool = await get_pool()
    async with pool.acquire() as conn:
        for parent in parents:
            await conn.execute(
                """
                INSERT INTO parent_chunks (parent_id, content, source_url, source_title,
                                           section, document_type, token_count)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                ON CONFLICT (parent_id) DO UPDATE SET
                    content = EXCLUDED.content,
                    document_type = EXCLUDED.document_type
                """,
                parent.parent_id,
                parent.content,
                parent.source_url,
                parent.source_title,
                parent.section,
                parent.document_type,
                parent.token_count,
            )
    return len(parents)


async def embed_and_store_children(children: list[ChildChunk]) -> int:
    """Embed child chunks (using contextual content when available) and store in pgvector."""
    if not children:
        return 0

    texts_to_embed = []
    for child in children:
        contextual = getattr(child, "_contextual_content", None)
        texts_to_embed.append(contextual or child.content)

    embeddings = await embed_texts(texts_to_embed)

    pool = await get_pool()
    async with pool.acquire() as conn:
        for child, embedding, embed_text in zip(children, embeddings, texts_to_embed):
            vec = np.array(embedding, dtype=np.float32)
            contextual_content = getattr(child, "_contextual_content", None)

            await conn.execute(
                """
                INSERT INTO documents (chunk_id, parent_id, content, contextual_content,
                                       source_url, source_title, section, document_type,
                                       content_hash, token_count, embedding)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                ON CONFLICT (chunk_id) DO UPDATE SET
                    content = EXCLUDED.content,
                    contextual_content = EXCLUDED.contextual_content,
                    embedding = EXCLUDED.embedding,
                    content_hash = EXCLUDED.content_hash
                """,
                child.chunk_id,
                child.parent_id,
                child.content,
                contextual_content,
                child.source_url,
                child.source_title,
                child.section,
                child.document_type,
                child.content_hash,
                child.token_count,
                vec,
            )

    return len(children)
