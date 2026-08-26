from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

ROOT = Path(__file__).resolve().parents[2]
BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(BACKEND))

from models import Base, KnowledgeEdge, KnowledgeNode, Paper  # noqa: E402
from services.paper_dedupe_service import repair_duplicate_papers  # noqa: E402


class PaperDedupeServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp.name)
        self.wiki_dir = self.tmp_path / "wiki"
        self.wiki_papers = self.wiki_dir / "papers"
        self.wiki_concepts = self.wiki_dir / "concepts"
        self.records_dir = self.tmp_path / "paper_records"
        self.wiki_papers.mkdir(parents=True)
        self.wiki_concepts.mkdir(parents=True)
        self.records_dir.mkdir(parents=True)

        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        self.SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    def tearDown(self):
        self.tmp.cleanup()

    def test_repair_duplicate_paper_rows_rewrites_db_and_wiki_refs(self):
        db = self.SessionLocal()
        try:
            old_id = "11111111-1111-4111-8111-111111111111"
            keep_id = "22222222-2222-4222-8222-222222222222"
            db.add_all([
                Paper(
                    id=old_id,
                    filepath="data/papers/foo.pdf",
                    filename="foo.pdf",
                    file_hash="same-hash",
                    title="Foo",
                    processed=True,
                    processed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                    raw_llm_response='{"title":"old"}',
                    created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                ),
                Paper(
                    id=keep_id,
                    filepath="data/papers/foo-copy.pdf",
                    filename="foo.pdf",
                    file_hash="same-hash",
                    title="Foo",
                    processed=True,
                    processed_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
                    raw_llm_response='{"title":"new"}',
                    created_at=datetime(2026, 1, 1, 0, 0, 1, tzinfo=timezone.utc),
                ),
                KnowledgeNode(
                    id="node-concept",
                    title="Concept",
                    content="Uses both",
                    node_type="concept",
                    source_paper_ids=[old_id, keep_id],
                ),
                KnowledgeNode(
                    id="node-paper",
                    title="Foo",
                    content="Paper node",
                    node_type="paper",
                    source_paper_ids=[old_id],
                ),
                KnowledgeEdge(
                    id="edge-1",
                    source_id="node-paper",
                    target_id="node-concept",
                    relation_type="mentions",
                ),
            ])
            db.commit()

            (self.wiki_papers / "11111111-foo.md").write_text(
                f'---\nkind: "paper"\npaper_id: "{old_id}"\nsource_paper_ids:\n'
                f'  - "{old_id}"\n---\n# Old [[paper:{old_id}]]\n',
                encoding="utf-8",
            )
            (self.wiki_papers / "22222222-foo.md").write_text(
                f'---\nkind: "paper"\npaper_id: "{keep_id}"\nsource_paper_ids:\n'
                f'  - "{keep_id}"\n---\n# Keep [[paper:{keep_id}]]\n',
                encoding="utf-8",
            )
            concept_path = self.wiki_concepts / "node-concept.md"
            concept_path.write_text(
                f'---\nkind: "concept"\nsource_paper_ids:\n'
                f'  - "{old_id}"\n  - "{keep_id}"\n---\n'
                f"Body [[paper:{old_id}]] and [[paper:{keep_id}]]\n",
                encoding="utf-8",
            )
            (self.records_dir / "11111111-foo.md").write_text("old", encoding="utf-8")
            (self.records_dir / "22222222-foo.md").write_text("keep", encoding="utf-8")

            summary = repair_duplicate_papers(
                db,
                wiki_dir=self.wiki_dir,
                wiki_papers_dir=self.wiki_papers,
                records_dir=self.records_dir,
                refresh_indexes=False,
                sync_records=False,
            )

            self.assertEqual(summary.duplicate_groups, 1)
            self.assertEqual(summary.canonical_by_removed, {old_id: keep_id})
            self.assertEqual(db.query(Paper).count(), 1)
            self.assertEqual(db.query(Paper).one().id, keep_id)
            self.assertEqual(db.query(KnowledgeNode).filter_by(id="node-concept").one().source_paper_ids, [keep_id])
            self.assertEqual(db.query(KnowledgeNode).filter_by(id="node-paper").one().source_paper_ids, [keep_id])
            self.assertFalse((self.wiki_papers / "11111111-foo.md").exists())
            self.assertTrue((self.wiki_papers / "22222222-foo.md").exists())

            concept_text = concept_path.read_text(encoding="utf-8")
            self.assertNotIn(old_id, concept_text)
            self.assertIn(keep_id, concept_text)
            self.assertFalse((self.records_dir / "11111111-foo.md").exists())
            self.assertTrue((self.records_dir / "22222222-foo.md").exists())
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
