"""Storage abstraction for the cloud-side sync flow.

The sync router never talks to Supabase Storage directly; instead it
asks an ``ObjectStorage`` instance for signed PUT URLs (prepare time)
and verifies hashes after the client claims an upload completed (commit
time). This indirection lets us:

  - Unit-test the router with a deterministic in-memory backend
  - Swap providers in the future (R2, GCS, S3) without touching
    routers/services
  - Keep secret credentials out of router code

Two implementations ship:

  ``SupabaseStorage``     — real, talks to Supabase Storage REST API
  ``InMemoryStorage``     — for tests; records "uploads" in a dict

The interface is intentionally narrow: sign + head. Anything richer
(deletes, listings) is added in later phases when concrete callers
need it.
"""
from __future__ import annotations

import hashlib
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional, Protocol


@dataclass(frozen=True)
class SignedUpload:
    """A pre-signed PUT URL the client can use to upload one file."""

    url: str
    expires_at: datetime
    method: str = "PUT"
    headers: tuple[tuple[str, str], ...] = (
        ("Content-Type", "text/markdown"),
        ("x-upsert", "true"),
    )

    @property
    def header_dict(self) -> dict[str, str]:
        return dict(self.headers)


@dataclass(frozen=True)
class StoredObject:
    """Result of HEAD-ing an object — used at commit time to verify
    the client's content_hash claim against what Storage actually
    holds."""

    content_hash: str
    size_bytes: int


@dataclass(frozen=True)
class SignedDownload:
    """A pre-signed GET URL the client can use to download one file."""

    url: str
    expires_at: datetime
    method: str = "GET"


def ascii_storage_key(rel_path: str) -> str:
    """Make a wiki ``rel_path`` safe to use as a Supabase Storage object key.

    Supabase Storage rejects keys with non-ASCII characters
    (``400 InvalidKey``, verified against the live bucket). Our wiki
    filenames embed the concept title, which is frequently Chinese
    (e.g. ``concepts/0029-视觉编码器.md``). Hex-escape every non-ASCII code
    point to ``_u<hex>_`` while leaving ASCII (incl. ``/ - . _`` and
    alphanumerics) untouched. The result is pure ASCII (Supabase-valid),
    deterministic and collision-free, so upload / HEAD / download all agree
    on the same key. The human-readable ``rel_path`` is unchanged in the DB;
    only the physical storage key is transformed.
    """
    return "".join(
        ch if ord(ch) < 128 else f"_u{ord(ch):x}_"
        for ch in rel_path
    )


class ObjectStorage(Protocol):
    """The single seam between sync router and the file backend."""

    def sign_upload(self, *, storage_path: str, ttl_seconds: int = 600) -> SignedUpload:
        """Return a pre-signed PUT URL for ``storage_path``. The path is
        relative to the bucket root and conventionally begins with
        ``wiki/<user_id>/`` so RLS / bucket policies can isolate."""
        ...

    def sign_download(
        self, *, storage_path: str, ttl_seconds: int = 600
    ) -> SignedDownload:
        """Return a pre-signed GET URL for ``storage_path``.

        Used by the mobile snapshot endpoint and by the single-file
        wiki GET endpoint so clients can pull file content directly
        from Storage rather than proxying through our FastAPI."""
        ...

    def head(self, storage_path: str) -> Optional[StoredObject]:
        """Verify an object exists and return its metadata. Returns
        ``None`` if the path is empty (used by commit to detect a
        client that claims an upload happened but didn't)."""
        ...

    def read_bytes(self, storage_path: str) -> Optional[bytes]:
        """Return the object's raw bytes, or ``None`` if absent.

        Used server-side by the cloud Ask handler to fetch wiki .md
        content for inclusion in the LLM prompt. For mobile snapshot
        / single-file retrieval the client uses the signed download
        URL instead (no server-side bandwidth)."""
        ...


# ── in-memory test backend ────────────────────────────────────────────


class InMemoryStorage:
    """Thread-safe Storage stand-in used by unit tests.

    ``sign_upload`` returns a deterministic fake URL. Tests can drive
    ``simulate_upload(...)`` to populate the backing dict before
    calling ``head``. This avoids any HTTP round-trip while still
    exercising every branch of the router."""

    _SCHEMA_PREFIX = "memstore://"

    def __init__(self, http_base_url: Optional[str] = None) -> None:
        self._lock = threading.Lock()
        # storage_path → (sha256_hex_content_hash, size_bytes)
        self._objects: dict[str, StoredObject] = {}
        # storage_path → raw bytes for read_bytes() support
        self._content: dict[str, bytes] = {}
        # storage_path → expires_at; URL is valid only until this time
        self._signed: dict[str, datetime] = {}
        # When set, signed URLs are real HTTP URLs pointing at a backdoor
        # endpoint (see backend/routers/test_storage.py). This lets the
        # e2e harness PUT bytes via real HTTP rather than calling
        # simulate_upload() directly. The memstore:// scheme stays the
        # default so existing unit tests don't change behavior.
        self._http_base_url = http_base_url.rstrip("/") if http_base_url else None

    def _format_url(self, storage_path: str, exp_ts: int, *, download: bool = False) -> str:
        if self._http_base_url:
            qs = f"exp={exp_ts}" + ("&get=1" if download else "")
            return f"{self._http_base_url}/api/_test_storage/{storage_path}?{qs}"
        return f"{self._SCHEMA_PREFIX}{storage_path}?{'get&' if download else ''}exp={exp_ts}"

    def sign_upload(self, *, storage_path: str, ttl_seconds: int = 600) -> SignedUpload:
        exp = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
        with self._lock:
            self._signed[storage_path] = exp
        return SignedUpload(
            url=self._format_url(storage_path, int(exp.timestamp())),
            expires_at=exp,
        )

    def sign_download(
        self, *, storage_path: str, ttl_seconds: int = 600
    ) -> SignedDownload:
        # Downloads don't need a corresponding _signed entry; the test
        # backend doesn't enforce expiry, the URL just encodes it.
        exp = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
        return SignedDownload(
            url=self._format_url(storage_path, int(exp.timestamp()), download=True),
            expires_at=exp,
        )

    def head(self, storage_path: str) -> Optional[StoredObject]:
        with self._lock:
            return self._objects.get(storage_path)

    def read_bytes(self, storage_path: str) -> Optional[bytes]:
        with self._lock:
            return self._content.get(storage_path)

    # ── test helpers ─────────────────────────────────────────────

    def simulate_upload(self, storage_path: str, content: bytes) -> StoredObject:
        """Test-only: pretend the client PUT raw bytes to ``storage_path``.

        Stores the hash, size, AND content so subsequent ``head`` and
        ``read_bytes`` calls both return the truth.

        Hash is raw hex (no ``sha256:`` prefix) — matches the format
        the desktop produces in ``backend/routers/sync_local.py`` and
        what the cloud sync router compares against in ``_commit``.
        Keeping the format aligned end-to-end avoids the hash-mismatch
        rejection the test_sync_router used to work around manually."""
        digest = hashlib.sha256(content).hexdigest()
        obj = StoredObject(content_hash=digest, size_bytes=len(content))
        with self._lock:
            self._objects[storage_path] = obj
            self._content[storage_path] = content
        return obj

    def delete_simulated(self, storage_path: str) -> None:
        with self._lock:
            self._objects.pop(storage_path, None)
            self._content.pop(storage_path, None)


# ── real Supabase Storage (W7) ───────────────────────────────────────


class SupabaseStorage:
    """HTTPX-backed implementation against the Supabase Storage REST API.

    Endpoints used (relative to ``{project_url}/storage/v1``):

      POST /object/upload/sign/{bucket}/{path}    sign a one-shot PUT URL
      POST /object/sign/{bucket}/{path}           sign a one-shot GET URL
      GET  /object/info/authenticated/{bucket}/{path}   object metadata (HEAD-like)
      GET  /object/authenticated/{bucket}/{path}        raw download (service-role auth)

    The service-role key is required for the upload-sign endpoint
    (it bypasses RLS); for the download path we use the same key for
    simplicity, but the response is the public CDN URL that needs no
    further auth.

    Two pragmatic relaxations vs the InMemoryStorage:

      1. ``head()`` returns ``content_hash=""`` — Supabase exposes an
         etag (md5-ish) but not sha256. The sync router treats an
         empty hash as "skip verification" so this still wires up
         end-to-end.
      2. Errors are logged but not raised — callers see ``None`` from
         ``head`` / ``read_bytes`` and react accordingly (commit
         rejects, ask falls back to empty context).
    """

    DEFAULT_BUCKET = "wiki"
    DEFAULT_TIMEOUT = 15.0

    def __init__(
        self,
        *,
        project_url: str,
        service_role_key: str,
        bucket: str = DEFAULT_BUCKET,
        timeout_seconds: float = DEFAULT_TIMEOUT,
        # Tests pass a custom client to avoid live HTTP. Production
        # uses the default.
        http_client: Optional[object] = None,
    ) -> None:
        if not project_url or not service_role_key:
            raise ValueError(
                "SupabaseStorage requires project_url + service_role_key"
            )
        self._base = project_url.rstrip("/") + "/storage/v1"
        self._key = service_role_key
        self._bucket = bucket
        self._client = http_client or _new_httpx_client(timeout_seconds)

    # ── auth ────────────────────────────────────────────────────

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._key}",
            "apikey": self._key,
        }

    def _absolutize(self, rel: str) -> str:
        """Supabase responses use relative paths like
        ``/object/upload/sign/…``; prepend the project base URL."""
        if rel.startswith("http://") or rel.startswith("https://"):
            return rel
        if rel.startswith("/"):
            return self._base + rel
        return f"{self._base}/{rel}"

    # ── upload signing ─────────────────────────────────────────

    def sign_upload(self, *, storage_path: str, ttl_seconds: int = 600) -> SignedUpload:
        # Supabase's upload-sign endpoint signals upsert via the
        # ``x-upsert: true`` HEADER, not a body field. (Match what the
        # supabase-js SDK does in ``createSignedUploadUrl``.) Putting
        # ``upsert`` in the body silently bakes ``upsert=false`` into
        # the returned token, so a 2nd sync of the same path would
        # fail with 409 Duplicate when the previous upload partially
        # succeeded. Upserting by default is safe for our use case:
        # the row's content_hash is what we trust, and Storage just
        # holds bytes addressed by user_id/rel_path.
        # The body must still be valid JSON; ``expiresIn`` is rejected
        # here (that's a sign_DOWNLOAD parameter — the upload URL TTL
        # is fixed by Supabase at ~2h).
        url = f"{self._base}/object/upload/sign/{self._bucket}/{storage_path}"
        headers = {**self._headers(), "x-upsert": "true"}
        resp = self._client.post(url, headers=headers, json={})
        resp.raise_for_status()
        body = resp.json()
        signed_path = body.get("url") or body.get("signedURL") or ""
        if not signed_path:
            raise RuntimeError(f"Supabase upload-sign returned no url: {body!r}")
        return SignedUpload(
            url=self._absolutize(signed_path),
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds),
        )

    # ── download signing ──────────────────────────────────────

    def sign_download(
        self, *, storage_path: str, ttl_seconds: int = 600
    ) -> SignedDownload:
        url = f"{self._base}/object/sign/{self._bucket}/{storage_path}"
        resp = self._client.post(
            url,
            headers=self._headers(),
            json={"expiresIn": ttl_seconds},
        )
        resp.raise_for_status()
        body = resp.json()
        signed_path = body.get("signedURL") or body.get("url") or ""
        if not signed_path:
            raise RuntimeError(f"Supabase sign returned no signedURL: {body!r}")
        return SignedDownload(
            url=self._absolutize(signed_path),
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds),
        )

    # ── HEAD ───────────────────────────────────────────────────

    def head(self, storage_path: str) -> Optional[StoredObject]:
        """Return object metadata or ``None`` if absent.

        Note: Supabase exposes ``contentLength`` + etag but no sha256
        digest. We return ``content_hash=""`` and let the sync router
        treat empty hash as "skip verification". The DB-side
        ``wiki_files.content_hash`` recorded by the client at prepare
        time is the source of truth."""
        url = (
            f"{self._base}/object/info/authenticated/{self._bucket}/{storage_path}"
        )
        try:
            resp = self._client.get(url, headers=self._headers())
        except Exception as exc:  # noqa: BLE001
            import logging
            logging.getLogger(__name__).warning(
                "supabase head failed for %s: %s", storage_path, exc
            )
            return None
        if resp.status_code >= 400:
            # Log the miss (path + status) so a sync that rejects uploads
            # is diagnosable from the server logs — 404 (genuinely absent)
            # vs 400/403 (bad path / auth) point at very different causes.
            import logging
            logging.getLogger(__name__).warning(
                "supabase head MISS %s -> HTTP %s", storage_path, resp.status_code
            )
            return None
        body = resp.json()
        size = int(
            body.get("size")
            or body.get("contentLength")
            or body.get("metadata", {}).get("size", 0)
            or 0
        )
        return StoredObject(content_hash="", size_bytes=size)

    # ── raw download ──────────────────────────────────────────

    def read_bytes(self, storage_path: str) -> Optional[bytes]:
        """Stream the object's bytes back via the service-role auth
        endpoint. Used by the cloud Ask handler to pull wiki .md
        content for context."""
        url = f"{self._base}/object/authenticated/{self._bucket}/{storage_path}"
        try:
            resp = self._client.get(url, headers=self._headers())
        except Exception as exc:  # noqa: BLE001
            import logging
            logging.getLogger(__name__).warning(
                "supabase read_bytes failed for %s: %s", storage_path, exc
            )
            return None
        if resp.status_code == 404:
            return None
        if resp.status_code >= 400:
            return None
        return resp.content


def _new_httpx_client(timeout_seconds: float):
    """Lazy import of httpx so the test path that uses InMemoryStorage
    doesn't pay the import cost."""
    import httpx
    return httpx.Client(timeout=timeout_seconds)


# ── module-level lookup ───────────────────────────────────────────────


_STORAGE_CACHE: Optional[ObjectStorage] = None


def get_storage() -> ObjectStorage:
    """Return the storage backend appropriate for the deploy mode.

    Tests can override via ``set_storage(backend)``; production reads
    env vars. Cached after first call."""
    global _STORAGE_CACHE
    if _STORAGE_CACHE is not None:
        return _STORAGE_CACHE
    mode = os.environ.get("KNOWRA_STORAGE_BACKEND", "").lower()
    if mode == "memory":
        # Optional: hand the in-memory backend an HTTP base URL so its
        # signed URLs point at the /_test_storage backdoor router. Used
        # by the e2e smoke harness to exercise the real PUT path without
        # needing Supabase. Defaults to None → memstore:// scheme so
        # existing unit tests are unaffected.
        http_base = os.environ.get("KNOWRA_MEMORY_STORAGE_HTTP_BASE") or None
        _STORAGE_CACHE = InMemoryStorage(http_base_url=http_base)
    else:
        project_url = os.environ.get("SUPABASE_PROJECT_URL", "")
        service_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
        if not project_url or not service_key:
            raise RuntimeError(
                "production cloud mode requires SUPABASE_PROJECT_URL and "
                "SUPABASE_SERVICE_ROLE_KEY; or set KNOWRA_STORAGE_BACKEND=memory "
                "for local/test use"
            )
        _STORAGE_CACHE = SupabaseStorage(
            project_url=project_url,
            service_role_key=service_key,
        )
    return _STORAGE_CACHE


def set_storage(backend: ObjectStorage) -> None:
    """Override the cached backend. Tests use this; production should
    not."""
    global _STORAGE_CACHE
    _STORAGE_CACHE = backend


def reset_storage_cache() -> None:
    """Drop the cached backend instance. Used by tests."""
    global _STORAGE_CACHE
    _STORAGE_CACHE = None
