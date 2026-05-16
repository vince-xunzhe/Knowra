import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.wiki_compiler import _render_frontmatter
from services import wiki_index


def _write_md(path: Path, meta: dict, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_render_frontmatter(meta) + f"\n# {meta.get('title', 'title')}\n\n{body}\n", encoding="utf-8")


class WikiIndexIncrementalTests(unittest.TestCase):
    def test_refresh_index_writes_incremental_body_and_clean_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wiki_dir = root / "wiki"
            papers_dir = wiki_dir / "papers"
            concepts_dir = wiki_dir / "concepts"
            index_path = wiki_dir / "index.md"

            paper_path = papers_dir / "0001-paper-a.md"
            concept_path = concepts_dir / "0007-motion-priors.md"
            _write_md(
                paper_path,
                {
                    "kind": "paper",
                    "title": "Paper A",
                    "paper_id": 1,
                    "slug": "paper-a",
                    "compiled_at": "2026-05-16T10:00:00+00:00",
                    "source_signature": "paper-sig-1",
                },
                "这是一篇关于运动先验的论文摘要。",
            )
            _write_md(
                concept_path,
                {
                    "kind": "concept",
                    "title": "Motion Priors",
                    "concept_id": 7,
                    "slug": "motion-priors",
                    "node_type": "concept",
                    "source_paper_ids": [1],
                    "compiled_at": "2026-05-16T10:01:00+00:00",
                    "source_signature": "concept-sig-1",
                },
                "统一了跨论文的运动先验定义与应用。",
            )

            paper_meta = [{
                "paper_id": 1,
                "filename": paper_path.name,
                "title": "Paper A",
                "slug": "paper-a",
                "compiled_at": "2026-05-16T10:00:00+00:00",
                "source_signature": "paper-sig-1",
                "disk_path": str(paper_path),
            }]
            concept_meta = [{
                "concept_id": 7,
                "filename": concept_path.name,
                "title": "Motion Priors",
                "slug": "motion-priors",
                "node_type": "concept",
                "source_paper_ids": [1],
                "compiled_at": "2026-05-16T10:01:00+00:00",
                "source_signature": "concept-sig-1",
                "disk_path": str(concept_path),
            }]

            with patch("services.wiki_index.WIKI_DIR", wiki_dir), patch(
                "services.wiki_index.INDEX_PATH", index_path
            ), patch("services.wiki_index.list_paper_pages", return_value=paper_meta), patch(
                "services.wiki_index.list_concept_pages", return_value=concept_meta
            ):
                path = wiki_index.refresh_index()
                self.assertEqual(path, index_path)
                text = index_path.read_text(encoding="utf-8")
                self.assertIn("## 论文 · 1", text)
                self.assertIn("- [[paper:1]] **Paper A**", text)
                self.assertIn("## 概念 · 1", text)
                self.assertIn("- [[motion-priors]] **Motion Priors**", text)
                summary = wiki_index.index_summary()

            self.assertTrue(summary["exists"])
            self.assertFalse(summary["stale"])
            self.assertEqual(summary["indexed_papers"], 1)
            self.assertEqual(summary["indexed_concepts"], 1)
            self.assertEqual(summary["indexed_digest"], summary["current_digest"])

    def test_index_summary_marks_stale_when_digest_changes_without_count_delta(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wiki_dir = root / "wiki"
            papers_dir = wiki_dir / "papers"
            index_path = wiki_dir / "index.md"
            paper_path = papers_dir / "0001-paper-a.md"
            _write_md(
                paper_path,
                {
                    "kind": "paper",
                    "title": "Paper A",
                    "paper_id": 1,
                    "slug": "paper-a",
                    "compiled_at": "2026-05-16T10:00:00+00:00",
                    "source_signature": "paper-sig-1",
                },
                "初始摘要。",
            )

            base_meta = [{
                "paper_id": 1,
                "filename": paper_path.name,
                "title": "Paper A",
                "slug": "paper-a",
                "compiled_at": "2026-05-16T10:00:00+00:00",
                "source_signature": "paper-sig-1",
                "disk_path": str(paper_path),
            }]
            changed_meta = [{
                "paper_id": 1,
                "filename": paper_path.name,
                "title": "Paper A",
                "slug": "paper-a",
                "compiled_at": "2026-05-16T10:05:00+00:00",
                "source_signature": "paper-sig-2",
                "disk_path": str(paper_path),
            }]

            with patch("services.wiki_index.WIKI_DIR", wiki_dir), patch(
                "services.wiki_index.INDEX_PATH", index_path
            ), patch("services.wiki_index.list_paper_pages", return_value=base_meta), patch(
                "services.wiki_index.list_concept_pages", return_value=[]
            ):
                wiki_index.refresh_index()

            with patch("services.wiki_index.WIKI_DIR", wiki_dir), patch(
                "services.wiki_index.INDEX_PATH", index_path
            ), patch("services.wiki_index.list_paper_pages", return_value=changed_meta), patch(
                "services.wiki_index.list_concept_pages", return_value=[]
            ):
                summary = wiki_index.index_summary()

            self.assertTrue(summary["exists"])
            self.assertTrue(summary["stale"])
            self.assertEqual(summary["indexed_papers"], 1)
            self.assertEqual(summary["current_papers"], 1)
            self.assertNotEqual(summary["indexed_digest"], summary["current_digest"])


if __name__ == "__main__":
    unittest.main()
