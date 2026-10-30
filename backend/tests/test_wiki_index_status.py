import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi import HTTPException


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from routers import ask, wiki
from services import wiki_index, wiki_search
from services.wiki_compiler import _render_frontmatter
from services.wiki_metrics import estimate_tokens, wiki_directory_metrics


def _write_page(path: Path, *, title: str, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        _render_frontmatter({
            "title": title,
            "compiled_at": "2026-10-30T00:00:00+00:00",
        })
        + f"\n# {title}\n\n{body}\n",
        encoding="utf-8",
    )


class WikiIndexStatusTests(unittest.TestCase):
    def test_directory_metrics_are_explicit_estimates_and_read_only(self):
        with tempfile.TemporaryDirectory() as temporary:
            wiki_dir = Path(temporary) / "wiki"
            _write_page(
                wiki_dir / "papers" / "0001-alpha.md",
                title="Alpha",
                body="中文 ab",
            )
            _write_page(
                wiki_dir / "concepts" / "0002-beta.md",
                title="Beta",
                body="Synthetic concept",
            )

            metrics = wiki_directory_metrics(wiki_dir)

        self.assertEqual(metrics["files"], 2)
        self.assertEqual(metrics["by_kind"], {"paper": 1, "concept": 1})
        self.assertGreater(metrics["bytes"], 0)
        self.assertGreater(metrics["characters"], 0)
        self.assertGreater(metrics["estimated_tokens"], 0)
        self.assertEqual(
            metrics["token_estimate_method"],
            "cjk_chars + non_cjk_chars / 3.6",
        )
        self.assertGreaterEqual(metrics["measurement_ms"], 0)
        self.assertEqual(estimate_tokens("中文ab"), 3)

    def test_overview_status_reports_tokens_latency_and_atomic_failure_preserves_old(self):
        with tempfile.TemporaryDirectory() as temporary:
            wiki_dir = Path(temporary) / "wiki"
            index_path = wiki_dir / "index.md"
            old = "---\nkind: index\n---\n\n# Preserved overview\n"
            index_path.parent.mkdir(parents=True)
            index_path.write_text(old, encoding="utf-8")

            with patch.object(wiki_index, "WIKI_DIR", wiki_dir), patch.object(
                wiki_index, "INDEX_PATH", index_path,
            ), patch.object(wiki_index, "list_paper_pages", return_value=[]), patch.object(
                wiki_index, "list_concept_pages", return_value=[],
            ):
                status = wiki_index.index_summary()
                with patch(
                    "services.wiki_metrics.os.replace",
                    side_effect=OSError("synthetic replace failure"),
                ):
                    with self.assertRaisesRegex(OSError, "synthetic replace failure"):
                        wiki_index.refresh_index()

            self.assertTrue(status["exists"])
            self.assertTrue(status["healthy"])
            self.assertGreater(status["estimated_tokens"], 0)
            self.assertGreaterEqual(status["read_latency_ms"], 0)
            self.assertEqual(index_path.read_text(encoding="utf-8"), old)
            self.assertEqual(list(wiki_dir.glob(".index.md.*.tmp")), [])

    def test_search_status_does_not_create_a_missing_database(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            db_path = root / "wiki_search.sqlite"
            wiki_dir = root / "wiki"
            _write_page(
                wiki_dir / "papers" / "0001-alpha.md",
                title="Alpha",
                body="Synthetic evidence",
            )

            with patch.object(wiki_search, "DB_PATH", db_path), patch.object(
                wiki_search, "WIKI_DIR", wiki_dir,
            ):
                status = wiki_search.index_stats()

            self.assertFalse(status["exists"])
            self.assertFalse(status["healthy"])
            self.assertTrue(status["stale"])
            self.assertEqual(status["source"]["files"], 1)
            self.assertFalse(db_path.exists())

    def test_failed_search_refresh_keeps_the_previous_database_queryable(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            db_path = root / "wiki_search.sqlite"
            wiki_dir = root / "wiki"
            _write_page(
                wiki_dir / "papers" / "0001-alpha.md",
                title="Alpha",
                body="Preserved searchable evidence",
            )

            with patch.object(wiki_search, "DB_PATH", db_path), patch.object(
                wiki_search, "WIKI_DIR", wiki_dir,
            ):
                rebuilt = wiki_search.rebuild_index()
                before = db_path.read_bytes()
                self.assertEqual(
                    wiki_search.search("Preserved searchable evidence")[0]["title"],
                    "Alpha",
                )
                with patch.object(
                    wiki_search,
                    "_parse_md",
                    side_effect=OSError("synthetic unreadable source"),
                ):
                    with self.assertRaisesRegex(OSError, "synthetic unreadable source"):
                        wiki_search.rebuild_index()
                status = wiki_search.index_stats()
                hits = wiki_search.search("Preserved searchable evidence")

            self.assertEqual(rebuilt["indexed"], 1)
            self.assertGreaterEqual(rebuilt["duration_ms"], 0)
            self.assertEqual(db_path.read_bytes(), before)
            self.assertTrue(status["healthy"])
            self.assertFalse(status["stale"])
            self.assertEqual(status["total"], 1)
            self.assertGreater(status["estimated_tokens"], 0)
            self.assertEqual(hits[0]["title"], "Alpha")
            self.assertEqual(list(root.glob(".wiki_search.sqlite.*.tmp*")), [])

    def test_unreadable_overview_source_keeps_the_previous_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            wiki_dir = Path(temporary) / "wiki"
            index_path = wiki_dir / "index.md"
            old = "---\nkind: index\n---\n\n# Preserved overview\n"
            index_path.parent.mkdir(parents=True)
            index_path.write_text(old, encoding="utf-8")
            unreadable = {"unreadable_files": 1}

            with patch.object(wiki_index, "WIKI_DIR", wiki_dir), patch.object(
                wiki_index, "INDEX_PATH", index_path,
            ), patch.object(
                wiki_index, "wiki_directory_metrics", return_value=unreadable,
            ):
                with self.assertRaisesRegex(OSError, "1 unreadable"):
                    wiki_index.refresh_index()

            self.assertEqual(index_path.read_text(encoding="utf-8"), old)
            self.assertEqual(list(wiki_dir.glob(".index.md.*.tmp")), [])

    def test_failed_search_atomic_replace_keeps_the_previous_database(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            db_path = root / "wiki_search.sqlite"
            wiki_dir = root / "wiki"
            _write_page(
                wiki_dir / "papers" / "0001-alpha.md",
                title="Alpha",
                body="Preserved searchable evidence",
            )

            with patch.object(wiki_search, "DB_PATH", db_path), patch.object(
                wiki_search, "WIKI_DIR", wiki_dir,
            ):
                wiki_search.rebuild_index()
                before = db_path.read_bytes()
                with patch.object(
                    wiki_search.os,
                    "replace",
                    side_effect=OSError("synthetic replace failure"),
                ):
                    with self.assertRaisesRegex(OSError, "synthetic replace failure"):
                        wiki_search.rebuild_index()
                status = wiki_search.index_stats()
                hits = wiki_search.search("Preserved searchable evidence")

            self.assertEqual(db_path.read_bytes(), before)
            self.assertTrue(status["healthy"])
            self.assertEqual(hits[0]["title"], "Alpha")
            self.assertEqual(list(root.glob(".wiki_search.sqlite.*.tmp*")), [])

    def test_search_status_marks_document_count_drift_stale(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            db_path = root / "wiki_search.sqlite"
            wiki_dir = root / "wiki"
            _write_page(
                wiki_dir / "papers" / "0001-alpha.md",
                title="Alpha",
                body="Evidence A",
            )
            with patch.object(wiki_search, "DB_PATH", db_path), patch.object(
                wiki_search, "WIKI_DIR", wiki_dir,
            ):
                wiki_search.rebuild_index()
                _write_page(
                    wiki_dir / "concepts" / "0002-beta.md",
                    title="Beta",
                    body="Evidence B",
                )
                status = wiki_search.index_stats()

            self.assertTrue(status["healthy"])
            self.assertTrue(status["stale"])
            self.assertEqual(status["total"], 1)
            self.assertEqual(status["source"]["files"], 2)
            self.assertGreater(status["db_size"], 0)
            self.assertGreaterEqual(status["status_latency_ms"], 0)

    def test_search_status_marks_newer_content_stale_without_count_change(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            db_path = root / "wiki_search.sqlite"
            wiki_dir = root / "wiki"
            page = wiki_dir / "papers" / "0001-alpha.md"
            _write_page(page, title="Alpha", body="Evidence A")
            with patch.object(wiki_search, "DB_PATH", db_path), patch.object(
                wiki_search, "WIKI_DIR", wiki_dir,
            ):
                wiki_search.rebuild_index()
                newer_ns = db_path.stat().st_mtime_ns + 1_000_000_000
                _write_page(page, title="Alpha", body="Changed evidence")
                os.utime(page, ns=(newer_ns, newer_ns))
                status = wiki_search.index_stats()

            self.assertEqual(status["total"], status["source"]["files"])
            self.assertTrue(status["stale"])

    def test_refresh_routes_explain_when_the_old_index_is_preserved(self):
        preserved_overview = {"exists": True, "healthy": True}
        with patch("routers.ask.wiki_index.index_summary", return_value=preserved_overview), patch(
            "routers.ask.wiki_index.rebuild_index",
            side_effect=RuntimeError("synthetic overview failure"),
        ), patch("routers.ask.load_config", return_value={}), patch(
            "routers.ask.task_model_id", return_value="synthetic/model",
        ):
            with self.assertRaises(HTTPException) as overview_error:
                ask.rebuild_index(db=MagicMock())
        self.assertIn("旧 index.md 仍可用", overview_error.exception.detail)

        with patch("routers.ask.wiki_index.index_summary", return_value=preserved_overview), patch(
            "routers.ask.load_config",
            side_effect=RuntimeError("synthetic config failure"),
        ):
            with self.assertRaises(HTTPException) as config_error:
                ask.rebuild_index(db=MagicMock())
        self.assertIn("旧 index.md 仍可用", config_error.exception.detail)

        preserved_search = {"exists": True, "healthy": True}
        with patch(
            "routers.wiki.wiki_search_service.index_stats",
            return_value=preserved_search,
        ), patch(
            "routers.wiki.wiki_search_service.rebuild_index",
            side_effect=RuntimeError("synthetic search failure"),
        ):
            with self.assertRaises(HTTPException) as search_error:
                wiki.wiki_reindex()
        self.assertIn("旧索引仍可用", search_error.exception.detail)

    def test_invalid_utf8_source_still_reports_preserved_overview(self):
        with tempfile.TemporaryDirectory() as temporary:
            wiki_dir = Path(temporary) / "wiki"
            papers_dir = wiki_dir / "papers"
            concepts_dir = wiki_dir / "concepts"
            index_path = wiki_dir / "index.md"
            papers_dir.mkdir(parents=True)
            concepts_dir.mkdir(parents=True)
            old = "---\nkind: index\n---\n\n# Preserved overview\n"
            index_path.write_text(old, encoding="utf-8")
            (papers_dir / "invalid.md").write_bytes(b"\xff\xfe invalid UTF-8")

            with patch.object(wiki_index, "WIKI_DIR", wiki_dir), patch.object(
                wiki_index, "INDEX_PATH", index_path,
            ), patch(
                "services.wiki_compiler.WIKI_PAPERS_DIR", papers_dir,
            ), patch(
                "services.wiki_compiler.WIKI_CONCEPTS_DIR", concepts_dir,
            ), patch(
                "routers.ask.load_config", return_value={},
            ), patch(
                "routers.ask.task_model_id", return_value="synthetic/model",
            ):
                status = wiki_index.index_summary()
                with self.assertRaises(HTTPException) as error:
                    ask.rebuild_index(db=MagicMock())

            self.assertTrue(status["healthy"])
            self.assertFalse(status["source_healthy"])
            self.assertTrue(status["stale"])
            self.assertIn("旧 index.md 仍可用", error.exception.detail)
            self.assertEqual(index_path.read_text(encoding="utf-8"), old)

    def test_successful_reindex_routes_return_the_new_status_snapshot(self):
        overview = {
            "exists": True,
            "healthy": True,
            "path": "fixture/index.md",
            "size": 42,
        }
        overview_path = MagicMock()
        overview_path.__str__.return_value = "fixture/index.md"
        overview_path.stat.return_value.st_size = 42
        with patch(
            "routers.ask.wiki_index.index_summary",
            side_effect=[{"exists": True, "healthy": True}, overview],
        ), patch(
            "routers.ask.wiki_index.rebuild_index", return_value=overview_path,
        ), patch("routers.ask.load_config", return_value={}), patch(
            "routers.ask.task_model_id", return_value="synthetic/model",
        ):
            overview_result = ask.rebuild_index(db=MagicMock())
        self.assertEqual(overview_result["summary"], overview)

        search = {"exists": True, "healthy": True, "total": 3}
        with patch(
            "routers.wiki.wiki_search_service.index_stats",
            side_effect=[{"exists": True, "healthy": True}, search],
        ), patch(
            "routers.wiki.wiki_search_service.rebuild_index",
            return_value={"indexed": 3},
        ):
            search_result = wiki.wiki_reindex()
        self.assertEqual(search_result["summary"], search)


if __name__ == "__main__":
    unittest.main()
