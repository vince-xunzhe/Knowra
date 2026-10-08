from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main


class StartupSchemaTests(unittest.TestCase):
    def test_schema_preflight_runs_before_active_worker_shortcut(self):
        events: list[str] = []
        queue = Mock()
        queue.active.side_effect = lambda: events.append("active") or [object()]

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "knowledge.db"
            db_path.touch()
            with (
                patch.object(main, "is_cloud_mode", return_value=False),
                patch.object(main, "DB_PATH", db_path),
                patch.object(main._local_backend_lock, "acquire"),
                patch.object(
                    main,
                    "ensure_runtime_schema",
                    side_effect=lambda: events.append("schema"),
                ),
                patch.object(main, "init_db") as init_db,
                patch("services.task_runtime.store", return_value=queue),
                patch(
                    "services.task_runtime.ensure_worker",
                    side_effect=lambda: events.append("worker"),
                ),
            ):
                main.startup()

        self.assertEqual(events, ["schema", "active", "worker"])
        init_db.assert_not_called()


if __name__ == "__main__":
    unittest.main()
