-- Migration 002: Observability, eval persistence, and query logging
-- Adds tables for OpenTelemetry trace storage, evaluation runs,
-- per-question eval results, and query logs across all interfaces.

-- Evaluation runs — one row per eval/runner.py invocation
CREATE TABLE IF NOT EXISTS eval_runs (
    run_id       TEXT PRIMARY KEY,
    started_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at  TIMESTAMPTZ,
    config       JSONB NOT NULL DEFAULT '{}'::jsonb,
    metrics      JSONB NOT NULL DEFAULT '{}'::jsonb,
    pair_count   INTEGER NOT NULL DEFAULT 0,
    status       TEXT NOT NULL DEFAULT 'running'
);

-- Per-question eval results — FK back to eval_runs
CREATE TABLE IF NOT EXISTS eval_results (
    id               SERIAL PRIMARY KEY,
    run_id           TEXT NOT NULL REFERENCES eval_runs(run_id) ON DELETE CASCADE,
    pair_id          TEXT NOT NULL,
    tier             TEXT NOT NULL DEFAULT 'unknown',
    question         TEXT NOT NULL,
    expected_answer  TEXT NOT NULL DEFAULT '',
    actual_answer    TEXT NOT NULL DEFAULT '',
    contexts         JSONB NOT NULL DEFAULT '[]'::jsonb,
    metrics          JSONB NOT NULL DEFAULT '{}'::jsonb,
    trace_id         TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_eval_results_run_id ON eval_results(run_id);
CREATE INDEX IF NOT EXISTS idx_eval_results_trace_id ON eval_results(trace_id);
CREATE INDEX IF NOT EXISTS idx_eval_results_tier ON eval_results(tier);

-- Query logs — every query from every interface
CREATE TABLE IF NOT EXISTS query_logs (
    id               SERIAL PRIMARY KEY,
    question         TEXT NOT NULL,
    answer           TEXT NOT NULL DEFAULT '',
    citations        JSONB NOT NULL DEFAULT '[]'::jsonb,
    confidence       TEXT,
    retrieval_method TEXT,
    pipeline_steps   JSONB NOT NULL DEFAULT '[]'::jsonb,
    chunks_used      INTEGER,
    parent_chunks_used INTEGER,
    latency_ms       FLOAT,
    retrieval_ms     FLOAT,
    generation_ms    FLOAT,
    trace_id         TEXT,
    session_id       TEXT,
    interface        TEXT NOT NULL DEFAULT 'api',
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_query_logs_trace_id ON query_logs(trace_id);
CREATE INDEX IF NOT EXISTS idx_query_logs_session_id ON query_logs(session_id);
CREATE INDEX IF NOT EXISTS idx_query_logs_created_at ON query_logs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_query_logs_interface ON query_logs(interface);

-- Traces — in-DB storage of OpenTelemetry span trees
CREATE TABLE IF NOT EXISTS traces (
    trace_id     TEXT PRIMARY KEY,
    spans        JSONB NOT NULL DEFAULT '[]'::jsonb,
    root_name    TEXT,
    span_count   INTEGER NOT NULL DEFAULT 0,
    duration_ms  FLOAT,
    status       TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_traces_created_at ON traces(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_traces_root_name ON traces(root_name);
