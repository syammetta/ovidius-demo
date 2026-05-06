-- Durable ingestion queue and logs.

CREATE TABLE IF NOT EXISTS ingestion_jobs (
    job_id TEXT PRIMARY KEY,
    job_type TEXT NOT NULL,
    source TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL DEFAULT 'queued',
    progress JSONB NOT NULL DEFAULT '{}'::jsonb,
    error TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    claimed_by TEXT,
    claimed_at TIMESTAMPTZ,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_ingestion_jobs_status_created
    ON ingestion_jobs (status, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_ingestion_jobs_created
    ON ingestion_jobs (created_at DESC);

CREATE TABLE IF NOT EXISTS ingestion_job_logs (
    id BIGSERIAL PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES ingestion_jobs(job_id) ON DELETE CASCADE,
    log TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_ingestion_job_logs_job_id_created
    ON ingestion_job_logs (job_id, created_at);
