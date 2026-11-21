-- Migration 001 — RLS + hybrid search (production DDL, from Ultra's review,
-- adapted to this repo's tables). Apply with psql as a DB owner.
-- The app's SQLAlchemy models in app/db.py create the same tables for the
-- local demo; this file is the auditable production version with RLS,
-- triggers, and indexes. Run it once against a fresh database, or
-- `docker compose down -v` and let the app recreate tables, then apply
-- sections 3-4 manually.
--
-- Verified claims used here:
--  * pgvector 0.8.0+ iterative scans: SET hnsw.iterative_scan='strict_order'
--    (pgvector README / CHANGELOG 2024-10-30) — confirmed via live search.
--  * OpenAI text-embedding-3-small accepts dimensions=384 (Matryoshka).
--
-- UNVERIFIED — do not cite as fact: Ultra's Vec2Text numbers
-- (arXiv:2611.05716, "47.4% exact recovery") could not be verified live.
-- The mitigation (never serialize embeddings, encrypt backups) stands
-- on its own; the percentages do not.

-- 1. Extensions & least-privilege application role
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_rag_user') THEN
    CREATE ROLE app_rag_user LOGIN PASSWORD 'replace_via_secret_manager' NOBYPASSRLS;
  END IF;
END $$;

-- 2. Canonical documents + ACL-synced chunks + decoupled embeddings
-- (App models use the same shape; UUIDs stop sequential-ID corpus leaks.)
CREATE TABLE IF NOT EXISTS documents (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  display_title TEXT NOT NULL,
  storage_key TEXT NOT NULL, -- never expose raw S3/disk path to non-admin DTOs
  allowed_roles TEXT[] NOT NULL CHECK (
    cardinality(allowed_roles) > 0
    AND allowed_roles <@ ARRAY['sales', 'hr', 'admin']::TEXT[]
  ),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS chunks (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  chunk_index INT NOT NULL,
  section_path TEXT NOT NULL DEFAULT '',
  content TEXT NOT NULL,
  allowed_roles TEXT[] NOT NULL,
  fts TSVECTOR GENERATED ALWAYS AS (
    to_tsvector('english', coalesce(section_path, '') || ' ' || content)
  ) STORED,
  UNIQUE (document_id, chunk_index)
);
CREATE TABLE IF NOT EXISTS chunk_embeddings (
  chunk_id UUID NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
  model_id TEXT NOT NULL,
  allowed_roles TEXT[] NOT NULL,
  embedding VECTOR(384) NOT NULL,
  PRIMARY KEY (chunk_id, model_id)
);

-- Keep denormalized allowed_roles in sync when a document ACL changes
CREATE OR REPLACE FUNCTION sync_document_roles() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
  UPDATE chunks SET allowed_roles = NEW.allowed_roles WHERE document_id = NEW.id;
  UPDATE chunk_embeddings ce SET allowed_roles = NEW.allowed_roles
    FROM chunks c WHERE ce.chunk_id = c.id AND c.document_id = NEW.id;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS trg_sync_doc_roles ON documents;
CREATE TRIGGER trg_sync_doc_roles
  AFTER UPDATE OF allowed_roles ON documents
  FOR EACH ROW WHEN (OLD.allowed_roles IS DISTINCT FROM NEW.allowed_roles)
  EXECUTE FUNCTION sync_document_roles();

-- 3. Indexes: GIN on ACLs + FTS, partial HNSW per active model_id
CREATE INDEX IF NOT EXISTS idx_documents_roles_gin ON documents USING GIN (allowed_roles);
CREATE INDEX IF NOT EXISTS idx_chunks_roles_gin ON chunks USING GIN (allowed_roles);
CREATE INDEX IF NOT EXISTS idx_chunks_fts_gin ON chunks USING GIN (fts);
CREATE INDEX IF NOT EXISTS idx_ce_roles_gin ON chunk_embeddings USING GIN (allowed_roles);
-- Build with CONCURRENTLY in migrations; m=16, ef_construction=64 for 200k-1M vectors
CREATE INDEX IF NOT EXISTS idx_ce_hnsw_openai_384 ON chunk_embeddings
  USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64)
  WHERE model_id = 'openai:text-embedding-3-small:384';

-- 4. Fail-closed Row-Level Security on ALL three tables
ALTER TABLE documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE documents FORCE ROW LEVEL SECURITY;
ALTER TABLE chunks ENABLE ROW LEVEL SECURITY;
ALTER TABLE chunks FORCE ROW LEVEL SECURITY;
ALTER TABLE chunk_embeddings ENABLE ROW LEVEL SECURITY;
ALTER TABLE chunk_embeddings FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS rls_documents_select ON documents;
CREATE POLICY rls_documents_select ON documents FOR SELECT TO app_rag_user
  USING (allowed_roles && string_to_array(coalesce(current_setting('app.user_roles', true), ''), ','));
DROP POLICY IF EXISTS rls_chunks_select ON chunks;
CREATE POLICY rls_chunks_select ON chunks FOR SELECT TO app_rag_user
  USING (allowed_roles && string_to_array(coalesce(current_setting('app.user_roles', true), ''), ','));
DROP POLICY IF EXISTS rls_ce_select ON chunk_embeddings;
CREATE POLICY rls_ce_select ON chunk_embeddings FOR SELECT TO app_rag_user
  USING (allowed_roles && string_to_array(coalesce(current_setting('app.user_roles', true), ''), ','));

GRANT SELECT ON documents, chunks, chunk_embeddings TO app_rag_user;

-- 5. How a query binds roles (transaction-local; see app/db.py set_rls_context):
--   BEGIN;
--   SELECT set_config('app.user_roles', 'sales', true);
--   SET LOCAL hnsw.iterative_scan = 'strict_order';
--   SET LOCAL hnsw.ef_search = 64;
--   SET LOCAL hnsw.max_scan_tuples = 20000;
--   -- ... hybrid RRF query from app/db.py hybrid_search() ...
--   COMMIT;
-- Note: SELECT set_config(..., true) is used instead of SET LOCAL with a
-- bind parameter because PostgreSQL's SET utility command rejects $1 binds
-- in asyncpg / psycopg.
