-- ============================================================================
-- 0010_edge_provenance.sql — edge-level source, evidence and confidence
--
-- Additive and rerunnable: rolling desktop clients may omit these fields, so
-- every column stays nullable.  Existing rows receive only origins that can be
-- inferred safely from the old relation type; no evidence is fabricated.
-- ============================================================================

ALTER TABLE knowledge_edges
  ADD COLUMN IF NOT EXISTS origin TEXT DEFAULT 'legacy',
  ADD COLUMN IF NOT EXISTS confidence DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS source_paper_id UUID REFERENCES papers(id) ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS source_field TEXT,
  ADD COLUMN IF NOT EXISTS evidence TEXT,
  ADD COLUMN IF NOT EXISTS metadata JSONB,
  ADD COLUMN IF NOT EXISTS extractor_version TEXT;

UPDATE knowledge_edges
SET origin = CASE
  WHEN relation_type = 'similar' THEN 'embedding'
  WHEN relation_type = 'curated_link' THEN 'manual'
  ELSE 'legacy'
END
WHERE origin IS NULL OR BTRIM(origin) = '';

UPDATE knowledge_edges
SET confidence = weight
WHERE relation_type = 'similar'
  AND confidence IS NULL
  AND weight BETWEEN 0.0 AND 1.0;

-- Extend the existing cross-tenant edge guard to the new paper reference.
-- RLS protects reads; this SECURITY INVOKER trigger validates ownership at
-- write time in the same transaction as the node endpoint checks.
CREATE OR REPLACE FUNCTION check_edge_user_consistency()
  RETURNS TRIGGER AS $$
DECLARE
  src_user UUID;
  tgt_user UUID;
  paper_user UUID;
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

  IF NEW.source_paper_id IS NOT NULL THEN
    SELECT user_id INTO paper_user FROM papers WHERE id = NEW.source_paper_id;
    IF paper_user IS NULL THEN
      RAISE EXCEPTION 'edge references non-existent source paper';
    END IF;
    IF paper_user <> NEW.user_id THEN
      RAISE EXCEPTION 'source paper user mismatch (paper user %, edge user %)',
        paper_user, NEW.user_id;
    END IF;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'knowledge_edges_origin_check'
  ) THEN
    ALTER TABLE knowledge_edges
      ADD CONSTRAINT knowledge_edges_origin_check
      CHECK (origin IS NULL OR origin IN ('explicit', 'inferred', 'embedding', 'manual', 'legacy'))
      NOT VALID;
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'knowledge_edges_confidence_check'
  ) THEN
    ALTER TABLE knowledge_edges
      ADD CONSTRAINT knowledge_edges_confidence_check
      CHECK (confidence IS NULL OR (confidence >= 0.0 AND confidence <= 1.0))
      NOT VALID;
  END IF;
END
$$;

ALTER TABLE knowledge_edges VALIDATE CONSTRAINT knowledge_edges_origin_check;
ALTER TABLE knowledge_edges VALIDATE CONSTRAINT knowledge_edges_confidence_check;

CREATE INDEX IF NOT EXISTS knowledge_edges_user_origin_idx
  ON knowledge_edges (user_id, origin);
CREATE INDEX IF NOT EXISTS knowledge_edges_user_source_paper_idx
  ON knowledge_edges (user_id, source_paper_id);
