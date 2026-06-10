"""Phase 1 W7: contract tests for the SupabaseStorage HTTPX implementation.

No live HTTP fires — we inject a ``_FakeHttpClient`` that records every
request and returns canned responses. The tests verify:

  - the exact URLs / headers / bodies we send to Supabase Storage
  - we correctly parse both response field-name variants Supabase has
    historically used (``url`` vs ``signedURL``)
  - relative paths in the response are absolutized against the
    project URL
  - HEAD returns ``content_hash=""`` (per the design relaxation) +
    correct size
  - 404 / 5xx / network failures collapse to ``None`` from head/read,
    raise from sign* (caller is expected to retry)

This is enough to catch the structural breaks ("we sent the wrong URL")
without standing up a real Supabase instance.
"""
from __future__ import annotations

import json
import sys
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


ROOT = Path(__file__).resolve().parents[2]
BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(BACKEND))

from services.storage import (  # noqa: E402
    SignedDownload,
    SignedUpload,
    StoredObject,
    SupabaseStorage,
)


# ── fake HTTPX client ────────────────────────────────────────────────


@dataclass
class _FakeResponse:
    status_code: int = 200
    _json: Any = None
    _content: bytes = b""
    text: str = ""

    def json(self):
        return self._json

    @property
    def content(self):
        return self._content

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}: {self.text}")


@dataclass
class _RecordedCall:
    method: str
    url: str
    headers: dict
    json_body: Optional[Any] = None


class _FakeHttpClient:
    """Records every call and returns the next queued response.

    Tests configure responses via .responses (a queue). For methods
    that don't take JSON bodies, json_body is None."""

    def __init__(self) -> None:
        self.calls: list[_RecordedCall] = []
        self.responses: list[_FakeResponse] = []

    def _next(self) -> _FakeResponse:
        if not self.responses:
            raise AssertionError("no canned response queued")
        return self.responses.pop(0)

    def post(self, url, *, headers=None, json=None):
        self.calls.append(_RecordedCall("POST", url, dict(headers or {}), json))
        return self._next()

    def get(self, url, *, headers=None):
        self.calls.append(_RecordedCall("GET", url, dict(headers or {}), None))
        return self._next()


# ── tests ────────────────────────────────────────────────────────────


PROJECT = "https://abcde12345.supabase.co"
KEY = "service-role-test-key-1234567890abcdef"


def _make_storage(http: _FakeHttpClient) -> SupabaseStorage:
    return SupabaseStorage(
        project_url=PROJECT,
        service_role_key=KEY,
        bucket="wiki",
        http_client=http,
    )


class SupabaseStorageContractTests(unittest.TestCase):

    # ---- construction ---------------------------------------------------

    def test_empty_key_rejected(self):
        with self.assertRaises(ValueError):
            SupabaseStorage(project_url=PROJECT, service_role_key="")

    def test_empty_url_rejected(self):
        with self.assertRaises(ValueError):
            SupabaseStorage(project_url="", service_role_key=KEY)

    # ---- sign_upload ----------------------------------------------------

    def test_sign_upload_sends_correct_request(self):
        http = _FakeHttpClient()
        http.responses.append(_FakeResponse(
            status_code=200,
            _json={"url": "/object/upload/sign/wiki/u1/papers/0001.md?token=ABC"},
        ))
        s = _make_storage(http)
        result = s.sign_upload(storage_path="u1/papers/0001.md", ttl_seconds=600)

        # request shape
        self.assertEqual(len(http.calls), 1)
        call = http.calls[0]
        self.assertEqual(call.method, "POST")
        self.assertEqual(
            call.url,
            f"{PROJECT}/storage/v1/object/upload/sign/wiki/u1/papers/0001.md",
        )
        self.assertEqual(call.headers["Authorization"], f"Bearer {KEY}")
        self.assertEqual(call.headers["apikey"], KEY)
        # Upsert is signalled via HEADER (x-upsert: true), NOT a body
        # field. Putting upsert in the body silently bakes upsert=false
        # into the returned token and any second upload to the same
        # path returns 409 Duplicate → 400 to our client.
        self.assertEqual(call.headers.get("x-upsert"), "true")
        self.assertEqual(call.json_body, {})

        # response shape
        self.assertIsInstance(result, SignedUpload)
        self.assertEqual(
            result.url,
            f"{PROJECT}/storage/v1/object/upload/sign/wiki/u1/papers/0001.md?token=ABC",
        )

    def test_sign_upload_handles_absolute_url_in_response(self):
        # Some Supabase versions return a full URL; we should keep it
        # as-is rather than double-prefixing.
        http = _FakeHttpClient()
        full_url = "https://abcde12345.supabase.co/storage/v1/object/upload/sign/wiki/x.md?token=XYZ"
        http.responses.append(_FakeResponse(
            status_code=200, _json={"url": full_url},
        ))
        s = _make_storage(http)
        result = s.sign_upload(storage_path="x.md")
        self.assertEqual(result.url, full_url)

    def test_sign_upload_accepts_signedURL_field_variant(self):
        # Older Supabase versions used "signedURL"; the impl falls back.
        http = _FakeHttpClient()
        http.responses.append(_FakeResponse(
            status_code=200,
            _json={"signedURL": "/object/upload/sign/wiki/foo.md?token=T"},
        ))
        s = _make_storage(http)
        result = s.sign_upload(storage_path="foo.md")
        self.assertIn("foo.md", result.url)

    def test_sign_upload_raises_on_missing_url_field(self):
        http = _FakeHttpClient()
        http.responses.append(_FakeResponse(status_code=200, _json={"weird": "shape"}))
        s = _make_storage(http)
        with self.assertRaises(RuntimeError):
            s.sign_upload(storage_path="x.md")

    def test_sign_upload_raises_on_5xx(self):
        http = _FakeHttpClient()
        http.responses.append(_FakeResponse(status_code=500))
        s = _make_storage(http)
        with self.assertRaises(RuntimeError):
            s.sign_upload(storage_path="x.md")

    # ---- sign_download --------------------------------------------------

    def test_sign_download_sends_correct_request(self):
        http = _FakeHttpClient()
        http.responses.append(_FakeResponse(
            status_code=200,
            _json={"signedURL": "/object/sign/wiki/u1/papers/0001.md?token=XYZ"},
        ))
        s = _make_storage(http)
        result = s.sign_download(storage_path="u1/papers/0001.md", ttl_seconds=300)

        self.assertEqual(len(http.calls), 1)
        call = http.calls[0]
        self.assertEqual(call.method, "POST")
        self.assertEqual(
            call.url,
            f"{PROJECT}/storage/v1/object/sign/wiki/u1/papers/0001.md",
        )
        self.assertEqual(call.json_body, {"expiresIn": 300})
        self.assertIsInstance(result, SignedDownload)
        self.assertIn("XYZ", result.url)

    def test_sign_download_accepts_url_field_variant(self):
        http = _FakeHttpClient()
        http.responses.append(_FakeResponse(
            status_code=200,
            _json={"url": "/object/sign/wiki/foo.md?token=T"},
        ))
        s = _make_storage(http)
        result = s.sign_download(storage_path="foo.md")
        self.assertIn("foo.md", result.url)

    # ---- head -----------------------------------------------------------

    def test_head_returns_size_and_empty_hash(self):
        http = _FakeHttpClient()
        http.responses.append(_FakeResponse(
            status_code=200,
            _json={"size": 1234, "contentType": "text/markdown"},
        ))
        s = _make_storage(http)
        result = s.head("u1/papers/0001.md")
        self.assertIsInstance(result, StoredObject)
        self.assertEqual(result.size_bytes, 1234)
        self.assertEqual(result.content_hash, "",
                         "real Supabase does not expose sha256; should return ''")

        call = http.calls[0]
        self.assertEqual(call.method, "GET")
        self.assertEqual(
            call.url,
            f"{PROJECT}/storage/v1/object/info/authenticated/wiki/u1/papers/0001.md",
        )

    def test_head_handles_metadata_size_field(self):
        # Supabase has historically nested size under "metadata.size".
        http = _FakeHttpClient()
        http.responses.append(_FakeResponse(
            status_code=200,
            _json={"metadata": {"size": 5678}},
        ))
        s = _make_storage(http)
        result = s.head("x.md")
        self.assertEqual(result.size_bytes, 5678)

    def test_head_returns_none_on_404(self):
        http = _FakeHttpClient()
        http.responses.append(_FakeResponse(status_code=404, text="not found"))
        s = _make_storage(http)
        result = s.head("missing.md")
        self.assertIsNone(result)

    def test_head_returns_none_on_5xx(self):
        http = _FakeHttpClient()
        http.responses.append(_FakeResponse(status_code=503))
        s = _make_storage(http)
        result = s.head("x.md")
        self.assertIsNone(result)

    def test_head_returns_none_on_network_error(self):
        class _Broken:
            def get(self, *a, **kw):
                raise RuntimeError("connection refused")

        s = SupabaseStorage(
            project_url=PROJECT, service_role_key=KEY,
            http_client=_Broken(),
        )
        self.assertIsNone(s.head("anywhere.md"))

    # ---- read_bytes -----------------------------------------------------

    def test_read_bytes_returns_content(self):
        http = _FakeHttpClient()
        http.responses.append(_FakeResponse(
            status_code=200, _content=b"# hello\n\nworld",
        ))
        s = _make_storage(http)
        result = s.read_bytes("u1/concepts/0001-rope.md")
        self.assertEqual(result, b"# hello\n\nworld")

        call = http.calls[0]
        self.assertEqual(call.method, "GET")
        self.assertEqual(
            call.url,
            f"{PROJECT}/storage/v1/object/authenticated/wiki/u1/concepts/0001-rope.md",
        )
        self.assertEqual(call.headers["Authorization"], f"Bearer {KEY}")

    def test_read_bytes_returns_none_on_404(self):
        http = _FakeHttpClient()
        http.responses.append(_FakeResponse(status_code=404))
        s = _make_storage(http)
        self.assertIsNone(s.read_bytes("missing.md"))

    def test_read_bytes_returns_none_on_network_error(self):
        class _Broken:
            def get(self, *a, **kw):
                raise RuntimeError("timeout")

        s = SupabaseStorage(
            project_url=PROJECT, service_role_key=KEY,
            http_client=_Broken(),
        )
        self.assertIsNone(s.read_bytes("anywhere.md"))


if __name__ == "__main__":
    unittest.main()
