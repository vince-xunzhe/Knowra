"""Mobile-facing /api/cloud/* router.

Read endpoints the React Native client hits to browse the user's
synced knowledge base. No writes — anything that mutates state goes
through the desktop's sync push flow. (Ask is the future exception;
it's its own router.)

All endpoints:
  - require JWT auth (current_user dependency)
  - scope every query by user_id (RLS is the defense-in-depth, but we
    don't rely on it; SQLite tests don't have RLS)
  - return the unified error envelope on failures

Endpoints:

  GET  /api/cloud/me                  — profile + corpus stats
  GET  /api/cloud/snapshot[?since=]   — bulk pull with optional cursor
  GET  /api/cloud/wiki/{file_id}      — single-file 302 to Storage
  POST /api/cloud/wiki/search         — title-LIKE search v1
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import RedirectResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from auth_deps import current_user
from cloud_models import (
    CloudDeletion,
    CloudKnowledgeEdge,
    CloudKnowledgeNode,
    CloudPaper,
    CloudRevision,
    SyncSession,  # noqa: F401 (kept in scope so SQLAlchemy registers)
    UserProfile,
    WikiFile,
)
from model_gateway.auth import AuthenticatedUser
from routers.sync import get_cloud_db
from schemas.cloud import (
    AskCitation,
    AskRequest,
    AskResponse,
    AskTokens,
    AskTraceStep,
    DeletedSince,
    MeResponse,
    MeStats,
    SnapshotResponse,
    WikiFileEntry,
    WikiSearchHit,
    WikiSearchRequest,
    WikiSearchResponse,
)
from schemas.sync import KnowledgeEdgeRow, KnowledgeNodeRow, PaperRow
from services.cloud_ask import (
    AskError,
    RateLimited,
    UpstreamFailure,
    run_cloud_ask,
)
from services.storage import get_storage

router = APIRouter(prefix="/api/cloud", tags=["cloud"])


SNAPSHOT_DOWNLOAD_TTL = 600  # 10 minutes per signed URL


def _err(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"error": code, "message": message},
    )


def _wiki_storage_path(user_id: str, rel_path: str) -> str:
    return f"wiki/{user_id}/{rel_path}"


def _current_revision(db: Session, user_id: str) -> int:
    rev = db.query(CloudRevision).filter(CloudRevision.user_id == user_id).one_or_none()
    return int(rev.revision) if rev else 0


# ── /me ───────────────────────────────────────────────────────────────


@router.get("/me", response_model=MeResponse)
def me(
    db: Session = Depends(get_cloud_db),
    user: AuthenticatedUser = Depends(current_user),
) -> MeResponse:
    profile = (
        db.query(UserProfile)
        .filter(UserProfile.user_id == user.user_id)
        .one_or_none()
    )
    papers = db.query(func.count(CloudPaper.id)).filter(
        CloudPaper.user_id == user.user_id
    ).scalar() or 0
    concepts = db.query(func.count(CloudKnowledgeNode.id)).filter(
        CloudKnowledgeNode.user_id == user.user_id,
        CloudKnowledgeNode.node_type != "paper",
    ).scalar() or 0
    edges = db.query(func.count(CloudKnowledgeEdge.id)).filter(
        CloudKnowledgeEdge.user_id == user.user_id
    ).scalar() or 0
    wiki_count = db.query(func.count(WikiFile.id)).filter(
        WikiFile.user_id == user.user_id
    ).scalar() or 0
    wiki_size = db.query(func.coalesce(func.sum(WikiFile.size_bytes), 0)).filter(
        WikiFile.user_id == user.user_id
    ).scalar() or 0

    return MeResponse(
        user_id=user.user_id,
        email=user.email,
        display_name=profile.display_name if profile else None,
        stats=MeStats(
            papers=int(papers),
            concepts=int(concepts),
            edges=int(edges),
            wiki_files=int(wiki_count),
            last_desktop_sync_at=profile.last_desktop_sync_at if profile else None,
            wiki_size_bytes=int(wiki_size),
        ),
    )


# ── /snapshot ─────────────────────────────────────────────────────────


@router.get("/snapshot", response_model=SnapshotResponse)
def snapshot(
    since: Optional[datetime] = Query(
        default=None,
        description="ISO 8601 timestamp; only rows updated after this "
                    "are returned. Null = full snapshot.",
    ),
    db: Session = Depends(get_cloud_db),
    user: AuthenticatedUser = Depends(current_user),
) -> SnapshotResponse:
    storage = get_storage()
    now = datetime.now(timezone.utc)
    revision = _current_revision(db, user.user_id)

    # ── upserts since cursor ──────────────────────────────────────
    def _filter_user_since(query, model):
        q = query.filter(model.user_id == user.user_id)
        if since is not None:
            q = q.filter(model.updated_at > since)
        return q

    papers = _filter_user_since(db.query(CloudPaper), CloudPaper).all()
    nodes = _filter_user_since(db.query(CloudKnowledgeNode), CloudKnowledgeNode).all()
    edges = _filter_user_since(db.query(CloudKnowledgeEdge), CloudKnowledgeEdge).all()
    wiki_rows = _filter_user_since(db.query(WikiFile), WikiFile).all()

    # ── augment wiki files with signed download URLs ──────────────
    # Sign all URLs in parallel — serial signing of 40+ files
    # NRT→Sydney took 5-10s and on first sync we'd flicker through a
    # noticeable loading state.  ThreadPoolExecutor + httpx (the
    # underlying sign_download client) is fine to call concurrently.
    from concurrent.futures import ThreadPoolExecutor

    def _sign_one(w):
        return w, storage.sign_download(
            storage_path=w.storage_path,
            ttl_seconds=SNAPSHOT_DOWNLOAD_TTL,
        )

    wiki_entries: list[WikiFileEntry] = []
    if wiki_rows:
        with ThreadPoolExecutor(max_workers=min(16, len(wiki_rows))) as pool:
            for w, signed in pool.map(_sign_one, wiki_rows):
                # str() everywhere a UUID column would otherwise leak
                # through — Pydantic's ``id: str`` declaration won't
                # auto-coerce uuid.UUID instances (raises
                # ``Input should be a valid string``).
                wiki_entries.append(WikiFileEntry(
                    id=str(w.id),
                    user_id=str(w.user_id),
                    kind=w.kind,
                    rel_path=w.rel_path,
                    content_hash=w.content_hash,
                    size_bytes=w.size_bytes,
                    title=w.title,
                    aliases=w.aliases or [],
                    compiled_at=w.compiled_at,
                    paper_id=str(w.paper_id) if w.paper_id else None,
                    concept_id=str(w.concept_id) if w.concept_id else None,
                    updated_at=w.updated_at,
                    download_url=signed.url,
                    download_url_expires_at=signed.expires_at,
                ))

    # ── deletions since cursor ────────────────────────────────────
    deletions_query = db.query(CloudDeletion).filter(
        CloudDeletion.user_id == user.user_id
    )
    if since is not None:
        deletions_query = deletions_query.filter(CloudDeletion.deleted_at > since)
    tombstones = deletions_query.all()

    bucket: dict[str, list[str]] = {
        "papers": [],
        "knowledge_nodes": [],
        "knowledge_edges": [],
        "wiki_files": [],
    }
    for t in tombstones:
        if t.table_name in bucket:
            bucket[t.table_name].append(t.row_id)

    # ── pack response ─────────────────────────────────────────────
    # Postgres returns UUID columns as ``uuid.UUID`` instances, but
    # the row Pydantic schemas declare ``id: str`` / ``user_id: str``
    # (mobile + desktop expect plain strings on the wire). Pydantic
    # 2.x refuses to auto-coerce UUID → str, so we cast at the
    # serialization boundary. Wrap in ``_s()`` for brevity since
    # nearly every UUID field needs it.
    def _s(v):
        return None if v is None else str(v)

    def _serialize_papers(rows):
        return [
            PaperRow(
                id=_s(r.id),
                user_id=_s(r.user_id),
                filepath=r.filepath,
                filename=r.filename,
                file_hash=r.file_hash,
                title=r.title,
                authors=r.authors,
                num_pages=r.num_pages,
                processed=r.processed,
                processed_at=r.processed_at,
                extraction_model=r.extraction_model,
                paper_category_model=r.paper_category_model,
                paper_category_override=r.paper_category_override,
                paper_team_model=r.paper_team_model,
                paper_team_override=r.paper_team_override,
                raw_llm_response=r.raw_llm_response,
                notes=r.notes,
                error=r.error,
                processing_status=r.processing_status,
                retry_count=r.retry_count,
                last_error_stage=r.last_error_stage,
                last_error_reason=r.last_error_reason,
                last_error_recoverable=r.last_error_recoverable,
                legacy_id=r.legacy_id,
                created_at=r.created_at,
                updated_at=r.updated_at,
            )
            for r in rows
        ]

    def _serialize_nodes(rows):
        return [
            KnowledgeNodeRow(
                id=_s(r.id),
                user_id=_s(r.user_id),
                title=r.title,
                content=r.content,
                node_type=r.node_type,
                node_origin=r.node_origin,
                hidden=r.hidden,
                promotion_status=r.promotion_status,
                promoted_by=r.promoted_by,
                promotion_reason=r.promotion_reason,
                last_promotion_eval_at=r.last_promotion_eval_at,
                tags=r.tags,
                embedding=r.embedding,
                # source_paper_ids is JSONB → already a list[str] in
                # Postgres, but defensive str() in case the migrator
                # left any int-shaped entries in legacy rows.
                source_paper_ids=[str(x) for x in (r.source_paper_ids or [])],
                legacy_id=r.legacy_id,
                created_at=r.created_at,
                updated_at=r.updated_at,
            )
            for r in rows
        ]

    def _serialize_edges(rows):
        return [
            KnowledgeEdgeRow(
                id=_s(r.id),
                user_id=_s(r.user_id),
                source_id=_s(r.source_id),
                target_id=_s(r.target_id),
                relation_type=r.relation_type,
                weight=r.weight,
                legacy_id=r.legacy_id,
                created_at=r.created_at,
                updated_at=r.updated_at,
            )
            for r in rows
        ]

    return SnapshotResponse(
        revision=revision,
        server_now=now,
        papers=_serialize_papers(papers),
        knowledge_nodes=_serialize_nodes(nodes),
        knowledge_edges=_serialize_edges(edges),
        wiki_files=wiki_entries,
        deleted_since=DeletedSince(**bucket),
    )


# ── /wiki/{file_id} (302 redirect to Storage) ─────────────────────────


@router.get("/wiki/{file_id}")
def wiki_file(
    file_id: str,
    db: Session = Depends(get_cloud_db),
    user: AuthenticatedUser = Depends(current_user),
):
    """Single-file download. Returns a 302 redirect to a short-lived
    pre-signed URL at Storage so the response itself doesn't proxy the
    file content.

    Note: we deliberately do NOT verify the calling user has read
    access via RLS — the WikiFile lookup already filters by user_id,
    so a foreign file_id returns 404 (not 403). This avoids leaking
    "this id exists but isn't yours" via timing."""
    row = (
        db.query(WikiFile)
        .filter(WikiFile.id == file_id, WikiFile.user_id == user.user_id)
        .one_or_none()
    )
    if row is None:
        raise _err(status.HTTP_404_NOT_FOUND, "not_found", "wiki file not found")
    signed = get_storage().sign_download(
        storage_path=row.storage_path,
        ttl_seconds=SNAPSHOT_DOWNLOAD_TTL,
    )
    return RedirectResponse(url=signed.url, status_code=status.HTTP_302_FOUND)


# ── /wiki/search ──────────────────────────────────────────────────────


@router.post("/wiki/search", response_model=WikiSearchResponse)
def wiki_search(
    body: WikiSearchRequest,
    db: Session = Depends(get_cloud_db),
    user: AuthenticatedUser = Depends(current_user),
) -> WikiSearchResponse:
    """Simple title-LIKE search. v2 will swap for Postgres FTS."""
    q = db.query(WikiFile).filter(WikiFile.user_id == user.user_id)
    if body.kind:
        q = q.filter(WikiFile.kind == body.kind)
    needle = f"%{body.q.lower()}%"
    q = q.filter(func.lower(func.coalesce(WikiFile.title, "")).like(needle))
    q = q.limit(body.limit)
    rows = q.all()
    hits = [
        WikiSearchHit(
            id=r.id,
            kind=r.kind,
            rel_path=r.rel_path,
            title=r.title,
            snippet=None,
        )
        for r in rows
    ]
    return WikiSearchResponse(query=body.q, hits=hits)


# ── /ask (mobile cross-wiki Q&A) ───────────────────────────────────────


@router.post("/ask", response_model=AskResponse)
def ask(
    body: AskRequest,
    db: Session = Depends(get_cloud_db),
    user: AuthenticatedUser = Depends(current_user),
) -> AskResponse:
    """Mobile Ask endpoint. The user's OpenAI key flows through this
    handler exactly once and is never persisted. See
    docs/SYNC-PROTOCOL.md §4 for the full contract."""
    try:
        result = run_cloud_ask(
            db,
            user_id=user.user_id,
            question=body.question,
            openai_api_key=body.openai_api_key,
            model=body.model,
            history=body.history,
            reasoning_effort=body.reasoning_effort,
        )
    except RateLimited as exc:
        raise HTTPException(
            status_code=exc.http_status,
            detail={
                "error": exc.error_code,
                "message": str(exc),
                "details": {"retry_after_seconds": exc.retry_after_seconds},
            },
            headers={"Retry-After": str(exc.retry_after_seconds)},
        )
    except UpstreamFailure as exc:
        raise HTTPException(
            status_code=exc.http_status,
            detail={"error": exc.error_code, "message": str(exc)},
        )
    except AskError as exc:
        raise HTTPException(
            status_code=exc.http_status,
            detail={"error": exc.error_code, "message": str(exc)},
        )

    return AskResponse(
        answer=result["answer"],
        citations=result["citations"],
        trace=result["trace"],
        tokens=result["tokens"],
        model=result["model"],
    )
