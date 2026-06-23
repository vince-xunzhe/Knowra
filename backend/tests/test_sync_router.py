"""End-to-end tests for the cloud sync router (prepare → commit).

Uses:
  - SQLite in-memory as a Postgres stand-in (RLS isn't enforced, but
    the router logic itself doesn't depend on it; RLS is a
    defense-in-depth layer at the DB boundary)
  - InMemoryStorage as the file backend (deterministic, no HTTP)

Covers:
  - happy path: prepare → simulated PUTs → commit returns revision 1
  - idempotency: repeated commit on same session_id returns same revision
  - content_hash dedup: unchanged files don't appear in uploads_required
  - storage HEAD mismatch → commit rejects that wiki file while still
    committing valid metadata rows
  - session expiry → commit returns 410
  - cross-tenant: user B can't commit user A's session
  - per-row user_id mismatch → validation_errors in prepare response
"""
from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

ROOT = Path(__file__).resolve().parents[2]
BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(BACKEND))

import auth_deps  # noqa: E402
from cloud_models import (  # noqa: E402
    CloudKnowledgeEdge,
    CloudKnowledgeNode,
    CloudPaper,
    SyncSession,
    WikiFile,
    init_cloud_schema,
)
from model_gateway.auth import AuthenticatedUser  # noqa: E402
from routers import sync as sync_router  # noqa: E402
from services.storage import InMemoryStorage, set_storage, reset_storage_cache  # noqa: E402


USER_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
USER_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


def _user_a() -> AuthenticatedUser:
    return AuthenticatedUser(user_id=USER_A, email="a@x.com", role="authenticated")


def _user_b() -> AuthenticatedUser:
    return AuthenticatedUser(user_id=USER_B, email="b@x.com", role="authenticated")


# ── test app factory ──────────────────────────────────────────────────


def _make_app_and_storage():
    """Build a FastAPI app with the sync router mounted against an
    in-memory SQLite engine + InMemoryStorage. Returns (app, db_session_factory, storage)."""
    # StaticPool keeps one shared connection so the :memory: DB persists
    # across sessions during the test (default behavior creates a new
    # connection per session, each with a fresh empty :memory: DB).
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    init_cloud_schema(engine)
    SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    storage = InMemoryStorage()
    set_storage(storage)

    app = FastAPI()

    def override_db():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    def override_user_a():
        return _user_a()

    app.include_router(sync_router.router)
    app.dependency_overrides[sync_router.get_cloud_db] = override_db
    app.dependency_overrides[auth_deps.current_user] = override_user_a

    return app, SessionLocal, storage


# ── tests ─────────────────────────────────────────────────────────────


class SyncRouterTests(unittest.TestCase):
    def setUp(self):
        os.environ["KNOWRA_STORAGE_BACKEND"] = "memory"
        reset_storage_cache()
        self.app, self.SessionLocal, self.storage = _make_app_and_storage()
        self.client = TestClient(self.app)

    def tearDown(self):
        reset_storage_cache()
        os.environ.pop("KNOWRA_STORAGE_BACKEND", None)

    # ---- happy path ----------------------------------------------------

    def _basic_payload(self, *, paper_id="paper-a", wiki_path="papers/foo.md"):
        return {
            "api_version": "1",
            "device_id": "dev-1",
            "tables": {
                "papers": [
                    {
                        "id": paper_id,
                        "user_id": USER_A,
                        "filepath": "/tmp/foo.pdf",
                        "filename": "foo.pdf",
                        "file_hash": "hash1",
                        "title": "Foo",
                        "updated_at": "2026-05-29T10:00:00Z",
                    }
                ],
                "knowledge_nodes": [],
                "knowledge_edges": [],
                "wiki_files": [
                    {
                        "id": "wf-1",
                        "user_id": USER_A,
                        "kind": "paper",
                        "rel_path": wiki_path,
                        "content_hash": "sha256:abc",
                        "size_bytes": 12,
                        "title": "Foo md",
                        "paper_id": paper_id,
                        "updated_at": "2026-05-29T10:00:00Z",
                    }
                ],
            },
        }

    def test_prepare_returns_session_id_and_upload_url(self):
        resp = self.client.post("/api/sync/prepare", json=self._basic_payload())
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertTrue(body["sync_session_id"])
        self.assertEqual(len(body["uploads_required"]), 1)
        self.assertEqual(body["uploads_required"][0]["rel_path"], "papers/foo.md")
        self.assertTrue(body["uploads_required"][0]["upload_url"].startswith("memstore://"))
        self.assertEqual(body["uploads_skipped"], [])
        self.assertEqual(body["validation_errors"], [])

    def test_full_flow_prepare_put_commit(self):
        # Step 1: prepare
        prep = self.client.post("/api/sync/prepare", json=self._basic_payload()).json()
        sid = prep["sync_session_id"]

        # Step 2: simulate the client PUT to Storage
        self.storage.simulate_upload(
            f"{USER_A}/papers/foo.md",
            b"some markdown content here",
        )
        # Update the simulated content_hash to match what we claim:
        # Tests want to use sha256:abc as the expected hash; simulate
        # again with whatever bytes hash to sha256:abc. Easier: just
        # set the hash directly via the public helper. We do it by
        # overriding the storage's hash table.
        # Simpler approach: re-prepare with a content_hash that matches
        # what InMemoryStorage actually computes.
        # For this test we just override the stored hash to match.
        from services.storage import StoredObject  # noqa: WPS433
        with self.storage._lock:  # noqa: SLF001 - test helper access
            self.storage._objects[f"{USER_A}/papers/foo.md"] = StoredObject(
                content_hash="sha256:abc", size_bytes=12,
            )

        # Step 3: commit
        resp = self.client.post("/api/sync/commit", json={
            "api_version": "1",
            "sync_session_id": sid,
            "uploaded": [{"rel_path": "papers/foo.md", "content_hash": "sha256:abc"}],
        })
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["revision"], 1)
        self.assertEqual(body["accepted"]["papers"], 1)
        self.assertEqual(body["accepted"]["wiki_files"], 1)
        self.assertEqual(body["rejected"], [])

        # DB should now hold the row
        db = self.SessionLocal()
        try:
            papers = db.query(CloudPaper).all()
            self.assertEqual(len(papers), 1)
            self.assertEqual(papers[0].title, "Foo")
            wfs = db.query(WikiFile).all()
            self.assertEqual(len(wfs), 1)
            self.assertEqual(wfs[0].storage_path, f"{USER_A}/papers/foo.md")
        finally:
            db.close()

    def test_commit_idempotency_replays_cached_response(self):
        prep = self.client.post("/api/sync/prepare", json=self._basic_payload()).json()
        sid = prep["sync_session_id"]
        from services.storage import StoredObject  # noqa: WPS433
        with self.storage._lock:
            self.storage._objects[f"{USER_A}/papers/foo.md"] = StoredObject(
                content_hash="sha256:abc", size_bytes=12,
            )
        body = {
            "api_version": "1", "sync_session_id": sid,
            "uploaded": [{"rel_path": "papers/foo.md", "content_hash": "sha256:abc"}],
        }
        first = self.client.post("/api/sync/commit", json=body).json()
        second = self.client.post("/api/sync/commit", json=body).json()
        self.assertEqual(first["revision"], second["revision"])
        self.assertEqual(first["accepted"], second["accepted"])

        # And the revision is still 1 — not bumped twice
        from cloud_models import CloudRevision
        db = self.SessionLocal()
        try:
            rev = db.query(CloudRevision).filter(CloudRevision.user_id == USER_A).one()
            self.assertEqual(rev.revision, 1)
        finally:
            db.close()

    # ---- dedup / hash mismatch -----------------------------------------

    def test_prepare_skips_files_with_matching_content_hash(self):
        # First, commit a file
        prep = self.client.post("/api/sync/prepare", json=self._basic_payload()).json()
        sid = prep["sync_session_id"]
        from services.storage import StoredObject  # noqa: WPS433
        with self.storage._lock:
            self.storage._objects[f"{USER_A}/papers/foo.md"] = StoredObject(
                content_hash="sha256:abc", size_bytes=12,
            )
        self.client.post("/api/sync/commit", json={
            "api_version": "1", "sync_session_id": sid,
            "uploaded": [{"rel_path": "papers/foo.md", "content_hash": "sha256:abc"}],
        })

        # Then re-prepare with same content_hash → should be skipped
        prep2 = self.client.post("/api/sync/prepare", json=self._basic_payload()).json()
        self.assertEqual(prep2["uploads_required"], [])
        self.assertEqual(len(prep2["uploads_skipped"]), 1)
        self.assertEqual(prep2["uploads_skipped"][0]["rel_path"], "papers/foo.md")

    def test_commit_rejects_storage_hash_mismatch(self):
        prep = self.client.post("/api/sync/prepare", json=self._basic_payload()).json()
        sid = prep["sync_session_id"]
        # Simulate upload with DIFFERENT content_hash than expected
        from services.storage import StoredObject  # noqa: WPS433
        with self.storage._lock:
            self.storage._objects[f"{USER_A}/papers/foo.md"] = StoredObject(
                content_hash="sha256:WRONG", size_bytes=12,
            )
        resp = self.client.post("/api/sync/commit", json={
            "api_version": "1", "sync_session_id": sid,
            "uploaded": [{"rel_path": "papers/foo.md", "content_hash": "sha256:abc"}],
        })
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["revision"], 1)
        self.assertEqual(body["accepted"]["papers"], 1)
        self.assertEqual(body["accepted"]["wiki_files"], 0)
        self.assertEqual(len(body["rejected"]), 1)
        self.assertEqual(body["rejected"][0]["code"], "HASH_MISMATCH")

    def test_commit_rejects_upload_missing_at_storage(self):
        prep = self.client.post("/api/sync/prepare", json=self._basic_payload()).json()
        sid = prep["sync_session_id"]
        # No simulate_upload — Storage knows nothing about this path
        resp = self.client.post("/api/sync/commit", json={
            "api_version": "1", "sync_session_id": sid,
            "uploaded": [{"rel_path": "papers/foo.md", "content_hash": "sha256:abc"}],
        }).json()
        self.assertEqual(resp["revision"], 1)
        self.assertEqual(resp["accepted"]["papers"], 1)
        self.assertEqual(resp["accepted"]["wiki_files"], 0)
        self.assertEqual(resp["rejected"][0]["code"], "UPLOAD_MISSING_AT_STORAGE")

    # ---- session lifecycle ---------------------------------------------

    def test_commit_unknown_session_returns_404(self):
        resp = self.client.post("/api/sync/commit", json={
            "api_version": "1", "sync_session_id": "nonexistent",
            "uploaded": [],
        })
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json()["detail"]["error"], "not_found")

    def test_commit_on_expired_session_returns_410(self):
        prep = self.client.post("/api/sync/prepare", json=self._basic_payload()).json()
        sid = prep["sync_session_id"]
        # Manually expire it
        db = self.SessionLocal()
        try:
            sess = db.query(SyncSession).filter(SyncSession.id == sid).one()
            sess.expires_at = datetime.now(timezone.utc) - timedelta(minutes=5)
            db.commit()
        finally:
            db.close()
        from services.storage import StoredObject  # noqa: WPS433
        with self.storage._lock:
            self.storage._objects[f"{USER_A}/papers/foo.md"] = StoredObject(
                content_hash="sha256:abc", size_bytes=12,
            )
        resp = self.client.post("/api/sync/commit", json={
            "api_version": "1", "sync_session_id": sid,
            "uploaded": [{"rel_path": "papers/foo.md", "content_hash": "sha256:abc"}],
        })
        self.assertEqual(resp.status_code, 410)
        self.assertEqual(resp.json()["detail"]["error"], "expired")

    # ---- cross-tenant --------------------------------------------------

    def test_user_b_cannot_commit_user_a_session(self):
        # User A creates a session
        prep = self.client.post("/api/sync/prepare", json=self._basic_payload()).json()
        sid = prep["sync_session_id"]

        # Swap the dependency to User B
        self.app.dependency_overrides[auth_deps.current_user] = lambda: _user_b()
        client_b = TestClient(self.app)
        resp = client_b.post("/api/sync/commit", json={
            "api_version": "1", "sync_session_id": sid,
            "uploaded": [],
        })
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json()["detail"]["error"], "forbidden")

    def test_prepare_flags_per_row_user_id_mismatch(self):
        payload = self._basic_payload()
        payload["tables"]["papers"][0]["user_id"] = USER_B
        resp = self.client.post("/api/sync/prepare", json=payload).json()
        self.assertEqual(len(resp["validation_errors"]), 1)
        self.assertEqual(resp["validation_errors"][0]["code"], "USER_ID_MISMATCH")
        self.assertEqual(resp["validation_errors"][0]["table"], "papers")

    # ---- re-sync (idempotent push) ------------------------------------
    #
    # The user-visible bug this guards against: first sync inserts 600+
    # nodes successfully. User clicks 立即同步 again later (whether to
    # push new data or just retry). The naive ``_bulk_upsert`` keyed its
    # "what already exists" dict by ``row.id`` — on Postgres those are
    # uuid.UUID instances but ``payload["id"]`` is a str, so the lookup
    # always missed → we ran INSERT for already-present rows → PK
    # violation → 500. Fixed by str-normalising both sides; this test
    # locks it in.

    def test_resync_same_rows_is_idempotent(self):
        """A second prepare+commit with the same row IDs must update,
        not crash with a duplicate-key violation. The keying-by-uuid
        bug only manifested on Postgres (SQLite tolerates the type
        mismatch and returns rows anyway) — so SQLite tests can't
        prove it. But the str() normalization we added defends both
        dialects, and this test verifies SQLAlchemy's "found existing
        row → UPDATE not INSERT" branch fires."""
        from services.storage import StoredObject

        # Round 1: first sync.
        payload = self._basic_payload()
        prep = self.client.post("/api/sync/prepare", json=payload).json()
        with self.storage._lock:
            self.storage._objects[f"{USER_A}/papers/foo.md"] = StoredObject(
                content_hash="sha256:abc", size_bytes=12,
            )
        resp = self.client.post("/api/sync/commit", json={
            "api_version": "1",
            "sync_session_id": prep["sync_session_id"],
            "uploaded": [{"rel_path": "papers/foo.md", "content_hash": "sha256:abc"}],
        })
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["revision"], 1)

        # Round 2: SAME payload, fresh prepare. The cloud already has
        # the rows from round 1; the bulk upsert must see them as
        # existing and not retry the INSERT.
        prep2 = self.client.post("/api/sync/prepare", json=payload).json()
        resp2 = self.client.post("/api/sync/commit", json={
            "api_version": "1",
            "sync_session_id": prep2["sync_session_id"],
            "uploaded": [],
        })
        self.assertEqual(resp2.status_code, 200,
                         f"re-sync must succeed; got {resp2.status_code}: {resp2.text}")
        body = resp2.json()
        # revision keeps moving forward even when nothing semantically
        # changed (helps clients tell "the server has acked").
        self.assertGreaterEqual(body["revision"], 2)

    def test_older_paper_payload_backfills_empty_team_metadata(self):
        """Team/category fields may be derived after the original sync.

        Older desktop snapshots used processed_at as paper.updated_at, so a
        later metadata-only backfill could carry the same/older timestamp than
        the existing cloud row. Empty cloud metadata should still be filled from
        the desktop source-of-truth snapshot.
        """
        server_ts = datetime(2020, 1, 2, 10, tzinfo=timezone.utc)
        db = self.SessionLocal()
        try:
            db.add(CloudPaper(
                id="paper-a",
                user_id=USER_A,
                filepath="/tmp/foo.pdf",
                filename="foo.pdf",
                file_hash="hash1",
                title="Foo",
                updated_at=server_ts,
                created_at=server_ts,
            ))
            db.commit()
        finally:
            db.close()

        payload = self._basic_payload()
        payload["tables"]["wiki_files"] = []
        payload["tables"]["papers"][0]["updated_at"] = "2020-01-01T10:00:00Z"
        payload["tables"]["papers"][0]["paper_team_model"] = "Kaiming He"
        payload["tables"]["papers"][0]["paper_category_model"] = "VLM"

        prep = self.client.post("/api/sync/prepare", json=payload).json()
        resp = self.client.post("/api/sync/commit", json={
            "api_version": "1",
            "sync_session_id": prep["sync_session_id"],
            "uploaded": [],
        })
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["accepted"]["papers"], 1)

        db = self.SessionLocal()
        try:
            paper = db.query(CloudPaper).filter(CloudPaper.id == "paper-a").one()
            self.assertEqual(paper.paper_team_model, "Kaiming He")
            self.assertEqual(paper.paper_category_model, "VLM")
            updated_at = paper.updated_at
            if updated_at.tzinfo is None:
                updated_at = updated_at.replace(tzinfo=timezone.utc)
            self.assertGreater(updated_at, server_ts)
        finally:
            db.close()

    # ---- protocol versioning -------------------------------------------

    def test_prepare_rejects_unknown_api_version(self):
        payload = self._basic_payload()
        payload["api_version"] = "99"
        resp = self.client.post("/api/sync/prepare", json=payload)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["detail"]["error"], "version_mismatch")


if __name__ == "__main__":
    unittest.main()
