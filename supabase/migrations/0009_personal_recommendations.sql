-- Personal metadata-only recommendations. Apply before deploying the new backend.

-- No existing tables or user data are changed. Service-role-only writes.

BEGIN;

CREATE TABLE IF NOT EXISTS rec_profiles (
	user_id VARCHAR NOT NULL,
	current_focus TEXT NOT NULL,
	snapshot JSON NOT NULL,
	feedback_weights JSON NOT NULL,
	version INTEGER NOT NULL,
	feedback_count INTEGER NOT NULL,
	feedback_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (user_id)
);

ALTER TABLE rec_profiles ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS rec_profiles_owner_read ON rec_profiles;

CREATE POLICY rec_profiles_owner_read ON rec_profiles FOR SELECT TO authenticated USING ((user_id = auth.uid()::text));

CREATE TABLE IF NOT EXISTS rec_candidates (
	arxiv_id VARCHAR NOT NULL,
	metadata_json JSON NOT NULL,
	features JSON NOT NULL,
	content_hash VARCHAR NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (arxiv_id)
);

ALTER TABLE rec_candidates ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS rec_candidates_owner_read ON rec_candidates;

CREATE POLICY rec_candidates_owner_read ON rec_candidates FOR SELECT TO authenticated USING (true);

CREATE TABLE IF NOT EXISTS rec_batches (
	id VARCHAR NOT NULL,
	user_id VARCHAR NOT NULL,
	slot VARCHAR NOT NULL,
	status VARCHAR NOT NULL,
	snapshot JSON NOT NULL,
	items JSON NOT NULL,
	node_id VARCHAR,
	lease_token VARCHAR,
	lease_expires_at TIMESTAMP WITH TIME ZONE,
	attempts INTEGER NOT NULL,
	error TEXT,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	completed_at TIMESTAMP WITH TIME ZONE,
	PRIMARY KEY (id),
	CONSTRAINT rec_batches_user_slot_uniq UNIQUE (user_id, slot)
);

CREATE INDEX IF NOT EXISTS ix_rec_batches_user_id ON rec_batches (user_id);

ALTER TABLE rec_batches ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS rec_batches_owner_read ON rec_batches;

CREATE POLICY rec_batches_owner_read ON rec_batches FOR SELECT TO authenticated USING ((user_id = auth.uid()::text));

CREATE TABLE IF NOT EXISTS rec_events (
	id VARCHAR NOT NULL,
	user_id VARCHAR NOT NULL,
	arxiv_id VARCHAR NOT NULL,
	kind VARCHAR NOT NULL,
	batch_id VARCHAR NOT NULL,
	payload JSON NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT rec_events_user_paper_kind_uniq UNIQUE (user_id, arxiv_id, kind)
);

CREATE INDEX IF NOT EXISTS ix_rec_events_user_id ON rec_events (user_id);

ALTER TABLE rec_events ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS rec_events_owner_read ON rec_events;

CREATE POLICY rec_events_owner_read ON rec_events FOR SELECT TO authenticated USING ((user_id = auth.uid()::text));

CREATE TABLE IF NOT EXISTS rec_workers (
	user_id VARCHAR NOT NULL,
	node_id VARCHAR NOT NULL,
	token_hash VARCHAR NOT NULL,
	health VARCHAR NOT NULL,
	last_seen_at TIMESTAMP WITH TIME ZONE,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (user_id, node_id),
	UNIQUE (token_hash)
);

ALTER TABLE rec_workers ENABLE ROW LEVEL SECURITY;

CREATE TABLE IF NOT EXISTS rec_usage (
	id VARCHAR NOT NULL,
	user_id VARCHAR NOT NULL,
	batch_id VARCHAR NOT NULL,
	provider VARCHAR NOT NULL,
	calls INTEGER NOT NULL,
	cost_cny FLOAT,
	reserved_cny FLOAT NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (batch_id)
);

CREATE INDEX IF NOT EXISTS ix_rec_usage_user_id ON rec_usage (user_id);

ALTER TABLE rec_usage ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS rec_usage_owner_read ON rec_usage;

CREATE POLICY rec_usage_owner_read ON rec_usage FOR SELECT TO authenticated USING ((user_id = auth.uid()::text));

COMMIT;

-- Rollback: stop workers/scheduler, then drop these six rec_* tables only after exporting feedback.
