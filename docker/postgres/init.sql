-- Runs once on first Postgres startup (empty data dir).

-- pgvector for embedding storage (sql + vector).
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ---- Auth: application users for Chainlit password login ----
CREATE TABLE IF NOT EXISTS app_users (
    id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    username      TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,           -- bcrypt/pbkdf2 hash, never plaintext
    role          TEXT NOT NULL DEFAULT 'user',
    metadata      JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---- Vector store: cached CSDA STAC items / documents ----
-- Dimension must match EMBED_DIM (nomic-embed-text = 768).
CREATE TABLE IF NOT EXISTS documents (
    id         UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    source     TEXT NOT NULL,              -- e.g. 'stac-item', 'collection', 'note'
    external_id TEXT,                      -- STAC item/collection id
    content    TEXT NOT NULL,
    metadata   JSONB NOT NULL DEFAULT '{}'::jsonb,
    embedding  VECTOR(768),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Approximate nearest-neighbour index (cosine).
CREATE INDEX IF NOT EXISTS documents_embedding_idx
    ON documents USING hnsw (embedding vector_cosine_ops);

CREATE INDEX IF NOT EXISTS documents_source_idx ON documents (source);

-- ---- Search history / audit ----
CREATE TABLE IF NOT EXISTS search_history (
    id         UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id    UUID REFERENCES app_users(id) ON DELETE SET NULL,
    query      TEXT NOT NULL,
    result     JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
