"""Tests for cross-encoder reranking."""

import pytest
from unittest.mock import MagicMock
from app.retrieval.reranker import rerank
from tests.conftest import make_chunk


class TestReranker:
    @pytest.mark.asyncio
    async def test_returns_top_k(self, mock_flashrank):
        chunks = [make_chunk(chunk_id=f"c{i}") for i in range(10)]
        mock_flashrank.rerank.return_value = [
            {"id": f"c{i}", "score": 0.9 - i * 0.05} for i in range(10)
        ]

        result = await rerank("test query", chunks, top_k=3)
        assert len(result) == 3

    @pytest.mark.asyncio
    async def test_fewer_chunks_than_top_k(self, mock_flashrank):
        chunks = [make_chunk(chunk_id="c0"), make_chunk(chunk_id="c1")]
        result = await rerank("test query", chunks, top_k=5)
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_empty_chunks(self, mock_flashrank):
        result = await rerank("test query", [], top_k=5)
        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_scores_updated(self, mock_flashrank):
        chunks = [make_chunk(chunk_id="c0", score=0.5)]
        mock_flashrank.rerank.return_value = [{"id": "c0", "score": 0.95}]

        result = await rerank("test query", chunks, top_k=5)
        assert result[0].score == 0.95

    @pytest.mark.asyncio
    async def test_retrieval_method_appended(self, mock_flashrank):
        chunks = [make_chunk(chunk_id="c0", retrieval_method="hybrid_rrf")]
        mock_flashrank.rerank.return_value = [{"id": "c0", "score": 0.9}]

        result = await rerank("test query", chunks, top_k=5)
        assert "rerank" in result[0].retrieval_method

    @pytest.mark.asyncio
    async def test_uses_contextual_content_for_ranking(self, mock_flashrank):
        chunks = [make_chunk(
            chunk_id="c0",
            content="raw content",
            contextual_content="contextual content with more info",
        )]
        mock_flashrank.rerank.return_value = [{"id": "c0", "score": 0.9}]

        await rerank("test query", chunks, top_k=5)
        call_args = mock_flashrank.rerank.call_args
        passages = call_args[0][0].passages
        assert passages[0]["text"] == "contextual content with more info"
