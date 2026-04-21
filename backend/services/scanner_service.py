from pathlib import Path
from sqlalchemy.orm import Session
from models import Paper
from services.pdf_service import compute_hash


def scan_directory(directory: str, db: Session) -> dict:
    """Scan directory for new PDF papers not yet in DB. Returns stats."""
    scan_path = Path(directory)
    if not scan_path.exists():
        raise FileNotFoundError(f"Directory not found: {directory}")

    existing_paths = {row.filepath for row in db.query(Paper.filepath).all()}
    existing_hashes = {row.file_hash for row in db.query(Paper.file_hash).all()}

    added = 0
    for ext in ("*.pdf", "*.PDF"):
        for pdf_path in scan_path.rglob(ext):
            filepath_str = str(pdf_path)
            if filepath_str in existing_paths:
                continue
            try:
                file_hash = compute_hash(filepath_str)
                if file_hash in existing_hashes:
                    continue  # same content already exists elsewhere
                paper = Paper(
                    filepath=filepath_str,
                    filename=pdf_path.name,
                    file_hash=file_hash,
                    processed=False,
                )
                db.add(paper)
                existing_hashes.add(file_hash)
                added += 1
            except Exception:
                continue

    db.commit()

    total = db.query(Paper).count()
    unprocessed = db.query(Paper).filter(Paper.processed == False).count()
    return {"new_found": added, "total": total, "unprocessed": unprocessed}
