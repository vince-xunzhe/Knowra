import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import QueuePool

from models import Base, KnowledgeNode, Paper
from services.db_snapshot import finish_read_snapshot
from services import wiki_compiler, wiki_lint_service
from routers import papers


class ReadSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.engine = create_engine(
            f"sqlite:///{self.root / 'test.db'}", poolclass=QueuePool,
            pool_size=1, max_overflow=0, pool_timeout=0.1,
        )
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine)
        self.db = self.sessions()
        self.addCleanup(self.db.close)
        for i in range(2):
            self.db.add(Paper(
                id=str(i), filepath=f"{i}.pdf", filename=f"{i}.pdf", file_hash=str(i),
                title=f"Paper {i}", processed=True, raw_llm_response='{"summary": "example"}',
                first_page_image_path="image.png",
            ))
            self.db.add(KnowledgeNode(
                id=f"c{i}", title=f"Concept {i}", content="example", node_type="technique",
                promotion_status="promoted", source_paper_ids=[str(i)], embedding=[1.0, 0.0],
            ))
        self.db.commit()

    def assert_connection_available(self, *args, **kwargs):
        self.assertEqual(self.engine.pool.checkedout(), 0)
        with self.sessions() as request_db:
            self.assertEqual(request_db.execute(text("SELECT 1")).scalar(), 1)
        return "## Summary\nTest body"

    def test_health_check_releases_connection_before_cpu_scan_and_model(self):
        original = wiki_lint_service._scan_merge_candidates

        def scan(nodes):
            self.assert_connection_available()
            return original(nodes)

        def judge(**kwargs):
            self.assert_connection_available()
            return {"used_model": True, "followups": ["Question?"]}

        with patch.object(wiki_lint_service, "WIKI_DIR", self.root), \
                patch.object(wiki_lint_service, "LINT_REPORT_PATH", self.root / "report.md"), \
                patch.object(wiki_compiler, "WIKI_CONCEPTS_DIR", self.root / "concepts"), \
                patch.object(wiki_lint_service, "_scan_merge_candidates", side_effect=scan), \
                patch.object(wiki_lint_service, "_llm_judge", side_effect=judge):
            result = wiki_lint_service.run_lint(self.db)
        self.assertEqual(result["counts"]["concepts_scanned"], 2)
        self.assertTrue((self.root / "report.md").exists())

    def test_batch_paper_and_concept_models_leave_only_connection_for_other_requests(self):
        with patch.object(wiki_compiler, "WIKI_PAPERS_DIR", self.root / "papers"), \
                patch.object(wiki_compiler, "WIKI_CONCEPTS_DIR", self.root / "concepts"), \
                patch.object(wiki_compiler, "_call_llm", side_effect=self.assert_connection_available) as model:
            self.assertEqual(len(wiki_compiler.compile_all_paper_pages(self.db, "", "test")), 2)
            self.assertEqual(len(wiki_compiler.compile_all_concept_pages(self.db, "", "test")), 2)
            self.assertEqual(model.call_count, 4)

    def test_single_compile_failure_does_not_hold_connection(self):
        paper = self.db.get(Paper, "0")
        # Exercise expired attributes left by commit, not just freshly loaded rows.
        self.db.commit()

        def fail(*args, **kwargs):
            self.assert_connection_available()
            raise RuntimeError("model offline")

        with patch.object(wiki_compiler, "WIKI_PAPERS_DIR", self.root / "papers"), \
                patch.object(wiki_compiler, "_call_llm", side_effect=fail):
            with self.assertRaisesRegex(RuntimeError, "model offline"):
                wiki_compiler.compile_paper_page(paper, "", "test")
        self.assertEqual(paper.title, "Paper 0")
        self.assert_connection_available()

    def test_pending_edits_are_not_discarded_or_committed(self):
        paper = self.db.get(Paper, "0")
        paper.title = "Unsaved edit"
        with self.assertRaisesRegex(RuntimeError, "pending database changes"):
            finish_read_snapshot(self.db)
        self.assertIn(paper, self.db.dirty)
        self.db.rollback()
        self.assertEqual(paper.title, "Paper 0")

    def test_pdf_and_thumbnail_release_connection_before_streaming(self):
        pdf = self.root / "paper.pdf"
        pdf.touch()
        with patch.object(papers, "resolve_paper_path", return_value=pdf):
            response = papers.serve_pdf("0", self.db)
        self.assertEqual(response.filename, "0.pdf")
        self.assert_connection_available()
        image = self.root / "image.png"
        image.touch()
        with patch.object(papers, "resolve_artifact_path", return_value=image):
            papers.serve_first_page("0", self.db)
        self.assert_connection_available()
