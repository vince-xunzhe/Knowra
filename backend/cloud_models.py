"""SQLAlchemy models for the cloud-mode tables.

These mirror ``supabase/migrations/0001_init.sql`` so the FastAPI cloud
backend can use ORM access without writing raw SQL. The same models
work against either:

  - Real Postgres on Supabase (production)
  - SQLite in-memory (unit tests; RLS is not enforced there but the
    business logic doesn't depend on it — RLS is a defense-in-depth
    layer that lives at the DB boundary)

⚠️ Keep this file 1:1 aligned with ``0001_init.sql``. Any column or
constraint change goes in both files plus a row in
``docs/SCHEMA-MIGRATION.md``. When unsure which side leads, the SQL
file is canonical.

The local-mode ``backend/models.py`` is NOT replaced by this file —
the desktop's SQLite still uses those Integer-id models until W3.2
flips them to UUID. The two model sets coexist during the transition.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import declarative_base


CloudBase = declarative_base()


def _uuid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ── user_profiles ─────────────────────────────────────────────────────


class UserProfile(CloudBase):
    __tablename__ = "user_profiles"

    user_id = Column(String, primary_key=True)
    display_name = Column(String, nullable=True)
    desktop_first_seen = Column(DateTime, nullable=True)
    last_desktop_sync_at = Column(DateTime, nullable=True)
    last_mobile_open_at = Column(DateTime, nullable=True)
    settings = Column(JSON, default=dict)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow)


# ── sync_state (per (user, device) watermark) ─────────────────────────


class SyncState(CloudBase):
    __tablename__ = "sync_state"

    user_id = Column(String, primary_key=True)
    device_id = Column(String, primary_key=True)
    last_pushed_at = Column(DateTime, default=_utcnow)
    last_push_revision = Column(Integer, default=0)
    pending_tables = Column(JSON, default=list)


# ── sync_sessions (3-step upload staging area) ────────────────────────


class SyncSession(CloudBase):
    __tablename__ = "sync_sessions"

    id = Column(String, primary_key=True, default=_uuid)
    user_id = Column(String, nullable=False)
    device_id = Column(String, nullable=False)

    # pending / committed / aborted / expired
    status = Column(String, nullable=False, default="pending")
    staging = Column(JSON, nullable=False)
    uploads_pending = Column(JSON, nullable=False, default=list)

    # cached response so a repeated commit returns the same revision
    committed_response = Column(JSON, nullable=True)

    created_at = Column(DateTime, default=_utcnow, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    committed_at = Column(DateTime, nullable=True)


# ── papers (cloud-side: no extracted_text / chat_history) ─────────────


class CloudPaper(CloudBase):
    __tablename__ = "papers"

    id = Column(String, primary_key=True, default=_uuid)
    user_id = Column(String, nullable=False, index=True)

    filepath = Column(String, nullable=False)
    filename = Column(String, nullable=False)
    file_hash = Column(String, nullable=False)
    num_pages = Column(Integer, nullable=True)

    title = Column(String, nullable=True)
    authors = Column(JSON, default=list)
    paper_category_model = Column(String, nullable=True)
    paper_category_override = Column(String, nullable=True)

    processed = Column(Boolean, default=False)
    processed_at = Column(DateTime, nullable=True)
    extraction_model = Column(String, nullable=True)
    processing_status = Column(String, default="scanning")
    retry_count = Column(Integer, default=0)
    last_error_stage = Column(String, nullable=True)
    last_error_reason = Column(Text, nullable=True)
    last_error_recoverable = Column(Boolean, nullable=True)
    error = Column(Text, nullable=True)

    raw_llm_response = Column(Text, nullable=True)
    notes = Column(Text, nullable=True)

    created_at = Column(DateTime, default=_utcnow, nullable=False)
    updated_at = Column(DateTime, default=_utcnow, nullable=False)
    legacy_id = Column(Integer, nullable=True)

    __table_args__ = (
        UniqueConstraint("user_id", "file_hash", name="papers_user_hash_uniq"),
    )


# ── knowledge_nodes ───────────────────────────────────────────────────


class CloudKnowledgeNode(CloudBase):
    __tablename__ = "knowledge_nodes"

    id = Column(String, primary_key=True, default=_uuid)
    user_id = Column(String, nullable=False, index=True)

    title = Column(String, nullable=False)
    content = Column(Text, nullable=False)
    node_type = Column(String, default="concept")
    node_origin = Column(String, default="auto")

    promotion_status = Column(String, default="pending")
    promoted_by = Column(String, nullable=True)
    promotion_reason = Column(Text, nullable=True)
    last_promotion_eval_at = Column(DateTime, nullable=True)
    hidden = Column(Boolean, default=False)

    tags = Column(JSON, default=list)
    embedding = Column(JSON, nullable=True)
    source_paper_ids = Column(JSON, default=list)

    created_at = Column(DateTime, default=_utcnow, nullable=False)
    updated_at = Column(DateTime, default=_utcnow, nullable=False)
    legacy_id = Column(Integer, nullable=True)


# ── knowledge_edges ───────────────────────────────────────────────────


class CloudKnowledgeEdge(CloudBase):
    __tablename__ = "knowledge_edges"

    id = Column(String, primary_key=True, default=_uuid)
    user_id = Column(String, nullable=False, index=True)

    # In Postgres the FKs reference knowledge_nodes(id). We omit the
    # ForeignKey here so SQLite tests don't need to declare it; cross-
    # user mismatches are caught by Postgres trigger + by our app-layer
    # validation in the sync router.
    source_id = Column(String, nullable=False)
    target_id = Column(String, nullable=False)
    relation_type = Column(String, default="related")
    weight = Column(Float, default=0.0)

    created_at = Column(DateTime, default=_utcnow, nullable=False)
    updated_at = Column(DateTime, default=_utcnow, nullable=False)
    legacy_id = Column(Integer, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "user_id", "source_id", "target_id", "relation_type",
            name="knowledge_edges_user_unique",
        ),
    )


# ── wiki_files ────────────────────────────────────────────────────────


class WikiFile(CloudBase):
    __tablename__ = "wiki_files"

    id = Column(String, primary_key=True, default=_uuid)
    user_id = Column(String, nullable=False, index=True)

    kind = Column(String, nullable=False)
    rel_path = Column(String, nullable=False)
    storage_path = Column(String, nullable=False)
    content_hash = Column(String, nullable=False)
    size_bytes = Column(Integer, nullable=False)

    title = Column(String, nullable=True)
    aliases = Column(JSON, default=list)
    compiled_at = Column(DateTime, nullable=True)

    paper_id = Column(String, nullable=True)
    concept_id = Column(String, nullable=True)

    created_at = Column(DateTime, default=_utcnow, nullable=False)
    updated_at = Column(DateTime, default=_utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("user_id", "rel_path", name="wiki_files_user_path_uniq"),
    )


# ── cloud_llm_calls (mobile Ask telemetry; no key, no content) ────────


class CloudLLMCall(CloudBase):
    __tablename__ = "cloud_llm_calls"

    id = Column(String, primary_key=True, default=_uuid)
    user_id = Column(String, nullable=False, index=True)
    called_at = Column(DateTime, default=_utcnow, nullable=False)
    task = Column(String, nullable=False)
    provider = Column(String, nullable=False)
    model = Column(String, nullable=False)
    prompt_tokens = Column(Integer, nullable=True)
    completion_tokens = Column(Integer, nullable=True)
    total_tokens = Column(Integer, nullable=True)
    latency_ms = Column(Integer, nullable=True)
    success = Column(Boolean, default=True)
    error_class = Column(String, nullable=True)


# ── revision counter (kept in a singleton table) ──────────────────────


class CloudRevision(CloudBase):
    """Server-side monotonic revision number. One row per user;
    incremented at every successful commit so clients can fetch deltas
    via `since=<rev>` semantics (not used by v1 protocol but reserved)."""

    __tablename__ = "cloud_revisions"

    user_id = Column(String, primary_key=True)
    revision = Column(Integer, default=0, nullable=False)
    updated_at = Column(DateTime, default=_utcnow, nullable=False)


# ── cloud_deletions (tombstones so mobile can drop deleted rows) ──────


class CloudDeletion(CloudBase):
    """Tombstone for any row deleted by a sync commit.

    The mobile snapshot endpoint reads this with ``deleted_at > since``
    so the client knows which previously-synced IDs to evict from its
    local cache. Without this, a deletion on desktop would leave a
    dangling row on mobile forever.

    Tombstones are kept for 90 days by a background GC (TBD); after
    that mobile clients past that horizon must do a full snapshot to
    catch up."""

    __tablename__ = "cloud_deletions"

    id = Column(String, primary_key=True, default=_uuid)
    user_id = Column(String, nullable=False, index=True)
    table_name = Column(String, nullable=False)        # papers / knowledge_nodes / etc.
    row_id = Column(String, nullable=False)
    deleted_at = Column(DateTime, default=_utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "user_id", "table_name", "row_id",
            name="cloud_deletions_user_table_row_uniq",
        ),
    )


def init_cloud_schema(engine) -> None:
    """Create all cloud-mode tables. Used by tests + the cloud-mode
    boot path; production goes through Supabase migrations instead."""
    CloudBase.metadata.create_all(bind=engine)
