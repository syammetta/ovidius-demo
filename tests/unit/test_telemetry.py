"""Tests for OpenTelemetry observability module."""

import pytest
from unittest.mock import MagicMock, patch

from app.telemetry import (
    InMemorySpanCollector,
    setup_telemetry,
    get_tracer,
    get_current_trace_id,
    traced,
    traced_sync,
    get_metrics_snapshot,
    record_request_latency,
    record_confidence,
    record_chunks_retrieved,
    record_tool_calls,
    _find_root_span,
    _safe_attributes,
)


class TestInMemorySpanCollector:
    def _make_mock_span(self, trace_id=1, span_id=1, parent_span_id=None, name="test_span"):
        span = MagicMock()
        span.context.trace_id = trace_id
        span.context.span_id = span_id
        span.name = name
        span.start_time = 1000000000
        span.end_time = 1050000000
        span.attributes = {"key": "value"}
        span.status.status_code.name = "OK"
        span.status.description = None
        span.events = []

        if parent_span_id:
            span.parent = MagicMock()
            span.parent.span_id = parent_span_id
        else:
            span.parent = None

        return span

    def test_export_stores_spans(self):
        collector = InMemorySpanCollector(max_traces=10)
        span = self._make_mock_span(trace_id=0x1234, span_id=0xABCD)
        result = collector.export([span])
        assert result.name == "SUCCESS"
        traces = collector.get_recent_traces(limit=10)
        assert len(traces) == 1
        assert traces[0]["span_count"] == 1

    def test_get_trace_returns_matching_trace(self):
        collector = InMemorySpanCollector()
        span = self._make_mock_span(trace_id=0x5678)
        collector.export([span])
        trace_id = format(0x5678, "032x")
        trace = collector.get_trace(trace_id)
        assert trace is not None
        assert trace["trace_id"] == trace_id
        assert len(trace["spans"]) == 1

    def test_get_trace_returns_none_for_missing(self):
        collector = InMemorySpanCollector()
        assert collector.get_trace("nonexistent") is None

    def test_ring_buffer_eviction(self):
        collector = InMemorySpanCollector(max_traces=3)
        for i in range(5):
            span = self._make_mock_span(trace_id=i + 1)
            collector.export([span])
        traces = collector.get_recent_traces(limit=10)
        assert len(traces) == 3

    def test_multiple_spans_per_trace(self):
        collector = InMemorySpanCollector()
        span1 = self._make_mock_span(trace_id=0x100, span_id=1, name="parent")
        span2 = self._make_mock_span(trace_id=0x100, span_id=2, parent_span_id=1, name="child")
        collector.export([span1, span2])
        traces = collector.get_recent_traces()
        assert len(traces) == 1
        assert traces[0]["span_count"] == 2

    def test_recent_traces_ordered_newest_first(self):
        collector = InMemorySpanCollector()
        for i in range(3):
            span = self._make_mock_span(trace_id=i + 1, name=f"trace_{i}")
            collector.export([span])
        traces = collector.get_recent_traces(limit=10)
        assert traces[0]["spans"][0]["name"] == "trace_2"
        assert traces[-1]["spans"][0]["name"] == "trace_0"

    def test_clear_removes_all_traces(self):
        collector = InMemorySpanCollector()
        span = self._make_mock_span()
        collector.export([span])
        collector.clear()
        assert collector.get_recent_traces() == []

    def test_limit_respected(self):
        collector = InMemorySpanCollector()
        for i in range(10):
            collector.export([self._make_mock_span(trace_id=i + 1)])
        traces = collector.get_recent_traces(limit=3)
        assert len(traces) == 3


class TestHelperFunctions:
    def test_find_root_span_no_parent(self):
        spans = [
            {"parent_span_id": None, "name": "root"},
            {"parent_span_id": "abc", "name": "child"},
        ]
        root = _find_root_span(spans)
        assert root["name"] == "root"

    def test_find_root_span_falls_back_to_first(self):
        spans = [
            {"parent_span_id": "abc", "name": "child1"},
            {"parent_span_id": "def", "name": "child2"},
        ]
        root = _find_root_span(spans)
        assert root["name"] == "child1"

    def test_find_root_span_empty(self):
        assert _find_root_span([]) is None

    def test_safe_attributes_primitives(self):
        result = _safe_attributes({"str": "val", "int": 42, "float": 3.14, "bool": True})
        assert result == {"str": "val", "int": 42, "float": 3.14, "bool": True}

    def test_safe_attributes_converts_complex(self):
        result = _safe_attributes({"list": [1, 2, 3]})
        assert result["list"] == "[1, 2, 3]"

    def test_safe_attributes_none(self):
        assert _safe_attributes(None) == {}


class TestSetupAndTracers:
    def test_setup_telemetry_returns_collector(self):
        collector = setup_telemetry()
        assert isinstance(collector, InMemorySpanCollector)

    def test_setup_telemetry_idempotent(self):
        c1 = setup_telemetry()
        c2 = setup_telemetry()
        assert c1 is c2

    def test_get_tracer_returns_tracer(self):
        setup_telemetry()
        tracer = get_tracer("test_module")
        assert tracer is not None

    def test_get_current_trace_id_outside_span(self):
        setup_telemetry()
        trace_id = get_current_trace_id()
        assert trace_id is None or trace_id == "00000000000000000000000000000000"


class TestTracedDecorator:
    @pytest.mark.asyncio
    async def test_traced_calls_function(self):
        setup_telemetry()

        @traced("test_operation")
        async def my_func(x):
            return x + 1

        result = await my_func(41)
        assert result == 42

    @pytest.mark.asyncio
    async def test_traced_propagates_exception(self):
        setup_telemetry()

        @traced("failing_op")
        async def failing():
            raise ValueError("test error")

        with pytest.raises(ValueError, match="test error"):
            await failing()

    def test_traced_sync_calls_function(self):
        setup_telemetry()

        @traced_sync("sync_op")
        def my_sync(x):
            return x * 2

        assert my_sync(21) == 42


class TestMetrics:
    def test_metrics_snapshot_has_structure(self):
        setup_telemetry()
        snapshot = get_metrics_snapshot()
        assert "metrics" in snapshot
        assert isinstance(snapshot["metrics"], list)

    def test_record_functions_dont_crash(self):
        setup_telemetry()
        record_request_latency(100.0, interface="test")
        record_confidence("confident")
        record_chunks_retrieved(5, method="hybrid")
        record_tool_calls(2)
