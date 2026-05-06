"""Citation-grounded answer generation with confidence-aware prompting.

Uses parent chunks (larger context) for generation while citing child chunks
(precise passages) for source attribution. Adjusts behavior based on
corrective RAG confidence level.
"""

import asyncio
import time
from dataclasses import dataclass

import anthropic

from app.config import settings
from app.retrieval.context_builder import RetrievalResult
from app.retrieval.corrective import RetrievalConfidence
from app.telemetry import get_tracer, record_generation_latency

SYSTEM_PROMPT = """You are an expert IRS tax documentation assistant. Answer the user's question using ONLY the provided context passages from official IRS publications, tax topics, and form instructions.

Rules:
- Cite sources using inline markers like [1], [2], etc. corresponding to the numbered passages.
- When citing specific amounts, thresholds, or deadlines, always include the citation marker.
- If the context doesn't contain enough information, say so explicitly rather than guessing.
- Never invent tax rules, amounts, or deadlines not present in the context.
- When multiple sources cover the same topic, synthesize them and cite all relevant passages.
- Use clear, plain language — taxpayers of all backgrounds should understand your answer.
- For calculation questions, show the math step by step with the relevant rules cited.
- Always note when rules may vary by filing status, income level, or tax year.
- Be concise and direct. Prefer bullet points for lists of requirements or eligibility criteria."""

LOW_CONFIDENCE_ADDENDUM = """

IMPORTANT: The retrieval system has LOW CONFIDENCE that the provided context is relevant to this question. Be extra cautious:
- If the context does not clearly answer the question, say "I don't have enough information in the knowledge base to answer this confidently."
- Do not stretch or infer beyond what the context explicitly states.
- It is better to acknowledge a gap than to provide a poorly supported answer."""


@dataclass
class Citation:
    index: int
    source_url: str
    source_title: str
    chunk_id: str


@dataclass
class AnswerResult:
    answer: str
    citations: list[Citation]
    confidence: str
    retrieval_method: str
    chunks_used: int
    parent_chunks_used: int


async def generate_answer(question: str, retrieval: RetrievalResult) -> AnswerResult:
    """Generate a cited answer using parent chunks for context and child chunks for citations."""
    tracer = get_tracer("generation")

    children = retrieval.children
    parent_contents = retrieval.parent_contents
    confidence = retrieval.corrective.confidence

    context_parts = []
    seen_parents = set()

    for i, child in enumerate(children):
        parent_content = parent_contents.get(child.parent_id)

        if parent_content and child.parent_id not in seen_parents:
            context_parts.append(
                f"[{i + 1}] (Source: {child.source_title} | Type: {child.document_type})\n"
                f"{parent_content}"
            )
            seen_parents.add(child.parent_id)
        else:
            context_parts.append(
                f"[{i + 1}] (Source: {child.source_title} | Type: {child.document_type})\n"
                f"{child.contextual_content or child.content}"
            )

    context = "\n\n---\n\n".join(context_parts)

    system = SYSTEM_PROMPT
    if confidence == RetrievalConfidence.LOW_CONFIDENCE:
        system += LOW_CONFIDENCE_ADDENDUM

    with tracer.start_as_current_span("generate_answer") as span:
        span.set_attribute("model", settings.generation_model)
        span.set_attribute("confidence", confidence.value)
        span.set_attribute("chunks_used", len(children))
        span.set_attribute("parent_chunks_used", len(seen_parents))
        span.set_attribute("context_length", len(context))

        t0 = time.perf_counter()

        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        response = await asyncio.to_thread(
            client.messages.create,
            model=settings.generation_model,
            max_tokens=1024,
            system=system,
            messages=[{
                "role": "user",
                "content": f"Context:\n{context}\n\nQuestion: {question}",
            }],
        )

        gen_ms = round((time.perf_counter() - t0) * 1000, 1)
        span.set_attribute("generation_ms", gen_ms)
        span.set_attribute("ttft_ms", gen_ms)
        span.set_attribute("input_tokens", response.usage.input_tokens)
        span.set_attribute("output_tokens", response.usage.output_tokens)
        if hasattr(response.usage, "cache_creation_input_tokens"):
            span.set_attribute("cache_creation_input_tokens", response.usage.cache_creation_input_tokens or 0)
        if hasattr(response.usage, "cache_read_input_tokens"):
            span.set_attribute("cache_read_input_tokens", response.usage.cache_read_input_tokens or 0)
        record_generation_latency(gen_ms)

    answer_text = response.content[0].text

    citations = [
        Citation(
            index=i + 1,
            source_url=child.source_url,
            source_title=child.source_title,
            chunk_id=child.chunk_id,
        )
        for i, child in enumerate(children)
    ]

    methods = list({c.retrieval_method for c in children})

    return AnswerResult(
        answer=answer_text,
        citations=citations,
        confidence=confidence.value,
        retrieval_method="+".join(methods) if methods else "unknown",
        chunks_used=len(children),
        parent_chunks_used=len(seen_parents),
    )
