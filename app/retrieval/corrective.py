"""Corrective RAG — evaluate retrieval quality and route accordingly.

Instead of blindly trusting retrieval results, score their relevance and decide:
- CONFIDENT: retrieved chunks are relevant → proceed with generation
- UNCERTAIN: mixed relevance → filter to only relevant chunks, note gaps
- LOW_CONFIDENCE: most chunks irrelevant → transform query and retry, or
  acknowledge insufficient context rather than hallucinating

This prevents the most common RAG failure mode: confidently generating answers
from irrelevant context.

Reference: Yan et al. (2024) — Corrective Retrieval Augmented Generation
"""

from dataclasses import dataclass
from enum import Enum

import anthropic

from app.config import settings
from app.retrieval.vector_store import RetrievedChunk


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


RELEVANCE_PROMPT = """Given the query and retrieved passage, rate the passage's relevance.

Query: {query}

Passage:
{passage}

Is this passage relevant to answering the query? Respond with ONLY one word: "relevant" or "irrelevant"."""


async def evaluate_retrieval(
    query: str,
    chunks: list[RetrievedChunk],
    relevance_threshold: float = 0.6,
) -> CorrectedRetrieval:
    """Score each chunk's relevance and route based on overall confidence."""
    if not chunks:
        return CorrectedRetrieval(
            chunks=[],
            confidence=RetrievalConfidence.LOW_CONFIDENCE,
            filtered_count=0,
            original_count=0,
        )

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    relevant_chunks = []

    for chunk in chunks:
        try:
            response = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=10,
                messages=[{
                    "role": "user",
                    "content": RELEVANCE_PROMPT.format(
                        query=query,
                        passage=(chunk.contextual_content or chunk.content)[:500],
                    ),
                }],
            )
            verdict = response.content[0].text.strip().lower()
            if "relevant" in verdict and "irrelevant" not in verdict:
                relevant_chunks.append(chunk)
        except Exception:
            relevant_chunks.append(chunk)

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

    return CorrectedRetrieval(
        chunks=relevant_chunks if relevant_chunks else chunks[:2],
        confidence=confidence,
        filtered_count=len(relevant_chunks),
        original_count=len(chunks),
        transformed_query=transformed_query,
    )


async def _transform_query(client: anthropic.Anthropic, query: str) -> str:
    """Transform a query that produced poor retrieval into a better search query."""
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=100,
        messages=[{
            "role": "user",
            "content": (
                f"The following question did not retrieve good results from a documentation "
                f"knowledge base. Rewrite it as a more specific search query that would "
                f"match documentation content better. Return ONLY the rewritten query.\n\n"
                f"Original: {query}"
            ),
        }],
    )
    return response.content[0].text.strip()
