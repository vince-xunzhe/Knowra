from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from models import Base, Paper  # noqa: E402
from services.scanner_service import scan_directory  # noqa: E402


class ScannerDuplicateDetailsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.scan_dir = Path(self.tmp.name) / "papers"
        self.scan_dir.mkdir()
        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        self.Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    def tearDown(self):
        self.tmp.cleanup()

    def test_scan_returns_sorted_duplicate_files_with_match_reason(self):
        original_bytes = b"%PDF-existing-paper"
        original_hash = hashlib.md5(original_bytes).hexdigest()
        db = self.Session()
        try:
            existing = Paper(
                filepath="data/papers/2501.12345v1.pdf",
                filename="2501.12345v1.pdf",
                file_hash=original_hash,
                title="Existing Paper",
                processed=True,
            )
            db.add(existing)
            db.commit()

            arxiv_duplicate = self.scan_dir / "a" / "2501.12345v2.pdf"
            content_duplicate = self.scan_dir / "b" / "copy.pdf"
            new_paper = self.scan_dir / "new.pdf"
            arxiv_duplicate.parent.mkdir()
            content_duplicate.parent.mkdir()
            arxiv_duplicate.write_bytes(b"%PDF-new-version")
            content_duplicate.write_bytes(original_bytes)
            new_paper.write_bytes(b"%PDF-brand-new")

            with patch("services.scanner_service.sync_record_from_paper"):
                result = scan_directory(str(self.scan_dir), db)

            self.assertEqual(result["duplicates"], 2)
            self.assertEqual(result["new_found"], 1)
            self.assertEqual(
                [item["relative_path"] for item in result["duplicate_files"]],
                ["a/2501.12345v2.pdf", "b/copy.pdf"],
            )
            self.assertEqual(result["duplicate_files"][0]["reason"], "same_arxiv_id")
            self.assertEqual(result["duplicate_files"][1]["reason"], "same_content")
            self.assertEqual(
                result["duplicate_files"][0]["matched_paper"]["title"],
                "Existing Paper",
            )
            self.assertEqual(
                result["duplicate_files"][0]["path"],
                str(arxiv_duplicate.resolve()),
            )
        finally:
            db.close()


class RevealScannedFileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.scan_dir = Path(self.tmp.name) / "papers"
        self.scan_dir.mkdir()
        self.pdf_path = self.scan_dir / "duplicate.pdf"
        self.pdf_path.write_bytes(b"%PDF-duplicate")

    def tearDown(self):
        self.tmp.cleanup()

    def test_macos_reveal_selects_pdf_inside_scan_directory(self):
        from routers import papers

        with patch.object(
            papers,
            "load_config",
            return_value={"scan_directory": str(self.scan_dir)},
        ):
            with patch.object(papers.sys, "platform", "darwin"):
                with patch.object(papers.subprocess, "run") as run:
                    result = papers.reveal_scanned_file(
                        papers.RevealScannedFileInput(path=str(self.pdf_path))
                    )

        self.assertTrue(result["selected"])
        self.assertEqual(result["file_manager"], "Finder")
        self.assertEqual(run.call_args.args[0], ["open", "-R", str(self.pdf_path.resolve())])

    def test_reveal_rejects_path_outside_scan_directory(self):
        from fastapi import HTTPException
        from routers import papers

        outside = Path(self.tmp.name) / "outside.pdf"
        outside.write_bytes(b"%PDF-outside")
        with patch.object(
            papers,
            "load_config",
            return_value={"scan_directory": str(self.scan_dir)},
        ):
            with self.assertRaises(HTTPException) as raised:
                papers.reveal_scanned_file(
                    papers.RevealScannedFileInput(path=str(outside))
                )

        self.assertEqual(raised.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
