import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pypdf import PdfWriter
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
sys.path[:0] = [str(ROOT), str(BACKEND)]

from database import get_db  # noqa: E402
from models import Base, Paper  # noqa: E402
from routers import papers  # noqa: E402
from services import pdf_service  # noqa: E402
from services.paper_preview_service import (  # noqa: E402
    attach_first_page_preview,
    ensure_first_page_image,
    paper_has_first_page_image,
)
from services.scanner_service import scan_directory  # noqa: E402


def write_pdf(path: Path, pages: int = 2) -> Path:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=200, height=280)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        writer.write(handle)
    return path


class FirstPagePreviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.papers_dir = self.root / "data" / "papers"
        self.artifacts_dir = self.root / "data" / "artifacts"
        self.papers_dir.mkdir(parents=True)
        self.artifacts_dir.mkdir(parents=True)

        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        self.Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)

        self.patches = [
            patch.object(pdf_service, "ARTIFACT_DIR", self.artifacts_dir),
            patch("path_utils.PROJECT_ROOT", self.root),
            patch("path_utils.DATA_DIR", self.root / "data"),
            patch("path_utils.PAPERS_DIR", self.papers_dir),
            patch("path_utils.ARTIFACTS_DIR", self.artifacts_dir),
            patch("services.scanner_service.sync_record_from_paper"),
        ]
        for mock in self.patches:
            mock.start()
            self.addCleanup(mock.stop)

    def _session(self):
        return self.Session()

    def test_unprocessed_scan_gets_first_page_without_model_extract(self):
        pdf = write_pdf(self.papers_dir / "new-paper.pdf", pages=3)
        db = self._session()
        try:
            result = scan_directory(str(self.papers_dir), db)
            self.assertEqual(result["new_found"], 1)
            paper = db.query(Paper).filter_by(filename="new-paper.pdf").one()
            self.assertFalse(paper.processed)
            self.assertTrue(paper_has_first_page_image(paper))
            self.assertEqual(paper.num_pages, 3)
            image = Path(pdf_service.ARTIFACT_DIR / f"{paper.file_hash}_page1.png")
            self.assertTrue(image.is_file())
            self.assertGreater(image.stat().st_size, 0)
        finally:
            db.close()

    def test_existing_paper_without_preview_is_backfilled_on_demand(self):
        pdf = write_pdf(self.papers_dir / "legacy.pdf", pages=2)
        db = self._session()
        try:
            paper = Paper(
                filepath=str(pdf),
                filename="legacy.pdf",
                file_hash="legacyhash",
                processed=False,
                first_page_image_path=None,
            )
            db.add(paper)
            db.commit()
            db.refresh(paper)
            self.assertFalse(paper_has_first_page_image(paper))

            changed = attach_first_page_preview(paper)
            self.assertTrue(changed)
            db.commit()
            self.assertTrue(paper_has_first_page_image(paper))
            self.assertEqual(paper.num_pages, 2)
        finally:
            db.close()

    def test_render_failure_leaves_placeholder_and_does_not_block_registration(self):
        broken = self.papers_dir / "broken.pdf"
        broken.write_bytes(b"%PDF-not-a-real-document")
        db = self._session()
        try:
            result = scan_directory(str(self.papers_dir), db)
            self.assertEqual(result["new_found"], 1)
            paper = db.query(Paper).filter_by(filename="broken.pdf").one()
            self.assertFalse(paper.processed)
            self.assertFalse(paper_has_first_page_image(paper))
            self.assertIsNone(paper.first_page_image_path)
        finally:
            db.close()

    def test_cache_does_not_rerender_the_same_hash(self):
        pdf = write_pdf(self.papers_dir / "cached.pdf")
        first = ensure_first_page_image(str(pdf), "same-hash")
        self.assertIsNotNone(first)
        image = self.artifacts_dir / "same-hash_page1.png"
        stamp = image.stat().st_mtime
        with patch.object(pdf_service, "render_first_page", side_effect=AssertionError("rerendered")):
            second = ensure_first_page_image(str(pdf), "same-hash")
        self.assertEqual(first, second)
        self.assertEqual(image.stat().st_mtime, stamp)


class FirstPagePreviewApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.papers_dir = self.root / "data" / "papers"
        self.artifacts_dir = self.root / "data" / "artifacts"
        self.papers_dir.mkdir(parents=True)
        self.artifacts_dir.mkdir(parents=True)

        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        self.Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)

        self.patches = [
            patch.object(pdf_service, "ARTIFACT_DIR", self.artifacts_dir),
            patch("path_utils.PROJECT_ROOT", self.root),
            patch("path_utils.DATA_DIR", self.root / "data"),
            patch("path_utils.PAPERS_DIR", self.papers_dir),
            patch("path_utils.ARTIFACTS_DIR", self.artifacts_dir),
        ]
        for mock in self.patches:
            mock.start()
            self.addCleanup(mock.stop)

    def _client(self):
        def override():
            db = self.Session()
            try:
                yield db
            finally:
                db.close()

        app = FastAPI()
        app.include_router(papers.router)
        app.dependency_overrides[get_db] = override
        return TestClient(app)

    def test_list_and_first_page_endpoint_backfill_unprocessed_paper(self):
        pdf = write_pdf(self.papers_dir / "api-paper.pdf", pages=2)
        db = self.Session()
        try:
            paper = Paper(
                filepath=str(pdf),
                filename="api-paper.pdf",
                file_hash="apihash",
                processed=False,
            )
            db.add(paper)
            db.commit()
            paper_id = paper.id
        finally:
            db.close()

        client = self._client()
        try:
            listed = client.get("/api/papers")
            self.assertEqual(listed.status_code, 200)
            item = next(row for row in listed.json() if row["filename"] == "api-paper.pdf")
            self.assertFalse(item["processed"])
            self.assertFalse(item["has_first_page_image"])

            response = client.get(f"/api/papers/{paper_id}/first_page")
            self.assertEqual(response.status_code, 200)
            self.assertIn("image/png", response.headers.get("content-type", ""))
            self.assertGreater(len(response.content), 32)
            self.assertEqual(response.content[:8], bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A]))

            listed = client.get("/api/papers")
            item = next(row for row in listed.json() if row["filename"] == "api-paper.pdf")
            self.assertTrue(item["has_first_page_image"])
            self.assertEqual(item["num_pages"], 2)
            self.assertFalse(item["processed"])
        finally:
            pass

    def test_first_page_failure_is_not_found_and_paper_remains(self):
        broken = self.papers_dir / "missing-preview.pdf"
        broken.write_bytes(b"%PDF-broken")
        db = self.Session()
        try:
            paper = Paper(
                filepath=str(broken),
                filename="missing-preview.pdf",
                file_hash="badhash",
                processed=False,
            )
            db.add(paper)
            db.commit()
            paper_id = paper.id
        finally:
            db.close()

        client = self._client()
        try:
            response = client.get(f"/api/papers/{paper_id}/first_page")
            self.assertEqual(response.status_code, 404)
            listed = client.get("/api/papers")
            item = next(row for row in listed.json() if str(row["id"]) == str(paper_id))
            self.assertFalse(item["has_first_page_image"])
            self.assertEqual(item["filename"], "missing-preview.pdf")
        finally:
            pass
