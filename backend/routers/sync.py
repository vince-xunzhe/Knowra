"""Cloud-mode sync router: 3-step upload (prepare → PUTs → commit).

See docs/SYNC-PROTOCOL.md §2 for the full contract. Only mounted when
``KNOWRA_DEPLOY_MODE=cloud``; in local mode the router module exists
but ``main.py`` doesn't include it.

High-level flow:

  prepare:
    1. validate every row's user_id matches the caller's JWT
    2. write the whole payload to a new sync_sessions row (staging)
    3. for each wiki_files entry, compare incoming content_hash to the
       most recent committed wiki_files row → decide upload_required
       vs upload_skipped
    4. ask the storage backend to sign a PUT URL for each upload_required
       at storage_path = wiki/<user_id>/<rel_path>
    5. return sync_session_id + upload list + 10-min expiry

  commit:
    1. validate sync_session_id exists, belongs to caller, not expired,
       not already committed
    2. for every claimed-uploaded file, HEAD Storage and verify the
       content_hash matches the client's claim
    3. inside one transaction:
        a. upsert papers / knowledge_nodes / knowledge_edges / wiki_files
           from the session's staging payload (skipping rows the
           server already has a newer updated_at for; idempotency)
        b. apply deletions
        c. bump CloudRevision.revision
    4. cache the response on the session row so a repeated commit with
       the same session_id replays the same revision (idempotency)
    5. return revision + accepted counts + rejected list

Idempotency invariants:
  - Same prepare payload (device + since) → same sync_session_id
    (de-dup at the metadata-hash level)
  - Same commit on the same sync_session_id → same response
  - rows with older updated_at than what's already committed → silently
    dropped (not rejected; treated as "already up to date")
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from auth_deps import current_user
from cloud_models import (
    CloudDeletion,
    CloudKnowledgeEdge,
    CloudKnowledgeNode,
    CloudLLMCall,  # noqa: F401 (re-exported by import for migration tests)
    CloudPaper,
    CloudRevision,
    SyncSession,
    UserProfile,
    WikiFile,
)
from model_gateway.auth import AuthenticatedUser
from schemas.sync import (
    API_VERSION,
    CommitAccepted,
    CommitRejection,
    CommitRequest,
    CommitResponse,
    KnowledgeEdgeRow,
    KnowledgeNodeRow,
    PaperRow,
    PrepareRequest,
    PrepareResponse,
    SkippedUpload,
    UploadInstruction,
    ValidationError,
    WikiFileRow,
)
from services.storage import ascii_storage_key, get_storage

router = APIRouter(prefix="/api/sync", tags=["sync"])


# ── DB dependency ─────────────────────────────────────────────────────


def get_cloud_db():
    """Yield a Session against the cloud DB.

    In production this resolves to the Supabase Postgres engine; tests
    inject their own via FastAPI dependency_overrides. We don't import
    a default engine here — that's set up at the deploy mode boot."""
    raise RuntimeError(
        "get_cloud_db() must be overridden via app.dependency_overrides "
        "for tests, or replaced by the real cloud engine at boot"
    )


# ── helpers ───────────────────────────────────────────────────────────


PREPARE_TTL_SECONDS = 600  # 10 minutes


def _to_utc(dt: datetime) -> datetime:
    """Coerce a possibly-naive datetime to UTC-aware so comparisons
    across DB-loaded (naive on SQLite) and client-sent (aware) values
    don't trip ``TypeError: can't compare offset-naive and
    offset-aware``. Naive values are treated as already UTC, which
    matches the rest of the codebase's storage convention."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _err(status_code: int, code: str, message: str, **details) -> HTTPException:
    """Raise an HTTPException with the unified error envelope."""
    payload = {"error": code, "message": message}
    if details:
        payload["details"] = details
    return HTTPException(status_code=status_code, detail=payload)


def _storage_path(user_id: str, rel_path: str) -> str:
    """Path WITHIN the wiki bucket: ``<user_id>/<rel_path>``.

    Do NOT prepend the bucket name — Supabase's REST API for signing
    URLs already takes the bucket separately (see
    ``SupabaseStorage.sign_upload`` which assembles
    ``/storage/v1/object/upload/sign/{bucket}/{path}``). Earlier this
    function returned ``wiki/<user_id>/...`` and the URL came out as
    ``.../sign/wiki/wiki/<user_id>/...`` (400 Bad Request).

    The Storage RLS policy on ``storage.objects`` reads
    ``split_part(name, '/', 1) = auth.uid()::text`` to enforce per-user
    isolation against this same convention.
    """
    return f"{user_id}/{ascii_storage_key(rel_path)}"


def _validate_user_id(row, expected_user_id: str, table: str) -> Optional[ValidationError]:
    if row.user_id != expected_user_id:
        return ValidationError(
            table=table,
            id=row.id,
            reason=f"user_id mismatch: row claims {row.user_id!r} but caller is {expected_user_id!r}",
            code="USER_ID_MISMATCH",
        )
    return None


def _decide_uploads(
    db: Session, *, user: AuthenticatedUser, rows: list[WikiFileRow]
) -> tuple[list[WikiFileRow], list[SkippedUpload]]:
    """For each incoming wiki file, decide upload-required vs skipped
    by comparing content_hash to the most recent committed wiki_file
    at the same (user_id, rel_path)."""
    if not rows:
        return [], []
    rel_paths = [r.rel_path for r in rows]
    existing = (
        db.query(WikiFile.rel_path, WikiFile.content_hash)
        .filter(WikiFile.user_id == user.user_id)
        .filter(WikiFile.rel_path.in_(rel_paths))
        .all()
    )
    seen = {rp: ch for rp, ch in existing}
    required: list[WikiFileRow] = []
    skipped: list[SkippedUpload] = []
    for row in rows:
        if seen.get(row.rel_path) == row.content_hash:
            skipped.append(SkippedUpload(
                rel_path=row.rel_path,
                reason="content_hash unchanged",
            ))
        else:
            required.append(row)
    return required, skipped


# ── prepare ───────────────────────────────────────────────────────────


@router.post("/prepare", response_model=PrepareResponse)
def prepare(
    body: PrepareRequest,
    db: Session = Depends(get_cloud_db),
    user: AuthenticatedUser = Depends(current_user),
) -> PrepareResponse:
    if body.api_version != API_VERSION:
        raise _err(
            status.HTTP_400_BAD_REQUEST,
            "version_mismatch",
            f"unsupported api_version {body.api_version!r}; expected {API_VERSION!r}",
        )

    # ── per-row user_id validation ────────────────────────────────
    errs: list[ValidationError] = []
    for r in body.tables.papers:
        e = _validate_user_id(r, user.user_id, "papers")
        if e: errs.append(e)
    for r in body.tables.knowledge_nodes:
        e = _validate_user_id(r, user.user_id, "knowledge_nodes")
        if e: errs.append(e)
    for r in body.tables.knowledge_edges:
        e = _validate_user_id(r, user.user_id, "knowledge_edges")
        if e: errs.append(e)
    for r in body.tables.wiki_files:
        e = _validate_user_id(r, user.user_id, "wiki_files")
        if e: errs.append(e)

    # ── decide which wiki files need upload ───────────────────────
    required, skipped = _decide_uploads(db, user=user, rows=body.tables.wiki_files)

    # ── stage everything in a new session ─────────────────────────
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=PREPARE_TTL_SECONDS)

    # mode='json' converts datetime → ISO 8601 string so the staging
    # dict survives a JSON round-trip through the sync_sessions table.
    staging: dict[str, Any] = {
        "device_id": body.device_id,
        "since": body.since.isoformat() if body.since else None,
        "tables": {
            "papers": [r.model_dump(mode="json") for r in body.tables.papers],
            "knowledge_nodes": [r.model_dump(mode="json") for r in body.tables.knowledge_nodes],
            "knowledge_edges": [r.model_dump(mode="json") for r in body.tables.knowledge_edges],
            "wiki_files": [r.model_dump(mode="json") for r in body.tables.wiki_files],
        },
        "deletions": body.deletions.model_dump(mode="json"),
    }
    uploads_pending = [
        {"rel_path": r.rel_path, "content_hash": r.content_hash}
        for r in required
    ]

    session = SyncSession(
        user_id=user.user_id,
        device_id=body.device_id,
        status="pending",
        staging=staging,
        uploads_pending=uploads_pending,
        expires_at=expires_at,
    )
    db.add(session)
    db.flush()

    # ── sign PUT URLs (parallelized) ──────────────────────────────
    # Each sign_upload is one HTTPX POST to Supabase Storage. Serial
    # signing of 121 files across NRT→Sydney is ~30s and trips the
    # frontend's prepare timeout. ThreadPoolExecutor with 16 workers
    # brings it to ~3-5s. SupabaseStorage's httpx client is
    # thread-safe (each call is a one-shot request), so concurrent
    # signing is safe.
    storage = get_storage()
    instructions: list[UploadInstruction] = []
    if required:
        from concurrent.futures import ThreadPoolExecutor

        def _sign_one(row):
            signed = storage.sign_upload(
                storage_path=_storage_path(user.user_id, row.rel_path),
                ttl_seconds=PREPARE_TTL_SECONDS,
            )
            return UploadInstruction(
                rel_path=row.rel_path,
                upload_url=signed.url,
                method=signed.method,
                headers=signed.header_dict,
            )

        with ThreadPoolExecutor(max_workers=min(16, len(required))) as pool:
            # Preserve input order so the client's progress UI stays
            # predictable.
            instructions = list(pool.map(_sign_one, required))

    db.commit()

    return PrepareResponse(
        sync_session_id=str(session.id),
        expires_at=expires_at,
        uploads_required=instructions,
        uploads_skipped=skipped,
        validation_errors=errs,
    )


# ── commit ────────────────────────────────────────────────────────────


def _upsert(db: Session, model, payload: dict, *, conflict_cols: tuple[str, ...]) -> str:
    """Single-row upsert. Retained for narrow call sites; the commit
    handler uses ``_bulk_upsert`` instead because doing N of these
    against Supabase costs N × ~150ms round trips (4000+ edges took
    10+ minutes pre-batching).

    Skipped happens when the incoming row's updated_at is older than
    the existing row's — used so a stale push doesn't clobber newer
    server state. Idempotency.
    """
    existing = db.query(model).filter(model.id == payload["id"]).one_or_none()
    incoming_updated_at = payload.get("updated_at")
    if existing is None:
        clean = {k: v for k, v in payload.items() if v is not None}
        db.add(model(**clean))
        return "inserted"
    server_updated = existing.updated_at
    if (
        incoming_updated_at is not None
        and server_updated is not None
        and server_updated >= incoming_updated_at
    ):
        return "skipped"
    for k, v in payload.items():
        if k in {"id"}:
            continue
        if v is None:
            continue
        setattr(existing, k, v)
    return "updated"


def _bulk_upsert(db: Session, model, payloads: list[dict]) -> int:
    """Same semantics as ``_upsert`` but with ONE pre-fetch SELECT for
    the whole batch, then in-memory partitioning into insert / update /
    skip.

    Pre-optimization commit of 38 papers + 639 nodes + 4356 edges + 40
    wiki rows did 5073 sequential SELECTs to Supabase Postgres in
    Sydney — at ~150ms RTT each that's 12+ minutes, well past the
    desktop's 5-min axios timeout. With this helper each table costs
    one SELECT + one batched INSERT/UPDATE flush ≈ a few seconds total.

    Returns the count of rows that were either inserted or updated
    (skipped rows aren't counted, matching the per-table ``accepted``
    semantics in the caller).
    """
    if not payloads:
        return 0
    # Key everything by ``str(id)``. ORM rows may come back with id as
    # ``uuid.UUID`` (Postgres native type) or ``str`` (SQLite / after
    # the UUID-as-text cast); incoming payloads always have string ids
    # (Pydantic schemas declare ``id: str``). Normalising both sides to
    # ``str`` before dict lookup makes the path safe regardless of
    # dialect, even if a future column slips through without the
    # psycopg2 cast in ``cloud_db._register_uuid_as_text``.
    ids = [str(p["id"]) for p in payloads if p.get("id") is not None]
    existing: dict = {}
    if ids:
        existing = {
            str(row.id): row
            for row in db.query(model).filter(model.id.in_(ids)).all()
        }
    accepted = 0
    for payload in payloads:
        pid = payload.get("id")
        ex = existing.get(str(pid)) if pid is not None else None
        if ex is None:
            clean = {k: v for k, v in payload.items() if v is not None}
            obj = model(**clean)
            db.add(obj)
            # Track the just-added object so a duplicate id later in the
            # same payload (rare — happens when client-side dedup is
            # incomplete) doesn't try to INSERT the same PK twice.
            if pid is not None:
                existing[str(pid)] = obj
            accepted += 1
            continue
        incoming_ts = payload.get("updated_at")
        server_ts = getattr(ex, "updated_at", None)
        # Normalize both to UTC-aware to avoid ``TypeError: can't
        # compare offset-naive and offset-aware datetimes``. SQLite
        # stores naive; Postgres + clients send aware. Treating naive
        # as UTC matches the rest of the codebase's convention.
        if (
            incoming_ts is not None and server_ts is not None
            and _to_utc(server_ts) >= _to_utc(incoming_ts)
        ):
            if model is CloudPaper and _backfill_empty_paper_metadata(ex, payload):
                accepted += 1
            continue
        for k, v in payload.items():
            if k == "id" or v is None:
                continue
            setattr(ex, k, v)
        accepted += 1
    return accepted


def _backfill_empty_paper_metadata(existing: CloudPaper, payload: dict) -> bool:
    """Fill metadata fields added after the original cloud row was synced.

    Older desktop builds stamped paper rows with processed_at. Team/category
    model fields can be derived later without changing processed_at, causing
    the cloud upsert to skip the row as "stale" and leaving mobile grouped
    under fallback lanes such as "others". When the server value is empty and
    the incoming full snapshot has a value, accept that narrow backfill even if
    the row timestamp is older.
    """
    changed = False
    for field in (
        "paper_category_model",
        "paper_category_override",
        "paper_team_model",
        "paper_team_override",
    ):
        incoming = payload.get(field)
        current = getattr(existing, field, None)
        if incoming not in (None, "") and current in (None, ""):
            setattr(existing, field, incoming)
            changed = True
    if changed:
        existing.updated_at = datetime.now(timezone.utc)
    return changed


def _to_paper_dict(row: PaperRow) -> dict:
    d = row.model_dump()
    d.pop("api_version", None)
    return d


def _to_node_dict(row: KnowledgeNodeRow) -> dict:
    d = row.model_dump()
    d.pop("api_version", None)
    return d


def _to_edge_dict(row: KnowledgeEdgeRow) -> dict:
    d = row.model_dump()
    d.pop("api_version", None)
    return d


def _to_wiki_dict(row: WikiFileRow, *, storage_path: str) -> dict:
    d = row.model_dump()
    d["storage_path"] = storage_path
    d.pop("api_version", None)
    return d


def _bump_revision(db: Session, user_id: str) -> int:
    rev = db.query(CloudRevision).filter(CloudRevision.user_id == user_id).one_or_none()
    if rev is None:
        rev = CloudRevision(user_id=user_id, revision=1)
        db.add(rev)
        return 1
    rev.revision += 1
    rev.updated_at = datetime.now(timezone.utc)
    return rev.revision


@router.post("/commit", response_model=CommitResponse)
def commit(
    body: CommitRequest,
    db: Session = Depends(get_cloud_db),
    user: AuthenticatedUser = Depends(current_user),
) -> CommitResponse:
    if body.api_version != API_VERSION:
        raise _err(
            status.HTTP_400_BAD_REQUEST,
            "version_mismatch",
            f"unsupported api_version {body.api_version!r}; expected {API_VERSION!r}",
        )

    # ── look up the session ───────────────────────────────────────
    session = db.query(SyncSession).filter(SyncSession.id == body.sync_session_id).one_or_none()
    if session is None:
        raise _err(status.HTTP_404_NOT_FOUND, "not_found", "sync_session_id not found")
    # ``session.user_id`` comes back as a ``uuid.UUID`` instance from
    # Postgres (the column type is UUID), while ``user.user_id`` is a
    # plain string off the JWT's ``sub`` claim. ``UUID('x') == 'x'`` is
    # always False — we have to compare as strings, otherwise EVERY
    # commit returns 403 in cloud mode. (SQLite tests didn't catch this
    # because SQLite stores UUIDs as TEXT and returns them as str.)
    if str(session.user_id) != str(user.user_id):
        raise _err(
            status.HTTP_403_FORBIDDEN, "forbidden",
            "sync_session belongs to a different user",
        )

    now = datetime.now(timezone.utc)
    expires_at = session.expires_at
    if expires_at is not None and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at and expires_at < now:
        raise _err(status.HTTP_410_GONE, "expired", "sync_session has expired; rerun prepare")

    # ── replay cached response on duplicate commit ────────────────
    if session.status == "committed" and session.committed_response:
        cached = session.committed_response
        return CommitResponse(
            revision=cached["revision"],
            accepted=CommitAccepted(**cached["accepted"]),
            rejected=[CommitRejection(**r) for r in cached.get("rejected", [])],
            server_now=now,
        )

    # ── HEAD-check uploaded files ─────────────────────────────────
    rejected: list[CommitRejection] = []
    storage = get_storage()
    uploaded_by_path = {u.rel_path: u.content_hash for u in body.uploaded}
    expected_uploads = {
        u["rel_path"]: u["content_hash"]
        for u in (session.uploads_pending or [])
    }

    # Every pending upload must be claimed. Files the client claimed
    # but weren't expected are silently ignored (forward-compat).
    for rel_path, expected_hash in expected_uploads.items():
        claimed = uploaded_by_path.get(rel_path)
        if claimed is None:
            rejected.append(CommitRejection(
                table="wiki_files",
                rel_path=rel_path,
                reason="upload was expected but not claimed in commit",
                code="UPLOAD_MISSING",
            ))
            continue
        if claimed != expected_hash:
            rejected.append(CommitRejection(
                table="wiki_files",
                rel_path=rel_path,
                reason="content_hash claimed does not match prepare",
                code="HASH_CLAIM_MISMATCH",
            ))
            continue
        obj = storage.head(_storage_path(user.user_id, rel_path))
        if obj is None:
            rejected.append(CommitRejection(
                table="wiki_files",
                rel_path=rel_path,
                reason="storage HEAD returned no object",
                code="UPLOAD_MISSING_AT_STORAGE",
            ))
            continue
        # Storage backends that can return a sha256 (e.g. InMemoryStorage
        # in tests) get strict verification. Backends that can't supply
        # one (e.g. Supabase Storage returns etag/md5 only) return
        # ``content_hash=""`` and we skip — the client-supplied hash
        # already in wiki_files.content_hash is the source of truth.
        if obj.content_hash and obj.content_hash != expected_hash:
            rejected.append(CommitRejection(
                table="wiki_files",
                rel_path=rel_path,
                reason="storage object content_hash mismatch",
                code="HASH_MISMATCH",
            ))

    # Files that failed their storage check are EXCLUDED from this commit
    # (their bytes aren't confirmed in storage) but must NOT abort the
    # whole sync. Papers / nodes / edges and every *good* wiki file still
    # commit; the rejected list is returned so the client can surface +
    # retry them on the next run.
    #
    # Previously ANY single rejection returned ``revision 0`` and committed
    # NOTHING — so one unreachable wiki file silently zeroed out the entire
    # sync (papers, nodes, edges included). That made a partial storage
    # hiccup look like a successful no-op on the desktop.
    rejected_paths = {r.rel_path for r in rejected if r.rel_path}

    # ── apply staging → canonical tables ──────────────────────────
    staging = session.staging or {}
    tables = staging.get("tables", {})
    accepted = CommitAccepted()

    # Order matters for FK shape: papers → nodes → edges → wiki_files.
    # Bulk variants here cut N round trips per table down to ~1 — see
    # ``_bulk_upsert`` docstring for the perf rationale.
    # Every flush below is wrapped so a constraint failure (e.g. a stale
    # client payload that violates a UNIQUE) gives a structured 409
    # instead of a CORS-stripped 500. The single outer ``db.commit()``
    # at the end provides the final atomicity.
    try:
        paper_payloads = [
            _to_paper_dict(PaperRow.model_validate(raw))
            for raw in tables.get("papers", [])
        ]
        accepted.papers = _bulk_upsert(db, CloudPaper, paper_payloads)

        node_payloads = [
            _to_node_dict(KnowledgeNodeRow.model_validate(raw))
            for raw in tables.get("knowledge_nodes", [])
        ]
        accepted.knowledge_nodes = _bulk_upsert(db, CloudKnowledgeNode, node_payloads)

        # FLUSH the nodes BEFORE inserting edges. The cloud schema's
        # ``edge_user_consistency_check`` trigger (supabase/migrations/
        # 0003_knowledge.sql L82-105) is BEFORE INSERT and does a
        # SELECT on knowledge_nodes — without this flush the nodes are
        # only pending in the SQLAlchemy session and the trigger sees
        # nothing → ``edge references non-existent node``.
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise _err(
            status.HTTP_409_CONFLICT, "conflict",
            "database integrity error during papers/nodes flush",
            error=str(exc.orig),
        )

    try:
        edge_payloads = [
            _to_edge_dict(KnowledgeEdgeRow.model_validate(raw))
            for raw in tables.get("knowledge_edges", [])
        ]
        # Edges are fully-DERIVED data: the desktop's similarity rebuild
        # periodically deletes + recreates them with FRESH UUIDs. So the
        # cloud may hold an edge with the same (source, target, relation)
        # triple under an OLD id, while the snapshot carries it under a
        # NEW id. ``_bulk_upsert`` matches by id, so it would try to INSERT
        # the new-id row — violating UNIQUE(user_id, source_id, target_id,
        # relation_type) and 409-ing the whole commit. Treat the snapshot
        # as source-of-truth: replace this user's edge set wholesale (one
        # bulk DELETE + bulk INSERT). Edges have no inbound FKs, and the
        # nodes they reference were already flushed above, so the
        # edge_user_consistency trigger is satisfied. Only replace when the
        # snapshot actually carries edges, so an edge-less partial snapshot
        # never wipes the cloud graph.
        if edge_payloads:
            db.query(CloudKnowledgeEdge).filter(
                CloudKnowledgeEdge.user_id == user.user_id
            ).delete(synchronize_session=False)
            db.flush()
            accepted.knowledge_edges = _bulk_upsert(
                db, CloudKnowledgeEdge, edge_payloads
            )
        else:
            accepted.knowledge_edges = 0

        wiki_payloads = []
        for raw in tables.get("wiki_files", []):
            row = WikiFileRow.model_validate(raw)
            # Skip files whose bytes failed the storage HEAD-check above —
            # committing the row would point the mobile snapshot at a
            # missing object. They stay "required" and retry next sync.
            if row.rel_path in rejected_paths:
                continue
            wiki_payloads.append(_to_wiki_dict(
                row, storage_path=_storage_path(user.user_id, row.rel_path)))
        accepted.wiki_files = _bulk_upsert(db, WikiFile, wiki_payloads)
        # Force the flush here so any constraint failure on the second
        # half of the commit (edges / wiki) surfaces as a structured
        # 409 too, not the bare 500 + CORS-stripped error the browser
        # was showing.
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise _err(
            status.HTTP_409_CONFLICT, "conflict",
            "database integrity error during edges/wiki flush",
            error=str(exc.orig),
        )

    # ── deletions ─────────────────────────────────────────────────
    # Each deletion drops the row from the canonical table AND writes a
    # tombstone to cloud_deletions so the mobile snapshot endpoint can
    # tell clients "you previously synced this id; please evict it."
    deletions = staging.get("deletions", {}) or {}

    def _record_tombstone(table_name: str, row_id: str) -> None:
        # Upsert: if a previous deletion of the same row exists, refresh
        # its deleted_at so the snapshot since-cursor reflects the latest
        # decision (e.g. user re-created then re-deleted).
        existing = db.query(CloudDeletion).filter(
            CloudDeletion.user_id == user.user_id,
            CloudDeletion.table_name == table_name,
            CloudDeletion.row_id == row_id,
        ).one_or_none()
        if existing is None:
            db.add(CloudDeletion(
                user_id=user.user_id,
                table_name=table_name,
                row_id=row_id,
                deleted_at=now,
            ))
        else:
            existing.deleted_at = now

    for pid in deletions.get("papers", []):
        deleted = db.query(CloudPaper).filter(
            CloudPaper.id == pid, CloudPaper.user_id == user.user_id
        ).delete(synchronize_session=False)
        if deleted:
            _record_tombstone("papers", pid)
    for nid in deletions.get("knowledge_nodes", []):
        deleted = db.query(CloudKnowledgeNode).filter(
            CloudKnowledgeNode.id == nid, CloudKnowledgeNode.user_id == user.user_id
        ).delete(synchronize_session=False)
        if deleted:
            _record_tombstone("knowledge_nodes", nid)
    for eid in deletions.get("knowledge_edges", []):
        deleted = db.query(CloudKnowledgeEdge).filter(
            CloudKnowledgeEdge.id == eid, CloudKnowledgeEdge.user_id == user.user_id
        ).delete(synchronize_session=False)
        if deleted:
            _record_tombstone("knowledge_edges", eid)
    for wid in deletions.get("wiki_files", []):
        deleted = db.query(WikiFile).filter(
            WikiFile.id == wid, WikiFile.user_id == user.user_id
        ).delete(synchronize_session=False)
        if deleted:
            _record_tombstone("wiki_files", wid)

    # ── bump revision + mark session committed ───────────────────
    revision = _bump_revision(db, user.user_id)

    # Update user_profiles.last_desktop_sync_at if present.
    profile = db.query(UserProfile).filter(UserProfile.user_id == user.user_id).one_or_none()
    if profile is None:
        profile = UserProfile(
            user_id=user.user_id,
            desktop_first_seen=now,
            last_desktop_sync_at=now,
        )
        db.add(profile)
    else:
        profile.last_desktop_sync_at = now

    session.status = "committed"
    session.committed_at = now
    session.committed_response = {
        "revision": revision,
        "accepted": accepted.model_dump(),
        "rejected": [r.model_dump() for r in rejected],
    }

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise _err(
            status.HTTP_409_CONFLICT, "conflict",
            "database integrity error on commit", error=str(exc.orig),
        )

    return CommitResponse(
        revision=revision,
        accepted=accepted,
        rejected=rejected,
        server_now=now,
    )
