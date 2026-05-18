import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.paper_pipeline_service import (
    PIPELINE_STATUS_DONE,
    PIPELINE_STATUS_EXTRACTING,
    PIPELINE_STATUS_SCANNING,
    compute_backoff_seconds,
    is_recoverable_error,
    normalize_processing_status,
)


class _StatusCodeError(Exception):
    def __init__(self, status_code: int):
        super().__init__(f"http {status_code}")
        self.status_code = status_code


class PaperPipelineServiceTests(unittest.TestCase):
    def test_normalize_processing_status(self):
        self.assertEqual(normalize_processing_status(PIPELINE_STATUS_DONE), PIPELINE_STATUS_DONE)
        self.assertEqual(normalize_processing_status("EXTRACTING"), PIPELINE_STATUS_EXTRACTING)
        self.assertEqual(normalize_processing_status("unknown"), PIPELINE_STATUS_SCANNING)

    def test_compute_backoff_seconds_exponential_and_capped(self):
        self.assertEqual(compute_backoff_seconds(0), 0.0)
        self.assertAlmostEqual(compute_backoff_seconds(1, base_seconds=1.5, max_seconds=20.0), 1.5)
        self.assertAlmostEqual(compute_backoff_seconds(2, base_seconds=1.5, max_seconds=20.0), 3.0)
        self.assertAlmostEqual(compute_backoff_seconds(5, base_seconds=1.5, max_seconds=10.0), 10.0)

    def test_is_recoverable_error_by_status_code(self):
        self.assertTrue(is_recoverable_error(_StatusCodeError(429)))
        self.assertTrue(is_recoverable_error(_StatusCodeError(503)))
        self.assertFalse(is_recoverable_error(_StatusCodeError(400)))

    def test_is_recoverable_error_by_message(self):
        self.assertTrue(is_recoverable_error(RuntimeError("request timed out while calling model")))
        self.assertTrue(is_recoverable_error(RuntimeError("解析失败: temporary network glitch")))
        self.assertFalse(is_recoverable_error(RuntimeError("OpenAI API key not configured")))
        self.assertFalse(is_recoverable_error(FileNotFoundError("PDF not found")))


if __name__ == "__main__":
    unittest.main()
