"""Phase 1 W4: end-to-end RLS isolation tests against a real Postgres.

Skipped unless ``KNOWRA_ALLOW_DESTRUCTIVE_PG_TESTS=1`` and a safe
``KNOWRA_TEST_POSTGRES_URL`` are both set. The URL must use a loopback
host, an explicit non-default port, and the dedicated ``knowra_test``
database, with no query parameters. The test creates / drops the
schema, including an ``auth`` stub that mimics Supabase Auth's
``auth.users`` + ``auth.uid()`` API just enough to make our RLS
policies behave at runtime.

Unset all ``PG*`` environment variables first; libpq connection overrides
must not redirect or supplement the explicitly approved test URL.

Recommended local setup:

    docker run --rm -d --name knowra-rls-test -p 54322:5432 \\
        -e POSTGRES_PASSWORD=knowra -e POSTGRES_DB=knowra_test \\
        postgres:16

    export KNOWRA_TEST_POSTGRES_URL=postgresql://postgres:knowra@localhost:54322/knowra_test
    export KNOWRA_ALLOW_DESTRUCTIVE_PG_TESTS=1
    backend/.venv/bin/python -m unittest backend.tests.test_postgres_rls

Coverage:
  - user A inserts rows; user B SELECT returns 0 rows from the same tables
  - user B INSERT with user_id=A is rejected by the WITH CHECK policy
  - knowledge_edges with cross-user source/target is rejected by trigger
  - cloud_deletions tombstones are also tenant-isolated
  - auth.uid()=NULL (unauth) sees nothing
"""
from __future__ import annotations

import os
import sys
import unittest
import uuid
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[2]
BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(BACKEND))


PG_URL = os.environ.get("KNOWRA_TEST_POSTGRES_URL")
MIGRATIONS_DIR = ROOT / "supabase" / "migrations"


def allow_destructive_postgres_tests(url: str | None, opt_in: str | None) -> bool:
    """Fail closed before importing a driver or opening a destructive target.

    Query parameters are rejected because libpq accepts connection overrides
    there. Do not put the URL (which may contain a password) in error output.
    """
    # libpq environment options (including PGHOSTADDR and PGSERVICE) can
    # override or supplement a URI. Require a fully explicit test target.
    if opt_in != "1" or not url or any(key.startswith("PG") for key in os.environ):
        return False
    try:
        target = urlsplit(url)
        return (
            target.scheme in {"postgresql", "postgres"}
            and target.hostname in {"localhost", "127.0.0.1", "::1"}
            and target.port is not None
            and target.port != 5432
            and unquote(target.path) == "/knowra_test"
            and not target.query
            and not target.fragment
        )
    except (ValueError, TypeError):
        return False


PG_OPTED_IN = allow_destructive_postgres_tests(
    PG_URL, os.environ.get("KNOWRA_ALLOW_DESTRUCTIVE_PG_TESTS")
)


# An ``auth`` stub: real Supabase ships this schema as part of GoTrue;
# for testing locally we declare just the surface our RLS policies
# touch (``auth.users`` rows + ``auth.uid()`` returning the per-session
# JWT claim ``sub``).
AUTH_STUB_SQL = """
CREATE SCHEMA IF NOT EXISTS auth;

CREATE TABLE IF NOT EXISTS auth.users (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  email       TEXT,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE OR REPLACE FUNCTION auth.uid() RETURNS UUID AS $$
  SELECT NULLIF(current_setting('request.jwt.claim.sub', true), '')::uuid;
$$ LANGUAGE sql STABLE;
"""


def _load_psycopg2():
    try:
        import psycopg2  # noqa: F401
        from psycopg2.extras import RealDictCursor  # noqa: F401
        return True
    except ImportError:
        return False


@unittest.skipUnless(
    PG_OPTED_IN and _load_psycopg2(),
    "RLS tests require explicit destructive opt-in, a dedicated loopback "
    "knowra_test database on a non-default port, no PG* environment overrides, "
    "and psycopg2-binary",
)
class PostgresRLSTests(unittest.TestCase):
    """Apply all 5 migrations to a real Postgres, then exercise RLS."""

    @classmethod
    def setUpClass(cls):
        import psycopg2
        cls.conn = psycopg2.connect(PG_URL)
        cls.conn.autocommit = True
        cls._reset_schema()
        cls._apply_migrations()
        cls._seed_auth_users()

    @classmethod
    def tearDownClass(cls):
        try:
            cls._reset_schema()
        finally:
            cls.conn.close()

    @classmethod
    def _reset_schema(cls):
        with cls.conn.cursor() as cur:
            # Drop everything we own, including the auth stub. We don't
            # use the Supabase project's real auth schema — this is a
            # plain Postgres test runner.
            cur.execute("""
                DROP TABLE IF EXISTS cloud_revisions, cloud_llm_calls,
                                     cloud_deletions, wiki_files,
                                     knowledge_edges, knowledge_nodes,
                                     papers, sync_sessions, sync_state,
                                     user_profiles CASCADE;
                DROP FUNCTION IF EXISTS check_edge_user_consistency CASCADE;
                DROP FUNCTION IF EXISTS knowra_touch_updated_at CASCADE;
                DROP SCHEMA IF EXISTS auth CASCADE;
            """)

    @classmethod
    def _apply_migrations(cls):
        with cls.conn.cursor() as cur:
            cur.execute(AUTH_STUB_SQL)
            for migration in sorted(MIGRATIONS_DIR.glob("*.sql")):
                cur.execute(migration.read_text())

    @classmethod
    def _seed_auth_users(cls):
        cls.user_a = str(uuid.uuid4())
        cls.user_b = str(uuid.uuid4())
        with cls.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO auth.users (id, email) VALUES (%s, %s), (%s, %s)",
                (cls.user_a, "a@x.com", cls.user_b, "b@x.com"),
            )

    # ── helpers ─────────────────────────────────────────────────

    def _as_user(self, user_id: str):
        """Return a context manager that switches auth.uid() to user_id
        for the duration of the block. Implemented as a per-session
        config setting."""
        outer = self

        class _Ctx:
            def __enter__(self_):
                self_.cur = outer.conn.cursor()
                self_.cur.execute(
                    "SELECT set_config('request.jwt.claim.sub', %s, false)",
                    (user_id or "",),
                )
                return self_.cur

            def __exit__(self_, *exc):
                self_.cur.close()

        return _Ctx()

    def _as_unauth(self):
        return self._as_user("")

    # ── tests ───────────────────────────────────────────────────

    def setUp(self):
        # Wipe data tables between tests; the schema is created once.
        with self.conn.cursor() as cur:
            cur.execute("""
                TRUNCATE cloud_revisions, cloud_llm_calls, cloud_deletions,
                         wiki_files, knowledge_edges, knowledge_nodes,
                         papers, sync_sessions, sync_state, user_profiles
                CASCADE
            """)

    def test_user_a_insert_user_b_cannot_see(self):
        with self._as_user(self.user_a) as cur:
            cur.execute("""
                INSERT INTO papers (user_id, filepath, filename, file_hash, title)
                VALUES (%s, '/a.pdf', 'a.pdf', 'h-a', 'Paper A')
            """, (self.user_a,))
        with self._as_user(self.user_b) as cur:
            cur.execute("SELECT COUNT(*) FROM papers")
            self.assertEqual(cur.fetchone()[0], 0,
                             "user B should not see user A's papers")

    def test_user_a_can_see_own_data(self):
        with self._as_user(self.user_a) as cur:
            cur.execute("""
                INSERT INTO papers (user_id, filepath, filename, file_hash, title)
                VALUES (%s, '/a.pdf', 'a.pdf', 'h-a', 'Paper A')
            """, (self.user_a,))
            cur.execute("SELECT title FROM papers")
            row = cur.fetchone()
            self.assertEqual(row[0], "Paper A")

    def test_user_b_cannot_insert_row_with_user_a_id(self):
        import psycopg2
        with self._as_user(self.user_b) as cur:
            with self.assertRaises(psycopg2.errors.InsufficientPrivilege):
                cur.execute("""
                    INSERT INTO papers (user_id, filepath, filename, file_hash)
                    VALUES (%s, '/x.pdf', 'x.pdf', 'h-x')
                """, (self.user_a,))

    def test_cross_user_edge_rejected_by_trigger(self):
        import psycopg2
        # Two nodes belonging to different users
        with self._as_user(self.user_a) as cur:
            cur.execute("""
                INSERT INTO knowledge_nodes (user_id, title, content)
                VALUES (%s, 'A node', 'content A') RETURNING id
            """, (self.user_a,))
            node_a = cur.fetchone()[0]
        with self._as_user(self.user_b) as cur:
            cur.execute("""
                INSERT INTO knowledge_nodes (user_id, title, content)
                VALUES (%s, 'B node', 'content B') RETURNING id
            """, (self.user_b,))
            node_b = cur.fetchone()[0]
        # User A tries to create an edge from their node to user B's node
        with self._as_user(self.user_a) as cur:
            with self.assertRaises(psycopg2.errors.RaiseException):
                cur.execute("""
                    INSERT INTO knowledge_edges (user_id, source_id, target_id)
                    VALUES (%s, %s, %s)
                """, (self.user_a, node_a, node_b))

    def test_unauthenticated_sees_nothing(self):
        with self._as_user(self.user_a) as cur:
            cur.execute("""
                INSERT INTO papers (user_id, filepath, filename, file_hash, title)
                VALUES (%s, '/a.pdf', 'a.pdf', 'h-a', 'Paper A')
            """, (self.user_a,))
        with self._as_unauth() as cur:
            cur.execute("SELECT COUNT(*) FROM papers")
            self.assertEqual(cur.fetchone()[0], 0,
                             "unauthenticated session must see nothing")

    def test_cloud_deletions_tombstones_are_tenant_isolated(self):
        # User A inserts a tombstone, user B should not see it
        with self._as_user(self.user_a) as cur:
            cur.execute("""
                INSERT INTO cloud_deletions (user_id, table_name, row_id)
                VALUES (%s, 'papers', %s)
            """, (self.user_a, str(uuid.uuid4())))
        with self._as_user(self.user_b) as cur:
            cur.execute("SELECT COUNT(*) FROM cloud_deletions")
            self.assertEqual(cur.fetchone()[0], 0)

    def test_user_profiles_isolated(self):
        with self._as_user(self.user_a) as cur:
            cur.execute("""
                INSERT INTO user_profiles (user_id, display_name)
                VALUES (%s, 'Alice')
            """, (self.user_a,))
        with self._as_user(self.user_b) as cur:
            cur.execute("SELECT COUNT(*) FROM user_profiles")
            # User B's own profile doesn't exist; A's profile is hidden
            self.assertEqual(cur.fetchone()[0], 0)

    def test_wiki_files_isolated(self):
        with self._as_user(self.user_a) as cur:
            cur.execute("""
                INSERT INTO papers (id, user_id, filepath, filename, file_hash)
                VALUES (gen_random_uuid(), %s, '/a.pdf', 'a.pdf', 'h-a')
                RETURNING id
            """, (self.user_a,))
            paper_id = cur.fetchone()[0]
            cur.execute("""
                INSERT INTO wiki_files (user_id, kind, rel_path, storage_path,
                                        content_hash, size_bytes, paper_id)
                VALUES (%s, 'paper', 'papers/0001-a.md',
                        'wiki/aaa/papers/0001-a.md',
                        'sha256:abc', 100, %s)
            """, (self.user_a, paper_id))
        with self._as_user(self.user_b) as cur:
            cur.execute("SELECT COUNT(*) FROM wiki_files")
            self.assertEqual(cur.fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
