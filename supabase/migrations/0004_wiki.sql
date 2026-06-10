-- ============================================================================
-- 0004_wiki.sql — Compiled wiki layer: file metadata + tombstones
--
-- The wiki .md content itself lives in Supabase Storage at
-- wiki/<user_id>/<rel_path>; this table only carries the searchable
-- metadata + frontmatter cache so the sync/snapshot endpoints don't
-- need to inspect Storage on every request.
--
-- cloud_deletions captures tombstones for any row deleted by a sync
-- commit so mobile clients can evict previously-synced rows.
-- ============================================================================

-- ── wiki_files ──────────────────────────────────────────────────────────────

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

DROP TRIGGER IF EXISTS wiki_files_touch_updated_at ON wiki_files;
CREATE TRIGGER wiki_files_touch_updated_at
  BEFORE UPDATE ON wiki_files
  FOR EACH ROW EXECUTE FUNCTION knowra_touch_updated_at();

ALTER TABLE wiki_files ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON wiki_files;
CREATE POLICY tenant_isolation ON wiki_files
  FOR ALL
  USING (user_id = auth.uid())
  WITH CHECK (user_id = auth.uid());

-- ── cloud_deletions ─────────────────────────────────────────────────────────
-- Tombstone for any row deleted by a sync commit. The mobile snapshot
-- endpoint reads `deleted_at > since` so the client can evict
-- previously-synced IDs from its local cache. Without these, a
-- deletion on desktop would leave a dangling row on mobile forever.

CREATE TABLE IF NOT EXISTS cloud_deletions (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id         UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  table_name      TEXT NOT NULL,         -- papers / knowledge_nodes / knowledge_edges / wiki_files
  row_id          TEXT NOT NULL,
  deleted_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  CONSTRAINT cloud_deletions_user_table_row_uniq UNIQUE (user_id, table_name, row_id)
);

CREATE INDEX IF NOT EXISTS cloud_deletions_user_time_idx ON cloud_deletions (user_id, deleted_at DESC);

ALTER TABLE cloud_deletions ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON cloud_deletions;
CREATE POLICY tenant_isolation ON cloud_deletions
  FOR ALL
  USING (user_id = auth.uid())
  WITH CHECK (user_id = auth.uid());
