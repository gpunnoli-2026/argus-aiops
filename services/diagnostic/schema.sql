-- Runbook index. Applied by the ingest job on every run; every statement is
-- safe to repeat.
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS runbooks (
  id            text PRIMARY KEY,
  title         text NOT NULL,
  alerts        text[] NOT NULL,
  services      text[] NOT NULL,
  severity      text NOT NULL,
  owner         text NOT NULL,
  last_reviewed date NOT NULL,
  source_path   text NOT NULL
);

CREATE TABLE IF NOT EXISTS runbook_chunks (
  id           text PRIMARY KEY,                 -- <runbook_id>#<section>[-n]
  runbook_id   text NOT NULL REFERENCES runbooks(id) ON DELETE CASCADE,
  section      text NOT NULL,
  ordinal      int  NOT NULL,
  content      text NOT NULL,                    -- the section as written
  text         text NOT NULL,                    -- what was embedded: title, section, content
  content_hash text NOT NULL,
  embedding    vector(384) NOT NULL,
  tsv          tsvector GENERATED ALWAYS AS (to_tsvector('english', text)) STORED
);

CREATE INDEX IF NOT EXISTS runbook_chunks_tsv ON runbook_chunks USING gin (tsv);
CREATE INDEX IF NOT EXISTS runbooks_alerts ON runbooks USING gin (alerts);

-- No vector index: at this size an exact scan is faster than HNSW and exact.

-- embedding_model, embedding_dim, corpus_git_sha, indexed_at
CREATE TABLE IF NOT EXISTS index_meta (
  key   text PRIMARY KEY,
  value text NOT NULL
);
