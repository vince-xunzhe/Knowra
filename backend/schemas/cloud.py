"""Pydantic response shapes for the mobile-facing /api/cloud/* endpoints.

Mirrors docs/SYNC-PROTOCOL.md §3 (snapshot) and §5 (auxiliary).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from schemas.sync import (
    KnowledgeEdgeRow,
    KnowledgeNodeRow,
    PaperRow,
)


# ── /api/cloud/me ─────────────────────────────────────────────────────


class MeStats(BaseModel):
    papers: int = 0
    concepts: int = 0
    edges: int = 0
    wiki_files: int = 0
    last_desktop_sync_at: Optional[datetime] = None
    wiki_size_bytes: int = 0


class MeResponse(BaseModel):
    user_id: str
    email: Optional[str] = None
    display_name: Optional[str] = None
    stats: MeStats


# ── /api/cloud/snapshot ───────────────────────────────────────────────


class WikiFileEntry(BaseModel):
    """A wiki_files row augmented with a pre-signed download URL so the
    mobile client can pull content directly from Storage."""

    model_config = ConfigDict(extra="allow")

    id: str
    user_id: str
    kind: str
    rel_path: str
    content_hash: str
    size_bytes: int
    title: Optional[str] = None
    aliases: Optional[list[str]] = None
    compiled_at: Optional[datetime] = None
    paper_id: Optional[str] = None
    concept_id: Optional[str] = None
    updated_at: Optional[datetime] = None
    download_url: str
    download_url_expires_at: datetime


class DeletedSince(BaseModel):
    papers: list[str] = Field(default_factory=list)
    knowledge_nodes: list[str] = Field(default_factory=list)
    knowledge_edges: list[str] = Field(default_factory=list)
    wiki_files: list[str] = Field(default_factory=list)


class SnapshotResponse(BaseModel):
    revision: int
    server_now: datetime
    papers: list[PaperRow] = Field(default_factory=list)
    knowledge_nodes: list[KnowledgeNodeRow] = Field(default_factory=list)
    knowledge_edges: list[KnowledgeEdgeRow] = Field(default_factory=list)
    wiki_files: list[WikiFileEntry] = Field(default_factory=list)
    deleted_since: DeletedSince = Field(default_factory=DeletedSince)


# ── /api/cloud/wiki/search ────────────────────────────────────────────


class WikiSearchRequest(BaseModel):
    q: str = Field(..., min_length=1, max_length=200)
    kind: Optional[str] = Field(default=None, description="paper / concept / ...")
    limit: int = Field(default=20, ge=1, le=100)


class WikiSearchHit(BaseModel):
    id: str
    kind: str
    rel_path: str
    title: Optional[str] = None
    snippet: Optional[str] = None       # null in v1; reserved for FTS v2


class WikiSearchResponse(BaseModel):
    query: str
    hits: list[WikiSearchHit] = Field(default_factory=list)


# ── /api/cloud/ask ────────────────────────────────────────────────────


class AskHistoryTurn(BaseModel):
    """One turn of prior conversation, replayed as part of the system
    context. The server NEVER stores history; the mobile client owns
    it and sends it back on each call."""

    role: str = Field(..., description="user | assistant")
    content: str = Field(..., max_length=8000)


class AskRequest(BaseModel):
    """``POST /api/cloud/ask`` body.

    See docs/SYNC-PROTOCOL.md §4.

    ⚠️ ``openai_api_key`` is used for the duration of this request and
    NEVER persisted (no DB row, no log line). The server-side handler
    drops it after the OpenAI call returns."""

    question: str = Field(..., min_length=1, max_length=8000)
    openai_api_key: str = Field(..., min_length=10, max_length=200)
    model: Optional[str] = Field(default=None, description="OpenAI model id; defaults to gpt-4o-mini")
    history: list[AskHistoryTurn] = Field(default_factory=list, max_length=20)
    reasoning_effort: Optional[str] = Field(default=None, description="low | medium | high (gpt-5 series only)")


class AskCitation(BaseModel):
    kind: str            # paper / concept
    ref: str             # e.g. "[[concept:abc-uuid]]"
    file_id: Optional[str] = None
    rel_path: Optional[str] = None
    title: Optional[str] = None


class AskTraceStep(BaseModel):
    """One reasoning step the cloud agent surfaced for the user. v1
    only emits a single "search" step + a "synthesis" step (no tool
    loop); v2 will add per-tool entries when we lift the desktop's
    ask_agent loop into the cloud."""

    step: int
    name: str            # search / synthesize / ...
    summary: str
    duration_ms: int


class AskTokens(BaseModel):
    prompt: int = 0
    completion: int = 0
    total: int = 0


class AskResponse(BaseModel):
    answer: str
    citations: list[AskCitation] = Field(default_factory=list)
    trace: list[AskTraceStep] = Field(default_factory=list)
    tokens: AskTokens = Field(default_factory=AskTokens)
    model: str
