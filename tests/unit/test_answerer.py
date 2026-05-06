"""Tests for citation-grounded answer generation."""

import pytest
from unittest.mock import MagicMock, patch, AsyncMock

from app.generation.answerer import generate_answer, SYSTEM_PROMPT, LOW_CONFIDENCE_ADDENDUM
from app.retrieval.context_builder import RetrievalResult
from app.retrieval.corrective import CorrectedRetrieval, RetrievalConfidence
from tests.conftest import make_chunk


def _make_retrieval_result(
    n_chunks: int = 3,
    confidence: RetrievalConfidence = RetrievalConfidence.CONFIDENT,
    with_parents: bool = True,
) -> RetrievalResult:
    children = [make_chunk(chunk_id=f"c{i}", parent_id=f"p{i}") for i in range(n_chunks)]
    parent_contents = {}
    if with_parents:
        parent_contents = {
            f"p{i}": f"Full parent content for section {i} with detailed tax information."
            for i in range(n_chunks)
        }

    return RetrievalResult(
        children=children,
        parent_contents=parent_contents,
        corrective=CorrectedRetrieval(
            chunks=children,
            confidence=confidence,
            filtered_count=n_chunks,
            original_count=n_chunks,
        ),
    )


class TestGenerateAnswer:
    @pytest.mark.asyncio
    async def test_returns_answer_with_citations(self):
        retrieval = _make_retrieval_result()
        with patch("app.generation.answerer.anthropic.Anthropic") as mock:
            client = MagicMock()
            resp = MagicMock()
            resp.content = [MagicMock(text="The standard deduction is $15,000 [1].")]
            client.messages.create.return_value = resp
            mock.return_value = client

            result = await generate_answer("What is the standard deduction?", retrieval)

        assert result.answer == "The standard deduction is $15,000 [1]."
        assert len(result.citations) == 3
        assert result.citations[0].index == 1
        assert result.confidence == "confident"

    @pytest.mark.asyncio
    async def test_low_confidence_adds_caution(self):
        retrieval = _make_retrieval_result(confidence=RetrievalConfidence.LOW_CONFIDENCE)
        with patch("app.generation.answerer.anthropic.Anthropic") as mock:
            client = MagicMock()
            resp = MagicMock()
            resp.content = [MagicMock(text="I don't have enough information.")]
            client.messages.create.return_value = resp
            mock.return_value = client

            await generate_answer("obscure question", retrieval)

            call_args = client.messages.create.call_args
            system_prompt = call_args.kwargs.get("system", "")
            assert "LOW CONFIDENCE" in system_prompt

    @pytest.mark.asyncio
    async def test_confident_uses_normal_prompt(self):
        retrieval = _make_retrieval_result(confidence=RetrievalConfidence.CONFIDENT)
        with patch("app.generation.answerer.anthropic.Anthropic") as mock:
            client = MagicMock()
            resp = MagicMock()
            resp.content = [MagicMock(text="Answer.")]
            client.messages.create.return_value = resp
            mock.return_value = client

            await generate_answer("question", retrieval)

            call_args = client.messages.create.call_args
            system_prompt = call_args.kwargs.get("system", "")
            assert "LOW CONFIDENCE" not in system_prompt

    @pytest.mark.asyncio
    async def test_parent_content_used_in_context(self):
        retrieval = _make_retrieval_result(with_parents=True)
        with patch("app.generation.answerer.anthropic.Anthropic") as mock:
            client = MagicMock()
            resp = MagicMock()
            resp.content = [MagicMock(text="Answer.")]
            client.messages.create.return_value = resp
            mock.return_value = client

            await generate_answer("question", retrieval)

            call_args = client.messages.create.call_args
            messages = call_args.kwargs.get("messages", [])
            user_content = messages[0]["content"]
            assert "Full parent content" in user_content

    @pytest.mark.asyncio
    async def test_falls_back_to_child_content_without_parents(self):
        retrieval = _make_retrieval_result(with_parents=False)
        with patch("app.generation.answerer.anthropic.Anthropic") as mock:
            client = MagicMock()
            resp = MagicMock()
            resp.content = [MagicMock(text="Answer.")]
            client.messages.create.return_value = resp
            mock.return_value = client

            await generate_answer("question", retrieval)

            call_args = client.messages.create.call_args
            messages = call_args.kwargs.get("messages", [])
            user_content = messages[0]["content"]
            assert "standard deduction" in user_content.lower()

    @pytest.mark.asyncio
    async def test_empty_retrieval(self):
        retrieval = _make_retrieval_result(n_chunks=0, with_parents=False)
        with patch("app.generation.answerer.anthropic.Anthropic") as mock:
            client = MagicMock()
            resp = MagicMock()
            resp.content = [MagicMock(text="No information available.")]
            client.messages.create.return_value = resp
            mock.return_value = client

            result = await generate_answer("question", retrieval)

        assert len(result.citations) == 0
        assert result.chunks_used == 0

    @pytest.mark.asyncio
    async def test_citation_source_urls_correct(self):
        retrieval = _make_retrieval_result(n_chunks=2)
        with patch("app.generation.answerer.anthropic.Anthropic") as mock:
            client = MagicMock()
            resp = MagicMock()
            resp.content = [MagicMock(text="Answer [1] [2].")]
            client.messages.create.return_value = resp
            mock.return_value = client

            result = await generate_answer("question", retrieval)

        for citation in result.citations:
            assert citation.source_url.startswith("https://")
            assert citation.chunk_id.startswith("c")

    @pytest.mark.asyncio
    async def test_deduplicates_parent_context(self):
        """Two children with the same parent should only include parent content once."""
        children = [
            make_chunk(chunk_id="c0", parent_id="p_shared"),
            make_chunk(chunk_id="c1", parent_id="p_shared"),
        ]
        parent_contents = {"p_shared": "Shared parent content about deductions."}

        retrieval = RetrievalResult(
            children=children,
            parent_contents=parent_contents,
            corrective=CorrectedRetrieval(
                chunks=children,
                confidence=RetrievalConfidence.CONFIDENT,
                filtered_count=2,
                original_count=2,
            ),
        )

        with patch("app.generation.answerer.anthropic.Anthropic") as mock:
            client = MagicMock()
            resp = MagicMock()
            resp.content = [MagicMock(text="Answer.")]
            client.messages.create.return_value = resp
            mock.return_value = client

            await generate_answer("question", retrieval)

            call_args = client.messages.create.call_args
            user_content = call_args.kwargs["messages"][0]["content"]
            assert user_content.count("Shared parent content") == 1
