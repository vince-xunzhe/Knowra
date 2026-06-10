-- ============================================================================
-- 0002_papers.sql — Papers table
--
-- Cloud-side schema deliberately omits extracted_text, chat_history,
-- openai_*_id, first_page_image_path: those are desktop-local artifacts
-- that don't belong in the cloud mirror (privacy + size). See
-- docs/SCHEMA-MIGRATION.md §2.3 for the full rationale.
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

CREATE INDEX IF NOT EXISTS papers_user_id_idx        ON papers (user_id);
CREATE INDEX IF NOT EXISTS papers_user_status_idx    ON papers (user_id, processing_status);
CREATE INDEX IF NOT EXISTS papers_user_processed_idx ON papers (user_id, processed, processed_at);

DROP TRIGGER IF EXISTS papers_touch_updated_at ON papers;
CREATE TRIGGER papers_touch_updated_at
  BEFORE UPDATE ON papers
  FOR EACH ROW EXECUTE FUNCTION knowra_touch_updated_at();

ALTER TABLE papers ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON papers;
CREATE POLICY tenant_isolation ON papers
  FOR ALL
  USING (user_id = auth.uid())
  WITH CHECK (user_id = auth.uid());
