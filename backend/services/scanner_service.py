import re
from typing import Optional

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


def scan_directory(directory: str, db: Session) -> dict:
    """Scan directory for new PDF papers not yet in DB.

    De-dup rules (a candidate is skipped — not added, not processed — when it
    matches an already-present paper):
      1. same stored path (a plain re-scan; not counted as a duplicate)
      2. same arXiv id — version-agnostic (``…v1`` vs ``…v2``)
      3. byte-identical content (file_hash)
    Returns stats including ``duplicates`` (count skipped by rule 2 or 3).
    """
    scan_path = resolve_papers_directory(directory)
    if not scan_path.exists():
        raise FileNotFoundError(f"Directory not found: {directory}")

    existing_paths = {
        portable_data_path(row.filepath) for row in db.query(Paper.filepath).all()
    }
    existing_hashes = {row.file_hash for row in db.query(Paper.file_hash).all()}
    existing_arxiv: set[str] = set()
    for (fn,) in db.query(Paper.filename).all():
        aid = _arxiv_base_id(fn)
        if aid:
            existing_arxiv.add(aid)

    added = 0
    duplicates = 0
    added_papers: list[Paper] = []
    for ext in ("*.pdf", "*.PDF"):
        for pdf_path in scan_path.rglob(ext):
            filepath_str = str(pdf_path)
            storage_path = portable_data_path(pdf_path)
            if storage_path in existing_paths:
                continue  # already scanned this exact file — not a duplicate
            # arXiv-id check first (cheap, filename only) before hashing bytes.
            aid = _arxiv_base_id(pdf_path.name)
            if aid and aid in existing_arxiv:
                duplicates += 1
                continue  # same arXiv paper (possibly a different version)
            try:
                file_hash = compute_hash(filepath_str)
                if file_hash in existing_hashes:
                    duplicates += 1
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
                existing_hashes.add(file_hash)
                if aid:
                    existing_arxiv.add(aid)
                added += 1
            except Exception:
                continue

    db.commit()

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
        "total": total,
        "unprocessed": unprocessed,
    }
