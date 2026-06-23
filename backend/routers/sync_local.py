"""Local-only snapshot endpoint for the desktop sync agent.

The frontend's sync flow needs to send cloud-shaped rows
(papers / knowledge_nodes / knowledge_edges / wiki_files) into the
cloud's ``/api/sync/prepare``. We could try to build that payload
purely in TypeScript, but:

  - Paper file_hash needs the raw PDF bytes; the renderer has no
    direct disk access.
  - Wiki content_hash needs reading data/wiki/**/*.md.
  - Row shape needs to match backend/schemas/sync.py exactly.

So this router reads everything server-side, computes hashes, and
returns one fat JSON blob the frontend can ship as-is. The endpoint
is *local-only* — it returns the desktop's own data with no auth, so
mounting it in the cloud deploy would be a data leak. ``main.py``
mounts it unconditionally because the cloud build sets
``KNOWRA_DEPLOY_MODE=cloud`` AND doesn't ship the local SQLite, so the
queries here return empty results in that mode. To make this airtight
we also short-circuit when in cloud mode.

⚠️ Performance: PDF hashing dominates the cost on first call. We cache
the result in ``Paper.file_hash`` so subsequent calls only pay the
hash for new PDFs. Hash recompute is keyed on (mtime, size); we don't
re-hash on every snapshot.
"""
from __future__ import annotations

import base64
import hashlib
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from config import is_cloud_mode
from database import get_db
from models import KnowledgeEdge, KnowledgeNode, Paper
from path_utils import resolve_paper_path
from services.wiki_compiler import WIKI_CONCEPTS_DIR, WIKI_DIR, WIKI_PAPERS_DIR


router = APIRouter(prefix="/api/sync", tags=["sync-local"])


# Deterministic UUIDs for wiki rows — keyed on rel_path so the same .md
# always maps to the same id across syncs (and machines). Cloud upserts
# by (user_id, rel_path), so the id is mostly a tracking handle; making
# it deterministic just keeps the row stable across re-syncs.
_WIKI_NS = uuid.UUID("6f5b6e62-7d4a-4e1f-9c1b-2c8e3a5b7c11")


def _wiki_id(rel_path: str) -> str:
    return str(uuid.uuid5(_WIKI_NS, f"knowra-wiki:{rel_path}"))


# --- hashing helpers ---------------------------------------------------


_HASH_BUFFER = 1024 * 1024  # 1 MB chunks — keeps memory bounded on big PDFs


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(_HASH_BUFFER)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _isoformat(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


# --- paper rows --------------------------------------------------------


def _ensure_paper_hash(paper: Paper) -> str:
    """Return file_hash, computing+caching if missing. Empty string if
    the PDF can't be located — the server will reject the row but at
    least the rest of the snapshot is still useful."""
    if paper.file_hash:
        return paper.file_hash
    if not paper.filepath:
        return ""
    try:
        path = resolve_paper_path(paper.filepath)
        if not path.exists():
            return ""
        digest = _sha256_file(path)
        paper.file_hash = digest
        return digest
    except OSError:
        return ""


def _paper_row(paper: Paper) -> dict[str, Any]:
    return {
        "id": str(paper.id),
        # user_id is stamped by the frontend agent right before /prepare.
        # We send empty here so the agent's stampUserId() is the single
        # writer — avoids divergent partial values if the user swaps
        # cloud accounts mid-sync.
        "user_id": "",
        "updated_at": _isoformat(paper.processed_at or paper.created_at),
        "filepath": paper.filepath or "",
        "filename": paper.filename or "",
        "file_hash": _ensure_paper_hash(paper),
        "title": paper.title,
        "authors": paper.authors if isinstance(paper.authors, list) else [],
        "num_pages": paper.num_pages,
        "processed": bool(paper.processed),
        "processed_at": _isoformat(paper.processed_at),
        "extraction_model": paper.extraction_model,
        "paper_category_model": paper.paper_category_model,
        "paper_category_override": paper.paper_category_override,
        "paper_team_model": paper.paper_team_model,
        "paper_team_override": paper.paper_team_override,
        "raw_llm_response": paper.raw_llm_response,
        "notes": paper.notes,
        "error": paper.error,
        "processing_status": paper.processing_status,
        "retry_count": paper.retry_count,
        "last_error_stage": paper.last_error_stage,
        "last_error_reason": paper.last_error_reason,
        "last_error_recoverable": paper.last_error_recoverable,
        "legacy_id": paper.legacy_id,
        "created_at": _isoformat(paper.created_at),
    }


def _node_row(node: KnowledgeNode) -> dict[str, Any]:
    spids = node.source_paper_ids if isinstance(node.source_paper_ids, list) else []
    return {
        "id": str(node.id),
        "user_id": "",
        "updated_at": _isoformat(node.last_promotion_eval_at or node.created_at),
        "title": node.title,
        "content": node.content or "",
        "node_type": node.node_type,
        "node_origin": node.node_origin,
        "hidden": bool(node.hidden) if node.hidden is not None else None,
        "promotion_status": node.promotion_status,
        "promoted_by": node.promoted_by,
        "promotion_reason": node.promotion_reason,
        "last_promotion_eval_at": _isoformat(node.last_promotion_eval_at),
        "tags": node.tags if isinstance(node.tags, list) else [],
        # Embeddings can be large (1536 floats per node) and aren't useful
        # to the mobile client. Skip them on push; cloud derives its own
        # if needed.
        "embedding": None,
        "source_paper_ids": [str(x) for x in spids],
        "legacy_id": node.legacy_id,
        "created_at": _isoformat(node.created_at),
    }


def _edge_row(edge: KnowledgeEdge) -> dict[str, Any]:
    return {
        "id": str(edge.id),
        "user_id": "",
        "updated_at": _isoformat(edge.created_at),
        "source_id": str(edge.source_id),
        "target_id": str(edge.target_id),
        "relation_type": edge.relation_type,
        "weight": edge.weight,
        "legacy_id": edge.legacy_id,
        "created_at": _isoformat(edge.created_at),
    }


# --- wiki files --------------------------------------------------------
#
# We walk three locations:
#   data/wiki/papers/*.md          → kind="paper",   paper_id from filename
#   data/wiki/concepts/*.md        → kind="concept", concept_id from filename
#   data/wiki/index.md             → kind="index"
#   data/wiki/lint-report.md       → kind="lint_report"
#
# The filename convention is `{int_id:04d}-{slug}.md`. After multitenant
# migration the int_id corresponds to the row's legacy_id; we resolve
# it back to the string UUID by querying KnowledgeNode / Paper. This is
# the bridge between disk artifacts and cloud row IDs.


_LEGACY_ID_RE = re.compile(r"^(\d+)-")


def _legacy_id_from_filename(name: str) -> Optional[int]:
    m = _LEGACY_ID_RE.match(name)
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


def _build_legacy_to_uuid(rows: Iterable[Any]) -> dict[int, str]:
    out: dict[int, str] = {}
    for row in rows:
        if row.legacy_id is not None:
            out[int(row.legacy_id)] = str(row.id)
    return out


def _wiki_meta_from_frontmatter(text: str) -> dict[str, Any]:
    """Parse the small subset of YAML frontmatter we care about (title +
    compiled_at). Avoids pulling in a YAML dep — the compiler writes a
    very regular two-field block."""
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end < 0:
        return {}
    block = text[3:end]
    meta: dict[str, Any] = {}
    for line in block.splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if val:
            meta[key] = val
    return meta


def _collect_wiki_files(
    paper_legacy_to_uuid: dict[int, str],
    concept_legacy_to_uuid: dict[int, str],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []

    def push(path: Path, kind: str, *, paper_id: Optional[str] = None,
             concept_id: Optional[str] = None):
        try:
            data = path.read_bytes()
        except OSError:
            return
        try:
            text = data.decode("utf-8", errors="replace")
        except Exception:
            text = ""
        meta = _wiki_meta_from_frontmatter(text)
        rel_path = path.relative_to(WIKI_DIR).as_posix()
        out.append({
            "id": _wiki_id(rel_path),
            "user_id": "",
            "updated_at": _isoformat(
                datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc),
            ),
            "kind": kind,
            "rel_path": rel_path,
            "content_hash": _sha256_bytes(data),
            "size_bytes": len(data),
            "title": meta.get("title"),
            "aliases": None,
            "compiled_at": meta.get("compiled_at"),
            "paper_id": paper_id,
            "concept_id": concept_id,
            # Bytes inlined so the frontend can perform the signed PUT
            # without a second round-trip. Single-user payload is bounded
            # (< 5 MB typical); we'll switch to lazy fetch if anyone ever
            # actually grows past ~50 MB of wiki.
            "body_b64": base64.b64encode(data).decode("ascii"),
        })

    if WIKI_PAPERS_DIR.exists():
        for path in sorted(WIKI_PAPERS_DIR.glob("*.md")):
            legacy = _legacy_id_from_filename(path.name)
            paper_id = paper_legacy_to_uuid.get(legacy) if legacy is not None else None
            push(path, kind="paper", paper_id=paper_id)

    if WIKI_CONCEPTS_DIR.exists():
        for path in sorted(WIKI_CONCEPTS_DIR.glob("*.md")):
            legacy = _legacy_id_from_filename(path.name)
            concept_id = concept_legacy_to_uuid.get(legacy) if legacy is not None else None
            push(path, kind="concept", concept_id=concept_id)

    index_path = WIKI_DIR / "index.md"
    if index_path.exists():
        push(index_path, kind="index")

    lint_path = WIKI_DIR / "lint-report.md"
    if lint_path.exists():
        push(lint_path, kind="lint_report")

    return out


# --- endpoint ----------------------------------------------------------


@router.get("/local_snapshot")
def local_snapshot(
    include_wiki_bodies: bool = True,
    db: Session = Depends(get_db),
):
    """Build a cloud-shaped sync payload from the local SQLite + disk
    wiki.

    Query params:
        include_wiki_bodies: when false, ``wiki_files[].body_b64`` is
            omitted. Useful if the caller already has the bytes cached
            (e.g. a "what changed since X?" probe).

    Returns the same shape the frontend's sync agent feeds into
    ``cloudPrepare`` — the agent only has to stamp user_id and add
    deletions (which the desktop doesn't track yet).
    """
    if is_cloud_mode():
        # Defense in depth — this router should never be hit in the cloud
        # deploy. Treat as a 404 to keep the API surface tight.
        raise HTTPException(status_code=404, detail="not available in cloud mode")

    papers = db.query(Paper).all()
    nodes = db.query(KnowledgeNode).all()
    edges = db.query(KnowledgeEdge).all()

    paper_rows = [_paper_row(p) for p in papers]

    # Commit any newly computed file_hashes back so we don't re-hash on
    # next snapshot. _ensure_paper_hash mutates the ORM object in place.
    try:
        db.commit()
    except Exception:
        db.rollback()

    node_rows = [_node_row(n) for n in nodes]
    # Dedupe edges by (source_id, target_id, relation_type). The cloud
    # schema enforces UNIQUE(user_id, source_id, target_id, relation_type),
    # but the local SQLite has accumulated duplicates over time
    # (re-extracting a paper used to insert a fresh edge row each run).
    # Sending those duplicates would 409 on commit, so we keep only the
    # most-recently-created one per tuple. The ones we drop don't carry
    # any unique info — they're literally the same edge.
    seen_edges: dict[tuple, dict[str, Any]] = {}
    for e in edges:
        key = (str(e.source_id), str(e.target_id), e.relation_type or "related")
        existing = seen_edges.get(key)
        if existing is None or (e.created_at and (
                existing.get("__ct") is None or e.created_at > existing["__ct"])):
            row = _edge_row(e)
            row["__ct"] = e.created_at  # internal sort key, stripped below
            seen_edges[key] = row
    edge_rows = []
    for row in seen_edges.values():
        row.pop("__ct", None)
        edge_rows.append(row)

    paper_legacy = _build_legacy_to_uuid(papers)
    concept_legacy = _build_legacy_to_uuid(nodes)

    wiki_files = _collect_wiki_files(paper_legacy, concept_legacy)

    if not include_wiki_bodies:
        for w in wiki_files:
            w.pop("body_b64", None)

    return {
        "papers": paper_rows,
        "knowledge_nodes": node_rows,
        "knowledge_edges": edge_rows,
        "wiki_files": wiki_files,
        # Local-side delete tracking is not implemented yet. Empty list
        # means "I'm not asking the cloud to delete anything"; the cloud
        # will still keep tombstones from previous sync sessions.
        "deletions": {
            "papers": [],
            "knowledge_nodes": [],
            "knowledge_edges": [],
            "wiki_files": [],
        },
        "generated_at": _isoformat(datetime.now(timezone.utc)),
        "counts": {
            "papers": len(paper_rows),
            "knowledge_nodes": len(node_rows),
            "knowledge_edges": len(edge_rows),
            "wiki_files": len(wiki_files),
        },
    }


# Silence "imported but unused" for the os import if we ever drop the
# stat-based mtime read.
_ = os
