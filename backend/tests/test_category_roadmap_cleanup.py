"""Offline tests for deleting a paper category together with 识途 research.

Temporary databases and synthetic files only. Never touches the daily library.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
sys.path[:0] = [str(ROOT), str(BACKEND)]

from database import get_db  # noqa: E402
from models import Base, Paper, RoadmapPaper, RoadmapRun, RoadmapWorkspace  # noqa: E402
from routers import papers as papers_router  # noqa: E402
from services.domain_categories import delete_category_canvas  # noqa: E402
from services.paper_category_service import (  # noqa: E402
    PAPER_CATEGORY_LLM,
    PAPER_CATEGORY_OTHER,
    PAPER_CATEGORY_VLM,
    PaperCategoryEditError,
    add_taxonomy_category,
    category_deletion_preview,
    delete_taxonomy_category,
    get_active_categories,
    persist_taxonomy,
    refresh_active_categories,
)
from services.roadmap_category_cleanup import (  # noqa: E402
    board_belongs_to_category,
    collect_category_research,
    set_cleanup_data_root,
    should_skip_deleted_roadmap_board,
    write_board_tombstone,
)
from services.roadmap_figures import ASSET_PREFIX  # noqa: E402


def _asset(name: str) -> str:
    return name.encode("utf-8").hex().ljust(64, "0")[:64]


class FakeStore:
    def __init__(self, jobs=None):
        self.jobs = jobs or {}

    def latest(self, channel):
        return self.jobs.get(channel)

    def update(self, job_id, **kwargs):
        for job in self.jobs.values():
            if job and job.get("job_id") == job_id:
                job.update(kwargs)

    def cancel_queued(self, job_id):
        self.update(job_id, status="cancelled", error="cancelled-queued")


class CategoryRoadmapCleanupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.data = self.root / "data"
        self.data.mkdir()
        (self.data / "roadmap_assets").mkdir()
        (self.data / "papers").mkdir()
        set_cleanup_data_root(self.data)
        self.addCleanup(lambda: set_cleanup_data_root(None))

        self.config = self.root / "config.json"
        self.config.write_text("{}", encoding="utf-8")
        patcher = patch("config.CONFIG_FILE", self.config)
        patcher.start()
        self.addCleanup(patcher.stop)
        refresh_active_categories()
        self.addCleanup(refresh_active_categories)
        persist_taxonomy([PAPER_CATEGORY_LLM, PAPER_CATEGORY_VLM, PAPER_CATEGORY_OTHER])
        add_taxonomy_category("具身智能数据")

        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        self.Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
        self.db = self.Session()
        self.addCleanup(self.db.close)
        self.sync_record = MagicMock()
        self.store = FakeStore()

        self.exclusive_asset = _asset("exclusive-ego")
        self.shared_asset = _asset("shared-figure")
        self.symlink_asset = _asset("symlink-figure")
        self.external_target = self.root / "outside" / "kept.png"
        self.external_target.parent.mkdir()
        self.external_target.write_bytes(b"external-png")
        self._write_asset(self.exclusive_asset, b"exclusive")
        self._write_asset(self.shared_asset, b"shared")
        link = self.data / "roadmap_assets" / f"{self.symlink_asset}.png"
        link.symlink_to(self.external_target)

        self.library_pdf = self.data / "papers" / "library-ego.pdf"
        self.library_pdf.write_bytes(b"%PDF-1.4 library")
        self.library_paper = Paper(
            filepath=str(self.library_pdf),
            filename="library-ego.pdf",
            file_hash="library-hash",
            title="Library Ego Paper",
            authors=["Ada"],
            processed=True,
            paper_category_model="具身智能数据",
        )
        self.db.add(self.library_paper)
        self.db.commit()
        self.db.refresh(self.library_paper)

        self.exclusive_catalog = "rp-exclusive"
        self.shared_catalog = "rp-shared"
        self.db.add(
            RoadmapPaper(
                id=self.exclusive_catalog,
                data={"id": self.exclusive_catalog, "title": "Exclusive catalog"},
                library_paper_id=str(self.library_paper.id),
            )
        )
        self.db.add(
            RoadmapPaper(
                id=self.shared_catalog,
                data={"id": self.shared_catalog, "title": "Shared catalog"},
            )
        )
        self.db.commit()

    def _write_asset(self, asset_id: str, body: bytes) -> Path:
        path = self.data / "roadmap_assets" / f"{asset_id}.png"
        path.write_bytes(body)
        return path

    def _board(self, board_id, name, category, keywords=None, papers=None, extra=None):
        nodes = []
        for index, paper_id in enumerate(papers or []):
            nodes.append({"id": f"{board_id}-n{index}", "kind": "paper", "paperId": paper_id})
        board = {
            "id": board_id,
            "name": name,
            "researchKeywords": list(keywords or []),
            "nodes": nodes,
            "edges": [],
        }
        if category is not None:
            board["category"] = category
        if extra:
            board.update(extra)
        return board

    def _workspace(self, boards, active=None):
        row = self.db.get(RoadmapWorkspace, 1)
        state = {"boards": boards, "activeId": active or (boards[0]["id"] if boards else None)}
        if row is None:
            self.db.add(RoadmapWorkspace(id=1, revision=1, state=state))
        else:
            row.state = state
            row.revision = (row.revision or 1) + 1
        self.db.commit()

    def _run(self, run_id, board_id, status="applied", assets=None, papers=None):
        markdown = " ".join(f"{ASSET_PREFIX}{item}.png" for item in (assets or []))
        snapshot = {"board": {"id": board_id, "nodes": []}, "report": markdown}
        proposal = {
            "board": {"id": board_id, "nodes": []},
            "_papers": [{"id": item} for item in (papers or [])],
        }
        run = RoadmapRun(
            id=run_id,
            board_id=board_id,
            status=status,
            snapshot=snapshot,
            proposal=proposal,
        )
        self.db.add(run)
        self.db.commit()
        return run

    def _preview(self, name="具身智能数据"):
        return category_deletion_preview(self.db, name, store=self.store)

    def _delete(self, name="具身智能数据", target=None, confirm=None, fingerprint=None, delete_canvas=None):
        preview = self._preview(name)
        if confirm is None:
            confirm = preview["research"]["count"] > 0
        if fingerprint is None and (preview["research"]["count"] or preview["papers"]["count"]):
            fingerprint = preview["fingerprint"]
        return delete_taxonomy_category(
            self.db,
            name,
            target,
            delete_canvas=delete_canvas or delete_category_canvas,
            sync_record=self.sync_record,
            confirm_research=confirm,
            research_fingerprint=fingerprint,
            store=self.store,
            data_root=self.data,
        )

    def _seed_standard(self, include_library_paper=False, running=False):
        boards = [
            self._board("b-ego", "具身智能数据 · ego", "具身智能数据", ["ego数据"], [self.exclusive_catalog]),
            self._board("b-llm", "LLM 主线", "LLM", ["transformers"], [self.shared_catalog]),
            self._board("b-custom", "具身智能数据笔记", "自定义方向", ["similar"], [self.shared_catalog]),
            self._board("b-legacy", "具身智能数据", None, ["legacy-ego"], []),
            self._board("b-empty", "具身智能数据", "具身智能数据", [], []),
        ]
        self._workspace(boards, active="b-ego")
        self._run("run-ego", "b-ego", assets=[self.exclusive_asset, self.symlink_asset], papers=[self.exclusive_catalog])
        self._run("run-llm", "b-llm", assets=[self.shared_asset], papers=[self.shared_catalog])
        if running:
            self.store.jobs["roadmap:b-ego"] = {
                "job_id": "job-ego",
                "status": "running",
                "kind": "research",
            }
        if not include_library_paper:
            self.library_paper.paper_category_model = PAPER_CATEGORY_VLM
            self.library_paper.paper_category_override = None
            self.db.commit()

    def test_association_is_exact_not_fuzzy(self):
        self.assertTrue(board_belongs_to_category({"id": "a", "name": "X", "category": "具身智能数据"}, "具身智能数据"))
        self.assertTrue(board_belongs_to_category({"id": "b", "name": "具身智能数据"}, "具身智能数据"))
        self.assertFalse(board_belongs_to_category({"id": "c", "name": "具身智能数据笔记", "category": "自定义方向"}, "具身智能数据"))
        self.assertFalse(board_belongs_to_category({"id": "d", "name": "关于具身智能数据", "category": "LLM"}, "具身智能数据"))
        self.assertFalse(board_belongs_to_category({"id": "e", "name": "具身智能数据", "category": "LLM"}, "具身智能数据"))

    def test_research_without_papers_is_not_an_empty_category(self):
        self._seed_standard(include_library_paper=False)
        preview = self._preview()
        self.assertGreaterEqual(preview["research"]["count"], 2)
        self.assertEqual(preview["papers"]["count"], 0)
        names = {item["name"] for item in preview["research"]["items"]}
        self.assertIn("具身智能数据 · ego", names)
        self.assertIn("具身智能数据", names)
        self.assertNotIn("具身智能数据笔记", names)
        self.assertNotIn("LLM 主线", names)
        with self.assertRaises(PaperCategoryEditError) as raised:
            self._delete(confirm=False, fingerprint=preview["fingerprint"])
        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(raised.exception.details["code"], "research_confirmation_required")
        self.assertIn("具身智能数据", get_active_categories())
        self.assertEqual(self.db.get(RoadmapWorkspace, 1).state["boards"][0]["id"], "b-ego")
        self.assertIsNotNone(self.db.get(RoadmapRun, "run-ego"))

    def test_cancel_and_unconfirmed_http_leave_everything(self):
        self._seed_standard()
        before = self.db.get(RoadmapWorkspace, 1).state
        preview = self._preview()
        self.assertGreater(preview["research"]["count"], 0)
        # Cancel means the client never calls delete; a GET preview is a no-op.
        self.assertEqual(self.db.get(RoadmapWorkspace, 1).state, before)
        self.assertTrue((self.data / "roadmap_assets" / f"{self.exclusive_asset}.png").is_file())
        with self.assertRaises(PaperCategoryEditError):
            self._delete(confirm=False, fingerprint=None)
        self.assertIn("具身智能数据", get_active_categories())
        self.assertTrue((self.data / "roadmap_assets" / f"{self.exclusive_asset}.png").is_file())

    def test_confirmed_cleanup_migrates_papers_and_keeps_library_and_other_research(self):
        self._seed_standard(include_library_paper=True, running=True)
        result = self._delete(target=PAPER_CATEGORY_VLM, confirm=True)
        self.assertEqual(result["migrated"], 1)
        self.assertGreaterEqual(result["research"]["cleaned"], 2)
        self.assertNotIn("具身智能数据", get_active_categories())
        self.db.refresh(self.library_paper)
        self.assertEqual(self.library_paper.paper_category_override or self.library_paper.paper_category_model, PAPER_CATEGORY_VLM)
        self.assertTrue(self.library_pdf.is_file())
        self.assertIsNone(self.db.get(RoadmapPaper, self.exclusive_catalog))
        self.assertIsNotNone(self.db.get(RoadmapPaper, self.shared_catalog))
        self.assertIsNone(self.db.get(RoadmapRun, "run-ego"))
        self.assertIsNotNone(self.db.get(RoadmapRun, "run-llm"))
        remaining = {board["id"] for board in self.db.get(RoadmapWorkspace, 1).state["boards"]}
        self.assertEqual(remaining, {"b-llm", "b-custom"})
        self.assertFalse((self.data / "roadmap_assets" / f"{self.exclusive_asset}.png").exists())
        self.assertTrue((self.data / "roadmap_assets" / f"{self.shared_asset}.png").is_file())
        self.assertTrue(self.external_target.is_file())
        self.assertTrue((self.data / "roadmap_assets" / f"{self.symlink_asset}.png").is_symlink())
        self.assertEqual(self.store.jobs["roadmap:b-ego"]["status"], "cancelled")
        self.assertTrue(should_skip_deleted_roadmap_board("b-ego", data_root=self.data))
        self.assertTrue(should_skip_deleted_roadmap_board("b-legacy", data_root=self.data))
        self.assertFalse(should_skip_deleted_roadmap_board("b-llm", data_root=self.data))

    def test_stale_fingerprint_after_new_research_is_rejected(self):
        self._seed_standard()
        stale = self._preview()["fingerprint"]
        self._run("run-new", "b-legacy", status="pending")
        with self.assertRaises(PaperCategoryEditError) as raised:
            self._delete(confirm=True, fingerprint=stale)
        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(raised.exception.details["code"], "research_changed")
        self.assertIn("具身智能数据", get_active_categories())
        self.assertIsNotNone(self.db.get(RoadmapRun, "run-ego"))
        self.assertTrue((self.data / "roadmap_assets" / f"{self.exclusive_asset}.png").is_file())

    def test_file_delete_failure_rolls_back_and_can_retry(self):
        self._seed_standard(include_library_paper=True)
        preview = self._preview()
        with patch(
            "services.roadmap_category_cleanup.safe_unlink",
            side_effect=OSError("fixture disk error"),
        ):
            with self.assertRaises(PaperCategoryEditError) as raised:
                self._delete(target=PAPER_CATEGORY_VLM, confirm=True, fingerprint=preview["fingerprint"])
        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(raised.exception.details["code"], "file_delete_failed")
        self.assertIn("具身智能数据", get_active_categories())
        self.db.refresh(self.library_paper)
        self.assertEqual(self.library_paper.paper_category_model, "具身智能数据")
        self.assertIsNotNone(self.db.get(RoadmapRun, "run-ego"))
        retry = self._delete(target=PAPER_CATEGORY_VLM, confirm=True)
        self.assertEqual(retry["migrated"], 1)
        self.assertNotIn("具身智能数据", get_active_categories())
        self.assertIsNone(self.db.get(RoadmapRun, "run-ego"))

    def test_late_research_results_do_not_resurrect_deleted_board(self):
        self._seed_standard(running=True)
        self._delete(confirm=True)
        from services.roadmap_state import Cancelled, check, persist_preview

        with patch("services.roadmap_state.SessionLocal", self.Session):
            with self.assertRaises(Cancelled):
                check("run-ego")
            # The applied run is already gone; a leftover pending run on the
            # tombstoned board must not write a preview or catalog row.
            leftover = RoadmapRun(
                id="run-late",
                board_id="b-ego",
                status="pending",
                snapshot={"board": {"id": "b-ego"}},
                proposal={},
            )
            self.db.add(leftover)
            self.db.commit()
            with self.assertRaises(Cancelled):
                check("run-late")
            with self.assertRaises(Cancelled):
                persist_preview("run-late", {"board": {"id": "b-ego", "name": "具身智能数据"}}, [])
        self.assertIsNone(self.db.get(RoadmapPaper, "should-not-appear"))
        remaining = {board["id"] for board in self.db.get(RoadmapWorkspace, 1).state["boards"]}
        self.assertNotIn("b-ego", remaining)

    def test_late_import_unlinks_unadopted_download_and_skips_writeback(self):
        self._seed_standard()
        self._delete(confirm=True)
        downloaded = self.data / "papers" / "2301.00001.pdf"
        calls = {"n": 0}

        def skip(board_id, data_root=None):
            calls["n"] += 1
            return calls["n"] >= 3

        def fake_download(aid, directory):
            path = Path(directory) / "2301.00001.pdf"
            path.write_bytes(b"%PDF-1.4 late")
            return path

        self.db.add(RoadmapPaper(id="rp-late", data={"id": "rp-late", "title": "Late", "arxivId": "2301.00001"}))
        self.db.commit()
        from services import roadmap_import

        with patch("services.roadmap_category_cleanup.should_skip_deleted_roadmap_board", side_effect=skip), \
             patch("services.roadmap_import.download_arxiv", side_effect=fake_download), \
             patch("services.roadmap_import.SessionLocal", self.Session), \
             patch("services.pdf_service.compute_hash", return_value="late-hash"), \
             patch("path_utils.DATA_DIR", self.data), \
             patch("path_utils.portable_data_path", side_effect=lambda path: str(path)), \
             patch("path_utils.resolve_papers_directory", return_value=self.data / "papers"), \
             patch("config.load_config", return_value={"scan_directory": str(self.data / "papers")}):
            with self.assertRaises(roadmap_import.CancelledImport):
                roadmap_import.import_paper("rp-late", board_id="b-ego")
        self.assertFalse(downloaded.exists())
        self.assertTrue(self.library_pdf.is_file())
        self.assertIsNone(self.db.query(Paper).filter_by(file_hash="late-hash").first())

    def test_http_preview_and_enforced_confirmation(self):
        self._seed_standard(include_library_paper=False)
        app = FastAPI()
        app.include_router(papers_router.router)

        def override():
            db = self.Session()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = override
        with patch("routers.papers.sync_record_from_paper", self.sync_record), \
             patch("routers.papers.promote_workspace_categories", side_effect=lambda db: get_active_categories()), \
             TestClient(app) as client:
            preview = client.get("/api/paper-categories/具身智能数据/deletion-preview")
            self.assertEqual(preview.status_code, 200)
            body = preview.json()
            self.assertGreaterEqual(body["research"]["count"], 2)
            self.assertEqual(body["papers"]["count"], 0)
            self.assertTrue(body["research"]["items"][0]["boardId"])
            self.assertTrue(body["research"]["items"][0]["willDelete"])
            refused = client.delete("/api/paper-categories/具身智能数据")
            self.assertEqual(refused.status_code, 409)
            self.assertEqual(refused.json()["detail"]["code"], "research_confirmation_required")
            stale = client.delete(
                "/api/paper-categories/具身智能数据",
                params={"confirm_research": True, "research_fingerprint": "stale"},
            )
            self.assertEqual(stale.status_code, 409)
            self.assertEqual(stale.json()["detail"]["code"], "research_changed")
            self.assertIn("fingerprint", stale.json()["detail"]["preview"])
            ok = client.delete(
                "/api/paper-categories/具身智能数据",
                params={"confirm_research": True, "research_fingerprint": body["fingerprint"]},
            )
            self.assertEqual(ok.status_code, 200)
            names = [item["name"] for item in ok.json()["categories"]]
            self.assertNotIn("具身智能数据", names)


if __name__ == "__main__":
    unittest.main()
