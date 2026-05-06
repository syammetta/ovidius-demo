"""Anthropic Contextual Retrieval — prepend situating context to each chunk before embedding.

For each child chunk, we use Claude to generate a brief (50-100 token) context
that explains where this chunk fits within the parent document. This dramatically
improves retrieval because the embedding captures document-level context, not just
the chunk's local content.

Uses prompt caching: the parent document is cached once, then each child chunk
generates context referencing the cached parent. This makes the technique cost-effective.

Runs up to 10 concurrent API calls with per-chunk progress reporting.

Reference: https://www.anthropic.com/news/contextual-retrieval
"""

import asyncio
import logging
import time
from typing import Awaitable, Callable

import anthropic

from app.config import settings
from app.ingestion.chunker import ParentChunk, ChildChunk
from app.telemetry import get_tracer

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[int, int, str], Awaitable[None]]

_CONCURRENCY = 10
_REQUEST_TIMEOUT = 30.0


async def _noop_progress(done: int, total: int, parent_title: str) -> None:
    pass


async def _contextualize_one(
    client: anthropic.AsyncAnthropic,
    child: ChildChunk,
    parent_content: str,
    tracer,
) -> tuple[ChildChunk, dict]:
    """Contextualize a single chunk. Returns (updated_child, usage_stats)."""
    stats = {"input_tokens": 0, "output_tokens": 0, "cache_create": 0, "cache_read": 0}

    try:
        with tracer.start_as_current_span("contextualize_chunk") as chunk_span:
            chunk_span.set_attribute("chunk_id", child.chunk_id[:32])
            t0 = time.perf_counter()
            response = await asyncio.wait_for(
                client.messages.create(
                    model="claude-haiku-4-5-20251001",
                    max_tokens=150,
                    messages=[{
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": f"<document>\n{parent_content}\n</document>",
                                "cache_control": {"type": "ephemeral"},
                            },
                            {
                                "type": "text",
                                "text": (
                                    "Here is the chunk we want to situate within the "
                                    f"whole document:\n<chunk>\n{child.content}\n</chunk>\n\n"
                                    "Please give a short succinct context to situate this "
                                    "chunk within the overall document for the purposes of "
                                    "improving search retrieval of the chunk. Answer only "
                                    "with the context, nothing else."
                                ),
                            },
                        ],
                    }],
                ),
                timeout=_REQUEST_TIMEOUT,
            )
            llm_ms = round((time.perf_counter() - t0) * 1000, 1)
            chunk_span.set_attribute("llm_latency_ms", llm_ms)
            chunk_span.set_attribute("input_tokens", response.usage.input_tokens)
            chunk_span.set_attribute("output_tokens", response.usage.output_tokens)
            stats["input_tokens"] = response.usage.input_tokens
            stats["output_tokens"] = response.usage.output_tokens

            cache_create = getattr(response.usage, "cache_creation_input_tokens", 0) or 0
            cache_read = getattr(response.usage, "cache_read_input_tokens", 0) or 0
            chunk_span.set_attribute("cache_creation_input_tokens", cache_create)
            chunk_span.set_attribute("cache_read_input_tokens", cache_read)
            stats["cache_create"] = cache_create
            stats["cache_read"] = cache_read

        contextualized_content = f"{response.content[0].text.strip()}\n\n{child.content}"
    except Exception as e:
        logger.warning("Contextualization failed for chunk %s: %s", child.chunk_id[:16], e)
        contextualized_content = child.content

    updated = ChildChunk(
        chunk_id=child.chunk_id,
        parent_id=child.parent_id,
        content=child.content,
        source_url=child.source_url,
        source_title=child.source_title,
        section=child.section,
        document_type=child.document_type,
        content_hash=child.content_hash,
        token_count=child.token_count,
    )
    updated._contextual_content = contextualized_content
    return updated, stats


async def contextualize_chunks(
    children: list[ChildChunk],
    parents: list[ParentChunk],
    on_progress: ProgressCallback | None = None,
) -> list[ChildChunk]:
    """Add contextual prefixes to child chunks using Anthropic's contextual retrieval approach.

    Groups children by parent, caches the parent document, then generates context
    for each child chunk referencing the cached parent. Uses async concurrency
    (up to 10 in-flight calls) with per-chunk progress reporting.
    """
    tracer = get_tracer("contextualizer")
    parent_map = {p.parent_id: p for p in parents}
    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    progress = on_progress or _noop_progress
    semaphore = asyncio.Semaphore(_CONCURRENCY)

    parent_groups: dict[str, list[ChildChunk]] = {}
    for child in children:
        parent_groups.setdefault(child.parent_id, []).append(child)

    contextualized: list[ChildChunk] = []
    done_count = 0
    total_count = len(children)
    total_input_tokens = 0
    total_output_tokens = 0
    total_cache_create = 0
    total_cache_read = 0

    for parent_id, group in parent_groups.items():
        parent = parent_map.get(parent_id)
        if not parent:
            contextualized.extend(group)
            done_count += len(group)
            continue

        parent_label = parent.section or parent.source_title or parent_id[:12]

        with tracer.start_as_current_span("contextualize_parent_group") as group_span:
            group_span.set_attribute("parent_id", parent_id[:32])
            group_span.set_attribute("parent_label", parent_label[:200])
            group_span.set_attribute("chunk_count", len(group))

            parent_content = parent.content

            async def _process(child: ChildChunk, parent_text: str = parent_content):
                nonlocal done_count, total_input_tokens, total_output_tokens
                nonlocal total_cache_create, total_cache_read
                async with semaphore:
                    updated, stats = await _contextualize_one(client, child, parent_text, tracer)
                contextualized.append(updated)
                done_count += 1
                total_input_tokens += stats["input_tokens"]
                total_output_tokens += stats["output_tokens"]
                total_cache_create += stats["cache_create"]
                total_cache_read += stats["cache_read"]
                await progress(done_count, total_count, parent_label)

            await asyncio.gather(*[_process(c) for c in group])

            group_span.set_attribute("total_input_tokens", total_input_tokens)
            group_span.set_attribute("total_output_tokens", total_output_tokens)
            group_span.set_attribute("total_cache_creation_tokens", total_cache_create)
            group_span.set_attribute("total_cache_read_tokens", total_cache_read)

    return contextualized
