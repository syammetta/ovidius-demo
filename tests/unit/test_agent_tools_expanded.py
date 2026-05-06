"""Tests for expanded agent tools — get_section, compare, calculate, cache."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.agent.tools import (
    TOOL_DEFINITIONS,
    ToolCache,
    handle_tool_call,
    _handle_calculate,
    STANDARD_DEDUCTIONS_2025,
    TAX_BRACKETS_2025,
)
from app.retrieval.context_builder import RetrievalResult
from app.retrieval.corrective import CorrectedRetrieval, RetrievalConfidence
from tests.conftest import make_chunk


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

class TestExpandedToolDefinitions:
    def test_four_tools_defined(self):
        names = [t["name"] for t in TOOL_DEFINITIONS]
        assert len(names) == 4
        assert "search_knowledge_base" in names
        assert "get_document_section" in names
        assert "compare_sources" in names
        assert "calculate_tax" in names

    def test_all_tools_have_required_fields(self):
        for tool in TOOL_DEFINITIONS:
            assert "name" in tool
            assert "description" in tool
            assert "input_schema" in tool
            assert tool["input_schema"]["type"] == "object"
            assert "required" in tool["input_schema"]
            assert len(tool["description"]) > 20

    def test_calculate_tax_has_enum_options(self):
        calc_tool = next(t for t in TOOL_DEFINITIONS if t["name"] == "calculate_tax")
        props = calc_tool["input_schema"]["properties"]
        assert "standard_deduction" in props["calculation_type"]["enum"]
        assert "tax_bracket" in props["calculation_type"]["enum"]
        assert "credit_phaseout" in props["calculation_type"]["enum"]


# ---------------------------------------------------------------------------
# Tool cache
# ---------------------------------------------------------------------------

class TestToolCache:
    def test_cache_miss(self):
        cache = ToolCache()
        assert cache.get("search", {"query": "test"}) is None

    def test_cache_hit(self):
        cache = ToolCache()
        cache.set("search", {"query": "test"}, "result text")
        assert cache.get("search", {"query": "test"}) == "result text"

    def test_different_inputs_different_keys(self):
        cache = ToolCache()
        cache.set("search", {"query": "a"}, "result a")
        cache.set("search", {"query": "b"}, "result b")
        assert cache.get("search", {"query": "a"}) == "result a"
        assert cache.get("search", {"query": "b"}) == "result b"

    def test_different_tools_different_keys(self):
        cache = ToolCache()
        cache.set("search", {"query": "x"}, "search result")
        cache.set("compare", {"query": "x"}, "compare result")
        assert cache.get("search", {"query": "x"}) == "search result"
        assert cache.get("compare", {"query": "x"}) == "compare result"


# ---------------------------------------------------------------------------
# calculate_tax handler
# ---------------------------------------------------------------------------

class TestCalculateTax:
    def test_standard_deduction_single(self):
        result = _handle_calculate({
            "calculation_type": "standard_deduction",
            "filing_status": "single",
        })
        assert "$15,000" in result
        assert "single" in result.lower() or "Single" in result

    def test_standard_deduction_married_jointly(self):
        result = _handle_calculate({
            "calculation_type": "standard_deduction",
            "filing_status": "married_jointly",
        })
        assert "$30,000" in result

    def test_standard_deduction_head_of_household(self):
        result = _handle_calculate({
            "calculation_type": "standard_deduction",
            "filing_status": "head_of_household",
        })
        assert "$22,500" in result

    def test_tax_bracket_single_75k(self):
        result = _handle_calculate({
            "calculation_type": "tax_bracket",
            "filing_status": "single",
            "income": 75000,
        })
        assert "Estimated Tax" in result
        assert "Effective Rate" in result
        assert "Marginal Rate" in result
        assert "22%" in result

    def test_tax_bracket_requires_positive_income(self):
        result = _handle_calculate({
            "calculation_type": "tax_bracket",
            "filing_status": "single",
            "income": 0,
        })
        assert "Error" in result

    def test_tax_bracket_married_jointly(self):
        result = _handle_calculate({
            "calculation_type": "tax_bracket",
            "filing_status": "married_jointly",
            "income": 150000,
        })
        assert "Estimated Tax" in result
        assert "150,000" in result

    def test_credit_phaseout_eitc(self):
        result = _handle_calculate({
            "calculation_type": "credit_phaseout",
            "filing_status": "single",
            "income": 15000,
            "credit_type": "eitc",
        })
        assert "EITC" in result
        assert "Estimated Credit" in result

    def test_credit_phaseout_eitc_above_range(self):
        result = _handle_calculate({
            "calculation_type": "credit_phaseout",
            "filing_status": "single",
            "income": 25000,
            "credit_type": "eitc",
        })
        assert "$0.00" in result

    def test_credit_phaseout_ctc(self):
        result = _handle_calculate({
            "calculation_type": "credit_phaseout",
            "filing_status": "single",
            "income": 180000,
            "credit_type": "ctc",
        })
        assert "Child Tax Credit" in result
        assert "$2,000" in result

    def test_unknown_calculation_type(self):
        result = _handle_calculate({
            "calculation_type": "unknown",
            "filing_status": "single",
        })
        assert "Unknown" in result


# ---------------------------------------------------------------------------
# get_document_section handler
# ---------------------------------------------------------------------------

class TestGetDocumentSection:
    @pytest.mark.asyncio
    async def test_returns_section_content(self, mock_db_pool):
        mock_db_pool.fetchrow.return_value = {
            "content": "Full section about standard deduction amounts.",
            "source_url": "https://irs.gov/pub/p501",
            "source_title": "Pub 501",
            "section": "publications",
            "document_type": "narrative",
        }

        result = await handle_tool_call("get_document_section", {"parent_id": "p_abc123_0"})
        assert "Full section about standard deduction amounts" in result
        assert "Pub 501" in result

    @pytest.mark.asyncio
    async def test_returns_not_found(self, mock_db_pool):
        mock_db_pool.fetchrow.return_value = None
        result = await handle_tool_call("get_document_section", {"parent_id": "nonexistent"})
        assert "No document section found" in result


# ---------------------------------------------------------------------------
# compare_sources handler
# ---------------------------------------------------------------------------

class TestCompareSources:
    @pytest.mark.asyncio
    async def test_compare_returns_multiple_sections(self):
        children = [make_chunk(chunk_id="c0", parent_id="p0")]
        retrieval = RetrievalResult(
            children=children,
            parent_contents={"p0": "Content about topic."},
            corrective=CorrectedRetrieval(
                chunks=children,
                confidence=RetrievalConfidence.CONFIDENT,
                filtered_count=1,
                original_count=1,
            ),
        )

        with patch("app.agent.tools.retrieve", new_callable=AsyncMock) as mock_retrieve:
            mock_retrieve.return_value = retrieval
            result = await handle_tool_call("compare_sources", {
                "queries": ["query one", "query two"],
            })

        assert "Query 1" in result
        assert "Query 2" in result
        assert "query one" in result
        assert "query two" in result


# ---------------------------------------------------------------------------
# handle_tool_call with cache
# ---------------------------------------------------------------------------

class TestHandleToolCallWithCache:
    @pytest.mark.asyncio
    async def test_cache_prevents_duplicate_calls(self):
        children = [make_chunk()]
        retrieval = RetrievalResult(
            children=children,
            parent_contents={},
            corrective=CorrectedRetrieval(
                chunks=children,
                confidence=RetrievalConfidence.CONFIDENT,
                filtered_count=1,
                original_count=1,
            ),
        )
        cache = ToolCache()

        with patch("app.agent.tools.retrieve", new_callable=AsyncMock) as mock_retrieve:
            mock_retrieve.return_value = retrieval

            await handle_tool_call("search_knowledge_base", {"query": "test"}, cache=cache)
            await handle_tool_call("search_knowledge_base", {"query": "test"}, cache=cache)

        mock_retrieve.assert_called_once()
