"""Tests for agent tool definitions and execution."""

import pytest
from unittest.mock import AsyncMock, patch

from app.agent.tools import TOOL_DEFINITIONS, handle_tool_call
from app.retrieval.context_builder import RetrievalResult
from app.retrieval.corrective import CorrectedRetrieval, RetrievalConfidence
from tests.conftest import make_chunk


class TestToolDefinitions:
    def test_search_tool_exists(self):
        names = [t["name"] for t in TOOL_DEFINITIONS]
        assert "search_knowledge_base" in names

    def test_search_tool_schema_valid(self):
        search_tool = next(t for t in TOOL_DEFINITIONS if t["name"] == "search_knowledge_base")
        schema = search_tool["input_schema"]
        assert schema["type"] == "object"
        assert "query" in schema["properties"]
        assert "query" in schema["required"]

    def test_search_tool_has_description(self):
        search_tool = next(t for t in TOOL_DEFINITIONS if t["name"] == "search_knowledge_base")
        assert len(search_tool["description"]) > 20


class TestHandleToolCall:
    @pytest.mark.asyncio
    async def test_search_returns_formatted_results(self):
        children = [make_chunk(chunk_id="c0", parent_id="p0")]
        retrieval = RetrievalResult(
            children=children,
            parent_contents={"p0": "Parent content about deductions."},
            corrective=CorrectedRetrieval(
                chunks=children,
                confidence=RetrievalConfidence.CONFIDENT,
                filtered_count=1,
                original_count=1,
            ),
        )

        with patch("app.agent.tools.retrieve", new_callable=AsyncMock) as mock_retrieve:
            mock_retrieve.return_value = retrieval
            result = await handle_tool_call("search_knowledge_base", {"query": "deductions"})

        assert "Parent content about deductions" in result
        assert "confident" in result.lower()
        assert "[1]" in result

    @pytest.mark.asyncio
    async def test_search_respects_top_k(self):
        children = [make_chunk(chunk_id="c0")]
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

        with patch("app.agent.tools.retrieve", new_callable=AsyncMock) as mock_retrieve:
            mock_retrieve.return_value = retrieval
            await handle_tool_call("search_knowledge_base", {"query": "test", "top_k": 3})

        mock_retrieve.assert_called_once_with("test", top_k=3)

    @pytest.mark.asyncio
    async def test_search_default_top_k(self):
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

        with patch("app.agent.tools.retrieve", new_callable=AsyncMock) as mock_retrieve:
            mock_retrieve.return_value = retrieval
            await handle_tool_call("search_knowledge_base", {"query": "test"})

        mock_retrieve.assert_called_once_with("test", top_k=5)

    @pytest.mark.asyncio
    async def test_unknown_tool(self):
        result = await handle_tool_call("nonexistent_tool", {})
        assert "Unknown tool" in result

    @pytest.mark.asyncio
    async def test_empty_retrieval_results(self):
        retrieval = RetrievalResult(
            children=[],
            parent_contents={},
            corrective=CorrectedRetrieval(
                chunks=[],
                confidence=RetrievalConfidence.LOW_CONFIDENCE,
                filtered_count=0,
                original_count=0,
            ),
        )

        with patch("app.agent.tools.retrieve", new_callable=AsyncMock) as mock_retrieve:
            mock_retrieve.return_value = retrieval
            result = await handle_tool_call("search_knowledge_base", {"query": "test"})

        assert "low_confidence" in result.lower()
