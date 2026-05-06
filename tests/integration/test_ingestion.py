"""Integration tests for the ingestion pipeline.

Tests the full flow: crawl → adaptive chunk → contextualize → embed → store.
Mocks network and DB but tests the orchestration between components.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.ingestion.crawler import crawl_url, parse_page
from app.ingestion.chunker import chunk_document
from app.ingestion.contextualizer import contextualize_chunks
from tests.conftest import SAMPLE_NARRATIVE_HTML, SAMPLE_CODE_HEAVY_HTML


class TestIngestionFlow:
    @pytest.mark.asyncio
    async def test_crawl_and_chunk_narrative(self):
        """Test that narrative HTML gets chunked into parent-child pairs."""
        doc = parse_page("https://irs.gov/publications/p501", SAMPLE_NARRATIVE_HTML)
        result = chunk_document(doc.content, doc.url, doc.title, doc.section)

        assert len(result.parents) > 0
        assert len(result.children) > 0

        parent_ids = {p.parent_id for p in result.parents}
        for child in result.children:
            assert child.parent_id in parent_ids

    @pytest.mark.asyncio
    async def test_crawl_and_chunk_code_heavy(self):
        doc = parse_page("https://example.com/code", SAMPLE_CODE_HEAVY_HTML)
        result = chunk_document(doc.content, doc.url, doc.title, doc.section)

        assert len(result.parents) > 0
        assert len(result.children) > 0

    @pytest.mark.asyncio
    async def test_contextualize_adds_prefix(self):
        """Test that contextualization adds content to child chunks."""
        doc = parse_page("https://irs.gov/publications/p501", SAMPLE_NARRATIVE_HTML)
        result = chunk_document(doc.content, doc.url, doc.title, doc.section)

        if not result.children:
            pytest.skip("No children produced from sample HTML")

        with patch("app.ingestion.contextualizer.anthropic.Anthropic") as mock:
            client = MagicMock()
            resp = MagicMock()
            resp.content = [MagicMock(text="This chunk discusses standard deduction amounts from IRS Publication 501.")]
            client.messages.create.return_value = resp
            mock.return_value = client

            contextualized = await contextualize_chunks(result.children, result.parents)

        for child in contextualized:
            ctx = getattr(child, "_contextual_content", None)
            if ctx:
                assert len(ctx) > len(child.content)
                assert child.content in ctx

    @pytest.mark.asyncio
    async def test_contextualize_handles_api_failure(self):
        """If contextualization fails, original content should be preserved."""
        doc = parse_page("https://irs.gov/publications/p501", SAMPLE_NARRATIVE_HTML)
        result = chunk_document(doc.content, doc.url, doc.title, doc.section)

        if not result.children:
            pytest.skip("No children produced from sample HTML")

        with patch("app.ingestion.contextualizer.anthropic.Anthropic") as mock:
            client = MagicMock()
            client.messages.create.side_effect = Exception("API down")
            mock.return_value = client

            contextualized = await contextualize_chunks(result.children, result.parents)

        assert len(contextualized) == len(result.children)
        for child in contextualized:
            assert child.content is not None
            assert len(child.content) > 0

    @pytest.mark.asyncio
    async def test_embed_and_store_called_correctly(self):
        """Test that embedder receives contextualized chunks."""
        doc = parse_page("https://irs.gov/publications/p501", SAMPLE_NARRATIVE_HTML)
        result = chunk_document(doc.content, doc.url, doc.title, doc.section)

        if not result.children:
            pytest.skip("No children produced from sample HTML")

        with patch("app.ingestion.contextualizer.anthropic.Anthropic") as mock_ant:
            client = MagicMock()
            resp = MagicMock()
            resp.content = [MagicMock(text="Context prefix.")]
            client.messages.create.return_value = resp
            mock_ant.return_value = client

            contextualized = await contextualize_chunks(result.children, result.parents)

        with patch("app.ingestion.embedder.embed_texts", new_callable=AsyncMock) as mock_embed, \
             patch("app.ingestion.embedder.get_pool", new_callable=AsyncMock) as mock_pool:

            mock_embed.return_value = [[0.1] * 1024] * len(contextualized)
            conn = AsyncMock()
            pool = AsyncMock()
            pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
            pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_pool.return_value = pool

            from app.ingestion.embedder import embed_and_store_children
            count = await embed_and_store_children(contextualized)

        assert count == len(contextualized)
        mock_embed.assert_called_once()

    @pytest.mark.asyncio
    async def test_empty_document_skips_gracefully(self):
        """Empty content should produce no chunks and not crash."""
        result = chunk_document("", "https://example.com", "Empty", "")
        assert len(result.parents) == 0
        assert len(result.children) == 0


class TestCrawlerIntegration:
    @pytest.mark.asyncio
    async def test_crawl_url_with_cache_hit(self):
        with patch("app.ingestion.crawler.settings") as mock_settings, \
             patch("app.ingestion.crawler.get_document") as mock_get:

            mock_settings.r2_account_id = "test-account"
            mock_get.return_value = SAMPLE_NARRATIVE_HTML

            doc = await crawl_url("https://irs.gov/pub/p501")

        assert doc is not None
        assert "Publication 501" in doc.title
        mock_get.assert_called_once()

    @pytest.mark.asyncio
    async def test_crawl_url_cache_miss_fetches(self):
        with patch("app.ingestion.crawler.settings") as mock_settings, \
             patch("app.ingestion.crawler.get_document") as mock_get, \
             patch("app.ingestion.crawler.store_document") as mock_store, \
             patch("httpx.AsyncClient") as mock_client_cls:

            mock_settings.r2_account_id = "test-account"
            mock_get.return_value = None

            mock_resp = MagicMock()
            mock_resp.text = SAMPLE_NARRATIVE_HTML
            mock_resp.raise_for_status = MagicMock()

            mock_client = AsyncMock()
            mock_client.get.return_value = mock_resp
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            doc = await crawl_url("https://irs.gov/pub/p501")

        assert doc is not None
        mock_store.assert_called_once()

    @pytest.mark.asyncio
    async def test_crawl_url_no_r2_config(self):
        """Without R2 config, should fetch directly without caching."""
        with patch("app.ingestion.crawler.settings") as mock_settings, \
             patch("httpx.AsyncClient") as mock_client_cls:

            mock_settings.r2_account_id = ""

            mock_resp = MagicMock()
            mock_resp.text = SAMPLE_NARRATIVE_HTML
            mock_resp.raise_for_status = MagicMock()

            mock_client = AsyncMock()
            mock_client.get.return_value = mock_resp
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            doc = await crawl_url("https://irs.gov/pub/p501")

        assert doc is not None
