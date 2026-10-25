"""Validate the destructive-suite gate without a driver or database."""
import unittest
import os
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_postgres_rls import allow_destructive_postgres_tests


class PostgresSafetyTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_url_alone_never_enables_destructive_tests(self):
        url = "postgresql://test:placeholder@127.0.0.1:54322/knowra_test"
        for value in (None, "", "true", "yes", "0"):
            with self.subTest(value=value):
                self.assertFalse(allow_destructive_postgres_tests(url, value))

    def test_only_a_dedicated_explicit_loopback_target_is_accepted(self):
        for host in ("127.0.0.1", "localhost", "[::1]"):
            self.assertTrue(allow_destructive_postgres_tests(
                f"postgresql://test:placeholder@{host}:54322/knowra_test", "1"))

    def test_remote_shared_and_libpq_overrides_are_rejected(self):
        for url in (
            "postgresql://test@db.example:54322/knowra_test",
            "postgresql://test@127.0.0.1/knowra_test",
            "postgresql://test@127.0.0.1:5432/knowra_test",
            "postgresql://test@127.0.0.1:54322/production",
            "postgresql://test@127.0.0.1:54322/knowra_test?host=db.example",
            "postgresql://test@127.0.0.1:54322/knowra_test#fragment",
            "postgresql://test@127.0.0.1:invalid/knowra_test",
            "postgresql://test@127.0.0.1:99999/knowra_test",
            "sqlite:///knowra_test",
            None,
        ):
            with self.subTest(url=url):
                self.assertFalse(allow_destructive_postgres_tests(url, "1"))

    def test_libpq_environment_cannot_redirect_a_valid_uri(self):
        url = "postgresql://test:placeholder@127.0.0.1:54322/knowra_test"
        for key in ("PGHOST", "PGHOSTADDR", "PGPORT", "PGDATABASE", "PGSERVICE",
                    "PGSERVICEFILE", "PGPASSFILE", "PGPASSWORD", "PGSSLCERT"):
            with self.subTest(key=key), patch.dict(os.environ, {key: "override"}):
                self.assertFalse(allow_destructive_postgres_tests(url, "1"))


if __name__ == "__main__":
    unittest.main()
