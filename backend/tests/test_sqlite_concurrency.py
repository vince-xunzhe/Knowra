import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from database import enable_sqlite_wal
from models import Base, Paper, KnowledgeNode, KnowledgeEdge
from routers import papers
from services import graph_service
from services.local_instance import LocalBackendLock
from services.paper_pipeline_service import is_recoverable_error
from services.sqlite_backup import backup_sqlite
from services.scanner_service import scan_directory


class SQLiteConcurrencyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / "test.db"
        self.engine = create_engine(f"sqlite:///{self.path}", connect_args={"timeout": 0.02})
        self.addCleanup(self.engine.dispose)
        enable_sqlite_wal(self.engine)
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine, autoflush=False)
        with self.sessions() as db:
            db.add(Paper(id="p", filepath="paper.pdf", filename="paper.pdf", file_hash="hash"))
            db.commit()
        self.extraction = {"title": "Paper", "problem_area": "AI", "abstract_summary": "Summary",
                           "techniques": [{"name": "Method", "aliases": ["Alias"]}],
                           "datasets": [{"name": "Data", "purpose": "train"}],
                           "baselines": ["Alias", "Baseline"]}

    def test_wal_reader_does_not_block_commit_and_backup_includes_wal(self):
        with self.engine.connect() as reader, self.engine.connect() as writer:
            reader.exec_driver_sql("BEGIN")
            reader.execute(text("SELECT * FROM papers")).all()
            writer.execute(text("UPDATE papers SET title='Committed' WHERE id='p'"))
            writer.commit()
            backup_sqlite(self.path, self.root / "backup.db")
            with sqlite3.connect(self.root / "backup.db") as backup:
                self.assertEqual(backup.execute("SELECT title FROM papers").fetchone()[0], "Committed")

    def test_scan_and_status_distinguish_pending_from_failed(self):
        with self.sessions() as db:
            db.add(Paper(id="failed", filepath="failed.pdf", filename="failed.pdf", file_hash="failed",
                         processed=False, error="timeout", processing_status="failed"))
            db.add(Paper(id="done", filepath="done.pdf", filename="done.pdf", file_hash="done", processed=True))
            db.commit()
            status = papers.get_status(db)
            scan = scan_directory(str(self.root), db)
            for result in (status, scan):
                self.assertEqual(result["pending"], 1)
                self.assertEqual(result["failed_count"], 1)
                self.assertEqual(result["unprocessed"], 2)

    def test_empty_submission_preserves_previous_failure_state(self):
        old = dict(papers.processing_state)
        self.addCleanup(papers.processing_state.update, old)
        papers.processing_state.update(running=False, errors=1, last_message="Previous failure")
        with self.sessions() as db:
            paper = db.get(Paper, "p")
            paper.error = "timeout"
            db.commit()
            with patch.object(papers, "_start_processing_worker") as start:
                result = papers.process_all(db)
            start.assert_not_called()
            self.assertFalse(result["accepted"])
            self.assertEqual(result["failed_count"], 1)
            self.assertEqual(result["pending"], 0)
            self.assertIn("重试", result["message"])
            self.assertEqual(papers.processing_state["errors"], 1)
            self.assertEqual(papers.processing_state["last_message"], "Previous failure")

    def test_explicit_retry_dispatches_only_failed_papers(self):
        old = dict(papers.processing_state)
        self.addCleanup(papers.processing_state.update, old)
        papers.processing_state["running"] = False
        with self.sessions() as db:
            failed = db.get(Paper, "p")
            failed.error = "timeout"
            failed.processing_status = "failed"
            db.add(Paper(id="pending", filepath="pending.pdf", filename="pending.pdf", file_hash="pending"))
            db.commit()
            with patch.object(papers, "_reconcile_failed_papers", return_value=[failed]), \
                 patch.object(papers, "_prepare_reprocess") as prepare, \
                 patch.object(papers, "_start_processing_worker") as start:
                result = papers.retry_failed_papers(db)
                prepare.assert_called_once_with(db, failed)
                start.assert_called_once_with(["p"])
                self.assertEqual(result["retried"], 1)

    def test_real_write_lock_is_recoverable_and_rollback_allows_retry(self):
        with self.engine.connect() as writer, self.sessions() as db:
            writer.exec_driver_sql("BEGIN IMMEDIATE")
            with self.assertRaises(OperationalError) as caught:
                db.execute(text("UPDATE papers SET title='Retry' WHERE id='p'"))
                db.commit()
            self.assertTrue(is_recoverable_error(caught.exception))
            db.rollback()
            writer.rollback()
            db.execute(text("UPDATE papers SET title='Retry' WHERE id='p'"))
            db.commit()
        self.assertFalse(is_recoverable_error(OperationalError("", {}, sqlite3.OperationalError("no such table: typo"))))

    def test_embeddings_run_without_transaction_and_graph_is_atomic(self):
        with self.sessions() as db:
            def embed(*args):
                self.assertFalse(db.in_transaction())
                with self.sessions() as other:
                    other.execute(text("UPDATE papers SET notes='During embedding' WHERE id='p'"))
                    other.commit()
                return [1.0, 0.0]
            with patch.object(graph_service, "get_embedding", side_effect=embed) as model:
                embeddings = graph_service.prepare_graph_embeddings(self.extraction, "p", db, "", "test")
                self.assertEqual(model.call_count, 5)
            with patch.object(graph_service, "get_embedding", side_effect=AssertionError("network in write transaction")):
                graph_service.add_nodes_from_paper_extraction(self.extraction, "p", "", "test", 0.6, db, embeddings=embeddings)
                db.rollback()
                self.assertEqual(db.query(KnowledgeNode).count(), 0)
                self.assertEqual(db.query(KnowledgeEdge).count(), 0)
                graph_service.add_nodes_from_paper_extraction(self.extraction, "p", "", "test", 0.6, db, embeddings=embeddings)
                db.commit()
                self.assertEqual(db.query(KnowledgeNode).count(), 5)
                first_count = db.query(KnowledgeEdge).count()
                graph_service.add_nodes_from_paper_extraction(self.extraction, "p", "", "test", 0.6, db, embeddings=embeddings)
                db.commit()
                self.assertEqual(db.query(KnowledgeNode).count(), 5)
                self.assertEqual(db.query(KnowledgeEdge).count(), first_count)

    def test_manual_graph_rebuild_preserves_existing_vectors(self):
        with self.sessions() as db:
            with patch.object(graph_service, "get_embedding", return_value=[1.0, 0.0]):
                vectors = graph_service.prepare_graph_embeddings(self.extraction, "p", db, "", "test")
            graph_service.add_nodes_from_paper_extraction(self.extraction, "p", "", "test", 0.6, db, embeddings=vectors)
            db.commit()
            with patch.object(graph_service, "get_embedding") as model:
                cached = graph_service.prepare_graph_embeddings(self.extraction, "p", db, "", "test")
                model.assert_not_called()
            graph_service.remove_nodes_for_paper(db, "p")
            graph_service.add_nodes_from_paper_extraction(self.extraction, "p", "", "test", 0.6, db, embeddings=cached)
            db.commit()
            nodes = db.query(KnowledgeNode).all()
            self.assertEqual(len(nodes), 5)
            self.assertTrue(all(node.embedding == [1.0, 0.0] for node in nodes))

    def run_pipeline(self, *, fail_preprocess=False, fail_retry_state=False):
        cfg = {"openai_api_key": "test", "extraction_prompt": "test", "similarity_threshold": 0.6,
               "paper_process_max_retries": 3, "paper_process_backoff_base_seconds": 0}
        pdf = self.root / "paper.pdf"
        pdf.touch()
        calls = {"failures": 0}
        original = graph_service._add_similarity_edges
        original_set_state = papers._set_pipeline_state
        state_failures = []

        def set_state(*args, **kwargs):
            if fail_retry_state and kwargs.get("error_recoverable") and not state_failures:
                state_failures.append(True)
                raise OperationalError("COMMIT", {}, sqlite3.OperationalError("database is locked"))
            return original_set_state(*args, **kwargs)

        def fail_once(*args, **kwargs):
            if not calls["failures"]:
                calls["failures"] += 1
                raise OperationalError("COMMIT", {}, sqlite3.OperationalError("database is locked"))
            return original(*args, **kwargs)

        def commit_failure(db):
            if not calls["failures"] and any(isinstance(p, Paper) and p.extracted_text for p in db.dirty):
                calls["failures"] += 1
                raise OperationalError("COMMIT", {}, sqlite3.OperationalError("database is locked"))

        old = dict(papers.processing_state)
        self.addCleanup(papers.processing_state.update, old)
        papers._mark_processing_started(["p"])
        if fail_preprocess:
            event.listen(self.sessions, "before_commit", commit_failure)
            self.addCleanup(event.remove, self.sessions, "before_commit", commit_failure)
        with ExitStack() as stack:
            for name, value in {
                "load_config": cfg, "resolve_paper_path": pdf, "extract_text": ("text", 1),
                "render_first_page": "image.png", "task_model_name": "test", "task_model_id": "test",
                "task_reasoning_effort": "low", "derive_model_paper_category": "Other",
                "derive_model_paper_team": "Other", "extraction_has_critical_issues": False,
                "parse_extraction_response": self.extraction, "_run_wiki_compile_phase": None,
                "_sync_processing_record": None,
            }.items():
                stack.enter_context(patch.object(papers, name, return_value=value))
            stack.enter_context(patch("database.SessionLocal", self.sessions))
            stack.enter_context(patch.object(papers, "_set_pipeline_state", side_effect=set_state))
            embed = stack.enter_context(patch.object(graph_service, "get_embedding", return_value=[1.0, 0.0]))
            if not fail_preprocess:
                stack.enter_context(patch.object(graph_service, "_add_similarity_edges", side_effect=fail_once))
            model = stack.enter_context(patch.object(papers, "extract_knowledge_from_paper", return_value=(
                self.extraction, json.dumps(self.extraction), None, None, None, None)))
            papers._process_single("p")
            self.assertEqual(model.call_count, 1)
            self.assertEqual(embed.call_count, 5)
        self.assertEqual(calls["failures"], 1)
        self.assertEqual(len(state_failures), int(fail_retry_state))
        with self.sessions() as db:
            paper = db.get(Paper, "p")
            self.assertTrue(paper.processed, paper.error)
            self.assertEqual(paper.retry_count, 1)
            self.assertEqual(db.query(KnowledgeNode).count(), 5)

    def test_graph_retry_reuses_successful_model_and_vectors(self):
        self.run_pipeline()

    def test_preprocessing_db_error_is_rolled_back_before_retry(self):
        self.run_pipeline(fail_preprocess=True)

    def test_locked_retry_metadata_does_not_abort_retry(self):
        self.run_pipeline(fail_retry_state=True)

    def test_single_instance_lock_blocks_another_process_and_releases(self):
        path = self.root / "backend.lock"
        lock = LocalBackendLock(path)
        code = "from pathlib import Path; from services.local_instance import LocalBackendLock; LocalBackendLock(Path(%r)).acquire()" % str(path)
        def attempt():
            return subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1],
                                  capture_output=True, text=True, timeout=5)
        lock.acquire()
        try:
            result = attempt()
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("already using", result.stderr)
        finally:
            lock.release()
        self.assertEqual(attempt().returncode, 0)
