"""Upload + serve images that users paste/drop into markdown notes.

Images are stored as opaque files under `data/artifacts/note_images/`, named
by UUID so the same clipboard screenshot pasted twice doesn't collide. The
markdown stored in `papers.notes` references them via `/api/note_images/<fn>`.
"""
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile, File
from fastapi.responses import FileResponse

from path_utils import ARTIFACTS_DIR


router = APIRouter(prefix="/api", tags=["note-images"])


NOTE_IMAGES_DIR = ARTIFACTS_DIR / "note_images"
# Clipboard screenshots are usually PNG; phones paste JPEG/HEIC; drag-drop can
# be SVG or webp. Keep the allowlist tight — this is user-authored content.
ALLOWED_EXT = {"png", "jpg", "jpeg", "gif", "webp", "svg"}
ALLOWED_MIME_PREFIX = "image/"
MAX_BYTES = 10 * 1024 * 1024  # 10MB


def _safe_ext(upload: UploadFile) -> str:
    name = upload.filename or ""
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext in ALLOWED_EXT:
        return ext
    ctype = (upload.content_type or "").lower()
    if ctype.startswith(ALLOWED_MIME_PREFIX):
        mapped = ctype.split("/", 1)[1]
        if mapped == "jpeg":
            return "jpg"
        if mapped in ALLOWED_EXT:
            return mapped
    return "png"


@router.post("/note_images")
async def upload_note_image(file: UploadFile = File(...)):
    if not (file.content_type or "").lower().startswith(ALLOWED_MIME_PREFIX):
        raise HTTPException(status_code=400, detail="仅支持图片类型")

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="空文件")
    if len(data) > MAX_BYTES:
        raise HTTPException(status_code=413, detail=f"图片过大（>{MAX_BYTES // (1024 * 1024)}MB）")

    NOTE_IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    ext = _safe_ext(file)
    filename = f"{uuid.uuid4().hex}.{ext}"
    out_path = NOTE_IMAGES_DIR / filename
    out_path.write_bytes(data)

    return {
        "filename": filename,
        "url": f"/api/note_images/{filename}",
        "size": len(data),
    }


@router.get("/note_images/{filename}")
def serve_note_image(filename: str):
    # Reject path traversal and absolute paths outright.
    safe = Path(filename).name
    if safe != filename or not safe:
        raise HTTPException(status_code=400, detail="非法文件名")
    path = NOTE_IMAGES_DIR / safe
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="图片不存在")
    ext = path.suffix.lower().lstrip(".")
    media_type = {
        "png": "image/png",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "gif": "image/gif",
        "webp": "image/webp",
        "svg": "image/svg+xml",
    }.get(ext, "application/octet-stream")
    return FileResponse(str(path), media_type=media_type)
