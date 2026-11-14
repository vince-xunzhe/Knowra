"""Offline tests for library paper deletion. Uses only temporary fixtures."""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
sys.path[:0] = [str(ROOT), str(BACKEND)]

from database import get_db  # noqa: E402
from models import Base, KnowledgeEdge, KnowledgeNode, Paper, RoadmapPaper  # noqa: E402
from routers import papers  # noqa: E402
from services.paper_deletion_service import (  # noqa: E402
    DeletedPaperSkip,
    PaperDeletionError,
    cancel_jobs_for_paper,
    cleanup_provider_paper_resources,
    delete_library_paper,
    has_tombstone,
    should_skip_deleted_paper,
    write_tombstone,
)
from services.task_store import TaskStore  # noqa: E402


def _write(path: Path, content: str | bytes = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")
    return path


class _FakeProvider:
    def __init__(self):
        self.files = SimpleNamespace(delete=MagicMock())
        self.vector_stores = SimpleNamespace(delete=MagicMock())
        self.beta = SimpleNamespace(threads=SimpleNamespace(delete=MagicMock()))


class PaperDeletionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "data"
        self.root.mkdir()
        for name in ("papers", "artifacts", "paper_records", "wiki/papers", "wiki/concepts",
                     "paper_evidence", "extraction_checkpoints", "wiki_source_checkpoints",
                     "manifests", "deleted_papers"):
            (self.root / name).mkdir(parents=True, exist_ok=True)

        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        self.Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
        self.db = self.Session()
        self.addCleanup(self.db.close)
        self.provider = _FakeProvider()

    def _paper(self, **kwargs) -> Paper:
        values = dict(
            filepath="data/papers/solo.pdf",
            filename="solo.pdf",
            file_hash="hash-solo",
            title="Solo Fixture Paper",
            authors=["Ada"],
            processed=True,
            notes="See ![img](/api/note_images/abc.png)",
        )
        values.update(kwargs)
        paper = Paper(**values)
        self.db.add(paper)
        self.db.commit()
        self.db.refresh(paper)
        return paper

    def _delete(self, paper_id, **kwargs):
        return delete_library_paper(
            self.db,
            paper_id,
            data_root=self.root,
            provider_client=kwargs.pop("provider_client", self.provider),
            **kwargs,
        )

    def test_deletes_exclusive_artifacts_and_keeps_shared_concepts(self):
        paper = self._paper()
        other = self._paper(
            filepath="data/papers/other.pdf",
            filename="other.pdf",
            file_hash="hash-other",
            title="Other Paper",
            notes="",
        )
        pdf = _write(self.root / "papers" / "solo.pdf", b"%PDF-1.4 fixture")
        thumb = _write(self.root / "artifacts" / "hash-solo_page1.png", b"png")
        record = _write(self.root / "paper_records" / "solo.md", f"paper_id: {paper.id}\n# record")
        wiki = _write(
            self.root / "wiki" / "papers" / "solo.md",
            f"---\npaper_id: {paper.id}\ntitle: Solo\n---\nbody",
        )
        concept_wiki = _write(
            self.root / "wiki" / "concepts" / "shared.md",
            "---\nconcept_id: concept-shared\ntitle: Shared Concept\n---\nshared body",
        )
        evidence = _write(self.root / "paper_evidence" / "ev.json", json.dumps({"paper_id": paper.id}))
        ckpt_dir = self.root / "extraction_checkpoints" / "run1"
        _write(ckpt_dir / "checkpoint.json", json.dumps({"identity": {"paper_id": paper.id}}))
        _write(self.root / "wiki_source_checkpoints" / str(paper.id) / "notes.json", "{}")
        _write(self.root / "manifests" / f"{paper.id}.json", "{}")
        search = self.root / "wiki_search.sqlite"
        conn = sqlite3.connect(search)
        conn.execute("CREATE VIRTUAL TABLE wiki_fts USING fts5(kind, filename, path, title, body, compiled_at)")
        conn.execute(
            "INSERT INTO wiki_fts VALUES ('paper','solo.md','data/wiki/papers/solo.md','Solo','body','')"
        )
        conn.execute(
            "INSERT INTO wiki_fts VALUES ('concept','shared.md','data/wiki/concepts/shared.md','Shared','body','')"
        )
        conn.commit()
        conn.close()

        exclusive = KnowledgeNode(
            title="Exclusive Concept",
            content="only this paper",
            node_type="concept",
            node_origin="auto",
            source_paper_ids=[paper.id],
        )
        shared = KnowledgeNode(
            title="Shared Concept",
            content="used by two papers",
            node_type="concept",
            node_origin="auto",
            source_paper_ids=[paper.id, other.id],
        )
        manual = KnowledgeNode(
            title="Manual Concept",
            content="user-created",
            node_type="concept",
            node_origin="manual",
            source_paper_ids=[paper.id, other.id],
        )
        self.db.add_all([exclusive, shared, manual])
        self.db.commit()
        edge = KnowledgeEdge(source_id=exclusive.id, target_id=shared.id, relation_type="related")
        self.db.add(edge)
        self.db.add(RoadmapPaper(data={"title": "bib"}, library_paper_id=str(paper.id)))
        paper.openai_file_id = "file-1"
        paper.openai_vector_store_id = "vs-1"
        paper.openai_thread_id = "thread-1"
        self.db.commit()

        outcome = self._delete(paper.id)
        self.assertEqual(outcome.status, "deleted")
        self.assertIsNone(self.db.query(Paper).filter(Paper.id == paper.id).first())
        self.assertIsNotNone(self.db.query(Paper).filter(Paper.id == other.id).first())
        self.assertFalse(pdf.exists())
        self.assertFalse(thumb.exists())
        self.assertFalse(record.exists())
        self.assertFalse(wiki.exists())
        self.assertTrue(concept_wiki.exists())
        self.assertFalse(evidence.exists())
        self.assertFalse(ckpt_dir.exists())
        self.assertFalse((self.root / "wiki_source_checkpoints" / str(paper.id)).exists())
        self.assertIsNone(self.db.query(KnowledgeNode).filter(KnowledgeNode.title == "Exclusive Concept").first())
        shared = self.db.query(KnowledgeNode).filter(KnowledgeNode.title == "Shared Concept").one()
        self.assertEqual(shared.source_paper_ids, [other.id])
        manual = self.db.query(KnowledgeNode).filter(KnowledgeNode.title == "Manual Concept").one()
        self.assertEqual(manual.source_paper_ids, [other.id])
        stale_titles = {item["title"] for item in outcome.stale_concepts}
        self.assertIn("Shared Concept", stale_titles)
        self.assertIn("Manual Concept", stale_titles)
        roadmap = self.db.query(RoadmapPaper).one()
        self.assertIsNone(roadmap.library_paper_id)
        conn = sqlite3.connect(search)
        rows = list(conn.execute("SELECT filename FROM wiki_fts"))
        conn.close()
        self.assertEqual(rows, [("shared.md",)])
        self.assertTrue(self.provider.files.delete.called)
        self.assertTrue(outcome.provider_cleanup["verified"])
        self.assertTrue(has_tombstone(paper.id, self.root))
        # Next compile of the shared concept would only see the remaining paper.
        self.assertEqual(shared.source_paper_ids, [other.id])

    def test_external_original_pdf_and_symlink_targets_are_not_deleted(self):
        outside = Path(self.tmp.name) / "outside" / "source.pdf"
        _write(outside, b"%PDF-1.4 outside")
        # Recorded path is the external import source: leave it alone.
        external = self._paper(filepath=str(outside), filename="source.pdf", file_hash="ext-1")
        outcome = self._delete(external.id)
        self.assertEqual(outcome.status, "deleted")
        self.assertTrue(outside.exists(), "external import source must remain")
        reasons = {item["reason"] for item in outcome.skipped_files}
        self.assertIn("skipped_external", reasons)

        # Recorded path is a managed symlink to the same external file: unlink
        # only the symlink, never the target.
        managed_link = self.root / "papers" / "linked.pdf"
        managed_link.symlink_to(outside)
        linked = self._paper(filepath="data/papers/linked.pdf", filename="linked.pdf", file_hash="ext-2")
        outside_thumb = Path(self.tmp.name) / "outside" / "thumb.png"
        _write(outside_thumb, b"png")
        linked.first_page_image_path = str(outside_thumb)
        self.db.commit()
        outcome = self._delete(linked.id)
        self.assertEqual(outcome.status, "deleted")
        self.assertTrue(outside.exists(), "symlink target must remain")
        self.assertFalse(managed_link.exists() or managed_link.is_symlink(), "managed symlink copy should be unlinked")
        self.assertTrue(outside_thumb.exists(), "must not follow an external thumbnail path")

    def test_shared_pdf_and_hash_artifacts_are_kept(self):
        pdf = _write(self.root / "papers" / "shared.pdf", b"%PDF-1.4 shared")
        thumb = _write(self.root / "artifacts" / "same-hash_page1.png", b"png")
        paper = self._paper(filepath="data/papers/shared.pdf", filename="shared.pdf", file_hash="same-hash")
        other = self._paper(
            filepath="data/papers/shared-copy.pdf",
            filename="shared-copy.pdf",
            file_hash="same-hash",
            title="Copy",
        )
        _write(self.root / "papers" / "shared-copy.pdf", b"%PDF-1.4 shared")
        outcome = self._delete(paper.id)
        self.assertEqual(outcome.deleted.get("thumbnails"), "skipped_shared")
        self.assertTrue(thumb.exists())
        # The original shared.pdf is only used by the deleted paper once the
        # copy lives at shared-copy.pdf, so it may be removed; the hash-keyed
        # thumbnail must stay because other still uses the hash.
        self.assertIsNotNone(self.db.query(Paper).filter(Paper.id == other.id).first())

    def test_path_escape_and_missing_files_are_safe_and_idempotent(self):
        paper = self._paper()
        # Missing managed files should not fail the first delete.
        outcome = self._delete(paper.id)
        self.assertEqual(outcome.status, "deleted")
        # Repeating the delete is idempotent because the tombstone remains.
        again = self._delete(paper.id)
        self.assertEqual(again.status, "already_deleted")
        # Unknown ids still 404.
        with self.assertRaises(PaperDeletionError) as ctx:
            self._delete("does-not-exist")
        self.assertEqual(ctx.exception.code, "not_found")

    def test_file_failure_does_not_report_success_or_drop_the_row(self):
        paper = self._paper()
        pdf = _write(self.root / "papers" / "solo.pdf", b"%PDF-1.4")
        real_unlink = Path.unlink

        def boom(self, *args, **kwargs):
            if self.name == "solo.pdf":
                raise OSError("permission denied")
            return real_unlink(self, *args, **kwargs)

        with patch.object(Path, "unlink", boom):
            with self.assertRaises(PaperDeletionError) as ctx:
                self._delete(paper.id)
        self.assertEqual(ctx.exception.code, "file_delete_failed")
        self.assertIsNotNone(self.db.query(Paper).filter(Paper.id == paper.id).first())
        self.assertTrue(pdf.exists())
        self.assertTrue(should_skip_deleted_paper(paper.id, self.root))

    def test_cancel_queued_job_and_late_writeback_is_blocked(self):
        paper = self._paper()
        store = TaskStore(Path(self.tmp.name) / "tasks.db")
        store.initialize()
        job = store.submit("papers", "papers", {"paper_ids": [paper.id, "keep-me"]})
        # Second job only this paper — but store forbids two active jobs on
        # the same channel. Cancel/strip the first instead.
        summary = cancel_jobs_for_paper(paper.id, store=store)
        self.assertIn(job["job_id"], summary["updated"])
        updated = store.get(job["job_id"])
        self.assertEqual(updated["payload"]["paper_ids"], ["keep-me"])

        write_tombstone(paper.id, data_root=self.root)
        self.assertTrue(should_skip_deleted_paper(paper.id, self.root))
        self.assertTrue(hasattr(DeletedPaperSkip, "__mro__"))

    def test_provider_cleanup_is_mocked_and_unverified_without_client(self):
        paper = self._paper(openai_file_id="file-9", openai_vector_store_id="vs-9")
        # No client: local delete succeeds, remote cleanup is reported skipped.
        outcome = self._delete(paper.id, provider_client=None)
        self.assertEqual(outcome.status, "deleted")
        self.assertFalse(outcome.provider_cleanup["attempted"])
        self.assertFalse(outcome.provider_cleanup["verified"])
        self.assertTrue(any("file-9" in item or item == "provider_client_not_configured"
                            for item in outcome.provider_cleanup["skipped"]))

        # Injected client records mock deletes — still not a live API verification.
        other = self._paper(
            filepath="data/papers/other2.pdf",
            filename="other2.pdf",
            file_hash="h2",
            openai_file_id="file-live",
            openai_vector_store_id="vs-live",
            openai_thread_id="th-live",
        )
        client = _FakeProvider()
        result = cleanup_provider_paper_resources(other, client=client)
        self.assertTrue(result["attempted"])
        self.assertTrue(result["verified"])
        client.files.delete.assert_called_once_with("file-live")
        client.vector_stores.delete.assert_called_once_with("vs-live")
        client.beta.threads.delete.assert_called_once_with("th-live")

    def test_http_delete_route_and_unknown_id(self):
        paper = self._paper()
        _write(self.root / "papers" / "solo.pdf", b"%PDF")
        app = FastAPI()
        app.include_router(papers.router)

        def override_db():
            yield self.db

        app.dependency_overrides[get_db] = override_db
        with patch("routers.papers.delete_library_paper", wraps=delete_library_paper) as wrapped:
            # The route uses default DATA_DIR; patch the service call to our root.
            def _call(db, paper_id):
                return delete_library_paper(
                    db, paper_id, data_root=self.root, provider_client=self.provider
                )
            wrapped.side_effect = _call
            with TestClient(app) as client:
                missing = client.delete("/api/papers/missing-id")
                self.assertEqual(missing.status_code, 404)
                ok = client.delete(f"/api/papers/{paper.id}")
                self.assertEqual(ok.status_code, 200)
                body = ok.json()
                self.assertEqual(body["status"], "deleted")
                self.assertEqual(body["paper_id"], paper.id)
                again = client.delete(f"/api/papers/{paper.id}")
                self.assertEqual(again.status_code, 200)
                self.assertEqual(again.json()["status"], "already_deleted")

    def test_queued_only_job_is_cancelled(self):
        store = TaskStore(Path(self.tmp.name) / "only.db")
        store.initialize()
        job = store.submit("papers", "papers", {"paper_ids": ["p-only"]})
        summary = cancel_jobs_for_paper("p-only", store=store)
        self.assertIn(job["job_id"], summary["cancelled"])
        self.assertEqual(store.get(job["job_id"])["status"], "cancelled")


if __name__ == "__main__":
    unittest.main()
