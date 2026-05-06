"""Adaptive chunking with parent-child architecture.

Different document types get different chunking strategies:
- API references: split by endpoint/method boundaries
- Narrative guides: semantic paragraph-based splitting with larger context
- Code examples: language-aware splitting respecting function boundaries
- Mixed content: structural splitting on headings, then adaptive sub-chunking

Parent chunks (large) are used for generation context.
Child chunks (small) are used for precise retrieval.
"""

from dataclasses import dataclass, field
import hashlib
import re

import tiktoken

from app.config import settings

enc = tiktoken.get_encoding("cl100k_base")


@dataclass
class ParentChunk:
    parent_id: str
    content: str
    source_url: str
    source_title: str
    section: str
    document_type: str
    token_count: int


@dataclass
class ChildChunk:
    chunk_id: str
    parent_id: str
    content: str
    source_url: str
    source_title: str
    section: str
    document_type: str
    content_hash: str
    token_count: int


@dataclass
class ChunkResult:
    parents: list[ParentChunk] = field(default_factory=list)
    children: list[ChildChunk] = field(default_factory=list)


def detect_document_type(content: str, url: str) -> str:
    """Classify document type from content patterns and URL."""
    url_lower = url.lower()

    if any(p in url_lower for p in ["/api/", "/reference/", "/sdk/"]):
        return "api_reference"

    api_patterns = [
        r"endpoint[s]?\s*:", r"HTTP\s+(GET|POST|PUT|DELETE|PATCH)",
        r"```json\s*\{", r"parameters?\s*\|", r"request\s+body",
        r"response\s+body", r"status\s+code",
    ]
    api_score = sum(1 for p in api_patterns if re.search(p, content, re.IGNORECASE))
    if api_score >= 3:
        return "api_reference"

    code_blocks = re.findall(r"```[\w]*\n[\s\S]*?```", content)
    code_ratio = sum(len(b) for b in code_blocks) / max(len(content), 1)
    if code_ratio > 0.5:
        return "code_heavy"

    return "narrative"


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _token_count(text: str) -> int:
    return len(enc.encode(text))


def _split_by_tokens(text: str, max_tokens: int, overlap_tokens: int) -> list[str]:
    """Fixed-size token splitting with overlap."""
    tokens = enc.encode(text)
    chunks = []
    start = 0
    while start < len(tokens):
        end = min(start + max_tokens, len(tokens))
        chunks.append(enc.decode(tokens[start:end]))
        if end >= len(tokens):
            break
        start = end - overlap_tokens
    return chunks


def _split_by_headings(content: str) -> list[str]:
    """Split on markdown headings, keeping heading with its content."""
    sections = re.split(r'(?=^#{1,3}\s)', content, flags=re.MULTILINE)
    return [s.strip() for s in sections if s.strip()]


def _split_by_paragraphs(content: str) -> list[str]:
    """Split on double newlines (paragraph boundaries)."""
    paras = re.split(r'\n\s*\n', content)
    return [p.strip() for p in paras if p.strip()]


def _split_code_blocks(content: str) -> list[str]:
    """Split preserving code blocks as atomic units."""
    parts = re.split(r'(```[\w]*\n[\s\S]*?```)', content)
    result = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if part.startswith("```"):
            result.append(part)
        else:
            result.extend(_split_by_paragraphs(part))
    return result


def _merge_small_segments(segments: list[str], min_tokens: int = 50) -> list[str]:
    """Merge segments that are too small to be meaningful on their own."""
    merged = []
    buffer = ""
    for seg in segments:
        if buffer:
            candidate = buffer + "\n\n" + seg
        else:
            candidate = seg
        if _token_count(candidate) < min_tokens:
            buffer = candidate
        else:
            if buffer and buffer != candidate:
                merged.append(buffer)
                buffer = seg
            else:
                buffer = candidate
    if buffer:
        merged.append(buffer)
    return merged


def chunk_api_reference(content: str) -> tuple[list[str], list[str]]:
    """API docs: split by endpoint/section boundaries.

    Parent chunks = full endpoint sections (parameters + examples + response).
    Child chunks = individual parameters, code examples, response fields.
    """
    sections = _split_by_headings(content)
    if not sections:
        sections = [content]

    parents = []
    children = []

    for section in sections:
        parent_text = section.strip()
        if not parent_text or _token_count(parent_text) < 30:
            continue

        parents.append(parent_text)

        sub_parts = _split_code_blocks(parent_text)
        sub_parts = _merge_small_segments(sub_parts, min_tokens=40)

        for part in sub_parts:
            if _token_count(part) > settings.chunk_size:
                for sub in _split_by_tokens(part, 200, 30):
                    children.append(sub)
            else:
                children.append(part)

    return parents, children


def chunk_narrative(content: str) -> tuple[list[str], list[str]]:
    """Narrative guides: paragraph-based splitting with larger context.

    Parent chunks = full sections under headings (~1500 tokens).
    Child chunks = individual paragraphs or paragraph groups (~300 tokens).
    """
    sections = _split_by_headings(content)
    if not sections:
        sections = _split_by_paragraphs(content)

    parents = []
    children = []

    for section in sections:
        section = section.strip()
        if not section or _token_count(section) < 30:
            continue

        if _token_count(section) > 1500:
            for parent_chunk in _split_by_tokens(section, 1500, 200):
                parents.append(parent_chunk)
        else:
            parents.append(section)

        paragraphs = _split_by_paragraphs(section)
        paragraphs = _merge_small_segments(paragraphs, min_tokens=60)

        for para in paragraphs:
            if _token_count(para) > 400:
                for sub in _split_by_tokens(para, 300, 50):
                    children.append(sub)
            else:
                children.append(para)

    return parents, children


def chunk_code_heavy(content: str) -> tuple[list[str], list[str]]:
    """Code-heavy docs: preserve code blocks as atomic units.

    Parent chunks = full sections with code + surrounding explanation.
    Child chunks = individual code blocks + their immediate description.
    """
    sections = _split_by_headings(content)
    if not sections:
        sections = [content]

    parents = []
    children = []

    for section in sections:
        section = section.strip()
        if not section or _token_count(section) < 30:
            continue

        if _token_count(section) > 1500:
            for parent_chunk in _split_by_tokens(section, 1500, 200):
                parents.append(parent_chunk)
        else:
            parents.append(section)

        parts = _split_code_blocks(section)
        parts = _merge_small_segments(parts, min_tokens=40)

        for part in parts:
            if _token_count(part) > 500:
                for sub in _split_by_tokens(part, 300, 50):
                    children.append(sub)
            else:
                children.append(part)

    return parents, children


CHUNKING_STRATEGIES = {
    "api_reference": chunk_api_reference,
    "narrative": chunk_narrative,
    "code_heavy": chunk_code_heavy,
}


def chunk_document(
    content: str,
    source_url: str,
    source_title: str,
    section: str,
    doc_type_override: str | None = None,
) -> ChunkResult:
    """Adaptively chunk a document based on its detected type.

    Returns parent chunks (for generation) and child chunks (for retrieval),
    linked by parent_id.
    """
    doc_type = doc_type_override or detect_document_type(content, source_url)
    strategy = CHUNKING_STRATEGIES.get(doc_type, chunk_narrative)

    parent_texts, child_texts = strategy(content)

    result = ChunkResult()

    parent_map: dict[int, str] = {}

    for i, parent_text in enumerate(parent_texts):
        h = _content_hash(parent_text)
        parent_id = f"p_{h}_{i}"
        parent_map[i] = parent_id

        result.parents.append(ParentChunk(
            parent_id=parent_id,
            content=parent_text,
            source_url=source_url,
            source_title=source_title,
            section=section,
            document_type=doc_type,
            token_count=_token_count(parent_text),
        ))

    for j, child_text in enumerate(child_texts):
        h = _content_hash(child_text)
        chunk_id = f"c_{h}_{j}"

        best_parent_idx = _find_parent(child_text, parent_texts)
        parent_id = parent_map.get(best_parent_idx, list(parent_map.values())[0] if parent_map else "orphan")

        result.children.append(ChildChunk(
            chunk_id=chunk_id,
            parent_id=parent_id,
            content=child_text,
            source_url=source_url,
            source_title=source_title,
            section=section,
            document_type=doc_type,
            content_hash=h,
            token_count=_token_count(child_text),
        ))

    return result


def _find_parent(child_text: str, parent_texts: list[str]) -> int:
    """Find which parent chunk contains (or most overlaps with) this child."""
    best_idx = 0
    best_overlap = 0
    child_words = set(child_text.lower().split())

    for i, parent_text in enumerate(parent_texts):
        if child_text in parent_text:
            return i
        parent_words = set(parent_text.lower().split())
        overlap = len(child_words & parent_words)
        if overlap > best_overlap:
            best_overlap = overlap
            best_idx = i

    return best_idx
