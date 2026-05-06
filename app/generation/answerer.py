"""Citation-grounded answer generation with confidence-aware prompting.

Uses parent chunks (larger context) for generation while citing child chunks
(precise passages) for source attribution. Adjusts behavior based on
corrective RAG confidence level.
"""

from dataclasses import dataclass

import anthropic

from app.config import settings
from app.retrieval.context_builder import RetrievalResult
from app.retrieval.corrective import RetrievalConfidence

SYSTEM_PROMPT = """You are a documentation assistant. Answer the user's question using ONLY the provided context passages.

Rules:
- Cite sources using inline markers like [1], [2], etc.
- Each marker corresponds to the numbered passage in the context.
- If the context doesn't contain enough information, say so explicitly.
- Never invent information not present in the context.
- Be concise and direct."""

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

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    response = client.messages.create(
        model=settings.generation_model,
        max_tokens=1024,
        system=system,
        messages=[{
            "role": "user",
            "content": f"Context:\n{context}\n\nQuestion: {question}",
        }],
    )

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
