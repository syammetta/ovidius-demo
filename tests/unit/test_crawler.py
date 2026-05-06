"""Tests for the crawler — HTML parsing, content extraction, tag stripping."""

import pytest
from app.ingestion.crawler import parse_page
from tests.conftest import (
    SAMPLE_NARRATIVE_HTML,
    SAMPLE_EMPTY_HTML,
    SAMPLE_MALFORMED_HTML,
    SAMPLE_NAV_HEAVY_HTML,
    SAMPLE_CODE_HEAVY_HTML,
)


class TestParsePage:
    def test_extracts_title(self):
        doc = parse_page("https://irs.gov/pub/p501", SAMPLE_NARRATIVE_HTML)
        assert "Publication 501" in doc.title

    def test_extracts_main_content(self):
        doc = parse_page("https://irs.gov/pub/p501", SAMPLE_NARRATIVE_HTML)
        assert "standard deduction" in doc.content.lower()
        assert "qualifying child" in doc.content.lower()

    def test_strips_nav_header_footer(self):
        doc = parse_page("https://irs.gov/topic/301", SAMPLE_NAV_HEAVY_HTML)
        assert "Skip this" not in doc.content
        assert "IRS footer" not in doc.content
        assert "sidebar" not in doc.content
        assert "tracking" not in doc.content

    def test_strips_script_tags(self):
        doc = parse_page("https://example.com", SAMPLE_NAV_HEAVY_HTML)
        assert "console.log" not in doc.content

    def test_extracts_section_from_url(self):
        doc = parse_page("https://irs.gov/publications/p501", SAMPLE_NARRATIVE_HTML)
        assert doc.section == "publications"

    def test_url_preserved(self):
        url = "https://www.irs.gov/publications/p501"
        doc = parse_page(url, SAMPLE_NARRATIVE_HTML)
        assert doc.url == url

    def test_html_preserved(self):
        doc = parse_page("https://example.com", SAMPLE_NARRATIVE_HTML)
        assert doc.html == SAMPLE_NARRATIVE_HTML

    def test_empty_main_content(self):
        doc = parse_page("https://example.com", SAMPLE_EMPTY_HTML)
        assert doc.content.strip() == ""

    def test_malformed_html_doesnt_crash(self):
        doc = parse_page("https://example.com", SAMPLE_MALFORMED_HTML)
        assert doc is not None
        assert "Some content" in doc.content

    def test_code_blocks_in_content(self):
        doc = parse_page("https://example.com", SAMPLE_CODE_HEAVY_HTML)
        assert "calculate_tax" in doc.content

    def test_section_from_root_url(self):
        doc = parse_page("https://example.com/", SAMPLE_NARRATIVE_HTML)
        assert doc.section == ""

    def test_title_fallback_to_path(self):
        html = "<html><body><main><p>No title tag</p></main></body></html>"
        doc = parse_page("https://example.com/some/path", html)
        assert doc.title is not None
        assert len(doc.title) > 0
