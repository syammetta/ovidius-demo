"""LLM-assisted document metadata classification for ingestion.

This stage labels each crawled document before chunking so downstream retrieval
can leverage better metadata filters/boosts (doc_type + section).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

import anthropic

from app.config import settings
from app.ingestion.chunker import detect_document_type

logger = logging.getLogger(__name__)

ALLOWED_DOC_TYPES = {"narrative", "api_reference", "code_heavy"}
ALLOWED_SECTIONS = {"publications", "taxtopics", "instructions", "other"}

DOC_CLASSIFY_PROMPT = """Classify this tax documentation page for ingestion metadata.
Return JSON only with this exact shape:
{{
  "doc_type": "narrative|api_reference|code_heavy",
  "section": "publications|taxtopics|instructions|other",
  "tax_topics": ["up to 4 short topic labels"],
  "metadata_tags": ["up to 4 short tags"],
  "reason": "one short sentence"
}}

URL: {url}
Title: {title}
Default section guess: {default_section}

Content sample:
{content}

Return ONLY valid JSON.
"""


@dataclass
class DocumentMetadata:
    doc_type: str
    section: str
    tax_topics: list[str]
    metadata_tags: list[str]
    reason: str
    llm_used: bool


def _heuristic_section(url: str, default_section: str) -> str:
    u = url.lower()
    if "/publications/" in u:
        return "publications"
    if "/taxtopics/" in u:
        return "taxtopics"
    if "/instructions/" in u:
        return "instructions"
    if default_section in ALLOWED_SECTIONS:
        return default_section
    return "other"


def _coerce_metadata(raw: dict, fallback_doc_type: str, fallback_section: str) -> DocumentMetadata:
    doc_type = raw.get("doc_type", fallback_doc_type)
    if doc_type not in ALLOWED_DOC_TYPES:
        doc_type = fallback_doc_type

    section = raw.get("section", fallback_section)
    if section not in ALLOWED_SECTIONS:
        section = fallback_section

    topics = [str(t).strip() for t in raw.get("tax_topics", []) if str(t).strip()][:4]
    tags = [str(t).strip() for t in raw.get("metadata_tags", []) if str(t).strip()][:4]

    return DocumentMetadata(
        doc_type=doc_type,
        section=section,
        tax_topics=topics,
        metadata_tags=tags,
        reason=str(raw.get("reason", "")).strip(),
        llm_used=True,
    )


async def classify_document_metadata(
    content: str,
    source_url: str,
    source_title: str,
    default_section: str,
) -> DocumentMetadata:
    fallback_doc_type = detect_document_type(content, source_url)
    fallback_section = _heuristic_section(source_url, default_section)

    if not settings.anthropic_api_key:
        return DocumentMetadata(
            doc_type=fallback_doc_type,
            section=fallback_section,
            tax_topics=[],
            metadata_tags=[],
            reason="No Anthropic key configured; heuristic metadata used.",
            llm_used=False,
        )

    snippet = content[:3500]
    prompt = DOC_CLASSIFY_PROMPT.format(
        url=source_url,
        title=source_title[:200],
        default_section=fallback_section,
        content=snippet,
    )

    try:
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        response = client.messages.create(
            model=settings.classification_model,
            max_tokens=260,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0]
        parsed = json.loads(text)
        return _coerce_metadata(parsed, fallback_doc_type, fallback_section)
    except Exception as exc:
        logger.warning("Document metadata classification failed for %s: %s", source_url, exc)
        return DocumentMetadata(
            doc_type=fallback_doc_type,
            section=fallback_section,
            tax_topics=[],
            metadata_tags=[],
            reason=f"LLM classification failed; fallback metadata used ({exc}).",
            llm_used=False,
        )
