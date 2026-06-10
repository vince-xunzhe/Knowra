"""Pydantic request / response models for the sync protocol.

Mirrors docs/SYNC-PROTOCOL.md §2 (push: prepare + commit) and §3
(pull: snapshot). One source of truth for both routers and tests.

⚠️ Field names are part of the public HTTP contract — never rename
without bumping ``API_VERSION`` and writing a compat shim. Keep them
in sync with the prose in SYNC-PROTOCOL.md.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


# Bump if the on-the-wire contract changes in a backwards-incompatible
# way. Routers should refuse requests that hit an unsupported version.
API_VERSION = "1"


# ── shared row shapes ──────────────────────────────────────────────────


class _Row(BaseModel):
    """Base for incoming row payloads. We accept ``extra='allow'`` so the
    desktop can ship newer fields than the cloud knows about (forward
    compat); the unknown fields are dropped at persist time."""

    model_config = ConfigDict(extra="allow")

    id: str
    user_id: str
    updated_at: Optional[datetime] = None


class PaperRow(_Row):
    filepath: str
    filename: str
    file_hash: str
    title: Optional[str] = None
    authors: Optional[list[Any]] = None
    num_pages: Optional[int] = None
    processed: Optional[bool] = None
    processed_at: Optional[datetime] = None
    extraction_model: Optional[str] = None
    paper_category_model: Optional[str] = None
    paper_category_override: Optional[str] = None
    raw_llm_response: Optional[str] = None
    notes: Optional[str] = None
    error: Optional[str] = None
    processing_status: Optional[str] = None
    retry_count: Optional[int] = None
    last_error_stage: Optional[str] = None
    last_error_reason: Optional[str] = None
    last_error_recoverable: Optional[bool] = None
    legacy_id: Optional[int] = None
    created_at: Optional[datetime] = None


class KnowledgeNodeRow(_Row):
    title: str
    content: str
    node_type: Optional[str] = None
    node_origin: Optional[str] = None
    hidden: Optional[bool] = None
    promotion_status: Optional[str] = None
    promoted_by: Optional[str] = None
    promotion_reason: Optional[str] = None
    last_promotion_eval_at: Optional[datetime] = None
    tags: Optional[list[Any]] = None
    embedding: Optional[list[float]] = None
    source_paper_ids: Optional[list[str]] = None
    legacy_id: Optional[int] = None
    created_at: Optional[datetime] = None


class KnowledgeEdgeRow(_Row):
    source_id: str
    target_id: str
    relation_type: Optional[str] = None
    weight: Optional[float] = None
    legacy_id: Optional[int] = None
    created_at: Optional[datetime] = None


class WikiFileRow(_Row):
    kind: str = Field(..., description="paper / concept / index / lint_report")
    rel_path: str
    content_hash: str
    size_bytes: int
    title: Optional[str] = None
    aliases: Optional[list[str]] = None
    compiled_at: Optional[datetime] = None
    paper_id: Optional[str] = None
    concept_id: Optional[str] = None


# ── prepare ────────────────────────────────────────────────────────────


class SyncDeletions(BaseModel):
    """IDs the client wants deleted from the cloud."""

    papers: list[str] = Field(default_factory=list)
    knowledge_nodes: list[str] = Field(default_factory=list)
    knowledge_edges: list[str] = Field(default_factory=list)
    wiki_files: list[str] = Field(default_factory=list)


class SyncTables(BaseModel):
    """Upserts grouped by table. Order of insertion is left to the
    server but conventionally: papers → knowledge_nodes →
    knowledge_edges → wiki_files (so FKs resolve)."""

    papers: list[PaperRow] = Field(default_factory=list)
    knowledge_nodes: list[KnowledgeNodeRow] = Field(default_factory=list)
    knowledge_edges: list[KnowledgeEdgeRow] = Field(default_factory=list)
    wiki_files: list[WikiFileRow] = Field(default_factory=list)


class PrepareRequest(BaseModel):
    """``POST /api/sync/prepare`` body (see SYNC-PROTOCOL.md §2.1)."""

    api_version: str = Field(default=API_VERSION, description="Protocol version")
    device_id: str
    since: Optional[datetime] = None
    tables: SyncTables = Field(default_factory=SyncTables)
    deletions: SyncDeletions = Field(default_factory=SyncDeletions)


class UploadInstruction(BaseModel):
    """A signed PUT the client should perform against Supabase Storage."""

    rel_path: str
    upload_url: str
    method: str = "PUT"
    headers: dict[str, str] = Field(default_factory=dict)


class SkippedUpload(BaseModel):
    """A file whose content_hash matched server-side; no upload needed."""

    rel_path: str
    reason: str


class ValidationError(BaseModel):
    """Used when payload metadata can't even be staged (rare; usually
    auth / quota issues 401/413 raise outright)."""

    table: str
    id: Optional[str] = None
    reason: str
    code: Optional[str] = None


class PrepareResponse(BaseModel):
    sync_session_id: str
    expires_at: datetime
    uploads_required: list[UploadInstruction] = Field(default_factory=list)
    uploads_skipped: list[SkippedUpload] = Field(default_factory=list)
    validation_errors: list[ValidationError] = Field(default_factory=list)


# ── commit ─────────────────────────────────────────────────────────────


class CommitFileEntry(BaseModel):
    """One uploaded file confirmation. content_hash is sent back so the
    server can HEAD against Storage and reject mismatches."""

    rel_path: str
    content_hash: str


class CommitRequest(BaseModel):
    api_version: str = Field(default=API_VERSION)
    sync_session_id: str
    uploaded: list[CommitFileEntry] = Field(default_factory=list)


class CommitAccepted(BaseModel):
    """Summary of how many rows ended up in the canonical tables."""

    papers: int = 0
    knowledge_nodes: int = 0
    knowledge_edges: int = 0
    wiki_files: int = 0


class CommitRejection(BaseModel):
    table: str
    id: Optional[str] = None
    rel_path: Optional[str] = None
    reason: str
    code: str


class CommitResponse(BaseModel):
    revision: int
    accepted: CommitAccepted
    rejected: list[CommitRejection] = Field(default_factory=list)
    server_now: datetime


# ── error envelope (matches docs/SYNC-PROTOCOL.md §6) ─────────────────


class ErrorResponse(BaseModel):
    error: str
    message: str
    details: Optional[dict[str, Any]] = None
