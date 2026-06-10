-- ============================================================================
-- 0003_knowledge.sql — Knowledge graph: nodes + edges
--
-- knowledge_edges has FKs to knowledge_nodes (same user) and a trigger
-- that prevents cross-user FK leakage. RLS handles read isolation;
-- the trigger handles write-time validation that bypasses RLS due to
-- the FK lookup happening with SECURITY DEFINER semantics.
-- ============================================================================

-- ── knowledge_nodes ──────────────────────────────────────────────────────────

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

DROP TRIGGER IF EXISTS knowledge_nodes_touch_updated_at ON knowledge_nodes;
CREATE TRIGGER knowledge_nodes_touch_updated_at
  BEFORE UPDATE ON knowledge_nodes
  FOR EACH ROW EXECUTE FUNCTION knowra_touch_updated_at();

ALTER TABLE knowledge_nodes ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON knowledge_nodes;
CREATE POLICY tenant_isolation ON knowledge_nodes
  FOR ALL
  USING (user_id = auth.uid())
  WITH CHECK (user_id = auth.uid());

-- ── knowledge_edges ──────────────────────────────────────────────────────────

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

DROP TRIGGER IF EXISTS knowledge_edges_touch_updated_at ON knowledge_edges;
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
