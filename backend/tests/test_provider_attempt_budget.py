import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from model_gateway.attempt_budget import (
    ProviderAttemptBudgetError,
    ProviderAttemptLimitExceeded,
    ProviderAttemptReplayBlocked,
)
from model_gateway.runtime import call_text_model


class ProviderAttemptBudgetTests(unittest.TestCase):
    def _env(self, root: Path, limit: int) -> dict[str, str]:
        return {
            "KNOWRA_PROVIDER_ATTEMPT_LEDGER": str(root / "attempts.json"),
            "KNOWRA_PROVIDER_ATTEMPT_LIMIT": str(limit),
            "KNOWRA_PROVIDER_ATTEMPT_RUN_ID": "public-paper-test-run",
        }

    def _call(self, operation_id: str | None):
        return call_text_model(
            {},
            model_id="codex-cli/gpt-6-astra",
            system="system",
            user="user",
            reasoning_effort="high",
            attempt_operation_id=operation_id,
        )

    def test_reserves_before_dispatch_and_persists_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            def dispatched(*_args, **_kwargs):
                ledger = json.loads((root / "attempts.json").read_text(encoding="utf-8"))
                self.assertEqual(ledger["attempts"][0]["status"], "started")
                return "ok"

            with (
                patch.dict(os.environ, self._env(root, 2), clear=False),
                patch("model_gateway.runtime._call_text_model_unbudgeted", side_effect=dispatched),
            ):
                self.assertEqual(self._call("map:one"), "ok")
            ledger = json.loads((root / "attempts.json").read_text(encoding="utf-8"))
            self.assertEqual(ledger["attempts"][0]["status"], "succeeded")
            self.assertEqual(ledger["attempts"][0]["number"], 1)

    def test_unknown_counts_and_same_operation_is_never_replayed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with (
                patch.dict(os.environ, self._env(root, 2), clear=False),
                patch(
                    "model_gateway.runtime._call_text_model_unbudgeted",
                    side_effect=TimeoutError("transport uncertain"),
                ) as dispatch,
            ):
                with self.assertRaises(ProviderAttemptBudgetError):
                    self._call("map:one")
                with self.assertRaises(ProviderAttemptReplayBlocked):
                    self._call("map:one")
            self.assertEqual(dispatch.call_count, 1)
            ledger = json.loads((root / "attempts.json").read_text(encoding="utf-8"))
            self.assertEqual(ledger["attempts"][0]["status"], "unknown")
            self.assertEqual(ledger["attempts"][0]["error_class"], "TimeoutError")

    def test_limit_blocks_before_sixteenth_equivalent_dispatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with (
                patch.dict(os.environ, self._env(root, 15), clear=False),
                patch("model_gateway.runtime._call_text_model_unbudgeted", return_value="ok") as dispatch,
            ):
                for number in range(1, 16):
                    self._call(f"operation:{number}")
                with self.assertRaises(ProviderAttemptLimitExceeded):
                    self._call("operation:16")
            self.assertEqual(dispatch.call_count, 15)
            ledger = json.loads((root / "attempts.json").read_text(encoding="utf-8"))
            self.assertEqual(len(ledger["attempts"]), 15)

    def test_active_guard_rejects_unidentified_request_without_dispatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with (
                patch.dict(os.environ, self._env(root, 15), clear=False),
                patch("model_gateway.runtime._call_text_model_unbudgeted") as dispatch,
            ):
                with self.assertRaises(ProviderAttemptBudgetError):
                    self._call(None)
            dispatch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
