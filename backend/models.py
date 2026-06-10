"""SQLAlchemy models for the local desktop SQLite.

Post-multitenant migration these declare:
  - id columns as String (UUID v4)
  - user_id column (per-row tenant attribution; nullable locally so
    pre-multitenant code paths still work during migration)
  - legacy_id column (preserved INT id from pre-migration days; kept
    around for 6 months for forensics)

These match the cloud Postgres schema declared in
``supabase/migrations/0001_init.sql`` 1:1 — any change here must mirror
the SQL file (or vice versa). See docs/SCHEMA-MIGRATION.md.
"""
from sqlalchemy import Column, Integer, String, Boolean, Float, DateTime, Text, JSON
from sqlalchemy.ext.declarative import declarative_base
from datetime import datetime, timezone
import uuid

Base = declarative_base()


def _uuid() -> str:
    """Generate a string UUID for new rows. Used as a Column default so
    callers can just ``Paper(filepath=..., ...)`` and the id is auto-
    populated, mirroring the pre-multitenant INT autoincrement UX."""
    return str(uuid.uuid4())


class Paper(Base):
    __tablename__ = "papers"

    id = Column(String, primary_key=True, default=_uuid)
    # Multi-tenant attribution. Nullable locally for backwards-compat
    # with pre-migration data; populated by the multitenant migration
    # for all existing rows and by routers for new rows once the
    # current_user dependency is wired through.
    user_id = Column(String, nullable=True, index=True)
    # Preserved INT id from the pre-multitenant schema. Kept ~6 months
    # for forensics and to support `legacy_id` URL fallbacks if needed.
    legacy_id = Column(Integer, nullable=True)

    filepath = Column(String, unique=True, nullable=False)
    filename = Column(String, nullable=False)
    file_hash = Column(String, nullable=False)

    # PDF-derived metadata
    num_pages = Column(Integer, nullable=True)
    extracted_text = Column(Text, nullable=True)       # full plain text
    first_page_image_path = Column(String, nullable=True)  # rendered PNG path

    # VLM-extracted metadata (for quick list display)
    title = Column(String, nullable=True)
    authors = Column(JSON, default=list)

    processed = Column(Boolean, default=False)
    processed_at = Column(DateTime, nullable=True)
    # Which OpenAI model produced the most recent raw_llm_response. Useful in
    # the Review page so the user can attribute extraction quality to model
    # vs. prompt when re-reading later.
    extraction_model = Column(String, nullable=True)
    # The paper's lane/category predicted by the extraction model. This is
    # stored separately from raw_llm_response so users can override it
    # without mutating the original model output.
    paper_category_model = Column(String, nullable=True)
    # Optional human override for the paper's lane/category. When present,
    # wiki graph grouping prefers this over the model-predicted value.
    paper_category_override = Column(String, nullable=True)
    raw_llm_response = Column(Text, nullable=True)
    notes = Column(Text, nullable=True)  # user-authored markdown notes
    error = Column(Text, nullable=True)
    # Pipeline lifecycle:
    # scanning -> extracting -> parsing -> graphing -> done/failed
    processing_status = Column(String, default="scanning")
    retry_count = Column(Integer, default=0)
    last_error_stage = Column(String, nullable=True)
    last_error_reason = Column(Text, nullable=True)
    last_error_recoverable = Column(Boolean, nullable=True)
    # OpenAI Files API — cached file_id so we don't re-upload the same PDF
    openai_file_id = Column(String, nullable=True)
    # Cached vector store for Responses API + file_search.
    openai_vector_store_id = Column(String, nullable=True)
    # OpenAI Assistants API — persisted thread for follow-up Q&A after extraction.
    # Threads expire on OpenAI's side after ~60 days; thread_created_at drives the
    # frontend countdown. chat_history keeps a local copy so the UI can render
    # past turns even after the remote thread is gone.
    openai_thread_id = Column(String, nullable=True)
    thread_created_at = Column(DateTime, nullable=True)
    chat_history = Column(JSON, default=list)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class KnowledgeNode(Base):
    __tablename__ = "knowledge_nodes"

    id = Column(String, primary_key=True, default=_uuid)
    user_id = Column(String, nullable=True, index=True)
    legacy_id = Column(Integer, nullable=True)

    title = Column(String, nullable=False)
    content = Column(Text, nullable=False)
    node_type = Column(String, default="concept")  # paper/technique/dataset/problem/concept/entity
    node_origin = Column(String, default="auto")   # auto/manual
    hidden = Column(Boolean, default=False)
    # Promotion lifecycle for the concept-first design. `promotion_status`
    # gates whether a node becomes a wiki concept page and whether it shows
    # in the curated graph view. Paper nodes ignore this field.
    promotion_status = Column(String, default="pending")  # pending/promoted/rejected
    promoted_by = Column(String, nullable=True)            # heuristic/llm/user/legacy
    promotion_reason = Column(Text, nullable=True)         # LLM rationale or user note
    last_promotion_eval_at = Column(DateTime, nullable=True)
    tags = Column(JSON, default=list)
    embedding = Column(JSON, nullable=True)
    # JSON array of paper id STRINGS post-migration. Pre-migration rows
    # may carry INT entries until the migration rewrites them; helper
    # code in graph_service tolerates both during the transition.
    source_paper_ids = Column(JSON, default=list)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class KnowledgeEdge(Base):
    __tablename__ = "knowledge_edges"

    id = Column(String, primary_key=True, default=_uuid)
    user_id = Column(String, nullable=True, index=True)
    legacy_id = Column(Integer, nullable=True)

    # FK to knowledge_nodes.id. We don't declare a SQLAlchemy
    # ForeignKey constraint to keep SQLite migrations cheap; the cloud
    # Postgres schema is the one with the strict FK + cross-user trigger.
    source_id = Column(String, nullable=False)
    target_id = Column(String, nullable=False)
    relation_type = Column(String, default="related")
    weight = Column(Float, default=0.0)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class LLMCall(Base):
    """Per-LLM-call audit row. Written best-effort by model_gateway.telemetry
    so cost / latency / model-mix analytics in the dashboard don't have to
    do post-hoc accounting. Writes are async and swallow errors — telemetry
    must never break a real LLM call."""

    __tablename__ = "llm_calls"

    id = Column(String, primary_key=True, default=_uuid)
    user_id = Column(String, nullable=True, index=True)
    legacy_id = Column(Integer, nullable=True)

    called_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    # Logical task label set via telemetry.task_context(...). "unknown" for
    # call sites that haven't been tagged yet.
    task = Column(String, nullable=False, index=True)
    provider = Column(String, nullable=False)
    model = Column(String, nullable=False, index=True)
    # API surface used: chat / responses / embeddings / codex_cli.
    surface = Column(String, nullable=True)
    # Token counts from the upstream usage object. Codex CLI calls have no
    # usage data — those rows leave the counts null.
    prompt_tokens = Column(Integer, nullable=True)
    completion_tokens = Column(Integer, nullable=True)
    total_tokens = Column(Integer, nullable=True)
    latency_ms = Column(Integer, nullable=True)
    success = Column(Boolean, default=True, index=True)
    # Exception class name on failure (no message — message can contain
    # tokens or PII; class is enough for grouping).
    error_class = Column(String, nullable=True)
