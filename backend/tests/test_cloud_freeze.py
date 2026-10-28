"""Cloud freeze boundaries using only synthetic data and mocked side effects.

Run through scripts/check.sh's isolated source snapshot, never against a
working research library. The retained sync contract has its own tests.
"""

import builtins
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import runpy
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import cloud_policy


ROOT = Path(__file__).resolve().parents[2]


class CloudPolicyTests(unittest.TestCase):
    def test_repository_policy_is_frozen_even_with_legacy_environment(self):
        self.assertEqual(
            json.loads((ROOT / "cloud-capabilities.json").read_text(encoding="utf-8")),
            {"enabled": False},
        )
        with patch.dict(os.environ, {
            "KNOWRA_DEPLOY_MODE": "cloud",
            "KNOWRA_CLOUD_ENABLED": "true",
            "VITE_CLOUD_ENABLED": "true",
            "KNOWRA_STORAGE_BACKEND": "memory",
            "SUPABASE_PROJECT_URL": "https://example.invalid",
            "KNOWRA_CLOUD_URL": "https://example.invalid",
        }):
            self.assertFalse(cloud_policy.cloud_features_enabled())
            with self.assertRaises(cloud_policy.CloudFeaturesDisabled):
                cloud_policy.ensure_cloud_deployment_allowed()

    def test_missing_or_invalid_policy_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cloud-capabilities.json"
            with patch.object(cloud_policy, "_POLICY_FILE", path):
                self.assertFalse(cloud_policy.cloud_features_enabled())
                for value in ("{", "[]", "null", "{}", '{"enabled":"true"}',
                              '{"enabled":1}', '{"enabled":false}'):
                    with self.subTest(value=value):
                        path.write_text(value, encoding="utf-8")
                        self.assertFalse(cloud_policy.cloud_features_enabled())
                path.write_bytes(b"\xff")
                self.assertFalse(cloud_policy.cloud_features_enabled())

    def test_only_reviewed_manifest_boolean_true_enables_features(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cloud-capabilities.json"
            path.write_text(json.dumps({"enabled": True}), encoding="utf-8")
            with patch.object(cloud_policy, "_POLICY_FILE", path):
                self.assertTrue(cloud_policy.cloud_features_enabled())
                cloud_policy.require_cloud_enabled()

    def test_cloud_startup_rejects_before_application_imports(self):
        imported = []
        original_import = builtins.__import__

        def guarded_import(name, *args, **kwargs):
            if name.split(".")[0] in {
                "config", "database", "cloud_db", "models", "routers", "services",
            }:
                imported.append(name)
                raise AssertionError("Application import ran before freeze check")
            return original_import(name, *args, **kwargs)

        with patch.dict(os.environ, {"KNOWRA_DEPLOY_MODE": " cloud "}), \
                patch("builtins.__import__", side_effect=guarded_import):
            with self.assertRaises(cloud_policy.CloudFeaturesDisabled):
                runpy.run_path(str(ROOT / "backend" / "main.py"))
        self.assertEqual(imported, [])

    def test_local_deployment_does_not_require_cloud(self):
        with patch.dict(os.environ, {"KNOWRA_DEPLOY_MODE": "local"}), \
                patch.object(cloud_policy, "require_cloud_enabled") as guard:
            cloud_policy.ensure_cloud_deployment_allowed()
        guard.assert_not_called()


class FrozenApiTests(unittest.TestCase):
    def test_repeated_frozen_requests_preserve_synthetic_library_bytes_and_rows(self):
        import config
        from database import get_db
        from routers import recommendation_local as lifecycle, sync_local
        from services import local_recommendations

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = root / "data"
            data.mkdir()
            database_path = data / "knowledge.db"
            with contextlib.closing(sqlite3.connect(database_path)) as connection:
                connection.executescript(
                    "CREATE TABLE papers (id TEXT PRIMARY KEY, title TEXT, notes TEXT);"
                    "CREATE TABLE note_images (id TEXT PRIMARY KEY, paper_id TEXT, path TEXT);"
                )
                connection.execute(
                    "INSERT INTO papers VALUES (?, ?, ?)",
                    ("synthetic-paper", "Synthetic paper", "合成笔记：保留全部内容。"),
                )
                connection.execute(
                    "INSERT INTO note_images VALUES (?, ?, ?)",
                    ("synthetic-image", "synthetic-paper", "note-images/sentinel.png"),
                )
                connection.commit()
            for relative, content in {
                "papers/sentinel.pdf": b"%PDF-1.4\nSynthetic PDF sentinel\n%%EOF\n",
                "wiki/papers/sentinel.md": "# 合成 Wiki\n保留正文。\n".encode(),
                "note-images/sentinel.png": b"\x89PNG\r\n\x1a\nSynthetic image sentinel",
                "config.json": b'{"synthetic_fixture":true}\n',
            }.items():
                path = data / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)

            def logical_rows():
                with contextlib.closing(sqlite3.connect(database_path)) as connection:
                    return (
                        connection.execute("SELECT * FROM papers ORDER BY id").fetchall(),
                        connection.execute("SELECT * FROM note_images ORDER BY id").fetchall(),
                    )

            def filesystem_state():
                return {
                    path.relative_to(root).as_posix(): (
                        "directory" if path.is_dir()
                        else hashlib.sha256(path.read_bytes()).hexdigest()
                    )
                    for path in sorted(root.rglob("*"))
                }

            rows_before = logical_rows()
            files_before = filesystem_state()
            database_access = Mock()

            def synthetic_database():
                database_access()
                with contextlib.closing(sqlite3.connect(database_path)) as connection:
                    yield connection

            app = FastAPI()
            app.include_router(sync_local.router)
            app.include_router(lifecycle.router)
            app.dependency_overrides[get_db] = synthetic_database
            with patch.object(config, "load_config") as load_config, \
                    patch.object(local_recommendations, "local_store") as local_store, \
                    patch.object(lifecycle.subprocess, "Popen") as spawn:
                client = TestClient(app)
                for _ in range(3):
                    responses = (
                        client.get("/api/sync/local_snapshot"),
                        client.post("/api/recommendations/worker/start", json={
                            "url": "https://example.invalid", "token": "krw_" + "x" * 32,
                        }),
                    )
                    for response in responses:
                        self.assertEqual(response.status_code, 403)
                        self.assertEqual(response.json()["detail"]["error"], "cloud_disabled")
            database_access.assert_not_called()
            load_config.assert_not_called()
            local_store.assert_not_called()
            spawn.assert_not_called()
            self.assertEqual(filesystem_state(), files_before)
            self.assertEqual(logical_rows(), rows_before)

    def test_snapshot_api_rejects_before_database_dependency(self):
        from database import get_db
        from routers import sync_local

        app = FastAPI()
        app.include_router(sync_local.router)
        database_access = Mock(side_effect=AssertionError("Database opened"))

        def forbidden_database():
            database_access()

        app.dependency_overrides[get_db] = forbidden_database
        response = TestClient(app).get("/api/sync/local_snapshot")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["detail"]["error"], "cloud_disabled")
        database_access.assert_not_called()

    def test_direct_snapshot_function_rejects_before_query(self):
        from routers import sync_local

        database = Mock()
        with self.assertRaises(HTTPException) as raised:
            sync_local.local_snapshot(db=database)
        self.assertEqual(raised.exception.status_code, 403)
        database.query.assert_not_called()
        database.commit.assert_not_called()

    def test_remote_worker_api_rejects_before_process_or_local_state(self):
        from routers import recommendation_local as lifecycle

        app = FastAPI()
        app.include_router(lifecycle.router)
        with patch.object(lifecycle, "_local_only") as local_check, \
                patch.object(lifecycle.subprocess, "Popen") as spawn:
            response = TestClient(app).post(
                "/api/recommendations/worker/start",
                json={"url": "https://example.invalid", "token": "krw_" + "x" * 32},
            )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["detail"]["error"], "cloud_disabled")
        local_check.assert_not_called()
        spawn.assert_not_called()

    def test_direct_remote_worker_function_rejects_before_local_state(self):
        from routers import recommendation_local as lifecycle

        body = lifecycle.StartWorker(
            url="https://example.invalid", token="krw_" + "x" * 32,
        )
        with patch.object(lifecycle, "_local_only") as local_check, \
                patch.object(lifecycle.subprocess, "Popen") as spawn:
            with self.assertRaises(HTTPException) as raised:
                lifecycle.start(body)
        self.assertEqual(raised.exception.status_code, 403)
        local_check.assert_not_called()
        spawn.assert_not_called()

    def test_local_worker_start_remains_available(self):
        from routers import recommendation_local as lifecycle
        from services import local_recommendations

        app = FastAPI()
        app.include_router(lifecycle.router)
        with patch.object(lifecycle, "_process", None), \
                patch.object(local_recommendations, "local_store") as local_store, \
                patch.object(local_recommendations, "worker_is_running", return_value=True), \
                patch.object(lifecycle.subprocess, "Popen") as spawn:
            response = TestClient(app).post("/api/recommendations/worker/start-local")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"running": True, "mode": "local"})
        local_store.assert_called_once_with()
        spawn.assert_not_called()


class FrozenWorkerTests(unittest.TestCase):
    def test_remote_client_constructor_rejects(self):
        from scripts import recommendation_worker as worker

        with self.assertRaises(cloud_policy.CloudFeaturesDisabled):
            worker.WorkerClient("https://example.invalid", "krw_" + "x" * 32)

    def test_existing_remote_client_cannot_send_after_freeze(self):
        from scripts import recommendation_worker as worker

        with patch.object(cloud_policy, "cloud_features_enabled", return_value=True):
            client = worker.WorkerClient("https://example.invalid", "krw_" + "x" * 32)
        with patch.object(worker.urllib.request, "build_opener") as transport:
            with self.assertRaises(cloud_policy.CloudFeaturesDisabled):
                client.post("/heartbeat")
        transport.assert_not_called()

    def test_remote_cli_rejects_before_loading_config_or_connecting(self):
        from scripts import recommendation_worker as worker

        for arguments in ([], ["--url", "https://example.invalid", "--provider", "api"]):
            with self.subTest(arguments=arguments), \
                    patch.object(sys, "argv", ["recommendation_worker.py", *arguments]), \
                    patch.object(worker, "load_config") as load_config, \
                    patch.object(worker, "WorkerClient") as remote, \
                    contextlib.redirect_stderr(io.StringIO()) as error:
                with self.assertRaises(SystemExit) as raised:
                    worker.main()
            self.assertEqual(raised.exception.code, 2)
            self.assertIn(cloud_policy.CLOUD_DISABLED_MESSAGE, error.getvalue())
            load_config.assert_not_called()
            remote.assert_not_called()

    def test_local_cli_still_uses_local_worker_path(self):
        from scripts import recommendation_worker as worker
        from services import local_recommendations

        with patch.object(sys, "argv", ["recommendation_worker.py", "--local", "--no-ai"]), \
                patch.object(local_recommendations, "acquire_worker_lock", return_value=None) as lock, \
                patch.object(worker, "WorkerClient") as remote, \
                patch.object(worker, "load_config") as load_config, \
                contextlib.redirect_stdout(io.StringIO()):
            worker.main()
        lock.assert_called_once_with()
        remote.assert_not_called()
        load_config.assert_not_called()


if __name__ == "__main__":
    unittest.main()
