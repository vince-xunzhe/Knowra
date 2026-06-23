"""Tests for the local snapshot exporter the desktop sync agent uses.

We focus on the shape contract:
  - papers / knowledge_nodes / knowledge_edges come out as cloud rows
    with stringified IDs and user_id = '' (agent stamps it)
  - file_hash is computed from the PDF on disk and cached back into the
    Paper row so subsequent calls are cheap
  - wiki files are walked from data/wiki/{papers,concepts}/ + index.md
    + lint-report.md, with content_hash matching sha256(bytes)
  - cloud mode short-circuits with 404 (defense-in-depth — local data
    must not leak through the cloud build)

No real Supabase / cloud — this router is local-only by design.
"""
from __future__ import annotations

import base64
import hashlib
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
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

# Force local mode BEFORE importing sync_local so its module-level
# is_cloud_mode() reads agree with the gate.
os.environ.pop("KNOWRA_DEPLOY_MODE", None)

import database  # noqa: E402
from models import Base, KnowledgeEdge, KnowledgeNode, Paper  # noqa: E402
from routers import sync_local  # noqa: E402


def _make_app():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    app = FastAPI()
    app.include_router(sync_local.router)

    def override_db():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[database.get_db] = override_db
    return app, SessionLocal


class LocalSnapshotTests(unittest.TestCase):
    def setUp(self):
        # Each test gets a fresh temp WIKI_DIR. We monkey-patch the
        # module's directory constants so the router walks our test
        # tree instead of the real data/wiki/.
        self.tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp.name)
        self.wiki_dir = self.tmp_path / "wiki"
        self.wiki_papers = self.wiki_dir / "papers"
        self.wiki_concepts = self.wiki_dir / "concepts"
        self.wiki_papers.mkdir(parents=True)
        self.wiki_concepts.mkdir(parents=True)

        self._orig_wiki = (
            sync_local.WIKI_DIR, sync_local.WIKI_PAPERS_DIR, sync_local.WIKI_CONCEPTS_DIR,
        )
        sync_local.WIKI_DIR = self.wiki_dir
        sync_local.WIKI_PAPERS_DIR = self.wiki_papers
        sync_local.WIKI_CONCEPTS_DIR = self.wiki_concepts

        self.app, self.SessionLocal = _make_app()
        self.client = TestClient(self.app)

    def tearDown(self):
        (sync_local.WIKI_DIR,
         sync_local.WIKI_PAPERS_DIR,
         sync_local.WIKI_CONCEPTS_DIR) = self._orig_wiki
        self.tmp.cleanup()

    # ---- helpers --------------------------------------------------------

    def _seed_rows(self):
        db = self.SessionLocal()
        try:
            paper = Paper(
                id="paper-uuid-1", legacy_id=1,
                filepath="data/papers/foo.pdf", filename="foo.pdf",
                file_hash="",  # empty so the route computes + caches
                title="Foo", processed=True,
                processed_at=datetime(2026, 5, 29, 10, tzinfo=timezone.utc),
                created_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
            )
            node = KnowledgeNode(
                id="node-uuid-1", legacy_id=11,
                title="Rope", content="A technique.",
                node_type="technique", node_origin="auto",
                promotion_status="promoted", promoted_by="user",
                source_paper_ids=["paper-uuid-1"],
                created_at=datetime(2026, 5, 2, tzinfo=timezone.utc),
            )
            edge = KnowledgeEdge(
                id="edge-uuid-1",
                source_id="paper-uuid-1", target_id="node-uuid-1",
                relation_type="mentions", weight=0.7,
                created_at=datetime(2026, 5, 3, tzinfo=timezone.utc),
            )
            db.add_all([paper, node, edge])
            db.commit()
        finally:
            db.close()

    def _seed_wiki(self):
        # paper page → 0001-foo.md
        paper_md = (
            "---\n"
            "title: \"Foo paper\"\n"
            "compiled_at: \"2026-05-29T10:00:00Z\"\n"
            "---\n\n# Body\n"
        )
        (self.wiki_papers / "0001-foo.md").write_text(paper_md, encoding="utf-8")

        # concept page → 0011-rope.md (matches legacy_id=11)
        concept_md = (
            "---\n"
            "title: \"Rope\"\n"
            "compiled_at: \"2026-05-29T10:01:00Z\"\n"
            "---\n\n# Concept\n"
        )
        (self.wiki_concepts / "0011-rope.md").write_text(concept_md, encoding="utf-8")

        # index + lint report
        (self.wiki_dir / "index.md").write_text("# Index\n", encoding="utf-8")
        (self.wiki_dir / "lint-report.md").write_text("# Lint\n", encoding="utf-8")

    # ---- tests ----------------------------------------------------------

    def test_empty_db_returns_zero_counts(self):
        resp = self.client.get("/api/sync/local_snapshot")
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["counts"],
                         {"papers": 0, "knowledge_nodes": 0,
                          "knowledge_edges": 0, "wiki_files": 0})
        self.assertEqual(body["deletions"]["papers"], [])

    def test_paper_rows_have_stringified_ids_and_blank_user_id(self):
        self._seed_rows()
        resp = self.client.get("/api/sync/local_snapshot")
        body = resp.json()
        self.assertEqual(len(body["papers"]), 1)
        p = body["papers"][0]
        self.assertEqual(p["id"], "paper-uuid-1")
        self.assertEqual(p["user_id"], "",
                         "agent stamps user_id; snapshot must leave empty")
        self.assertEqual(p["legacy_id"], 1)
        self.assertTrue(p["processed"])

    def test_paper_updated_at_uses_snapshot_time(self):
        self._seed_rows()
        before = datetime.now(timezone.utc)
        body = self.client.get("/api/sync/local_snapshot").json()
        after = datetime.now(timezone.utc)

        p = body["papers"][0]
        ts = datetime.fromisoformat(p["updated_at"])
        self.assertGreaterEqual(ts, before)
        self.assertLessEqual(ts, after)
        self.assertEqual(p["updated_at"], body["generated_at"])

    def test_node_source_paper_ids_are_strings(self):
        self._seed_rows()
        body = self.client.get("/api/sync/local_snapshot").json()
        node = body["knowledge_nodes"][0]
        self.assertEqual(node["source_paper_ids"], ["paper-uuid-1"])
        # Embeddings are stripped from local push.
        self.assertIsNone(node["embedding"])

    def test_edge_endpoints_are_strings(self):
        self._seed_rows()
        body = self.client.get("/api/sync/local_snapshot").json()
        edge = body["knowledge_edges"][0]
        self.assertEqual(edge["source_id"], "paper-uuid-1")
        self.assertEqual(edge["target_id"], "node-uuid-1")

    def test_file_hash_computed_and_cached(self):
        # Write a fake PDF so resolve_paper_path can find it.
        papers_dir = self.tmp_path / "data" / "papers"
        papers_dir.mkdir(parents=True)
        pdf_bytes = b"%PDF-1.4 fake content for hashing\n"
        (papers_dir / "foo.pdf").write_bytes(pdf_bytes)

        # Point resolve_paper_path at our temp PAPERS_DIR.
        import path_utils
        orig = path_utils.PAPERS_DIR
        path_utils.PAPERS_DIR = papers_dir
        try:
            self._seed_rows()
            body = self.client.get("/api/sync/local_snapshot").json()
            expected = hashlib.sha256(pdf_bytes).hexdigest()
            self.assertEqual(body["papers"][0]["file_hash"], expected)

            # Second call should hit the cache: tamper with the file on
            # disk but the row's cached hash is what comes back.
            (papers_dir / "foo.pdf").write_bytes(b"different content")
            body2 = self.client.get("/api/sync/local_snapshot").json()
            self.assertEqual(body2["papers"][0]["file_hash"], expected,
                             "file_hash should be cached after first compute")
        finally:
            path_utils.PAPERS_DIR = orig

    def test_wiki_files_have_expected_kinds_and_hashes(self):
        self._seed_rows()
        self._seed_wiki()
        body = self.client.get("/api/sync/local_snapshot").json()
        wiki = {w["rel_path"]: w for w in body["wiki_files"]}
        self.assertIn("papers/0001-foo.md", wiki)
        self.assertIn("concepts/0011-rope.md", wiki)
        self.assertIn("index.md", wiki)
        self.assertIn("lint-report.md", wiki)

        self.assertEqual(wiki["papers/0001-foo.md"]["kind"], "paper")
        self.assertEqual(wiki["concepts/0011-rope.md"]["kind"], "concept")
        self.assertEqual(wiki["index.md"]["kind"], "index")
        self.assertEqual(wiki["lint-report.md"]["kind"], "lint_report")

        # content_hash matches raw bytes
        raw = (self.wiki_papers / "0001-foo.md").read_bytes()
        self.assertEqual(
            wiki["papers/0001-foo.md"]["content_hash"],
            hashlib.sha256(raw).hexdigest(),
        )
        # frontmatter parsed
        self.assertEqual(wiki["papers/0001-foo.md"]["title"], "Foo paper")

    def test_wiki_files_link_back_to_row_uuid_via_legacy_id(self):
        self._seed_rows()
        self._seed_wiki()
        body = self.client.get("/api/sync/local_snapshot").json()
        wiki = {w["rel_path"]: w for w in body["wiki_files"]}
        self.assertEqual(wiki["papers/0001-foo.md"]["paper_id"], "paper-uuid-1")
        self.assertEqual(wiki["concepts/0011-rope.md"]["concept_id"], "node-uuid-1")

    def test_wiki_body_is_base64_inlined(self):
        self._seed_wiki()
        body = self.client.get("/api/sync/local_snapshot").json()
        wiki = {w["rel_path"]: w for w in body["wiki_files"]}
        decoded = base64.b64decode(wiki["index.md"]["body_b64"])
        self.assertEqual(decoded, (self.wiki_dir / "index.md").read_bytes())

    def test_include_wiki_bodies_false_strips_body(self):
        self._seed_wiki()
        body = self.client.get(
            "/api/sync/local_snapshot?include_wiki_bodies=false",
        ).json()
        for w in body["wiki_files"]:
            self.assertNotIn("body_b64", w)

    def test_wiki_id_is_stable_across_runs(self):
        self._seed_wiki()
        ids_1 = {w["rel_path"]: w["id"]
                 for w in self.client.get("/api/sync/local_snapshot").json()["wiki_files"]}
        ids_2 = {w["rel_path"]: w["id"]
                 for w in self.client.get("/api/sync/local_snapshot").json()["wiki_files"]}
        self.assertEqual(ids_1, ids_2,
                         "deterministic wiki ids let the cloud upsert "
                         "without spawning duplicates per sync")

    def test_cloud_mode_returns_404(self):
        # Defense-in-depth: even if the router got mounted in cloud, it
        # must refuse — local SQLite data must not leak through cloud.
        # is_cloud_mode reads a module-level constant frozen at import,
        # so patch the reference the router actually calls.
        orig = sync_local.is_cloud_mode
        sync_local.is_cloud_mode = lambda: True
        try:
            resp = self.client.get("/api/sync/local_snapshot")
            self.assertEqual(resp.status_code, 404)
        finally:
            sync_local.is_cloud_mode = orig


if __name__ == "__main__":
    unittest.main()
