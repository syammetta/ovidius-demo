"""Integration tests for observability endpoints — traces, metrics, query logs."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    with patch("app.api.routes.get_pool", new_callable=AsyncMock), \
         patch("app.api.routes.close_pool", new_callable=AsyncMock):
        from app.api.routes import app
        with TestClient(app) as c:
            yield c


class TestTracesEndpoint:
    def test_list_traces_returns_list(self, client):
        resp = client.get("/traces")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)

    def test_get_trace_not_found(self, client):
        resp = client.get("/traces/nonexistent-trace-id")
        assert resp.status_code == 404

    def test_list_traces_with_limit(self, client):
        resp = client.get("/traces?limit=5")
        assert resp.status_code == 200


class TestMetricsEndpoint:
    def test_metrics_returns_structure(self, client):
        resp = client.get("/metrics")
        assert resp.status_code == 200
        data = resp.json()
        assert "metrics" in data
        assert isinstance(data["metrics"], list)


class TestQueryLogsEndpoint:
    def test_query_logs_returns_list(self, client):
        with patch("app.api.routes.get_pool", new_callable=AsyncMock) as mock_pool:
            conn = AsyncMock()
            conn.fetch.return_value = []
            pool = MagicMock()
            pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
            pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_pool.return_value = pool

            resp = client.get("/query-logs")

        assert resp.status_code == 200

    def test_query_logs_filter_by_interface(self, client):
        with patch("app.api.routes.get_pool", new_callable=AsyncMock) as mock_pool:
            conn = AsyncMock()
            conn.fetch.return_value = []
            pool = MagicMock()
            pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
            pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_pool.return_value = pool

            resp = client.get("/query-logs?interface=agent")

        assert resp.status_code == 200


class TestQAWithTracing:
    def test_qa_returns_trace_id(self, client):
        from app.retrieval.context_builder import RetrievalResult
        from app.retrieval.corrective import CorrectedRetrieval, RetrievalConfidence
        from app.generation.answerer import AnswerResult, Citation
        from tests.conftest import make_chunk

        children = [make_chunk()]
        retrieval = RetrievalResult(
            children=children,
            parent_contents={"p_def456_0": "Full parent."},
            corrective=CorrectedRetrieval(
                chunks=children,
                confidence=RetrievalConfidence.CONFIDENT,
                filtered_count=1, original_count=1,
            ),
        )
        answer = AnswerResult(
            answer="The answer [1].",
            citations=[Citation(index=1, source_url="https://irs.gov", source_title="Pub", chunk_id="c0")],
            confidence="confident",
            retrieval_method="hybrid",
            chunks_used=1,
            parent_chunks_used=1,
        )

        with patch("app.api.routes.retrieve", new_callable=AsyncMock) as mock_ret, \
             patch("app.api.routes.generate_answer", new_callable=AsyncMock) as mock_gen, \
             patch("app.api.routes.log_query", new_callable=AsyncMock):
            mock_ret.return_value = retrieval
            mock_gen.return_value = answer

            resp = client.post("/qa", json={"question": "test?"})

        assert resp.status_code == 200
        data = resp.json()
        assert "trace_id" in data
