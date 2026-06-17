-- ============================================================================
-- 0007_recommendations.sql — arXiv recommendation feed (GLOBAL, not per-user)
--
-- A Mon/Wed/Fri cloud scheduler searches arXiv per system tag and stores the
-- results here, pruned to 30 days. arXiv metadata is public, so any signed-in
-- user may read; only the service role (the scheduler) writes. Per-user
-- "followed tags" is a client-side display filter, so there is no per-user
-- subscription table.
-- ============================================================================

CREATE TABLE IF NOT EXISTS recommendations (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tag               TEXT NOT NULL,
  arxiv_id          TEXT NOT NULL,
  title             TEXT NOT NULL,
  authors           JSONB NOT NULL DEFAULT '[]'::jsonb,
  abstract          TEXT,
  pdf_url           TEXT,
  primary_category  TEXT,
  published         TIMESTAMPTZ,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT recommendations_tag_arxiv_uniq UNIQUE (tag, arxiv_id)
);

CREATE INDEX IF NOT EXISTS recommendations_tag_created_idx
  ON recommendations (tag, created_at DESC);

ALTER TABLE recommendations ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS recommendations_read_authenticated ON recommendations;
CREATE POLICY recommendations_read_authenticated ON recommendations
  FOR SELECT
  TO authenticated
  USING (true);

-- Per-tag last-search bookmark; touched only by the scheduler (service role).
CREATE TABLE IF NOT EXISTS rec_search_state (
  tag               TEXT PRIMARY KEY,
  last_searched_at  TIMESTAMPTZ
);
ALTER TABLE rec_search_state ENABLE ROW LEVEL SECURITY;
-- No policy → no access for the anon/authenticated roles; service role bypasses.
