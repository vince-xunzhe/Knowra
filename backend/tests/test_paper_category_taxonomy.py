"""Offline tests for paper-category taxonomy edits. Temporary fixtures only."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
sys.path[:0] = [str(ROOT), str(BACKEND)]

from database import get_db  # noqa: E402
from models import Base, Paper  # noqa: E402
from routers import papers as papers_router  # noqa: E402
from services.paper_category_service import (  # noqa: E402
    PAPER_CATEGORY_LLM,
    PAPER_CATEGORY_OTHER,
    PAPER_CATEGORY_VLA,
    PAPER_CATEGORY_VLM,
    PaperCategoryEditError,
    add_taxonomy_category,
    delete_taxonomy_category,
    derive_model_paper_category,
    effective_paper_category,
    get_active_categories,
    normalize_paper_category,
    paper_category_source,
    persist_taxonomy,
    refresh_active_categories,
    rename_taxonomy_category,
    sync_paper_category_fields,
    category_deletion_preview,
)


class PaperCategoryTaxonomyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config = Path(self.tmp.name) / "config.json"
        self.config.write_text("{}", encoding="utf-8")
        patcher = patch("config.CONFIG_FILE", self.config)
        patcher.start()
        self.addCleanup(patcher.stop)
        refresh_active_categories()
        self.addCleanup(refresh_active_categories)

        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        self.Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
        self.db = self.Session()
        self.addCleanup(self.db.close)
        self.canvas_delete = MagicMock()
        self.canvas_rename = MagicMock()
        self.sync_record = MagicMock()
        persist_taxonomy(
            [PAPER_CATEGORY_LLM, PAPER_CATEGORY_VLM, PAPER_CATEGORY_VLA, PAPER_CATEGORY_OTHER]
        )

    def _paper(self, **kwargs) -> Paper:
        n = len(self.db.query(Paper).all()) + 1
        values = dict(
            filepath=f"data/papers/p{n}.pdf",
            filename=f"p{n}.pdf",
            file_hash=f"hash-{n}",
            title=f"Paper {n}",
            authors=["Ada"],
            processed=True,
        )
        values.update(kwargs)
        paper = Paper(**values)
        self.db.add(paper)
        self.db.commit()
        self.db.refresh(paper)
        return paper

    def _delete(self, name, target=None, delete_canvas=None, **kwargs):
        if "research_fingerprint" not in kwargs or "confirm_canvases" not in kwargs:
            preview = category_deletion_preview(self.db, name)
            if "research_fingerprint" not in kwargs and (
                preview["papers"]["count"] or preview["research"]["count"] or (preview.get("canvases") or {}).get("count")
            ):
                kwargs["research_fingerprint"] = preview["fingerprint"]
            if "confirm_canvases" not in kwargs and (preview.get("canvases") or {}).get("contentful_count"):
                kwargs["confirm_canvases"] = True
        return delete_taxonomy_category(
            self.db,
            name,
            target,
            delete_canvas=delete_canvas or self.canvas_delete,
            sync_record=self.sync_record,
            **kwargs,
        )

    def _rename(self, name, new_name, rename_canvas=None):
        return rename_taxonomy_category(
            self.db,
            name,
            new_name,
            rename_canvas=rename_canvas or self.canvas_rename,
            sync_record=self.sync_record,
        )

    def test_add_rejects_blank_and_normalized_duplicates(self):
        with self.assertRaises(PaperCategoryEditError) as blank:
            add_taxonomy_category("   ")
        self.assertEqual(blank.exception.status_code, 400)
        with self.assertRaises(PaperCategoryEditError) as dup:
            add_taxonomy_category(" LLM ")
        self.assertEqual(dup.exception.status_code, 400)
        self.assertIn("LLM", dup.exception.detail)
        with self.assertRaises(PaperCategoryEditError) as aliased:
            add_taxonomy_category("vlm")
        self.assertEqual(aliased.exception.status_code, 400)
        added = add_taxonomy_category("具身数据")
        self.assertIn("具身数据", added)
        self.assertEqual(added[-1], PAPER_CATEGORY_OTHER)

    def test_rename_moves_manual_and_model_papers_and_leaves_others(self):
        manual = self._paper(
            title="Manual",
            paper_category_override=PAPER_CATEGORY_LLM,
            paper_category_model=PAPER_CATEGORY_VLM,
        )
        following = self._paper(title="Follow", paper_category_model=PAPER_CATEGORY_LLM)
        other = self._paper(title="Other", paper_category_model=PAPER_CATEGORY_VLA)
        result = self._rename(PAPER_CATEGORY_LLM, "语言模型")
        self.assertEqual(result["migrated"], 2)
        self.db.refresh(manual)
        self.db.refresh(following)
        self.db.refresh(other)
        self.assertEqual(manual.paper_category_override, "语言模型")
        self.assertEqual(manual.paper_category_model, PAPER_CATEGORY_VLM)
        self.assertEqual(paper_category_source(manual), "manual")
        self.assertEqual(following.paper_category_model, "语言模型")
        self.assertIsNone(following.paper_category_override)
        self.assertEqual(paper_category_source(following), "model")
        self.assertEqual(other.paper_category_model, PAPER_CATEGORY_VLA)
        self.assertNotIn(PAPER_CATEGORY_LLM, get_active_categories())
        self.assertIn("语言模型", get_active_categories())
        self.assertEqual(normalize_paper_category("LLM"), "语言模型")

    def test_empty_delete_requires_no_target_and_is_idempotent(self):
        first = self._delete(PAPER_CATEGORY_VLA)
        self.assertEqual(first["migrated"], 0)
        self.assertFalse(first["already_deleted"])
        self.assertNotIn(PAPER_CATEGORY_VLA, get_active_categories())
        again = self._delete(PAPER_CATEGORY_VLA)
        self.assertTrue(again["already_deleted"])

    def test_occupied_delete_requires_existing_target_and_preserves_source(self):
        manual = self._paper(
            title="Manual LLM",
            paper_category_override=PAPER_CATEGORY_LLM,
            paper_category_model=PAPER_CATEGORY_VLA,
        )
        following = self._paper(title="Follow LLM", paper_category_model=PAPER_CATEGORY_LLM)
        other = self._paper(title="Keep VLA", paper_category_model=PAPER_CATEGORY_VLA)
        with self.assertRaises(PaperCategoryEditError) as missing:
            self._delete(PAPER_CATEGORY_LLM)
        self.assertEqual(missing.exception.status_code, 400)
        self.assertIn("请选择另一个已有大类", missing.exception.detail)
        with self.assertRaises(PaperCategoryEditError) as same:
            self._delete(PAPER_CATEGORY_LLM, PAPER_CATEGORY_LLM)
        self.assertEqual(same.exception.status_code, 400)
        with self.assertRaises(PaperCategoryEditError) as gone:
            self._delete(PAPER_CATEGORY_LLM, "不存在的大类")
        self.assertEqual(gone.exception.status_code, 409)
        result = self._delete(PAPER_CATEGORY_LLM, PAPER_CATEGORY_VLM)
        self.assertEqual(result["migrated"], 2)
        self.db.refresh(manual)
        self.db.refresh(following)
        self.db.refresh(other)
        self.assertEqual(manual.paper_category_override, PAPER_CATEGORY_VLM)
        self.assertEqual(manual.paper_category_model, PAPER_CATEGORY_VLA)
        self.assertEqual(paper_category_source(manual), "manual")
        self.assertEqual(following.paper_category_model, PAPER_CATEGORY_VLM)
        self.assertIsNone(following.paper_category_override)
        self.assertEqual(paper_category_source(following), "model")
        self.assertEqual(other.paper_category_model, PAPER_CATEGORY_VLA)
        self.assertEqual(other.paper_category_override, None)
        self.assertNotIn(PAPER_CATEGORY_LLM, get_active_categories())
        self.assertEqual(effective_paper_category(following), PAPER_CATEGORY_VLM)

    def test_delete_blocked_when_no_alternative_category_exists(self):
        self._paper(paper_category_model=PAPER_CATEGORY_LLM)
        with patch(
            "services.paper_category_service.get_active_categories",
            return_value=[PAPER_CATEGORY_LLM],
        ):
            with self.assertRaises(PaperCategoryEditError) as blocked:
                self._delete(PAPER_CATEGORY_LLM, None)
        self.assertEqual(blocked.exception.status_code, 400)
        self.assertIn("请先新增大类", blocked.exception.detail)
        self.assertIn(PAPER_CATEGORY_LLM, json.loads(self.config.read_text())["paper_categories"])
        self.assertEqual(self.db.query(Paper).one().paper_category_model, PAPER_CATEGORY_LLM)

    def test_canvas_conflict_rolls_back_before_config_or_paper_changes(self):
        paper = self._paper(paper_category_model=PAPER_CATEGORY_LLM)

        def boom(_db, _name):
            raise HTTPException(409, "Canvas changed during category deletion; please retry")

        with self.assertRaises(HTTPException):
            self._delete(PAPER_CATEGORY_LLM, PAPER_CATEGORY_VLM, delete_canvas=boom)
        self.db.refresh(paper)
        self.assertEqual(paper.paper_category_model, PAPER_CATEGORY_LLM)
        self.assertIn(PAPER_CATEGORY_LLM, get_active_categories())
        self.canvas_delete.assert_not_called()

    def test_config_write_failure_after_commit_can_be_retried(self):
        self._paper(paper_category_model=PAPER_CATEGORY_LLM)
        calls = {"n": 0}
        real_persist = persist_taxonomy

        def flaky(categories, redirects=None):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("config write failed")
            return real_persist(categories, redirects)

        with patch("services.paper_category_service.persist_taxonomy", side_effect=flaky):
            with self.assertRaises(RuntimeError):
                self._delete(PAPER_CATEGORY_LLM, PAPER_CATEGORY_VLM)
        # Papers already moved; the name may still be in the list.
        self.assertEqual(self.db.query(Paper).one().paper_category_model, PAPER_CATEGORY_VLM)
        retry = self._delete(PAPER_CATEGORY_LLM, PAPER_CATEGORY_VLM)
        self.assertNotIn(PAPER_CATEGORY_LLM, get_active_categories())
        self.assertEqual(self.db.query(Paper).one().paper_category_model, PAPER_CATEGORY_VLM)
        self.assertTrue(retry["already_deleted"] or retry["migrated"] == 0)

    def test_reextraction_does_not_revive_deleted_category(self):
        following = self._paper(paper_category_model=PAPER_CATEGORY_LLM)
        self._delete(PAPER_CATEGORY_LLM, PAPER_CATEGORY_VLM)
        self.db.refresh(following)
        extraction = {"paper_category": "LLM"}
        self.assertEqual(derive_model_paper_category(following, extraction), PAPER_CATEGORY_VLM)
        sync_paper_category_fields(following, extraction, overwrite_model=True)
        self.assertEqual(following.paper_category_model, PAPER_CATEGORY_VLM)
        self.assertEqual(effective_paper_category(following, extraction), PAPER_CATEGORY_VLM)
        self.assertNotIn(PAPER_CATEGORY_LLM, get_active_categories())
        self.assertEqual(normalize_paper_category("LLM"), PAPER_CATEGORY_VLM)

    def test_reserved_other_cannot_be_removed(self):
        with self.assertRaises(PaperCategoryEditError):
            self._delete(PAPER_CATEGORY_OTHER)
        with self.assertRaises(PaperCategoryEditError):
            self._rename(PAPER_CATEGORY_OTHER, "misc")


class PaperCategoryTaxonomyRouteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config = Path(self.tmp.name) / "config.json"
        self.config.write_text("{}", encoding="utf-8")
        patcher = patch("config.CONFIG_FILE", self.config)
        patcher.start()
        self.addCleanup(patcher.stop)
        refresh_active_categories()
        self.addCleanup(refresh_active_categories)
        persist_taxonomy([PAPER_CATEGORY_LLM, PAPER_CATEGORY_VLM, PAPER_CATEGORY_OTHER])

        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
        self.Session = Session
        session = Session()
        session.add(
            Paper(
                filepath="data/papers/a.pdf",
                filename="a.pdf",
                file_hash="h1",
                title="A",
                authors=[],
                processed=True,
                paper_category_model=PAPER_CATEGORY_LLM,
            )
        )
        session.commit()
        session.close()

        app = FastAPI()
        app.include_router(papers_router.router)

        def override():
            db = Session()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = override
        self.addCleanup(app.dependency_overrides.clear)
        self.sync = patch("routers.papers.sync_record_from_paper", MagicMock())
        self.sync.start()
        self.addCleanup(self.sync.stop)
        self.rename_canvas = patch("routers.papers.rename_category_canvas", MagicMock())
        self.rename_canvas.start()
        self.addCleanup(self.rename_canvas.stop)
        self.delete_canvas = patch("routers.papers.delete_category_canvas", MagicMock())
        self.delete_canvas.start()
        self.addCleanup(self.delete_canvas.stop)
        self.promote = patch("routers.papers.promote_workspace_categories", side_effect=lambda db: get_active_categories())
        self.promote.start()
        self.addCleanup(self.promote.stop)
        self.client = TestClient(app)

    def test_http_add_rename_delete_and_occupied_guard(self):
        empty = self.client.post("/api/paper-categories", json={"name": "  "})
        self.assertEqual(empty.status_code, 400)
        created = self.client.post("/api/paper-categories", json={"name": "机器人"})
        self.assertEqual(created.status_code, 200)
        names = [item["name"] for item in created.json()["categories"]]
        self.assertIn("机器人", names)
        blocked = self.client.delete("/api/paper-categories/LLM")
        self.assertEqual(blocked.status_code, 400)
        preview = self.client.get("/api/paper-categories/LLM/deletion-preview")
        self.assertEqual(preview.status_code, 200)
        moved = self.client.delete(
            "/api/paper-categories/LLM",
            params={"target": "VLM", "research_fingerprint": preview.json()["fingerprint"]},
        )
        self.assertEqual(moved.status_code, 200)
        names = [item["name"] for item in moved.json()["categories"]]
        self.assertNotIn("LLM", names)
        self.assertEqual(moved.json()["migrated"], 1)
        # Retry is safe.
        again = self.client.delete("/api/paper-categories/LLM", params={"target": "VLM"})
        self.assertEqual(again.status_code, 200)
        self.assertTrue(again.json().get("already_deleted"))


if __name__ == "__main__":
    unittest.main()
