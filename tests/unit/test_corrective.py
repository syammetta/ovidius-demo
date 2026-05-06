"""Tests for Corrective RAG — confidence routing and query transformation.

Key bugs to catch:
- All chunks filtered producing empty result
- Confidence thresholds at exact boundaries
- Query transformation called only on low confidence
- Relevance parsing edge cases ("relevant" vs "irrelevant" vs garbage)
"""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.retrieval.corrective import (
    evaluate_retrieval,
    RetrievalConfidence,
)
from tests.conftest import make_chunk


def _mock_batch_response(n_relevant: int, n_total: int):
    """Create a mock async client whose batch call returns JSON verdicts."""
    client = MagicMock()
    verdicts = {}
    for i in range(1, n_total + 1):
        verdicts[str(i)] = "relevant" if i <= n_relevant else "irrelevant"

    batch_resp = MagicMock()
    batch_resp.content = [MagicMock(text=json.dumps(verdicts))]
    batch_resp.usage = MagicMock(input_tokens=100, output_tokens=20)

    transform_resp = MagicMock()
    transform_resp.content = [MagicMock(text="rewritten query about taxes")]
    transform_resp.usage = MagicMock(input_tokens=50, output_tokens=15)

    client.messages.create = AsyncMock(side_effect=[batch_resp, transform_resp])
    return client


class TestEvaluateRetrieval:
    @pytest.mark.asyncio
    async def test_confident_when_most_relevant(self):
        chunks = [make_chunk(chunk_id=f"c{i}") for i in range(5)]
        with patch("app.retrieval.corrective.anthropic.AsyncAnthropic") as mock:
            mock.return_value = _mock_batch_response(4, 5)
            result = await evaluate_retrieval("test query", chunks)

        assert result.confidence == RetrievalConfidence.CONFIDENT
        assert result.filtered_count == 4
        assert result.original_count == 5

    @pytest.mark.asyncio
    async def test_uncertain_when_mixed(self):
        chunks = [make_chunk(chunk_id=f"c{i}") for i in range(5)]
        with patch("app.retrieval.corrective.anthropic.AsyncAnthropic") as mock:
            mock.return_value = _mock_batch_response(2, 5)
            result = await evaluate_retrieval("test query", chunks)

        assert result.confidence == RetrievalConfidence.UNCERTAIN

    @pytest.mark.asyncio
    async def test_low_confidence_when_mostly_irrelevant(self):
        chunks = [make_chunk(chunk_id=f"c{i}") for i in range(5)]
        with patch("app.retrieval.corrective.anthropic.AsyncAnthropic") as mock:
            mock.return_value = _mock_batch_response(1, 5)
            result = await evaluate_retrieval("test query", chunks)

        assert result.confidence == RetrievalConfidence.LOW_CONFIDENCE
        assert result.transformed_query is not None

    @pytest.mark.asyncio
    async def test_empty_chunks(self):
        result = await evaluate_retrieval("test query", [])
        assert result.confidence == RetrievalConfidence.LOW_CONFIDENCE
        assert result.filtered_count == 0
        assert result.original_count == 0

    @pytest.mark.asyncio
    async def test_api_error_defaults_to_all_relevant(self):
        """If the relevance check fails, keep all chunks (fail open) — confident."""
        chunks = [make_chunk(chunk_id="c0")]
        with patch("app.retrieval.corrective.anthropic.AsyncAnthropic") as mock:
            client = MagicMock()
            client.messages.create = AsyncMock(side_effect=Exception("API error"))
            mock.return_value = client

            result = await evaluate_retrieval("test query", chunks)

        assert len(result.chunks) > 0
        assert result.confidence == RetrievalConfidence.CONFIDENT

    @pytest.mark.asyncio
    async def test_exact_boundary_60_percent(self):
        """60% relevance should be confident (threshold is 0.6)."""
        chunks = [make_chunk(chunk_id=f"c{i}") for i in range(5)]
        with patch("app.retrieval.corrective.anthropic.AsyncAnthropic") as mock:
            mock.return_value = _mock_batch_response(3, 5)
            result = await evaluate_retrieval("test query", chunks)

        assert result.confidence == RetrievalConfidence.CONFIDENT

    @pytest.mark.asyncio
    async def test_exact_boundary_30_percent(self):
        """30% relevance should be uncertain (>= 0.3)."""
        chunks = [make_chunk(chunk_id=f"c{i}") for i in range(10)]
        with patch("app.retrieval.corrective.anthropic.AsyncAnthropic") as mock:
            mock.return_value = _mock_batch_response(3, 10)
            result = await evaluate_retrieval("test query", chunks)

        assert result.confidence == RetrievalConfidence.UNCERTAIN

    @pytest.mark.asyncio
    async def test_low_confidence_returns_fallback_chunks(self):
        """Even at low confidence, should return at least some chunks."""
        chunks = [make_chunk(chunk_id=f"c{i}") for i in range(5)]
        with patch("app.retrieval.corrective.anthropic.AsyncAnthropic") as mock:
            mock.return_value = _mock_batch_response(0, 5)
            result = await evaluate_retrieval("test query", chunks)

        assert len(result.chunks) > 0, "Should return fallback chunks even when all irrelevant"

    @pytest.mark.asyncio
    async def test_json_with_code_fence(self):
        """Handle LLM wrapping JSON in code fences."""
        chunks = [make_chunk(chunk_id=f"c{i}") for i in range(3)]
        with patch("app.retrieval.corrective.anthropic.AsyncAnthropic") as mock:
            client = MagicMock()
            verdicts = {"1": "relevant", "2": "relevant", "3": "irrelevant"}
            fenced = f'```json\n{json.dumps(verdicts)}\n```'
            resp = MagicMock()
            resp.content = [MagicMock(text=fenced)]
            resp.usage = MagicMock(input_tokens=100, output_tokens=20)
            client.messages.create = AsyncMock(return_value=resp)
            mock.return_value = client

            result = await evaluate_retrieval("test query", chunks)

        assert result.filtered_count == 2
        assert result.confidence == RetrievalConfidence.CONFIDENT
