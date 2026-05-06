-- Track latest canonical content hash per source URL for reliable dedup.

CREATE TABLE IF NOT EXISTS source_ingest_state (
    source_url TEXT PRIMARY KEY,
    source_hash TEXT NOT NULL,
    last_parent_count INTEGER NOT NULL DEFAULT 0,
    last_child_count INTEGER NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_source_ingest_state_updated_at
    ON source_ingest_state (updated_at DESC);
