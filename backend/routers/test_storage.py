"""HTTP wrapper around InMemoryStorage — for e2e testing only.

Mounted by ``main.py`` *only* when ``KNOWRA_STORAGE_BACKEND=memory``,
which is itself a flag we'd never set in production. So this router
cannot accidentally end up exposed on a real cloud deploy: production
uses ``SupabaseStorage`` (HTTPX → Supabase) and never instantiates
``InMemoryStorage``.

Why this exists: the in-memory backend's signed URLs are fake
``memstore://`` strings — convenient for unit tests that call
``simulate_upload()`` directly, but unusable when the test driver is
in a *different* process (it can't reach the in-process dict). For
real HTTP end-to-end smoke (mobile / desktop hitting a local cloud
binary), we need the signed URLs to actually resolve to something an
HTTPX client can PUT bytes to. That's all this router is.

The PUT handler just calls ``simulate_upload()``; the GET handler
just calls ``read_bytes()``. No auth check — the signed URL itself is
the auth, same model as the real Supabase signed URLs.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response

from services.storage import InMemoryStorage, get_storage


router = APIRouter(prefix="/api/_test_storage", tags=["test"])


def _require_memory_storage() -> InMemoryStorage:
    storage = get_storage()
    if not isinstance(storage, InMemoryStorage):
        # Defense in depth — main.py only mounts this router when the
        # backend is in memory mode, but if something flipped at
        # runtime we still refuse to serve.
        raise HTTPException(status_code=404, detail="not available")
    return storage


@router.put("/{storage_path:path}")
async def put_object(storage_path: str, request: Request):
    storage = _require_memory_storage()
    body = await request.body()
    obj = storage.simulate_upload(storage_path, body)
    return {
        "ok": True,
        "storage_path": storage_path,
        "size_bytes": obj.size_bytes,
        "content_hash": obj.content_hash,
    }


@router.get("/{storage_path:path}")
def get_object(storage_path: str):
    storage = _require_memory_storage()
    body = storage.read_bytes(storage_path)
    if body is None:
        raise HTTPException(status_code=404, detail="not found")
    return Response(content=body, media_type="application/octet-stream")
