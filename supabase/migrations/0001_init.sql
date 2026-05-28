-- ============================================================================
-- 0001_init.sql — Knowra cloud schema initialization
--
-- Idempotent (uses CREATE TABLE IF NOT EXISTS, CREATE INDEX IF NOT EXISTS, etc.)
-- so this migration can be re-applied against any environment safely.
--
-- Tables in this migration:
--   - user_profiles      Per-user metadata extending auth.users
--   - sync_state         Sync watermark per (user, desktop device)
--   - papers             Per-user paper rows (no extracted_text, no chat_history)
--   - knowledge_nodes    Per-user graph nodes
--   - knowledge_edges    Per-user graph edges
--   - wiki_files         Per-user .md file metadata (content in Storage)
--   - cloud_llm_calls    Per-user mobile-Ask telemetry (no key, no content)
--
-- Conventions established here (followed by all later migrations):
--   1. PRIMARY KEY is uuid (gen_random_uuid()).
--   2. user_id is the second column, NOT NULL, REFERENCES auth.users(id) CASCADE.
--   3. updated_at TIMESTAMPTZ with NOW() default, maintained by trigger.
--   4. Every table gets ENABLE ROW LEVEL SECURITY + tenant_isolation policy
--      that filters by auth.uid().
--   5. legacy_id INTEGER column on tables that originated in the desktop SQLite
--      so we can backtrack to the pre-migration int id during the first 6
--      months.
-- ============================================================================

-- pgcrypto for gen_random_uuid()
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ----------------------------------------------------------------------------
-- Generic helper: maintain updated_at on UPDATE
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION knowra_touch_updated_at()
  RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at := NOW();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- ============================================================================
-- user_profiles
-- ============================================================================
CREATE TABLE IF NOT EXISTS user_profiles (
  user_id              UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
  display_name         TEXT,
  desktop_first_seen   TIMESTAMPTZ,
  last_desktop_sync_at TIMESTAMPTZ,
  last_mobile_open_at  TIMESTAMPTZ,
  settings             JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TRIGGER user_profiles_touch_updated_at
  BEFORE UPDATE ON user_profiles
  FOR EACH ROW EXECUTE FUNCTION knowra_touch_updated_at();

ALTER TABLE user_profiles ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON user_profiles;
CREATE POLICY tenant_isolation ON user_profiles
  FOR ALL
  USING (user_id = auth.uid())
  WITH CHECK (user_id = auth.uid());

-- ============================================================================
-- sync_state
-- ============================================================================
CREATE TABLE IF NOT EXISTS sync_state (
  user_id             UUID  NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  device_id           TEXT  NOT NULL,
  last_pushed_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  last_push_revision  BIGINT NOT NULL DEFAULT 0,
  pending_tables      JSONB NOT NULL DEFAULT '[]'::jsonb,
  PRIMARY KEY (user_id, device_id)
);

ALTER TABLE sync_state ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON sync_state;
CREATE POLICY tenant_isolation ON sync_state
  FOR ALL
  USING (user_id = auth.uid())
  WITH CHECK (user_id = auth.uid());

-- ============================================================================
-- papers
-- ============================================================================
CREATE TABLE IF NOT EXISTS papers (
  id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id                  UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,

  -- file identity
  filepath                 TEXT NOT NULL,
  filename                 TEXT NOT NULL,
  file_hash                TEXT NOT NULL,
  num_pages                INTEGER,

  -- content metadata (intentionally NO extracted_text, NO chat_history)
  title                    TEXT,
  authors                  JSONB NOT NULL DEFAULT '[]'::jsonb,
  paper_category_model     TEXT,
  paper_category_override  TEXT,

  -- processing state
  processed                BOOLEAN NOT NULL DEFAULT FALSE,
  processed_at             TIMESTAMPTZ,
  extraction_model         TEXT,
  processing_status        TEXT NOT NULL DEFAULT 'scanning',
  retry_count              INTEGER NOT NULL DEFAULT 0,
  last_error_stage         TEXT,
  last_error_reason        TEXT,
  last_error_recoverable   BOOLEAN,
  error                    TEXT,

  -- structured extraction (compact, post-processing)
  raw_llm_response         TEXT,
  notes                    TEXT,

  created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  legacy_id                INTEGER,

  CONSTRAINT papers_user_hash_uniq UNIQUE (user_id, file_hash)
);

CREATE INDEX IF NOT EXISTS papers_user_id_idx       ON papers (user_id);
CREATE INDEX IF NOT EXISTS papers_user_status_idx   ON papers (user_id, processing_status);
CREATE INDEX IF NOT EXISTS papers_user_processed_idx ON papers (user_id, processed, processed_at);

CREATE TRIGGER papers_touch_updated_at
  BEFORE UPDATE ON papers
  FOR EACH ROW EXECUTE FUNCTION knowra_touch_updated_at();

ALTER TABLE papers ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON papers;
CREATE POLICY tenant_isolation ON papers
  FOR ALL
  USING (user_id = auth.uid())
  WITH CHECK (user_id = auth.uid());

-- ============================================================================
-- knowledge_nodes
-- ============================================================================
CREATE TABLE IF NOT EXISTS knowledge_nodes (
  id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id                 UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,

  title                   TEXT NOT NULL,
  content                 TEXT NOT NULL,
  node_type               TEXT NOT NULL DEFAULT 'concept',
  node_origin             TEXT NOT NULL DEFAULT 'auto',

  promotion_status        TEXT NOT NULL DEFAULT 'pending',
  promoted_by             TEXT,
  promotion_reason        TEXT,
  last_promotion_eval_at  TIMESTAMPTZ,

  hidden                  BOOLEAN NOT NULL DEFAULT FALSE,

  tags                    JSONB NOT NULL DEFAULT '[]'::jsonb,
  embedding               JSONB,
  source_paper_ids        JSONB NOT NULL DEFAULT '[]'::jsonb,

  created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  legacy_id               INTEGER
);

CREATE INDEX IF NOT EXISTS knowledge_nodes_user_id_idx     ON knowledge_nodes (user_id);
CREATE INDEX IF NOT EXISTS knowledge_nodes_user_type_idx   ON knowledge_nodes (user_id, node_type);
CREATE INDEX IF NOT EXISTS knowledge_nodes_user_status_idx ON knowledge_nodes (user_id, promotion_status);

CREATE TRIGGER knowledge_nodes_touch_updated_at
  BEFORE UPDATE ON knowledge_nodes
  FOR EACH ROW EXECUTE FUNCTION knowra_touch_updated_at();

ALTER TABLE knowledge_nodes ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON knowledge_nodes;
CREATE POLICY tenant_isolation ON knowledge_nodes
  FOR ALL
  USING (user_id = auth.uid())
  WITH CHECK (user_id = auth.uid());

-- ============================================================================
-- knowledge_edges
-- ============================================================================
CREATE TABLE IF NOT EXISTS knowledge_edges (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id         UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,

  source_id       UUID NOT NULL REFERENCES knowledge_nodes(id) ON DELETE CASCADE,
  target_id       UUID NOT NULL REFERENCES knowledge_nodes(id) ON DELETE CASCADE,
  relation_type   TEXT NOT NULL DEFAULT 'related',
  weight          DOUBLE PRECISION NOT NULL DEFAULT 0.0,

  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  legacy_id       INTEGER,

  CONSTRAINT knowledge_edges_user_unique UNIQUE (user_id, source_id, target_id, relation_type)
);

CREATE INDEX IF NOT EXISTS knowledge_edges_user_source_idx ON knowledge_edges (user_id, source_id);
CREATE INDEX IF NOT EXISTS knowledge_edges_user_target_idx ON knowledge_edges (user_id, target_id);

CREATE TRIGGER knowledge_edges_touch_updated_at
  BEFORE UPDATE ON knowledge_edges
  FOR EACH ROW EXECUTE FUNCTION knowra_touch_updated_at();

-- Cross-user FK leak prevention: edges' source/target must belong to the
-- same user. RLS handles the read side; this trigger handles writes.
CREATE OR REPLACE FUNCTION check_edge_user_consistency()
  RETURNS TRIGGER AS $$
DECLARE
  src_user UUID;
  tgt_user UUID;
BEGIN
  SELECT user_id INTO src_user FROM knowledge_nodes WHERE id = NEW.source_id;
  SELECT user_id INTO tgt_user FROM knowledge_nodes WHERE id = NEW.target_id;

  IF src_user IS NULL OR tgt_user IS NULL THEN
    RAISE EXCEPTION 'edge references non-existent node';
  END IF;
  IF src_user <> NEW.user_id THEN
    RAISE EXCEPTION 'source node user mismatch (source user %, edge user %)',
      src_user, NEW.user_id;
  END IF;
  IF tgt_user <> NEW.user_id THEN
    RAISE EXCEPTION 'target node user mismatch (target user %, edge user %)',
      tgt_user, NEW.user_id;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS edge_user_consistency_check ON knowledge_edges;
CREATE TRIGGER edge_user_consistency_check
  BEFORE INSERT OR UPDATE ON knowledge_edges
  FOR EACH ROW EXECUTE FUNCTION check_edge_user_consistency();

ALTER TABLE knowledge_edges ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON knowledge_edges;
CREATE POLICY tenant_isolation ON knowledge_edges
  FOR ALL
  USING (user_id = auth.uid())
  WITH CHECK (user_id = auth.uid());

-- ============================================================================
-- wiki_files
-- ============================================================================
CREATE TABLE IF NOT EXISTS wiki_files (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id         UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,

  kind            TEXT NOT NULL,                     -- paper / concept / index / lint_report
  rel_path        TEXT NOT NULL,                     -- e.g. papers/0001-foo.md
  storage_path    TEXT NOT NULL,                     -- wiki/{user_id}/{rel_path}
  content_hash    TEXT NOT NULL,                     -- sha256:...
  size_bytes      INTEGER NOT NULL,

  -- frontmatter cache
  title           TEXT,
  aliases         JSONB NOT NULL DEFAULT '[]'::jsonb,
  compiled_at     TIMESTAMPTZ,

  -- relations
  paper_id        UUID REFERENCES papers(id) ON DELETE SET NULL,
  concept_id      UUID REFERENCES knowledge_nodes(id) ON DELETE SET NULL,

  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  CONSTRAINT wiki_files_user_path_uniq UNIQUE (user_id, rel_path)
);

CREATE INDEX IF NOT EXISTS wiki_files_user_kind_idx ON wiki_files (user_id, kind);

CREATE TRIGGER wiki_files_touch_updated_at
  BEFORE UPDATE ON wiki_files
  FOR EACH ROW EXECUTE FUNCTION knowra_touch_updated_at();

ALTER TABLE wiki_files ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON wiki_files;
CREATE POLICY tenant_isolation ON wiki_files
  FOR ALL
  USING (user_id = auth.uid())
  WITH CHECK (user_id = auth.uid());

-- ============================================================================
-- cloud_llm_calls
--
-- Cloud-side mobile Ask telemetry. Only meta (no key, no prompt, no response).
-- ============================================================================
CREATE TABLE IF NOT EXISTS cloud_llm_calls (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id           UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  called_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  task              TEXT NOT NULL,
  provider          TEXT NOT NULL,
  model             TEXT NOT NULL,
  prompt_tokens     INTEGER,
  completion_tokens INTEGER,
  total_tokens      INTEGER,
  latency_ms        INTEGER,
  success           BOOLEAN NOT NULL DEFAULT TRUE,
  error_class       TEXT
);

CREATE INDEX IF NOT EXISTS cloud_llm_calls_user_time_idx ON cloud_llm_calls (user_id, called_at DESC);

ALTER TABLE cloud_llm_calls ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON cloud_llm_calls;
CREATE POLICY tenant_isolation ON cloud_llm_calls
  FOR ALL
  USING (user_id = auth.uid())
  WITH CHECK (user_id = auth.uid());

-- ============================================================================
-- Done. Next migrations should add features incrementally (e.g. pgvector
-- on knowledge_nodes.embedding, FTS index on wiki_files, etc.) — never
-- modify or drop columns introduced here without a separate compat plan
-- documented in docs/SCHEMA-MIGRATION.md.
-- ============================================================================
