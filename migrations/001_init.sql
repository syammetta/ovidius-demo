CREATE EXTENSION IF NOT EXISTS vector;

-- Parent chunks: large context windows used for generation
CREATE TABLE IF NOT EXISTS parent_chunks (
    parent_id    TEXT PRIMARY KEY,
    content      TEXT NOT NULL,
    source_url   TEXT NOT NULL,
    source_title TEXT NOT NULL,
    section      TEXT NOT NULL DEFAULT '',
    document_type TEXT NOT NULL DEFAULT 'narrative',
    token_count  INTEGER NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Child chunks: small, precise chunks used for retrieval
CREATE TABLE IF NOT EXISTS documents (
    chunk_id             TEXT PRIMARY KEY,
    parent_id            TEXT REFERENCES parent_chunks(parent_id),
    content              TEXT NOT NULL,
    contextual_content   TEXT,
    source_url           TEXT NOT NULL,
    source_title         TEXT NOT NULL,
    section              TEXT NOT NULL DEFAULT '',
    document_type        TEXT NOT NULL DEFAULT 'narrative',
    content_hash         TEXT NOT NULL,
    token_count          INTEGER NOT NULL,
    embedding            vector(1024),
    tsv                  tsvector,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Vector similarity index (IVFFlat for demo scale; HNSW for production)
CREATE INDEX IF NOT EXISTS idx_documents_embedding
    ON documents USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);

-- Full-text search index for BM25-style keyword retrieval
CREATE INDEX IF NOT EXISTS idx_documents_tsv
    ON documents USING gin(tsv);

CREATE INDEX IF NOT EXISTS idx_documents_content_hash
    ON documents (content_hash);

CREATE INDEX IF NOT EXISTS idx_documents_parent_id
    ON documents (parent_id);

-- Auto-populate tsvector from contextual_content (preferred) or raw content
CREATE OR REPLACE FUNCTION update_tsv() RETURNS trigger AS $$
BEGIN
    NEW.tsv := to_tsvector('english', COALESCE(NEW.contextual_content, NEW.content));
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_documents_tsv ON documents;
CREATE TRIGGER trg_documents_tsv
    BEFORE INSERT OR UPDATE ON documents
    FOR EACH ROW EXECUTE FUNCTION update_tsv();

-- Vector similarity search function
CREATE OR REPLACE FUNCTION match_documents(
    query_embedding vector(1024),
    match_count INTEGER DEFAULT 20
)
RETURNS TABLE (
    chunk_id TEXT,
    parent_id TEXT,
    content TEXT,
    contextual_content TEXT,
    source_url TEXT,
    source_title TEXT,
    section TEXT,
    document_type TEXT,
    similarity FLOAT
)
LANGUAGE sql STABLE
AS $$
    SELECT
        d.chunk_id,
        d.parent_id,
        d.content,
        d.contextual_content,
        d.source_url,
        d.source_title,
        d.section,
        d.document_type,
        1 - (d.embedding <=> query_embedding) AS similarity
    FROM documents d
    ORDER BY d.embedding <=> query_embedding
    LIMIT match_count;
$$;

-- BM25-style full-text search function
CREATE OR REPLACE FUNCTION search_documents_bm25(
    query_text TEXT,
    match_count INTEGER DEFAULT 20
)
RETURNS TABLE (
    chunk_id TEXT,
    parent_id TEXT,
    content TEXT,
    contextual_content TEXT,
    source_url TEXT,
    source_title TEXT,
    section TEXT,
    document_type TEXT,
    rank FLOAT
)
LANGUAGE sql STABLE
AS $$
    SELECT
        d.chunk_id,
        d.parent_id,
        d.content,
        d.contextual_content,
        d.source_url,
        d.source_title,
        d.section,
        d.document_type,
        ts_rank_cd(d.tsv, websearch_to_tsquery('english', query_text)) AS rank
    FROM documents d
    WHERE d.tsv @@ websearch_to_tsquery('english', query_text)
    ORDER BY rank DESC
    LIMIT match_count;
$$;

-- Sessions table for multi-turn agent
CREATE TABLE IF NOT EXISTS sessions (
    session_id  TEXT PRIMARY KEY,
    messages    JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
