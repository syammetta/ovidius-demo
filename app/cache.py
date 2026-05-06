"""Redis cache layer — embedding cache, response cache, and shared client.

Three cache tiers:
1. Embedding cache: avoids re-calling Voyage API for identical text (TTL 24h)
2. Response cache: full QA responses for identical queries (TTL 1h)
3. Job queue acceleration: already implemented in job_queue.py, shares client

All caches gracefully degrade — a Redis failure never blocks the pipeline.
"""

import hashlib
import json
import logging
from typing import Any

from redis import asyncio as redis

from app.config import settings

logger = logging.getLogger(__name__)

_client: redis.Redis | None = None

EMBEDDING_PREFIX = "ovidius:emb:"
EMBEDDING_TTL = 86400  # 24 hours

RESPONSE_PREFIX = "ovidius:resp:"
RESPONSE_TTL = 3600  # 1 hour


async def get_client() -> redis.Redis | None:
    """Return shared async Redis client, or None if Redis isn't configured."""
    global _client
    if not settings.redis_url:
        return None
    if _client is None:
        _client = redis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
    return _client


def _hash_key(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:24]


# ---------------------------------------------------------------------------
# Embedding cache
# ---------------------------------------------------------------------------

async def get_cached_embedding(text: str) -> list[float] | None:
    """Look up a cached embedding vector for the given text."""
    client = await get_client()
    if not client:
        return None
    try:
        key = EMBEDDING_PREFIX + _hash_key(text)
        raw = await client.get(key)
        if raw:
            return json.loads(raw)
    except Exception:
        pass
    return None


async def get_cached_embeddings(texts: list[str]) -> dict[int, list[float]]:
    """Batch lookup — returns {index: embedding} for cache hits."""
    client = await get_client()
    if not client or not texts:
        return {}

    try:
        keys = [EMBEDDING_PREFIX + _hash_key(t) for t in texts]
        values = await client.mget(keys)
        hits = {}
        for i, val in enumerate(values):
            if val:
                hits[i] = json.loads(val)
        return hits
    except Exception:
        return {}


async def cache_embeddings(texts: list[str], embeddings: list[list[float]]) -> None:
    """Store embedding vectors in Redis with TTL."""
    client = await get_client()
    if not client:
        return

    try:
        pipe = client.pipeline(transaction=False)
        for text, emb in zip(texts, embeddings):
            key = EMBEDDING_PREFIX + _hash_key(text)
            pipe.setex(key, EMBEDDING_TTL, json.dumps(emb))
        await pipe.execute()
    except Exception as e:
        logger.debug("Failed to cache embeddings: %s", e)


# ---------------------------------------------------------------------------
# Response cache
# ---------------------------------------------------------------------------

async def get_cached_response(query: str) -> dict[str, Any] | None:
    """Look up a cached QA response for the exact query."""
    client = await get_client()
    if not client:
        return None
    try:
        key = RESPONSE_PREFIX + _hash_key(query.strip().lower())
        raw = await client.get(key)
        if raw:
            logger.info("Response cache hit for query: %.60s", query)
            return json.loads(raw)
    except Exception:
        pass
    return None


async def cache_response(query: str, response: dict[str, Any]) -> None:
    """Store a QA response in Redis with TTL."""
    client = await get_client()
    if not client:
        return

    try:
        key = RESPONSE_PREFIX + _hash_key(query.strip().lower())
        await client.setex(key, RESPONSE_TTL, json.dumps(response))
    except Exception as e:
        logger.debug("Failed to cache response: %s", e)


async def invalidate_responses() -> None:
    """Clear all cached responses (e.g. after corpus re-ingestion)."""
    client = await get_client()
    if not client:
        return
    try:
        cursor = 0
        while True:
            cursor, keys = await client.scan(cursor, match=RESPONSE_PREFIX + "*", count=200)
            if keys:
                await client.delete(*keys)
            if cursor == 0:
                break
    except Exception as e:
        logger.debug("Failed to invalidate response cache: %s", e)


# ---------------------------------------------------------------------------
# Cache stats (for /health and dashboard)
# ---------------------------------------------------------------------------

async def get_cache_stats() -> dict[str, Any] | None:
    """Return cache hit/miss counts and key counts."""
    client = await get_client()
    if not client:
        return None
    try:
        info = await client.info("stats")
        dbinfo = await client.info("keyspace")
        emb_count = 0
        resp_count = 0
        cursor = 0
        while True:
            cursor, keys = await client.scan(cursor, match=EMBEDDING_PREFIX + "*", count=500)
            emb_count += len(keys)
            if cursor == 0:
                break
        cursor = 0
        while True:
            cursor, keys = await client.scan(cursor, match=RESPONSE_PREFIX + "*", count=500)
            resp_count += len(keys)
            if cursor == 0:
                break
        return {
            "connected": True,
            "embedding_keys": emb_count,
            "response_keys": resp_count,
            "hits": info.get("keyspace_hits", 0),
            "misses": info.get("keyspace_misses", 0),
            "used_memory_human": info.get("used_memory_human", "unknown"),
        }
    except Exception:
        return {"connected": False}
