import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from database import enable_sqlite_wal
from models import Base, Paper, KnowledgeNode
from routers import papers
from services.graph_service import get_graph_data, add_nodes_from_paper_extraction
from services import wiki_compiler


class BrowsingDuringProcessingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.engine = create_engine(f"sqlite:///{self.tmp.name}/test.db", connect_args={"timeout": 0.05})
        self.addCleanup(self.engine.dispose)
        enable_sqlite_wal(self.engine)
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine, autoflush=False)
        with self.sessions() as db:
            db.add(Paper(id="p", filepath="paper.pdf", filename="paper.pdf", file_hash="test",
                         processed=True, processing_status="graphing", title="Paper",
                         raw_llm_response=json.dumps({"title": "Paper", "year": 2026}),
                         extracted_text="large PDF " * 100000))
            db.add(KnowledgeNode(title="Paper", content="Summary", node_type="paper", source_paper_ids=["p"],
                                 embedding=[0.1] * 3072))
            db.commit()

    def test_incremental_graph_loads_vector_table_only_once(self):
        statements = []
        def record(conn, cursor, statement, params, context, many):
            statements.append(statement)
        event.listen(self.engine, "before_cursor_execute", record)
        with self.sessions() as db:
            add_nodes_from_paper_extraction(
                {"title": "Paper", "techniques": [{"name": "Alpha"}, {"name": "Beta"}], "datasets": []},
                "p", "", "unused", 0.9, db,
                embeddings={(kind, name): [0.1] * 3072 for kind, name in [
                    ("paper", "Paper"), ("technique", "Alpha"), ("technique", "Beta"),
                ]},
            )
        vector_scans = [sql for sql in statements if "embedding IS NOT NULL" in sql]
        self.assertEqual(len(vector_scans), 1)

    def test_list_stays_read_only_while_writer_holds_lock(self):
        with self.engine.connect() as writer, self.sessions() as db:
            writer.exec_driver_sql("BEGIN IMMEDIATE")
            writer.exec_driver_sql("UPDATE papers SET notes='Writing' WHERE id='p'")
            with patch.object(papers, "_sync_bulk_record_state", side_effect=AssertionError("repair in GET")), \
                 patch.object(papers, "_reconcile_processed_paper", side_effect=AssertionError("repair in GET")):
                start = time.monotonic()
                result = papers.list_papers(db)
                self.assertLess(time.monotonic() - start, 1)
                self.assertEqual(result[0]["title"], "Paper")
                self.assertEqual(result[0]["year"], 2026)
                self.assertFalse(db.in_transaction())
            writer.rollback()

    def test_list_releases_connection_before_serialization(self):
        statements = []
        def record(conn, cursor, statement, params, context, many):
            statements.append(statement)
        event.listen(self.engine, "before_cursor_execute", record)
        self.addCleanup(event.remove, self.engine, "before_cursor_execute", record)
        with self.sessions() as db:
            original = papers._serialize_paper_list_item
            def serialize(paper):
                self.assertFalse(db.in_transaction())
                return original(paper)
            with patch.object(papers, "_serialize_paper_list_item", side_effect=serialize):
                papers.list_papers(db)
        self.assertEqual(len(statements), 1)
        self.assertNotIn("papers.extracted_text", statements[0])
        self.assertNotIn("papers.chat_history", statements[0])

    def test_graph_read_does_not_load_vectors(self):
        statements = []
        def record(conn, cursor, statement, params, context, many):
            statements.append(statement)
        event.listen(self.engine, "before_cursor_execute", record)
        self.addCleanup(event.remove, self.engine, "before_cursor_execute", record)
        with self.sessions() as db:
            graph = get_graph_data(db)
            self.assertEqual(len(graph["nodes"]), 1)
        self.assertFalse(any("knowledge_nodes.embedding" in sql for sql in statements))

    def test_detail_does_not_repair_during_browsing(self):
        with self.engine.connect() as writer, self.sessions() as db:
            writer.exec_driver_sql("BEGIN IMMEDIATE")
            with patch.object(papers, "_chat_state", return_value={}):
                result = papers.get_paper("p", db)
            self.assertEqual(result["title"], "Paper")
            self.assertFalse(db.in_transaction())
            writer.rollback()

    def test_freshness_reuses_paper_snapshot_instead_of_querying_per_concept(self):
        with self.sessions() as db:
            for i in range(20):
                db.add(KnowledgeNode(title=f"Concept {i}", content="Summary", node_type="technique",
                                     promotion_status="promoted", source_paper_ids=["p"], embedding=[0.1] * 3072))
            db.commit()
        statements = []
        def record(conn, cursor, statement, params, context, many):
            statements.append(statement)
        event.listen(self.engine, "before_cursor_execute", record)
        self.addCleanup(event.remove, self.engine, "before_cursor_execute", record)
        with self.sessions() as db, \
             patch.object(wiki_compiler, "list_paper_pages", return_value=[]), \
             patch.object(wiki_compiler, "list_concept_pages", return_value=[]):
            wiki_compiler.compute_freshness_summary(db)
            self.assertFalse(db.in_transaction())
        self.assertEqual(len(statements), 3)
        self.assertFalse(any("knowledge_nodes.embedding" in sql for sql in statements))
