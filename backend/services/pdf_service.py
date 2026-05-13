import hashlib
from pathlib import Path
from typing import Optional

import pypdf
import pypdfium2
from path_utils import portable_data_path

# Artifact directory for rendered first pages
ARTIFACT_DIR = Path(__file__).parent.parent.parent / "data" / "artifacts"
ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)


def extract_text(filepath: str, max_chars: int = 40000) -> tuple[str, int]:
    """
    Extract plain text from a PDF. Returns (text, num_pages).
    Truncates to max_chars so we don't blow context window.
    """
    reader = pypdf.PdfReader(filepath)
    num_pages = len(reader.pages)

    parts = []
    total_len = 0
    for page in reader.pages:
        try:
            txt = page.extract_text() or ""
        except Exception:
            txt = ""
        parts.append(txt)
        total_len += len(txt)
        if total_len >= max_chars:
            break

    full_text = "\n\n".join(parts)
    if len(full_text) > max_chars:
        full_text = full_text[:max_chars] + "\n\n[...TRUNCATED...]"
    return full_text, num_pages


def extract_text_pages(
    filepath: str,
    *,
    max_chars_per_page: int = 5000,
    max_total_chars: int = 50000,
    max_pages: Optional[int] = None,
) -> tuple[list[dict], int]:
    """Extract per-page plain text with lightweight truncation.

    Returns ([{page_number, text}], num_pages). Empty pages are skipped.
    The result preserves page boundaries so downstream local-VLM flows can
    chunk the paper more like retrieval instead of one long text blob.
    """
    reader = pypdf.PdfReader(filepath)
    num_pages = len(reader.pages)

    pages: list[dict] = []
    total_chars = 0
    for index, page in enumerate(reader.pages, start=1):
        if max_pages is not None and index > max_pages:
            break
        try:
            text = (page.extract_text() or "").strip()
        except Exception:
            text = ""
        if not text:
            continue
        if len(text) > max_chars_per_page:
            text = text[:max_chars_per_page].rstrip() + "\n\n[...TRUNCATED PAGE TEXT...]"
        pages.append({"page_number": index, "text": text})
        total_chars += len(text)
        if total_chars >= max_total_chars:
            break
    return pages, num_pages


def render_first_page(filepath: str, file_hash: str, dpi: int = 150) -> Optional[str]:
    """
    Render the first page of a PDF to PNG. Returns the output path or None on failure.
    Uses file_hash as cache key so we don't re-render the same PDF.
    """
    output_path = ARTIFACT_DIR / f"{file_hash}_page1.png"
    if output_path.exists():
        return portable_data_path(output_path)

    try:
        pdf = pypdfium2.PdfDocument(filepath)
        if len(pdf) == 0:
            return None
        page = pdf[0]
        scale = dpi / 72.0
        image = page.render(scale=scale).to_pil()
        image.save(str(output_path), "PNG")
        page.close()
        pdf.close()
        return portable_data_path(output_path)
    except Exception:
        return None


def select_key_pages(num_pages: int, max_pages: int = 5) -> list[int]:
    if num_pages <= 0 or max_pages <= 0:
        return []
    if num_pages <= max_pages:
        return list(range(1, num_pages + 1))

    picks = {
        1,
        2,
        3,
        max(1, int(round(num_pages * 0.5))),
        max(1, int(round(num_pages * 0.75))),
        num_pages,
    }
    ordered = sorted(page for page in picks if 1 <= page <= num_pages)
    if len(ordered) <= max_pages:
        return ordered

    # Keep early pages, one middle signal, and the tail page.
    head = ordered[: max_pages - 2]
    tail = [ordered[-2], ordered[-1]]
    merged = sorted(set(head + tail))
    while len(merged) > max_pages:
        merged.pop(-2)
    return merged


def render_pdf_page(filepath: str, file_hash: str, page_number: int, dpi: int = 144) -> Optional[str]:
    """Render a 1-indexed PDF page to PNG and return a portable artifact path."""
    if page_number < 1:
        return None
    output_path = ARTIFACT_DIR / f"{file_hash}_page{page_number}.png"
    if output_path.exists():
        return portable_data_path(output_path)

    try:
        pdf = pypdfium2.PdfDocument(filepath)
        if len(pdf) < page_number:
            pdf.close()
            return None
        page = pdf[page_number - 1]
        scale = dpi / 72.0
        image = page.render(scale=scale).to_pil()
        image.save(str(output_path), "PNG")
        page.close()
        pdf.close()
        return portable_data_path(output_path)
    except Exception:
        return None


def render_pdf_pages(filepath: str, file_hash: str, page_numbers: list[int], dpi: int = 144) -> list[str]:
    rendered: list[str] = []
    seen: set[int] = set()
    for page_number in page_numbers:
        if page_number in seen:
            continue
        seen.add(page_number)
        path = render_pdf_page(filepath, file_hash, page_number, dpi=dpi)
        if path:
            rendered.append(path)
    return rendered


def compute_hash(filepath: str) -> str:
    h = hashlib.md5()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()
