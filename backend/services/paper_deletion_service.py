"""Delete one library paper and the Knowra-managed artifacts that belong to it.

The public contract is conservative:

- Immediately remove the paper row, its managed PDF copy, thumbnails,
  extraction records, paper wiki page, retrieval-index entries, graph
  citations and relationships, checkpoints and manifests.
- Keep concepts that are still cited by other papers (or that were created
  manually) and mark them stale so the next concept compile no longer uses
  the deleted source.
- Never follow a symlink out of the data root; never delete an original PDF
  that lives outside Knowra's managed data directory; never delete a PDF or
  hash-keyed thumbnail that another paper still uses.
- File-operation failures are returned to the caller and are not reported
  as success. Missing files are treated as already gone (idempotent).
- Provider (OpenAI file / vector store / thread) cleanup is best-effort and
  injectable so tests can mock it without network or paid calls.
"""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from sqlalchemy.orm import Session

from models import KnowledgeNode, Paper, RoadmapPaper
from path_utils import DATA_DIR, PAPERS_DIR, portable_data_path
from services.graph_service import MANUAL_NODE_ORIGIN, node_origin, remove_nodes_for_paper
from services.note_images_gc import gc_on_notes_update
from services.paper_record_service import record_path_for_paper
from services.paper_source_compilation import evidence_manifest_path
from services.pipeline_manifest import _manifest_path


SAFE_ID_RE = re.compile(r"[^A-Za-z0-9._-]")
_TOMBSTONE_DIRNAME = "deleted_papers"


class PaperDeletionError(Exception):
    def __init__(self, message: str, *, code: str = "delete_failed", details: Optional[dict] = None):
        super().__init__(message)
        self.code = code
        self.details = details or {}


class DeletedPaperSkip(Exception):
    """Raised inside a processing worker when the paper was deleted mid-run."""


@dataclass
class DeleteOutcome:
    status: str
    paper_id: str
    title: Optional[str] = None
    filename: Optional[str] = None
    deleted: dict[str, Any] = field(default_factory=dict)
    retained: dict[str, Any] = field(default_factory=dict)
    stale_concepts: list[dict[str, Any]] = field(default_factory=list)
    skipped_files: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    provider_cleanup: dict[str, Any] = field(default_factory=dict)
    already_deleted: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "paper_id": self.paper_id,
            "title": self.title,
            "filename": self.filename,
            "deleted": self.deleted,
            "retained": self.retained,
            "stale_concepts": self.stale_concepts,
            "skipped_files": self.skipped_files,
            "warnings": self.warnings,
            "provider_cleanup": self.provider_cleanup,
            "already_deleted": self.already_deleted,
        }


def _safe_id(paper_id: str) -> str:
    text = str(paper_id or "").strip()
    if not text:
        raise PaperDeletionError("paper id is required", code="invalid_id")
    return SAFE_ID_RE.sub("_", text)


def tombstone_dir(data_root: Optional[Path] = None) -> Path:
    return Path(data_root or DATA_DIR) / _TOMBSTONE_DIRNAME


def tombstone_path(paper_id: str, data_root: Optional[Path] = None) -> Path:
    return tombstone_dir(data_root) / f"{_safe_id(paper_id)}.json"


def write_tombstone(paper_id: str, payload: Optional[dict] = None, data_root: Optional[Path] = None) -> Path:
    path = tombstone_path(paper_id, data_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {
        "paper_id": str(paper_id),
        "deleted_at": datetime.now(timezone.utc).isoformat(),
        **(payload or {}),
    }
    path.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def has_tombstone(paper_id: str, data_root: Optional[Path] = None) -> bool:
    try:
        return tombstone_path(paper_id, data_root).is_file()
    except PaperDeletionError:
        return False


def should_skip_deleted_paper(paper_id: str, data_root: Optional[Path] = None) -> bool:
    """Workers must consult this before any write-back or late graph update."""
    return has_tombstone(paper_id, data_root)


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def _lexical_within(path: Path, root: Path) -> bool:
    """Membership without resolving the final symlink target."""
    try:
        base = root.resolve()
        cur = path if path.is_absolute() else (base / path)
        # Resolve only the parent so a final symlink is judged by its location.
        parent = cur.parent.resolve()
        return (parent / cur.name) == (parent / cur.name) and parent.is_relative_to(base)
    except (OSError, ValueError):
        return False


def is_managed_data_path(path: Path, data_root: Path) -> bool:
    """True when the path (or symlink itself) lives under Knowra's data root."""
    if not path:
        return False
    return _lexical_within(path, data_root)


def safe_unlink(path: Path, data_root: Path) -> str:
    """Unlink a file or symlink only if the link itself is under data_root.

    If ``path`` is a symlink, the symlink is removed and the external target
    is left untouched. Returns one of: deleted, missing, skipped_unmanaged.
    Raises PaperDeletionError on an unexpected OS error for a managed path.
    """
    if not path:
        return "missing"
    candidate = path if path.is_absolute() else data_root / path
    if not is_managed_data_path(candidate, data_root):
        return "skipped_unmanaged"
    try:
        if candidate.is_symlink() or candidate.is_file():
            candidate.unlink()
            return "deleted"
        if not candidate.exists():
            return "missing"
        return "skipped_not_file"
    except FileNotFoundError:
        return "missing"
    except OSError as exc:
        raise PaperDeletionError(
            f"failed to delete {candidate}: {exc}",
            code="file_delete_failed",
            details={"path": str(candidate), "error": str(exc)},
        ) from exc


def safe_rmtree(path: Path, data_root: Path) -> str:
    """Remove a managed directory tree without following outbound symlinks."""
    if not path:
        return "missing"
    candidate = path if path.is_absolute() else data_root / path
    if not is_managed_data_path(candidate, data_root):
        return "skipped_unmanaged"
    if candidate.is_symlink():
        return safe_unlink(candidate, data_root)
    if not candidate.exists():
        return "missing"
    if not candidate.is_dir():
        return safe_unlink(candidate, data_root)
    errors: list[str] = []

    def _walk(current: Path) -> None:
        try:
            entries = list(current.iterdir())
        except OSError as exc:
            errors.append(f"{current}: {exc}")
            return
        for child in entries:
            try:
                if child.is_symlink() or child.is_file():
                    if is_managed_data_path(child, data_root):
                        child.unlink()
                    continue
                if child.is_dir() and not child.is_symlink():
                    _walk(child)
                    if is_managed_data_path(child, data_root):
                        child.rmdir()
            except OSError as exc:
                errors.append(f"{child}: {exc}")
        try:
            if current.exists() and is_managed_data_path(current, data_root):
                current.rmdir()
        except OSError as exc:
            errors.append(f"{current}: {exc}")

    _walk(candidate)
    if errors:
        raise PaperDeletionError(
            "failed to delete directory " + str(candidate),
            code="file_delete_failed",
            details={"path": str(candidate), "errors": errors},
        )
    return "deleted"


def _resolve_recorded_path(raw: Optional[str], data_root: Path) -> Optional[Path]:
    if not raw:
        return None
    text = str(raw)
    portable = portable_data_path(text)
    if portable.startswith("data/"):
        return data_root / Path(portable).as_posix()[len("data/"):]
    path = Path(text)
    if path.is_absolute():
        return path
    return data_root / path


def _other_papers_using_path_or_hash(
    db: Session,
    paper: Paper,
) -> tuple[list[Paper], list[Paper]]:
    others = db.query(Paper).filter(Paper.id != paper.id).all()
    same_path: list[Paper] = []
    same_hash: list[Paper] = []
    source = portable_data_path(paper.filepath or "")
    for other in others:
        if portable_data_path(other.filepath or "") == source or (
            paper.filepath and other.filepath and Path(other.filepath) == Path(paper.filepath)
        ):
            same_path.append(other)
        if paper.file_hash and other.file_hash == paper.file_hash:
            same_hash.append(other)
    return same_path, same_hash


def _managed_papers_dir(data_root: Path) -> Path:
    if data_root.resolve() == DATA_DIR.resolve():
        return PAPERS_DIR
    return data_root / "papers"


def _should_delete_original_pdf(pdf_path: Optional[Path], data_root: Path) -> tuple[bool, str]:
    if pdf_path is None:
        return False, "missing"
    if not is_managed_data_path(pdf_path, data_root):
        return False, "skipped_external"
    # Only delete PDFs under the managed papers (or broader data) tree.
    papers_dir = _managed_papers_dir(data_root)
    if not is_managed_data_path(pdf_path, papers_dir) and not is_managed_data_path(pdf_path, data_root):
        return False, "skipped_external"
    # Prefer papers/ as the managed original location. Other locations under
    # data/ (e.g. accidental copies) are still Knowra-managed and may go.
    return True, "managed"


def _wiki_paper_pages(data_root: Path, paper: Optional[Paper], paper_id: str) -> list[Path]:
    pages_dir = data_root / "wiki" / "papers"
    if not pages_dir.is_dir():
        return []
    identities = {str(paper_id)}
    if paper is not None and getattr(paper, "legacy_id", None) is not None:
        identities.add(str(paper.legacy_id))
    matches: list[Path] = []
    for candidate in pages_dir.glob("*.md"):
        if candidate.is_symlink() and not is_managed_data_path(candidate, data_root):
            continue
        try:
            text = candidate.read_text(encoding="utf-8")
        except OSError:
            continue
        # Lightweight frontmatter check — avoid importing compiler side effects.
        header = text[4 : text.find("\n---", 4)] if text.startswith("---\n") else ""
        for ident in identities:
            if re.search(rf"(?m)^paper_id:\s*['\"]?{re.escape(ident)}['\"]?\s*$", header) or (
                f"paper_id: {ident}" in header or f'paper_id: "{ident}"' in header
            ):
                matches.append(candidate)
                break
    return matches


def _extraction_checkpoint_dirs(data_root: Path, paper_id: str) -> list[Path]:
    root = data_root / "extraction_checkpoints"
    if not root.is_dir():
        return []
    found: list[Path] = []
    for child in root.iterdir():
        if not child.is_dir() or child.is_symlink():
            continue
        marker = child / "checkpoint.json"
        if not marker.is_file():
            continue
        try:
            payload = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        identity = payload.get("identity") if isinstance(payload, dict) else None
        blob = json.dumps(identity or payload, ensure_ascii=False)
        if str(paper_id) in blob:
            found.append(child)
    return found


def _evidence_paths(data_root: Path, paper_id: str, file_hash: Optional[str]) -> list[Path]:
    root = data_root / "paper_evidence"
    paths = [
        evidence_manifest_path(root, paper_id, library_file_hash=file_hash),
        evidence_manifest_path(root, paper_id, library_file_hash=None),
    ]
    # Also drop any leftover hash variants whose manifest still names this paper.
    if root.is_dir():
        for candidate in root.glob("*.json"):
            if candidate in paths:
                continue
            try:
                payload = json.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if str(payload.get("paper_id") or "") == str(paper_id):
                paths.append(candidate)
    return paths


def _artifact_paths(data_root: Path, file_hash: Optional[str]) -> list[Path]:
    if not file_hash:
        return []
    artifacts = data_root / "artifacts"
    if not artifacts.is_dir():
        return []
    return sorted(artifacts.glob(f"{file_hash}_page*.png"))


def drop_paper_from_search_index(data_root: Path, filenames: list[str]) -> str:
    db_path = data_root / "wiki_search.sqlite"
    if not db_path.is_file() or not filenames:
        return "missing" if not db_path.is_file() else "noop"
    if not is_managed_data_path(db_path, data_root):
        return "skipped_unmanaged"
    try:
        conn = sqlite3.connect(str(db_path))
        try:
            for name in filenames:
                conn.execute("DELETE FROM wiki_fts WHERE filename = ?", (name,))
            conn.commit()
        finally:
            conn.close()
        return "updated"
    except sqlite3.Error as exc:
        raise PaperDeletionError(
            f"failed to update search index: {exc}",
            code="index_update_failed",
            details={"error": str(exc)},
        ) from exc


def cancel_jobs_for_paper(paper_id: str, store=None) -> dict[str, Any]:
    """Strip a paper from queued papers/pipeline jobs, or cancel a now-empty job.

    Running jobs are left running; workers consult should_skip_deleted_paper
    and must not write the paper back.
    """
    summary = {"cancelled": [], "updated": [], "running": []}
    if store is None:
        try:
            from services.task_runtime import store as runtime_store
            store = runtime_store()
        except Exception:
            return summary
    target = str(paper_id)
    try:
        active = store.active()
    except Exception:
        return summary
    for job in active:
        if job.get("kind") not in {"papers", "pipeline"}:
            continue
        payload = dict(job.get("payload") or {})
        ids = [str(item) for item in payload.get("paper_ids") or []]
        if target not in ids:
            # A full-library pipeline with no explicit ids still must skip
            # this paper via the tombstone; record that it was running.
            if job.get("status") == "running":
                summary["running"].append(job.get("job_id") or job.get("id"))
            continue
        remaining = [item for item in ids if item != target]
        job_id = job.get("job_id") or job.get("id")
        if job.get("status") == "queued":
            if not remaining:
                store.update(job_id, status="cancelled", payload={**payload, "paper_ids": []})
                summary["cancelled"].append(job_id)
            else:
                store.update(job_id, payload={**payload, "paper_ids": remaining})
                summary["updated"].append(job_id)
        else:
            # Do not mutate a running payload mid-flight; the worker skips.
            summary["running"].append(job_id)
    return summary


def cleanup_provider_paper_resources(
    paper: Paper,
    *,
    client: Any = None,
    client_factory: Optional[Callable[[], Any]] = None,
) -> dict[str, Any]:
    """Best-effort deletion of OpenAI Files / vector stores / threads.

    Tests inject ``client`` or ``client_factory``. Production may pass a
    real client; this function never constructs one from a secret itself,
    and never raises for remote failures — they are reported in the result
    so local deletion can still complete without claiming remote success.
    """
    result = {
        "attempted": False,
        "verified": False,
        "deleted": [],
        "errors": [],
        "skipped": [],
    }
    handles = {
        "file": getattr(paper, "openai_file_id", None),
        "vector_store": getattr(paper, "openai_vector_store_id", None),
        "thread": getattr(paper, "openai_thread_id", None),
    }
    if not any(handles.values()):
        result["skipped"].append("no_provider_handles")
        return result
    api = client
    if api is None and client_factory is not None:
        try:
            api = client_factory()
        except Exception as exc:  # pragma: no cover - factory errors are reported
            result["errors"].append({"resource": "client", "error": type(exc).__name__})
            return result
    if api is None:
        result["skipped"].append("provider_client_not_configured")
        result["skipped"].extend(
            f"{kind}:{value}" for kind, value in handles.items() if value
        )
        return result

    result["attempted"] = True

    def _call(kind: str, handle: Optional[str], runner: Callable[[], None]) -> None:
        if not handle:
            return
        try:
            runner()
            result["deleted"].append({"resource": kind, "id": handle})
        except Exception as exc:
            result["errors"].append({"resource": kind, "id": handle, "error": type(exc).__name__})

    _call("thread", handles["thread"], lambda: api.beta.threads.delete(handles["thread"]))
    _call("vector_store", handles["vector_store"], lambda: api.vector_stores.delete(handles["vector_store"]))
    _call("file", handles["file"], lambda: api.files.delete(handles["file"]))
    result["verified"] = not result["errors"] and bool(result["deleted"])
    return result


def detach_paper_from_graph(db: Session, paper_id: str) -> dict[str, Any]:
    """Remove paper-only auto nodes; keep shared and manual concepts, stale them."""
    affected_ids = []
    for node in db.query(KnowledgeNode).all():
        raw_ids = node.source_paper_ids or []
        ids = list(raw_ids) if isinstance(raw_ids, list) else [raw_ids]
        if any(str(source_id) == str(paper_id) for source_id in ids):
            affected_ids.append(node.id)
    removed = remove_nodes_for_paper(db, paper_id)
    stale: list[dict[str, Any]] = []
    retained: list[dict[str, Any]] = []
    for node_id in affected_ids:
        still = db.query(KnowledgeNode).filter(KnowledgeNode.id == node_id).first()
        if still is None:
            continue
        raw_ids = still.source_paper_ids or []
        ids = list(raw_ids) if isinstance(raw_ids, list) else [raw_ids]
        if any(str(source_id) == str(paper_id) for source_id in ids):
            still.source_paper_ids = [source_id for source_id in ids if str(source_id) != str(paper_id)]
        item = {"id": str(still.id), "title": still.title, "origin": node_origin(still)}
        stale.append(item)
        retained.append(item)
    db.flush()
    return {
        "nodes_touched": removed + len(stale),
        "stale_concepts": stale,
        "retained_concepts": retained,
        "removed_nodes": removed,
    }


def delete_library_paper(
    db: Session,
    paper_id: str,
    *,
    data_root: Optional[Path] = None,
    provider_client: Any = None,
    provider_client_factory: Optional[Callable[[], Any]] = None,
    task_store=None,
) -> DeleteOutcome:
    """Delete one paper and its exclusive Knowra-managed artifacts."""
    root = Path(data_root or DATA_DIR)
    paper = db.query(Paper).filter(Paper.id == paper_id).first()
    leftovers_only = paper is None
    title = (paper.title if paper else None)
    filename = (paper.filename if paper else None)
    leftover_wiki = _wiki_paper_pages(root, paper, paper_id)
    leftover_ckpts = _extraction_checkpoint_dirs(root, paper_id)
    leftover_evidence = [path for path in _evidence_paths(root, paper_id, paper.file_hash if paper else None) if path.exists()]
    known = (
        paper is not None
        or has_tombstone(paper_id, root)
        or leftover_wiki
        or leftover_ckpts
        or leftover_evidence
        or (root / "wiki_source_checkpoints" / str(paper_id)).exists()
        or (root / "manifests" / f"{_safe_id(paper_id)}.json").exists()
    )
    if not known:
        raise PaperDeletionError("Paper not found", code="not_found")

    write_tombstone(
        paper_id,
        {
            "title": title,
            "filename": filename,
            "filepath": paper.filepath if paper else None,
        },
        root,
    )
    job_summary = cancel_jobs_for_paper(paper_id, store=task_store)

    provider = {"attempted": False, "verified": False, "deleted": [], "errors": [], "skipped": ["no_paper"]}
    if paper is not None:
        provider = cleanup_provider_paper_resources(
            paper,
            client=provider_client,
            client_factory=provider_client_factory,
        )
        if not provider.get("verified") and (
            getattr(paper, "openai_file_id", None)
            or getattr(paper, "openai_vector_store_id", None)
            or getattr(paper, "openai_thread_id", None)
        ):
            # Local delete may proceed; callers must not claim remote cleanup.
            pass

    same_path: list[Paper] = []
    same_hash: list[Paper] = []
    if paper is not None:
        same_path, same_hash = _other_papers_using_path_or_hash(db, paper)

    pdf_path = _resolve_recorded_path(paper.filepath, root) if paper else None
    image_path = _resolve_recorded_path(paper.first_page_image_path, root) if paper else None

    deleted: dict[str, Any] = {}
    skipped: list[dict[str, Any]] = []
    warnings: list[str] = []

    # PDF
    if paper is not None:
        if same_path:
            skipped.append({
                "kind": "pdf",
                "reason": "skipped_shared",
                "path": str(pdf_path) if pdf_path else paper.filepath,
                "shared_with": [other.id for other in same_path],
            })
            deleted["pdf"] = "skipped_shared"
        else:
            allowed, reason = _should_delete_original_pdf(pdf_path, root)
            if not allowed:
                skipped.append({"kind": "pdf", "reason": reason, "path": str(pdf_path) if pdf_path else paper.filepath})
                deleted["pdf"] = reason
                if reason == "skipped_external":
                    warnings.append("original PDF is outside the Knowra data directory and was left in place")
            else:
                deleted["pdf"] = safe_unlink(pdf_path, root) if pdf_path else "missing"
    else:
        deleted["pdf"] = "missing"

    # Hash-keyed thumbnails / page renders
    hash_artifacts: list[Path] = []
    if paper is not None:
        if same_hash:
            skipped.append({
                "kind": "thumbnails",
                "reason": "skipped_shared",
                "shared_with": [other.id for other in same_hash],
            })
            deleted["thumbnails"] = "skipped_shared"
        else:
            hash_artifacts = _artifact_paths(root, paper.file_hash)
            if image_path and image_path not in hash_artifacts:
                hash_artifacts.append(image_path)
            states = [safe_unlink(path, root) for path in hash_artifacts]
            deleted["thumbnails"] = states if states else "missing"

    # Paper record markdown
    record_states: list[str] = []
    if paper is not None:
        try:
            record_states.append(safe_unlink(record_path_for_paper(paper), root))
        except Exception:
            # record_path_for_paper uses module DATA_DIR; fall back to data_root.
            fallback = root / "paper_records"
            if fallback.is_dir():
                prefix = str(paper.id).replace("-", "")[:8]
                for candidate in fallback.glob("*.md"):
                    if prefix in candidate.name or (paper.filename and Path(paper.filename).stem in candidate.name):
                        record_states.append(safe_unlink(candidate, root))
        # Also search data_root records by id prefix so tests can isolate roots.
        records_dir = root / "paper_records"
        if records_dir.is_dir():
            for candidate in records_dir.glob("*.md"):
                try:
                    text = candidate.read_text(encoding="utf-8")
                except OSError:
                    continue
                if f"id: {paper.id}" in text or f"paper_id: {paper.id}" in text:
                    record_states.append(safe_unlink(candidate, root))
    deleted["record"] = record_states or "missing"

    # Evidence, manifests, checkpoints, wiki paper pages
    evidence_states = [safe_unlink(path, root) for path in _evidence_paths(root, paper_id, paper.file_hash if paper else None)]
    deleted["evidence"] = evidence_states or "missing"
    try:
        deleted["manifest"] = safe_unlink(_manifest_path(paper_id, root / "manifests"), root)
    except Exception as exc:
        warnings.append(f"manifest: {type(exc).__name__}")
        deleted["manifest"] = "error"

    wiki_pages = _wiki_paper_pages(root, paper, paper_id)
    wiki_names = [path.name for path in wiki_pages]
    deleted["wiki_pages"] = [safe_unlink(path, root) for path in wiki_pages] or "missing"
    deleted["search_index"] = drop_paper_from_search_index(root, wiki_names)

    wiki_ckpt = root / "wiki_source_checkpoints" / str(paper_id)
    deleted["wiki_checkpoints"] = safe_rmtree(wiki_ckpt, root) if wiki_ckpt.exists() or wiki_ckpt.is_symlink() else "missing"
    ckpt_states = [safe_rmtree(path, root) for path in _extraction_checkpoint_dirs(root, paper_id)]
    deleted["extraction_checkpoints"] = ckpt_states or "missing"

    # Graph + shared concepts
    graph = detach_paper_from_graph(db, paper_id)
    stale = graph["stale_concepts"]

    # Note images exclusive to this paper
    if paper is not None:
        try:
            deleted["note_images"] = gc_on_notes_update(db, paper.id, paper.notes, "")
        except Exception as exc:
            warnings.append(f"note_images: {type(exc).__name__}")
            deleted["note_images"] = 0

    # Roadmap references: drop the library link, keep bibliographic rows.
    roadmap_cleared = 0
    try:
        roadmap_cleared = (
            db.query(RoadmapPaper)
            .filter(RoadmapPaper.library_paper_id == str(paper_id))
            .update({"library_paper_id": None}, synchronize_session=False)
        )
    except Exception as exc:
        warnings.append(f"roadmap: {type(exc).__name__}")
    deleted["roadmap_links"] = int(roadmap_cleared or 0)

    # Finally the database row — only after managed file ops succeeded.
    if paper is not None:
        db.delete(paper)
    db.commit()
    deleted["record_row"] = not leftovers_only

    outcome = DeleteOutcome(
        status="deleted" if not leftovers_only else "already_deleted",
        paper_id=str(paper_id),
        title=title,
        filename=filename,
        deleted=deleted,
        retained={
            "shared_concepts": graph["retained_concepts"],
            "shared_pdf_papers": [other.id for other in same_path],
            "shared_thumbnail_papers": [other.id for other in same_hash],
            "jobs": job_summary,
        },
        stale_concepts=stale,
        skipped_files=skipped,
        warnings=warnings,
        provider_cleanup=provider,
        already_deleted=leftovers_only,
    )
    return outcome
