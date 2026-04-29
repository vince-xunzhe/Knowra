import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.wiki_compiler import _reconcile_generated_pages, _render_frontmatter


def _write_page(path: Path, meta: dict, body: str = "body") -> None:
    path.write_text(_render_frontmatter(meta) + body + "\n", encoding="utf-8")


class WikiCompilerReconcileTests(unittest.TestCase):
    def test_reconcile_removes_duplicate_files_for_same_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            old_path = base / "0004-2501.12387v1.pdf.md"
            new_path = base / "0004-continuous-3d-perception-model-with-persistent-state.md"
            _write_page(
                old_path,
                {
                    "kind": "paper",
                    "paper_id": 4,
                    "title": "2501.12387v1.pdf",
                    "compiled_at": "2026-04-29T02:49:28+00:00",
                },
            )
            _write_page(
                new_path,
                {
                    "kind": "paper",
                    "paper_id": 4,
                    "title": "Continuous 3D Perception Model with Persistent State",
                    "compiled_at": "2026-04-29T08:35:05+00:00",
                },
            )

            result = _reconcile_generated_pages(
                base,
                "paper_id",
                {4: new_path},
                prune_orphans=True,
            )

            self.assertTrue(new_path.exists())
            self.assertFalse(old_path.exists())
            self.assertEqual(result["removed_count"], 1)
            self.assertEqual(result["duplicate_removed"], 1)
            self.assertEqual(result["orphan_removed"], 0)

    def test_reconcile_keeps_single_legacy_file_until_recompile_writes_new_slug(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            old_path = base / "0004-2501.12387v1.pdf.md"
            expected_new_path = base / "0004-continuous-3d-perception-model-with-persistent-state.md"
            _write_page(
                old_path,
                {
                    "kind": "paper",
                    "paper_id": 4,
                    "title": "2501.12387v1.pdf",
                    "compiled_at": "2026-04-29T02:49:28+00:00",
                },
            )

            result = _reconcile_generated_pages(
                base,
                "paper_id",
                {4: expected_new_path},
                prune_orphans=True,
            )

            self.assertTrue(old_path.exists())
            self.assertFalse(expected_new_path.exists())
            self.assertEqual(result["removed_count"], 0)

    def test_reconcile_removes_orphan_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            orphan = base / "9999-obsolete.md"
            _write_page(
                orphan,
                {
                    "kind": "concept",
                    "concept_id": 9999,
                    "title": "Obsolete",
                    "compiled_at": "2026-04-29T08:35:05+00:00",
                },
            )

            result = _reconcile_generated_pages(
                base,
                "concept_id",
                {},
                prune_orphans=True,
            )

            self.assertFalse(orphan.exists())
            self.assertEqual(result["removed_count"], 1)
            self.assertEqual(result["duplicate_removed"], 0)
            self.assertEqual(result["orphan_removed"], 1)


if __name__ == "__main__":
    unittest.main()
