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

-- ===========================================================================
-- Chainlit data layer (SQLAlchemyDataLayer). Column names are camelCase and
-- MUST be quoted; the schema is dictated by Chainlit, do not rename.
-- Persists threads/steps/elements so users can browse and resume past chats.
-- ===========================================================================
CREATE TABLE IF NOT EXISTS users (
    "id"         UUID PRIMARY KEY,
    "identifier" TEXT NOT NULL UNIQUE,
    "metadata"   JSONB NOT NULL,
    "createdAt"  TEXT
);

CREATE TABLE IF NOT EXISTS threads (
    "id"             UUID PRIMARY KEY,
    "createdAt"      TEXT,
    "name"           TEXT,
    "userId"         UUID,
    "userIdentifier" TEXT,
    "tags"           TEXT[],
    "metadata"       JSONB,
    FOREIGN KEY ("userId") REFERENCES users("id") ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS steps (
    "id"            UUID PRIMARY KEY,
    "name"          TEXT NOT NULL,
    "type"          TEXT NOT NULL,
    "threadId"      UUID NOT NULL,
    "parentId"      UUID,
    "streaming"     BOOLEAN NOT NULL,
    "waitForAnswer" BOOLEAN,
    "isError"       BOOLEAN,
    "metadata"      JSONB,
    "tags"          TEXT[],
    "input"         TEXT,
    "output"        TEXT,
    "createdAt"     TEXT,
    "command"       TEXT,
    "start"         TEXT,
    "end"           TEXT,
    "generation"    JSONB,
    "showInput"     TEXT,
    "language"      TEXT,
    "indent"        INT,
    "defaultOpen"   BOOLEAN,
    "modes"         JSONB
);

CREATE TABLE IF NOT EXISTS elements (
    "id"          UUID PRIMARY KEY,
    "threadId"    UUID,
    "type"        TEXT,
    "url"         TEXT,
    "chainlitKey" TEXT,
    "name"        TEXT NOT NULL,
    "display"     TEXT,
    "objectKey"   TEXT,
    "size"        TEXT,
    "page"        INT,
    "language"    TEXT,
    "forId"       UUID,
    "mime"        TEXT,
    "props"       JSONB
);

CREATE TABLE IF NOT EXISTS feedbacks (
    "id"       UUID PRIMARY KEY,
    "forId"    UUID NOT NULL,
    "threadId" UUID NOT NULL,
    "value"    INT NOT NULL,
    "comment"  TEXT
);

CREATE INDEX IF NOT EXISTS steps_threadid_idx ON steps ("threadId");
CREATE INDEX IF NOT EXISTS elements_threadid_idx ON elements ("threadId");
CREATE INDEX IF NOT EXISTS threads_userid_idx ON threads ("userId");

-- ---- Agent conversation state ----
-- Full Pydantic AI message history (incl. tool calls) per Chainlit thread, so a
-- resumed chat continues with complete agent context, not just visible text.
CREATE TABLE IF NOT EXISTS conversation_state (
    thread_id  UUID PRIMARY KEY,
    history    JSONB NOT NULL DEFAULT '[]'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
