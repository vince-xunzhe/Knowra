import hashlib
from pathlib import Path
from typing import Optional

import pypdf
import pypdfium2

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


def render_first_page(filepath: str, file_hash: str, dpi: int = 150) -> Optional[str]:
    """
    Render the first page of a PDF to PNG. Returns the output path or None on failure.
    Uses file_hash as cache key so we don't re-render the same PDF.
    """
    output_path = ARTIFACT_DIR / f"{file_hash}_page1.png"
    if output_path.exists():
        return str(output_path)

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
        return str(output_path)
    except Exception:
        return None


def compute_hash(filepath: str) -> str:
    h = hashlib.md5()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()
