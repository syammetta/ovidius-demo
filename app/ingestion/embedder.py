"""Embed contextualized chunks and store parent-child structure in Postgres pgvector."""

import numpy as np
import voyageai

from app.config import settings
from app.db import get_pool
from app.ingestion.chunker import ParentChunk, ChildChunk


async def embed_texts(texts: list[str]) -> list[list[float]]:
    """Generate embeddings via Voyage AI, batching to respect API limits."""
    client = voyageai.Client(api_key=settings.voyage_api_key)
    all_embeddings = []
    batch_size = 50

    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        result = client.embed(batch, model=settings.embedding_model)
        all_embeddings.extend(result.embeddings)

    return all_embeddings


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
