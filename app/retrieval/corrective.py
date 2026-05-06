"""Corrective RAG — evaluate retrieval quality and route accordingly.

Instead of blindly trusting retrieval results, score their relevance and decide:
- CONFIDENT: retrieved chunks are relevant → proceed with generation
- UNCERTAIN: mixed relevance → filter to only relevant chunks, note gaps
- LOW_CONFIDENCE: most chunks irrelevant → transform query and retry, or
  acknowledge insufficient context rather than hallucinating

Uses batched evaluation (single LLM call for all chunks) to keep latency low.

Reference: Yan et al. (2024) — Corrective Retrieval Augmented Generation
"""

import json
import logging
import time
from dataclasses import dataclass
from enum import Enum

import anthropic

from app.config import settings
from app.retrieval.vector_store import RetrievedChunk
from app.telemetry import get_tracer

logger = logging.getLogger(__name__)


class RetrievalConfidence(str, Enum):
    CONFIDENT = "confident"
    UNCERTAIN = "uncertain"
    LOW_CONFIDENCE = "low_confidence"


@dataclass
class CorrectedRetrieval:
    chunks: list[RetrievedChunk]
    confidence: RetrievalConfidence
    filtered_count: int
    original_count: int
    transformed_query: str | None = None


BATCH_RELEVANCE_PROMPT = """Given the query and retrieved passages, rate EACH passage's relevance.

Query: {query}

{passages}

For each passage number, respond with "relevant" or "irrelevant".
Return ONLY a JSON object mapping passage numbers to verdicts, like:
{{"1": "relevant", "2": "irrelevant", "3": "relevant"}}"""


async def evaluate_retrieval(
    query: str,
    chunks: list[RetrievedChunk],
    relevance_threshold: float = 0.6,
) -> CorrectedRetrieval:
    """Score each chunk's relevance via a single batched LLM call and route based on confidence."""
    if not chunks:
        return CorrectedRetrieval(
            chunks=[],
            confidence=RetrievalConfidence.LOW_CONFIDENCE,
            filtered_count=0,
            original_count=0,
        )

    tracer = get_tracer("corrective")
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    relevant_chunks = []

    try:
        with tracer.start_as_current_span("batch_relevance_eval") as span:
            span.set_attribute("model", settings.classification_model)
            span.set_attribute("chunk_count", len(chunks))

            passages_text = "\n\n".join(
                f"Passage {i + 1}:\n{(c.contextual_content or c.content)[:400]}"
                for i, c in enumerate(chunks)
            )

            t0 = time.perf_counter()
            response = client.messages.create(
                model=settings.classification_model,
                max_tokens=200,
                messages=[{
                    "role": "user",
                    "content": BATCH_RELEVANCE_PROMPT.format(
                        query=query,
                        passages=passages_text,
                    ),
                }],
            )
            llm_ms = round((time.perf_counter() - t0) * 1000, 1)
            span.set_attribute("llm_latency_ms", llm_ms)
            span.set_attribute("input_tokens", response.usage.input_tokens)
            span.set_attribute("output_tokens", response.usage.output_tokens)

        raw = response.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]

        verdicts = json.loads(raw)
        for i, chunk in enumerate(chunks):
            verdict = str(verdicts.get(str(i + 1), "")).lower()
            if "relevant" in verdict and "irrelevant" not in verdict:
                relevant_chunks.append(chunk)
    except Exception as e:
        logger.warning("Batch relevance evaluation failed: %s — passing all chunks through", e)
        relevant_chunks = list(chunks)

    relevance_ratio = len(relevant_chunks) / len(chunks) if chunks else 0

    if relevance_ratio >= relevance_threshold:
        confidence = RetrievalConfidence.CONFIDENT
    elif relevance_ratio >= 0.3:
        confidence = RetrievalConfidence.UNCERTAIN
    else:
        confidence = RetrievalConfidence.LOW_CONFIDENCE

    transformed_query = None
    if confidence == RetrievalConfidence.LOW_CONFIDENCE:
        transformed_query = await _transform_query(client, query)

    final_chunks = relevant_chunks if relevant_chunks else chunks[:2]

    return CorrectedRetrieval(
        chunks=final_chunks,
        confidence=confidence,
        filtered_count=len(relevant_chunks),
        original_count=len(chunks),
        transformed_query=transformed_query,
    )


async def _transform_query(client: anthropic.Anthropic, query: str) -> str | None:
    """Transform a query that produced poor retrieval into a better search query."""
    tracer = get_tracer("corrective")
    try:
        with tracer.start_as_current_span("query_transform_llm") as span:
            span.set_attribute("model", settings.classification_model)
            t0 = time.perf_counter()
            response = client.messages.create(
                model=settings.classification_model,
                max_tokens=100,
                messages=[{
                    "role": "user",
                    "content": (
                        f"The following question did not retrieve good results from an IRS tax "
                        f"documentation knowledge base. Rewrite it as a more specific search query "
                        f"that would match IRS publication content better. Return ONLY the rewritten query.\n\n"
                        f"Original: {query}"
                    ),
                }],
            )
            llm_ms = round((time.perf_counter() - t0) * 1000, 1)
            span.set_attribute("llm_latency_ms", llm_ms)
            span.set_attribute("input_tokens", response.usage.input_tokens)
            span.set_attribute("output_tokens", response.usage.output_tokens)
        return response.content[0].text.strip()
    except Exception as e:
        logger.warning("Query transform failed: %s", e)
        return None
