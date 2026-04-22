"""Garbage-collect note image files that no note references anymore.

Images are uploaded as a side effect of editing a note block. When the block
(or just the image reference in it) is later removed, the file on disk has
no pointer — this module cleans it up.

Scope is intentionally narrow: we only delete files that were referenced in
the OLD notes but not in the NEW notes AND aren't referenced by any other
paper. That avoids racing with images another paper just uploaded but hasn't
saved into its notes yet.
"""
import re
from pathlib import Path

from sqlalchemy.orm import Session

from models import Paper
from routers.note_images import NOTE_IMAGES_DIR


# Match filenames in markdown image URLs: ![alt](/api/note_images/xxxx.png)
# Allowlist matches the UUID-hex + extension we generate on upload.
_REF_RE = re.compile(r"/api/note_images/([A-Za-z0-9._-]+)")


def _refs_in(text: str | None) -> set[str]:
    if not text:
        return set()
    return set(_REF_RE.findall(text))


def gc_on_notes_update(
    db: Session,
    paper_id: int,
    old_notes: str | None,
    new_notes: str | None,
) -> int:
    """Delete image files that were in `old_notes`, are gone from `new_notes`,
    and are not referenced by any other paper. Returns files deleted."""
    removed = _refs_in(old_notes) - _refs_in(new_notes)
    if not removed:
        return 0

    other_refs: set[str] = set()
    rows = db.query(Paper.notes).filter(Paper.id != paper_id).all()
    for (notes,) in rows:
        other_refs |= _refs_in(notes)

    deletable = removed - other_refs
    if not deletable:
        return 0

    deleted = 0
    for fn in deletable:
        # Defense in depth: reject anything that would escape NOTE_IMAGES_DIR.
        safe = Path(fn).name
        if safe != fn or not safe:
            continue
        path = NOTE_IMAGES_DIR / safe
        try:
            if path.is_file():
                path.unlink()
                deleted += 1
        except OSError:
            pass
    return deleted
