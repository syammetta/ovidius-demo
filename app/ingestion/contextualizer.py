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
import random
import time
from typing import Awaitable, Callable

import anthropic
import httpx

from app.config import settings
from app.ingestion.chunker import ParentChunk, ChildChunk
from app.telemetry import get_tracer

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[int, int, str], Awaitable[None]]

_CONCURRENCY = 10
_REQUEST_TIMEOUT = 20.0
_MAX_RETRIES = 2
_BASE_BACKOFF_SECONDS = 1.0
_MAX_BACKOFF_SECONDS = 8.0

_INDEX_SEE_PATTERN_THRESHOLD = 3
_INDEX_COMMA_RATIO_THRESHOLD = 0.15
_INDEX_SHORT_LINE_RATIO_THRESHOLD = 0.6
_NUMERIC_DIGIT_RATIO_THRESHOLD = 0.4


def _is_low_value_content(text: str) -> bool:
    """Detect chunks that add little retrieval value: indexes, glossaries, numeric tables.

    Catches:
    - Alphabetical topic lists / cross-references ("see ...")
    - Comma-heavy term listings
    - Flattened tax tables (mostly digits with no natural language)
    """
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    if not lines:
        return False

    words = text.split()
    if len(words) < 10:
        return False

    # Index/glossary detection
    see_count = text.lower().count("(see") + text.lower().count("(see\n")
    if see_count >= _INDEX_SEE_PATTERN_THRESHOLD:
        return True

    commas = text.count(",")
    comma_ratio = commas / len(words)
    short_lines = sum(1 for ln in lines if len(ln.split()) <= 4)
    short_ratio = short_lines / len(lines)

    if comma_ratio >= _INDEX_COMMA_RATIO_THRESHOLD and short_ratio >= _INDEX_SHORT_LINE_RATIO_THRESHOLD:
        return True

    # Numeric table detection (flattened tax tables, rate schedules)
    stripped = text.replace(",", "").replace(".", "").replace(" ", "").replace("\n", "")
    if stripped:
        digit_ratio = sum(1 for ch in stripped if ch.isdigit()) / len(stripped)
        if digit_ratio >= _NUMERIC_DIGIT_RATIO_THRESHOLD:
            return True

    return False


async def _noop_progress(done: int, total: int, parent_title: str) -> None:
    pass


def _is_retryable_context_error(exc: Exception) -> bool:
    """Retry on rate-limit and transient timeout-like errors."""
    if isinstance(exc, TimeoutError):
        return True
    status_code = getattr(exc, "status_code", None)
    if status_code == 429:
        return True
    message = str(exc).lower()
    return (
        "rate_limit_error" in message
        or "error code: 429" in message
        or "timed out" in message
        or "timeout" in message
    )


def _retry_after_seconds(exc: Exception) -> float | None:
    """Best-effort parse of API retry hint, if exposed by SDK exception."""
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if not headers:
        return None
    raw = headers.get("retry-after")
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except Exception:
        return None


async def _contextualize_one(
    client: anthropic.AsyncAnthropic,
    child: ChildChunk,
    parent_content: str,
    tracer,
) -> tuple[ChildChunk, dict]:
    """Contextualize a single chunk. Returns (updated_child, usage_stats)."""
    stats = {"input_tokens": 0, "output_tokens": 0, "cache_create": 0, "cache_read": 0}

    contextualized_content = child.content
    with tracer.start_as_current_span("contextualize_chunk") as chunk_span:
        chunk_span.set_attribute("chunk_id", child.chunk_id[:32])
        last_error: Exception | None = None

        for attempt in range(_MAX_RETRIES + 1):
            try:
                t0 = time.perf_counter()
                if attempt == 0:
                    logger.debug("Haiku call for chunk %s", child.chunk_id[:16])
                else:
                    logger.warning("Haiku retry %d/%d for chunk %s", attempt, _MAX_RETRIES, child.chunk_id[:16])
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
                chunk_span.set_attribute("retry_count", attempt)
                stats["input_tokens"] = response.usage.input_tokens
                stats["output_tokens"] = response.usage.output_tokens

                cache_create = getattr(response.usage, "cache_creation_input_tokens", 0) or 0
                cache_read = getattr(response.usage, "cache_read_input_tokens", 0) or 0
                chunk_span.set_attribute("cache_creation_input_tokens", cache_create)
                chunk_span.set_attribute("cache_read_input_tokens", cache_read)
                stats["cache_create"] = cache_create
                stats["cache_read"] = cache_read
                contextualized_content = f"{response.content[0].text.strip()}\n\n{child.content}"
                break
            except Exception as e:
                last_error = e
                retryable = _is_retryable_context_error(e)
                final_attempt = attempt >= _MAX_RETRIES
                if not retryable or final_attempt:
                    if final_attempt and retryable:
                        logger.warning(
                            "Contextualization retries exhausted for chunk %s after %s attempts: %s",
                            child.chunk_id[:16],
                            attempt + 1,
                            e,
                        )
                    else:
                        logger.warning("Contextualization failed for chunk %s: %s", child.chunk_id[:16], e)
                    chunk_span.set_attribute("retry_count", attempt)
                    chunk_span.set_attribute("contextualize_error", str(e)[:500])
                    break

                suggested = _retry_after_seconds(e)
                exponential = min(_MAX_BACKOFF_SECONDS, _BASE_BACKOFF_SECONDS * (2 ** attempt))
                base_sleep = suggested if suggested is not None else exponential
                sleep_for = min(_MAX_BACKOFF_SECONDS, base_sleep + random.uniform(0, 0.5))
                logger.warning(
                    "Contextualization retry %s/%s for chunk %s after %0.2fs due to: %s",
                    attempt + 1,
                    _MAX_RETRIES,
                    child.chunk_id[:16],
                    sleep_for,
                    e,
                )
                await asyncio.sleep(sleep_for)

        if last_error and contextualized_content == child.content:
            chunk_span.set_attribute("contextualized_fallback", True)

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
    client = anthropic.AsyncAnthropic(
        api_key=settings.anthropic_api_key,
        timeout=httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0),
    )
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
    skipped_count = 0

    for parent_id, group in parent_groups.items():
        parent = parent_map.get(parent_id)
        if not parent:
            contextualized.extend(group)
            done_count += len(group)
            continue

        parent_label = parent.section or parent.source_title or parent_id[:12]

        if _is_low_value_content(parent.content):
            skipped_count += len(group)
            done_count += len(group)
            contextualized.extend(group)
            logger.info(
                "Skipped contextualizing %d chunks from '%s' (index/glossary content)",
                len(group), parent_label,
            )
            await progress(done_count, total_count, parent_label)
            continue

        with tracer.start_as_current_span("contextualize_parent_group") as group_span:
            group_span.set_attribute("parent_id", parent_id[:32])
            group_span.set_attribute("parent_label", parent_label[:200])
            group_span.set_attribute("chunk_count", len(group))

            eligible = []
            for child in group:
                if _is_low_value_content(child.content):
                    contextualized.append(child)
                    done_count += 1
                    skipped_count += 1
                    await progress(done_count, total_count, parent_label)
                else:
                    eligible.append(child)

            group_span.set_attribute("eligible_count", len(eligible))
            group_span.set_attribute("skipped_index", len(group) - len(eligible))

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

            if eligible:
                logger.info(
                    "Starting %d Haiku API calls for parent '%s' (done=%d/%d)",
                    len(eligible), parent_label[:60], done_count, total_count,
                )
                group_timeout = max(120.0, len(eligible) * 15.0)
                try:
                    results = await asyncio.wait_for(
                        asyncio.gather(
                            *[_process(c) for c in eligible],
                            return_exceptions=True,
                        ),
                        timeout=group_timeout,
                    )
                except asyncio.TimeoutError:
                    logger.error(
                        "Parent group '%s' timed out after %.0fs (%d eligible chunks). "
                        "Falling back to uncontextualized content.",
                        parent_label, group_timeout, len(eligible),
                    )
                    for child in eligible:
                        if not any(c.chunk_id == child.chunk_id for c in contextualized):
                            contextualized.append(child)
                            done_count += 1
                    await progress(done_count, total_count, parent_label)
                    results = []

                pause_exc = None
                for result in results:
                    if isinstance(result, Exception):
                        if result.__class__.__name__ == "PauseRequested":
                            pause_exc = result
                        else:
                            logger.warning(
                                "Contextualization task failed for chunk in '%s': %s",
                                parent_label, result,
                            )
                if pause_exc:
                    raise pause_exc

            group_span.set_attribute("total_input_tokens", total_input_tokens)
            group_span.set_attribute("total_output_tokens", total_output_tokens)
            group_span.set_attribute("total_cache_creation_tokens", total_cache_create)
            group_span.set_attribute("total_cache_read_tokens", total_cache_read)

    if skipped_count:
        logger.info(
            "Contextualized %d/%d chunks, skipped %d as low-value index/glossary content",
            total_count - skipped_count, total_count, skipped_count,
        )

    return contextualized
