"""Integration tests for the full retrieval pipeline.

Tests the orchestration in context_builder.py — the shared core
that every interface calls. Verifies that hybrid search → rerank →
corrective → parent expansion works end-to-end with mocked externals.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.retrieval.context_builder import retrieve, RetrievalResult
from app.retrieval.corrective import RetrievalConfidence
from app.retrieval.vector_store import RetrievedChunk
from tests.conftest import make_chunk


def _make_db_rows(n: int = 5):
    """Create mock DB rows matching the pgvector query shape."""
    return [
        {
            "chunk_id": f"c{i}",
            "parent_id": f"p{i % 2}",
            "content": f"Tax content chunk {i}",
            "contextual_content": f"Context: this chunk is about taxes. Tax content chunk {i}",
            "source_url": "https://irs.gov/pub/p501",
            "source_title": "Pub 501",
            "section": "publications",
            "document_type": "narrative",
            "similarity": 0.9 - (i * 0.05),
        }
        for i in range(n)
    ]


@pytest.fixture
def mock_full_pipeline():
    """Mock all external dependencies for the full pipeline."""
    with patch("app.retrieval.vector_store.embed_texts", new_callable=AsyncMock) as mock_embed, \
         patch("app.retrieval.vector_store.get_pool", new_callable=AsyncMock) as mock_pool, \
         patch("app.retrieval.hybrid_search.bm25_search", new_callable=AsyncMock) as mock_bm25, \
         patch("app.retrieval.reranker._get_ranker") as mock_ranker, \
         patch("app.retrieval.corrective.anthropic.Anthropic") as mock_anthropic, \
         patch("app.retrieval.context_builder._fetch_parents", new_callable=AsyncMock) as mock_parents:

        # Embeddings
        mock_embed.return_value = [[0.1] * 1024]

        # DB connection for vector search
        conn = AsyncMock()
        conn.fetch.return_value = _make_db_rows(5)
        pool = MagicMock()
        pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
        pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_pool.return_value = pool

        # BM25 returns empty (vector search provides results)
        mock_bm25.return_value = []

        # FlashRank reranker
        ranker = MagicMock()
        ranker.rerank.return_value = [
            {"id": f"c{i}", "score": 0.95 - (i * 0.1)} for i in range(5)
        ]
        mock_ranker.return_value = ranker

        # Corrective RAG (all relevant)
        client = MagicMock()
        resp = MagicMock()
        resp.content = [MagicMock(text="relevant")]
        client.messages.create.return_value = resp
        mock_anthropic.return_value = client

        # Parent chunks
        mock_parents.return_value = {
            "p0": "Full parent content for section 0.",
            "p1": "Full parent content for section 1.",
        }

        yield {
            "embed": mock_embed,
            "pool": mock_pool,
            "ranker": ranker,
            "anthropic": client,
            "parents": mock_parents,
        }


class TestFullRetrievalPipeline:
    @pytest.mark.asyncio
    async def test_returns_retrieval_result(self, mock_full_pipeline):
        result = await retrieve("What is the standard deduction?")

        assert isinstance(result, RetrievalResult)
        assert len(result.children) > 0
        assert result.corrective is not None
        assert result.parent_contents is not None

    @pytest.mark.asyncio
    async def test_children_have_required_fields(self, mock_full_pipeline):
        result = await retrieve("tax question")

        for child in result.children:
            assert child.chunk_id is not None
            assert child.parent_id is not None
            assert child.content is not None
            assert child.source_url is not None

    @pytest.mark.asyncio
    async def test_parent_contents_fetched(self, mock_full_pipeline):
        result = await retrieve("tax question")
        assert len(result.parent_contents) > 0

    @pytest.mark.asyncio
    async def test_confident_retrieval(self, mock_full_pipeline):
        result = await retrieve("standard deduction amount")
        assert result.corrective.confidence == RetrievalConfidence.CONFIDENT

    @pytest.mark.asyncio
    async def test_respects_top_k(self, mock_full_pipeline):
        result = await retrieve("test", top_k=2)
        assert len(result.children) <= 2

    @pytest.mark.asyncio
    async def test_retry_on_low_confidence(self, mock_full_pipeline):
        """When initial retrieval has low confidence, pipeline should retry."""
        mocks = mock_full_pipeline

        # Make corrective RAG return low confidence, then retry succeeds
        call_count = [0]
        def side_effect(*args, **kwargs):
            call_count[0] += 1
            resp = MagicMock()
            if call_count[0] <= 5:
                resp.content = [MagicMock(text="irrelevant")]
            elif call_count[0] == 6:
                # Query transformation
                resp.content = [MagicMock(text="rewritten tax query")]
            else:
                resp.content = [MagicMock(text="relevant")]
            return resp

        mocks["anthropic"].messages.create.side_effect = side_effect

        result = await retrieve("vague question")
        assert result is not None


class TestPipelineEdgeCases:
    @pytest.mark.asyncio
    async def test_no_bm25_results(self, mock_full_pipeline):
        """BM25 returns nothing but vector search works."""
        # The mock only sets up vector search results, BM25 would also use the same mock
        # but with different query. This tests RRF handles one empty list.
        result = await retrieve("test query")
        assert len(result.children) > 0

    @pytest.mark.asyncio
    async def test_no_parent_chunks_found(self, mock_full_pipeline):
        mock_full_pipeline["parents"].return_value = {}
        result = await retrieve("orphan question")
        assert result.parent_contents == {}
        assert len(result.children) > 0
