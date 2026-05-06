"""Tests for adaptive chunking — the most bug-prone module in the pipeline.

Key things that break:
- Empty content producing no chunks
- Document type misclassification
- Parent-child linkage (orphaned children, wrong parent assignment)
- Chunks splitting mid-sentence or mid-code-block
- Tiny chunks below the merge threshold surviving
- Overlapping token boundaries causing duplication
"""

import pytest
from app.ingestion.chunker import (
    chunk_document,
    detect_document_type,
    chunk_narrative,
    chunk_api_reference,
    chunk_code_heavy,
    _split_by_tokens,
    _split_by_headings,
    _split_by_paragraphs,
    _merge_small_segments,
    _token_count,
)


# ---------------------------------------------------------------------------
# Document type detection
# ---------------------------------------------------------------------------

class TestDocumentTypeDetection:
    def test_detects_api_reference_from_url(self):
        assert detect_document_type("some content", "https://api.example.com/api/v1/docs") == "api_reference"
        assert detect_document_type("some content", "https://docs.example.com/reference/endpoints") == "api_reference"
        assert detect_document_type("some content", "https://docs.example.com/sdk/python") == "api_reference"

    def test_detects_api_reference_from_content(self):
        content = """
        ## POST /api/calculate
        Endpoint: POST /calculate
        Request body parameters:
        | Parameter | Type |
        Status code: 200
        ```json
        {"result": 42}
        ```
        """
        assert detect_document_type(content, "https://example.com/docs") == "api_reference"

    def test_detects_code_heavy(self):
        code = "```python\n" + "x = 1\n" * 100 + "```\n"
        prose = "Short intro.\n"
        content = prose + code
        assert detect_document_type(content, "https://example.com/tutorial") == "code_heavy"

    def test_detects_narrative(self):
        content = "This is a guide about filing your taxes. " * 50
        assert detect_document_type(content, "https://www.irs.gov/publications/p501") == "narrative"

    def test_narrative_is_default(self):
        assert detect_document_type("hello world", "https://example.com") == "narrative"

    def test_empty_content_defaults_to_narrative(self):
        assert detect_document_type("", "https://example.com") == "narrative"


# ---------------------------------------------------------------------------
# Splitting helpers
# ---------------------------------------------------------------------------

class TestSplitHelpers:
    def test_split_by_tokens_basic(self):
        text = "word " * 100
        chunks = _split_by_tokens(text, max_tokens=50, overlap_tokens=10)
        assert len(chunks) >= 2
        for chunk in chunks:
            assert _token_count(chunk) <= 50

    def test_split_by_tokens_short_text(self):
        text = "short text"
        chunks = _split_by_tokens(text, max_tokens=100, overlap_tokens=10)
        assert len(chunks) == 1
        assert chunks[0] == text

    def test_split_by_tokens_overlap_creates_redundancy(self):
        text = "word " * 100
        chunks = _split_by_tokens(text, max_tokens=50, overlap_tokens=10)
        if len(chunks) >= 2:
            last_words_chunk0 = chunks[0].split()[-5:]
            first_words_chunk1 = chunks[1].split()[:5]
            overlap = set(last_words_chunk0) & set(first_words_chunk1)
            assert len(overlap) > 0

    def test_split_by_headings(self):
        content = "# Heading 1\nContent 1\n\n## Heading 2\nContent 2\n\n### Heading 3\nContent 3"
        sections = _split_by_headings(content)
        assert len(sections) >= 2

    def test_split_by_headings_no_headings(self):
        content = "Just plain text without any headings."
        sections = _split_by_headings(content)
        assert len(sections) == 1

    def test_split_by_paragraphs(self):
        content = "Paragraph one.\n\nParagraph two.\n\nParagraph three."
        paras = _split_by_paragraphs(content)
        assert len(paras) == 3

    def test_split_by_paragraphs_strips_whitespace(self):
        content = "  Para one.  \n\n  Para two.  "
        paras = _split_by_paragraphs(content)
        assert all(p == p.strip() for p in paras)

    def test_merge_small_segments(self):
        segments = ["Hi", "there", "This is a longer segment with enough tokens to stand alone " * 5]
        merged = _merge_small_segments(segments, min_tokens=10)
        assert len(merged) <= len(segments)
        assert any("Hi" in seg for seg in merged)

    def test_merge_preserves_large_segments(self):
        large = "This is a substantial segment. " * 20
        segments = [large, large]
        merged = _merge_small_segments(segments, min_tokens=10)
        assert len(merged) == 2


# ---------------------------------------------------------------------------
# Parent-child chunking
# ---------------------------------------------------------------------------

class TestChunkDocument:
    def test_produces_parents_and_children(self):
        content = "## Section 1\n\nParagraph about taxes. " * 20 + "\n\n## Section 2\n\nMore content. " * 20
        result = chunk_document(content, "https://irs.gov/pub/p501", "Pub 501", "publications")

        assert len(result.parents) > 0
        assert len(result.children) > 0

    def test_children_link_to_valid_parents(self):
        content = "## Section 1\n\n" + "Content here. " * 30 + "\n\n## Section 2\n\n" + "More content. " * 30
        result = chunk_document(content, "https://irs.gov/pub/p501", "Pub 501", "pubs")

        parent_ids = {p.parent_id for p in result.parents}
        for child in result.children:
            assert child.parent_id in parent_ids, f"Child {child.chunk_id} has orphan parent_id {child.parent_id}"

    def test_no_empty_chunks(self):
        content = "## Deductions\n\nYou can deduct certain expenses. " * 20
        result = chunk_document(content, "https://irs.gov/pub/p502", "Pub 502", "pubs")

        for parent in result.parents:
            assert len(parent.content.strip()) > 0
            assert parent.token_count > 0

        for child in result.children:
            assert len(child.content.strip()) > 0
            assert child.token_count > 0

    def test_document_type_propagated(self):
        content = "Narrative content about tax rules. " * 50
        result = chunk_document(content, "https://irs.gov/pub/p17", "Pub 17", "pubs")

        for parent in result.parents:
            assert parent.document_type in ("narrative", "api_reference", "code_heavy")
        for child in result.children:
            assert child.document_type == result.parents[0].document_type

    def test_metadata_preserved(self):
        result = chunk_document(
            "Some tax content. " * 20,
            "https://irs.gov/pub/p501",
            "Publication 501",
            "publications",
        )
        for child in result.children:
            assert child.source_url == "https://irs.gov/pub/p501"
            assert child.source_title == "Publication 501"
            assert child.section == "publications"

    def test_content_hash_unique_per_chunk(self):
        content = "## Section A\n\nContent A. " * 15 + "\n\n## Section B\n\nContent B. " * 15
        result = chunk_document(content, "https://irs.gov/pub/p501", "Pub 501", "pubs")

        hashes = [c.content_hash for c in result.children]
        # If content differs, hashes should differ (not guaranteed for all, but most)
        if len(result.children) > 1:
            assert len(set(hashes)) > 1

    def test_chunk_ids_unique(self):
        content = "## A\n\n" + "Text. " * 30 + "\n\n## B\n\n" + "More. " * 30
        result = chunk_document(content, "https://irs.gov/pub/p17", "Pub 17", "pubs")

        child_ids = [c.chunk_id for c in result.children]
        assert len(child_ids) == len(set(child_ids)), "Duplicate chunk IDs found"

        parent_ids = [p.parent_id for p in result.parents]
        assert len(parent_ids) == len(set(parent_ids)), "Duplicate parent IDs found"

    def test_empty_content_produces_empty_result(self):
        result = chunk_document("", "https://example.com", "Empty", "")
        assert len(result.parents) == 0
        assert len(result.children) == 0

    def test_whitespace_only_content(self):
        result = chunk_document("   \n\n   \t  ", "https://example.com", "Whitespace", "")
        assert len(result.children) == 0


# ---------------------------------------------------------------------------
# Strategy-specific tests
# ---------------------------------------------------------------------------

class TestNarrativeChunking:
    def test_splits_on_headings(self):
        section_a = "## Filing Status\n\n" + "Your filing status determines your tax rate and bracket. You must choose from single, married filing jointly, married filing separately, head of household, or qualifying surviving spouse. " * 5
        section_b = "\n\n## Dependents\n\n" + "You may claim dependents if they meet certain qualifying tests including relationship, age, residency, and support requirements as outlined by the IRS in publication 501. " * 5
        content = section_a + section_b
        parents, children = chunk_narrative(content)
        assert len(parents) >= 2

    def test_paragraph_boundaries_respected(self):
        content = "First paragraph about deductions.\n\nSecond paragraph about credits.\n\nThird paragraph about income."
        parents, children = chunk_narrative(content)
        for text, _parent_idx in children:
            assert not text.startswith("\n")

    def test_children_carry_parent_index(self):
        section_a = "## Filing Status\n\n" + "Your filing status determines your tax rate. " * 10
        section_b = "\n\n## Dependents\n\n" + "You may claim dependents. " * 10
        content = section_a + section_b
        parents, children = chunk_narrative(content)
        for _text, parent_idx in children:
            assert 0 <= parent_idx < len(parents)


class TestApiReferenceChunking:
    def test_splits_on_sections(self):
        section_a = "## GET /endpoint\n\nThis endpoint retrieves the tax calculation for a given filing status and income level. It accepts query parameters for filing status and gross income and returns the computed federal tax amount along with the effective and marginal rates."
        section_b = "\n\n## POST /other\n\nThis endpoint submits a new tax return for processing. It accepts a JSON body with all required W-2 and 1099 information and returns a confirmation ID along with the estimated refund or amount owed."
        content = section_a + section_b
        parents, children = chunk_api_reference(content)
        assert len(parents) >= 2

    def test_code_blocks_preserved(self):
        content = "## Endpoint\n\nThis endpoint returns the tax calculation result as a JSON object with the following structure for all supported filing statuses.\n\n```json\n{\"key\": \"value\", \"tax_owed\": 12500, \"effective_rate\": 0.167}\n```\n\nThe response includes the computed tax owed, effective rate, and marginal bracket information."
        parents, children = chunk_api_reference(content)
        found_code = any("```" in text for text, _ in children)
        assert found_code or any("{\"key\"" in text for text, _ in children)


class TestCodeHeavyChunking:
    def test_code_blocks_atomic(self):
        code = "```python\ndef foo():\n    return 42\n```"
        content = f"## Example\n\n{code}\n\nExplanation text."
        parents, children = chunk_code_heavy(content)
        for text, _ in children:
            if "def foo" in text:
                assert "return 42" in text, "Code block was split"


class TestParentLinkage:
    def test_children_link_to_correct_parent_via_chunk_document(self):
        section_a = "## Standard Deduction\n\n" + "The standard deduction reduces your taxable income. " * 15
        section_b = "\n\n## Itemized Deductions\n\n" + "You can choose to itemize your deductions instead. " * 15
        content = section_a + section_b
        result = chunk_document(content, "https://irs.gov/pub/p501", "Pub 501", "pubs")

        parent_ids = {p.parent_id for p in result.parents}
        for child in result.children:
            assert child.parent_id in parent_ids
