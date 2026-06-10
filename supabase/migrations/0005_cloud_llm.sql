-- ============================================================================
-- 0005_cloud_llm.sql — Cloud-side LLM telemetry + per-user revision counter
--
-- cloud_llm_calls is separate from the desktop's llm_calls table because
-- they capture different surfaces: desktop telemetry records local
-- Codex CLI calls + OpenAI calls made through the desktop pipeline;
-- cloud telemetry only records mobile-Ask. v1 keeps them apart so the
-- "cost view" on each side stays interpretable.
--
-- cloud_revisions provides a monotonically increasing revision per
-- user, bumped on every successful sync commit. Mobile clients can
-- (in a future protocol version) request snapshots by revision rather
-- than timestamp.
-- ============================================================================

-- ── cloud_llm_calls ─────────────────────────────────────────────────────────

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
  -- ⚠️ NO openai_api_key, NO prompt content, NO response content.
  -- See docs/SYNC-PROTOCOL.md §4.3 for the privacy contract.
);

CREATE INDEX IF NOT EXISTS cloud_llm_calls_user_time_idx ON cloud_llm_calls (user_id, called_at DESC);

ALTER TABLE cloud_llm_calls ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON cloud_llm_calls;
CREATE POLICY tenant_isolation ON cloud_llm_calls
  FOR ALL
  USING (user_id = auth.uid())
  WITH CHECK (user_id = auth.uid());

-- ── cloud_revisions ─────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS cloud_revisions (
  user_id     UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
  revision    BIGINT NOT NULL DEFAULT 0,
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE cloud_revisions ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON cloud_revisions;
CREATE POLICY tenant_isolation ON cloud_revisions
  FOR ALL
  USING (user_id = auth.uid())
  WITH CHECK (user_id = auth.uid());
