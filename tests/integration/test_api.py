"""Integration tests for FastAPI endpoints.

Tests the HTTP interface, request validation, response schema, and
error handling. Mocks external services (Anthropic, Voyage, DB)
but tests the full FastAPI request→response cycle.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient

from app.retrieval.vector_store import RetrievedChunk
from app.retrieval.context_builder import RetrievalResult
from app.retrieval.corrective import CorrectedRetrieval, RetrievalConfidence
from app.generation.answerer import AnswerResult, Citation


def _mock_retrieval_result():
    children = [
        RetrievedChunk(
            chunk_id="c0", parent_id="p0",
            content="Standard deduction info",
            contextual_content=None,
            source_url="https://irs.gov/pub/p501",
            source_title="Pub 501",
            section="pub", document_type="narrative",
            score=0.9, retrieval_method="hybrid_rrf+rerank",
        )
    ]
    return RetrievalResult(
        children=children,
        parent_contents={"p0": "Full parent content."},
        corrective=CorrectedRetrieval(
            chunks=children,
            confidence=RetrievalConfidence.CONFIDENT,
            filtered_count=1, original_count=1,
        ),
    )


def _mock_answer_result():
    return AnswerResult(
        answer="The standard deduction is $15,000 [1].",
        citations=[Citation(index=1, source_url="https://irs.gov/pub/p501", source_title="Pub 501", chunk_id="c0")],
        confidence="confident",
        retrieval_method="hybrid_rrf+rerank",
        chunks_used=1,
        parent_chunks_used=1,
    )


@pytest.fixture
def client():
    """Create a test client with mocked lifespan (no real DB)."""
    with patch("app.api.routes.get_pool", new_callable=AsyncMock), \
         patch("app.api.routes.close_pool", new_callable=AsyncMock):
        from app.api.routes import app
        with TestClient(app) as c:
            yield c


class TestQAEndpoint:
    def test_successful_qa(self, client):
        with patch("app.api.routes.retrieve", new_callable=AsyncMock) as mock_retrieve, \
             patch("app.api.routes.generate_answer", new_callable=AsyncMock) as mock_generate:
            mock_retrieve.return_value = _mock_retrieval_result()
            mock_generate.return_value = _mock_answer_result()

            resp = client.post("/qa", json={"question": "What is the standard deduction?"})

        assert resp.status_code == 200
        data = resp.json()
        assert "answer" in data
        assert "citations" in data
        assert "confidence" in data
        assert "pipeline" in data
        assert "total_ms" in data
        assert data["confidence"] == "confident"
        assert len(data["citations"]) == 1

    def test_qa_with_custom_top_k(self, client):
        with patch("app.api.routes.retrieve", new_callable=AsyncMock) as mock_retrieve, \
             patch("app.api.routes.generate_answer", new_callable=AsyncMock) as mock_generate:
            mock_retrieve.return_value = _mock_retrieval_result()
            mock_generate.return_value = _mock_answer_result()

            resp = client.post("/qa", json={"question": "test", "top_k": 3})

        assert resp.status_code == 200
        mock_retrieve.assert_called_once_with("test", top_k=3)

    def test_qa_missing_question(self, client):
        resp = client.post("/qa", json={})
        assert resp.status_code == 422

    def test_qa_empty_question(self, client):
        with patch("app.api.routes.retrieve", new_callable=AsyncMock) as mock_retrieve, \
             patch("app.api.routes.generate_answer", new_callable=AsyncMock) as mock_generate:
            mock_retrieve.return_value = _mock_retrieval_result()
            mock_generate.return_value = _mock_answer_result()

            resp = client.post("/qa", json={"question": ""})

        assert resp.status_code == 200

    def test_qa_pipeline_timing(self, client):
        with patch("app.api.routes.retrieve", new_callable=AsyncMock) as mock_retrieve, \
             patch("app.api.routes.generate_answer", new_callable=AsyncMock) as mock_generate:
            mock_retrieve.return_value = _mock_retrieval_result()
            mock_generate.return_value = _mock_answer_result()

            resp = client.post("/qa", json={"question": "test"})

        data = resp.json()
        assert len(data["pipeline"]) == 2
        assert data["pipeline"][0]["step"] == "hybrid_search_rerank_correct"
        assert data["pipeline"][1]["step"] == "generate_answer"
        for step in data["pipeline"]:
            assert step["duration_ms"] >= 0
        assert data["total_ms"] >= 0

    def test_qa_citation_structure(self, client):
        with patch("app.api.routes.retrieve", new_callable=AsyncMock) as mock_retrieve, \
             patch("app.api.routes.generate_answer", new_callable=AsyncMock) as mock_generate:
            mock_retrieve.return_value = _mock_retrieval_result()
            mock_generate.return_value = _mock_answer_result()

            resp = client.post("/qa", json={"question": "test"})

        citation = resp.json()["citations"][0]
        assert "index" in citation
        assert "source_url" in citation
        assert "source_title" in citation
        assert citation["index"] == 1


class TestHealthEndpoint:
    def test_health_check(self, client):
        with patch("app.api.routes.get_pool", new_callable=AsyncMock) as mock_pool:
            conn = AsyncMock()
            conn.fetchval = AsyncMock(side_effect=[142, 30])
            pool = AsyncMock()
            pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
            pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_pool.return_value = pool

            resp = client.get("/health")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "child_chunks" in data
        assert "parent_chunks" in data


class TestAgentEndpoint:
    def test_new_session(self, client):
        with patch("app.agent.routes.create_session", new_callable=AsyncMock) as mock_create, \
             patch("app.agent.routes.save_session", new_callable=AsyncMock), \
             patch("app.agent.routes.anthropic.Anthropic") as mock_anthropic:

            from app.agent.session import Session
            mock_create.return_value = Session(session_id="test-session-123")

            mock_client = MagicMock()
            resp_mock = MagicMock()
            resp_mock.content = [MagicMock(text="Hello! How can I help with taxes?", type="text")]
            resp_mock.stop_reason = "end_turn"
            mock_client.messages.create.return_value = resp_mock
            mock_anthropic.return_value = mock_client

            resp = client.post("/agent/chat", json={"message": "Hello"})

        assert resp.status_code == 200
        data = resp.json()
        assert "reply" in data
        assert "session_id" in data
        assert "tool_calls" in data
        assert data["session_id"] == "test-session-123"

    def test_continue_session(self, client):
        with patch("app.agent.routes.load_session", new_callable=AsyncMock) as mock_load, \
             patch("app.agent.routes.save_session", new_callable=AsyncMock), \
             patch("app.agent.routes.anthropic.Anthropic") as mock_anthropic:

            from app.agent.session import Session, Message
            mock_load.return_value = Session(
                session_id="existing-session",
                messages=[
                    Message(role="user", content="What is AGI?"),
                    Message(role="assistant", content="AGI stands for..."),
                ],
            )

            mock_client = MagicMock()
            resp_mock = MagicMock()
            resp_mock.content = [MagicMock(text="Follow-up answer.", type="text")]
            resp_mock.stop_reason = "end_turn"
            mock_client.messages.create.return_value = resp_mock
            mock_anthropic.return_value = mock_client

            resp = client.post("/agent/chat", json={
                "message": "Tell me more",
                "session_id": "existing-session",
            })

        assert resp.status_code == 200
        data = resp.json()
        assert data["session_id"] == "existing-session"

    def test_invalid_session_creates_new(self, client):
        with patch("app.agent.routes.load_session", new_callable=AsyncMock) as mock_load, \
             patch("app.agent.routes.create_session", new_callable=AsyncMock) as mock_create, \
             patch("app.agent.routes.save_session", new_callable=AsyncMock), \
             patch("app.agent.routes.anthropic.Anthropic") as mock_anthropic:

            from app.agent.session import Session
            mock_load.return_value = None
            mock_create.return_value = Session(session_id="new-session")

            mock_client = MagicMock()
            resp_mock = MagicMock()
            resp_mock.content = [MagicMock(text="Answer.", type="text")]
            resp_mock.stop_reason = "end_turn"
            mock_client.messages.create.return_value = resp_mock
            mock_anthropic.return_value = mock_client

            resp = client.post("/agent/chat", json={
                "message": "Hello",
                "session_id": "nonexistent-session",
            })

        assert resp.status_code == 200
        assert resp.json()["session_id"] == "new-session"

    def test_missing_message_field(self, client):
        resp = client.post("/agent/chat", json={})
        assert resp.status_code == 422
