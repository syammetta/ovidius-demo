"""Tests for query logging middleware."""

import pytest
from unittest.mock import AsyncMock, patch

from app.middleware.query_logger import QueryLogEntry, log_query


class TestQueryLogEntry:
    def test_defaults(self):
        entry = QueryLogEntry(question="What is AGI?")
        assert entry.question == "What is AGI?"
        assert entry.answer == ""
        assert entry.interface == "api"
        assert entry.session_id is None
        assert entry.trace_id is None

    def test_full_entry(self):
        entry = QueryLogEntry(
            question="What is the standard deduction?",
            answer="$15,000 for single filers.",
            citations=[{"index": 1, "source_url": "https://irs.gov"}],
            confidence="confident",
            retrieval_method="hybrid_rrf+rerank",
            latency_ms=1234.5,
            retrieval_ms=800.0,
            generation_ms=400.0,
            trace_id="abc123",
            session_id="sess-456",
            interface="agent",
        )
        assert entry.confidence == "confident"
        assert entry.interface == "agent"


class TestLogQuery:
    @pytest.mark.asyncio
    async def test_inserts_into_db(self, mock_db_pool):
        entry = QueryLogEntry(
            question="test question",
            answer="test answer",
            interface="api",
        )
        await log_query(entry)
        mock_db_pool.execute.assert_called_once()
        call_args = mock_db_pool.execute.call_args
        assert "INSERT INTO query_logs" in call_args[0][0]
        assert call_args[0][1] == "test question"

    @pytest.mark.asyncio
    async def test_swallows_exceptions(self, mock_db_pool):
        mock_db_pool.execute.side_effect = Exception("DB down")
        entry = QueryLogEntry(question="test")
        await log_query(entry)

    @pytest.mark.asyncio
    async def test_all_fields_passed(self, mock_db_pool):
        entry = QueryLogEntry(
            question="q",
            answer="a",
            citations=[{"index": 1}],
            confidence="confident",
            retrieval_method="hybrid",
            pipeline_steps=[{"step": "search"}],
            chunks_used=5,
            parent_chunks_used=2,
            latency_ms=100.0,
            retrieval_ms=60.0,
            generation_ms=40.0,
            trace_id="trace-123",
            session_id="sess-456",
            interface="agent",
        )
        await log_query(entry)
        call_args = mock_db_pool.execute.call_args[0]
        assert call_args[1] == "q"
        assert call_args[2] == "a"
        assert call_args[14] == "agent"
