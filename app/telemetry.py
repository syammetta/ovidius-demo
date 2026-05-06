"""OpenTelemetry observability — traces, spans, and metrics.

Provides both an in-memory span collector (for the dashboard, no external
infra required) and an optional OTLP exporter (for Jaeger, Grafana Tempo, etc.).

Usage:
    collector = setup_telemetry()       # called once at startup
    tracer = get_tracer("my_module")    # per-module tracer

    with tracer.start_as_current_span("my_operation") as span:
        span.set_attribute("key", "value")
        ...

For async functions, use the @traced decorator:
    @traced("operation_name")
    async def my_function(...):
        ...
"""

import functools
import json
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Sequence

from opentelemetry import trace, metrics
from opentelemetry.sdk.trace import TracerProvider, ReadableSpan
from opentelemetry.sdk.trace.export import (
    SpanExporter,
    SpanExportResult,
    BatchSpanProcessor,
    SimpleSpanProcessor,
)
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.trace import StatusCode, format_trace_id, format_span_id
from opentelemetry.trace.propagation import set_span_in_context

from app.config import settings


# ---------------------------------------------------------------------------
# In-memory span collector
# ---------------------------------------------------------------------------

class InMemorySpanCollector(SpanExporter):
    """Collects completed spans in a ring buffer for dashboard queries.

    Thread-safe. Supports querying recent traces and individual trace lookups.
    Spans are grouped by trace_id. When the buffer is full, the oldest traces
    are evicted.
    """

    def __init__(self, max_traces: int = 500):
        self._traces: dict[str, list[dict]] = {}
        self._trace_order: deque[str] = deque()
        self._max_traces = max_traces
        self._lock = threading.Lock()

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        with self._lock:
            for span in spans:
                trace_id = format_trace_id(span.context.trace_id)
                span_data = self._serialize_span(span)

                if trace_id not in self._traces:
                    self._traces[trace_id] = []
                    self._trace_order.append(trace_id)

                self._traces[trace_id].append(span_data)

                while len(self._trace_order) > self._max_traces:
                    old = self._trace_order.popleft()
                    self._traces.pop(old, None)

        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        pass

    def force_flush(self, timeout_millis: int = 0) -> bool:
        return True

    def _serialize_span(self, span: ReadableSpan) -> dict:
        parent_id = None
        if span.parent and span.parent.span_id:
            parent_id = format_span_id(span.parent.span_id)

        events = []
        for event in span.events:
            events.append({
                "name": event.name,
                "timestamp_ns": event.timestamp,
                "attributes": dict(event.attributes) if event.attributes else {},
            })

        return {
            "span_id": format_span_id(span.context.span_id),
            "parent_span_id": parent_id,
            "name": span.name,
            "start_ns": span.start_time,
            "end_ns": span.end_time,
            "duration_ms": round((span.end_time - span.start_time) / 1e6, 2),
            "attributes": _safe_attributes(span.attributes),
            "status": span.status.status_code.name,
            "status_description": span.status.description,
            "events": events,
        }

    def get_recent_traces(self, limit: int = 50) -> list[dict]:
        with self._lock:
            trace_ids = list(self._trace_order)[-limit:]
            trace_ids.reverse()
            result = []
            for tid in trace_ids:
                spans = self._traces.get(tid, [])
                root = _find_root_span(spans)
                result.append({
                    "trace_id": tid,
                    "root_name": root["name"] if root else None,
                    "span_count": len(spans),
                    "duration_ms": root["duration_ms"] if root else None,
                    "status": root["status"] if root else None,
                    "started_at_ns": root["start_ns"] if root else None,
                    "spans": spans,
                })
            return result

    def get_trace(self, trace_id: str) -> dict | None:
        with self._lock:
            spans = self._traces.get(trace_id)
            if spans is None:
                return None
            root = _find_root_span(spans)
            return {
                "trace_id": trace_id,
                "root_name": root["name"] if root else None,
                "span_count": len(spans),
                "duration_ms": root["duration_ms"] if root else None,
                "status": root["status"] if root else None,
                "spans": spans,
            }

    def clear(self):
        with self._lock:
            self._traces.clear()
            self._trace_order.clear()


def _find_root_span(spans: list[dict]) -> dict | None:
    for span in spans:
        if span["parent_span_id"] is None:
            return span
    return spans[0] if spans else None


def _safe_attributes(attrs) -> dict:
    if not attrs:
        return {}
    result = {}
    for k, v in attrs.items():
        if isinstance(v, (str, int, float, bool)):
            result[k] = v
        else:
            result[k] = str(v)
    return result


# ---------------------------------------------------------------------------
# Metrics collector
# ---------------------------------------------------------------------------

_metric_reader: InMemoryMetricReader | None = None

# Pre-defined metric instruments (initialized in setup_telemetry)
_request_latency = None
_confidence_counter = None
_chunks_retrieved = None
_tool_calls_histogram = None
_retrieval_latency = None
_generation_latency = None
_rerank_latency = None


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

_collector: InMemorySpanCollector | None = None
_initialized = False


def setup_telemetry() -> InMemorySpanCollector:
    """Initialize OpenTelemetry tracing and metrics. Idempotent."""
    global _collector, _initialized, _metric_reader
    global _request_latency, _confidence_counter, _chunks_retrieved
    global _tool_calls_histogram, _retrieval_latency, _generation_latency
    global _rerank_latency

    if _initialized and _collector:
        return _collector

    resource = Resource.create({
        "service.name": settings.otel_service_name,
        "service.version": "0.1.0",
        "deployment.environment": "development",
    })

    # --- Tracing ---
    provider = TracerProvider(resource=resource)

    _collector = InMemorySpanCollector(max_traces=settings.traces_max_in_memory)
    provider.add_span_processor(SimpleSpanProcessor(_collector))

    if settings.otel_exporter_endpoint:
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        otlp_exporter = OTLPSpanExporter(endpoint=settings.otel_exporter_endpoint)
        provider.add_span_processor(BatchSpanProcessor(otlp_exporter))

    trace.set_tracer_provider(provider)

    # --- Metrics ---
    _metric_reader = InMemoryMetricReader()
    meter_provider = MeterProvider(resource=resource, metric_readers=[_metric_reader])
    metrics.set_meter_provider(meter_provider)

    meter = metrics.get_meter("ovidius.doc_qa", "0.1.0")

    _request_latency = meter.create_histogram(
        "request.latency_ms",
        description="End-to-end request latency in milliseconds",
        unit="ms",
    )
    _confidence_counter = meter.create_counter(
        "retrieval.confidence_total",
        description="Count of retrieval results by confidence level",
    )
    _chunks_retrieved = meter.create_histogram(
        "retrieval.chunks_count",
        description="Number of chunks returned per retrieval",
    )
    _tool_calls_histogram = meter.create_histogram(
        "agent.tool_calls_per_turn",
        description="Number of tool calls per agent turn",
    )
    _retrieval_latency = meter.create_histogram(
        "retrieval.latency_ms",
        description="Retrieval pipeline latency in milliseconds",
        unit="ms",
    )
    _generation_latency = meter.create_histogram(
        "generation.latency_ms",
        description="Answer generation latency in milliseconds",
        unit="ms",
    )
    _rerank_latency = meter.create_histogram(
        "rerank.latency_ms",
        description="Reranking latency in milliseconds",
        unit="ms",
    )

    _initialized = True
    return _collector


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_tracer(name: str) -> trace.Tracer:
    return trace.get_tracer(name, "0.1.0")


def get_current_trace_id() -> str | None:
    span = trace.get_current_span()
    ctx = span.get_span_context()
    if ctx and ctx.trace_id:
        return format_trace_id(ctx.trace_id)
    return None


def get_current_span_id() -> str | None:
    span = trace.get_current_span()
    ctx = span.get_span_context()
    if ctx and ctx.span_id:
        return format_span_id(ctx.span_id)
    return None


def get_collector() -> InMemorySpanCollector | None:
    return _collector


# ---------------------------------------------------------------------------
# Metric recording helpers
# ---------------------------------------------------------------------------

def record_request_latency(ms: float, interface: str = "api"):
    if _request_latency:
        _request_latency.record(ms, {"interface": interface})


def record_confidence(level: str):
    if _confidence_counter:
        _confidence_counter.add(1, {"confidence": level})


def record_chunks_retrieved(count: int, method: str = "hybrid"):
    if _chunks_retrieved:
        _chunks_retrieved.record(count, {"method": method})


def record_tool_calls(count: int):
    if _tool_calls_histogram:
        _tool_calls_histogram.record(count)


def record_retrieval_latency(ms: float):
    if _retrieval_latency:
        _retrieval_latency.record(ms)


def record_generation_latency(ms: float):
    if _generation_latency:
        _generation_latency.record(ms)


def record_rerank_latency(ms: float):
    if _rerank_latency:
        _rerank_latency.record(ms)


def get_metrics_snapshot() -> dict:
    """Collect current metric data for the /metrics endpoint."""
    if not _metric_reader:
        return {"metrics": []}

    data = _metric_reader.get_metrics_data()
    result = []
    for resource_metric in data.resource_metrics:
        for scope_metric in resource_metric.scope_metrics:
            for metric in scope_metric.metrics:
                metric_data = {
                    "name": metric.name,
                    "description": metric.description,
                    "unit": metric.unit,
                    "data_points": [],
                }
                if hasattr(metric.data, "data_points"):
                    for dp in metric.data.data_points:
                        point = {"attributes": dict(dp.attributes) if dp.attributes else {}}
                        if hasattr(dp, "sum"):
                            point["value"] = dp.sum
                        elif hasattr(dp, "bucket_counts"):
                            point["count"] = dp.count
                            point["sum"] = dp.sum
                            point["min"] = dp.min
                            point["max"] = dp.max
                            point["bucket_counts"] = list(dp.bucket_counts)
                        metric_data["data_points"].append(point)
                result.append(metric_data)
    return {"metrics": result}


# ---------------------------------------------------------------------------
# Decorators
# ---------------------------------------------------------------------------

def traced(span_name: str, extract_attrs: dict[str, str] | None = None):
    """Decorator: wrap an async function in an OpenTelemetry span.

    extract_attrs maps span attribute names to kwarg names. For example:
        @traced("embed_query", extract_attrs={"query": "query"})
        async def embed(query: str): ...
    sets span attribute "query" from the kwarg "query".
    """
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            tracer = get_tracer(func.__module__)
            with tracer.start_as_current_span(span_name) as span:
                if extract_attrs:
                    for attr_name, kwarg_name in extract_attrs.items():
                        val = kwargs.get(kwarg_name)
                        if val is not None:
                            span.set_attribute(attr_name, str(val)[:500])
                try:
                    result = await func(*args, **kwargs)
                    return result
                except Exception as e:
                    span.set_status(StatusCode.ERROR, str(e))
                    span.record_exception(e)
                    raise
        return wrapper
    return decorator


def traced_sync(span_name: str):
    """Decorator: wrap a synchronous function in an OpenTelemetry span."""
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            tracer = get_tracer(func.__module__)
            with tracer.start_as_current_span(span_name) as span:
                try:
                    result = func(*args, **kwargs)
                    return result
                except Exception as e:
                    span.set_status(StatusCode.ERROR, str(e))
                    span.record_exception(e)
                    raise
        return wrapper
    return decorator


# ---------------------------------------------------------------------------
# DB flush — persist traces to Postgres
# ---------------------------------------------------------------------------

async def flush_traces_to_db(pool) -> int:
    """Persist in-memory traces to the traces table. Returns count flushed."""
    if not _collector:
        return 0

    traces = _collector.get_recent_traces(limit=100)
    if not traces:
        return 0

    count = 0
    async with pool.acquire() as conn:
        for t in traces:
            await conn.execute(
                """INSERT INTO traces (trace_id, spans, root_name, span_count, duration_ms, status)
                   VALUES ($1, $2, $3, $4, $5, $6)
                   ON CONFLICT (trace_id) DO UPDATE SET
                       spans = EXCLUDED.spans,
                       span_count = EXCLUDED.span_count,
                       duration_ms = EXCLUDED.duration_ms""",
                t["trace_id"],
                json.dumps(t["spans"]),
                t.get("root_name"),
                t["span_count"],
                t.get("duration_ms"),
                t.get("status"),
            )
            count += 1
    return count
