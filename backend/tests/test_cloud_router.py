"""End-to-end tests for the mobile-facing /api/cloud/* router.

Uses the same SQLite + InMemoryStorage harness as the sync router
tests. The fixture seeds a small synced corpus for User A so /me,
/snapshot, /wiki/{id}, /wiki/search all have realistic data.

Covers:
  - /me returns correct stats
  - /snapshot full payload includes papers/nodes/edges/wiki_files
  - /snapshot wiki_files have signed download URLs
  - /snapshot?since= returns only rows updated after the cursor
  - /snapshot includes deleted_since tombstones
  - /wiki/{file_id} 302s to a Storage URL
  - /wiki/{file_id} 404s when the file belongs to another user
  - /wiki/search returns title matches scoped to the caller
  - cross-tenant: User B's /me / /snapshot only sees User B's data
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
    CloudDeletion,
    CloudKnowledgeEdge,
    CloudKnowledgeNode,
    CloudPaper,
    CloudRevision,
    UserProfile,
    WikiFile,
    init_cloud_schema,
)
from model_gateway.auth import AuthenticatedUser  # noqa: E402
from routers import cloud as cloud_router  # noqa: E402
from routers import sync as sync_router  # noqa: E402
from services.storage import (  # noqa: E402
    InMemoryStorage,
    reset_storage_cache,
    set_storage,
)


USER_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
USER_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


def _ua():
    return AuthenticatedUser(user_id=USER_A, email="a@x.com", role="authenticated")


def _ub():
    return AuthenticatedUser(user_id=USER_B, email="b@x.com", role="authenticated")


def _make_app_with_seed():
    """Build app + SQLite + InMemoryStorage, seed User A with a small
    corpus, and return (app, SessionLocal, storage)."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    init_cloud_schema(engine)
    SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    storage = InMemoryStorage()
    set_storage(storage)

    # Seed User A
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        # 2 papers
        db.add(CloudPaper(
            id="paper-1", user_id=USER_A,
            filepath="/a/foo.pdf", filename="foo.pdf", file_hash="h1",
            title="Paper Foo", processed=True,
            updated_at=now - timedelta(hours=2),
        ))
        db.add(CloudPaper(
            id="paper-2", user_id=USER_A,
            filepath="/a/bar.pdf", filename="bar.pdf", file_hash="h2",
            title="Paper Bar", processed=True,
            updated_at=now - timedelta(minutes=10),
        ))
        # 2 concept nodes + 1 paper-node
        db.add(CloudKnowledgeNode(
            id="node-1", user_id=USER_A,
            title="Foo Concept", content="content 1", node_type="concept",
            promotion_status="promoted",
            updated_at=now - timedelta(hours=1),
        ))
        db.add(CloudKnowledgeNode(
            id="node-2", user_id=USER_A,
            title="Bar Method", content="content 2", node_type="technique",
            promotion_status="promoted",
            updated_at=now - timedelta(minutes=20),
        ))
        db.add(CloudKnowledgeNode(
            id="node-paper-1", user_id=USER_A,
            title="Paper as Node", content="content P", node_type="paper",
            updated_at=now - timedelta(hours=2),
        ))
        # 1 edge
        db.add(CloudKnowledgeEdge(
            id="edge-1", user_id=USER_A,
            source_id="node-1", target_id="node-2",
            updated_at=now - timedelta(minutes=15),
        ))
        # 2 wiki files
        db.add(WikiFile(
            id="wiki-1", user_id=USER_A,
            kind="paper", rel_path="papers/0001-foo.md",
            storage_path=f"wiki/{USER_A}/papers/0001-foo.md",
            content_hash="sha256:foo", size_bytes=100,
            title="Foo md",
            updated_at=now - timedelta(hours=1),
            paper_id="paper-1",
        ))
        db.add(WikiFile(
            id="wiki-2", user_id=USER_A,
            kind="concept", rel_path="concepts/0001-bar.md",
            storage_path=f"wiki/{USER_A}/concepts/0001-bar.md",
            content_hash="sha256:bar", size_bytes=200,
            title="Bar Concept md",
            updated_at=now - timedelta(minutes=5),
            concept_id="node-1",
        ))
        # Profile + revision
        db.add(UserProfile(
            user_id=USER_A,
            display_name="Alice",
            last_desktop_sync_at=now - timedelta(minutes=5),
        ))
        db.add(CloudRevision(user_id=USER_A, revision=3))
        # Old tombstone (for testing deleted_since)
        db.add(CloudDeletion(
            user_id=USER_A,
            table_name="papers",
            row_id="paper-deleted",
            deleted_at=now - timedelta(minutes=10),
        ))
        db.commit()
    finally:
        db.close()

    app = FastAPI()

    def override_db():
        s = SessionLocal()
        try:
            yield s
        finally:
            s.close()

    def override_user_a():
        return _ua()

    app.include_router(cloud_router.router)
    app.include_router(sync_router.router)  # needed for get_cloud_db
    app.dependency_overrides[cloud_router.get_cloud_db] = override_db
    app.dependency_overrides[sync_router.get_cloud_db] = override_db
    app.dependency_overrides[auth_deps.current_user] = override_user_a

    return app, SessionLocal, storage


# ── tests ─────────────────────────────────────────────────────────────


class CloudRouterTests(unittest.TestCase):
    def setUp(self):
        os.environ["KNOWRA_STORAGE_BACKEND"] = "memory"
        reset_storage_cache()
        self.app, self.SessionLocal, self.storage = _make_app_with_seed()
        self.client = TestClient(self.app)

    def tearDown(self):
        reset_storage_cache()
        os.environ.pop("KNOWRA_STORAGE_BACKEND", None)

    # ---- /me ------------------------------------------------------------

    def test_me_returns_profile_and_stats(self):
        resp = self.client.get("/api/cloud/me")
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["user_id"], USER_A)
        self.assertEqual(body["email"], "a@x.com")
        self.assertEqual(body["display_name"], "Alice")
        self.assertEqual(body["stats"]["papers"], 2)
        # 2 concepts (node-1, node-2), excludes node-paper-1
        self.assertEqual(body["stats"]["concepts"], 2)
        self.assertEqual(body["stats"]["edges"], 1)
        self.assertEqual(body["stats"]["wiki_files"], 2)
        self.assertEqual(body["stats"]["wiki_size_bytes"], 300)
        self.assertIsNotNone(body["stats"]["last_desktop_sync_at"])

    def test_me_for_fresh_user_returns_zeros(self):
        # Switch to User B who has no profile or data.
        self.app.dependency_overrides[auth_deps.current_user] = lambda: _ub()
        resp = self.client.get("/api/cloud/me").json()
        self.assertEqual(resp["user_id"], USER_B)
        self.assertEqual(resp["stats"]["papers"], 0)
        self.assertEqual(resp["stats"]["concepts"], 0)
        self.assertEqual(resp["stats"]["wiki_files"], 0)
        self.assertEqual(resp["stats"]["wiki_size_bytes"], 0)
        self.assertIsNone(resp["display_name"])

    # ---- /snapshot ------------------------------------------------------

    def test_snapshot_full_returns_all_user_a_data(self):
        resp = self.client.get("/api/cloud/snapshot")
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["revision"], 3)
        self.assertEqual(len(body["papers"]), 2)
        self.assertEqual(len(body["knowledge_nodes"]), 3)  # incl node-paper-1
        self.assertEqual(len(body["knowledge_edges"]), 1)
        self.assertEqual(len(body["wiki_files"]), 2)

    def test_snapshot_wiki_files_have_signed_urls(self):
        body = self.client.get("/api/cloud/snapshot").json()
        for wf in body["wiki_files"]:
            self.assertIn("download_url", wf)
            self.assertTrue(wf["download_url"].startswith("memstore://"))
            self.assertIn("get", wf["download_url"])
            self.assertIn("download_url_expires_at", wf)

    def test_snapshot_with_since_returns_only_newer_rows(self):
        # Cursor at 30 minutes ago → should see only recent rows. Use Z
        # suffix instead of +00:00 so the offset isn't URL-decoded into
        # a space by the query-string parser.
        cursor = (
            datetime.now(timezone.utc) - timedelta(minutes=30)
        ).replace(tzinfo=None).isoformat() + "Z"
        body = self.client.get(f"/api/cloud/snapshot?since={cursor}").json()
        paper_titles = {p["title"] for p in body["papers"]}
        self.assertIn("Paper Bar", paper_titles)  # updated 10 min ago
        self.assertNotIn("Paper Foo", paper_titles)  # updated 2h ago
        node_titles = {n["title"] for n in body["knowledge_nodes"]}
        self.assertIn("Bar Method", node_titles)  # 20 min ago
        self.assertNotIn("Foo Concept", node_titles)  # 1h ago

    def test_snapshot_includes_deleted_since_tombstones(self):
        body = self.client.get("/api/cloud/snapshot").json()
        # Seed put a tombstone for 'paper-deleted'
        self.assertIn("paper-deleted", body["deleted_since"]["papers"])
        self.assertEqual(body["deleted_since"]["knowledge_nodes"], [])
        self.assertEqual(body["deleted_since"]["knowledge_edges"], [])
        self.assertEqual(body["deleted_since"]["wiki_files"], [])

    def test_snapshot_since_filters_tombstones_too(self):
        # Cursor 5 min ago, but tombstone is 10 min old → no tombstone
        cursor = (
            datetime.now(timezone.utc) - timedelta(minutes=5)
        ).replace(tzinfo=None).isoformat() + "Z"
        body = self.client.get(f"/api/cloud/snapshot?since={cursor}").json()
        self.assertEqual(body["deleted_since"]["papers"], [])

    def test_snapshot_scopes_to_caller_user(self):
        # User B has no data
        self.app.dependency_overrides[auth_deps.current_user] = lambda: _ub()
        body = self.client.get("/api/cloud/snapshot").json()
        self.assertEqual(body["papers"], [])
        self.assertEqual(body["knowledge_nodes"], [])
        self.assertEqual(body["knowledge_edges"], [])
        self.assertEqual(body["wiki_files"], [])
        self.assertEqual(body["revision"], 0)

    # ---- /wiki/{file_id} ------------------------------------------------

    def test_wiki_file_redirects_to_signed_url(self):
        # FastAPI TestClient follows redirects by default — disable to inspect
        resp = self.client.get("/api/cloud/wiki/wiki-1", follow_redirects=False)
        self.assertEqual(resp.status_code, 302)
        loc = resp.headers["location"]
        self.assertTrue(loc.startswith("memstore://"))
        self.assertIn(f"wiki/{USER_A}/papers/0001-foo.md", loc)

    def test_wiki_file_404_for_other_user(self):
        # User B asking for User A's wiki file
        self.app.dependency_overrides[auth_deps.current_user] = lambda: _ub()
        resp = self.client.get("/api/cloud/wiki/wiki-1", follow_redirects=False)
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json()["detail"]["error"], "not_found")

    def test_wiki_file_404_for_unknown_id(self):
        resp = self.client.get(
            "/api/cloud/wiki/does-not-exist",
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 404)

    # ---- /wiki/search ---------------------------------------------------

    def test_wiki_search_matches_title(self):
        resp = self.client.post("/api/cloud/wiki/search", json={"q": "foo"})
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["query"], "foo")
        titles = {h["title"] for h in body["hits"]}
        self.assertIn("Foo md", titles)
        self.assertNotIn("Bar Concept md", titles)

    def test_wiki_search_case_insensitive(self):
        body = self.client.post(
            "/api/cloud/wiki/search", json={"q": "FOO"}
        ).json()
        self.assertEqual(len(body["hits"]), 1)

    def test_wiki_search_filter_by_kind(self):
        # "concept" Bar matches, but kind=paper should hide it
        body = self.client.post("/api/cloud/wiki/search", json={
            "q": "bar", "kind": "concept",
        }).json()
        self.assertEqual(len(body["hits"]), 1)
        self.assertEqual(body["hits"][0]["kind"], "concept")
        body = self.client.post("/api/cloud/wiki/search", json={
            "q": "bar", "kind": "paper",
        }).json()
        self.assertEqual(body["hits"], [])

    def test_wiki_search_scoped_to_caller(self):
        self.app.dependency_overrides[auth_deps.current_user] = lambda: _ub()
        body = self.client.post(
            "/api/cloud/wiki/search", json={"q": "foo"}
        ).json()
        self.assertEqual(body["hits"], [])

    def test_wiki_search_validates_query_length(self):
        resp = self.client.post("/api/cloud/wiki/search", json={"q": ""})
        self.assertEqual(resp.status_code, 422)


if __name__ == "__main__":
    unittest.main()
