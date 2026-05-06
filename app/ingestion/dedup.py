"""Source-level dedup helpers for ingestion."""

from __future__ import annotations

import hashlib
import re

from app.db import get_pool


def canonical_source_hash(text: str) -> str:
    """Hash normalized source text for stable duplicate detection."""
    normalized = re.sub(r"\s+", " ", text).strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


async def get_source_state(source_url: str) -> dict | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT source_url, source_hash, last_parent_count, last_child_count, updated_at
               FROM source_ingest_state
               WHERE source_url = $1""",
            source_url,
        )
    return dict(row) if row else None


async def upsert_source_state(
    source_url: str,
    source_hash: str,
    parent_count: int,
    child_count: int,
) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO source_ingest_state
               (source_url, source_hash, last_parent_count, last_child_count, updated_at)
               VALUES ($1, $2, $3, $4, now())
               ON CONFLICT (source_url) DO UPDATE SET
                 source_hash = EXCLUDED.source_hash,
                 last_parent_count = EXCLUDED.last_parent_count,
                 last_child_count = EXCLUDED.last_child_count,
                 updated_at = now()""",
            source_url,
            source_hash,
            parent_count,
            child_count,
        )
