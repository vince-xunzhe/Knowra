from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.paper_evidence_service import build_evidence_manifest
from services.shadow_generation import (
    ShadowGenerationError,
    build_restored_shadow_generation,
    finalize_shadow_generation,
)


class RestoredShadowGenerationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.data = self.root / "restore" / "data"
        self.papers = self.data / "papers"
        self.papers.mkdir(parents=True)
        self.production = self.root / "production" / "wiki"
        self.production.mkdir(parents=True)
        self.pdf = self.papers / "paper.pdf"
        self.pdf.write_bytes(b"synthetic-pdf")
        database = sqlite3.connect(self.data / "knowledge.db")
        database.execute(
            "CREATE TABLE papers (id TEXT, filepath TEXT, filename TEXT, file_hash TEXT, title TEXT, "
            "raw_llm_response TEXT, notes TEXT, processed INTEGER)"
        )
        database.execute(
            "INSERT INTO papers VALUES (?,?,?,?,?,?,?,1)",
            (
                "paper-1", "data/papers/paper.pdf", "paper.pdf",
                hashlib.md5(b"synthetic-pdf").hexdigest(),
                "Synthetic Paper", json.dumps({
                    "title": "Synthetic Paper", "year": 2024,
                    "innovations": {"this_work": "Actions condition the transition."},
                }), "",
            ),
        )
        database.commit()
        database.close()

    def tearDown(self):
        self.temporary.cleanup()

    def _manifest(self):
        return build_evidence_manifest(
            paper_id="paper-1",
            source_file_hash=hashlib.sha256(b"synthetic-pdf").hexdigest(),
            source_size_bytes=len(b"synthetic-pdf"),
            total_pages=1,
            pages=[{"page_number": 1, "status": "ok", "text": "Actions condition the latent transition."}],
            parser_version="synthetic@test",
        )

    def test_builds_complete_non_production_generation(self):
        output = self.root / "shadow" / "generation-1"
        output.parent.mkdir()
        with patch("services.shadow_generation.build_pdf_evidence", return_value=self._manifest()):
            summary = build_restored_shadow_generation(
                data_root=self.data,
                output_root=output,
                production_wiki_root=self.production,
            )
        self.assertEqual(summary["papers_compiled"], 1)
        self.assertEqual(summary["papers_failed"], 0)
        generation = json.loads((output / "generation.json").read_text())
        self.assertFalse(generation["production_eligible"])
        self.assertEqual(generation["status"], "ready")
        self.assertTrue((output / "indexes" / "claims.sqlite").is_file())

        (output / "generation.json").unlink()
        (output / "summary.json").unlink()
        (output / "indexes" / "claims.sqlite").unlink()
        resumed = finalize_shadow_generation(
            output_root=output, production_wiki_root=self.production
        )
        self.assertEqual(resumed["papers_compiled"], 1)
        self.assertTrue((output / "generation.json").is_file())

    def test_refuses_existing_or_overlapping_output(self):
        output = self.root / "existing"
        output.mkdir()
        with self.assertRaisesRegex(ShadowGenerationError, "already exists"):
            build_restored_shadow_generation(
                data_root=self.data,
                output_root=output,
                production_wiki_root=self.production,
            )
        nested = self.data / "shadow"
        with self.assertRaisesRegex(ShadowGenerationError, "overlaps"):
            build_restored_shadow_generation(
                data_root=self.data,
                output_root=nested,
                production_wiki_root=self.production,
            )


if __name__ == "__main__":
    unittest.main()
