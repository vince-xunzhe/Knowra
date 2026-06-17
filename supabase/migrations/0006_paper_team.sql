-- Team/lab classification dimension — a second grouping axis parallel to
-- paper_category. Auto-assigned by matching a paper's authors against an
-- editable team registry (see backend/services/paper_team_service.py); manual
-- override wins; unmatched papers fall back to "others" (computed, not stored).
-- Idempotent so re-running the migration set is safe.
alter table papers add column if not exists paper_team_model text;
alter table papers add column if not exists paper_team_override text;
