"""Local first-page previews for library papers, independent of model extract."""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Iterable, Optional

import pypdfium2
from path_utils import portable_data_path, resolve_artifact_path, resolve_paper_path
from services import pdf_service

_RENDER_GATE = threading.Semaphore(2)
_RENDER_WORKERS = 2


def paper_has_first_page_image(paper) -> bool:
    path = getattr(paper, "first_page_image_path", None)
    if not path:
        return False
    try:
        return resolve_artifact_path(path).is_file()
    except Exception:
        return False


def inspect_pdf_page_count(filepath: str) -> Optional[int]:
    try:
        pdf = pypdfium2.PdfDocument(filepath)
        try:
            count = len(pdf)
        finally:
            pdf.close()
        return count if count > 0 else None
    except Exception:
        return None


def ensure_first_page_image(filepath: str, file_hash: str, dpi: int = 150) -> Optional[str]:
    """Return a cached or newly rendered first-page PNG path. Never raises."""
    if not filepath or not file_hash:
        return None
    cached = pdf_service.ARTIFACT_DIR / f"{file_hash}_page1.png"
    try:
        if cached.is_file():
            return portable_data_path(cached)
    except Exception:
        pass
    acquired = False
    try:
        acquired = _RENDER_GATE.acquire(timeout=30)
        if not acquired:
            return None
        if cached.is_file():
            return portable_data_path(cached)
        return pdf_service.render_first_page(filepath, file_hash, dpi=dpi)
    except Exception:
        return None
    finally:
        if acquired:
            _RENDER_GATE.release()


def attach_first_page_preview(paper) -> bool:
    """Best-effort: set first_page_image_path and num_pages when missing.

    Rendering failures leave the paper record otherwise unchanged.
    """
    changed = False
    try:
        pdf_path = resolve_paper_path(paper.filepath)
        if not pdf_path.is_file():
            return False
        if not paper_has_first_page_image(paper):
            image_path = ensure_first_page_image(str(pdf_path), paper.file_hash or "")
            if image_path:
                paper.first_page_image_path = image_path
                changed = True
        if not getattr(paper, "num_pages", None):
            pages = inspect_pdf_page_count(str(pdf_path))
            if pages:
                paper.num_pages = pages
                changed = True
    except Exception:
        return changed
    return changed


def _preview_payload(paper) -> tuple[object, Optional[str], Optional[int]]:
    try:
        pdf_path = resolve_paper_path(paper.filepath)
        if not pdf_path.is_file():
            return paper.id, None, None
        image_path = None
        if not paper_has_first_page_image(paper):
            image_path = ensure_first_page_image(str(pdf_path), paper.file_hash or "")
        pages = None
        if not getattr(paper, "num_pages", None):
            pages = inspect_pdf_page_count(str(pdf_path))
        return paper.id, image_path, pages
    except Exception:
        return getattr(paper, "id", None), None, None


def attach_first_page_previews(papers: Iterable, db, *, max_workers: int = _RENDER_WORKERS) -> int:
    """Render previews for registered papers without failing the caller.

    Work is bounded so a large scan cannot fan out into one render per PDF at
    once. The scan/import request can still return if individual renders fail.
    """
    jobs = [paper for paper in papers if getattr(paper, "filepath", None)]
    if not jobs:
        return 0
    workers = max(1, min(max_workers, len(jobs)))
    results: dict = {}
    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for paper_id, image_path, pages in pool.map(_preview_payload, jobs):
                if paper_id is not None:
                    results[paper_id] = (image_path, pages)
    except Exception:
        return 0

    updated = 0
    for paper in jobs:
        image_path, pages = results.get(paper.id, (None, None))
        changed = False
        if image_path and paper.first_page_image_path != image_path:
            paper.first_page_image_path = image_path
            changed = True
        if pages and not getattr(paper, "num_pages", None):
            paper.num_pages = pages
            changed = True
        if changed:
            updated += 1
    if not updated:
        return 0
    try:
        db.commit()
    except Exception:
        db.rollback()
        return 0
    return updated
