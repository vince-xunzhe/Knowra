-- ============================================================================
-- 0001_meta.sql — Foundational extensions, helpers, and per-user infra tables
--
-- This file MUST apply before any other migration. Provides:
--   - pgcrypto extension (for gen_random_uuid())
--   - knowra_touch_updated_at() trigger function used by every domain table
--   - user_profiles: per-user extended profile
--   - sync_state: per-(user, device) sync watermark
--   - sync_sessions: 3-step upload staging area
--
-- All conventions established here (UUID PK, NOT NULL user_id, RLS policy,
-- updated_at trigger) are followed by 0002_papers, 0003_knowledge, etc.
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

DROP TRIGGER IF EXISTS user_profiles_touch_updated_at ON user_profiles;
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
-- sync_sessions (3-step upload staging area)
--
-- prepare writes metadata here; commit moves it to canonical tables. A
-- periodic GC drops sessions older than 1h that never committed and cleans
-- up Storage objects uploaded against that expired session.
-- ============================================================================
CREATE TABLE IF NOT EXISTS sync_sessions (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id         UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  device_id       TEXT NOT NULL,

  status          TEXT NOT NULL DEFAULT 'pending',   -- pending / committed / aborted / expired
  staging         JSONB NOT NULL,
  uploads_pending JSONB NOT NULL DEFAULT '[]'::jsonb,

  committed_response JSONB,

  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  expires_at      TIMESTAMPTZ NOT NULL DEFAULT NOW() + INTERVAL '1 hour',
  committed_at    TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS sync_sessions_user_id_idx ON sync_sessions (user_id);
CREATE INDEX IF NOT EXISTS sync_sessions_expires_idx ON sync_sessions (expires_at) WHERE status = 'pending';

ALTER TABLE sync_sessions ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON sync_sessions;
CREATE POLICY tenant_isolation ON sync_sessions
  FOR ALL
  USING (user_id = auth.uid())
  WITH CHECK (user_id = auth.uid());
