import re
from pathlib import Path
from typing import Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from models import Paper
from path_utils import portable_data_path, resolve_papers_directory
from services.pdf_service import compute_hash
from services.paper_record_service import sync_record_from_paper
from services.paper_pipeline_service import PIPELINE_STATUS_SCANNING


# arXiv ids look like ``2512.08924`` with an optional version suffix
# (``v1`` / ``v2`` …). The base id (version stripped) is the stable paper
# identity — ``2512.08924v1`` and ``2512.08924v2`` are the same paper.
_ARXIV_RE = re.compile(r"(\d{4}\.\d{4,5})(v\d+)?", re.IGNORECASE)


def _arxiv_base_id(name: Optional[str]) -> Optional[str]:
    m = _ARXIV_RE.search(name or "")
    return m.group(1) if m else None


def scan_directory(
    directory: str,
    db: Session,
    *,
    _retry_on_integrity: bool = True,
) -> dict:
    """Scan directory for new PDF papers not yet in DB.

    De-dup rules (a candidate is skipped — not added, not processed — when it
    matches an already-present paper):
      1. same stored path (a plain re-scan; not counted as a duplicate)
      2. same arXiv id — version-agnostic (``…v1`` vs ``…v2``)
      3. byte-identical content (file_hash)
    Returns stats including ``duplicates`` plus a path-sorted
    ``duplicate_files`` menu with the matching database paper.
    """
    scan_path = resolve_papers_directory(directory)
    if not scan_path.exists():
        raise FileNotFoundError(f"Directory not found: {directory}")

    existing_papers = db.query(Paper).all()
    existing_paths = {portable_data_path(paper.filepath) for paper in existing_papers}
    paper_by_hash = {
        paper.file_hash: paper
        for paper in existing_papers
        if paper.file_hash
    }
    paper_by_arxiv: dict[str, Paper] = {}
    for paper in existing_papers:
        aid = _arxiv_base_id(paper.filename)
        if aid:
            paper_by_arxiv.setdefault(aid, paper)

    added = 0
    duplicates = 0
    duplicate_files: list[dict] = []
    added_papers: list[Paper] = []
    # Stable ordering makes "the first duplicate under this path"
    # deterministic across scans and operating systems.
    pdf_paths = sorted(
        (
            path
            for path in scan_path.rglob("*")
            if path.is_file() and path.suffix.lower() == ".pdf"
        ),
        key=lambda path: path.relative_to(scan_path).as_posix().casefold(),
    )
    for pdf_path in pdf_paths:
        filepath_str = str(pdf_path)
        storage_path = portable_data_path(pdf_path)
        if storage_path in existing_paths:
            continue  # already scanned this exact file — not a duplicate
        # arXiv-id check first (cheap, filename only) before hashing bytes.
        aid = _arxiv_base_id(pdf_path.name)
        matched_paper = paper_by_arxiv.get(aid) if aid else None
        if matched_paper is not None:
            duplicates += 1
            duplicate_files.append(
                _duplicate_file_item(
                    pdf_path,
                    scan_path,
                    reason="same_arxiv_id",
                    matched_paper=matched_paper,
                )
            )
            continue  # same arXiv paper (possibly a different version)
        try:
            file_hash = compute_hash(filepath_str)
            matched_paper = paper_by_hash.get(file_hash)
            if matched_paper is not None:
                duplicates += 1
                duplicate_files.append(
                    _duplicate_file_item(
                        pdf_path,
                        scan_path,
                        reason="same_content",
                        matched_paper=matched_paper,
                    )
                )
                continue  # byte-identical copy elsewhere
            paper = Paper(
                filepath=storage_path,
                filename=pdf_path.name,
                file_hash=file_hash,
                processed=False,
                processing_status=PIPELINE_STATUS_SCANNING,
                retry_count=0,
                last_error_stage=None,
                last_error_reason=None,
                last_error_recoverable=None,
            )
            db.add(paper)
            added_papers.append(paper)
            existing_paths.add(storage_path)
            paper_by_hash[file_hash] = paper
            if aid:
                paper_by_arxiv[aid] = paper
            added += 1
        except Exception:
            continue

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        if _retry_on_integrity:
            # A concurrent scan may have inserted the same path/hash after
            # our in-memory de-dupe sets were built. Re-run once against the
            # now-current DB; the duplicate will be skipped by the normal rules.
            return scan_directory(directory, db, _retry_on_integrity=False)
        raise

    for paper in added_papers:
        try:
            sync_record_from_paper(paper, event="scan")
        except Exception:
            pass

    total = db.query(Paper).count()
    unprocessed = db.query(Paper).filter(Paper.processed == False).count()
    return {
        "new_found": added,
        "duplicates": duplicates,
        "duplicate_files": duplicate_files,
        "total": total,
        "unprocessed": unprocessed,
    }


def _duplicate_file_item(
    pdf_path: Path,
    scan_path: Path,
    *,
    reason: str,
    matched_paper: Paper,
) -> dict:
    resolved_path = pdf_path.resolve()
    try:
        relative_path = resolved_path.relative_to(scan_path.resolve()).as_posix()
    except ValueError:
        relative_path = pdf_path.name
    return {
        "filename": pdf_path.name,
        "path": str(resolved_path),
        "relative_path": relative_path,
        "reason": reason,
        "matched_paper": {
            "id": matched_paper.id,
            "title": matched_paper.title,
            "filename": matched_paper.filename,
            "filepath": matched_paper.filepath,
        },
    }
