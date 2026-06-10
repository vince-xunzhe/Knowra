"""Phase 1 W3.1: tests for the SQLite → multi-tenant UUID migration.

Uses an in-memory SQLite seeded with synthetic single-tenant data so the
tests don't touch the real desktop database. Covers:

  - id types become TEXT after migration
  - user_id is populated on every row
  - legacy_id is preserved on every row
  - knowledge_edges.source_id / target_id are rewritten via the nodes
    remap (point to the correct migrated nodes)
  - knowledge_nodes.source_paper_ids JSON arrays are rewritten via the
    papers remap
  - orphan edges (FK-pointing to deleted node) are dropped, not migrated
  - re-running migration is a no-op (idempotency)
  - verify_post_migration returns the expected shape
"""
from __future__ import annotations

import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine, text


ROOT = Path(__file__).resolve().parents[2]
BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(BACKEND))

from multitenant_migration import (  # noqa: E402
    DEFAULT_LOCAL_USER_ID,
    MULTITENANT_MARKER,
    is_multitenant_migrated,
    migrate_to_multitenant,
    verify_post_migration,
)


# ── single-tenant fixture ─────────────────────────────────────────────


def _make_legacy_db() -> "Engine":  # noqa: F821
    """Build an in-memory SQLite with the pre-migration (single-tenant
    INT id) schema and a small synthetic corpus."""
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text(
            """
            CREATE TABLE papers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filepath TEXT NOT NULL,
                filename TEXT NOT NULL,
                file_hash TEXT NOT NULL,
                title TEXT,
                processed BOOLEAN DEFAULT 0,
                processed_at DATETIME,
                authors TEXT,
                num_pages INTEGER,
                created_at DATETIME
            )
            """
        ))
        conn.execute(text(
            """
            CREATE TABLE knowledge_nodes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                node_type TEXT DEFAULT 'concept',
                node_origin TEXT DEFAULT 'auto',
                hidden BOOLEAN DEFAULT 0,
                promotion_status TEXT DEFAULT 'pending',
                promoted_by TEXT,
                promotion_reason TEXT,
                last_promotion_eval_at DATETIME,
                tags TEXT,
                embedding TEXT,
                source_paper_ids TEXT,
                created_at DATETIME
            )
            """
        ))
        conn.execute(text(
            """
            CREATE TABLE knowledge_edges (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id INTEGER NOT NULL,
                target_id INTEGER NOT NULL,
                relation_type TEXT DEFAULT 'related',
                weight REAL DEFAULT 0.0,
                created_at DATETIME
            )
            """
        ))
        conn.execute(text(
            """
            CREATE TABLE llm_calls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                called_at DATETIME,
                task TEXT NOT NULL,
                provider TEXT NOT NULL,
                model TEXT NOT NULL,
                surface TEXT,
                prompt_tokens INTEGER,
                completion_tokens INTEGER,
                total_tokens INTEGER,
                latency_ms INTEGER,
                success BOOLEAN DEFAULT 1,
                error_class TEXT
            )
            """
        ))

        # 3 papers (id 1, 2, 3)
        conn.execute(text(
            """
            INSERT INTO papers (filepath, filename, file_hash, title)
            VALUES
              ('/a.pdf', 'a.pdf', 'hashA', 'Paper A'),
              ('/b.pdf', 'b.pdf', 'hashB', 'Paper B'),
              ('/c.pdf', 'c.pdf', 'hashC', 'Paper C')
            """
        ))
        # 4 nodes (id 1, 2, 3, 4)
        #   node 1 — references papers [1, 2]
        #   node 2 — references papers [2, 3]
        #   node 3 — references paper [1]
        #   node 4 — references nothing
        conn.execute(text(
            """
            INSERT INTO knowledge_nodes (title, content, source_paper_ids)
            VALUES
              ('Concept X', 'content X', '[1, 2]'),
              ('Concept Y', 'content Y', '[2, 3]'),
              ('Concept Z', 'content Z', '[1]'),
              ('Concept W', 'content W', '[]')
            """
        ))
        # 4 edges:
        #   edge 1: node 1 → node 2  (valid)
        #   edge 2: node 2 → node 3  (valid)
        #   edge 3: node 3 → node 1  (valid)
        #   edge 4: node 99 → node 1 (orphan source — should be dropped)
        conn.execute(text(
            """
            INSERT INTO knowledge_edges (source_id, target_id, relation_type)
            VALUES
              (1, 2, 'related'),
              (2, 3, 'derives_from'),
              (3, 1, 'related'),
              (99, 1, 'related')
            """
        ))
        # 2 llm_calls (standalone)
        conn.execute(text(
            """
            INSERT INTO llm_calls (task, provider, model, total_tokens, success)
            VALUES
              ('paper_extract', 'openai', 'gpt-4o', 1000, 1),
              ('wiki_compile', 'codex_cli', 'gpt-5.5', 0, 1)
            """
        ))
    return engine


# ── unit tests ────────────────────────────────────────────────────────


class MultitenantMigrationTests(unittest.TestCase):
    def setUp(self):
        self.engine = _make_legacy_db()

    # ---- idempotency ----------------------------------------------------

    def test_is_multitenant_migrated_false_initially(self):
        with self.engine.connect() as conn:
            self.assertFalse(is_multitenant_migrated(conn))

    def test_marker_set_after_migration(self):
        with self.engine.begin() as conn:
            migrate_to_multitenant(conn, user_id=DEFAULT_LOCAL_USER_ID)
        with self.engine.connect() as conn:
            self.assertTrue(is_multitenant_migrated(conn))
            row = conn.execute(
                text("SELECT value FROM _meta WHERE key = :k"),
                {"k": MULTITENANT_MARKER},
            ).fetchone()
            counts = json.loads(row[0])
            self.assertEqual(counts["papers"], 3)
            self.assertEqual(counts["knowledge_nodes"], 4)
            self.assertEqual(counts["knowledge_edges"], 3)  # 1 orphan dropped
            self.assertEqual(counts["llm_calls"], 2)

    def test_second_call_is_noop(self):
        with self.engine.begin() as conn:
            first = migrate_to_multitenant(conn, user_id=DEFAULT_LOCAL_USER_ID)
        with self.engine.begin() as conn:
            second = migrate_to_multitenant(conn, user_id=DEFAULT_LOCAL_USER_ID)
        self.assertNotEqual(first, {})
        self.assertEqual(second, {})

    # ---- id types -------------------------------------------------------

    def test_ids_become_text_after_migration(self):
        with self.engine.begin() as conn:
            migrate_to_multitenant(conn, user_id=DEFAULT_LOCAL_USER_ID)
        with self.engine.connect() as conn:
            for tbl in ("papers", "knowledge_nodes", "knowledge_edges", "llm_calls"):
                info = conn.execute(text(f"PRAGMA table_info({tbl})")).fetchall()
                id_col = next(r for r in info if r[1] == "id")
                self.assertEqual(id_col[2].upper(), "TEXT", f"{tbl}.id should be TEXT")

    def test_legacy_id_column_added_and_populated(self):
        with self.engine.begin() as conn:
            migrate_to_multitenant(conn, user_id=DEFAULT_LOCAL_USER_ID)
        with self.engine.connect() as conn:
            for tbl in ("papers", "knowledge_nodes", "knowledge_edges", "llm_calls"):
                rows = conn.execute(
                    text(f"SELECT legacy_id FROM {tbl}")
                ).fetchall()
                self.assertTrue(all(r[0] is not None for r in rows),
                                f"{tbl}.legacy_id has nulls")

    def test_user_id_assigned_everywhere(self):
        with self.engine.begin() as conn:
            migrate_to_multitenant(conn, user_id="test-uid")
        with self.engine.connect() as conn:
            for tbl in ("papers", "knowledge_nodes", "knowledge_edges", "llm_calls"):
                nulls = conn.execute(
                    text(f"SELECT COUNT(*) FROM {tbl} WHERE user_id IS NULL")
                ).scalar()
                self.assertEqual(nulls, 0, f"{tbl} has rows without user_id")
                wrong = conn.execute(
                    text(f"SELECT COUNT(*) FROM {tbl} WHERE user_id != 'test-uid'")
                ).scalar()
                self.assertEqual(wrong, 0, f"{tbl} has user_id != test-uid")

    # ---- FK rewriting ----------------------------------------------------

    def test_edges_source_target_rewritten_via_nodes_remap(self):
        with self.engine.begin() as conn:
            migrate_to_multitenant(conn, user_id=DEFAULT_LOCAL_USER_ID)
        with self.engine.connect() as conn:
            # Every edge's source/target must exist in knowledge_nodes
            orphans = conn.execute(
                text(
                    """
                    SELECT COUNT(*) FROM knowledge_edges e
                    WHERE NOT EXISTS (SELECT 1 FROM knowledge_nodes n WHERE n.id = e.source_id)
                       OR NOT EXISTS (SELECT 1 FROM knowledge_nodes n WHERE n.id = e.target_id)
                    """
                )
            ).scalar()
            self.assertEqual(orphans, 0, "FK rewrite left dangling references")

    def test_orphan_edges_dropped(self):
        with self.engine.begin() as conn:
            migrate_to_multitenant(conn, user_id=DEFAULT_LOCAL_USER_ID)
        with self.engine.connect() as conn:
            cnt = conn.execute(text("SELECT COUNT(*) FROM knowledge_edges")).scalar()
            self.assertEqual(cnt, 3, "expected 3 valid edges (the 4th was orphan)")
            # And the orphan (legacy_id=4) should be absent
            row = conn.execute(
                text("SELECT id FROM knowledge_edges WHERE legacy_id = 4")
            ).fetchone()
            self.assertIsNone(row, "orphan edge with legacy_id=4 was not dropped")

    def test_source_paper_ids_rewritten(self):
        with self.engine.begin() as conn:
            migrate_to_multitenant(conn, user_id=DEFAULT_LOCAL_USER_ID)
        with self.engine.connect() as conn:
            # Get the new paper UUIDs
            paper_ids = {
                int(r[0]): r[1]
                for r in conn.execute(
                    text("SELECT legacy_id, id FROM papers")
                ).fetchall()
            }
            # Concept X originally referenced [1, 2]; should now hold UUIDs
            # of papers with legacy_id 1 and 2.
            row = conn.execute(
                text("SELECT source_paper_ids FROM knowledge_nodes WHERE title = 'Concept X'")
            ).fetchone()
            actual = json.loads(row[0])
            expected = [paper_ids[1], paper_ids[2]]
            self.assertEqual(actual, expected)
            # Concept W originally referenced []; should still be [].
            row = conn.execute(
                text("SELECT source_paper_ids FROM knowledge_nodes WHERE title = 'Concept W'")
            ).fetchone()
            self.assertEqual(json.loads(row[0]), [])

    def test_source_paper_ids_drops_orphans(self):
        """If knowledge_nodes points at a paper that no longer exists in
        the papers table, the migration should silently drop that ref."""
        # Inject an orphan ref pre-migration
        with self.engine.begin() as conn:
            conn.execute(text(
                """
                INSERT INTO knowledge_nodes (title, content, source_paper_ids)
                VALUES ('Concept Orphan', 'content', '[1, 999]')
                """
            ))
        with self.engine.begin() as conn:
            migrate_to_multitenant(conn, user_id=DEFAULT_LOCAL_USER_ID)
        with self.engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT source_paper_ids FROM knowledge_nodes WHERE title = 'Concept Orphan'"
                )
            ).fetchone()
            arr = json.loads(row[0])
            self.assertEqual(len(arr), 1, "orphan paper ref 999 should be dropped")

    # ---- payload preservation -------------------------------------------

    def test_paper_columns_preserved(self):
        with self.engine.begin() as conn:
            migrate_to_multitenant(conn, user_id=DEFAULT_LOCAL_USER_ID)
        with self.engine.connect() as conn:
            row = conn.execute(
                text("SELECT title, filepath, file_hash FROM papers WHERE legacy_id = 1")
            ).fetchone()
            self.assertEqual(row[0], "Paper A")
            self.assertEqual(row[1], "/a.pdf")
            self.assertEqual(row[2], "hashA")

    def test_node_columns_preserved(self):
        with self.engine.begin() as conn:
            migrate_to_multitenant(conn, user_id=DEFAULT_LOCAL_USER_ID)
        with self.engine.connect() as conn:
            row = conn.execute(
                text("SELECT title, content, node_type FROM knowledge_nodes WHERE legacy_id = 1")
            ).fetchone()
            self.assertEqual(row[0], "Concept X")
            self.assertEqual(row[1], "content X")
            self.assertEqual(row[2], "concept")

    # ---- verify helper --------------------------------------------------

    def test_verify_post_migration_reports_clean(self):
        with self.engine.begin() as conn:
            migrate_to_multitenant(conn, user_id=DEFAULT_LOCAL_USER_ID)
        with self.engine.connect() as conn:
            report = verify_post_migration(conn)
        for tbl in ("papers", "knowledge_nodes", "knowledge_edges", "llm_calls"):
            self.assertEqual(report["id_types"][tbl], "TEXT")
            self.assertTrue(report["all_user_id_set"][tbl])
        self.assertEqual(report["orphan_edges"], 0)
        self.assertEqual(report["orphan_node_refs"], 0)
        self.assertEqual(report["row_counts"]["papers"], 3)
        self.assertEqual(report["row_counts"]["knowledge_nodes"], 4)
        self.assertEqual(report["row_counts"]["knowledge_edges"], 3)
        self.assertEqual(report["row_counts"]["llm_calls"], 2)

    # ---- safety net -----------------------------------------------------

    def test_migration_aborts_if_id_already_text_but_meta_missing(self):
        # Simulate a partially-migrated database: id column is TEXT but
        # _meta marker is absent (corrupted prior migration).
        with self.engine.begin() as conn:
            migrate_to_multitenant(conn, user_id=DEFAULT_LOCAL_USER_ID)
            # Drop the marker but leave the schema migrated.
            conn.execute(text("DELETE FROM _meta WHERE key = :k"),
                         {"k": MULTITENANT_MARKER})
        with self.engine.begin() as conn:
            result = migrate_to_multitenant(conn, user_id=DEFAULT_LOCAL_USER_ID)
        self.assertEqual(result, {}, "migration should abort safely")

    def test_empty_database_migration_succeeds(self):
        # No data at all; migration should set the marker and return {}.
        engine = create_engine("sqlite:///:memory:")
        with engine.begin() as conn:
            # Need to at least have the table definitions so the
            # migration can find them. Same DDL as fixture but no INSERTs.
            conn.execute(text("CREATE TABLE papers (id INTEGER PRIMARY KEY, filepath TEXT, filename TEXT, file_hash TEXT)"))
            conn.execute(text("CREATE TABLE knowledge_nodes (id INTEGER PRIMARY KEY, title TEXT NOT NULL, content TEXT NOT NULL)"))
            conn.execute(text("CREATE TABLE knowledge_edges (id INTEGER PRIMARY KEY, source_id INTEGER, target_id INTEGER)"))
        with engine.begin() as conn:
            counts = migrate_to_multitenant(conn, user_id=DEFAULT_LOCAL_USER_ID)
        # No rows to migrate, so no per-table count, but marker should be set
        with engine.connect() as conn:
            self.assertTrue(is_multitenant_migrated(conn))


if __name__ == "__main__":
    unittest.main()
